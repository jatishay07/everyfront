"""agent_core.pipeline -- the deterministic orchestration and, critically,
the human-in-the-loop filing gate (§4 persona 5: Strategist may only emit
filing.requested AFTER POST /cases/{id}/approve_filing).

Every agent's `run()` is monkeypatched to a canned result here -- this suite
is about the ORCHESTRATION logic (event log shape, front status transitions,
the approval gate), not about re-testing each agent's own internals (covered
in test_rules_bridge.py, test_genai_client.py, etc.) or hitting a real model.
"""

from __future__ import annotations

import asyncio

from _helpers import async_fake_turn, make_memory_store
from agent_core import pipeline
from agent_core.agents import auditor, clock, filer, lookup, reader, strategist, verifier


def _patch_store(monkeypatch, s):
    monkeypatch.setattr(pipeline, "store", s)
    monkeypatch.setattr(lookup, "store", s)
    monkeypatch.setattr(auditor, "store", s)
    monkeypatch.setattr(verifier, "store", s)
    monkeypatch.setattr(filer, "store", s)


def _patch_no_op_pubsub(monkeypatch):
    published = []
    monkeypatch.setattr(
        pipeline.pubsub_client, "publish", lambda topic, payload: published.append((topic, payload))
    )
    return published


def _patch_agents_for_document_added(monkeypatch, *, hospital=None):
    monkeypatch.setattr(
        reader,
        "run",
        lambda case_id, doc_id, text, hint=None: async_fake_turn(
            {
                "case_id": case_id,
                "doc_id": doc_id,
                "label": "bill",
                "gemma_raw": "bill",
                "gemma_error": None,
                "extraction": {
                    "amount_cents": 500_00,
                    "first_statement_date": "2026-01-01",
                    "hospital_ein": "36-2169147",
                },
                "citations": [],
            },
            "classified as a bill",
        ),
    )
    monkeypatch.setattr(
        lookup,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "resolved": hospital is not None,
                "hospital": hospital,
                "citations": [],
                "note": "resolved" if hospital else "not found",
            },
            "hospital note",
        ),
    )
    monkeypatch.setattr(
        clock,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "case_id": case_id,
                "deadlines": [
                    {
                        "front": "charity_care",
                        "name": "Charity care application",
                        "due": "2026-08-29",
                        "basis_date": "2026-01-01",
                        "basis_field": "first_statement_date",
                        "citation": "26 CFR 1.501(r)-4(b)(1)(iv)",
                        "days": 240,
                        "explain": "due 2026-08-29",
                    }
                ],
            },
            "one deadline",
        ),
    )
    monkeypatch.setattr(
        auditor,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "case_id": case_id,
                "findings": [],
                "total_findings_cents": 0,
                "denial_check": {"ran": False, "reason": "no denial letter"},
                "source": {},
            },
            "no findings",
        ),
    )
    monkeypatch.setattr(
        strategist,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "case_id": case_id,
                "fronts": [
                    {
                        "front": "charity_care",
                        "applicable": True,
                        "reason": "nonprofit hospital",
                        "citation": "26 CFR 1.501(r)-4",
                        "deadline": "2026-08-29",
                        "status": "open",
                    }
                ],
                "source": "test",
            },
            "one front selected",
        ),
    )


def test_on_document_added_writes_events_and_fronts(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_document_added(monkeypatch, hospital={"name": "Advocate", "nonprofit": True})

    s.create_case("c1", {"patient": {"state": "IL", "insured": False}})
    doc_id = s.add_document("c1", {"raw_text": "a bill", "type": None})

    asyncio.run(pipeline.on_document_added("c1", doc_id))

    case = s.get_case("c1")
    assert case["status"] == "strategy_ready"
    assert case["hospital"]["name"] == "Advocate"
    assert case["bill"]["amount_cents"] == 500_00
    assert len(case["fronts"]) == 1
    assert case["fronts"][0]["front"] == "charity_care"
    assert case["fronts"][0]["status"] == "open"

    events = s.list_events("c1")
    agents_seen = {e["agent"] for e in events}
    assert {"reader", "lookup", "clock", "strategist"} <= agents_seen


def test_on_document_added_missing_case_is_handled(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    result = asyncio.run(pipeline.on_document_added("nope", "docnope"))
    assert "error" in result


def test_approve_filing_rejects_when_front_missing(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    s.create_case("c1", {})
    result = asyncio.run(pipeline.approve_and_request_filing("c1", "ppdr"))
    assert result["ok"] is False
    assert "no such" not in result["reason"]  # message about the missing FRONT, not the case


def test_approve_filing_blocks_when_verifier_fails(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    published = _patch_no_op_pubsub(monkeypatch)
    s.create_case(
        "c1",
        {"patient": {"annual_income_cents": 100_000_00, "household_size": 2}},
    )
    s.upsert_front("c1", {"front": "charity_care", "applicable": True, "status": "open"})

    monkeypatch.setattr(
        verifier,
        "run",
        lambda case_id, case, front: async_fake_turn(
            {
                "case_id": case_id,
                "front": front,
                "passed": False,
                "issues": ["no income_proof document on file"],
            },
            "verification failed",
        ),
    )

    result = asyncio.run(pipeline.approve_and_request_filing("c1", "charity_care"))
    assert result["ok"] is False
    assert "income_proof" in result["reason"]
    # the gate held: no filing.requested was published
    assert all(topic != pipeline.config.TOPIC_FILING_REQUESTED for topic, _ in published)
    # front reverts to open, not stuck in "filing"
    case = s.get_case("c1")
    assert case["fronts"][0]["status"] == "open"


def test_approve_filing_runs_filer_when_verifier_passes(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    published = _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "ppdr", "applicable": True, "status": "open"})

    monkeypatch.setattr(
        verifier,
        "run",
        lambda case_id, case, front: async_fake_turn(
            {"case_id": case_id, "front": front, "passed": True, "issues": []}, "clear to file"
        ),
    )
    monkeypatch.setattr(
        filer,
        "run",
        lambda case_id, case, front, filing_id=None: async_fake_turn(
            {
                "case_id": case_id,
                "front": front,
                "filing_id": filing_id,
                "channel": "fax",
                "vendor_id": "SIMULATED-FAX-abc",
                "status": "sent",
                "simulated": True,
                "source": {},
            },
            "filed via fax",
        ),
    )

    result = asyncio.run(pipeline.approve_and_request_filing("c1", "ppdr"))
    assert result["ok"] is True
    case = s.get_case("c1")
    assert case["fronts"][0]["status"] == "filed"

    topics_published = [t for t, _ in published]
    assert pipeline.config.TOPIC_FILING_REQUESTED in topics_published
    assert pipeline.config.TOPIC_FILING_COMPLETED in topics_published

    events = s.list_events("c1")
    assert any(e["agent"] == "verifier" for e in events)
    assert any(e["agent"] == "filer" for e in events)
    assert any(e["action"] == "filing_requested" for e in events)


def test_approve_filing_rejects_front_not_applicable(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    s.create_case("c1", {})
    s.upsert_front("c1", {"front": "charity_care", "applicable": False, "status": "na"})
    result = asyncio.run(pipeline.approve_and_request_filing("c1", "charity_care"))
    assert result["ok"] is False
    assert "not applicable" in result["reason"]


def test_approve_filing_rejects_already_filed_front(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    s.create_case("c1", {})
    s.upsert_front("c1", {"front": "ppdr", "applicable": True, "status": "filed"})
    result = asyncio.run(pipeline.approve_and_request_filing("c1", "ppdr"))
    assert result["ok"] is False
