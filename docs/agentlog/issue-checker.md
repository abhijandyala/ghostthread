# issue-checker log

Append newest entries at the bottom. See `docs/AGENT_WORKFLOW.md` for the entry
format and what `Verified` is allowed to mean.

## 2026-07-28 — A3 static review: memory.py, killshot.py, knowledge_query.py

**Task:** static review of the three A3 files on `track-a/a2-leak-detection`
(uncommitted working tree) against `main`. Read only; nothing run, nothing
edited except this log.

**Did:** read all three files plus `contracts.py`, `pipeline.py`, `api.py`,
`leaks.py`, `hydra.py`, `config.py`, `smoke.py`, `verify_no_hardcoding.py`,
`web/index.html`, `insforge/intent_profile.json` and `state/connectors.json`;
diffed the three files against `main`. Eleven confirmed defects, eight
suspected. The four that decide whether the demo survives:

1. `killshot.py` cannot tell an empty tenant from an unreachable one.
   `detect_leaks` returns `[]` for both, and `_degradation` then emits the
   positive claim "no complaint in this tenant resolves to work that lives only
   in an out-of-scope work source" with `degradation_observed: false`. A bad
   token produces a confident, fabricated null result on stage.
2. `knowledge_query.evaluate` derives `coverage` and `sources_used` from the
   *declared* provider scope while `search_work` silently narrows to
   `live_collections`. `state/connectors.json` records github with 0 documents,
   so github is never queried yet is counted as an agreeing source and reported
   as a used connector UUID. Leak confidence is inflated by a source that was
   not read.
3. `refresh_documents()` has zero call sites and `_collection_cache` is a single
   `"value"` key. `memory_write` creating the `memories` collection is invisible
   to `memory_read` for the life of the process, and `/reload` and `/complaint`
   leave the killshot on stale documents.
4. `DRY_RUN` defaults true and `.env` sets it true, so `memory_write` never
   writes. `memories` never exists, `memory_read` always returns zeros — while
   `IS_STUB=False` makes `smoke.py --demo-ready` report no stubs and
   `/run` report memory as a live backend.

Also confirmed: `/run` (N2 = `leaks.find_leaks`) and `/killshot`
(`detect_leaks`) now answer the same question through two engines and can
disagree; `detect_leaks_as_results` is dead. `memory_read` counts the current
complaint's own upserted memory as prior history on a re-run, and with
`reply_tone_thresholds.returning = 1` a first contact reads as "returning" the
second time the demo is run. The `provider not in scope` guard deleted from
`_parse_chunks` was the last defence against `graph_context=True` returning a
cross-collection document into a scoped run.

**Verified:** not run. This is a read-only review; no claim here was executed.
Findings 2 and 4 are traced through code and the on-disk connector/profile
state, not observed against the live tenant.

**Left undone:** no fixes applied. `qa-tester` should confirm (a) whether
`graph_context=True` can return a chunk whose `sub_tenant_id` is outside
`collections=[provider]`, (b) whether one memory ingest produces one chunk or
several, since `times_reported_by_actor` dedupes on chunk id rather than
`complaint_id`.

**For others:** `contracts.py` is untouched by A3 — confirmed clean against
`main`. No `metadata_filters` call site passes a list any more; `memory._fetch`
and `search_work` both pass scalars. But `killshot.py`'s module docstring still
documents the broken `{connector_id: [...]}` list form, which will invite
someone to put it back. `memory.py` reuses `_calibrate`, `_client`, `distil` and
`live_collections` from `knowledge_query` rather than duplicating them; the only
real duplication is chunk normalisation (`memory._fetch` vs
`knowledge_query._parse_chunks`). `verify_no_hardcoding.py` does cover
`memory.py` (`DECISION_MODULES` includes it) — `killshot.py` and
`knowledge_query.py` are the uncovered ones.
