import { useState } from "react";
import { integrationCatalog } from "../data/mock";
import type { IntegrationDef, ApprovalSettings } from "../data/types";
import { loadApprovalSettings, persistApprovalSettings } from "../data/project";
import { BrandIcon } from "./BrandIcons";
import { useProject } from "../ProjectContext";

type IntegrationItem = IntegrationDef & { connected: boolean };

type SettingsSection = "profile" | "workspace" | "integrations" | "approvals" | "notifications" | "api";

const sections: { id: SettingsSection; label: string }[] = [
  { id: "profile", label: "Profile" },
  { id: "workspace", label: "Workspace" },
  { id: "integrations", label: "Integrations" },
  { id: "approvals", label: "Approvals" },
  { id: "notifications", label: "Notifications" },
  { id: "api", label: "API Keys" },
];

function IntegrationCard({ integration }: { integration: IntegrationItem }) {
  const connected = integration.connected;
  return (
    <div className="rounded-lg border border-border bg-panel2 p-4 flex flex-col justify-between">
      <div>
        <div className="flex items-center gap-2.5 mb-2">
          <div className={`w-8 h-8 rounded-md flex items-center justify-center border ${
            connected ? "bg-bg/60 border-accent/30" : "bg-border/50 border-border"
          }`}>
            <BrandIcon id={integration.id} className="w-4 h-4" />
          </div>
          <div>
            <div className="text-[13px] font-medium text-text">{integration.name}</div>
            {connected && integration.account && (
              <div className="text-[11px] text-dim">{integration.account}</div>
            )}
          </div>
        </div>
        <div className="text-[12px] text-dim mt-1">{integration.description}</div>
      </div>
      <div className="mt-3">
        {connected ? (
          <div className="flex items-center gap-2">
            <span className="text-[11px] font-semibold text-accent uppercase tracking-wider flex items-center gap-1.5">
              <span className="w-1.5 h-1.5 rounded-full bg-accent" />
              Connected
            </span>
            <button className="ml-auto text-[11px] text-muted hover:text-text transition-colors cursor-pointer">
              Disconnect
            </button>
          </div>
        ) : (
          <button className="w-full py-1.5 rounded-md border border-border bg-panel text-[12px] font-medium text-dim hover:text-text hover:border-border-light transition-colors cursor-pointer">
            Connect
          </button>
        )}
      </div>
    </div>
  );
}

function ProfileSection() {
  return (
    <div className="space-y-4 max-w-lg">
      <div>
        <label className="block text-[11px] font-semibold text-muted uppercase tracking-wider mb-1.5">Display Name</label>
        <input
          type="text"
          defaultValue="Abhi Jandyala"
          className="w-full px-3 py-2 rounded-md bg-panel2 border border-border text-[13px] text-text focus:outline-none focus:border-accent"
        />
      </div>
      <div>
        <label className="block text-[11px] font-semibold text-muted uppercase tracking-wider mb-1.5">Email</label>
        <input
          type="email"
          defaultValue="abhijandyala@gmail.com"
          className="w-full px-3 py-2 rounded-md bg-panel2 border border-border text-[13px] text-text focus:outline-none focus:border-accent"
        />
      </div>
      <div>
        <label className="block text-[11px] font-semibold text-muted uppercase tracking-wider mb-1.5">Role</label>
        <input
          type="text"
          defaultValue="Software Engineer"
          className="w-full px-3 py-2 rounded-md bg-panel2 border border-border text-[13px] text-text focus:outline-none focus:border-accent"
        />
      </div>
      <button className="px-4 py-2 rounded-md bg-accent text-bg text-[13px] font-semibold hover:opacity-90 transition-opacity cursor-pointer">
        Save Changes
      </button>
    </div>
  );
}

function WorkspaceSection() {
  const project = useProject();
  return (
    <div className="space-y-4 max-w-lg">
      <div>
        <label className="block text-[11px] font-semibold text-muted uppercase tracking-wider mb-1.5">Workspace Name</label>
        <input
          type="text"
          defaultValue={project?.name ?? "Workspace"}
          className="w-full px-3 py-2 rounded-md bg-panel2 border border-border text-[13px] text-text focus:outline-none focus:border-accent"
        />
      </div>
      <div>
        <label className="block text-[11px] font-semibold text-muted uppercase tracking-wider mb-1.5">Pipeline Endpoint</label>
        <input
          type="text"
          defaultValue="https://cloud.rocketride.ai/ghostthread/pipeline"
          readOnly
          className="w-full px-3 py-2 rounded-md bg-bg border border-border text-[13px] text-dim font-mono text-[12px]"
        />
      </div>
      <div>
        <label className="block text-[11px] font-semibold text-muted uppercase tracking-wider mb-1.5">Default Sandbox Repo</label>
        <input
          type="text"
          defaultValue="testteam-eng/sandbox-app"
          className="w-full px-3 py-2 rounded-md bg-panel2 border border-border text-[13px] text-text font-mono text-[12px] focus:outline-none focus:border-accent"
        />
      </div>
      <button className="px-4 py-2 rounded-md bg-accent text-bg text-[13px] font-semibold hover:opacity-90 transition-opacity cursor-pointer">
        Save Changes
      </button>
    </div>
  );
}

