/**
 * THE SWAP POINT. Every screen imports from here, never from lib/store or
 * lib/mock-data directly. Set NEXT_PUBLIC_API_BASE_URL to point at a real
 * services/api deployment (SWARM, §3.3) and every function below switches
 * from the in-memory mock to real `fetch` calls — nothing else in web/
 * needs to change.
 *
 * Until that env var is set, we build against the mock (§0: "do not wait").
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

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL?.replace(/\/$/, "") || "";
export const USING_MOCK = API_BASE === "";

// Small artificial latency so mock-mode loading states look and behave like
// the real network call they'll become — this is what lets us build/tune
// skeletons and the "zero layout shift" polling behavior against something
// realistic before services/api exists.
const MOCK_LATENCY_MS = 220;
function delay<T>(value: T): Promise<T> {
  return new Promise((resolve) => setTimeout(() => resolve(value), MOCK_LATENCY_MS));
}

async function realFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(`${API_BASE}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...init?.headers },
    cache: "no-store",
  });
  if (!res.ok) {
    const text = await res.text().catch(() => "");
    throw new Error(`${path} -> ${res.status} ${text}`.trim());
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
  if (USING_MOCK) return delay(store.getStats());
  return realFetch<DashboardStats>("/dashboard/stats");
}

/** GET /cases */
export async function getCases(): Promise<CaseSummary[]> {
  if (USING_MOCK) return delay(store.listCases().map(toSummary));
  return realFetch<CaseSummary[]>("/cases");
}

/** GET /cases/{id} */
export async function getCase(caseId: string): Promise<CaseDetail | null> {
  if (USING_MOCK) return delay(store.getCase(caseId) ?? null);
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
  if (USING_MOCK) {
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
  if (USING_MOCK) {
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
  if (USING_MOCK) return delay(store.getHospital(ein));
  try {
    return await realFetch<Hospital>(`/hospitals/${encodeURIComponent(ein)}`);
  } catch {
    return null;
  }
}

/**
 * Not in §3.3 as a dedicated endpoint — the global activity feed (WO3) reads
 * it by flattening every case's events/. Once services/api exists this can
 * either stay client-side (fetch each case) or, better, get its own
 * `GET /events` endpoint; flagged as a HANDOFF item in the PR rather than
 * silently diverging from the contract.
 */
export async function getActivityFeed() {
  if (USING_MOCK) return delay(store.getAllEvents());
  const cases = await getCases();
  const details = await Promise.all(cases.map((c) => getCase(c.case_id)));
  return details
    .filter((c): c is CaseDetail => c !== null)
    .flatMap((c) => c.events)
    .sort((a, b) => new Date(b.ts).getTime() - new Date(a.ts).getTime());
}

export async function listFixtures(): Promise<string[]> {
  if (USING_MOCK) return delay(store.availableFixtures());
  return ["maria_uninsured_ca", "unparseable_bill"];
}

/**
 * Manual intake — NOT in the literal §3.3 contract (only the Gmail watch
 * and /demo/inject_bill create cases there). A HANDOFF note proposes adding
 * `POST /cases` for this; until then it only works in mock mode, and this
 * function is a no-op stub against a real backend so the intake screen
 * degrades honestly instead of pretending to work.
 */
export async function createCase(
  input: store.IntakeInput
): Promise<{ ok: boolean; case_id?: string; error?: string }> {
  if (USING_MOCK) {
    const result = store.createCaseFromIntake(input);
    return delay(
      result.ok ? { ok: true, case_id: result.case.case_id } : { ok: false, error: result.error }
    );
  }
  return {
    ok: false,
    error:
      "Manual intake has no real-API endpoint yet (see HANDOFF in the CANVAS PR) — use the Gmail watch or /demo/inject_bill.",
  };
}
