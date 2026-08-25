"""The recording FakeVendor.

§4 persona 4 WO3: "If you cannot get vendor credentials, build the interface
and a recording FakeVendor that produces realistic vendor IDs and proof
objects -- the interface and the audit trail matter more than the live send."

Used as the default in dev/CI (no network, no API keys) and as the fallback
if a live vendor call raises -- §6 treats vendor test-mode failure as a
"swap the vendor" risk, and both `PhaxioFaxClient`/`LobMailClient` degrade to
this rather than blocking the demo.
"""

from __future__ import annotations

import uuid
from typing import Any

from .allowlist import assert_fax_destination_allowed, assert_mail_destination_allowed
from .base import VendorResult


class FakeFaxVendor:
    channel = "fax"

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []  # the recording -- inspectable in tests

    def send(self, filing_id: str, pdf: bytes, destination: str) -> VendorResult:
        number = assert_fax_destination_allowed(destination)
        vendor_id = f"fake-fax-{uuid.uuid4().hex[:12]}"
        record = {
            "filing_id": filing_id,
            "destination": number,
            "pdf_bytes": len(pdf),
            "vendor_id": vendor_id,
        }
        self.sent.append(record)
        return VendorResult(
            vendor="fake",
            vendor_id=vendor_id,
            status="sent",
            proof={"phaxio_id": vendor_id, "pages": 1, "destination": number},
        )

    def parse_status_callback(self, payload: dict) -> tuple[str, str]:
        return payload.get("vendor_id", ""), payload.get("status", "delivered")


class FakeMailVendor:
    channel = "mail"

    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, filing_id: str, pdf: bytes, destination: dict) -> VendorResult:
        address = assert_mail_destination_allowed(destination)
        vendor_id = f"fake-ltr_{uuid.uuid4().hex[:20]}"
        tracking = f"9400 1000 0000 {uuid.uuid4().int % 10**8:08d}"
        record = {
            "filing_id": filing_id,
            "destination": address,
            "pdf_bytes": len(pdf),
            "vendor_id": vendor_id,
            "tracking": tracking,
        }
        self.sent.append(record)
        return VendorResult(
            vendor="fake",
            vendor_id=vendor_id,
            status="sent",
            proof={"lob_id": vendor_id, "tracking": tracking, "destination": address},
        )

    def parse_status_callback(self, payload: dict) -> tuple[str, str]:
        return payload.get("vendor_id", ""), payload.get("status", "delivered")
