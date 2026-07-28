# GHOSTTHREAD — Final Build Plan
**Agents You Love 2 · Frontier Tower, SF · 2 builders · A1 already complete**

This is the merged plan: GhostThread's structure and safety rules, retooled around the memory-first pitch you liked from the Groundskeeper materials, sized for two people, and designed so two Cursor sessions can't clobber each other's work.

---

## What changed vs. the last version

1. **Pitch reoriented around memory.** The lede is no longer "we find the leak" — it's "every competitor reads one message; we read the history." The leak-detection kill shot still runs, but it's now framed as one *consequence* of having the memory, not the whole product. That's the pitch that survives the fact that Continue.dev and Lattice already ship the one-message version.
2. **Two tracks, not three.** Track A (Grounding + Memory + Kill Shot) and Track B (Intelligence + Actions + Demo). Each track owns disjoint files. Sync points are scheduled, not ad-hoc.
3. **A1 removed** — you've done it. Track A now starts at A2. Track A ends earlier (~2:45); Person A joins Person B for demo hardening from 2:45 onward.
4. **Kept from Groundskeeper:** the memory-first framing, the pre-flight checkpoint pattern (curl each API before writing app code), the seeded-data-before-demo insurance, the specific fallback playbook. All folded in below.
5. **Rejected from Groundskeeper:** Jira + Outlook connectors (extra work, no rubric points — the four the problem statement gives you are Slack/GitHub/Linear/Gmail, use those), and writing to both InsForge Postgres and HydraDB Memories (redundant). HydraDB Memories carries the memory story; InsForge holds `category_policy` + idempotency.
6. **Also rejected:** treating this as a "watch inbound messages 24/7" product. For a hackathon demo you want messages arriving synchronously from a live input box that hits the pipeline directly — real webhooks are engineering time that doesn't buy you rubric points.

---

## The new one-sentence pitch
Every complaint-handling agent out there reads one message and acts on it — Groundskeeper, Continue, all of them. GhostThread reads the history: before it files a ticket or touches code, HydraDB has already told it that this customer complained about the same thing twice before, that a Linear ticket was closed six weeks ago claiming it was fixed, and that a specific PR from a month ago probably introduced the regression. The action taken — the ticket, the tone of the reply, the scope of the fix — reflects all of that. Take the memory away and it's a status bot.

**Say this on stage, verbatim if you have to.**

---

## Why memory-first still clears every mandatory requirement

| Requirement | How it's satisfied |
|---|---|
| HydraDB — 2+ connectors | 4 connectors, all load-bearing: Slack + Gmail carry complaints, Linear + GitHub carry work. Kill shot proves it. |
| HydraDB — the "impossible question" | *"For this specific customer / component / error, what's the full history across every tool — and does the current complaint match a pattern we've seen before?"* No single tool answers this. |
| HydraDB — the kill shot | Two flavors, both real. **Flavor 1:** scope query to Slack `connector_id` only — every leak/resolved verdict flips to `unknown`. **Flavor 2:** scope OFF GitHub only — regression evidence goes null, the sandbox fix loses its target file and searches broadly. Different degradations, both honest. Pick one for the required 90s; keep the other in reserve for Q&A. |
| HydraDB — memory / context (prize track) | HydraDB Memories used deliberately for per-customer episodic memory. First contact → warm reply. Third complaint about the same thing → the tone escalates. Live on stage. |
| Pipeshift — model specialization | Two deployed models: Mistral-7B for classification and reply generation, DeepSeek Coder for the targeted fix. Have both curl-tested before touching app code. |
| RocketRide Cloud — managed pipeline | `pipeline.json` deployed live, dashboard visible during demo, webhook source node hit by the live input box. |
| InsForge — intent profiles + lifecycle | `category_policy` provisioned in InsForge Postgres by Cursor via CLI, served through an edge function, promoted branch-to-production live on stage. |

---

## Fold-in: how memory actually changes the pipeline (§9 update)

