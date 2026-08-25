"""Bridge to packages/delivery (owned by RELAY, persona 4).

As of this work order, `packages/delivery/delivery/__init__.py` is an empty
stub -- RELAY has not started WO2-4 (PDF engine, Phaxio, Lob) yet. The Filer
agent still needs *something* to call so the human-in-the-loop approval path
is demoable end-to-end today, so this module probes for RELAY's real
interface first and falls back to a clearly-labeled SIMULATED sender that
returns the same shape (`vendor_id`, `status`) the real one will.

Every simulated send is logged as SIMULATED in the case's events -- never
silently indistinguishable from a real Phaxio/Lob call -- and the guardrail in
BUILD_PLAYBOOK.md §4 persona 4 ("never send to a real hospital fax/address")
is upheld trivially because the fallback never contacts a network at all.

HANDOFF -> RELAY: expected interface, so swapping this out is a one-line diff
in `_probe_fax`/`_probe_mail`/`_probe_calendar` below --

    fax.send(filing_id: str, pdf_bytes: bytes, to_number: str) -> dict
        return {"vendor_id": ..., "status": ...}
    mail.send(filing_id: str, pdf_bytes: bytes, to_address: dict) -> dict
        return {"vendor_id": ..., "status": ..., "tracking": ...}
    calendar.add_deadline(summary: str, due: date, citation: str, color: str) -> str
        returns an event id
"""

from __future__ import annotations

import uuid
from typing import Any


def _probe_fax():
    try:
        from delivery.fax import send  # type: ignore[import-not-found]

        return send, "delivery.fax.send (RELAY)"
    except ImportError:
        return None, "SWARM fallback -- RELAY's Phaxio client not yet available"


def _probe_mail():
    try:
        from delivery.mail import send  # type: ignore[import-not-found]

        return send, "delivery.mail.send (RELAY)"
    except ImportError:
        return None, "SWARM fallback -- RELAY's Lob client not yet available"


def _probe_calendar():
    try:
        from delivery.calendar import add_deadline  # type: ignore[import-not-found]

        return add_deadline, "delivery.calendar.add_deadline (RELAY)"
    except ImportError:
        return None, "SWARM fallback -- RELAY's Calendar client not yet available"


_fax_send, FAX_SOURCE = _probe_fax()
_mail_send, MAIL_SOURCE = _probe_mail()
_calendar_add, CALENDAR_SOURCE = _probe_calendar()


def send_fax(filing_id: str, pdf_bytes: bytes, to_number: str) -> dict[str, Any]:
    if _fax_send is not None:
        return _fax_send(filing_id, pdf_bytes, to_number)
    return {
        "vendor_id": f"SIMULATED-FAX-{uuid.uuid4().hex[:12]}",
        "status": "sent",
        "simulated": True,
        "to_number": to_number,
        "bytes": len(pdf_bytes),
    }


def send_mail(filing_id: str, pdf_bytes: bytes, to_address: dict) -> dict[str, Any]:
    if _mail_send is not None:
        return _mail_send(filing_id, pdf_bytes, to_address)
    return {
        "vendor_id": f"SIMULATED-LOB-{uuid.uuid4().hex[:12]}",
        "status": "sent",
        "tracking": f"SIMULATED-TRACK-{uuid.uuid4().hex[:10].upper()}",
        "simulated": True,
        "to_address": to_address,
        "bytes": len(pdf_bytes),
    }


def add_calendar_deadline(summary: str, due, citation: str, color: str = "default") -> str:
    if _calendar_add is not None:
        return _calendar_add(summary, due, citation, color)
    return f"SIMULATED-CAL-{uuid.uuid4().hex[:8]}"


def bridge_sources() -> dict[str, str]:
    return {"fax": FAX_SOURCE, "mail": MAIL_SOURCE, "calendar": CALENDAR_SOURCE}
