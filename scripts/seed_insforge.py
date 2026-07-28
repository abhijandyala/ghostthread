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

from ghostthread import config  # noqa: E402
from ghostthread.intent import push_profile  # noqa: E402


def headers() -> dict[str, str]:
    return {
        "X-API-Key": config.INSFORGE_API_KEY,
        "Authorization": f"Bearer {config.INSFORGE_API_KEY}",
        "Content-Type": "application/json",
    }


def col(name: str, type_: str, nullable: bool = True, unique: bool = False) -> dict:
    # The live API uses columnName/isNullable/isUnique, not the name/nullable/
    # unique spelling the published docs show. All three keys are required.
    return {
        "columnName": name,
        "type": type_,
        "isNullable": nullable,
        "isUnique": unique,
    }


PROFILE_COLUMNS = [
    col("key", "string", nullable=False, unique=True),
    col("value", "json", nullable=False),
]

# Mirrors the PRD schema, and 1:1 onto ResolutionAction's field names -- see
# actions_log.row_from_resolution. `complaint_id` UNIQUE is the idempotency key
# and the only column the guarantee actually depends on; `resolution` carries the
# full outcome so a replay re-renders the original rather than recomputing it.
# The rest are flat mirrors so the memory dashboard can be plain SQL.
ACTIONS_COLUMNS = [
    col("complaint_id", "string", nullable=False, unique=True),
    col("received_at", "string"),
    col("source", "string"),
    col("actor_resolved", "string"),
    col("complaint_text", "string"),
    col("category", "string"),
    col("confidence", "float"),
    col("reply_tone", "string"),
    col("times_reported_actor", "integer"),
    col("times_seen_topic", "integer"),
    col("regression_evidence", "string"),
    col("verdict", "string"),
    col("verdict_confidence", "float"),
    col("actions_taken", "json"),
    col("ticket_url", "string"),
    col("pr_url", "string"),
    col("pr_confidence", "float"),
    col("reply_sent", "boolean"),
    col("escalated", "boolean"),
    col("cost_usd", "float"),
    col("latency_ms", "float"),
    col("dry_run", "boolean"),
    col("resolution", "json"),
]

# If the extended column types are rejected, this is the smallest table that
# still delivers the guarantee. actions_log.py writes the reduced shape on a
# schema rejection, so a project provisioned this way still dedupes correctly --
# it just loses the flat dashboard columns.
MINIMAL_ACTIONS_COLUMNS = [
    col("complaint_id", "string", nullable=False, unique=True),
    col("resolution", "json"),
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
    """Full schema first, then the minimal one the guarantee actually needs.

    The full schema uses column types beyond the string/json pair the live API is
    known to accept, so a rejection here is a real possibility rather than a
    theoretical one. Falling back to two columns keeps idempotency working; what
    is lost is only the dashboard's flat columns, and the retry says so.
    """
    if create_table(config.INSFORGE_ACTIONS_TABLE, ACTIONS_COLUMNS):
        return True
    print("  retrying with the minimal complaint_id + resolution schema")
    return create_table(config.INSFORGE_ACTIONS_TABLE, MINIMAL_ACTIONS_COLUMNS)


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
