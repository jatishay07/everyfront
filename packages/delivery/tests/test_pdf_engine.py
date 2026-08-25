"""PDF engine -- §4 persona 4 WO2.

Requires `pypdf` and `reportlab`, which CI's base install does not include
(see the PR HANDOFF re: adding this package's `requirements.txt` to CI).
`pytest.importorskip` makes that a clean skip rather than a collection
failure everywhere else in the suite -- see `pdf/engine.py`'s module
docstring for the reasoning. Run these for real with
`.venv/bin/pytest packages/delivery` in a venv that has the two libraries
installed (this repo's `.venv` does).
"""

from __future__ import annotations

import datetime as dt
import io

import pytest

pypdf = pytest.importorskip("pypdf")
pytest.importorskip("reportlab")

from delivery.pdf import FORM_REGISTRY, fill_form  # noqa: E402
from delivery.pdf.forms import TEMPLATES_DIR  # noqa: E402

# Shape matches contract §3.1 `cases/{case_id}` exactly: `patient` carries
# ONE `name` field (not first_name/last_name) and no address/email/phone/DOB
# -- see the PR's HANDOFF re: those last four. `annual_income_cents`, not
# `annual_income` -- FORGE's 2026-08-25 amendment (every money field is cents).
CASE = {
    "patient": {
        "name": "Maria Gonzalez",
        "household_size": 3,
        "annual_income_cents": 2_400_000,
        "insured": False,
        "state": "IL",
    },
    "bill": {
        "hospital_ein": "36-2169147",
        "provider_name": "Advocate Christ Medical Center",
        "amount_cents": 480_000,
        "gfe_amount_cents": 40_000,
        "service_date": dt.date(2026, 5, 1),
        "first_statement_date": dt.date(2026, 5, 20),
        "account_number": "ACCT-DEMO-0042",
        "in_collections": True,
        "collector_name": "Synthetic Recovery Associates",
    },
}
EXTRA = {
    "hospital_name": "Advocate Christ Medical Center",
    "hospital_facility": "Christ Medical Center",
    "patient_email": "maria.gonzalez@example.test",
    "patient_phone": "312-555-0142",
    "patient_date_of_birth": dt.date(1990, 4, 12),
    "patient_address": {
        "street": "742 Evergreen Ter",
        "city": "Chicago",
        "state": "IL",
        "zip": "60601",
    },
    "hospital_address": {
        "street": "4440 W 95th St",
        "city": "Oak Lawn",
        "state": "IL",
        "zip": "60453",
    },
    "filing_date": dt.date(2026, 8, 25),
    "today": dt.date(2026, 8, 25),
    "account_number": "ACCT-DEMO-0042",
}


def _text(pdf_bytes: bytes) -> str:
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    return "\n".join(page.extract_text() or "" for page in reader.pages)


@pytest.mark.parametrize("form_id", sorted(FORM_REGISTRY))
def test_every_registered_form_fills_without_raising(form_id):
    pdf_bytes = fill_form(form_id, CASE, EXTRA)
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 500


def test_cms_ppdr_acroform_fields_are_set():
    pdf_bytes = fill_form("cms_ppdr", CASE, EXTRA)
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    fields = reader.get_fields()
    assert fields["Patient First Name"]["/V"] == "Maria"
    assert fields["Last Name"]["/V"] == "Gonzalez"
    # $480,000 - $40,000 = $440,000 >= the $400 PPDR threshold -> Yes checked.
    assert (
        fields[
            "Select Yes if your bill from your health care provider is at least "
            "$400 more than the good faith estimate"
        ]["/V"]
        == "/Yes"
    )


def test_ppdr_delta_under_400_does_not_check_yes():
    case = {**CASE, "bill": {**CASE["bill"], "amount_cents": 40_300}}  # delta = $300
    pdf_bytes = fill_form("cms_ppdr", case, EXTRA)
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    fields = reader.get_fields()
    key = (
        "Select Yes if your bill from your health care provider is at least "
        "$400 more than the good faith estimate"
    )
    assert fields[key].get("/V") != "/Yes"


def test_ppdr_answers_override_beats_recomputed_fallback():
    """Agreement §2.1: the rules engine's answer, when supplied, wins over
    this module's own convenience recomputation."""
    extra = {**EXTRA, "ppdr_answers": {"delta_at_least_400": False}}
    pdf_bytes = fill_form("cms_ppdr", CASE, extra)  # CASE's own math says True
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    fields = reader.get_fields()
    key = (
        "Select Yes if your bill from your health care provider is at least "
        "$400 more than the good faith estimate"
    )
    assert fields[key].get("/V") != "/Yes"


def test_sutter_acroform_fields_are_set():
    pdf_bytes = fill_form("sutter_fap", CASE, EXTRA)
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    fields = reader.get_fields()
    assert fields["pat_name"]["/V"] == "Maria Gonzalez"
    assert fields["account"]["/V"] == "ACCT-DEMO-0042"
    assert fields["family_size"]["/V"] == "3"


def test_advocate_overlay_places_patient_name_and_account_number():
    pdf_bytes = fill_form("advocate_fap", CASE, EXTRA)
    text = _text(pdf_bytes)
    assert "Gonzalez" in text
    assert "Maria" in text
    assert "ACCT-DEMO-0042" in text


def test_advocate_overlay_has_no_acroform_fields_to_confirm_it_used_overlay():
    reader = pypdf.PdfReader(str(TEMPLATES_DIR / "advocate_fap_application.pdf"))
    assert not reader.get_fields()  # confirms this template genuinely has none


def test_debt_validation_letter_cites_the_statute_and_amount():
    pdf_bytes = fill_form("debt_validation_letter", CASE, EXTRA)
    text = _text(pdf_bytes)
    assert "12 CFR 1006.34(b)" in text
    assert "15 USC 1692g(a)" in text
    assert "$4,800.00" in text
    assert "SYNTHETIC" in text  # watermark convention, agreement item 6


def test_records_request_letter_default_citation():
    pdf_bytes = fill_form("records_request_letter", CASE, EXTRA)
    text = _text(pdf_bytes)
    assert "42 USC 1395b-7(b)" in text
    assert "2560.503-1" not in text  # ERISA cite only added when applicable


def test_records_request_letter_adds_erisa_citation_when_denied_claim():
    pdf_bytes = fill_form("records_request_letter", CASE, {**EXTRA, "denied_insurance_claim": True})
    text = _text(pdf_bytes)
    assert "29 CFR 2560.503-1(h)(2)(iii)" in text


def test_unknown_form_id_raises_clear_error():
    with pytest.raises(ValueError, match="unknown form_id"):
        fill_form("not_a_real_form", CASE, EXTRA)
