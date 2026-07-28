import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import type {
  ActivityItem,
  InboxItem,
  RecurringReporter,
  PendingApproval,
  AgentNode,
  GraphEdge,
  ConnectorId,
} from "./types";
import {
  activityItems,
  inboxItems,
  recurringReporters,
  pendingApprovals,
  stats,
  integrationCatalog,
  connectorColors,
} from "./mock";
import {
  fetchHealth,
  postComplaint,
  postRun,
  subscribeEvents,
  type BusEvent,
  type RunReport,
} from "./api";
import { buildInboxItems, buildReporters, reportCost, timeLabel } from "./mapReport";
import { loadApprovalSettings } from "./project";
import { useProject } from "../ProjectContext";

// Sources the backend engine knows how to scope a run to.
const BACKEND_SOURCE_IDS = ["slack", "gmail", "linear", "github"];

export type PipelineStats = {
  complaintsProcessed: number;
  leaksFound: number;
  ticketsFiled: number;
  prsOpened: number;
  runCost: number;
};

/** Shape of a live run: sub-agents + actions spawned by the orchestrator. */
export type PipelineRun = { nodes: AgentNode[]; edges: GraphEdge[] };

export type BackendStatus = {
  connected: boolean;
  grounding?: string;
  extraction?: string;
};

export type PipelineData = {
  stats: PipelineStats;
  activity: ActivityItem[];
  inbox: InboxItem[];
  reporters: RecurringReporter[];
  approvals: PendingApproval[];
  activeRun: PipelineRun | null;
  running: boolean;
  backend: BackendStatus;
};

type PipelineStore = PipelineData & {
  /** Merge a partial update into the store — every consumer re-renders. */
  update: (patch: Partial<PipelineData>) => void;
  runComplaint: (text: string, source: string) => Promise<void>;
  runFullScan: () => Promise<void>;
  resolveApproval: (id: string, approved: boolean) => void;
};

const initialData: PipelineData = {
  stats,
  activity: activityItems,
  inbox: inboxItems,
  reporters: recurringReporters,
  approvals: pendingApprovals,
  activeRun: null,
  running: false,
  backend: { connected: false },
};

const PipelineDataContext = createContext<PipelineStore>({
  ...initialData,
  update: () => {},
  runComplaint: async () => {},
  runFullScan: async () => {},
  resolveApproval: () => {},
});

// --- live run graph -----------------------------------------------------------

const STEP_DEFS: { step: string; name: string; model?: string }[] = [
  { step: "memory_read", name: "Memory Reader", model: "HydraDB" },
  { step: "classify", name: "Fact Extractor", model: "Pipeshift" },
  { step: "route", name: "Policy Router", model: "InsForge" },
  { step: "act", name: "Action Agent" },
  { step: "memory_write", name: "Memory Writer", model: "HydraDB" },
];

const MAX_ACTION_NODES = 6;

function sourceName(id: string): string {
  return integrationCatalog.find((d) => d.id === id)?.name ?? id;
}

function buildRunGraph(scope: string[]): PipelineRun {
  const sources: AgentNode[] = scope.map((s) => ({
    id: `${s}-src`,
    name: sourceName(s),
    role: "source",
    status: "running",
    color: connectorColors[s] ?? "#8B97A8",
  }));
  const prime: AgentNode = {
    id: "prime",
    name: "GhostThread Prime",
    role: "primary",
    status: "running",
    color: "#FFCE1B",
  };
  const edges: GraphEdge[] = sources.map((s) => ({ from: s.id, to: "prime", active: true }));
  return { nodes: [...sources, prime], edges };
}

