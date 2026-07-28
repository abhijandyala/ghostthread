# merge-warden log

Append newest entries at the bottom. See `docs/AGENT_WORKFLOW.md` for the entry
format and what `Verified` is allowed to mean.

## 2026-07-28 12:41 — gate PR #1 (`track-a/a2-leak-detection`) into `main`

**Task:** decide whether PR #1 is safe to merge, given PR #2 (`baseline`) is also
open and `origin/track-b` is built on `baseline`.

**Verdict:** merge with named risks. Blocking condition: **merge commit or
rebase only — never squash.**

**Did:** checked GitHub state, branch containment, contracts drift, file
ownership, stubs, dirty tree; ran the smoke and anti-hardcoding gates; simulated
both merge strategies against `origin/track-b` with `git merge-tree`.

**Verified (observed, not inferred):**
- `smoke.py --demo-ready` — 5/5 PASS, 14 complaints, 14 verdicts, 14 actions,
  26358ms, "wiring is sound and no stubs remain". Exit 0.
- `verify_no_hardcoding.py` — 3/3 PASS. Exit 0.
- `git status --short` — only `?? ui/`. Nothing else outstanding. `.env` and
  `ui/` are untracked and `node_modules/` was added to `.gitignore` on this
  branch.
- `git log HEAD..origin/baseline` is **empty**: `baseline` tip `c239ad5` is an
  ancestor of `ff76634`. PR #1 already contains all of PR #2.
- `gh pr checks 1` — CodeRabbit *pending* ("Review queued"), reviewer state
  "Changes requested". `mergeStateStatus: UNSTABLE`, `mergeable: MERGEABLE`.
- `git merge-tree --write-tree origin/track-b HEAD` → clean tree, no conflicts.
- `git merge-tree fd8a351 origin/track-b HEAD` (squash simulation) → **41
  conflict hunks across 10 files**, including `contracts.py`, `pipeline.py`,
  `router.py`, `extract.py`, `memory.py`, `smoke.py`, `intent_profile.json`.

**Findings, most severe first:**
1. *Squash-merging PR #1 strands Track B.* `origin/track-b` forks at `bd16a4b`,
   inside `baseline`. A merge commit keeps that fork point reachable and the
   later `track-b` merge is clean. A squash discards it, dropping the merge base
   to `fd8a351` and forcing git to re-resolve all of baseline against itself.
2. *Contracts drift is additive only.* Track A's own additions since `baseline`
   are `WorkItemRef`, `LeakVerdict`, and `IntentProfile.anchor_github_via_linear`
   / `recall_mode` — new shapes plus two fields with defaults. No rename, no
   retype, no removal. `LeakResult.sources_missing` and `evidence_sources` come
   from `baseline`, both defaulted. Nothing in Track B breaks.
3. *Two verdict vocabularies now coexist in `contracts.py`.* `Verdict` is
   `actioned|leaked|unknown_insufficient_sources`; `LeakVerdict.verdict` is
   documented as `leak|resolved|unknown`. Bridged today by the translation map
   at `knowledge_query.py:948-950`, and `LeakVerdict` is imported only by
   `killshot.py` and `knowledge_query.py`. A trap for the next writer, not a
   present defect.
4. *Ownership:* `insforge/intent_profile.json` (+56) and
   `scripts/verify_no_hardcoding.py` (+33) are Track-B-owned paths edited by
   Track A. The verifier change is defensible (offline mutation test). The
   profile edit is two legitimate Track A knobs plus a whole-file JSON reflow
   that inflated the diff. Both auto-merge against `track-b`. `contracts.py`,
   `killshot.py`, `knowledge_query.py`, `memory.py` are legitimate. The
   `extract.py` / `pipeline.py` / `router.py` / `smoke.py` / `Makefile` lines in
   `git diff --stat origin/main...HEAD` come from `baseline`, not Track A.
