"""agent_core.agents.strategist -- persona 5 WO8's stopgap veto over
`rules.fronts`' real bug at `packages/rules/rules/fronts.py:121`
(`_select_charity_care`: `hospital.get("nonprofit", True) is False` treats an
UNRESOLVED hospital the same as a CONFIRMED nonprofit). See
`strategist._veto_charity_care_without_a_resolved_hospital`'s docstring for
the live repro (ef-2026-0006) and the HANDOFF to STATUTE.

`_facts` is a pure function over `rules_bridge.select_fronts` (no LLM, no
Firestore), so these are plain unit tests.
"""

from __future__ import annotations

from datetime import date, timedelta

from agent_core.agents import strategist


def _front(fronts: list[dict], name: str) -> dict:
    return next(f for f in fronts if f["front"] == name)


def test_charity_care_vetoed_when_no_hospital_resolved():
    """The exact ef-2026-0006 repro: a CA patient whose income alone would
    otherwise qualify for free care, but whose hospital was never resolved
    (Lookup returned nothing, so `case["hospital"]` is absent)."""
    case = {
        "patient": {
            "state": "CA",
            "insured": False,
            "annual_income_cents": 2_600_000,
            "household_size": 2,
        },
        "bill": {},
        # No "hospital" key at all -- Lookup could not resolve one.
    }
    fact = strategist._facts("c1", case)
    cc = _front(fact["fronts"], "charity_care")
    assert cc["applicable"] is False
    assert "no hospital could be resolved" in cc["reason"]
    assert cc["deadline"] is None
    assert cc["status"] == "na"


def test_charity_care_vetoed_when_hospital_is_an_empty_dict():
    case = {
        "patient": {
            "state": "CA",
            "insured": False,
            "annual_income_cents": 2_600_000,
            "household_size": 2,
        },
        "bill": {},
        "hospital": {},
    }
    fact = strategist._facts("c1", case)
    cc = _front(fact["fronts"], "charity_care")
    assert cc["applicable"] is False
    assert "no hospital could be resolved" in cc["reason"]


def test_charity_care_untouched_when_hospital_is_resolved_and_nonprofit():
    """The veto must be a no-op the moment a hospital is genuinely resolved --
    it should not touch STATUTE's own determination in the normal case."""
    case = {
        "patient": {
            "state": "CA",
            "insured": False,
            "annual_income_cents": 2_600_000,
            "household_size": 2,
        },
        "bill": {},
        "hospital": {"name": "Sutter Bay Hospitals", "nonprofit": True},
    }
    fact = strategist._facts("c1", case)
    cc = _front(fact["fronts"], "charity_care")
    assert cc["applicable"] is True
    assert "no hospital could be resolved" not in cc["reason"]


def test_other_fronts_are_not_touched_by_the_veto():
    """The veto is scoped to charity_care only -- ppdr/debt_validation/audit
    decisions must pass through untouched even when no hospital resolved."""
    case = {
        "patient": {"state": "CA", "insured": False},
        "bill": {
            "amount_cents": 10_000_00,
            "gfe_amount_cents": 9_000_00,
            # Within the 120-day PPDR window as of "today" -- `_facts` calls
            # `rules_bridge.select_fronts` with no injected `today`, so this
            # must be relative to the real current date, not a fixed one.
            "first_statement_date": date.today() - timedelta(days=10),
        },
        "documents": [{"type": "itemized_bill"}],
    }
    fact = strategist._facts("c1", case)
    ppdr = _front(fact["fronts"], "ppdr")
    audit = _front(fact["fronts"], "audit")
    assert ppdr["applicable"] is True
    assert audit["applicable"] is True
