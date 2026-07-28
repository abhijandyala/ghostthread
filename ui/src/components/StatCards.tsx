import { usePipelineData } from "../data/store";

export default function StatCards() {
  const { stats } = usePipelineData();

  const cards = [
    { label: "Complaints Processed", value: stats.complaintsProcessed.toString(), color: "text-text" },
    { label: "Leaks Found", value: stats.leaksFound.toString(), color: "text-rust" },
    { label: "Tickets Filed", value: stats.ticketsFiled.toString(), color: "text-accent" },
    { label: "PRs Opened", value: stats.prsOpened.toString(), color: "text-sun" },
    { label: "Run Cost", value: `$${stats.runCost.toFixed(2)}`, color: "text-ember" },
  ];

  return (
    <div className="grid grid-cols-5 gap-3">
      {cards.map((card) => (
        <div
          key={card.label}
          className="rounded-lg border border-border bg-panel p-4"
        >
          <div className={`text-[22px] font-semibold tracking-tight ${card.color}`}>
            {card.value}
          </div>
          <div className="text-[11px] text-muted font-medium mt-1 uppercase tracking-wider">
            {card.label}
          </div>
        </div>
      ))}
    </div>
  );
}
