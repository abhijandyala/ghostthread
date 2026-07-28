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

## 2026-07-28 13:35 — A4, the Track A eval suite

**Task:** build the phase A4 eval suite. Files created:
`src/ghostthread/eval_suite.py`, `scripts/eval.py`, `fixtures/eval_cases.json`.
One line added to the `Makefile` (`eval` target + `.PHONY`). Nothing else
touched; `contracts.py`, `memory.py`, `killshot.py` and `knowledge_query.py`
were read only.

**Did:**

The constraint that shaped everything: the tenant holds noise. 23 complaint
documents that are Google security alerts, Linear onboarding notices and
"testing" Slack messages; GitHub has a connector but zero documents; nothing
resolves; `SEED.md` is not seeded. A table of "complaint X must be a leak" would
have been unrunnable today and stale the moment the tenant changes, and an eval
that cannot run is worse than none because its silence reads as approval. So the
suite is two layers plus a set of negative controls.

*Layer 1, ten invariants.* Properties of the system rather than of the data, so
they hold on an empty tenant, on the seeded one, and on whatever is there next
week:

- three separate checks that a complaint-side-only scope (`slack`, `gmail`,
  `slack+gmail`) makes every verdict `unknown` with `confidence is None`. This
  is the graded kill-shot claim, checked per scope because each fails for its
  own reason.
- a narrower scope never reports more resolved complaints than the full-scope
  run. Five subsets are compared. This is the property a display-filter kill
  shot satisfies trivially and a genuinely re-scoped query has to earn.
- `resolved` always carries at least one `matched_work_items` entry, `leak`
  carries none.
- `sources_used` and `sources_missing` are disjoint and every id in them is a
  real connector id read from `ConnectorRegistry`.
- `confidence is None` if and only if `verdict == "unknown"`.
- a freshly generated actor with a freshly generated topic reads back zeros and
  tone `first_contact`.
- `memory_read` with `HYDRA_TOKEN` blanked returns an honest empty result rather
  than raising or inventing counts.
- with the credential blanked, `detect_leaks` raises `SourceUnavailable` *and*
  `detect_leaks_run` returns `grounded=False` with per-provider reasons and no
  verdicts. Both halves asserted: the raise is what makes an empty list mean
  "read them, found nothing", and the run is what lets a UI degrade instead of
  crashing.

The last three shape checks walk every verdict from every scope the suite ran —
115 verdicts across 6 scopes on the current tenant — rather than only the
reference run.

*Layer 2, `fixtures/eval_cases.json`.* Schema plus two illustrative entries with
placeholder complaint ids. A case whose `complaint_id` is not in the tenant is
SKIPPED with the complaint id quoted, never passed. Data rather than Python for
two reasons: `verify_no_hardcoding.py` scans `src/` for demo entity names and
would rightly fail this file if it named one, and the table has to be fillable
by whoever seeds `SEED.md` without editing code.

*Negative controls.* Three defined, two runnable against this tenant:

- `ctl.bogus_connector_id` — query the work collections with a freshly
  generated UUID as `connector_id`. The real id returns 4 candidates, the
  generated one returns 0. This is the check that the kill shot's honesty rests
  on: if a made-up id still returned hits, the metadata filter would be inert
  and every scoped row would be a display filter wearing a costume.
- `ctl.semantic_floor_moves_scores` — drop `semantic_floor` to 0 and assert
  calibrated scores rise. 4 of 4 rose. A score indifferent to the one profile
  knob that touches every score is not being calibrated against the profile.
- `ctl.unreachable_match_threshold` — raise `match_threshold` to
  `math.nextafter(1.0, inf)`, which `evaluate`'s clamped score can never reach,
  and assert every resolution flips to leak. Skipped today: the tenant resolves
  nothing, so there is no resolution to break. Derived rather than written as a
  literal, because a threshold this file invented is the thing the project
  claims not to do.

*Three states, kept apart.* `pass`, `fail`, `skip`. The count line prints all
three and the banner adds "a skip is not a pass: the data those checks need is
not in the tenant, so they proved nothing either way". With an empty tenant this
is the whole point — 12 passed / 0 failed / 3 skipped must not read as 15 green.

The suite never writes to the tenant. Both memory probes use a uuid-derived
actor, topic and complaint id, so the actor-filtered and the topical query both
have to come back empty; a nonzero count there is an invented one.

**Verified:** (live tenant, `DRY_RUN=true`, `.venv/bin/python`, `PYTHONPATH=src`)

