#!/usr/bin/env python3
"""Phase A1: provision HydraDB's managed connectors for the TestTeam tenant.

Per connector the sequence is create -> discover -> configure -> sync, then
poll until every activated resource reports a provider_cursor, which is how
HydraDB says "this resource is synced and queryable".

Each connector gets its own sub_tenant_id (the provider name) and its own
provider_account_scope. The resulting connector_id is what the kill shot
filters on, so it is written to state/connectors.json for the pipeline to read.

Idempotent: re-running adopts existing connectors instead of duplicating them.

    .venv/bin/python scripts/setup_connectors.py
    .venv/bin/python scripts/setup_connectors.py --only slack,linear
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import hydra_db  # noqa: E402

from ghostthread import config  # noqa: E402

POLL_INTERVAL_SECONDS = 5.0
SYNC_TIMEOUT_SECONDS = 300.0


def credentials_for(provider: str) -> Optional[dict[str, Any]]:
    """Each provider spells its credential differently. Verified against the
    live API - github wants auth_token, gmail wants IMAP app-password creds."""
    if provider == "slack":
        return {"access_token": config.SLACK_TOKEN} if config.SLACK_TOKEN else None
    if provider == "linear":
        return {"access_token": config.LINEAR_TOKEN} if config.LINEAR_TOKEN else None
    if provider == "github":
        # Both keys are required, and not redundantly: create validates
        # access_token while discover reads auth_token. Send one and the other
        # step fails.
        if not config.GITHUB_TOKEN:
            return None
        return {"auth_token": config.GITHUB_TOKEN, "access_token": config.GITHUB_TOKEN}
    if provider == "gmail":
        if not (config.GMAIL_ADDRESS and config.GMAIL_APP_PASSWORD):
            return None
        return {"email": config.GMAIL_ADDRESS, "app_password": config.GMAIL_APP_PASSWORD}
    return None


def account_scope(provider: str) -> str:
    """Dedup key for objects from one provider account. Single-tenant today,
    but HydraDB uses it for deduplication so it is set deliberately."""
    if provider == "gmail":
        return config.GMAIL_ADDRESS or "testteam-gmail"
    return f"testteam-{provider}"


def usable_resources(provider: str, resources: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Drop resources we cannot actually read.

    Slack exposes every public channel through discovery, but history is only
    readable in channels the bot has joined. Configuring the rest guarantees a
    resource that never gets a cursor, which stalls the sync wait and leaves the
    connector permanently 'partial'.
    """
    if provider != "slack":
        return resources

    joined, skipped = [], []
    for r in resources:
        if (r.get("metadata") or {}).get("is_member"):
            joined.append(r)
        else:
            skipped.append(r.get("name") or r["id"])
    if skipped:
        print(f"  skipping {len(skipped)} channel(s) the bot has not joined: {', '.join(skipped)}")
        print("  (run /invite @GhostThread in those channels to include them)")
    return joined


def find_existing(client: hydra_db.HydraDB, provider: str) -> Optional[dict[str, Any]]:
    try:
        listing = client.connectors.list(provider=provider)
    except Exception:
        return None
    for item in listing.get("connectors") or []:
        if item.get("provider") == provider:
            return item
    return None


def _credentials_work(client: hydra_db.HydraDB, connector_id: Optional[str]) -> bool:
    if not connector_id:
        return False
    try:
        client.connectors.discover(connector_id, limit=1)
        return True
    except Exception:
        return False


