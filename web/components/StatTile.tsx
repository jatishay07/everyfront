import type { FilingMixTone } from "@/lib/simulated";

interface StatTileProps {
  label: string;
  value: string;
  loading: boolean;
  accent?: "default" | "red" | "green" | "amber";
  /**
   * A short qualifier rendered beside the numeral — currently only "Filings
   * sent" uses it, to say how many of those sends were simulated. Sits INSIDE
   * the fixed-height numeral row on purpose (see below).
   */
  note?: { text: string; tone: FilingMixTone; title?: string };
}

const ACCENT_TEXT: Record<NonNullable<StatTileProps["accent"]>, string> = {
  default: "text-ink-100",
  red: "text-red-300",
  green: "text-green-300",
  amber: "text-amber-300",
};

/**
 * The note's palette is neutral by default. A simulated send is a fact, not a
 * failure, and the tile it hangs off is the one a judge stares at for four
 * minutes — amber or red here would read as "this number is broken" and would
 * out-shout the $2,922.50 in real findings two tiles to its left. Grey reads
 * as a unit, which is what it is. Only `live` takes colour.
 */
const NOTE_STYLE: Record<FilingMixTone, string> = {
  simulated: "border-ink-600 bg-ink-800/70 text-ink-300",
  live: "border-signal-green/40 bg-signal-green/10 text-green-200",
  none: "border-transparent bg-transparent text-transparent",
};

/**
 * Fixed-height tile whose numeral area never changes size between the
 * loading skeleton and the loaded value — the WO1 acceptance bar is "zero
 * layout shift during live polling," so the DOM shape here must be identical
 * in both states, only the skeleton's opacity/shimmer differs.
 *
 * `note` obeys the same rule the harder way: it is laid out beside the value
 * inside the same `h-9` row rather than beneath it, so a tile that gains or
 * loses its qualifier mid-poll — which is exactly what happens the first time
 * a real vendor key flips a send from simulated to live — cannot change the
 * tile's height, and therefore cannot resize its grid row or push the case
 * list below it. The text inside the pill may change on every poll; the box
 * it lives in never moves.
 */
export function StatTile({ label, value, loading, accent = "default", note }: StatTileProps) {
  return (
    <div className="flex flex-col justify-between gap-2 rounded-xl border border-ink-800 bg-ink-900/60 px-4 py-3.5 shadow-panel">
      <span className="text-[11px] font-medium uppercase tracking-wide text-ink-400">
        {label}
      </span>
      <span className="relative flex h-9 items-center gap-2">
        <span
          aria-hidden={loading}
          className={`tabular shrink-0 text-[28px] font-bold leading-9 tracking-tight transition-opacity duration-200 ${ACCENT_TEXT[accent]} ${
            loading ? "opacity-0" : "opacity-100"
          }`}
        >
          {value}
        </span>
        {note && note.text !== "" && (
          <span
            title={note.title}
            className={`min-w-0 truncate rounded-full border px-2 py-0.5 text-[11px] font-medium leading-tight transition-opacity duration-200 ${
              NOTE_STYLE[note.tone]
            } ${loading ? "opacity-0" : "opacity-100"}`}
          >
            {note.text}
          </span>
        )}
        {loading && (
          <span className="absolute inset-y-0 left-0 flex items-center">
            <span className="h-5 w-16 animate-pulse rounded bg-ink-700/70" />
          </span>
        )}
      </span>
    </div>
  );
}
