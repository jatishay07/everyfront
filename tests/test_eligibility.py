"""Eligibility screen tests -- STATUTE work order 2.

The zero-sentinel class is the important one. IRS Schedule H reports an
unoffered discount tier as ``0``; read literally that screens every patient as
ineligible while looking perfectly healthy. Spike gate (a) found Sutter Bay
filing exactly that across all seven facilities.
"""

from __future__ import annotations

import pytest
from rules.eligibility import STATE_FLOORS, screen_eligibility

# Real thresholds from Advocate's IRS Schedule H filing (spike gate a).
ADVOCATE_IL = {
    "name": "Advocate Christ Medical Center",
    "free_care_max_fpl_pct": 250,
    "discounted_care_max_fpl_pct": 600,
    "nonprofit": True,
}
# Real Sutter Bay filing -- note discounted == 0, meaning NOT OFFERED.
SUTTER_CA = {
    "name": "Sutter Bay Hospitals",
    "free_care_max_fpl_pct": 400,
    "discounted_care_max_fpl_pct": 0,
    "nonprofit": True,
}

FPL4_2026 = 32_990_00  # household of 4, 48 states: 15,960 + 3*5,680


class TestZeroSentinel:
    """A 0 threshold means the tier is not offered -- never a 0% ceiling."""

    def test_zero_does_not_screen_everyone_ineligible(self):
        r = screen_eligibility(60_000_00, 4, "CA", SUTTER_CA)
        assert r.determination == "free"

    def test_zero_is_reported_as_not_offered(self):
        r = screen_eligibility(60_000_00, 4, "CA", SUTTER_CA)
        assert any("does not offer" in n for n in r.notes)

    def test_zero_free_tier_falls_through_to_discounted(self):
        h = {"free_care_max_fpl_pct": 0, "discounted_care_max_fpl_pct": 300}
        r = screen_eligibility(FPL4_2026 * 2, 4, "TX", h)
        assert r.determination == "discounted"

    def test_both_tiers_zero_is_unknown_not_ineligible(self):
        """Refusing to answer beats confidently denying someone their bill."""
        h = {"free_care_max_fpl_pct": 0, "discounted_care_max_fpl_pct": 0}
        r = screen_eligibility(FPL4_2026, 4, "TX", h)
        assert r.determination == "unknown"

    def test_negative_threshold_is_rejected(self):
        h = {"free_care_max_fpl_pct": -50, "discounted_care_max_fpl_pct": 300}
        r = screen_eligibility(FPL4_2026, 4, "TX", h)
        assert any("negative" in n for n in r.notes)


class TestTiers:
    def test_below_free_threshold(self):
        r = screen_eligibility(50_000_00, 4, "IL", ADVOCATE_IL)
        assert r.determination == "free"
        assert r.fpl_pct == pytest.approx(151.56, abs=0.1)

    def test_between_free_and_discounted(self):
        r = screen_eligibility(120_000_00, 4, "IL", ADVOCATE_IL)
        assert r.determination == "discounted"

    def test_above_every_threshold(self):
        r = screen_eligibility(400_000_00, 4, "IL", ADVOCATE_IL)
        assert r.determination == "ineligible"

    def test_boundary_is_inclusive(self):
        """At exactly the threshold the patient qualifies -- 'up to 250%'."""
        h = {"free_care_max_fpl_pct": 100, "discounted_care_max_fpl_pct": 200}
        r = screen_eligibility(FPL4_2026, 4, "TX", h)
        assert r.determination == "free"


