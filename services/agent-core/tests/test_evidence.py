"""`agent_core.evidence` -- the descriptor that orders analysis passes by how
well-informed they were, rather than by which one happened to finish last."""

from __future__ import annotations

from agent_core import evidence


def _doc(doc_id: str, doc_type: str, extracted: dict | None = None) -> dict:
    return {"doc_id": doc_id, "type": doc_type, "extracted": extracted}


def test_more_documents_is_strictly_more_evidence():
    bill = _doc("d1", "itemized_bill", {"amount_cents": 262_500})
    stub = _doc("d2", "income_proof", {"annual_income_cents": 3_200_000})
    assert evidence.is_strictly_weaker(
        evidence.from_documents([bill]), evidence.from_documents([bill, stub])
    )
    assert not evidence.is_strictly_weaker(
        evidence.from_documents([bill, stub]), evidence.from_documents([bill])
    )


def test_identical_evidence_is_not_weaker_so_re_analysis_still_writes():
    """§2.3 idempotency: a redelivery that saw exactly the same documents must
    still be allowed to write, or the second run of an unchanged analysis
    would behave differently from the first."""
    docs = [_doc("d1", "bill", {"amount_cents": 1}), _doc("d2", "gfe", {"gfe_amount_cents": 2})]
    assert evidence.from_documents(docs) == evidence.from_documents(list(reversed(docs)))
    assert not evidence.is_strictly_weaker(
        evidence.from_documents(docs), evidence.from_documents(docs)
    )


def test_an_unread_document_is_not_evidence():
    """THE TRAP a doc-id set would have fallen into. A cascade re-reads the
    document store at its Auditor and Strategist steps, so it routinely SEES a
    document Reader has not classified yet -- `type` is `""`, `extracted` is
    absent, and `factmerge` skips it. Counting it would let a pass holding
    {d1-read, d2-unread} claim a superset of one holding {d1-read} and
    overwrite it with an answer built from strictly less."""
    read = _doc("d1", "bill", {"amount_cents": 262_500})
    unread = _doc("d2", "", None)
    assert evidence.from_documents([read, unread]) == evidence.from_documents([read])
    assert evidence.is_strictly_weaker(
        evidence.from_documents([read, unread]),
        evidence.from_documents([read, _doc("d2", "income_proof", {"annual_income_cents": 1})]),
    )


def test_a_filing_this_system_generated_is_not_evidence():
    """`generated_application`/`generated_letter` are documents this system
    produced. `pipeline.is_agent_generated` already keeps them from
    re-triggering analysis and `factmerge` already keeps them out of the
    merge; counting them here would let a stale cascade that started after a
    filing claim evidence it never used."""
    bill = _doc("d1", "bill", {"amount_cents": 1})
    filing = _doc("d2", "generated_application", {"form_id": "cms_ppdr"})
    assert evidence.from_documents([bill, filing]) == evidence.from_documents([bill])


def test_a_re_read_that_changes_the_extraction_is_different_evidence():
    """A document is identified by what can be READ off it, not by its id: a
    bill that was unreadable on the first pass and parsed on the second is
    genuinely new evidence, and must not be mistaken for the old one."""
    before = evidence.from_documents([_doc("d1", "bill", {"_extraction_error": "unreadable"})])
    after = evidence.from_documents([_doc("d1", "bill", {"amount_cents": 262_500})])
    assert before != after
    assert not evidence.is_strictly_weaker(after, before)


def test_no_evidence_never_overrules_some_evidence():
    assert evidence.is_strictly_weaker(
        evidence.EMPTY, evidence.from_documents([_doc("d1", "bill", {"amount_cents": 1})])
    )


def test_an_untracked_caller_is_never_treated_as_weaker():
    """The guard is opt-in: `evidence=None` keeps every pre-existing caller of
    `upsert_front_from_analysis` behaving exactly as it did."""
    some = evidence.from_documents([_doc("d1", "bill", {"amount_cents": 1})])
    assert not evidence.is_strictly_weaker(None, some)
    assert not evidence.is_strictly_weaker(some, None)
