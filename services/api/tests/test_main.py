"""services/api/main.py -- contract §3.3 exactly, plus /demo/inject_bill.

`main.store` and the two agent-core HTTP calls are monkeypatched so this
suite never touches a real Firestore project or a real agent-core service.
"""

from __future__ import annotations

import main
from _helpers import make_memory_store
from fastapi.testclient import TestClient


def client_with_store(monkeypatch):
    s = make_memory_store()
    monkeypatch.setattr(main, "store", s)
    monkeypatch.setattr(main, "publish", lambda topic, payload: None)
    return TestClient(main.app), s


def test_root_and_health():
    c = TestClient(main.app)
    assert c.get("/").json()["service"] == "api"
    assert c.get("/health").json() == {"ok": True}


def test_list_cases_empty_and_populated(monkeypatch):
    c, s = client_with_store(monkeypatch)
    assert c.get("/cases").json() == []
    s.create_case("c1", {"patient": {"state": "CA"}})
    resp = c.get("/cases").json()
    assert len(resp) == 1
    assert resp[0]["case_id"] == "c1"


def test_get_case_404(monkeypatch):
    c, _ = client_with_store(monkeypatch)
    resp = c.get("/cases/nope")
    assert resp.status_code == 404


def test_get_case_includes_documents_events_filings(monkeypatch):
    c, s = client_with_store(monkeypatch)
    s.create_case("c1", {})
    s.add_document("c1", {"type": "bill"})
    s.append_event("c1", "reader", "classify", "detail")
    resp = c.get("/cases/c1").json()
    assert len(resp["documents"]) == 1
    assert len(resp["events"]) == 1
    assert resp["filings"] == []


def test_approve_filing_404_missing_case(monkeypatch):
    c, _ = client_with_store(monkeypatch)
    resp = c.post("/cases/nope/approve_filing", json={"front": "ppdr"})
    assert resp.status_code == 404


