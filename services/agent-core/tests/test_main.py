"""`services/agent-core/main.py`'s Pub/Sub push endpoints.

This file exists because the handler in `main.py` was the one piece of
agent-core with no test at all, and it is where the whole Gmail path died: a
bill that arrived by email produced no case, no document and no analysis, and
answered HTTP 200 `{"status":"ok","result_keys":["error"]}` at every step
(HANDOFF's "THE BUG PATTERN" -- every serious defect here reported success
while doing nothing).

Every test below was run against the pre-fix code first. The ones under
REGRESSION failed there (exact messages in this work order's PR description);
the two under GUARD passed, and exist so the `/demo/inject_bill` dedupe --
the thing standing between the demo's activity feed and 4x duplication -- is
not traded away for the fix.
"""

from __future__ import annotations

import base64
import json

import main
import pytest
from _helpers import async_fake_turn, make_memory_store
from agent_core import pipeline
from agent_core.agents import auditor, filer, lookup, reader, strategist, verifier
from agent_core.agents import clock as clock_agent
from fastapi.testclient import TestClient

# A real `case.document.added` payload as services/intake publishes it
# (services/intake/intake/pipeline.py): thread-derived case id, deterministic
# doc id, and the document itself travelling in the event.
GMAIL_EVENT = {
    "case_id": "case-thread-19a4f2",
    "doc_id": "b3f1c0d9e8a7b6c5",
    "gcs_uri": "gs://ef-documents-everyfront-hack-2026/intake/msg-1/bill.pdf",
    "filename": "bill.pdf",
    "raw_text": "SYNTHETIC -- DEMO ONLY. Mercy General Hospital. TOTAL DUE: $2,625.00",
    "gmail_message_id": "msg-1",
    "gmail_thread_id": "thread-19a4f2",
}


def envelope(payload: dict, message_id: str = "pubsub-1") -> dict:
    return {
        "message": {
            "messageId": message_id,
            "data": base64.b64encode(json.dumps(payload).encode()).decode(),
        }
    }


@pytest.fixture
def store(monkeypatch):
    """One in-memory CaseStore behind every module that holds the singleton."""
    s = make_memory_store()
    for module in (main, pipeline, lookup, auditor, verifier, filer):
        monkeypatch.setattr(module, "store", s)
    monkeypatch.setattr(pipeline.pubsub_client, "publish", lambda topic, payload: None)
    return s


@pytest.fixture
def client():
    return TestClient(main.app, raise_server_exceptions=False)


def patch_agents(monkeypatch, *, hospital=None):
    """Every agent's `run()` canned -- this file is about the HANDLER, not
    about re-testing the hierarchy (test_pipeline.py covers that) and never
    about reaching a real model."""
    monkeypatch.setattr(
        reader,
        "run",
        lambda case_id, doc_id, text, hint=None: async_fake_turn(
            {
                "case_id": case_id,
                "doc_id": doc_id,
                # The point of the assertion in
                # test_gmail_document_is_stored_with_the_text_the_event_carried:
                # Gemma classified it, because nothing pre-labelled it.
                "label": "bill" if not hint else f"hinted:{hint}",
                "gemma_raw": "bill",
                "gemma_error": None,
                "extraction": {"amount_cents": 262500, "first_statement_date": "2026-01-01"},
                "citations": [],
                "seen_text": text,
            },
            "classified as a bill",
        ),
    )
    monkeypatch.setattr(
        lookup,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "resolved": hospital is not None,
                "hospital": hospital,
                "citations": [],
                "note": "resolved" if hospital else "not found",
            },
            "hospital note",
        ),
    )
    monkeypatch.setattr(
        clock_agent,
        "run",
        lambda case_id, case: async_fake_turn(
            {"case_id": case_id, "deadlines": []}, "no deadlines"
        ),
    )
    monkeypatch.setattr(
        auditor,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "case_id": case_id,
                "findings": [],
                "total_findings_cents": 0,
                "denial_check": {"ran": False, "reason": "no denial letter"},
                "source": {},
            },
            "no findings",
        ),
    )
    monkeypatch.setattr(
        strategist,
        "run",
        lambda case_id, case: async_fake_turn(
            {
                "case_id": case_id,
                "fronts": [
                    {
                        "front": "audit",
                        "applicable": True,
                        "reason": "itemized bill present",
                        "citation": "42 USC 1395b-7(b)",
                        "deadline": None,
                        "status": "open",
                    }
                ],
                "source": "test",
            },
            "one front",
        ),
    )


# ---------------------------------------------------------------- REGRESSION


