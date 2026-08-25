"""Phaxio fax client -- §4 persona 4 WO3.

Docs: https://www.phaxio.com/docs/api/v2/faxes/create_and_send_fax

    curl https://api.phaxio.com/v2/faxes \\
      -u 'API_KEY:API_SECRET' -F 'to=+1...' -F 'file=@bill.pdf'

Test API keys (the only kind this repo ever configures -- agreement §2.4,
no secrets in code, and §4 persona 4's "test-mode first, always") simulate
the whole call: Phaxio never actually dials, and the response shape is
identical to a live send. NOT exercised against a live Phaxio account in
this change -- verified against the public API reference above. `send_fax`
degrades to `FakeFaxVendor` on any request exception so a vendor outage never
blocks the demo pipeline (§6: "vendor test-mode surprises -- swap to the
other vendor"; here the swap is to the fake recorder, which is the same
interface).

`requests` is imported lazily inside `send()` -- see `pdf/engine.py`'s
docstring for why a hard top-level import of an optional dependency here
would break test collection for personas who never touch this package.
"""

from __future__ import annotations

import os

from .allowlist import assert_fax_destination_allowed
from .base import VendorResult
from .fake import FakeFaxVendor

PHAXIO_BASE_URL = "https://api.phaxio.com/v2"


class PhaxioFaxClient:
    channel = "fax"

    def __init__(
        self,
        api_key: str | None = None,
        api_secret: str | None = None,
        *,
        callback_url: str | None = None,
        timeout_s: float = 20.0,
    ) -> None:
        self.api_key = api_key or os.environ.get("PHAXIO_API_KEY", "")
        self.api_secret = api_secret or os.environ.get("PHAXIO_API_SECRET", "")
        self.callback_url = callback_url or os.environ.get("PHAXIO_CALLBACK_URL", "")
        self.timeout_s = timeout_s
        self._fallback = FakeFaxVendor()

    def send(self, filing_id: str, pdf: bytes, destination: str) -> VendorResult:
        number = assert_fax_destination_allowed(destination)
        if not (self.api_key and self.api_secret):
            # No credentials configured -- fall back rather than fail the filing.
            return self._fallback.send(filing_id, pdf, number)

        import requests

        data = {"to": number}
        if self.callback_url:
            data["callback_url"] = self.callback_url
        try:
            resp = requests.post(
                f"{PHAXIO_BASE_URL}/faxes",
                auth=(self.api_key, self.api_secret),
                data=data,
                files={"file": (f"{filing_id}.pdf", pdf, "application/pdf")},
                timeout=self.timeout_s,
            )
            resp.raise_for_status()
            body = resp.json()
            fax_id = str(body.get("data", {}).get("id", ""))
            if not fax_id:
                raise ValueError(f"Phaxio response missing data.id: {body!r}")
            return VendorResult(
                vendor="phaxio",
                vendor_id=fax_id,
                status="sent",
                proof={"phaxio_id": fax_id, "destination": number},
            )
        except Exception:  # noqa: BLE001 -- vendor outage must not block the filing
            return self._fallback.send(filing_id, pdf, number)

    def parse_status_callback(self, payload: dict) -> tuple[str, str]:
        """Phaxio webhook body: {"fax": {"id": ..., "status": "success"|"failed"}, ...}."""
        fax = payload.get("fax", payload)
        vendor_id = str(fax.get("id", ""))
        raw_status = str(fax.get("status", "")).lower()
        status = {"success": "delivered", "failed": "failed"}.get(raw_status, "sent")
        return vendor_id, status
