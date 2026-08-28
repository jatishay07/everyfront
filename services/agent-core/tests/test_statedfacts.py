"""`agent_core.statedfacts` -- what the patient SAYS, and the hard line
between that and what a document PROVES.

THE LIVE CASE these tests are built on (Sutter Bay / CA / self-pay, 2026-08):
a $2,625 bill, a $1,925 Good Faith Estimate, a $32,000 pay stub, and an email
body reading "I'm uninsured and paying out of pocket. Household of three, I
make about $32,000 a year." Household size appears in no document and no §3.1
document type can carry it, so charity care refused -- while the one number
that decides whether the whole $2,625 is erased sat unread in the email.

Every test here is about the same question asked in a different place: can a
reader of this system, at this point, still tell a document from a sentence?
"""

from __future__ import annotations

import pytest
from agent_core import factmerge, statedfacts
from agent_core.rules_bridge import select_fronts

SUTTER = {
    "name": "Sutter Bay Hospitals",
    "ein": "94-0562680",
    "state": "CA",
    "nonprofit": True,
}


def _statement_doc(doc_id: str, extracted: dict) -> dict:
    return {
        "doc_id": doc_id,
        "type": factmerge.PATIENT_STATEMENT_TYPE,
        "extracted": extracted,
    }


def _live_case(patient: dict | None = None, stated: dict | None = None) -> dict:
    return {
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
        },
        "hospital": SUTTER,
        "documents": [],
        "patient_stated": stated or {},
    }


LIVE_STATEMENT = _statement_doc(
    "body01",
    {
        "household_size": 3,
        "household_size_quote": "Household of three",
        "annual_income_cents": 3_200_000,
        "annual_income_quote": "I make about $32,000 a year",
        "uninsured_self_pay": True,
        "coverage_quote": "I'm uninsured and paying out of pocket",
    },
)


# --------------------------------------------------------------------------
# collect(): only patient_statement documents, only plausible values
# --------------------------------------------------------------------------
def test_collect_reads_only_patient_statement_documents():
    """A pay stub's `annual_income_cents` is a DOCUMENT fact and belongs to
    `factmerge`. If it leaked in here it would be re-reported as something the
    patient claimed, which is a document quietly demoting itself into hearsay.
    """
    docs = [
        {
            "doc_id": "pay01",
            "type": "income_proof",
            "extracted": {"annual_income_cents": 3_200_000},
        },
        {"doc_id": "gfe01", "type": "gfe", "extracted": {"uninsured_self_pay": True}},
        LIVE_STATEMENT,
    ]
    facts = statedfacts.facts(statedfacts.collect(docs))
    assert set(facts) == {"household_size", "annual_income_cents", "insured"}
    assert all(record["source_doc_id"] == "body01" for record in facts.values())


def test_collect_keeps_the_verbatim_quote_with_every_value():
    facts = statedfacts.facts(statedfacts.collect([LIVE_STATEMENT]))
    assert facts["household_size"] == {
        "value": 3,
        "quote": "Household of three",
        "source": "patient_statement",
        "source_doc_id": "body01",
    }


def test_collect_reads_insured_from_what_the_patient_said_about_coverage():
    """`uninsured_self_pay: True` -> `insured: False`, never the other way
    round -- the model is asked what the patient SAID, not to judge coverage.
    """
    facts = statedfacts.facts(statedfacts.collect([LIVE_STATEMENT]))
    assert facts["insured"]["value"] is False


@pytest.mark.parametrize("household", [0, -2, 21, 999, "three", 3.0, True, None])
def test_collect_rejects_an_implausible_or_wrong_typed_household(household):
    """AMBIGUOUS-EXTRACTION CASE: the model returns a number that is not a
    household. `0` is the JSON-schema "not found" sentinel, `True` is an int
    in Python, `"three"` is the word rather than the count, and 999 is a page
    number or a case id the model reached for. None of them may become the
    denominator of an FPL percentage."""
    docs = [_statement_doc("b", {"household_size": household, "household_size_quote": "q"})]
    assert "household_size" not in statedfacts.facts(statedfacts.collect(docs))


@pytest.mark.parametrize("income", [0, -1, 100_000_001, "32000", None])
def test_collect_rejects_an_implausible_or_wrong_typed_income(income):
    docs = [_statement_doc("b", {"annual_income_cents": income, "annual_income_quote": "q"})]
    assert "annual_income_cents" not in statedfacts.facts(statedfacts.collect(docs))


def test_collect_ignores_a_statement_whose_extraction_failed():
    docs = [_statement_doc("b", {"_extraction_error": "invalid JSON after retry"})]
    assert statedfacts.facts(statedfacts.collect(docs)) == {}