5. *No stubs reach main.* `memory.IS_STUB` is `False`. The only `stub=True` is
   runtime honesty for the non-operational path.

**Left undone:** CodeRabbit on PR #1 had not returned when I ran; treat PR #1 as
having an unresolved "changes requested" review. `eval_suite.py` (Track A) does
not exist yet. `--demo-ready` reports `extraction: heuristic`, so the real
Pipeshift classifier is still on `track-b` (B1) and not in this merge.

**For others:** after merging, do **not** run `make seed` until `track-b` lands
— `track-a`'s `intent_profile.json` omits `global_overrides.min_confidence_for_fix_pr`
(0.7), which `track-b`'s `fixgen.py` expects. PR #2 will auto-close as merged
once `c239ad5` is reachable from `main`; that is expected, not a loss.

## 2026-07-28 13:40 — gate PR #3 (`track-a/a4-eval-suite`) into `main` — final Track A merge

**Task:** decide whether PR #3 is safe to merge, name the safe merge method, and
predict what the partner hits when `main` later flows into `origin/track-b`.

**Verdict:** safe to merge. **Any of the three methods is safe this time —
merge commit, squash, or fast-forward all produce byte-identical trees and all
merge cleanly into `track-b`.** Prefer a merge commit for consistency with PR #1.
Carry-forward risks are handoff risks, not merge risks.

**Did:** checked GitHub state, branch topology, contracts drift, file ownership,
stubs, dirty tree; ran the eval, demo-ready and anti-hardcoding gates; cloned the
repo to `/tmp/mw-sim` and simulated all three merge methods end-to-end, then
merged each result into `origin/track-b` and compared the resulting trees;
import-checked the merged `track-b` tree.

**Verified (observed, not inferred):**
- `scripts/eval.py` — **13 passed, 0 failed, 2 skipped**, `EVAL PASSED`, exit 0.
  47023ms. Grounded `True`, profile `insforge` (match_threshold 0.36), 31
  complaints / 29 clusters / 6 resolved across `slack`+`gmail`.
- `smoke.py --demo-ready` — 5/5 PASS, 18 complaints, `{'leaked': 18}`, 18
  actions, 63914ms, "wiring is sound and no stubs remain".
- `verify_no_hardcoding.py` — 3/3 PASS, exit 0.
- `git status --short` — only `?? ui/`. Nothing else outstanding.
- `git diff origin/main...HEAD -- src/ghostthread/contracts.py` — **empty**.
- `git merge-base origin/main origin/track-b` = `ceca59c` = **`origin/main`'s own
  tip**. `track-b` merged `main` at `36fdc01`, so `main` is fully contained in
  `track-b` and `track-b` contains none of PR #3's four commits.
- `git merge-base --is-ancestor origin/main HEAD` → true; the merge into `main`
  is a fast-forward.
- Simulation: merge-commit / squash / fast-forward onto `main` all clean, all
  tree `83ad1c9`. Merging each into `origin/track-b`: all clean, all tree
  `e1eaa86`. **Zero conflicts, zero hunks, under every method.**
- Merged `track-b` tree: `compileall` clean; all 15 modules import, including
  `knowledge_query`, `resolve`, `memory`, `killshot`, `fixgen`, `actions_log`.
- `gh pr checks 3` — CodeRabbit **pending**, "Review in progress".

**Findings, most severe first:**
1. *The squash hazard from PR #1 no longer applies.* Last time `track-b` forked
   at `bd16a4b` **inside** the branch being merged, so a squash discarded the
   fork point and forced 41 conflict hunks across 10 files. `track-b` has since
   merged `main` (`36fdc01`), moving its merge base to `main`'s current tip.
   Nothing PR #3 carries is reachable from `track-b`, so there is no shared
   history for a squash to discard. Simulated all three; all clean.
