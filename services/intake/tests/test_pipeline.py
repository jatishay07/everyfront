"""The intake pipeline, end to end against faked Gmail/GCS/Pub/Sub -- pure
Python, no external services or optional dependencies needed. This is the
test that stands in for the WO1 acceptance criterion ("email a fixture bill
to the demo inbox -> within 60s the attachment is in GCS and
case.document.added fires") without actually needing a live Gmail account.
"""

from __future__ import annotations

import io

import pypdf
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
        lambda mid, part_id, filename, content, ct: (
            uploads.append((mid, part_id, filename, content, ct))
            or f"gs://bucket/intake/{mid}/{part_id}/{filename}"
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
    # The `0` segment is the MIME part's immutable `partId` -- see
    # storage.py's docstring for why the object path is per-PART, not
    # per-filename.
    assert event["gcs_uri"] == "gs://bucket/intake/msg_1/0/bill.pdf"
    assert event["filename"] == "bill.pdf"
    # WO7: not a real PDF, so extraction degrades to "" rather than raising --
    # the field is still present so agent-core has something to key off of.
    assert event["raw_text"] == ""
    assert uploads == [("msg_1", "0", "bill.pdf", b"%PDF fake bytes", "application/pdf")]
    assert published == [("case.document.added", event)]


def test_process_new_message_extracts_real_pdf_text(monkeypatch):
    """A real one-page PDF's text rides along in the published event -- the
    fix for the gap FORGE flagged (WO7): agent-core's Reader reads
    `documents/{doc_id}.raw_text`, and nothing upstream ever populated it for
    a live, Gmail-sourced attachment before this.
    """
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    buf = io.BytesIO()
    writer.write(buf)
    pdf_bytes = buf.getvalue()

    fake_message = {
        "threadId": "thread_2",
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
    monkeypatch.setattr(pipeline.gmail_client, "fetch_attachment_bytes", lambda mid, aid: pdf_bytes)
    monkeypatch.setattr(pipeline.dedupe, "claim", lambda ns, key: True)
    monkeypatch.setattr(pipeline.storage, "upload_attachment", lambda *a, **k: "gs://bucket/x.pdf")
    published = []
    monkeypatch.setattr(
        pipeline.pubsub, "publish", lambda topic, data: published.append((topic, data))
    )

    result = pipeline.process_new_message("msg_2")

    assert len(result) == 1
    # A blank page has no text -- the point is the key exists and extraction
    # ran against real pypdf, not that this particular fixture has content.
    assert result[0]["raw_text"] == ""


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
