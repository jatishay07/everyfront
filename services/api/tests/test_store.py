"""api_core.CaseStore, in-memory backend -- contract §3.1 shapes."""

from __future__ import annotations

from _helpers import make_memory_store


def test_backend_is_memory():
    assert make_memory_store().backend == "memory"


def test_create_and_get_case_roundtrip():
    s = make_memory_store()
    s.create_case("c1", {"patient": {"state": "CA"}})
    case = s.get_case("c1")
    assert case["case_id"] == "c1"
    assert case["status"] == "intake"
    assert case["fronts"] == []


def test_get_case_missing_returns_none():
    assert make_memory_store().get_case("nope") is None


def test_list_cases():
    s = make_memory_store()
    s.create_case("c1", {})
    s.create_case("c2", {})
    assert {c["case_id"] for c in s.list_cases()} == {"c1", "c2"}


def test_documents_and_events_roundtrip():
    s = make_memory_store()
    s.create_case("c1", {})
    doc_id = s.add_document("c1", {"type": "bill"})
    assert s.get_document("c1", doc_id)["type"] == "bill"
    assert len(s.list_documents("c1")) == 1

    s.append_event("c1", "reader", "classify", "detail", ["citation"])
    events = s.list_events("c1")
    assert len(events) == 1
    assert events[0]["agent"] == "reader"


def test_hospital_roundtrip():
    s = make_memory_store()
    s.put_hospital("94-0562680", {"name": "Sutter", "nonprofit": True})
    h = s.get_hospital("94-0562680")
    assert h["name"] == "Sutter"
    assert h["ein"] == "94-0562680"


def test_filings_filtered_by_case():
    s = make_memory_store()
    s._filings["f1"] = {"case_id": "c1", "front": "ppdr"}
    s._filings["f2"] = {"case_id": "c2", "front": "audit"}
    assert len(s.list_filings("c1")) == 1
    assert len(s.list_filings()) == 2


def test_list_all_events_is_cross_case_newest_first():
    s = make_memory_store()
    s.create_case("c1", {})
    s.create_case("c2", {})
    s.append_event("c1", "reader", "classify", "first")
    s.append_event("c2", "clock", "compute_deadline", "second")
    s.append_event("c1", "strategist", "select_front", "third")

    events = s.list_all_events()
    assert [e["detail"] for e in events] == ["third", "second", "first"]
    assert {e["case_id"] for e in events} == {"c1", "c2"}


def test_list_all_events_filters_by_agent():
    s = make_memory_store()
    s.create_case("c1", {})
    s.append_event("c1", "reader", "classify", "a")
    s.append_event("c1", "clock", "compute_deadline", "b")
    events = s.list_all_events(agent="clock")
    assert len(events) == 1
    assert events[0]["detail"] == "b"


def test_list_all_events_respects_limit():
    s = make_memory_store()
    s.create_case("c1", {})
    for i in range(5):
        s.append_event("c1", "reader", "classify", str(i))
    assert len(s.list_all_events(limit=2)) == 2
