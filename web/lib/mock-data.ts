/**
 * Synthetic demo corpus, built against BUILD_PLAYBOOK.md §3.1 / §3.4.
 *
 * PROOF (persona 7) owns fixtures/ and will eventually seed real Firestore
 * data from fixtures/. Until services/api is live, this module IS the
 * backend CANVAS builds against — see lib/api.ts for the single swap point.
 *
 * All patient names are fabricated and watermarked (SYNTHETIC). Hospital
 * names are real (public 990/Schedule-H filers), per persona 6 instructions;
 * EINs/CCNs here are illustrative placeholders, not verified filings — the
 * real values come from LEDGER's Schedule H pipeline (packages/datapipes)
 * once that's wired to services/api.
 *
 * Every number in DashboardStats is an aggregate computed by `computeStats`
 * from this same case array — never hand-typed twice — so the banner can
 * never drift from the case data behind it (§7: "a judge doing arithmetic
 * must not catch a discrepancy").
 */
import { CITE } from "./citations";
import { dateOnlyToUTCms } from "./format";
import type {
  CaseDetail,
  CaseEvent,
  DashboardStats,
  Filing,
  Hospital,
} from "./types";

// ---------------------------------------------------------------------------
// date helpers — offsets are relative to the moment the module is evaluated,
// truncated to the day, so deadline chips are stable across a single demo
// session and always "count down" correctly whenever the demo actually runs.
//
// addDays formats the LOCAL calendar date by hand rather than going through
// `Date.toISOString()` — that method converts to UTC first, which silently
// shifts the date by one in any timezone behind UTC. Date-only fields
// (service_date, deadlines, …) must never take that round trip.
// ---------------------------------------------------------------------------
const TODAY = (() => {
  const d = new Date();
  d.setHours(0, 0, 0, 0);
  return d;
})();

function pad2(n: number): string {
  return n.toString().padStart(2, "0");
}

function addDays(n: number): string {
  const d = new Date(TODAY);
  d.setDate(d.getDate() + n);
  return `${d.getFullYear()}-${pad2(d.getMonth() + 1)}-${pad2(d.getDate())}`;
}

function addDaysTime(n: number, hh: number, mm: number): string {
  const d = new Date(TODAY);
  d.setDate(d.getDate() + n);
  d.setHours(hh, mm, 0, 0);
  return d.toISOString();
}

// ---------------------------------------------------------------------------
// hospitals/{ein} — six unique facilities, mixed nonprofit/for-profit so the
// "honest no-501(r)-obligation" path (HCA Aventura) is on screen too.
// ---------------------------------------------------------------------------
export const HOSPITALS: Hospital[] = [
  {
    ein: "36-2167612",
    name: "Advocate Christ Medical Center",
    ccn: "140010",
    state: "IL",
    fap_url: "https://www.advocatehealth.com/financial-assistance",
    fap_app_url: "https://www.advocatehealth.com/financial-assistance/apply",
    free_care_max_fpl_pct: 200,
    discounted_care_max_fpl_pct: 400,
    nonprofit: true,
    source: "schedule_h",
    tax_year: 2024,
    mrf_url: "https://www.advocatehealth.com/cms-hpt.json",
  },
  {
    ein: "94-1156600",
    name: "Sutter Bay Medical Center",
    ccn: "050054",
    state: "CA",
    fap_url: "https://www.sutterhealth.org/financial-assistance",
    fap_app_url: "https://www.sutterhealth.org/financial-assistance/apply",
    free_care_max_fpl_pct: 300,
    discounted_care_max_fpl_pct: 400,
    nonprofit: true,
    source: "schedule_h",
    tax_year: 2024,
    mrf_url: "https://www.sutterhealth.org/cms-hpt.json",
  },
  {
    ein: "94-1156365",
    name: "Stanford Health Care",
    ccn: "050269",
    state: "CA",
    fap_url: "https://www.stanfordhealthcare.org/financial-assistance",
    fap_app_url: "https://www.stanfordhealthcare.org/financial-assistance/apply",
    free_care_max_fpl_pct: 400,
    discounted_care_max_fpl_pct: 500,
    nonprofit: true,
    source: "schedule_h",
    tax_year: 2024,
    mrf_url: "https://www.stanfordhealthcare.org/cms-hpt.json",
  },
  {
    ein: "36-2167805",
    name: "Northwestern Memorial Hospital",
    ccn: "140281",
    state: "IL",
    fap_url: "https://www.nm.org/patients-and-visitors/billing/financial-assistance",
    fap_app_url: "https://www.nm.org/patients-and-visitors/billing/financial-assistance/apply",
    free_care_max_fpl_pct: 250,
    discounted_care_max_fpl_pct: 400,
    nonprofit: true,
    source: "schedule_h",
    tax_year: 2024,
    mrf_url: "https://www.nm.org/cms-hpt.json",
  },
  {
    ein: "65-0678910",
    name: "HCA Florida Aventura Hospital",
    ccn: "100241",
    state: "FL",
    fap_url: null,
    fap_app_url: null,
    free_care_max_fpl_pct: null,
    discounted_care_max_fpl_pct: null,
    nonprofit: false,
    source: "schedule_h",
    tax_year: 2024,
    mrf_url: "https://aventurahospital.com/cms-hpt.json",
  },
  {
    ein: "94-1156329",
    name: "UCSF Medical Center",
    ccn: "050448",
    state: "CA",
    fap_url: "https://www.ucsfhealth.org/financial-assistance",
    fap_app_url: "https://www.ucsfhealth.org/financial-assistance/apply",
    free_care_max_fpl_pct: 350,
    discounted_care_max_fpl_pct: 500,
    nonprofit: true,
    source: "schedule_h",
    tax_year: 2024,
    mrf_url: "https://www.ucsfhealth.org/cms-hpt.json",
  },
];

