"""The two routes into the analysis: `/pubsub/document-added` and
`/internal/process_document(s)`.

This file is about who is allowed to START a cascade, and it exists because
both routes reported success while running the same work several times over.
Measured against the deployed system on 2026-08-26: 137 events for 36 distinct
facts on `ef-2026-0007`, its flagship audit finding in the activity feed 14
times; 67 for 28 on `ef-2026-0001`, the on-camera flagship.

Every test here was run against the pre-fix code first; the exact failure
messages are in this branch's commit bodies.

HANDOFF -> FORGE, on merging `swarm/gmail-case-autocreate-2`: that branch adds
`services/agent-core/tests/test_main.py`, whose
`test_inject_bill_two_routes_should_run_one_cascade` is an xfail(strict=False)
for precisely the defect `test_the_two_inject_bill_routes_run_one_cascade`
below now fixes. It will XPASS (not fail) once both are merged; please delete
that test and its `KNOWN OPEN DEFECT` banner rather than leaving a known-open
marker on a closed defect. This file is deliberately named differently so the
two branches do not collide on one file.
"""

from __future__ import annotations

import asyncio
import base64
import json
from datetime import UTC, datetime, timedelta

import main
import pytest
from _helpers import make_memory_store
from agent_core import pipeline
from fastapi import HTTPException
from fastapi.testclient import TestClient


def envelope(payload: dict, message_id: str = "pubsub-1") -> dict:
    return {
        "message": {
            "messageId": message_id,
            "data": base64.b64encode(json.dumps(payload).encode()).decode(),
        }
    }


@pytest.fixture
def store(monkeypatch):
    s = make_memory_store()
    monkeypatch.setattr(main, "store", s)
    monkeypatch.setattr(pipeline, "store", s)
    s.create_case("c1", {"patient": {}, "bill": {}})
    s.add_document("c1", {"raw_text": "a bill", "type": "bill"}, "d1")
    return s


def _stub_cascade(monkeypatch, sink: list, *, delay: float = 0.0):
    """Stub out everything below the handler: this suite is about routing and
    dedupe, not about re-testing the hierarchy (test_pipeline.py does that)."""

    async def _cascade(case_id, case):
        sink.append(case_id)
        if delay:
            await asyncio.sleep(delay)
        return {}

    async def _reader(case_id, doc_id):
        return {"fact": {"label": "bill", "extraction": {}}}

    monkeypatch.setattr(pipeline, "_run_cascade", _cascade)
    monkeypatch.setattr(pipeline, "_run_reader", _reader)


def test_the_two_inject_bill_routes_run_one_cascade(store, monkeypatch):
    """`/demo/inject_bill` reaches agent-core twice for the same document: it
    publishes `case.document.added` AND calls `/internal/process_documents`
    synchronously. `dce54e6` deduped only the push route, and only ever WROTE
    the key from there, so the two routes never saw each other and a
    3-document case ran 4 cascades -- every audit finding four times over on
    the live activity feed, which §4 persona 6 WO3 calls the demo's money
    shot.
    """
    cascades: list[str] = []
    _stub_cascade(monkeypatch, cascades)
    client = TestClient(main.app, raise_server_exceptions=False)

    client.post("/internal/process_documents", json={"case_id": "c1", "doc_ids": ["d1"]})
    again = client.post("/pubsub/document-added", json=envelope({"case_id": "c1", "doc_id": "d1"}))

    assert len(cascades) == 1, f"the two routes ran {len(cascades)} cascades for one document"
    assert again.json()["status"] == "document already processed"


def test_the_two_routes_run_one_cascade_in_the_other_order_too(store, monkeypatch):
    """The same property when the push wins the race. Marking the key AFTER
    the work would not give this: the batch call takes minutes and push
    delivery is sub-second, so whoever is second would still find the key
    unwritten and re-run everything.
    """
    cascades: list[str] = []
    _stub_cascade(monkeypatch, cascades)
    client = TestClient(main.app, raise_server_exceptions=False)

    client.post("/pubsub/document-added", json=envelope({"case_id": "c1", "doc_id": "d1"}))
    batch = client.post("/internal/process_documents", json={"case_id": "c1", "doc_ids": ["d1"]})

    assert len(cascades) == 1, f"the two routes ran {len(cascades)} cascades for one document"
    assert batch.json()["status"] == "every document already processed or in progress"


