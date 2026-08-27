"""MIME-tree walking for PDF attachments -- pure dict logic, no Gmail API
client needed (that's only constructed inside `_service()`, never called
here)."""

from __future__ import annotations

import base64

from intake.gmail_client import extract_pdf_attachments, fetch_attachment_bytes


def test_finds_pdf_in_flat_multipart_message():
    message = {
        "payload": {
            "mimeType": "multipart/mixed",
            "parts": [
                {"mimeType": "text/plain", "body": {}, "filename": ""},
                {
                    "mimeType": "application/pdf",
                    "filename": "bill.pdf",
                    "body": {"attachmentId": "att_1", "size": 12345},
                },
            ],
        }
    }
    found = extract_pdf_attachments(message)
    assert found == [
        {
            "filename": "bill.pdf",
            "attachment_id": "att_1",
            "mime_type": "application/pdf",
            # The MIME part's position. `partId` is absent from this fixture,
            # so the walk-position fallback supplies it -- see
            # `extract_pdf_attachments` for why the id is load-bearing.
            "part_id": "1",
        }
    ]


def test_finds_pdf_nested_inside_multipart_alternative():
    message = {
        "payload": {
            "mimeType": "multipart/mixed",
            "parts": [
                {
                    "mimeType": "multipart/alternative",
                    "parts": [
                        {"mimeType": "text/plain", "body": {}, "filename": ""},
                        {"mimeType": "text/html", "body": {}, "filename": ""},
                    ],
                },
                {
                    "mimeType": "application/octet-stream",
                    "filename": "itemized_bill.pdf",  # sniffed by extension, not mimeType
                    "body": {"attachmentId": "att_2"},
                },
            ],
        }
    }
    found = extract_pdf_attachments(message)
    assert found == [
        {
            "filename": "itemized_bill.pdf",
            "attachment_id": "att_2",
            "mime_type": "application/octet-stream",
            "part_id": "1",
        }
    ]


def test_ignores_non_pdf_attachments():
    message = {
        "payload": {
            "parts": [
                {
                    "mimeType": "image/png",
                    "filename": "signature.png",
                    "body": {"attachmentId": "att_3"},
                },
            ]
        }
    }
    assert extract_pdf_attachments(message) == []


def test_single_part_message_with_attachment_directly_on_payload():
    message = {
        "payload": {
            "mimeType": "application/pdf",
            "filename": "bill.pdf",
            "body": {"attachmentId": "att_4"},
        }
    }
    found = extract_pdf_attachments(message)
    assert found == [
        {
            "filename": "bill.pdf",
            "attachment_id": "att_4",
            "mime_type": "application/pdf",
            # A single-part payload's own `partId` is `""`; the fallback
            # numbers it `"0"` so the claim key and object path stay stable.
            "part_id": "0",
        }
    ]


def test_no_attachments_at_all():
    assert extract_pdf_attachments({"payload": {"parts": []}}) == []
    assert extract_pdf_attachments({}) == []


def test_fetch_attachment_bytes_handles_unpadded_base64url(monkeypatch):
    """Gmail's attachment `data` is base64url and often arrives without the
    trailing '=' padding -- a naive decode raises; this must not."""
    raw = b"a fake PDF byte stream that is not a multiple of 3 bytes!!"
    unpadded = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    class _FakeAttachments:
        def get(self, userId, messageId, id):  # noqa: A002, N803 -- Gmail API's own arg names
            return self

        def execute(self):
            return {"data": unpadded}

    class _FakeMessages:
        def attachments(self):
            return _FakeAttachments()

    class _FakeUsers:
        def messages(self):
            return _FakeMessages()

    class _FakeService:
        def users(self):
            return _FakeUsers()

    monkeypatch.setattr("intake.gmail_client._service", lambda: _FakeService())
    assert fetch_attachment_bytes("msg_1", "att_1") == raw
