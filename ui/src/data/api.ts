/**
 * GhostThread backend client.
 *
 * In dev the Vite app talks straight to the FastAPI server (CORS is open);
 * in production FastAPI serves the built UI itself, so paths are same-origin.
 */

export const API_BASE = import.meta.env.DEV ? "http://127.0.0.1:8000" : "";

export type BusEvent = {
  seq: number;
  type:
    | "run_started"
    | "sources_loaded"
    | "leak_found"
    | "agent_step"
    | "action_taken"
    | "run_complete";
  ts: number;
  // Payload shapes vary per event type; consumers narrow on `type`.
  data: Record<string, unknown>;
};

export type RunReport = {
  question: string;
  sources_requested: string[];
  sources_loaded: Record<string, number>;
  backends: Record<string, string>;
  identities: Record<string, unknown>[];
  results: Record<string, unknown>[];
  actions: Record<string, unknown>[];
  summary: {
    complaints_examined: number;
    leaks: number;
    answerable: number;
    mean_confidence: number;
    unanswered_customer_hours: number;
  };
  elapsed_ms: number;
};

export type Health = {
  ok: boolean;
  backends: { grounding: string; extraction: string; intent_profile: string };
  last_seq?: number;
};

export async function fetchHealth(): Promise<Health | null> {
  try {
    const res = await fetch(`${API_BASE}/health`, { signal: AbortSignal.timeout(4000) });
    if (!res.ok) return null;
    return (await res.json()) as Health;
  } catch {
    return null;
  }
}

export async function postComplaint(
  text: string,
  source: string,
  sources?: string[]
): Promise<RunReport> {
  const res = await fetch(`${API_BASE}/complaint`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ text, source, sources: sources ?? null }),
  });
  if (!res.ok) throw new Error(`complaint failed: ${res.status}`);
  return (await res.json()) as RunReport;
}

export async function postRun(sources?: string[]): Promise<RunReport> {
  const res = await fetch(`${API_BASE}/run`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ sources: sources ?? null, act: true }),
  });
  if (!res.ok) throw new Error(`run failed: ${res.status}`);
  return (await res.json()) as RunReport;
}

/** Subscribe to the pipeline event stream. Returns a cleanup function. */
export function subscribeEvents(since: number, onEvent: (e: BusEvent) => void): () => void {
  const es = new EventSource(`${API_BASE}/events?since=${since}`);
  es.onmessage = (msg) => {
    try {
      onEvent(JSON.parse(msg.data) as BusEvent);
    } catch {
      // Malformed frame; skip.
    }
  };
  return () => es.close();
}
