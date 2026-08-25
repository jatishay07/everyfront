"use client";

import Link from "next/link";
import { getCase } from "@/lib/api";
import { formatUSD } from "@/lib/format";
import { usePolling } from "@/hooks/usePolling";
import { CitationChip } from "./CitationChip";
import { DocumentGallery } from "./DocumentGallery";
import { EventTimeline } from "./EventTimeline";
import { DeadlineLadder, FrontsPanel } from "./FrontsPanel";

const POLL_MS = 5000;

const STATUS_LABEL: Record<string, string> = {
  intake: "Intake",
  analyzing: "Analyzing",
  strategy_ready: "Strategy ready",
  filing: "Filing",
  awaiting_response: "Awaiting response",
  denied: "Denied",
  won: "Won",
  closed: "Closed",
};

export function CaseDetailView({ caseId }: { caseId: string }) {
  const { data: c, initialLoading, refresh } = usePolling(() => getCase(caseId), POLL_MS, [caseId]);

  if (initialLoading) {
    return (
      <div className="space-y-4">
        <div className="h-24 animate-pulse rounded-xl border border-ink-800 bg-ink-900/40" />
        <div className="h-64 animate-pulse rounded-xl border border-ink-800 bg-ink-900/40" />
      </div>
    );
  }

  if (!c) {
    return (
      <div className="rounded-xl border border-ink-800 bg-ink-900/40 p-8 text-center">
        <p className="text-ink-300">Case not found.</p>
        <Link href="/" className="mt-3 inline-block text-sm text-signal-blue hover:underline">
          ← Back to Command Center
        </Link>
      </div>
    );
  }

  return (
    <div className="space-y-8">
      <div>
        <Link href="/" className="text-sm text-ink-500 hover:text-ink-300">
          ← Command Center
        </Link>
        <div className="mt-2 flex flex-wrap items-start justify-between gap-4">
          <div>
            <h1 className="text-xl font-semibold tracking-tight text-ink-100">{c.patient.name}</h1>
            <p className="mt-1 text-sm text-ink-400">
              {c.hospital_name} · {c.patient.state} · {c.hospital_nonprofit ? "Nonprofit" : "For-profit"} ·{" "}
              {c.patient.insured ? "Insured" : "Uninsured"}
            </p>
          </div>
          <span className="inline-flex items-center rounded-full border border-ink-700 bg-ink-900 px-3 py-1.5 text-sm font-medium text-ink-200">
            {STATUS_LABEL[c.status] ?? c.status}
          </span>
        </div>
      </div>

      {c.denial_flag?.violated && (
        <div className="rounded-xl border border-signal-red/40 bg-signal-red/10 p-4">
          <div className="flex items-center gap-2 text-sm font-semibold text-red-200">
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4 shrink-0">
              <path
                d="M12 9v4m0 4h.01M10.3 3.9 2.7 17a2 2 0 0 0 1.7 3h15.2a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0Z"
                stroke="currentColor"
                strokeWidth="1.7"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            Unlawful denial flagged
          </div>
          <p className="mt-2 text-sm leading-relaxed text-red-100/90">{c.denial_flag.reason}</p>
          <div className="mt-2">
            <CitationChip citation={c.denial_flag.citation} />
          </div>
        </div>
      )}

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
        <SummaryStat label="Billed" value={formatUSD(c.bill.amount_cents)} />
        <SummaryStat label="Savings found" value={formatUSD(c.savings_found_cents)} accent="green" />
        <SummaryStat label="Audit findings" value={formatUSD(c.audit_findings_cents)} accent="amber" />
        <SummaryStat label="Filings sent" value={String(c.filings.length)} />
      </div>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-400">
          Deadline ladder
        </h2>
        <DeadlineLadder fronts={c.fronts} />
      </section>

      <div className="grid gap-8 lg:grid-cols-2">
        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-400">
            Fronts
          </h2>
          <FrontsPanel caseId={c.case_id} fronts={c.fronts} onApproved={refresh} />
        </section>

        <section>
          <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-400">
            Timeline
          </h2>
          <div className="max-h-[560px] overflow-y-auto rounded-xl border border-ink-800 bg-ink-900/30 p-4 scrollbar-thin">
            <EventTimeline events={c.events} />
          </div>
        </section>
      </div>

      <section>
        <h2 className="mb-3 text-sm font-semibold uppercase tracking-wide text-ink-400">
          Documents
        </h2>
        <DocumentGallery documents={c.documents} />
      </section>
    </div>
  );
}

function SummaryStat({
  label,
  value,
  accent = "default",
}: {
  label: string;
  value: string;
  accent?: "default" | "green" | "amber";
}) {
  const color =
    accent === "green" ? "text-green-300" : accent === "amber" ? "text-amber-300" : "text-ink-100";
  return (
    <div className="rounded-xl border border-ink-800 bg-ink-900/50 px-4 py-3">
      <span className="block text-[11px] font-medium uppercase tracking-wide text-ink-400">
        {label}
      </span>
      <span className={`tabular block text-lg font-bold ${color}`}>{value}</span>
    </div>
  );
}