def test_gmail_sourced_event_creates_the_case_and_produces_analysis(store, client, monkeypatch):
    """THE defect: an emailed bill produced nothing and reported success.

    Pre-fix this failed at the first assertion -- no case existed, and the
    endpoint answered 200 `{"status":"ok","result_keys":["error"]}`.
    """
    patch_agents(monkeypatch, hospital={"name": "Mercy General", "nonprofit": True})

    response = client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT))

    assert response.status_code == 200
    assert "error" not in response.json().get("result_keys", [])

    case = store.get_case(GMAIL_EVENT["case_id"])
    assert case is not None, "the emailed bill produced no case at all"
    assert [f["front"] for f in case["fronts"]] == ["audit"]
    assert case["status"] == "strategy_ready"
    actions = [e["action"] for e in store.list_events(GMAIL_EVENT["case_id"])]
    assert "case_opened_from_intake" in actions
    assert "classify_and_extract" in actions


def test_gmail_document_is_stored_with_the_text_the_event_carried(store, client, monkeypatch):
    """Reader reads `documents/{doc_id}.raw_text` (pipeline._run_reader). The
    event is the only place that text exists -- intake extracts it and has no
    Firestore grant to write it itself.

    Pre-fix: no document existed, so Reader never ran on anything.
    """
    patch_agents(monkeypatch)

    client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT))

    doc = store.get_document(GMAIL_EVENT["case_id"], GMAIL_EVENT["doc_id"])
    assert doc is not None, "the event's document was never created"
    assert doc["raw_text"] == GMAIL_EVENT["raw_text"]
    assert doc["gcs_uri"] == GMAIL_EVENT["gcs_uri"]
    assert doc["filename"] == GMAIL_EVENT["filename"]
    # Gemma classified it: the stored type was falsy, so reader.run got no
    # hint. Writing a guessed `type` here would silently override the
    # bonus-point classifier (agents/reader.py: `label = doc_type_hint or
    # classification["label"]`).
    assert doc["type"] == "bill"


def test_created_case_matches_casesummary_and_invents_nothing(store, client, monkeypatch):
    """web/lib/types.ts `CaseSummary` -- a missing non-nullable field is a
    silent `undefined` in the dashboard, not a loud error. And HANDOFF defect
    #5: absent facts must stay absent, never a plausible placeholder.

    Reader is patched to extract NOTHING here, standing in for a bill that
    could not be read: the case must still be well-shaped and still empty.
    """
    patch_agents(monkeypatch)
    monkeypatch.setattr(
        reader,
        "run",
        lambda case_id, doc_id, text, hint=None: async_fake_turn(
            {
                "case_id": case_id,
                "doc_id": doc_id,
                "label": "bill",
                "gemma_raw": "bill",
                "gemma_error": None,
                "extraction": {},
                "citations": [],
            },
            "could not read this document",
        ),
    )

    client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT))
    case = store.get_case(GMAIL_EVENT["case_id"])

    for field in (
        "case_id",
        "patient",
        "bill",
        "status",
        "fronts",
        "savings_found_cents",
        "audit_findings_cents",
        "hospital_name",
        "hospital_nonprofit",
        "denial_flag",
        "created_at",
        "updated_at",
    ):
        assert field in case, f"CaseSummary field {field!r} missing -> undefined in the UI"

    # Nothing invented: no name, no EIN, no dates, no amount.
    assert case["patient"] == {}
    assert case["bill"] == {}
    assert case["hospital_name"] == ""


def test_a_failed_document_is_not_marked_processed(store, client, monkeypatch):
    """The poisoned-doc_key bug: processing failed, the handler acked anyway
    and marked the document done, so Pub/Sub never redelivered it and that
    document could never be processed again.

    Pre-fix: 200 + both keys marked.
    """
    monkeypatch.setattr(
        pipeline,
        "on_document_added",
        lambda case_id, doc_id: _async({"error": "Gemini unavailable"}),
    )
    store.create_case("case-thread-19a4f2", {"patient": {}, "bill": {}})
    store.add_document("case-thread-19a4f2", {"raw_text": "x"}, GMAIL_EVENT["doc_id"])

    response = client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT))

    assert response.status_code >= 500, "a failure must be a non-2xx so Pub/Sub retries"
    doc_key = f"doc:{GMAIL_EVENT['case_id']}:{GMAIL_EVENT['doc_id']}"
    assert not store.has_processed_message(doc_key), "failed document marked processed -- poisoned"
    assert not store.has_processed_message("pubsub-1")


def test_redelivery_after_a_failure_reprocesses(store, client, monkeypatch):
    """The other half of the same fix: the retry Pub/Sub sends must actually
    do the work, not hit the dedupe guard the failed attempt left behind."""
    calls = []

    def flaky(case_id, doc_id):
        calls.append(doc_id)
        if len(calls) == 1:
            return _async({"error": "Gemini unavailable"})
        return _async({"reader": {}, "strategist": {}})

    monkeypatch.setattr(pipeline, "on_document_added", flaky)

    first = client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT, "pubsub-1"))
    second = client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT, "pubsub-1"))

    assert first.status_code >= 500
    assert second.status_code == 200
    assert len(calls) == 2, "the redelivery was swallowed by the failed attempt's dedupe key"


