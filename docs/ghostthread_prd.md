# GhostThread — Product Requirements Document
**Agents You Love 2 Hackathon | July 28, 2026 | Frontier Tower, SF**
**2 builders · 5-hour window · Verified against real HydraDB, Pipeshift, RocketRide, and InsForge APIs**

---

## What It Is

GhostThread watches Slack and Gmail for customer complaints. When one arrives, it doesn't act on that one message. It reads the history — every prior complaint from the same customer, every prior ticket about the same component, every prior PR that touched implicated files — through HydraDB's context graph. It uses that memory to decide four things at once: has this been reported before, does tracked work already exist, what does the code fix need to target, and what tone should the reply take. Then it files a Linear ticket with the full historical context, opens a draft PR against a sandbox repo, and replies to the reporter in a tone that reflects whether they're new or on their third complaint about the same problem.

The loop runs without a human in the middle. Complaints that were already handled don't get re-actioned. Complaints that got dropped between tools finally get answered.

---

## The Differentiator — Say This Clearly

Every competitor in this space — Continue.dev's Slack agent, Lattice Bot, IrisAgent, Linear's Jira sync — reads **one message** and acts on it. A human has to tag the bot to invoke it. Nothing looks backward.

GhostThread reads **the history**. Before it files a ticket or touches code, HydraDB has already told it that this customer complained about the same thing six weeks ago in Gmail, that the Linear ticket claiming to fix it was closed too fast, and that a specific PR merged three weeks ago probably introduced the current regression. The ticket body carries that history. The PR targets specific files, not a broad codebase search. The reply's tone reflects that this is a repeat contact, not a first one.

Take the memory away and it's a status bot. That's what the kill shot proves.

---

## The Two Impossible Questions

**Question 1 — Leak detection (the required "impossible question"):**
*"Which complaints in Slack or Gmail never became tracked work in Linear or GitHub for the same underlying issue?"*

No single tool answers this. Slack can't see Linear. Linear can't see whether the customer who complained is still waiting. HydraDB's context graph joins them and answers in one query.

**Question 2 — Historical grounding (the memory pitch):**
*"For this specific customer and this specific component, what have we already seen — prior complaints, resolutions, and code changes — and how should that change what we do now?"*

No support tool, ticket tracker, or code intelligence tool holds all four contexts at once. HydraDB does.

Both questions run against the same connectors and the same context graph. They're the same product.

---

## Full Pipeline — 8 Nodes

### Trigger: live input (demo) or webhook (production)

For the hackathon demo, complaints arrive from a live input box in the UI that POSTs directly to the RocketRide Cloud endpoint. In production, InsForge edge functions on Slack Events and Gmail push webhooks fan into the same endpoint. Same pipeline either way.

### RocketRide Pipeline

