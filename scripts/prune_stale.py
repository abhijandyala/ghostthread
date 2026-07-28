#!/usr/bin/env python3
"""Delete indexed documents the live connectors no longer return.

`purge_tenant.py --all` empties the tenant and forces a full re-ingest. That is
correct but blunt: it throws away documents that are still valid alongside the
ones that are not.

This prunes only what has gone stale. It asks each connector what it currently
returns, lists what HydraDB currently holds, and deletes the difference.

The failure it exists to fix
----------------------------
Retrieval asks for a handful of results per complaint. Every stale document in
the index is a candidate for those slots, and a stale one is dropped on the way
back out because `hydra._parse_results` cannot resolve it to a loaded work item.
So a polluted index does not merely add noise -- it silently starves the join,
and every complaint comes back as a leak with no evidence at all.

That is how this tenant reached 107 GitHub documents while the connector was
returning 6: `GITHUB_REPO` briefly pointed at a large public repository, 100 of
its issues were indexed, and they outranked the real ones from then on.

Memories are never touched. They live in their own collection, they are written
deliberately rather than synced, and nothing here can reach them.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import hydra_db  # noqa: E402

from ghostthread import config, connectors  # noqa: E402

COLLECTIONS = ["slack", "gmail", "linear", "github"]


def live_ids() -> dict[str, set[str]]:
    """What each connector returns right now. This is the source of truth."""
    live: dict[str, set[str]] = {c: set() for c in COLLECTIONS}
    for name, fetch in {**connectors.COMPLAINT_FETCHERS, **connectors.WORK_FETCHERS}.items():
        if name in live:
            live[name] = {e.id for e in fetch()}
    return live


def indexed_ids(client: hydra_db.HydraDB, collection: str) -> list[str]:
    out: list[str] = []
    page = 1
    while True:
        resp = client.context.list(
            database=config.HYDRA_DATABASE,
            collection=collection,
            type="knowledge",
            page=page,
            page_size=100,
            include_fields=["title"],
        )
        sources = getattr(resp.data, "sources", None) or []
        if not sources:
            break
        for s in sources:
            doc_id = s.get("id") if isinstance(s, dict) else getattr(s, "id", None)
            if doc_id:
                out.append(str(doc_id))
        if len(sources) < 100:
            break
        page += 1
    return out


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="report, delete nothing")
    args = parser.parse_args()

    if not config.HYDRA_TOKEN:
        raise SystemExit("HYDRA_TOKEN is not set; there is no tenant to prune")

    client = hydra_db.HydraDB(token=config.HYDRA_TOKEN)

    print("asking the connectors what is live...")
    live = live_ids()
    for name in COLLECTIONS:
        print(f"  {name:8s} {len(live[name]):4d} live")
    print()

    removed = 0
    for collection in COLLECTIONS:
        present = indexed_ids(client, collection)
        keep = live[collection]

        # An empty live set means the connector returned nothing at all. That is
        # far more likely to be a credential or network failure than a genuinely
        # empty source, and acting on it would delete the whole collection. Skip
        # rather than guess.
        if not keep:
            print(f"{collection:8s} SKIPPED - connector returned nothing ({len(present)} indexed)")
            continue

        stale = [i for i in present if i not in keep]
        if not stale:
            print(f"{collection:8s} clean ({len(present)} indexed, all live)")
            continue

        print(f"{collection:8s} {len(stale)} stale of {len(present)} indexed -> deleting")
        for doc_id in stale[:5]:
            print(f"           {doc_id}")
        if len(stale) > 5:
            print(f"           ... and {len(stale) - 5} more")

        if args.dry_run:
            continue

        for doc_id in stale:
            try:
                client.context.delete(
                    database=config.HYDRA_DATABASE,
                    collection=collection,
                    type="knowledge",
                    ids=[doc_id],
                )
                removed += 1
            except Exception as exc:
                print(f"           {doc_id}: {str(exc)[-120:]}")

    stats = client.databases.stats(database=config.HYDRA_DATABASE)
    rows = stats.data.knowledge_collection.row_count
    print()
    if args.dry_run:
        print(f"dry run: nothing deleted; {rows} row(s) currently indexed")
    else:
        print(f"deleted {removed}; {rows} row(s) remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
