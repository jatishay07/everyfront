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
import contextvars
import copy

from _helpers import async_fake_turn, fake_turn, make_memory_store
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


def test_approve_filing_publishes_and_returns_without_running_filer(monkeypatch):
    """CHANGED 2026-08-25 (SWARM WO7, "approval times out clients"):
    `approve_and_request_filing` no longer runs Filer in-process -- it
    publishes `filing.requested` and returns as soon as Verifier passes.
    Filer now only runs from the `filing.requested` push subscriber
    (`finalize_filing`, exercised separately below) -- see that function's
    docstring for why the synchronous call routinely took over 6 minutes and
    blew every timeout in the chain."""
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

    def _boom(*a, **k):
        raise AssertionError("Filer must not run synchronously from approve_and_request_filing")

    monkeypatch.setattr(filer, "run", _boom)

    result = asyncio.run(pipeline.approve_and_request_filing("c1", "ppdr"))
    assert result["ok"] is True
    assert result["status"] == "filing_requested"
    case = s.get_case("c1")
    # not yet "filed" -- that only happens once finalize_filing (the async
    # push path) actually runs Filer.
    assert case["fronts"][0]["status"] == "filing"

    topics_published = [t for t, _ in published]
    assert pipeline.config.TOPIC_FILING_REQUESTED in topics_published
    assert pipeline.config.TOPIC_FILING_COMPLETED not in topics_published

    events = s.list_events("c1")
    assert any(e["agent"] == "verifier" for e in events)
    assert any(e["action"] == "filing_requested" for e in events)
    assert not any(e["agent"] == "filer" for e in events)


def _fake_filer_run(status="sent"):
    return lambda case_id, case, front, filing_id=None: async_fake_turn(
        {
            "case_id": case_id,
            "front": front,
            "filing_id": filing_id,
            "channel": "fax",
            "vendor_id": "SIMULATED-FAX-abc",
            "status": status,
            "simulated": True,
            "source": {},
        },
        "filed via fax",
    )


def test_finalize_filing_runs_filer_and_marks_front_filed(monkeypatch):
    """The async counterpart of the old in-process call: `finalize_filing` is
    what `services/agent-core/main.py`'s `/pubsub/filing-requested` push
    subscriber calls once `filing.requested` actually arrives."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    published = _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "ppdr", "applicable": True, "status": "filing"})
    monkeypatch.setattr(filer, "run", _fake_filer_run())

    result = asyncio.run(pipeline.finalize_filing("c1", "ppdr", "filing-1"))
    assert result["fact"]["status"] == "sent"
    case = s.get_case("c1")
    assert case["fronts"][0]["status"] == "filed"
    assert any(t == pipeline.config.TOPIC_FILING_COMPLETED for t, _ in published)
    events = s.list_events("c1")
    assert any(e["agent"] == "filer" and e["action"] == "file" for e in events)


def test_finalize_filing_reverts_front_to_open_when_filer_raises(monkeypatch):
    """BUG found live (persona 5 WO6 task 1): a deploy-time defect (agent-core's
    container was missing RELAY's pypdf/reportlab dependency) made every
    Filer call raise -- and the front was left stuck at "filing" forever,
    since nothing reverted it. Every retry after the underlying bug was fixed
    then failed anyway with "front ... is not open (status=filing)". The
    front must revert to "open" on a Filer failure so the case stays
    retryable, and the failure must still surface (not be swallowed as if it
    succeeded)."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "ppdr", "applicable": True, "status": "filing"})

    async def _boom(case_id, case, front, filing_id=None):
        raise ModuleNotFoundError("No module named 'pypdf'")

    monkeypatch.setattr(filer, "run", _boom)

    raised = False
    try:
        asyncio.run(pipeline.finalize_filing("c1", "ppdr", "filing-1"))
    except ModuleNotFoundError:
        raised = True
    assert raised  # the failure is not swallowed

    case = s.get_case("c1")
    assert case["fronts"][0]["status"] == "open"  # reverted, not stuck at "filing"

    events = s.list_events("c1")
    assert any(e["agent"] == "filer" and e["action"] == "file_failed" for e in events)

    # And the front is retryable now that it is back to open.
    monkeypatch.setattr(filer, "run", _fake_filer_run())
    result = asyncio.run(pipeline.finalize_filing("c1", "ppdr", "filing-2"))
    assert result["fact"]["status"] == "sent"
    assert s.get_case("c1")["fronts"][0]["status"] == "filed"


def test_approve_filing_retries_a_front_stuck_at_filing_with_no_completed_filing(monkeypatch):
    """BUG found live (SWARM WO7): ef-2026-0001's charity_care front got stuck
    at status "filing" from a timed-out approval, back when `filing.requested`
    went into an unread PULL queue (before infra/deploy.sh wired the push
    subscriptions) -- Filer never ran, and every later approve_filing call was
    rejected as "not open". A front that requested a filing and never got one
    must be retryable, not permanently wedged."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    published = _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "ppdr", "applicable": True, "status": "filing"})
    # no filings/ record exists for this front -- genuinely stuck, not filed.

    monkeypatch.setattr(
        verifier,
        "run",
        lambda case_id, case, front: async_fake_turn(
            {"case_id": case_id, "front": front, "passed": True, "issues": []}, "clear to file"
        ),
    )

    result = asyncio.run(pipeline.approve_and_request_filing("c1", "ppdr"))
    assert result["ok"] is True
    assert s.get_case("c1")["fronts"][0]["status"] == "filing"
    assert any(t == pipeline.config.TOPIC_FILING_REQUESTED for t, _ in published)


def test_approve_filing_refuses_retry_when_filing_already_completed(monkeypatch):
    """The flip side of the stuck-front fix above: a front at status "filing"
    that already HAS a real `filings/` record (its own `fronts[]` status
    patch just never landed, e.g. a crash between the two writes) must not be
    silently re-filed -- that would double-file the same front."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "ppdr", "applicable": True, "status": "filing"})
    s.create_filing({"case_id": "c1", "front": "ppdr", "status": "sent"})

    result = asyncio.run(pipeline.approve_and_request_filing("c1", "ppdr"))
    assert result["ok"] is False
    assert "already has a completed filing" in result["reason"]


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


# ---------------------------------------------------------------------------
# Defect #1: savings_found_cents must be real, auditable, and never invented.
# ---------------------------------------------------------------------------


def test_charity_erasure_is_the_full_bill_when_free_tier_applies():
    case = {
        "patient": {"annual_income_cents": 24_000_00, "household_size": 3, "state": "CA"},
        "bill": {"amount_cents": 6_400_00},
        "hospital": {"nonprofit": True, "free_care_max_fpl_pct": 400},
    }
    cents, explain = pipeline._charity_care_erasure_cents(case)
    assert cents == 6_400_00
    assert "400" in explain


def test_charity_erasure_is_zero_for_discounted_not_free():
    case = {
        # ~370% FPL for household of 2 -- above Advocate's real 250% free
        # threshold but at/below its 600% discounted threshold: "discounted",
        # not "free", so no single dollar figure is defensible (26 CFR
        # 1.501(r) leaves the discount percentage to the hospital's own
        # sliding scale, which this system does not model).
        "patient": {"annual_income_cents": 80_000_00, "household_size": 2, "state": "IL"},
        "bill": {"amount_cents": 1_000_00},
        "hospital": {
            "nonprofit": True,
            "free_care_max_fpl_pct": 250,
            "discounted_care_max_fpl_pct": 600,
        },
    }
    cents, explain = pipeline._charity_care_erasure_cents(case)
    assert cents == 0
    assert "discounted" in explain


def test_charity_erasure_is_zero_for_for_profit_hospital():
    case = {
        "patient": {"annual_income_cents": 1_00, "household_size": 1, "state": "IL"},
        "bill": {"amount_cents": 100_00},
        "hospital": {"nonprofit": False},
    }
    cents, explain = pipeline._charity_care_erasure_cents(case)
    assert cents == 0
    assert "for-profit" in explain


def test_charity_erasure_is_zero_with_insufficient_patient_data():
    case = {"patient": {}, "bill": {"amount_cents": 100_00}, "hospital": {"nonprofit": True}}
    cents, _explain = pipeline._charity_care_erasure_cents(case)
    assert cents == 0


def _patch_agents_for_cascade(monkeypatch, *, hospital, findings_total_cents, denial_check):
    monkeypatch.setattr(
        lookup,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "resolved": hospital is not None,
                "hospital": hospital,
                "ein": (hospital or {}).get("ein"),
                "citations": [],
                "note": "resolved" if hospital else "not found",
            },
            "hospital note",
        ),
    )
    monkeypatch.setattr(
        clock, "run", lambda case_id, case: async_fake_turn({"case_id": case_id, "deadlines": []})
    )
    monkeypatch.setattr(
        auditor,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "case_id": case_id,
                "findings": [],
                "total_findings_cents": findings_total_cents,
                "denial_check": denial_check,
                "source": {},
            }
        ),
    )
    monkeypatch.setattr(
        strategist,
        "run",
        lambda case_id, case: async_fake_turn({"case_id": case_id, "fronts": [], "source": "test"}),
    )


