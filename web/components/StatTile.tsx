interface StatTileProps {
  label: string;
  value: string;
  loading: boolean;
  accent?: "default" | "red" | "green" | "amber";
}

const ACCENT_TEXT: Record<NonNullable<StatTileProps["accent"]>, string> = {
  default: "text-ink-100",
  red: "text-red-300",
  green: "text-green-300",
  amber: "text-amber-300",
};

/**
 * Fixed-height tile whose numeral area never changes size between the
 * loading skeleton and the loaded value — the WO1 acceptance bar is "zero
 * layout shift during live polling," so the DOM shape here must be identical
 * in both states, only the skeleton's opacity/shimmer differs.
 */
export function StatTile({ label, value, loading, accent = "default" }: StatTileProps) {
  return (
    <div className="flex flex-col justify-between gap-2 rounded-xl border border-ink-800 bg-ink-900/60 px-4 py-3.5 shadow-panel">
      <span className="text-[11px] font-medium uppercase tracking-wide text-ink-400">
        {label}
      </span>
      <span className="relative block h-9 leading-9">
        <span
          aria-hidden={loading}
          className={`tabular block text-[28px] font-bold tracking-tight transition-opacity duration-200 ${ACCENT_TEXT[accent]} ${
            loading ? "opacity-0" : "opacity-100"
          }`}
        >
          {value}
        </span>
        {loading && (
          <span className="absolute inset-y-0 left-0 flex items-center">
            <span className="h-5 w-16 animate-pulse rounded bg-ink-700/70" />
          </span>
        )}
      </span>
    </div>
  );
}
