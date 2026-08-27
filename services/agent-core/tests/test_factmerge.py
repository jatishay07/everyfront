"""agent_core.factmerge -- documents[].extracted -> canonical patient/bill.

THE DEFECT THIS SUITE EXISTS FOR, measured live on a real emailed bill
(case `case-1a0412ccfef90917`, 2026-08-26): Gemma classified all three PDFs
correctly, Gemini extracted all three cleanly, and the case still came out
with `patient` entirely null and a `bill` with no `line_items` -- so
`select_fronts`, which reads `case["patient"]`/`case["bill"]`, marked all four
fronts inapplicable. Meanwhile the Auditor, which scans the DOCUMENTS
directly, had booked $210.00 of duplicate 80053 onto the same case. One case,
two contradictory answers, and every test in this repo green: the demo path
writes a fixture's patient/bill straight onto the case, so the merge was never
on the critical path.

`_LIVE_*` below are that run's actual extractions.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest
from agent_core import factmerge
from agent_core.casedata import parse_bill_dates
from rules.fronts import select_fronts

_TODAY = date(2026, 8, 27)  # the day the live case was measured

_SUTTER = {
    "ein": "94-0562680",
    "name": "Sutter Bay Hospitals",
    "state": "CA",
    "nonprofit": True,
    "free_care_max_fpl_pct": 400,
    "discounted_care_max_fpl_pct": 0,
}

_LIVE_LINE_ITEMS = [
    {
        "code": "99284",
        "description": "EMERGENCY DEPT VISIT, HIGH COMPLEXITY",
        "units": 1,
        "charge_cents": 185000,
    },
    {"code": "71046", "description": "CHEST X-RAY, 2 VIEWS", "units": 1, "charge_cents": 32000},
    {
        "code": "80053",
        "description": "COMPREHENSIVE METABOLIC PANEL",
        "units": 1,
        "charge_cents": 21000,
    },
    {
        "code": "80053",
        "description": "COMPREHENSIVE METABOLIC PANEL",
        "units": 1,
        "charge_cents": 21000,
    },
    {
        "code": "36415",
        "description": "COLLECTION OF VENOUS BLOOD BY VENIPUNCTURE",
        "units": 1,
        "charge_cents": 3500,
    },
]


def _live_documents() -> list[dict]:
    """The three documents of `case-1a0412ccfef90917`, as Reader left them."""
    return [
        {
            "doc_id": "doc-bill",
            "type": "itemized_bill",
            "extracted": {
                "service_date": "2026-05-01",
                "provider_name": "Sutter Bay Hospitals",
                "line_items": list(_LIVE_LINE_ITEMS),
                "amount_cents": 262500,
                "hospital_ein": "94-0562680",
                "first_statement_date": "2026-06-05",
                # The two facts that WERE in the raw text and were not in the
                # schema until this work order: the letterhead reads
                # "Sutter Bay Hospitals / CA -- EIN 94-0562680".
                "state": "CA",
                "patient_name": "Jordan Alvarez",
            },
        },
        {
            "doc_id": "doc-gfe",
            "type": "gfe",
            "extracted": {
                "provider_name": "Sutter Bay Hospitals",
                "gfe_amount_cents": 192500,
                "hospital_ein": "94-0562680",
                "state": "CA",
                "patient_name": "Jordan Alvarez",
                # "Per 45 CFR 149.610 -- provided to an uninsured / self-pay
                # patient." -- verbatim on the GFE.
                "uninsured_self_pay": True,
            },
        },
        {
            "doc_id": "doc-income",
            "type": "income_proof",
            "extracted": {"annual_income_cents": 3200000, "is_income_proof": True},
        },
    ]


def _merged_case(case: dict | None = None, documents: list[dict] | None = None) -> dict:
    case = case if case is not None else {"patient": {}, "bill": {}}
    documents = documents if documents is not None else _live_documents()
    patch, _report = factmerge.merge_document_facts(case, documents)
    return {**case, **patch}


def _fronts(case: dict, documents: list[dict]) -> dict:
    """`select_fronts` over a merged case, exactly as `_run_cascade` calls it."""
    ready = {**case, "documents": documents, "bill": parse_bill_dates(case.get("bill") or {})}
    return {d.front: d for d in select_fronts(ready, today=_TODAY)}


# --------------------------------------------------------------------------
# The live defect itself
# --------------------------------------------------------------------------


def test_line_items_reach_the_case_so_the_audit_front_stops_contradicting_the_auditor():
    """THE regression. The Auditor books $210 off `documents[].extracted`;
    `_select_audit` reads `case["bill"]["line_items"]`. Before the merge those
    two read different places and disagreed inside one case."""
    documents = _live_documents()
    case = _merged_case(documents=documents)

    assert case["bill"]["line_items"] == _LIVE_LINE_ITEMS
    audit = _fronts(case, documents)["audit"]
    assert audit.applicable, audit.reason
    assert "no usable line items were extracted" not in audit.reason


def test_the_live_emailed_bill_reaches_every_front_its_documents_actually_support():
    """The whole acceptance criterion for `case-1a0412ccfef90917` in one place."""
    documents = _live_documents()
    case = {**_merged_case(documents=documents), "hospital": _SUTTER}
    fronts = _fronts(case, documents)

    assert fronts["audit"].applicable, fronts["audit"].reason
    # uninsured (GFE) + $2,625.00 - $1,925.00 = $700 >= $400, first statement
    # 2026-06-05 so the 120-day window runs to 2026-10-03.
    assert fronts["ppdr"].applicable, fronts["ppdr"].reason
    assert fronts["ppdr"].deadline == date(2026, 10, 3)
    assert "$700.00" in fronts["ppdr"].reason
    assert fronts["debt_validation"].applicable is False
    # Not applicable -- but for the ONE fact no document states.
    assert fronts["charity_care"].applicable is False
    assert "household size" in fronts["charity_care"].reason
    # ...and demonstrably NOT because income or state is unknown: both are.
    assert case["patient"]["annual_income_cents"] == 3200000
    assert case["patient"]["state"] == "CA"
    assert "household_size" not in case["patient"]


def test_the_gmail_path_and_the_demo_path_agree_on_what_the_documents_establish():
    """`case-1a0412ccfef90917` is the same underlying bill as PROOF's
    `case_01_uninsured_gfe_ca` / `ef-2026-0001`, which reaches the same fronts
    only because its fixture hands the case a ready-made `patient`/`bill`.
    Merging the documents must land on the same facts -- everything except
    `household_size`, which the fixture supplies and no document states.
    """
    fixture_path = (
        Path(__file__).resolve().parents[3]
        / "fixtures/generated/cases/case_01_uninsured_gfe_ca/case.json"
    )
    if not fixture_path.is_file():  # pragma: no cover -- fixtures not generated
        pytest.skip("fixtures/generated is not built in this checkout")
    fixture = json.loads(fixture_path.read_text(encoding="utf-8"))

    merged = _merged_case()
    for field, expected in fixture["bill"].items():
        if expected is None or expected is False:
            # The fixture spells out its nulls and its `in_collections: false`.
            # No document on file SAYS the account is not in collections --
            # the absence of a collection notice is not a statement -- so the
            # merge leaves the field absent, and `_select_debt_validation`
            # reads absent and False identically (`if not bill.get(...)`).
            assert not merged["bill"].get(field), field
        else:
            assert merged["bill"][field] == expected, field
    for field in ("name", "state", "insured", "annual_income_cents"):
        assert merged["patient"][field] == fixture["patient"][field], field
    assert fixture["patient"]["household_size"] == 3
    assert "household_size" not in merged["patient"]


# --------------------------------------------------------------------------
# Rule 1 -- never invent
# --------------------------------------------------------------------------


def test_household_size_is_never_taken_from_a_document_even_when_one_reports_it():
    """The honest boundary. No §3.1 document type states household size, so a
    model that emits one has inferred it -- and an FPL percentage computed on
    an invented household is defect #5 with a different field name."""
    documents = _live_documents()
    documents[2]["extracted"]["household_size"] = 1
    merged = _merged_case(documents=documents)
    assert "household_size" not in merged["patient"]


