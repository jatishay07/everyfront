import { ActivityFeed } from "@/components/ActivityFeed";

export default function ActivityPage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink-100">Live Activity</h1>
        <p className="mt-1 text-sm text-ink-400">
          Every agent, across every case, in one stream — watch the fleet think.
        </p>
      </div>
      <ActivityFeed />
    </div>
  );
}
