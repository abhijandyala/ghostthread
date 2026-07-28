# Agent workflow

GhostThread is built by five specialised agents rather than one general one.
Definitions live in `.claude/agents/`; each keeps a running log in
`docs/agentlog/`.

## The agents

| agent | responsibility | acts or reports |
|---|---|---|
| `orchestrator` | sequences work, decides what to delegate | reports |
| `coder` | implements one scoped change in named files | edits code |
| `issue-checker` | static review: defects, contract drift, dead paths | reports |
| `qa-tester` | functional verification: runs the suite, the kill shot, the API | reports |
| `merge-warden` | decides whether a branch is safe to merge | reports |
| `git-operator` | branch, commit, push, PR, merge | acts on git |

## The loop

```
orchestrator  ->  coder  ->  issue-checker  ->  qa-tester  ->  merge-warden  ->  git-operator
   plan          implement      read it         run it          gate it          ship it
```

Two properties of this shape matter more than the shape itself.

**Whoever decides is not whoever acts.** `merge-warden` judges; `git-operator`
carries it out. `issue-checker` and `qa-tester` find problems; `coder` fixes
them. An agent that both judges and acts on its own judgement has no gate, and
under time pressure it will pass itself.

**Reading and running catch different bugs.** `qa-tester` executes and finds
what fails. `issue-checker` reads and finds what will fail, what is unreachable,
and what quietly disagrees with something else. This codebase has shipped
defects that the whole suite passed over — a green run is not a reason to skip
the read.

Skipping straight from `coder` to `git-operator` is how an unverified branch
reaches `main`. When time is short, drop `issue-checker` before `qa-tester`, and
never drop `merge-warden` before a merge.

## Logging

Every agent appends to its own log, newest last:

```
docs/agentlog/
  orchestrator.md
  coder.md
  issue-checker.md
  qa-tester.md
  merge-warden.md
  git-operator.md
```

Entry format:

```markdown
## 2026-07-28 14:05 — A3 memory read
**Task:** implement memory_read in memory.py, replacing the stub
**Did:** ...
**Verified:** smoke 5/5, memory_read returns 3 prior contacts for the demo actor
**Left undone:** likely_regression still null when GitHub has no issues
**For others:** MemoryReadResult.stub is now False, so smoke --demo-ready passes
```

`Verified` means observed passing. If it was not run, write "not run" — never
infer. The gap between *implemented* and *verified* is the one that costs a
demo, so the log keeps it visible.

Read the relevant log before delegating. It is how a decision gets made once
rather than re-litigated every phase.

## Non-negotiables every agent inherits

These come from the graded claims, not from taste. Violating one falsifies
something the project asserts on stage.

1. **File ownership.** Track A owns `hydra.py`, `leaks.py`,
   `knowledge_query.py`, `killshot.py`, `resolve.py`, `memory.py`,
   `eval_suite.py`. Track B owns everything else. `contracts.py` changes only at
   a sync point.
2. **No hardcoded decisions.** Thresholds, weights, category policy and tone
   steps come from the InsForge profile via `get_profile()`.
3. **Never fabricate grounding.** Zero prior contacts is a real answer.
   "Unknown" is a real answer. A confident wrong answer is the worst outcome
   this project can produce.
4. **The profile lives in InsForge.** `intent_profile.json` is a seed. Editing
   it changes nothing until `make seed` runs.
5. **Writes stay behind `DRY_RUN`**, and the sandbox fix is checked against
   `sandbox_repo_allowlist` and never auto-merged.
