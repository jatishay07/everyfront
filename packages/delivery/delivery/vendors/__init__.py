"""Fax (Phaxio) + mail (Lob) behind one interface -- see `base.VendorClient`."""

from __future__ import annotations

from .allowlist import (
    UnsafeDestinationError,
    assert_fax_destination_allowed,
    assert_mail_destination_allowed,
)
from .base import VendorClient, VendorResult
from .credentials import (
    ProductionCredentialError,
    assert_lob_key_is_test,
    assert_phaxio_credentials_are_test,
)
from .fake import FakeFaxVendor, FakeMailVendor
from .fax import PhaxioFaxClient
from .filing import get_fax_client, get_mail_client, handle_status_callback, send_filing
from .mail import LobMailClient

__all__ = [
    "VendorClient",
    "VendorResult",
    "UnsafeDestinationError",
    "ProductionCredentialError",
    "assert_fax_destination_allowed",
    "assert_mail_destination_allowed",
    "assert_lob_key_is_test",
    "assert_phaxio_credentials_are_test",
    "FakeFaxVendor",
    "FakeMailVendor",
    "PhaxioFaxClient",
    "LobMailClient",
    "send_filing",
    "handle_status_callback",
    "get_fax_client",
    "get_mail_client",
]
