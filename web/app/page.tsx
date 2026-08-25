import { CaseList } from "@/components/CaseList";
import { StatsBanner } from "@/components/StatsBanner";

export default function CommandCenterPage() {
  return (
    <div className="space-y-8">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink-100">Command Center</h1>
        <p className="mt-1 text-sm text-ink-400">
          Every case the fleet is working, and what it has found so far.
        </p>
      </div>

      <StatsBanner />

      <div>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-400">
          Cases
        </h2>
        <CaseList />
      </div>
    </div>
  );
}