2. *Zero file overlap between this branch and `track-b`'s new work.* PR #3
   touches `Makefile`, `SEED.md`, `docs/agentlog/coder.md`,
   `fixtures/eval_cases.json`, `fixtures/memory_seed.json`, `scripts/eval.py`,
   `scripts/seed_memory.py`, `eval_suite.py`, `knowledge_query.py`, `resolve.py`.
   `track-b` vs `main` touches `act.py`, `actions_log.py`, `api.py`, `config.py`,
   `extract.py`, `fixgen.py`, `intent.py`, `pipeline.py`, `insforge/`,
   `scripts/seed_insforge.py`, `scripts/smoke.py`,
   `scripts/deploy_edge_functions.py`, `.env.example`, `README.md`,
   `.gitignore`. The intersection is empty. The partner hand-resolves **nothing**.
3. *One rename in a shared module, contained.* `knowledge_query._iso_to_epoch`
   became `_to_epoch`. It is private and its only call site is inside
   `knowledge_query.py` itself, which `track-b` does not modify — so the rename
   travels as a unit. `_client`, `_calibrate`, `distil`, `live_collections`,
   `_actor_of` and `load_documents_result` keep their signatures, which matters
   because `track-b`'s `memory.py:74` and `killshot.py:40` import them.
   `resolve.py` is a pure append (+202/-1) below `IdentityGraph`, which
   `track-b`'s `leaks.py:29` and `pipeline.py:55` import unchanged.
4. *Contracts are untouched.* The diff is empty. Neither track breaks.
5. *Ownership: two nominal Track B paths, both benign.* `Makefile` adds a
   `.PHONY` entry and an `eval:` target — additive, no existing target changed.
   `scripts/eval.py` and `scripts/seed_memory.py` are new files, not edits to
   `track-b`'s scripts; `track-b`'s own `smoke.py`, `seed_insforge.py` and
   `deploy_edge_functions.py` are untouched here. No real violation.
6. *No stubs reach main.* `memory.IS_STUB` is `False`. Every `stub=True` is
   runtime honesty on the non-operational path, asserted by
   `inv.fresh_actor_no_history` and `inv.memory_degrades_without_credential`.
7. *`fixtures/eval_cases.json` ships two placeholder rows that SKIP, not pass.*
   Both `complaint_id`s are literal `replace-with-a-real-complaint-id-...`
   strings. The runner prints "a skip is not a pass". Layer 2 of the eval suite
   is therefore **unexercised** — the green result rests entirely on the ten
   invariants and three negative controls.
8. *Live tenant now carries demo state.* `scripts/seed_memory.py` has written
   **2 memory rows** for `ops@northbeam.io` (`gmail-nb-104`, `slack-nb-118`,
   topic `csv-export-truncation`) into HydraDB Memories. This is durable
   external state, not repo state; `--purge` removes it and `--actor` re-keys it.

**Left undone:** CodeRabbit on PR #3 had not returned when I ran; PR #3 is
`OPEN` with review in progress. Layer 2 expected-case coverage is unproven.
I did not run `track-b`'s own gates against the merged tree beyond import checks.

**For others (Track B handoff):** Track A ends here. Waiting for you on `main`
after this merge: `eval_suite.py` + `make eval` (13 invariant/control checks that
must stay green), `scripts/seed_memory.py` for demo memory, the Slack
member-id→email resolver in `resolve.py` (`slack_emails`, `SlackDirectory`), and
connector-metadata reading in `knowledge_query.py`. Three things to know:
(a) the `make seed` hazard from PR #1 still stands — `main`'s
`insforge/intent_profile.json` still omits `min_confidence_for_fix_pr` (0.7),
which your `fixgen.py` expects and which your branch already restores, so merge
`track-b` before seeding; (b) `--demo-ready` on `main` still reports
`extraction: heuristic`, because B1's Pipeshift classifier is only on `track-b`;
(c) `ui/` stays untracked — 40 source files plus `node_modules/` (gitignored)
sit in the working tree, so never `git add -A` at the repo root.