const hospitalByEin = (ein: string): Hospital => {
  const h = HOSPITALS.find((x) => x.ein === ein);
  if (!h) throw new Error(`unknown hospital EIN ${ein}`);
  return h;
};

// ---------------------------------------------------------------------------
// The 8-case matrix. See §7 for the target banner this is built to hit
// exactly: 8 cases · 6 hospitals · 5 deadlines this week · $84,200 billed ·
// 4 charity-eligible · 2 PPDR-eligible · 1 unlawful denial flagged ·
// $9,100 in billing errors · 11 filings sent · 0 human hours.
// ---------------------------------------------------------------------------
export function buildCases(): CaseDetail[] {
  const cases: CaseDetail[] = [
    // 1 — Dana Whitfield: uninsured IL, unlawful denial flagged (the drama).
    {
      case_id: "case_dana_advocate_il",
      patient: {
        name: "Dana Whitfield (SYNTHETIC)",
        household_size: 2,
        annual_income_cents: 21_500_00,
        insured: false,
        state: "IL",
      },
      bill: {
        hospital_ein: "36-2167612",
        hospital_ccn: "140010",
        provider_name: "Advocate Christ Medical Center",
        amount_cents: 18_400_00,
        service_date: addDays(-140),
        first_statement_date: addDays(-100),
        gfe_amount_cents: null,
        in_collections: false,
        collector_name: null,
        validation_notice_date: null,
      },
      status: "denied",
      fronts: [
        {
          front: "charity_care",
          applicable: true,
          reason:
            "Uninsured Illinois patient under 300% FPL — the Hospital Uninsured Patient Discount Act right runs alongside Advocate's federal FAP obligation, from the latest of discharge, service, screening, and public-program denial dates.",
          deadline: addDays(3),
          citation: CITE.IL_UNINSURED_DISCOUNT,
          status: "open",
        },
        {
          front: "ppdr",
          applicable: false,
          reason: "No Good Faith Estimate on file — this was an emergency admission.",
          deadline: null,
          citation: CITE.PPDR_DELTA,
          status: "na",
        },
        {
          front: "debt_validation",
          applicable: false,
          reason: "Account has not been placed with a third-party collector.",
          deadline: null,
          citation: CITE.VALIDATION,
          status: "na",
        },
        {
          front: "audit",
          applicable: true,
          reason:
            "Itemized bill shows a column-2 NCCI edit billed alongside its column-1 code on the same date of service.",
          deadline: null,
          citation: CITE.ITEMIZED_BILL,
          status: "filed",
        },
      ],
      savings_found_cents: 19_600_00,
      audit_findings_cents: 1_200_00,
      hospital_name: "Advocate Christ Medical Center",
      hospital_nonprofit: true,
      denial_flag: {
        violated: true,
        reason:
          "Advocate Christ's denial letter demanded a state-issued photo ID and three months of bank statements. Neither document appears on the hospital's own published FAP required-documents list — a hospital may not deny for missing paperwork its FAP doesn't require.",
        citation: CITE.FAP_DENIAL_DOCS,
      },
      created_at: addDaysTime(-100, 9, 12),
      updated_at: addDaysTime(-1, 16, 40),
      documents: [
        doc("d1a", "bill", "case_dana_advocate_il", -100, true, ""),
        doc("d1b", "itemized_bill", "case_dana_advocate_il", -95, true, ""),
        doc(
          "d1c",
          "denial_letter",
          "case_dana_advocate_il",
          -14,
          true,
          "Extracted required-document list cross-checked against Advocate Christ's published FAP — 2 of 4 demanded items unlisted."
        ),
        doc("d1d", "income_proof", "case_dana_advocate_il", -98, true, ""),
      ],
      events: [
        ev("case_dana_advocate_il", -100, 9, 20, "reader", "Classified document",
          "Gemma first-pass classified the upload as a hospital billing statement; Gemini 3.7 Flash structured extraction populated amount, service date, and statement date.", []),
        ev("case_dana_advocate_il", -100, 9, 24, "lookup", "Resolved hospital",
          "Matched provider name to EIN 36-2167612 (Advocate Christ Medical Center) via the CCN crosswalk. Nonprofit — subject to 26 CFR 1.501(r).", []),
        ev("case_dana_advocate_il", -100, 9, 31, "clock", "Computed deadlines",
          "Illinois Hospital Uninsured Patient Discount Act: 90 days from the latest of discharge, service, screening, and public-program denial. Federal FAP window (240 days from first statement) also runs, but IL is shorter here and controls.",
          [CITE.IL_UNINSURED_DISCOUNT, CITE.IL_LATEST_OF]),
        ev("case_dana_advocate_il", -99, 11, 2, "strategist", "Selected fronts",
          "Charity care (IL discount act) and billing audit selected. PPDR and debt validation not applicable — no GFE on file, account not in collections.", []),
        ev("case_dana_advocate_il", -95, 14, 10, "auditor", "Flagged NCCI edit",
          "CPT 99284 billed alongside a bundled column-2 code on the same claim line. Estimated overcharge $1,200.", [CITE.NCCI_EDIT]),
        ev("case_dana_advocate_il", -80, 10, 5, "filer", "Filed charity care application",
          "FAP application faxed to Advocate Christ's financial assistance office. Vendor confirmation phx_8834021.", [CITE.IL_UNINSURED_DISCOUNT]),
        ev("case_dana_advocate_il", -14, 15, 50, "auditor", "Denial triage: unlawful denial flagged",
          "Denial letter demands a state ID and bank statements; neither is on Advocate Christ's own published FAP document list. Flagging as an improper denial and drafting a dispute citing the hospital's own policy against it.",
          [CITE.FAP_DENIAL_DOCS]),
        ev("case_dana_advocate_il", -1, 16, 40, "clock", "Deadline approaching",
          "3 days remain on the Illinois 90-day window. Escalating priority.", [CITE.IL_UNINSURED_DISCOUNT]),
      ],
      filings: [
        filing("f1a", "case_dana_advocate_il", "charity_care", "fax", "phx_8834021", "delivered", -30),
        filing("f1b", "case_dana_advocate_il", "audit", "mail", "lob_5599213", "delivered", -12),
      ],
    },

    // 2 — Marcus Chen: uninsured CA, PPDR + charity + audit all live.
    {
      case_id: "case_marcus_sutterbay_ca",
      patient: {
        name: "Marcus Chen (SYNTHETIC)",
        household_size: 1,
        annual_income_cents: 26_000_00,
        insured: false,
        state: "CA",
      },
      bill: {
        hospital_ein: "94-1156600",
        hospital_ccn: "050054",
        provider_name: "Sutter Bay Medical Center",
        amount_cents: 6_200_00,
        service_date: addDays(-95),
        first_statement_date: addDays(-60),
        gfe_amount_cents: 3_500_00,
        in_collections: false,
        collector_name: null,
        validation_notice_date: null,
      },
      status: "filing",
      fronts: [
        {
          front: "charity_care",
          applicable: true,
          reason:
            "Nonprofit CA hospital; income under Sutter Bay's discounted-care threshold. California imposes no application deadline for financial assistance.",
          deadline: null,
          citation: CITE.CA_NO_DEADLINE,
          status: "filed",
        },
        {
          front: "ppdr",
          applicable: true,
          reason:
            "Uninsured; bill exceeds the Good Faith Estimate by $2,700 (≥ $400 floor), within 120 days of the initial bill.",
          deadline: addDays(7),
          citation: CITE.PPDR_DEADLINE,
          status: "open",
        },
        {
          front: "debt_validation",
          applicable: false,
          reason: "Account has not been placed with a collector.",
          deadline: null,
          citation: CITE.VALIDATION,
          status: "na",
        },
        {
          front: "audit",
          applicable: true,
          reason: "Duplicate lab panel line found — same CPT code billed twice for one draw.",
          deadline: null,
          citation: CITE.ITEMIZED_BILL,
          status: "filed",
        },
      ],
      savings_found_cents: 6_900_00,
      audit_findings_cents: 900_00,
      hospital_name: "Sutter Bay Medical Center",
      hospital_nonprofit: true,
      denial_flag: null,
      created_at: addDaysTime(-60, 8, 2),
      updated_at: addDaysTime(-2, 11, 15),
      documents: [
        doc("d2a", "bill", "case_marcus_sutterbay_ca", -60, true, ""),
        doc("d2b", "gfe", "case_marcus_sutterbay_ca", -96, true, ""),
        doc("d2c", "itemized_bill", "case_marcus_sutterbay_ca", -55, true, ""),
        doc("d2d", "income_proof", "case_marcus_sutterbay_ca", -58, true, ""),
      ],
      events: [
        ev("case_marcus_sutterbay_ca", -60, 8, 5, "reader", "Classified document",
          "Bill and prior Good Faith Estimate both extracted; delta computed at $2,700.", []),
        ev("case_marcus_sutterbay_ca", -60, 8, 9, "lookup", "Resolved hospital",
          "EIN 94-1156600 (Sutter Bay Medical Center) — nonprofit, CA.", []),
        ev("case_marcus_sutterbay_ca", -60, 8, 20, "clock", "Computed deadlines",
          "PPDR: 120 days from initial bill. Charity care: no deadline (California).",
          [CITE.PPDR_DEADLINE, CITE.CA_NO_DEADLINE]),
        ev("case_marcus_sutterbay_ca", -59, 9, 40, "strategist", "Selected fronts",
          "Charity care, PPDR, and billing audit all selected — no conflict; sequencing charity first since it has no clock.", []),
        ev("case_marcus_sutterbay_ca", -18, 13, 0, "filer", "Filed charity care application",
          "FAP application faxed to Sutter Bay financial assistance. Vendor confirmation phx_8834099.", [CITE.CA_NO_DEADLINE]),
        ev("case_marcus_sutterbay_ca", -9, 9, 30, "filer", "Filed audit dispute",
          "Duplicate-charge dispute letter sent via certified mail. Vendor confirmation lob_5599310.", [CITE.DUPLICATE_BILLING]),
        ev("case_marcus_sutterbay_ca", -2, 11, 15, "strategist", "PPDR queued for approval",
          "PPDR initiation form rendered and awaiting human approval — 7 days remain on the 120-day window.", [CITE.PPDR_DEADLINE]),
      ],
      filings: [
        filing("f2a", "case_marcus_sutterbay_ca", "charity_care", "fax", "phx_8834099", "delivered", -18),
        filing("f2b", "case_marcus_sutterbay_ca", "audit", "mail", "lob_5599310", "delivered", -9),
      ],
    },

    // 3 — Priya Nair: insured, audit-only, honest "not charity/PPDR eligible".
    {
      case_id: "case_priya_stanford_ca",
      patient: {
        name: "Priya Nair (SYNTHETIC)",
        household_size: 3,
        annual_income_cents: 98_000_00,
        insured: true,
        state: "CA",
      },
      bill: {
        hospital_ein: "94-1156365",
        hospital_ccn: "050269",
        provider_name: "Stanford Health Care",
        amount_cents: 22_750_00,
        service_date: addDays(-70),
        first_statement_date: addDays(-40),
        gfe_amount_cents: null,
        in_collections: false,
        collector_name: null,
        validation_notice_date: null,
      },
      status: "awaiting_response",
      fronts: [
        {
          front: "charity_care",
          applicable: false,
          reason:
            "Household income exceeds Stanford's published discounted-care threshold (500% FPL) and the patient is insured.",
          deadline: null,
          citation: CITE.FAP_ELIGIBILITY,
          status: "na",
        },
        {
          front: "ppdr",
          applicable: false,
          reason: "Patient has employer insurance — PPDR is limited to uninsured/self-pay bills.",
          deadline: null,
          citation: CITE.PPDR_SCOPE,
          status: "na",
        },
        {
          front: "debt_validation",
          applicable: false,
          reason: "Account has not been placed with a collector.",
          deadline: null,
          citation: CITE.VALIDATION,
          status: "na",
        },
        {
          front: "audit",
          applicable: true,
          reason:
            "Itemized bill shows a column-2 NCCI edit billed alongside its column-1 code, plus a unit count above the MUE ceiling on one CPT line.",
          deadline: addDays(35),
          citation: CITE.ITEMIZED_BILL,
          status: "filed",
        },
      ],
      savings_found_cents: 2_300_00,
      audit_findings_cents: 2_300_00,
      hospital_name: "Stanford Health Care",
      hospital_nonprofit: true,
      denial_flag: null,
      created_at: addDaysTime(-40, 10, 0),
      updated_at: addDaysTime(-3, 9, 0),
      documents: [
        doc("d3a", "bill", "case_priya_stanford_ca", -40, true, ""),
        doc("d3b", "itemized_bill", "case_priya_stanford_ca", -36, true, ""),
      ],
      events: [
        ev("case_priya_stanford_ca", -40, 10, 3, "reader", "Classified document",
          "Itemized bill extracted; 14 CPT lines parsed.", []),
        ev("case_priya_stanford_ca", -40, 10, 8, "lookup", "Resolved hospital",
          "EIN 94-1156365 (Stanford Health Care) — nonprofit, CA. Insured patient, income above threshold: charity care not applicable.", [CITE.FAP_ELIGIBILITY]),
        ev("case_priya_stanford_ca", -39, 14, 30, "auditor", "NCCI + MUE check",
          "Found one column-1/column-2 pair billed together and one unit count over the CMS MUE ceiling.", [CITE.NCCI_EDIT]),
        ev("case_priya_stanford_ca", -38, 9, 0, "strategist", "Selected fronts",
          "Only the billing audit applies — logged the charity/PPDR non-eligibility reasons for the record rather than silently dropping them.", []),
        ev("case_priya_stanford_ca", -15, 12, 40, "filer", "Filed audit dispute",
          "Dispute letter with line-item citations sent via certified mail. Vendor confirmation lob_5599402.", [CITE.NCCI_EDIT]),
      ],
      filings: [filing("f3a", "case_priya_stanford_ca", "audit", "mail", "lob_5599402", "delivered", -15)],
    },

    // 4 — Jordan Rivera: in collections; validation-first ordering.
    {
      case_id: "case_jordan_northwestern_il",
      patient: {
        name: "Jordan Rivera (SYNTHETIC)",
        household_size: 4,
        annual_income_cents: 32_000_00,
        insured: false,
        state: "IL",
      },
      bill: {
        hospital_ein: "36-2167805",
        hospital_ccn: "140281",
        provider_name: "Northwestern Memorial Hospital",
        amount_cents: 11_300_00,
        service_date: addDays(-150),
        first_statement_date: addDays(-110),
        gfe_amount_cents: null,
        in_collections: true,
        collector_name: "Meridian Recovery Group (SYNTHETIC)",
        validation_notice_date: addDays(-25),
      },
      status: "filing",
      fronts: [
        {
          front: "debt_validation",
          applicable: true,
          reason:
            "Account placed with a third-party collector; written validation notice received. Debt validation is pursued first — it freezes collection activity while everything else proceeds.",
          deadline: addDays(5),
          citation: CITE.VALIDATION,
          status: "open",
        },
        {
          front: "charity_care",
          applicable: true,
          reason:
            "Nonprofit IL hospital; uninsured household under 300% FPL — 90-day Hospital Uninsured Patient Discount Act window, running from the latest of discharge, service, screening, and public-program denial.",
          deadline: addDays(45),
          citation: CITE.IL_UNINSURED_DISCOUNT,
          status: "filed",
        },
        {
          front: "ppdr",
          applicable: false,
          reason: "No Good Faith Estimate on file.",
          deadline: null,
          citation: CITE.PPDR_DELTA,
          status: "na",
        },
        {
          front: "audit",
          applicable: true,
          reason: "Duplicate imaging line — two identical CPT 71046 charges for one date of service.",
          deadline: null,
          citation: CITE.ITEMIZED_BILL,
          status: "filed",
        },
      ],
      savings_found_cents: 11_300_00,
      audit_findings_cents: 1_400_00,
      hospital_name: "Northwestern Memorial Hospital",
      hospital_nonprofit: true,
      denial_flag: null,
      created_at: addDaysTime(-110, 13, 0),
      updated_at: addDaysTime(-1, 8, 30),
      documents: [
        doc("d4a", "bill", "case_jordan_northwestern_il", -110, true, ""),
        doc("d4b", "collection_notice", "case_jordan_northwestern_il", -25, true, ""),
        doc("d4c", "itemized_bill", "case_jordan_northwestern_il", -90, true, ""),
        doc("d4d", "income_proof", "case_jordan_northwestern_il", -108, true, ""),
      ],
      events: [
        ev("case_jordan_northwestern_il", -110, 13, 5, "reader", "Classified document",
          "Bill extracted. Household of 4, uninsured, Illinois.", []),
        ev("case_jordan_northwestern_il", -25, 9, 0, "reader", "Classified document",
          "New upload classified as a third-party collection notice from Meridian Recovery Group.", []),
        ev("case_jordan_northwestern_il", -25, 9, 6, "clock", "Computed deadlines",
          "Debt validation: 30 days from notice. Charity care (IL): 90 days from latest-of trigger dates. Validation is more urgent — sequencing it first.",
          [CITE.VALIDATION, CITE.IL_UNINSURED_DISCOUNT]),
        ev("case_jordan_northwestern_il", -25, 9, 12, "strategist", "Selected fronts",
          "Debt validation first (freezes collection activity), then charity care, then billing audit.", [CITE.VALIDATION]),
        ev("case_jordan_northwestern_il", -20, 10, 0, "filer", "Filed charity care application",
          "FAP application faxed to Northwestern Memorial. Vendor confirmation phx_8834150.", [CITE.IL_UNINSURED_DISCOUNT]),
        ev("case_jordan_northwestern_il", -20, 14, 0, "auditor", "Flagged duplicate imaging charge",
          "CPT 71046 billed twice for a single chest X-ray. Estimated overcharge $1,400.", [CITE.DUPLICATE_BILLING]),
        ev("case_jordan_northwestern_il", -20, 14, 30, "filer", "Filed audit dispute",
          "Duplicate-charge dispute letter sent via certified mail. Vendor confirmation lob_5599487.", [CITE.DUPLICATE_BILLING]),
        ev("case_jordan_northwestern_il", -1, 8, 30, "strategist", "Debt validation queued for approval",
          "Validation dispute letter drafted, citing 12 CFR 1006.34(b) and 15 USC 1692g(a). 5 days remain — awaiting human approval.",
          [CITE.VALIDATION, CITE.FDCPA]),
      ],
      filings: [
        filing("f4a", "case_jordan_northwestern_il", "charity_care", "fax", "phx_8834150", "delivered", -20),
        filing("f4b", "case_jordan_northwestern_il", "audit", "mail", "lob_5599487", "delivered", -20),
      ],
    },

    // 5 — Alicia Torres: for-profit hospital, the honest "no 501(r)" path.
    {
      case_id: "case_alicia_hcaaventura_fl",
      patient: {
        name: "Alicia Torres (SYNTHETIC)",
        household_size: 2,
        annual_income_cents: 41_000_00,
        insured: true,
        state: "FL",
      },
      bill: {
        hospital_ein: "65-0678910",
        hospital_ccn: "100241",
        provider_name: "HCA Florida Aventura Hospital",
        amount_cents: 9_800_00,
        service_date: addDays(-80),
        first_statement_date: addDays(-50),
        gfe_amount_cents: null,
        in_collections: false,
        collector_name: null,
        validation_notice_date: null,
      },
      status: "awaiting_response",
      fronts: [
        {
          front: "charity_care",
          applicable: false,
          reason:
            "HCA Florida Aventura Hospital is a for-profit facility — no 501(r) financial-assistance obligation exists, and Florida has no statewide uninsured-discount analog to Illinois's Act.",
          deadline: null,
          citation: CITE.FOR_PROFIT_NO_DUTY,
          status: "na",
        },
        {
          front: "ppdr",
          applicable: false,
          reason: "Patient is insured — PPDR is limited to uninsured/self-pay billing disputes.",
          deadline: null,
          citation: CITE.PPDR_SCOPE,
          status: "na",
        },
        {
          front: "debt_validation",
          applicable: false,
          reason: "Account has not been placed with a collector.",
          deadline: null,
          citation: CITE.VALIDATION,
          status: "na",
        },
        {
          front: "audit",
          applicable: true,
          reason: "Facility fee billed twice under two different revenue codes for the same ED visit.",
          deadline: addDays(20),
          citation: CITE.ITEMIZED_BILL,
          status: "filed",
        },
      ],
      savings_found_cents: 600_00,
      audit_findings_cents: 600_00,
      hospital_name: "HCA Florida Aventura Hospital",
      hospital_nonprofit: false,
      denial_flag: null,
      created_at: addDaysTime(-50, 15, 0),
      updated_at: addDaysTime(-4, 10, 0),
      documents: [
        doc("d5a", "bill", "case_alicia_hcaaventura_fl", -50, true, ""),
        doc("d5b", "itemized_bill", "case_alicia_hcaaventura_fl", -45, true, ""),
      ],
      events: [
        ev("case_alicia_hcaaventura_fl", -50, 15, 4, "reader", "Classified document",
          "Bill and itemized statement extracted.", []),
        ev("case_alicia_hcaaventura_fl", -50, 15, 10, "lookup", "Resolved hospital",
          "EIN 65-0678910 (HCA Florida Aventura Hospital) — for-profit. No 501(r) obligation; charity-care front correctly not offered rather than guessed.",
          [CITE.FOR_PROFIT_NO_DUTY]),
        ev("case_alicia_hcaaventura_fl", -49, 9, 0, "auditor", "Flagged duplicate facility fee",
          "Revenue codes 0450 and 0451 both billed for one ED encounter. Estimated overcharge $600.", [CITE.DUPLICATE_BILLING]),
        ev("case_alicia_hcaaventura_fl", -49, 9, 20, "strategist", "Selected fronts",
          "Only the billing audit applies. Charity care and PPDR correctly excluded with reasons on record.", []),
        ev("case_alicia_hcaaventura_fl", -14, 11, 0, "filer", "Filed audit dispute",
          "Duplicate facility-fee dispute sent via certified mail. Vendor confirmation lob_5599560.", [CITE.DUPLICATE_BILLING]),
      ],
      filings: [filing("f5a", "case_alicia_hcaaventura_fl", "audit", "mail", "lob_5599560", "delivered", -14)],
    },

    // 6 — Ben Okafor: CA charity care, pending human approval (no clock, low drama by design).
    {
      case_id: "case_ben_ucsf_ca",
      patient: {
        name: "Ben Okafor (SYNTHETIC)",
        household_size: 1,
        annual_income_cents: 20_500_00,
        insured: false,
        state: "CA",
      },
      bill: {
        hospital_ein: "94-1156329",
        hospital_ccn: "050448",
        provider_name: "UCSF Medical Center",
        amount_cents: 6_150_00,
        service_date: addDays(-60),
        first_statement_date: addDays(-20),
        gfe_amount_cents: null,
        in_collections: false,
        collector_name: null,
        validation_notice_date: null,
      },
      status: "filing",
      fronts: [
        {
          front: "charity_care",
          applicable: true,
          reason:
            "Nonprofit CA hospital; income under UCSF's free-care threshold. California imposes no application deadline.",
          deadline: null,
          citation: CITE.CA_NO_DEADLINE,
          status: "open",
        },
        {
          front: "ppdr",
          applicable: false,
          reason: "No Good Faith Estimate on file.",
          deadline: null,
          citation: CITE.PPDR_DELTA,
          status: "na",
        },
        {
          front: "debt_validation",
          applicable: false,
          reason: "Account has not been placed with a collector.",
          deadline: null,
          citation: CITE.VALIDATION,
          status: "na",
        },
        {
          front: "audit",
          applicable: true,
          reason: "One duplicate supply-code line found.",
          deadline: null,
          citation: CITE.ITEMIZED_BILL,
          status: "filed",
        },
      ],
      savings_found_cents: 6_150_00,
      audit_findings_cents: 650_00,
      hospital_name: "UCSF Medical Center",
      hospital_nonprofit: true,
      denial_flag: null,
      created_at: addDaysTime(-20, 9, 0),
      updated_at: addDaysTime(-2, 9, 0),
      documents: [
        doc("d6a", "bill", "case_ben_ucsf_ca", -20, true, ""),
        doc("d6b", "itemized_bill", "case_ben_ucsf_ca", -17, true, ""),
        doc("d6c", "income_proof", "case_ben_ucsf_ca", -19, true, ""),
      ],
      events: [
        ev("case_ben_ucsf_ca", -20, 9, 2, "reader", "Classified document", "Bill extracted; uninsured, CA.", []),
        ev("case_ben_ucsf_ca", -20, 9, 8, "lookup", "Resolved hospital", "EIN 94-1156329 (UCSF Medical Center) — nonprofit, CA.", []),
        ev("case_ben_ucsf_ca", -19, 10, 0, "auditor", "Flagged duplicate supply line",
          "One supply code billed twice on the same claim. Estimated overcharge $650.", [CITE.DUPLICATE_BILLING]),
        ev("case_ben_ucsf_ca", -19, 10, 20, "strategist", "Selected fronts",
          "Charity care and billing audit selected; no PPDR (no GFE) and no debt validation (not in collections).", []),
        ev("case_ben_ucsf_ca", -8, 13, 0, "filer", "Filed audit dispute",
          "Duplicate-charge dispute letter sent via certified mail. Vendor confirmation lob_5599623.", [CITE.DUPLICATE_BILLING]),
        ev("case_ben_ucsf_ca", -2, 9, 0, "strategist", "Charity care queued for approval",
          "FAP application rendered and ready to fax — no statutory clock in California, but queued for human sign-off before sending.",
          [CITE.CA_NO_DEADLINE]),
      ],
      filings: [filing("f6a", "case_ben_ucsf_ca", "audit", "mail", "lob_5599623", "delivered", -8)],
    },

    // 7 — Sam Whitaker: insured, insurer-denial appeal supported by the audit.
    {
      case_id: "case_sam_advocate_il",
      patient: {
        name: "Sam Whitaker (SYNTHETIC)",
        household_size: 2,
        annual_income_cents: 68_000_00,
        insured: true,
        state: "IL",
      },
      bill: {
        hospital_ein: "36-2167612",
        hospital_ccn: "140010",
        provider_name: "Advocate Christ Medical Center",
        amount_cents: 5_100_00,
        service_date: addDays(-50),
        first_statement_date: addDays(-20),
        gfe_amount_cents: null,
        in_collections: false,
        collector_name: null,
        validation_notice_date: null,
      },
      status: "awaiting_response",
      fronts: [
        {
          front: "charity_care",
          applicable: false,
          reason: "Insured; household income above Advocate Christ's discounted-care threshold.",
          deadline: null,
          citation: CITE.FAP_ELIGIBILITY,
          status: "na",
        },
        {
          front: "ppdr",
          applicable: false,
          reason: "Patient is insured — PPDR is limited to uninsured/self-pay bills.",
          deadline: null,
          citation: CITE.PPDR_SCOPE,
          status: "na",
        },
        {
          front: "debt_validation",
          applicable: false,
          reason: "Account has not been placed with a collector.",
          deadline: null,
          citation: CITE.VALIDATION,
          status: "na",
        },
        {
          front: "audit",
          applicable: true,
          reason:
            "Insurer's EOB denied the claim as 'not medically necessary' for a CPT pair NCCI permits together; the billing audit supports the pending appeal. Advocate Christ's billing office gives a 10-business-day response window on disputes like this one.",
          deadline: addDays(6),
          citation: CITE.ITEMIZED_BILL,
          status: "filed",
        },
      ],
      savings_found_cents: 860_00,
      audit_findings_cents: 860_00,
      hospital_name: "Advocate Christ Medical Center",
      hospital_nonprofit: true,
      denial_flag: null,
      created_at: addDaysTime(-20, 11, 0),
      updated_at: addDaysTime(-5, 9, 0),
      documents: [
        doc("d7a", "bill", "case_sam_advocate_il", -20, true, ""),
        doc("d7b", "denial_letter", "case_sam_advocate_il", -12, true, ""),
      ],
      events: [
        ev("case_sam_advocate_il", -20, 11, 2, "reader", "Classified document", "Bill extracted; insured, IL.", []),
        ev("case_sam_advocate_il", -12, 9, 0, "reader", "Classified document",
          "New upload classified as an insurer EOB denial ('not medically necessary').", []),
        ev("case_sam_advocate_il", -12, 9, 10, "auditor", "NCCI check on denial basis",
          "The denied CPT pair is not a restricted NCCI PTP edit — the denial basis does not match CMS's own bundling rules.", [CITE.NCCI_EDIT]),
        ev("case_sam_advocate_il", -11, 10, 0, "strategist", "Selected fronts",
          "Billing audit selected to support the insurer appeal. Charity care and PPDR correctly excluded (insured).", []),
        ev("case_sam_advocate_il", -10, 13, 0, "filer", "Filed audit dispute",
          "Appeal support letter with NCCI citation sent via certified mail. Vendor confirmation lob_5599701.", [CITE.NCCI_EDIT]),
        ev("case_sam_advocate_il", -5, 9, 0, "clock", "Deadline approaching",
          "6 days remain on Advocate Christ's billing-dispute response window.", []),
      ],
      filings: [filing("f7a", "case_sam_advocate_il", "audit", "mail", "lob_5599701", "delivered", -10)],
    },

    // 8 — Nina Kowalski: the cat-photo case; Verifier blocks charity care, PPDR still moves.
    {
      case_id: "case_nina_stanford_ca",
      patient: {
        name: "Nina Kowalski (SYNTHETIC)",
        household_size: 1,
        annual_income_cents: 29_000_00,
        insured: false,
        state: "CA",
      },
      bill: {
        hospital_ein: "94-1156365",
        hospital_ccn: "050269",
        provider_name: "Stanford Health Care",
        amount_cents: 4_500_00,
        service_date: addDays(-30),
        first_statement_date: addDays(-10),
        gfe_amount_cents: 2_500_00,
        in_collections: false,
        collector_name: null,
        validation_notice_date: null,
      },
      status: "analyzing",
      fronts: [
        {
          front: "ppdr",
          applicable: true,
          reason:
            "Uninsured; bill exceeds the Good Faith Estimate by $2,000 (≥ $400 floor), within 120 days of the initial bill.",
          deadline: addDays(2),
          citation: CITE.PPDR_DEADLINE,
          status: "open",
        },
        {
          front: "charity_care",
          applicable: false,
          reason:
            "Screening deferred: the Verifier flagged the uploaded income document as unverifiable (the image did not depict an income record). Charity-care determination is paused pending a valid replacement upload.",
          deadline: null,
          citation: CITE.FAP_WINDOW,
          status: "na",
        },
        {
          front: "debt_validation",
          applicable: false,
          reason: "Account has not been placed with a collector.",
          deadline: null,
          citation: CITE.VALIDATION,
          status: "na",
        },
        {
          front: "audit",
          applicable: true,
          reason: "One duplicate pharmacy line found.",
          deadline: null,
          citation: CITE.ITEMIZED_BILL,
          status: "filed",
        },
      ],
      savings_found_cents: 4_500_00,
      audit_findings_cents: 1_190_00,
      hospital_name: "Stanford Health Care",
      hospital_nonprofit: true,
      denial_flag: null,
      created_at: addDaysTime(-10, 8, 0),
      updated_at: addDaysTime(0, 7, 45),
      documents: [
        doc("d8a", "bill", "case_nina_stanford_ca", -10, true, ""),
        doc("d8b", "gfe", "case_nina_stanford_ca", -31, true, ""),
        doc("d8c", "itemized_bill", "case_nina_stanford_ca", -9, true, ""),
        doc(
          "d8d",
          "income_proof",
          "case_nina_stanford_ca",
          -6,
          false,
          "Uploaded file does not appear to be an income document — image classified as a photograph of a cat. Please upload a pay stub, W-2, or benefits award letter."
        ),
      ],
      events: [
        ev("case_nina_stanford_ca", -10, 8, 3, "reader", "Classified document",
          "Bill and prior Good Faith Estimate extracted; delta computed at $2,000.", []),
        ev("case_nina_stanford_ca", -10, 8, 9, "lookup", "Resolved hospital",
          "EIN 94-1156365 (Stanford Health Care) — nonprofit, CA.", []),
        ev("case_nina_stanford_ca", -10, 8, 20, "clock", "Computed deadlines",
          "PPDR: 120 days from initial bill.", [CITE.PPDR_DEADLINE]),
        ev("case_nina_stanford_ca", -9, 12, 0, "auditor", "Flagged duplicate pharmacy line",
          "One pharmacy NDC billed twice on the same date. Estimated overcharge $1,190.", [CITE.DUPLICATE_BILLING]),
        ev("case_nina_stanford_ca", -9, 12, 15, "filer", "Filed audit dispute",
          "Duplicate-charge dispute letter sent via certified mail. Vendor confirmation lob_5599788.", [CITE.DUPLICATE_BILLING]),
        ev("case_nina_stanford_ca", -6, 17, 30, "verifier", "Blocked charity-care screening",
          "Requested income document does not match a pay stub, W-2, or benefits letter — the uploaded image was classified as a photograph of a cat. Charity-care screening cannot proceed until a valid document is provided.", []),
        ev("case_nina_stanford_ca", 0, 7, 45, "strategist", "PPDR queued for approval",
          "PPDR initiation form rendered and awaiting human approval — only 2 days remain on the 120-day window.", [CITE.PPDR_DEADLINE]),
      ],
      filings: [filing("f8a", "case_nina_stanford_ca", "audit", "mail", "lob_5599788", "delivered", -3)],
    },
  ];

  return cases;
}

