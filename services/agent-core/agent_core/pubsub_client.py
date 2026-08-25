"""Thin Pub/Sub publisher for contract §3.2 events.

Falls back to a logged no-op when `google-cloud-pubsub` cannot construct a
client (no credentials -- unit tests, local dev without ADC) so the pipeline
never crashes on the publish step; the pipeline's own Firestore writes and
events/ log are still the source of truth regardless of whether the message
actually reached Pub/Sub.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import config

logger = logging.getLogger("agent_core.pubsub")

_publisher = None
_topic_paths: dict[str, str] = {}


def _client():
    global _publisher
    if _publisher is None:
        try:
            from google.cloud import pubsub_v1

            _publisher = pubsub_v1.PublisherClient()
        except Exception:  # noqa: BLE001 -- no creds / no package -> stay None
            _publisher = False
    return _publisher or None


def publish(topic: str, payload: dict[str, Any]) -> str | None:
    """Publish `payload` (JSON-encoded) to `topic`. Returns the message id, or
    None if Pub/Sub is unavailable in this environment (logged, not raised).
    """
    client = _client()
    if client is None or not config.PROJECT_ID:
        logger.info("pubsub unavailable; would publish topic=%s payload=%s", topic, payload)
        return None
    path = _topic_paths.setdefault(topic, client.topic_path(config.PROJECT_ID, topic))
    future = client.publish(path, json.dumps(payload).encode("utf-8"))
    return future.result(timeout=10)