def test_two_statements_that_disagree_are_reported_not_resolved():
    """AMBIGUOUS-EXTRACTION CASE: the patient wrote twice and said different
    things. Taking the later one silently would be this system inventing a
    correction protocol nobody agreed to; both are unverified claims and a
    human has to pick."""
    docs = [
        _statement_doc("a", {"household_size": 3, "household_size_quote": "Household of three"}),
        _statement_doc("b", {"household_size": 5, "household_size_quote": "there are five of us"}),
    ]
    stated = statedfacts.collect(docs)
    assert statedfacts.facts(stated)["household_size"]["value"] == 3
    conflicts = statedfacts.conflicts(stated)
    assert len(conflicts) == 1
    assert conflicts[0]["field"] == "household_size"
    assert conflicts[0]["also_stated"]["value"] == 5


def test_statement_order_does_not_depend_on_firestore_stream_order():
    """`list_documents` returns whatever Firestore streamed. Which statement
    wins must not."""
    docs = [
        _statement_doc("b", {"household_size": 5, "household_size_quote": "five of us"}),
        _statement_doc("a", {"household_size": 3, "household_size_quote": "three of us"}),
    ]
    assert statedfacts.facts(statedfacts.collect(docs))["household_size"]["value"] == 3
    assert statedfacts.facts(statedfacts.collect(docs[::-1]))["household_size"]["value"] == 3


# --------------------------------------------------------------------------
# overlay(): third tier, gaps only, never mutating the case
# --------------------------------------------------------------------------
def test_overlay_fills_only_the_gap_a_document_could_not():
    patient = {"state": "CA", "insured": False, "annual_income_cents": 3_200_000}
    stated = statedfacts.collect([LIVE_STATEMENT])
    view, filled = statedfacts.overlay(patient, stated)

    assert filled == ("household_size",)
    assert view["household_size"] == 3
    # Income and coverage were already established BY DOCUMENTS. The statement
    # agrees with both, and still does not get to re-assert either.
    assert view["annual_income_cents"] == 3_200_000
    assert view["insured"] is False


def test_overlay_never_overwrites_an_established_fact_even_when_they_disagree():
    """THE PRECEDENCE RULE. A GFE establishes `insured` under 45 CFR
    149.610(a); a sentence does not get to overrule it, in either direction."""
    patient = {"insured": True, "household_size": 2, "annual_income_cents": 9_000_000}
    view, filled = statedfacts.overlay(patient, statedfacts.collect([LIVE_STATEMENT]))
    assert filled == ()
    assert view == patient


def test_overlay_does_not_mutate_the_case_patient():
    patient = {"state": "CA"}
    view, _ = statedfacts.overlay(patient, statedfacts.collect([LIVE_STATEMENT]))
    assert "household_size" not in patient
    assert view is not patient


def test_overlay_treats_an_empty_string_or_none_as_a_gap():
    patient = {"household_size": None, "annual_income_cents": ""}
    _, filled = statedfacts.overlay(patient, statedfacts.collect([LIVE_STATEMENT]))
    assert set(filled) == {"household_size", "annual_income_cents", "insured"}


# --------------------------------------------------------------------------
# reconcile(): agreement is corroboration, disagreement is logged
# --------------------------------------------------------------------------
def test_agreement_with_a_document_is_corroboration_not_a_new_fact():
    """The live case exactly: the GFE already established `insured: False`
    and the patient also wrote "I'm uninsured"."""
    patient = {"insured": False, "annual_income_cents": 3_200_000}
    result = statedfacts.reconcile(patient, statedfacts.collect([LIVE_STATEMENT]))
    fields = {entry["field"] for entry in result["corroborated"]}
    assert fields == {"patient.insured", "patient.annual_income_cents"}
    assert result["contradicted"] == []


def test_disagreement_with_a_document_is_reported_and_the_document_wins():
    """AMBIGUOUS-EXTRACTION CASE: the body contradicts a document. The patient
    writes "I'm uninsured" on a case whose denial letter established coverage."""
    patient = {"insured": True}
    result = statedfacts.reconcile(patient, statedfacts.collect([LIVE_STATEMENT]))
    assert result["corroborated"] == []
    (entry,) = result["contradicted"]
    assert entry["field"] == "patient.insured"
    assert entry["established_value"] is True
    assert entry["stated_value"] is False
    assert entry["quote"] == "I'm uninsured and paying out of pocket"


def test_reconcile_says_nothing_about_a_fact_no_document_established():
    """Household size is not corroborated OR contradicted -- there is nothing
    on the other side of the comparison. It shows up in `overlay`, not here."""
    result = statedfacts.reconcile({}, statedfacts.collect([LIVE_STATEMENT]))
    assert result == {"corroborated": [], "contradicted": []}