function doc(
  doc_id: string,
  type: CaseDetail["documents"][number]["type"],
  caseId: string,
  dayOffset: number,
  verified: boolean | null,
  notes: string
): CaseDetail["documents"][number] {
  return {
    doc_id,
    type,
    gcs_uri: `gs://ef-documents/${caseId}/${doc_id}.pdf`,
    uploaded_at: addDaysTime(dayOffset, 8 + (dayOffset % 5), 0),
    extracted: {},
    verified,
    verification_notes: notes,
  };
}

function ev(
  caseId: string,
  dayOffset: number,
  hh: number,
  mm: number,
  agent: CaseEvent["agent"],
  action: string,
  detail: string,
  citations: string[]
): CaseEvent {
  return {
    event_id: `${caseId}_${dayOffset}_${hh}${mm}`,
    case_id: caseId,
    ts: addDaysTime(dayOffset, hh, mm),
    agent,
    action,
    detail,
    citations,
  };
}

function filing(
  filing_id: string,
  caseId: string,
  front: Filing["front"],
  channel: Filing["channel"],
  vendor_id: string,
  status: Filing["status"],
  dayOffset: number
): Filing {
  return {
    filing_id,
    case_id: caseId,
    front,
    channel,
    vendor_id,
    status,
    proof:
      channel === "fax"
        ? { phaxio_id: vendor_id }
        : { lob_id: vendor_id, tracking: `9400 1000 0000 ${dayOffset.toString().padStart(4, "0")} 0000 00` },
    sent_at: addDaysTime(dayOffset, 10, 0),
  };
}

