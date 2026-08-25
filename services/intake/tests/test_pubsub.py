"""Pub/Sub push envelope decoding -- pure stdlib, always runs."""

from __future__ import annotations

import base64
import json

import pytest
from intake.pubsub import BadPushEnvelope, decode_push_envelope


def _envelope(data: dict, message_id: str = "12345") -> dict:
    encoded = base64.b64encode(json.dumps(data).encode()).decode()
    return {
        "message": {
            "data": encoded,
            "messageId": message_id,
            "publishTime": "2026-08-25T00:00:00Z",
        },
        "subscription": "projects/p/subscriptions/s",
    }


def test_decodes_gmail_watch_notification():
    body = _envelope({"emailAddress": "demo@example.test", "historyId": "998877"})
    message_id, data = decode_push_envelope(body)
    assert message_id == "12345"
    assert data == {"emailAddress": "demo@example.test", "historyId": "998877"}


def test_missing_message_object_is_rejected():
    with pytest.raises(BadPushEnvelope):
        decode_push_envelope({"subscription": "x"})


def test_missing_message_id_is_rejected():
    with pytest.raises(BadPushEnvelope):
        decode_push_envelope({"message": {"data": base64.b64encode(b"{}").decode()}})


def test_empty_data_decodes_to_empty_dict():
    body = _envelope({})
    _, data = decode_push_envelope(body)
    assert data == {}


def test_garbage_data_is_rejected_cleanly():
    body = {"message": {"messageId": "1", "data": "not-valid-base64-json!!!"}}
    with pytest.raises(BadPushEnvelope):
        decode_push_envelope(body)
