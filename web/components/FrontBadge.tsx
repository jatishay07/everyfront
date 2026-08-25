import { FRONT_LABELS } from "@/lib/format";
import type { Front } from "@/lib/types";

const STATUS_STYLE: Record<Front["status"], string> = {
  open: "border-signal-amber/40 bg-signal-amber/10 text-amber-200",
  filed: "border-signal-blue/40 bg-signal-blue/10 text-blue-200",
  won: "border-signal-green/40 bg-signal-green/10 text-green-200",
  lost: "border-signal-red/40 bg-signal-red/10 text-red-200",
  na: "border-ink-700 bg-ink-900 text-ink-500",
};

const STATUS_LABEL: Record<Front["status"], string> = {
  open: "Open",
  filed: "Filed",
  won: "Won",
  lost: "Lost",
  na: "N/A",
};

export function FrontBadge({ front, compact = false }: { front: Front; compact?: boolean }) {
  if (!front.applicable) {
    return (
      <span className="inline-flex items-center gap-1.5 rounded-full border border-ink-800 bg-ink-900/60 px-2.5 py-1 text-xs font-medium text-ink-500 line-through decoration-ink-600">
        {FRONT_LABELS[front.front]}
      </span>
    );
  }
  return (
    <span
      className={`inline-flex items-center gap-1.5 rounded-full border px-2.5 py-1 text-xs font-semibold ${STATUS_STYLE[front.status]}`}
    >
      {FRONT_LABELS[front.front]}
      {!compact && <span className="opacity-70">· {STATUS_LABEL[front.status]}</span>}
    </span>
  );
}
