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


def test_send_filing_reports_a_genuine_production_send_as_not_simulated():
    """`simulated` must track what the vendor client actually did, not the
    vendor's NAME. A hypothetical production-mode client -- none exists in
    this package, `credentials.py` refuses production keys -- says so itself
    and `send_filing` passes it through verbatim."""

    class _StubProductionVendor:
        channel = "fax"

        def send(self, filing_id, pdf, destination):
            from delivery.vendors.base import VendorResult

            return VendorResult(
                vendor="phaxio", vendor_id="real-123", status="sent", simulated=False
            )

    result = send_filing(
        filing_id="fil_5",
        case_id="case_1",
        front="ppdr",
        channel="fax",
        pdf=b"%PDF fake",
        destination=FAX_OK,
        fax_client=_StubProductionVendor(),
    )
    assert result["vendor"] == "phaxio"
    assert result["simulated"] is False


def test_send_filing_reports_a_test_mode_vendor_send_as_simulated():
    """The subtler half of defect #6. `simulated` used to be derived as
    `vendor == "fake"`, which calls a Phaxio/Lob call made with TEST
    credentials a LIVE send -- it has a real vendor id and a vendor name that
    isn't "fake", and it still puts nothing on a phone line or in a mailbag.
    Only the client knows its mode, so only the client may say."""

    class _StubTestModeVendor:
        channel = "fax"

        def send(self, filing_id, pdf, destination):
            from delivery.vendors.base import VendorResult

            return VendorResult(
                vendor="phaxio", vendor_id="fax-4711", status="sent", simulated=True
            )

    result = send_filing(
        filing_id="fil_5b",
        case_id="case_1",
        front="ppdr",
        channel="fax",
        pdf=b"%PDF fake",
        destination=FAX_OK,
        fax_client=_StubTestModeVendor(),
    )
    assert result["vendor"] == "phaxio"
    assert result["simulated"] is True


def test_send_filing_fails_closed_when_a_client_omits_simulated():
    """A vendor client that does not answer the question must never be read
    as "this was live". The safe error is under-claiming a real send."""

    class _SilentVendor:
        channel = "fax"

        def send(self, filing_id, pdf, destination):
            class _Result:
                vendor = "somevendor"
                vendor_id = "x-1"
                status = "sent"
                proof: dict = {}

                class _TS:
                    @staticmethod
                    def isoformat():
                        return "1970-01-01T00:00:00+00:00"

                sent_at = _TS()

            return _Result()

    result = send_filing(
        filing_id="fil_5c",
        case_id="case_1",
        front="ppdr",
        channel="fax",
        pdf=b"%PDF fake",
        destination=FAX_OK,
        fax_client=_SilentVendor(),
    )
    assert result["simulated"] is True


def test_send_filing_overrides_a_fake_vendor_that_claims_to_be_live():
    """Rule 2 of the contract, re-asserted rather than trusted: `vendor ==
    "fake"` is simulated, whatever the client returned."""

    class _LyingFakeVendor:
        channel = "fax"

        def send(self, filing_id, pdf, destination):
            from delivery.vendors.base import VendorResult

            return VendorResult(vendor="fake", vendor_id="fake-1", status="sent", simulated=False)

    result = send_filing(
        filing_id="fil_5d",
        case_id="case_1",
        front="ppdr",
        channel="fax",
        pdf=b"%PDF fake",
        destination=FAX_OK,
        fax_client=_LyingFakeVendor(),
    )
    assert result["simulated"] is True


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
