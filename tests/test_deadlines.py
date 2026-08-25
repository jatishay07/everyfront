"""Deadline engine tests -- STATUTE (persona 3), work order 1.

The acceptance criterion (§4 persona 3) names specific cases: statement date
!= service date, the IL "latest of" trigger, CA's absence of a deadline, and
day-29-vs-31 on validation. Each is here.
"""

from __future__ import annotations

from datetime import date

import pytest
from rules.deadlines import (
    FAP_WINDOW_DAYS,
    STATE_FAP_WINDOWS,
    STATE_UNINSURED_DISCOUNTS,
    StateFAPRule,
    StateUninsuredDiscount,
    compute_deadlines,
)


def _bill(**kw) -> dict:
    base = {
        "service_date": date(2026, 1, 10),
        "first_statement_date": date(2026, 3, 1),
    }
    base.update(kw)
    return base


def _front(deadlines, name_contains):
    return next(d for d in deadlines if name_contains in d.name)


class TestFAPWindowBasisDate:
    """The clock runs from the first post-discharge statement, not service."""

    def test_uses_statement_date_not_service_date(self):
        d = _front(compute_deadlines(_bill(), "TX"), "Charity care")
        # 2026-03-01 + 240 days, NOT 2026-01-10 + 240 days.
        assert d.due == date(2026, 10, 27)
        assert d.basis_field == "first_statement_date"

    def test_service_date_would_have_been_wrong(self):
        """Guard the regression directly: the two bases differ by 50 days."""
        d = _front(compute_deadlines(_bill(), "TX"), "Charity care")
        wrong = date(2026, 1, 10) + __import__("datetime").timedelta(days=FAP_WINDOW_DAYS)
        assert d.due != wrong

    def test_missing_statement_date_yields_no_guess(self):
        b = _bill()
        del b["first_statement_date"]
        d = _front(compute_deadlines(b, "TX"), "Charity care")
        assert d.due is None, "must not invent a deadline from a missing date"


class TestStateOverrides:
    def test_california_has_no_charity_care_deadline(self):
        d = _front(compute_deadlines(_bill(), "CA"), "Charity care")
        assert d.due is None
        assert d.is_expired(date(2030, 1, 1)) is False
        assert "127405" in d.citation

    def test_washington_two_year_window_beats_federal_floor(self):
        d = _front(compute_deadlines(_bill(), "WA"), "Charity care")
        assert d.due == date(2028, 2, 29)  # 2026-03-01 + 730 days
        assert d.days == 730

    def test_illinois_keeps_the_full_federal_fap_window(self):
        """IL's 90-day act does NOT shorten the federal 240-day FAP window.

        The two are separate programs. Collapsing them either way loses a real
        right: the literal 90 would expire the federal claim ~150 days early.
        """
        b = _bill(discharge_date=date(2026, 1, 12))
        d = _front(compute_deadlines(b, "IL"), "Charity care application")
        assert d.days == FAP_WINDOW_DAYS
        assert "1.501(r)" in d.citation
        assert d.due == date(2026, 10, 27)

    def test_unlisted_state_falls_back_to_federal_floor(self):
        d = _front(compute_deadlines(_bill(), "ZZ"), "Charity care")
        assert d.days == FAP_WINDOW_DAYS
        assert "1.501(r)" in d.citation

    def test_state_code_is_case_insensitive(self):
        assert _front(compute_deadlines(_bill(), "ca"), "Charity care").due is None

    @pytest.mark.parametrize("st", sorted(STATE_FAP_WINDOWS))
    def test_every_state_rule_carries_a_citation(self, st):
        assert STATE_FAP_WINDOWS[st].citation.strip()


class TestValidationWindow:
    """12 CFR 1006.34 -- 30 days from the validation notice."""

    def test_day_29_is_still_open(self):
        b = _bill(validation_notice_date=date(2026, 6, 1))
        d = _front(compute_deadlines(b, "TX"), "Written dispute")
        assert d.is_expired(date(2026, 6, 30)) is False  # day 29

    def test_day_31_is_blown(self):
        b = _bill(validation_notice_date=date(2026, 6, 1))
        d = _front(compute_deadlines(b, "TX"), "Written dispute")
        assert d.is_expired(date(2026, 7, 2)) is True  # day 31

    def test_no_notice_means_no_validation_deadline(self):
        names = [d.name for d in compute_deadlines(_bill(), "TX")]
        assert not any("Written dispute" in n for n in names)


