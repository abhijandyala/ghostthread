import { usePipelineData } from "../data/store";
import { ConnectorIcon } from "./Icons";

function kindColor(kind: string): string {
  switch (kind) {
    case "leak": return "text-rust";
    case "pr": return "text-sun";
    case "ticket": return "text-accent";
    case "reply": return "text-accent";
    case "escalation": return "text-ember";
    case "memory": return "text-sun";
    default: return "text-dim";
  }
}

function kindLabel(kind: string): string {
  switch (kind) {
    case "leak": return "Leak";
    case "pr": return "PR";
    case "ticket": return "Ticket";
    case "reply": return "Reply";
    case "escalation": return "Escalation";
    case "memory": return "Memory";
    default: return kind;
  }
}

export default function ActivityFeed() {
  const { activity: activityItems } = usePipelineData();

  if (activityItems.length === 0) {
    return (
      <div className="rounded-lg border border-border bg-panel">
        <div className="px-5 py-3 border-b border-border">
          <h2 className="text-[11px] font-semibold text-muted uppercase tracking-wider">
            Activity
          </h2>
        </div>
        <div className="px-5 py-8 text-center text-[12px] text-muted">
          No activity yet. Activity will appear here as the pipeline processes complaints.
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border border-border bg-panel">
      <div className="px-5 py-3 border-b border-border">
        <h2 className="text-[11px] font-semibold text-muted uppercase tracking-wider">
          Activity
        </h2>
      </div>
      <div className="divide-y divide-border">
        {activityItems.map((item) => (
          <div
            key={item.id}
            className="px-5 py-3 flex items-start gap-3 hover:bg-panel2/50 transition-colors"
          >
            <div className="mt-0.5 flex-shrink-0">
              <ConnectorIcon id={item.source} className="w-3.5 h-3.5 text-dim" />
            </div>
            <div className="flex-1 min-w-0">
              <div className="flex items-center gap-2">
                <span className="text-[13px] font-medium text-text">{item.title}</span>
                <span className={`text-[10px] font-semibold uppercase tracking-wider ${kindColor(item.kind)}`}>
                  {kindLabel(item.kind)}
                </span>
              </div>
              <div className="text-[12px] text-dim mt-0.5 truncate">{item.detail}</div>
            </div>
            <div className="text-[11px] text-muted flex-shrink-0 mt-0.5">{item.ts}</div>
          </div>
        ))}
      </div>
    </div>
  );
}