```
[SOURCE: webhook / live input]
       │
[N1] HydraDB — Memory Read
     Query type: "memory"
     Keyed on: resolved actor + complaint topic
     Returns: prior complaints from this actor, prior resolutions on this
              topic, likely regression PR/commit implicated by history
       │
[N2] HydraDB — Leak Detection (Knowledge Query)
     POST /query with type: "knowledge", query_by: "hybrid",
     graph_context: true, mode: "thinking"
     Returns LeakVerdict: leak | resolved | unknown, with confidence and
     sources_used
       │
[N3] Pipeshift Mistral-7B — Classify + Extract
     Input: complaint text + memory summary from N1
     Output JSON (ExtractedFacts): {
       category (13 options),
       confidence, actor_type, sentiment, urgency,
       is_code_issue, file_hint, severity,
       times_reported_by_actor, times_seen_on_topic,
       reply_tone (first_contact | returning | escalation),
       regression_evidence (PR/commit ref or null),
       references_existing_ticket
     }
       │
[N4] InsForge — category_policy Lookup
     Reads live policy JSON via edge function
     Returns action set + human approval flag + escalation contact
       │
[N5] Router — Pure Function
     route(facts, policy) → RoutingDecision
     Actions: file_ticket, propose_sandbox_fix, reply, escalate, log_only
       │
       ├── low confidence (<0.65) → force escalate, skip N6-N8
       ├── spam_or_unrelated → no action
       │
[N6] Actions
     ├── Linear GraphQL — create/update ticket
     │   Title includes severity + root_cause_hypothesis
     │   Body carries full memory summary (this is the visible payoff)
     │   Dedup against existing tickets for the same issue cluster
     │
     ├── Pipeshift DeepSeek Coder — Generate Fix (if regression_evidence)
     │   Input: root_cause + regression_evidence + file contents
     │   Output: diff, explanation, confidence_score
     │   Gate: sandbox_repo_allowlist checked in code
     │
     ├── GitHub REST — Open Draft PR
     │   Branch: fix/bug-{ticket_id}
     │   NEVER auto-merge. Confidence < 0.70 → labeled low-confidence.
     │
     └── Reply
         Slack thread reply / Gmail reply
         Tone from ExtractedFacts.reply_tone
         Grounded only in retrieved facts, never invented
       │
[N7] Memory Write
     HydraDB POST /context/ingest with type: "memory"
     Persists: {actor, complaint_summary, category, action_taken,
                ticket_url, resolved_at}
     This is what makes the NEXT complaint smarter than this one.
       │
[N8] InsForge Postgres — Idempotency Log
     Writes ResolutionAction to actions_log table
     Keyed on complaint.id to prevent double-processing
     Also feeds the memory dashboard shown on stage
```

---

## Why Each Sponsor Technology Is Load-Bearing

### HydraDB

Two roles, both essential.

**Role 1 — Knowledge (leak detection):** Cross-source topical join. A Slack complaint about "CSV export throwing errors" matches a Linear ticket titled "export fails with 500" through hybrid search, whether or not they share exact words. This is the impossible question.

**Role 2 — Memories (episodic history):** Per-actor and per-topic history that persists across sessions. This is the memory pitch. First contact → warm reply. Third complaint about the same thing → escalated tone. Not a template — a function of what the memory returns.

**Kill shot — two flavors, both real.** Both are single `POST /query` calls with `metadata_filters` scoping to specific `connector_id`s. No UI trick.

- **Flavor A — Slack only:** Scope query to Slack `connector_id` only. Every leak/resolved verdict flips to `unknown`. HydraDB genuinely cannot see Linear or GitHub, so it refuses to answer instead of guessing.
- **Flavor B — no GitHub:** Scope OFF GitHub only. `regression_evidence` goes null. The sandbox fix loses its target files and searches broadly — PR balloons from 2 files to 14. Fix quality visibly degrades.

Flavor B is the demo-primary because it's more visually dramatic and directly serves the memory pitch. Flavor A stays in reserve for Q&A.

**Real call:**
```python
# Memory read (Node 1)
memory = await hydra.query(
    database=DATABASE_ID,
    query=f"{actor_email} complaints about {topic}",
    type="memory",
    query_by="hybrid",
    graph_context=True,
    max_results=10
)

# Leak detection (Node 2), same endpoint, different type
leak = await hydra.query(
    database=DATABASE_ID,
    query=complaint_text,
    type="knowledge",
    query_by="hybrid",
    graph_context=True,
    mode="thinking",
    metadata_filters={
        "additional_metadata": {
            "connector_id": active_connectors  # kill-shot handle
        }
    }
)
```

### Pipeshift — Two Deployed Models

**Mistral-7B (Nodes 3 + reply drafting):** Fast, structured JSON output, strong classification. Deployed on Pipeshift with MAGIC-optimized inference for latency. Powers the classify-and-extract call and the reply generator.

**DeepSeek Coder (Node 6 fix generation):** Purpose-built for code understanding. When given `regression_evidence` from HydraDB's memory read, it targets specific files — dramatically better than general models, which hallucinate paths.

