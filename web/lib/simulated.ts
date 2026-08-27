/**
 * "Did this filing actually leave the building?" — the one place the UI is
 * allowed to answer that.
 *
 * WHY THIS IS A MODULE AND NOT THREE INLINE TERNARIES.
 * Until today every filing this system has made was a recording stub, the
 * `filings/` record read `status: "sent"`, and the banner read "12 filings
 * sent" with nothing on screen saying otherwise. The backend now reports the
 * truth per filing and in aggregate. The failure mode being designed against
 * is not "today's screen is wrong" — it is the day a real Phaxio/Lob key
 * exists and SOME filings are live while others are not. Every function here
 * is total over that mixed world; not one of them encodes "all simulated".
 *
 * THE DEFAULT DIRECTION IS LOAD-BEARING. Absent, null, or non-boolean reads
 * as SIMULATED, never as live — matching `services/api/main.py`'s
 * `normalize_filing` and `agent_core.delivery_bridge.simulated_flag` exactly.
 * A record that does not say it was real is not evidence that it was, and an
 * error in this direction can only ever understate what the system did.
 */
import type { DashboardStats, Filing } from "./types";

/** True unless the delivery layer explicitly reported a live send. */
export function isSimulated(
  filing: Pick<Filing, "simulated"> | null | undefined
): boolean {
  return filing?.simulated !== false;
}

/**
 * How many of `stats.filings_sent` were simulated.
 *
 * An absent/invalid count resolves to `filings_sent` (all of them), not to 0.
 * The deployed API revision omits the key entirely (verified via curl), and
 * "0 simulated" is an affirmative claim that all twelve filings were really
 * transmitted — a claim an API that never mentioned the subject has not made.
 *
 * Clamped into `[0, filings_sent]` because the banner subtracts the two and a
 * count larger than the total would render "13 of 12 simulated". main.py
 * derives both numbers from the same enumeration so this cannot happen today;
 * it costs one line to keep it from ever being able to.
 */
export function simulatedFilingCount(
  stats: Pick<DashboardStats, "filings_sent" | "filings_simulated"> | null | undefined
): number {
  if (!stats) return 0;
  const sent = Number.isFinite(stats.filings_sent) ? stats.filings_sent : 0;
  const reported = stats.filings_simulated;
  if (typeof reported === "number" && Number.isFinite(reported)) {
    return Math.min(Math.max(reported, 0), sent);
  }
  return sent;
}

/**
 * `simulated` describes a fact, not a fault — so it gets a neutral tone, the
 * same weight as a unit label, never the red/amber the dashboard reserves for
 * deadlines and unlawful denials. `live` is the only state that earns colour.
 */
export type FilingMixTone = "simulated" | "live" | "none";

export interface FilingMix {
  sent: number;
  simulated: number;
  live: number;
  /** Empty string when there is nothing yet to describe. */
  note: string;
  tone: FilingMixTone;
}

/**
 * The banner's one-line summary of the send mix.
 *
 *   0 filings          → ""              (nothing to qualify yet)
 *   12 sent, 12 sim    → "all simulated" (today)
 *   12 sent, 9 sim     → "9 of 12 simulated"
 *   12 sent, 0 sim     → "all live"      (the day a vendor key exists)
 *
 * Note that no branch here has to be edited when that day comes; the wording
 * follows from the two integers, which is the whole point of the backend
 * reporting two integers instead of relabelling one.
 */
export function describeFilingMix(sent: number, simulated: number): FilingMix {
  const safeSent = Number.isFinite(sent) ? Math.max(Math.trunc(sent), 0) : 0;
  const safeSim = Number.isFinite(simulated)
    ? Math.min(Math.max(Math.trunc(simulated), 0), safeSent)
    : safeSent;
  const live = safeSent - safeSim;

  if (safeSent === 0) {
    return { sent: 0, simulated: 0, live: 0, note: "", tone: "none" };
  }
  if (safeSim === 0) {
    return { sent: safeSent, simulated: 0, live, note: "all live", tone: "live" };
  }
  if (safeSim === safeSent) {
    return {
      sent: safeSent,
      simulated: safeSim,
      live: 0,
      note: "all simulated",
      tone: "simulated",
    };
  }
  return {
    sent: safeSent,
    simulated: safeSim,
    live,
    note: `${safeSim} of ${safeSent} simulated`,
    tone: "simulated",
  };
}

/**
 * What "simulated" means, in one sentence, in the words the Filer writes into
 * `events/` (`agent_core.pipeline._SIMULATED_PREFIX`). Leads with what really
 * happened, because most of it did: the form is real, the allowlist check is
 * real, only the transmission is not.
 */
export const SIMULATED_EXPLAINER =
  "Simulated = a test-mode send: the form was really rendered and really " +
  "checked against the destination allowlist, but no live fax or mail vendor " +
  "credentials are configured yet.";

export const LIVE_EXPLAINER =
  "Live = transmitted by a real fax or certified-mail vendor.";

export function filingModeLabel(simulated: boolean): string {
  return simulated ? "Simulated" : "Live";
}

export function filingModeExplainer(simulated: boolean): string {
  return simulated ? SIMULATED_EXPLAINER : LIVE_EXPLAINER;
}
