"""The patient's own words, end to end through the pipeline: intake event ->
document -> `patient_stated` -> a provisional front -> a blocked filing.

Every test below is on the LIVE case (Sutter Bay / CA / self-pay, $2,625
billed, $1,925 GFE, $32,000 pay stub, "Household of three"), because the
whole design exists for one measured outcome: charity care refused on a case
where the deciding number was sitting in the email nobody read.

What must be true at every stop:
  * `cases/{id}.patient` still means "established by a document or a human";
  * the claim is visible, quoted, and separately addressed as a claim;
  * a front that borrowed one says so, and cannot be filed.
"""

from __future__ import annotations

import asyncio

from _helpers import async_fake_turn, make_memory_store
from agent_core import evidence, factmerge, pipeline, statedfacts
from agent_core.agents import auditor, clock, filer, lookup, reader, strategist, verifier

SUTTER = {
    "name": "Sutter Bay Hospitals",
    "ein": "94-0562680",
    "state": "CA",
    "nonprofit": True,
}

BODY = (
    "Hi -- attached is the bill from my ER visit, the estimate, and my pay stub.\n"
    "I'm uninsured and paying out of pocket. Household of three, I make about "
    "$32,000 a year.\n"
)

STATEMENT_EXTRACTION = {
    "household_size": 3,
    "household_size_quote": "Household of three",
    "annual_income_cents": 3_200_000,
    "annual_income_quote": "I make about $32,000 a year",
    "uninsured_self_pay": True,
    "coverage_quote": "I'm uninsured and paying out of pocket",
}


def _patch_store(monkeypatch, s):
    monkeypatch.setattr(pipeline, "store", s)
    monkeypatch.setattr(lookup, "store", s)
    monkeypatch.setattr(auditor, "store", s)
    monkeypatch.setattr(verifier, "store", s)
    monkeypatch.setattr(filer, "store", s)


def _no_op_pubsub(monkeypatch):
    published = []
    monkeypatch.setattr(
        pipeline.pubsub_client, "publish", lambda topic, payload: published.append((topic, payload))
    )
    return published


def _seed_live_case(s, *, with_statement=True, patient=None):
    """The live case as the merge leaves it: everything the three PDFs
    established, and nothing else."""
    s.create_case(
        "c1",
        {
            "patient": {
                "name": "Jordan Alvarez",
                "state": "CA",
                "insured": False,
                "annual_income_cents": 3_200_000,
                **(patient or {}),
            },
            "bill": {
                "amount_cents": 262_500,
                "gfe_amount_cents": 192_500,
                "hospital_ein": "94-0562680",
                "first_statement_date": "2026-06-05",
            },
            "hospital": SUTTER,
            "hospital_name": SUTTER["name"],
            "hospital_nonprofit": True,
        },
    )
    s.add_document(
        "c1",
        {"type": "income_proof", "extracted": {"annual_income_cents": 3_200_000}},
        doc_id="pay01",
    )
    if with_statement:
        s.add_document(
            "c1",
            {
                "type": factmerge.PATIENT_STATEMENT_TYPE,
                "raw_text": BODY,
                "extracted": STATEMENT_EXTRACTION,
            },
            doc_id="body01",
        )
    return s.get_case("c1")


def _events(s, action):
    return [e for e in s.list_events("c1") if e["action"] == action]


