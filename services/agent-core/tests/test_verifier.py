"""agent_core.agents.verifier -- persona 5 WO6 task 3: the household-size
false positive that blocked the demo's own happy path.

BUG (PROOF PR #23 HANDOFF #1, verified live on case_01): Reader's
JSON-schema extraction returns 0 -- not null -- as its "field not found"
sentinel for an integer the document never states. A pay stub has no reason
to mention household size, so it extracted `household_size: 0`; the old
`doc_household is not None` guard let that sentinel through as if it were a
real stated value of zero, and `0 != 3` (the case's real household size)
tripped a false-positive block. `_facts` is a pure function (no LLM, no
Firestore) so these are plain unit tests, not integration tests.
"""

from __future__ import annotations

from agent_core.agents import verifier


def _income_doc(doc_id, **extracted):
    return {
        "doc_id": doc_id,
        "type": "income_proof",
        "extracted": extracted,
    }


def test_zero_household_size_sentinel_does_not_false_positive(monkeypatch):
    """The exact case_01 repro: a pay stub that never mentions household size
    extracts household_size=0 (Reader's sentinel), income correctly."""
    docs = [_income_doc("d1", annual_income_cents=3_200_000, household_size=0)]
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: docs)

    case = {"patient": {"annual_income_cents": 3_200_000, "household_size": 3}}
    fact = verifier._facts("c1", case, "charity_care")

    assert fact["passed"] is True
    assert fact["issues"] == []


def test_a_real_household_size_mismatch_still_blocks(monkeypatch):
    """The fix must not blind Verifier to a REAL mismatch -- only the 0/absent
    sentinel is exempted."""
    docs = [_income_doc("d1", annual_income_cents=3_200_000, household_size=7)]
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: docs)

    case = {"patient": {"annual_income_cents": 3_200_000, "household_size": 3}}
    fact = verifier._facts("c1", case, "charity_care")

    assert fact["passed"] is False
    assert any("household size 7" in issue for issue in fact["issues"])


def test_zero_income_sentinel_does_not_false_positive(monkeypatch):
    """The same 0-sentinel exposure existed on the income field (the guard
    used `is not None`, which a real 0 sentinel also satisfies) -- fixed the
    same way."""
    docs = [_income_doc("d1", annual_income_cents=0, household_size=3)]
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: docs)

    case = {"patient": {"annual_income_cents": 3_200_000, "household_size": 3}}
    fact = verifier._facts("c1", case, "charity_care")

    assert fact["passed"] is True
    assert fact["issues"] == []


def test_a_real_income_mismatch_still_blocks(monkeypatch):
    docs = [_income_doc("d1", annual_income_cents=9_999_999, household_size=3)]
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: docs)

    case = {"patient": {"annual_income_cents": 3_200_000, "household_size": 3}}
    fact = verifier._facts("c1", case, "charity_care")

    assert fact["passed"] is False
    assert any("outside the" in issue for issue in fact["issues"])


def test_no_income_proof_document_blocks_charity_care(monkeypatch):
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: [])
    case = {"patient": {"annual_income_cents": 3_200_000, "household_size": 3}}
    fact = verifier._facts("c1", case, "charity_care")
    assert fact["passed"] is False
    assert "no income_proof document on file" in fact["issues"][0]


def test_cat_photo_check_blocks(monkeypatch):
    docs = [_income_doc("d1", is_income_proof=False)]
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: docs)
    case = {"patient": {"annual_income_cents": 3_200_000, "household_size": 3}}
    fact = verifier._facts("c1", case, "charity_care")
    assert fact["passed"] is False
    assert "does not appear to actually be an income" in fact["issues"][0]


def test_non_charity_care_front_skips_income_checks(monkeypatch):
    """Verifier's income/household checks are specific to charity_care --
    PPDR/debt_validation/audit filings must not be blocked by them."""
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: [])
    case = {"patient": {"annual_income_cents": 3_200_000, "household_size": 3}}
    fact = verifier._facts("c1", case, "ppdr")
    assert fact["passed"] is True
