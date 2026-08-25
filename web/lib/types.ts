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
}

export interface Bill {
  hospital_ein: string;
  hospital_ccn: string;
  provider_name: string;
  amount_cents: number;
  service_date: string;
  first_statement_date: string;
  gfe_amount_cents: number | null;
  in_collections: boolean;
  collector_name: string | null;
  validation_notice_date: string | null;
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
  gcs_uri: string;
  uploaded_at: string;
  extracted: Record<string, unknown>;
  verified: boolean | null;
  verification_notes: string;
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
  proof: { phaxio_id?: string; lob_id?: string; tracking?: string | null };
  sent_at: string;
}

export interface Hospital {
  ein: string;
  name: string;
  ccn: string;
  state: string;
  fap_url: string | null;
  fap_app_url: string | null;
  free_care_max_fpl_pct: number | null;
  discounted_care_max_fpl_pct: number | null;
  nonprofit: boolean;
  source: string;
  tax_year: number;
  mrf_url: string | null;
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
  human_hours: number;
}
