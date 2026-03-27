# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this project does
Fetches DMCA takedown data from Google's Transparency Report API for a list of domains, then outputs results as a console table, CSV files, HTML dashboard, or to Google Sheets.

## Commands
```bash
# Install dependencies (use venv in project root)
pip install -r requirements.txt

# Dry-run (console output only)
python3 main.py --dry-run

# Export to CSV (writes dmca_summary.csv + dmca_details.csv)
python3 main.py --csv

# Generate self-contained HTML dashboard
python3 main.py --dashboard --output dmca_dashboard.html

# Start interactive dashboard server (add/remove domains, trigger reports from browser)
python3 main.py --serve
# Opens at http://localhost:8050

# Write to Google Sheets (requires credentials/service_account.json + sheet ID in config/settings.json)
python3 main.py
```

No test suite exists. No linter is configured.

## Architecture

**Data flow:** `main.py` loads domains from `config/domains.txt`, calls `fetch_all_reports()` which fetches each domain sequentially (with rate-limit delays and SQLite caching), then dispatches to the chosen output mode.

**`fetch_all_reports()`** (in `main.py`) iterates domains, checks the SQLite cache first, and calls `fetch_domain_report()` for stale or missing entries. Accepts a `progress_cb(index, total, domain, cached)` callback for progress reporting.

**`fetch_domain_report()`** (in `src/transparency_api.py`) orchestrates two API calls per domain:
1. `domains/detail` — aggregate stats (total requested, removed, duplicate, waiting, no action)
2. `requests/summary` — per-notice metadata (reporter, owner, dates, URL counts)

For each notice returned by the summary, a third call to `requests/details` fetches the real Lumen database URL. These detail fetches run concurrently via `ThreadPoolExecutor(max_workers=5)` with a 0.3s per-request delay.

**Output modules** (`src/sheets_writer.py`, `src/dashboard.py`, and CSV logic in `main.py`) all consume `list[DomainReport]`.

**Interactive server** (`src/server.py`) is a Flask app started via `--serve`. It serves a live dashboard at `GET /` with domain management UI (add/remove domains, trigger report generation). Adding a domain automatically fetches its report before responding. Report generation for all domains runs in a background thread with progress polling via `GET /api/status`. The server is localhost-only (rejects non-127.0.0.1 requests).

### Server REST API

| Method | Path | Purpose |
|--------|------|---------|
| `GET /` | Live dashboard HTML with management UI |
| `GET /api/domains` | List domains from `config/domains.txt` |
| `POST /api/domains` | Add domain (validates, fetches initial report) |
| `DELETE /api/domains/<domain>` | Remove domain + cached data |
| `POST /api/generate` | Start full report generation (background thread) |
| `GET /api/status` | Poll generation progress |

## API data mapping (Google Transparency Report)

### `domains/detail` response — `data[0][2][0]`
| Index | Meaning |
|-------|---------|
| `[1]` | # of DMCA requests (notices) |
| `[2]` | Total URLs requested |
| `[3]` | URLs removed/deleted |
| `[5]` | Duplicate |
| `[6]` | Waiting |
| `[7]` | No action taken |

### `requests/summary` response — each item in `data[0][1]`
| Index | Meaning |
|-------|---------|
| `[0]` | Google request ID |
| `[1]` | Timestamp (ms) |
| `[2][2]` | Reporter name |
| `[3]` | URLs claimed |
| `[4]` | URLs removed |
| `[5][2]` | Owner name |

### `requests/details?req={id}` response — `data[0]`
| Index | Meaning |
|-------|---------|
| `[1]` | Real Lumen database URL |

## Key conventions
- All API responses have a `)]}'\n` anti-hijacking prefix that gets stripped before JSON parsing
- Lumen URLs are fetched via a second API call (`requests/details`), not constructed from the Google request ID
- `_safe_get()` is used throughout `src/transparency_api.py` for safely traversing nested arrays
- `config/settings.json` controls page size, rate limits, retry count, and Google Sheets names
- `config/domains.txt` — one domain per line; lines starting with `#` are ignored
- `credentials/service_account.json` — Google service account key (not committed)
- `src/dashboard.py` accepts `server_mode=True` to render management UI (add/remove domains, generate reports); static export omits it
- `_esc()` for HTML escaping, `_esc_js()` for JavaScript string escaping (prevents XSS in onclick handlers)
- `_csv_safe()` in `main.py` prefixes formula-triggering characters in CSV output
- Domain validation uses `_normalize_domain()` in `src/server.py` (regex + length check)
- File writes to `config/domains.txt` are atomic (write to temp file, then `os.replace`)