class TestPPDRAndECA:
    def test_ppdr_is_120_days_from_initial_bill(self):
        d = _front(compute_deadlines(_bill(), "TX"), "dispute resolution")
        assert d.due == date(2026, 6, 29)
        assert "149.620" in d.citation

    def test_eca_moratorium_is_120_days(self):
        d = _front(compute_deadlines(_bill(), "TX"), "Extraordinary collection")
        assert d.due == date(2026, 6, 29)
        assert "1.501(r)-6" in d.citation


class TestExplainability:
    """Agreement §2.2 -- every deadline must be able to show its work."""

    def test_explain_names_the_basis_and_the_citation(self):
        d = _front(compute_deadlines(_bill(), "TX"), "Charity care")
        text = d.explain(today=date(2026, 3, 2))
        assert "first_statement_date" in text
        assert "1.501(r)" in text
        assert "239 days remaining" in text

    def test_explain_reports_an_expired_deadline(self):
        d = _front(compute_deadlines(_bill(), "TX"), "Charity care")
        assert "EXPIRED" in d.explain(today=date(2027, 1, 1))

    def test_explain_handles_a_state_with_no_deadline(self):
        d = _front(compute_deadlines(_bill(), "CA"), "Charity care")
        assert "no deadline applies" in d.explain(today=date(2026, 3, 2))

    def test_every_deadline_carries_a_citation(self):
        b = _bill(validation_notice_date=date(2026, 6, 1))
        for d in compute_deadlines(b, "IL"):
            assert d.citation.strip(), f"{d.name} has no citation"


class TestLatestOfSelection:
    """IL's trigger picks the latest populated date and ignores junk."""

    def test_picks_the_latest_not_the_first(self):
        b = _bill(
            discharge_date=date(2026, 1, 12),
            screening_date=date(2026, 5, 20),
            public_program_denial_date=date(2026, 4, 1),
        )
        # WA is long enough that the state rule (not the federal floor) applies,
        # so we can observe which basis date the "latest of" logic chose.
        STATE_FAP_WINDOWS["ZY"] = StateFAPRule(
            365, "test-only", runs_from_latest_of=("discharge_date", "screening_date")
        )
        try:
            d = _front(compute_deadlines(b, "ZY"), "Charity care")
            assert d.basis_field == "screening_date"
            assert d.basis_date == date(2026, 5, 20)
        finally:
            del STATE_FAP_WINDOWS["ZY"]

    def test_ignores_non_date_values(self):
        STATE_FAP_WINDOWS["ZX"] = StateFAPRule(
            365, "test-only", runs_from_latest_of=("discharge_date", "screening_date")
        )
        try:
            b = _bill(discharge_date=date(2026, 1, 12), screening_date="not a date")
            d = _front(compute_deadlines(b, "ZX"), "Charity care")
            assert d.basis_field == "discharge_date"
        finally:
            del STATE_FAP_WINDOWS["ZX"]

    def test_no_populated_trigger_yields_no_deadline(self):
        STATE_FAP_WINDOWS["ZW"] = StateFAPRule(
            365, "test-only", runs_from_latest_of=("screening_date",)
        )
        try:
            d = _front(compute_deadlines(_bill(), "ZW"), "Charity care")
            assert d.due is None
        finally:
            del STATE_FAP_WINDOWS["ZW"]


def test_explain_without_today_omits_the_countdown():
    d = _front(compute_deadlines(_bill(), "TX"), "Charity care")
    text = d.explain()
    assert "remaining" not in text and "EXPIRED" not in text
    assert "1.501(r)" in text


