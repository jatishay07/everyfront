"use client";

import { getStats } from "@/lib/api";
import { formatCompactUSD } from "@/lib/format";
import { usePolling } from "@/hooks/usePolling";
import { StatTile } from "./StatTile";

const POLL_MS = 4000;

export function StatsBanner() {
  const { data: stats, initialLoading, error } = usePolling(getStats, POLL_MS);

  return (
    <section aria-label="Fleet stats" className="space-y-2">
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        <StatTile label="Open cases" value={String(stats?.open_cases ?? 0)} loading={initialLoading} />
        <StatTile label="Hospitals" value={String(stats?.hospitals ?? 0)} loading={initialLoading} />
        <StatTile
          label="Deadlines this week"
          value={String(stats?.deadlines_this_week ?? 0)}
          loading={initialLoading}
          accent={stats && stats.deadlines_this_week > 0 ? "red" : "default"}
        />
        <StatTile
          label="Total billed"
          value={formatCompactUSD(stats?.total_billed_cents ?? 0)}
          loading={initialLoading}
        />
        <StatTile label="Charity-eligible" value={String(stats?.charity_eligible ?? 0)} loading={initialLoading} />
        <StatTile label="PPDR-eligible" value={String(stats?.ppdr_eligible ?? 0)} loading={initialLoading} />
        <StatTile
          label="Unlawful denials flagged"
          value={String(stats?.unlawful_denials_flagged ?? 0)}
          loading={initialLoading}
          accent={stats && stats.unlawful_denials_flagged > 0 ? "red" : "default"}
        />
        <StatTile
          label="Billing errors found"
          value={formatCompactUSD(stats?.audit_findings_cents ?? 0)}
          loading={initialLoading}
          accent="amber"
        />
        <StatTile
          label="Filings sent"
          value={String(stats?.filings_sent ?? 0)}
          loading={initialLoading}
          accent="green"
        />
        <StatTile label="Human hours" value={String(stats?.human_hours ?? 0)} loading={initialLoading} accent="green" />
      </div>
      {error && (
        <p className="text-xs text-red-400">
          Stats poll failed ({error}) — showing last known values.
        </p>
      )}
    </section>
  );
}