def test_second_attachment_reuses_the_case(store, client, monkeypatch):
    """Two PDFs on one email are two events sharing one thread-derived case id
    (services/intake `_case_id_for_thread`). The second must add a document to
    the existing case, not a second case and not a reset of the first."""
    patch_agents(monkeypatch, hospital={"name": "Mercy General", "nonprofit": True})
    second_event = {**GMAIL_EVENT, "doc_id": "0000aaaa1111bbbb", "filename": "itemized.pdf"}

    client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT, "pubsub-1"))
    fronts_after_first = store.get_case(GMAIL_EVENT["case_id"])["fronts"]
    client.post("/pubsub/document-added", json=envelope(second_event, "pubsub-2"))

    assert len(store.list_cases()) == 1
    assert len(store.list_documents(GMAIL_EVENT["case_id"])) == 2
    assert fronts_after_first, "guard: the first cascade did write fronts"
    assert store.get_case(GMAIL_EVENT["case_id"])["fronts"], (
        "the second attachment reset the case it should have joined"
    )


# --------------------------------------------------------------------- GUARD
# The two dedupe tests immediately below are the ones that passed pre-fix.
# Everything after them is regression coverage again.


def test_same_document_by_two_routes_runs_once(store, client, monkeypatch):
    """GUARD (passed pre-fix). `/demo/inject_bill` publishes the same document
    AND calls agent-core synchronously, with different message ids; deduping on
    `doc:{case_id}:{doc_id}` is what stopped the activity feed showing every
    audit finding 4x on the demo's own screen. Do not trade this away."""
    calls = []
    monkeypatch.setattr(
        pipeline,
        "on_document_added",
        lambda case_id, doc_id: _async(calls.append(doc_id) or {"reader": {}}),
    )
    store.create_case(GMAIL_EVENT["case_id"], {"patient": {}, "bill": {}})
    store.add_document(GMAIL_EVENT["case_id"], {"raw_text": "x"}, GMAIL_EVENT["doc_id"])

    client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT, "pubsub-1"))
    again = client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT, "pubsub-2"))

    assert len(calls) == 1
    assert again.json()["status"] == "document already processed"


def test_duplicate_message_id_short_circuits(store, client, monkeypatch):
    """GUARD (passed pre-fix): §2.3 redelivery of the identical message."""
    calls = []
    monkeypatch.setattr(
        pipeline,
        "on_document_added",
        lambda case_id, doc_id: _async(calls.append(doc_id) or {"reader": {}}),
    )

    client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT, "pubsub-1"))
    again = client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT, "pubsub-1"))

    assert len(calls) == 1
    assert again.json()["status"] == "duplicate, skipped"


def test_id_only_event_for_a_missing_case_creates_nothing(store, client, monkeypatch):
    """REGRESSION + the reason auto-creation is gated on the event carrying a
    document. Pre-fix this failed with `assert 500 == 200`: the handler ran
    the whole pipeline against a case that no longer exists, then marked the
    document processed.

    `/demo/inject_bill` publishes `{case_id, doc_id}` with no document in it,
    and `fixtures/demo_reset.py` deletes each case while those messages are
    still in flight. Creating a case from an id-only event would resurrect
    every purged case as a zombie row in `GET /cases` -- the exact defect
    `store.update_case`'s "never resurrect a purged case" guard was added for.
    """
    monkeypatch.setattr(
        pipeline,
        "on_document_added",
        lambda case_id, doc_id: pytest.fail("must not run the pipeline on a deleted case"),
    )

    response = client.post(
        "/pubsub/document-added",
        json=envelope({"case_id": "ef-2026-0003-old", "doc_id": "d1"}),
    )

    assert response.status_code == 200  # acked: no retry can reconstruct it
    assert store.list_cases() == []


def test_redelivery_never_clobbers_an_already_read_document(store, client, monkeypatch):
    """A retry re-runs `ensure_case_and_document_from_event`. If that used
    plain `.set()` it would wipe the `type`/`extracted` Reader had written and
    reset the case to `intake` with no fronts."""
    patch_agents(monkeypatch, hospital={"name": "Mercy General", "nonprofit": True})

    client.post("/pubsub/document-added", json=envelope(GMAIL_EVENT, "pubsub-1"))
    pipeline.ensure_case_and_document_from_event(GMAIL_EVENT)

    case = store.get_case(GMAIL_EVENT["case_id"])
    doc = store.get_document(GMAIL_EVENT["case_id"], GMAIL_EVENT["doc_id"])
    assert case["status"] == "strategy_ready"
    assert case["fronts"]
    assert doc["type"] == "bill"
    assert doc["extracted"]


async def _async(value):
    """Wrap a plain value as an awaitable, for monkeypatching the async
    `pipeline.on_document_added` with a canned result."""
    return value