def test_denial_flag_is_never_left_none_when_the_check_actually_ran(monkeypatch):
    """Defect #4: outside PROOF's two hand-seeded denial fixtures, every real
    hospital record has no `fap_required_documents` -- the denial check runs
    but `insufficient_data` is True. That must still produce a definite
    `denial_flag` object (violated=False), never leave it at the store's bare
    `None` default."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_cascade(
        monkeypatch,
        hospital={"ein": "1", "name": "Test", "nonprofit": True},
        findings_total_cents=0,
        denial_check={
            "ran": True,
            "insufficient_data": True,
            "violation": False,
            "detail": "Cannot assess denial lawfulness: no FAP documentation list is on file.",
            "citation": "26 CFR 1.501(r)-4(b)(3)",
        },
    )
    s.create_case("c1", {"patient": {}, "bill": {}})
    case = s.get_case("c1")
    asyncio.run(pipeline._run_cascade("c1", case))

    updated = s.get_case("c1")
    assert updated["denial_flag"] is not None
    assert updated["denial_flag"]["violated"] is False


def test_denial_flag_stays_none_when_no_denial_letter_at_all(monkeypatch):
    """A case with nothing to check is a legitimate 'not applicable' null
    state, distinct from 'checked and found no violation.'"""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_cascade(
        monkeypatch,
        hospital=None,
        findings_total_cents=0,
        denial_check={"ran": False, "reason": "no denial_letter document on file"},
    )
    s.create_case("c1", {"patient": {}, "bill": {}})
    case = s.get_case("c1")
    asyncio.run(pipeline._run_cascade("c1", case))

    assert s.get_case("c1")["denial_flag"] is None


def test_savings_combines_audit_and_charity_erasure_without_double_counting(monkeypatch):
    """A granted free-tier charity-care determination erases the WHOLE bill,
    which already contains whatever the audit found -- so the reported
    savings is the max of the two, never their sum (never more than the
    bill itself)."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    hospital = {
        "ein": "94-0562680",
        "name": "Sutter",
        "nonprofit": True,
        "free_care_max_fpl_pct": 400,
    }
    _patch_agents_for_cascade(
        monkeypatch,
        hospital=hospital,
        findings_total_cents=245_00,  # smaller than the full bill
        denial_check={"ran": False, "reason": "no denial_letter document on file"},
    )
    s.create_case(
        "c1",
        {
            "patient": {"annual_income_cents": 24_000_00, "household_size": 3, "state": "CA"},
            "bill": {"amount_cents": 2_625_00},
        },
    )
    case = s.get_case("c1")
    asyncio.run(pipeline._run_cascade("c1", case))

    updated = s.get_case("c1")
    assert updated["savings_found_cents"] == 2_625_00  # the bill, not 2_625_00 + 245_00
    assert updated["audit_findings_cents"] == 245_00  # the audit component alone is untouched

    events = s.list_events("c1")
    assert any(e["action"] == "savings_summary" for e in events)


def test_savings_is_audit_only_when_charity_care_does_not_apply(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_cascade(
        monkeypatch,
        hospital={"ein": "1", "name": "Test", "nonprofit": False},
        findings_total_cents=210_00,
        denial_check={"ran": False, "reason": "no denial_letter document on file"},
    )
    s.create_case("c1", {"patient": {}, "bill": {"amount_cents": 900_00}})
    case = s.get_case("c1")
    asyncio.run(pipeline._run_cascade("c1", case))

    assert s.get_case("c1")["savings_found_cents"] == 210_00


def test_savings_found_cents_is_idempotent_across_repeated_cascade_runs(monkeypatch):
    """DEFECT (persona 5 WO6 task 4): this used to be
    `(case.get("savings_found_cents") or 0) + combined_cents` -- an
    ACCUMULATION, not a recomputation. `_run_cascade` can genuinely run more
    than once for the same case (a redelivered Pub/Sub `case.document.added`,
    §2.3's own "every handler must tolerate redelivery," or simply a second
    document arriving via `on_document_added`'s one-cascade-per-document
    path) -- each run already recomputes the CURRENT total from every
    document/field on file, so re-running it with nothing new must report
    the same number, not double it.
    """
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_cascade(
        monkeypatch,
        hospital={"ein": "1", "name": "Test", "nonprofit": False},
        findings_total_cents=210_00,
        denial_check={"ran": False, "reason": "no denial_letter document on file"},
    )
    s.create_case("c1", {"patient": {}, "bill": {"amount_cents": 900_00}})

    case = s.get_case("c1")
    asyncio.run(pipeline._run_cascade("c1", case))
    assert s.get_case("c1")["savings_found_cents"] == 210_00
    assert s.get_case("c1")["audit_findings_cents"] == 210_00

    # Simulate redelivery / a second document triggering the same cascade
    # again with the identical audit facts -- the number must not double.
    case = s.get_case("c1")
    asyncio.run(pipeline._run_cascade("c1", case))
    assert s.get_case("c1")["savings_found_cents"] == 210_00
    assert s.get_case("c1")["audit_findings_cents"] == 210_00


# ---------------------------------------------------------------------------
# Defect #3: batch document processing runs Reader concurrently and the
# Lookup->Clock/Auditor->Strategist cascade exactly once, not once per doc.
# ---------------------------------------------------------------------------


def test_process_case_documents_runs_readers_concurrently_and_cascade_once(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)

    reader_calls = []

    async def fake_reader_run(case_id, doc_id, text, hint=None):
        reader_calls.append(doc_id)
        return {
            "fact": {
                "case_id": case_id,
                "doc_id": doc_id,
                "label": "bill",
                "extraction": {"amount_cents": 100_00},
                "citations": [],
            },
            "answer": "ok",
            "trace": [],
            "model": "test",
            "error": None,
        }

    monkeypatch.setattr(reader, "run", fake_reader_run)

    cascade_calls = []
    orig_run_cascade = pipeline._run_cascade

    async def counting_cascade(case_id, case, pass_evidence=None):
        cascade_calls.append(case_id)
        return await orig_run_cascade(case_id, case, pass_evidence)

    monkeypatch.setattr(pipeline, "_run_cascade", counting_cascade)
    _patch_agents_for_cascade(
        monkeypatch,
        hospital=None,
        findings_total_cents=0,
        denial_check={"ran": False, "reason": "no denial_letter document on file"},
    )

    s.create_case("c1", {"patient": {}, "bill": {}})
    doc_ids = [s.add_document("c1", {"raw_text": "doc", "type": "bill"}) for _ in range(3)]

    result = asyncio.run(pipeline.process_case_documents("c1", doc_ids))

    assert sorted(reader_calls) == sorted(doc_ids)  # every document was read
    assert len(cascade_calls) == 1  # exactly one cascade for all three documents
    assert "readers" in result
    assert set(result["readers"]) == set(doc_ids)


def test_process_case_documents_missing_case_is_handled(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    result = asyncio.run(pipeline.process_case_documents("nope", ["d1"]))
    assert "error" in result


def test_two_concurrent_filings_on_one_case_do_not_clobber_each_other(monkeypatch):
    """THE regression test for the blocker PROOF found live (PR #37).

    Since filing went asynchronous (ca9fd40), each approved front settles in
    its own `/pubsub/filing-requested` push handler, and nothing serializes
    them per case. `run_filer` used to write the WHOLE `fronts[]` array back
    from the snapshot it read before calling Filer -- so whichever handler
    finished second reverted its sibling's already-"filed" status, leaving a
    front showing open/filing with a real, sent `filings/` record underneath.
    Reproduced 3-for-3 live on ef-2026-0001, -0003 and -0007, and it made
    `make demo-run` exit 1.

    The barrier here makes that interleaving deterministic instead of lucky:
    neither Filer returns until BOTH are holding the pre-filing snapshot.
    """
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    published = _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "ppdr", "applicable": True, "status": "filing"})
    s.upsert_front("c1", {"front": "charity_care", "applicable": True, "status": "filing"})

    async def _drive():
        barrier = asyncio.Barrier(2)

        async def _filer_that_waits_for_its_sibling(case_id, case, front, filing_id=None):
            await barrier.wait()
            return await _fake_filer_run()(case_id, case, front, filing_id=filing_id)

        monkeypatch.setattr(filer, "run", _filer_that_waits_for_its_sibling)
        await asyncio.gather(
            pipeline.finalize_filing("c1", "ppdr", "filing-1"),
            pipeline.finalize_filing("c1", "charity_care", "filing-2"),
        )

    asyncio.run(_drive())

    assert {f["front"]: f["status"] for f in s.get_case("c1")["fronts"]} == {
        "ppdr": "filed",
        "charity_care": "filed",
    }
    completed = [p for t, p in published if t == pipeline.config.TOPIC_FILING_COMPLETED]
    assert {p["filing_id"] for p in completed} == {"filing-1", "filing-2"}


