"use client";

import { formatDateTime, relativeTime } from "@/lib/format";
import { useNow } from "@/hooks/useNow";
import type { CaseEvent } from "@/lib/types";
import { AgentAvatar } from "./AgentAvatar";
import { CitationRow } from "./CitationChip";

/**
 * The events/ audit log rendered as a timeline — §4 persona 6 WO2. Newest
 * first; every citation the agent cited is a freeze-frame-able chip.
 */
export function EventTimeline({ events }: { events: CaseEvent[] }) {
  const now = useNow(30_000);
  const sorted = [...events].sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());

  if (sorted.length === 0) {
    return <p className="text-sm text-ink-500">No events yet.</p>;
  }

  return (
    <ol className="relative space-y-5 pl-1">
      <div aria-hidden className="absolute bottom-4 left-[15px] top-4 w-px bg-ink-800" />
      {sorted.map((e) => (
        <li key={e.event_id} className="relative flex gap-3.5">
          <div className="relative z-10">
            <AgentAvatar agent={e.agent} />
          </div>
          <div className="min-w-0 flex-1 pb-1">
            <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
              <span className="text-sm font-semibold text-ink-100">{e.action}</span>
              <span className="text-xs text-ink-500" title={formatDateTime(e.ts)}>
                {relativeTime(e.ts, now)}
              </span>
            </div>
            <p className="mt-1 text-sm leading-relaxed text-ink-300">{e.detail}</p>
            {e.citations.length > 0 && (
              <div className="mt-2">
                <CitationRow citations={e.citations} />
              </div>
            )}
          </div>
        </li>
      ))}
    </ol>
  );
}
