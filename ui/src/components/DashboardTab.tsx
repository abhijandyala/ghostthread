import ComplaintInput from "./ComplaintInput";
import AgentGraph from "./AgentGraph";
import StatCards from "./StatCards";
import ActivityFeed from "./ActivityFeed";

export default function DashboardTab() {
  return (
    <div className="p-6 space-y-5 max-w-[1400px]">
      <ComplaintInput />
      <StatCards />
      <AgentGraph />
      <ActivityFeed />
    </div>
  );
}
