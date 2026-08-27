/**
 * Types mirror BUILD_PLAYBOOK.md §3.1 (Firestore collections) and §3.3 (REST
 * API) exactly. This file is the contract boundary: if services/api starts
 * returning something shaped differently, this is the one place the mismatch
 * will show up as a type error.
 *
 * Two additions beyond the literal §3.1 schema, both flagged for FORGE in the
 * PR as HANDOFF items rather than silently diverging (§0.3):
 *   1. `events[].agent` includes "verifier" — §4 persona 5 WO1 names a
 *      Verifier agent (income-doc / cat-photo checks) that the literal §3.1
 *      enum (reader|lookup|clock|auditor|strategist|filer) omits.
 *   2. `Case.denial_flag` surfaces `check_denial_lawfulness` (§3.5) results.
 *      §3.1 has no field for this; without one the "1 unlawful denial
 *      flagged" stat (§3.4) has nowhere to read from.
 */

export type FrontType = "charity_care" | "ppdr" | "debt_validation" | "audit";

export type FrontStatus = "open" | "filed" | "won" | "lost" | "na";

export interface Front {
  front: FrontType;
  applicable: boolean;
  reason: string;
  /** ISO date string, or null when no deadline applies (e.g. CA charity care). */
  deadline: string | null;
  citation: string;
  status: FrontStatus;
}

export type CaseStatus =
  | "intake"
  | "analyzing"
  | "strategy_ready"
  | "filing"
  | "awaiting_response"
  | "denied"
  | "won"
  | "closed";

export interface Patient {
  name: string;
  household_size: number;
  annual_income_cents: number;
  insured: boolean;
  state: string;
  /** Legacy pre-amendment field name, still echoed by the live API alongside
   *  `annual_income_cents` (verified via curl 2026-08-25). Never read this —
   *  it's the exact ambiguous-units field the §3.1 amendment replaced. */
  annual_income?: number;
}

export interface Bill {
  hospital_ein: string;
  /** Live API omits this key on some cases and returns `""` on others
   *  (verified via curl) — never relied on by the UI, so kept optional
   *  rather than required. */
  hospital_ccn?: string | null;
  provider_name: string;
  amount_cents: number;
  service_date: string;
  first_statement_date: string;
  gfe_amount_cents: number | null;
  in_collections: boolean;
  collector_name: string | null;
  validation_notice_date: string | null;
  /** Undocumented in §3.1 but present on every live response (verified via
   *  curl) — the Reader agent's "was there an itemized bill" signal that
   *  feeds the audit front's applicability. */
  has_itemized_bill?: boolean;
}

export interface DenialFlag {
  violated: boolean;
  reason: string;
  citation: string;
}

export interface CaseSummary {
  case_id: string;
  patient: Patient;
  bill: Bill;
  status: CaseStatus;
  fronts: Front[];
  savings_found_cents: number;
  audit_findings_cents: number;
  hospital_name: string;
  hospital_nonprofit: boolean;
  denial_flag: DenialFlag | null;
  created_at: string;
  updated_at: string;
}

export type DocumentType =
  | "bill"
  | "itemized_bill"
  | "denial_letter"
  | "collection_notice"
  | "gfe"
  | "income_proof"
  | "generated_application"
  | "generated_letter";

export interface CaseDocument {
  doc_id: string;
  type: DocumentType;
  /** `null` on every live document so far (verified via curl) — documents
   *  created by the demo-injection pipeline carry their text inline via
   *  `raw_text` instead of a GCS object. */
  gcs_uri: string | null;
  uploaded_at: string;
  extracted: Record<string, unknown>;
  verified: boolean | null;
  verification_notes: string;
  /** Not in §3.1, but present on every live document (verified via curl) —
   *  the Reader agent's source text, prefixed "SYNTHETIC -- DEMO ONLY." on
   *  every fixture. Worth showing: it's the PROOF corpus's watermark. */
  raw_text?: string;
}

export type AgentName =
  | "reader"
  | "lookup"
  | "clock"
  | "auditor"
  | "strategist"
  | "verifier"
  | "filer";

export interface CaseEvent {
  event_id: string;
  case_id: string;
  ts: string;
  agent: AgentName;
  action: string;
  detail: string;
  citations: string[];
}

export type FilingChannel = "fax" | "mail" | "email";

