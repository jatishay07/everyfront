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

    def test_illinois_runs_from_latest_of_several_events(self):
        """IL's 90 days is shorter than the federal 240, so the floor wins.

        This is the invariant in _resolve_fap_window: 1.501(r) is a minimum,
        and a shorter state clock cannot narrow the federal right.
        """
        b = _bill(
            discharge_date=date(2026, 1, 12),
            screening_date=date(2026, 5, 20),
            public_program_denial_date=date(2026, 4, 1),
        )
        d = _front(compute_deadlines(b, "IL"), "Charity care")
        assert d.days == FAP_WINDOW_DAYS
        assert "federal floor" in d.citation
        assert "89/25" in d.citation

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
        STATE_FAP_WINDOWS["ZY"] = STATE_FAP_WINDOWS["IL"].__class__(
            365, "test-only", runs_from_latest_of=("discharge_date", "screening_date")
        )
        try:
            d = _front(compute_deadlines(b, "ZY"), "Charity care")
            assert d.basis_field == "screening_date"
            assert d.basis_date == date(2026, 5, 20)
        finally:
            del STATE_FAP_WINDOWS["ZY"]

    def test_ignores_non_date_values(self):
        STATE_FAP_WINDOWS["ZX"] = STATE_FAP_WINDOWS["IL"].__class__(
            365, "test-only", runs_from_latest_of=("discharge_date", "screening_date")
        )
        try:
            b = _bill(discharge_date=date(2026, 1, 12), screening_date="not a date")
            d = _front(compute_deadlines(b, "ZX"), "Charity care")
            assert d.basis_field == "discharge_date"
        finally:
            del STATE_FAP_WINDOWS["ZX"]

    def test_no_populated_trigger_yields_no_deadline(self):
        STATE_FAP_WINDOWS["ZW"] = STATE_FAP_WINDOWS["IL"].__class__(
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
