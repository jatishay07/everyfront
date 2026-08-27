"""Lob certified-mail client -- §4 persona 4 WO4.

REQUEST/RESPONSE SHAPE, source: Lob's own OpenAPI specification,
https://github.com/lob/lob-openapi (fetched 2026-08-26 while writing this
module) -- `resources/letters/models/letter_editable.yml`,
`resources/letters/attributes/extra_service.yml`, and
`resources/letters/models/certified.yml`.

    POST https://api.lob.com/v1/letters
    Basic auth: API_KEY as username, empty password

    letter_editable required fields: to, from, file, color, use_type
      * extra_service enum: certified | certified_return_receipt | registered
        -- SINGULAR `extra_service`. Lob's help-centre article says
        `extra_services` (plural); the OpenAPI spec is the authority and says
        singular. Cited because the two disagree and a wrong field name is a
        422 that would land in the degrade-to-stub path looking like a vendor
        outage.
      * `use_type` is REQUIRED (enum marketing | operational) unless an
        account default is configured. This client sends `operational` -- a
        statutory dispute letter is transactional correspondence, not
        marketing. It was missing before this change, which would have made
        every live request fail validation.

    certified letter response: id (`ltr_...`), tracking_number, tracking_events.
    The spec's own note on test mode: tracking_number -- "Dummy tracking
    numbers are created in test mode"; tracking_events -- "Not populated in
    test mode."

TEST MODE IS MANDATORY HERE. Lob prefixes keys by environment (`test_...` vs
`live_...`, docs.lob.com "API Keys"), so `credentials.py` can check it exactly,
and does so BEFORE the destination is considered. A `live_` key is refused
outright: a certified letter is physical and irreversible.

NOT exercised against a live Lob account -- no key exists yet (see this
package's README for the signup). Everything below is verified against the
spec above and a faked HTTP transport only.

Falls back to `FakeMailVendor` on a transport failure with the reason recorded
in the proof object, same as the fax client, and likewise refuses to degrade
a credential or destination violation into a quiet stub.

`requests` is imported lazily inside `send()` -- see `pdf/engine.py`'s
docstring for why a hard top-level import of an optional dependency here
would break test collection for personas who never touch this package.
"""

from __future__ import annotations

import os

from .allowlist import assert_mail_destination_allowed
from .base import VendorResult, degraded
from .credentials import assert_lob_key_is_test
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

    def _assert_from_address_allowed(self) -> None:
        """The return address is a destination too -- a returned-to-sender
        certified letter is delivered to it. The constructor's comment has
        always claimed this address "must also clear the allowlist"; before
        this change nothing checked, which is a convention, not a control."""
        assert_mail_destination_allowed(
            {
                "line1": self.from_address.get("address_line1", ""),
                "city": self.from_address.get("address_city", ""),
                "state": self.from_address.get("address_state", ""),
                "zip": self.from_address.get("address_zip", ""),
            }
        )

    def send(self, filing_id: str, pdf: bytes, destination: dict) -> VendorResult:
        # Credentials first -- see PhaxioFaxClient.send for why the order is
        # itself the guardrail.
        if self.api_key:
            assert_lob_key_is_test(self.api_key)
        address = assert_mail_destination_allowed(destination)
        self._assert_from_address_allowed()
        if not self.api_key:
            # No credentials -- clearly-labelled simulated send, as always.
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
            # Required by letter_editable.yml; a statutory dispute letter is
            # operational correspondence. Omitting it 422s a live request.
            "use_type": "operational",
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
                # A `test_` key produces a real `ltr_` id and, per Lob's spec,
                # a DUMMY tracking number -- nothing is printed or mailed. It
                # is a simulation and must say so. See base.py's contract.
                simulated=True,
                proof={
                    "lob_id": letter_id,
                    "tracking": tracking,
                    "destination": address,
                    "mode": "test",
                },
            )
        except Exception as exc:  # noqa: BLE001 -- vendor outage must not block the filing
            return degraded(self._fallback.send(filing_id, pdf, address), "lob", exc)

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
