"""agent_core.agents.strategist -- persona 5 WO8 regression coverage over
STATUTE's real fix for the ef-2026-0006 defect (commit 69f4531):
`rules.fronts._select_charity_care` now requires a hospital's `nonprofit`
field to be the literal `True` to proceed, and `_select_audit` requires
actual usable line items, not just a document classification tag.

This persona used to carry its own local veto here as a stopgap while that
fix lived only upstream in a HANDOFF; it has been deleted now that STATUTE's
real fix lands the correct behavior directly out of `rules_bridge.select_fronts`
(see `_facts`'s comment) -- keeping a second copy after the real fix landed
would be exactly the duplicated-logic drift risk §2.1 exists to prevent.
These tests exist to prove the delegation actually produces the right
answer end-to-end, not to reimplement the check a second time.

`_facts` is a pure function over `rules_bridge.select_fronts` (no LLM, no
Firestore), so these are plain unit tests.
"""

from __future__ import annotations

from datetime import date, timedelta

from agent_core.agents import strategist


def _front(fronts: list[dict], name: str) -> dict:
    return next(f for f in fronts if f["front"] == name)


def test_charity_care_not_applicable_when_no_hospital_resolved():
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
    assert "not established" in cc["reason"]
    assert cc["deadline"] is None
    assert cc["status"] == "na"


def test_charity_care_not_applicable_when_hospital_is_an_empty_dict():
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


def test_charity_care_applicable_when_hospital_is_resolved_and_nonprofit():
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


def test_audit_not_applicable_with_zero_usable_line_items():
    """The other half of ef-2026-0006: a document classified as an itemized
    bill, but nothing was actually extracted from it (a failed read)."""
    case = {
        "patient": {"state": "CA", "insured": False},
        "bill": {"line_items": []},
        "documents": [{"type": "itemized_bill"}],
    }
    fact = strategist._facts("c1", case)
    audit = _front(fact["fronts"], "audit")
    assert audit["applicable"] is False


def test_other_fronts_unaffected_when_real_data_is_present():
    case = {
        "patient": {"state": "CA", "insured": False},
        "bill": {
            "amount_cents": 10_000_00,
            "gfe_amount_cents": 9_000_00,
            # Within the 120-day PPDR window as of "today" -- `_facts` calls
            # `rules_bridge.select_fronts` with no injected `today`, so this
            # must be relative to the real current date, not a fixed one.
            "first_statement_date": date.today() - timedelta(days=10),
            "line_items": [{"code": "99213", "units": 1, "charge_cents": 15000}],
        },
        "documents": [{"type": "itemized_bill"}],
    }
    fact = strategist._facts("c1", case)
    ppdr = _front(fact["fronts"], "ppdr")
    audit = _front(fact["fronts"], "audit")
    assert ppdr["applicable"] is True
    assert audit["applicable"] is True
