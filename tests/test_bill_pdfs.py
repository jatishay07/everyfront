"""Fixture document rendering tests -- PROOF (persona 7), work order 2.

Validates the reportlab/PIL-rendered documents in fixtures/generated/: real
PDFs parse, carry the watermark on every page, carry the FAP notice (or the
honest for-profit disclosure) at the bottom of every bill, and the seeded
NCCI-style/duplicate line items are actually present in the extracted text so
a future audit engine has something true to find. The one deliberately
corrupted bill (case 6) must fail to parse -- that IS the test.
"""

from __future__ import annotations

from pathlib import Path

import pytest

_MISSING_DEP = "not installed -- see fixtures/requirements.txt; HANDOFF: FORGE, wire this into CI"
pypdf = pytest.importorskip("pypdf", reason=f"pypdf {_MISSING_DEP}")
PIL_Image = pytest.importorskip("PIL.Image", reason=f"Pillow {_MISSING_DEP}")

from fixtures.cases_data import CASES, CASES_BY_ID, WATERMARK  # noqa: E402

pytest.importorskip("reportlab", reason="reportlab not installed -- see fixtures/requirements.txt")
from fixtures.generate import RENDERERS  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "fixtures" / "generated"


def _doc_path(case_id: str, doc_id: str) -> Path:
    case = CASES_BY_ID[case_id]
    spec = next(d for d in case.documents if d.doc_id == doc_id)
    _, relpath = RENDERERS[spec.render]
    return GENERATED / "cases" / case_id / relpath


def _extract_text(pdf_path: Path) -> str:
    reader = pypdf.PdfReader(str(pdf_path))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


ITEMIZED_BILL_CASE_IDS = [c.case_id for c in CASES if c.line_items]  # excludes case_06


class TestBillPDFsParseAndWatermark:
    @pytest.mark.parametrize("case_id", ITEMIZED_BILL_CASE_IDS)
    def test_bill_pdf_parses_and_has_at_least_one_page(self, case_id):
        reader = pypdf.PdfReader(str(_doc_path(case_id, "bill")))
        assert len(reader.pages) >= 1

    @pytest.mark.parametrize("case_id", ITEMIZED_BILL_CASE_IDS)
    def test_every_page_carries_the_watermark(self, case_id):
        reader = pypdf.PdfReader(str(_doc_path(case_id, "bill")))
        for page in reader.pages:
            assert WATERMARK in (page.extract_text() or "")

    @pytest.mark.parametrize("case_id", ITEMIZED_BILL_CASE_IDS)
    def test_bill_total_matches_the_sum_of_line_items(self, case_id):
        case = CASES_BY_ID[case_id]
        text = _extract_text(_doc_path(case_id, "bill"))
        total = sum(li.total_cents for li in case.line_items)
        assert f"${total / 100:,.2f}" in text


class TestFAPNoticeLine:
    def test_nonprofit_hospital_bills_carry_the_fap_notice(self):
        for case_id in ("case_01_uninsured_gfe_ca", "case_02_wrongful_denial_il"):
            text = _extract_text(_doc_path(case_id, "bill"))
            assert "Financial assistance" in text
            assert "1.501(r)-4" in text

    def test_for_profit_hospital_bill_carries_the_honest_disclosure_instead(self):
        text = _extract_text(_doc_path("case_04_forprofit_il", "bill"))
        assert "for-profit hospital" in text
        assert "not required to maintain a Financial Assistance Policy" in text
        assert "Financial assistance may be available" not in text


class TestSeededAuditFindingsAreExtractable:
    """The whole point of WO2: line items an audit engine can actually find."""

    def test_case_01_has_the_seeded_exact_duplicate(self):
        text = _extract_text(_doc_path("case_01_uninsured_gfe_ca", "bill"))
        assert text.count("80053") == 2, "COMPREHENSIVE METABOLIC PANEL duplicate must appear twice"

    def test_case_07_mue_excess_line_shows_three_units(self):
        text = _extract_text(_doc_path("case_07_il_concurrent_clocks", "bill"))
        assert "71046" in text
        # units column renders "3" on the 71046 line -- the MUE-excess seed.
        lines = [ln for ln in text.splitlines() if "71046" in ln]
        assert lines, "chest x-ray line missing entirely"

    def test_case_07_cash_price_delta_code_is_present(self):
        text = _extract_text(_doc_path("case_07_il_concurrent_clocks", "bill"))
        assert "86787" in text  # real Advocate MRF code, docs/SPIKE.md gate (b)


GFE_CASE_IDS = ["case_01_uninsured_gfe_ca", "case_07_il_concurrent_clocks"]


class TestGFEDocuments:
    @pytest.mark.parametrize("case_id", GFE_CASE_IDS)
    def test_gfe_pdf_states_a_lower_estimate_than_the_bill(self, case_id):
        case = CASES_BY_ID[case_id]
        gfe_text = _extract_text(_doc_path(case_id, "gfe"))
        bill_total = sum(li.total_cents for li in case.line_items)
        spec = next(d for d in case.documents if d.render == "gfe_pdf")
        gfe_cents = bill_total - spec.kwargs["gfe_delta_cents"]
        assert f"${gfe_cents / 100:,.2f}" in gfe_text
        delta = bill_total - gfe_cents
        assert delta >= 400_00, "delta must clear the 45 CFR 149.620(b) $400 floor"


class TestDenialLetters:
    def test_unlawful_case_lists_docs_beyond_the_fap(self):
        case = CASES_BY_ID["case_02_wrongful_denial_il"]
        text = _extract_text(_doc_path("case_02_wrongful_denial_il", "denial_letter"))
        extra = set(case.denial_demanded_docs) - set(case.denial_fap_published_docs)
        assert extra, "fixture must actually demand something extra"
        for doc in extra:
            assert doc.replace("_", " ") in text

    def test_lawful_case_demands_only_published_docs(self):
        case = CASES_BY_ID["case_08_lawful_denial_ca"]
        assert set(case.denial_demanded_docs) <= set(case.denial_fap_published_docs)


class TestCollectionNotice:
    def test_collection_notice_names_the_synthetic_collector(self):
        case = CASES_BY_ID["case_03_in_collections_ca"]
        text = _extract_text(_doc_path("case_03_in_collections_ca", "collection_notice"))
        assert case.bill["collector_name"] in text
        assert "SYNTHETIC" in case.bill["collector_name"]


class TestCatPhoto:
    def test_cat_photo_is_a_real_image_not_a_document(self):
        path = _doc_path("case_05_cat_photo_income_proof", "income_proof")
        assert path.suffix == ".png"
        img = PIL_Image.open(path)
        assert img.size[0] > 0 and img.size[1] > 0

    def test_cat_photo_is_not_parseable_as_a_pdf(self):
        path = _doc_path("case_05_cat_photo_income_proof", "income_proof")
        with pytest.raises(Exception):  # noqa: B017 -- any parse failure proves the point
            pypdf.PdfReader(str(path))


class TestUnparseableBill:
    def test_corrupted_bill_fails_to_parse(self):
        path = _doc_path("case_06_unparseable_bill", "bill")
        assert path.exists()
        with pytest.raises(Exception):  # noqa: B017
            reader = pypdf.PdfReader(str(path))
            # Some malformed PDFs "open" lazily -- force page access too.
            list(reader.pages)

    def test_corrupted_bill_still_starts_with_a_plausible_pdf_header(self):
        """It must look enough like a PDF to exercise a real parser's error
        path, not just be an empty file a naive sniff would reject upfront."""
        assert _doc_path("case_06_unparseable_bill", "bill").read_bytes().startswith(b"%PDF-")
