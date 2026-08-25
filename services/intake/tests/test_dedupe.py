"""Idempotency claim -- fail-open path needs no GCS client installed; the
atomic-claim path is exercised against a fake `google.cloud.storage.Client`
so it needs neither network nor real credentials."""

from __future__ import annotations

import pytest
from intake import dedupe


def test_claim_fails_open_without_a_bucket_configured(monkeypatch):
    monkeypatch.delenv("GCS_DOCUMENTS_BUCKET", raising=False)
    assert dedupe.claim("gmail_push", "evt_1") is True
    assert dedupe.claim("gmail_push", "evt_1") is True  # still True -- no state to dedupe against


def test_claim_is_atomic_first_true_then_false(monkeypatch):
    pytest.importorskip("google.cloud.storage")
    monkeypatch.setenv("GCS_DOCUMENTS_BUCKET", "ef-documents-test")

    written: dict[str, bool] = {}

    class FakePreconditionFailed(Exception):
        pass

    class FakeBlob:
        def __init__(self, path):
            self.path = path

        def upload_from_string(self, data, if_generation_match=None):
            if self.path in written:
                raise FakePreconditionFailed(self.path)
            written[self.path] = True

    class FakeBucket:
        def blob(self, path):
            return FakeBlob(path)

    class FakeClient:
        def bucket(self, name):
            return FakeBucket()

    import google.api_core.exceptions as gax

    monkeypatch.setattr(gax, "PreconditionFailed", FakePreconditionFailed, raising=False)
    monkeypatch.setattr("google.cloud.storage.Client", FakeClient)

    assert dedupe.claim("gmail_attachment", "msg_1:bill.pdf") is True
    assert dedupe.claim("gmail_attachment", "msg_1:bill.pdf") is False  # already claimed
    assert dedupe.claim("gmail_attachment", "msg_1:other.pdf") is True  # different key, own claim