def test_reanalysis_triggered_by_a_filing_does_not_reopen_the_filed_front(monkeypatch):
    """The second cause of the same live symptom, at the pipeline level.

    The Filer stores its generated PDF as a case document; that publishes
    `case.document.added`; that re-runs the hierarchy. Strategist's fronts
    come back at "open" because `select_fronts` is pure and knows nothing
    about filings -- and used to be written straight over "filed".

    Live trace, ef-2026-0007 (2026-08-26): audit filed 08:40:46, charity_care
    08:40:51, re-analyses at 08:40:50 and 08:40:52-54 reset both to "open"
    while `filings/` held three real "sent" records.
    """
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_document_added(monkeypatch, hospital={"name": "Advocate", "nonprofit": True})

    s.create_case("c1", {"patient": {"state": "IL", "insured": False}})
    doc_id = s.add_document("c1", {"raw_text": "a bill", "type": None})
    asyncio.run(pipeline.on_document_added("c1", doc_id))
    assert s.get_case("c1")["fronts"][0]["status"] == "open"

    # The front is approved and filed...
    s.set_front_status("c1", "charity_care", "filed")

    # ...and the Filer's own generated PDF lands as a document, re-analysing.
    generated = s.add_document("c1", {"type": "generated_application", "raw_text": "a bill"})
    asyncio.run(pipeline.on_document_added("c1", generated))

    front = s.get_case("c1")["fronts"][0]
    assert front["status"] == "filed", "re-analysis reopened a front that was already filed"
    assert front["reason"] == "nonprofit hospital"  # analysis still owns everything else


def test_a_generated_filing_does_not_re_run_the_analysis(monkeypatch):
    """FIX 1 (the re-analysis storm). The Filer stores every filled form it
    sends as a case document, so filing three fronts adds three documents and
    -- before this -- three more full Lookup/Clock/Auditor/Strategist
    cascades, each re-logging its entire event set. Measured live against the
    deployed system on 2026-08-26: `ef-2026-0007`'s flagship audit finding
    ("80053 billed at $220.00 vs the hospital's attested cash price...") was
    in the activity feed 14 times, `ef-2026-0001`'s `lookup/resolve_hospital`
    6 times.

    A letter we produced is not new evidence about the bill. The
    counter-property -- a genuinely new INCOMING document must still re-run
    everything -- is
    `test_a_newly_uploaded_income_proof_still_re_runs_the_analysis` below;
    the two have to hold together or this fix has broken the product.
    """
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_document_added(monkeypatch, hospital={"name": "Advocate", "nonprofit": True})

    s.create_case("c1", {"patient": {"state": "IL", "insured": False}})
    bill = s.add_document("c1", {"raw_text": "a bill", "type": None})
    asyncio.run(pipeline.on_document_added("c1", bill))
    events_after_the_bill = len(s.list_events("c1"))

    for doc_type in ("generated_application", "generated_letter"):
        generated = s.add_document("c1", {"type": doc_type, "gcs_uri": "gs://x/y.pdf"})
        result = asyncio.run(pipeline.on_document_added("c1", generated))
        assert "skipped" in result, result

    assert len(s.list_events("c1")) == events_after_the_bill, (
        "filing re-ran the analysis and re-logged the whole audit trail"
    )


def test_a_newly_uploaded_income_proof_still_re_runs_the_analysis(monkeypatch):
    """The guard on FIX 1: only documents THIS SYSTEM generated are inert. A
    document that genuinely arrives later -- an income proof the patient
    uploads, a denial letter that shows up in week three -- must still re-run
    the whole cascade. That is the product working as designed, not the bug.
    """
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_document_added(monkeypatch, hospital={"name": "Advocate", "nonprofit": True})

    s.create_case("c1", {"patient": {"state": "IL", "insured": False}})
    bill = s.add_document("c1", {"raw_text": "a bill", "type": None})
    asyncio.run(pipeline.on_document_added("c1", bill))

    cascades = []
    monkeypatch.setattr(pipeline, "_run_cascade", _counting_cascade(cascades))
    later = s.add_document("c1", {"raw_text": "2025 W-2", "type": "income_proof"})
    result = asyncio.run(pipeline.on_document_added("c1", later))

    assert "skipped" not in result
    assert cascades == ["c1"], "a genuinely new document no longer re-runs the analysis"


def _counting_cascade(sink: list):
    async def _cascade(case_id, case, pass_evidence=None):
        sink.append(case_id)
        return {}

    return _cascade


