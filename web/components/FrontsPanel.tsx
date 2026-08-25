import { formatDate } from "@/lib/format";
import type { Front } from "@/lib/types";
import { ApproveFilingButton } from "./ApproveFilingButton";
import { CitationChip } from "./CitationChip";
import { DeadlineChip } from "./DeadlineChip";
import { FrontBadge } from "./FrontBadge";

export function FrontsPanel({
  caseId,
  fronts,
  onApproved,
}: {
  caseId: string;
  fronts: Front[];
  onApproved: () => void;
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-2">
      {fronts.map((f) => (
        <div
          key={f.front}
          className={`flex flex-col gap-3 rounded-xl border px-4 py-3.5 ${
            f.applicable ? "border-ink-800 bg-ink-900/50" : "border-ink-800/60 bg-ink-950/40"
          }`}
        >
          <div className="flex items-center justify-between gap-2">
            <FrontBadge front={f} />
            <DeadlineChip deadline={f.deadline} />
          </div>
          <p className="text-sm leading-relaxed text-ink-300">{f.reason}</p>
          <CitationChip citation={f.citation} />
          {f.applicable && f.status === "open" && (
            <div className="pt-1">
              <ApproveFilingButton caseId={caseId} front={f.front} onApproved={onApproved} />
            </div>
          )}
        </div>
      ))}
    </div>
  );
}

/** A compact, soonest-first ladder of every applicable front's deadline. */
export function DeadlineLadder({ fronts }: { fronts: Front[] }) {
  const rows = fronts
    .filter((f) => f.applicable)
    .slice()
    .sort((a, b) => {
      if (a.deadline === b.deadline) return 0;
      if (!a.deadline) return 1;
      if (!b.deadline) return -1;
      return a.deadline < b.deadline ? -1 : 1;
    });

  if (rows.length === 0) {
    return <p className="text-sm text-ink-500">No applicable fronts on this case.</p>;
  }

  return (
    <ol className="space-y-2">
      {rows.map((f, i) => (
        <li
          key={f.front}
          className="flex items-center gap-3 rounded-lg border border-ink-800 bg-ink-900/40 px-3 py-2"
        >
          <span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-ink-800 text-[11px] font-bold text-ink-300">
            {i + 1}
          </span>
          <FrontBadge front={f} compact />
          <span className="ml-auto text-xs text-ink-500">{formatDate(f.deadline)}</span>
          <DeadlineChip deadline={f.deadline} />
        </li>
      ))}
    </ol>
  );
}