def test_the_merge_names_the_one_missing_fact_instead_of_defaulting_it():
    _patch, report = factmerge.merge_document_facts({"patient": {}, "bill": {}}, _live_documents())
    missing = {u["field"]: u["reason"] for u in report["unknown"]}
    assert "patient.household_size" in missing
    assert (
        "household size was not stated in any document on file" in missing["patient.household_size"]
    )
    for established in ("patient.state", "patient.insured", "patient.annual_income_cents"):
        assert established not in missing


def test_an_absent_coverage_statement_stays_absent_rather_than_becoming_insured():
    documents = [d for d in _live_documents() if d["type"] != "gfe"]
    merged = _merged_case(documents=documents)
    assert "insured" not in merged["patient"]
    # and PPDR must then refuse, rather than be handed a front on a default
    assert _fronts(merged, documents)["ppdr"].applicable is False


def test_a_zero_income_is_read_as_unknown_not_as_a_stated_zero():
    """`0` is also the extractor's not-found sentinel for an integer, and a
    fabricated $0 income screens as free-care eligible -- an invented
    determination. Unknown is the safe error."""
    documents = _live_documents()
    documents[2]["extracted"]["annual_income_cents"] = 0
    assert "annual_income_cents" not in _merged_case(documents=documents)["patient"]