The pipeline gains two nodes and rearranges one. Same shape as before, deeper behavior:

1. **Ingest** — complaint arrives (live input box in the demo; would be webhook in production)
2. **Memory recall (new)** — HydraDB Memories lookup keyed on the resolved actor + a topical HydraDB Knowledge query on the complaint text. Returns: prior complaints from this actor, prior resolutions on this topic, prior regressions on implicated components. This is the "read the history" step.
3. **Leak detection** — the existing HydraDB Knowledge query against all 4 connectors. Returns `LeakVerdict`.
4. **Classify + extract** — Pipeshift Mistral. Prompt now includes the memory summary from step 2, so `times_reported`, `reply_tone`, and severity reflect history, not just this one message.
5. **Policy lookup** — InsForge `category_policy` (unchanged).
6. **Router** — pure function of facts + policy (unchanged).
7. **Actions** — file/update ticket, propose sandbox fix (Pipeshift DeepSeek Coder targeting `file_hint` from step 2 if regression evidence exists), draft reply (Mistral, tone from step 2), escalate.
8. **Memory write (new)** — write an episodic memory back to HydraDB Memories: what the complaint was, what was decided, what the resolution looked like. This is what makes the *next* complaint smarter than this one.

The two new nodes are the memory read (step 2) and the memory write (step 8). Everything else is what you already planned.

---

## New / updated fields

Add these to `ExtractedFacts`:
- `times_reported_by_actor` (int) — from the memory read
- `times_seen_on_topic` (int) — from the memory read across all actors
- `reply_tone` (enum: `first_contact | returning | escalation`) — derived from the two counts above
- `regression_evidence` (string | null) — PR/commit implicated by the memory read, null if none

The router doesn't change. The reply drafter and the fix generator both read `regression_evidence` and `reply_tone` from facts and act accordingly.

---

## Cursor conflict-prevention rules

Both of you are running Cursor. Merge conflicts are the single most common way this kind of build blows up mid-afternoon. These rules cost nothing and prevent everything:

1. **File ownership is exclusive.** Every file belongs to exactly one track. The phase cards below name the files. If your track's phase doesn't list a file, you don't create it or edit it.
2. **Shared types live in `contracts/`**. `contracts/types.ts` (or `.py`) is the *only* jointly-edited file. It gets touched only at scheduled sync points (see below), never mid-phase.
3. **`pipeline.json` is Track B's file.** Track A adds *nodes* to it only during sync points, with Person B in the room.
4. **Branch per person.** `track-a/*` and `track-b/*`. Merge to `main` only at sync points, not opportunistically.
5. **Sync points are 5 minutes, standing up.** Diff, merge, verify main runs, back to your branches. No debugging during syncs — that's what phase time is for.

**Sync points:**
- 11:30 — after Track A's A2 finishes. Contract check: is `LeakVerdict` shape agreed?
- 1:15 — post-lunch. Kill shot mechanism (A3) integrated into pipeline.json.
- 2:45 — Track A joins Track B. From here, one shared branch, `demo-prep`.
- 4:00 — Feature freeze.

---

## Pre-flight checkpoints (before phase work starts)

Do these before you write any application code. Each is a 30-second curl. If any fail, fix it before proceeding — an app built on top of a broken API is much harder to debug than a curl that returns 401.

You'll notice A's HydraDB check is already implicitly done from A1 — just re-verify the tenant/connector state is still healthy after the network moves around.

| Check | Who | Command shape | Pass = |
|---|---|---|---|
| HydraDB responding + 4 connectors still synced | A | Query test message from A1 | Non-empty chunks |
| Pipeshift Mistral endpoint | B | `POST /v1/chat/completions` with model = your Mistral ID, message = "Say OK" | Returns "OK" |
| Pipeshift DeepSeek Coder endpoint | B | Same, model = your DeepSeek ID, message = "Write hello world" | Returns Python |
| RocketRide Cloud Hello World endpoint | B | POST to the endpoint | 200 response |
| InsForge CLI linked to `ghostthread` project | B | `npx @insforge/cli whoami` + `current` | Shows project name |
| Linear API + team ID | B | GraphQL `{ teams { nodes { id name } } }` | Returns your team |
| GitHub token + sandbox repo access | B | `git ls-remote` or REST call for repo info | 200 |

