"""The swappable interface every vendor (fax, mail, and the recording fake)
implements -- §4 persona 4 WO3/WO4: "ONE swappable interface:
`send(filing_id, pdf, destination) -> vendor_id`, plus a status callback
that publishes filing.completed."

§6 risk register calls vendor test-mode failure a "swap the vendor" risk
precisely because both Phaxio and Lob sit behind this one shape.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class VendorResult:
    """What every `send()` returns, and what `filings/{filing_id}.proof` is
    built from (contract §3.1).

    THE `simulated` CONTRACT -- published here for `services/agent-core` to
    consume; this docstring is the source of truth for both halves.
    -------------------------------------------------------------------
    `simulated` answers exactly one question: **did a physical fax or a
    physical letter become possible as a result of this call?**

        simulated = True   Nothing left this system that could reach a real
                           recipient. Either a stub vendor recorded the send
                           (`vendor == "fake"`), or a real vendor accepted it
                           under TEST-MODE credentials -- Phaxio test keys
                           place no phone call, Lob `test_` keys never enter
                           the mail stream. A test-mode send is still a
                           simulation; a real vendor id in `proof` does not
                           make it a real send.

        simulated = False  A genuine production send: production credentials,
                           real vendor, a fax dialed or a letter printed.

    Three rules the two halves depend on:

      1. **Always present, never None.** It is a required field with no
         default precisely so a new vendor client cannot forget it and
         inherit a silent `False` -- defect #6 in HANDOFF.md was exactly that
         ("every filing was reported as a live send") and it recurred once
         after being fixed.
      2. **`vendor == "fake"` implies `simulated is True`**, always, with no
         exception. `filing.py` re-asserts this rather than trusting it.
      3. **Fail closed.** If a `VendorClient` returns something without a
         boolean `simulated`, `send_filing()` reports `True`. The safe error
         is under-claiming a live send; over-claiming one is how a stub gets
         reported to a patient as a filed dispute.

    As of this change `simulated` is **True for every send this package can
    produce**, because `credentials.py` refuses to send with anything that is
    not a test credential. `False` is reachable only by a future vendor
    client that deliberately opts into production mode.

    `send_filing()` copies this value verbatim into its returned dict under
    the key `"simulated"`; `agent_core.agents.filer` reads that key to set
    `filings/{filing_id}.simulated` and to narrate "SIMULATED" vs "live" in
    the audit trail.
    """

    vendor: str  # "phaxio" | "lob" | "fake"
    vendor_id: str
    status: str  # "queued" | "sent" | "delivered" | "failed"
    simulated: bool  # see the contract above -- required, never defaulted
    proof: dict[str, Any] = field(default_factory=dict)
    sent_at: datetime = field(default_factory=lambda: datetime.now(UTC))


def degraded(result: VendorResult, attempted_vendor: str, exc: BaseException) -> VendorResult:
    """Stamp a stub result with the reason the real vendor call failed.

    A fallback that leaves no trace is a silent failure, and silent failure is
    this project's signature defect (HANDOFF.md, "THE BUG PATTERN": every
    serious defect reported success while doing nothing). The stub still
    stands in so a vendor outage cannot block a filing, but the persisted
    `filings/{filing_id}.proof` now says which vendor was attempted and what
    it raised.
    """
    return replace(
        result,
        simulated=True,
        proof={
            **result.proof,
            "mode": "stub",
            "attempted_vendor": attempted_vendor,
            "fallback_reason": f"{type(exc).__name__}: {exc}",
        },
    )


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
