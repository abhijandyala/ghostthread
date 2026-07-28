/**
 * The memory dashboard — read-only, and read-only in the strong sense.
 *
 * This is the "this customer has told us three times" panel, served straight
 * out of the `actions_log` table that pipeline node N8 writes. It is the PRD's
 * `recurring_reporters` view, computed in the function rather than as a SQL
 * view, because the InsForge REST surface provisions tables and not views and a
 * view we could not create is worse than an aggregate we can.
 *
 * HydraDB Memories remains the source of truth for what the pipeline *knows*.
 * This is the audit trail of what it *did*, which is the thing that is safe to
 * put on a screen: every row here is an action that was actually recorded, so
 * nothing on the dashboard can be ahead of what happened.
 *
 * The distinction this file is careful about
 * ------------------------------------------
 * An unprovisioned table and an empty table are different facts and they are
 * answered differently. A missing `actions_log` is a 503 naming the seed
 * script. A present-but-empty one is a 200 with `rows_scanned: 0` and a note
 * saying so. Collapsing those two into "the dashboard is empty" would let a
 * broken deployment read on stage as "nobody has complained yet", which is the
 * exact class of plausible-looking wrong answer this project refuses to
 * produce.
 *
 * Nothing here writes. There is no code path in this file that issues anything
 * other than a GET.
 *
 * Access
 * ------
 * `/functions/{slug}` is public. If a `GHOSTTHREAD_DASHBOARD_TOKEN` secret is
 * configured on the project, this function requires it as `?token=` or an
 * `X-GhostThread-Token` header. If it is not configured, the endpoint serves
 * openly and says `"access": "public"` in the payload rather than implying a
 * protection that is not there.
 *
 * Deploy:  PYTHONPATH=src python scripts/deploy_edge_functions.py
 * Invoke:  GET/POST {INSFORGE_BASE_URL}/functions/memory-dashboard
 *          ?limit=500       rows to scan, newest first (max 2000)
 *          &min_times=2     only reporters seen at least this many times
 *          &actor=a@b.com   drill into one reporter's history
 */

const DEFAULT_ACTIONS_TABLE = "actions_log";
const DEFAULT_LIMIT = 500;
const MAX_LIMIT = 2000;

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-API-Key, X-GhostThread-Token",
};

type Row = {
  complaint_id?: string;
  received_at?: string | null;
  source?: string | null;
  actor_resolved?: string | null;
  complaint_text?: string | null;
  category?: string | null;
  confidence?: number | null;
  reply_tone?: string | null;
  verdict?: string | null;
  actions_taken?: string[] | null;
  ticket_url?: string | null;
  pr_url?: string | null;
  escalated?: boolean | null;
  reply_sent?: boolean | null;
  dry_run?: boolean | null;
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body, null, 2), {
    status,
    headers: { ...CORS, "Content-Type": "application/json" },
  });
}

function env(name: string, fallback = ""): string {
  try {
    return Deno.env.get(name) || fallback;
  } catch {
    return fallback;
  }
}

function clampInt(raw: string | null, fallback: number, min: number, max: number): number {
  const parsed = Number.parseInt(raw ?? "", 10);
  if (!Number.isFinite(parsed)) return fallback;
  return Math.min(max, Math.max(min, parsed));
}

/** Newer of two ISO timestamps, tolerating nulls and unparseable values. */
function latest(a: string | null | undefined, b: string | null | undefined): string | null {
  if (!a) return b ?? null;
  if (!b) return a ?? null;
  return a > b ? a : b;
}