---

## Track A — Grounding, Memory, Kill Shot (Person A)

**Owns files:**
- `hydra/knowledge_query.py` — the leak-detection query
- `hydra/memory_read.py` — the memory recall for a given actor + topic
- `hydra/memory_write.py` — writes episodic memory after a resolution
- `hydra/kill_shot.py` — the `metadata_filters` scoping toggle
- `hydra/eval_suite.py` — regression tests for seeded cases

**Does not touch:** `pipeline.json`, `insforge/`, `pipeshift/`, any UI file.

**LLM context block for Cursor (paste at the start of every phase):**
```
PROJECT: GhostThread. 2-person hackathon build. My track (A) owns the
HydraDB layer only — knowledge queries, memory read/write, and the
kill shot mechanism. My partner (B) owns everything else.

CORE PITCH: every complaint-handling agent reads one message; we read
the history. HydraDB Memories is how we do that. HydraDB Knowledge
answers the leak-detection question. The kill shot proves both
matter.

CONSTRAINTS:
- Never write to files outside hydra/ or contracts/. Any change to
  contracts/ is coordinated at scheduled sync points, not mid-phase.
- Never claim "resolved" when a required connector is out of query
  scope. Return "unknown". This is the whole point of the kill shot.
- Memory writes are episodic (one per resolution), not knowledge —
  type="memory" on POST /context/ingest, not type="knowledge".
- Never invent structure that isn't in HydraDB's actual API. If unsure
  about a field, ask me before assuming.

INTERFACE: LeakVerdict, MemoryReadResult, MemoryWriteInput —
see contracts/types.
```

### A2 · 11:00–12:00 · Leak-detection query
Files: `hydra/knowledge_query.py`.
- `POST /query` with `type: "knowledge"`, `query_by: "hybrid"`, `graph_context: true`, `mode: "thinking"`.
- Turn results into `LeakVerdict` objects — the shape you agreed with Person B at 11:30.
- Test against 3–4 seeded leak cases and 2–3 seeded resolved cases in your data.
- **Accept:** correct verdict on every seeded case; `LeakVerdict` carries `sources_used`; query runs <3s.
- **Cut rule:** if GitHub-to-complaint matching is unreliable (commits rarely reference customers), anchor GitHub through Linear — only count a GitHub match if it references a Linear ticket that's already linked to the complaint.

### A3 · 12:00–1:00 (crosses lunch) · Kill shot + memory read
Files: `hydra/kill_shot.py`, `hydra/memory_read.py`.
- **Kill shot:** wire `metadata_filters: { additional_metadata: { connector_id } }`. Full 4-connector vs. Slack-only run — every verdict flips to `unknown`. Also verify the partial degrade (drop GitHub only) → `regression_evidence` goes null.
- **Memory read:** given a resolved actor identity + a complaint topic, return `{ times_reported_by_actor, times_seen_on_topic, prior_resolutions[], likely_regression_pr_or_commit }`. Uses `type: "memory"` on `POST /query`.
- **Accept:** kill shot toggle takes effect <2s; two flavors of degradation (all-Slack vs. no-GitHub) produce visibly different results, not identical collapse; memory read returns non-empty on the actors you seed.
- **Cut rule:** none for the kill shot — this is the disqualification risk. Memory read is not cuttable either; it's the pitch. Cut A4 first, then A5, before you cut anything in A3.

