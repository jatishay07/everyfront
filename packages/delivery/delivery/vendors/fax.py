"""Phaxio fax client -- §4 persona 4 WO3.

REQUEST/RESPONSE SHAPE, source: https://www.phaxio.com/docs/api/v2/faxes/create_and_send_fax
(fetched 2026-08-26 while writing this module).

    POST https://api.phaxio.com/v2/faxes
    Basic auth: API_KEY:API_SECRET
    multipart form: to (E.164, required), file (binary, required),
                    callback_url (optional)

    200 body, verbatim from the reference:
        {"success":true,"message":"Fax queued for sending","data":{"id":1234}}

    -> `data.id` is the fax id, and the only field this client needs.

TEST MODE IS MANDATORY HERE, not a default. `credentials.py` refuses any
credential that is not provably a Phaxio TEST credential, and it runs before
the destination is even normalized -- so there is no ordering in which a
production key reaches `requests.post`. Phaxio's own description of test
credentials: "the Phaxio system will simulate faxes being sent or received
and your balance will not be affected"; nothing dials, nothing prints
(https://www.phaxio.com/blog/guide/test-credentials).

NOT exercised against a live Phaxio account -- no key exists yet (see this
package's README for the signup). Everything below is verified against the
public API reference and a faked HTTP transport only.

`send()` degrades to `FakeFaxVendor` on a *transport* failure so a vendor
outage never blocks the demo pipeline (§6: "vendor test-mode surprises"),
and records WHY in the proof object so the degradation is never silent. It
deliberately does NOT degrade on `ProductionCredentialError` or
`UnsafeDestinationError`: those are misconfigurations of a safety control
and must stop, loudly.

`requests` is imported lazily inside `send()` -- see `pdf/engine.py`'s
docstring for why a hard top-level import of an optional dependency here
would break test collection for personas who never touch this package.
"""

from __future__ import annotations

import os

from .allowlist import assert_fax_destination_allowed
from .base import VendorResult, degraded
from .credentials import assert_phaxio_credentials_are_test
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
        # ORDER IS THE GUARDRAIL. Credentials first: a production key is
        # refused before any destination is considered, so "production key +
        # real hospital number" has no code path at all -- not even one that
        # gets as far as evaluating whether the number is allowed.
        if self.api_key or self.api_secret:
            assert_phaxio_credentials_are_test(self.api_key, self.api_secret)
        number = assert_fax_destination_allowed(destination)
        if not (self.api_key and self.api_secret):
            # No credentials configured -- a clearly-labelled simulated send,
            # which is exactly what this system has always done. Never a
            # silent failure, never reported as live (`simulated is True`).
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
                # A real Phaxio fax id from a TEST credential is still a
                # simulation -- no phone call was placed. Calling this a live
                # send because the vendor name isn't "fake" is precisely the
                # lie the `simulated` contract (base.py) exists to prevent.
                simulated=True,
                proof={"phaxio_id": fax_id, "destination": number, "mode": "test"},
            )
        except Exception as exc:  # noqa: BLE001 -- vendor outage must not block the filing
            return degraded(self._fallback.send(filing_id, pdf, number), "phaxio", exc)

    def parse_status_callback(self, payload: dict) -> tuple[str, str]:
        """Phaxio webhook body: {"fax": {"id": ..., "status": "success"|"failed"}, ...}."""
        fax = payload.get("fax", payload)
        vendor_id = str(fax.get("id", ""))
        raw_status = str(fax.get("status", "")).lower()
        status = {"success": "delivered", "failed": "failed"}.get(raw_status, "sent")
        return vendor_id, status
