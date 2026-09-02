"""DataForSEO-backed indexation checks for DMCA-reported NSN URLs."""

from __future__ import annotations

import base64
import json
import os
import re
import sqlite3
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

DATAFORSEO_ENDPOINT = "https://api.dataforseo.com/v3/serp/google/organic/live/regular"
DATAFORSEO_ONPAGE_ENDPOINT = "https://api.dataforseo.com/v3/on_page/instant_pages"
USER_AGENT = "NSN-DMCA-Indexation-Monitor/1.0"
MAX_HTML_BYTES = 262_144

INDEXATION_SCHEMA = """
CREATE TABLE IF NOT EXISTS indexation_state (
    url TEXT PRIMARY KEY,
    state TEXT NOT NULL DEFAULT 'unknown',
    first_seen_at TEXT NOT NULL,
    last_checked_at TEXT,
    last_positive_at TEXT,
    first_absent_at TEXT,
    consecutive_absent INTEGER NOT NULL DEFAULT 0,
    absence_confirmed_at TEXT,
    last_http_status INTEGER,
    last_indexable INTEGER,
    last_matched_url TEXT NOT NULL DEFAULT '',
    last_cost_usd REAL NOT NULL DEFAULT 0,
    last_error TEXT NOT NULL DEFAULT '',
    alert_emitted_at TEXT,
    location_code INTEGER NOT NULL DEFAULT 0,
    language_code TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_indexation_state_state ON indexation_state(state);
CREATE TABLE IF NOT EXISTS indexation_checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    checked_at TEXT NOT NULL,
    result TEXT NOT NULL,
    http_status INTEGER,
    indexable INTEGER,
    matched_url TEXT NOT NULL DEFAULT '',
    cost_usd REAL NOT NULL DEFAULT 0,
    error TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_indexation_checks_url_time
ON indexation_checks(url, checked_at DESC);
"""


@dataclass(frozen=True)
class PageHealth:
    status: int | None
    indexable: bool
    final_url: str
    reason: str = ""
    cost_usd: float = 0.0


@dataclass(frozen=True)
class SerpCheck:
    indexed: bool
    matched_url: str = ""
    cost_usd: float = 0.0


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _url_key(value: str) -> tuple[str, str, str]:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").lower().removeprefix("www.")
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/")
    # Fragments never reach the server and are not part of Google's index key.
    return host, path, parsed.query


def urls_equivalent(left: str, right: str) -> bool:
    try:
        return _url_key(left) == _url_key(right)
    except ValueError:
        return False


def url_identity(value: str) -> str:
    host, path, query = _url_key(value)
    suffix = f"?{query}" if query else ""
    return f"https://{host}{path}{suffix}"


def _target_from_url(value: str) -> str:
    parsed = urlsplit(value)
    host = (parsed.hostname or "").removeprefix("www.")
    path = parsed.path or "/"
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{host}{path}{query}"


def _canonical_from_html(html: str, base_url: str) -> str:
    match = re.search(
        r"<link\b[^>]*\brel=[\"'][^\"']*canonical[^\"']*[\"'][^>]*\bhref=[\"']([^\"']+)",
        html,
        flags=re.I,
    )
    if not match:
        match = re.search(
            r"<link\b[^>]*\bhref=[\"']([^\"']+)[\"'][^>]*\brel=[\"'][^\"']*canonical",
            html,
            flags=re.I,
        )
    if not match:
        return ""
    candidate = unescape(match.group(1).strip())
    if candidate.startswith("//"):
        return f"{urlsplit(base_url).scheme}:{candidate}"
    if candidate.startswith("/"):
        parsed = urlsplit(base_url)
        return urlunsplit((parsed.scheme, parsed.netloc, candidate, "", ""))
    return candidate


def check_page_health(url: str, timeout: float = 15.0) -> PageHealth:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            status = int(response.status)
            final_url = response.geturl()
            headers = response.headers
            body = response.read(MAX_HTML_BYTES).decode(
                headers.get_content_charset() or "utf-8", errors="replace"
            )
    except urllib.error.HTTPError as exc:
        return PageHealth(int(exc.code), False, exc.geturl() or url, f"http_{exc.code}")
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        return PageHealth(None, False, url, f"fetch_error:{type(exc).__name__}")

    if status != 200:
        return PageHealth(status, False, final_url, f"http_{status}")
    x_robots = " ".join(headers.get_all("X-Robots-Tag", [])).lower()
    meta_robots = " ".join(
        re.findall(
            r"<meta\b[^>]*(?:name|http-equiv)=[\"'](?:robots|googlebot)[\"'][^>]*content=[\"']([^\"']+)",
            body,
            flags=re.I,
        )
    ).lower()
    if "noindex" in x_robots or "noindex" in meta_robots:
        return PageHealth(status, False, final_url, "noindex")
    canonical = _canonical_from_html(body, final_url)
    if canonical and not urls_equivalent(canonical, final_url):
        return PageHealth(status, False, final_url, f"canonical_to:{canonical}")
    return PageHealth(status, True, final_url)


