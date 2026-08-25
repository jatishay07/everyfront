"""agent_core.rules_bridge -- the defensive bridge to packages/rules.

STATUTE's select_fronts / audit_line_items / check_denial_lawfulness have
since shipped and merged, so these tests now exercise the REAL functions
(`bridge_sources()` reports "(STATUTE)", not "SWARM fallback") -- see
`test_bridge_actually_resolves_to_statute_not_the_vendored_fallback` below,
added after this bridge was found silently running its own fallback logic
during a test run because of a sys.path ordering trap (see conftest.py).
These tests intentionally overlap with STATUTE's own suite (tests/test_fronts.py
etc.) -- that overlap IS the point: it proves agent_core is wired to the real
package, not just that the package works in isolation.
"""

from __future__ import annotations

from datetime import date

from agent_core import rules_bridge


def test_bridge_sources_names_every_bridged_function():
    sources = rules_bridge.bridge_sources()
    assert set(sources) == {"select_fronts", "audit_line_items", "check_denial_lawfulness"}
    for source in sources.values():
        # Either STATUTE's real function landed, or we're honestly on the
        # fallback -- never silent about which.
        assert "STATUTE" in source or "fallback" in source


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


def test_bridge_actually_resolves_to_statute_not_the_vendored_fallback():
    """Regression guard for the sys.path ordering trap this module's
    docstring and conftest.py both describe: services/agent-core ships a
    VENDORED copy of packages/rules (for Cloud Build's per-service context),
    and it is easy to end up silently testing that stale copy's absence of
    fronts.py/audit.py/denial.py instead of STATUTE's real, merged package.
    """
    sources = rules_bridge.bridge_sources()
    for source in sources.values():
        assert "STATUTE" in source, (
            f"bridge fell back to a placeholder ({source!r}) -- either STATUTE's "
            "module moved, or sys.path is resolving `rules` to the vendored copy "
            "in services/agent-core/rules instead of packages/rules"
        )
