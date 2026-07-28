import { useState } from "react";
import { usePipelineData } from "../data/store";
import { loadApprovalSettings } from "../data/project";
import { ConnectorIcon } from "./Icons";

function kindLabel(kind: string) {
  switch (kind) {
    case "code_change": return "Code Change";
    case "pr_push": return "PR Push";
    case "ticket": return "Linear Ticket";
    case "reply": return "Reply";
    default: return kind;
  }
}

function kindColor(kind: string) {
  switch (kind) {
    case "code_change": return "text-ember";
    case "pr_push": return "text-sun";
    case "ticket": return "text-accent";
    case "reply": return "text-accent";
    default: return "text-dim";
  }
}

export default function ApprovalsTab() {
  const { approvals: pendingApprovals, resolveApproval } = usePipelineData();
  const [settings] = useState(() => loadApprovalSettings());

  if (pendingApprovals.length === 0) {
    return (
      <div className="p-6">
        <div>
          <h2 className="text-[14px] font-semibold text-text tracking-tight">Pending Approvals</h2>
          <p className="text-[12px] text-dim mt-1">
            Actions the agent wants to take that require your sign-off before executing.
          </p>
        </div>
        <div className="mt-8 text-center max-w-md mx-auto">
          <div className="text-[14px] text-text font-medium mb-1">Nothing waiting on you</div>
          {settings.mode === "review" ? (
            <div className="text-[12px] text-muted leading-relaxed">
              When GhostThread proposes a code change, pushes a PR, files a Linear ticket, or drafts
              a customer reply, it will pause and appear here for review before anything runs.
            </div>
          ) : (
            <div className="text-[12px] text-muted leading-relaxed">
              Autonomous mode is on — the agent executes actions automatically and logs them to
              Activity. Switch to Review &amp; approve in Settings to gate actions here.
            </div>
          )}
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-5">
      <div>
        <h2 className="text-[14px] font-semibold text-text tracking-tight">Pending Approvals</h2>
        <p className="text-[12px] text-dim mt-1">
          Actions the agent wants to take that require your sign-off before executing.
        </p>
      </div>

      <div className="rounded-lg border border-border bg-panel divide-y divide-border">
        {pendingApprovals.map((item) => (
          <div key={item.id} className="px-5 py-4 flex items-start gap-3">
            <div className="mt-0.5 flex-shrink-0">
              <ConnectorIcon id={item.source} className="w-4 h-4 text-dim" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-[13px] font-medium text-text">{item.title}</span>
                <span className={`text-[10px] font-semibold uppercase tracking-wider ${kindColor(item.kind)}`}>
                  {kindLabel(item.kind)}
                </span>
              </div>
              <div className="text-[12px] text-dim mt-0.5">{item.detail}</div>
            </div>
            <div className="flex items-center gap-2 flex-shrink-0">
              <button
                onClick={() => resolveApproval(item.id, false)}
                className="px-3 py-1.5 rounded-md border border-border text-[12px] font-medium text-dim hover:text-text transition-colors cursor-pointer"
              >
                Reject
              </button>
              <button
                onClick={() => resolveApproval(item.id, true)}
                className="px-3 py-1.5 rounded-md bg-accent text-bg text-[12px] font-semibold hover:opacity-90 transition-opacity cursor-pointer"
              >
                Approve
              </button>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