- `scripts/eval.py` exit 0: 12 passed, 0 failed, 3 skipped. `grounded=True`,
  profile origin `insforge`, 23 complaints / 23 clusters / 0 resolved,
  ~24s wall.
- `scripts/smoke.py --demo-ready` exit 0, 5/5 PASS, no stubs, 14 complaints /
  14 actions, `backends.grounding=hydradb`.
- `scripts/verify_no_hardcoding.py` exit 0, 3/3 PASS — including "no demo entity
  names in source" with `eval_suite.py` now inside `src/`.
- **The eval was proven able to fail.** Two regressions injected at runtime from
  a scratch file outside the repo (never committed, since deleted): `evaluate`
  wrapped to turn every `unknown` into `leak` with confidence 0.9, and
  `search_work` wrapped to drop `use_connector_filter`. Result: exit 1, 8
  passed / 4 failed / 3 skipped. All three `inv.refuses_without_work_source.*`
  rows failed with "18/5/23 verdict(s) were not unknown and carried a
  confidence; with no work-side connector in scope the system must refuse, not
  answer", and `ctl.bogus_connector_id` failed with "5 candidate(s) came back
  under a connector id that exists nowhere; the connector filter is not scoping
  the query". Un-patched and re-run: exit 0, 12/0/3 again.
- Tenant left clean: collections are `github, gmail, linear, slack`, the
  `memories` collection does not exist, 0 memory rows.

**Left undone / for others:**

- **`ctl.unreachable_match_threshold` has never actually executed**, because
  nothing in this tenant resolves. It is written and it skips honestly, but its
  own correctness is unproven until something resolves. Whoever seeds `SEED.md`
  should re-run `make eval` and confirm that row goes PASS rather than SKIP —
  if it stays SKIP after seeding, that is itself a finding about the matcher.
- Layer 2 is two placeholder rows. Both skip. Filling
  `fixtures/eval_cases.json` with real complaint ids after seeding is the
  remaining A4 work and needs no code change.
- The suite issues 6 full `detect_leaks_run` sweeps and takes ~24s. Acceptable
  for a pre-rehearsal check, too slow for CI on every commit. The document cache
  carries most of it; the per-complaint work queries are the cost.
- `check_leaks_without_credential` calls `refresh_documents()` on the way in and
  out, so any caller running the suite inside a longer-lived process pays a cold
  document read afterwards. Necessary: a warm cache answers from the last good
  read and the check would pass without ever going near the blanked credential.
- The complaint count moved from 16 to 23 between two runs a few minutes apart
  while I was testing. The connectors are live-syncing, so eval numbers are not
  stable across runs — another reason layer 1 asserts on properties rather than
  counts.
- `eval_suite.py` is not in `verify_no_hardcoding.py`'s `DECISION_MODULES`, same
  as `killshot.py` and `knowledge_query.py`. I checked it by hand against the
  same AST rule: the only float literals are `NO_SEMANTIC_FLOOR = 0.0` and
  `math.nextafter(1.0, math.inf)`, neither of which appears in a comparison.
  Adding the file to that list is a one-line change to a script I was not
  scoped to touch.

## 2026-07-28 14:05 — A5, seeding HydraDB Memories before the demo

**Task:** put Northbeam's two prior contacts into HydraDB Memories so the live
third contact is a third contact. Files created: `scripts/seed_memory.py`,
`fixtures/memory_seed.json`. Nothing else touched; `contracts.py`, `memory.py`,
`knowledge_query.py` and `intent.py` were read only.

**Did:**

*The fixture is data.* `fixtures/memory_seed.json` holds actors → topics →
episodes, each episode mapping 1:1 onto `MemoryWriteInput`, plus the topic's
`live_complaint` (the third contact, not written) which the seeder reuses as
its retrieval probe — so the verification query is the query the demo will run.
Recency is `resolved_days_ago`, not a date, for the same reason `corpus.json`
stores offsets in hours: a hardcoded date rots and the demo stops working the
next morning without saying so. More actors, topics and episodes can be added
without touching the script.

*The seeder writes through production.* Every episode goes through
`memory.memory_write`, not `context.ingest`. A seeder with its own write path
proves nothing about the pipeline, and the id derivation, the source
attribution from the complaint id prefix and the upsert all come free.

*The dry-run gate is explicit.* `memory_write` returns `DRY-` ids and persists
nothing while `DRY_RUN` is true, which is the default, so with neither flag the
script refuses and exits 2 with the reason. `--force` flips `config.DRY_RUN`
for that process only (never the `.env`); an environment that already has
`DRY_RUN=false` needs no flag. `--dry-run` *pins* `DRY_RUN` true rather than
leaving it alone, so a dry run in a live environment cannot quietly write.

