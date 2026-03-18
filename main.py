#!/usr/bin/env python3
"""DMCA Report Monitoring System — CLI entry point."""

import argparse
import csv
import json
import logging
import os
import sys
import time

from src.models import DomainReport
from src.transparency_api import fetch_domain_report
from src.sheets_writer import write_to_sheets

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def load_domains(path: str) -> list[str]:
    domains = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#"):
                domains.append(line)
    return domains


def load_settings(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _date_to_period(date_str: str) -> str:
    """Convert YYYY-MM-DD to semester label like 'S1 2025' or 'S2 2025'."""
    if date_str == "N/A" or len(date_str) < 7:
        return "Unknown"
    year = date_str[:4]
    month = int(date_str[5:7])
    semester = "S1" if month <= 6 else "S2"
    return f"{semester} {year}"


def _year_stats(r: DomainReport, year: int) -> tuple[int, int]:
    """Sum claimed/removed for notices in a given year."""
    claimed = sum(n.urls_claimed for n in r.notices if n.date.startswith(str(year)))
    removed = sum(n.urls_removed for n in r.notices if n.date.startswith(str(year)))
    return claimed, removed


def print_dry_run(reports: list[DomainReport]):
    print(
        f"\n{'='*150}\n"
        f"{'Domain':<30} {'Requested':>10} {'Removed':>10} {'No Action':>10} {'Duplicate':>10} {'Waiting':>10} "
        f"{'2026 Clm':>10} {'2026 Rmv':>10} {'Notices':>8} {'Status'}\n"
        f"{'-'*150}"
    )
    for r in reports:
        if r.error:
            print(f"{r.domain:<30} {'':>10} {'':>10} {'':>10} {'':>10} {'':>10} {'':>10} {'':>10} {'':>8} ERROR: {r.error}")
        elif r.total_requested == 0 and not r.notices:
            print(f"{r.domain:<30} {'—':>10} {'—':>10} {'—':>10} {'—':>10} {'—':>10} {'—':>10} {'—':>10} {'—':>8} No DMCA")
        else:
            c26, r26 = _year_stats(r, 2026)
            print(
                f"{r.domain:<30} {r.total_requested:>10} {r.total_removed:>10} "
                f"{r.no_action_taken:>10} {r.duplicate:>10} {r.waiting:>10} "
                f"{c26:>10} {r26:>10} {len(r.notices):>8} OK"
            )
    print("=" * 150)

    # Print notice details for each domain
    for r in reports:
        if r.notices:
            print(f"\n--- {r.domain} ({len(r.notices)} notices) ---")
            for n in r.notices[:10]:
                print(
                    f"  {n.date}  claimed={n.urls_claimed:<6} removed={n.urls_removed:<6} "
                    f"reporter={n.reporter_name}  owner={n.owner_name}"
                )
            if len(r.notices) > 10:
                print(f"  ... and {len(r.notices) - 10} more notices")


def export_csv(reports: list[DomainReport], output_dir: str):
    """Export summary and notice details as CSV files."""
    from collections import Counter

    summary_path = os.path.join(output_dir, "dmca_summary.csv")
    details_path = os.path.join(output_dir, "dmca_details.csv")

    # Summary CSV
    with open(summary_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "Domain", "URLs Requested", "URLs Removed", "No Action Taken",
            "Duplicate", "Waiting", "2026 Claimed", "2026 Removed", "# Notices",
            "Latest Notice Date", "Top Reporter", "Top Owner", "Status",
            "Transparency Report URL",
        ])
        for r in reports:
            tr_url = f"https://transparencyreport.google.com/copyright/domains/{r.domain}?hl=fr"
            if r.error:
                w.writerow([r.domain, "", "", "", "", "", "", "", "", "", "", "", f"ERROR: {r.error}", tr_url])
                continue
            if r.total_requested == 0 and not r.notices:
                w.writerow([r.domain, 0, 0, 0, 0, 0, 0, 0, 0, "", "", "", "No DMCA", tr_url])
                continue

            c26, r26 = _year_stats(r, 2026)
            latest_date = r.notices[0].date if r.notices else ""
            reporters = Counter(n.reporter_name for n in r.notices)
            owners = Counter(n.owner_name for n in r.notices)
            top_reporter = reporters.most_common(1)[0][0] if reporters else ""
            top_owner = owners.most_common(1)[0][0] if owners else ""

            w.writerow([
                r.domain, r.total_requested, r.total_removed, r.no_action_taken,
                r.duplicate, r.waiting, c26, r26, len(r.notices),
                latest_date, top_reporter, top_owner, "OK", tr_url,
            ])
    logger.info("Summary CSV: %s", summary_path)

    # Details CSV — sorted by date with semester period column
    all_notices = []
    for r in reports:
        for n in r.notices:
            all_notices.append((r.domain, n))
    all_notices.sort(key=lambda x: x[1].date if x[1].date != "N/A" else "0000-00-00", reverse=True)

    with open(details_path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow([
            "Period", "Domain", "Notice ID", "Date", "URLs Claimed", "URLs Removed",
            "Reporter", "Owner", "Lumen URL",
        ])
        for domain, n in all_notices:
            period = _date_to_period(n.date)
            w.writerow([
                period, domain, n.notice_id, n.date, n.urls_claimed, n.urls_removed,
                n.reporter_name, n.owner_name, n.lumen_url,
            ])
    logger.info("Details CSV: %s", details_path)


def main():
    parser = argparse.ArgumentParser(description="DMCA Report Monitoring System")
    parser.add_argument(
        "--config", default="config/settings.json", help="Path to settings.json"
    )
    parser.add_argument(
        "--domains", default="config/domains.txt", help="Path to domains file"
    )
    parser.add_argument(
        "--credentials",
        default="credentials/service_account.json",
        help="Path to Google service account JSON key",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print results to console instead of writing to Google Sheets",
    )
    parser.add_argument(
        "--csv",
        action="store_true",
        help="Export results to CSV files",
    )
    args = parser.parse_args()

    settings = load_settings(args.config)
    domains = load_domains(args.domains)

    if not domains:
        logger.error("No domains found in %s", args.domains)
        sys.exit(1)

    logger.info("Loaded %d domains", len(domains))

    page_size = settings.get("requests_page_size", 100)
    delay = settings.get("rate_limit_delay_seconds", 1.5)
    max_retries = settings.get("max_retries", 3)

    reports: list[DomainReport] = []
    for i, domain in enumerate(domains, 1):
        logger.info("Processing %d/%d: %s ...", i, len(domains), domain)
        report = fetch_domain_report(
            domain, page_size=page_size, max_retries=max_retries
        )
        reports.append(report)

        if i < len(domains):
            time.sleep(delay)

    if args.csv:
        export_csv(reports, os.path.dirname(os.path.abspath(__file__)))
        print_dry_run(reports)
    elif args.dry_run:
        print_dry_run(reports)
    else:
        sheet_id = settings.get("google_sheet_id", "")
        if not sheet_id:
            logger.error("google_sheet_id is empty in settings.json")
            sys.exit(1)

        url = write_to_sheets(
            reports,
            sheet_id=sheet_id,
            credentials_path=args.credentials,
            summary_sheet_name=settings.get("summary_sheet_name", "Summary"),
            details_sheet_name=settings.get("details_sheet_name", "Notice Details"),
        )
        logger.info("Done! Sheet URL: %s", url)


if __name__ == "__main__":
    main()
