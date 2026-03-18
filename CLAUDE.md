# DMCA Report Monitoring System

## What this project does
Fetches DMCA takedown data from Google's Transparency Report API for a list of domains, then outputs results as a console table, CSV files, or to Google Sheets.

## Project structure
- `main.py` — CLI entry point (dry-run, CSV export, Google Sheets modes)
- `src/transparency_api.py` — All API calls to Google Transparency Report (`domains/detail`, `requests/summary`, `requests/details`)
- `src/models.py` — `DomainReport` and `NoticeDetail` dataclasses
- `src/sheets_writer.py` — Google Sheets export via gspread
- `config/domains.txt` — One domain per line (lines starting with `#` are ignored)
- `config/settings.json` — Sheet ID, page size, rate limits, retry config
- `credentials/service_account.json` — Google service account key (not committed)

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

## Commands
```bash
# Install dependencies
pip install -r requirements.txt

# Dry-run (console output only)
python3 main.py --dry-run

# Export to CSV
python3 main.py --csv

# Write to Google Sheets (requires credentials + sheet ID in settings.json)
python3 main.py
```

## Key conventions
- All API responses have a `)]}'\n` anti-hijacking prefix that gets stripped before JSON parsing
- Lumen URLs are fetched via a second API call (`requests/details`), not constructed from the Google request ID
- Detail fetches use `ThreadPoolExecutor(max_workers=5)` with 0.3s delay per request for rate limiting
- `_safe_get()` is used throughout for safely traversing nested arrays
