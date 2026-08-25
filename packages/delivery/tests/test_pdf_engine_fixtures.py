"""PDF engine against PROOF's real synthetic fixture corpus
(`fixtures/generated/cases/`), per FORGE's direction to use those as the
PDF-filling test inputs rather than inventing new ones.

`fixtures/generated/cases/*/case.json` predates FORGE's 2026-08-25
`annual_income` -> `annual_income_cents` amendment (see BUILD_PLAYBOOK.md
§3.1) -- PROOF's fixture generator has not been updated yet. `_adapt_case`
below normalizes for that one known drift (HANDOFF: PROOF should regenerate
once the fixture pipeline catches up) rather than silently teaching
`packages/delivery` to accept the old field name -- production code stays
strict on the current contract; only this test's fixture loader compensates.
"""

from __future__ import annotations

import datetime as dt
import json
from pathlib import Path

import pytest

pytest.importorskip("pypdf")
pytest.importorskip("reportlab")

from delivery.pdf import fill_form  # noqa: E402

FIXTURES_DIR = Path(__file__).resolve().parents[3] / "fixtures" / "generated" / "cases"


def _adapt_case(raw: dict) -> dict:
    """Real fixture JSON -> the §3.1 shape `packages/delivery` expects:
    ISO date strings become `date` objects, and the pre-amendment
    `annual_income` key is read as a fallback for `annual_income_cents`."""
    patient = dict(raw["patient"])
    if "annual_income_cents" not in patient and "annual_income" in patient:
        patient["annual_income_cents"] = patient.pop("annual_income")

    bill = dict(raw["bill"])
    for date_field in ("service_date", "first_statement_date", "validation_notice_date"):
        value = bill.get(date_field)
        if isinstance(value, str):
            bill[date_field] = dt.date.fromisoformat(value)

    return {"patient": patient, "bill": bill}


def _fixture_case_dirs() -> list[Path]:
    if not FIXTURES_DIR.is_dir():
        return []
    return sorted(p for p in FIXTURES_DIR.iterdir() if (p / "case.json").exists())


CASE_DIRS = _fixture_case_dirs()


@pytest.mark.skipif(not CASE_DIRS, reason="fixtures/generated/cases not present in this checkout")
@pytest.mark.parametrize("case_dir", CASE_DIRS, ids=lambda p: p.name)
def test_advocate_fap_fills_for_every_fixture_case(case_dir):
    """Every PROOF fixture case renders a non-empty, well-formed PDF -- the
    overlay path degrades to blank boxes rather than raising when a fixture
    lacks a field (e.g. every fixture's income screens blank until PROOF
    regenerates with `annual_income_cents`, per this module's docstring)."""
    raw = json.loads((case_dir / "case.json").read_text())
    case = _adapt_case(raw)
    pdf_bytes = fill_form("advocate_fap", case, {"filing_date": dt.date(2026, 8, 25)})
    assert pdf_bytes[:4] == b"%PDF"
    assert len(pdf_bytes) > 500


def test_ppdr_form_reflects_case_01s_real_gfe_delta():
    """case_01_uninsured_gfe_ca: bill $2,625.00 vs GFE $1,925.00, a $700
    delta -- comfortably over the $400 floor, so PPDR's "Yes" box should be
    checked using the SAME arithmetic `rules.deadlines`/`rules.fronts`
    already verified for this fixture (see its `expected.fronts_reference_model`).
    """
    import io

    import pypdf

    case_dir = FIXTURES_DIR / "case_01_uninsured_gfe_ca"
    if not case_dir.exists():
        pytest.skip("fixtures/generated/cases/case_01_uninsured_gfe_ca not present")
    raw = json.loads((case_dir / "case.json").read_text())
    case = _adapt_case(raw)

    pdf_bytes = fill_form("cms_ppdr", case, {"filing_date": dt.date(2026, 8, 25)})
    reader = pypdf.PdfReader(io.BytesIO(pdf_bytes))
    fields = reader.get_fields()
    assert (
        fields[
            "Select Yes if your bill from your health care provider is at least "
            "$400 more than the good faith estimate"
        ]["/V"]
        == "/Yes"
    )
