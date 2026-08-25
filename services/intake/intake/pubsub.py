"""Pub/Sub push envelope decoding + publish -- contract §3.2.

Both directions live here: decoding what Cloud Pub/Sub's push subscription
POSTs to us (Gmail's `intake.email.received` notification), and publishing
what we produce (`case.document.added`, and `filing.completed` on the vendor
webhook routes).
"""

from __future__ import annotations

import base64
import json
import os
from typing import Any


class BadPushEnvelope(Exception):
    """The POST body did not look like a Pub/Sub push envelope at all."""


def decode_push_envelope(body: dict) -> tuple[str, dict[str, Any]]:
    """Pub/Sub push body -> (message_id, decoded JSON data).

    Push format: `{"message": {"data": "<base64>", "messageId": "...", ...},
    "subscription": "..."}`. `message_id` is Pub/Sub's own id -- the natural
    dedup key for `dedupe.claim()`, since it is stable across redeliveries of
    the exact same message (agreement §2.3).
    """
    message = body.get("message")
    if not isinstance(message, dict):
        raise BadPushEnvelope(f"no 'message' object in push body: {body!r}")
    message_id = message.get("messageId") or message.get("message_id")
    if not message_id:
        raise BadPushEnvelope(f"push message has no messageId: {message!r}")
    raw = message.get("data", "")
    try:
        decoded = base64.b64decode(raw).decode("utf-8") if raw else "{}"
        data = json.loads(decoded) if decoded else {}
    except Exception as exc:  # noqa: BLE001 -- surfaced as a clear 4xx, not a 500 traceback
        raise BadPushEnvelope(f"could not decode push data: {exc}") from exc
    return str(message_id), data


def publish(topic: str, data: dict) -> str | None:
    """Publish `data` as JSON to `topic`. Returns the Pub/Sub message id, or
    None if no project is configured (local dev without emulator/creds) --
    logged by the caller rather than raised, since a downstream publish
    failure must not turn an already-stored attachment into a 500."""
    project_id = os.environ.get("GOOGLE_CLOUD_PROJECT", "")
    if not project_id:
        return None
    from google.cloud import pubsub_v1

    publisher = pubsub_v1.PublisherClient()
    topic_path = publisher.topic_path(project_id, topic)
    future = publisher.publish(topic_path, json.dumps(data).encode("utf-8"))
    return future.result(timeout=30)