function NotificationsSection() {
  const items = [
    { label: "New leaks detected", description: "Get notified when complaints have no matching work items", defaultChecked: true },
    { label: "PR opened", description: "When GhostThread opens a draft PR", defaultChecked: true },
    { label: "Escalations", description: "Security concerns and urgent issues requiring human review", defaultChecked: true },
    { label: "Memory updates", description: "When episodic memory is written for a recurring reporter", defaultChecked: false },
    { label: "Pipeline errors", description: "When a pipeline run fails or times out", defaultChecked: true },
  ];

  return (
    <div className="space-y-1 max-w-lg">
      {items.map((item) => (
        <label key={item.label} className="flex items-start gap-3 py-3 cursor-pointer hover:bg-panel2/30 rounded-md px-2 -mx-2 transition-colors">
          <input
            type="checkbox"
            defaultChecked={item.defaultChecked}
            className="mt-0.5 accent-accent"
          />
          <div>
            <div className="text-[13px] font-medium text-text">{item.label}</div>
            <div className="text-[12px] text-dim">{item.description}</div>
          </div>
        </label>
      ))}
    </div>
  );
}

function ApiKeysSection() {
  return (
    <div className="space-y-4 max-w-lg">
      <div className="rounded-md border border-border bg-panel2 p-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[13px] font-medium text-text">Production Key</div>
            <div className="text-[12px] text-dim font-mono mt-0.5">gt_live_••••••••••••k3mD</div>
          </div>
          <button className="text-[12px] text-dim hover:text-text transition-colors cursor-pointer">Copy</button>
        </div>
      </div>
      <div className="rounded-md border border-border bg-panel2 p-4">
        <div className="flex items-center justify-between">
          <div>
            <div className="text-[13px] font-medium text-text">Development Key</div>
            <div className="text-[12px] text-dim font-mono mt-0.5">gt_test_••••••••••••x9fR</div>
          </div>
          <button className="text-[12px] text-dim hover:text-text transition-colors cursor-pointer">Copy</button>
        </div>
      </div>
      <button className="px-4 py-2 rounded-md border border-border bg-panel text-[13px] font-medium text-dim hover:text-text hover:border-border-light transition-colors cursor-pointer">
        Generate New Key
      </button>
    </div>
  );
}

function ApprovalsSection() {
  const [settings, setSettings] = useState<ApprovalSettings>(() => loadApprovalSettings());

  const update = (patch: Partial<ApprovalSettings>) => {
    const next = { ...settings, ...patch };
    setSettings(next);
    persistApprovalSettings(next);
  };

  const isReview = settings.mode === "review";

  const actionToggles: { key: keyof ApprovalSettings; label: string; description: string }[] = [
    { key: "requireForCodeChanges", label: "Code changes", description: "Generated fixes and diffs before they are committed" },
    { key: "requireForPrPush", label: "PR pushes", description: "Draft pull requests before they are opened on GitHub" },
    { key: "requireForTickets", label: "Linear ticket creation", description: "New tickets before they are filed" },
    { key: "requireForReplies", label: "Customer replies", description: "Drafted replies before they are sent to the reporter" },
  ];

  return (
    <div className="space-y-6 max-w-lg">
      {/* Mode selection */}
      <div className="space-y-2">
        <button
          onClick={() => update({ mode: "review" })}
          className={`w-full text-left px-4 py-3.5 rounded-lg border transition-colors cursor-pointer ${
            isReview ? "border-accent bg-accent-dim/30" : "border-border bg-panel2 hover:border-border-light"
          }`}
        >
          <div className="flex items-center gap-2">
            <div className={`w-3.5 h-3.5 rounded-full border-2 flex-shrink-0 ${
              isReview ? "border-accent bg-accent" : "border-muted"
            }`} />
            <span className="text-[13px] font-medium text-text">Review &amp; approve</span>
            <span className="text-[10px] font-semibold text-muted uppercase tracking-wider">Default</span>
          </div>
          <div className="text-[12px] text-dim mt-1 ml-[22px]">
            The agent pauses before acting. Every proposed action lands in the Approvals tab and
            waits for your sign-off.
          </div>
        </button>

        <button
          onClick={() => update({ mode: "auto" })}
          className={`w-full text-left px-4 py-3.5 rounded-lg border transition-colors cursor-pointer ${
            !isReview ? "border-accent bg-accent-dim/30" : "border-border bg-panel2 hover:border-border-light"
          }`}
        >
          <div className="flex items-center gap-2">
            <div className={`w-3.5 h-3.5 rounded-full border-2 flex-shrink-0 ${
              !isReview ? "border-accent bg-accent" : "border-muted"
            }`} />
            <span className="text-[13px] font-medium text-text">Fully autonomous</span>
          </div>
          <div className="text-[12px] text-dim mt-1 ml-[22px]">
            The agent executes actions on its own and logs everything to Activity. PRs are still
            draft-only and never auto-merged.
          </div>
        </button>
      </div>

      {/* Per-action gates */}
      <div className={isReview ? "" : "opacity-40 pointer-events-none"}>
        <div className="text-[11px] font-semibold text-muted uppercase tracking-wider mb-2">
          Require approval for
        </div>
        <div className="space-y-1">
          {actionToggles.map((t) => (
            <label
              key={t.key}
              className="flex items-start gap-3 py-2.5 cursor-pointer hover:bg-panel2/30 rounded-md px-2 -mx-2 transition-colors"
            >
              <input
                type="checkbox"
                checked={settings[t.key] as boolean}
                onChange={(e) => update({ [t.key]: e.target.checked })}
                className="mt-0.5 accent-accent"
              />
              <div>
                <div className="text-[13px] font-medium text-text">{t.label}</div>
                <div className="text-[12px] text-dim">{t.description}</div>
              </div>
            </label>
          ))}
        </div>
      </div>
    </div>
  );
}

