"""Phaxio/Lob webhook parsing -- pure dict logic without a bucket configured
(the vendor-map lookaside then always misses, which is the correct
"unrecognized vendor id -> no-op" behavior, exercised here directly)."""

from __future__ import annotations

import pytest
from intake.vendor_callbacks import handle_vendor_callback


def test_phaxio_callback_without_a_known_vendor_id_is_a_noop(monkeypatch):
    monkeypatch.delenv("GCS_DOCUMENTS_BUCKET", raising=False)
    result = handle_vendor_callback("fax", {"fax": {"id": "999", "status": "success"}})
    assert result is None  # no vendor map configured -- correctly a no-op, not an error


def test_lob_callback_without_a_known_vendor_id_is_a_noop(monkeypatch):
    monkeypatch.delenv("GCS_DOCUMENTS_BUCKET", raising=False)
    result = handle_vendor_callback(
        "mail", {"event_type": {"id": "letter.delivered"}, "body": {"id": "ltr_abc"}}
    )
    assert result is None


def test_unknown_channel_raises():
    with pytest.raises(ValueError, match="unknown channel"):
        handle_vendor_callback("carrier_pigeon", {})


def test_phaxio_callback_resolves_through_the_vendor_map(monkeypatch):
    pytest.importorskip("google.cloud.storage")
    monkeypatch.setenv("GCS_DOCUMENTS_BUCKET", "ef-documents-test")

    class FakeBlob:
        def exists(self):
            return True

        def download_as_text(self):
            return '{"filing_id": "fil_42", "case_id": "case_1"}'

    class FakeBucket:
        def blob(self, path):
            assert path == "_vendor_map/phaxio/999.json"
            return FakeBlob()

    class FakeClient:
        def bucket(self, name):
            return FakeBucket()

    monkeypatch.setattr("google.cloud.storage.Client", FakeClient)

    result = handle_vendor_callback("fax", {"fax": {"id": "999", "status": "success"}})
    assert result == {"filing_id": "fil_42", "status": "delivered"}
