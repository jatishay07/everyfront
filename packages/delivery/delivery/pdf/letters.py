"""Generated letters -- (c) debt validation, (d) records request.

Unlike the three forms in `forms.py`, these have no upstream template: we own
the whole layout, so they're composed with reportlab's `platypus` (paragraph
flow) rather than absolute-coordinate overlay. Every legal citation here must
match its `packages/rules` counterpart exactly (agreement §2.2) -- this
module is prose around the rules engine's output, never a second source of
truth for what the law says.

`reportlab` is imported lazily inside `_styles()`/`_doc()` rather than at
module scope -- see `engine.py`'s docstring for why a hard top-level import
here would break test collection for personas who never touch this package.
"""

from __future__ import annotations

import io
from datetime import date
from typing import Any


def _styles():
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet

    sheet = getSampleStyleSheet()
    body = ParagraphStyle(
        "EFBody", parent=sheet["Normal"], fontName="Helvetica", fontSize=10.5, leading=14.5
    )
    head = ParagraphStyle(
        "EFHead", parent=sheet["Normal"], fontName="Helvetica-Bold", fontSize=11, leading=15
    )
    small = ParagraphStyle(
        "EFSmall", parent=sheet["Normal"], fontName="Helvetica-Oblique", fontSize=8.5, leading=11
    )
    return body, head, small


def _get(d: dict, *path: str, default: Any = None) -> Any:
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur if cur is not None else default


def _doc():
    from reportlab.lib.pagesizes import LETTER
    from reportlab.lib.units import inch
    from reportlab.platypus import SimpleDocTemplate

    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=LETTER,
        topMargin=0.9 * inch,
        bottomMargin=0.9 * inch,
        leftMargin=1 * inch,
        rightMargin=1 * inch,
    )
    return buf, doc


def _render(doc, buf: io.BytesIO, flowables: list) -> bytes:
    doc.build(flowables)
    return buf.getvalue()


def render_debt_validation_letter(case: dict, extra: dict | None = None) -> bytes:
    """12 CFR 1006.34(b); 15 USC 1692g(a) -- written dispute of a collections debt.

    Citation must match `rules.deadlines.compute_deadlines`'s "debt_validation"
    entry verbatim: "12 CFR 1006.34(b); 15 USC 1692g(a)".
    """
    from reportlab.platypus import Paragraph, Spacer

    body, head, small = _styles()
    extra = extra or {}
    patient = _get(case, "patient", default={})
    bill = _get(case, "bill", default={})
    filing_date = extra.get("filing_date") or date.today()
    collector = _get(bill, "collector_name", default="[collector name not on file]")
    account = extra.get("account_number", "") or _get(bill, "account_number", default="")
    amount = _get(bill, "amount_cents")
    amount_str = (
        f"${amount / 100:,.2f}" if isinstance(amount, int | float) else "[amount not on file]"
    )

    flow = [
        Paragraph(filing_date.strftime("%B %d, %Y"), body),
        Spacer(1, 12),
        Paragraph(
            f"{_get(patient, 'first_name', default='')} {_get(patient, 'last_name', default='')}",
            body,
        ),
        Paragraph(_get(patient, "address", "street", default=""), body),
        Paragraph(
            f"{_get(patient, 'address', 'city', default='')}, "
            f"{_get(patient, 'address', 'state', default='')} "
            f"{_get(patient, 'address', 'zip', default='')}",
            body,
        ),
        Spacer(1, 12),
        Paragraph(f"{collector}", body),
        Spacer(1, 12),
        Paragraph("Re: Written Dispute of Debt -- Request for Validation", head),
        Paragraph(f"Account Number: {account}" if account else "Account Number: [on file]", body),
        Spacer(1, 12),
        Paragraph(
            "This letter is a formal dispute of the above-referenced debt and a request for "
            "validation under 12 CFR 1006.34(b) and 15 USC 1692g(a). I dispute this debt and "
            "request that you provide verification of the debt, including the name and address "
            f"of the original creditor, the amount claimed ({amount_str}), and an itemized "
            "accounting of all charges.",
            body,
        ),
        Spacer(1, 8),
        Paragraph(
            "Under 15 USC 1692g(a), you must cease collection of this debt until you have "
            "mailed the requested verification to me. This includes reporting the debt to any "
            "consumer reporting agency as disputed, and refraining from any further collection "
            "communications that do not include the validation information required by law.",
            body,
        ),
        Spacer(1, 8),
        Paragraph(
            "Please direct all further communication regarding this account to the address "
            "above, in writing.",
            body,
        ),
        Spacer(1, 20),
        Paragraph("Sincerely,", body),
        Spacer(1, 28),
        Paragraph(
            f"{_get(patient, 'first_name', default='')} {_get(patient, 'last_name', default='')}",
            body,
        ),
        Spacer(1, 24),
        Paragraph(
            "SYNTHETIC -- DEMO. Generated by Every Front. "
            "Citation: 12 CFR 1006.34(b); 15 USC 1692g(a).",
            small,
        ),
    ]
    buf, doc = _doc()
    return _render(doc, buf, flow)


