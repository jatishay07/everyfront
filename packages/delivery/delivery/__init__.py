"""RELAY (persona 4) -- vendors behind a swappable interface.

    packages/delivery/delivery/
      pdf/            fill_form(form_id, case, extra) -- AcroForm + overlay + generated
      vendors/         Phaxio (fax) + Lob (mail), one interface, FakeVendor fallback
      calendar_sync    Deadline -> demo Google Calendar, red <=7 days
      drive_sync       generated filings -> per-case Drive folder
      google_auth      one OAuth credential loader for the demo Google account

Every public name below is what SWARM's Filer (services/agent-core) is
expected to call; nothing in this package writes to Firestore or decides
legal outcomes (agreement §2.1) -- it renders and delivers what
`packages/rules` already computed.
"""

from __future__ import annotations

from .calendar_sync import sync_deadlines
from .drive_sync import mirror_case_filings
from .pdf import FORM_REGISTRY, fill_form
from .vendors import (
    FakeFaxVendor,
    FakeMailVendor,
    LobMailClient,
    PhaxioFaxClient,
    ProductionCredentialError,
    UnsafeDestinationError,
    handle_status_callback,
    send_filing,
)

__all__ = [
    "fill_form",
    "FORM_REGISTRY",
    "send_filing",
    "handle_status_callback",
    "FakeFaxVendor",
    "FakeMailVendor",
    "PhaxioFaxClient",
    "LobMailClient",
    "UnsafeDestinationError",
    "ProductionCredentialError",
    "sync_deadlines",
    "mirror_case_filings",
]