class DataForSEOClient:
    def __init__(self, login: str, password: str, timeout: float = 30.0):
        self.login = login
        self.password = password
        self.timeout = timeout

    @classmethod
    def from_env(cls) -> "DataForSEOClient":
        login = os.environ.get("DATAFORSEO_LOGIN", "")
        password = os.environ.get("DATAFORSEO_PASSWORD", "")
        if not login or not password:
            raise RuntimeError("DATAFORSEO_LOGIN and DATAFORSEO_PASSWORD are required")
        return cls(login, password)

    def check(self, url: str, location_code: int = 2840, language_code: str = "en") -> SerpCheck:
        payload = [{
            "keyword": f"site:{url}",
            "target": _target_from_url(url),
            "location_code": int(location_code),
            "language_code": language_code,
            "device": "desktop",
            "depth": 10,
            "tag": "dmca-indexation",
        }]
        token = base64.b64encode(f"{self.login}:{self.password}".encode()).decode()
        request = urllib.request.Request(
            DATAFORSEO_ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.load(response)
        if data.get("status_code") != 20000:
            raise RuntimeError(f"DataForSEO error {data.get('status_code')}: {data.get('status_message')}")
        task = (data.get("tasks") or [{}])[0]
        if task.get("status_code") == 40102:
            return SerpCheck(False, cost_usd=float(task.get("cost") or 0))
        if task.get("status_code") != 20000:
            raise RuntimeError(
                f"DataForSEO task error {task.get('status_code')}: {task.get('status_message')}"
            )
        matched = ""
        for result in task.get("result") or []:
            for item in result.get("items") or []:
                candidate = item.get("url") or ""
                if candidate and urls_equivalent(url, candidate):
                    matched = candidate
                    break
            if matched:
                break
        return SerpCheck(bool(matched), matched, float(task.get("cost") or 0))

    def page_health(self, url: str) -> PageHealth:
        payload = [{"url": url, "load_resources": False, "enable_javascript": False}]
        token = base64.b64encode(f"{self.login}:{self.password}".encode()).decode()
        request = urllib.request.Request(
            DATAFORSEO_ONPAGE_ENDPOINT,
            data=json.dumps(payload).encode(),
            headers={"Authorization": f"Basic {token}", "Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            data = json.load(response)
        if data.get("status_code") != 20000:
            raise RuntimeError(
                f"DataForSEO OnPage error {data.get('status_code')}: {data.get('status_message')}"
            )
        task = (data.get("tasks") or [{}])[0]
        if task.get("status_code") != 20000:
            raise RuntimeError(
                f"DataForSEO OnPage task error {task.get('status_code')}: {task.get('status_message')}"
            )
        result = (task.get("result") or [{}])[0]
        item = (result.get("items") or [{}])[0]
        status = item.get("status_code")
        status = int(status) if status is not None else None
        final_url = item.get("url") or url
        cost = float(task.get("cost") or 0)
        if status != 200:
            return PageHealth(status, False, final_url, f"http_{status}", cost)
        meta = item.get("meta") or {}
        robots = json.dumps(meta.get("robots") or "").lower()
        if "noindex" in robots:
            return PageHealth(status, False, final_url, "noindex", cost)
        canonical = meta.get("canonical") or ""
        if canonical and not urls_equivalent(canonical, final_url):
            return PageHealth(status, False, final_url, f"canonical_to:{canonical}", cost)
        return PageHealth(status, True, final_url, cost_usd=cost)


def ensure_indexation_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(INDEXATION_SCHEMA)
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(indexation_state)").fetchall()
    }
    additions = {
        "absence_confirmed_at": "TEXT",
        "location_code": "INTEGER NOT NULL DEFAULT 0",
        "language_code": "TEXT NOT NULL DEFAULT ''",
    }
    for name, definition in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE indexation_state ADD COLUMN {name} {definition}")
    if "deindexed_at" in columns:
        conn.execute(
            """
            UPDATE indexation_state
            SET state=CASE WHEN state='deindexed' THEN 'serp_absent_confirmed' ELSE state END,
                absence_confirmed_at=COALESCE(absence_confirmed_at, deindexed_at)
            WHERE state='deindexed' OR (absence_confirmed_at IS NULL AND deindexed_at IS NOT NULL)
            """
        )


def _eligible_urls(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT u.url, n.notice_id, n.search_domain
        FROM notices n
        JOIN notice_urls u ON u.notice_id = n.notice_id
        WHERE n.role = 'targeted'
          AND n.status = 'complete'
          AND u.kind = 'infringing'
          AND u.monitored = 1
        ORDER BY u.url, n.notice_id
        """
    ).fetchall()
    grouped = {}
    for row in rows:
        try:
            key = _url_key(row["url"])
        except (TypeError, ValueError):
            # Redacted or malformed publisher values are not checkable URLs.
            continue
        item = grouped.setdefault(
            key,
            {"url": row["url"], "notice_ids": set(), "domains": set()},
        )
        item["notice_ids"].add(int(row["notice_id"]))
        item["domains"].add(row["search_domain"])
    return [
        {
            "url": item["url"],
            "state_url": url_identity(item["url"]),
            "notice_ids": ",".join(str(value) for value in sorted(item["notice_ids"])),
            "domains": ",".join(sorted(item["domains"])),
        }
        for item in grouped.values()
    ]


def _insert_check(
    conn: sqlite3.Connection,
    *,
    url: str,
    checked_at: str,
    result: str,
    health: PageHealth,
    matched_url: str = "",
    cost_usd: float = 0.0,
    error: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO indexation_checks
        (url, checked_at, result, http_status, indexable, matched_url, cost_usd, error)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            url,
            checked_at,
            result,
            health.status,
            int(health.indexable),
            matched_url,
            float(cost_usd),
            error,
        ),
    )


def _upsert_initial(
    conn: sqlite3.Connection,
    url: str,
    now_iso: str,
    location_code: int,
    language_code: str,
) -> sqlite3.Row:
    conn.execute(
        """
        INSERT OR IGNORE INTO indexation_state
        (url, first_seen_at, location_code, language_code)
        VALUES (?, ?, ?, ?)
        """,
        (url, now_iso, int(location_code), language_code),
    )
    row = conn.execute("SELECT * FROM indexation_state WHERE url = ?", (url,)).fetchone()
    if (
        int(row["location_code"] or 0) not in {0, int(location_code)}
        or (row["language_code"] and row["language_code"] != language_code)
    ):
        conn.execute(
            """
            UPDATE indexation_state
            SET state='unknown', last_checked_at=NULL, last_positive_at=NULL,
                first_absent_at=NULL, consecutive_absent=0, absence_confirmed_at=NULL,
                last_matched_url='', last_error='', alert_emitted_at=NULL,
                location_code=?, language_code=?
            WHERE url=?
            """,
            (int(location_code), language_code, url),
        )
    elif int(row["location_code"] or 0) == 0 or not row["language_code"]:
        conn.execute(
            "UPDATE indexation_state SET location_code=?, language_code=? WHERE url=?",
            (int(location_code), language_code, url),
        )
    return conn.execute("SELECT * FROM indexation_state WHERE url = ?", (url,)).fetchone()


def run_indexation_checks(
    db_path: str | Path,
    client,
    *,
    now: datetime | None = None,
    min_absence: timedelta = timedelta(hours=6),
    health_checker=check_page_health,
    location_code: int = 2840,
    language_code: str = "en",
) -> dict:
    """Check every unique DMCA-targeted URL and return transition alerts."""
    now = now or datetime.now(timezone.utc)
    now_iso = _utc_iso(now)
    conn = sqlite3.connect(db_path, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 60000")
    alerts: list[dict] = []
    checked = errors = 0
    total_cost = 0.0
    try:
        ensure_indexation_schema(conn)
        targets = _eligible_urls(conn)
        for target in targets:
            url = target["url"]
            state_url = target["state_url"]
            state = _upsert_initial(
                conn, state_url, now_iso, location_code=location_code, language_code=language_code
            )
            health = health_checker(url)
            checked += 1
            if (
                not health.indexable
                and (health.status in {401, 403, 429} or health.status is None)
                and hasattr(client, "page_health")
            ):
                try:
                    health = client.page_health(url)
                except Exception:
                    pass
            total_cost += health.cost_usd
            if not health.indexable:
                reason = health.reason or "page_not_indexable"
                _insert_check(
                    conn, url=state_url, checked_at=now_iso, result="technical", health=health, error=reason
                )
                conn.execute(
                    """
                    UPDATE indexation_state
                    SET state='technical', last_checked_at=?, last_http_status=?, last_indexable=0,
                        first_absent_at=NULL, consecutive_absent=0,
                        last_error=?, alert_emitted_at=NULL
                    WHERE url=?
                    """,
                    (now_iso, health.status, reason, state_url),
                )
                continue
            try:
                serp = client.check(
                    health.final_url or url,
                    location_code=location_code,
                    language_code=language_code,
                )
            except Exception as exc:  # API/network failures are observations, not absences.
                errors += 1
                message = f"{type(exc).__name__}: {exc}"
                _insert_check(
                    conn, url=state_url, checked_at=now_iso, result="error", health=health, error=message
                )
                conn.execute(
                    """
                    UPDATE indexation_state
                    SET last_checked_at=?, last_http_status=?, last_indexable=1, last_error=?
                    WHERE url=?
                    """,
                    (now_iso, health.status, message[:500], state_url),
                )
                continue

            total_cost += serp.cost_usd
            if serp.indexed:
                previous = state["state"]
                _insert_check(
                    conn,
                    url=state_url,
                    checked_at=now_iso,
                    result="indexed",
                    health=health,
                    matched_url=serp.matched_url,
                    cost_usd=serp.cost_usd,
                )
                conn.execute(
                    """
                    UPDATE indexation_state
                    SET state='indexed', last_checked_at=?, last_positive_at=?, first_absent_at=NULL,
                        consecutive_absent=0, absence_confirmed_at=NULL, last_http_status=?, last_indexable=1,
                        last_matched_url=?, last_cost_usd=?, last_error='', alert_emitted_at=NULL
                    WHERE url=?
                    """,
                    (
                        now_iso,
                        now_iso,
                        health.status,
                        serp.matched_url,
                        serp.cost_usd,
                        state_url,
                    ),
                )
                if previous == "serp_absent_confirmed":
                    alerts.append({"kind": "reindexed", "url": url, **target})
                continue

            _insert_check(
                conn,
                url=state_url,
                checked_at=now_iso,
                result="absent",
                health=health,
                cost_usd=serp.cost_usd,
            )
            last_positive = _parse_iso(state["last_positive_at"])
            first_absent = _parse_iso(state["first_absent_at"])
            previous_count = int(state["consecutive_absent"] or 0)
            if not last_positive:
                conn.execute(
                    """
                    UPDATE indexation_state
                    SET state='unknown', last_checked_at=?, first_absent_at=COALESCE(first_absent_at, ?),
                        consecutive_absent=?, last_http_status=?, last_indexable=1,
                        last_matched_url='', last_cost_usd=?, last_error=''
                    WHERE url=?
                    """,
                    (now_iso, now_iso, previous_count + 1, health.status, serp.cost_usd, state_url),
                )
                continue

            first_absent = first_absent or now
            count = previous_count + 1
            confirmed = count >= 2 and now - first_absent >= min_absence
            if confirmed:
                first_transition = state["state"] != "serp_absent_confirmed"
                conn.execute(
                    """
                    UPDATE indexation_state
                    SET state='serp_absent_confirmed', last_checked_at=?, first_absent_at=?, consecutive_absent=?,
                        absence_confirmed_at=COALESCE(absence_confirmed_at, ?), last_http_status=?, last_indexable=1,
                        last_matched_url='', last_cost_usd=?, last_error='',
                        alert_emitted_at=CASE WHEN state='serp_absent_confirmed' THEN alert_emitted_at ELSE ? END
                    WHERE url=?
                    """,
                    (
                        now_iso,
                        _utc_iso(first_absent),
                        count,
                        now_iso,
                        health.status,
                        serp.cost_usd,
                        now_iso,
                        state_url,
                    ),
                )
                if first_transition:
                    alerts.append({"kind": "likely_deindexed", "url": url, **target})
            else:
                conn.execute(
                    """
                    UPDATE indexation_state
                    SET state='suspect', last_checked_at=?, first_absent_at=?, consecutive_absent=?,
                        last_http_status=?, last_indexable=1, last_matched_url='',
                        last_cost_usd=?, last_error=''
                    WHERE url=?
                    """,
                    (
                        now_iso,
                        _utc_iso(first_absent),
                        count,
                        health.status,
                        serp.cost_usd,
                        state_url,
                    ),
                )
        cutoff = _utc_iso(now - timedelta(days=120))
        conn.execute("DELETE FROM indexation_checks WHERE checked_at < ?", (cutoff,))
        conn.commit()
    finally:
        conn.close()
    return {
        "targets": len(targets),
        "checked": checked,
        "errors": errors,
        "cost_usd": round(total_cost, 6),
        "alerts": alerts,
    }


def format_alerts(result: dict) -> str:
    lines = []
    for alert in result.get("alerts", []):
        notice_ids = alert.get("notice_ids") or "unknown"
        if alert["kind"] == "likely_deindexed":
            lines.extend([
                "🔴 PRIORITY — DMCA-reported page likely deindexed",
                alert["url"],
                f"Lumen notice(s): {notice_ids}",
                "No longer returned by two exact-URL DataForSEO checks at least 6 hours apart; the page still returns 200 and remains indexable.",
            ])
        elif alert["kind"] == "reindexed":
            lines.extend([
                "🟢 RECOVERED — DMCA-reported page is visible in Google again",
                alert["url"],
                f"Lumen notice(s): {notice_ids}",
            ])
        lines.append("")
    return "\n".join(lines).strip()