/** §3.4 — every field is an aggregate over `cases`, never hand-typed. */
export function computeStats(cases: CaseDetail[]): DashboardStats {
  const open = cases.filter((c) => c.status !== "won" && c.status !== "closed");
  const hospitalSet = new Set(cases.map((c) => c.bill.hospital_ein));
  const now = new Date();
  const todayUTCms = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  const weekOutUTCms = todayUTCms + 7 * 24 * 60 * 60 * 1000;

  let deadlinesThisWeek = 0;
  let charityEligible = 0;
  let ppdrEligible = 0;
  let filingsSent = 0;
  let auditFindings = 0;
  let totalBilled = 0;
  let unlawful = 0;

  for (const c of cases) {
    totalBilled += c.bill.amount_cents;
    auditFindings += c.audit_findings_cents;
    if (c.denial_flag?.violated) unlawful += 1;

    for (const f of c.fronts) {
      if (!f.applicable) continue;
      if (f.front === "charity_care") charityEligible += 1;
      if (f.front === "ppdr") ppdrEligible += 1;
      if (f.deadline) {
        const dms = dateOnlyToUTCms(f.deadline);
        if (dms >= todayUTCms && dms <= weekOutUTCms) deadlinesThisWeek += 1;
      }
    }
    for (const filing of c.filings) {
      if (filing.status === "sent" || filing.status === "delivered") filingsSent += 1;
    }
  }

  return {
    open_cases: open.length,
    hospitals: hospitalSet.size,
    deadlines_this_week: deadlinesThisWeek,
    total_billed_cents: totalBilled,
    charity_eligible: charityEligible,
    ppdr_eligible: ppdrEligible,
    unlawful_denials_flagged: unlawful,
    audit_findings_cents: auditFindings,
    filings_sent: filingsSent,
    human_hours: 0,
  };
}

export { hospitalByEin };
