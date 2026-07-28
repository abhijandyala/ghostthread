export type ConnectorId = "slack" | "gmail" | "linear" | "github";

export type IntegrationDef = {
  id: string;
  name: string;
  description: string;
  account?: string;
};

export type AgentNode = {
  id: string;
  name: string;
  role: "source" | "primary" | "subagent" | "action";
  model?: string;
  status: "idle" | "running" | "done";
  color: string;
};

export type GraphEdge = {
  from: string;
  to: string;
  active: boolean;
};

export type ActivityItem = {
  id: string;
  kind: "leak" | "pr" | "ticket" | "reply" | "escalation" | "memory";
  source: ConnectorId;
  title: string;
  detail: string;
  ts: string;
};

export type InboxItem = {
  id: string;
  source: ConnectorId;
  type: "pr" | "message" | "ticket";
  title: string;
  actor: string;
  body: string;
  verdict?: "leak" | "resolved" | "unknown";
  confidence?: number;
  tone?: "first_contact" | "returning" | "escalation";
  prMeta?: { branch: string; filesChanged: number; draft: boolean };
  ts: string;
  memorySummary?: string;
  actionsTaken?: string[];
};

export type RecurringReporter = {
  actor: string;
  timesReported: number;
  categories: string[];
  lastSeen: string;
  avgConfidence: number;
  timeline?: { date: string; event: string; type: "complaint" | "resolution" | "regression" }[];
};

export type Tab = "dashboard" | "inbox" | "approvals" | "memory" | "settings";

export type AutonomyMode = "review" | "auto";

export type ApprovalSettings = {
  mode: AutonomyMode;
  requireForCodeChanges: boolean;
  requireForPrPush: boolean;
  requireForTickets: boolean;
  requireForReplies: boolean;
};

export type PendingApproval = {
  id: string;
  kind: "code_change" | "pr_push" | "ticket" | "reply";
  title: string;
  detail: string;
  source: string;
  ts: string;
};

export type Project = {
  name: string;
  connectors: string[];
  createdAt: string;
};
