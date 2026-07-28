# qa-tester log

Append newest entries at the bottom. See `docs/AGENT_WORKFLOW.md` for the entry
format and what `Verified` is allowed to mean.

## 2026-07-28 12:10 — A3 verification (memory.py, killshot.py, knowledge_query.py)

**Task:** run the standing suite against `track-a/a2-leak-detection` and verify five
claims: the connector_id filter is live, the kill shot degrades without caching,
memory read never fabricates, memory write respects `DRY_RUN`, degraded mode runs.

**Did:** ran the four standing commands, then wrote throwaway probes under `/tmp`
(nothing in the repo was edited except this log). Probes: filtered vs unfiltered
vs wrong-connector-id retrieval; raw `client.query` calls so swallowed exceptions
could not hide; repeated and interleaved kill-shot scopes from both cold and warm
caches; `memory_read` for a brand-new actor and for an actor present in the
tenant; `memory_write` under `DRY_RUN` with a before/after enumeration of the
`memories` collection; every credential blanked at runtime; `/health` and
`/complaint` against a local uvicorn.

**Verified (observed passing):**
- `scripts/smoke.py` 5/5, exit 0, 25.7s. `--demo-ready` 5/5, exit 0, 31.7s,
  "no stubs remain". `verify_no_hardcoding.py` 3/3, exit 0. `make killshot`
  exit 0: slack 0% answerable / 5 invisible, slack+gmail+linear 9 leaks F1 1.00,
  full 9 leaks F1 1.00.
- The connector_id filter is genuinely live. Filtered retrieval over Linear
  returns the 4 connector-synced documents; unfiltered returns 8 (the 4 AGE-*
  fixture copies as well). A bogus connector id returns 0 candidates, and the raw
  query confirms 0 chunks rather than a swallowed error. The list-valued filter
  that was fixed does reproduce: `{"connector_id": [linear_id, slack_id]}` -> 0
  chunks, each id alone -> 4 and 2. The fix is correct and was necessary.
- Kill shot degrades and does not cache. slack-only: 4 clusters, every verdict
  `unknown`, every confidence `None`. Repeated identical scopes are byte-identical
  (full x3, slack x2, no-github x2), interleaving changes nothing, and running
  slack-only first from a cold cache gives the same answer as running it warm.
- `memory.IS_STUB` is False. The actor filter is exact and does not invent:
  a brand-new address returns `times_reported_by_actor=0`; a known address
  returns 1. Blanking `HYDRA_TOKEN` at runtime returns all-zero counts and
  `first_contact` with no exception. `memory_write` under `DRY_RUN=True` returns
  `DRY-mem-<complaint_id>` and adds nothing to the tenant.
- Degraded mode runs: every credential blanked, pipeline completes in 5ms on the
  fixture corpus, `profile.origin` = `local:intent_profile.json`,
  `grounding.backend` = `local-tfidf`, `capability_report()` all false,
  `run_killshot` returns without raising.
- `/health` 200. `/complaint` returns 422 (not 500) on empty, short and missing
  text, and a full payload on real and on nonsense text.

**Found (detail in the report to the orchestrator):**
1. HydraDB writes bypass `DRY_RUN`. `HydraGrounding.ingest` has no gate, so API
   startup and every `/complaint` write knowledge documents into the shared
   tenant with `DRY_RUN=True`. My two `/complaint` calls created `live-a05f06d4`
   and `live-ca88c0aa` in the `slack` collection; I deleted both and confirmed
   slack is back to 4 documents and `make killshot` back to 9 leaks.
2. A first contact is currently reported as `returning`. The `memories`
   collection holds 4 leftover dev-probe records (`context.list(type="memory")`
   reports 0, so it looks empty but is not). A brand-new actor scored
   `times_seen_on_topic=2`, and with `reply_tone_thresholds.returning=1` the tone
   flips. One of the two hits ("probe A actor tom@graywater.io topic digest",
   0.748) is topically unrelated and still clears `semantic_floor=0.72`.
3. `killshot.py:318` hardcodes `"backends": {"grounding": "hydradb"}`. With no
   credential the table is all zeros and `degradation_observed` is False with a
   note saying the narrow scope reproduced the reference answer.
4. GitHub has no collection at all, so `search_work` reduces `[linear, github]`
   to `[linear]` and the per-connector fan-out is never exercised on the real
   scope. I exercised it over `[linear, slack]` instead.
5. Full-scope confidence counts GitHub as a source that agreed, via
   `coverage = len(work_scope)/2`, although GitHub is unreadable. The -0.37 mean
   confidence delta on the no-GitHub row is that constant, not measured evidence.
6. `pipeline.py:329` reports `memory` as the grounding backend, so degraded mode
   claims `memory: local-tfidf` when `memory.py` has no local path.

**Left undone:** flavour B of the kill shot (dropping GitHub nulls
`regression_evidence`) is unverifiable — GitHub has zero documents and no
collection, and no memory record carries `regression_ref`. No verdict can flip
from resolved to leak in this tenant because nothing resolves, so `false_leaks`
and `precision` are untested against real data. `/run`, `/killshot`, `/profile`
and the sandbox-fix allowlist path were not exercised.

**For others:** `web/index.html` was deleted at 12:06 during this session. I did
not delete it and found no code path that does — `api.py:112` only reads it.
Likely a concurrent agent mid-write; worth confirming before a rehearsal. The
tenant is otherwise in the state I found it in.