export interface Filing {
  filing_id: string;
  case_id: string;
  front: FrontType;
  channel: FilingChannel;
  vendor_id: string;
  status: "sent" | "delivered" | "failed";
  proof: {
    phaxio_id?: string;
    lob_id?: string;
    tracking?: string | null;
    /** RELAY's own per-send report, mirrored into `proof` by the Filer. The
     *  top-level `simulated` below is the field to read; this one is the
     *  vendor result it was derived from. */
    simulated?: boolean | null;
    vendor?: string;
  };
  sent_at: string;

  /**
   * Did this filing actually leave the building?
   *
   * `services/api/main.py::normalize_filing` guarantees a bool on the way out
   * of `GET /cases/{id}`, and `agent_core.agents.filer` always writes one
   * now — so the contract says "required boolean". It is typed OPTIONAL here
   * anyway, deliberately:
   *
   *   - The currently deployed API revision predates `normalize_filing`
   *     (verified 2026-08-26: `curl {API_BASE}/cases/ef-2026-0001` returns
   *     filings with `proof.simulated: true` and NO top-level key at all).
   *     Typing it `boolean` would tell TypeScript a field is always there
   *     that today's production API does not send, and `undefined` would
   *     then render as nothing — which next to `status: "sent"` is
   *     indistinguishable from a real send. That is the exact defect this
   *     field exists to close.
   *   - Firestore also still holds pre-flag records written as
   *     `{"simulated": null}`.
   *
   * Optional forces every read through `lib/simulated.ts::isSimulated`,
   * which resolves absent/null to SIMULATED — the same direction, for the
   * same reason, as the backend's `normalize_filing` and
   * `delivery_bridge.simulated_flag`. A record that does not say it was real
   * is not evidence that it was.
   */
  simulated?: boolean | null;

  /** Present on every live filing (verified via curl), absent from the §3.1
   *  shape. `vendor: "fake"` is RELAY's recording stub. Never sniffed to
   *  decide `simulated` — see delivery_bridge.simulated_flag's own note on
   *  why a vendor-id prefix is not a fact about transmission. */
  vendor?: string;
  form_id?: string;
  doc_id?: string;
  gcs_uri?: string | null;
  pdf_bytes?: number;
  /** Where this WOULD have gone: fax number or provider name, allowlist-
   *  checked before the send. Worth showing next to a simulated filing —
   *  it's what makes "test mode" a description rather than an excuse. */
  real_destination?: string | null;
}

export interface Hospital {
  ein: string;
  name: string;
  /** `null` on every live hospital seen so far (verified via curl) — LEDGER's
   *  Schedule H seed doesn't populate CCN. */
  ccn: string | null;
  state: string;
  fap_url: string | null;
  fap_app_url: string | null;
  free_care_max_fpl_pct: number | null;
  discounted_care_max_fpl_pct: number | null;
  nonprofit: boolean;
  source: string;
  tax_year: number;
  mrf_url: string | null;
  /** Not in §3.1, but present on some live hospitals (verified via curl) —
   *  the FAP-required-document list Auditor needs for the denial-lawfulness
   *  check (services/agent-core/agent_core/agents/auditor.py). */
  fap_required_documents?: string[];
}

export interface CaseDetail extends CaseSummary {
  documents: CaseDocument[];
  events: CaseEvent[];
  filings: Filing[];
}

/** §3.4 — the demo stat object. Every field is a live aggregate, never hand-typed. */
export interface DashboardStats {
  open_cases: number;
  hospitals: number;
  deadlines_this_week: number;
  total_billed_cents: number;
  charity_eligible: number;
  ppdr_eligible: number;
  unlawful_denials_flagged: number;
  audit_findings_cents: number;
  filings_sent: number;
  /**
   * How many of `filings_sent` were test-mode sends — a §3.4 amendment
   * (services/api/main.py, SWARM WO8). By construction a SUBSET of
   * `filings_sent`, never a replacement for it: those filings are real work
   * (the real CMS PPDR form, two hospitals' own FAP applications, rendered,
   * allowlist-checked, proof recorded), they just didn't reach a vendor.
   *
   * Optional for the same reason as `Filing.simulated`: the deployed API
   * revision does not send this key yet (verified 2026-08-26 —
   * `curl {API_BASE}/dashboard/stats` returns the ten §3.4 keys and no
   * eleventh). Read it through `lib/simulated.ts::simulatedFilingCount`,
   * which resolves an absent count to `filings_sent` rather than to 0. Zero
   * simulated is a claim that every filing was really transmitted, and an
   * API that never mentioned the subject has not made that claim.
   */
  filings_simulated?: number;
  human_hours: number;
}
