"""Filer: renders and sends one front's filing, records vendor proof.

Playbook §4 persona 5, WO1: "renders via RELAY, sends, records proof, appends
events."

REWIRED 2026-08-25 (FORGE, integration): this used to render `_render_stub_pdf`
-- five lines of plain text labelled as a placeholder -- because RELAY's
packages/delivery was an empty stub when SWARM wrote it. RELAY has since shipped
the real thing: five filled forms including the ACTUAL CMS PPDR initiation form
and two hospitals' own FAP applications. It now renders those.

That distinction is the product. "We computed that you qualify" is advice;
"here is your hospital's own application, filled in, sent" is the Taskmaster
claim in §1.1. The placeholder made the demo path complete while looking
complete -- which is exactly why it needed finding rather than trusting.

Filer only ever runs from `pipeline.approve_and_request_filing`, which will
not call it unless (a) `POST /cases/{id}/approve_filing` has been called and
(b) Verifier passed. The human-in-the-loop gate is enforced by the caller, not
by this module trusting its own judgment.
"""

from __future__ import annotations

import uuid

from .. import config, delivery_bridge, document_storage
from ..store import store
from . import common

NAME = "filer"

INSTRUCTION = (
    "You are Filer. Call get_filer_result exactly once and report the filing "
    "channel, vendor id, and status it returns in one sentence."
)

# Which channel each front normally uses. PPDR is a CMS form faxed to C2C;
# everything else here goes out as certified mail. (See BUILD_PLAYBOOK.md §1.2.)
CHANNEL_BY_FRONT = {
    "ppdr": "fax",
    "charity_care": "mail",
    "debt_validation": "mail",
    "audit": "mail",
}

# Real-world destination would be the C2C fax line for PPDR (888-610-4092,
# §1.2) or the hospital's/collector's mailing address for the rest -- recorded
# here, and in every filing's audit trail, purely for the record.
PPDR_FAX_NUMBER = "888-610-4092"

# BUG (verified live, persona 5 WO6 task 1): `packages/delivery/vendors/
# allowlist.py` enforces RELAY's own guardrail ("never send to a real
# hospital fax number or address") IN CODE, unconditionally -- even
# FakeFaxVendor/FakeMailVendor call `assert_*_destination_allowed` before
# recording a send. It was never going to accept the REAL C2C fax number
# above, or a destination dict shaped `{"name", "test_mode"}` with no
# `line1`/`city`/`state`/`zip` at all (what this module used to build for
# every mail-channel front) -- every `approve_filing` call for every front
# raised `UnsafeDestinationError` before a filing could ever be recorded.
# The fix is to send test-mode to RELAY's own documented allowlisted
# destinations (NANP 555-01XX for fax, ZIP 000XX for mail) and log the real
# destination in the filing/event record instead of trying to reach it.
_TEST_FAX_DESTINATION = "+18005550142"  # NANP 555-0142: reserved-fictional, RELAY-allowlisted
_TEST_MAIL_ADDRESS = {"line1": "1 Demo Plaza", "city": "Sandbox", "state": "CA", "zip": "00000"}


async def run(case_id: str, case: dict, front: str, filing_id: str | None = None) -> dict:
    filing_id = filing_id or str(uuid.uuid4())
    channel = delivery_bridge.channel_for_front(front)

    # Render RELAY's real form for this front. A failure here must NOT be
    # swallowed into a placeholder: a filing that silently is not the real form
    # is worse than a filing that failed, because only one of them gets noticed.
    pdf_bytes, form_id = delivery_bridge.render_filing_pdf(
        front,
        case,
        {
            "filing_id": filing_id,
            "hospital_address": (case.get("hospital") or {}).get("address"),
        },
    )

    hospital_name = (case.get("hospital") or {}).get("name", "unknown hospital")
    if channel == "fax":
        destination = _TEST_FAX_DESTINATION
        real_destination = PPDR_FAX_NUMBER
    else:
        destination = {"name": hospital_name, **_TEST_MAIL_ADDRESS}
        real_destination = hospital_name
    vendor_result = delivery_bridge.deliver(
        filing_id=filing_id,
        case_id=case_id,
        front=front,
        pdf=pdf_bytes,
        destination=destination,
        channel=channel,
    )

    # Task 2 (persona 5 WO6): save the real filled PDF Filer just rendered as
    # a case document -- contract §3.1's `generated_application`/
    # `generated_letter`, exactly the artifact CANVAS's document gallery
    # exists to show a judge. `gcs_uri` degrades to `None` (never raises) if
    # no bucket is configured; the document record still gets created either
    # way so "a filing happened" always has a corresponding document.
    gcs_uri = document_storage.upload_pdf(case_id, filing_id, form_id, pdf_bytes)
    doc_type = document_storage.doc_type_for_front(front)
    doc_id = store.add_document(
        case_id,
        {
            "type": doc_type,
            "gcs_uri": gcs_uri,
            "extracted": {
                "front": front,
                "form_id": form_id,
                "filing_id": filing_id,
                "channel": channel,
                "pdf_bytes": len(pdf_bytes),
            },
            "verified": None,
            "verification_notes": "",
        },
    )

    filing = {
        "case_id": case_id,
        "front": front,
        "channel": channel,
        "vendor_id": vendor_result.get("vendor_id"),
        "status": vendor_result.get("status", "sent"),
        "form_id": form_id,
        "pdf_bytes": len(pdf_bytes),
        "doc_id": doc_id,
        "gcs_uri": gcs_uri,
        "real_destination": real_destination,
        "proof": {
            k: v
            for k, v in vendor_result.items()
            if k in ("vendor_id", "tracking", "simulated", "vendor")
        },
    }
    store.create_filing(filing, filing_id=filing_id)

    fact = {
        "case_id": case_id,
        "front": front,
        "filing_id": filing_id,
        "channel": channel,
        "vendor_id": vendor_result.get("vendor_id"),
        "status": filing["status"],
        "simulated": bool(vendor_result.get("simulated")),
        "form_id": form_id,
        "pdf_bytes": len(pdf_bytes),
        "doc_id": doc_id,
        "gcs_uri": gcs_uri,
        "real_destination": real_destination,
    }
    tool = common.make_fact_tool(
        "get_filer_result",
        "Return the filing that was just sent: channel, vendor id, and status.",
        fact,
    )
    prompt = (
        f"Report the filing just sent for case {case_id}, front {front!r}. "
        "Call get_filer_result first."
    )
    turn = await common.run_agent_turn(NAME, config.GEMINI_MODEL, INSTRUCTION, [tool], prompt)
    return {"fact": fact, "filing_id": filing_id, **turn}
