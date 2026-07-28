#!/usr/bin/env python3
"""Run the Track A eval suite. Run this before every demo rehearsal.

Exit codes:
    0  no check failed (some may have skipped -- read the count line)
    1  at least one check failed

A skip is not a pass. The tenant currently holds noise rather than the seeded
demo data, so layer 2 and some negative controls will skip; the banner says how
many and why. Treating that as green is the failure mode this suite exists to
prevent.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from ghostthread.eval_suite import format_report, run_eval  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--json", action="store_true", help="emit the structured report instead"
    )
    args = parser.parse_args()

    report = run_eval()
    if args.json:
        print(json.dumps(report.to_dict(), indent=2, default=str))
    else:
        print(format_report(report))
    return 0 if report.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
