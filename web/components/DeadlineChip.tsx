"use client";

import { daysUntil, formatDate } from "@/lib/format";
import { useNow } from "@/hooks/useNow";

/** Red at ≤7 days, per §4 persona 6 WO1. */
export function DeadlineChip({ deadline }: { deadline: string | null }) {
  const now = useNow(60_000);

  if (!deadline) {
    return (
      <span className="inline-flex items-center gap-1 rounded-full border border-ink-700 bg-ink-900 px-2.5 py-1 text-xs font-medium text-ink-400">
        No deadline
      </span>
    );
  }

  const days = daysUntil(deadline, now);
  const urgent = days <= 7;
  const expired = days < 0;

  const tone = expired
    ? "border-signal-red/50 bg-signal-red/15 text-red-200"
    : urgent
    ? "border-signal-red/40 bg-signal-red/10 text-red-300"
    : "border-signal-blue/30 bg-signal-blue/10 text-blue-200";

  const label = expired
    ? `${Math.abs(days)}d overdue`
    : days === 0
    ? "due today"
    : `${days}d left`;

  return (
    <span
      title={formatDate(deadline)}
      className={`tabular inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${tone}`}
    >
      {(urgent || expired) && (
        <span className="h-1.5 w-1.5 shrink-0 animate-pulseDot rounded-full bg-signal-red" />
      )}
      {label}
    </span>
  );
}