def render_records_request_letter(case: dict, extra: dict | None = None) -> bytes:
    """Itemized-bill / claim-file records request.

    Primary citation matches `rules.deadlines.ITEMIZED_BILL_DAYS`'s
    "42 USC 1395b-7(b)" exactly. When `extra["denied_insurance_claim"]` is
    true, the ERISA claim-file basis (29 CFR 2560.503-1(h)(2)(iii)) is added
    as an alternate ground, per §4 persona 4 WO2(d).
    """
    from reportlab.platypus import Paragraph, Spacer

    body, head, small = _styles()
    extra = extra or {}
    patient = _get(case, "patient", default={})
    bill = _get(case, "bill", default={})
    filing_date = extra.get("filing_date") or date.today()
    provider = _get(bill, "provider_name", default="[provider name not on file]")
    account = extra.get("account_number", "") or _get(bill, "account_number", default="")

    citation_line = "42 USC 1395b-7(b); 45 CFR Part 180"
    erisa_para = None
    if extra.get("denied_insurance_claim"):
        citation_line += "; 29 CFR 2560.503-1(h)(2)(iii)"
        erisa_para = Paragraph(
            "Because this claim was denied by my health plan, I additionally request, under "
            "29 CFR 2560.503-1(h)(2)(iii), a copy of the complete claim file, including all "
            "documents, records, and other information relevant to the denial of my claim.",
            body,
        )

    flow = [
        Paragraph(filing_date.strftime("%B %d, %Y"), body),
        Spacer(1, 12),
        Paragraph(
            f"{_get(patient, 'first_name', default='')} {_get(patient, 'last_name', default='')}",
            body,
        ),
        Paragraph(_get(patient, "address", "street", default=""), body),
        Spacer(1, 12),
        Paragraph(f"{provider} -- Billing Department", body),
        Spacer(1, 12),
        Paragraph("Re: Request for Itemized Bill and Records", head),
        Paragraph(f"Account Number: {account}" if account else "Account Number: [on file]", body),
        Spacer(1, 12),
        Paragraph(
            f"Under {citation_line}, I request an itemized statement of all charges billed to "
            "this account, including CPT/HCPCS codes, units billed, and per-line charges, "
            "within 30 days of this request.",
            body,
        ),
    ]
    if erisa_para is not None:
        flow += [Spacer(1, 8), erisa_para]
    flow += [
        Spacer(1, 20),
        Paragraph("Sincerely,", body),
        Spacer(1, 28),
        Paragraph(
            f"{_get(patient, 'first_name', default='')} {_get(patient, 'last_name', default='')}",
            body,
        ),
        Spacer(1, 24),
        Paragraph(
            f"SYNTHETIC -- DEMO. Generated by Every Front. Citation: {citation_line}.", small
        ),
    ]
    buf, doc = _doc()
    return _render(doc, buf, flow)
