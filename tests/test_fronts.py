"""Front-selector tests -- STATUTE (persona 3), work order 3.

The acceptance criterion names the decision tree explicitly: PPDR needs
uninsured + GFE + delta >= $400 + within 120d; charity care needs nonprofit +
under-threshold income + within window; in_collections + within 30d of a
validation notice must select debt validation FIRST because it freezes
everything else; an itemized bill always triggers audit. Each branch below
maps to one of those clauses, plus the graceful-degradation paths.
"""

from __future__ import annotations

from datetime import date

import pytest
from rules.fronts import FRONT_ORDER, FrontDecision, select_fronts

TODAY = date(2026, 3, 15)


def _decision(decisions: list[FrontDecision], front: str) -> FrontDecision:
    return next(d for d in decisions if d.front == front)


def _case(**overrides) -> dict:
    """A baseline case: uninsured CA patient, nonprofit hospital, no triggers armed."""
    case = {
        "patient": {
            "household_size": 4,
            "annual_income": 30_000_00,
            "insured": False,
            "state": "TX",
        },
        "bill": {
            "amount_cents": 10_000_00,
            "first_statement_date": date(2026, 1, 1),
        },
        "hospital": {
            "nonprofit": True,
            "free_care_max_fpl_pct": 200,
            "discounted_care_max_fpl_pct": 400,
        },
    }
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(case.get(key), dict):
            case[key] = {**case[key], **value}
        else:
            case[key] = value
    return case


class TestReturnsEveryFront:
    def test_one_decision_per_front(self):
        decisions = select_fronts(_case(), today=TODAY)
        assert {d.front for d in decisions} == set(FRONT_ORDER)
        assert len(decisions) == len(FRONT_ORDER)


class TestPPDR:
    def test_uninsured_gfe_over_threshold_within_window_is_applicable(self):
        case = _case(bill={"gfe_amount_cents": 9_600_00})  # delta = $400
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is True
        assert "149.620" in d.citation

    def test_delta_of_399_is_rejected(self):
        case = _case(bill={"gfe_amount_cents": 9_601_00})  # delta = $399
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False
        assert "399" in d.reason

    def test_delta_of_400_is_accepted(self):
        case = _case(bill={"amount_cents": 10_000_00, "gfe_amount_cents": 9_600_00})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is True

    def test_insured_patient_is_not_eligible(self):
        case = _case(patient={"insured": True}, bill={"gfe_amount_cents": 9_600_00})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False
        assert "the documents on file state this patient is insured" in d.reason

    def test_unstated_coverage_names_the_gap_rather_than_asserting_insurance(self):
        """`insured` absent/null means NO document says either way. The old
        wording ("coverage status unknown") did not say where the gap was;
        worse, any non-bool value fell through to "patient is insured", which
        asserted a fact no document established."""
        case = _case(patient={"insured": None}, bill={"gfe_amount_cents": 9_600_00})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False
        assert "insurance status was not stated in any document on file" in d.reason
        assert "this patient is insured" not in d.reason

    def test_unreadable_coverage_value_is_not_reported_as_insured(self):
        case = _case(patient={"insured": "self-pay?"}, bill={"gfe_amount_cents": 9_600_00})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False
        assert "insurance status is on file but unreadable" in d.reason
        assert "recorded as str, not a yes/no value" in d.reason
        assert "the documents on file state this patient is insured" not in d.reason

    def test_no_gfe_on_file_names_the_gfe_and_only_the_gfe(self):
        """The bill total IS on file in `_case()`; only the GFE is missing,
        so only the GFE may be named."""
        d = _decision(select_fronts(_case(), today=TODAY), "ppdr")
        assert d.applicable is False
        assert "no Good Faith Estimate amount is on file" in d.reason
        assert "billed amount" not in d.reason

    def test_missing_bill_total_is_not_blamed_on_the_gfe(self):
        """fixture case_06 (unparseable bill) has BOTH null and the old
        wording named only the Good Faith Estimate -- blaming the document
        the patient did send for the number the failed read lost."""
        case = _case(bill={"amount_cents": None, "gfe_amount_cents": None})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False
        assert "no Good Faith Estimate amount is on file" in d.reason
        assert "no billed amount is on file" in d.reason

    def test_gfe_present_but_bill_total_missing_names_the_bill_total(self):
        case = _case(bill={"amount_cents": None, "gfe_amount_cents": 100_00})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False
        assert "no billed amount is on file" in d.reason
        assert "no Good Faith Estimate amount is on file" not in d.reason

    def test_malformed_gfe_is_reported_as_unreadable_not_absent(self):
        case = _case(bill={"gfe_amount_cents": "not a number"})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False
        assert "the Good Faith Estimate amount on file is unreadable" in d.reason
        assert "recorded as str, not a whole-cents amount" in d.reason

    def test_missing_first_statement_date_yields_no_clock(self):
        case = _case(bill={"first_statement_date": None, "gfe_amount_cents": 9_600_00})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False
        assert "clock" in d.reason

    def test_expired_window_is_not_eligible(self):
        case = _case(bill={"first_statement_date": date(2025, 1, 1), "gfe_amount_cents": 9_600_00})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False
        assert "expired" in d.reason
        assert d.deadline is not None

    def test_applicable_decision_carries_its_deadline(self):
        from datetime import timedelta

        case = _case(bill={"gfe_amount_cents": 9_600_00})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.deadline == date(2026, 1, 1) + timedelta(days=120)


