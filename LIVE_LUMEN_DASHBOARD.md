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

## Priority indexation alerts

Only exact NSN URLs named as allegedly infringing are monitored. Duplicate URLs across notices count as one DataForSEO check.

The monitor uses two signals:

1. DataForSEO Google Organic Live Regular with `site:<exact URL>` and an exact `target`.
2. A page-health check for HTTP 200, no `noindex`, and a matching canonical. If the site blocks the local probe, DataForSEO OnPage Instant Pages provides the fallback.

The state machine is conservative:

```text
no baseline -> indexed -> suspect -> deindexed -> indexed
                 |          |            |
              present    1 absence    2 absences >= 6h apart
```

A red alert requires a prior positive baseline plus two absences at least six hours apart. API failures do not count as absences. A 404, `noindex`, or canonical change becomes a technical page issue instead of a DMCA deindexation alert.

The script prints only transitions, so a no-agent cron can deliver the output directly to Telegram without routine noise. The dashboard keeps the current state and 120 days of check history.

Current DataForSEO cost is $0.01 per URL check because search operators apply a 5x multiplier to the $0.002 live SERP price. With the current seven unique targeted URLs and a six-hour schedule, the estimate is $0.28/day or $8.40 per 30-day month. The OnPage fallback costs $0.00015 when needed.

Run a baseline manually:

```bash
DATAFORSEO_LOGIN='...' DATAFORSEO_PASSWORD='...' \
  .venv/bin/python scripts/check_indexation.py --json
```

Cron mode (silent unless an alert or provider error occurs):

```bash
DATAFORSEO_LOGIN='...' DATAFORSEO_PASSWORD='...' \
  .venv/bin/python scripts/check_indexation.py
```

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
