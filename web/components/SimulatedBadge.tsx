import { filingModeExplainer, filingModeLabel } from "@/lib/simulated";

/**
 * The per-filing send-mode pill: "Simulated" or "Live".
 *
 * TONE. Simulated is deliberately NOT styled as a warning. The dashboard
 * already spends red on deadlines and unlawful denials and amber on billing
 * errors; spending it here too would say "something went wrong", and nothing
 * did — a test-mode send is an accurate description of a correctly executed
 * filing that had no vendor key to hand off to. So it gets the neutral ink
 * palette: legible, unmissable, unalarming. `Live` is the only mode that
 * earns colour, and it earns it by being the rarer, stronger claim.
 */
export function SimulatedBadge({
  simulated,
  className = "",
}: {
  simulated: boolean;
  className?: string;
}) {
  const style = simulated
    ? "border-ink-600 bg-ink-800/80 text-ink-200"
    : "border-signal-green/40 bg-signal-green/10 text-green-200";
  return (
    <span
      title={filingModeExplainer(simulated)}
      className={`inline-flex shrink-0 items-center gap-1.5 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${style} ${className}`}
    >
      <span
        aria-hidden
        className={`h-1.5 w-1.5 rounded-full ${
          simulated ? "bg-ink-400" : "bg-signal-green"
        }`}
      />
      {filingModeLabel(simulated)}
    </span>
  );
}