class TestCharityCare:
    def test_nonprofit_under_threshold_within_window_is_applicable(self):
        d = _decision(select_fronts(_case(), today=TODAY), "charity_care")
        assert d.applicable is True
        assert "1.501(r)-4(b)(2)" in d.citation

    def test_for_profit_hospital_has_no_501r_obligation(self):
        case = _case(hospital={"nonprofit": False})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "for-profit" in d.reason
        assert "1.501(r)-1(b)(18)" in d.citation

    def test_unresolved_hospital_does_not_get_the_benefit_of_the_doubt(self):
        """ef-2026-0006: Lookup could not resolve a hospital at all (no EIN,
        no name -- `hospital == {}`). Previously `nonprofit` defaulted to
        `True` for a missing key, which marked charity_care applicable for a
        facility nobody could name. It must now refuse instead of assume.

        Note: `_case(hospital={})` would MERGE an empty override onto the
        baseline hospital dict (see `_case`'s dict-merge rule) and so would
        not actually remove `nonprofit` -- the hospital dict is replaced
        outright here instead, to reproduce the real shape an unresolved
        `hospital` join actually takes.
        """
        case = _case()
        case["hospital"] = {}
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "not established" in d.reason
        assert "1.501(r)-1(b)(18)" in d.citation

    def test_nonprofit_key_present_but_not_a_bool_is_also_unresolved(self):
        case = _case(hospital={"nonprofit": None})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "not established" in d.reason

    def test_income_over_every_threshold_is_ineligible(self):
        case = _case(patient={"annual_income": 900_000_00})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "exceeds" in d.reason

    def test_both_thresholds_zero_is_unknown(self):
        case = _case(hospital={"free_care_max_fpl_pct": 0, "discounted_care_max_fpl_pct": 0})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "unknown" in d.reason

    def test_unknown_eligibility_reason_is_not_double_prefixed(self):
        """`EligibilityResult.explain()` already opens "Eligibility unknown:"
        and then names the specific gap. Wrapping it in a second "eligibility
        unknown:" printed the phrase twice on screen and added nothing."""
        case = _case(hospital={"free_care_max_fpl_pct": 0, "discounted_care_max_fpl_pct": 0})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.reason.lower().count("eligibility unknown") == 1
        assert d.reason.startswith("Eligibility unknown: ")
        # And the gap it names is a specific one, not a category.
        assert "no state statutory floor is on file for TX" in d.reason

    def test_california_never_expires(self):
        case = _case(
            patient={"state": "CA"},
            bill={"first_statement_date": date(2020, 1, 1)},
            hospital={"free_care_max_fpl_pct": 500, "discounted_care_max_fpl_pct": 500},
        )
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is True
        assert d.deadline is None

    def test_expired_federal_window_is_not_applicable(self):
        case = _case(bill={"first_statement_date": date(2020, 1, 1)})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "expired" in d.reason

    def test_explain_is_carried_as_the_reason_when_applicable(self):
        d = _decision(select_fronts(_case(), today=TODAY), "charity_care")
        assert "federal poverty level" in d.reason

    def test_annual_income_cents_key_takes_precedence_when_present(self):
        """A caller that already disambiguated cents vs dollars is honored first."""
        case = _case(patient={"annual_income_cents": 900_000_00, "annual_income": 30_000_00})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "exceeds" in d.reason


