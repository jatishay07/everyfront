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


def test_release_returns_a_key_to_unclaimed_so_a_retry_can_take_it(monkeypatch):
    """REGRESSION: without `release`, a claim taken before work that then
    fails is permanent -- the redelivery reads the marker, reports
    `{"status": "duplicate"}` and 200-acks a message it never processed.
    """
    pytest.importorskip("google.cloud.storage")
    monkeypatch.setenv("GCS_DOCUMENTS_BUCKET", "ef-documents-test")
    written = _install_fake_gcs(monkeypatch)

    assert dedupe.claim("gmail_push", "delivery_1") is True
    assert dedupe.claim("gmail_push", "delivery_1") is False
    dedupe.release("gmail_push", "delivery_1")
    assert written == {}, "release did not remove the marker blob"
    assert dedupe.claim("gmail_push", "delivery_1") is True  # the retry gets to run


def test_release_never_raises_over_the_error_that_caused_it(monkeypatch):
    """`release` runs inside an `except` block whose exception is about to be
    re-raised. That exception is the one that explains what actually went
    wrong; a failure to clean up a marker must not replace it with a confusing
    GCS error from the cleanup path."""
    pytest.importorskip("google.cloud.storage")
    monkeypatch.setenv("GCS_DOCUMENTS_BUCKET", "ef-documents-test")

    class ExplodingClient:
        def bucket(self, name):
            raise RuntimeError("GCS is having a day")

    monkeypatch.setattr("google.cloud.storage.Client", ExplodingClient)
    dedupe.release("gmail_push", "delivery_1")  # must not raise


def test_release_is_a_no_op_without_a_bucket_configured(monkeypatch):
    """Mirrors `claim`'s fail-open: local dev has no bucket, so there is no
    marker to remove and no GCS client to build."""
    monkeypatch.delenv("GCS_DOCUMENTS_BUCKET", raising=False)
    dedupe.release("gmail_push", "delivery_1")  # must not raise or import GCS


def _install_fake_gcs(monkeypatch):
    """A `google.cloud.storage.Client` backed by a dict, with the atomic
    `if_generation_match=0` create and the delete that `release` needs."""
    written: dict[str, bool] = {}

    class FakePreconditionFailed(Exception):
        pass

    class FakeNotFound(Exception):
        pass

    class FakeBlob:
        def __init__(self, path):
            self.path = path

        def upload_from_string(self, data, if_generation_match=None):
            if self.path in written:
                raise FakePreconditionFailed(self.path)
            written[self.path] = True

        def delete(self):
            if self.path not in written:
                raise FakeNotFound(self.path)
            del written[self.path]

    class FakeBucket:
        def blob(self, path):
            return FakeBlob(path)

    class FakeClient:
        def bucket(self, name):
            return FakeBucket()

    import google.api_core.exceptions as gax

    monkeypatch.setattr(gax, "PreconditionFailed", FakePreconditionFailed, raising=False)
    monkeypatch.setattr("google.cloud.storage.Client", FakeClient)
    return written
