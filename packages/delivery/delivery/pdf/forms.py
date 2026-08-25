"""Per-form specs: where each real PDF's fields live, and how to build the
values that go into them from a §3.1 ``cases/{case_id}`` document.

Every coordinate below was measured directly off the real template with
``pdfplumber`` (word bounding boxes + rect dividers), not guessed. See the
PR description for the measurement method. Template PDFs live in
``pdf/templates/`` -- they are the actual government / hospital forms fetched
from the URLs cited in each ``FormSpec.source_url``, which is why the fill
methods differ per form:

  * ``cms_ppdr``   -- real CMS form, HAS AcroForm fields -> pypdf fill.
  * ``sutter_fap`` -- real Sutter Health (CA) charity-care application, HAS
    AcroForm fields (102 of them) -> pypdf fill.
  * ``advocate_fap`` -- real Advocate Health Care (IL) FAP application, a
    FLAT/scanned-style form with NO AcroForm fields -> reportlab overlay
    against the hand-measured coordinate map (`ADVOCATE_OVERLAY`).

This gives the engine a real example of both documented fill strategies
(§4 persona 4 WO2) rather than picking the easy path for every form.

The two letters (`debt_validation_letter`, `records_request_letter`) have no
upstream template -- we own the whole layout -- so they are generated
overlay-style onto a blank canvas; no coordinate map is needed for those,
`engine.py` special-cases ``template=None``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Literal

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"

PAGE_W, PAGE_H = 612.0, 792.0  # US Letter, points -- confirmed on every template


def _get(d: dict, *path: str, default: Any = None) -> Any:
    """Walk a chain of dict keys, returning `default` on any miss.

    Never raises -- a missing field in the case document means the filled
    PDF has a blank box, not a crash. An invented value would be worse than
    an empty box on a legal filing.
    """
    cur: Any = d
    for key in path:
        if not isinstance(cur, dict):
            return default
        cur = cur.get(key)
        if cur is None:
            return default
    return cur if cur is not None else default


def _fmt_date(d: Any) -> str:
    if isinstance(d, date):
        return d.strftime("%m/%d/%Y")
    return "" if d is None else str(d)


def _fmt_money(cents: Any) -> str:
    if cents is None:
        return ""
    try:
        return f"{int(cents) / 100:,.2f}"
    except (TypeError, ValueError):
        return ""


def _fmt_monthly_income(cents: Any) -> str:
    """Annual income cents -> gross MONTHLY dollars, the unit these forms want."""
    if cents is None:
        return ""
    try:
        return f"{int(cents) / 100 / 12:,.2f}"
    except (TypeError, ValueError):
        return ""


def _full_name(patient: dict) -> str:
    """Contract §3.1 `patient` carries one `name` field, not split
    first/last -- read that. `first_name`/`last_name` fall back only for a
    caller that has not adopted the contract shape yet (defensive, never the
    primary path)."""
    name = _get(patient, "name", default="")
    if name:
        return str(name).strip()
    first = _get(patient, "first_name", default="")
    last = _get(patient, "last_name", default="")
    return f"{first} {last}".strip()


def _split_name(patient: dict) -> tuple[str, str]:
    """Some real forms (CMS PPDR, Advocate's FAP) have separate First/Last
    boxes; §3.1 gives us one `name` string. Split on the first space -- "First
    Middle Last" becomes first="First", last="Middle Last", which is the
    patient-safe reading for a legal filing (the surname is what a clerk
    matches against, and losing a middle name is far less costly than
    silently swapping first/last)."""
    name = _full_name(patient)
    if not name:
        return "", ""
    parts = name.split(None, 1)
    return (parts[0], parts[1] if len(parts) > 1 else "")


@dataclass(frozen=True)
class AcroField:
    """One AcroForm field: `pdf_field` <- `value_fn(case, extra)`."""

    pdf_field: str
    value_fn: Any  # Callable[[dict, dict], str | None]


@dataclass(frozen=True)
class OverlayField:
    """One overlay text draw: absolute point coordinates, origin bottom-left.

    `page` is 0-indexed. `y` is already converted to reportlab's bottom-up
    coordinate system at measurement time (see the module docstring's PR
    description for the `page_height - top` conversion used).
    """

    page: int
    x: float
    y: float
    value_fn: Any  # Callable[[dict, dict], str | None]
    font: str = "Helvetica"
    size: float = 9


@dataclass(frozen=True)
class FormSpec:
    form_id: str
    title: str
    citation: str
    source_url: str
    method: Literal["acroform", "overlay", "generated"]
    template: str | None = None  # relative to TEMPLATES_DIR; None for "generated"
    acro_fields: tuple[AcroField, ...] = field(default_factory=tuple)
    overlay_fields: tuple[OverlayField, ...] = field(default_factory=tuple)
    page_count: int = 1  # only used by "generated" forms to size the canvas


# --------------------------------------------------------------------------
# (a) CMS PPDR initiation form -- real PDF, AcroForm fields, pypdf fill.
# https://www.cms.gov/files/document/billing-dispute-initiation-form.pdf
# 45 CFR 149.620 -- Patient-Provider Dispute Resolution, uninsured/self-pay.
# --------------------------------------------------------------------------


def _ppdr_yes_no(extra: dict, case: dict, key: str, *, fallback: Any) -> bool | None:
    """PPDR eligibility answers should come from STATUTE's rules engine.

    `extra["ppdr_answers"][key]` is the authoritative source -- the caller
    (the Filer, per agreement §2.1) is expected to pass through whatever
    `select_fronts`/`compute_deadlines` already decided. `fallback` is only a
    convenience for callers/tests that have not wired that through yet; it
    recomputes the same $400/120-day thresholds STATUTE encodes, so it can
    never produce an answer more generous than the rules engine would.
    """
    answers = _get(extra or {}, "ppdr_answers", default={})
    if key in answers:
        return answers[key]
    return fallback


def _ppdr_context(case: dict, extra: dict) -> dict[str, Any]:
    patient = _get(case, "patient", default={})
    bill = _get(case, "bill", default={})
    extra = extra or {}
    first_name, last_name = _split_name(patient)

    gfe_cents = bill.get("gfe_amount_cents")
    amount_cents = bill.get("amount_cents")
    has_gfe = gfe_cents is not None
    delta_ge_400 = has_gfe and amount_cents is not None and (amount_cents - gfe_cents) >= 40_000
    first_statement = bill.get("first_statement_date")
    within_120 = None
    if isinstance(first_statement, date):
        today = extra.get("today") or date.today()
        within_120 = (today - first_statement).days <= 120

    return {
        "gfe": _ppdr_yes_no(extra, case, "gave_gfe", fallback=has_gfe),
        "delta_400": _ppdr_yes_no(extra, case, "delta_at_least_400", fallback=delta_ge_400),
        "within_120": _ppdr_yes_no(extra, case, "bill_within_120_days", fallback=within_120),
        "first_name": first_name,
        "last_name": last_name,
        # §3.1's `patient` object does not carry a street address, email,
        # phone, or DOB (HANDOFF: see the PR description) -- these real
        # forms need them, so they are sourced from `extra` until the case
        # shape grows them or the Filer sources them from
        # `documents.extracted`. Blank rather than invented when absent.
        "street": _get(extra, "patient_address", "street", default=""),
        "state": _get(patient, "state", default=""),
        "service_date": bill.get("service_date"),
        "description": extra.get(
            "item_description", "Hospital services -- see attached itemized bill"
        ),
        "provider_name": _get(bill, "provider_name", default=""),
        "hospital_name": _get(extra, "hospital_name", default=""),
        "hospital_street": _get(extra, "hospital_address", "street", default=""),
        "hospital_city": _get(extra, "hospital_address", "city", default=""),
        "hospital_state": _get(extra, "hospital_address", "state", default=""),
        "hospital_zip": _get(extra, "hospital_address", "zip", default=""),
        "email": _get(extra, "patient_email", default=""),
        "phone": _get(extra, "patient_phone", default=""),
        "print_name": _full_name(patient),
        "date": _fmt_date(extra.get("filing_date") or date.today()),
    }


def _yn(value: bool | None) -> str | None:
    return "/Yes" if value else None


CMS_PPDR = FormSpec(
    form_id="cms_ppdr",
    title="Patient-Provider Dispute Resolution -- Initiation Form",
    citation="45 CFR 149.620",
    source_url="https://www.cms.gov/files/document/billing-dispute-initiation-form.pdf",
    method="acroform",
    template="cms_ppdr_initiation.pdf",
    # These AcroField names are copied verbatim from the real PDF's field
    # dictionary (see the module docstring's measurement method) -- some
    # exceed the 100-col limit; noqa rather than reflow, since altering
    # whitespace in a dict-key string would silently break the field match.
    acro_fields=(
        AcroField(
            "Select Yes if your health care provider gave you a good faith estimate for the item or service",  # noqa: E501
            lambda c, x: _yn(_ppdr_context(c, x)["gfe"]),
        ),
        AcroField(
            "Select No if your health care provider gave you a good faith estimate for the item or service",  # noqa: E501
            lambda c, x: _yn(_ppdr_context(c, x)["gfe"] is False),
        ),
        AcroField(
            "Select Yes if your bill from your health care provider is at least $400 more than the good faith estimate",  # noqa: E501
            lambda c, x: _yn(_ppdr_context(c, x)["delta_400"]),
        ),
        AcroField(
            "Select No if your bill from your health care provider is at least $400 more than the good faith estimate",  # noqa: E501
            lambda c, x: _yn(_ppdr_context(c, x)["delta_400"] is False),
        ),
        AcroField(
            "Select yes if the date on the top of the bill is within the last 120 calendar days",
            lambda c, x: _yn(_ppdr_context(c, x)["within_120"]),
        ),
        AcroField(
            "Select No if the date on the top of the bill is within 120 calendar days",
            lambda c, x: _yn(_ppdr_context(c, x)["within_120"] is False),
        ),
        AcroField("Patient First Name", lambda c, x: _ppdr_context(c, x)["first_name"]),
        AcroField("Last Name", lambda c, x: _ppdr_context(c, x)["last_name"]),
        AcroField("Street or PO Box", lambda c, x: _ppdr_context(c, x)["street"]),
        AcroField(
            "State where the patient received the item or service",
            lambda c, x: _ppdr_context(c, x)["state"],
        ),
        AcroField(
            "Short description of item or service disputed",
            lambda c, x: _ppdr_context(c, x)["description"],
        ),
        AcroField("Health Care Provider Name", lambda c, x: _ppdr_context(c, x)["provider_name"]),
        AcroField(
            "Hospital, Facility, or Group Name", lambda c, x: _ppdr_context(c, x)["hospital_name"]
        ),
        AcroField("Street", lambda c, x: _ppdr_context(c, x)["hospital_street"]),
        AcroField("City", lambda c, x: _ppdr_context(c, x)["hospital_city"]),
        AcroField("State", lambda c, x: _ppdr_context(c, x)["hospital_state"]),
        AcroField("ZIP", lambda c, x: _ppdr_context(c, x)["hospital_zip"]),
        AcroField("Email", lambda c, x: _ppdr_context(c, x)["email"]),
        AcroField("Phone", lambda c, x: _ppdr_context(c, x)["phone"]),
        AcroField("Print Name", lambda c, x: _ppdr_context(c, x)["print_name"]),
        AcroField("Date", lambda c, x: _ppdr_context(c, x)["date"]),
        AcroField(
            "Select if including a copy of the bill from your health care provider that you want to dispute",  # noqa: E501
            lambda c, x: "/Yes",
        ),
        AcroField(
            "Select if including a copy of the Good Faith Estimate for the item or service you want to dispute",  # noqa: E501
            lambda c, x: _yn(_ppdr_context(c, x)["gfe"]),
        ),
    ),
)


# --------------------------------------------------------------------------
# (b1) Sutter Health (CA) charity-care application -- real PDF, HAS AcroForm
# fields -> pypdf fill. Source: California OSHPD/HCAI hospital fair-pricing
# attachment (Schedule H 16b-equivalent posting for Sutter Bay Hospitals).
# --------------------------------------------------------------------------


def _sutter_context(case: dict, extra: dict) -> dict[str, Any]:
    patient = _get(case, "patient", default={})
    bill = _get(case, "bill", default={})
    extra = extra or {}
    # §3.1's `patient` carries no address/phone (HANDOFF, PR description) --
    # sourced from `extra` until the case shape grows them.
    address = _get(extra, "patient_address", default={})
    return {
        "pat_name": _full_name(patient),
        "address": ", ".join(
            p
            for p in (
                address.get("street"),
                address.get("city"),
                address.get("state"),
                address.get("zip"),
            )
            if p
        ),
        "phone": _get(extra, "patient_phone", default=""),
        "account": _get(bill, "account_number", default=extra.get("account_number", "")),
        "family_size": str(_get(patient, "household_size", default="") or ""),
        "income_gross_pat": _fmt_monthly_income(_get(patient, "annual_income_cents")),
        "income_total_pat": _fmt_monthly_income(_get(patient, "annual_income_cents")),
        "sig_date_1": _fmt_date(extra.get("filing_date") or date.today()),
    }


SUTTER_FAP = FormSpec(
    form_id="sutter_fap",
    title="Sutter Health Charity Care and Discount Payment Application",
    citation="26 CFR 1.501(r)-4; Cal. Health & Safety Code §127405",
    source_url="https://api.hdc.hcai.ca.gov/Public/Extract/Attachment?id=3b468d26-47d3-48b6-a908-92f4a3764128",
    method="acroform",
    template="sutter_fap_application.pdf",
    acro_fields=(
        AcroField("pat_name", lambda c, x: _sutter_context(c, x)["pat_name"]),
        AcroField("address", lambda c, x: _sutter_context(c, x)["address"]),
        AcroField("phone", lambda c, x: _sutter_context(c, x)["phone"]),
        AcroField("account", lambda c, x: _sutter_context(c, x)["account"]),
        AcroField("family_size", lambda c, x: _sutter_context(c, x)["family_size"]),
        AcroField("income_gross_pat", lambda c, x: _sutter_context(c, x)["income_gross_pat"]),
        AcroField("income_total_pat", lambda c, x: _sutter_context(c, x)["income_total_pat"]),
        AcroField("sig_date_1", lambda c, x: _sutter_context(c, x)["sig_date_1"]),
    ),
)


# --------------------------------------------------------------------------
# (b2) Advocate Health Care (IL) FAP application -- real PDF, NO AcroForm
# fields (flat layout) -> reportlab overlay against a hand-measured map.
# Source: 16b application URL, Schedule H Part V Sec B (see docs/SPIKE.md).
#
# Coordinate method: pdfplumber word boxes gave the exact (x0, top) of every
# printed field caption. Every caption in the PATIENT INFORMATION table sits
# at the BOTTOM of its cell -- the write-in space is the blank line directly
# above it -- so each value is placed at (caption.x0, page_height -
# (caption.top - 3)), a few points above the caption's own top edge. The
# account-number line is the one inline exception (label then a blank
# underscore run on the SAME baseline), handled with its own explicit y.
# --------------------------------------------------------------------------

ADVOCATE_FAP = FormSpec(
    form_id="advocate_fap",
    title="Advocate Health Care Financial Assistance Application",
    citation="26 CFR 1.501(r)-4; 210 ILCS 89/10",
    source_url=(
        "https://www.advocatehealth.com/-/media/Project/Health-System-Enterprise/"
        "AdvocateHealthCom/advocatehealth/documents/about-us/financial-assistance-for-patients/"
        "fa-application-english.pdf"
    ),
    method="overlay",
    template="advocate_fap_application.pdf",
    # §3.1's `patient` object has no email/address/phone/DOB (HANDOFF, PR
    # description) -- those overlay fields read `extra["patient_*"]` and
    # render blank rather than invented when the caller doesn't supply them.
    overlay_fields=(
        # Header line: "Patient account number: ____" -- inline, same baseline.
        OverlayField(
            0, 418, PAGE_H - 60.8 + 2, lambda c, x: _get(x or {}, "account_number", default="")
        ),
        # PATIENT INFORMATION table, page 1 (all "blank line above caption").
        OverlayField(
            0, 42.4, PAGE_H - (222.5 - 3), lambda c, x: _get(x, "patient_email", default="")
        ),
        OverlayField(
            0,
            452.4,
            PAGE_H - (222.5 - 3),
            lambda c, x: str(_get(c, "patient", "household_size", default="") or ""),
        ),
        OverlayField(
            0,
            42.4,
            PAGE_H - (246.4 - 3),
            lambda c, x: _split_name(_get(c, "patient", default={}))[1],
        ),
        OverlayField(
            0,
            206.2,
            PAGE_H - (246.4 - 3),
            lambda c, x: _split_name(_get(c, "patient", default={}))[0],
        ),
        OverlayField(
            0,
            364.4,
            PAGE_H - (246.4 - 3),
            lambda c, x: _fmt_date(_get(x, "patient_date_of_birth")),
        ),
        OverlayField(
            0,
            42.4,
            PAGE_H - (273.2 - 3),
            lambda c, x: _get(x, "patient_address", "street", default=""),
        ),
        OverlayField(
            0,
            219.3,
            PAGE_H - (273.2 - 3),
            lambda c, x: _get(x, "patient_address", "city", default=""),
        ),
        OverlayField(
            0,
            300.2,
            PAGE_H - (273.2 - 3),
            lambda c, x: _get(x, "patient_address", "state", default=""),
        ),
        OverlayField(
            0,
            363.7,
            PAGE_H - (273.2 - 3),
            lambda c, x: _get(x, "patient_address", "zip", default=""),
        ),
        OverlayField(
            0, 452.4, PAGE_H - (273.2 - 3), lambda c, x: _get(x, "patient_phone", default="")
        ),
        OverlayField(
            0,
            360.2,
            PAGE_H - (322.8 - 3),
            lambda c, x: _fmt_monthly_income(_get(c, "patient", "annual_income_cents")),
        ),
        # Certification, page 3 ("Applicant Signature: ___ Date: ___").
        OverlayField(
            2, 126, PAGE_H - 615.4 + 2, lambda c, x: _full_name(_get(c, "patient", default={}))
        ),
        OverlayField(
            2,
            460,
            PAGE_H - 615.4 + 2,
            lambda c, x: _fmt_date((x or {}).get("filing_date") or date.today()),
        ),
    ),
)

# Page-2 hospital-preference checkbox glyph positions (x, top) measured off
# the template; a hospital name absent from this map is simply left unchecked
# rather than guessed at.
_ADVOCATE_HOSPITAL_CHECKBOXES: dict[str, tuple[float, float]] = {
    "Christ Medical Center": (44.5, 107.9),
    "Lutheran General Hospital": (314.5, 108.3),
    "Condell Medical Center": (44.5, 122.2),
    "Sherman Hospital": (314.5, 122.2),
    "Good Samaritan Hospital": (44.5, 136.2),
    "South Suburban Hospital": (314.5, 136.2),
}


def advocate_hospital_checkbox_field(hospital_name: str) -> OverlayField | None:
    """Optional extra overlay field: tick the matching hospital-preference box.

    Not baked into ADVOCATE_FAP.overlay_fields because it depends on which of
    Advocate's facilities the case names -- the engine adds it dynamically
    when `extra["hospital_facility"]` matches a known checkbox.
    """
    pos = _ADVOCATE_HOSPITAL_CHECKBOXES.get(hospital_name)
    if pos is None:
        return None
    x, bottom = pos
    # Checkbox glyphs are ~7.8pt tall; an 8pt X sized to match sits inside the
    # box instead of overlapping the row above it.
    return OverlayField(1, x, PAGE_H - bottom + 1, lambda c, x_: "X", font="Helvetica-Bold", size=8)


# --------------------------------------------------------------------------
# (c) Debt-validation letter -- generated from scratch (no upstream template).
# 12 CFR 1006.34(b); 15 USC 1692g(a) -- must match rules/deadlines.py exactly.
# --------------------------------------------------------------------------

DEBT_VALIDATION_LETTER = FormSpec(
    form_id="debt_validation_letter",
    title="Debt Validation Dispute Letter",
    citation="12 CFR 1006.34(b); 15 USC 1692g(a)",
    source_url="",  # generated document, no upstream template
    method="generated",
    page_count=1,
)

# --------------------------------------------------------------------------
# (d) Records-request letter -- generated from scratch.
# 42 USC 1395b-7(b) (itemized bill, 30 days) is the primary basis and matches
# rules/deadlines.py's ITEMIZED_BILL_DAYS citation exactly; 29 CFR
# 2560.503-1(h)(2)(iii) (ERISA claim-file access) is offered as the alternate
# citation when the case involves a denied insurance claim, per §4 persona 4
# WO2(d).
# --------------------------------------------------------------------------

RECORDS_REQUEST_LETTER = FormSpec(
    form_id="records_request_letter",
    title="Itemized Bill / Claim File Records Request",
    citation=(
        "42 USC 1395b-7(b); 45 CFR Part 180; "
        "29 CFR 2560.503-1(h)(2)(iii) (ERISA claim file, when applicable)"
    ),
    source_url="",
    method="generated",
    page_count=1,
)


FORM_REGISTRY: dict[str, FormSpec] = {
    spec.form_id: spec
    for spec in (CMS_PPDR, SUTTER_FAP, ADVOCATE_FAP, DEBT_VALIDATION_LETTER, RECORDS_REQUEST_LETTER)
}
