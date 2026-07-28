/**
 * Pure mapping from a backend RunReport onto the UI's data shapes.
 * The store applies these after every /run or /complaint response.
 */

import type { RunReport } from "./api";
import type { ConnectorId, InboxItem, RecurringReporter } from "./types";

type Dict = Record<string, any>;

export function timeLabel(epochSeconds?: number): string {
  const d = epochSeconds ? new Date(epochSeconds * 1000) : new Date();
  return d.toLocaleTimeString([], { hour: "numeric", minute: "2-digit" });
}

function verdictLabel(v: string): InboxItem["verdict"] {
  if (v === "leaked") return "leak";
  if (v === "actioned") return "resolved";
  return "unknown";
}

function asConnector(source: string): ConnectorId {
  return (["slack", "gmail", "linear", "github"].includes(source) ? source : "slack") as ConnectorId;
}

export function buildInboxItems(report: RunReport): InboxItem[] {
  const items: InboxItem[] = [];
  const actionsByComplaint = new Map<string, Dict>();
  for (const a of report.actions as Dict[]) {
    const cid = a.leak?.complaint?.id ?? a.facts?.complaint_id;
    if (cid) actionsByComplaint.set(cid, a);
  }

  for (const r of report.results as Dict[]) {
    const c = r.complaint ?? {};
    const action = actionsByComplaint.get(c.id);
    items.push({
      id: c.id,
      source: asConnector(c.source),
      type: "message",
      title: (c.text ?? "").slice(0, 90) || "Complaint",
      // author_email can be an empty string in the corpus, so || not ??
      actor: c.author_email || c.entity_id || "unknown",
      body: c.text ?? "",
      verdict: verdictLabel(r.verdict),
      confidence: typeof r.confidence === "number" ? r.confidence : undefined,
      tone: action?.facts?.reply_tone,
      ts: timeLabel(c.t),
      memorySummary: action?.memory
        ? `${action.memory.times_reported_by_actor ?? 0} prior reports from this actor, ${action.memory.times_seen_on_topic ?? 0} on this topic`
        : undefined,
      actionsTaken: action?.actions_taken,
    });
  }

  for (const a of report.actions as Dict[]) {
    const c = a.leak?.complaint ?? {};
    const facts = a.facts ?? {};
    const cid = c.id ?? facts.complaint_id ?? "unknown";

    if (a.ticket_created_id) {
      items.push({
        id: `${cid}-ticket`,
        source: "linear",
        type: "ticket",
        title: `${a.ticket_created_id}: ${facts.what_broke ?? "Untracked complaint"}`,
        actor: "GhostThread",
        body: a.meta?.ticket?.payload?.description ?? facts.what_broke ?? "",
        actionsTaken: a.actions_taken,
        ts: timeLabel(),
      });
    }
    if (a.fix_attempted) {
      const branch = a.meta?.fix?.branch ?? `ghostthread/${(a.ticket_created_id ?? cid).toLowerCase()}`;
      items.push({
        id: `${cid}-pr`,
        source: "github",
        type: "pr",
        title: `Fix: ${facts.what_broke ?? "sandbox patch"}`,
        actor: "GhostThread",
        body: (a.meta?.fix?.diff ?? "").slice(0, 600) || "Sandboxed patch drafted.",
        prMeta: { branch, filesChanged: 1, draft: true },
        ts: timeLabel(),
      });
    }
    if (a.reply_channel) {
      items.push({
        id: `${cid}-reply`,
        source: asConnector(c.source ?? "slack"),
        type: "message",
        title: `Reply drafted to ${c.author_email ?? "reporter"}`,
        actor: "GhostThread",
        body: a.meta?.reply?.body ?? a.meta?.reply?.would_send_to ?? "",
        tone: facts.reply_tone,
        ts: timeLabel(),
      });
    }
  }

  return items;
}

export function buildReporters(report: RunReport): RecurringReporter[] {
  const byActor = new Map<string, RecurringReporter>();

  const actionsByComplaint = new Map<string, Dict>();
  for (const a of report.actions as Dict[]) {
    const cid = a.leak?.complaint?.id ?? a.facts?.complaint_id;
    if (cid) actionsByComplaint.set(cid, a);
  }

  for (const r of report.results as Dict[]) {
    const c = r.complaint ?? {};
    // author_email can be an empty string in the corpus, so || not ??
    const actor = c.author_email || c.entity_id;
    if (!actor) continue;
    const action = actionsByComplaint.get(c.id);
    const facts = action?.facts ?? {};

    const existing = byActor.get(actor);
    const entry: RecurringReporter = existing ?? {
      actor,
      timesReported: 0,
      categories: [],
      lastSeen: timeLabel(c.t),
      avgConfidence: 0,
      timeline: [],
    };

    entry.timesReported = Math.max(
      entry.timesReported + 1,
      (action?.memory?.times_reported_by_actor ?? 0) + 1
    );
    if (facts.category && !entry.categories.includes(facts.category)) {
      entry.categories.push(facts.category);
    }
    entry.lastSeen = timeLabel(c.t);
    if (typeof r.confidence === "number") {
      entry.avgConfidence =
        entry.avgConfidence === 0 ? r.confidence : (entry.avgConfidence + r.confidence) / 2;
    }

    entry.timeline = entry.timeline ?? [];
    entry.timeline.push({
      date: timeLabel(c.t),
      event: (c.text ?? "").slice(0, 80),
      type: "complaint",
    });
    if (action?.ticket_created_id) {
      entry.timeline.push({
        date: timeLabel(),
        event: `Filed ${action.ticket_created_id}`,
        type: "resolution",
      });
    }
    if (action?.memory?.likely_regression?.ref) {
      entry.timeline.push({
        date: timeLabel(),
        event: `Likely regression: ${action.memory.likely_regression.ref}`,
        type: "regression",
      });
    }

    byActor.set(actor, entry);
  }

  return [...byActor.values()].sort((a, b) => b.timesReported - a.timesReported);
}

export function reportCost(report: RunReport): number {
  let cost = 0;
  for (const a of report.actions as Dict[]) {
    cost += Number(a.cost_usd ?? 0) + Number(a.facts?.cost_usd ?? 0);
  }
  return cost;
}
