"use client";

import Link from "next/link";
import { useMemo, useRef, useState } from "react";
import { getActivityFeed } from "@/lib/api";
import { AGENT_LABELS, formatDateTime, relativeTime } from "@/lib/format";
import { useNow } from "@/hooks/useNow";
import { usePolling } from "@/hooks/usePolling";
import type { AgentName, CaseEvent } from "@/lib/types";
import { AgentAvatar } from "./AgentAvatar";
import { CitationRow } from "./CitationChip";

const POLL_MS = 3000;
const AGENTS: AgentName[] = ["reader", "lookup", "clock", "auditor", "strategist", "verifier", "filer"];

/**
 * The global activity feed — §4 persona 6 WO3, "watch the fleet think."
 * Polls fast (3s) since this screen's entire job is to look alive.
 */
export function ActivityFeed() {
  const { data: events, initialLoading, refreshing } = usePolling(getActivityFeed, POLL_MS);
  const now = useNow(15_000);
  const [filter, setFilter] = useState<AgentName | null>(null);
  const seenIds = useRef<Set<string>>(new Set());

  const filtered = useMemo(() => {
    const list = events ?? [];
    return filter ? list.filter((e) => e.agent === filter) : list;
  }, [events, filter]);

  // Mark which ids were already on screen before this render, so only
  // genuinely new events get the "just landed" entrance animation.
  const isNew = (id: string) => {
    const seen = seenIds.current.has(id);
    return !seen;
  };
  if (events) {
    for (const e of events) seenIds.current.add(e.event_id);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap items-center gap-2">
        <FilterChip active={filter === null} onClick={() => setFilter(null)}>
          All agents
        </FilterChip>
        {AGENTS.map((a) => (
          <FilterChip key={a} active={filter === a} onClick={() => setFilter(a)}>
            {AGENT_LABELS[a]}
          </FilterChip>
        ))}
        <span className="ml-auto flex items-center gap-1.5 text-xs text-ink-500">
          <span
            className={`h-1.5 w-1.5 rounded-full bg-signal-green ${refreshing ? "animate-pulseDot" : ""}`}
          />
          polling every {POLL_MS / 1000}s
        </span>
      </div>

      {initialLoading ? (
        <div className="space-y-2">
          {Array.from({ length: 6 }).map((_, i) => (
            <div key={i} className="h-20 animate-pulse rounded-xl border border-ink-800 bg-ink-900/40" />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <p className="text-sm text-ink-500">No events match this filter.</p>
      ) : (
        <ul className="space-y-2.5">
          {filtered.map((e) => (
            <ActivityRow key={e.event_id} event={e} now={now} fresh={isNew(e.event_id)} />
          ))}
        </ul>
      )}
    </div>
  );
}

function ActivityRow({ event, now, fresh }: { event: CaseEvent; now: Date; fresh: boolean }) {
  return (
    <li
      className={`flex gap-3 rounded-xl border border-ink-800 bg-ink-900/40 p-3.5 ${
        fresh ? "animate-riseIn" : ""
      }`}
    >
      <AgentAvatar agent={event.agent} />
      <div className="min-w-0 flex-1">
        <div className="flex flex-wrap items-baseline gap-x-2 gap-y-0.5">
          <span className="text-sm font-semibold text-ink-100">{event.action}</span>
          <Link
            href={`/cases/${event.case_id}`}
            className="text-xs text-signal-blue hover:underline"
          >
            {event.case_id.replace(/^case_/, "").replace(/_/g, " ")}
          </Link>
          <span className="ml-auto text-xs text-ink-500" title={formatDateTime(event.ts)}>
            {relativeTime(event.ts, now)}
          </span>
        </div>
        <p className="mt-1 text-sm leading-relaxed text-ink-300">{event.detail}</p>
        {event.citations.length > 0 && (
          <div className="mt-2">
            <CitationRow citations={event.citations} />
          </div>
        )}
      </div>
    </li>
  );
}

function FilterChip({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full border px-3 py-1 text-xs font-medium transition-colors ${
        active
          ? "border-signal-blue/50 bg-signal-blue/15 text-blue-200"
          : "border-ink-700 bg-ink-900 text-ink-400 hover:text-ink-200"
      }`}
    >
      {children}
    </button>
  );
}
