"""Federal Poverty Level table tests -- STATUTE work order 2 dependency."""

from __future__ import annotations

import pytest
from rules.fpl import _FPL, fpl_annual_cents, income_as_fpl_pct, state_group


class TestStateGrouping:
    @pytest.mark.parametrize("st,expect", [("CA", "48"), ("IL", "48"), ("AK", "AK"), ("HI", "HI")])
    def test_groups(self, st, expect):
        assert state_group(st) == expect

    def test_is_case_and_whitespace_insensitive(self):
        assert state_group(" ak ") == "AK"


class TestFPLTable:
    def test_2026_one_person_48_states(self):
        """91 FR 1797: $15,960 for a household of one."""
        assert fpl_annual_cents(1, "CA", 2026) == 15_960_00

    def test_each_additional_person_adds_5680(self):
        assert fpl_annual_cents(4, "CA", 2026) - fpl_annual_cents(3, "CA", 2026) == 5_680_00

    def test_alaska_and_hawaii_are_higher(self):
        assert fpl_annual_cents(1, "AK", 2026) > fpl_annual_cents(1, "CA", 2026)
        assert fpl_annual_cents(1, "HI", 2026) > fpl_annual_cents(1, "CA", 2026)

    def test_rejects_household_below_one(self):
        with pytest.raises(ValueError, match="household_size"):
            fpl_annual_cents(0, "CA")

    def test_rejects_unknown_year_rather_than_extrapolating(self):
        with pytest.raises(ValueError, match="no FPL table"):
            fpl_annual_cents(1, "CA", 1999)


class TestFPLPercentage:
    def test_income_at_the_line_is_100_pct(self):
        assert income_as_fpl_pct(15_960_00, 1, "CA", 2026) == 100.0

    def test_double_the_line_is_200_pct(self):
        assert income_as_fpl_pct(31_920_00, 1, "CA", 2026) == 200.0

    def test_zero_income_is_zero_pct(self):
        assert income_as_fpl_pct(0, 3, "IL", 2026) == 0.0

    def test_matches_a_real_hospital_threshold(self):
        """Advocate (IL) publishes free care at 250% FPL -- spike gate (a).

        A household of four at $60k sits just under that line, so this is a
        realistic 'eligible' case rather than a synthetic one.
        """
        pct = income_as_fpl_pct(60_000_00, 4, "IL", 2026)
        assert 175 < pct < 185
        assert pct < 250


class TestTableMatchesPrimarySource:
    """Pin every (year, state group) entry to the Federal Register notice that
    published it. Each figure here was independently re-verified against the
    primary source on 2026-08-25 (STATUTE wo6, the citation audit) -- not
    copied from `rules/fpl.py`'s own table, so this test can actually catch a
    transcription error in that table rather than just restating it.

    2025 AK/HI regression: LEDGER found (and this audit independently
    confirmed against ASPE's 2025 detailed-guidelines PDF) that the AK and HI
    2025 per-person increments were each entered $10 low ($6,870 / $6,320
    instead of $6,880 / $6,330). This table encodes the corrected, verified
    values so a future transcription slip fails a test instead of silently
    shipping.
    """

    # (year, state group) -> (first_person, each_additional_person, citation)
    VERIFIED = {
        (2026, "48"): (15_960, 5_680, "91 FR 1797 (HHS, Jan. 15, 2026; FR Doc. 2026-00755)"),
        (2026, "AK"): (19_950, 7_100, "91 FR 1797 (HHS, Jan. 15, 2026; FR Doc. 2026-00755)"),
        (2026, "HI"): (18_360, 6_530, "91 FR 1797 (HHS, Jan. 15, 2026; FR Doc. 2026-00755)"),
        (2025, "48"): (15_650, 5_500, "90 FR 5917 (HHS, Jan. 17, 2025; FR Doc. 2025-01377)"),
        (2025, "AK"): (19_550, 6_880, "90 FR 5917 (HHS, Jan. 17, 2025; FR Doc. 2025-01377)"),
        (2025, "HI"): (17_990, 6_330, "90 FR 5917 (HHS, Jan. 17, 2025; FR Doc. 2025-01377)"),
    }

    def test_every_table_entry_has_a_pinned_expectation(self):
        """The verified set and the shipping table must cover exactly the same keys."""
        shipping_keys = {(year, group) for year, groups in _FPL.items() for group in groups}
        assert shipping_keys == set(self.VERIFIED)

    @pytest.mark.parametrize("year,group", sorted(VERIFIED))
    def test_value_matches_the_published_notice(self, year, group):
        first, additional, citation = self.VERIFIED[(year, group)]
        assert citation.strip(), "every pinned value must carry its Federal Register citation"
        assert _FPL[year][group] == (first, additional)
