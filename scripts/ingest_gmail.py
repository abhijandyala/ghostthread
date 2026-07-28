#!/usr/bin/env python3
"""Pull Gmail into HydraDB over OAuth. Re-runnable; upserts by message id.

    .venv/bin/python scripts/ingest_gmail.py
    .venv/bin/python scripts/ingest_gmail.py --limit 50 --query "newer_than:7d"
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import hydra_db  # noqa: E402

from ghostthread import config, gmail_ingest  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=100)
    parser.add_argument("--query", help="Gmail search syntax, e.g. 'newer_than:7d'")
    args = parser.parse_args()

    result = gmail_ingest.ingest(limit=args.limit, query=args.query)
    print(f"ingested {result['ingested']} message(s)")
    if not result["ingested"]:
        print(f"  {result.get('reason', '')}")
        return 1

    client = hydra_db.HydraDB(token=config.HYDRA_TOKEN)
    print("waiting for indexing...")
    for _ in range(30):
        time.sleep(3)
        listing = client.context.list(
            database=config.HYDRA_DATABASE, collection="gmail", type="knowledge", page_size=1
        )
        total = int(getattr(listing.data, "total", 0) or 0)
        if total >= result["ingested"]:
            print(f"gmail collection now holds {total} document(s)")
            return 0
    print("timed out waiting for the index to catch up (documents were accepted)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