### A4 · 1:00–1:45 · Memory write + eval suite
Files: `hydra/memory_write.py`, `hydra/eval_suite.py`.
- **Memory write:** after a resolution, write one episodic memory: `{ actor, complaint_summary, category, action_taken, ticket_url, resolved_at }`. Uses `POST /context/ingest` with `type: "memory"`, appropriate metadata.
- **Eval suite:** codify seeded cases + expected verdicts + expected memory-read results as a script. Run before every demo rehearsal.
- **Accept:** eval passes on all seeded cases; a deliberately-broken query gets caught.
- **Cut rule:** cut eval suite first if A3 needs more time.

### A5 · 1:45–2:45 · Seed the memory, harden the queries
- Run 5–6 pre-written resolutions through the memory write path so HydraDB Memories has real data before demo. Same insurance principle as Groundskeeper's seed step.
- Manual-run the memory read for the actor and topic you'll use in the demo — the second/third-complaint tones only exist if the memory shows repeat contact.
- Sanity-check the kill shot one more time from the phase's dedicated file, not from the pipeline. Own it end to end.
- **Accept:** memory dashboard (query via the InsForge edge function Person B will build) shows the seeded actor with `times_reported = 3` on your demo topic; kill shot rehearsed cold.

### A6 · 2:45 → · Join B for demo hardening
Merge to `demo-prep`. From here, you're pair-programming with Person B, not on your own branch.

---

## Track B — Intelligence, Actions, Orchestration, Demo (Person B)

**Owns files:**
- `pipeshift/mistral_classify.py` — the classification / extraction + reply drafting
- `pipeshift/deepseek_fix.py` — the targeted fix generator
- `insforge/schema.sql` and `insforge/category_policy_seed.sql`
- `insforge/edge_functions/policy_read.ts` — serves the current `category_policy`
- `insforge/edge_functions/memory_dashboard.ts` — read-only view for the demo
- `router/route.py` — pure function `(facts, policy) → RoutingDecision`
- `actions/linear.py`, `actions/github.py`, `actions/reply.py`
- `pipeline.json` — the RocketRide pipeline definition
- `ui/` — the live input box, connector toggles, results panel

**Does not touch:** `hydra/`, `contracts/` (except at sync points).

**LLM context block for Cursor:**
```
PROJECT: GhostThread. 2-person hackathon build. My track (B) owns
Pipeshift, InsForge, RocketRide, Linear/GitHub/reply actions, and
the demo UI. My partner (A) owns HydraDB.

CORE PITCH: every complaint-handling agent reads one message; we
read the history. My pipeline calls A's memory read before doing
anything else — the classification, the ticket body, the reply
tone, and the scope of the code fix all reflect it.

CONSTRAINTS:
- Every one of the 13 categories has a policy entry — no undefined
  fallthrough, ever.
- No category name appears as a string literal driving an if/else in
  action-dispatch code — only in the policy lookup. If I violate
  this, the "not hardcoded" claim is false.
- The "zero redeploy" proof is an InsForge branch promotion, not a
  local file edit — set that up deliberately.
- sandbox_repo_allowlist is the one hardcoded exception in the whole
  system. The fix agent checks it before ever opening a PR. Never
  touches a real repo. Never auto-merges.
- Idempotent on complaint.id — no double ticket, no double reply.
- Reply drafting is grounded only in retrieved facts. Never invents
  a ticket status.

INTERFACE: ExtractedFacts, RoutingDecision, ResolutionAction,
LeakVerdict (from A), MemoryReadResult (from A) — see contracts/types.
```

### B1 · 11:00–12:00 · Two Pipeshift endpoints + InsForge schema
Files: `pipeshift/mistral_classify.py`, `pipeshift/deepseek_fix.py`, `insforge/schema.sql`, `insforge/category_policy_seed.sql`.
- Mistral: classification + extraction prompt. Include memory-read summary in the input so the model can populate `times_reported_by_actor`, `reply_tone`, `regression_evidence`. Start from §5's table, extend to ~20 few-shot examples.
- DeepSeek: given a `regression_evidence` string + file contents from `targeted_files`, propose a diff. Prompt explicitly for confidence.
- InsForge: run `create ghostthread`, run the migration, seed the `category_policy` table with the full JSON from the previous plan (13 categories), plus `global_overrides` including `sandbox_repo_allowlist: ["testteam-eng/sandbox-app"]`.
- **Accept:** both models return valid JSON on 20/20 test messages; `category_policy` reads live from InsForge with all 13 rows present.

