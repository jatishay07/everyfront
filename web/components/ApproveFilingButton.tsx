"use client";

import { useState } from "react";
import { approveFiling } from "@/lib/api";
import { FRONT_LABELS } from "@/lib/format";
import type { FrontType } from "@/lib/types";

/**
 * The human-in-the-loop gate — §3.3 POST /cases/{id}/approve_filing.
 * Judges reward this: nothing gets sent to a hospital, collector, or payer
 * without an explicit human click.
 */
export function ApproveFilingButton({
  caseId,
  front,
  onApproved,
}: {
  caseId: string;
  front: FrontType;
  onApproved: () => void;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleClick() {
    setBusy(true);
    setError(null);
    const result = await approveFiling(caseId, front);
    setBusy(false);
    if (result.ok) {
      onApproved();
    } else {
      setError(result.error ?? "approval failed");
    }
  }

  return (
    <div className="flex flex-col items-start gap-1">
      <button
        type="button"
        onClick={handleClick}
        disabled={busy}
        className="inline-flex items-center gap-2 rounded-lg bg-signal-green px-3.5 py-2 text-sm font-semibold text-ink-950 shadow-sm transition-colors hover:bg-emerald-400 disabled:cursor-not-allowed disabled:opacity-60"
      >
        {busy ? (
          <>
            <span className="h-3.5 w-3.5 animate-spin rounded-full border-2 border-ink-950/30 border-t-ink-950" />
            Filing…
          </>
        ) : (
          <>
            <svg viewBox="0 0 24 24" fill="none" className="h-4 w-4">
              <path d="M20 6 9 17l-5-5" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
            Approve &amp; file {FRONT_LABELS[front]}
          </>
        )}
      </button>
      {error && <span className="text-xs text-red-400">{error}</span>}
    </div>
  );
}
