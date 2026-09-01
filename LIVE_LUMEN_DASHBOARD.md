# Live Lumen DMCA dashboard

## Purpose

`dmca-report.labnsn.com` now reads the direct Lumen monitoring pipeline instead of Google Transparency Report. Google data is intentionally excluded because its publication lag makes it unsuitable for incident response.

The dashboard answers three questions:

1. Which NSN pages are named as allegedly infringing?
2. Which NSN pages are listed as the original source and copied elsewhere?
3. Which notices are incomplete and need recovery work?

## Data flow

```text
Lumen scanner + Scrapfly worker
  -> dmca_pending_access.json
  -> lumen_dmca_dashboard_sync.py
  -> ~/.hermes/profiles/tank/dmca_monitor.db
  -> atomic SCP upload
  -> /opt/lab-nsn/data/dmca-report/dmca_monitor.db
  -> Flask read-only dashboard
```

The access worker runs the database sync every 30 minutes. A failed sync returns a non-zero exit and produces a Telegram alert through the existing no-agent cron.

## SQLite model

- `sites`: the 129 active URL scopes from `active-sites.csv`, including partner path boundaries.
- `notices`: one durable row per Lumen notice, with status, sender and computed NSN role.
- `notice_urls`: original and allegedly infringing URLs, with an exact `monitored` flag and matched scope.
- `metadata`: sync time, fleet size and baseline coverage.

The database never stores inbox addresses, Scrapfly URLs, API keys or Lumen access tokens.

## Dashboard

- Action list first: targeted NSN pages and incomplete notices.
- Four factual counters: total, targeted, source, unresolved.
- Search and role/status filters.
- Expandable notice rows with exact original and infringing URLs.
- Responsive desktop/mobile layout.
- Read-only Flask endpoints: `/`, `/api/notices`, `/health`.
- Existing `auth.labnsn.com` protection is unchanged.

## Operations

Local database build without upload:

```bash
python3 scripts/sync_lumen_db.py --no-upload
```

Build and upload:

```bash
python3 scripts/sync_lumen_db.py
```

Run locally:

```bash
DMCA_DB_PATH=~/.hermes/profiles/tank/dmca_monitor.db \
  python3 -c "from src.server import create_app; create_app().run(port=8050)"
```

Tests:

```bash
python3 -m unittest discover -s tests -v
```

## Rollback

VPS source backup and image tags are created before deployment. Restore the previous image, recreate `lab-dmca-report` with the existing port and volume mapping, then verify `/health` through `127.0.0.1:3104`.