### B2 · 12:00–1:00 (crosses lunch) · Router + actions
Files: `router/route.py`, `actions/linear.py`, `actions/github.py`, `actions/reply.py`, `insforge/edge_functions/policy_read.ts`.
- Router: pure `route(facts, policy) → RoutingDecision`. Handles `multi_intent`, low confidence → forced escalate, `internal_notice` → never customer-facing.
- Linear: create/update with dedup against existing tickets for the same issue cluster.
- GitHub: PR-only against the allowlisted sandbox repo, checked in code, never auto-merge. If `regression_evidence` is null, PR title flags "broad search — low confidence."
- Reply: three tone templates (`first_contact | returning | escalation`), all grounded on real facts, never inventing ticket status.
- Edge function serves the current policy JSON to the pipeline.
- **Accept:** no category string literal in action-dispatch code; filing the same complaint twice doesn't duplicate a ticket; PR opens only against allowlist; reply tone changes visibly when the memory read shows a repeat contact.

### B3 · 1:00–2:00 · Pipeline + live deploy
Files: `pipeline.json`.
- Define the 8-node pipeline (§ Fold-in above): ingest → memory read → leak detect → classify → policy → route → actions → memory write.
- Nodes 2, 3 (memory read + leak detect) call functions in `hydra/` — Person A's code.
- Node 4 (classify) reads from memory-read output, so `pipeline.json` needs to route data correctly between A's and B's nodes. This is the reason the 1:15 sync point exists.
- Deploy to RocketRide Cloud. Test from a phone hotspot — the endpoint must be reachable off your laptop's wifi.
- **Accept:** deployed live URL runs the same `pipeline.json` locally and in cloud; reachable from an unrelated network; one end-to-end test through the real pipeline returns a complete `ResolutionAction`.

### B4 · 2:00–2:45 · UI + memory dashboard
Files: `ui/`, `insforge/edge_functions/memory_dashboard.ts`.
- Live input box → hits real RocketRide endpoint. No mocks.
- Connector toggles (4 checkboxes) → wire to A's kill shot. Default all on.
- Results panel: classification, memory-read summary, routing decision, action taken, leak verdict + confidence, cost counter.
- Memory dashboard: separate route that queries HydraDB Memories through an InsForge edge function and shows the top recurring actors/topics. This is the "prize track for memory" evidence.
- **Accept:** typing a novel message returns a fresh result in ~few seconds; toggle to Slack-only flips verdicts visibly; memory dashboard shows the seeded actor with 3 prior contacts before the demo starts.

### B5 · 2:45 → · Pair with A on demo hardening
Both of you on `demo-prep` from here.

---

## 2:45–4:00 shared block — demo hardening
- Wire the `category_policy` branch-promotion demo bit. Which policy line changes? Which branch? What message re-runs? Time it.
- Three full run-throughs of §12's demo script, timed. Fix whatever breaks.
- Both people should be able to run the demo alone — no single point of failure.
- Insurance: capture a screen recording of a full clean run at 3:30. If everything goes wrong at 5 PM, you play the recording and disclose. This is Groundskeeper's fallback pattern and it's a good one.

---

## The demo script — memory-first version

3-ish minutes total; the 90-second HydraDB segment is fixed.

