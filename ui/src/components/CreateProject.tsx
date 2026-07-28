import { useEffect, useState } from "react";
import { integrationCatalog } from "../data/mock";
import { BrandIcon } from "./BrandIcons";
import Plasma from "./Plasma";

type Props = {
  onProjectCreated: (name: string, selectedConnectors: string[]) => void;
  onCancel?: () => void;
};

function WorkspaceLoader({
  projectName,
  connectorIds,
  onDone,
}: {
  projectName: string;
  connectorIds: string[];
  onDone: () => void;
}) {
  const steps = [
    ...connectorIds.map((id) => {
      const def = integrationCatalog.find((d) => d.id === id);
      return `Linking ${def?.name ?? id}\u2026`;
    }),
    "Waking GhostThread Prime\u2026",
    "Preparing your dashboard\u2026",
  ];
  const [stepIdx, setStepIdx] = useState(0);
  const [leaving, setLeaving] = useState(false);

  useEffect(() => {
    if (stepIdx < steps.length - 1) {
      const t = setTimeout(() => setStepIdx((i) => i + 1), 750);
      return () => clearTimeout(t);
    }
    // Last step: hold briefly, fade out, then hand off to the app.
    const hold = setTimeout(() => setLeaving(true), 900);
    const done = setTimeout(onDone, 1450);
    return () => {
      clearTimeout(hold);
      clearTimeout(done);
    };
  }, [stepIdx, steps.length, onDone]);

  const progress = ((stepIdx + 1) / steps.length) * 100;

  return (
    <div
      className={`absolute inset-0 z-20 flex flex-col items-center justify-center bg-bg transition-opacity duration-500 ${
        leaving ? "opacity-0" : "opacity-100"
      }`}
    >
      {/* Floating ghost */}
      <div className="relative flex flex-col items-center animate-scale-in">
        <div className="relative">
          <div className="absolute -inset-10 rounded-full bg-accent/20 blur-3xl animate-glow-pulse" />
          <img
            src="/ghostthread-logo.png"
            alt=""
            className="relative w-20 h-20 object-contain animate-ghost-float"
          />
        </div>
        <div className="mt-3 w-12 h-2 rounded-full bg-white/15 blur-[4px] animate-ghost-shadow" />
      </div>

      <h2 className="mt-8 text-[18px] font-semibold text-text tracking-tight">
        Getting <span className="text-accent">{projectName}</span> ready
      </h2>

      {/* Cycling status line — keyed so each step fades in */}
      <div key={stepIdx} className="mt-2 text-[13px] text-dim animate-fade-in-up">
        {steps[stepIdx]}
      </div>

      {/* Progress */}
      <div className="mt-6 w-52 h-[3px] rounded-full bg-border overflow-hidden">
        <div
          className="h-full rounded-full bg-accent transition-all duration-700 ease-out"
          style={{ width: `${progress}%` }}
        />
      </div>
    </div>
  );
}

