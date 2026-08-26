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

    async def counting_cascade(case_id, case):
        cascade_calls.append(case_id)
        return await orig_run_cascade(case_id, case)

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
