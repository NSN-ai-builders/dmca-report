import json
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import requests

from src.models import NoticeDetail, DomainReport

logger = logging.getLogger(__name__)

BASE_URL = "https://transparencyreport.google.com/transparencyreport/api/v3/copyright"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json",
}


def _safe_get(data, *indices, default=None):
    """Safely traverse nested arrays/dicts without IndexError/KeyError."""
    current = data
    for idx in indices:
        try:
            current = current[idx]
        except (IndexError, KeyError, TypeError):
            return default
    return current


def _strip_prefix(text: str) -> str:
    """Strip the )]}' anti-hijacking prefix from Google API responses."""
    newline_pos = text.find("\n")
    if newline_pos != -1:
        return text[newline_pos + 1:]
    return text


def _fetch_raw(url: str, params: dict, max_retries: int = 3) -> list | None:
    """GET with retry on 429, strip prefix, parse JSON. Returns None on 400/404 (no DMCA data)."""
    for attempt in range(max_retries):
        resp = requests.get(url, params=params, headers=HEADERS, timeout=30)

        if resp.status_code in (400, 404):
            return None

        if resp.status_code == 429:
            wait = 2 ** (attempt + 1)
            logger.warning("Rate limited (429), retrying in %ds...", wait)
            time.sleep(wait)
            continue

        resp.raise_for_status()
        clean = _strip_prefix(resp.text)
        return json.loads(clean)

    raise Exception(f"Failed after {max_retries} retries (429)")


def _ts_to_date(ts) -> str:
    """Convert 13-digit ms timestamp to YYYY-MM-DD."""
    if ts is None:
        return "N/A"
    return datetime.fromtimestamp(int(ts) / 1000, tz=timezone.utc).strftime("%Y-%m-%d")


def fetch_domain_detail(domain: str, max_retries: int = 3) -> tuple[int, int, int, int, int]:
    """Fetch domain-level DMCA stats. Returns (requested, removed, duplicate, waiting, no_action_taken)."""
    url = f"{BASE_URL}/domains/detail"
    params = {"domain": domain}
    data = _fetch_raw(url, params, max_retries=max_retries)

    if data is None:
        return 0, 0, 0, 0, 0

    total_requested = int(_safe_get(data, 0, 2, 0, 2, default=0) or 0)
    total_removed = int(_safe_get(data, 0, 2, 0, 3, default=0) or 0)
    duplicate = int(_safe_get(data, 0, 2, 0, 5, default=0) or 0)
    waiting = int(_safe_get(data, 0, 2, 0, 6, default=0) or 0)
    no_action_taken = int(_safe_get(data, 0, 2, 0, 7, default=0) or 0)
    return total_requested, total_removed, duplicate, waiting, no_action_taken


def fetch_request_details(request_id: str, max_retries: int = 3) -> str:
    """Fetch the real Lumen URL for a Google Transparency Report request ID."""
    url = f"{BASE_URL}/requests/details"
    params = {"req": request_id}
    data = _fetch_raw(url, params, max_retries=max_retries)
    if data is None:
        return ""
    return str(_safe_get(data, 0, 1, default="") or "")


def _fetch_lumen_url_with_delay(request_id: str, max_retries: int = 3) -> tuple[str, str]:
    """Wrapper that adds a small delay for rate limiting. Returns (request_id, lumen_url)."""
    time.sleep(0.3)
    lumen_url = fetch_request_details(request_id, max_retries=max_retries)
    return request_id, lumen_url


def fetch_request_history(
    domain: str, page_size: int = 100, max_retries: int = 3
) -> list[NoticeDetail]:
    """Fetch individual DMCA notice history for a domain, including real Lumen URLs."""
    url = f"{BASE_URL}/requests/summary"
    params = {"domain": domain, "size": page_size}
    data = _fetch_raw(url, params, max_retries=max_retries)

    if data is None:
        return []

    items = _safe_get(data, 0, 1, default=[]) or []

    # Parse notice metadata from summary
    notice_data = []
    request_ids = []
    for item in items:
        notice_id = str(_safe_get(item, 0, default=""))
        notice_data.append({
            "notice_id": notice_id,
            "date": _ts_to_date(_safe_get(item, 1)),
            "urls_claimed": int(_safe_get(item, 3, default=0) or 0),
            "urls_removed": int(_safe_get(item, 4, default=0) or 0),
            "reporter_name": str(_safe_get(item, 2, 2, default="Unknown") or "Unknown"),
            "owner_name": str(_safe_get(item, 5, 2, default="Unknown") or "Unknown"),
        })
        if notice_id:
            request_ids.append(notice_id)

    # Fetch real Lumen URLs concurrently (max 5 workers)
    lumen_map: dict[str, str] = {}
    if request_ids:
        logger.info("Fetching Lumen URLs for %d notices (%s)...", len(request_ids), domain)
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(_fetch_lumen_url_with_delay, rid, max_retries): rid
                for rid in request_ids
            }
            for future in as_completed(futures):
                try:
                    rid, lumen_url = future.result()
                    lumen_map[rid] = lumen_url
                except Exception as e:
                    rid = futures[future]
                    logger.warning("Failed to fetch Lumen URL for request %s: %s", rid, e)
                    lumen_map[rid] = ""

    # Build NoticeDetail list
    notices = []
    for nd in notice_data:
        notices.append(
            NoticeDetail(
                notice_id=nd["notice_id"],
                date=nd["date"],
                urls_claimed=nd["urls_claimed"],
                urls_removed=nd["urls_removed"],
                reporter_name=nd["reporter_name"],
                owner_name=nd["owner_name"],
                lumen_url=lumen_map.get(nd["notice_id"], ""),
            )
        )
    return notices


def fetch_domain_report(
    domain: str, page_size: int = 100, max_retries: int = 3
) -> DomainReport:
    """Orchestrate both API calls and return a complete DomainReport."""
    report = DomainReport(domain=domain)
    try:
        (
            report.total_requested,
            report.total_removed,
            report.duplicate,
            report.waiting,
            report.no_action_taken,
        ) = fetch_domain_detail(domain, max_retries=max_retries)
        report.notices = fetch_request_history(
            domain, page_size=page_size, max_retries=max_retries
        )
    except Exception as e:
        logger.error("Error fetching %s: %s", domain, e)
        report.error = str(e)
    return report