# --------------------------------------------------------------------------
# The intake event
# --------------------------------------------------------------------------
def test_an_intake_event_may_declare_a_patient_statement(monkeypatch):
    """`services/intake` decoded the text/plain MIME part itself, so "this is
    prose a human typed" is a fact about transport, not a classification."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    pipeline.ensure_case_and_document_from_event(
        {
            "case_id": "c1",
            "doc_id": "body01",
            "doc_type": "patient_statement",
            "raw_text": BODY,
            "filename": "message-body.txt",
            "gcs_uri": "gs://bucket/intake/m1/body/message-body.txt",
        }
    )
    assert s.get_document("c1", "body01")["type"] == "patient_statement"
    detail = _events(s, "case_opened_from_intake")[0]["detail"]
    assert "the patient's own words" in detail


def test_an_intake_event_may_not_declare_a_document_type(monkeypatch):
    """THE GUARD. If a publisher could stamp `bill` or `gfe` on an event, it
    would override Gemma's first-pass classification with an upstream
    assumption -- silently mislabelling a denial letter and disabling the
    §1.3-bonus model's whole job. Only `patient_statement` is admissible, and
    anything else falls back to `""` so the classifier decides."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    for declared in ("bill", "itemized_bill", "gfe", "income_proof", "generated_letter", "junk"):
        doc_id = f"doc-{declared}"
        pipeline.ensure_case_and_document_from_event(
            {"case_id": "c1", "doc_id": doc_id, "doc_type": declared, "raw_text": "x"}
        )
        assert s.get_document("c1", doc_id)["type"] == "", (
            f"an intake event was allowed to classify a document as {declared!r}"
        )


def test_a_statement_document_counts_as_evidence_a_pass_has_seen():
    """Not in `factmerge.INCOMING_DOC_TYPES` (it may establish nothing) but
    very much in the evidence set (a pass that read it knows more). Leaving it
    out would make a cascade that saw the email indistinguishable from one
    that did not, and last-to-finish would win again -- the exact race
    `write_analysis` exists to close."""
    docs = [
        {"doc_id": "bill01", "type": "bill", "extracted": {"amount_cents": 262_500}},
        {"doc_id": "body01", "type": factmerge.PATIENT_STATEMENT_TYPE, "extracted": {}},
    ]
    with_body = evidence.from_documents(docs)
    without_body = evidence.from_documents(docs[:1])
    assert len(with_body) == 2
    assert evidence.is_strictly_weaker(without_body, with_body) is True


# --------------------------------------------------------------------------
# The merge step
# --------------------------------------------------------------------------
def test_the_merge_records_the_claim_and_leaves_patient_alone(monkeypatch):
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _seed_live_case(s)

    case, _ = pipeline._merge_document_facts("c1", s.get_case("c1"))

    assert case["patient_stated"]["household_size"]["value"] == 3
    assert case["patient_stated"]["household_size"]["quote"] == "Household of three"
    assert "household_size" not in case["patient"], (
        "a patient-stated household size was merged into `patient`, where nothing "
        "downstream can tell it apart from a value a document established"
    )
    detail = _events(s, "patient_stated_facts")[0]["detail"]
    assert "Household of three" in detail
    assert "not as a fact" in detail


def test_the_merge_logs_corroboration_when_the_statement_agrees(monkeypatch):
    """The live case: the GFE established `insured: False` under 45 CFR
    149.610(a), and the patient also wrote "I'm uninsured". Agreement is a
    second voice on a settled fact, not a new one -- and nothing is
    rewritten."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _seed_live_case(s)

    case, _ = pipeline._merge_document_facts("c1", s.get_case("c1"))

    assert case["patient"]["insured"] is False
    details = [e["detail"] for e in _events(s, "patient_statement_corroborates_document")]
    assert any("insurance status" in d and "AGREES" in d for d in details)


def test_the_merge_logs_a_contradiction_instead_of_resolving_it(monkeypatch):
    """AMBIGUOUS CASE: the body contradicts a document. The document wins, the
    disagreement is recorded, and no value moves."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _seed_live_case(s, patient={"insured": True})

    case, _ = pipeline._merge_document_facts("c1", s.get_case("c1"))

    assert case["patient"]["insured"] is True, "a sentence overruled a document"
    (event,) = _events(s, "patient_statement_contradicts_document")
    assert "KEPT" in event["detail"]
    assert "I'm uninsured and paying out of pocket" in event["detail"]


