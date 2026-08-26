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


def test_inject_bill_merges_fixture_hospital_fields_into_an_already_seeded_hospital(monkeypatch):
    """DEFECT (PROOF PR #23 HANDOFF #2): the old code only wrote a fixture's
    `hospital` dict to Firestore when `store.get_hospital(ein) is None` --
    "don't clobber LEDGER's real seed." But that meant PROOF's two
    hand-seeded denial-triage fixtures (case_02, case_08), whose EINs LEDGER
    HAD already seeded, never got their `fap_required_documents` field
    persisted at all: Lookup resolves straight from Firestore and
    agent_core/pipeline.py fully replaces `case["hospital"]` with whatever
    Lookup found, so a field that was never actually written there vanishes
    -- reproducing case_08's `insufficient_data` bug non-deterministically
    depending on which hospital happened to need the fixture-only field.

    Fix: merge in only the keys the existing (real, LEDGER-seeded) record
    does not already have -- never touch a key LEDGER's real record already
    carries, but do not silently drop the fixture's own additive data either.
    """
    c, s = client_with_store(monkeypatch)
    ein = "36-2169147"
    # Simulate LEDGER having already seeded this EIN with real Schedule H data
    # (no fap_required_documents -- that field is not part of §3.1's schema).
    s.put_hospital(
        ein, {"name": "Advocate Christ Medical Center", "nonprofit": True, "state": "IL"}
    )

    fake_fixture = {
        "patient": {"name": "SYNTHETIC -- TEST", "state": "IL"},
        "bill": {"hospital_ein": ein, "amount_cents": 1000_00},
        "hospital": {
            "ein": ein,
            "name": "Advocate Christ Medical Center",
            "nonprofit": True,
            "fap_required_documents": [
                "completed application form",
                "proof of income last 30 days",
            ],
        },
        "documents": [],
    }
    monkeypatch.setattr(main, "load_fixture", lambda name: fake_fixture)

    async def fake_process(case_id, doc_ids):
        return {"readers": {}, "lookup": {}, "clock": {}, "auditor": {}, "strategist": {}}

    monkeypatch.setattr(main, "agent_core_process_documents", fake_process)

    resp = c.post("/demo/inject_bill", json={"fixture_name": "case_02_wrongful_denial_il"})
    assert resp.status_code == 200

    updated = s.get_hospital(ein)
    # LEDGER's real field is untouched...
    assert updated["name"] == "Advocate Christ Medical Center"
    assert updated["state"] == "IL"
    # ...and the fixture's additive field actually made it into Firestore this
    # time, where agent_core.agents.lookup will actually see it.
    assert updated["fap_required_documents"] == [
        "completed application form",
        "proof of income last 30 days",
    ]


def test_inject_bill_never_overwrites_an_existing_hospital_field(monkeypatch):
    """The merge must be additive-only: a fixture's own (possibly stale)
    hospital field must never clobber LEDGER's real seeded value."""
    c, s = client_with_store(monkeypatch)
    ein = "94-6174066"
    s.put_hospital(ein, {"name": "Stanford Health Care", "nonprofit": True, "mrf_url": "real-url"})

    fake_fixture = {
        "patient": {"name": "SYNTHETIC -- TEST", "state": "CA"},
        "bill": {"hospital_ein": ein, "amount_cents": 500_00},
        "hospital": {"ein": ein, "name": "STALE FIXTURE NAME", "mrf_url": None},
        "documents": [],
    }
    monkeypatch.setattr(main, "load_fixture", lambda name: fake_fixture)

    async def fake_process(case_id, doc_ids):
        return {"readers": {}, "lookup": {}, "clock": {}, "auditor": {}, "strategist": {}}

    monkeypatch.setattr(main, "agent_core_process_documents", fake_process)

    resp = c.post("/demo/inject_bill", json={"fixture_name": "case_08_lawful_denial_ca"})
    assert resp.status_code == 200

    updated = s.get_hospital(ein)
    assert updated["name"] == "Stanford Health Care"  # not clobbered by the fixture
    assert updated["mrf_url"] == "real-url"


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


def test_inject_bill_publishes_only_after_agent_core_has_processed(monkeypatch):
    """The ordering that stops `/demo/inject_bill` running two cascades per
    document.

    This endpoint reaches agent-core twice for the same document: it publishes
    `case.document.added` (which agent-core consumes as a push subscriber) and
    it calls `/internal/process_documents` synchronously. It used to publish
    FIRST, and a comment here claimed agent-core's `doc:{case_id}:{doc_id}`
    dedupe made that safe. It did not: push delivery is sub-second and this
    call takes minutes, so the push won the race and a 3-document case ran 4
    cascades -- every audit finding four times over on the live activity feed.

    Announcing the work after doing it removes the race rather than trying to
    win it. The topic is still genuinely published to, which §1.3 asks us to
    demonstrate and a judge can see in the Cloud Console.
    """
    c, _ = client_with_store(monkeypatch)
    order = []
    monkeypatch.setattr(
        main, "publish", lambda topic, payload: order.append(("publish", payload["doc_id"]))
    )

    async def fake_process(case_id, doc_ids):
        order.append(("process", tuple(doc_ids)))
        return {"readers": {}}

    monkeypatch.setattr(main, "agent_core_process_documents", fake_process)
    resp = c.post("/demo/inject_bill", json={"fixture_name": "maria_uninsured_ca"})

    doc_ids = resp.json()["doc_ids"]
    assert order[0] == ("process", tuple(doc_ids)), (
        f"published before agent-core had processed anything: {order}"
    )
    assert order[1:] == [("publish", d) for d in doc_ids]
