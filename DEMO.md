# The 90 seconds

Rehearse this. The four mandatory beats are **question → answered → broken → live input**. Everything else is cuttable.

## Before you start

```bash
make verify     # green, leave the output on screen behind you
make demo
```

Check the badges in the top right. Every one you can turn from `local` to `live` before demoing is worth more than any feature you could add in the same time. Priority order: HydraDB, Pipeshift, InsForge, coding agent.

---

## 0:00–0:15 — The question

> "Anyone can search Slack. Anyone can search Linear. Neither one can tell you which customer complaints *never became tracked work* — because that answer doesn't live in either tool, it lives in the gap between them."

Point at the question banner. Say it out loud, exactly as written.

## 0:15–0:40 — Answered

Click **Run across all four sources**.

> "Twelve complaints across Slack and Gmail. Six of them were tracked. Six leaked — nobody filed them, and nobody replied. That's 690 customer-hours of people waiting on an answer that was never coming."

Point at one leak card:

> "This isn't a vibe. Identity resolution matched the reporter across three tools, retrieval found no plausible work item, and the timing signal rules out the one near-miss. Score 0.15 against a threshold of 0.55 — and that threshold comes from InsForge, not from the code."

## 0:40–1:05 — Broken (the kill shot)

Click **Run the kill shot**.

> "Same question, fewer connectors. Slack alone: zero percent answerable — not *wrong*, **unanswerable**, because there's nothing to join against.
>
> Add Linear and it starts *lying to you*. Six leaks reported, two of them false — those two were fixed in GitHub, and this scope can't see GitHub. Three more complaints are invisible entirely because they came in over email.
>
> All four sources: F1 goes to 1.0. That's the join HydraDB does before you ask."

Let the F1 column do the work: **0.00 → 0.62 → 0.82 → 1.00**.

## 1:05–1:30 — It's real, and it acts

Type a complaint into **Live complaint** — take one from a judge if you can:

> "Nothing here is memorised. Type anything."

Submit. Then:

> "Pipeshift extracted the structured facts — what broke, is it code, how severe. That severity is what routes it: below the risk threshold and code-shaped, so a sandboxed coding agent drafts the fix. Ticket filed, and the person who reported it gets this reply. That's the loop closed."

Point at the drafted reply.

---

## If you have 15 seconds spare

Change `risk_threshold` in the **Intent profile** box, save, re-run. The same complaint routes to `escalate` instead of `auto_fix`.

> "Policy lives in InsForge and is read on every call. I just changed what the agent is allowed to do, with no deploy."

This is the single most convincing anti-hardcoding move available. Use it if the clock allows.

---

## Answers to the questions you will get

**"Is this hardcoded?"** → `make verify`. Three checks: no demo entity name appears in `src/`, an AST pass proves no decision threshold is inlined, and a mutation test adds tracked work for a leaked complaint and fails if the verdict doesn't flip.

**"What if HydraDB is down?"** → It degrades to a local TF-IDF index and the badge flips to `local`. Recall drops, which you can show. It never fabricates.

**"Why isn't the whole pipeline inside RocketRide?"** → RocketRide's nodes can't install arbitrary PyPI packages on managed Cloud, and `tool_python` blocks `import`. So RocketRide owns the agent loop, tool routing and the public endpoint, and reaches this service through `tool_http_request` — the supported shape.

**"Did you fine-tune?"** → No. Pipeshift exposes no fine-tuning on its public API. Specialisation is constrained decoding against a strict JSON schema on a small open model, which is what makes the extraction reliable enough to drive control flow.

**"Will it email real people?"** → `DRY_RUN` defaults to true. Every side effect is computed and displayed, nothing is sent. The coding agent only touches a disposable sandbox repo.
