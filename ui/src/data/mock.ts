import type {
  IntegrationDef,
  ActivityItem,
  InboxItem,
  RecurringReporter,
  PendingApproval,
} from "./types";

export const integrationCatalog: IntegrationDef[] = [
  { id: "slack", name: "Slack", description: "Monitor channels for customer complaints", account: "#testteam-support" },
  { id: "gmail", name: "Gmail", description: "Scan inbound support emails", account: "support@testteam.dev" },
  { id: "linear", name: "Linear", description: "File and track engineering tickets", account: "TestTeam Engineering" },
  { id: "github", name: "GitHub", description: "Open draft PRs against sandbox repos", account: "testteam-eng" },
  { id: "discord", name: "Discord", description: "Community server monitoring" },
  { id: "teams", name: "Microsoft Teams", description: "Teams channel complaint ingestion" },
  { id: "outlook", name: "Outlook", description: "Enterprise email complaint scanning" },
  { id: "jira", name: "Jira", description: "Issue tracking and ticket management" },
  { id: "zendesk", name: "Zendesk", description: "Customer support ticket integration" },
  { id: "intercom", name: "Intercom", description: "Live chat and customer messaging" },
];

export const connectorColors: Record<string, string> = {
  slack: "#E01E5A",
  gmail: "#EA4335",
  linear: "#5E6AD2",
  github: "#F0F0F0",
};

export const pendingApprovals: PendingApproval[] = [];

export const activityItems: ActivityItem[] = [];

export const inboxItems: InboxItem[] = [];

export const recurringReporters: RecurringReporter[] = [];

export const stats = {
  complaintsProcessed: 0,
  leaksFound: 0,
  ticketsFiled: 0,
  prsOpened: 0,
  runCost: 0,
};
