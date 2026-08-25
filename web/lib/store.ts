/**
 * In-memory mutable demo store. This is what makes the mock API feel real:
 * clicking "Approve filing" actually flips front/case state and appends
 * events, and /demo/inject_bill actually adds a new case — all client-side,
 * with no backend required, matching the §3.3 contract's shapes exactly so
 * swapping to the real services/api later is just changing lib/api.ts.
 *
 * Deep-cloned from the seed on first import so repeated demo runs (or a dev
 * server hot-reload) start clean.
 */
import { buildCases, computeStats, HOSPITALS } from "./mock-data";
import type { CaseDetail, CaseEvent, Filing, FrontType } from "./types";

let cases: CaseDetail[] = clone(buildCases());

function clone<T>(v: T): T {
  return JSON.parse(JSON.stringify(v));
}

function nowIso(): string {
  return new Date().toISOString();
}

export function listCases(): CaseDetail[] {
  return cases;
}

export function getCase(caseId: string): CaseDetail | undefined {
  return cases.find((c) => c.case_id === caseId);
}

export function getStats() {
  return computeStats(cases);
}

export function getHospitals() {
  return HOSPITALS;
}

export function getHospital(ein: string) {
  return HOSPITALS.find((h) => h.ein === ein) ?? null;
}

export function getAllEvents(): CaseEvent[] {
  return cases
    .flatMap((c) => c.events)
    .sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());
}

const CHANNEL_FOR_FRONT: Record<FrontType, Filing["channel"]> = {
  charity_care: "fax",
  ppdr: "fax",
  debt_validation: "mail",
  audit: "mail",
};

let vendorSeq = 9000;

/** POST /cases/{id}/approve_filing {front} — the human-in-the-loop gate. */
export function approveFiling(
  caseId: string,
  front: FrontType
): { ok: true; case: CaseDetail } | { ok: false; error: string } {
  const c = cases.find((x) => x.case_id === caseId);
  if (!c) return { ok: false, error: "case not found" };
  const f = c.fronts.find((x) => x.front === front);
  if (!f) return { ok: false, error: "front not found on case" };
  if (!f.applicable) return { ok: false, error: "front is not applicable on this case" };
  if (f.status === "filed" || f.status === "won" || f.status === "lost") {
    return { ok: false, error: "front has already been filed" };
  }

  const channel = CHANNEL_FOR_FRONT[front];
  const vendorId = channel === "fax" ? `phx_${vendorSeq++}` : `lob_${vendorSeq++}`;
  const ts = nowIso();

  f.status = "filed";
  c.updated_at = ts;
  if (c.status !== "filing") c.status = "filing";

  c.events.push({
    event_id: `${caseId}_approve_${vendorSeq}`,
    case_id: caseId,
    ts,
    agent: "strategist",
    action: "Filing approved by operator",
    detail: `Human approved sending the ${front.replace("_", " ")} filing. Handing off to the Filer.`,
    citations: [],
  });
  c.events.push({
    event_id: `${caseId}_filed_${vendorSeq}`,
    case_id: caseId,
    ts,
    agent: "filer",
    action: channel === "fax" ? "Filed via fax" : "Filed via certified mail",
    detail: `Sent in test mode via ${channel === "fax" ? "Phaxio" : "Lob"}. Vendor confirmation ${vendorId}.`,
    citations: [f.citation],
  });

  c.filings.push({
    filing_id: `${caseId}_${front}_${vendorSeq}`,
    case_id: caseId,
    front,
    channel,
    vendor_id: vendorId,
    status: "sent",
    proof:
      channel === "fax" ? { phaxio_id: vendorId } : { lob_id: vendorId, tracking: null },
    sent_at: ts,
  });

  return { ok: true, case: c };
}