**Judge answer for "why two models":** Different tasks need different specializations. Mistral is fast and structured for classification and replies. DeepSeek understands code and produces better diffs when given precise regression context. Serving both on Pipeshift's dedicated endpoints gives us controllable SLA per task.

### RocketRide Cloud

Portable `pipeline.json`, developed against the local runtime in VS Code, one-click deployed to `cloud.rocketride.ai`. Webhook source node hit by the live input box during the demo. Eight nodes execute visibly in the Cloud dashboard.

Remove RocketRide → you have a Python script on a laptop. Not cloud-deployed. Not observable during the demo. Explicitly disqualified by the brief.

### InsForge — Three Roles

**Role 1 — `category_policy` intent profile:** All routing logic lives in a Postgres table Cursor provisions through InsForge. Served to the pipeline via edge function. 13 categories, each with allowed actions, approval flags, and escalation contacts.

**Role 2 — Branching for dev-to-deployment lifecycle:** The "no hardcoded logic" requirement is proven live by promoting an InsForge branch to production on stage. Edit a policy line, promote, watch behavior change with zero redeploy. This is the actual InsForge feature, not a fake.

**Role 3 — Idempotency + memory dashboard:** `actions_log` table prevents double-processing on retried webhooks. A separate query into HydraDB Memories (via InsForge edge function) powers the dashboard showing "this customer has complained 3 times about auth" — the visible memory payoff on stage.

```bash
# Setup
npx @insforge/cli login
npx @insforge/cli create ghostthread
npx @insforge/cli db migrations new create-ghostthread-schema
npx @insforge/cli db migrations up --all
npx @insforge/cli functions deploy policy-read --file ./functions/policy-read.ts
npx @insforge/cli functions deploy memory-dashboard --file ./functions/memory-dashboard.ts
```

---

## Data Contracts

```typescript
// Raw ingested complaint, normalized across sources
type Complaint = {
  id: string;
  source: "slack" | "gmail";
  actor_email: string | null;
  actor_display_name: string;
  raw_text: string;
  thread_id: string | null;
  timestamp: string;  // ISO 8601
};

// Memory read result (Node 1 output)
type MemoryReadResult = {
  actor: string;
  times_reported_by_actor: number;
  times_seen_on_topic: number;
  prior_resolutions: Array<{ ticket_url: string; resolved_at: string; summary: string }>;
  likely_regression: { source: "github" | "linear"; ref: string; url: string } | null;
};

// Leak-detection result (Node 2 output)
type LeakVerdict = {
  issue_cluster_id: string;
  verdict: "leak" | "resolved" | "unknown";
  confidence: number | null;   // null whenever verdict === "unknown"
  complaint_ids: string[];
  matched_work_items: Array<{ source: "linear" | "github"; id: string; url: string }>;
  sources_used: string[];      // connector_ids in scope
  sources_missing: string[];   // connector_ids out of scope
};

// Classification + extraction output (Node 3)
type ExtractedFacts = {
  category:
    | "genuine_bug" | "user_error" | "question" | "feature_request"
    | "feedback_positive" | "feedback_negative" | "duplicate_or_known_issue"
    | "security_concern" | "billing_or_account" | "outage_or_urgent"
    | "spam_or_unrelated" | "internal_notice" | "unclear";
  confidence: number;
  actor_type: "customer" | "internal_employee" | "unknown" | "automated";
  sentiment: "neutral" | "frustrated" | "angry" | "positive";
  urgency: "low" | "medium" | "high" | "critical";
  is_code_issue: boolean | null;
  file_hint: string | null;
  severity: "low" | "medium" | "high" | null;
  times_reported_by_actor: number;
  times_seen_on_topic: number;
  reply_tone: "first_contact" | "returning" | "escalation";
  regression_evidence: string | null;
  multi_intent: boolean;
  sub_facts: ExtractedFacts[] | null;
  references_existing_ticket: string | null;
};

// Router output (Node 5)
type RoutingDecision = {
  complaint_id: string;
  category: ExtractedFacts["category"];
  actions: ("file_ticket" | "propose_sandbox_fix" | "reply" | "escalate" | "log_only" | "link_existing_ticket")[];
  requires_human_approval: boolean;
  escalation_contact: string | null;
};

// Final logged outcome (Node 8)
type ResolutionAction = {
  complaint_id: string;
  actions_taken: RoutingDecision["actions"];
  ticket_url: string | null;
  pr_url: string | null;
  pr_confidence: number | null;
  reply_sent: boolean;
  escalated: boolean;
  memory_write_id: string | null;
  cost_usd: number;
  latency_ms: number;
  timestamp: string;
};
```