class TestStateFloors:
    def test_california_floor_raises_a_stingy_hospital(self):
        stingy = {"free_care_max_fpl_pct": 150, "discounted_care_max_fpl_pct": 200}
        r = screen_eligibility(FPL4_2026 * 3, 4, "CA", stingy)
        assert r.free_threshold_pct == 400
        assert r.determination == "free"
        assert any("127405" in c for c in r.citations)

    def test_floor_never_lowers_a_generous_hospital(self):
        r = screen_eligibility(FPL4_2026 * 5, 4, "IL", ADVOCATE_IL)
        assert r.discounted_threshold_pct == 600  # not lowered to IL's 300

    def test_illinois_floor_binds_for_profit_hospitals(self):
        """210 ILCS 89 reaches every IL hospital, unlike 26 CFR 1.501(r).

        400% FPL sits between the general-hospital free floor (200%) and its
        discount ceiling (600%) -- verified against ilga.gov, 210 ILCS 89/10.
        """
        for_profit = {
            "free_care_max_fpl_pct": None,
            "discounted_care_max_fpl_pct": None,
            "nonprofit": False,
        }
        r = screen_eligibility(FPL4_2026 * 4, 4, "IL", for_profit)
        assert r.determination == "discounted"
        assert any("210 ILCS 89" in c for c in r.citations)

    def test_illinois_general_hospital_free_floor_is_200_pct(self):
        """The corrected general-hospital floor also sets a FREE tier -- the
        pre-audit table had no free floor for IL at all."""
        h = {"free_care_max_fpl_pct": None, "discounted_care_max_fpl_pct": None}
        r = screen_eligibility(FPL4_2026 * 2, 4, "IL", h)  # 200% FPL, at the line
        assert r.determination == "free"
        assert r.free_threshold_pct == 200

    def test_illinois_rural_critical_access_gets_the_lower_tier(self):
        """A hospital explicitly flagged rural/CAH gets 125%/300%, not 200%/600%."""
        h = {
            "free_care_max_fpl_pct": None,
            "discounted_care_max_fpl_pct": None,
            "rural_or_critical_access": True,
        }
        r = screen_eligibility(FPL4_2026 * 4, 4, "IL", h)  # 400% FPL
        assert r.determination == "ineligible"  # above the rural 300% discount ceiling
        assert r.discounted_threshold_pct == 300
        assert any("rural/critical-access hospital tier applied" in n for n in r.notes)

    def test_illinois_unclassified_hospital_notes_the_assumption(self):
        """When rural/CAH status is unknown, the general floor applies but the
        assumption is surfaced rather than presented as a confirmed fact."""
        h = {"free_care_max_fpl_pct": None, "discounted_care_max_fpl_pct": None}
        r = screen_eligibility(FPL4_2026 * 4, 4, "IL", h)
        assert r.discounted_threshold_pct == 600
        assert any("assuming the more common class" in n for n in r.notes)

    def test_washington_large_system_gets_the_higher_tier(self):
        h = {
            "free_care_max_fpl_pct": None,
            "discounted_care_max_fpl_pct": None,
            "large_system": True,
        }
        r = screen_eligibility(FPL4_2026 * 3, 4, "WA", h)  # 300% FPL
        assert r.determination == "free"  # large-system free floor is 300%
        assert r.free_threshold_pct == 300

    def test_washington_default_is_the_smaller_non_large_system_floor(self):
        h = {"free_care_max_fpl_pct": None, "discounted_care_max_fpl_pct": None}
        r = screen_eligibility(FPL4_2026 * 3, 4, "WA", h)  # 300% FPL
        assert r.determination == "discounted"  # non-large-system ceiling is 300%
        assert r.discounted_threshold_pct == 300

    def test_state_floor_classification_false_is_treated_like_absent(self):
        """An explicit False is not "unknown" -- it means confirmed non-elevated,
        so no assumption note should be appended."""
        h = {
            "free_care_max_fpl_pct": None,
            "discounted_care_max_fpl_pct": None,
            "rural_or_critical_access": False,
        }
        r = screen_eligibility(FPL4_2026 * 4, 4, "IL", h)
        assert r.discounted_threshold_pct == 600
        assert not any("assuming the more common class" in n for n in r.notes)

    def test_for_profit_gets_no_501r_citation(self):
        for_profit = {
            "free_care_max_fpl_pct": 200,
            "discounted_care_max_fpl_pct": 300,
            "nonprofit": False,
        }
        r = screen_eligibility(FPL4_2026, 4, "TX", for_profit)
        assert not any("1.501(r)" in c for c in r.citations)

    @pytest.mark.parametrize("st", sorted(STATE_FLOORS))
    def test_every_floor_carries_a_citation(self, st):
        assert STATE_FLOORS[st].citation.strip()