class TestIllinoisUninsuredDiscount:
    """210 ILCS 89 runs ALONGSIDE the federal FAP window, not instead of it.

    Verified against the statute: "Hospitals shall permit an uninsured patient
    to apply for a discount within 90 days of the date of discharge or date of
    service." Enacted at 60 days, amended to 90.
    """

    def test_both_clocks_are_emitted(self):
        b = _bill(discharge_date=date(2026, 1, 12))
        names = [d.name for d in compute_deadlines(b, "IL", insured=False)]
        assert "Charity care application" in names
        assert "IL uninsured discount application" in names

    def test_the_state_clock_expires_first(self):
        """This is the whole point -- and the demo's drama (§2.6)."""
        b = _bill(discharge_date=date(2026, 1, 12))
        ds = compute_deadlines(b, "IL", insured=False)
        state = _front(ds, "uninsured discount")
        federal = _front(ds, "Charity care application")
        assert state.due < federal.due
        assert state.due == date(2026, 4, 12)  # 2026-01-12 + 90

    def test_runs_from_the_latest_trigger(self):
        b = _bill(discharge_date=date(2026, 1, 12), screening_date=date(2026, 3, 1))
        d = _front(compute_deadlines(b, "IL", insured=False), "uninsured discount")
        assert d.basis_date == date(2026, 3, 1)

    def test_confirmed_trigger_is_not_flagged(self):
        """Real IL: all four statutory triggers are now primary-source confirmed."""
        b = _bill(discharge_date=date(2026, 1, 12), screening_date=date(2026, 3, 1))
        d = _front(compute_deadlines(b, "IL", insured=False), "uninsured discount")
        assert "unverified" not in d.citation
        assert "210 ILCS 89" in d.citation

    def test_unconfirmed_trigger_is_flagged_not_hidden(self):
        """Agreement §2.2: a citation we have not verified must say so.

        As of the wo6 citation audit, all four of IL's real triggers are
        primary-source confirmed (see STATE_UNINSURED_DISCOUNTS["IL"]), so
        this branch is not reachable with real shipping data any more. It
        stays load-bearing for the next state added with a partially-checked
        trigger list, so it is exercised here with a synthetic entry rather
        than left untested until that day.
        """
        STATE_UNINSURED_DISCOUNTS["ZV"] = StateUninsuredDiscount(
            days=90,
            citation="Fake Stat. §9",
            max_fpl_pct=300,
            uninsured_only=False,
            runs_from_latest_of=("discharge_date", "screening_date"),
            confirmed_triggers=("discharge_date",),  # screening_date deliberately unconfirmed
        )
        try:
            b = _bill(discharge_date=date(2026, 1, 12), screening_date=date(2026, 3, 1))
            d = _front(compute_deadlines(b, "ZV", insured=False), "uninsured discount")
            assert "unverified" in d.citation
            assert d.basis_field == "screening_date"
        finally:
            del STATE_UNINSURED_DISCOUNTS["ZV"]

    def test_insured_patient_does_not_get_the_uninsured_discount(self):
        b = _bill(discharge_date=date(2026, 1, 12))
        names = [d.name for d in compute_deadlines(b, "IL", insured=True)]
        assert not any("uninsured discount" in n for n in names)

    def test_unknown_coverage_still_emits_the_deadline(self):
        """A missed state clock is unrecoverable; a spurious one is just noise."""
        b = _bill(discharge_date=date(2026, 1, 12))
        names = [d.name for d in compute_deadlines(b, "IL")]
        assert any("uninsured discount" in n for n in names)

    def test_for_profit_illinois_hospital_still_owes_the_discount(self):
        """210 ILCS 89 binds every IL hospital, 501(r) status notwithstanding.

        §1.2 routes for-profits to a "no 501(r) obligation" path. In Illinois
        that patient still holds this right, so the deadline must survive.
        """
        b = _bill(discharge_date=date(2026, 1, 12))
        d = _front(compute_deadlines(b, "IL", insured=False), "uninsured discount")
        assert d.due is not None

    def test_states_without_such_a_program_emit_only_the_federal_clock(self):
        b = _bill(discharge_date=date(2026, 1, 12))
        names = [d.name for d in compute_deadlines(b, "TX", insured=False)]
        assert not any("uninsured discount" in n for n in names)

    def test_missing_all_triggers_yields_no_guess(self):
        b = _bill()
        del b["service_date"]
        d = _front(compute_deadlines(b, "IL", insured=False), "uninsured discount")
        assert d.due is None


def test_a_shorter_state_fap_window_cannot_narrow_the_federal_floor():
    """26 CFR 1.501(r) is a minimum. A state may extend it, never shrink it.

    No shipping state currently trips this branch -- Illinois used to, before
    its 90-day rule was correctly identified as a separate program. The guard
    stays so the next state rule someone adds cannot silently cost a patient
    their federal window.
    """
    STATE_FAP_WINDOWS["ZQ"] = StateFAPRule(30, "Fake Stat. §1")
    try:
        d = _front(compute_deadlines(_bill(), "ZQ"), "Charity care application")
        assert d.days == FAP_WINDOW_DAYS
        assert "federal floor" in d.citation
        assert "Fake Stat. §1 is shorter at 30 days" in d.citation
    finally:
        del STATE_FAP_WINDOWS["ZQ"]
