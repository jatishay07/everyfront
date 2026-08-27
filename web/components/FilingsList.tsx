import { formatDateTime, FRONT_LABELS } from "@/lib/format";
import { isSimulated } from "@/lib/simulated";
import type { Filing } from "@/lib/types";
import { SimulatedBadge } from "./SimulatedBadge";

const CHANNEL_LABEL: Record<Filing["channel"], string> = {
  fax: "Fax",
  mail: "Certified mail",
  email: "Email",
};

const STATUS_LABEL: Record<Filing["status"], string> = {
  sent: "Sent",
  delivered: "Delivered",
  failed: "Failed",
};

/**
 * Every filing this case has made, one row each — §4 persona 6 WO2's
 * "document gallery + proof" half.
 *
 * The case detail screen previously surfaced filings only as a count in the
 * summary strip ("Filings sent: 3"), which meant the vendor id, the channel
 * and — the point — whether anything was actually transmitted existed in the
 * API response and nowhere on screen. Send mode is a first-class column here,
 * not a tooltip: the acceptance bar is that a judge can read it off a
 * freeze-frame without hovering or clicking.
 */
export function FilingsList({ filings }: { filings: Filing[] }) {
  if (filings.length === 0) {
    return <p className="text-sm text-ink-500">No filings sent on this case yet.</p>;
  }

  const sorted = [...filings].sort(
    (a, b) => new Date(b.sent_at).getTime() - new Date(a.sent_at).getTime()
  );

  return (
    <ul className="grid gap-3 sm:grid-cols-2">
      {sorted.map((f) => {
        const simulated = isSimulated(f);
        return (
          <li
            key={f.filing_id}
            className="flex flex-col gap-2 rounded-xl border border-ink-800 bg-ink-900/40 px-4 py-3.5"
          >
            <div className="flex items-start justify-between gap-2">
              <span className="text-sm font-semibold text-ink-100">
                {FRONT_LABELS[f.front] ?? f.front}
              </span>
              <SimulatedBadge simulated={simulated} />
            </div>
            <span className="text-xs text-ink-400">
              {CHANNEL_LABEL[f.channel] ?? f.channel} · {STATUS_LABEL[f.status] ?? f.status} ·{" "}
              {formatDateTime(f.sent_at)}
            </span>
            {f.real_destination && (
              <span className="text-xs text-ink-500">
                {/* Named differently in each mode on purpose: in test mode
                    this address is where the filing WOULD have gone, and
                    saying "to" would be the same overclaim in miniature. */}
                {simulated ? "Addressed to" : "Sent to"}{" "}
                <span className="text-ink-300">{f.real_destination}</span>
              </span>
            )}
            <span
              className="truncate font-mono text-[11px] text-ink-500"
              title={f.vendor_id}
            >
              {f.vendor_id}
              {f.proof?.tracking ? ` · ${f.proof.tracking}` : ""}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
