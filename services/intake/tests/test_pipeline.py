"""The intake pipeline, end to end against faked Gmail/GCS/Pub/Sub -- pure
Python, no external services or optional dependencies needed. This is the
test that stands in for the WO1 acceptance criterion ("email a fixture bill
to the demo inbox -> within 60s the attachment is in GCS and
case.document.added fires") without actually needing a live Gmail account.
"""

from __future__ import annotations

from intake import pipeline


def test_process_new_message_stores_attachment_and_publishes_event(monkeypatch):
    fake_message = {
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
    monkeypatch.setattr(pipeline.gmail_client, "fetch_message", lambda mid: fake_message)
    monkeypatch.setattr(
        pipeline.gmail_client, "fetch_attachment_bytes", lambda mid, aid: b"%PDF fake bytes"
    )
    monkeypatch.setattr(pipeline.dedupe, "claim", lambda ns, key: True)

    uploads = []
    monkeypatch.setattr(
        pipeline.storage,
        "upload_attachment",
        lambda mid, filename, content, ct: (
            uploads.append((mid, filename, content, ct)) or f"gs://bucket/intake/{mid}/{filename}"
        ),
    )
    published = []
    monkeypatch.setattr(
        pipeline.pubsub, "publish", lambda topic, data: published.append((topic, data))
    )

    result = pipeline.process_new_message("msg_1")

    assert len(result) == 1
    event = result[0]
    assert event["case_id"] == "case-thread_1"
    assert event["gcs_uri"] == "gs://bucket/intake/msg_1/bill.pdf"
    assert event["filename"] == "bill.pdf"
    assert uploads == [("msg_1", "bill.pdf", b"%PDF fake bytes", "application/pdf")]
    assert published == [("case.document.added", event)]


def test_process_new_message_skips_already_claimed_attachments(monkeypatch):
    fake_message = {
        "threadId": "thread_1",
        "payload": {
            "parts": [
                {
                    "mimeType": "application/pdf",
                    "filename": "bill.pdf",
                    "body": {"attachmentId": "a"},
                }
            ]
        },
    }
    monkeypatch.setattr(pipeline.gmail_client, "fetch_message", lambda mid: fake_message)
    monkeypatch.setattr(pipeline.dedupe, "claim", lambda ns, key: False)  # already processed
    fetch_calls = []
    monkeypatch.setattr(
        pipeline.gmail_client,
        "fetch_attachment_bytes",
        lambda mid, aid: fetch_calls.append(1) or b"",
    )

    result = pipeline.process_new_message("msg_1")

    assert result == []
    assert fetch_calls == []  # never even fetched -- claim failed first


def test_process_new_message_with_no_attachments_publishes_nothing(monkeypatch):
    monkeypatch.setattr(
        pipeline.gmail_client, "fetch_message", lambda mid: {"threadId": "t", "payload": {}}
    )
    published = []
    monkeypatch.setattr(
        pipeline.pubsub, "publish", lambda topic, data: published.append((topic, data))
    )
    assert pipeline.process_new_message("msg_1") == []
    assert published == []


def test_process_gmail_push_is_idempotent_on_the_pubsub_message_id(monkeypatch):
    claims = []

    def already_claimed(ns, key):
        claims.append(key)
        return False  # simulates: this Pub/Sub message_id was already processed

    monkeypatch.setattr(pipeline.dedupe, "claim", already_claimed)

    result = pipeline.process_gmail_push("delivery_1", {"historyId": "500"})
    assert result == {"status": "duplicate", "message_id": "delivery_1"}
    assert claims == ["delivery_1"]


def test_process_gmail_push_lists_and_processes_new_messages(monkeypatch):
    monkeypatch.setattr(pipeline.dedupe, "claim", lambda ns, key: True)
    monkeypatch.setattr(pipeline.state, "get_last_history_id", lambda: "100")
    history_calls = []
    monkeypatch.setattr(
        pipeline.gmail_client,
        "list_new_message_ids",
        lambda start_id: history_calls.append(start_id) or ["msg_a", "msg_b"],
    )
    processed = []
    monkeypatch.setattr(
        pipeline, "process_new_message", lambda mid: processed.append(mid) or [{"doc_id": mid}]
    )
    set_calls = []
    monkeypatch.setattr(pipeline.state, "set_last_history_id", lambda hid: set_calls.append(hid))

    result = pipeline.process_gmail_push("delivery_2", {"historyId": "555"})

    assert history_calls == ["100"]  # started from the STORED cursor, not the incoming historyId
    assert processed == ["msg_a", "msg_b"]
    assert result["documents_published"] == 2
    assert set_calls == ["555"]  # watermark advances to the new historyId


def test_process_gmail_push_bootstraps_cursor_on_first_run(monkeypatch):
    """No stored cursor yet -- falls back to the incoming historyId, which
    correctly yields nothing to backfill (there is no earlier state to diff
    against) rather than raising."""
    monkeypatch.setattr(pipeline.dedupe, "claim", lambda ns, key: True)
    monkeypatch.setattr(pipeline.state, "get_last_history_id", lambda: None)
    history_calls = []
    monkeypatch.setattr(
        pipeline.gmail_client,
        "list_new_message_ids",
        lambda start_id: history_calls.append(start_id) or [],
    )
    monkeypatch.setattr(pipeline.state, "set_last_history_id", lambda hid: None)

    result = pipeline.process_gmail_push("delivery_3", {"historyId": "42"})
    assert history_calls == ["42"]
    assert result["documents_published"] == 0