function IntegrationsSection() {
  const project = useProject();
  const items: IntegrationItem[] = integrationCatalog.map((def) => ({
    ...def,
    connected: project?.connectors.includes(def.id) ?? false,
  }));
  const connected = items.filter((i) => i.connected);
  const available = items.filter((i) => !i.connected);

  return (
    <div className="space-y-6">
      <div>
        <div className="text-[11px] font-semibold text-muted uppercase tracking-wider mb-3">Connected</div>
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-3">
          {connected.map((i) => (
            <IntegrationCard key={i.id} integration={i} />
          ))}
        </div>
      </div>
      <div>
        <div className="text-[11px] font-semibold text-muted uppercase tracking-wider mb-3">Available</div>
        <div className="grid grid-cols-1 lg:grid-cols-2 xl:grid-cols-3 gap-3">
          {available.map((i) => (
            <IntegrationCard key={i.id} integration={i} />
          ))}
        </div>
      </div>
    </div>
  );
}

export default function SettingsTab() {
  const [activeSection, setActiveSection] = useState<SettingsSection>("integrations");

  return (
    <div className="flex h-full">
      {/* Settings sidebar */}
      <div className="w-48 flex-shrink-0 border-r border-border p-3">
        <div className="space-y-0.5">
          {sections.map((section) => {
            const active = activeSection === section.id;
            return (
              <button
                key={section.id}
                onClick={() => setActiveSection(section.id)}
                className={`w-full text-left px-3 py-1.5 rounded-md text-[13px] font-medium transition-colors cursor-pointer ${
                  active
                    ? "bg-border text-text"
                    : "text-dim hover:text-text hover:bg-border/50"
                }`}
              >
                {section.label}
              </button>
            );
          })}
        </div>
      </div>

      {/* Settings content */}
      <div className="flex-1 p-6 overflow-y-auto">
        <h2 className="text-[16px] font-semibold text-text tracking-tight mb-1">
          {sections.find((s) => s.id === activeSection)?.label}
        </h2>
        <p className="text-[12px] text-dim mb-5">
          {activeSection === "profile" && "Your personal information and preferences."}
          {activeSection === "workspace" && "Workspace configuration and pipeline settings."}
          {activeSection === "integrations" && "Manage connected apps and available integrations."}
          {activeSection === "approvals" && "Control how much the agent does on its own before asking you."}
          {activeSection === "notifications" && "Configure notification preferences."}
          {activeSection === "api" && "Manage API keys for programmatic access."}
        </p>

        {activeSection === "profile" && <ProfileSection />}
        {activeSection === "workspace" && <WorkspaceSection />}
        {activeSection === "integrations" && <IntegrationsSection />}
        {activeSection === "approvals" && <ApprovalsSection />}
        {activeSection === "notifications" && <NotificationsSection />}
        {activeSection === "api" && <ApiKeysSection />}
      </div>
    </div>
  );
}
