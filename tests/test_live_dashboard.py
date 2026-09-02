import csv
import json
import sqlite3
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest import mock

from scripts import sync_lumen_db as sync
from src.dashboard import render_dashboard
from src.database import load_dashboard_data
from src.server import create_app


class TestLiveDashboard(unittest.TestCase):
    def test_recently_retrieved_is_exhaustive(self):
        notices = []
        for notice_id in range(1, 11):
            notices.append({
                "notice_id": notice_id,
                "domain": "bojoko.ca",
                "query_domain": "bojoko.ca",
                "date": "2026-08-30",
                "status": "complete",
                "role": "targeted",
                "sender": "Sender",
                "attempts": 1,
                "captured_at": notice_id,
                "monitored_urls": ["https://bojoko.ca/page"],
                "original_urls": [],
                "infringing_urls": [],
            })
        html = render_dashboard({
            "notices": notices,
            "summary": {
                "total_notices": 10,
                "complete": 10,
                "targeted": 10,
                "source": 0,
                "unresolved": 0,
                "site_scopes": 1,
                "search_domains": 1,
                "baseline_domains": 1,
            },
            "metadata": {
                "lookback_days": 90,
                "cutoff_date": "2026-06-02",
                "synced_at": "2026-09-01T12:00:00+00:00",
            },
        })
        self.assertEqual(html.count("data-recent-id="), 10)
        self.assertLess(html.index('data-recent-id="10"'), html.index('data-recent-id="1"'))
        self.assertIn("All 10 retrieved notices", html)

    def build_fixture(self, root: Path) -> Path:
        sites = root / "active-sites.csv"
        with sites.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=[
                "url", "namespace", "domain", "language", "tech", "ownership",
                "white_label", "priority", "active",
            ])
            writer.writeheader()
            writer.writerows([
                {"url": "https://bojoko.ca/", "namespace": "bojoko-ca", "domain": "bojoko.ca",
                 "ownership": "NSN website", "active": "yes"},
                {"url": "https://www.partner.com/betting/", "namespace": "partner-bet",
                 "domain": "partner.com", "ownership": "NSN website", "white_label": "Partner",
                 "active": "yes"},
            ])
        pending = root / "pending.json"
        pending.write_text(json.dumps([
            {
                "id": 1, "domain": "bojoko.co.za", "date": "August 30, 2026",
                "status": "complete", "attempts": 1, "captured_at": 1788170400,
                "scopes": [{"url": "https://bojoko.ca/", "host": "bojoko.ca", "path": "/"}],
                "sender": "Sender One", "original_urls": ["https://source.example/work"],
                "infringing_urls": ["https://bojoko.ca/casino/page"],
                "email": "must-not-be-stored@example.invalid",
            },
            {
                "id": 2, "domain": "bojoko.ca", "date": "August 29, 2026",
                "status": "complete", "attempts": 1, "captured_at": 1788166800,
                "sender": "Sender Two", "original_urls": ["https://bojoko.ca/source/page"],
                "infringing_urls": ["https://copy.example/page", "https://d[redacted]o.co.pl"],
                "last_result": {"access_token": "MUST_NOT_BE_STORED"},
            },
            {
                "id": 3, "domain": "partner.com", "date": "August 28, 2026",
                "status": "token_consumption_error", "attempts": 1,
                "verified_scopes": ["https://www.partner.com/betting/"],
                "original_urls": [], "infringing_urls": [],
            },
            {
                "id": 4, "domain": "partner.com", "date": "August 27, 2026",
                "status": "complete", "attempts": 1,
                "original_urls": [], "infringing_urls": ["https://www.partner.com/news/not-ours"],
            },
            {
                "id": 5, "domain": "bojoko.ca", "date": "January 1, 2026",
                "status": "pending", "attempts": 0,
                "original_urls": [], "infringing_urls": [],
            },
        ]))
        state = root / "state.json"
        state.write_text(json.dumps({"bojoko.ca": {"ids": [1, 2]}, "partner.com": {"ids": [3, 4]}}))
        db = root / "dmca.db"
        with mock.patch.object(sync, "ACTIVE_SITES", sites), \
             mock.patch.object(sync, "PENDING", pending), \
             mock.patch.object(sync, "STATE", state), \
             mock.patch.object(sync, "utc_today", return_value=date(2026, 8, 31)):
            result = sync.build_database(db)
        self.assertEqual(result["notices"], 3)
        self.assertEqual(result["filtered_out_of_window"], 1)
        self.assertEqual(result["filtered_unverified"], 1)
        return db

    def test_ingestion_roles_scopes_and_secret_exclusion(self):
        with tempfile.TemporaryDirectory() as td:
            db = self.build_fixture(Path(td))
            data = load_dashboard_data(str(db))
            by_id = {item["notice_id"]: item for item in data["notices"]}
            self.assertEqual(by_id[1]["role"], "targeted")
            self.assertEqual(by_id[1]["domain"], "bojoko.ca")
            self.assertEqual(by_id[1]["query_domain"], "bojoko.co.za")
            self.assertEqual(by_id[2]["role"], "source")
            self.assertEqual(by_id[3]["role"], "unresolved")
            self.assertNotIn(4, by_id)
            self.assertEqual(data["summary"]["site_scopes"], 2)
            self.assertEqual(data["summary"]["search_domains"], 2)
            raw = db.read_bytes()
            self.assertNotIn(b"must-not-be-stored", raw)
            self.assertNotIn(b"MUST_NOT_BE_STORED", raw)

    def test_dashboard_and_health(self):
        with tempfile.TemporaryDirectory() as td:
            db = self.build_fixture(Path(td))
            app = create_app(str(db))
            app.config.update(TESTING=True)
            client = app.test_client()
            page = client.get("/")
            self.assertEqual(page.status_code, 200)
            html = page.get_data(as_text=True)
            self.assertIn("Lumen notices", html)
            self.assertIn("NSN page targeted", html)
            self.assertIn('<html lang="en">', html)
            self.assertIn("30 August 2026", html)
            self.assertIn('href="https://lumendatabase.org/notices/1"', html)
            self.assertIn("View notice", html)
            self.assertIn("Last 90 days", html)
            self.assertIn("Notices in last 90 days", html)
            self.assertIn("Recently retrieved", html)
            self.assertIn('data-recent-id="1"', html)
            self.assertIn('data-recent-id="2"', html)
            self.assertLess(html.index('data-recent-id="1"'), html.index('data-recent-id="2"'))
            self.assertIn('id="domain-filter"', html)
            self.assertIn('<option value="bojoko.ca">bojoko.ca (2)</option>', html)
            self.assertIn('data-domain="bojoko.ca"', html)
            self.assertIn("row.dataset.domain===domain.value", html)
            self.assertIn("Sorted by notice date, not retrieval time", html)
            self.assertIn("fetch('/health'", html)
            self.assertIn("Auto-refreshes when new data lands", html)
            self.assertLess(html.index("30 August 2026"), html.index("29 August 2026"))
            self.assertEqual(page.headers["X-Frame-Options"], "DENY")
            health = client.get("/health")
            self.assertEqual(health.status_code, 200)
            self.assertEqual(health.get_json()["notices"], 3)
            self.assertEqual(health.get_json()["likely_deindexed"], 0)
            self.assertEqual(health.get_json()["site_scopes"], 2)

    def test_deindexation_alert_is_prioritized(self):
        with tempfile.TemporaryDirectory() as td:
            db = self.build_fixture(Path(td))
            conn = sqlite3.connect(db)
            conn.execute(
                """
                INSERT INTO indexation_state
                (url, state, first_seen_at, last_checked_at, last_positive_at,
                 first_absent_at, consecutive_absent, absence_confirmed_at,
                 last_http_status, last_indexable, alert_emitted_at)
                VALUES (?, 'serp_absent_confirmed', ?, ?, ?, ?, 2, ?, 200, 1, ?)
                """,
                (
                    "https://bojoko.ca/casino/page",
                    "2026-09-01T00:00:00+00:00",
                    "2026-09-02T12:00:00+00:00",
                    "2026-09-02T00:00:00+00:00",
                    "2026-09-02T06:00:00+00:00",
                    "2026-09-02T12:00:00+00:00",
                    "2026-09-02T12:00:00+00:00",
                ),
            )
            conn.commit()
            conn.close()
            data = load_dashboard_data(str(db))
            self.assertEqual(data["summary"]["likely_deindexed"], 1)
            self.assertEqual(data["notices"][0]["indexation_state"], "serp_absent_confirmed")
            html = render_dashboard(data)
            self.assertIn("Priority deindexation alerts", html)
            self.assertIn("Google: likely deindexed", html)
            self.assertIn("two exact-URL Google checks at least six hours apart", html)

    def test_missing_database_is_503(self):
        with tempfile.TemporaryDirectory() as td:
            app = create_app(str(Path(td) / "missing.db"))
            app.config.update(TESTING=True)
            self.assertEqual(app.test_client().get("/").status_code, 503)


if __name__ == "__main__":
    unittest.main()
