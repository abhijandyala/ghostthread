#!/usr/bin/env python3
"""Provision GhostThread's two InsForge tables and push the local profile.

Run once at the start of the event. After this:

  * `intent_profiles` is authoritative for policy; the local JSON is a fallback.
  * `actions_log` is authoritative for idempotency. UNIQUE on `complaint_id` is
    the whole guarantee -- it is what makes a retried webhook safe across
    instances rather than only within one process.

Both steps are idempotent: an existing table is reported as existing, not
recreated. Neither step is fatal to the other, so a partially provisioned
project still gets whatever it can.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ghostthread import actions_log, config  # noqa: E402
from ghostthread.intent import push_profile  # noqa: E402

# The actions_log schema lives in the library, not here. A schema that only a
# script knows about is a schema the code cannot check itself against -- which is
# how the table went missing while the run report still claimed InsForge.
column = actions_log.column


def headers() -> dict[str, str]:
    return {
        "X-API-Key": config.INSFORGE_API_KEY,
        "Authorization": f"Bearer {config.INSFORGE_API_KEY}",
        "Content-Type": "application/json",
    }


PROFILE_COLUMNS = [
    column("key", "string", nullable=False, unique=True),
    column("value", "json", nullable=False),
]


def create_table(table: str, columns: list[dict]) -> bool:
    """True if the table exists afterwards. Never recreates an existing one."""
    url = f"{config.INSFORGE_BASE_URL.rstrip('/')}/api/database/tables"
    resp = httpx.post(
        url, headers=headers(), json={"tableName": table, "columns": columns}, timeout=20.0
    )
    if resp.is_success:
        print(f"table {table}: created ({len(columns)} columns)")
        return True
    if "already exists" in resp.text:
        print(f"table {table}: exists")
        return True
    print(f"table {table}: NOT created -- {resp.status_code} {resp.text[:300]}")
    return False


def create_actions_log() -> bool:
    """Provision N8's table, then read it back to prove it is actually usable.

    `provision()` tries the full schema and falls back to the two columns the
    guarantee actually needs. The read-back matters more than the create: a
    create that reports success and a table that answers 404 look identical from
    here, and that gap is what let the idempotency backend regress unnoticed.
    """
    created, detail = actions_log.provision()
    print(f"table {config.INSFORGE_ACTIONS_TABLE}: {detail}")
    if not created:
        return False

    result = actions_log.probe()
    if result.ok:
        print(f"table {config.INSFORGE_ACTIONS_TABLE}: readable, idempotency is durable")
        return True
    print(f"table {config.INSFORGE_ACTIONS_TABLE}: NOT readable -- {result.reason}")
    return False


def main() -> int:
    if not (config.INSFORGE_BASE_URL and config.INSFORGE_API_KEY):
        raise SystemExit("set INSFORGE_BASE_URL and INSFORGE_API_KEY in .env first")

    problems = 0

    if not create_table(config.INSFORGE_TABLE, PROFILE_COLUMNS):
        raise SystemExit("intent profile table could not be provisioned")

    if not create_actions_log():
        print(
            f"WARNING: {config.INSFORGE_ACTIONS_TABLE} was not provisioned. "
            f"Idempotency will fall back to the in-process log, which does not "
            f"survive a restart. The UI will show it as degraded."
        )
        problems += 1

    profile = json.loads(config.LOCAL_PROFILE_PATH.read_text())
    result = push_profile(profile)
    print("push:", result)
    if not result.get("insforge"):
        print("WARNING: profile was written locally but not to InsForge")
        problems += 1
    else:
        print("intent profile is now served from InsForge")

    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
