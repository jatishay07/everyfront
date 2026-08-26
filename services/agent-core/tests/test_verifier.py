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


# A resolved, nonprofit hospital -- the baseline every test below that is NOT
# specifically about hospital resolution (persona 5 WO8) uses, so the new
# hospital-resolution check (see `_facts`) does not confound the income/
# household checks these tests actually exercise.
_RESOLVED_HOSPITAL = {"name": "Test Hospital", "nonprofit": True}


def test_zero_household_size_sentinel_does_not_false_positive(monkeypatch):
    """The exact case_01 repro: a pay stub that never mentions household size
    extracts household_size=0 (Reader's sentinel), income correctly."""
    docs = [_income_doc("d1", annual_income_cents=3_200_000, household_size=0)]
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: docs)

    case = {
        "patient": {"annual_income_cents": 3_200_000, "household_size": 3},
        "hospital": _RESOLVED_HOSPITAL,
    }
    fact = verifier._facts("c1", case, "charity_care")

    assert fact["passed"] is True
    assert fact["issues"] == []


def test_a_real_household_size_mismatch_still_blocks(monkeypatch):
    """The fix must not blind Verifier to a REAL mismatch -- only the 0/absent
    sentinel is exempted."""
    docs = [_income_doc("d1", annual_income_cents=3_200_000, household_size=7)]
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: docs)

    case = {
        "patient": {"annual_income_cents": 3_200_000, "household_size": 3},
        "hospital": _RESOLVED_HOSPITAL,
    }
    fact = verifier._facts("c1", case, "charity_care")

    assert fact["passed"] is False
    assert any("household size 7" in issue for issue in fact["issues"])


def test_zero_income_sentinel_does_not_false_positive(monkeypatch):
    """The same 0-sentinel exposure existed on the income field (the guard
    used `is not None`, which a real 0 sentinel also satisfies) -- fixed the
    same way."""
    docs = [_income_doc("d1", annual_income_cents=0, household_size=3)]
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: docs)

    case = {
        "patient": {"annual_income_cents": 3_200_000, "household_size": 3},
        "hospital": _RESOLVED_HOSPITAL,
    }
    fact = verifier._facts("c1", case, "charity_care")

    assert fact["passed"] is True
    assert fact["issues"] == []


def test_a_real_income_mismatch_still_blocks(monkeypatch):
    docs = [_income_doc("d1", annual_income_cents=9_999_999, household_size=3)]
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: docs)

    case = {
        "patient": {"annual_income_cents": 3_200_000, "household_size": 3},
        "hospital": _RESOLVED_HOSPITAL,
    }
    fact = verifier._facts("c1", case, "charity_care")

    assert fact["passed"] is False
    assert any("outside the" in issue for issue in fact["issues"])


def test_no_income_proof_document_blocks_charity_care(monkeypatch):
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: [])
    case = {
        "patient": {"annual_income_cents": 3_200_000, "household_size": 3},
        "hospital": _RESOLVED_HOSPITAL,
    }
    fact = verifier._facts("c1", case, "charity_care")
    assert fact["passed"] is False
    assert "no income_proof document on file" in fact["issues"][0]


def test_cat_photo_check_blocks(monkeypatch):
    docs = [_income_doc("d1", is_income_proof=False)]
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: docs)
    case = {
        "patient": {"annual_income_cents": 3_200_000, "household_size": 3},
        "hospital": _RESOLVED_HOSPITAL,
    }
    fact = verifier._facts("c1", case, "charity_care")
    assert fact["passed"] is False
    assert "does not appear to actually be an income" in fact["issues"][0]


def test_non_charity_care_front_skips_income_checks(monkeypatch):
    """Verifier's income/household checks are specific to charity_care --
    PPDR/debt_validation/audit filings must not be blocked by them."""
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: [])
    case = {
        "patient": {"annual_income_cents": 3_200_000, "household_size": 3},
        "hospital": _RESOLVED_HOSPITAL,
    }
    fact = verifier._facts("c1", case, "ppdr")
    assert fact["passed"] is True


# --------------------------------------------------------------------------
# persona 5 WO8: "never file for a case whose facts were never established"
# -- the ef-2026-0006 defect (a fabricated hospital identity, then a real
# records-request letter sent to "unknown hospital" for a bill with zero
# extracted line items). See this module's docstring.
# --------------------------------------------------------------------------


def test_no_hospital_resolved_blocks_charity_care_filing(monkeypatch):
    """charity_care is a mail-channel filing (delivery_bridge.channel_for_front)
    -- it must not file addressed to an unresolved hospital."""
    docs = [_income_doc("d1", annual_income_cents=3_200_000, household_size=3)]
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: docs)
    case = {"patient": {"annual_income_cents": 3_200_000, "household_size": 3}}  # no hospital
    fact = verifier._facts("c1", case, "charity_care")
    assert fact["passed"] is False
    assert any("no hospital could be resolved" in issue for issue in fact["issues"])


def test_no_hospital_resolved_blocks_audit_filing(monkeypatch):
    """audit is also mail-channel -- the exact ef-2026-0006 repro (a records-
    request letter addressed to 'unknown hospital')."""
    monkeypatch.setattr(
        verifier.store,
        "list_documents",
        lambda case_id: [{"doc_id": "d1", "type": "bill", "extracted": {"line_items": [{}]}}],
    )
    case = {"patient": {}}  # no hospital
    fact = verifier._facts("c1", case, "audit")
    assert fact["passed"] is False
    assert any("no hospital could be resolved" in issue for issue in fact["issues"])


def test_hospital_resolution_not_required_for_ppdr(monkeypatch):
    """PPDR is filed by fax to CMS's C2C contractor regardless of which
    hospital this is -- it must not be blocked by hospital resolution."""
    monkeypatch.setattr(verifier.store, "list_documents", lambda case_id: [])
    case = {"patient": {}}  # no hospital
    fact = verifier._facts("c1", case, "ppdr")
    assert fact["passed"] is True


def test_audit_blocked_when_no_line_items_were_ever_extracted(monkeypatch):
    """The ef-2026-0006 repro's other half: a document classified as an
    itemized bill but with nothing actually extracted must not let `audit`
    file a records-request letter with nothing real to request."""
    monkeypatch.setattr(
        verifier.store,
        "list_documents",
        lambda case_id: [{"doc_id": "d1", "type": "itemized_bill", "extracted": {}}],
    )
    case = {"patient": {}, "hospital": _RESOLVED_HOSPITAL}
    fact = verifier._facts("c1", case, "audit")
    assert fact["passed"] is False
    assert any("no line items were ever extracted" in issue for issue in fact["issues"])


def test_audit_passes_with_real_line_items_and_a_resolved_hospital(monkeypatch):
    monkeypatch.setattr(
        verifier.store,
        "list_documents",
        lambda case_id: [
            {
                "doc_id": "d1",
                "type": "itemized_bill",
                "extracted": {"line_items": [{"code": "99213", "units": 1, "charge_cents": 15000}]},
            }
        ],
    )
    case = {"patient": {}, "hospital": _RESOLVED_HOSPITAL}
    fact = verifier._facts("c1", case, "audit")
    assert fact["passed"] is True
    assert fact["issues"] == []