class TestCharityCareNamesTheMissingFact:
    """STATUTE wo8, from a real emailed bill (Sutter Bay / CA / self-pay).

    The documents established provider, state, self-pay status, income
    ($32,000/yr), the $2,625 bill and five line items. Exactly one fact
    appeared in no document: household size. `select_fronts` correctly
    declined charity care and then said "insufficient patient data (income,
    household size, or state) to screen eligibility" -- true, and useless.
    Income WAS established. State WAS established. And household size is the
    fact that decides the case: at household 3, $32,000 is 117% of the 2026
    FPL and clears CA's 400% floor (Cal. Health & Safety Code
    §127405(a)(1)(A)), erasing the whole bill.

    Every test here asserts BOTH halves: the missing fact is named, and the
    facts that are established are NOT named as missing.
    """

    def test_only_household_size_missing_names_only_household_size(self):
        case = _case(patient={"state": "CA", "annual_income_cents": 32_000_00})
        del case["patient"]["household_size"]
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "household size was not stated in any document on file" in d.reason
        assert "It is the only missing input" in d.reason
        assert "annual household income and state of residence are established" in d.reason
        # The defect: naming things that are not actually missing.
        assert "annual household income was not stated" not in d.reason
        assert "state of residence was not stated" not in d.reason
        # And the law that makes household size decisive is still cited.
        assert "91 FR 1797" in d.reason
        assert d.citation == "26 CFR 1.501(r)-4(b)(2)"

    def test_only_income_missing_names_only_income(self):
        case = _case(patient={"annual_income": None})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "annual household income was not stated in any document on file" in d.reason
        assert "household size and state of residence are established" in d.reason
        assert "household size was not stated" not in d.reason
        assert "1.501(r)-4(b)(2)" in d.reason

    def test_only_state_missing_names_only_state(self):
        case = _case(patient={"state": ""})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "state of residence was not stated in any document on file" in d.reason
        assert "annual household income and household size are established" in d.reason
        assert "§127405(a)(1)(A)" in d.reason

    def test_two_missing_facts_are_both_named(self):
        """Accuracy, not always-name-exactly-one: when two are genuinely
        absent, say both, and still credit the one that is established."""
        case = _case(patient={"annual_income": None, "household_size": None})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "annual household income was not stated in any document on file" in d.reason
        assert "household size was not stated in any document on file" in d.reason
        assert "(state of residence is established)" in d.reason
        assert "only missing input" not in d.reason

    def test_all_three_missing_says_nothing_is_established(self):
        case = _case(patient={"annual_income": None, "household_size": None, "state": ""})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "No patient fact this screen needs is established." in d.reason
        assert "are established." not in d.reason.replace(
            "No patient fact this screen needs is established.", ""
        )

    def test_unreadable_income_is_not_reported_as_never_stated(self):
        """A pay stub WAS provided; extraction produced a string. Telling the
        patient to send income proof they already sent is the wrong
        instruction. Same distinction as `_has_itemized_bill_document` vs
        `_usable_line_item_count`, applied to a patient fact."""
        case = _case(patient={"annual_income": "lots"})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "annual household income is on file but unreadable" in d.reason
        assert "recorded as str, not a whole number" in d.reason
        assert "annual household income was not stated" not in d.reason

    def test_unreadable_household_size_is_not_reported_as_never_stated(self):
        case = _case(patient={"household_size": "four"})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "household size is on file but unreadable" in d.reason
        assert "household size was not stated" not in d.reason

    def test_a_bool_household_size_is_unreadable_not_a_number(self):
        """`_is_plain_int` rejects bool; the reason must not then claim the
        field was never stated."""
        case = _case(patient={"household_size": True})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "recorded as bool, not a whole number" in d.reason

    def test_the_old_category_wording_is_gone(self):
        """The literal string this work order exists to delete. Any path that
        reintroduces "income, household size, or state" fails here."""
        for patient in (
            {"annual_income": None},
            {"household_size": None},
            {"state": ""},
            {"annual_income": None, "household_size": None, "state": ""},
        ):
            d = _decision(select_fronts(_case(patient=patient), today=TODAY), "charity_care")
            assert "insufficient patient data" not in d.reason
            assert "income, household size, or state" not in d.reason