def test_a_batch_of_only_generated_documents_runs_no_cascade(monkeypatch):
    """Same rule on the batch route (`/internal/process_documents`)."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    cascades = []
    monkeypatch.setattr(pipeline, "_run_cascade", _counting_cascade(cascades))

    s.create_case("c1", {"patient": {}})
    generated = s.add_document("c1", {"type": "generated_letter", "gcs_uri": "gs://x/y.pdf"})
    result = asyncio.run(pipeline.process_case_documents("c1", [generated]))

    assert "skipped" in result
    assert cascades == []


def _cascade_twice(monkeypatch, s, **kwargs):
    """Run the analysis cascade twice over an unchanged case -- exactly what a
    Pub/Sub redelivery, or a second document, does."""
    _patch_agents_for_document_added(monkeypatch, **kwargs)
    s.create_case("c1", {"patient": {"state": "IL", "insured": False}})
    doc_id = s.add_document("c1", {"raw_text": "a bill", "type": None})
    asyncio.run(pipeline.on_document_added("c1", doc_id))
    first = s.list_events("c1")
    asyncio.run(pipeline.on_document_added("c1", doc_id))
    return first, s.list_events("c1")


def test_re_analysis_does_not_re_log_facts_the_audit_log_already_holds(monkeypatch):
    """FIX 2 (the audit log is idempotent). §2.3 requires every handler to
    tolerate redelivery, and §3.1 gives `events/{event_id}` an explicit id for
    exactly this. Before this, a second run of the same analysis appended its
    whole event set again: 137 events for 36 distinct facts on ef-2026-0007,
    50 for 22 on ef-2026-0002.
    """
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)

    first, second = _cascade_twice(monkeypatch, s, hospital={"name": "Advocate", "nonprofit": True})

    assert [e["event_id"] for e in second] == [e["event_id"] for e in first]
    assert len(second) == len(first), (
        f"re-analysis re-logged {len(second) - len(first)} facts the feed already had"
    )


def test_a_narration_that_changes_does_not_make_a_new_event(monkeypatch):
    """The specific reason content-hashing the DETAIL would not have worked.
    `lookup/resolve_hospital` logs the model's own sentence, and the model
    words it differently every run -- ef-2026-0001 carries six differently
    worded rows saying one thing. The identity is the resolved hospital, not
    the sentence.
    """
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_document_added(monkeypatch, hospital={"name": "Advocate", "nonprofit": True})
    s.create_case("c1", {"patient": {"state": "IL", "insured": False}})
    doc_id = s.add_document("c1", {"raw_text": "a bill", "type": None})
    asyncio.run(pipeline.on_document_added("c1", doc_id))

    monkeypatch.setattr(
        lookup,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "resolved": True,
                "hospital": {"name": "Advocate", "nonprofit": True},
                "citations": [],
                "note": "resolved",
            },
            "I have identified the facility as Advocate Christ Medical Center.",
        ),
    )
    asyncio.run(pipeline.on_document_added("c1", doc_id))

    resolved = [e for e in s.list_events("c1") if e["action"] == "resolve_hospital"]
    assert len(resolved) == 1, f"the same hospital was logged {len(resolved)} times"
    assert resolved[0]["detail"] == "hospital note"  # the first narration stands


def test_two_findings_that_read_identically_are_still_two_rows(monkeypatch):
    """THE TRAP on the other side of FIX 2, and it is not hypothetical:
    `rules.audit._cash_price_findings` runs PER LINE, so the same overcharged
    code on two lines of one bill produces two findings whose kind, prose and
    citation are byte-identical. They are two real overcharges. Deduping on
    (agent, action, detail) would have silently halved the finding count while
    the dashboard's dollar total -- computed separately, from the findings
    themselves -- kept the full amount. A judge doing arithmetic on screen
    would have caught the discrepancy.
    """
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_document_added(monkeypatch, hospital={"name": "Advocate", "nonprofit": True})
    twin = {
        "kind": "cash_price_delta",
        "detail": "80053 billed at $220.00 vs the hospital's attested cash price of $110.00",
        "codes": ["80053"],
        "amount_cents": 11000,
        "citation": "45 CFR 180.50",
    }
    monkeypatch.setattr(
        auditor,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "case_id": case_id,
                "findings": [{**twin, "line_refs": [2]}, {**twin, "line_refs": [7]}],
                "total_findings_cents": 22000,
                "line_items_examined": 9,
                "denial_check": {"ran": False, "reason": "no denial letter"},
                "source": {},
            },
            "two findings",
        ),
    )
    s.create_case("c1", {"patient": {"state": "IL", "insured": False}})
    doc_id = s.add_document("c1", {"raw_text": "a bill", "type": None})
    asyncio.run(pipeline.on_document_added("c1", doc_id))
    asyncio.run(pipeline.on_document_added("c1", doc_id))

    findings = [e for e in s.list_events("c1") if e["action"].startswith("audit_finding:")]
    assert len(findings) == 2, (
        f"{len(findings)} rows for two distinct overcharges on two distinct lines"
    )


def test_a_deadline_recomputed_from_a_corrected_date_is_a_new_row(monkeypatch):
    """The constraint FIX 2 must not violate: a fact that legitimately CHANGES
    on re-analysis has to stay visible in the feed. Nothing is rewritten -- the
    old row stands next to the new one, which is what an audit log is for.
    """
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_document_added(monkeypatch, hospital={"name": "Advocate", "nonprofit": True})
    s.create_case("c1", {"patient": {"state": "IL", "insured": False}})
    doc_id = s.add_document("c1", {"raw_text": "a bill", "type": None})
    asyncio.run(pipeline.on_document_added("c1", doc_id))

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
                        "due": "2026-09-30",
                        "basis_date": "2026-02-02",
                        "basis_field": "first_statement_date",
                        "citation": "26 CFR 1.501(r)-4(b)(1)(iv)",
                        "days": 240,
                        "explain": "due 2026-09-30",
                    }
                ],
            },
            "one deadline",
        ),
    )
    asyncio.run(pipeline.on_document_added("c1", doc_id))

    deadlines = [e["detail"] for e in s.list_events("c1") if e["action"] == "compute_deadline"]
    assert deadlines == ["due 2026-08-29", "due 2026-09-30"], deadlines


# ==========================================================================
# SWARM WO8 -- TASK 2: the `simulated` flag must survive LLM narration.
#
# `run_filer` used to log `filer_turn["answer"] or (<fallback saying
# SIMULATED>)`. The fallback is only ever reached when the model fails to
# narrate at all, so in every healthy run the word "SIMULATED" was never
# written -- the model's sentence (which is told the channel, vendor id and
# status, and nothing about simulation) replaced the entire line. Live, every
# filing in `filings/` is a fake-vendor send and not one `filer.file` event
# says so.
# ==========================================================================


def _filer_run_returning(*, simulated: bool, answer: str):
    async def _run(case_id, case, front, filing_id=None):
        return {
            "fact": {
                "case_id": case_id,
                "front": front,
                "filing_id": filing_id,
                "channel": "mail",
                "vendor_id": "fake-ltr_1f0ae92e7adb44e3946e",
                "status": "sent",
                "simulated": simulated,
                "form_id": "records_request_letter",
                "doc_id": "d1",
                "gcs_uri": None,
                "real_destination": "Test Hospital",
            },
            "filing_id": filing_id,
            "pdf": b"%PDF-fake",
            "answer": answer,
            "trace": [],
            "model": "test-model",
            "error": None,
        }

    return _run


#: The kind of sentence the Filer's own model actually produces -- fluent,
#: accurate about channel/vendor/status, and silent about simulation.
_LLM_NARRATION = (
    "The audit filing was sent by mail with vendor ID fake-ltr_1f0ae92e7adb44e3946e "
    "and its status is sent."
)


def _file_event_detail(s, case_id="c1"):
    return next(
        e["detail"]
        for e in s.list_events(case_id)
        if e["agent"] == "filer" and e["action"] == "file"
    )


def test_the_filing_event_says_simulated_even_when_the_llm_narrates(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "filing"})
    monkeypatch.setattr(filer, "run", _filer_run_returning(simulated=True, answer=_LLM_NARRATION))

    asyncio.run(pipeline.finalize_filing("c1", "audit", "filing-1"))

    detail = _file_event_detail(s)
    assert "SIMULATED" in detail, detail
    # The narration is kept -- it is presentation, appended AFTER the fact,
    # never in place of it.
    assert _LLM_NARRATION in detail


def test_the_filing_event_says_live_only_when_the_send_really_was(monkeypatch):
    """Do not fabricate the opposite either."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "filing"})
    monkeypatch.setattr(filer, "run", _filer_run_returning(simulated=False, answer=_LLM_NARRATION))

    asyncio.run(pipeline.finalize_filing("c1", "audit", "filing-1"))

    detail = _file_event_detail(s)
    assert "[LIVE]" in detail
    assert "SIMULATED" not in detail


