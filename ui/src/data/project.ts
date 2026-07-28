import type { Project, ApprovalSettings } from "./types";

const PROJECTS_KEY = "ghostthread.projects";
const ACTIVE_KEY = "ghostthread.active-project";
const APPROVAL_KEY = "ghostthread.approval-settings";

export function loadProjects(): Project[] {
  try {
    const raw = localStorage.getItem(PROJECTS_KEY);
    return raw ? (JSON.parse(raw) as Project[]) : [];
  } catch {
    return [];
  }
}

export function persistProjects(projects: Project[]) {
  localStorage.setItem(PROJECTS_KEY, JSON.stringify(projects));
}

export function loadActiveProjectName(): string | null {
  return localStorage.getItem(ACTIVE_KEY);
}

export function persistActiveProjectName(name: string) {
  localStorage.setItem(ACTIVE_KEY, name);
}

export function clearActiveProjectName() {
  localStorage.removeItem(ACTIVE_KEY);
}

export const defaultApprovalSettings: ApprovalSettings = {
  mode: "review",
  requireForCodeChanges: true,
  requireForPrPush: true,
  requireForTickets: true,
  requireForReplies: true,
};

export function loadApprovalSettings(): ApprovalSettings {
  try {
    const raw = localStorage.getItem(APPROVAL_KEY);
    return raw
      ? { ...defaultApprovalSettings, ...(JSON.parse(raw) as Partial<ApprovalSettings>) }
      : defaultApprovalSettings;
  } catch {
    return defaultApprovalSettings;
  }
}

export function persistApprovalSettings(settings: ApprovalSettings) {
  localStorage.setItem(APPROVAL_KEY, JSON.stringify(settings));
}
