"""SQLite caching layer for DMCA report data."""

import sqlite3
from datetime import datetime, timezone

from src.models import DomainReport, NoticeDetail

DB_PATH = "dmca_data.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS domain_stats (
    domain          TEXT PRIMARY KEY,
    total_requested INTEGER NOT NULL DEFAULT 0,
    total_removed   INTEGER NOT NULL DEFAULT 0,
    no_action_taken INTEGER NOT NULL DEFAULT 0,
    duplicate       INTEGER NOT NULL DEFAULT 0,
    waiting         INTEGER NOT NULL DEFAULT 0,
    fetched_at      TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS notices (
    notice_id     TEXT NOT NULL,
    domain        TEXT NOT NULL,
    date          TEXT NOT NULL,
    urls_claimed  INTEGER NOT NULL DEFAULT 0,
    urls_removed  INTEGER NOT NULL DEFAULT 0,
    reporter_name TEXT NOT NULL DEFAULT '',
    owner_name    TEXT NOT NULL DEFAULT '',
    lumen_url     TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (notice_id, domain),
    FOREIGN KEY (domain) REFERENCES domain_stats(domain)
);

CREATE INDEX IF NOT EXISTS idx_notices_domain ON notices(domain);
"""


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.executescript(_SCHEMA)
    return conn


def save_domain_report(conn: sqlite3.Connection, report: DomainReport) -> None:
    if report.error:
        return
    now = datetime.now(timezone.utc).isoformat()
    with conn:
        conn.execute(
            "INSERT OR REPLACE INTO domain_stats "
            "(domain, total_requested, total_removed, no_action_taken, duplicate, waiting, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (report.domain, report.total_requested, report.total_removed,
             report.no_action_taken, report.duplicate, report.waiting, now),
        )
        conn.execute("DELETE FROM notices WHERE domain = ?", (report.domain,))
        conn.executemany(
            "INSERT INTO notices "
            "(notice_id, domain, date, urls_claimed, urls_removed, reporter_name, owner_name, lumen_url) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (n.notice_id, report.domain, n.date, n.urls_claimed, n.urls_removed,
                 n.reporter_name, n.owner_name, n.lumen_url)
                for n in report.notices
            ],
        )


def load_domain_report(conn: sqlite3.Connection, domain: str) -> DomainReport | None:
    row = conn.execute(
        "SELECT total_requested, total_removed, no_action_taken, duplicate, waiting "
        "FROM domain_stats WHERE domain = ?",
        (domain,),
    ).fetchone()
    if row is None:
        return None

    notices = [
        NoticeDetail(
            notice_id=r[0], date=r[1], urls_claimed=r[2], urls_removed=r[3],
            reporter_name=r[4], owner_name=r[5], lumen_url=r[6],
        )
        for r in conn.execute(
            "SELECT notice_id, date, urls_claimed, urls_removed, reporter_name, owner_name, lumen_url "
            "FROM notices WHERE domain = ? ORDER BY date DESC",
            (domain,),
        ).fetchall()
    ]

    return DomainReport(
        domain=domain,
        total_requested=row[0],
        total_removed=row[1],
        no_action_taken=row[2],
        duplicate=row[3],
        waiting=row[4],
        notices=notices,
    )


def is_stale(conn: sqlite3.Connection, domain: str, max_age_hours: int = 24) -> bool:
    row = conn.execute(
        "SELECT fetched_at FROM domain_stats WHERE domain = ?", (domain,),
    ).fetchone()
    if row is None:
        return True
    fetched_at = datetime.fromisoformat(row[0])
    age = datetime.now(timezone.utc) - fetched_at
    return age.total_seconds() > max_age_hours * 3600


def clear_all(conn: sqlite3.Connection) -> None:
    with conn:
        conn.execute("DELETE FROM notices")
        conn.execute("DELETE FROM domain_stats")