def test_a_filer_fact_with_no_simulated_key_is_narrated_as_simulated(monkeypatch):
    """An unknown provenance is not evidence of a real send -- the same
    `.get(key, True)` rule the store and the API both apply."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "filing"})

    async def _run(case_id, case, front, filing_id=None):
        turn = await _filer_run_returning(simulated=True, answer="")(
            case_id, case, front, filing_id
        )
        del turn["fact"]["simulated"]
        return turn

    monkeypatch.setattr(filer, "run", _run)
    asyncio.run(pipeline.finalize_filing("c1", "audit", "filing-1"))

    assert "SIMULATED" in _file_event_detail(s)


# ==========================================================================
# SWARM WO8 -- TASK 1: Calendar (WO5) and Drive (WO6) wired into the pipeline.
#
# Both modules shipped in packages/delivery in PR #35, both fully unit-tested,
# and until this change NOTHING in services/agent-core called either of them.
# ==========================================================================


def _no_google_credentials(monkeypatch):
    for var in (
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)


def _with_google_credentials(monkeypatch):
    """Pretend the demo account's token is minted, without minting one."""
    monkeypatch.setattr(pipeline.delivery_bridge, "google_sync_configured", lambda: True)


def _cascade_case(s, monkeypatch, case_id="c1"):
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    _patch_agents_for_document_added(monkeypatch, hospital={"name": "Advocate", "nonprofit": True})
    s.create_case(case_id, {"patient": {"name": "Maria G.", "state": "CA", "insured": False}})
    return s.add_document(case_id, {"raw_text": "a bill", "type": None})


def test_every_computed_deadline_is_written_to_google_calendar(monkeypatch):
    """§4 persona 4 WO5, the wiring half. The Clock's deadlines reach
    `delivery.calendar_sync.sync_deadlines` with the case, a patient label and
    the serialized deadline dicts -- exactly the shape that module documents
    itself as accepting."""
    s = make_memory_store()
    doc_id = _cascade_case(s, monkeypatch)
    _with_google_credentials(monkeypatch)
    calls = []

    def _sync(case_id, patient_label, deadlines, **kw):
        calls.append((case_id, patient_label, deadlines))
        return [
            {
                "front": d["front"],
                "name": d["name"],
                "event_id": f"evt-{d['front']}",
                "due": d["due"],
            }
            for d in deadlines
            if d.get("due")
        ]

    monkeypatch.setattr(pipeline.delivery_bridge, "sync_deadlines", _sync)

    asyncio.run(pipeline.on_document_added("c1", doc_id))

    assert len(calls) == 1, "sync_deadlines was never called by the pipeline"
    case_id, patient_label, deadlines = calls[0]
    assert case_id == "c1"
    assert patient_label == "Maria G."
    assert [d["front"] for d in deadlines] == ["charity_care"]
    assert deadlines[0]["citation"] == "26 CFR 1.501(r)-4(b)(1)(iv)"

    # §3.1: logged to cases/{id}/events under `clock` -- the agent that owns
    # deadlines, and a member of §3.1's CLOSED agent enum (an invented name
    # renders a blank avatar in CANVAS's Record<AgentName, ...> lookup).
    event = next(e for e in s.list_events("c1") if e["action"] == "calendar_sync")
    assert event["agent"] == "clock"
    assert "2026-08-29" in event["detail"]
    assert event["citations"] == ["26 CFR 1.501(r)-4(b)(1)(iv)"]


def test_calendar_sync_does_not_duplicate_its_event_row_on_re_analysis(monkeypatch):
    """The activity feed was drowning in duplicates until today, and four
    copies of every deadline on the demo Calendar is the same defect wearing
    a different hat. The Calendar side of idempotency is `calendar_sync`'s
    own stable event ids (it UPDATEs, falling back to insert on 404); the feed
    side is this: an unchanged sync must not add a second identical row."""
    s = make_memory_store()
    doc_id = _cascade_case(s, monkeypatch)
    _with_google_credentials(monkeypatch)
    synced = [
        {
            "front": "charity_care",
            "name": "Charity care application",
            "event_id": "evt-1",
            "due": "2026-08-29",
        }
    ]
    calls = []
    monkeypatch.setattr(
        pipeline.delivery_bridge,
        "sync_deadlines",
        lambda *a, **kw: calls.append(a) or synced,
    )

    asyncio.run(pipeline.on_document_added("c1", doc_id))
    asyncio.run(pipeline.on_document_added("c1", doc_id))

    # Re-synced (so a moved deadline would be corrected upstream)...
    assert len(calls) == 2
    # ...but the audit log records the same fact once.
    rows = [e for e in s.list_events("c1") if e["action"] == "calendar_sync"]
    assert len(rows) == 1, [r["detail"] for r in rows]


def test_calendar_event_ids_are_stable_so_a_re_sync_updates_instead_of_duplicating():
    """agent-core depends on this property of RELAY's module; assert it from
    this side too, so a change over there surfaces as a failure over here."""
    from delivery.calendar_sync import _stable_event_id

    d = {"front": "ppdr", "name": "PPDR window", "due": "2026-09-01"}
    assert _stable_event_id("c1", d) == _stable_event_id("c1", dict(d))
    assert _stable_event_id("c1", d) != _stable_event_id("c2", d)


def test_calendar_sync_no_ops_cleanly_with_no_oauth_token_and_changes_nothing(monkeypatch):
    """THE PATH THAT ACTUALLY RUNS TODAY, and until a human mints a token per
    infra/OAUTH.md. `MissingCredentialsError`'s own docstring: callers must
    degrade gracefully. So the cascade must complete unchanged, log something
    TRUE about why nothing was written, and touch nothing else."""
    s = make_memory_store()
    doc_id = _cascade_case(s, monkeypatch)
    _no_google_credentials(monkeypatch)
    calls = []
    monkeypatch.setattr(
        pipeline.delivery_bridge,
        "sync_deadlines",
        lambda *a, **kw: calls.append(a),
    )

    asyncio.run(pipeline.on_document_added("c1", doc_id))

    assert calls == [], "reached the Google client with no credentials configured"
    # The analysis is completely unaffected.
    case = s.get_case("c1")
    assert case["status"] == "strategy_ready"
    assert [f["front"] for f in case["fronts"]] == ["charity_care"]
    assert any(e["action"] == "compute_deadline" for e in s.list_events("c1"))
    # And the record says so, in words that are true.
    skipped = next(e for e in s.list_events("c1") if e["action"] == "calendar_sync_skipped")
    assert skipped["agent"] == "clock"
    assert "OAuth refresh token is not configured" in skipped["detail"]
    assert not any(e["action"] == "calendar_sync" for e in s.list_events("c1"))


def test_a_failing_calendar_never_fails_the_analysis(monkeypatch):
    """Not just missing credentials -- an expired token, a 500 from Google, or
    `google-api-python-client` absent from the image."""
    s = make_memory_store()
    doc_id = _cascade_case(s, monkeypatch)
    _with_google_credentials(monkeypatch)

    def _boom(*a, **kw):
        raise ModuleNotFoundError("No module named 'googleapiclient'")

    monkeypatch.setattr(pipeline.delivery_bridge, "sync_deadlines", _boom)

    result = asyncio.run(pipeline.on_document_added("c1", doc_id))

    assert "error" not in result
    assert s.get_case("c1")["status"] == "strategy_ready"
    failed = next(e for e in s.list_events("c1") if e["action"] == "calendar_sync_failed")
    assert failed["agent"] == "clock"
    assert "ModuleNotFoundError" in failed["detail"]