| time | beat |
|---|---|
| 0:00–0:15 | Cold open, verbatim: "Every complaint-handling agent out there reads one message and acts on it. We read the history." |
| 0:15–0:30 | **[required 90s starts]** State the impossible question — "which complaints never became work, and for this specific customer, what have they told us before?" Show the connector toggles, all four checked. |
| 0:30–1:10 | Run the query live. Show the leak count. Click into one leak — the raw Slack message, no matching ticket. Then show the memory panel next to it: "this same customer complained twice before, once about the same component." That's the moment memory becomes visible. |
| 1:10–1:45 | **The kill shot.** Uncheck GitHub. Re-run the same message. `regression_evidence` goes null. The generated PR now targets 14 files instead of 2. Say the asymmetry line. **[required 90s ends]** |
| 1:45–2:20 | Judge-typed live message through the full pipeline. Prompt: "try a bug about our CSV export, then something ambiguous." Show the tone difference on the reply for the first-contact case vs. a message from a seeded repeat actor. |
| 2:20–2:45 | InsForge branch promotion — flip one policy line (e.g. lower `min_confidence_for_autoaction` from 0.65 to 0.85). Re-run. Watch the routing change. Zero redeploy. |
| 2:45–3:05 | Show the pre-generated sandbox PR from a real seeded bug — be upfront it's not from the last 10 seconds, the mechanism is real. Mention live RocketRide Cloud endpoint. Cost counter shows the total. |
| 3:05–3:15 | Close: "It's not a status bot. It reads the history and only acts where a specific human report actually got dropped — with a reply that reflects what we already know about them." |

---

## Talking points to memorize
- "Every complaint agent out there reads one message. We read the history."
- "Take away the memory and it's a status bot. That's what the kill shot proves — twice, in two different ways."
- "The reply tone isn't a template. It's a function of how many times this customer has told us this already."
- "Dropping a complaint-side connector barely matters. Dropping GitHub loses regression evidence, and the fix becomes a guess. That asymmetry is the whole point."
- "The sandbox allowlist and the never-auto-merge rule are the only two hardcoded things in this whole system. On purpose. Everything else — policy, tone, severity — is data."

---

## Fallback playbook (from Groundskeeper — kept because it's good)

| failure | response |
|---|---|
| Kill shot doesn't visibly change anything | Something in the query path is cached. Audit for it — this is disqualifying if unfixed. |
| Pipeshift DeepSeek too slow live | Switch fix generation to Mistral temporarily. Announce: "we'd deploy DeepSeek in production; using Mistral for demo speed." |
| RocketRide Cloud endpoint slow / unreachable | Run pipeline locally, same `pipeline.json`, same runtime. Announce that. Not ideal but not disqualifying. |
| Live input doesn't fire in demo | Have curl commands ready. Announce: "triggering via API to save time." Result is the same. |
| HydraDB Memories empty at demo time | You seeded in A5. If somehow gone, the fallback is running 3–4 test resolutions right before demo. |
| Judge types something off-topic | `spam_or_unrelated` / `unclear` should handle it. Point out live that it correctly does nothing. |
| Wifi dies at venue | RocketRide endpoint is cloud; test on cellular beforehand. |
| Cursor merge conflict | The file ownership rules are why this shouldn't happen. If it does anyway, whoever's file it is by ownership wins; the other person re-applies their change on top. |

---

## Submission checklist
- [ ] Public repo, live RocketRide Cloud endpoint (tested off your own network)
- [ ] `category_policy` in InsForge, separate from app code, with a branch history showing at least one promotion
- [ ] HydraDB Memories seeded with your demo actor's prior contacts
- [ ] Eval suite committed and passing
- [ ] Kill shot rehearsed 3x, both flavors (all-Slack scope AND no-GitHub scope) tested
- [ ] 90-second HydraDB segment timed on its own
- [ ] Backup screen recording of a clean run captured at ~3:30 (Groundskeeper's insurance pattern)
- [ ] Project description explicitly maps all four sponsor techs and makes the "read the history" argument first
- [ ] Sandbox allowlist verified in code
- [ ] (Bonus) Social track — LinkedIn post + Instagram follow + Discord join (RocketRide free $1,000 entry)
