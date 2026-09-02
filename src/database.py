"""Read-only query layer for the live Lumen DMCA SQLite database."""

from __future__ import annotations

import os
import sqlite3
from collections import defaultdict
from pathlib import Path
from urllib.parse import urlsplit

from src.indexation import url_identity

DB_PATH = os.environ.get("DMCA_DB_PATH", "/app/data/dmca_monitor.db")


def _connect(db_path: str = DB_PATH) -> sqlite3.Connection:
    path = Path(db_path).resolve()
    if not path.exists():
        raise FileNotFoundError(path)
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def _metadata(conn: sqlite3.Connection) -> dict[str, str]:
    try:
        rows = conn.execute("SELECT key, value FROM metadata").fetchall()
    except sqlite3.OperationalError:
        return {}
    return {row["key"]: row["value"] for row in rows}


def load_dashboard_data(db_path: str = DB_PATH) -> dict:
    """Return the complete dashboard payload from a consistent read transaction."""
    conn = _connect(db_path)
    try:
        conn.execute("BEGIN")
        metadata = _metadata(conn)
        notice_rows = conn.execute(
            """
            SELECT notice_id, search_domain, notice_date, title, status, role,
                   sender, attempts, discovered_at, captured_at, updated_at
            FROM notices
            ORDER BY COALESCE(notice_date, '') DESC, notice_id DESC
            """
        ).fetchall()
        url_rows = conn.execute(
            """
            SELECT notice_id, kind, url, monitored, scope_url
            FROM notice_urls
            ORDER BY notice_id DESC, kind, url
            """
        ).fetchall()
        try:
            index_rows = conn.execute(
                """
                SELECT url, state, last_checked_at, last_positive_at, first_absent_at,
                       consecutive_absent, absence_confirmed_at, last_http_status, last_indexable,
                       last_matched_url, last_error
                FROM indexation_state
                """
            ).fetchall()
        except sqlite3.OperationalError:
            index_rows = []
        site_count = conn.execute("SELECT COUNT(*) FROM sites WHERE active = 1").fetchone()[0]
        domain_count = conn.execute(
            "SELECT COUNT(DISTINCT search_domain) FROM sites WHERE active = 1"
        ).fetchone()[0]
        conn.commit()
    finally:
        conn.close()

    index_by_url = {row["url"]: dict(row) for row in index_rows}
    urls_by_notice: dict[int, list[dict]] = defaultdict(list)
    for row in url_rows:
        identity = ""
        if row["monitored"] and row["kind"] == "infringing":
            try:
                identity = url_identity(row["url"])
            except (TypeError, ValueError):
                # Lumen uses bracketed redaction markers in some published URLs.
                pass
        indexation = index_by_url.get(identity, {})
        urls_by_notice[int(row["notice_id"])].append({
            "kind": row["kind"],
            "url": row["url"],
            "monitored": bool(row["monitored"]),
            "scope_url": row["scope_url"] or "",
            "indexation_state": indexation.get("state", "unknown"),
            "indexation_last_checked_at": indexation.get("last_checked_at"),
            "indexation_absence_confirmed_at": indexation.get("absence_confirmed_at"),
            "indexation_last_error": indexation.get("last_error", ""),
        })

    notices = []
    for row in notice_rows:
        notice_id = int(row["notice_id"])
        urls = urls_by_notice.get(notice_id, [])
        monitored_urls = [item["url"] for item in urls if item["monitored"]]
        monitored_infringing = [
            item for item in urls if item["monitored"] and item["kind"] == "infringing"
        ]
        severity = {"serp_absent_confirmed": 5, "suspect": 4, "technical": 3, "indexed": 2, "unknown": 1}
        indexation_state = max(
            (item["indexation_state"] for item in monitored_infringing),
            key=lambda value: severity.get(value, 0),
            default="unknown",
        )
        matched_scopes = list(dict.fromkeys(
            item["scope_url"] for item in urls if item["monitored"] and item["scope_url"]
        ))
        matched_domains = list(dict.fromkeys(
            (urlsplit(scope).hostname or "").removeprefix("www.") for scope in matched_scopes
        ))
        query_domain = row["search_domain"]
        display_domain = ", ".join(domain for domain in matched_domains if domain) or query_domain
        notices.append({
            "notice_id": notice_id,
            "domain": display_domain,
            "query_domain": query_domain,
            "matched_scopes": matched_scopes,
            "date": row["notice_date"] or "",
            "title": row["title"] or "DMCA",
            "status": row["status"],
            "role": row["role"],
            "sender": row["sender"] or "",
            "attempts": int(row["attempts"] or 0),
            "discovered_at": row["discovered_at"],
            "captured_at": row["captured_at"],
            "updated_at": row["updated_at"],
            "monitored_urls": monitored_urls,
            "indexation_state": indexation_state,
            "indexation_urls": monitored_infringing,
            "original_urls": [item for item in urls if item["kind"] == "original"],
            "infringing_urls": [item for item in urls if item["kind"] == "infringing"],
        })

    deindexed_urls = {
        item["url"]
        for items in urls_by_notice.values()
        for item in items
        if item["monitored"]
        and item["kind"] == "infringing"
        and item["indexation_state"] == "serp_absent_confirmed"
    }
    summary = {
        "total_notices": len(notices),
        "targeted": sum(item["role"] == "targeted" for item in notices),
        "source": sum(item["role"] == "source" for item in notices),
        "unresolved": sum(item["role"] in {"unresolved", "other"} for item in notices),
        "complete": sum(item["status"] == "complete" for item in notices),
        "likely_deindexed": len(deindexed_urls),
        "suspect_indexation": sum(item["indexation_state"] == "suspect" for item in notices),
        "site_scopes": site_count,
        "search_domains": domain_count,
        "baseline_domains": int(metadata.get("baseline_domains", "0") or 0),
    }
    return {"metadata": metadata, "summary": summary, "notices": notices}