def test_the_approval_path_never_touches_google(monkeypatch):
    """Filing went asynchronous because `approve_filing` took 6+ minutes.
    Neither sync belongs anywhere a human is waiting on an HTTP response."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "open"})
    monkeypatch.setattr(
        verifier,
        "run",
        lambda case_id, case, front: async_fake_turn({"passed": True, "issues": []}, "ok"),
    )

    def _never(*a, **kw):
        raise AssertionError("the approval path reached a Google API")

    monkeypatch.setattr(pipeline.delivery_bridge, "google_sync_configured", _never)
    monkeypatch.setattr(pipeline.delivery_bridge, "sync_deadlines", _never)
    monkeypatch.setattr(pipeline.delivery_bridge, "mirror_case_filings", _never)

    result = asyncio.run(pipeline.approve_and_request_filing("c1", "audit"))
    assert result["ok"] is True


def test_each_filing_is_mirrored_to_the_cases_drive_folder(monkeypatch):
    """§4 persona 4 WO6, the wiring half."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    published = _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "filing"})
    monkeypatch.setattr(filer, "run", _filer_run_returning(simulated=True, answer="filed"))
    _with_google_credentials(monkeypatch)
    calls = []

    def _mirror(case_id, filings, **kw):
        # Captured AT CALL TIME: the filing must already be durable and
        # announced before a slow Google API is ever touched.
        calls.append(
            {
                "case_id": case_id,
                "filings": filings,
                "front_status": s.get_case(case_id)["fronts"][0]["status"],
                "published": list(published),
            }
        )
        return {"case_folder_id": "folder-1", "files": [{"file_id": "f1"}]}

    monkeypatch.setattr(pipeline.delivery_bridge, "mirror_case_filings", _mirror)

    asyncio.run(pipeline.finalize_filing("c1", "audit", "filing-1"))

    assert len(calls) == 1, "mirror_case_filings was never called by the pipeline"
    call = calls[0]
    assert call["case_id"] == "c1"
    assert call["filings"] == [
        {
            "filename": "audit_records_request_letter.pdf",
            "pdf_bytes": b"%PDF-fake",
            "front": "audit",
        }
    ]
    assert call["front_status"] == "filed"
    assert any(t == pipeline.config.TOPIC_FILING_COMPLETED for t, _ in call["published"])

    event = next(e for e in s.list_events("c1") if e["action"] == "drive_mirror")
    assert event["agent"] == "filer"
    assert "audit_records_request_letter.pdf" in event["detail"]


def test_the_drive_filename_carries_no_filing_id_so_a_re_file_updates(monkeypatch):
    """`mirror_case_filings` updates a same-named file inside the case folder
    instead of creating a second one, so the advocate's folder holds ONE
    current document per front rather than four copies of the same letter."""
    assert pipeline.delivery_bridge.drive_filename("ppdr", "cms_ppdr") == "ppdr_cms_ppdr.pdf"
    assert pipeline.delivery_bridge.drive_filename(
        "ppdr", "cms_ppdr"
    ) == pipeline.delivery_bridge.drive_filename("ppdr", "cms_ppdr")


