import type { Tab } from "../data/types";
import { usePipelineData } from "../data/store";
import { SearchIcon } from "./Icons";

const tabTitles: Record<Tab, string> = {
  dashboard: "Dashboard",
  inbox: "Inbox",
  approvals: "Approvals",
  memory: "Memory",
  settings: "Settings",
};

export default function TopBar({ activeTab }: { activeTab: Tab }) {
  const { backend, running } = usePipelineData();
  return (
    <header className="h-12 border-b border-border bg-panel flex items-center px-5 gap-4 flex-shrink-0">
      <h1 className="text-[14px] font-semibold text-text tracking-tight">
        {tabTitles[activeTab]}
      </h1>

      <div className="flex-1" />

      <div className="flex items-center gap-1.5 text-[11px] text-muted" title={
        backend.connected
          ? `grounding: ${backend.grounding} \u00b7 extraction: ${backend.extraction}`
          : "GhostThread engine is not reachable"
      }>
        <span
          className={`w-1.5 h-1.5 rounded-full ${
            running ? "bg-sun animate-pulse" : backend.connected ? "bg-accent" : "bg-border-light"
          }`}
        />
        {running ? "Running" : backend.connected ? "Engine connected" : "Engine offline"}
      </div>

      <div className="flex items-center gap-2 px-3 py-1.5 rounded-md bg-panel2 border border-border text-dim text-[12px] w-56">
        <SearchIcon className="w-3.5 h-3.5 flex-shrink-0" />
        <span>Search...</span>
        <span className="ml-auto text-[10px] text-muted border border-border rounded px-1">
          /
        </span>
      </div>
    </header>
  );
}