# --------------------------------------------------------------------------
# decide_fronts(): the leave-one-out attribution
# --------------------------------------------------------------------------
def test_the_live_case_charity_care_is_decided_and_marked_provisional():
    """THE HEADLINE. With household size supplied by the patient's own
    sentence, STATUTE's screen reaches free care at 117% of the 2026 FPL --
    under California's 400% floor -- and the front comes back APPLICABLE with
    `rests_on == ("household_size",)`. Both halves matter: the determination
    is real, and it is visibly not established."""
    case = _live_case()
    stated = statedfacts.collect([LIVE_STATEMENT])
    decisions, rests_on = statedfacts.decide_fronts(select_fronts, case, stated)

    charity = next(d for d in decisions if d.front == "charity_care")
    assert charity.applicable is True
    assert "free care" in charity.reason
    assert rests_on["charity_care"] == ("household_size",)


def test_without_the_statement_the_same_case_still_refuses():
    """The control. Nothing about this change may make charity care
    applicable on a case where household size is genuinely unknown."""
    decisions, rests_on = statedfacts.decide_fronts(select_fronts, _live_case(), {})
    charity = next(d for d in decisions if d.front == "charity_care")
    assert charity.applicable is False
    assert "household size" in charity.reason
    assert rests_on == {}


def test_a_front_the_statement_did_not_change_is_not_marked_provisional():
    """PPDR turns on uninsured + a GFE delta >= $400 + the 120-day window --
    none of which the statement supplied, since `insured` was already
    established. Marking every front provisional because SOME fact was stated
    would make the flag meaningless."""
    case = _live_case()
    case["bill"]["first_statement_date"] = "2026-06-05"
    stated = statedfacts.collect([LIVE_STATEMENT])
    _, rests_on = statedfacts.decide_fronts(select_fronts, case, stated)
    assert "ppdr" not in rests_on
    assert "debt_validation" not in rests_on


def test_attribution_names_the_fact_that_is_actually_load_bearing():
    """Leave-one-out, not a rule of thumb. Here the patient stated BOTH
    household size and income, and only household size is doing any work --
    income was already on the case from the pay stub."""
    stated = statedfacts.collect([LIVE_STATEMENT])
    _, rests_on = statedfacts.decide_fronts(select_fronts, _live_case(), stated)
    assert rests_on["charity_care"] == ("household_size",)


def test_a_stated_fact_that_makes_the_answer_WORSE_is_still_provisional():
    """A determination that rests on an unverified claim is provisional
    whichever way it came out. Here the patient states an income that puts
    them over every threshold: charity care goes from "cannot screen" to a
    substantive refusal, and that refusal is no better established than a
    favourable determination would have been."""
    case = _live_case()
    case["patient"].pop("annual_income_cents")
    rich = _statement_doc(
        "body01",
        {
            "household_size": 1,
            "household_size_quote": "just me",
            "annual_income_cents": 40_000_000,
            "annual_income_quote": "I make about $400,000 a year",
        },
    )
    decisions, rests_on = statedfacts.decide_fronts(
        select_fronts, case, statedfacts.collect([rich])
    )
    charity = next(d for d in decisions if d.front == "charity_care")
    assert charity.applicable is False
    assert "exceeds" in charity.reason
    assert set(rests_on["charity_care"]) == {"household_size", "annual_income_cents"}


# --------------------------------------------------------------------------
# provisional_reason(): the prefix a judge reads
# --------------------------------------------------------------------------
def test_the_reason_leads_with_the_provenance_and_quotes_the_patient():
    stated = statedfacts.collect([LIVE_STATEMENT])
    reason = statedfacts.provisional_reason(
        "Income is 117.1303% of the federal poverty level.", ("household_size",), stated
    )
    assert reason.startswith("[PROVISIONAL")
    assert "not on a document" in reason
    assert '"Household of three"' in reason
    assert "Income is 117.1303%" in reason


def test_the_statement_type_is_excluded_from_the_document_merge():
    """THE STRUCTURAL GUARANTEE, asserted directly. Everything above depends
    on a patient statement never reaching `factmerge`'s precedence table --
    if it were ever added to `INCOMING_DOC_TYPES`, a stated income could be
    merged into `patient.annual_income_cents` and become indistinguishable
    from the pay stub's."""
    assert factmerge.PATIENT_STATEMENT_TYPE not in factmerge.INCOMING_DOC_TYPES
    patch, _ = factmerge.merge_document_facts({"patient": {}, "bill": {}}, [LIVE_STATEMENT])
    assert patch.get("patient", {}) == {}
    assert patch.get("bill", {}) == {}