def test_drive_mirror_no_ops_cleanly_with_no_oauth_token(monkeypatch):
    """The path that actually runs today. The filing itself must be entirely
    unaffected -- recorded, front filed, `filing.completed` published."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    published = _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "filing"})
    monkeypatch.setattr(filer, "run", _filer_run_returning(simulated=True, answer="filed"))
    _no_google_credentials(monkeypatch)
    calls = []
    monkeypatch.setattr(
        pipeline.delivery_bridge,
        "mirror_case_filings",
        lambda *a, **kw: calls.append(a),
    )

    result = asyncio.run(pipeline.finalize_filing("c1", "audit", "filing-1"))

    assert calls == [], "reached the Google client with no credentials configured"
    assert result["fact"]["status"] == "sent"
    assert s.get_case("c1")["fronts"][0]["status"] == "filed"
    assert any(t == pipeline.config.TOPIC_FILING_COMPLETED for t, _ in published)
    skipped = next(e for e in s.list_events("c1") if e["action"] == "drive_mirror_skipped")
    assert skipped["agent"] == "filer"
    assert "OAuth refresh token is not configured" in skipped["detail"]


def test_a_failing_drive_mirror_never_fails_a_completed_filing(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _patch_no_op_pubsub(monkeypatch)
    s.create_case("c1", {"hospital": {"name": "Test Hospital"}})
    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "filing"})
    monkeypatch.setattr(filer, "run", _filer_run_returning(simulated=True, answer="filed"))
    _with_google_credentials(monkeypatch)

    def _boom(*a, **kw):
        raise TimeoutError("Drive took too long")

    monkeypatch.setattr(pipeline.delivery_bridge, "mirror_case_filings", _boom)

    result = asyncio.run(pipeline.finalize_filing("c1", "audit", "filing-1"))

    assert result["fact"]["status"] == "sent"
    assert s.get_case("c1")["fronts"][0]["status"] == "filed"
    failed = next(e for e in s.list_events("c1") if e["action"] == "drive_mirror_failed")
    assert failed["agent"] == "filer"
    assert "TimeoutError" in failed["detail"]


# ==========================================================================
# The merge step: documents[].extracted -> canonical patient/bill
#
# THE DEFECT (measured live, case `case-1a0412ccfef90917`, 2026-08-26): the
# pipeline never carried a document's extraction up into `case["patient"]` /
# `case["bill"]`, which is where select_fronts/compute_deadlines/
# screen_eligibility read. A real emailed bill whose three PDFs all classified
# and extracted perfectly still reached the Strategist as an empty patient and
# a line-item-less bill. The precedence rules themselves are covered in
# test_factmerge.py; these tests are about the STEP being wired into both
# entry points, writing to Firestore, and saying so in the audit trail.
# ==========================================================================

_MERGE_EXTRACTIONS: dict[str, tuple[str, dict]] = {
    "d-bill": (
        "itemized_bill",
        {
            "provider_name": "Sutter Bay Hospitals",
            "amount_cents": 262500,
            "service_date": "2026-05-01",
            "first_statement_date": "2026-06-05",
            "hospital_ein": "94-0562680",
            "state": "CA",
            "patient_name": "Jordan Alvarez",
            "line_items": [
                {"code": "80053", "description": "CMP", "units": 1, "charge_cents": 21000},
                {"code": "80053", "description": "CMP", "units": 1, "charge_cents": 21000},
            ],
        },
    ),
    "d-gfe": (
        "gfe",
        {
            "provider_name": "Sutter Bay Hospitals",
            "gfe_amount_cents": 192500,
            "hospital_ein": "94-0562680",
            "state": "CA",
            "uninsured_self_pay": True,
        },
    ),
    "d-income": ("income_proof", {"annual_income_cents": 3200000, "is_income_proof": True}),
}


def _patch_reader_with_real_extractions(monkeypatch, extractions):
    def _run(case_id, doc_id, text, hint=None):
        label, extraction = extractions[doc_id]
        return async_fake_turn(
            {
                "case_id": case_id,
                "doc_id": doc_id,
                "label": label,
                "gemma_raw": label,
                "gemma_error": None,
                "extraction": extraction,
                "citations": [],
            },
            f"classified as {label}",
        )

    monkeypatch.setattr(reader, "run", _run)


def _patch_cascade_agents(monkeypatch, *, hospital=None):
    monkeypatch.setattr(
        lookup,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "resolved": hospital is not None,
                "hospital": hospital,
                "ein": (hospital or {}).get("ein"),
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
            {"case_id": case_id, "deadlines": []}, "no deadlines"
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
                "line_items_examined": 2,
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
            {"case_id": case_id, "fronts": [], "source": "test"}, "no fronts"
        ),
    )


def _seed_merge_case(store_, monkeypatch, *, hospital=None, patient=None, extractions=None):
    """One case, three unread documents, and every agent but the merge faked."""
    extractions = copy.deepcopy(_MERGE_EXTRACTIONS) if extractions is None else extractions
    _patch_store(monkeypatch, store_)
    _patch_no_op_pubsub(monkeypatch)
    _patch_reader_with_real_extractions(monkeypatch, extractions)
    _patch_cascade_agents(monkeypatch, hospital=hospital)
    store_.create_case("c1", {"patient": patient or {}, "bill": {}})
    for doc_id in extractions:
        store_.add_document_if_absent("c1", doc_id, {"type": "", "raw_text": "text"})


def test_document_facts_reach_the_case_patient_and_bill(monkeypatch):
    """The live defect, at the pipeline level: three documents in, one
    populated `patient` and one `bill` with line items out."""
    s = make_memory_store()
    _seed_merge_case(s, monkeypatch)

    asyncio.run(pipeline.process_case_documents("c1", list(_MERGE_EXTRACTIONS)))

    case = s.get_case("c1")
    assert case["bill"]["line_items"] and len(case["bill"]["line_items"]) == 2
    assert case["bill"]["amount_cents"] == 262500
    assert case["bill"]["gfe_amount_cents"] == 192500
    assert case["bill"]["has_itemized_bill"] is True
    assert case["patient"]["name"] == "Jordan Alvarez"
    assert case["patient"]["state"] == "CA"
    assert case["patient"]["insured"] is False
    assert case["patient"]["annual_income_cents"] == 3200000
    # ...and the one fact no document states is still absent, not defaulted.
    assert "household_size" not in case["patient"]


def test_the_one_document_path_merges_the_same_way_the_batch_path_does(monkeypatch):
    """`on_document_added` (Gmail intake -- documents arrive one at a time)
    and `process_case_documents` (the demo's whole-fixture upload) must land
    on the same case."""
    s = make_memory_store()
    _seed_merge_case(s, monkeypatch)
    for doc_id in _MERGE_EXTRACTIONS:
        asyncio.run(pipeline.on_document_added("c1", doc_id))
    one_at_a_time = s.get_case("c1")

    s2 = make_memory_store()
    _seed_merge_case(s2, monkeypatch)
    asyncio.run(pipeline.process_case_documents("c1", list(_MERGE_EXTRACTIONS)))
    all_at_once = s2.get_case("c1")

    assert one_at_a_time["patient"] == all_at_once["patient"]
    assert one_at_a_time["bill"] == all_at_once["bill"]


def test_the_audit_trail_names_the_fact_that_is_missing(monkeypatch):
    """A case that cannot be screened must say WHICH single fact is blocking
    it, so a human -- or a future intake form -- supplies exactly that one."""
    s = make_memory_store()
    _seed_merge_case(s, monkeypatch)
    asyncio.run(pipeline.process_case_documents("c1", list(_MERGE_EXTRACTIONS)))

    merged = next(e for e in s.list_events("c1") if e["action"] == "merge_document_facts")
    assert "patient.state='CA' (from the itemized_bill document)" in merged["detail"]
    assert "patient.insured=False (from the gfe document)" in merged["detail"]
    assert "Nothing was inferred" in merged["detail"]

    missing = next(e for e in s.list_events("c1") if e["action"] == "facts_not_established")
    assert "household size was not stated in any document on file" in missing["detail"]


def test_re_analysis_does_not_oscillate_the_merged_facts(monkeypatch):
    """§2.3: this handler runs on every Pub/Sub redelivery and must converge."""
    s = make_memory_store()
    _seed_merge_case(s, monkeypatch)

    asyncio.run(pipeline.process_case_documents("c1", list(_MERGE_EXTRACTIONS)))
    first = s.get_case("c1")
    merge_events = len([e for e in s.list_events("c1") if e["action"] == "merge_document_facts"])

    for _ in range(3):
        asyncio.run(pipeline.process_case_documents("c1", list(_MERGE_EXTRACTIONS)))
    again = s.get_case("c1")

    assert again["patient"] == first["patient"]
    assert again["bill"] == first["bill"]
    # And the feed does not grow a row per redelivery for the same facts.
    assert (
        len([e for e in s.list_events("c1") if e["action"] == "merge_document_facts"])
        == merge_events
    )


def test_a_human_entered_patient_fact_survives_the_merge_and_is_reported(monkeypatch):
    """`POST /cases` (§3.3) is how the one unsourceable fact gets supplied.
    The merge must not then rewrite what the human typed -- and a document
    that disagrees is recorded rather than silently applied."""
    s = make_memory_store()
    _seed_merge_case(s, monkeypatch, patient={"household_size": 3, "annual_income_cents": 4000000})

    asyncio.run(pipeline.process_case_documents("c1", list(_MERGE_EXTRACTIONS)))

    case = s.get_case("c1")
    assert case["patient"]["household_size"] == 3
    assert case["patient"]["annual_income_cents"] == 4000000
    disagreement = next(
        e for e in s.list_events("c1") if e["action"] == "document_disagrees_with_case"
    )
    assert "3200000" in disagreement["detail"]
    assert "the case value was KEPT" in disagreement["detail"]


def test_the_resolved_hospital_record_backfills_a_state_no_document_stated(monkeypatch):
    """State selects the whole deadline regime (§3.5). When no letterhead read
    produced one, the resolved `hospitals/{ein}` record -- LEDGER's IRS
    Schedule H / CMS data -- is the backstop."""
    stateless = copy.deepcopy(_MERGE_EXTRACTIONS)
    for _label, extraction in stateless.values():
        extraction.pop("state", None)  # no document carries a state at all
    s = make_memory_store()
    _seed_merge_case(
        s,
        monkeypatch,
        hospital={"ein": "94-0562680", "name": "Sutter Bay", "state": "CA"},
        extractions=stateless,
    )

    asyncio.run(pipeline.process_case_documents("c1", list(_MERGE_EXTRACTIONS)))

    case = s.get_case("c1")
    assert case["patient"]["state"] == "CA"
    backfill = next(e for e in s.list_events("c1") if e["action"] == "state_from_hospital_record")
    assert "no document on file stated a state" in backfill["detail"]


# ---------------------------------------------------------------------------
# CONVERGENCE. After every cascade for a case has finished, in ANY completion
# order, the stored analysis must equal what a single cascade over all the
# documents produces.  (Live defect: case-1a043f4f4ae26dfa, 2026-08-26.)
# ---------------------------------------------------------------------------

#: Names the pass a coroutine belongs to, so the gated stubs below can hold a
#: SPECIFIC cascade at a SPECIFIC step. A contextvar and not an argument
#: because `strategist.run(case_id, case)` is the contract and a task gets its
#: own copy of the context at creation, which is exactly the per-cascade scope
#: needed here.
_ANALYSIS_PASS: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "analysis_pass", default=None
)

_SUTTER = {
    "ein": "94-0562680",
    "name": "Sutter Bay Hospitals",
    "nonprofit": True,
    "state": "CA",
    "free_care_max_fpl_pct": 200,
    "discounted_care_max_fpl_pct": 400,
}


def _patch_real_strategist_and_auditor(monkeypatch, *, reached=None, release=None, seen=None):
    """Strategist and Auditor that really depend on what their pass can see.

    Strategist calls STATUTE's own `select_fronts` through
    `strategist._facts`, so the reason text is the real thing -- which is the
    point: the live symptom was a reason that named the wrong missing fact.
    Auditor counts the line items actually on file at the moment it runs.

    `reached`/`release` turn each pass's Strategist into a gate, making the
    interleaving explicit rather than lucky.
    """
    monkeypatch.setattr(
        auditor,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "case_id": case_id,
                "findings": [],
                "total_findings_cents": 100_00 * len(auditor.all_line_items(case_id)),
                "line_items_examined": len(auditor.all_line_items(case_id)),
                "denial_check": {"ran": False, "reason": "no denial letter"},
                "source": {},
            },
            "audited",
        ),
    )

    async def _strategist(case_id, case):
        fact = strategist._facts(case_id, case)  # STATUTE's select_fronts, for real
        name = _ANALYSIS_PASS.get()
        if name is not None and seen is not None:
            seen[name] = fact["fronts"]
        if name is not None and reached is not None:
            reached[name].set()
            await release[name].wait()
        return fake_turn(fact, "strategy")

    monkeypatch.setattr(strategist, "run", _strategist)


def test_concurrent_cascades_converge_whatever_order_they_finish_in(monkeypatch):
    """THE property test for the stale-cascade race, and the one that matters.

    An email with three PDFs publishes three `case.document.added` events;
    `ef-document-added`'s 60s ack deadline against a 60-130s cascade adds
    redeliveries on top. Every one of them runs the whole
    Reader -> merge -> Lookup -> {Clock, Auditor} -> Strategist pass and ends
    by writing the entire `fronts[]` reason/applicable set from whatever
    documents existed when IT started. Last writer wins -- and "last" means
    last to FINISH, not best informed:

        16:03:22  reader      merge_document_facts  (the bill, alone)
        16:03:46  strategist  charity_care: "annual household income was not
                              stated in any document on file..."
        16:04:01  reader      merge_document_facts  (all three)
        16:04:30  strategist  charity_care: "household size was not stated..."

    The reason STORED afterwards was the 16:03:46 one, from a pass that never
    saw the $32,000 pay stub -- while `patient.annual_income_cents` on the same
    Firestore document read 3,200,000. The case contradicted itself on screen.

    THE INTERLEAVING IS EXPLICIT, not timed. Every pass is held at its own
    Strategist until all three are in flight, and then released in REVERSE
    order, so the worst-informed cascade is guaranteed to be the last writer --
    the exact shape that produced the live defect, made deterministic the way
    `test_two_concurrent_filings_on_one_case_do_not_clobber_each_other`'s
    `asyncio.Barrier` made the filing race deterministic.

    THE ASSERTION IS CONVERGENCE, not "the reason looks right": the raced case
    must end up byte-identical to a case that saw all three documents in ONE
    cascade. Fronts, savings, audit findings -- everything the pass writes.
    """
    # The reference: one cascade, every document present. This is the answer.
    ref_store = make_memory_store()
    _seed_merge_case(ref_store, monkeypatch, hospital=_SUTTER)
    _patch_real_strategist_and_auditor(monkeypatch)
    asyncio.run(pipeline.process_case_documents("c1", list(_MERGE_EXTRACTIONS)))
    reference = ref_store.get_case("c1")

    # The race: three cascades, one per document, finishing in reverse order.
    s = make_memory_store()
    _seed_merge_case(s, monkeypatch, hospital=_SUTTER)
    order = ["gfe-only", "gfe+bill", "all-three"]
    doc_for = dict(zip(order, ["d-gfe", "d-bill", "d-income"], strict=True))
    seen: dict[str, list] = {}

    async def _drive():
        reached = {name: asyncio.Event() for name in order}
        release = {name: asyncio.Event() for name in order}
        _patch_real_strategist_and_auditor(monkeypatch, reached=reached, release=release, seen=seen)

        async def _pass(name: str):
            _ANALYSIS_PASS.set(name)
            return await pipeline.on_document_added("c1", doc_for[name])

        tasks = {}
        for name in order:  # each cascade reaches its Strategist before the next starts
            tasks[name] = asyncio.create_task(_pass(name))
            await reached[name].wait()
        for name in reversed(order):  # ...and they finish in the opposite order
            release[name].set()
            await tasks[name]

    asyncio.run(_drive())
    raced = s.get_case("c1")

    # The passes really did disagree -- otherwise this test proves nothing.
    def _charity(fronts):
        return next(f["reason"] for f in fronts if f["front"] == "charity_care")

    assert _charity(seen["gfe-only"]) != _charity(seen["all-three"])
    assert "income" in _charity(seen["gfe-only"])
    assert "household size" in _charity(seen["all-three"])

    assert raced["fronts"] == reference["fronts"], (
        "the last cascade to finish overwrote a better-informed one: stored "
        f"{_charity(raced['fronts'])!r}, expected {_charity(reference['fronts'])!r}"
    )
    assert raced["savings_found_cents"] == reference["savings_found_cents"]
    assert raced["audit_findings_cents"] == reference["audit_findings_cents"]
    assert raced["patient"] == reference["patient"]
    assert raced["bill"] == reference["bill"]


def test_a_superseded_cascade_does_not_narrate_its_stale_answer(monkeypatch):
    """Half the live symptom was in the activity feed, not just the case:
    ef-2026-0006's feed carried "annual household income was not stated in any
    document on file" AFTER the pass that had read the pay stub. A superseded
    pass logs ONE row saying it was superseded, and none of its conclusions."""
    s = make_memory_store()
    _seed_merge_case(s, monkeypatch, hospital=_SUTTER)
    order = ["gfe-only", "all-three"]
    doc_for = {"gfe-only": "d-gfe", "all-three": "d-income"}

    async def _drive():
        reached = {name: asyncio.Event() for name in order}
        release = {name: asyncio.Event() for name in order}
        _patch_real_strategist_and_auditor(monkeypatch, reached=reached, release=release)

        async def _pass(name: str):
            _ANALYSIS_PASS.set(name)
            return await pipeline.on_document_added("c1", doc_for[name])

        tasks = {}
        for name in order:
            tasks[name] = asyncio.create_task(_pass(name))
            await reached[name].wait()
        # The bill is read by neither pass's Reader here; `d-bill` stays
        # unclassified, so "all-three" is really "the gfe and the pay stub" --
        # still a strict superset of "gfe-only", which is what matters.
        for name in reversed(order):
            release[name].set()
            await tasks[name]

    asyncio.run(_drive())

    charity_rows = [e for e in s.list_events("c1") if e["action"] == "select_front:charity_care"]
    stale = [r for r in charity_rows if "annual household income was not stated" in r["detail"]]
    assert not stale, (
        "the superseded pass narrated its stale conclusion into the activity feed: "
        f"{stale[0]['detail'][:120]!r} -- while patient.annual_income_cents on the same case "
        f"reads {s.get_case('c1')['patient'].get('annual_income_cents')!r}"
    )
    assert len(charity_rows) == 1
    # ...and the dropped pass is not silent about having been dropped.
    assert "analysis_superseded" in [e["action"] for e in s.list_events("c1")]


def test_re_analysis_with_the_same_documents_writes_and_logs_nothing_new(monkeypatch):
    """§2.3 from the other side. The guard must not turn a redelivery into a
    no-op that behaves DIFFERENTLY from the first run: identical evidence is
    not weaker evidence, so the pass writes -- and, writing the same values,
    changes nothing and adds no rows."""
    s = make_memory_store()
    _seed_merge_case(s, monkeypatch, hospital=_SUTTER)
    _patch_real_strategist_and_auditor(monkeypatch)

    asyncio.run(pipeline.process_case_documents("c1", list(_MERGE_EXTRACTIONS)))
    first_case = s.get_case("c1")
    first_events = [e["event_id"] for e in s.list_events("c1")]

    asyncio.run(pipeline.process_case_documents("c1", list(_MERGE_EXTRACTIONS)))
    second_case = s.get_case("c1")

    assert [e["event_id"] for e in s.list_events("c1")] == first_events
    for field in ("fronts", "savings_found_cents", "audit_findings_cents", "patient", "bill"):
        assert second_case[field] == first_case[field]
    assert "analysis_superseded" not in [e["action"] for e in s.list_events("c1")]
