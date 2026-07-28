# GhostThread

**The question:** *Which customer complaints in Slack and Gmail never became tracked work in Linear or GitHub — and who is still waiting on an answer?*

No single tool can answer that. Slack knows people complained. Linear knows what got tracked. Neither knows what fell between them. GhostThread joins all four through HydraDB's context graph, finds the complaints that leaked, and then closes the loop: it extracts structured facts with a Pipeshift-hosted model, files the ticket, drafts a fix when the issue is small and code-shaped, and replies to the human who reported it.

Built at Agents You Love 2, Frontier Tower SF.

---

## The 90 seconds

```bash
make demo      # serves the UI on http://127.0.0.1:8000
```

1. **State the question.** It is printed at the top of the UI.
2. **Answer it.** Hit *Run across all four sources*. Six leaks, each with the signal breakdown that produced the verdict, the extracted facts, and the action taken.
3. **Break it.** Hit *Run the kill shot*. The same question is re-asked with fewer connectors:

| sources | answerable | leaks reported | false leaks | invisible | F1 |
|---|---|---|---|---|---|
| slack | 0% | 0 | 0 | 3 | 0.00 |
| slack + linear | 100% | 5 | 2 | 3 | 0.55 |
| slack + gmail + linear | 100% | 9 | 3 | 0 | 0.80 |
| **all four** | **100%** | **6** | **0** | **0** | **1.00** |

Those numbers are computed at run time from whatever is loaded, not asserted. Scoped to Slack alone the question is not merely harder, it is **unanswerable** — there is nothing to join against, so every complaint returns `unknown_insufficient_sources`. Add Linear and it starts lying: two complaints that were genuinely fixed in GitHub get reported as leaks, because the scope cannot see GitHub.

---

## How the four mandatory technologies are load-bearing

**HydraDB — real-time grounding.** All four connectors ingest into one database, one `sub_tenant_id` per source. Retrieval is always scoped by an explicit source list, and that list is threaded from the API call all the way down to `client.query(sub_tenant_ids=...)` — a scoped run genuinely never sees the data it is not allowed to see. `graph_context=True` supplies the cross-tool entity links; `relations.ids` on ingest is what builds them. Remove HydraDB and there is no join, which is the entire product.

**Pipeshift — model specialisation.** Every leak goes through a constrained decode against a fixed JSON schema (`response_format: json_schema`, `strict: true`) on a small open model. It returns `what_broke`, `is_code_issue`, `file_hint` and `severity`. Those four fields *are* the control flow: `severity` versus `risk_threshold` decides escalation, `is_code_issue` decides whether a coding agent runs at all. A wrong extraction makes the agent do the wrong thing, which is what load-bearing means.

**InsForge — enterprise intent.** The intent profile is a row in an InsForge table, read at call time with a five-second TTL. Every threshold, weight and permission in the system comes from it. You can edit the policy in the UI mid-demo, re-run, and watch verdicts and actions change with no code deployment. *(InsForge has no first-class "intent profile" primitive — it's a `json` column addressed by key. Documented rather than pretended.)*

**RocketRide Cloud — managed pipeline.** `rocketride/ghostthread.pipe` is the deployed node graph: `webhook → agent → {ghostthread_run, ghostthread_killshot} → response`. See the note on the split below.

### Why the heavy lifting lives behind RocketRide's HTTP tool nodes

RocketRide's engine is C++ with a node model where custom nodes must be compiled into the engine, and the built-in `tool_python` node runs a hard sandbox that blocks `import` entirely. Arbitrary PyPI dependencies — which we need for `hydradb-sdk` — cannot run inside a node on managed Cloud. So RocketRide owns orchestration, agent loop, tool routing and the public endpoint, and calls this service through `tool_http_request`. That is the supported integration shape, not a workaround.

---

## Architecture

```
slack ─┐                                        ┌─ file Linear ticket
gmail ─┤                                        ├─ sandboxed coding agent (Claude/OpenAI)
       ├─► HydraDB ─► identity ─► scoped ─► Pipeshift ─► policy ─┤
linear ┤  (context     resolution   join     extraction   gate   ├─ reply to the reporter
github ┘   graph)                                                └─ escalate to a human
                                                    ▲
                                          InsForge intent profile
                                            (read at call time)
```

| Module | Responsibility |
|---|---|
| `contracts.py` | The five frozen shapes everything speaks |
| `connectors.py` | Four read connectors, live API or corpus, identical output |
| `hydra.py` | Ingest + source-scoped retrieval, with a degraded local index |
| `resolve.py` | Cross-source entity resolution (member id = address = handle) |
| `leaks.py` | The join, the verdict, and the confidence — all profile-driven |
| `extract.py` | Pipeshift structured extraction |
| `act.py` | Ticket, sandboxed fix, reply, escalate |
| `killshot.py` | Scope degradation, scored against the full-source answer |
| `pipeline.py` | Orchestration |
| `api.py` | HTTP surface for RocketRide and the UI |

---

## The anti-hardcoding guarantee

```bash
make verify
```

Three checks, and the demo runs them live:

1. **No demo entity name appears anywhere in `src/`.** Derived from the corpus, so it cannot go stale. It has already caught a docstring of mine.
2. **No inline decision thresholds.** An AST pass over `leaks.py` and `act.py` flags any comparison against a numeric literal that isn't a structural constant. Every real threshold comes from the intent profile.
3. **A mutation test.** It adds tracked work for a currently-leaked complaint, re-runs, and fails if the verdict doesn't flip. Memorised answers don't react to the data changing.

Beyond that: swap `fixtures/corpus.json` for any other corpus and the pipeline recomputes from scratch, and the *Live complaint* box in the UI takes anything a judge types and runs it through the identical path.

---

## Running it

```bash
python3.11 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env          # fill in what you have
make demo
```

Every integration degrades independently. With an empty `.env` the whole thing still runs end to end on a local TF-IDF index and a heuristic extractor, and the UI badges say `local` instead of `live` so nobody is misled about what is running. Fill in `HYDRA_TOKEN` and grounding goes live; fill in `PIPESHIFT_API_KEY` and extraction goes live. There is no mode where a missing credential silently fabricates an answer.

`DRY_RUN` defaults to **true**: tickets, patches and replies are computed and displayed but not sent. The coding agent only ever operates inside `sandbox_repo/`, which is created fresh and never points at a real repository.

```bash
make verify        # the three anti-hardcoding checks
make killshot      # the degradation table, no server needed
python scripts/seed_insforge.py    # once, to move the profile into InsForge
```

## Known gaps

- The `.pipe` graph is written against RocketRide's documented pipeline rules but the provider config schemas need validating against the `.rocketride/services-catalog.json` the VS Code extension generates on connect.
- Pipeshift exposes no fine-tuning on its public API, so "specialisation" here is constrained decoding on a small model rather than a fine-tune.
- Gmail and Slack write paths are implemented but only exercised in dry run.
