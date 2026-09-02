#!/usr/bin/env python3
"""Check DMCA-reported NSN pages in Google via DataForSEO."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.indexation import DataForSEOClient, format_alerts, run_indexation_checks

DEFAULT_DB = Path.home() / ".hermes" / "profiles" / "tank" / "dmca_monitor.db"


def parse_args(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--location-code", type=int, default=2840)
    parser.add_argument("--language-code", default="en")
    parser.add_argument("--confirmation-hours", type=float, default=6.0)
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the complete run summary; default output is alert-only for cron delivery",
    )
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    result = run_indexation_checks(
        args.db,
        DataForSEOClient.from_env(),
        min_absence=timedelta(hours=args.confirmation_hours),
        location_code=args.location_code,
        language_code=args.language_code,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        alerts = format_alerts(result)
        if alerts:
            print(alerts)
        if result["errors"]:
            if alerts:
                print()
            print(
                f"🟡 DMCA indexation monitor incomplete — {result['errors']} "
                "DataForSEO error(s). No absence was counted for failed checks."
            )
    return 1 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