---

## Schema (InsForge Postgres)

```sql
CREATE TABLE actions_log (
    id                    UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    complaint_id          TEXT UNIQUE NOT NULL,       -- idempotency key
    received_at           TIMESTAMPTZ DEFAULT NOW(),
    source                TEXT NOT NULL,              -- 'slack' | 'gmail'
    actor_resolved        TEXT,
    complaint_text        TEXT NOT NULL,
    category              TEXT,
    confidence            FLOAT,
    reply_tone            TEXT,
    times_reported_actor  INTEGER,
    times_seen_topic      INTEGER,
    regression_evidence   TEXT,
    verdict               TEXT,                       -- 'leak' | 'resolved' | 'unknown'
    verdict_confidence    FLOAT,
    actions_taken         TEXT[],
    ticket_url            TEXT,
    pr_url                TEXT,
    pr_confidence         FLOAT,
    reply_sent            BOOLEAN,
    escalated             BOOLEAN,
    cost_usd              NUMERIC(8,4),
    latency_ms            INTEGER
);

CREATE TABLE category_policy (
    category              TEXT PRIMARY KEY,
    allowed_actions       TEXT[] NOT NULL,
    requires_human_approval BOOLEAN DEFAULT FALSE,
    escalation_contact    TEXT,
    autofix_max_severity  TEXT,
    customer_facing       BOOLEAN DEFAULT TRUE,
    updated_at            TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE global_overrides (
    key                   TEXT PRIMARY KEY,
    value                 JSONB NOT NULL
);

-- Memory dashboard view (fed from actions_log; HydraDB Memories is the source of truth)
CREATE VIEW recurring_reporters AS
SELECT
    actor_resolved,
    COUNT(*)                                  AS times_reported,
    MAX(received_at)                          AS last_seen,
    ARRAY_AGG(DISTINCT category)              AS categories,
    ROUND(AVG(confidence)::numeric, 2)        AS avg_confidence
FROM actions_log
WHERE actor_resolved IS NOT NULL
GROUP BY actor_resolved
ORDER BY times_reported DESC;
```

---

## The 13-Category Taxonomy

| category | what it means | default policy |
|---|---|---|
| `genuine_bug` | real product defect | ticket + (if small) sandbox fix + reply |
| `user_error` | works as intended, user misunderstood | reply only |
| `question` | how-to / clarification | reply only |
| `feature_request` | asking for something that doesn't exist | ticket tagged feature, no fix |
| `feedback_positive` | praise, thanks | optional reply |
| `feedback_negative` | general dissatisfaction, no specific defect | log + optional reply |
| `duplicate_or_known_issue` | matches existing ticket | link existing, reply |
| `security_concern` | possible vulnerability | **always escalate**, human approval |
| `billing_or_account` | payment / subscription | escalate to billing |
| `outage_or_urgent` | broad / severe (site down) | ticket + **immediate escalate** |
| `spam_or_unrelated` | not actually a complaint | no action |
| `internal_notice` | employee's own "I broke X" | internal ticket, never customer-facing |
| `unclear` | low-confidence catch-all | always escalate, never auto-act |

Confidence below 0.65 always forces `unclear` regardless of category guess.

---

## `category_policy` — Seed JSON