const FIXTURES: Record<string, () => CaseDetail> = {
  maria_uninsured_ca: () => {
    const ts = nowIso();
    const caseId = `case_maria_uninsured_ca_${Date.now()}`;
    return {
      case_id: caseId,
      patient: {
        name: "Maria Alvarado (SYNTHETIC)",
        household_size: 2,
        annual_income_cents: 24_000_00,
        insured: false,
        state: "CA",
      },
      bill: {
        hospital_ein: "94-1156600",
        hospital_ccn: "050054",
        provider_name: "Sutter Bay Medical Center",
        amount_cents: 7_400_00,
        service_date: ts.slice(0, 10),
        first_statement_date: ts.slice(0, 10),
        gfe_amount_cents: 4_200_00,
        in_collections: false,
        collector_name: null,
        validation_notice_date: null,
      },
      status: "intake",
      fronts: [
        {
          front: "charity_care",
          applicable: true,
          reason: "Nonprofit CA hospital; income screen pending confirmation.",
          deadline: null,
          citation: "Cal. Health & Safety Code §127405(e)(3)",
          status: "open",
        },
        {
          front: "ppdr",
          applicable: true,
          reason: "Uninsured; bill exceeds GFE by $3,200 (≥ $400 floor).",
          deadline: null,
          citation: "45 CFR 149.620(c)",
          status: "open",
        },
        {
          front: "debt_validation",
          applicable: false,
          reason: "Not in collections.",
          deadline: null,
          citation: "12 CFR 1006.34(b)",
          status: "na",
        },
        {
          front: "audit",
          applicable: false,
          reason: "Itemized bill not yet received.",
          deadline: null,
          citation: "42 USC 1395b-7(b)",
          status: "na",
        },
      ],
      savings_found_cents: 0,
      audit_findings_cents: 0,
      hospital_name: "Sutter Bay Medical Center",
      hospital_nonprofit: true,
      denial_flag: null,
      created_at: ts,
      updated_at: ts,
      documents: [
        {
          doc_id: `${caseId}_bill`,
          type: "bill",
          gcs_uri: `gs://ef-documents/${caseId}/bill.pdf`,
          uploaded_at: ts,
          extracted: {},
          verified: null,
          verification_notes: "",
        },
      ],
      events: [
        {
          event_id: `${caseId}_inject`,
          case_id: caseId,
          ts,
          agent: "reader",
          action: "Case injected via /demo/inject_bill",
          detail: "Fixture 'maria_uninsured_ca' dropped into the pipeline as if emailed to the intake inbox. Gemma classified the attachment as a hospital bill.",
          citations: [],
        },
        {
          event_id: `${caseId}_lookup`,
          case_id: caseId,
          ts,
          agent: "lookup",
          action: "Resolved hospital",
          detail: "EIN 94-1156600 (Sutter Bay Medical Center) — nonprofit, CA.",
          citations: [],
        },
      ],
      filings: [],
    };
  },
  unparseable_bill: () => {
    const ts = nowIso();
    const caseId = `case_unparseable_${Date.now()}`;
    return {
      case_id: caseId,
      patient: {
        name: "Unknown Patient (SYNTHETIC)",
        household_size: 1,
        annual_income_cents: 0,
        insured: false,
        state: "CA",
      },
      bill: {
        hospital_ein: "",
        hospital_ccn: "",
        provider_name: "(unresolved)",
        amount_cents: 0,
        service_date: ts.slice(0, 10),
        first_statement_date: ts.slice(0, 10),
        gfe_amount_cents: null,
        in_collections: false,
        collector_name: null,
        validation_notice_date: null,
      },
      status: "intake",
      fronts: [],
      savings_found_cents: 0,
      audit_findings_cents: 0,
      hospital_name: "(unresolved)",
      hospital_nonprofit: false,
      denial_flag: null,
      created_at: ts,
      updated_at: ts,
      documents: [
        {
          doc_id: `${caseId}_bill`,
          type: "bill",
          gcs_uri: `gs://ef-documents/${caseId}/bill.pdf`,
          uploaded_at: ts,
          extracted: {},
          verified: false,
          verification_notes:
            "Scan quality too low to extract structured fields. Flagged for manual review rather than guessing at amounts.",
        },
      ],
      events: [
        {
          event_id: `${caseId}_inject`,
          case_id: caseId,
          ts,
          agent: "reader",
          action: "Case injected via /demo/inject_bill",
          detail: "Fixture 'unparseable_bill' dropped into the pipeline. Extraction failed gracefully: scan quality too low for structured fields — routed to manual review instead of guessing.",
          citations: [],
        },
      ],
      filings: [],
    };
  },
};

/** POST /demo/inject_bill {fixture_name} */
export function injectBill(
  fixtureName: string
): { ok: true; case: CaseDetail } | { ok: false; error: string } {
  const build = FIXTURES[fixtureName];
  if (!build) {
    return {
      ok: false,
      error: `unknown fixture '${fixtureName}' (have: ${Object.keys(FIXTURES).join(", ")})`,
    };
  }
  const newCase = build();
  cases = [newCase, ...cases];
  return { ok: true, case: newCase };
}

export function availableFixtures(): string[] {
  return Object.keys(FIXTURES);
}

export interface IntakeInput {
  patientName: string;
  householdSize: number;
  annualIncomeCents: number;
  insured: boolean;
  state: string;
  hospitalEin: string;
  amountCents: number;
  gfeAmountCents: number | null;
  inCollections: boolean;
  incomeDocUploaded: boolean;
  incomeDocLooksValid: boolean;
}

/**
 * Beyond the literal §3.3 contract — there's no `POST /cases` for manual
 * (non-Gmail) intake. This is a HANDOFF proposal for FORGE/SWARM: an
 * advocate keying in a case by hand is a real path, not just the Gmail
 * watch. The mini front-selection below is illustrative only — the real
 * determination is packages/rules' job once services/api is live.
 */