*It reports what the system recalls, not what it wanted.* After seeding it
polls `databases.stats(...).memory_collection.row_count` until it reaches the
episode count and stops moving (three unchanged polls, 120s ceiling), then
reads back through `memory.memory_read` and prints
`times_reported_by_actor`, `times_seen_on_topic`, `stub`, the prior
resolutions and `derive_reply_tone`. The acceptance check is in the script: the
expected tone is obtained by calling `derive_reply_tone` on a synthetic result
carrying the number of episodes seeded, so it retunes with the profile instead
of comparing against a number. Mismatch exits 1.

*`--purge` is scoped to the fixture ids.* A rehearsal reset must not delete
memories the pipeline wrote for real complaints, so it deletes exactly
`mem-{complaint_id}` for the seeded episodes and polls the count until the
deletion has settled.

**Verified:** (live tenant, `.venv/bin/python`, `PYTHONPATH=src`)

- `--dry-run` exit 0: both payloads printed, ids `DRY-mem-gmail-nb-104` /
  `DRY-mem-slack-nb-118`, tenant untouched at 0 rows.
- No flags: exit 2, "REFUSING TO RUN: DRY_RUN is true...". It does not write and
  it does not claim to.
- `--force` exit 0: `mem-gmail-nb-104`, `mem-slack-nb-118`, rows 0 → 2,
  read back `times_reported_by_actor 2`, `times_seen_on_topic 2`, `stub False`,
  both prior resolutions with their dates and NB ticket urls, tone `returning`.
- `--force` again exit 0: same two ids, rows still 2, counts still 2. Idempotent.
- `--purge --force` exit 0: both ids deleted, rows 2 → 0. Re-seeded immediately
  after with `--force`: rows back to 2, same counts, tone `returning`.
- **`escalation` proven end-to-end, not asserted.** Two episodes is a
  *returning* actor: `{returning: 1, escalation: 3}` and two prior contacts is
  two. So a throwaway third memory was written for the same actor with the live
  contact-3 text, read back `times_reported_by_actor: 3`,
  `times_seen_on_topic: 3`, tone `escalation` — then deleted, count confirmed
  back at 2. The script prints this as "tone on the live contact: escalation (at
  3 contacts)" rather than padding the fixture to make the seeded state look
  nicer than it is.
- `scripts/eval.py` exit 0: 12 passed / 0 failed / 3 skipped, unchanged by the
  seed. `scripts/verify_no_hardcoding.py` exit 0, 3/3. `scripts/smoke.py`
  exit 0, 5/5, `memory: hydradb`.
- **Tenant left at 2 memory rows**, both seeded, as demo state.

**Left undone / for others:**

- **The seeded actor is `ops@northbeam.io` and the live complaint must carry
  that exact `author_email`, or the counts read back as zero** — a legitimate
  answer, so it fails silently. `memory_read` filters on the actor metadata
  exactly. A Slack message posted by the operator relaying the report arrives
  with the *operator's* email, so contact 3 needs to come in through the
  `/complaint` API with `author_email` set to the fixture's actor, or the
  fixture's actor changed to whatever Slack will present. This is the one thing
  standing between the seed and the demo beat, and it is not something the
  seeder can check for you.
- The `NB-104` / `NB-118` ticket urls are the prior resolutions SEED.md
  describes. They are demo history, not live Linear issues, and the urls do not
  resolve in a browser. Flagged in the fixture's `_schema` block.
- `ctl.unreachable_match_threshold` still SKIPs. Nothing in the tenant resolves,
  and memories are not verdicts, so seeding memory was never going to change
  that — it needs the SEED.md Linear/GitHub issues.
- `fixtures/eval_cases.json` is still two placeholders. Unrelated to A5, still
  the remaining A4 work.

## 2026-07-28 13:55 — Slack complaints had no actor at all

**Task:** two compounding defects that left every Slack complaint with
`actor_email == ""`, so no Slack message could ever match a memory or a Gmail
complaint from the same human. Files touched:
`src/ghostthread/knowledge_query.py`, `src/ghostthread/resolve.py`. Both Track
A. `contracts.py`, `memory.py`, `killshot.py`, `eval_suite.py` and every Track
B file read only.

**Defect A — `load_documents` read the wrong metadata keys.**

