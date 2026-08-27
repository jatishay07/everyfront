export function formatUSD(cents: number): string {
  return (cents / 100).toLocaleString("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

export function formatCompactUSD(cents: number): string {
  const dollars = cents / 100;
  if (dollars >= 1000) {
    return (
      "$" +
      (dollars / 1000).toLocaleString("en-US", { maximumFractionDigits: 1 }) +
      "k"
    );
  }
  return formatUSD(cents);
}

export function formatDate(iso: string | null): string {
  if (!iso) return "—";
  // A date-only "YYYY-MM-DD" is a CALENDAR date, not an instant. `new Date()`
  // parses it as UTC midnight, and `toLocaleDateString` then renders it in the
  // viewer's zone -- so a statutory deadline of 2026-10-03 displayed as
  // "Oct 2, 2026" to anyone west of UTC. Observed live on case-1a0412ccfef90917,
  // where the ladder read "Oct 2, 2026" and the chip beside it read "37d left"
  // -- 37 days being correct for Oct 3. The two disagreed on screen because
  // `daysUntil` below already handles this and this function did not, eleven
  // lines under a comment explaining the exact trap.
  //
  // A deadline rendered a day early is the safer direction to be wrong in, and
  // still wrong: this product's entire claim is that it computes statutory
  // dates correctly and shows its work.
  const isDateOnly = /^\d{4}-\d{2}-\d{2}$/.test(iso);
  const d = isDateOnly ? new Date(dateOnlyToUTCms(iso)) : new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
    // Pin the zone for calendar dates so every viewer sees the same day.
    // Timestamps (events, filings) keep local rendering -- an instant SHOULD
    // localise.
    ...(isDateOnly ? { timeZone: "UTC" } : {}),
  });
}

export function formatDateTime(iso: string): string {
  const d = new Date(iso);
  return d.toLocaleString("en-US", {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });
}

/**
 * Whole calendar days from now until `iso` (negative once past).
 *
 * `iso` is a date-only string ("YYYY-MM-DD"). We deliberately do NOT do
 * `new Date(iso).getFullYear()` etc. — `new Date("2026-08-28")` parses as
 * UTC midnight, and reading it back with local getters shifts the *date* by
 * a day in any timezone behind UTC. Parsing the components by hand keeps the
 * deadline math (the whole point of the ≤7-day red chip) timezone-safe.
 */
export function daysUntil(iso: string, now: Date = new Date()): number {
  const msPerDay = 1000 * 60 * 60 * 24;
  const target = dateOnlyToUTCms(iso);
  const a = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((target - a) / msPerDay);
}

export function dateOnlyToUTCms(iso: string): number {
  const [y, m, d] = iso.split("-").map(Number);
  return Date.UTC(y, m - 1, d);
}

export function relativeTime(iso: string, now: Date = new Date()): string {
  const ms = now.getTime() - new Date(iso).getTime();
  const sec = Math.round(ms / 1000);
  if (sec < 5) return "just now";
  if (sec < 60) return `${sec}s ago`;
  const min = Math.round(sec / 60);
  if (min < 60) return `${min}m ago`;
  const hr = Math.round(min / 60);
  if (hr < 24) return `${hr}h ago`;
  const day = Math.round(hr / 24);
  return `${day}d ago`;
}

export function titleCase(s: string): string {
  return s
    .split("_")
    .map((w) => w.charAt(0).toUpperCase() + w.slice(1))
    .join(" ");
}

/**
 * Event `action` strings from the live pipeline are snake_case, sometimes
 * with a `:sub_kind` suffix (e.g. `select_front:audit`,
 * `audit_finding:duplicate_line_item`) — verified via curl against the live
 * API's `events[]`. Freeze-frame readability (§4 persona 6 WO2/WO3) wants
 * "Select Front: Audit", not the raw wire value.
 */
export function humanizeAction(action: string): string {
  const [base, sub] = action.split(":");
  return sub ? `${titleCase(base)}: ${titleCase(sub)}` : titleCase(base);
}

export const FRONT_LABELS: Record<string, string> = {
  charity_care: "Charity Care",
  ppdr: "PPDR",
  debt_validation: "Debt Validation",
  audit: "Billing Audit",
};

export const AGENT_LABELS: Record<string, string> = {
  reader: "Reader",
  lookup: "Lookup",
  clock: "Clock",
  auditor: "Auditor",
  strategist: "Strategist",
  verifier: "Verifier",
  filer: "Filer",
};
