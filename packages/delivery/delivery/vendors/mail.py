"""Lob certified-mail client -- §4 persona 4 WO4.

Docs: https://docs.lob.com/#tag/Letters/operation/letter_create -- certified
mail is `extra_service=certified`, basic auth with the API key as username.
`test_...` keys (§1.4, "Lob (certified mail, `test_` keys)") validate the
request and simulate the full lifecycle without ever entering the physical
mail stream, same shape as a live send. NOT exercised against a live Lob
account in this change -- verified against the public API reference. Falls
back to `FakeMailVendor` on any request failure, same reasoning as the fax
client.

`requests` is imported lazily inside `send()` -- see `pdf/engine.py`'s
docstring for why a hard top-level import of an optional dependency here
would break test collection for personas who never touch this package.
"""

from __future__ import annotations

import os

from .allowlist import assert_mail_destination_allowed
from .base import VendorResult
from .fake import FakeMailVendor

LOB_BASE_URL = "https://api.lob.com/v1"


class LobMailClient:
    channel = "mail"

    def __init__(
        self,
        api_key: str | None = None,
        *,
        from_address: dict | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("LOB_API_KEY", "")
        # The advocate/patient's own return address -- must also clear the
        # allowlist so a live key never mails FROM a real address either.
        self.from_address = from_address or {
            "name": "Every Front Patient Advocate",
            "address_line1": "1 Demo Plaza",
            "address_city": "Sandbox",
            "address_state": "CA",
            "address_zip": "00000",
        }
        self.timeout_s = timeout_s
        self._fallback = FakeMailVendor()

    def send(self, filing_id: str, pdf: bytes, destination: dict) -> VendorResult:
        address = assert_mail_destination_allowed(destination)
        if not self.api_key:
            return self._fallback.send(filing_id, pdf, address)

        import requests

        payload = {
            "description": f"Every Front filing {filing_id}",
            "to[name]": address.get("name", ""),
            "to[address_line1]": address.get("line1", ""),
            "to[address_city]": address.get("city", ""),
            "to[address_state]": address.get("state", ""),
            "to[address_zip]": address.get("zip", ""),
            "from[name]": self.from_address["name"],
            "from[address_line1]": self.from_address["address_line1"],
            "from[address_city]": self.from_address["address_city"],
            "from[address_state]": self.from_address["address_state"],
            "from[address_zip]": self.from_address["address_zip"],
            "color": "false",
            "extra_service": "certified",
        }
        try:
            resp = requests.post(
                f"{LOB_BASE_URL}/letters",
                auth=(self.api_key, ""),
                data=payload,
                files={"file": (f"{filing_id}.pdf", pdf, "application/pdf")},
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            body = resp.json()
            letter_id = str(body.get("id", ""))
            if not letter_id:
                raise ValueError(f"Lob response missing id: {body!r}")
            tracking = body.get("tracking_number", "")
            return VendorResult(
                vendor="lob",
                vendor_id=letter_id,
                status="sent",
                proof={"lob_id": letter_id, "tracking": tracking, "destination": address},
            )
        except Exception:  # noqa: BLE001 -- vendor outage must not block the filing
            return self._fallback.send(filing_id, pdf, address)

    def parse_status_callback(self, payload: dict) -> tuple[str, str]:
        """Lob webhook event: {"event_type": {"id": "letter.delivered"}, "body": {"id": ...}}."""
        body = payload.get("body", payload)
        vendor_id = str(body.get("id", ""))
        event_id = str(payload.get("event_type", {}).get("id", "")).lower()
        if "delivered" in event_id:
            status = "delivered"
        elif "rejected" in event_id or "cancelled" in event_id or "returned" in event_id:
            status = "failed"
        else:
            status = "sent"
        return vendor_id, status
