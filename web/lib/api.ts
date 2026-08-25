/**
 * THE SWAP POINT. Every screen imports from here, never from lib/store or
 * lib/mock-data directly.
 *
 * The live/mock decision and the real API's base URL are both resolved at
 * REQUEST time by server-side Route Handlers (app/api/config, app/api/proxy)
 * reading the plain (non-`NEXT_PUBLIC_*`) `API_BASE_URL` env var — never
 * baked into the client bundle at `next build` time. See those files'
 * docstrings for why: same-container runtime repointing (no rebuild) and
 * sidestepping services/api's missing CORS headers. Client code below only
 * ever talks to same-origin `/api/*`.
 *
 * The mock intentionally implements the exact same request/response shapes
 * as §3.3 so the swap is mechanical.
 */
import * as store from "./store";
import type {
  CaseDetail,
  CaseSummary,
  DashboardStats,
  FrontType,
  Hospital,
} from "./types";

// Small artificial latency so mock-mode loading states look and behave like
// the real network call they'll become — this is what lets us build/tune
// skeletons and the "zero layout shift" polling behavior against something
// realistic before services/api exists.
const MOCK_LATENCY_MS = 220;
function delay<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), MOCK_LATENCY_MS));
}

// Cached for the lifetime of the page (a hard refresh re-checks) so every
// call site can `await usingMock()` cheaply instead of threading a prop
// through every component. Fails safe toward mock: a network hiccup hitting
// /api/config should never blank the demo.
let mockFlag: Promise<boolean> | null = null;
export function usingMock(): Promise<boolean> {
  if (!mockFlag) {
    mockFlag = fetch("/api/config", { cache: "no-store" })
      .then((res) => (res.ok ? res.json() : { usingMock: true }))
      .then((data: { usingMock: boolean }) => Boolean(data.usingMock))
      .catch(() => true);
  }
  return mockFlag;
}

