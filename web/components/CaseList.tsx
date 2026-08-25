"use client";

import Link from "next/link";
import { getCases } from "@/lib/api";
import { formatUSD } from "@/lib/format";
import { usePolling } from "@/hooks/usePolling";
import type { CaseSummary } from "@/lib/types";
import { DeadlineChip } from "./DeadlineChip";
import { FrontBadge } from "./FrontBadge";

const POLL_MS = 6000;

const STATUS_LABEL: Record<CaseSummary["status"], string> = {
  intake: "Intake",
  analyzing: "Analyzing",
  strategy_ready: "Strategy ready",
  filing: "Filing",
  awaiting_response: "Awaiting response",
  denied: "Denied",
  won: "Won",
  closed: "Closed",
};

function earliestDeadline(c: CaseSummary): string | null {
  const deadlines = c.fronts
    .filter((f) => f.applicable && f.deadline)
    .map((f) => f.deadline as string)
    .sort();
  return deadlines[0] ?? null;
}

export function CaseList() {
  const { data: cases, initialLoading, error } = usePolling(getCases, POLL_MS);

  if (initialLoading) {
    return (
      <div className="space-y-2">
        {Array.from({ length: 5 }).map((_, i) => (
          <div key={i} className="h-[92px] animate-pulse rounded-xl border border-ink-800 bg-ink-900/40" />
        ))}
      </div>
    );
  }

  if (error && !cases) {
    return <p className="text-sm text-red-400">Failed to load cases: {error}</p>;
  }

  return (
    <div className="overflow-hidden rounded-xl border border-ink-800">
      <table className="w-full border-collapse text-sm">
        <thead>
          <tr className="border-b border-ink-800 bg-ink-900/60 text-left text-[11px] uppercase tracking-wide text-ink-400">
            <th className="px-4 py-2.5 font-medium">Case</th>
            <th className="px-4 py-2.5 font-medium">Hospital</th>
            <th className="px-4 py-2.5 font-medium">Status</th>
            <th className="px-4 py-2.5 font-medium">Fronts</th>
            <th className="px-4 py-2.5 font-medium">Next deadline</th>
            <th className="px-4 py-2.5 text-right font-medium">Billed</th>
          </tr>
        </thead>
        <tbody>
          {(cases ?? []).map((c) => (
            <tr
              key={c.case_id}
              className="group border-b border-ink-800/70 bg-ink-950 transition-colors last:border-b-0 hover:bg-ink-900/50"
            >
              <td className="px-4 py-3">
                <Link href={`/cases/${c.case_id}`} className="block">
                  <span className="font-medium text-ink-100 group-hover:text-white">
                    {c.patient.name}
                  </span>
                  <span className="mt-0.5 block text-xs text-ink-500">
                    {c.patient.state} · household of {c.patient.household_size}
                    {c.denial_flag?.violated && (
                      <span className="ml-2 rounded-full bg-signal-red/15 px-1.5 py-0.5 font-semibold text-red-300">
                        unlawful denial
                      </span>
                    )}
                  </span>
                </Link>
              </td>
              <td className="px-4 py-3">
                <span className="block text-ink-200">{c.hospital_name}</span>
                <span className="block text-xs text-ink-500">
                  {c.hospital_nonprofit ? "Nonprofit" : "For-profit"}
                </span>
              </td>
              <td className="px-4 py-3">
                <span className="inline-flex items-center rounded-full border border-ink-700 bg-ink-900 px-2.5 py-1 text-xs font-medium text-ink-300">
                  {STATUS_LABEL[c.status]}
                </span>
              </td>
              <td className="px-4 py-3">
                <div className="flex max-w-xs flex-wrap gap-1.5">
                  {c.fronts.map((f) => (
                    <FrontBadge key={f.front} front={f} compact />
                  ))}
                </div>
              </td>
              <td className="px-4 py-3">
                <DeadlineChip deadline={earliestDeadline(c)} />
              </td>
              <td className="px-4 py-3 text-right">
                <span className="tabular font-semibold text-ink-100">
                  {formatUSD(c.bill.amount_cents)}
                </span>
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