class TestDegradation:
    """Never raise mid-caseload; return 'unknown' and let a human decide."""

    def test_empty_hospital_record_is_unknown(self):
        r = screen_eligibility(FPL4_2026, 4, "TX", {})
        assert r.determination == "unknown"

    def test_bad_household_size_is_unknown_not_an_exception(self):
        r = screen_eligibility(FPL4_2026, 0, "TX", ADVOCATE_IL)
        assert r.determination == "unknown"
        assert any("household_size" in n for n in r.notes)

    def test_explain_always_produces_text(self):
        for h in ({}, ADVOCATE_IL, SUTTER_CA):
            assert screen_eligibility(FPL4_2026, 4, "CA", h).explain().strip()


class TestNamesTheMissingInput:
    """STATUTE wo8: an "unknown" must say WHICH input is missing.

    `explain()` for an unknown determination is what `select_fronts` now
    carries verbatim into `FrontDecision.reason` -- it appears on screen in
    the demo, so it has to be a gap a reader can act on, not a category.
    """

    def test_unsupported_state_says_no_floor_is_on_file_for_it(self):
        r = screen_eligibility(FPL4_2026, 4, "TX", {})
        assert r.determination == "unknown"
        assert any("no state statutory floor is on file for TX" in n for n in r.notes)
        # Honest about OUR coverage, not a legal claim about Texas law.
        assert not any("TX has no" in n for n in r.notes)
        assert any("CA" in n and "IL" in n and "WA" in n for n in r.notes)

    def test_the_engines_floor_list_excludes_hospital_class_tiers(self):
        """IL_RURAL / WA_LARGE_SYSTEM are tiers of a state, not states a
        caller could pass -- listing them would misdescribe our coverage."""
        r = screen_eligibility(FPL4_2026, 4, "TX", {})
        note = next(n for n in r.notes if "no state statutory floor is on file" in n)
        assert "IL_RURAL" not in note and "WA_LARGE_SYSTEM" not in note

    def test_blank_state_says_the_state_is_not_recorded(self):
        r = screen_eligibility(FPL4_2026, 4, "", {})
        assert r.determination == "unknown"
        assert any("the patient's state is not recorded" in n for n in r.notes)

    def test_no_threshold_note_names_the_hospital_record_not_a_category(self):
        r = screen_eligibility(FPL4_2026, 4, "TX", {})
        assert any(
            "the hospital record supplies neither a free-care nor a discounted-care FPL "
            "threshold" in n
            for n in r.notes
        )
        assert not any("from the hospital record or state law" in n for n in r.notes)

    def test_a_state_with_a_floor_does_not_get_the_missing_floor_note(self):
        r = screen_eligibility(FPL4_2026, 4, "CA", {})
        assert not any("no state statutory floor is on file" in n for n in r.notes)


class TestExplainability:
    def test_explain_shows_the_arithmetic_and_the_law(self):
        text = screen_eligibility(50_000_00, 4, "IL", ADVOCATE_IL).explain()
        assert "% of the federal poverty level" in text
        assert "250%" in text
        assert "1.501(r)" in text