def test_approve_filing_success(monkeypatch):
    c, s = client_with_store(monkeypatch)
    s.create_case("c1", {})

    async def fake_approve(case_id, front):
        assert case_id == "c1"
        assert front == "ppdr"
        return {"ok": True, "front": {"front": "ppdr", "status": "filed"}}

    monkeypatch.setattr(main, "agent_core_approve_filing", fake_approve)
    resp = c.post("/cases/c1/approve_filing", json={"front": "ppdr"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True


def test_approve_filing_gate_rejects_when_agent_core_says_no(monkeypatch):
    c, s = client_with_store(monkeypatch)
    s.create_case("c1", {})

    async def fake_approve(case_id, front):
        return {"ok": False, "reason": "verifier failed: no income_proof document on file"}

    monkeypatch.setattr(main, "agent_core_approve_filing", fake_approve)
    resp = c.post("/cases/c1/approve_filing", json={"front": "charity_care"})
    assert resp.status_code == 409
    assert "income_proof" in resp.json()["detail"]


def test_dashboard_stats_shape_matches_contract(monkeypatch):
    c, _ = client_with_store(monkeypatch)
    stats = c.get("/dashboard/stats").json()
    assert set(stats) == {
        "open_cases",
        "hospitals",
        "deadlines_this_week",
        "total_billed_cents",
        "charity_eligible",
        "ppdr_eligible",
        "unlawful_denials_flagged",
        "audit_findings_cents",
        "filings_sent",
        "human_hours",
    }
    assert stats["human_hours"] == 0


def test_dashboard_stats_aggregates_cases(monkeypatch):
    c, s = client_with_store(monkeypatch)
    s.create_case(
        "c1",
        {
            "bill": {"hospital_ein": "94-0562680", "amount_cents": 5_000_00},
            "fronts": [
                {"front": "charity_care", "applicable": True, "status": "open"},
                {"front": "ppdr", "applicable": True, "status": "filed"},
            ],
            "audit_findings_cents": 1_200_00,
            "denial_flag": {
                "violated": True,
                "reason": "unlisted doc",
                "citation": "26 CFR 1.501(r)-4(b)(3)",
            },
        },
    )
    stats = c.get("/dashboard/stats").json()
    assert stats["hospitals"] == 1
    assert stats["total_billed_cents"] == 5_000_00
    assert stats["charity_eligible"] == 1
    assert stats["ppdr_eligible"] == 1
    assert stats["unlawful_denials_flagged"] == 1
    assert stats["audit_findings_cents"] == 1_200_00
    assert stats["filings_sent"] == 1


def test_dashboard_stats_ignores_denial_flag_when_not_violated(monkeypatch):
    c, s = client_with_store(monkeypatch)
    s.create_case("c1", {"denial_flag": {"violated": False, "reason": "", "citation": ""}})
    s.create_case("c2", {"denial_flag": None})
    stats = c.get("/dashboard/stats").json()
    assert stats["unlawful_denials_flagged"] == 0


def test_inject_bill_unknown_fixture_404(monkeypatch):
    c, _ = client_with_store(monkeypatch)
    resp = c.post("/demo/inject_bill", json={"fixture_name": "does-not-exist"})
    assert resp.status_code == 404


def test_inject_bill_known_fixture_creates_case_and_calls_agent_core(monkeypatch):
    """Defect #3 (persona 5 WO2): `/demo/inject_bill` now calls agent-core's
    BATCH endpoint (`process_documents`) exactly once for every document in
    the fixture, instead of once per document -- see agent_core_client.py and
    agent_core/pipeline.py's `process_case_documents`.
    """
    c, s = client_with_store(monkeypatch)
    calls = []

    async def fake_process(case_id, doc_ids):
        calls.append((case_id, tuple(doc_ids)))
        return {"readers": {}, "lookup": {}, "clock": {}, "auditor": {}, "strategist": {}}

    monkeypatch.setattr(main, "agent_core_process_documents", fake_process)
    resp = c.post("/demo/inject_bill", json={"fixture_name": "maria_uninsured_ca"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["case_id"].startswith("demo-maria_uninsured_ca-")
    assert len(calls) == 1
    assert len(calls[0][1]) == len(body["doc_ids"])  # one batch call, every doc_id included

    case = s.get_case(body["case_id"])
    assert case["patient"]["state"] == "CA"
    assert case["bill"]["amount_cents"] == 6_400_00
    assert s.get_hospital("94-0562680") is not None


def test_get_hospital_404_and_success(monkeypatch):
    c, s = client_with_store(monkeypatch)
    assert c.get("/hospitals/00-0000000").status_code == 404
    s.put_hospital("94-0562680", {"name": "Sutter"})
    resp = c.get("/hospitals/94-0562680").json()
    assert resp["name"] == "Sutter"


def test_get_events_is_cross_case_and_supports_limit_and_agent(monkeypatch):
    c, s = client_with_store(monkeypatch)
    s.create_case("c1", {})
    s.create_case("c2", {})
    s.append_event("c1", "reader", "classify", "first")
    s.append_event("c2", "clock", "compute_deadline", "second")

    all_events = c.get("/events").json()
    assert len(all_events) == 2
    assert all_events[0]["detail"] == "second"  # newest first

    filtered = c.get("/events", params={"agent": "reader"}).json()
    assert len(filtered) == 1
    assert filtered[0]["agent"] == "reader"

    limited = c.get("/events", params={"limit": 1}).json()
    assert len(limited) == 1


def test_post_cases_creates_case_shell(monkeypatch):
    c, s = client_with_store(monkeypatch)
    resp = c.post(
        "/cases",
        json={
            "patient": {"name": "SYNTHETIC -- DEMO", "state": "CA", "household_size": 2},
            "bill": {"amount_cents": 1000},
        },
    )
    assert resp.status_code == 200
    case_id = resp.json()["case_id"]
    case = s.get_case(case_id)
    assert case["patient"]["state"] == "CA"
    assert case["bill"]["amount_cents"] == 1000
    assert case["status"] == "intake"


def test_post_cases_defaults_bill_to_empty_dict(monkeypatch):
    c, _ = client_with_store(monkeypatch)
    resp = c.post("/cases", json={"patient": {"name": "SYNTHETIC -- DEMO"}})
    assert resp.status_code == 200
