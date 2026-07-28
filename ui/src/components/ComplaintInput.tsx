import { useState } from "react";
import { useProject } from "../ProjectContext";
import { integrationCatalog } from "../data/mock";
import { usePipelineData } from "../data/store";

// Message-capable sources a complaint can arrive from.
const MESSAGE_SOURCES = ["slack", "gmail", "discord", "teams", "outlook", "intercom"];
// Sources the backend can actually ingest a live complaint into.
const BACKEND_SOURCES = ["slack", "gmail"];

export default function ComplaintInput() {
  const project = useProject();
  const { running, backend, runComplaint, runFullScan } = usePipelineData();
  const [text, setText] = useState("");

  const sources = (project?.connectors ?? [])
    .filter((id) => MESSAGE_SOURCES.includes(id))
    .map((id) => integrationCatalog.find((d) => d.id === id))
    .filter((d): d is NonNullable<typeof d> => Boolean(d));

  const [source, setSource] = useState(sources[0]?.id ?? "slack");
  const canRun = text.trim().length >= 8 && backend.connected && !running;

  const handleRun = async () => {
    if (!canRun) return;
    const backendSource = BACKEND_SOURCES.includes(source) ? source : "slack";
    const submitted = text;
    setText("");
    await runComplaint(submitted, backendSource);
  };

  return (
    <div className="rounded-lg border border-border bg-panel p-4">
      <div className="flex items-center justify-between mb-3">
        <div className="text-[11px] font-semibold text-muted uppercase tracking-wider">
          Live Complaint
        </div>
        <button
          onClick={runFullScan}
          disabled={!backend.connected || running}
          className={`text-[11px] font-medium transition-colors ${
            backend.connected && !running
              ? "text-dim hover:text-text cursor-pointer"
              : "text-muted cursor-not-allowed"
          }`}
        >
          {running ? "Pipeline running..." : "Run full scan"}
        </button>
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={text}
          onChange={(e) => setText(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && handleRun()}
          placeholder={
            backend.connected
              ? "Paste a customer complaint to run it through the pipeline..."
              : "Engine offline — start the GhostThread backend to run complaints."
          }
          className="flex-1 px-3 py-2 rounded-md bg-panel2 border border-border text-[13px] text-text placeholder:text-muted focus:outline-none focus:border-accent"
        />
        {sources.length > 1 && (
          <select
            value={source}
            onChange={(e) => setSource(e.target.value)}
            className="px-2.5 py-2 rounded-md bg-panel2 border border-border text-[13px] text-dim focus:outline-none focus:border-accent cursor-pointer"
          >
            {sources.map((s) => (
              <option key={s.id} value={s.id}>{s.name}</option>
            ))}
          </select>
        )}
        <button
          disabled={!canRun}
          onClick={handleRun}
          className={`px-5 py-2 rounded-md text-[13px] font-semibold transition-colors ${
            canRun
              ? "bg-accent text-bg hover:opacity-90 cursor-pointer"
              : "bg-border text-muted cursor-not-allowed"
          }`}
        >
          {running ? "Running..." : "Run"}
        </button>
      </div>
    </div>
  );
}
