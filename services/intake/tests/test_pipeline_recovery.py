"""The two ways the intake pipeline used to go quietly dark.

Both defects have the shape HANDOFF.md names as this project's signature: no
crash, no failing test, a 200 on every response, and no document ever reaching
`case.document.added`.

1. A dedupe claim that outlived the work it was guarding, so the retry after a
   transient error was answered `{"status": "duplicate"}`.
2. An expired Gmail `startHistoryId`, which 404s forever with no fallback --
   and, combined with (1), wedges permanently: 404 -> 500 -> redelivery ->
   "duplicate" 200 -> the cursor never advances -> the next push 404s too.

Kept out of `test_pipeline.py` because these need stateful fakes that survive
across two calls; that file's fakes are deliberately stateless one-shots.
Nothing here imports `googleapiclient` -- see `test_gmail_history.py`.
"""

from __future__ import annotations

import logging

import pytest
from intake import gmail_client, pipeline


class _StatefulDedupe:
    """An in-memory stand-in for `intake.dedupe` with the SAME semantics as the
    real GCS `if_generation_match=0` marker: the first claim wins, every later
    claim for that key is refused until the key is released.

    Substituted for the whole module (`monkeypatch.setattr(pipeline, "dedupe",
    ...)`) rather than for `claim`/`release` individually, so that the pre-fix
    code -- which never calls `release` at all -- runs against it unchanged
    instead of erroring on a missing attribute. What these tests assert is
    behaviour on the SECOND delivery, not which functions got called.
    """

    def __init__(self) -> None:
        self.markers: set[tuple[str, str]] = set()
        self.released: list[tuple[str, str]] = []

    def claim(self, namespace: str, event_id: str) -> bool:
        key = (namespace, event_id)
        if key in self.markers:
            return False
        self.markers.add(key)
        return True

    def release(self, namespace: str, event_id: str) -> None:
        self.released.append((namespace, event_id))
        self.markers.discard((namespace, event_id))


_ONE_PDF_MESSAGE = {
    "threadId": "thread_1",
    "payload": {
        "parts": [
            {
                "mimeType": "application/pdf",
                "filename": "bill.pdf",
                "body": {"attachmentId": "att_1"},
            }
        ]
    },
}


def _wire_happy_attachment_path(monkeypatch):
    monkeypatch.setattr(pipeline.gmail_client, "fetch_message", lambda mid: _ONE_PDF_MESSAGE)
    monkeypatch.setattr(pipeline.gmail_client, "fetch_attachment_bytes", lambda mid, aid: b"%PDF")
    monkeypatch.setattr(pipeline.storage, "upload_attachment", lambda *a, **k: "gs://b/bill.pdf")


def _publish_failing_once():
    """A `pubsub.publish` that raises on its FIRST call and succeeds after."""
    calls: list[tuple] = []

    def publish(topic, data):
        calls.append((topic, data))
        if len(calls) == 1:
            raise RuntimeError("Pub/Sub publish timed out")

    return publish, calls


# ---------------------------------------------------------------------------
# Defect 1 -- claim-before-work
# ---------------------------------------------------------------------------


def test_a_failed_attachment_is_retried_on_redelivery_not_skipped_as_duplicate(monkeypatch):
    """REGRESSION: `claim()` writes the dedupe marker BEFORE the work happens.
    If the attachment fetch, the GCS upload or the publish then raises, the
    marker is already committed -- Pub/Sub redelivers, `claim` returns False,
    the handler reports `{"status": "duplicate"}` with a 200, and that
    attachment is dropped permanently. The log shows one traceback followed by
    a clean success.

    Asserted as behaviour across two deliveries -- the thing that actually
    matters -- rather than as "release() was called".
    """
    fake_dedupe = _StatefulDedupe()
    monkeypatch.setattr(pipeline, "dedupe", fake_dedupe)
    _wire_happy_attachment_path(monkeypatch)
    publish, published = _publish_failing_once()
    monkeypatch.setattr(pipeline.pubsub, "publish", publish)

    with pytest.raises(RuntimeError):
        pipeline.process_new_message("msg_1")

    # Pub/Sub nacks the 500 and redelivers the same notification.
    result = pipeline.process_new_message("msg_1")

    assert len(result) == 1, (
        "the retry after a failed publish was swallowed as a duplicate -- the claim was "
        "never released, so this attachment is dropped forever while the handler reports "
        f"success; publish attempts={len(published)}"
    )
    assert result[0]["gcs_uri"] == "gs://b/bill.pdf"
    # `0` is the MIME part's immutable `partId`, now part of the claim key --
    # see gmail_client.extract_pdf_attachments for why a filename alone was
    # dropping same-named attachments.
    assert fake_dedupe.released == [("gmail_attachment", "msg_1:0:bill.pdf")]