function upsertStepNode(run: PipelineRun, step: string, status: "running" | "done"): PipelineRun {
  const def = STEP_DEFS.find((d) => d.step === step);
  if (!def) return run;
  const id = `step-${step}`;
  const existing = run.nodes.find((n) => n.id === id);

  let nodes: AgentNode[];
  if (existing) {
    nodes = run.nodes.map((n) => (n.id === id ? { ...n, status } : n));
  } else {
    nodes = [
      ...run.nodes,
      { id, name: def.name, role: "subagent", status, color: "#069494", model: def.model },
    ];
  }
  const edges = run.edges.some((e) => e.from === "prime" && e.to === id)
    ? run.edges
    : [...run.edges, { from: "prime", to: id, active: true }];
  return { nodes, edges };
}

function addActionNode(run: PipelineRun, id: string, name: string, color: string): PipelineRun {
  if (run.nodes.some((n) => n.id === id)) return run;
  const actionNodes = run.nodes.filter((n) => n.role === "action");
  if (actionNodes.length >= MAX_ACTION_NODES) return run;
  return {
    nodes: [...run.nodes, { id, name, role: "action", status: "done", color }],
    edges: [...run.edges, { from: "step-act", to: id, active: true }],
  };
}

function finishRun(run: PipelineRun | null): PipelineRun | null {
  if (!run) return run;
  return {
    nodes: run.nodes.map((n) => ({ ...n, status: "done" as const })),
    edges: run.edges.map((e) => ({ ...e, active: false })),
  };
}

// --- event -> state -------------------------------------------------------------

// Leaks re-detected by a later scan are not new: the backend's idempotency log
// already prevents duplicate actions, so the UI dedupes the count/feed too.
const seenLeakIds = new Set<string>();

let activitySeq = 0;
function act(kind: ActivityItem["kind"], source: string, title: string, detail: string): ActivityItem {
  const src = (["slack", "gmail", "linear", "github"].includes(source)
    ? source
    : "slack") as ConnectorId;
  return { id: `live-act-${++activitySeq}`, kind, source: src, title, detail, ts: timeLabel() };
}

function applyEvent(prev: PipelineData, evt: BusEvent): PipelineData {
  const d = evt.data as Record<string, any>;

  switch (evt.type) {
    case "run_started": {
      return {
        ...prev,
        running: true,
        activeRun: buildRunGraph((d.scope as string[]) ?? []),
        activity: [
          act(
            "memory",
            "slack",
            d.live ? "Live complaint run started" : "Full scan started",
            `Scope: ${((d.scope as string[]) ?? []).join(", ")}`
          ),
          ...prev.activity,
        ].slice(0, 60),
      };
    }

    case "sources_loaded":
      return prev;

    case "leak_found": {
      return {
        ...prev,
        stats: { ...prev.stats, leaksFound: prev.stats.leaksFound + 1 },
        activity: [
          act("leak", d.source, `Leak detected from ${d.author || d.source}`, d.text ?? ""),
          ...prev.activity,
        ].slice(0, 60),
      };
    }

    case "agent_step": {
      if (!prev.activeRun) return prev;
      return {
        ...prev,
        activeRun: upsertStepNode(prev.activeRun, d.step, d.status === "done" ? "done" : "running"),
      };
    }

    case "action_taken": {
      let run = prev.activeRun;
      const activity = [...prev.activity];
      const approvals = [...prev.approvals];
      const settings = loadApprovalSettings();
      const review = settings.mode === "review";
      let { ticketsFiled, prsOpened } = prev.stats;
      const cid = d.complaint_id ?? "c";

      if (d.ticket_id) {
        ticketsFiled += 1;
        if (run) run = addActionNode(run, `action-ticket-${cid}`, `Ticket ${d.ticket_id}`, "#5E6AD2");
        activity.unshift(
          act("ticket", "linear", `Filed ${d.ticket_id}`, d.what_broke ?? "")
        );
        if (review && settings.requireForTickets) {
          approvals.unshift({
            id: `${cid}-ticket-appr`,
            kind: "ticket",
            title: `File ${d.ticket_id} in Linear`,
            detail: d.what_broke ?? "",
            source: "linear",
            ts: timeLabel(),
          });
        }
      }
      if (d.fix_attempted) {
        prsOpened += 1;
        if (run) run = addActionNode(run, `action-pr-${cid}`, "Draft PR", "#F0F0F0");
        activity.unshift(
          act("pr", "github", "Sandboxed fix drafted", d.what_broke ?? "")
        );
        if (review && settings.requireForCodeChanges) {
          approvals.unshift({
            id: `${cid}-fix-appr`,
            kind: "code_change",
            title: "Apply sandboxed patch",
            detail: d.what_broke ?? "",
            source: "github",
            ts: timeLabel(),
          });
        }
      }
      if (d.reply_channel) {
        if (run) run = addActionNode(run, `action-reply-${cid}`, "Reply drafted", "#069494");
        activity.unshift(
          act("reply", d.source, `Reply drafted to ${d.author ?? "reporter"}`, d.what_broke ?? "")
        );
        if (review && settings.requireForReplies) {
          approvals.unshift({
            id: `${cid}-reply-appr`,
            kind: "reply",
            title: `Send reply to ${d.author ?? "reporter"}`,
            detail: d.what_broke ?? "",
            source: d.source ?? "slack",
            ts: timeLabel(),
          });
        }
      }
      if (d.escalated) {
        if (run) run = addActionNode(run, `action-esc-${cid}`, "Escalated", "#B7410E");
        activity.unshift(
          act("escalation", d.source, "Escalated to on-call", d.what_broke ?? "")
        );
      }

      return {
        ...prev,
        activeRun: run,
        activity: activity.slice(0, 60),
        approvals,
        stats: { ...prev.stats, ticketsFiled, prsOpened },
      };
    }

    case "run_complete": {
      return {
        ...prev,
        running: false,
        activeRun: finishRun(prev.activeRun),
        stats: {
          ...prev.stats,
          complaintsProcessed:
            prev.stats.complaintsProcessed + (d.summary?.complaints_examined ?? 0),
        },
        activity: [
          act(
            "memory",
            "slack",
            "Run complete",
            `${d.summary?.leaks ?? 0} leaks across ${d.summary?.complaints_examined ?? 0} complaints in ${Math.round(d.elapsed_ms ?? 0)}ms`
          ),
          ...prev.activity,
        ].slice(0, 60),
      };
    }

    default:
      return prev;
  }
}

