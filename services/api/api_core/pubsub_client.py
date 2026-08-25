"""Thin Pub/Sub publisher for contract §3.2 events.

Same pattern as `services/agent-core/agent_core/pubsub_client.py` -- falls
back to a logged no-op when Pub/Sub is unavailable (no credentials) so the
demo endpoint never crashes on the publish step.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from . import config

logger = logging.getLogger("api_core.pubsub")

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
    client = _client()
    if client is None or not config.PROJECT_ID:
        logger.info("pubsub unavailable; would publish topic=%s payload=%s", topic, payload)
        return None
    path = _topic_paths.setdefault(topic, client.topic_path(config.PROJECT_ID, topic))
    future = client.publish(path, json.dumps(payload).encode("utf-8"))
    return future.result(timeout=10)