def test_a_redelivery_during_the_cascade_does_not_start_a_second_one(store, monkeypatch):
    """THE BIGGEST CONTRIBUTOR to the live numbers, and the one a
    check-then-mark cannot fix.

    `ef-document-added` has a 60-second ack deadline and
    `--max-delivery-attempts=5` (infra/setup.sh). A cascade takes 60-130
    seconds. Pub/Sub therefore redelivers the message WHILE the first attempt
    is still running; `has_processed_message` was False (nothing is marked
    until the end) and the redelivery started another concurrent cascade,
    which was also slow, up to five times over. That is how one audit finding
    reached the feed 14 times.

    The redelivery must NOT be acked: the holder releases its claim if it
    fails, and acking here would ack the only delivery of a document that then
    never got analysed -- this repo's signature defect.
    """
    cascades: list[str] = []
    _stub_cascade(monkeypatch, cascades, delay=0.05)
    body = envelope({"case_id": "c1", "doc_id": "d1"})

    async def _both() -> list:
        async def _attempt():
            try:
                return await main.pubsub_document_added(body)
            except HTTPException as exc:
                return exc

        first, second = await asyncio.gather(_attempt(), _attempt())
        return [first, second]

    results = asyncio.run(_both())

    assert len(cascades) == 1, f"a redelivery mid-cascade started {len(cascades)} cascades"
    rejected = [r for r in results if isinstance(r, HTTPException)]
    assert len(rejected) == 1
    assert rejected[0].status_code == 409  # retried by Pub/Sub, never silently acked


def test_a_failed_cascade_releases_its_claim_so_a_retry_can_run(store, monkeypatch):
    """The other half of claiming before the work. If a claim were kept on
    failure, claiming would recreate the exact defect
    `swarm/gmail-case-autocreate-2` fixed from the other direction: a document
    whose first attempt failed marked forever and never read.
    """
    attempts: list[str] = []

    async def _sometimes(case_id, doc_id):
        attempts.append(doc_id)
        if len(attempts) == 1:
            return {"error": "hospital lookup exploded"}
        return {"reader": {}}

    monkeypatch.setattr(pipeline, "on_document_added", _sometimes)
    client = TestClient(main.app, raise_server_exceptions=False)

    first = client.post(
        "/pubsub/document-added", json=envelope({"case_id": "c1", "doc_id": "d1"}, "pubsub-1")
    )
    second = client.post(
        "/pubsub/document-added", json=envelope({"case_id": "c1", "doc_id": "d1"}, "pubsub-2")
    )

    assert attempts == ["d1", "d1"], "a failed document was marked processed and never retried"
    assert first.status_code == 500  # Pub/Sub must see the failure, not an ack
    assert second.status_code == 200


def test_a_raised_cascade_releases_its_claim_too(store, monkeypatch):
    """Same, for a handler that raises rather than returning `{"error": ...}`
    -- the shape a Firestore outage or a model timeout actually takes."""

    async def _boom(case_id, doc_id):
        raise RuntimeError("firestore unavailable")

    monkeypatch.setattr(pipeline, "on_document_added", _boom)
    client = TestClient(main.app, raise_server_exceptions=False)

    resp = client.post("/pubsub/document-added", json=envelope({"case_id": "c1", "doc_id": "d1"}))

    assert resp.status_code >= 500  # Pub/Sub sees a real failure and retries
    assert not store.has_processed_message("doc:c1:d1")
    assert store.claim_message("doc:c1:d1"), "the claim outlived the attempt that failed"


def test_a_claim_whose_holder_died_expires_instead_of_wedging_the_document(store):
    """A Cloud Run instance killed mid-cascade cannot release its own claim.
    A document nothing may ever analyse again is a worse failure than a rare
    duplicate run, so a claim older than the lease may be taken over."""
    assert store.claim_message("doc:c1:d1")
    assert not store.claim_message("doc:c1:d1")

    stale = datetime.now(UTC) - timedelta(seconds=store.CLAIM_LEASE_SECONDS + 60)
    store._processed["doc:c1:d1"]["claimed_at"] = stale.isoformat()

    assert store.claim_message("doc:c1:d1"), "a dead holder's claim wedged the document forever"
    assert not store.has_processed_message("doc:c1:d1")  # claimed is not processed


def test_a_completed_key_is_never_stolen(store):
    """The lease applies only to claims in flight. A finished document stays
    finished no matter how long ago it finished -- otherwise the whole corpus
    re-analyses itself 15 minutes after the demo."""
    store.mark_message_processed("doc:c1:d1")
    assert store.has_processed_message("doc:c1:d1")
    assert not store.claim_message("doc:c1:d1")


def test_a_legacy_processed_record_still_reads_as_done(store):
    """`_processed_messages` records written before the lease existed carry
    only `processed_at` and no `status`. They were only ever written on
    completion, so the live registry must not need a migration."""
    store._processed["doc:c1:d1"] = {"processed_at": "2026-08-26T08:40:46+00:00"}
    assert store.has_processed_message("doc:c1:d1")
    assert not store.claim_message("doc:c1:d1")