def test_a_document_that_is_not_actually_an_income_proof_sets_no_income():
    """The cat-photo case (§4 persona 7 WO1)."""
    documents = _live_documents()
    documents[2]["extracted"] = {"annual_income_cents": 9999999, "is_income_proof": False}
    assert "annual_income_cents" not in _merged_case(documents=documents)["patient"]


def test_a_failed_extraction_contributes_nothing():
    documents = _live_documents()
    documents[0]["extracted"] = {"_extraction_error": "invalid JSON after 3 retries"}
    merged = _merged_case(documents=documents)
    assert "line_items" not in merged["bill"]
    assert "amount_cents" not in merged["bill"]
    # ...but the GFE and the pay stub still speak for themselves
    assert merged["bill"]["gfe_amount_cents"] == 192500
    assert merged["patient"]["annual_income_cents"] == 3200000


def test_a_generated_filing_is_not_evidence_about_the_bill():
    documents = _live_documents() + [
        {
            "doc_id": "doc-filed",
            "type": "generated_letter",
            "extracted": {"amount_cents": 1, "provider_name": "Some Other Hospital"},
        }
    ]
    merged = _merged_case(documents=documents)
    assert merged["bill"]["amount_cents"] == 262500
    assert merged["bill"]["provider_name"] == "Sutter Bay Hospitals"


# --------------------------------------------------------------------------
# Rule 2 -- explicit precedence, independent of arrival order
# --------------------------------------------------------------------------


def test_precedence_does_not_depend_on_the_order_documents_arrived_in():
    documents = _live_documents()
    forward = _merged_case(documents=documents)
    backward = _merged_case(documents=list(reversed(documents)))
    assert forward == backward


def test_a_good_faith_estimate_never_sets_the_amount_owed():
    """A GFE is an ESTIMATE. Letting it write `amount_cents` would collapse
    the PPDR delta (bill - GFE) to zero and erase the front it proves."""
    documents = _live_documents()
    documents[1]["extracted"]["amount_cents"] = 192500
    merged = _merged_case(documents=documents)
    assert merged["bill"]["amount_cents"] == 262500
    assert _fronts(merged, documents)["ppdr"].applicable is True


def test_the_bill_outranks_the_gfe_on_the_facts_they_both_carry():
    documents = _live_documents()
    documents[1]["extracted"]["hospital_ein"] = "00-1111111"
    documents[1]["extracted"]["provider_name"] = "Sutter Bay Hosp. (est.)"
    merged = _merged_case(documents=documents)
    assert merged["bill"]["hospital_ein"] == "94-0562680"
    assert merged["bill"]["provider_name"] == "Sutter Bay Hospitals"


def test_only_a_collection_notice_establishes_the_validation_notice_date():
    """The 30-day 12 CFR 1006.34 dispute clock runs off this date; a bill
    guessing at one would start a legal clock nobody was served."""
    documents = _live_documents()
    documents[0]["extracted"]["validation_notice_date"] = "2026-07-01"
    documents[0]["extracted"]["collector_name"] = "Not A Collector LLC"
    merged = _merged_case(documents=documents)
    assert "validation_notice_date" not in merged["bill"]
    assert "collector_name" not in merged["bill"]

    documents.append(
        {
            "doc_id": "doc-collect",
            "type": "collection_notice",
            "extracted": {
                "in_collections": True,
                "collector_name": "Bay Area Recovery Services",
                "validation_notice_date": "2026-08-10",
            },
        }
    )
    merged = _merged_case(documents=documents)
    assert merged["bill"]["validation_notice_date"] == "2026-08-10"
    assert merged["bill"]["collector_name"] == "Bay Area Recovery Services"
    assert merged["bill"]["in_collections"] is True


def test_a_collection_notice_outranks_a_bill_on_whether_the_account_is_in_collections():
    documents = _live_documents()
    documents[0]["extracted"]["in_collections"] = False
    documents.append(
        {
            "doc_id": "doc-collect",
            "type": "collection_notice",
            "extracted": {"in_collections": True},
        }
    )
    assert _merged_case(documents=documents)["bill"]["in_collections"] is True


def test_a_pay_stub_never_names_the_patient():
    """It names an EMPLOYEE. The earner in a household is not necessarily the
    patient, and a FAP application filed under the wrong name is worse than
    one filed under none."""
    documents = [d for d in _live_documents() if d["type"] == "income_proof"]
    documents[0]["extracted"]["patient_name"] = "Someone Else Entirely"
    assert "name" not in _merged_case(documents=documents)["patient"]


def test_a_collectors_letterhead_never_sets_the_governing_state():
    """State selects the whole deadline regime; the collector's address has
    nothing to do with which hospital-billing statute governs."""
    documents = [
        {
            "doc_id": "doc-collect",
            "type": "collection_notice",
            "extracted": {"state": "NV", "in_collections": True},
        }
    ]
    assert "state" not in _merged_case(documents=documents)["patient"]


