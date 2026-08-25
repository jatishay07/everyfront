"""The swappable interface every vendor (fax, mail, and the recording fake)
implements -- §4 persona 4 WO3/WO4: "ONE swappable interface:
`send(filing_id, pdf, destination) -> vendor_id`, plus a status callback
that publishes filing.completed."

§6 risk register calls vendor test-mode failure a "swap the vendor" risk
precisely because both Phaxio and Lob sit behind this one shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class VendorResult:
    """What every `send()` returns, and what `filings/{filing_id}.proof` is
    built from (contract §3.1)."""

    vendor: str  # "phaxio" | "lob" | "fake"
    vendor_id: str
    status: str  # "queued" | "sent" | "delivered" | "failed"
    proof: dict[str, Any] = field(default_factory=dict)
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))


class VendorClient(Protocol):
    """`destination` is a fax number (str) for fax clients or an address
    dict ({line1, city, state, zip}) for mail clients -- callers already
    know which because they picked fax vs mail."""

    channel: str  # "fax" | "mail"

    def send(self, filing_id: str, pdf: bytes, destination: Any) -> VendorResult: ...

    def parse_status_callback(self, payload: dict) -> tuple[str, str]:
        """Vendor webhook payload -> (vendor_id, normalized status).

        Normalized status is one of "sent" | "delivered" | "failed", the
        vocabulary `filings/{filing_id}.status` uses regardless of vendor.
        """
        ...