`context.inspect` returns two different shapes and the loader only understood
one. A hand ingest (the Gmail OAuth path, the older Linear fixtures) writes
`additional_metadata` / `document_metadata` / `tenant_metadata`. A document
synced by a HydraDB managed connector carries none of those; it holds
`app_metadata` (connector_id, `slack_author_id`, `resource_id`) and `app_fields`
(`author`, `body`, `created_at`). The loader merged only the first three, so
every connector document built `metadata = {}` and, from that, no actor, and
`timestamp` and `url` came off a top level that does not carry them either.

`_metadata_of` now flattens all five, explicit-ingest keys winning, because one
tenant holds both kinds at once. `text` falls back to `app_fields.body`, and the
timestamp falls back to `created_at` and then to `slack_ts` — the latter is
already epoch seconds carried as a string, so `_iso_to_epoch` became `_to_epoch`
and tries a native epoch before ISO.

Two smaller things came out of the same read. `_actor_of` used to accept
`slack_author_id` as an address; a member id in an email field makes a complaint
look attributed while matching nothing, which is worse than an empty one, so
addresses now go through `resolve.extract_email` (which also unwraps a
`Name <a@b.com>` `from` header) and the member id gets its own `Document.actor_id`
field. The id stays available, so a document with no address is still
identifiable.

**Defect B — Slack has no email anywhere, only a member id.**

New section at the bottom of `resolve.py`: `SlackDirectory`, member id -> address
through `GET users.info`, plus `SlackMember` and a process-wide singleton behind
`slack_emails()`. `knowledge_query._resolve_slack_actors` runs once per load
over the distinct ids actually present, so 18 documents cost exactly one
`users.info` call (verified by a counting transport). Misses are cached too: a
bot has no address and a token without `users:read.email` never will, so
re-asking per document buys a call to learn the same nothing. The transport is
injectable, which is how the degraded paths below were exercised without
breaking the live token.

Nothing here ever invents an address. Every failure yields an empty
`actor_email` and a `reason`, and an empty actor is a real answer —
`memory_read` correctly reports zero prior contacts for it.

**Verified:** (live tenant, `.venv/bin/python`, `PYTHONPATH=src`)

Before: slack 18 documents, **0 with an actor**, `metadata {}`, `timestamp 0.0`
on all of them. Gmail 8 documents, 8 with an actor.

After: slack 18/18 with `actor_email 'abhijandyala@gmail.com'`, `actor_id
'U0BL91BSTDL'`, real timestamps, full connector metadata. Gmail unchanged at
8/8 with the same five addresses and the same timestamps — the old shape still
works.

Degraded paths, each producing an empty actor and a stated reason rather than a
guess: no `SLACK_TOKEN` ("no SLACK_TOKEN configured, so member ids cannot be
resolved"), transport raising ("users.info failed: RuntimeError: connection
reset"), `ok:false` ("users.info refused: missing_scope"), bot author ("Slack
author is a bot or app, which has no address").

- `scripts/eval.py` exit 0: 12 passed / 0 failed / 3 skipped, `complaints_examined
  26`, all invariants holding across 130 verdicts and 6 scopes. (A4 logged 23
  documents; the difference is tenant data added since, not this change — the
  loader returned 18 Slack documents before and after, they simply had no actor.)
- `scripts/smoke.py --demo-ready` exit 0, 5/5, `memory: hydradb`.
- `scripts/verify_no_hardcoding.py` exit 0, 3/3.
- Tenant left at **2 memory rows**. Nothing created, nothing deleted.

**Left undone / for others:**

- **The Slack messages resolve to `abhijandyala@gmail.com`, and the memory seed
  fixture is keyed on `ops@northbeam.io`.** Proven directly: `memory_read` for
  `ops@northbeam.io` reads back `times_reported_by_actor 2`, and for
  `abhijandyala@gmail.com` reads back `0`. Both answers are honest; they are
  about different people. Re-key `fixtures/` (A5's seed) to
  `abhijandyala@gmail.com` and the Slack complaint becomes contact 3.
- The tenant holds each Slack message **twice** — 9 connector-synced documents
  and 9 hand-ingested duplicates of the same channel, which is why the count is
  18 for 9 messages. The hand-ingested copies put the member id in the generic
  `entity_id` rather than in `slack_author_id`, so `_slack_member_id` reads
  `entity_id` as a member id for Slack documents only; both copies resolve. The
  duplication itself is an ingest problem, not a loader one, and is untouched.
- Connector documents have no top-level `id`, so their `Document.id` is the
  content hash HydraDB enumerates them under. That gives up no source prefix, so
  `memory._source_of` files a memory written for one as `unattributed`. Noted,
  not fixed: it is a memory-side question and `memory.py` is out of scope.