export default function CreateProject({ onProjectCreated, onCancel }: Props) {
  const [projectName, setProjectName] = useState("");
  const [selectedConnectors, setSelectedConnectors] = useState<string[]>([]);
  const [step, setStep] = useState<"name" | "connectors">("name");
  const [loading, setLoading] = useState(false);

  const toggleConnector = (id: string) => {
    setSelectedConnectors((prev) =>
      prev.includes(id) ? prev.filter((c) => c !== id) : [...prev, id]
    );
  };

  const canProceed =
    step === "name" ? projectName.trim().length >= 2 : selectedConnectors.length >= 1;

  const handleNext = () => {
    if (step === "name" && canProceed) {
      setStep("connectors");
    } else if (step === "connectors" && canProceed && !loading) {
      setLoading(true);
    }
  };

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && canProceed) handleNext();
  };

  return (
    <div className="relative h-screen bg-bg overflow-hidden">
      {/* Workspace-prep loader, shown once OAuths are picked */}
      {loading && (
        <WorkspaceLoader
          projectName={projectName.trim()}
          connectorIds={selectedConnectors}
          onDone={() => onProjectCreated(projectName.trim(), selectedConnectors)}
        />
      )}

      {/* Plasma background */}
      <div
        className={`absolute inset-0 transition-opacity duration-700 ${
          loading ? "opacity-0" : "opacity-100"
        }`}
      >
        <Plasma
          color="#ffffff"
          speed={1}
          direction="forward"
          scale={1.9}
          opacity={1}
          mouseInteractive={false}
          renderScale={0.55}
          maxDpr={1.5}
          targetFps={60}
          iterations={60}
        />
        {/* Readability vignette over the shader */}
        <div className="absolute inset-0 bg-gradient-to-b from-bg/70 via-bg/35 to-bg/85 pointer-events-none" />
      </div>

      {/* Foreground */}
      <div
        className={`relative z-10 h-full flex flex-col transition-all duration-500 ${
          loading ? "opacity-0 scale-[0.98] pointer-events-none" : "opacity-100"
        }`}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-8 pt-6">
          <div className="flex items-center gap-2.5 animate-fade-in-up">
            <img src="/ghostthread-logo.png" alt="GhostThread" className="w-7 h-7 object-contain" />
            <span className="text-[14px] font-semibold text-text tracking-tight">GhostThread</span>
          </div>
          {onCancel && (
            <button
              onClick={onCancel}
              className="text-[12px] text-dim hover:text-text transition-colors cursor-pointer animate-fade-in-up"
            >
              &larr; Back to dashboard
            </button>
          )}
        </div>

        {/* Centered card */}
        <div className="flex-1 flex items-center justify-center px-6 py-8 overflow-y-auto">
          <div
            className={`w-full transition-all duration-500 ${
              step === "name" ? "max-w-[440px]" : "max-w-[620px]"
            }`}
          >
            <div className="rounded-2xl border border-white/10 bg-panel/70 backdrop-blur-2xl shadow-[0_24px_80px_rgba(0,0,0,0.55)] animate-scale-in">
              {/* Step indicator */}
              <div className="flex items-center gap-2 px-8 pt-7">
                <div className={`h-[3px] flex-1 rounded-full transition-colors duration-300 ${
                  step === "name" ? "bg-accent" : "bg-accent/40"
                }`} />
                <div className={`h-[3px] flex-1 rounded-full transition-colors duration-300 ${
                  step === "connectors" ? "bg-accent" : "bg-border-light"
                }`} />
              </div>

              {step === "name" ? (
                <div key="name" className="px-8 pb-8 pt-6">
                  <div className="animate-fade-in-up">
                    <h1 className="text-[24px] font-semibold text-text tracking-tight leading-tight">
                      Create a new project
                    </h1>
                    <p className="text-[13px] text-dim mt-1.5 leading-relaxed">
                      Projects group your connected apps, pipeline runs, and memory into one workspace.
                    </p>
                  </div>

                  <div className="animate-fade-in-up mt-7" style={{ animationDelay: "80ms" }}>
                    <label className="block text-[11px] font-semibold text-muted uppercase tracking-wider mb-2">
                      Project name
                    </label>
                    <input
                      type="text"
                      value={projectName}
                      onChange={(e) => setProjectName(e.target.value)}
                      onKeyDown={handleKeyDown}
                      placeholder="e.g. TestTeam, Acme Support"
                      autoFocus
                      className="w-full px-4 py-3 rounded-xl bg-bg/60 border border-border-light text-[15px] text-text placeholder:text-muted focus:outline-none focus:border-accent focus:ring-2 focus:ring-accent/20 transition-all"
                    />
                  </div>

                  <button
                    onClick={handleNext}
                    disabled={!canProceed}
                    style={{ animationDelay: "140ms" }}
                    className={`animate-fade-in-up mt-6 w-full py-3 rounded-xl text-[13px] font-semibold transition-all cursor-pointer ${
                      canProceed
                        ? "bg-accent text-bg hover:opacity-90 shadow-[0_8px_24px_rgba(6,148,148,0.25)]"
                        : "bg-border/70 text-muted cursor-not-allowed"
                    }`}
                  >
                    Continue
                  </button>
                </div>
              ) : (
                <div key="connectors" className="px-8 pb-8 pt-6">
                  <div className="animate-fade-in-up">
                    <h1 className="text-[24px] font-semibold text-text tracking-tight leading-tight">
                      Connect your apps
                    </h1>
                    <p className="text-[13px] text-dim mt-1.5 leading-relaxed">
                      Choose the integrations{" "}
                      <span className="text-text font-medium">{projectName}</span> will monitor and
                      act through. You can add more later in Settings.
                    </p>
                  </div>

                  <div className="grid grid-cols-2 gap-2 mt-6 max-h-[46vh] overflow-y-auto pr-1">
                    {integrationCatalog.map((integration, i) => {
                      const isSelected = selectedConnectors.includes(integration.id);
                      return (
                        <button
                          key={integration.id}
                          onClick={() => toggleConnector(integration.id)}
                          style={{ animationDelay: `${60 + i * 40}ms` }}
                          className={`animate-fade-in-up group flex items-center gap-3 px-3.5 py-3 rounded-xl border text-left transition-all duration-200 cursor-pointer ${
                            isSelected
                              ? "border-accent/70 bg-accent/10 shadow-[0_0_0_1px_rgba(6,148,148,0.3)]"
                              : "border-white/8 bg-bg/40 hover:bg-bg/60 hover:border-white/20"
                          }`}
                        >
                          <span
                            className={`w-9 h-9 rounded-lg flex items-center justify-center flex-shrink-0 border transition-colors ${
                              isSelected
                                ? "bg-bg/70 border-accent/40"
                                : "bg-white/5 border-white/8 group-hover:border-white/15"
                            }`}
                          >
                            <BrandIcon id={integration.id} className="w-[18px] h-[18px]" />
                          </span>
                          <span className="flex-1 min-w-0">
                            <span className="block text-[13px] font-medium text-text">
                              {integration.name}
                            </span>
                            <span className="block text-[11px] text-dim truncate">
                              {integration.description}
                            </span>
                          </span>
                          <span
                            className={`w-[18px] h-[18px] rounded-full border flex items-center justify-center flex-shrink-0 transition-all duration-200 ${
                              isSelected
                                ? "border-accent bg-accent scale-100"
                                : "border-border-light scale-90 opacity-60"
                            }`}
                          >
                            {isSelected && (
                              <svg viewBox="0 0 12 12" className="w-2.5 h-2.5 text-bg" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round">
                                <path d="M2.5 6l2.5 2.5 4.5-5" />
                              </svg>
                            )}
                          </span>
                        </button>
                      );
                    })}
                  </div>

                  <div className="flex items-center gap-3 mt-6 animate-fade-in-up" style={{ animationDelay: "200ms" }}>
                    <button
                      onClick={() => setStep("name")}
                      className="px-5 py-3 rounded-xl border border-white/10 text-[13px] font-medium text-dim hover:text-text hover:border-white/25 transition-all cursor-pointer"
                    >
                      Back
                    </button>
                    <button
                      onClick={handleNext}
                      disabled={!canProceed}
                      className={`flex-1 py-3 rounded-xl text-[13px] font-semibold transition-all cursor-pointer ${
                        canProceed
                          ? "bg-accent text-bg hover:opacity-90 shadow-[0_8px_24px_rgba(6,148,148,0.25)]"
                          : "bg-border/70 text-muted cursor-not-allowed"
                      }`}
                    >
                      {loading
                        ? "Creating\u2026"
                        : selectedConnectors.length > 0
                          ? `Create project \u00b7 ${selectedConnectors.length} app${selectedConnectors.length !== 1 ? "s" : ""}`
                          : "Create project"}
                    </button>
                  </div>

                  <div className="mt-3.5 text-[11px] text-muted text-center">
                    Saved to your local project database
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