```json
{
  "category_policy": {
    "genuine_bug":              { "allowed_actions": ["file_ticket", "propose_sandbox_fix", "reply"], "requires_human_approval": false, "autofix_max_severity": "low" },
    "user_error":               { "allowed_actions": ["reply"], "requires_human_approval": false },
    "question":                 { "allowed_actions": ["reply"], "requires_human_approval": false },
    "feature_request":          { "allowed_actions": ["file_ticket_tagged_feature", "reply"], "requires_human_approval": false },
    "feedback_positive":        { "allowed_actions": ["reply"], "requires_human_approval": false },
    "feedback_negative":        { "allowed_actions": ["log_only", "reply"], "requires_human_approval": false },
    "duplicate_or_known_issue": { "allowed_actions": ["link_existing_ticket", "reply"], "requires_human_approval": false },
    "security_concern":         { "allowed_actions": ["escalate"], "requires_human_approval": true, "escalation_contact": "security-lead" },
    "billing_or_account":       { "allowed_actions": ["escalate"], "requires_human_approval": true, "escalation_contact": "billing-ops" },
    "outage_or_urgent":         { "allowed_actions": ["file_ticket", "escalate"], "requires_human_approval": true, "escalation_contact": "on-call" },
    "spam_or_unrelated":        { "allowed_actions": [], "requires_human_approval": false },
    "internal_notice":          { "allowed_actions": ["file_ticket_internal"], "requires_human_approval": false, "customer_facing": false },
    "unclear":                  { "allowed_actions": ["escalate"], "requires_human_approval": true, "escalation_contact": "triage-owner" }
  },
  "global_overrides": {
    "min_confidence_for_autoaction": 0.65,
    "never_automerge": true,
    "sandbox_repo_allowlist": ["testteam-eng/sandbox-app"]
  }
}
```

The `sandbox_repo_allowlist` and `never_automerge` values are the only two hardcoded rules in the entire system. Everything else — categories, actions, tones, severity — is data.

---

## Reply Tone Logic

Derived from `times_reported_by_actor` and `times_seen_on_topic` in `MemoryReadResult`:

| condition | tone | What the reply sounds like |
|---|---|---|
| Actor has 0 prior complaints | `first_contact` | Warm, professional, no assumed history |
| Actor has 1–2 prior complaints on this or related topics | `returning` | Acknowledges prior history directly, doesn't pretend this is new |
| Actor has 3+ prior complaints on this topic | `escalation` | Leads with accountability, specific about root cause, no boilerplate |

The escalation reply is the visible memory payoff on stage. Every judge who has been that frustrated customer will feel it.

---

## Confidence Gates

Two independent gates, both hard:

1. **Classification confidence** — if Mistral's `confidence` is below `min_confidence_for_autoaction` (0.65), the router forces category `unclear` and escalates regardless of what was guessed.
2. **Fix confidence** — DeepSeek's PR is always a draft. If confidence ≥ 0.70, labeled `ready for review`. If < 0.70, labeled `low confidence — needs human review`. **Never auto-merged, ever.**

---

## TestTeam — The Demo Tenant

TestTeam is the demo company GhostThread watches. Played by the builder's own personal Slack, Gmail, Linear, and GitHub, connected once to HydraDB. No OAuth flow to build — the requirement is connectors *used*, not a production auth system.

- **Slack** — `#testteam-support` channel where complaints land
- **Gmail** — support-style label
- **Linear** — "TestTeam Engineering" team with tickets (some corresponding to seeded complaints so the system has genuine `resolved` cases; some not, so it has genuine `leak` cases)
- **GitHub** — `testteam-eng/sandbox-app`, a small disposable app with 1–2 real, small, findable bugs (the CSV export null-check bug is the canonical seeded issue)

Each becomes one HydraDB connector with its own `provider_account_scope`.

---

## 90-Second Demo Script (with 3-min extended flow)

