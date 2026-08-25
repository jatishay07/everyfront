"""Gmail API wrapper -- watch, history, message + attachment fetch.

Authenticates as the demo Gmail account via this service's own
`google_auth.py` (a deliberate standalone copy of
`packages/delivery/delivery/google_auth.py` -- see that module's docstring
for why). `googleapiclient` is imported lazily inside `_service()` for the
same "don't break test collection for environments without it installed"
reason as `packages/delivery`.
"""

from __future__ import annotations

import base64
import os

from . import state
from .google_auth import load_user_credentials

# read-only is sufficient: watching for new mail and reading messages/
# attachments never needs write access (least privilege).
GMAIL_SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]


def _service():
    creds = load_user_credentials(GMAIL_SCOPES)
    from googleapiclient.discovery import build

    return build("gmail", "v1", credentials=creds)


def start_watch(topic_name: str | None = None) -> dict:
    """`users.watch` -- Gmail pushes new-message notifications to our topic
    for 7 days from this call. WO1: "Handle the 7-day watch renewal with a
    Cloud Scheduler job" -- this is the function that job's HTTP hit
    (`POST /gmail/watch/renew` in `main.py`) calls.
    """
    service = _service()
    topic = topic_name or (
        f"projects/{os.environ['GOOGLE_CLOUD_PROJECT']}/topics/"
        f"{os.environ.get('TOPIC_INTAKE_EMAIL_RECEIVED', 'intake.email.received')}"
    )
    result = (
        service.users()
        .watch(userId="me", body={"topicName": topic, "labelIds": ["INBOX"]})
        .execute()
    )
    if result.get("historyId"):
        state.set_last_history_id(str(result["historyId"]))
    return result


def list_new_message_ids(start_history_id: str) -> list[str]:
    """Every message added since `start_history_id`, across paginated results."""
    service = _service()
    message_ids: list[str] = []
    page_token = None
    while True:
        resp = (
            service.users()
            .history()
            .list(
                userId="me",
                startHistoryId=start_history_id,
                historyTypes=["messageAdded"],
                pageToken=page_token,
            )
            .execute()
        )
        for record in resp.get("history", []):
            for added in record.get("messagesAdded", []):
                message_ids.append(added["message"]["id"])
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return message_ids


def fetch_message(message_id: str) -> dict:
    service = _service()
    return service.users().messages().get(userId="me", id=message_id, format="full").execute()


def extract_pdf_attachments(message: dict) -> list[dict]:
    """Walk a message's MIME tree; return PDF parts as
    `[{"filename", "attachment_id", "mime_type"}, ...]`.

    WO1 scopes this to PDF bills specifically ("stores raw attachments (PDF
    bills) to GCS") -- a non-PDF attachment (e.g. an inline signature image)
    is intentionally skipped rather than stored, to keep the intake surface
    matching what the rest of the pipeline expects to classify.
    """
    out: list[dict] = []

    def _is_pdf(filename: str, mime_type: str) -> bool:
        return filename.lower().endswith(".pdf") or mime_type == "application/pdf"

    def walk(parts: list[dict]) -> None:
        for part in parts or []:
            filename = part.get("filename") or ""
            body = part.get("body", {})
            mime_type = part.get("mimeType", "")
            if filename and body.get("attachmentId") and _is_pdf(filename, mime_type):
                out.append(
                    {
                        "filename": filename,
                        "attachment_id": body["attachmentId"],
                        "mime_type": mime_type or "application/pdf",
                    }
                )
            if part.get("parts"):
                walk(part["parts"])

    payload = message.get("payload", {})
    walk(payload.get("parts", []))
    # Single-part messages carry the attachment directly on the payload.
    if (
        not payload.get("parts")
        and payload.get("filename")
        and payload.get("body", {}).get("attachmentId")
    ):
        walk([payload])
    return out


def fetch_attachment_bytes(message_id: str, attachment_id: str) -> bytes:
    service = _service()
    att = (
        service.users()
        .messages()
        .attachments()
        .get(userId="me", messageId=message_id, id=attachment_id)
        .execute()
    )
    # Gmail returns URL-safe base64 without guaranteed padding.
    padded = att["data"] + "=" * (-len(att["data"]) % 4)
    return base64.urlsafe_b64decode(padded)
