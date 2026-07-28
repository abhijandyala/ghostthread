# coder log

Append newest entries at the bottom. See `docs/AGENT_WORKFLOW.md` for the entry
format and what `Verified` is allowed to mean.

## 2026-07-28 11:52 — A3 kill shot on the HydraDB-native path

**Task:** rewrite `killshot.py` around `knowledge_query.detect_leaks`, producing
two distinct flavours of degradation scored against the full four-provider run.
Only file touched: `src/ghostthread/killshot.py`.

**Did:** dropped the `pipeline.GhostThread` dependency and the old
`leaked/actioned/unknown_insufficient_sources` vocabulary. The reference answer
is now `detect_leaks(["slack","gmail","linear","github"])`; every scope in
`DEFAULT_SCOPES` (`["slack"]`, `["slack","gmail","linear"]`, all four) is re-run
and scored against it. Scoring is indexed per complaint id rather than per
cluster id, because `_merge_clusters` folds resolved complaints together and a
cluster id is therefore not stable across scopes. Each row reports verdict
counts, leaks reported, answerable rate, false leaks (reported `leak` here but
`resolved` at full scope) with the complaint text named, misses split into
*invisible* (never ingested at this scope) and *unmatched*, precision/recall/F1,
the connector UUIDs in and out of scope from `ConnectorRegistry`, mean-confidence
delta, and wall-clock latency. `run_killshot(engine, scopes=...)` keeps working;
`engine` and `now` are accepted and ignored. No numeric literal appears in any
comparison (checked by AST over the file); the threshold comes from
`get_profile()` and is read once so all scopes share it.

**Verified:**
- `scripts/smoke.py` 5/5 PASS, exit 0 (memory_read/memory_write still stubbed).
- `scripts/verify_no_hardcoding.py` 3/3 PASS, exit 0.
- AST scan of `killshot.py`: no numeric literals in comparisons.
- `make killshot` and the `api.py` call site both run green.
- Live tenant, warm run: `slack` 7.5ms, all `unknown`, 0% answerable, refusal
  path (no work-side connector, so no retrieval happens at all).
  `slack+gmail+linear` 7283ms, 9 leaks, F1 1.00, mean confidence 0.361 against
  0.722 at full scope. Full scope 8297ms, 9 leaks.
- False-leak and invisible-miss arithmetic exercised directly against synthetic
  `LeakVerdict`s: a complaint resolved via GitHub at full scope is correctly
  named as a false leak when GitHub leaves scope (precision 0.50, recall 1.00).

**Left undone:**
- Flavour B is unverifiable against the live tenant: GitHub holds zero documents
  and nothing in the tenant resolves at all, so dropping GitHub cannot flip a
  `resolved` to a `leak`. The code path is proven synthetically only. This is a
  data gap — it needs the `SEED.md` GitHub issues.