def test_a_body_that_states_nothing_changes_nothing(monkeypatch):
    """AMBIGUOUS CASE: a covering note with no facts in it. There must be no
    `patient_stated` entries, no events about claims, and no change to any
    front -- an empty statement is not a statement."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _seed_live_case(s, with_statement=False)
    s.add_document(
        "c1",
        {
            "type": factmerge.PATIENT_STATEMENT_TYPE,
            "raw_text": "Please help, the bill is attached. Thank you.",
            "extracted": {
                "household_size": None,
                "annual_income_cents": None,
                "uninsured_self_pay": None,
            },
        },
        doc_id="body01",
    )

    case, _ = pipeline._merge_document_facts("c1", s.get_case("c1"))

    assert statedfacts.facts(case.get("patient_stated") or {}) == {}
    assert _events(s, "patient_stated_facts") == []


# --------------------------------------------------------------------------
# The cascade: a real, provisional determination
# --------------------------------------------------------------------------
def _patch_cascade_agents(monkeypatch, *, audit_cents=210_00):
    """Everything except the Strategist, which runs for real -- the point of
    these tests is what STATUTE's own `select_fronts` concludes.

    The Strategist's own LLM narration turn is stubbed (`run_agent_turn`)
    rather than its `_facts`: the fronts these tests assert on are computed by
    code before the model is ever called, so stubbing the turn keeps the real
    decision path and keeps the suite off the network.
    """
    monkeypatch.setattr(
        strategist.common,
        "run_agent_turn",
        lambda *a, **k: _async({"answer": "", "trace": [], "model": "t", "error": None}),
    )
    monkeypatch.setattr(
        lookup,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "resolved": True,
                "hospital": SUTTER,
                "ein": SUTTER["ein"],
                "citations": [],
                "note": "ok",
            },
            "resolved Sutter Bay",
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
                "total_findings_cents": audit_cents,
                "line_items_examined": 6,
                "denial_check": {"ran": False, "reason": "no denial letter"},
                "source": {},
            },
            "duplicate 80053",
        ),
    )


def test_the_live_case_reaches_a_provisional_charity_care_determination(monkeypatch):
    """THE HEADLINE. With the email read, STATUTE's screen reaches free care
    at 117% of the 2026 FPL (under California's 400% floor) and the front is
    APPLICABLE -- and every trace of where that came from is on the record:
    `provisional`, `rests_on`, and a reason that leads with the provenance and
    quotes the patient."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _no_op_pubsub(monkeypatch)
    _patch_cascade_agents(monkeypatch)
    _seed_live_case(s)
    case, ev = pipeline._merge_document_facts("c1", s.get_case("c1"))

    asyncio.run(pipeline._run_cascade("c1", case, ev))

    charity = next(f for f in s.get_case("c1")["fronts"] if f["front"] == "charity_care")
    assert charity["applicable"] is True
    assert charity["provisional"] is True
    assert charity["rests_on"] == ["household_size"]
    assert charity["reason"].startswith("[PROVISIONAL")
    assert '"Household of three"' in charity["reason"]
    assert "blocked until a human confirms it" in charity["reason"]


def test_the_reported_savings_never_include_an_unverified_erasure(monkeypatch):
    """`savings_found_cents` is a claim about money this system found. $2,625
    that exists only because someone typed "Household of three" is a
    conditional, and it is reported as one -- in the audit trail, with the
    condition attached, and NOT in the integer a judge does arithmetic on."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _no_op_pubsub(monkeypatch)
    _patch_cascade_agents(monkeypatch)
    _seed_live_case(s)
    case, ev = pipeline._merge_document_facts("c1", s.get_case("c1"))

    asyncio.run(pipeline._run_cascade("c1", case, ev))

    assert s.get_case("c1")["savings_found_cents"] == 210_00
    (summary,) = _events(s, "savings_summary")
    assert "Provisional, unverified" in summary["detail"]
    assert "$2,625.00" in summary["detail"]
    assert "NOT counted" in summary["detail"]


def test_without_the_email_the_case_still_refuses_and_names_the_gap(monkeypatch):
    """The control, and the behaviour that must never regress: with no
    statement on file, charity care is inapplicable and says which single
    fact is missing."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _no_op_pubsub(monkeypatch)
    _patch_cascade_agents(monkeypatch)
    _seed_live_case(s, with_statement=False)
    case, ev = pipeline._merge_document_facts("c1", s.get_case("c1"))

    asyncio.run(pipeline._run_cascade("c1", case, ev))

    charity = next(f for f in s.get_case("c1")["fronts"] if f["front"] == "charity_care")
    assert charity["applicable"] is False
    assert charity["provisional"] is False
    assert charity["rests_on"] == []
    assert "household size" in charity["reason"]
    assert s.get_case("c1")["savings_found_cents"] == 210_00


def test_a_human_entered_household_size_makes_the_determination_established(monkeypatch):
    """The loop closing. Once a human supplies the one fact (§3.3
    `POST /cases`), the same determination stops being provisional -- the
    overlay fills nothing, `rests_on` is empty, and the reason is STATUTE's
    own sentence with no prefix."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _no_op_pubsub(monkeypatch)
    _patch_cascade_agents(monkeypatch)
    _seed_live_case(s, patient={"household_size": 3})
    case, ev = pipeline._merge_document_facts("c1", s.get_case("c1"))

    asyncio.run(pipeline._run_cascade("c1", case, ev))

    charity = next(f for f in s.get_case("c1")["fronts"] if f["front"] == "charity_care")
    assert charity["applicable"] is True
    assert charity["provisional"] is False
    assert not charity["reason"].startswith("[PROVISIONAL")
    assert s.get_case("c1")["savings_found_cents"] == 262_500


# --------------------------------------------------------------------------
# The filing gate
# --------------------------------------------------------------------------
def _run_real_verifier(monkeypatch):
    monkeypatch.setattr(
        verifier.common,
        "run_agent_turn",
        lambda *a, **k: _async({"answer": "", "trace": [], "model": "t", "error": None}),
    )


async def _async(value):
    return value


def test_a_provisional_front_cannot_be_filed(monkeypatch):
    """THE ANSWER TO "do not silently make charity care applicable". The
    determination is computed and shown; the filing dies at the gate a human
    is already standing at, and the refusal names the one fact to confirm and
    quotes the patient's own words for it."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    published = _no_op_pubsub(monkeypatch)
    _run_real_verifier(monkeypatch)
    _seed_live_case(s)
    s.update_case("c1", {"patient_stated": statedfacts.collect(s.list_documents("c1"))})
    s.upsert_front(
        "c1",
        {
            "front": "charity_care",
            "applicable": True,
            "status": "open",
            "provisional": True,
            "rests_on": ["household_size"],
        },
    )

    result = asyncio.run(pipeline.approve_and_request_filing("c1", "charity_care"))

    assert result["ok"] is False
    assert "household size" in result["reason"]
    assert "Household of three" in result["reason"]
    assert "POST /cases" in result["reason"]
    assert all(topic != pipeline.config.TOPIC_FILING_REQUESTED for topic, _ in published)
    assert s.get_case("c1")["fronts"][0]["status"] == "open"
    (check,) = _events(s, "pre_filing_check")
    assert check["detail"].startswith("BLOCKED:")


def test_the_block_holds_even_if_the_front_carries_no_flag(monkeypatch):
    """The independent guard. A front written before `rests_on` existed -- or
    by a pass whose flags were lost -- must still not be filed on a household
    size that is not on the case. An applicable charity-care front whose
    `patient.household_size` is absent can only have got there through the
    overlay."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _no_op_pubsub(monkeypatch)
    _run_real_verifier(monkeypatch)
    _seed_live_case(s)
    s.update_case("c1", {"patient_stated": statedfacts.collect(s.list_documents("c1"))})
    s.upsert_front("c1", {"front": "charity_care", "applicable": True, "status": "open"})

    result = asyncio.run(pipeline.approve_and_request_filing("c1", "charity_care"))

    assert result["ok"] is False
    assert "household size" in result["reason"]


def test_a_stated_income_that_contradicts_the_pay_stub_blocks_the_filing(monkeypatch):
    """The Verifier check §4 persona 5 WO1 always described, with two
    genuinely independent numbers in it for the first time: until the email
    body reached the pipeline it was comparing a document against a value the
    merge had copied off that same document."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _no_op_pubsub(monkeypatch)
    _run_real_verifier(monkeypatch)
    _seed_live_case(s, with_statement=False, patient={"household_size": 3})
    s.add_document(
        "c1",
        {
            "type": factmerge.PATIENT_STATEMENT_TYPE,
            "raw_text": "I make about $58,000 a year.",
            "extracted": {
                "annual_income_cents": 5_800_000,
                "annual_income_quote": "I make about $58,000 a year",
            },
        },
        doc_id="body01",
    )
    s.update_case("c1", {"patient_stated": statedfacts.collect(s.list_documents("c1"))})
    s.upsert_front("c1", {"front": "charity_care", "applicable": True, "status": "open"})

    result = asyncio.run(pipeline.approve_and_request_filing("c1", "charity_care"))

    assert result["ok"] is False
    assert "5800000" in result["reason"] and "3200000" in result["reason"]
    assert "tolerance" in result["reason"]


def test_a_stated_income_that_matches_the_pay_stub_is_logged_as_corroboration(monkeypatch):
    """And the positive finding gets said out loud, so the Verifier is not an
    agent that only ever speaks when something is wrong."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _no_op_pubsub(monkeypatch)
    _run_real_verifier(monkeypatch)
    _seed_live_case(s, patient={"household_size": 3})
    s.update_case("c1", {"patient_stated": statedfacts.collect(s.list_documents("c1"))})
    s.upsert_front("c1", {"front": "charity_care", "applicable": True, "status": "open"})

    result = asyncio.run(pipeline.approve_and_request_filing("c1", "charity_care"))

    assert result["ok"] is True, result.get("reason")
    (event,) = _events(s, "income_cross_check")
    assert "agree within" in event["detail"]


def test_reader_routes_a_statement_document_to_the_statement_extractor(monkeypatch):
    """The stored `type` is what routes it: a document whose type is
    `patient_statement` must never be run through the bill schema, or a
    patient's prose would be extracted into `bill.amount_cents`."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    seen = {}

    async def _fake_run(case_id, doc_id, text, hint=None):
        seen["hint"] = hint
        return {
            "fact": {
                "case_id": case_id,
                "doc_id": doc_id,
                "label": hint or "bill",
                "extraction": STATEMENT_EXTRACTION,
                "ungrounded_fields": ["annual_income_cents"],
                "citations": [],
            },
            "answer": "the patient states a household of three",
            "trace": [],
            "model": "t",
            "error": None,
        }

    monkeypatch.setattr(reader, "run", _fake_run)
    _seed_live_case(s)

    asyncio.run(pipeline._run_reader("c1", "body01"))

    assert seen["hint"] == factmerge.PATIENT_STATEMENT_TYPE
    (event,) = _events(s, "statement_not_grounded")
    assert "annual_income_cents" in event["detail"]
    assert "not a claim the patient made" in event["detail"]


def test_the_strategist_is_the_only_place_the_overlay_is_applied(monkeypatch):
    """A structural guard on the design. If any other module started writing
    a stated value into `patient`, this test is what notices: after a full
    cascade the case's own patient record still holds only what the documents
    established."""
    s = make_memory_store()
    _patch_store(monkeypatch, s)
    _no_op_pubsub(monkeypatch)
    _patch_cascade_agents(monkeypatch)
    _seed_live_case(s)
    case, ev = pipeline._merge_document_facts("c1", s.get_case("c1"))
    asyncio.run(pipeline._run_cascade("c1", case, ev))

    patient = s.get_case("c1")["patient"]
    assert "household_size" not in patient
    assert set(patient) == {"name", "state", "insured", "annual_income_cents"}
