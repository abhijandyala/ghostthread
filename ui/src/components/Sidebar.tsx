import type { Tab, Project, IntegrationDef } from "../data/types";
import { integrationCatalog } from "../data/mock";
import { usePipelineData } from "../data/store";
import {
  DashboardIcon,
  InboxIcon,
  BellIcon,
  MemoryIcon,
  SettingsIcon,
  ProjectIcon,
  ConnectorIcon,
  PulseIcon,
  PlusIcon,
  SignOutIcon,
} from "./Icons";

const navItems: { id: Tab; label: string; icon: typeof DashboardIcon }[] = [
  { id: "dashboard", label: "Dashboard", icon: DashboardIcon },
  { id: "inbox", label: "Inbox", icon: InboxIcon },
  { id: "approvals", label: "Approvals", icon: BellIcon },
  { id: "memory", label: "Memory", icon: MemoryIcon },
  { id: "settings", label: "Settings", icon: SettingsIcon },
];

type Props = {
  activeTab: Tab;
  onTabChange: (tab: Tab) => void;
  projects: Project[];
  activeProject: Project;
  onSelectProject: (name: string) => void;
  onNewProject: () => void;
  onSignOut: () => void;
};

export default function Sidebar({
  activeTab,
  onTabChange,
  projects,
  activeProject,
  onSelectProject,
  onNewProject,
  onSignOut,
}: Props) {
  const { approvals: pendingApprovals } = usePipelineData();
  const connectedApps = activeProject.connectors
    .map((id) => integrationCatalog.find((d) => d.id === id))
    .filter((d): d is IntegrationDef => Boolean(d));

  return (
    <aside className="w-56 h-screen flex flex-col border-r border-border bg-panel flex-shrink-0">
      {/* Workspace header */}
      <div className="px-4 pt-4 pb-3 border-b border-border">
        <div className="flex items-center gap-2">
          <img src="/ghostthread-logo.png" alt="GhostThread" className="w-6 h-6 object-contain flex-shrink-0" />
          <div>
            <div className="text-[13px] font-semibold text-text tracking-tight">GhostThread</div>
            <div className="text-[11px] text-muted">{activeProject.name}</div>
          </div>
        </div>
      </div>

      {/* Nav items */}
      <nav className="flex-1 px-2 pt-3 overflow-y-auto">
        <div className="space-y-0.5">
          {navItems.map((item) => {
            const active = activeTab === item.id;
            const badge = item.id === "approvals" ? pendingApprovals.length : 0;
            return (
              <button
                key={item.id}
                onClick={() => onTabChange(item.id)}
                className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-[13px] font-medium transition-colors cursor-pointer ${
                  active
                    ? "bg-border text-text"
                    : "text-dim hover:text-text hover:bg-border/50"
                }`}
              >
                <item.icon className="w-4 h-4 flex-shrink-0" />
                <span className="flex-1 text-left">{item.label}</span>
                {badge > 0 && (
                  <span className="text-[10px] font-semibold text-sun">{badge}</span>
                )}
              </button>
            );
          })}
        </div>

        {/* Recent Projects */}
        <div className="mt-5">
          <div className="px-2.5 mb-2 flex items-center justify-between">
            <span className="text-[11px] font-semibold text-muted uppercase tracking-wider">
              Recent Projects
            </span>
            <button
              onClick={onNewProject}
              title="New project"
              className="text-muted hover:text-text transition-colors cursor-pointer"
            >
              <PlusIcon className="w-3.5 h-3.5" />
            </button>
          </div>
          <div className="space-y-0.5">
            {projects.map((p) => {
              const isActive = p.name === activeProject.name;
              return (
                <button
                  key={p.name}
                  onClick={() => onSelectProject(p.name)}
                  className={`w-full flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-[13px] transition-colors cursor-pointer ${
                    isActive
                      ? "text-text bg-border/30"
                      : "text-dim hover:text-text hover:bg-border/50"
                  }`}
                >
                  <ProjectIcon className="w-3.5 h-3.5 flex-shrink-0 text-dim" />
                  <span className="flex-1 truncate text-left">{p.name}</span>
                </button>
              );
            })}
          </div>
        </div>

        {/* Connected apps section */}
        <div className="mt-5">
          <div className="px-2.5 mb-2 text-[11px] font-semibold text-muted uppercase tracking-wider">
            Connected Apps
          </div>
          <div className="space-y-0.5">
            {connectedApps.map((c) => (
              <div
                key={c.id}
                className="flex items-center gap-2.5 px-2.5 py-1.5 rounded-md text-[13px] text-dim"
              >
                <ConnectorIcon id={c.id} className="w-3.5 h-3.5 flex-shrink-0" />
                <span className="flex-1 truncate">{c.name}</span>
                <PulseIcon className="w-1.5 h-1.5" color="#069494" />
              </div>
            ))}
          </div>
        </div>
      </nav>

      {/* User chip */}
      <div className="px-3 py-3 border-t border-border">
        <div className="group flex items-center gap-2.5">
          <div className="w-6 h-6 rounded-full bg-accent flex items-center justify-center text-[10px] font-bold text-bg">
            A
          </div>
          <div className="flex-1 min-w-0">
            <div className="text-[12px] font-medium text-text truncate">Abhi Jandyala</div>
            <div className="text-[10px] text-muted truncate">abhijandyala@gmail.com</div>
          </div>
          <button
            onClick={onSignOut}
            title="Sign out"
            className="p-1.5 rounded-md text-muted opacity-0 group-hover:opacity-100 hover:text-text hover:bg-border/60 transition-all cursor-pointer"
          >
            <SignOutIcon className="w-4 h-4" />
          </button>
        </div>
      </div>
    </aside>
  );
}