/** Merge a run report into the store: inbox, memory, and run cost. */
function applyReport(prev: PipelineData, report: RunReport): PipelineData {
  const fresh = buildInboxItems(report);
  const freshIds = new Set(fresh.map((i) => i.id));
  const inbox = [...fresh, ...prev.inbox.filter((i) => !freshIds.has(i.id))].slice(0, 120);

  const freshReporters = buildReporters(report);
  const freshActors = new Set(freshReporters.map((r) => r.actor));
  const reporters = [
    ...freshReporters,
    ...prev.reporters.filter((r) => !freshActors.has(r.actor)),
  ].sort((a, b) => b.timesReported - a.timesReported);

  return {
    ...prev,
    inbox,
    reporters,
    stats: { ...prev.stats, runCost: prev.stats.runCost + reportCost(report) },
  };
}

/**
 * Single source of truth for everything the pipeline produces. All tabs
 * (Dashboard stats/activity/graph, Inbox, Approvals, Memory) and the sidebar
 * badge read from here.
 *
 * On mount it health-checks the GhostThread backend and subscribes to its SSE
 * event stream; every pipeline node and side effect streams in live and is
 * applied through `applyEvent`. Full run reports (the POST responses) are
 * merged through `applyReport` for the richer Inbox/Memory payloads.
 */
