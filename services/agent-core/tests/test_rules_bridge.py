"""agent_core.rules_bridge -- direct re-export of packages/rules.

REWRITTEN 2026-08-26 (FORGE directive, persona 5 WO8): this module used to
carry its own fallback reimplementation of select_fronts/audit_line_items/
check_denial_lawfulness (used only if the real `rules.*` import ever
failed) -- and that fallback independently carried the exact "unresolved
hospital defaults to nonprofit=True" bug STATUTE fixed in the real
`rules.fronts` (ef-2026-0006, commit 69f4531). §2.1 ("all front-selection
logic lives in packages/rules") means a second copy here, however
defensive, is a liability, not a safety net -- it can only ever be wrong in
the same way a real bug was right. `rules_bridge` now just imports and
re-exports STATUTE's real functions directly, so these tests exercise the
REAL functions unconditionally. They intentionally overlap with STATUTE's
own suite (tests/test_fronts.py etc.) -- that overlap IS the point: it
proves agent_core is wired to the real package, not just that the package
works in isolation.
"""

from __future__ import annotations

from datetime import date

import rules.fronts
from agent_core import rules_bridge


def test_bridge_sources_names_every_bridged_function():
    sources = rules_bridge.bridge_sources()
    assert set(sources) == {
        "select_fronts",
        "audit_line_items",
        "check_denial_lawfulness",
        "total_savings_cents",
    }
    for source in sources.values():
        assert "STATUTE" in source


def test_select_fronts_debt_validation_first_when_in_collections():
    """`bill` dates must be real `date` objects, not ISO strings -- exactly
    like `compute_deadlines` (rules.fronts calls it internally and shares its
    isinstance(v, date) convention). Passing a string here is the trap
    agent_core.pipeline had to fix (see casedata.parse_bill_dates)."""
    case = {
        "bill": {
            "in_collections": True,
            "validation_notice_date": date(2026, 1, 1),
        },
        "patient": {"insured": True},
        "hospital": {"nonprofit": True},
    }
    decisions = rules_bridge.select_fronts(case, today=date(2026, 1, 15))
    dv = next(d for d in decisions if d.front == "debt_validation")
    assert dv.applicable is True


def test_select_fronts_ppdr_needs_400_delta_and_uninsured():
    case_ok = {
        "bill": {
            "amount_cents": 10_000_00,
            "gfe_amount_cents": 9_500_00,
            "first_statement_date": date(2026, 1, 1),
        },
        "patient": {"insured": False},
        "hospital": {},
    }
    case_short = {
        "bill": {
            "amount_cents": 10_000_00,
            "gfe_amount_cents": 9_650_00,  # $350 delta
            "first_statement_date": date(2026, 1, 1),
        },
        "patient": {"insured": False},
        "hospital": {},
    }
    today = date(2026, 1, 15)
    ok = next(d for d in rules_bridge.select_fronts(case_ok, today=today) if d.front == "ppdr")
    short = next(
        d for d in rules_bridge.select_fronts(case_short, today=today) if d.front == "ppdr"
    )
    assert ok.applicable is True
    assert short.applicable is False


def test_select_fronts_for_profit_hospital_no_charity_care():
    case = {"bill": {}, "patient": {}, "hospital": {"nonprofit": False}}
    cc = next(d for d in rules_bridge.select_fronts(case) if d.front == "charity_care")
    assert cc.applicable is False
    assert "no 501(r)" in cc.reason or "for-profit" in cc.reason


def test_audit_line_items_flags_exact_duplicates():
    items = [
        {"code": "99213", "units": 1, "charge_cents": 15000},
        {"code": "99213", "units": 1, "charge_cents": 15000},
    ]
    findings = rules_bridge.audit_line_items(items)
    assert any(f.kind == "duplicate" for f in findings)


def test_audit_line_items_no_findings_for_clean_bill():
    items = [
        {"code": "99213", "units": 1, "charge_cents": 15000},
        {"code": "80053", "units": 1, "charge_cents": 4500},
    ]
    assert rules_bridge.audit_line_items(items) == []


def test_check_denial_lawfulness_flags_unlisted_documents():
    result = rules_bridge.check_denial_lawfulness(
        demanded_docs=["Pay stub", "Notarized affidavit of unemployment"],
        fap_doc_list=["Pay stub", "Tax return"],
    )
    assert result.violation is True
    assert "Notarized affidavit of unemployment" in result.unlisted_docs


def test_check_denial_lawfulness_all_listed_is_lawful():
    result = rules_bridge.check_denial_lawfulness(
        demanded_docs=["Pay stub"], fap_doc_list=["Pay Stub", "Tax Return"]
    )
    assert result.violation is False
    assert result.unlisted_docs == ()


def test_select_fronts_unresolved_hospital_no_charity_care():
    """The ef-2026-0006 regression, exercised through the real function this
    bridge now re-exports directly (STATUTE's fix, commit 69f4531): an
    unresolved hospital (`{}`, no `nonprofit` key) must not default to
    "nonprofit" -- `nonprofit` must be the literal `True` to proceed."""
    case = {"bill": {}, "patient": {}, "hospital": {}}
    cc = next(d for d in rules_bridge.select_fronts(case) if d.front == "charity_care")
    assert cc.applicable is False
    assert "not established" in cc.reason


def test_select_fronts_missing_hospital_key_no_charity_care():
    case = {"bill": {}, "patient": {}}
    cc = next(d for d in rules_bridge.select_fronts(case) if d.front == "charity_care")
    assert cc.applicable is False


def test_bridge_re_exports_are_identical_to_the_real_functions():
    """This bridge no longer wraps or reimplements anything (see module
    docstring) -- it must be the literal same function object as
    `rules.fronts.select_fronts`, not a lookalike, and not a stale vendored
    copy shadowing it via sys.path (the historical trap conftest.py's own
    docstring describes)."""
    assert rules_bridge.select_fronts is rules.fronts.select_fronts
