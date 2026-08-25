"use client";

import { useRouter } from "next/navigation";
import { useState } from "react";
import { createCase, injectBill } from "@/lib/api";
import { HOSPITALS } from "@/lib/mock-data";

const STATES = ["CA", "IL", "FL", "NY", "WA", "NJ", "TX"];

export function IntakeForm() {
  const router = useRouter();

  const [patientName, setPatientName] = useState("");
  const [householdSize, setHouseholdSize] = useState(1);
  const [annualIncome, setAnnualIncome] = useState(24000);
  const [insured, setInsured] = useState(false);
  const [state, setState] = useState("CA");
  const [hospitalEin, setHospitalEin] = useState(HOSPITALS[0].ein);
  const [amount, setAmount] = useState(6500);
  const [hasGfe, setHasGfe] = useState(false);
  const [gfeAmount, setGfeAmount] = useState(3000);
  const [inCollections, setInCollections] = useState(false);

  const [fileName, setFileName] = useState<string | null>(null);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [simulateBadUpload, setSimulateBadUpload] = useState(false);
  const [verified, setVerified] = useState<boolean | null>(null);

  const [submitting, setSubmitting] = useState(false);
  const [injecting, setInjecting] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  function handleFileChange(file: File | null) {
    if (!file) {
      setFileName(null);
      setUploadProgress(null);
      setVerified(null);
      return;
    }
    setFileName(file.name);
    setVerified(null);
    setUploadProgress(0);
    const id = setInterval(() => {
      setUploadProgress((p) => {
        if (p === null) return null;
        if (p >= 100) {
          clearInterval(id);
          // Verifier pass: the "simulate bad upload" toggle stands in for
          // Gemini classifying the image as e.g. a photo of a cat — this is
          // a demo control, not a real classifier running in the browser.
          setVerified(!simulateBadUpload);
          return 100;
        }
        return p + 20;
      });
    }, 90);
  }

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    const result = await createCase({
      patientName: patientName || "Unnamed Patient",
      householdSize,
      annualIncomeCents: Math.round(annualIncome * 100),
      insured,
      state,
      hospitalEin,
      amountCents: Math.round(amount * 100),
      gfeAmountCents: hasGfe ? Math.round(gfeAmount * 100) : null,
      inCollections,
      incomeDocUploaded: fileName !== null,
      incomeDocLooksValid: verified ?? true,
    });
    setSubmitting(false);
    if (result.ok && result.case_id) {
      router.push(`/cases/${result.case_id}`);
    } else {
      setError(result.error ?? "failed to create case");
    }
  }

  async function handleInject(fixture: string) {
    setInjecting(fixture);
    setError(null);
    const result = await injectBill(fixture);
    setInjecting(null);
    if (result.ok && result.case_id) {
      router.push(`/cases/${result.case_id}`);
    } else {
      setError(result.error ?? "injection failed");
    }
  }

  return (
    <div className="space-y-8">
      <section className="rounded-xl border border-ink-800 bg-ink-900/40 p-5">
        <h2 className="text-sm font-semibold text-ink-100">Quick demo injection</h2>
        <p className="mt-1 text-sm text-ink-400">
          Mirrors <code className="rounded bg-ink-800 px-1 py-0.5 text-xs">POST /demo/inject_bill</code> —
          drops a fixture into the pipeline as if it had just been emailed to the intake inbox.
        </p>
        <div className="mt-3 flex flex-wrap gap-2">
          <button
            type="button"
            onClick={() => handleInject("maria_uninsured_ca")}
            disabled={injecting !== null}
            className="rounded-lg border border-signal-blue/40 bg-signal-blue/10 px-3 py-2 text-sm font-medium text-blue-200 hover:bg-signal-blue/20 disabled:opacity-60"
          >
            {injecting === "maria_uninsured_ca" ? "Injecting…" : "Inject: Maria (uninsured, CA)"}
          </button>
          <button
            type="button"
            onClick={() => handleInject("unparseable_bill")}
            disabled={injecting !== null}
            className="rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm font-medium text-ink-300 hover:bg-ink-800 disabled:opacity-60"
          >
            {injecting === "unparseable_bill" ? "Injecting…" : "Inject: unparseable bill (degradation demo)"}
          </button>
        </div>
      </section>

      <form onSubmit={handleSubmit} className="space-y-6">
        <section className="rounded-xl border border-ink-800 bg-ink-900/40 p-5">
          <h2 className="text-sm font-semibold text-ink-100">New case</h2>
          <div className="mt-4 grid gap-4 sm:grid-cols-2">
            <Field label="Patient name (synthetic)">
              <input
                value={patientName}
                onChange={(e) => setPatientName(e.target.value)}
                placeholder="e.g. Jordan Rivera"
                className="input"
              />
            </Field>
            <Field label="State">
              <select value={state} onChange={(e) => setState(e.target.value)} className="input">
                {STATES.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Household size">
              <input
                type="number"
                min={1}
                value={householdSize}
                onChange={(e) => setHouseholdSize(Number(e.target.value))}
                className="input"
              />
            </Field>
            <Field label="Annual income ($)">
              <input
                type="number"
                min={0}
                value={annualIncome}
                onChange={(e) => setAnnualIncome(Number(e.target.value))}
                className="input"
              />
            </Field>
            <Field label="Hospital">
              <select value={hospitalEin} onChange={(e) => setHospitalEin(e.target.value)} className="input">
                {HOSPITALS.map((h) => (
                  <option key={h.ein} value={h.ein}>
                    {h.name} ({h.state})
                  </option>
                ))}
              </select>
            </Field>
            <Field label="Bill amount ($)">
              <input
                type="number"
                min={0}
                value={amount}
                onChange={(e) => setAmount(Number(e.target.value))}
                className="input"
              />
            </Field>
            <label className="flex items-center gap-2 text-sm text-ink-300">
              <input type="checkbox" checked={insured} onChange={(e) => setInsured(e.target.checked)} />
              Patient has insurance
            </label>
            <label className="flex items-center gap-2 text-sm text-ink-300">
              <input
                type="checkbox"
                checked={inCollections}
                onChange={(e) => setInCollections(e.target.checked)}
              />
              Account is in collections
            </label>
            <label className="flex items-center gap-2 text-sm text-ink-300">
              <input type="checkbox" checked={hasGfe} onChange={(e) => setHasGfe(e.target.checked)} />
              Has a Good Faith Estimate
            </label>
            {hasGfe && (
              <Field label="Good Faith Estimate amount ($)">
                <input
                  type="number"
                  min={0}
                  value={gfeAmount}
                  onChange={(e) => setGfeAmount(Number(e.target.value))}
                  className="input"
                />
              </Field>
            )}
          </div>
        </section>

        <section className="rounded-xl border border-ink-800 bg-ink-900/40 p-5">
          <h2 className="text-sm font-semibold text-ink-100">Income document</h2>
          <p className="mt-1 text-sm text-ink-400">
            Uploads go to GCS via a signed URL once services/api is live; here they&rsquo;re
            simulated client-side so the Verifier&rsquo;s inline feedback can be demoed without a
            backend.
          </p>
          <div className="mt-3 flex flex-wrap items-center gap-3">
            <label className="inline-flex cursor-pointer items-center gap-2 rounded-lg border border-ink-700 bg-ink-900 px-3 py-2 text-sm font-medium text-ink-200 hover:bg-ink-800">
              <input
                type="file"
                className="hidden"
                onChange={(e) => handleFileChange(e.target.files?.[0] ?? null)}
              />
              Choose file
            </label>
            {fileName && <span className="text-sm text-ink-400">{fileName}</span>}
            <label className="ml-auto flex items-center gap-2 text-xs text-ink-500">
              <input
                type="checkbox"
                checked={simulateBadUpload}
                onChange={(e) => setSimulateBadUpload(e.target.checked)}
              />
              Demo: simulate a mismatched upload (the &ldquo;cat photo&rdquo; case)
            </label>
          </div>

          {uploadProgress !== null && uploadProgress < 100 && (
            <div className="mt-3 h-1.5 w-full overflow-hidden rounded-full bg-ink-800">
              <div
                className="h-full rounded-full bg-signal-blue transition-all"
                style={{ width: `${uploadProgress}%` }}
              />
            </div>
          )}

          {verified === true && (
            <p className="mt-3 rounded-md border border-signal-green/30 bg-signal-green/10 px-3 py-2 text-sm text-green-200">
              Verifier: document matches a recognized income-record format.
            </p>
          )}
          {verified === false && (
            <p className="mt-3 rounded-md border border-signal-red/30 bg-signal-red/10 px-3 py-2 text-sm text-red-200">
              Verifier: uploaded document does not match stated income — it does not appear to be a pay
              stub, W-2, or benefits award letter. Charity-care screening will be paused until a valid
              document is provided.
            </p>
          )}
        </section>

        {error && <p className="text-sm text-red-400">{error}</p>}

        <button
          type="submit"
          disabled={submitting}
          className="rounded-lg bg-signal-blue px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-500 disabled:opacity-60"
        >
          {submitting ? "Creating case…" : "Create case"}
        </button>
      </form>
    </div>
  );
}

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label className="block text-sm">
      <span className="mb-1 block text-xs font-medium uppercase tracking-wide text-ink-500">
        {label}
      </span>
      {children}
    </label>
  );
}
