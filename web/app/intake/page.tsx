import { IntakeForm } from "@/components/IntakeForm";

export default function IntakePage() {
  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink-100">Intake</h1>
        <p className="mt-1 text-sm text-ink-400">
          Start a new case by hand, or inject a fixture the way the demo does.
        </p>
      </div>
      <IntakeForm />
    </div>
  );
}