```
[0:00] Cold open: "Every complaint-handling agent out there reads one
       message and acts on it. GhostThread reads the history."

[0:15] [Required 90s starts.] State the impossible question. Show the
       four connector toggles, all checked.

[0:30] Run the leak query live. N complaints, K leaks with confidence.
       Click into one — raw Slack message, no matching ticket. Then
       show the memory panel next to it: "this customer has told us
       twice before. Same component both times."

[1:10] Kill shot (Flavor B — no GitHub). Uncheck GitHub. Re-run.
       regression_evidence goes null. PR balloons from 2 files to 14.
       "Same message. Half the memory. The fix becomes a guess."

[1:45] [Required 90s ends.] Judge-typed live message through the full
       pipeline. Prompt them loosely: "try a bug about CSV export,
       then something ambiguous." Show reply tone shift between a
       first_contact and a returning actor.

[2:20] InsForge branch promotion. Flip min_confidence_for_autoaction
       from 0.65 to 0.85. Promote. Re-run same message. Watch routing
       change. Zero redeploy.

[2:45] Show the sandbox PR opened earlier from a seeded bug. Be upfront:
       "This PR opened 30 minutes ago from a real seeded bug — the
       mechanism is what's real, not the timing." Cost counter shows
       the total.

[3:05] Close: "It's not a status bot. It reads the history and only
       acts where a specific human report actually got dropped —
       with a reply that reflects what we already know about them."
```

---

## Prize Targets

| Prize | Why GhostThread wins it |
|---|---|
| Best Use of Memory / Context | Memory-first pitch. HydraDB Memories powers reply tone, regression targeting, and repeat-contact detection. Visible dashboard proves it. |
| Best Workflow Agent | 8-node pipeline, 4 inbound sources, 4 outbound actions, cloud-deployed, observable in RocketRide dashboard. |
| Best Overall | End-to-end automation with a memory story no other team can match. |
| RocketRide $1,000 | Webhook-triggered pipeline live on `cloud.rocketride.ai`. Plus social track (LinkedIn + Instagram + Discord). |
| Best Agent People Love | The escalation reply. Every engineer has been the customer on their third complaint about the same issue. |

---

## Submission Checklist

```
[ ] Public repo with README explaining the full stack
[ ] Live RocketRide Cloud endpoint (tested off own network via phone hotspot)
[ ] 4 HydraDB connectors synced (Slack + Gmail + Linear + GitHub)
[ ] HydraDB Memories seeded — demo actor shows 3 prior contacts
[ ] Kill shot verified both flavors (Slack-only AND no-GitHub)
[ ] category_policy in InsForge with branch history showing ≥1 promotion
[ ] Pipeshift: Mistral + DeepSeek Coder both deployed, both used in pipeline
[ ] Sandbox allowlist verified in code
[ ] Eval suite committed and passing
[ ] Idempotency verified — same complaint.id filed twice = one ticket
[ ] 90-second demo timed ≥3 times
[ ] Backup screen recording captured at ~3:30 as insurance
[ ] Project description explicitly maps all 4 sponsor technologies
[ ] Social track: LinkedIn post + Instagram follow + Discord join
```

---

## The One-Paragraph Judge Answer

*If asked "why is this different from every other Slack bug bot"*:

Every other Slack bug bot in this space — Continue, Lattice, IrisAgent — reads one message and acts on it. Someone has to tag the bot, and the bot has no memory of what came before. GhostThread reads the history first. Before it decides anything, HydraDB has already surfaced every prior complaint from this customer, every related resolution, and the specific PR that likely introduced the regression. The Linear ticket carries that history. The sandbox PR targets specific files, not a broad search. The reply's tone reflects whether this is their first contact or their third. Take the memory away and the fix quality drops visibly — that's the kill shot. Every sponsor technology is load-bearing: HydraDB is the memory, Pipeshift specializes classification vs. code generation, InsForge holds the policy and proves the dev-to-deployment lifecycle through live branch promotion, RocketRide runs the pipeline observably in the cloud. It's not a status bot.
