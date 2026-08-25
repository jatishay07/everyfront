"""Filer: renders and sends one front's filing, records vendor proof.

Playbook §4 persona 5, WO1: "renders via RELAY, sends, records proof, appends
events." RELAY's PDF-fill engine and vendor clients (packages/delivery) are
still an empty stub as of this work order, so `_render_stub_pdf` below stands
in for RELAY's coordinate-map PDF fill -- it produces real bytes and a real
filing record so the human-in-the-loop demo path is complete end-to-end, but
it is NOT a filled form. See delivery_bridge.py for the same honesty pattern
on the send side (SIMULATED vendor ids, clearly marked).

Filer only ever runs from `pipeline.approve_and_request_filing`, which will
not call it unless (a) `POST /cases/{id}/approve_filing` has been called and
(b) Verifier passed. The human-in-the-loop gate is enforced by the caller, not
by this module trusting its own judgment.
"""

from __future__ import annotations

import uuid

from .. import config, delivery_bridge
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
# §1.2) or the hospital's/collector's mailing address for the rest. Test-mode
# only, per RELAY's guardrail (persona 4): never a real hospital destination.
PPDR_FAX_NUMBER = "888-610-4092"


def _render_stub_pdf(case: dict, front: str) -> bytes:
    lines = [
        "SYNTHETIC FILING -- DEMO ONLY",
        f"case_id={case.get('case_id')}",
        f"front={front}",
        f"hospital={(case.get('hospital') or {}).get('name', 'unknown')}",
        "This is a SWARM placeholder PDF -- RELAY's real PDF-fill engine "
        "(packages/delivery) had not shipped a coordinate map when this was written.",
    ]
    return ("\n".join(lines)).encode("utf-8")


async def run(case_id: str, case: dict, front: str, filing_id: str | None = None) -> dict:
    filing_id = filing_id or str(uuid.uuid4())
    channel = CHANNEL_BY_FRONT.get(front, "mail")
    pdf_bytes = _render_stub_pdf(case, front)

    if channel == "fax":
        vendor_result = delivery_bridge.send_fax(filing_id, pdf_bytes, PPDR_FAX_NUMBER)
    else:
        to_address = {
            "name": (case.get("hospital") or {}).get("name", "unknown hospital"),
            "test_mode": True,
        }
        vendor_result = delivery_bridge.send_mail(filing_id, pdf_bytes, to_address)

    filing = {
        "case_id": case_id,
        "front": front,
        "channel": channel,
        "vendor_id": vendor_result.get("vendor_id"),
        "status": vendor_result.get("status", "sent"),
        "proof": {
            k: v for k, v in vendor_result.items() if k in ("vendor_id", "tracking", "simulated")
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
        "source": delivery_bridge.bridge_sources(),
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