def provision(client: hydra_db.HydraDB, provider: str) -> dict[str, Any]:
    creds = credentials_for(provider)
    if creds is None:
        return {"provider": provider, "status": "skipped", "reason": "no credentials configured"}

    existing = find_existing(client, provider)
    if existing and not _credentials_work(client, existing.get("connector_id")):
        # A connector created with a rejected credential persists in a broken
        # state and cannot be repaired by re-running create. Drop it.
        print(f"  existing connector {existing.get('connector_id')} has bad credentials, replacing")
        try:
            client.connectors.delete(existing["connector_id"])
        except Exception as exc:
            print(f"  could not delete: {str(exc)[-150:]}")
        existing = None

    if existing:
        connector_id = existing.get("connector_id")
        print(f"  adopting existing connector {connector_id}")
    else:
        created = client.connectors.create(
            provider=provider,
            credentials=creds,
            database=config.HYDRA_DATABASE,
            tenant_id=config.HYDRA_TENANT_ID,
            sub_tenant_id=provider,
            provider_account_scope=account_scope(provider),
            name=f"testteam-{provider}",
            sync_interval_seconds=900,
        )
        connector_id = created.connector_id
        print(f"  created connector {connector_id}")

    discovered = client.connectors.discover(connector_id, limit=100)
    resources = discovered.get("resources") or []
    print(f"  discovered {len(resources)} resource(s)")
    resources = usable_resources(provider, resources)
    if not resources:
        return {
            "provider": provider,
            "connector_id": connector_id,
            "status": "no_resources",
            "reason": "nothing readable to sync - check the account has content and access",
        }

    mapping = [
        {
            "resource_id": r["id"],
            "name": r.get("name") or r["id"],
            "resource_type": r.get("resource_type"),
            "sub_tenant_id": provider,
            "tenant_id": config.HYDRA_TENANT_ID,
            "database": config.HYDRA_DATABASE,
        }
        for r in resources
    ]
    client.connectors.configure(
        connector_id, resources=mapping, lookback_days=config.HYDRA_LOOKBACK_DAYS
    )
    print(f"  configured {len(mapping)} resource(s), lookback {config.HYDRA_LOOKBACK_DAYS}d")

    before = client.connectors.get(connector_id).last_successful_sync_at
    client.connectors.sync(connector_id)
    print("  sync triggered, waiting...")

    # provider_cursor is not a reliable readiness signal - it stays empty on a
    # perfectly successful Slack sync. A fresh last_successful_sync_at with the
    # connector back to idle is what actually means "done".
    deadline = time.time() + SYNC_TIMEOUT_SECONDS
    state = client.connectors.get(connector_id)
    while time.time() < deadline:
        state = client.connectors.get(connector_id)
        finished = state.last_successful_sync_at and state.last_successful_sync_at != before
        if finished and (state.sync_status or "").lower() in {"idle", "success", "completed", ""}:
            break
        if state.last_error:
            break
        time.sleep(POLL_INTERVAL_SECONDS)

    total = len(client.connectors.list_resources(connector_id).get("resources") or [])
    rows = _collection_rows(client, provider)
    status = "ready" if (state.last_successful_sync_at and not state.last_error) else "partial"
    print(f"  sync {status}: {rows} document(s) in the '{provider}' collection")
    if state.last_error:
        print(f"  last_error: {state.last_error}")

    return {
        "provider": provider,
        "connector_id": connector_id,
        "provider_account_scope": account_scope(provider),
        "sub_tenant_id": provider,
        "resources_total": total,
        "documents": rows,
        "last_successful_sync_at": state.last_successful_sync_at,
        "last_error": state.last_error,
        "status": status,
    }


def _collection_rows(client: hydra_db.HydraDB, collection: str) -> int:
    try:
        listing = client.context.list(
            database=config.HYDRA_DATABASE, collection=collection, type="knowledge", page_size=1
        )
        return int(getattr(listing.data, "total", 0) or 0)
    except Exception:
        return -1


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", help="comma-separated provider list")
    args = parser.parse_args()

    providers = ["slack", "gmail", "linear", "github"]
    if args.only:
        providers = [p.strip() for p in args.only.split(",") if p.strip()]

    client = hydra_db.HydraDB(token=config.HYDRA_TOKEN)
    results = []
    for provider in providers:
        print(f"\n=== {provider} ===")
        try:
            results.append(provision(client, provider))
        except Exception as exc:
            print(f"  FAILED: {str(exc)[-300:]}")
            results.append({"provider": provider, "status": "failed", "error": str(exc)[-300:]})

    config.CONNECTOR_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    state = {r["provider"]: r for r in results}
    config.CONNECTOR_STATE_PATH.write_text(json.dumps(state, indent=2) + "\n")

    print("\n=== summary ===")
    for r in results:
        detail = r.get("connector_id") or r.get("reason") or r.get("error", "")
        counts = ""
        if "resources_synced" in r:
            counts = f" [{r['resources_synced']}/{r['resources_total']} synced]"
        print(f"  {r['provider']:8s} {r['status']:12s} {detail}{counts}")

    ready = [r for r in results if r["status"] == "ready"]
    print(f"\n{len(ready)}/{len(results)} connectors ready -> {config.CONNECTOR_STATE_PATH}")
    # Two or more is the disqualification floor.
    return 0 if len(ready) >= 2 else 1


if __name__ == "__main__":
    raise SystemExit(main())