export function createCaseFromIntake(
  input: IntakeInput
): { ok: true; case: CaseDetail } | { ok: false; error: string } {
  const hospital = HOSPITALS.find((h) => h.ein === input.hospitalEin);
  if (!hospital) return { ok: false, error: "unknown hospital" };

  const ts = nowIso();
  const caseId = `case_intake_${Date.now()}`;
  const gfeDeltaCents = input.gfeAmountCents != null ? input.amountCents - input.gfeAmountCents : 0;

  const events: CaseEvent[] = [
    {
      event_id: `${caseId}_intake`,
      case_id: caseId,
      ts,
      agent: "reader",
      action: "Case opened via manual intake",
      detail: `Advocate-entered case for ${input.state}. Bill amount ${(
        input.amountCents / 100
      ).toLocaleString("en-US", { style: "currency", currency: "USD" })} at ${hospital.name}.`,
      citations: [],
    },
    {
      event_id: `${caseId}_lookup`,
      case_id: caseId,
      ts,
      agent: "lookup",
      action: "Resolved hospital",
      detail: `EIN ${hospital.ein} (${hospital.name}) — ${hospital.nonprofit ? "nonprofit" : "for-profit"}, ${hospital.state}.`,
      citations: [],
    },
  ];

  let charityApplicable = false;
  let charityReason = "Not screened yet.";
  if (!hospital.nonprofit) {
    charityReason = `${hospital.name} is a for-profit facility — no 501(r) financial-assistance obligation exists.`;
  } else if (input.insured) {
    charityReason = "Patient is insured; charity-care screening is reserved for uninsured/self-pay patients.";
  } else if (!input.incomeDocUploaded) {
    charityReason = "Awaiting an income document before a charity-care determination can be screened.";
  } else if (!input.incomeDocLooksValid) {
    charityReason =
      "Verifier flagged the uploaded income document as unverifiable — charity-care screening is paused pending a valid replacement upload.";
    events.push({
      event_id: `${caseId}_verifier`,
      case_id: caseId,
      ts,
      agent: "verifier",
      action: "Blocked charity-care screening",
      detail:
        "The uploaded document does not match the stated income — it does not appear to be a pay stub, W-2, or benefits award letter.",
      citations: [],
    });
  } else {
    charityApplicable = true;
    charityReason = `Nonprofit facility, uninsured, income document verified. Screening against ${hospital.name}'s published thresholds is queued.`;
  }

  const ppdrApplicable = !input.insured && input.gfeAmountCents != null && gfeDeltaCents >= 400_00;

  const c: CaseDetail = {
    case_id: caseId,
    patient: {
      name: `${input.patientName} (SYNTHETIC)`,
      household_size: input.householdSize,
      annual_income_cents: input.annualIncomeCents,
      insured: input.insured,
      state: input.state,
    },
    bill: {
      hospital_ein: hospital.ein,
      hospital_ccn: hospital.ccn,
      provider_name: hospital.name,
      amount_cents: input.amountCents,
      service_date: ts.slice(0, 10),
      first_statement_date: ts.slice(0, 10),
      gfe_amount_cents: input.gfeAmountCents,
      in_collections: input.inCollections,
      collector_name: input.inCollections ? "Unnamed collector (SYNTHETIC)" : null,
      validation_notice_date: input.inCollections ? ts.slice(0, 10) : null,
    },
    status: "intake",
    fronts: [
      {
        front: "charity_care",
        applicable: charityApplicable,
        reason: charityReason,
        deadline: null,
        citation: hospital.nonprofit ? "26 CFR 1.501(r)-4(b)(1)(iv)" : "26 CFR 1.501(r)-1(b)(20)",
        status: charityApplicable ? "open" : "na",
      },
      {
        front: "ppdr",
        applicable: ppdrApplicable,
        reason: ppdrApplicable
          ? `Uninsured; bill exceeds the Good Faith Estimate by $${(gfeDeltaCents / 100).toLocaleString()} (≥ $400 floor).`
          : "Not applicable — either insured or no qualifying Good Faith Estimate delta.",
        deadline: null,
        citation: "45 CFR 149.620(c)",
        status: ppdrApplicable ? "open" : "na",
      },
      {
        front: "debt_validation",
        applicable: input.inCollections,
        reason: input.inCollections
          ? "Account reported as in collections — validation is pursued first to freeze collection activity."
          : "Account has not been placed with a collector.",
        deadline: null,
        citation: "12 CFR 1006.34(b)",
        status: input.inCollections ? "open" : "na",
      },
      {
        front: "audit",
        applicable: false,
        reason: "Itemized bill not yet received — audit will run once it's uploaded.",
        deadline: null,
        citation: "42 USC 1395b-7(b)",
        status: "na",
      },
    ],
    savings_found_cents: 0,
    audit_findings_cents: 0,
    hospital_name: hospital.name,
    hospital_nonprofit: hospital.nonprofit,
    denial_flag: null,
    created_at: ts,
    updated_at: ts,
    documents: input.incomeDocUploaded
      ? [
          {
            doc_id: `${caseId}_income`,
            type: "income_proof",
            gcs_uri: `gs://ef-documents/${caseId}/income_proof.pdf`,
            uploaded_at: ts,
            extracted: {},
            verified: input.incomeDocLooksValid,
            verification_notes: input.incomeDocLooksValid
              ? ""
              : "Uploaded file does not appear to be an income document. Please upload a pay stub, W-2, or benefits award letter.",
          },
        ]
      : [],
    events,
    filings: [],
  };

  cases = [c, ...cases];
  return { ok: true, case: c };
}
