import { useEffect, useState } from "react";

// Retro-sunset palette, bottom layer first. The dark bg layer sits on top so
// the screen ends dark before the reveal cascade plays in reverse.
const LAYERS = ["#B7410E", "#BE5103", "#FFCE1B", "#069494", "#0B0B0C"];

const GOODBYE_MS = 1500;
const COVER_MS = 1050;
const REVEAL_MS = 1100;

type Phase = "goodbye" | "cover" | "reveal";

type Props = {
  name: string;
  /** Called once the screen is fully covered — swap what's behind here. */
  onCovered: () => void;
  /** Called when the reveal finishes — unmount the overlay here. */
  onDone: () => void;
};

export default function SignOutTransition({ name, onCovered, onDone }: Props) {
  const [phase, setPhase] = useState<Phase>("goodbye");

  useEffect(() => {
    const t1 = setTimeout(() => setPhase("cover"), GOODBYE_MS);
    const t2 = setTimeout(() => {
      onCovered();
      setPhase("reveal");
    }, GOODBYE_MS + COVER_MS);
    const t3 = setTimeout(onDone, GOODBYE_MS + COVER_MS + REVEAL_MS);
    return () => {
      clearTimeout(t1);
      clearTimeout(t2);
      clearTimeout(t3);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="fixed inset-0 z-50 overflow-hidden">
      {/* Goodbye screen — removed once the layers have covered it */}
      {phase !== "reveal" && (
        <div className="absolute inset-0 bg-bg flex items-center justify-center">
          <div className="text-center animate-fade-in-up">
            <div className="relative inline-block">
              <div className="absolute -inset-8 rounded-full bg-accent/15 blur-2xl animate-glow-pulse" />
              <img
                src="/ghostthread-logo.png"
                alt=""
                className="relative w-14 h-14 object-contain mx-auto animate-ghost-float"
              />
            </div>
            <h1 className="mt-6 text-[28px] font-semibold text-text tracking-tight">
              Goodbye, {name}
            </h1>
            <p className="text-[13px] text-dim mt-1.5">Signing you out&hellip;</p>
          </div>
        </div>
      )}

      {/* Palette wipe — covers bottom-up, then cascades away top layer first */}
      {phase !== "goodbye" &&
        LAYERS.map((color, i) => (
          <div
            key={color}
            className="absolute inset-0"
            style={{
              backgroundColor: color,
              zIndex: i + 1,
              animation:
                phase === "cover"
                  ? `slide-cover 0.55s cubic-bezier(0.7, 0, 0.2, 1) ${i * 100}ms both`
                  : `slide-reveal 0.55s cubic-bezier(0.7, 0, 0.2, 1) ${(LAYERS.length - 1 - i) * 100}ms both`,
            }}
          />
        ))}
    </div>
  );
}