def test_a_successful_attachment_stays_claimed_so_the_retry_does_not_double_publish(monkeypatch):
    """The other half of the same contract: releasing on failure must not also
    release on success, or every redelivery would re-publish
    `case.document.added` and agent-core would open a second document per bill.
    """
    fake_dedupe = _StatefulDedupe()
    monkeypatch.setattr(pipeline, "dedupe", fake_dedupe)
    _wire_happy_attachment_path(monkeypatch)
    published: list[tuple] = []
    monkeypatch.setattr(
        pipeline.pubsub, "publish", lambda topic, data: published.append((topic, data))
    )

    assert len(pipeline.process_new_message("msg_1")) == 1
    assert pipeline.process_new_message("msg_1") == []  # redelivery: correctly a no-op
    assert len(published) == 1
    assert fake_dedupe.released == []


def test_a_failed_push_is_retried_on_redelivery_not_skipped_as_duplicate(monkeypatch):
    """REGRESSION: the same shape one level up. The `gmail_push` claim is taken
    on the Pub/Sub delivery id before `history.list` is even called, so any
    failure below it turns the redelivery into a 200 no-op and the entire
    notification -- every attachment on it -- is lost.
    """
    fake_dedupe = _StatefulDedupe()
    monkeypatch.setattr(pipeline, "dedupe", fake_dedupe)
    monkeypatch.setattr(pipeline.state, "get_last_history_id", lambda: "100")
    monkeypatch.setattr(pipeline.state, "set_last_history_id", lambda hid: None)

    attempts: list[str] = []

    def flaky_list(start_id):
        attempts.append(start_id)
        if len(attempts) == 1:
            raise RuntimeError("Gmail 503")
        return ["msg_a"]

    monkeypatch.setattr(pipeline.gmail_client, "list_new_message_ids", flaky_list)
    monkeypatch.setattr(pipeline, "process_new_message", lambda mid: [{"doc_id": mid}])

    with pytest.raises(RuntimeError):
        pipeline.process_gmail_push("delivery_1", {"historyId": "555"})

    result = pipeline.process_gmail_push("delivery_1", {"historyId": "555"})

    assert result["status"] == "ok", (
        "the redelivery of a push whose first attempt failed was waved through as a "
        f"duplicate; got {result!r}"
    )
    assert result["documents_published"] == 1
    assert fake_dedupe.released == [("gmail_push", "delivery_1")]


# ---------------------------------------------------------------------------
# Defect 2 -- an expired startHistoryId
# ---------------------------------------------------------------------------


class _FakeHttpError(Exception):
    """Shaped like `googleapiclient.errors.HttpError` -- see
    `test_gmail_history.py` for where that shape was read from."""

    def __init__(self, status: int):
        super().__init__(f"<HttpError {status}>")
        self.status_code = status
        self.resp = type("_Resp", (), {"status": status})()


class _FakeGmailService:
    """A Gmail service where only history ids >= `oldest_valid` still resolve;
    anything older 404s, exactly as Gmail does once the history window rolls
    past a cursor."""

    def __init__(self, *, oldest_valid: int, messages_after: dict[str, list[str]], watch: dict):
        self.oldest_valid = oldest_valid
        self.messages_after = messages_after
        self.watch_result = watch
        self.list_calls: list[dict] = []
        self.watch_calls: list[dict] = []

    # -- googleapiclient's fluent shape -------------------------------------
    def users(self):
        return self

    def history(self):
        return self

    def list(self, **kwargs):
        self.list_calls.append(kwargs)
        self._pending = kwargs
        return self

    def watch(self, **kwargs):
        self.watch_calls.append(kwargs)
        self._pending = None
        return self

    def execute(self):
        if self._pending is None:
            return self.watch_result
        start = self._pending["startHistoryId"]
        if int(start) < self.oldest_valid:
            raise _FakeHttpError(404)
        ids = self.messages_after.get(str(start), [])
        return {"history": [{"messagesAdded": [{"message": {"id": i}}]} for i in ids]}


