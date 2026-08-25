#!/usr/bin/env python3
"""Render fixtures/cases_data.py into the on-disk fixture bundle.

PROOF (persona 7), WO1-2. Run with:

    .venv/bin/python fixtures/generate.py

Regenerates everything under fixtures/generated/ from scratch. Nothing here
is hand-maintained JSON -- the corpus lives in cases_data.py, and this script
is the only thing allowed to write the derived files, so the numbers can
never drift from the source of truth (WO5's "the stats must be EXACTLY
consistent with the case data" is enforced by construction: everything is
summed from the same LineItem objects, once).

Every rendered document carries the "SYNTHETIC — DEMO" watermark on every
page (rule 0.6 / CLAUDE.md). Nonprofit-hospital bills carry the legally
required FAP notice line at the bottom (26 CFR 1.501(r)-4); the for-profit
fixture bill carries the honest converse disclosure instead.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "packages" / "rules"))

from reportlab.lib.pagesizes import LETTER  # noqa: E402
from reportlab.lib.units import inch  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

from fixtures.build import (  # noqa: E402
    DOCUMENT_PATHS,
    _json_default,
    build_case_json,
    hospital_to_contract,
)
from fixtures.cases_data import CASES, HOSPITALS, WATERMARK, CaseFixture, Hospital  # noqa: E402

OUT = REPO_ROOT / "fixtures" / "generated"


# ---------------------------------------------------------------------------
# PDF / image rendering helpers
# ---------------------------------------------------------------------------


def _watermark(c: canvas.Canvas, width: float, height: float) -> None:
    c.saveState()
    c.setFont("Helvetica-Bold", 46)
    c.setFillGray(0.85)
    c.translate(width / 2, height / 2)
    c.rotate(35)
    c.drawCentredString(0, 0, WATERMARK)
    c.restoreState()
    # A second, small, non-rotated instance so text extraction never depends
    # on a rotated-glyph reader picking up the big one.
    c.saveState()
    c.setFont("Helvetica", 8)
    c.setFillGray(0.4)
    c.drawString(0.5 * inch, 0.35 * inch, WATERMARK + " — fictional patient, fictional bill")
    c.restoreState()


def _letterhead(c: canvas.Canvas, width: float, top: float, hospital: Hospital) -> float:
    c.setFont("Helvetica-Bold", 16)
    c.drawString(0.75 * inch, top, hospital.name)
    c.setFont("Helvetica", 9)
    top -= 14
    c.drawString(0.75 * inch, top, f"{hospital.state} — EIN {hospital.ein}")
    top -= 22
    c.setLineWidth(1)
    c.line(0.75 * inch, top, width - 0.75 * inch, top)
    return top - 20


def _fap_notice_line(hospital: Hospital) -> str:
    if hospital.nonprofit:
        where = hospital.fap_url or "the hospital's Financial Assistance Office"
        return (
            f"Financial assistance may be available. Contact {where} to learn "
            "if you qualify. See 26 CFR 1.501(r)-4."
        )
    return (
        "This is a for-profit hospital and is not required to maintain a "
        "Financial Assistance Policy under 26 CFR 1.501(r). Uninsured "
        "patients in this state may still hold a separate state-law "
        "discount right."
    )


def render_bill_pdf(path: Path, case: CaseFixture, hospital: Hospital) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    width, height = LETTER
    _watermark(c, width, height)
    y = _letterhead(c, width, height - 0.75 * inch, hospital)

    c.setFont("Helvetica-Bold", 12)
    c.drawString(0.75 * inch, y, "ITEMIZED STATEMENT")
    y -= 18
    c.setFont("Helvetica", 9)
    c.drawString(0.75 * inch, y, f"Patient: {case.patient['name']} (SYNTHETIC — fictional)")
    y -= 12
    c.drawString(0.75 * inch, y, f"Service date: {case.bill.get('service_date')}")
    y -= 12
    c.drawString(0.75 * inch, y, f"Statement date: {case.bill.get('first_statement_date')}")
    y -= 22

    c.setFont("Helvetica-Bold", 9)
    c.drawString(0.75 * inch, y, "CODE")
    c.drawString(1.4 * inch, y, "DESCRIPTION")
    c.drawString(5.3 * inch, y, "UNITS")
    c.drawString(6.0 * inch, y, "CHARGE")
    y -= 6
    c.line(0.75 * inch, y, width - 0.75 * inch, y)
    y -= 14

    c.setFont("Helvetica", 9)
    total_cents = 0
    for li in case.line_items:
        c.drawString(0.75 * inch, y, li.code)
        c.drawString(1.4 * inch, y, li.description[:52])
        c.drawString(5.3 * inch, y, str(li.units))
        c.drawRightString(7.25 * inch, y, f"${li.total_cents / 100:,.2f}")
        total_cents += li.total_cents
        y -= 14
        if y < 1.6 * inch:
            c.showPage()
            _watermark(c, width, height)
            y = height - 0.75 * inch

    y -= 8
    c.line(0.75 * inch, y, width - 0.75 * inch, y)
    y -= 16
    c.setFont("Helvetica-Bold", 10)
    c.drawRightString(7.25 * inch, y, f"TOTAL: ${total_cents / 100:,.2f}")

    y -= 30
    c.setFont("Helvetica-Oblique", 8)
    for line in _wrap(_fap_notice_line(hospital), 100):
        c.drawString(0.75 * inch, y, line)
        y -= 10

    c.showPage()
    c.save()


def render_corrupted_bill_pdf(path: Path, case: CaseFixture, hospital: Hospital | None) -> None:
    """A file that LOOKS like it could be a PDF and parses as nothing.

    Starts with a real PDF header (so a naive "does it start with %PDF"
    sniff would pass) but has no xref table and is truncated mid-stream --
    pypdf (and any other real parser) must raise on it. That is the whole
    point of case_06: the Reader has to fail closed, not invent data.
    """
    del case, hospital
    header = (
        b"%PDF-1.4\n"
        b"1 0 obj\n<< /Type /Catalog >>\nendobj\n"
        b"%% SYNTHETIC \xe2\x80\x94 DEMO. Deliberately truncated / corrupted "
        b"fixture -- see fixtures/cases_data.py case_06_unparseable_bill.\n"
        b"2 0 obj\n<< /Length 999999 >>\nstream\n"
    )
    garbage = bytes(range(256)) * 4  # no endstream/xref/trailer -- deliberately truncated
    path.write_bytes(header + garbage)


def render_gfe_pdf(path: Path, case: CaseFixture, hospital: Hospital, gfe_delta_cents: int) -> None:
    total_cents = sum(li.total_cents for li in case.line_items)
    gfe_cents = total_cents - gfe_delta_cents
    c = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    width, height = LETTER
    _watermark(c, width, height)
    y = _letterhead(c, width, height - 0.75 * inch, hospital)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(0.75 * inch, y, "GOOD FAITH ESTIMATE")
    y -= 16
    c.setFont("Helvetica", 9)
    c.drawString(
        0.75 * inch, y, "Per 45 CFR 149.610 -- provided to an uninsured / self-pay patient."
    )
    y -= 20
    c.drawString(0.75 * inch, y, f"Patient: {case.patient['name']} (SYNTHETIC — fictional)")
    y -= 14
    c.drawString(0.75 * inch, y, f"Estimated total expected charges: ${gfe_cents / 100:,.2f}")
    y -= 30
    c.setFont("Helvetica-Oblique", 8)
    c.drawString(
        0.75 * inch,
        y,
        "If your final bill is $400 or more above this estimate, you may be able to",
    )
    y -= 10
    c.drawString(
        0.75 * inch, y, "dispute the difference under the No Surprises Act (45 CFR 149.620)."
    )
    c.showPage()
    c.save()


def render_pay_stub_pdf(path: Path, case: CaseFixture) -> None:
    annual_cents = case.patient["annual_income_cents"]
    biweekly_gross = annual_cents / 26
    c = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    width, height = LETTER
    _watermark(c, width, height)
    y = height - 0.9 * inch
    c.setFont("Helvetica-Bold", 13)
    c.drawString(0.75 * inch, y, "EARNINGS STATEMENT (SYNTHETIC — DEMO)")
    y -= 20
    c.setFont("Helvetica", 9)
    c.drawString(0.75 * inch, y, f"Employee: {case.patient['name']} (fictional)")
    y -= 14
    c.drawString(0.75 * inch, y, "Employer: Fictional Employer, Inc. (SYNTHETIC)")
    y -= 14
    c.drawString(0.75 * inch, y, "Pay period: bi-weekly")
    y -= 14
    c.drawString(0.75 * inch, y, f"Gross pay this period: ${biweekly_gross / 100:,.2f}")
    y -= 14
    c.drawString(0.75 * inch, y, f"Year-to-date estimate / annualized: ${annual_cents / 100:,.2f}")
    c.showPage()
    c.save()


def render_denial_letter_pdf(
    path: Path, case: CaseFixture, hospital: Hospital, lawful: bool
) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    width, height = LETTER
    _watermark(c, width, height)
    y = _letterhead(c, width, height - 0.75 * inch, hospital)
    c.setFont("Helvetica-Bold", 12)
    c.drawString(0.75 * inch, y, "FINANCIAL ASSISTANCE APPLICATION — DECISION")
    y -= 18
    c.setFont("Helvetica", 9)
    c.drawString(0.75 * inch, y, f"Patient: {case.patient['name']} (SYNTHETIC — fictional)")
    y -= 20
    c.drawString(
        0.75 * inch, y, "Your application cannot be processed without the following documents:"
    )
    y -= 16
    for doc in case.denial_demanded_docs:
        c.drawString(0.95 * inch, y, f"- {doc.replace('_', ' ')}")
        y -= 13
    y -= 10
    c.setFont("Helvetica-Oblique", 8)
    note = (
        "(This fixture is the LAWFUL contrast case: every item above is on "
        f"{hospital.name}'s own published FAP document list.)"
        if lawful
        else (
            "(This fixture deliberately demands items beyond "
            f"{hospital.name}'s own published FAP document list -- see "
            "26 CFR 1.501(r)-4(b)(3).)"
        )
    )
    for line in _wrap(note, 95):
        c.drawString(0.75 * inch, y, line)
        y -= 10
    c.showPage()
    c.save()


def render_collection_notice_pdf(path: Path, case: CaseFixture) -> None:
    c = canvas.Canvas(str(path), pagesize=LETTER, invariant=1)
    width, height = LETTER
    _watermark(c, width, height)
    y = height - 0.9 * inch
    c.setFont("Helvetica-Bold", 13)
    c.drawString(0.75 * inch, y, case.bill["collector_name"])
    y -= 16
    c.setFont("Helvetica", 9)
    c.drawString(0.75 * inch, y, "DEBT VALIDATION NOTICE (SYNTHETIC — DEMO)")
    y -= 18
    c.drawString(
        0.75 * inch, y, f"Re: account referred by {case.bill['provider_name']} (fictional)"
    )
    y -= 14
    c.drawString(0.75 * inch, y, f"Notice date: {case.bill['validation_notice_date']}")
    y -= 20
    for line in _wrap(
        "You have the right to dispute this debt in writing within 30 days of "
        "receiving this notice. 12 CFR 1006.34; 15 USC 1692g(a).",
        95,
    ):
        c.drawString(0.75 * inch, y, line)
        y -= 12
    c.showPage()
    c.save()


def render_cat_photo_png(path: Path, case: CaseFixture) -> None:
    from PIL import Image, ImageDraw, ImageFont

    del case
    img = Image.new("RGB", (640, 480), "white")
    d = ImageDraw.Draw(img)
    # A deliberately simple, obviously-drawn (not photographic) cat doodle.
    d.ellipse((170, 140, 470, 420), fill=(230, 180, 120), outline="black", width=3)  # head
    d.polygon([(190, 170), (150, 60), (250, 150)], fill=(230, 180, 120), outline="black")  # L ear
    d.polygon([(450, 170), (490, 60), (390, 150)], fill=(230, 180, 120), outline="black")  # R ear
    d.ellipse((240, 240, 290, 290), fill="black")  # L eye
    d.ellipse((350, 240, 400, 290), fill="black")  # R eye
    d.polygon([(305, 300), (335, 300), (320, 325)], fill=(200, 120, 120))  # nose
    for dx in (-1, 1):
        for dy in (-10, 0, 10):
            d.line((320 + dx * 20, 330 + dy, 320 + dx * 120, 320 + dy), fill="black", width=2)
    try:
        font = ImageFont.truetype("Helvetica.ttc", 22)
    except OSError:
        font = ImageFont.load_default()
    d.text((30, 20), "SYNTHETIC — DEMO", fill="red", font=font)
    d.text((30, 440), "NOT AN INCOME DOCUMENT (this is a cat)", fill="red", font=font)
    img.save(path)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    cur = ""
    for w in words:
        candidate = f"{cur} {w}".strip()
        if len(candidate) > width:
            lines.append(cur)
            cur = w
        else:
            cur = candidate
    if cur:
        lines.append(cur)
    return lines


RENDERERS = {
    "bill_pdf": (render_bill_pdf, DOCUMENT_PATHS["bill_pdf"]),
    "corrupted_bill_pdf": (render_corrupted_bill_pdf, DOCUMENT_PATHS["corrupted_bill_pdf"]),
    "gfe_pdf": (render_gfe_pdf, DOCUMENT_PATHS["gfe_pdf"]),
    "pay_stub_pdf": (render_pay_stub_pdf, DOCUMENT_PATHS["pay_stub_pdf"]),
    "denial_letter_pdf": (render_denial_letter_pdf, DOCUMENT_PATHS["denial_letter_pdf"]),
    "collection_notice_pdf": (
        render_collection_notice_pdf,
        DOCUMENT_PATHS["collection_notice_pdf"],
    ),
    "cat_photo_png": (render_cat_photo_png, DOCUMENT_PATHS["cat_photo_png"]),
}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    hospitals_json = {ein: hospital_to_contract(h) for ein, h in HOSPITALS.items()}
    (OUT / "hospitals.json").write_text(
        json.dumps(hospitals_json, indent=2, sort_keys=True, default=_json_default) + "\n"
    )

    stats = {
        "open_cases": 0,
        "hospitals": 0,
        "deadlines_this_week": 0,
        "total_billed_cents": 0,
        "charity_eligible": 0,
        "ppdr_eligible": 0,
        "unlawful_denials_flagged": 0,
        "audit_findings_cents": 0,
        "filings_sent": 0,
        "human_hours": 0,
    }
    seen_hospitals: set[str] = set()

    case_dir = OUT / "cases"
    for case in CASES:
        cdir = case_dir / case.case_id
        (cdir / "documents").mkdir(parents=True)
        cjson = build_case_json(case, HOSPITALS)
        (cdir / "case.json").write_text(
            json.dumps(cjson, indent=2, sort_keys=True, default=_json_default) + "\n"
        )

        hospital = HOSPITALS.get(case.bill.get("hospital_ein") or "")
        for doc in case.documents:
            fn, relpath = RENDERERS[doc.render]
            out_path = cdir / relpath
            out_path.parent.mkdir(parents=True, exist_ok=True)
            kwargs = dict(doc.kwargs)
            if fn is render_bill_pdf or fn is render_corrupted_bill_pdf:
                fn(out_path, case, hospital)
            elif fn is render_gfe_pdf or fn is render_denial_letter_pdf:
                fn(out_path, case, hospital, **kwargs)
            elif fn in (render_collection_notice_pdf, render_pay_stub_pdf, render_cat_photo_png):
                fn(out_path, case)
            else:  # pragma: no cover
                raise AssertionError(f"unhandled renderer {fn!r}")

        # --- stats (WO5: must add up exactly, so derive everything from the
        # same case.json this loop already computed) ---
        stats["open_cases"] += 1
        if hospital is not None:
            seen_hospitals.add(hospital.ein)
        exp = cjson["expected"]
        if cjson["bill"]["amount_cents"]:
            stats["total_billed_cents"] += cjson["bill"]["amount_cents"]
        if exp["eligibility"] and exp["eligibility"]["determination"] in ("free", "discounted"):
            stats["charity_eligible"] += 1
        if any(f["front"] == "ppdr" and f["applicable"] for f in exp["fronts_reference_model"]):
            stats["ppdr_eligible"] += 1
        if exp["denial_check_reference_model"] and exp["denial_check_reference_model"]["violation"]:
            stats["unlawful_denials_flagged"] += 1
        stats["audit_findings_cents"] += exp["audit_findings_cents_total"]
        for d in exp["deadlines"]:
            left = d["days_remaining_as_of_2026_08_25"]
            if left is not None and 0 <= left <= 7:
                stats["deadlines_this_week"] += 1

    stats["hospitals"] = len(seen_hospitals)
    (OUT / "expected_stats.json").write_text(json.dumps(stats, indent=2, sort_keys=True) + "\n")

    print(f"Generated {len(CASES)} cases + {len(HOSPITALS)} hospitals into {OUT}")
    print(json.dumps(stats, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
