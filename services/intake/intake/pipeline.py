"""The intake pipeline: one Gmail push notification -> zero or more
`case.document.added` events.

Kept separate from `main.py` so the FastAPI route handler stays a thin
adapter and this logic is directly unit-testable against faked
`gmail_client`/`storage`/`pubsub` modules (see `tests/test_pipeline.py`).
"""

from __future__ import annotations

import hashlib
import logging
import os

from . import dedupe, gmail_client, pubsub, state, storage, text_extract

logger = logging.getLogger("intake.pipeline")

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

    The claim is PROVISIONAL: it is written before the work, so a failure
    anywhere between it and the publish releases it again (`dedupe.release`)
    and re-raises, and the redelivery genuinely retries instead of being
    waved through as a duplicate.

    NOT COVERED by that pairing: a release only happens if this process lives
    long enough to run its own `except` block. If the instance dies between
    the claim and the release -- Cloud Run eviction, OOM kill, request
    timeout, SIGKILL -- the marker survives, the redelivery reads it as a
    duplicate, and that attachment is dropped silently. Closing that needs a
    lease with a TTL or a Firestore two-phase commit; see `dedupe.release`.
    """
    message = gmail_client.fetch_message(message_id)
    thread_id = message.get("threadId", message_id)
    case_id = _case_id_for_thread(thread_id)

    published: list[dict] = []
    for att in gmail_client.extract_pdf_attachments(message):
        claim_key = f"{message_id}:{att['filename']}"
        if not dedupe.claim("gmail_attachment", claim_key):
            continue

        # Everything from here to the publish is the claimed work. If any of
        # it raises, the claim has to come back off or this attachment is
        # dropped forever on redelivery -- see `dedupe.release`'s docstring.
        try:
            content = gmail_client.fetch_attachment_bytes(message_id, att["attachment_id"])
            gcs_uri = storage.upload_attachment(
                message_id, att["filename"], content, att["mime_type"]
            )
            doc_id = hashlib.sha1(claim_key.encode()).hexdigest()[:16]  # noqa: S324 -- id derivation
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
        except Exception:
            logger.exception(
                "failed handling attachment %s of Gmail message %s -- releasing its dedupe "
                "claim so Pub/Sub's redelivery can retry it instead of skipping it as a "
                "duplicate",
                att["filename"],
                message_id,
            )
            dedupe.release("gmail_attachment", claim_key)
            raise
        published.append(event)
    return published


def process_gmail_push(message_id: str, data: dict) -> dict:
    """Full pipeline for one Pub/Sub delivery of a Gmail watch notification.

    Idempotent on `message_id` -- the Pub/Sub delivery id, stable across
    redeliveries of the exact same push (agreement §2.3).

    Same provisional-claim contract, and the same uncovered failure mode, as
    `process_new_message` above: a crash between claim and release still
    turns a redelivery into a silent no-op.
    """
    if not dedupe.claim("gmail_push", message_id):
        return {"status": "duplicate", "message_id": message_id}

    try:
        return _process_gmail_push(message_id, data)
    except Exception:
        # The claim is provisional until the work succeeds. Without this, a
        # transient Gmail/GCS/Pub-Sub error would 500, Pub/Sub would redeliver
        # the same messageId, `claim` would return False, and the handler
        # would 200-ack a notification it never processed.
        logger.exception(
            "failed processing Gmail push %s -- releasing its dedupe claim so the "
            "redelivery actually retries",
            message_id,
        )
        dedupe.release("gmail_push", message_id)
        raise


def _process_gmail_push(message_id: str, data: dict) -> dict:
    """The body of `process_gmail_push`, minus the claim/release bookkeeping."""
    new_history_id = data.get("historyId")
    start_id = state.get_last_history_id() or new_history_id

    published: list[dict] = []
    if start_id:
        try:
            message_ids = gmail_client.list_new_message_ids(str(start_id))
        except gmail_client.HistoryExpired:
            # Gmail has discarded the history records we needed. The cursor is
            # unusable and no retry will fix it -- retrying is exactly how this
            # goes dark: every push 404s, 500s, gets swallowed as a duplicate on
            # redelivery, and the mailbox silently stops flowing while every
            # response says 200 (HANDOFF.md's bug pattern, verbatim).
            #
            # Re-arm the watch, which returns a FRESH historyId and rewrites the
            # cursor, so the next notification diffs against something valid.
            # Deliberately loud: a silent self-heal is nearly as bad as a silent
            # failure, because the messages that aged out of the window are NOT
            # recoverable from history and are genuinely skipped here.
            logger.error(
                "Gmail history cursor %s has expired (404) -- the history window has "
                "rolled past it. Re-bootstrapping via users.watch. ANY message that "
                "arrived while the cursor was stale is NOT recoverable from the history "
                "API and is being skipped; intake resumes from the new cursor. If this "
                "recurs, intake is going dark between pushes -- check that "
                "GCS_DOCUMENTS_BUCKET is set so state.py can actually persist the cursor.",
                start_id,
            )
            watch = gmail_client.start_watch()
            fresh_id = watch.get("historyId")
            logger.error(
                "Gmail watch re-armed; cursor reset to %s (expires %s)",
                fresh_id,
                watch.get("expiration"),
            )
            # "Process from there": the historyId on THIS notification is
            # minutes old, so unlike the stored cursor it is still inside the
            # history window. Try it once, so the message that triggered this
            # very push is not the one message the recovery throws away.
            # Anything older than it aged out with the cursor and is gone.
            recovered: list[dict] = []
            if new_history_id and str(new_history_id) != str(start_id):
                try:
                    for msg_id in gmail_client.list_new_message_ids(str(new_history_id)):
                        recovered.extend(process_new_message(msg_id))
                except gmail_client.HistoryExpired:
                    logger.error(
                        "the notification's own historyId %s is expired too -- nothing is "
                        "recoverable from this push; intake resumes at the new cursor",
                        new_history_id,
                    )
            # `start_watch` already persisted `fresh_id`, which is >= the
            # notification's historyId. Return before the tail of this function
            # walks the cursor BACKWARDS onto the stale notification value.
            return {
                "status": "history_expired_rebootstrapped",
                "message_id": message_id,
                "documents_published": len(recovered),
                "documents": recovered,
                "new_history_id": str(fresh_id) if fresh_id else None,
            }
        for msg_id in message_ids:
            published.extend(process_new_message(msg_id))

    if new_history_id:
        state.set_last_history_id(str(new_history_id))

    return {
        "status": "ok",
        "message_id": message_id,
        "documents_published": len(published),
        "documents": published,
    }