def test_an_expired_cursor_rebootstraps_the_watch_instead_of_wedging(monkeypatch, caplog):
    """REGRESSION: Gmail 404s any `startHistoryId` that has aged out of its
    history window (the Gmail v1 discovery doc: "typically valid for at least a
    week, but in some rare circumstances may be valid for only a few hours").
    With no `try/except` and no full-sync fallback that 404 escapes as a raw
    HttpError, 500s the route, and -- with defect 1 -- is swallowed as a
    duplicate on redelivery. The cursor never advances, so the NEXT push 404s
    too. Intake goes dark permanently while every response says 200.

    Driven through the real `gmail_client.list_new_message_ids` rather than a
    stubbed one, so the 404 -> `HistoryExpired` -> re-bootstrap chain is
    exercised end to end across both modules.
    """
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "everyfront-test")
    fake_dedupe = _StatefulDedupe()
    monkeypatch.setattr(pipeline, "dedupe", fake_dedupe)

    service = _FakeGmailService(
        oldest_valid=9000,
        messages_after={"9100": ["msg_a"]},
        watch={"historyId": "9500", "expiration": "1790000000000"},
    )
    monkeypatch.setattr(gmail_client, "_service", lambda: service)

    cursor: list[str] = []
    monkeypatch.setattr(pipeline.state, "get_last_history_id", lambda: "100")  # three weeks old
    monkeypatch.setattr(pipeline.state, "set_last_history_id", lambda hid: cursor.append(hid))
    monkeypatch.setattr(pipeline, "process_new_message", lambda mid: [{"doc_id": mid}])

    with caplog.at_level(logging.ERROR, logger="intake.pipeline"):
        result = pipeline.process_gmail_push("delivery_1", {"historyId": "9100"})

    assert result["status"] == "history_expired_rebootstrapped", (
        "an expired history cursor did not re-bootstrap -- the 404 escaped and this push "
        f"failed, which is how intake wedges shut; got {result!r}"
    )
    assert service.watch_calls, "users.watch was never re-armed, so the cursor stays expired"
    assert cursor == ["9500"], (
        f"the cursor must be reset to the fresh historyId users.watch returned; got {cursor!r}"
    )
    # "process from there": the notification's own historyId is minutes old and
    # therefore still inside the window, so the message that triggered THIS
    # push is recovered rather than thrown away with the stale cursor.
    assert result["documents_published"] == 1


def test_the_rebootstrap_is_logged_loudly(monkeypatch, caplog):
    """A silent self-heal is nearly as bad as a silent failure on this project:
    messages that aged out of the window are genuinely, unrecoverably skipped,
    and a recurrence means intake is going dark between pushes. Both facts have
    to be in the log at ERROR."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "everyfront-test")
    monkeypatch.setattr(pipeline, "dedupe", _StatefulDedupe())
    service = _FakeGmailService(
        oldest_valid=9000, messages_after={}, watch={"historyId": "9500", "expiration": "1"}
    )
    monkeypatch.setattr(gmail_client, "_service", lambda: service)
    monkeypatch.setattr(pipeline.state, "get_last_history_id", lambda: "100")
    monkeypatch.setattr(pipeline.state, "set_last_history_id", lambda hid: None)

    with caplog.at_level(logging.ERROR, logger="intake.pipeline"):
        pipeline.process_gmail_push("delivery_1", {"historyId": "9100"})

    errors = [r.getMessage() for r in caplog.records if r.levelno >= logging.ERROR]
    joined = "\n".join(errors)
    assert errors, "the re-bootstrap was silent -- nothing was logged at ERROR"
    assert "100" in joined, "the log does not name the cursor that expired"
    assert "9500" in joined, "the log does not name the fresh cursor it reset to"
    assert "NOT recoverable" in joined, (
        "the log does not say that mail which aged out of the window is genuinely lost"
    )


def test_a_transient_gmail_error_does_not_burn_the_watch(monkeypatch):
    """A 500 must NOT be treated as an expired cursor. Re-arming the watch on a
    transient blip would discard a perfectly good cursor and skip every message
    still recoverable from it -- turning a retryable error into permanent data
    loss."""
    monkeypatch.setenv("GOOGLE_CLOUD_PROJECT", "everyfront-test")
    monkeypatch.setattr(pipeline, "dedupe", _StatefulDedupe())
    service = _FakeGmailService(
        oldest_valid=0, messages_after={}, watch={"historyId": "9500", "expiration": "1"}
    )

    def boom(**kwargs):
        raise _FakeHttpError(500)

    monkeypatch.setattr(gmail_client, "_service", lambda: service)
    monkeypatch.setattr(service, "list", boom)
    monkeypatch.setattr(pipeline.state, "get_last_history_id", lambda: "9100")
    monkeypatch.setattr(pipeline.state, "set_last_history_id", lambda hid: None)

    with pytest.raises(_FakeHttpError):
        pipeline.process_gmail_push("delivery_1", {"historyId": "9200"})
    assert service.watch_calls == [], "a transient 500 re-armed the watch and burned the cursor"
