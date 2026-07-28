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