export function PipelineDataProvider({ children }: { children: ReactNode }) {
  const project = useProject();
  const [data, setData] = useState<PipelineData>(initialData);
  const queueRef = useRef<BusEvent[]>([]);
  const drainingRef = useRef(false);
  const lastSeqRef = useRef(0);

  // Runs are scoped to the connectors this project actually selected — the
  // backend threads the scope all the way down, so out-of-scope sources are
  // genuinely never consulted.
  const scopeRef = useRef<string[] | undefined>(undefined);
  scopeRef.current = (() => {
    const scoped = (project?.connectors ?? []).filter((c) => BACKEND_SOURCE_IDS.includes(c));
    return scoped.length > 0 ? scoped : undefined;
  })();

  const update = useCallback((patch: Partial<PipelineData>) => {
    setData((prev) => ({ ...prev, ...patch }));
  }, []);

  // Events are applied through a small pacing queue so a burst (the local
  // pipeline is fast) still reads as a sequence on the canvas.
  const drain = useCallback(() => {
    if (drainingRef.current) return;
    drainingRef.current = true;
    const step = () => {
      const evt = queueRef.current.shift();
      if (!evt) {
        drainingRef.current = false;
        return;
      }
      // Dedup here (not inside the state updater, which must stay pure —
      // React double-invokes updaters in dev). A leak re-detected by a later
      // scan is not news; the backend's idempotency log matches.
      if (evt.type === "leak_found") {
        const id = String((evt.data as Record<string, unknown>).complaint_id ?? "");
        if (seenLeakIds.has(id)) {
          step();
          return;
        }
        seenLeakIds.add(id);
      }
      setData((prev) => applyEvent(prev, evt));
      const delay = queueRef.current.length > 14 ? 50 : 220;
      setTimeout(step, delay);
    };
    step();
  }, []);

  const enqueue = useCallback(
    (evt: BusEvent) => {
      if (evt.seq <= lastSeqRef.current) return;
      lastSeqRef.current = evt.seq;
      queueRef.current.push(evt);
      drain();
    },
    [drain]
  );

  useEffect(() => {
    let cleanup: (() => void) | undefined;
    let cancelled = false;

    (async () => {
      const health = await fetchHealth();
      if (cancelled) return;
      if (health?.ok) {
        lastSeqRef.current = health.last_seq ?? 0;
        setData((prev) => ({
          ...prev,
          backend: {
            connected: true,
            grounding: health.backends?.grounding,
            extraction: health.backends?.extraction,
          },
        }));
        cleanup = subscribeEvents(lastSeqRef.current, enqueue);
      }
    })();

    return () => {
      cancelled = true;
      cleanup?.();
    };
  }, [enqueue]);

  const runComplaint = useCallback(async (text: string, source: string) => {
    setData((prev) => ({ ...prev, running: true }));
    try {
      const report = await postComplaint(text, source, scopeRef.current);
      setData((prev) => applyReport(prev, report));
    } catch {
      setData((prev) => ({ ...prev, running: false }));
    }
  }, []);

  const runFullScan = useCallback(async () => {
    setData((prev) => ({ ...prev, running: true }));
    try {
      const report = await postRun(scopeRef.current);
      setData((prev) => applyReport(prev, report));
    } catch {
      setData((prev) => ({ ...prev, running: false }));
    }
  }, []);

  const resolveApproval = useCallback((id: string, approved: boolean) => {
    setData((prev) => {
      const item = prev.approvals.find((a) => a.id === id);
      if (!item) return prev;
      return {
        ...prev,
        approvals: prev.approvals.filter((a) => a.id !== id),
        activity: [
          act(
            item.kind === "reply" ? "reply" : item.kind === "ticket" ? "ticket" : "pr",
            item.source,
            `${approved ? "Approved" : "Rejected"}: ${item.title}`,
            item.detail
          ),
          ...prev.activity,
        ].slice(0, 60),
      };
    });
  }, []);

  // Dev escape hatch: lets the console push data into the UI,
  // e.g. window.__ghostthread.update({ stats: ... }).
  useEffect(() => {
    if (import.meta.env.DEV) {
      (window as unknown as Record<string, unknown>).__ghostthread = { update };
    }
  }, [update]);

  return (
    <PipelineDataContext.Provider
      value={{ ...data, update, runComplaint, runFullScan, resolveApproval }}
    >
      {children}
    </PipelineDataContext.Provider>
  );
}

export function usePipelineData() {
  return useContext(PipelineDataContext);
}
