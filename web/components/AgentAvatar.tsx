import { AGENT_LABELS } from "@/lib/format";
import type { AgentName } from "@/lib/types";

const AGENT_STYLE: Record<AgentName, string> = {
  reader: "bg-signal-blue/20 text-blue-200 ring-signal-blue/30",
  lookup: "bg-signal-violet/20 text-violet-200 ring-signal-violet/30",
  clock: "bg-signal-amber/20 text-amber-200 ring-signal-amber/30",
  auditor: "bg-signal-red/20 text-red-200 ring-signal-red/30",
  strategist: "bg-signal-green/20 text-green-200 ring-signal-green/30",
  verifier: "bg-pink-400/20 text-pink-200 ring-pink-400/30",
  filer: "bg-cyan-400/20 text-cyan-200 ring-cyan-400/30",
};

const AGENT_INITIAL: Record<AgentName, string> = {
  reader: "Rd",
  lookup: "Lk",
  clock: "Ck",
  auditor: "Au",
  strategist: "St",
  verifier: "Vf",
  filer: "Fl",
};

export function AgentAvatar({ agent, size = "md" }: { agent: AgentName; size?: "sm" | "md" }) {
  const dims = size === "sm" ? "h-6 w-6 text-[10px]" : "h-8 w-8 text-xs";
  return (
    <span
      title={AGENT_LABELS[agent]}
      className={`grid shrink-0 place-items-center rounded-full font-bold ring-1 ${dims} ${AGENT_STYLE[agent]}`}
    >
      {AGENT_INITIAL[agent]}
    </span>
  );
}