- Latency misses the 2s-per-scope target for any scope that actually queries:
  5-8s for nine complaints. That is HydraDB per-key throttling of concurrent
  queries (already documented in `knowledge_query`'s docstring), not this file.
  Only the refusal scope is instant.
- `gmail` has no `connector_id` in `state/connectors.json`, so it never appears
  in `connector_ids_in_scope`. Reported as `providers_without_connector` rather
  than hidden.

**For others:** row keys the demo UI reads (`scope_label`, `answerable_rate`,
`leaks_reported`, `false_leaks`, `missed_invisible`, `f1`) and the top-level
`headline` are unchanged. New keys added: `by_verdict`, `refused`,
`mean_confidence_delta`, `*_detail`, `connector_ids_in_scope` /
`_out_of_scope`, `latency_ms`, plus top-level `degradation` and `elapsed_ms`.
Verdict strings are now `leak`/`resolved`/`unknown`, not the pipeline's
`leaked`/`actioned`. Document caching is per provider so a narrow scope loads
strictly fewer complaints; work-side retrieval is not cached, which the 5-8s
per-scope latency confirms — the scopes are genuinely re-queried.

## 2026-07-28 11:56 — A3 memory read/write on HydraDB

**Task:** replace the `memory_read` / `memory_write` stubs with real HydraDB
memory queries and flip `IS_STUB`. Only file touched:
`src/ghostthread/memory.py`. `derive_reply_tone` left byte-identical.

**Did:** both nodes now talk to the `memories` collection with `type="memory"`,
reusing `knowledge_query`'s `_client`, `distil`, `live_collections` and
`_calibrate` rather than duplicating them. `memory_read` resolves the actor from
`author_email or entity_id` and runs two families of query per complaint source
in scope: one filtered to `additional_metadata.actor`, whose hits count as
`times_reported_by_actor` because the metadata match is exact, and one open
across actors whose hits must clear `profile.semantic_floor` through
`_calibrate` to count as `times_seen_on_topic`. Passes run in a thread pool and
are unioned by chunk id. `prior_resolutions` and `likely_regression` come from
the recalled metadata only; a memory with neither `ticket_url` nor `resolved_at`
is counted as a prior contact but not reported as a prior resolution.
`memory_write` composes a `text` carrying both actor and topic (the read side
needs both handles in the body, not only in metadata), puts actor, complaint_id,
category, source, ticket_url, resolved_at, actions and summary in
`additional_metadata`, and ingests under a deterministic id `mem-<complaint_id>`
with `upsert="true"` so a replayed resolution updates one row instead of
inflating the counts the read reports. Under `DRY_RUN` it writes nothing and
returns `DRY-mem-<complaint_id>`, matching `act.py`'s `DRY-<id>` convention.
Every HydraDB call is wrapped; missing credential, absent collection, no
complaint source in scope and query failure all return honest zeros.

Scoping detail worth knowing: measured live, `metadata_filters` **does** work on
memory queries as a dict (the SDK's `str` type hint is wrong), multiple keys
AND together, but a *list* value means AND, not OR —
`{"source": ["slack","gmail"]}` matches nothing while either name alone matches
one row. So a multi-source scope is issued as one filtered query per source and
unioned, never as one query filtered afterwards. `MemoryWriteInput` has no
source field, so the source is recovered from the complaint id prefix; a full
run adds one unconstrained pass so memories with no source metadata are still
recalled, and a partial run omits it.

**Verified:** (all against the live tenant, `DRY_RUN=true` except where noted)
- `scripts/smoke.py` 5/5 PASS, exit 0. The stub line is gone and
  `backends.memory` reads `hydradb`. `scripts/smoke.py --demo-ready` also 5/5
  PASS: "wiring is sound and no stubs remain".
- `scripts/verify_no_hardcoding.py` 3/3 PASS, exit 0. (First run failed: a
  docstring quoted a corpus complaint id. Removed.)
- Round trip on a throwaway actor: read before write returns all zeros; write
  returns `mem-slack-gtroundtrip-<run>`; indexed in ~9s; read after write
  returns `times_reported_by_actor=1`, `times_seen_on_topic=1`, one
  `PriorResolution` with the ticket url and resolved_at intact, and
  `likely_regression` `{github, #1234}`. Tone steps `first_contact` ->
  `returning`.
- Same actor with an unrelated topic: actor count stays 1, topic count 0 —
  the two counts are independent, not the same number twice.
- Kill shot: scoped to `["linear","github"]` the same read returns all zeros and
  the tone drops back to `first_contact`; scoped to `["gmail"]` (the memory was
  written from slack) it also returns zeros.
- Unrelated actor + unrelated topic stays zero, so the floor is doing work: an
  unrelated memory scores ~0.65 against ~0.83 for a real match, under the
  profile's 0.72.
- Credential removed at runtime: read returns zeros, write returns None, neither
  raises.
- One full-scope read is ~0.9-1.5s (six queries in parallel). Smoke's wall clock
  went 29s -> 54s because seven complaints are read three times over.
- Probe memories written during this work were deleted from the tenant
  afterwards; `context.delete` confirmed each id.

**Left undone:**
- `MemoryWriteInput` has no field for the PR a fix landed in, so
  `likely_regression` is only ever populated when the ticket url itself is a
  GitHub pull request url. Filling this properly needs a contract change
  (a `regression_ref` field, or passing `ResolutionAction.fix_pr_url` through);
  I did not make it. Reads recall whatever regression metadata is present, so
  the read side is ready for it.
- `prior_resolutions` is not deduplicated by content — two memories with
  identical summaries appear twice, because they are two genuine prior contacts.
- No caching. Each complaint costs its own queries on every pipeline run.
- Three memories already in the tenant carry no `source` metadata (seeded before
  this landed). They are recalled on a full run and invisible on a scoped one,
  by design, but nothing rewrites them.

**For others:** `memory.IS_STUB` is now False, so `smoke.py --demo-ready`
passes and `backends.memory` reports `hydradb`; `MemoryReadResult.stub` is False
on every path including the degraded ones, because the read is real even when
the honest answer is zero. Signatures unchanged, `contracts.py` untouched.
Anything writing memories outside `memory_write` should set
`additional_metadata.source` to the originating complaint source, or the record
will only ever be recalled on a full-scope run.

## 2026-07-28 12:40 — three review defects in memory.py

**Task:** fix the three defects two reviewers found in the memory
implementation. Only file touched: `src/ghostthread/memory.py`.
`contracts.py` untouched, `killshot.py` / `knowledge_query.py` read only.

**Did:**

1. *`memory_read` went permanently blind.* The collection guard trusted
   `live_collections()`, whose cache is keyed on the literal `"value"`, lives
   for the whole process, and is cleared only by `refresh_documents()` — which
   has no callers. A cache primed before the first memory existed made every
   subsequent read return zeros for the life of the process, and it failed
   looking exactly like "no history". A cached miss is now re-checked with
   `live_collections(client, refresh=True)` before it is believed, so the
   cheap cached hit stays cheap and only the rare miss pays the ~330ms;
   `memory_write` repopulates the cache after a successful ingest, since that
   is the moment the collection can come into existence. Neither change can
   fail the operation it guards.
2. *`DRY_RUN` made the node inert while `IS_STUB=False` certified it live.*
   Added `is_operational()` — `bool(HYDRA_TOKEN) and not DRY_RUN` — and every
   `MemoryReadResult` now carries `stub=not is_operational()`. `IS_STUB` is
   untouched and still False: it answers "is this code a placeholder", which
   it is not. The argued position, and it is in the code as a comment: a dry
   run *is* a reason to flag the read, because the write side persists nothing
   so the zero is an artefact of the configuration rather than a measurement
   of the world, and that is precisely what `stub` exists to say. The flag is
   deliberately *not* tied to scope — a count that fell to zero because a
   source was scoped away is a real measured zero and is the kill shot, so
   those results stay `stub=False`. Behaviour is not silently switched on
   `DRY_RUN` anywhere; only the honesty flag moves.
3. *Prior-contact counts were chunk counts.* Both buckets are now keyed by
   `_memory_key`, which is `additional_metadata.complaint_id` falling back to
   the chunk id, and `_keep_best` keeps the strongest chunk per contact so
   ranking survives the dedup. Verified offline: 5 chunks spanning 2 complaints
   plus 1 legacy row collapse to 3 prior contacts, highest-scoring chunk kept.
   On truncation: `client.query` has no page or offset parameter, so paging is
   done by widening the window — `_fetch_paged` re-reads at 25 → 100 → 400
   while the window keeps filling, and only a still-saturated 400 counts as
   truncated. That case sets `last_read_truncated()` and raises a
   `warnings.warn` saying the counts are a lower bound; it could not go in the
   result itself because `MemoryReadResult` is frozen contract with no field
   for it. Measured window escalation for 10/25/120/900 matching rows:
   `[25]`, `[25,100]`, `[25,100,400]`, `[25,100,400] truncated=True`.
4. *Self-counting on re-run.* `memory_write` upserts under `mem-{complaint_id}`
   and the read applied no self-exclusion, so a replay counted the complaint as
   its own prior history. Hits whose `complaint_id` is this complaint, or whose
   chunk id starts with `mem-{id}`, are now skipped.
5. *Live-typed complaints were stored `unattributed`.* `live-a1b2c3d4` gives
   `_source_of` no prefix to recover, so the judge-typed complaint — the demo
   moment — was invisible to every scoped run. `MemoryWriteInput` has no source
   field and `contracts.py` is frozen, but N1 always runs immediately before N7
   for the same complaint and N1 holds the `ComplaintEvent`. `memory_read` now
   records `complaint_id -> complaint.source` and `_source_of` consults that
   before falling back to the prefix. It is an observation of what the event
   declared, not an inference; the map is capped at `OBSERVED_SOURCE_LIMIT`.

**Verified:** (live tenant; it held 0 memory rows before and after)
- `scripts/smoke.py --demo-ready` 5/5 PASS, exit 0, "wiring is sound and no
  stubs remain", `backends.memory` = `hydradb`, 7 complaints / 7 actions.
- `scripts/verify_no_hardcoding.py` 3/3 PASS, exit 0.
- Defect 1, single process, `DRY_RUN` off at runtime: prime the cache while no
  `memories` collection exists (`['gmail','linear','slack']`), write, read.
  With the refresh neutralised (pre-fix behaviour) the read returned
  `times_reported_by_actor=0`, tone `first_contact`, while the tenant genuinely
  held 1 memory row. With the fix, same process and same row: `1`, tone
  `returning`.
- Attribution and scope on a `live-` id: full scope 1, `["slack"]` 1,
  `["gmail"]` 0 — recalled where it belongs and honestly absent where it does
  not. Self-exclusion: reading for the complaint that owns the memory gives 0.
- `DRY_RUN` back on: `is_operational()` False and the read comes back
  `stub=True` with the same query path.
- Both probe memories deleted; `databases.stats` confirms `memory_collection
  row_count=0`, `knowledge_collection row_count=17`, and the collection list is
  back to `['gmail','linear','slack']`.

**Left undone / for others:**
- `pipeline.py` still reports `backends.memory` from `IS_STUB` alone, so a dry
  run shows `hydradb` even though nothing persists. Whoever owns `pipeline.py`
  should surface `memory.is_operational()` there and in `/health`; the
  predicate is public and cheap. `MemoryReadResult.stub` already carries it
  per result and reaches the UI through `resolution.memory`.
- Truncation cannot be expressed in the result without a `counts_truncated`
  field on `MemoryReadResult`. Contract change, not made — the module-level
  `last_read_truncated()` plus the warning is the best available substitute.
- The source recovery for `live-` ids works because N1 precedes N7 in
  `resolve_one`. Anything calling `memory_write` without a preceding
  `memory_read` for the same complaint still stores `unattributed`. A `source`
  field on `MemoryWriteInput` is the real fix.
- `derive_reply_tone` untouched, and it still ignores `stub`; a stubbed read
  yields `first_contact`, which is the safe direction but is a judgement the
  caller cannot currently override.

## 2026-07-28 12:55 — five review defects in killshot.py / knowledge_query.py

**Task:** fix the five defects two reviewers found in the kill shot and the
leak query. Files touched: `src/ghostthread/killshot.py` and
`src/ghostthread/knowledge_query.py`. `contracts.py` untouched, `memory.py`
read only (another agent held it).

**Did:**

1. *The kill shot printed a confident null result when HydraDB was
   unreachable.* Every HydraDB read now returns its outcome, not only its
   contents. `probe_collections()` returns `(collections, error)` and
   `live_collections()` becomes the lossy membership-test view of it, so
   `memory.py`'s import keeps its exact signature and semantics.
   `load_documents_result()` returns a `SourceLoad` carrying `documents`,
   `error`, `present` and `unreadable`; `load_documents()` is the
   contents-only wrapper. Failures that used to be a `break` or a `None` —
   failed collection listing, failed page enumeration, every document failing
   to inspect — are reported instead of arriving as a short list, and a
   partial read is no longer cached, so a transient failure cannot become
   permanent for the process. `detect_leaks_run()` returns a `LeakRun` with
   `grounded`, `degraded`, `errors`, which providers were read, which were
   empty, and which were absent. `detect_leaks()` keeps its `list[LeakVerdict]`
   return and now raises `SourceUnavailable` when nothing could be read, so an
   empty list only ever means "read them, found nothing". `run_killshot` then
   refuses: with zero reference clusters — from either an unreachable backend
   or a genuinely empty tenant — `degradation.assessed` is False,
   `degradation_observed` is `null` rather than `false`, no notes are emitted,
   and the headline says the run is not an answer. `backends.grounding` is
   derived from the reference read (`hydradb` / `hydradb (partial)` /
   `unavailable`) instead of the hardcoded `"hydradb"` at the old line 318.
2. *Leak confidence counted a source that was never queried.* `evaluate` takes
   coverage and `sources_used` from `WorkSearch.searched` — the providers that
   answered — not from the declared scope. The complaint side gets the same
   treatment through a new `complaint_providers_read` argument that
   `detect_leaks_run` fills from the loads that succeeded; a direct caller
   still defaults to the declared scope. Requested-but-unavailable providers
   are not dropped, they move to `providers_missing` and get a named reason.
3. *`search_work` returning `[]` was indistinguishable from a failure.* It now
   returns a `WorkSearch` separating `searched`, `unavailable`, `failed` and
   `out_of_scope_chunks`. Nothing answering is `unknown` with a null
   confidence and a reason naming which of the three it was; only a genuine
   no-match over providers that answered yields a leak, and its reason string
   names `search.searched` rather than the requested scope.
4. *The out-of-scope guard was gone while `graph_context=True` remained.*
   Restored in `_parse_chunks`, which now takes the allowed provider set and
   drops any chunk whose `sub_tenant_id` falls outside it, counting what it
   dropped so the count surfaces in the verdict's reasons.
5. *Stale docstring.* `killshot.py`'s module docstring documented
   `metadata_filters={connector_id: [...]}` with a list. Corrected to the
   scalar form actually issued, with the reason a list is wrong (it ANDs).

**On defect 4, empirically: no — graph expansion did not cross collections.**
Against the live tenant, `client.query(collections=[one], graph_context=True)`
never returned a chunk whose `sub_tenant_id` differed from the named
collection, across 36 probes: three collections × six complaint-derived
queries × `graph_context` True/False, plus both `recall_mode` values, plus
`max_results` raised to 50 so in-collection hits could not saturate the
budget, plus `max_results=1` so there was room for a neighbour, plus queries
deliberately written to match a *different* collection's content. Positive
control: naming two collections returns `{'slack': 4, 'linear': 8}`, so the
detector does fire when chunks really do span collections. Two caveats that
limit how far this generalises. First, `graph_context=True` and `False`
returned byte-identical chunk ids and relevancy scores on every probe, so
graph expansion appears to be doing nothing observable in this tenant at all,
and a test of a feature that is inert is weak evidence about the feature.
Second, the reason is probably the data: the ingested relation key is
`relations.source_ids`, not `relations.ids` as the review assumed, and its
values are degenerate — every slack and linear document carries the empty key
`person:` (their `reporter_email` is blank) while the gmail documents carry
real addresses that no other collection shares. So the one key that does span
slack and linear is an empty string, and the real emails bridge nothing. The
guard is restored regardless: this is a measurement of HydraDB's current
behaviour on this data, not a guarantee, and it costs one set lookup per
chunk against a silently wrong answer.

**Verified:** (live tenant, `DRY_RUN=true`)
- `scripts/smoke.py` 5/5 PASS, exit 0, `backends.grounding=hydradb`,
  7 complaints / 7 actions.
- `scripts/verify_no_hardcoding.py` 3/3 PASS, exit 0.
- `make killshot` exit 0: `slack` 0% answerable / 5 invisible / F1 0.00,
  `slack+gmail+linear` 100% / 9 leaks / F1 1.00, full scope 100% / 9 leaks.
- Defect 1 before and after, all credentials blanked at runtime. Before: exit
  0, three rows of zeros, `backends.grounding` `"hydradb"`,
  `degradation_observed` `false`, and the note "no complaint in this tenant
  resolves to work that lives only in an out-of-scope work source". After:
  `grounded` False, `backends.grounding` `"unavailable"`,
  `degradation_observed` `null`, `assessed` False, per-provider
  `grounding_errors`, and the headline "No answer: the reference run could not
  read any complaint source (...). The scope table below is zero against zero
  and nothing about source scoping can be concluded from it."
- The reachable-but-empty tenant refuses too, and correctly still reports
  `grounding: hydradb` — simulated by stubbing `load_documents_result` to an
  empty `SourceLoad`. That is the case a bare `grounded` flag would have got
  wrong.
- `detect_leaks` raises `SourceUnavailable` with blanked credentials and for a
  work-only scope; `detect_leaks_run` returns the reason in both cases without
  raising.
- Defect 2 on the live tenant, full scope: `providers_used` is
  `['gmail','linear','slack']` and github's connector UUID has moved from
  `sources_used` to `sources_missing`, with the reason "github was in scope but
  holds no live collection, so it was never queried". Confidence on a leak
  fell 1.0 → 0.5, which is margin 1.0 × coverage 0.5 rather than × 1.0.
- `POST /killshot` 200, payload JSON-serialisable, every row key the demo UI
  reads unchanged.
- AST scan of both files: no numeric literals in comparisons.

**Left undone / for others:**
- **Mean confidence at full scope dropped from ~0.72 to ~0.37, and the kill
  shot's confidence-delta column is now flat at +0.00.** This is the defect 2
  fix landing, not a regression: github was never queryable at *either* scope,
  so dropping it genuinely costs nothing and the old 0.361 → 0.722 spread was
  the fabricated difference. It does mean the "confidence falls with coverage"
  demo beat has no live data behind it until github holds documents. The
  `unchanged_scopes` note now says this in words instead of quoting the same
  number twice as if it were a change. Seeding the `SEED.md` GitHub issues
  restores both this and flavour B.
- `killshot.py` and `knowledge_query.py` are still outside
  `verify_no_hardcoding.py`'s `DECISION_MODULES`. I checked both by hand with
  the same AST rule and they are clean, but the check is not enforced. Adding
  them is a one-line change to a script I was not scoped to touch.
- The graph-expansion finding above should be re-run once github is seeded and
  once documents carry real `person:<email>` relations. The current answer is
  "no crossing observed on data where expansion appears inert", which is not
  the same as "expansion does not cross".
- `detect_leaks_as_results` now propagates `SourceUnavailable`. It has no
  callers, so nothing breaks, but whoever wires it into N2 must catch it.
- The web UI renders `rows` and `headline` only. It does not read the new
  top-level `grounded` / `grounding_errors` or `degradation.assessed`, so on an
  ungrounded run it will still draw a table of zeros with the refusal only in
  the headline text. Track B should grey the table out when `grounded` is
  false; the data is there.
