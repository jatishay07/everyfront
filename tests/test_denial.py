"""Denial triage tests -- STATUTE (persona 3), work order 5.

The "24% of denials are paperwork" feature: a hospital may not deny financial
assistance for documentation its own published FAP doesn't list. This is a
set-difference, but the empty-FAP-list case must degrade to "insufficient
data" rather than accusing a hospital of a violation the extraction pipeline
simply failed to capture.
"""

from __future__ import annotations

from rules.denial import DenialCheck, check_denial_lawfulness


class TestViolation:
    def test_demanding_a_document_off_the_published_list_is_a_violation(self):
        result = check_denial_lawfulness(
            demanded_docs=["notarized affidavit of poverty", "pay stub"],
            fap_doc_list=["pay stub", "tax return"],
        )
        assert result.violation is True
        assert result.unlisted_docs == ("notarized affidavit of poverty",)
        assert "1.501(r)-4(b)(3)" in result.citation

    def test_every_demand_on_the_list_is_no_violation(self):
        result = check_denial_lawfulness(
            demanded_docs=["pay stub", "tax return"],
            fap_doc_list=["pay stub", "tax return", "bank statement"],
        )
        assert result.violation is False
        assert result.unlisted_docs == ()

    def test_comparison_is_case_and_whitespace_insensitive(self):
        result = check_denial_lawfulness(
            demanded_docs=["  Pay STUB  "],
            fap_doc_list=["pay stub"],
        )
        assert result.violation is False

    def test_no_documents_demanded_is_no_violation(self):
        result = check_denial_lawfulness(demanded_docs=[], fap_doc_list=["pay stub"])
        assert result.violation is False
        assert result.unlisted_docs == ()

    def test_multiple_unlisted_documents_are_all_captured(self):
        result = check_denial_lawfulness(
            demanded_docs=["notarized affidavit", "landlord letter", "pay stub"],
            fap_doc_list=["pay stub"],
        )
        assert result.violation is True
        assert set(result.unlisted_docs) == {"notarized affidavit", "landlord letter"}


class TestInsufficientData:
    def test_empty_fap_list_is_insufficient_data_not_a_violation(self):
        result = check_denial_lawfulness(demanded_docs=["pay stub"], fap_doc_list=[])
        assert result.insufficient_data is True
        assert result.violation is False
        assert result.unlisted_docs == ()

    def test_none_fap_list_is_insufficient_data(self):
        result = check_denial_lawfulness(demanded_docs=["pay stub"], fap_doc_list=None)
        assert result.insufficient_data is True

    def test_fap_list_of_only_blanks_is_insufficient_data(self):
        result = check_denial_lawfulness(demanded_docs=["pay stub"], fap_doc_list=["   ", ""])
        assert result.insufficient_data is True

    def test_non_string_entries_in_fap_list_are_dropped(self):
        result = check_denial_lawfulness(demanded_docs=["pay stub"], fap_doc_list=[None, 123])
        assert result.insufficient_data is True


class TestGracefulDegradation:
    def test_none_demanded_docs_is_treated_as_empty(self):
        result = check_denial_lawfulness(demanded_docs=None, fap_doc_list=["pay stub"])
        assert result.violation is False
        assert result.demanded_docs == ()

    def test_non_string_demanded_entries_are_dropped(self):
        result = check_denial_lawfulness(
            demanded_docs=[None, 42, "pay stub"], fap_doc_list=["pay stub"]
        )
        assert result.demanded_docs == ("pay stub",)
        assert result.violation is False

    def test_blank_demanded_entries_are_dropped(self):
        result = check_denial_lawfulness(
            demanded_docs=["   ", "pay stub"], fap_doc_list=["pay stub"]
        )
        assert result.demanded_docs == ("pay stub",)


class TestDraftedCitation:
    def test_drafted_citation_names_the_unlisted_documents(self):
        result = check_denial_lawfulness(
            demanded_docs=["notarized affidavit"], fap_doc_list=["pay stub"]
        )
        assert "notarized affidavit" in result.drafted_citation
        assert "1.501(r)-4(b)(3)" in result.drafted_citation
        assert "unlawful" in result.drafted_citation

    def test_drafted_citation_is_clean_when_no_violation(self):
        result = check_denial_lawfulness(demanded_docs=["pay stub"], fap_doc_list=["pay stub"])
        assert "unlawful" not in result.drafted_citation

    def test_drafted_citation_flags_insufficient_data(self):
        result = check_denial_lawfulness(demanded_docs=["pay stub"], fap_doc_list=[])
        assert "No FAP documentation list" in result.drafted_citation


class TestExplain:
    def test_explain_reports_a_violation(self):
        result = check_denial_lawfulness(
            demanded_docs=["notarized affidavit"], fap_doc_list=["pay stub"]
        )
        assert "Violation" in result.explain()
        assert "notarized affidavit" in result.explain()

    def test_explain_reports_no_violation(self):
        result = check_denial_lawfulness(demanded_docs=["pay stub"], fap_doc_list=["pay stub"])
        assert "No violation" in result.explain()

    def test_explain_reports_insufficient_data(self):
        result = check_denial_lawfulness(demanded_docs=["pay stub"], fap_doc_list=[])
        assert "Cannot assess" in result.explain()


def test_every_result_carries_the_citation():
    for demanded, fap in (
        (["pay stub"], ["pay stub"]),
        (["pay stub"], ["tax return"]),
        (["pay stub"], []),
        ([], []),
    ):
        result = check_denial_lawfulness(demanded, fap)
        assert isinstance(result, DenialCheck)
        assert result.citation.strip()
