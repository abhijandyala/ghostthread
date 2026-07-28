/**
 * Pipeline node N4 — serve the live `category_policy` to whatever is running.
 *
 * The intent profile is one row in `intent_profiles`, keyed by name, with the
 * whole governance document in a `json` column. This function is the read
 * surface in front of it, so the pipeline (and the RocketRide graph, and the
 * demo UI) can ask "what is the policy right now" over one public HTTP call
 * instead of holding an admin credential.
 *
 * That is the point of it. The admin key never leaves InsForge: it is read from
 * the function's own environment, which is why this can be invoked without
 * authentication while the table underneath cannot.
 *
 * The one rule this file exists to enforce
 * ----------------------------------------
 * There is no default policy in here. If the row is missing, this answers 404
 * and says the profile is absent. It does NOT fall back to a built-in
 * `category_policy`, because a fabricated policy would silently route real
 * complaints — escalating none of them, auto-fixing all of them, whichever way
 * the invented default happened to lean — while looking exactly like a working
 * system. A caller that gets a 404 knows it has no policy. A caller that gets an
 * invented one does not.
 *
 * The offline fallback for the pipeline is the checked-in
 * `insforge/intent_profile.json`, which is a seed a human wrote and reviewed.
 * That is a different thing from a default this function made up on the spot.
 *
 * Conventions follow src/ghostthread/intent.py exactly: both the X-API-Key and
 * the Authorization headers are sent, because InsForge's docs and its own SDK
 * disagree about which one authenticates an admin call, and PostgREST filters
 * are spelled `key=eq.<value>`.
 *
 * Deploy:  PYTHONPATH=src python scripts/deploy_edge_functions.py
 * Invoke:  GET/POST {INSFORGE_BASE_URL}/functions/policy-read
 *          ?key=ghostthread          which profile row to read
 *          &section=category_policy  return just that key of the document
 *          &category=genuine_bug     return just one category's policy
 */

const DEFAULT_PROFILE_TABLE = "intent_profiles";
const DEFAULT_PROFILE_KEY = "ghostthread";

const CORS = {
  "Access-Control-Allow-Origin": "*",
  "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
  "Access-Control-Allow-Headers": "Content-Type, Authorization, X-API-Key",
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

export default async function (request: Request): Promise<Response> {
  if (request.method === "OPTIONS") {
    return new Response(null, { status: 204, headers: CORS });
  }

  const url = new URL(request.url);
  const profileKey = url.searchParams.get("key") || env("INSFORGE_PROFILE_KEY", DEFAULT_PROFILE_KEY);
  const table = url.searchParams.get("table") || env("INSFORGE_TABLE", DEFAULT_PROFILE_TABLE);
  const section = url.searchParams.get("section");
  const category = url.searchParams.get("category");

  const base = env("INSFORGE_BASE_URL").replace(/\/+$/, "");
  const apiKey = env("API_KEY");
  if (!base || !apiKey) {
    // Refusing rather than guessing. See the header comment.
    return json(
      {
        error: "policy_read is not configured",
        detail: "INSFORGE_BASE_URL and API_KEY are not both present in the function environment",
        served: null,
      },
      500,
    );
  }

  const query = `${base}/api/database/records/${table}` +
    `?key=eq.${encodeURIComponent(profileKey)}&select=value&limit=1`;

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
    return json(
      {
        error: "could not reach the intent profile table",
        detail: String(err).slice(0, 300),
        table,
        served: null,
      },
      502,
    );
  }

  const text = await resp.text();
  if (!resp.ok) {
    return json(
      {
        error: "the intent profile table rejected the read",
        detail: text.slice(0, 400),
        status: resp.status,
        table,
        served: null,
      },
      502,
    );
  }

  let rows: Array<{ value?: unknown }>;
  try {
    rows = JSON.parse(text);
  } catch (err) {
    return json(
      { error: "intent profile table returned an unparseable body", detail: String(err).slice(0, 200), served: null },
      502,
    );
  }

  if (!Array.isArray(rows) || rows.length === 0) {
    return json(
      {
        error: "no intent profile is stored under this key",
        detail:
          `${table} has no row with key='${profileKey}'. Run scripts/seed_insforge.py to push ` +
          `insforge/intent_profile.json into InsForge. No default policy is substituted here, ` +
          `because an invented policy would route real complaints while looking like a working system.`,
        key: profileKey,
        table,
        served: null,
      },
      404,
    );
  }

  let profile = rows[0]?.value as Record<string, unknown> | string | undefined;
  if (typeof profile === "string") {
    try {
      profile = JSON.parse(profile);
    } catch {
      profile = undefined;
    }
  }
  if (!profile || typeof profile !== "object") {
    return json(
      {
        error: "the stored intent profile is empty or not an object",
        detail: `${table}.value for key='${profileKey}' did not parse into a policy document`,
        key: profileKey,
        served: null,
      },
      502,
    );
  }

  const doc = profile as Record<string, unknown>;
  const policy = (doc["category_policy"] || {}) as Record<string, unknown>;
  const servedAt = new Date().toISOString();

  // One category. Missing is 404, never an empty permission set: an empty
  // `allowed_actions` reads as a deliberate "do nothing" policy, which is a
  // decision this function has no standing to make.
  if (category) {
    const entry = policy[category];
    if (entry === undefined) {
      return json(
        {
          error: "no policy entry for that category",
          detail:
            `The profile defines ${Object.keys(policy).length} categories and '${category}' is ` +
            `not one of them. An empty action set is not returned in its place.`,
          category,
          key: profileKey,
          served: null,
        },
        404,
      );
    }
    return json({ key: profileKey, category, policy: entry, origin: "insforge", served_at: servedAt });
  }

  if (section) {
    if (doc[section] === undefined) {
      return json(
        { error: "no such section in the intent profile", section, key: profileKey, served: null },
        404,
      );
    }
    return json({ key: profileKey, section, value: doc[section], origin: "insforge", served_at: servedAt });
  }

  return json({
    key: profileKey,
    // The whole governance document, so a caller can construct an IntentProfile
    // from this response alone — same shape intent.py reads from the table.
    profile: doc,
    // Lifted to the top level because N4 asks for these by name.
    category_policy: policy,
    global_overrides: doc["global_overrides"] || {},
    reply_tone_thresholds: doc["reply_tone_thresholds"] || {},
    severity_bands: doc["severity_bands"] || {},
    categories_defined: Object.keys(policy).length,
    version: doc["version"] ?? null,
    origin: "insforge",
    served_at: servedAt,
  });
}
