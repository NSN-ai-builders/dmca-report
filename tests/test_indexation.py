import sqlite3
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from scripts import sync_lumen_db as sync
from src.indexation import (
    PageHealth,
    SerpCheck,
    ensure_indexation_schema,
    format_alerts,
    run_indexation_checks,
    urls_equivalent,
)


class FakeClient:
    def __init__(self, observations):
        self.observations = list(observations)
        self.calls = []

    def check(self, url, **kwargs):
        self.calls.append((url, kwargs))
        value = self.observations.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def healthy(url):
    return PageHealth(200, True, url)


class TestIndexationMonitor(unittest.TestCase):
    def build_db(self, root: Path) -> Path:
        db = root / "dmca.db"
        conn = sqlite3.connect(db)
        conn.executescript(sync.SCHEMA)
        conn.executemany(
            """
            INSERT INTO notices
            (notice_id, search_domain, notice_date, title, status, role, sender,
             attempts, discovered_at, last_attempt_at, submission_started_at,
             captured_at, updated_at)
            VALUES (?, 'bojoko.ca', '2026-09-01', 'DMCA', 'complete', 'targeted', '',
                    1, NULL, NULL, NULL, NULL, '2026-09-02T00:00:00+00:00')
            """,
            [(100,), (101,)],
        )
        conn.executemany(
            """
            INSERT INTO notice_urls(notice_id, kind, url, monitored, scope_url)
            VALUES (?, 'infringing', ?, 1, 'https://bojoko.ca/')
            """,
            [
                (100, "https://bojoko.ca/casino/flexepin"),
                (101, "https://www.bojoko.ca/casino/flexepin/#details"),
            ],
        )
        conn.commit()
        conn.close()
        return db

    def test_url_equivalence_accepts_www_and_trailing_slash_only(self):
        self.assertTrue(urls_equivalent(
            "https://bojoko.ca/casino/flexepin",
            "https://www.bojoko.ca/casino/flexepin/",
        ))
        self.assertFalse(urls_equivalent(
            "https://bojoko.ca/casino/flexepin",
            "https://bojoko.ca/casino/astropay/",
        ))

    def test_legacy_deindexed_state_is_migrated(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "legacy.db"
            conn = sqlite3.connect(db)
            conn.executescript(
                """
                CREATE TABLE indexation_state (
                    url TEXT PRIMARY KEY,
                    state TEXT NOT NULL DEFAULT 'unknown',
                    first_seen_at TEXT NOT NULL,
                    last_checked_at TEXT,
                    last_positive_at TEXT,
                    first_absent_at TEXT,
                    consecutive_absent INTEGER NOT NULL DEFAULT 0,
                    deindexed_at TEXT,
                    last_http_status INTEGER,
                    last_indexable INTEGER,
                    last_matched_url TEXT NOT NULL DEFAULT '',
                    last_cost_usd REAL NOT NULL DEFAULT 0,
                    last_error TEXT NOT NULL DEFAULT '',
                    alert_emitted_at TEXT
                );
                INSERT INTO indexation_state
                (url, state, first_seen_at, deindexed_at)
                VALUES ('https://bojoko.ca/page', 'deindexed', '2026-09-01T00:00:00+00:00',
                        '2026-09-02T06:00:00+00:00');
                """
            )
            ensure_indexation_schema(conn)
            row = conn.execute(
                "SELECT state, absence_confirmed_at, location_code, language_code "
                "FROM indexation_state"
            ).fetchone()
            conn.close()
            self.assertEqual(
                row,
                ("serp_absent_confirmed", "2026-09-02T06:00:00+00:00", 0, ""),
            )

    def test_unique_url_baseline_two_absences_and_one_shot_alert(self):
        with tempfile.TemporaryDirectory() as td:
            db = self.build_db(Path(td))
            t0 = datetime(2026, 9, 2, 0, tzinfo=timezone.utc)

            baseline = FakeClient([SerpCheck(True, "https://bojoko.ca/casino/flexepin/", 0.01)])
            result = run_indexation_checks(db, baseline, now=t0, health_checker=healthy)
            self.assertEqual(result["targets"], 1)
            self.assertEqual(len(baseline.calls), 1)
            self.assertEqual(result["alerts"], [])

            first = FakeClient([SerpCheck(False, cost_usd=0.01)])
            result = run_indexation_checks(
                db, first, now=t0 + timedelta(hours=6), health_checker=healthy
            )
            self.assertEqual(result["alerts"], [])

            second = FakeClient([SerpCheck(False, cost_usd=0.01)])
            result = run_indexation_checks(
                db, second, now=t0 + timedelta(hours=12), health_checker=healthy
            )
            self.assertEqual([item["kind"] for item in result["alerts"]], ["likely_deindexed"])
            self.assertEqual(result["alerts"][0]["notice_ids"], "100,101")
            self.assertIn("PRIORITY", format_alerts(result))

            third = FakeClient([SerpCheck(False, cost_usd=0.01)])
            result = run_indexation_checks(
                db, third, now=t0 + timedelta(hours=18), health_checker=healthy
            )
            self.assertEqual(result["alerts"], [])

            conn = sqlite3.connect(db)
            state = conn.execute(
                "SELECT state, consecutive_absent, last_positive_at FROM indexation_state"
            ).fetchone()
            checks = conn.execute("SELECT COUNT(*) FROM indexation_checks").fetchone()[0]
            conn.close()
            self.assertEqual(state[0], "serp_absent_confirmed")
            self.assertEqual(state[1], 3)
            self.assertIsNotNone(state[2])
            self.assertEqual(checks, 4)

    def test_never_indexed_url_does_not_alert(self):
        with tempfile.TemporaryDirectory() as td:
            db = self.build_db(Path(td))
            t0 = datetime(2026, 9, 2, 0, tzinfo=timezone.utc)
            for hours in (0, 6, 12):
                result = run_indexation_checks(
                    db,
                    FakeClient([SerpCheck(False, cost_usd=0.01)]),
                    now=t0 + timedelta(hours=hours),
                    health_checker=healthy,
                )
                self.assertEqual(result["alerts"], [])
            conn = sqlite3.connect(db)
            state = conn.execute("SELECT state FROM indexation_state").fetchone()[0]
            conn.close()
            self.assertEqual(state, "unknown")

    def test_redacted_monitored_url_is_skipped(self):
        with tempfile.TemporaryDirectory() as td:
            db = self.build_db(Path(td))
            conn = sqlite3.connect(db)
            conn.execute(
                """
                INSERT INTO notice_urls(notice_id, kind, url, monitored, scope_url)
                VALUES (100, 'infringing', 'https://d[redacted]o.co.pl', 1, 'https://bojoko.ca/')
                """
            )
            conn.commit()
            conn.close()
            client = FakeClient([SerpCheck(True, "https://bojoko.ca/casino/flexepin/", 0.01)])
            result = run_indexation_checks(
                db,
                client,
                now=datetime(2026, 9, 2, tzinfo=timezone.utc),
                health_checker=healthy,
            )
            self.assertEqual(result["targets"], 1)
            self.assertEqual(len(client.calls), 1)

    def test_non_indexable_page_skips_dataforseo_and_resets_streak(self):
        with tempfile.TemporaryDirectory() as td:
            db = self.build_db(Path(td))
            t0 = datetime(2026, 9, 2, 0, tzinfo=timezone.utc)
            run_indexation_checks(
                db,
                FakeClient([SerpCheck(True, "https://bojoko.ca/casino/flexepin/", 0.01)]),
                now=t0,
                health_checker=healthy,
            )
            run_indexation_checks(
                db,
                FakeClient([SerpCheck(False, cost_usd=0.01)]),
                now=t0 + timedelta(hours=6),
                health_checker=healthy,
            )
            client = FakeClient([])
            result = run_indexation_checks(
                db,
                client,
                now=t0 + timedelta(hours=12),
                health_checker=lambda url: PageHealth(404, False, url, "http_404"),
            )
            self.assertEqual(client.calls, [])
            self.assertEqual(result["alerts"], [])
            conn = sqlite3.connect(db)
            state = conn.execute(
                "SELECT state, consecutive_absent, first_absent_at FROM indexation_state"
            ).fetchone()
            conn.close()
            self.assertEqual(state, ("technical", 0, None))

    def test_api_error_does_not_count_as_absence(self):
        with tempfile.TemporaryDirectory() as td:
            db = self.build_db(Path(td))
            t0 = datetime(2026, 9, 2, 0, tzinfo=timezone.utc)
            run_indexation_checks(
                db,
                FakeClient([SerpCheck(True, "https://bojoko.ca/casino/flexepin/", 0.01)]),
                now=t0,
                health_checker=healthy,
            )
            result = run_indexation_checks(
                db,
                FakeClient([RuntimeError("provider down")]),
                now=t0 + timedelta(hours=6),
                health_checker=healthy,
            )
            self.assertEqual(result["errors"], 1)
            conn = sqlite3.connect(db)
            state = conn.execute(
                "SELECT state, consecutive_absent FROM indexation_state"
            ).fetchone()
            conn.close()
            self.assertEqual(state, ("indexed", 0))

    def test_indexation_state_survives_notice_rebuild(self):
        with tempfile.TemporaryDirectory() as td:
            db = self.build_db(Path(td))
            t0 = datetime(2026, 9, 2, 0, tzinfo=timezone.utc)
            run_indexation_checks(
                db,
                FakeClient([SerpCheck(True, "https://bojoko.ca/casino/flexepin/", 0.01)]),
                now=t0,
                health_checker=healthy,
            )
            conn = sqlite3.connect(db)
            conn.execute("DELETE FROM notices")
            remaining = conn.execute("SELECT COUNT(*) FROM indexation_state").fetchone()[0]
            conn.close()
            self.assertEqual(remaining, 1)

    def test_reindexation_emits_recovery_once(self):
        with tempfile.TemporaryDirectory() as td:
            db = self.build_db(Path(td))
            t0 = datetime(2026, 9, 2, 0, tzinfo=timezone.utc)
            observations = [
                (0, SerpCheck(True, "https://bojoko.ca/casino/flexepin/", 0.01)),
                (6, SerpCheck(False, cost_usd=0.01)),
                (12, SerpCheck(False, cost_usd=0.01)),
                (18, SerpCheck(True, "https://bojoko.ca/casino/flexepin/", 0.01)),
            ]
            final = None
            for hours, observation in observations:
                final = run_indexation_checks(
                    db,
                    FakeClient([observation]),
                    now=t0 + timedelta(hours=hours),
                    health_checker=healthy,
                )
            self.assertEqual([item["kind"] for item in final["alerts"]], ["reindexed"])
            self.assertIn("RECOVERED", format_alerts(final))


if __name__ == "__main__":
    unittest.main()