export default async function (request: Request): Promise<Response> {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: CORS });
  }

  const url = new URL(request.url);

  const gate = env("GHOSTTHREAD_DASHBOARD_TOKEN");
  if (gate) {
    const offered = url.searchParams.get("token") || request.headers.get("X-GhostThread-Token") || "";
    if (offered !== gate) {
      return json({ error: "this dashboard requires GHOSTTHREAD_DASHBOARD_TOKEN" }, 401);
    }
  }

  const table = url.searchParams.get("table") || env("INSFORGE_ACTIONS_TABLE", DEFAULT_ACTIONS_TABLE);
  const limit = clampInt(url.searchParams.get("limit"), DEFAULT_LIMIT, 1, MAX_LIMIT);
  const minTimes = clampInt(url.searchParams.get("min_times"), 1, 1, 1000);
  const onlyActor = url.searchParams.get("actor");

  const base = env("INSFORGE_BASE_URL").replace(/\/+$/, "");
  const apiKey = env("API_KEY");
  if (!base || !apiKey) {
    return json(
      {
        error: "memory_dashboard is not configured",
        detail: "INSFORGE_BASE_URL and API_KEY are not both present in the function environment",
      },
      500,
    );
  }

  const select = [
    "complaint_id", "received_at", "source", "actor_resolved", "complaint_text",
    "category", "confidence", "reply_tone", "verdict", "actions_taken",
    "ticket_url", "pr_url", "escalated", "reply_sent", "dry_run",
  ].join(",");

  let query = `${base}/api/database/records/${table}` +
    `?select=${select}&order=received_at.desc&limit=${limit}`;
  if (onlyActor) {
    query += `&actor_resolved=eq.${encodeURIComponent(onlyActor)}`;
  }

  let resp: Response;
  try {
    resp = await fetch(query, {
      headers: {
        "X-API-Key": apiKey,
        "Authorization": `Bearer ${apiKey}`,
        "Content-Type": "application/json",
      },
    });
  } catch (err) {
    return json({ error: "could not reach the actions log", detail: String(err).slice(0, 300), table }, 502);
  }

  const text = await resp.text();
  if (!resp.ok) {
    // 42P01 is Postgres' undefined_table. It means the project was never
    // provisioned, which is a different problem from "no complaints yet" and
    // must not render as an empty dashboard.
    const missing = text.includes("42P01") || text.toLowerCase().includes("does not exist");
    return json(
      {
        error: missing
          ? `the '${table}' table does not exist in this InsForge project`
          : "the actions log rejected the read",
        detail: missing
          ? "Run scripts/seed_insforge.py. This is reported as an error rather than as an " +
            "empty dashboard, because an empty dashboard would read as 'nobody has complained'."
          : text.slice(0, 400),
        status: resp.status,
        table,
      },
      missing ? 503 : 502,
    );
  }

  let rows: Row[];
  try {
    rows = JSON.parse(text);
  } catch (err) {
    return json({ error: "actions log returned an unparseable body", detail: String(err).slice(0, 200) }, 502);
  }
  if (!Array.isArray(rows)) rows = [];

  // --- aggregate ------------------------------------------------------------
  // Deliberately over the rows actually returned, and `rows_scanned` is
  // reported alongside `limit` so a truncated window is visible rather than
  // being presented as the whole history.

  type Reporter = {
    actor: string;
    times_reported: number;
    last_seen: string | null;
    first_seen: string | null;
    categories: string[];
    sources: string[];
    tones: string[];
    confidence_total: number;
    confidence_n: number;
    leaked: number;
    escalated: number;
    tickets: number;
    replies: number;
    latest_complaint: string | null;
  };

  const reporters = new Map<string, Reporter>();
  const topics = new Map<string, { category: string; times_seen: number; last_seen: string | null; actors: Set<string> }>();

  let withoutActor = 0;
  let leakedTotal = 0;
  let escalatedTotal = 0;
  let dryRunRows = 0;

  for (const row of rows) {
    if (row.verdict === "leaked") leakedTotal += 1;
    if (row.escalated) escalatedTotal += 1;
    if (row.dry_run) dryRunRows += 1;

    const category = row.category || "uncategorised";
    const topic = topics.get(category) ||
      { category, times_seen: 0, last_seen: null as string | null, actors: new Set<string>() };
    topic.times_seen += 1;
    topic.last_seen = latest(topic.last_seen, row.received_at);
    if (row.actor_resolved) topic.actors.add(row.actor_resolved);
    topics.set(category, topic);

    const actor = row.actor_resolved;
    if (!actor) {
      // Counted, not dropped. A complaint whose reporter could not be resolved
      // is a gap in identity resolution, and the dashboard should show that it
      // exists rather than quietly shrink the denominator.
      withoutActor += 1;
      continue;
    }

    const entry = reporters.get(actor) || {
      actor,
      times_reported: 0,
      last_seen: null,
      first_seen: null,
      categories: [],
      sources: [],
      tones: [],
      confidence_total: 0,
      confidence_n: 0,
      leaked: 0,
      escalated: 0,
      tickets: 0,
      replies: 0,
      latest_complaint: null,
    };

    entry.times_reported += 1;
    const previousLast = entry.last_seen;
    entry.last_seen = latest(entry.last_seen, row.received_at);
    if (row.received_at && (!entry.first_seen || row.received_at < entry.first_seen)) {
      entry.first_seen = row.received_at;
    }
    if (entry.last_seen !== previousLast && row.complaint_text) {
      entry.latest_complaint = row.complaint_text.slice(0, 240);
    }
    if (row.category && !entry.categories.includes(row.category)) entry.categories.push(row.category);
    if (row.source && !entry.sources.includes(row.source)) entry.sources.push(row.source);
    if (row.reply_tone && !entry.tones.includes(row.reply_tone)) entry.tones.push(row.reply_tone);
    if (typeof row.confidence === "number") {
      entry.confidence_total += row.confidence;
      entry.confidence_n += 1;
    }
    if (row.verdict === "leaked") entry.leaked += 1;
    if (row.escalated) entry.escalated += 1;
    if (row.ticket_url) entry.tickets += 1;
    if (row.reply_sent) entry.replies += 1;

    reporters.set(actor, entry);
  }

  const recurringReporters = [...reporters.values()]
    .filter((r) => r.times_reported >= minTimes)
    .sort((a, b) => b.times_reported - a.times_reported || (b.last_seen || "").localeCompare(a.last_seen || ""))
    .map((r) => ({
      actor: r.actor,
      times_reported: r.times_reported,
      first_seen: r.first_seen,
      last_seen: r.last_seen,
      categories: r.categories,
      sources: r.sources,
      reply_tones_used: r.tones,
      // null, not 0. No row carried a confidence, which is not the same as
      // every row carrying a confidence of zero.
      avg_confidence: r.confidence_n ? Number((r.confidence_total / r.confidence_n).toFixed(2)) : null,
      leaked: r.leaked,
      escalated: r.escalated,
      tickets_filed: r.tickets,
      replies_sent: r.replies,
      latest_complaint: r.latest_complaint,
    }));

  const recurringTopics = [...topics.values()]
    .sort((a, b) => b.times_seen - a.times_seen)
    .map((t) => ({
      category: t.category,
      times_seen: t.times_seen,
      distinct_actors: t.actors.size,
      last_seen: t.last_seen,
    }));

  return json({
    source: `insforge:${table}`,
    access: gate ? "token-gated" : "public",
    generated_at: new Date().toISOString(),
    window: {
      limit,
      rows_scanned: rows.length,
      // The window is only a window when it filled up. Said explicitly so a
      // reader never mistakes a truncated view for the whole history.
      truncated: rows.length >= limit,
      min_times: minTimes,
      actor_filter: onlyActor,
    },
    note: rows.length === 0
      ? `The '${table}' table exists and is empty: the pipeline has not recorded any resolution yet. ` +
        `This is an empty history, not a failed read.`
      : null,
    totals: {
      resolutions_logged: rows.length,
      distinct_reporters: reporters.size,
      unresolved_reporter_rows: withoutActor,
      leaked: leakedTotal,
      escalated: escalatedTotal,
      dry_run_rows: dryRunRows,
    },
    recurring_reporters: recurringReporters,
    recurring_topics: recurringTopics,
  });
}
