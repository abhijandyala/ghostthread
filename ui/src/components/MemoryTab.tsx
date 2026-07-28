import { useState } from "react";
import { usePipelineData } from "../data/store";

function timelineColor(type: string) {
  if (type === "complaint") return "bg-rust";
  if (type === "resolution") return "bg-accent";
  return "bg-ember";
}

function timelineLabel(type: string) {
  if (type === "complaint") return "Complaint";
  if (type === "resolution") return "Resolution";
  return "Regression";
}

export default function MemoryTab() {
  const { reporters: recurringReporters } = usePipelineData();
  const [selectedActor, setSelectedActor] = useState<string>(recurringReporters[0]?.actor ?? "");
  const selected = recurringReporters.find((r) => r.actor === selectedActor);

  if (recurringReporters.length === 0) {
    return (
      <div className="p-6">
        <div>
          <h2 className="text-[14px] font-semibold text-text tracking-tight">Recurring Reporters</h2>
          <p className="text-[12px] text-dim mt-1">
            Episodic memory from HydraDB Memories — actors with repeat complaints across all connected sources.
          </p>
        </div>
        <div className="mt-8 text-center">
          <div className="text-[14px] text-text font-medium mb-1">No memory data yet</div>
          <div className="text-[12px] text-muted">
            Reporter history and timelines will build up as complaints are processed through the pipeline.
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 space-y-5">
      <div>
        <h2 className="text-[14px] font-semibold text-text tracking-tight">Recurring Reporters</h2>
        <p className="text-[12px] text-dim mt-1">
          Episodic memory from HydraDB Memories — actors with repeat complaints across all connected sources.
        </p>
      </div>

      <div className="rounded-lg border border-border bg-panel overflow-hidden">
        <table className="w-full">
          <thead>
            <tr className="border-b border-border">
              <th className="text-left px-4 py-2.5 text-[10px] font-semibold text-muted uppercase tracking-wider">Actor</th>
              <th className="text-left px-4 py-2.5 text-[10px] font-semibold text-muted uppercase tracking-wider w-20">Reports</th>
              <th className="text-left px-4 py-2.5 text-[10px] font-semibold text-muted uppercase tracking-wider">Categories</th>
              <th className="text-left px-4 py-2.5 text-[10px] font-semibold text-muted uppercase tracking-wider w-32">Last Seen</th>
              <th className="text-left px-4 py-2.5 text-[10px] font-semibold text-muted uppercase tracking-wider w-28">Avg Conf.</th>
            </tr>
          </thead>
          <tbody className="divide-y divide-border">
            {recurringReporters.map((reporter) => {
              const isSelected = reporter.actor === selectedActor;
              return (
                <tr
                  key={reporter.actor}
                  onClick={() => setSelectedActor(reporter.actor)}
                  className={`cursor-pointer transition-colors ${
                    isSelected ? "bg-panel2" : "hover:bg-panel2/40"
                  }`}
                >
                  <td className="px-4 py-3">
                    <span className="text-[12px] font-medium text-text font-mono">{reporter.actor}</span>
                  </td>
                  <td className="px-4 py-3">
                    <span className={`text-[13px] font-semibold ${
                      reporter.timesReported >= 3 ? "text-rust" :
                      reporter.timesReported >= 2 ? "text-ember" : "text-text"
                    }`}>
                      {reporter.timesReported}
                    </span>
                  </td>
                  <td className="px-4 py-3">
                    <div className="flex flex-wrap gap-1">
                      {[...new Set(reporter.categories)].map((cat, i) => (
                        <span key={i} className="text-[10px] text-dim bg-panel2 px-1.5 py-0.5 rounded">
                          {cat.replace(/_/g, " ")}
                        </span>
                      ))}
                    </div>
                  </td>
                  <td className="px-4 py-3 text-[12px] text-dim">{reporter.lastSeen}</td>
                  <td className="px-4 py-3">
                    <div className="flex items-center gap-2">
                      <div className="w-12 h-1.5 bg-border rounded-full">
                        <div
                          className="h-full rounded-full bg-accent"
                          style={{ width: `${reporter.avgConfidence * 100}%` }}
                        />
                      </div>
                      <span className="text-[11px] text-dim font-mono">
                        {(reporter.avgConfidence * 100).toFixed(0)}%
                      </span>
                    </div>
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>

      {selected && selected.timeline && selected.timeline.length > 0 && (
        <div className="rounded-lg border border-border bg-panel">
          <div className="px-4 py-3 border-b border-border flex items-center gap-3">
            <div className="text-[11px] font-semibold text-muted uppercase tracking-wider">Timeline</div>
            <div className="text-[12px] text-text font-medium font-mono">{selected.actor}</div>
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3">
            {selected.timeline.map((entry, i) => (
              <div key={i} className="flex items-start gap-3 px-4 py-3 border-b border-border last:border-b-0">
                <div className={`w-[9px] h-[9px] rounded-full ${timelineColor(entry.type)} flex-shrink-0 mt-[4px]`} />
                <div className="flex-1 min-w-0">
                  <div className="text-[12px] font-medium text-text">{entry.event}</div>
                  <div className="flex items-center gap-2 mt-0.5">
                    <span className="text-[11px] text-muted">{entry.date}</span>
                    <span className={`text-[9px] font-semibold uppercase tracking-wider px-1.5 py-0.5 rounded ${
                      entry.type === "complaint" ? "text-rust bg-rust-dim" :
                      entry.type === "resolution" ? "text-accent bg-accent-dim" :
                      "text-ember bg-ember-dim"
                    }`}>
                      {timelineLabel(entry.type)}
                    </span>
                  </div>
                </div>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
