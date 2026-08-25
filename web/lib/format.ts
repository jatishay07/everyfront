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
  const d = new Date(iso);
  return d.toLocaleDateString("en-US", {
    month: "short",
    day: "numeric",
    year: "numeric",
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
