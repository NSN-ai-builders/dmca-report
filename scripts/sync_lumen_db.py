#!/usr/bin/env python3
"""Build the canonical DMCA SQLite database and optionally sync it to lab-nsn."""

from __future__ import annotations

import argparse
import csv
import json
import os
import sqlite3
import subprocess
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

PROFILE = Path.home() / ".hermes" / "profiles" / "tank"
ACTIVE_SITES = PROFILE / "active-sites.csv"
PENDING = PROFILE / "dmca_pending_access.json"
STATE = PROFILE / "dmca_state.json"
DB_PATH = PROFILE / "dmca_monitor.db"
REMOTE_HOST = "deploy@46.225.135.201"
REMOTE_PATH = "/opt/lab-nsn/data/dmca-report/dmca_monitor.db"
LOOKBACK_DAYS = 90

SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sites (
    id INTEGER PRIMARY KEY,
    scope_url TEXT NOT NULL UNIQUE,
    search_domain TEXT NOT NULL,
    host TEXT NOT NULL,
    path TEXT NOT NULL,
    namespace TEXT NOT NULL DEFAULT '',
    language TEXT NOT NULL DEFAULT '',
    tech TEXT NOT NULL DEFAULT '',
    ownership TEXT NOT NULL DEFAULT '',
    white_label TEXT NOT NULL DEFAULT '',
    priority TEXT NOT NULL DEFAULT '',
    active INTEGER NOT NULL DEFAULT 1
);
CREATE INDEX IF NOT EXISTS idx_sites_search_domain ON sites(search_domain);
CREATE TABLE IF NOT EXISTS notices (
    notice_id INTEGER PRIMARY KEY,
    search_domain TEXT NOT NULL,
    notice_date TEXT,
    title TEXT NOT NULL DEFAULT 'DMCA',
    status TEXT NOT NULL,
    role TEXT NOT NULL,
    sender TEXT NOT NULL DEFAULT '',
    attempts INTEGER NOT NULL DEFAULT 0,
    discovered_at INTEGER,
    last_attempt_at INTEGER,
    submission_started_at INTEGER,
    captured_at INTEGER,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_notices_date ON notices(notice_date DESC);
CREATE INDEX IF NOT EXISTS idx_notices_role ON notices(role);
CREATE TABLE IF NOT EXISTS notice_urls (
    notice_id INTEGER NOT NULL,
    kind TEXT NOT NULL CHECK(kind IN ('original', 'infringing')),
    url TEXT NOT NULL,
    monitored INTEGER NOT NULL DEFAULT 0,
    scope_url TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (notice_id, kind, url),
    FOREIGN KEY (notice_id) REFERENCES notices(notice_id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_notice_urls_monitored ON notice_urls(monitored);
"""


def _normalize_domain(value: str) -> str:
    value = (value or "").strip().lower().strip(".")
    if "://" in value:
        value = (urlsplit(value).hostname or "").lower().strip(".")
    return value[4:] if value.startswith("www.") else value


def _normalize_scope(value: str) -> dict | None:
    value = (value or "").strip()
    if not value:
        return None
    parsed = urlsplit(value if "://" in value else "https://" + value)
    host = (parsed.hostname or "").lower().strip(".")
    if not host:
        return None
    path = parsed.path or "/"
    path = "/" + "/".join(part for part in path.split("/") if part)
    if path == "":
        path = "/"
    if path != "/":
        path = path.rstrip("/") + "/"
    return {"scope_url": f"https://{host}{path}", "host": host, "path": path}


def load_sites(path: Path | None = None) -> tuple[list[dict], dict[str, list[dict]]]:
    path = path or ACTIVE_SITES
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    sites = []
    by_domain: dict[str, list[dict]] = {}
    seen = set()
    for row in rows:
        if (row.get("active") or "").strip().lower() not in {"yes", "true", "1"}:
            continue
        scope = _normalize_scope(row.get("url", ""))
        domain = _normalize_domain(row.get("domain", ""))
        if not scope or not domain:
            continue
        key = (scope["host"], scope["path"])
        if key in seen:
            continue
        seen.add(key)
        site = {
            **scope,
            "search_domain": domain,
            "namespace": (row.get("namespace") or "").strip(),
            "language": (row.get("language") or "").strip(),
            "tech": (row.get("tech") or "").strip(),
            "ownership": (row.get("ownership") or "").strip(),
            "white_label": (row.get("white_label") or "").strip(),
            "priority": (row.get("priority") or "").strip(),
        }
        sites.append(site)
        by_domain.setdefault(domain, []).append(site)
    if not sites:
        raise RuntimeError("active-sites.csv contains no active site")
    return sites, by_domain


def _scope_match(url: str, scope: dict) -> bool:
    try:
        parsed = urlsplit(url)
    except ValueError:
        return False
    host = (parsed.hostname or "").lower().strip(".")
    scope_host = scope["host"].lower().strip(".")
    host_cmp = host[4:] if host.startswith("www.") else host
    scope_cmp = scope_host[4:] if scope_host.startswith("www.") else scope_host
    if host_cmp != scope_cmp and not (scope["path"] == "/" and host_cmp.endswith("." + scope_cmp)):
        return False
    if scope["path"] == "/":
        return True
    path = (parsed.path or "/").rstrip("/") + "/"
    return path.startswith(scope["path"])


def _matching_scope(url: str, scopes: list[dict]) -> str:
    for scope in scopes:
        if _scope_match(url, scope):
            return scope.get("scope_url") or scope.get("url") or ""
    return ""


def _iso_date(value: str | None) -> str | None:
    if not value:
        return None
    for fmt in ("%B %d, %Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(value, fmt).date().isoformat()
        except ValueError:
            continue
    return value


def utc_today():
    return datetime.now(timezone.utc).date()


def _in_lookback_window(item: dict, cutoff) -> bool:
    value = _iso_date(item.get("date"))
    if not value:
        return True
    try:
        return datetime.strptime(value, "%Y-%m-%d").date() >= cutoff
    except ValueError:
        return True


def _payload(item: dict, by_domain: dict[str, list[dict]]) -> tuple[dict, list[dict]]:
    result = item.get("last_result") if isinstance(item.get("last_result"), dict) else {}
    originals = item.get("original_urls") or result.get("original_urls") or []
    infringing = item.get("infringing_urls") or result.get("infringing_urls") or []
    sender = item.get("sender") or result.get("sender") or ""
    scopes = item.get("scopes") or by_domain.get(_normalize_domain(item.get("domain", "")), [])

    url_rows = []
    targeted = False
    source = False
    for kind, values in (("original", originals), ("infringing", infringing)):
        for url in dict.fromkeys(values):
            scope_url = _matching_scope(url, scopes)
            monitored = bool(scope_url)
            targeted = targeted or (kind == "infringing" and monitored)
            source = source or (kind == "original" and monitored)
            url_rows.append({
                "kind": kind, "url": url, "monitored": int(monitored), "scope_url": scope_url,
            })
    status = item.get("status") or "pending"
    if targeted:
        role = "targeted"
    elif source:
        role = "source"
    elif status != "complete":
        role = "unresolved"
    else:
        role = "other"
    notice = {
        "notice_id": int(item["id"]),
        "search_domain": _normalize_domain(item.get("domain", "")),
        "notice_date": _iso_date(item.get("date")),
        "title": item.get("title") or "DMCA",
        "status": status,
        "role": role,
        "sender": sender,
        "attempts": int(item.get("attempts") or 0),
        "discovered_at": item.get("discovered_at"),
        "last_attempt_at": item.get("last_attempt_at"),
        "submission_started_at": item.get("submission_started_at"),
        "captured_at": item.get("captured_at") or result.get("captured_at"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    return notice, url_rows


def build_database(db_path: Path = DB_PATH) -> dict:
    sites, by_domain = load_sites()
    all_pending = json.loads(PENDING.read_text())
    cutoff = utc_today() - timedelta(days=LOOKBACK_DAYS)
    pending = [item for item in all_pending if _in_lookback_window(item, cutoff)]
    filtered_out = len(all_pending) - len(pending)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(SCHEMA)
        with conn:
            conn.execute("DELETE FROM sites")
            # The queue is canonical. Rebuild notices so aged-out rows cannot linger.
            conn.execute("DELETE FROM notices")
            conn.executemany(
                """
                INSERT INTO sites
                (scope_url, search_domain, host, path, namespace, language, tech,
                 ownership, white_label, priority, active)
                VALUES (:scope_url, :search_domain, :host, :path, :namespace, :language,
                        :tech, :ownership, :white_label, :priority, 1)
                """,
                sites,
            )
            for item in pending:
                notice, urls = _payload(item, by_domain)
                conn.execute(
                    """
                    INSERT INTO notices
                    (notice_id, search_domain, notice_date, title, status, role, sender,
                     attempts, discovered_at, last_attempt_at, submission_started_at,
                     captured_at, updated_at)
                    VALUES (:notice_id, :search_domain, :notice_date, :title, :status, :role,
                            :sender, :attempts, :discovered_at, :last_attempt_at,
                            :submission_started_at, :captured_at, :updated_at)
                    ON CONFLICT(notice_id) DO UPDATE SET
                      search_domain=excluded.search_domain, notice_date=excluded.notice_date,
                      title=excluded.title, status=excluded.status, role=excluded.role,
                      sender=excluded.sender, attempts=excluded.attempts,
                      discovered_at=excluded.discovered_at, last_attempt_at=excluded.last_attempt_at,
                      submission_started_at=excluded.submission_started_at,
                      captured_at=excluded.captured_at, updated_at=excluded.updated_at
                    """,
                    notice,
                )
                conn.execute("DELETE FROM notice_urls WHERE notice_id = ?", (notice["notice_id"],))
                conn.executemany(
                    """
                    INSERT INTO notice_urls (notice_id, kind, url, monitored, scope_url)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    [(notice["notice_id"], row["kind"], row["url"], row["monitored"], row["scope_url"])
                     for row in urls],
                )
            synced_at = datetime.now(timezone.utc).isoformat()
            metadata = {
                "synced_at": synced_at,
                "site_scopes": str(len(sites)),
                "search_domains": str(len(by_domain)),
                "baseline_domains": str(len(state)),
                "source": "lumen_direct",
                "lookback_days": str(LOOKBACK_DAYS),
                "cutoff_date": cutoff.isoformat(),
            }
            conn.executemany(
                "INSERT INTO metadata(key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                metadata.items(),
            )
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        row = conn.execute(
            "SELECT COUNT(*), SUM(role='targeted'), SUM(role='source'), "
            "SUM(role IN ('unresolved','other')) FROM notices"
        ).fetchone()
    finally:
        conn.close()
    return {
        "database": str(db_path), "notices": int(row[0] or 0),
        "targeted": int(row[1] or 0), "source": int(row[2] or 0),
        "unresolved": int(row[3] or 0), "site_scopes": len(sites),
        "search_domains": len(by_domain), "baseline_domains": len(state),
        "lookback_days": LOOKBACK_DAYS, "cutoff_date": cutoff.isoformat(),
        "filtered_out_of_window": filtered_out,
    }


def upload_database(db_path: Path = DB_PATH) -> None:
    if not db_path.exists():
        raise FileNotFoundError(db_path)
    fd, snapshot_name = tempfile.mkstemp(prefix="dmca-monitor-", suffix=".db")
    os.close(fd)
    snapshot = Path(snapshot_name)
    try:
        source = sqlite3.connect(db_path)
        target = sqlite3.connect(snapshot)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        remote_tmp = REMOTE_PATH + ".new"
        subprocess.run(["scp", "-q", str(snapshot), f"{REMOTE_HOST}:{remote_tmp}"], check=True)
        subprocess.run([
            "ssh", REMOTE_HOST,
            f"chmod 644 {remote_tmp} && mv {remote_tmp} {REMOTE_PATH}",
        ], check=True)
    finally:
        snapshot.unlink(missing_ok=True)


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--no-upload", action="store_true")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    result = build_database(args.db)
    if not args.no_upload:
        upload_database(args.db)
        result["uploaded"] = True
    else:
        result["uploaded"] = False
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