class TestDebtValidation:
    def test_collection_status_never_recorded_says_so(self):
        """`in_collections` absent is not the same fact as `in_collections:
        False`, and only one of them is fixable by sending a document."""
        d = _decision(select_fronts(_case(), today=TODAY), "debt_validation")
        assert d.applicable is False
        assert "no collection status is recorded" in d.reason
        assert "states this account is not in collections" not in d.reason

    def test_recorded_as_not_in_collections_says_that_instead(self):
        case = _case(bill={"in_collections": False})
        d = _decision(select_fronts(case, today=TODAY), "debt_validation")
        assert d.applicable is False
        assert "the bill record states this account is not in collections" in d.reason
        assert "no collection status is recorded" not in d.reason

    def test_in_collections_without_notice_date_is_not_applicable(self):
        case = _case(bill={"in_collections": True})
        d = _decision(select_fronts(case, today=TODAY), "debt_validation")
        assert d.applicable is False
        assert "no validation-notice date" in d.reason

    def test_unreadable_notice_date_is_not_reported_as_a_missing_one(self):
        """A value IS on file; the extractor could not turn it into a date.
        Telling the patient to send a notice they already sent is the wrong
        instruction -- same distinction `_has_itemized_bill_document` vs
        `_usable_line_item_count` draws for documents."""
        case = _case(bill={"in_collections": True, "validation_notice_date": "March 1st"})
        d = _decision(select_fronts(case, today=TODAY), "debt_validation")
        assert d.applicable is False
        assert "unreadable" in d.reason
        assert "recorded as str, not a date" in d.reason
        assert "no validation-notice date is on file" not in d.reason

    def test_within_30_days_is_applicable(self):
        case = _case(bill={"in_collections": True, "validation_notice_date": date(2026, 3, 1)})
        d = _decision(select_fronts(case, today=date(2026, 3, 20)), "debt_validation")
        assert d.applicable is True
        assert d.deadline == date(2026, 3, 31)

    def test_day_31_is_blown(self):
        case = _case(bill={"in_collections": True, "validation_notice_date": date(2026, 1, 1)})
        d = _decision(select_fronts(case, today=date(2026, 2, 1)), "debt_validation")
        assert d.applicable is False
        assert "closed" in d.reason

    def test_day_29_is_still_open(self):
        case = _case(bill={"in_collections": True, "validation_notice_date": date(2026, 1, 1)})
        d = _decision(select_fronts(case, today=date(2026, 1, 30)), "debt_validation")
        assert d.applicable is True


