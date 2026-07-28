#!/usr/bin/env python3
"""Create the intent profile table in InsForge and push the local profile into it.

Run once at the start of the event. After this, the profile in InsForge is
authoritative and the local JSON file is only a fallback.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghostthread import config  # noqa: E402
from ghostthread.intent import push_profile  # noqa: E402


def headers() -> dict[str, str]:
    return {
        "X-API-Key": config.INSFORGE_API_KEY,
        "Authorization": f"Bearer {config.INSFORGE_API_KEY}",
        "Content-Type": "application/json",
    }


def create_table() -> None:
    url = f"{config.INSFORGE_BASE_URL.rstrip('/')}/api/database/tables"
    # The live API uses columnName/isNullable/isUnique, not the name/nullable/
    # unique spelling the published docs show. All three are required.
    body = {
        "tableName": config.INSFORGE_TABLE,
        "columns": [
            {"columnName": "key", "type": "string", "isNullable": False, "isUnique": True},
            {"columnName": "value", "type": "json", "isNullable": False, "isUnique": False},
        ],
    }
    resp = httpx.post(url, headers=headers(), json=body, timeout=20.0)
    if resp.status_code >= 400 and "already exists" not in resp.text:
        raise SystemExit(f"table creation failed: {resp.status_code} {resp.text}")
    print(f"table {config.INSFORGE_TABLE}: {'exists' if resp.status_code >= 400 else 'created'}")


def main() -> int:
    if not (config.INSFORGE_BASE_URL and config.INSFORGE_API_KEY):
        raise SystemExit("set INSFORGE_BASE_URL and INSFORGE_API_KEY in .env first")

    create_table()
    profile = json.loads(config.LOCAL_PROFILE_PATH.read_text())
    result = push_profile(profile)
    print("push:", result)
    if not result.get("insforge"):
        print("WARNING: profile was written locally but not to InsForge")
        return 1
    print("intent profile is now served from InsForge")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