class TestExplainCoversEveryBranch:
    """explain() text is filed with applications -- every path must read well."""

    def test_discounted_wording_names_both_thresholds(self):
        text = screen_eligibility(120_000_00, 4, "IL", ADVOCATE_IL).explain()
        assert "250%" in text and "600%" in text
        assert "free-care threshold but at or below" in text

    def test_ineligible_wording_names_the_ceiling(self):
        text = screen_eligibility(400_000_00, 4, "IL", ADVOCATE_IL).explain()
        assert "Above the highest applicable threshold (600%)" in text

    def test_ineligible_falls_back_to_free_ceiling_when_no_discount_tier(self):
        h = {"free_care_max_fpl_pct": 100, "discounted_care_max_fpl_pct": 0}
        text = screen_eligibility(FPL4_2026 * 4, 4, "TX", h).explain()
        assert "(100%)" in text

    def test_unknown_wording_lists_the_reason(self):
        assert "Eligibility unknown" in screen_eligibility(FPL4_2026, 4, "TX", {}).explain()

    def test_no_citations_still_explains(self):
        h = {"free_care_max_fpl_pct": 200, "discounted_care_max_fpl_pct": 300, "nonprofit": False}
        text = screen_eligibility(FPL4_2026, 4, "TX", h).explain()
        assert "Basis:" not in text and text.strip()


class TestFloorEdges:
    def test_floor_with_no_free_tier_leaves_free_alone(self):
        """A state floor that sets only a discounted tier must not touch free.

        Neither shipping state currently does this (both CA and the
        corrected IL table set a free floor too), so this exercises the
        `floor.free_pct is None` branch with a synthetic state rather than
        leaving it untested.
        """
        from rules.eligibility import StateFloor

        STATE_FLOORS["ZD"] = StateFloor(
            free_pct=None,
            discounted_pct=200,
            citation="Fake Stat. §3 (discount-only floor)",
        )
        try:
            h = {"free_care_max_fpl_pct": 150, "discounted_care_max_fpl_pct": 100}
            r = screen_eligibility(FPL4_2026, 4, "ZD", h)
            assert r.free_threshold_pct == 150, "no free floor -- hospital's own number stands"
            assert r.discounted_threshold_pct == 200, "raised to the state's discount floor"
        finally:
            del STATE_FLOORS["ZD"]

    def test_hospital_already_above_the_floor_is_untouched(self):
        h = {"free_care_max_fpl_pct": 500, "discounted_care_max_fpl_pct": 700}
        r = screen_eligibility(FPL4_2026, 4, "CA", h)
        assert r.free_threshold_pct == 500
        assert not any("raised to" in n for n in r.notes)


def test_a_nonprofit_only_floor_does_not_reach_for_profit_hospitals():
    """Not every state act binds for-profits -- 26 CFR 1.501(r) certainly doesn't.

    No shipping state currently sets applies_to_for_profit=False (both CA and
    IL reach every hospital), so this exercises the guard with a synthetic rule
    rather than leaving the branch untested until someone adds such a state.
    """
    from rules.eligibility import StateFloor

    STATE_FLOORS["ZF"] = StateFloor(
        free_pct=900,
        discounted_pct=900,
        citation="Fake Stat. §2",
        applies_to_for_profit=False,
        note="",
    )
    try:
        for_profit = {
            "free_care_max_fpl_pct": 100,
            "discounted_care_max_fpl_pct": 150,
            "nonprofit": False,
        }
        r = screen_eligibility(FPL4_2026 * 3, 4, "ZF", for_profit)
        assert r.free_threshold_pct == 100, "floor must not reach a for-profit here"
        assert r.determination == "ineligible"
        assert not any("Fake Stat" in c for c in r.citations)

        nonprofit = {
            "free_care_max_fpl_pct": 100,
            "discounted_care_max_fpl_pct": 150,
            "nonprofit": True,
        }
        r2 = screen_eligibility(FPL4_2026 * 3, 4, "ZF", nonprofit)
        assert r2.free_threshold_pct == 900, "floor must reach a nonprofit"
        assert r2.determination == "free"
        # floor.note is empty here -- the note branch must stay quiet.
        assert not any(n == "" for n in r2.notes)
    finally:
        del STATE_FLOORS["ZF"]
