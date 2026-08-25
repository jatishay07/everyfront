"""FastAPI routes -- requires `fastapi`/`httpx` for TestClient, which CI's
base install does not include (see the PR HANDOFF). Guarded with
`pytest.importorskip` for the same reason as `packages/delivery`'s PDF
engine tests.
"""

from __future__ import annotations

import base64
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")  # FastAPI's TestClient needs it

import main  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture
def client():
    return TestClient(main.app)


def test_health(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}


def test_pubsub_gmail_processes_and_returns_200(client, monkeypatch):
    monkeypatch.setattr(
        main.pipeline, "process_gmail_push", lambda mid, data: {"status": "ok", "message_id": mid}
    )
    envelope = {
        "message": {
            "data": base64.b64encode(json.dumps({"historyId": "1"}).encode()).decode(),
            "messageId": "m1",
        }
    }
    resp = client.post("/pubsub/gmail", json=envelope)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "message_id": "m1"}


def test_pubsub_gmail_acks_a_malformed_envelope_instead_of_500ing(client):
    resp = client.post("/pubsub/gmail", json={"not": "a push envelope"})
    assert resp.status_code == 200
    assert resp.json()["status"] == "ignored"


def test_webhook_phaxio_publishes_filing_completed_when_resolved(client, monkeypatch):
    monkeypatch.setattr(
        main.vendor_callbacks,
        "handle_vendor_callback",
        lambda channel, payload: {"filing_id": "fil_1", "status": "delivered"},
    )
    published = []
    monkeypatch.setattr(main.pubsub, "publish", lambda topic, data: published.append((topic, data)))

    resp = client.post("/webhooks/phaxio", json={"fax": {"id": "1", "status": "success"}})
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
    assert published == [("filing.completed", {"filing_id": "fil_1", "status": "delivered"})]


def test_webhook_lob_is_a_noop_for_an_unrecognized_vendor_id(client, monkeypatch):
    monkeypatch.setattr(
        main.vendor_callbacks, "handle_vendor_callback", lambda channel, payload: None
    )
    published = []
    monkeypatch.setattr(main.pubsub, "publish", lambda topic, data: published.append((topic, data)))

    resp = client.post(
        "/webhooks/lob", json={"event_type": {"id": "letter.delivered"}, "body": {"id": "x"}}
    )
    assert resp.status_code == 200
    assert resp.json() == {"status": "ignored"}
    assert published == []
