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


def test_front_status_write_ignores_a_stale_caller_snapshot():
    """THE regression test for the lost-update race (PROOF, PR #37).

    Filing went asynchronous at ca9fd40, so two fronts on one case settle in
    two concurrent `/pubsub/filing-requested` handlers. Each handler used to
    write the WHOLE `fronts[]` array back from the snapshot it read before
    running the Filer -- so the handler that finished second reverted its
    sibling's already-"filed" status, while a real `filings/` record sat
    underneath proving the filing had been sent. Reproduced live 3-for-3 on
    ef-2026-0001, -0003 and -0007.

    No threads needed: the defect is the stale snapshot, not the timing.
    """
    s = make_store()
    s.create_case("c1", {})
    s.upsert_front("c1", {"front": "ppdr", "applicable": True, "status": "open"})
    s.upsert_front("c1", {"front": "charity_care", "applicable": True, "status": "open"})

    # Filer A reads the case, then spends seconds rendering and sending a PDF.
    stale = s.get_case("c1")
    assert {f["front"]: f["status"] for f in stale["fronts"]}["charity_care"] == "open"

    # Filer B finishes first and settles its own front.
    s.set_front_status("c1", "charity_care", "filed")

    # Filer A now settles ITS front, still holding `stale`.
    s.set_front_status("c1", "ppdr", "filed")

    assert {f["front"]: f["status"] for f in s.get_case("c1")["fronts"]} == {
        "ppdr": "filed",
        "charity_care": "filed",
    }


def test_set_front_status_leaves_the_rest_of_the_entry_alone():
    """Only `status` moves -- `applicable`, `reason`, `deadline` and the
    citation a judge may freeze-frame all survive a filing."""
    s = make_store()
    s.create_case("c1", {})
    s.upsert_front(
        "c1",
        {
            "front": "charity_care",
            "applicable": True,
            "reason": "income 180% FPL, under the hospital's 200% free-care threshold",
            "deadline": "2026-11-30",
            "status": "open",
        },
    )
    s.set_front_status("c1", "charity_care", "filed")
    front = s.get_case("c1")["fronts"][0]
    assert front["status"] == "filed"
    assert front["deadline"] == "2026-11-30"
    assert front["applicable"] is True
    assert front["reason"].startswith("income 180% FPL")


def test_set_front_status_never_invents_a_front():
    s = make_store()
    s.create_case("c1", {})
    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "open"})
    s.set_front_status("c1", "ppdr", "filed")
    assert [f["front"] for f in s.get_case("c1")["fronts"]] == ["audit"]


def test_concurrent_front_writes_all_survive():
    """The same invariant under real threads: every front approved close
    together must end up "filed", none clobbered by a sibling's write."""
    import threading

    names = ["charity_care", "ppdr", "debt_validation", "audit"]
    s = make_store()
    s.create_case("c1", {})
    for name in names:
        s.upsert_front("c1", {"front": name, "applicable": True, "status": "open"})

    barrier = threading.Barrier(len(names))

    def settle(name: str) -> None:
        barrier.wait()
        s.set_front_status("c1", name, "filed")

    threads = [threading.Thread(target=settle, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert {f["front"]: f["status"] for f in s.get_case("c1")["fronts"]} == dict.fromkeys(
        names, "filed"
    )


def test_analysis_does_not_reopen_a_filed_front():
    """The second, independent cause of PROOF's symptom (live, ef-2026-0007).

    The Filer stores each generated PDF as a case document, which publishes
    `case.document.added`, which re-runs the whole hierarchy. `select_fronts`
    is pure -- it has no idea anything has been filed -- so it hands back
    every applicable front at status "open", and a plain `upsert_front` wrote
    that straight over a sibling's "filed".
    """
    s = make_store()
    s.create_case("c1", {})
    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "open"})
    s.set_front_status("c1", "audit", "filed")

    # A re-analysis lands: same front, freshly computed, status "open".
    s.upsert_front_from_analysis(
        "c1",
        {
            "front": "audit",
            "applicable": True,
            "status": "open",
            "reason": "itemized bill on file; a billing audit is always performed",
        },
    )

    front = s.get_case("c1")["fronts"][0]
    assert front["status"] == "filed"
    # ...but everything analysis DOES own still updates.
    assert front["reason"].startswith("itemized bill on file")


def test_analysis_still_moves_a_front_that_is_not_yet_filed():
    """The preserve rule is narrow: only the filing lifecycle's own statuses
    are protected. An "open" or "na" front is analysis's to move."""
    s = make_store()
    s.create_case("c1", {})
    s.upsert_front("c1", {"front": "ppdr", "applicable": False, "status": "na"})
    s.upsert_front_from_analysis(
        "c1", {"front": "ppdr", "applicable": True, "status": "open", "reason": "GFE delta $900"}
    )
    assert s.get_case("c1")["fronts"][0]["status"] == "open"

    s.upsert_front_from_analysis(
        "c1", {"front": "ppdr", "applicable": False, "status": "na", "reason": "past 120 days"}
    )
    assert s.get_case("c1")["fronts"][0]["status"] == "na"


def test_analysis_preserves_every_filing_owned_status():
    s = make_store()
    for i, owned in enumerate(("filing", "filed", "won", "lost")):
        s.create_case(f"c{i}", {})
        s.upsert_front(f"c{i}", {"front": "audit", "applicable": True, "status": owned})
        s.upsert_front_from_analysis(
            f"c{i}", {"front": "audit", "applicable": True, "status": "open"}
        )
        assert s.get_case(f"c{i}")["fronts"][0]["status"] == owned


def test_analysis_inserts_a_front_it_has_never_seen():
    s = make_store()
    s.create_case("c1", {})
    s.upsert_front_from_analysis(
        "c1", {"front": "charity_care", "applicable": True, "status": "open"}
    )
    assert [f["front"] for f in s.get_case("c1")["fronts"]] == ["charity_care"]


def test_writes_never_resurrect_a_purged_case():
    """`.set(merge=True)` creates a document that does not exist, so a write
    arriving after a delete used to bring the case back as a half-empty
    zombie -- observed live on 2026-08-26 as a stray
    `demo-case_08_lawful_denial_ca-65c0a5df` recreated 13s after
    `fixtures/demo_reset.py` renamed and deleted it. A stray case shows up in
    `GET /cases`, and therefore on camera."""
    s = make_store()
    s.create_case("c1", {})
    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "open"})

    s._cases.pop("c1")  # the purge (demo-reset, or a rename's delete)

    assert s.update_case("c1", {"status": "strategy_ready"}) is None
    assert s.set_front_status("c1", "audit", "filed") is None
    assert s.upsert_front("c1", {"front": "ppdr", "applicable": True, "status": "open"}) is None
    assert s.upsert_front_from_analysis("c1", {"front": "audit", "applicable": True}) is None
    assert s.get_case("c1") is None
