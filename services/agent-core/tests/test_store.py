"""CaseStore, in-memory backend. Contract §3.1 shapes + §2.3 idempotency."""

from __future__ import annotations

from _helpers import make_memory_store as make_store


def test_backend_is_memory():
    s = make_store()
    assert s.backend == "memory"


def test_create_and_get_case_roundtrip():
    s = make_store()
    s.create_case("c1", {"patient": {"state": "CA"}})
    case = s.get_case("c1")
    assert case["case_id"] == "c1"
    assert case["status"] == "intake"
    assert case["fronts"] == []
    assert case["patient"]["state"] == "CA"


def test_get_case_missing_returns_none():
    s = make_store()
    assert s.get_case("nope") is None


def test_update_case_merges():
    s = make_store()
    s.create_case("c1", {})
    s.update_case("c1", {"status": "analyzing"})
    case = s.get_case("c1")
    assert case["status"] == "analyzing"
    assert "created_at" in case


def test_upsert_front_inserts_then_replaces():
    s = make_store()
    s.create_case("c1", {})
    s.upsert_front("c1", {"front": "ppdr", "applicable": True, "status": "open"})
    case = s.get_case("c1")
    assert len(case["fronts"]) == 1

    s.upsert_front("c1", {"front": "ppdr", "applicable": True, "status": "filed"})
    case = s.get_case("c1")
    assert len(case["fronts"]) == 1
    assert case["fronts"][0]["status"] == "filed"

    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "open"})
    case = s.get_case("c1")
    assert len(case["fronts"]) == 2


def test_documents_roundtrip():
    s = make_store()
    s.create_case("c1", {})
    doc_id = s.add_document("c1", {"type": "bill", "gcs_uri": "gs://x"})
    doc = s.get_document("c1", doc_id)
    assert doc["type"] == "bill"
    assert doc["verified"] is None  # default per §3.1

    s.update_document("c1", doc_id, {"verified": True})
    assert s.get_document("c1", doc_id)["verified"] is True
    assert len(s.list_documents("c1")) == 1


def test_event_dedupe_on_redelivery():
    """§2.3: idempotent Pub/Sub handlers -- an event_id appended twice must
    not create two audit-log entries."""
    s = make_store()
    s.create_case("c1", {})
    s.append_event("c1", "reader", "classify", "first", event_id="evt-1")
    s.append_event("c1", "reader", "classify", "REDELIVERED", event_id="evt-1")
    events = s.list_events("c1")
    assert len(events) == 1
    assert events[0]["detail"] == "first"


def test_event_shape_matches_contract():
    s = make_store()
    s.create_case("c1", {})
    s.append_event("c1", "clock", "compute_deadline", "due in 240 days", ["26 CFR 1.501(r)-4"])
    event = s.list_events("c1")[0]
    assert {"ts", "case_id", "agent", "action", "detail", "citations", "event_id"} <= set(event)


def test_hospital_roundtrip():
    s = make_store()
    s.put_hospital("36-2169147", {"name": "Advocate", "nonprofit": True})
    h = s.get_hospital("36-2169147")
    assert h["name"] == "Advocate"
    assert h["ein"] == "36-2169147"
    assert s.get_hospital("00-0000000") is None


def test_filings_roundtrip_and_filter_by_case():
    s = make_store()
    fid = s.create_filing({"case_id": "c1", "front": "ppdr", "channel": "fax", "status": "sent"})
    assert s.list_filings("c1")[0]["filing_id"] == fid
    assert s.list_filings("c2") == []


def test_message_idempotency_guard():
    s = make_store()
    assert s.has_processed_message("m1") is False
    s.mark_message_processed("m1")
    assert s.has_processed_message("m1") is True