async function realFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`/api/proxy${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    // services/api returns FastAPI's default {"detail": "..."} shape on
    // errors (verified live, e.g. approve_filing on an inapplicable front
    // -> 409 {"detail":"case has no front 'audit'"}) — surface that message
    // directly rather than the raw response body when we can parse it.
    let detail = text;
    try {
      const parsed = JSON.parse(text);
      if (parsed && typeof parsed.detail === "string") detail = parsed.detail;
    } catch {
      // not JSON — fall through with the raw text
    }
    throw new Error(detail || `${path} -> ${res.status}`);
  }
  return res.json() as Promise<T>;
}

function toSummary(c: CaseDetail): CaseSummary {
  return {
    case_id: c.case_id,
    patient: c.patient,
    bill: c.bill,
    status: c.status,
    fronts: c.fronts,
    savings_found_cents: c.savings_found_cents,
    audit_findings_cents: c.audit_findings_cents,
    hospital_name: c.hospital_name,
    hospital_nonprofit: c.hospital_nonprofit,
    denial_flag: c.denial_flag,
    created_at: c.created_at,
    updated_at: c.updated_at,
  };
}

/** GET /dashboard/stats */
export async function getStats(): Promise<DashboardStats> {
  if (await usingMock()) return delay(store.getStats());
  return realFetch<DashboardStats>("/dashboard/stats");
}

/** GET /cases */
export async function getCases(): Promise<CaseSummary[]> {
  if (await usingMock()) return delay(store.listCases().map(toSummary));
  return realFetch<CaseSummary[]>("/cases");
}

/** GET /cases/{id} */
export async function getCase(caseId: string): Promise<CaseDetail | null> {
  if (await usingMock()) return delay(store.getCase(caseId) ?? null);
  try {
    return await realFetch<CaseDetail>(`/cases/${encodeURIComponent(caseId)}`);
  } catch {
    return null;
  }
}

/** POST /cases/{id}/approve_filing {front} — the human-in-the-loop gate. */
export async function approveFiling(
  caseId: string,
  front: FrontType
): Promise<{ ok: boolean; error?: string }> {
  if (await usingMock()) {
    const result = store.approveFiling(caseId, front);
    return delay(result.ok ? { ok: true } : { ok: false, error: result.error });
  }
  try {
    await realFetch(`/cases/${encodeURIComponent(caseId)}/approve_filing`, {
      method: "POST",
      body: JSON.stringify({ front }),
    });
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "request failed" };
  }
}

/** POST /demo/inject_bill {fixture_name} — drives the live demo. */
export async function injectBill(
  fixtureName: string
): Promise<{ ok: boolean; case_id?: string; error?: string }> {
  if (await usingMock()) {
    const result = store.injectBill(fixtureName);
    return delay(
      result.ok ? { ok: true, case_id: result.case.case_id } : { ok: false, error: result.error }
    );
  }
  try {
    const res = await realFetch<{ case_id: string }>("/demo/inject_bill", {
      method: "POST",
      body: JSON.stringify({ fixture_name: fixtureName }),
    });
    return { ok: true, case_id: res.case_id };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "request failed" };
  }
}

/** GET /hospitals/{ein} */
export async function getHospital(ein: string): Promise<Hospital | null> {
  if (await usingMock()) return delay(store.getHospital(ein));
  try {
    return await realFetch<Hospital>(`/hospitals/${encodeURIComponent(ein)}`);
  } catch {
    return null;
  }
}

/**
 * §3.3 added a dedicated `GET /events?limit&agent` for exactly this (the
 * "money shot" global activity feed) — but it is live-broken: verified
 * `curl {API_BASE}/events` and `?limit=10` both return a bare 500 Internal
 * Server Error with no JSON body. Flagged as a HANDOFF in the PR. Rather
 * than surface that 500 to the activity screen, try the real endpoint first
 * and silently fall back to the previous (correct, just O(cases)) approach
 * of fetching every case and flattening `events[]` client-side — so the
 * feed keeps working today and picks up the real endpoint for free the
 * moment it's fixed server-side.
 */
export async function getActivityFeed(): Promise<CaseDetail["events"]> {
  if (await usingMock()) return delay(store.getAllEvents());
  try {
    return await realFetch<CaseDetail["events"]>("/events?limit=200");
  } catch {
    // fall through to the per-case flatten below
  }
  const cases = await getCases();
  const details = await Promise.all(cases.map((c) => getCase(c.case_id)));
  return details
    .filter((c): c is CaseDetail => c !== null)
    .flatMap((c) => c.events)
    .sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());
}

/**
 * PROOF's synthetic corpus (fixtures/cases_data.py) — the real
 * `POST /demo/inject_bill` has no companion "list available fixtures"
 * endpoint, so these 8 names are hardcoded from that file rather than
 * invented; they're stable, documented fixture ids, not guessed data.
 */
const LIVE_FIXTURES = [
  "case_01_uninsured_gfe_ca",
  "case_02_wrongful_denial_il",
  "case_03_in_collections_ca",
  "case_04_forprofit_il",
  "case_05_cat_photo_income_proof",
  "case_06_unparseable_bill",
  "case_07_il_concurrent_clocks",
  "case_08_lawful_denial_ca",
];

export async function listFixtures(): Promise<string[]> {
  if (await usingMock()) return delay(store.availableFixtures());
  return LIVE_FIXTURES;
}

/**
 * Manual intake. §3.3 added `POST /cases {patient, bill} -> case_id` for
 * exactly this (previously a HANDOFF item from the mock-only build) —
 * verified live via curl. The Verifier-adjacent fields on `IntakeInput`
 * (`incomeDocUploaded`, `incomeDocLooksValid`) have no home in that request
 * body (§3.3 doesn't accept document upload on case creation), so they stay
 * a client-side-only simulation exactly as the mock treated the upload
 * step — only the case-creation call itself goes to the real API.
 */
export async function createCase(
  input: store.IntakeInput
): Promise<{ ok: boolean; case_id?: string; error?: string }> {
  if (await usingMock()) {
    const result = store.createCaseFromIntake(input);
    return delay(
      result.ok ? { ok: true, case_id: result.case.case_id } : { ok: false, error: result.error }
    );
  }
  const today = new Date().toISOString().slice(0, 10);
  try {
    const res = await realFetch<{ case_id: string }>("/cases", {
      method: "POST",
      body: JSON.stringify({
        patient: {
          name: `${input.patientName} (SYNTHETIC)`,
          household_size: input.householdSize,
          annual_income_cents: input.annualIncomeCents,
          insured: input.insured,
          state: input.state,
        },
        bill: {
          hospital_ein: input.hospitalEin,
          amount_cents: input.amountCents,
          service_date: today,
          first_statement_date: today,
          gfe_amount_cents: input.gfeAmountCents,
          in_collections: input.inCollections,
        },
      }),
    });
    return { ok: true, case_id: res.case_id };
  } catch (e) {
    return { ok: false, error: e instanceof Error ? e.message : "request failed" };
  }
}
