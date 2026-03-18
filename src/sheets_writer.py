import logging
from collections import Counter

import gspread
from google.oauth2.service_account import Credentials

from src.models import DomainReport

logger = logging.getLogger(__name__)

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]


def _get_client(credentials_path: str) -> gspread.Client:
    creds = Credentials.from_service_account_file(credentials_path, scopes=SCOPES)
    return gspread.authorize(creds)


def _get_or_create_worksheet(
    spreadsheet: gspread.Spreadsheet, title: str, rows: int, cols: int
) -> gspread.Worksheet:
    try:
        ws = spreadsheet.worksheet(title)
        ws.clear()
    except gspread.WorksheetNotFound:
        ws = spreadsheet.add_worksheet(title=title, rows=rows, cols=cols)
    return ws


def _build_summary_rows(reports: list[DomainReport]) -> list[list]:
    header = [
        "Domain",
        "URLs Requested",
        "URLs Removed",
        "No Action Taken",
        "Duplicate",
        "Waiting",
        "# Notices",
        "Latest Notice Date",
        "Top Reporter",
        "Top Owner",
        "Status",
    ]
    rows = [header]
    for r in reports:
        if r.error:
            rows.append([r.domain, "", "", "", "", "", "", "", "", "", f"ERROR: {r.error}"])
            continue

        latest_date = ""
        top_reporter = ""
        top_owner = ""
        if r.notices:
            latest_date = r.notices[0].date
            reporters = Counter(n.reporter_name for n in r.notices)
            owners = Counter(n.owner_name for n in r.notices)
            top_reporter = reporters.most_common(1)[0][0] if reporters else ""
            top_owner = owners.most_common(1)[0][0] if owners else ""

        status = "OK" if r.total_requested > 0 or r.notices else "No DMCA"
        rows.append([
            r.domain,
            r.total_requested,
            r.total_removed,
            r.no_action_taken,
            r.duplicate,
            r.waiting,
            len(r.notices),
            latest_date,
            top_reporter,
            top_owner,
            status,
        ])
    return rows


def _build_detail_rows(reports: list[DomainReport]) -> list[list]:
    header = [
        "Domain",
        "Notice ID",
        "Date",
        "URLs Claimed",
        "URLs Removed",
        "Reporter",
        "Owner",
        "Lumen URL",
    ]
    rows = [header]
    for r in reports:
        for n in r.notices:
            rows.append([
                r.domain,
                n.notice_id,
                n.date,
                n.urls_claimed,
                n.urls_removed,
                n.reporter_name,
                n.owner_name,
                n.lumen_url,
            ])
    return rows


def write_to_sheets(
    reports: list[DomainReport],
    sheet_id: str,
    credentials_path: str,
    summary_sheet_name: str = "Summary",
    details_sheet_name: str = "Notice Details",
) -> str:
    """Write reports to Google Sheets. Returns the sheet URL."""
    client = _get_client(credentials_path)
    spreadsheet = client.open_by_key(sheet_id)

    summary_rows = _build_summary_rows(reports)
    detail_rows = _build_detail_rows(reports)

    # Summary sheet
    ws_summary = _get_or_create_worksheet(
        spreadsheet, summary_sheet_name, len(summary_rows) + 10, 11
    )
    ws_summary.update(summary_rows, value_input_option="USER_ENTERED")
    logger.info("Wrote %d rows to '%s'", len(summary_rows), summary_sheet_name)

    # Details sheet
    ws_details = _get_or_create_worksheet(
        spreadsheet, details_sheet_name, len(detail_rows) + 10, 8
    )
    ws_details.update(detail_rows, value_input_option="USER_ENTERED")
    logger.info("Wrote %d rows to '%s'", len(detail_rows), details_sheet_name)

    return f"https://docs.google.com/spreadsheets/d/{sheet_id}"
