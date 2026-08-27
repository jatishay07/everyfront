"use client";

import { getStats } from "@/lib/api";
import { formatCompactUSD } from "@/lib/format";
import { describeFilingMix, simulatedFilingCount, SIMULATED_EXPLAINER } from "@/lib/simulated";
import { usePolling } from "@/hooks/usePolling";
import { StatTile } from "./StatTile";

const POLL_MS = 4000;

export function StatsBanner() {
  const { data: stats, initialLoading, error } = usePolling(getStats, POLL_MS);

  // §3.4's `filings_sent` and `filings_simulated`, resolved together so the
  // banner's own arithmetic is always internally consistent — a judge doing
  // the subtraction on camera must not catch a discrepancy (§4 persona 7 WO5).
  const filingsSent = stats?.filings_sent ?? 0;
  const filingMix = describeFilingMix(filingsSent, simulatedFilingCount(stats));

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
        {/*
          The one tile in this banner that was overclaiming. `filings_sent`
          keeps its numeral and its green accent — those filings are real work
          — and the qualifier rides beside it rather than replacing it, so the
          tile reads "12 · all simulated" today and "12 · 9 of 12 simulated"
          the day a vendor key exists, with no redesign in between.
        */}
        <StatTile
          label="Filings sent"
          value={String(filingsSent)}
          loading={initialLoading}
          accent="green"
          note={{ text: filingMix.note, tone: filingMix.tone, title: SIMULATED_EXPLAINER }}
        />
        <StatTile label="Human hours" value={String(stats?.human_hours ?? 0)} loading={initialLoading} accent="green" />
      </div>

      {/*
        One reserved line under the grid, never a conditionally-mounted one:
        both messages below appear and disappear in response to a poll (the
        error on a flaky fetch, the footnote the moment the last simulated
        filing becomes live), and mounting either of them would shove the
        entire case list down mid-demo. `min-h` holds the row open whether it
        is occupied or not.
      */}
      <div className="min-h-[1.25rem] text-xs leading-5">
        {error ? (
          <p className="text-red-400">
            Stats poll failed ({error}) — showing last known values.
          </p>
        ) : filingMix.simulated > 0 ? (
          // `text-ink-400`, the same weight as this page's own subtitle and
          // every tile label — not `ink-500`, which is the dimmest ink in the
          // palette and would make the one sentence a judge needs to read the
          // hardest text on the screen to read. Calm, not quiet.
          <p className="text-ink-400">{SIMULATED_EXPLAINER}</p>
        ) : null}
      </div>
    </section>
  );
}
