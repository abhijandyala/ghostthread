#!/usr/bin/env python3
"""Delete documents from the HydraDB tenant.

Needed because the tenant accumulates three kinds of junk during development:
fixture data from offline runs, smoke-test rows, and mutation-test artefacts.
Once the managed connectors are live, anything not placed there by a connector
is noise that will corrupt the leak verdicts.

    .venv/bin/python scripts/purge_tenant.py --seeded    # fixtures/smoke/mutation only
    .venv/bin/python scripts/purge_tenant.py --all       # everything, start clean
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import hydra_db  # noqa: E402

from ghostthread import config  # noqa: E402

COLLECTIONS = ["slack", "gmail", "linear", "github"]


def seeded_ids() -> set[str]:
    """Ids this repo has ever pushed by hand, derived from the corpus itself."""
    ids = {"smoke-slack-1", "MUT-1"}
    corpus_path = config.FIXTURES_DIR / "corpus.json"
    if corpus_path.exists():
        corpus = json.loads(corpus_path.read_text())
        ids |= {c["id"] for c in corpus.get("complaints", [])}
        ids |= {w["id"] for w in corpus.get("work", [])}
    return ids


def list_ids(client: hydra_db.HydraDB, collection: str) -> list[str]:
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
    parser.add_argument("--all", action="store_true", help="delete every document")
    parser.add_argument("--seeded", action="store_true", help="delete only hand-seeded documents")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    if not (args.all or args.seeded):
        parser.error("pass --all or --seeded")

    client = hydra_db.HydraDB(token=config.HYDRA_TOKEN)
    targets = seeded_ids()
    removed = 0

    for collection in COLLECTIONS:
        present = list_ids(client, collection)
        doomed = present if args.all else [i for i in present if i in targets]
        if not doomed:
            print(f"{collection:8s} nothing to delete ({len(present)} kept)")
            continue
        print(f"{collection:8s} deleting {len(doomed)}/{len(present)}")
        if args.dry_run:
            continue
        for doc_id in doomed:
            try:
                client.context.delete(
                    database=config.HYDRA_DATABASE, collection=collection, type="knowledge", ids=[doc_id]
                )
                removed += 1
            except Exception as exc:
                print(f"    {doc_id}: {str(exc)[-120:]}")

    stats = client.databases.stats(database=config.HYDRA_DATABASE)
    print(f"\ndeleted {removed}; {stats.data.knowledge_collection.row_count} row(s) remain")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
