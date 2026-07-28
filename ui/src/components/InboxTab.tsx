import { useState } from "react";
import { usePipelineData } from "../data/store";
import type { InboxItem } from "../data/types";
import { ConnectorIcon, GitHubIcon } from "./Icons";

function verdictStyle(verdict?: string) {
  if (verdict === "leak") return "text-rust bg-rust-dim";
  if (verdict === "resolved") return "text-accent bg-accent-dim";
  return "text-muted bg-panel2";
}

function toneStyle(tone?: string) {
  if (tone === "escalation") return "text-ember bg-ember-dim";
  if (tone === "returning") return "text-sun bg-sun-dim";
  return "text-accent bg-accent-dim";
}

function sourceLabel(source: string) {
  return source.charAt(0).toUpperCase() + source.slice(1);
}

function ItemRow({
  item,
  selected,
  onClick,
}: {
  item: InboxItem;
  selected: boolean;
  onClick: () => void;
}) {
  return (
    <button
      onClick={onClick}
      className={`w-full text-left px-4 py-3 flex items-start gap-3 transition-colors cursor-pointer border-l-2 ${
        selected
          ? "bg-panel2 border-l-accent"
          : "border-l-transparent hover:bg-panel2/40"
      }`}
    >
      <div className="mt-0.5 flex-shrink-0">
        <ConnectorIcon id={item.source} className="w-3.5 h-3.5 text-dim" />
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-medium text-text truncate">
          {item.title}
        </div>
        <div className="text-[12px] text-dim mt-0.5 truncate">{item.actor}</div>
        <div className="flex items-center gap-1.5 mt-1.5">
          {item.verdict && (
            <span className={`text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded ${verdictStyle(item.verdict)}`}>
              {item.verdict}
            </span>
          )}
          {item.tone && (
            <span className={`text-[10px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded ${toneStyle(item.tone)}`}>
              {item.tone.replace("_", " ")}
            </span>
          )}
          {item.prMeta && (
            <span className="text-[10px] text-dim">
              {item.prMeta.filesChanged} file{item.prMeta.filesChanged !== 1 ? "s" : ""}
              {item.prMeta.draft && " \u00b7 draft"}
            </span>
          )}
        </div>
      </div>
      <div className="text-[11px] text-muted flex-shrink-0 mt-0.5">{item.ts}</div>
    </button>
  );
}

function DetailPanel({ item }: { item: InboxItem }) {
  return (
    <div className="p-5 space-y-5 overflow-y-auto h-full">
      <div>
        <div className="flex items-center gap-2 mb-1">
          <ConnectorIcon id={item.source} className="w-4 h-4 text-dim" />
          <span className="text-[11px] text-muted font-medium uppercase tracking-wider">
            {sourceLabel(item.source)} {item.type === "pr" ? "Pull Request" : item.type === "ticket" ? "Ticket" : "Message"}
          </span>
          <span className="text-[11px] text-muted">{item.ts}</span>
        </div>
        <h2 className="text-[16px] font-semibold text-text tracking-tight">{item.title}</h2>
        <div className="text-[13px] text-dim mt-1">{item.actor}</div>
      </div>

      {(item.verdict || item.confidence != null) && (
        <div className="flex items-center gap-3">
          {item.verdict && (
            <span className={`text-[10px] font-semibold uppercase tracking-wider px-2 py-0.5 rounded ${verdictStyle(item.verdict)}`}>
              {item.verdict}
            </span>
          )}
          {item.confidence != null && (
            <div className="flex items-center gap-2 flex-1">
              <span className="text-[11px] text-muted">Confidence</span>
              <div className="flex-1 h-1.5 bg-border rounded-full max-w-[160px]">
                <div
                  className="h-full rounded-full bg-accent"
                  style={{ width: `${item.confidence * 100}%` }}
                />
              </div>
              <span className="text-[11px] text-accent font-medium">
                {(item.confidence * 100).toFixed(0)}%
              </span>
            </div>
          )}
        </div>
      )}

      {item.prMeta && (
        <div className="rounded-md border border-border bg-panel2 p-3 space-y-1.5">
          <div className="flex items-center gap-2">
            <GitHubIcon className="w-3.5 h-3.5 text-dim" />
            <span className="text-[12px] font-medium text-text font-mono">{item.prMeta.branch}</span>
            {item.prMeta.draft && (
              <span className="text-[10px] font-semibold uppercase tracking-wider text-sun bg-sun-dim px-1.5 py-0.5 rounded">
                Draft
              </span>
            )}
          </div>
          <div className="text-[11px] text-dim">
            {item.prMeta.filesChanged} file{item.prMeta.filesChanged !== 1 ? "s" : ""} changed
          </div>
        </div>
      )}

      {item.memorySummary && (
        <div className="rounded-md border border-sun-dim bg-sun-dim/30 p-3">
          <div className="text-[11px] font-semibold text-sun uppercase tracking-wider mb-1">
            Memory Context
          </div>
          <div className="text-[12px] text-dim leading-relaxed">{item.memorySummary}</div>
        </div>
      )}

      <div className="rounded-md border border-border bg-bg p-4">
        <div className="text-[13px] text-text leading-relaxed whitespace-pre-wrap">{item.body}</div>
      </div>

      {item.actionsTaken && item.actionsTaken.length > 0 && (
        <div>
          <div className="text-[11px] font-semibold text-muted uppercase tracking-wider mb-2">
            Actions Taken
          </div>
          <div className="space-y-1.5">
            {item.actionsTaken.map((action, i) => (
              <div key={i} className="flex items-center gap-2 text-[12px] text-dim">
                <span className="w-1 h-1 rounded-full bg-accent flex-shrink-0" />
                {action}
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}

export default function InboxTab() {
  const { inbox: inboxItems } = usePipelineData();
  const [selectedId, setSelectedId] = useState<string>(inboxItems[0]?.id ?? "");
  const selected = inboxItems.find((i) => i.id === selectedId) ?? inboxItems[0];

  if (inboxItems.length === 0) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="text-center">
          <div className="text-[14px] text-text font-medium mb-1">No items yet</div>
          <div className="text-[12px] text-muted">
            PRs, messages, and tickets will appear here as the pipeline processes complaints.
          </div>
        </div>
      </div>
    );
  }

  const grouped = {
    github: inboxItems.filter((i) => i.source === "github"),
    slack: inboxItems.filter((i) => i.source === "slack"),
    linear: inboxItems.filter((i) => i.source === "linear"),
  };

  return (
    <div className="flex h-full">
      <div className="w-[340px] xl:w-[420px] flex-shrink-0 border-r border-border overflow-y-auto">
        {Object.entries(grouped).map(
          ([source, items]) =>
            items.length > 0 && (
              <div key={source}>
                <div className="px-4 pt-4 pb-1.5 text-[10px] font-semibold text-muted uppercase tracking-wider">
                  {sourceLabel(source)} ({items.length})
                </div>
                <div className="divide-y divide-border">
                  {items.map((item) => (
                    <ItemRow
                      key={item.id}
                      item={item}
                      selected={item.id === selectedId}
                      onClick={() => setSelectedId(item.id)}
                    />
                  ))}
                </div>
              </div>
            )
        )}
      </div>

      <div className="flex-1 min-w-0 overflow-y-auto">
        {selected ? (
          <DetailPanel item={selected} />
        ) : (
          <div className="flex items-center justify-center h-full text-dim text-[13px]">
            Select an item to view details
          </div>
        )}
      </div>
    </div>
  );
}
