"""FakeFaxVendor/FakeMailVendor + the send_filing/handle_status_callback
orchestrator. Pure Python -- no vendor SDKs, no network -- so this is the
path that always runs, mirroring how the demo itself falls back when no
Phaxio/Lob credentials are configured (§4 persona 4 WO3: "the interface and
the audit trail matter more than the live send").
"""

from __future__ import annotations

import pytest
from delivery.vendors import UnsafeDestinationError, handle_status_callback, send_filing
from delivery.vendors.fake import FakeFaxVendor, FakeMailVendor

FAX_OK = "+13125550142"
MAIL_OK = {"line1": "1 Demo Plaza", "city": "Sandbox", "state": "CA", "zip": "00000"}


def test_fake_fax_send_records_and_returns_realistic_proof():
    vendor = FakeFaxVendor()
    result = send_filing(
        filing_id="fil_1",
        case_id="case_1",
        front="ppdr",
        channel="fax",
        pdf=b"%PDF fake",
        destination=FAX_OK,
        fax_client=vendor,
    )
    assert result["vendor"] == "fake"
    assert result["status"] == "sent"
    assert result["proof"]["phaxio_id"] == result["vendor_id"]
    # Regression (RELAY WO8): `agent_core.agents.filer` reads this exact key
    # to decide "SIMULATED" vs "live" in the audit trail -- it must be True
    # whenever the fake vendor is what actually ran, not just implied by
    # `vendor == "fake"` elsewhere and left unpopulated here.
    assert result["simulated"] is True
    assert len(vendor.sent) == 1
    assert vendor.sent[0]["destination"] == FAX_OK


def test_fake_mail_send_records_and_returns_tracking():
    vendor = FakeMailVendor()
    result = send_filing(
        filing_id="fil_2",
        case_id="case_1",
        front="charity_care",
        channel="mail",
        pdf=b"%PDF fake",
        destination=MAIL_OK,
        mail_client=vendor,
    )
    assert result["vendor"] == "fake"
    assert "tracking" in result["proof"]
    assert vendor.sent[0]["destination"] == MAIL_OK
    assert result["simulated"] is True  # regression, see the fax test above


def test_send_filing_marks_a_non_fake_vendor_as_not_simulated():
    """`simulated` must track whichever vendor actually ran, not always be
    True -- a real Phaxio/Lob send (vendor name != "fake") must report
    `simulated: False` so the audit trail can tell the two apart."""

    class _StubRealVendor:
        channel = "fax"

        def send(self, filing_id, pdf, destination):
            from delivery.vendors.base import VendorResult

            return VendorResult(vendor="phaxio", vendor_id="real-123", status="sent")

    result = send_filing(
        filing_id="fil_5",
        case_id="case_1",
        front="ppdr",
        channel="fax",
        pdf=b"%PDF fake",
        destination=FAX_OK,
        fax_client=_StubRealVendor(),
    )
    assert result["vendor"] == "phaxio"
    assert result["simulated"] is False


def test_send_filing_refuses_unsafe_fax_destination():
    vendor = FakeFaxVendor()
    with pytest.raises(UnsafeDestinationError):
        send_filing(
            filing_id="fil_3",
            case_id="case_1",
            front="ppdr",
            channel="fax",
            pdf=b"%PDF",
            destination="+17735551234",
            fax_client=vendor,
        )
    assert vendor.sent == []  # refused before anything was recorded


def test_send_filing_refuses_unsafe_mail_destination():
    vendor = FakeMailVendor()
    with pytest.raises(UnsafeDestinationError):
        send_filing(
            filing_id="fil_4",
            case_id="case_1",
            front="charity_care",
            channel="mail",
            pdf=b"%PDF",
            destination={
                "line1": "4440 W 95th St",
                "city": "Oak Lawn",
                "state": "IL",
                "zip": "60453",
            },
            mail_client=vendor,
        )
    assert vendor.sent == []


def test_send_filing_rejects_unknown_channel():
    with pytest.raises(ValueError, match="unknown channel"):
        send_filing(
            filing_id="fil_5",
            case_id="case_1",
            front="ppdr",
            channel="carrier_pigeon",
            pdf=b"%PDF",
            destination=FAX_OK,
        )


def test_status_callback_round_trips_through_the_vendor_map(monkeypatch, tmp_path):
    """Without a GCS bucket configured, the vendor-id -> filing-id lookaside
    is a no-op (returns None) -- exercised here directly against
    `filing.py`'s pure functions rather than faking GCS."""
    monkeypatch.delenv("GCS_DOCUMENTS_BUCKET", raising=False)
    vendor = FakeFaxVendor()
    result = send_filing(
        filing_id="fil_6",
        case_id="case_1",
        front="ppdr",
        channel="fax",
        pdf=b"%PDF",
        destination=FAX_OK,
        fax_client=vendor,
    )
    callback = handle_status_callback(
        "fax", {"vendor_id": result["vendor_id"], "status": "delivered"}
    )
    assert callback is None  # no bucket configured -- graceful no-op, not a crash


def test_status_callback_returns_none_for_unknown_vendor_id():
    assert (
        handle_status_callback("fax", {"vendor_id": "does-not-exist", "status": "delivered"})
        is None
    )
    assert handle_status_callback("mail", {"vendor_id": "", "status": "delivered"}) is None
