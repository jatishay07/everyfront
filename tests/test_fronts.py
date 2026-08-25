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
        assert "insured" in d.reason

    def test_unknown_coverage_is_not_eligible(self):
        case = _case(patient={"insured": None}, bill={"gfe_amount_cents": 9_600_00})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False
        assert "unknown" in d.reason

    def test_no_gfe_on_file_is_not_eligible(self):
        d = _decision(select_fronts(_case(), today=TODAY), "ppdr")
        assert d.applicable is False
        assert "Good Faith Estimate" in d.reason

    def test_malformed_gfe_is_treated_as_absent(self):
        case = _case(bill={"gfe_amount_cents": "not a number"})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False

    def test_malformed_amount_is_treated_as_absent(self):
        case = _case(bill={"amount_cents": None, "gfe_amount_cents": 100_00})
        d = _decision(select_fronts(case, today=TODAY), "ppdr")
        assert d.applicable is False

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

    def test_income_over_every_threshold_is_ineligible(self):
        case = _case(patient={"annual_income": 900_000_00})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "exceeds" in d.reason

    def test_missing_income_is_insufficient_data(self):
        case = _case(patient={"annual_income": None})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "insufficient" in d.reason

    def test_missing_household_size_is_insufficient_data(self):
        case = _case(patient={"household_size": None})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "insufficient" in d.reason

    def test_missing_state_is_insufficient_data(self):
        case = _case(patient={"state": ""})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "insufficient" in d.reason

    def test_non_int_income_is_insufficient_data(self):
        case = _case(patient={"annual_income": "lots"})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "insufficient" in d.reason

    def test_both_thresholds_zero_is_unknown(self):
        case = _case(hospital={"free_care_max_fpl_pct": 0, "discounted_care_max_fpl_pct": 0})
        d = _decision(select_fronts(case, today=TODAY), "charity_care")
        assert d.applicable is False
        assert "unknown" in d.reason

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


class TestDebtValidation:
    def test_not_in_collections_is_not_applicable(self):
        d = _decision(select_fronts(_case(), today=TODAY), "debt_validation")
        assert d.applicable is False
        assert "not reported in collections" in d.reason

    def test_in_collections_without_notice_date_is_not_applicable(self):
        case = _case(bill={"in_collections": True})
        d = _decision(select_fronts(case, today=TODAY), "debt_validation")
        assert d.applicable is False
        assert "no validation-notice date" in d.reason

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

    def test_itemized_bill_in_documents_triggers_audit(self):
        case = _case(documents=[{"type": "denial_letter"}, {"type": "itemized_bill"}])
        d = _decision(select_fronts(case, today=TODAY), "audit")
        assert d.applicable is True
        assert "1395b-7(b)" in d.citation

    def test_line_items_on_the_bill_also_trigger_audit(self):
        case = _case(bill={"line_items": [{"code": "99213"}]})
        d = _decision(select_fronts(case, today=TODAY), "audit")
        assert d.applicable is True

    def test_non_list_documents_is_handled_gracefully(self):
        case = _case(documents="not a list")
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
