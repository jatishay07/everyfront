"""Bridge from the Filer agent to RELAY's real delivery package.

WHY THIS FILE WAS REWRITTEN (FORGE, integration pass 2026-08-25)
---------------------------------------------------------------
SWARM wrote this bridge while `packages/delivery` was still an empty stub, so
it guessed the module layout: `delivery.fax`, `delivery.mail`,
`delivery.calendar`. RELAY then shipped `delivery.vendors.fax`,
`delivery.vendors.mail` and `delivery.calendar_sync`.

Neither guessed wrong on purpose and neither was at fault. But the probe was a
try/except ImportError, so the mismatch failed SILENTLY: every filing fell
through to a simulated vendor ID and the case looked fine. RELAY's entire
package -- five real filled forms including the actual CMS PPDR form and two
hospitals' own FAP applications, plus the vendor allowlist -- sat unused while
the product reported "sent".

That is the parallel-agent failure mode in one file: both halves correct, the
seam between them never joined, and a fallback that hid it. The product's whole
claim is that it FILES THE REAL FORMS; without this wiring it only advises.

Import paths are now direct and NOT wrapped in try/except -- if RELAY's package
moves, this must fail loudly at deploy rather than quietly degrade to
simulation. `infra/deploy.sh` stages packages/delivery into agent-core's image.
"""

from __future__ import annotations

from typing import Any

from delivery.calendar_sync import CALENDAR_SCOPES, sync_deadlines
from delivery.drive_sync import mirror_case_filings
from delivery.google_auth import MissingCredentialsError, load_user_credentials
from delivery.pdf.engine import fill_form
from delivery.vendors.filing import send_filing

__all__ = [
    "form_for_front",
    "channel_for_front",
    "render_filing_pdf",
    "deliver",
    "simulated_flag",
    "google_sync_configured",
    "sync_deadlines",
    "mirror_case_filings",
    "drive_filename",
]

# front -> the form RELAY renders for it (delivery/pdf/forms.py FORM_REGISTRY).
# charity_care resolves per hospital: a hospital's own FAP application is the
# form it actually accepts, and 26 CFR 1.501(r)-4(b)(1)(iv) requires the FAP to
# say which that is.
_FRONT_FORMS: dict[str, str] = {
    "ppdr": "cms_ppdr",
    "debt_validation": "debt_validation_letter",
    "audit": "records_request_letter",
}
_CHARITY_FORMS_BY_EIN: dict[str, str] = {
    "94-0562680": "sutter_fap",  # Sutter Bay Hospitals
    "940562680": "sutter_fap",
    "36-2169147": "advocate_fap",  # Advocate Health and Hospitals
    "362169147": "advocate_fap",
}
_CHARITY_DEFAULT_FORM = "sutter_fap"

# A front is filed on the channel its authority actually contemplates.
# Debt validation must be provable delivery -- 15 USC 1692g gives the consumer
# 30 days and the burden of showing the dispute was sent is theirs.
_FRONT_CHANNELS: dict[str, str] = {
    "ppdr": "fax",  # CMS routes PPDR to C2C by fax
    "charity_care": "mail",
    "debt_validation": "mail",  # certified, with tracking
    "audit": "mail",
}


def form_for_front(front: str, case: dict) -> str:
    """Which of RELAY's forms this front is filed on."""
    if front != "charity_care":
        return _FRONT_FORMS.get(front, "records_request_letter")
    ein = str((case.get("bill") or {}).get("hospital_ein") or "").strip()
    return _CHARITY_FORMS_BY_EIN.get(ein, _CHARITY_DEFAULT_FORM)


def channel_for_front(front: str) -> str:
    return _FRONT_CHANNELS.get(front, "mail")


def render_filing_pdf(front: str, case: dict, extra: dict | None = None) -> tuple[bytes, str]:
    """Render the real filled PDF for `front`. Returns (pdf_bytes, form_id)."""
    form_id = form_for_front(front, case)
    return fill_form(form_id, case, extra or {}), form_id


def deliver(
    *,
    filing_id: str,
    case_id: str,
    front: str,
    pdf: bytes,
    destination: Any,
    channel: str | None = None,
) -> dict[str, Any]:
    """Send a rendered filing through RELAY's vendor interface.

    RELAY enforces a destination allowlist IN CODE (delivery/vendors/allowlist.py),
    so this cannot reach a real hospital fax line or address even by mistake --
    the §4 persona 4 guardrail. Without live vendor credentials RELAY's own
    FakeFaxVendor/FakeMailVendor record the send and return a vendor id; that
    fallback is RELAY's, is labelled as such, and still exercises the real
    rendering and allowlist path rather than skipping them.
    """
    return send_filing(
        filing_id=filing_id,
        case_id=case_id,
        front=front,
        channel=channel or channel_for_front(front),
        pdf=pdf,
        destination=destination,
    )


def simulated_flag(vendor_result: dict[str, Any]) -> bool:
    """Was this send a simulation? Read from what RELAY actually returned.

    THE CONTRACT THIS DEPENDS ON. `delivery.vendors.filing.send_filing()`
    returns `"simulated": bool` on every result -- `True` when the send fell
    back to `FakeFaxVendor`/`FakeMailVendor` (no Phaxio/Lob credentials
    configured, which is every send today), `False` when a real vendor
    actually transmitted. It is derived there from `VendorResult.vendor`
    ("fake" | "phaxio" | "lob"), the one fact that function computes
    first-hand. This bridge does NOT re-derive it, and specifically does not
    sniff the `fake-` prefix on `vendor_id`: a vendor-id format is a string
    RELAY is free to change, not a statement about whether anything left the
    building.

    WHY AN ABSENT KEY IS NOT `False`. HANDOFF.md defect #6 in full: the Filer
    read `vendor_result.get("simulated")` for weeks while `send_filing` never
    set the key, so `bool(None)` made every simulated filing report itself as
    live. `.get()` with a falsy default is what turned a missing fact into a
    confident false claim. Absence now means "the delivery layer did not tell
    us", and an untold fact is not evidence of a real send -- so it reads as
    simulated, the direction that can only ever understate what this system
    did. A genuine live send says so explicitly (`False`) and is reported as
    live, which is the other half of the honesty requirement.
    """
    value = vendor_result.get("simulated")
    return value if isinstance(value, bool) else True


def google_sync_configured() -> bool:
    """True if the demo account's OAuth refresh token is configured.

    `sync_deadlines` and `mirror_case_filings` both swallow
    `MissingCredentialsError` and return `[]` / `None` -- deliberately, so a
    missing Google integration can never fail a filing. That makes their
    empty result ambiguous to a CALLER, though: `[]` is also what a case with
    no dated deadline returns (California charity care has no deadline at
    all -- HSC §127405(e)(3)). The audit trail has to be able to say which
    happened, because "nothing to put on a calendar" and "there is no
    calendar" are different facts about this system and only one of them is a
    missing integration.
    """
    try:
        load_user_credentials(CALENDAR_SCOPES)
    except MissingCredentialsError:
        return False
    return True


def drive_filename(front: str, form_id: str) -> str:
    """The per-case Drive filename for one front's filing.

    Deliberately carries NO filing id or timestamp. `mirror_case_filings`
    updates an existing file of the same name inside the case folder rather
    than creating a second one, so a redelivered `filing.requested`, or a
    front re-filed after a correction, leaves the advocate's folder holding
    ONE current document per front instead of four copies of the same letter.
    Same reasoning as `calendar_sync._stable_event_id`, and the same defect
    it avoids -- §2.3's idempotency requirement applied to an artifact store.
    """
    return f"{front}_{form_id}.pdf"