# --------------------------------------------------------------------------
# Rule 3 -- never overwrite a better-established fact with a weaker one
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "junk"),
    [
        ("amount_cents", 0),
        ("amount_cents", None),
        ("provider_name", ""),
        ("provider_name", "   "),
        ("first_statement_date", ""),
        ("line_items", []),
        ("line_items", [{"description": "no code"}]),
    ],
)
def test_a_failed_extraction_never_overwrites_a_real_value(field, junk):
    """The $2,625 bill that became `amount=0`: the extractor returns 0 and ""
    for "not found" as well as null, and `is not None` let both through."""
    documents = _live_documents()
    established = documents[0]["extracted"][field]
    documents.append({"doc_id": "doc-bill-2", "type": "bill", "extracted": {field: junk}})
    assert _merged_case(documents=documents)["bill"][field] == established


def test_a_free_text_date_is_rejected_rather_than_stored():
    """It would occupy the field, block a lower-precedence ISO date, and then
    be silently dropped by `casedata.parse_bill_dates` -- leaving every clock
    looking like the document simply never carried a date."""
    documents = _live_documents()
    documents[0]["extracted"]["first_statement_date"] = "June 5, 2026"
    documents.append(
        {
            "doc_id": "doc-bill-2",
            "type": "bill",
            "extracted": {"first_statement_date": "2026-06-05"},
        }
    )
    assert _merged_case(documents=documents)["bill"]["first_statement_date"] == "2026-06-05"


@pytest.mark.parametrize("junk", ["California", "Sutter Bay", "CA 94304", "XX", ""])
def test_a_state_that_is_not_a_real_two_letter_code_is_refused(junk):
    documents = [{"doc_id": "d", "type": "itemized_bill", "extracted": {"state": junk}}]
    assert "state" not in _merged_case(documents=documents)["patient"]


def test_a_lowercase_state_code_is_normalized():
    documents = [{"doc_id": "d", "type": "itemized_bill", "extracted": {"state": " ca "}}]
    assert _merged_case(documents=documents)["patient"]["state"] == "CA"


def test_a_document_does_not_overwrite_a_patient_fact_the_case_already_carries():
    """A human intake form (§3.3 `POST /cases`) or a curated fixture is a
    stronger source than a model's read of a letterhead: no medical-billing
    document is a RECORD of a person, it only mentions one."""
    case = {
        "patient": {
            "name": "Someone A Human Typed",
            "household_size": 3,
            "state": "IL",
            "insured": True,
            "annual_income_cents": 4000000,
        },
        "bill": {},
    }
    merged = _merged_case(case=case)
    assert merged["patient"] == case["patient"]


def test_a_document_that_disagrees_with_the_case_is_reported_not_applied():
    case = {"patient": {"annual_income_cents": 4000000}, "bill": {}}
    _patch, report = factmerge.merge_document_facts(case, _live_documents())
    disagreements = {d["field"]: d for d in report["deferred"]}
    assert disagreements["patient.annual_income_cents"]["document_value"] == 3200000
    assert disagreements["patient.annual_income_cents"]["case_value"] == 4000000
    assert disagreements["patient.annual_income_cents"]["source_type"] == "income_proof"


def test_a_corrected_bill_document_still_moves_the_amount_owed():
    """`bill` is a projection of the documents, so unlike `patient` it is not
    gap-fill-only -- a re-read of a corrected statement must be able to move
    the number."""
    case = {"patient": {}, "bill": {"amount_cents": 100000}}
    assert _merged_case(case=case)["bill"]["amount_cents"] == 262500


# --------------------------------------------------------------------------
# Rule 4 -- idempotent and convergent (§2.3)
# --------------------------------------------------------------------------


def test_re_running_the_merge_over_an_unchanged_corpus_writes_nothing():
    documents = _live_documents()
    case = _merged_case(documents=documents)
    patch, report = factmerge.merge_document_facts(case, documents)
    assert patch == {}
    assert report["established"] == []


def test_the_merge_converges_rather_than_oscillating():
    documents = _live_documents()
    case = {"patient": {}, "bill": {}}
    seen = []
    for _ in range(4):
        case = _merged_case(case=case, documents=documents)
        seen.append(json.dumps(case, sort_keys=True))
    assert seen[1:] == seen[:-1]


# --------------------------------------------------------------------------
# The hospital-record backstop for `state`
# --------------------------------------------------------------------------


def test_the_resolved_hospital_record_can_supply_a_state_no_document_stated():
    assert factmerge.state_from_hospital(_SUTTER) == "CA"
    assert factmerge.state_from_hospital({"state": "Illinois"}) is None
    assert factmerge.state_from_hospital({}) is None
    assert factmerge.state_from_hospital(None) is None
