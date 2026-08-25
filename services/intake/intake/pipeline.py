"""The intake pipeline: one Gmail push notification -> zero or more
`case.document.added` events.

Kept separate from `main.py` so the FastAPI route handler stays a thin
adapter and this logic is directly unit-testable against faked
`gmail_client`/`storage`/`pubsub` modules (see `tests/test_pipeline.py`).
"""

from __future__ import annotations

import hashlib
import os

from . import dedupe, gmail_client, pubsub, state, storage, text_extract

TOPIC_CASE_DOCUMENT_ADDED = os.environ.get("TOPIC_CASE_DOCUMENT_ADDED", "case.document.added")


def _case_id_for_thread(thread_id: str) -> str:
    """A Gmail thread becomes one case: replies/forwards about the same bill
    land in the same case rather than spawning a new one per message.

    HANDOFF (see PR description, WO7): this service has no Firestore grant
    (see `dedupe.py`), so it cannot look up or create `cases/{case_id}` /
    `documents/{doc_id}` itself. As of WO7 the `case.document.added` event
    (below) carries everything needed to do so -- `gcs_uri`, `filename`, and
    now `raw_text` -- so agent-core's document-added handler auto-creating
    both records on first sight is a pure "store what you were already
    given" op, not a new GCS round-trip or PDF parser. See the PR
    description's HANDOFF section for the exact patch.
    """
    return f"case-{thread_id}"


def process_new_message(message_id: str) -> list[dict]:
    """Fetch one Gmail message, store its PDF attachments to GCS, and
    publish `case.document.added` per attachment.

    Idempotent per `(message_id, filename)` via `dedupe.claim` -- safe to
    call twice for the same message (e.g. if it shows up in two overlapping
    `history.list` pages).
    """
    message = gmail_client.fetch_message(message_id)
    thread_id = message.get("threadId", message_id)
    case_id = _case_id_for_thread(thread_id)

    published: list[dict] = []
    for att in gmail_client.extract_pdf_attachments(message):
        claim_key = f"{message_id}:{att['filename']}"
        if not dedupe.claim("gmail_attachment", claim_key):
            continue

        content = gmail_client.fetch_attachment_bytes(message_id, att["attachment_id"])
        gcs_uri = storage.upload_attachment(message_id, att["filename"], content, att["mime_type"])
        doc_id = hashlib.sha1(claim_key.encode()).hexdigest()[:16]  # noqa: S324 -- id derivation only
        # `raw_text` travels IN the event, extracted here rather than left for
        # a downstream consumer to re-fetch from GCS -- see text_extract.py's
        # docstring for why this closes the real gap (agent-core's Reader
        # reads `documents/{doc_id}.raw_text`, and nothing upstream of this
        # commit ever wrote it for a live, Gmail-sourced PDF).
        event = {
            "case_id": case_id,
            "doc_id": doc_id,
            "gcs_uri": gcs_uri,
            "filename": att["filename"],
            "raw_text": text_extract.extract_pdf_text(content),
            "gmail_message_id": message_id,
            "gmail_thread_id": thread_id,
        }
        pubsub.publish(TOPIC_CASE_DOCUMENT_ADDED, event)
        published.append(event)
    return published


def process_gmail_push(message_id: str, data: dict) -> dict:
    """Full pipeline for one Pub/Sub delivery of a Gmail watch notification.

    Idempotent on `message_id` -- the Pub/Sub delivery id, stable across
    redeliveries of the exact same push (agreement §2.3).
    """
    if not dedupe.claim("gmail_push", message_id):
        return {"status": "duplicate", "message_id": message_id}

    new_history_id = data.get("historyId")
    start_id = state.get_last_history_id() or new_history_id

    published: list[dict] = []
    if start_id:
        for msg_id in gmail_client.list_new_message_ids(str(start_id)):
            published.extend(process_new_message(msg_id))

    if new_history_id:
        state.set_last_history_id(str(new_history_id))

    return {
        "status": "ok",
        "message_id": message_id,
        "documents_published": len(published),
        "documents": published,
    }