class TestAudit:
    def test_no_itemized_bill_is_not_applicable(self):
        d = _decision(select_fronts(_case(), today=TODAY), "audit")
        assert d.applicable is False
        assert "no itemized bill on file" in d.reason

    def test_itemized_bill_with_usable_line_items_triggers_audit(self):
        case = _case(
            documents=[{"type": "denial_letter"}, {"type": "itemized_bill"}],
            bill={"line_items": [{"code": "99213"}]},
        )
        d = _decision(select_fronts(case, today=TODAY), "audit")
        assert d.applicable is True
        assert "1395b-7(b)" in d.citation

    def test_line_items_on_the_bill_also_trigger_audit(self):
        case = _case(bill={"line_items": [{"code": "99213"}]})
        d = _decision(select_fronts(case, today=TODAY), "audit")
        assert d.applicable is True

    def test_itemized_bill_document_with_no_usable_line_items_is_not_applicable(self):
        """ef-2026-0006: a document tagged `itemized_bill` reached the case,
        but extraction produced zero usable line items (the Reader could not
        parse it). This must NOT read as "audited, found nothing" -- it must
        read as "could not audit", and must not be applicable."""
        case = _case(documents=[{"type": "itemized_bill"}])
        d = _decision(select_fronts(case, today=TODAY), "audit")
        assert d.applicable is False
        assert "no usable line items were extracted" in d.reason
        assert "no itemized bill on file" not in d.reason

    def test_itemized_bill_document_with_only_malformed_line_items_is_not_applicable(self):
        case = _case(
            documents=[{"type": "itemized_bill"}],
            bill={"line_items": [{"code": ""}, {"code": None}, "not a dict"]},
        )
        d = _decision(select_fronts(case, today=TODAY), "audit")
        assert d.applicable is False
        assert "no usable line items were extracted" in d.reason

    def test_non_list_documents_is_handled_gracefully(self):
        case = _case(documents="not a list")
        d = _decision(select_fronts(case, today=TODAY), "audit")
        assert d.applicable is False

    def test_non_list_line_items_is_handled_gracefully(self):
        case = _case(bill={"line_items": "not a list"})
        d = _decision(select_fronts(case, today=TODAY), "audit")
        assert d.applicable is False

    def test_documents_present_but_none_are_itemized(self):
        case = _case(documents=[{"type": "denial_letter"}, {"type": "gfe"}])
        d = _decision(select_fronts(case, today=TODAY), "audit")
        assert d.applicable is False


class TestOrdering:
    """The ordering is load-bearing -- debt validation must sort first."""

    def test_debt_validation_sorts_first_when_applicable(self):
        case = _case(
            bill={
                "gfe_amount_cents": 9_600_00,
                "in_collections": True,
                "validation_notice_date": TODAY,
            },
            documents=[{"type": "itemized_bill"}],
        )
        decisions = select_fronts(case, today=TODAY)
        assert decisions[0].front == "debt_validation"
        assert decisions[0].applicable is True

    def test_other_applicable_fronts_are_flagged_as_sequenced(self):
        case = _case(
            bill={
                "gfe_amount_cents": 9_600_00,
                "in_collections": True,
                "validation_notice_date": TODAY,
            },
            documents=[{"type": "itemized_bill"}],
        )
        decisions = select_fronts(case, today=TODAY)
        for front in ("charity_care", "ppdr", "audit"):
            d = _decision(decisions, front)
            if d.applicable:
                assert "Sequenced after debt validation" in d.reason

    def test_default_order_is_the_contract_order_when_no_debt_validation(self):
        case = _case(documents=[{"type": "itemized_bill"}])
        decisions = select_fronts(case, today=TODAY)
        assert [d.front for d in decisions] == list(FRONT_ORDER)

    def test_not_applicable_debt_validation_does_not_reorder(self):
        decisions = select_fronts(_case(), today=TODAY)
        assert [d.front for d in decisions] == list(FRONT_ORDER)
        assert not any("Sequenced after" in d.reason for d in decisions)


class TestDefaultToday:
    def test_omitting_today_does_not_raise(self):
        # Exercises the `today is None -> date.today()` branch.
        decisions = select_fronts(_case())
        assert len(decisions) == 4


class TestExplain:
    def test_explain_names_the_front_and_citation(self):
        d = _decision(select_fronts(_case(), today=TODAY), "charity_care")
        text = d.explain()
        assert "charity_care" in text
        assert "1.501(r)" in text

    def test_explain_omits_deadline_tail_when_none(self):
        case = _case(hospital={"nonprofit": False})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert "Deadline:" not in d.explain()

    def test_explain_includes_deadline_tail_when_present(self):
        case = _case(bill={"gfe_amount_cents": 9_600_00})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert "Deadline:" in d.explain()


@pytest.mark.parametrize("front", FRONT_ORDER)
def test_every_front_always_carries_a_citation(front):
    for case in (_case(), _case(hospital={"nonprofit": False}), _case(patient={"insured": True})):
        d = _decision(select_fronts(case, today=TODAY), front)
        assert d.citation.strip()
