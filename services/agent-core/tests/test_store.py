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


def test_create_case_if_absent_creates_once_and_never_clobbers():
    """Two Gmail attachments on one email derive the SAME `case-{thread_id}`
    id, and a Pub/Sub redelivery reaches the same id again. `create_case`
    `.set()`s, so a second call would reset the case to `intake` with no
    fronts, discarding everything the first cascade wrote.
    """
    s = make_store()
    case, created = s.create_case_if_absent("case-thread-1", {"patient": {}, "bill": {}})
    assert created is True
    assert case["status"] == "intake"

    s.update_case("case-thread-1", {"status": "strategy_ready"})
    s.upsert_front("case-thread-1", {"front": "audit", "applicable": True, "status": "filed"})

    case, created = s.create_case_if_absent("case-thread-1", {"patient": {}, "bill": {}})
    assert created is False
    assert case["status"] == "strategy_ready"
    assert [f["front"] for f in case["fronts"]] == ["audit"]
    assert len(s.list_cases()) == 1


def test_add_document_if_absent_preserves_what_reader_wrote():
    """The same hazard one level down: a redelivered intake event must not
    wipe the `type`/`extracted` Reader already wrote on that document."""
    s = make_store()
    s.create_case("c1", {})
    doc, created = s.add_document_if_absent("c1", "d1", {"type": "", "raw_text": "a bill"})
    assert created is True
    assert doc["verified"] is None  # contract §3.1 default still applied

    s.update_document("c1", "d1", {"type": "bill", "extracted": {"amount_cents": 262500}})

    doc, created = s.add_document_if_absent("c1", "d1", {"type": "", "raw_text": "a bill"})
    assert created is False
    assert doc["type"] == "bill"
    assert doc["extracted"] == {"amount_cents": 262500}
    assert len(s.list_documents("c1")) == 1


# ---------------------------------------------------------------------------
# The stale-cascade race: an analysis pass must not overwrite a front written
# by a better-informed pass (live, case-1a043f4f4ae26dfa, 2026-08-26).
# ---------------------------------------------------------------------------


def _evidence(*doc_ids: str) -> list[str]:
    """An evidence descriptor holding these documents, shaped like the real one
    (`agent_core.evidence.from_documents`) without needing extractions."""
    return sorted(f"{doc_id}#fingerprint" for doc_id in doc_ids)


def test_a_pass_that_saw_less_cannot_overwrite_one_that_saw_more():
    """THE regression test for the defect measured live on
    case-1a043f4f4ae26dfa.

    An email with three PDFs publishes three `case.document.added` events, and
    Pub/Sub redelivers each of them mid-cascade, so several full analysis
    passes run concurrently on one case. Each writes the whole `fronts[]`
    reason/applicable set from whatever documents existed when IT started, and
    last writer wins -- where "last" is last to FINISH:

        16:03:46  strategist  charity_care: "annual household income was not
                              stated in any document on file..."
        16:04:30  strategist  charity_care: "household size was not stated..."

    and the reason STORED afterwards was the 16:03:46 one, written by a pass
    that had never seen the $32,000 pay stub -- while
    `patient.annual_income_cents` on the same Firestore document read
    3,200,000. The case contradicted itself on screen.
    """
    s = make_store()
    s.create_case("c1", {})

    well_informed = _evidence("d-bill", "d-gfe", "d-paystub")
    s.write_analysis(
        "c1",
        evidence=well_informed,
        fronts=[
            {
                "front": "charity_care",
                "applicable": False,
                "status": "na",
                "reason": "household size was not stated in any document on file",
            }
        ],
    )

    # The pass that only ever saw the bill finishes last and tries to write.
    outcome = s.write_analysis(
        "c1",
        evidence=_evidence("d-bill"),
        fronts=[
            {
                "front": "charity_care",
                "applicable": False,
                "status": "na",
                "reason": "annual household income was not stated in any document on file",
            }
        ],
    )

    assert outcome["written"] is False
    assert outcome["superseded"] is True
    assert outcome["recorded_evidence"] == well_informed
    front = s.get_case("c1")["fronts"][0]
    assert front["reason"] == "household size was not stated in any document on file", (
        "a pass that never saw the pay stub overwrote the conclusion of one that had"
    )


def test_the_superseded_pass_is_handed_the_better_informed_case_back():
    """`write_analysis` returns the case as it STANDS, not the caller's stale
    copy, so a superseded cascade finishes against the truth -- the Auditor's
    denial check still gets a resolved `case["hospital"]`, and nothing
    downstream has to branch on whether its own write landed."""
    s = make_store()
    s.create_case("c1", {})
    s.write_analysis("c1", evidence=_evidence("d1", "d2"), patch={"savings_found_cents": 262_500})

    outcome = s.write_analysis("c1", evidence=_evidence("d1"), patch={"savings_found_cents": 0})
    assert outcome["written"] is False
    assert outcome["case"]["savings_found_cents"] == 262_500


def test_a_better_informed_pass_overwrites_whatever_came_before():
    """The guard is one-directional. A pass that saw MORE always writes, in
    whatever order the passes happened to finish."""
    s = make_store()
    s.create_case("c1", {})
    s.write_analysis(
        "c1",
        evidence=_evidence("d-bill"),
        fronts=[
            {"front": "charity_care", "applicable": False, "status": "na", "reason": "no income"}
        ],
    )
    outcome = s.write_analysis(
        "c1",
        evidence=_evidence("d-bill", "d-paystub"),
        fronts=[
            {"front": "charity_care", "applicable": True, "status": "open", "reason": "117% FPL"}
        ],
    )
    assert outcome["written"] is True
    assert s.get_case("c1")["fronts"][0]["reason"] == "117% FPL"


def test_re_analysis_with_identical_evidence_still_writes():
    """§2.3 idempotency, from the other side: equal evidence is NOT weaker. A
    redelivery that saw the same documents recomputes the same values, so
    letting it write changes nothing -- but refusing it would make the second
    run of an unchanged analysis behave differently from the first."""
    s = make_store()
    s.create_case("c1", {})
    same = _evidence("d1", "d2")
    front = {"front": "audit", "applicable": True, "status": "open", "reason": "itemized bill"}
    assert s.write_analysis("c1", evidence=same, fronts=[front])["written"] is True
    second = s.write_analysis("c1", evidence=same, fronts=[front])
    assert second["written"] is True
    assert second["superseded"] is False
    assert s.get_case("c1")["fronts"] == [front]


def test_the_evidence_guard_never_reopens_a_filed_front():
    """The two protections compose. `write_analysis` applies the
    filing-lifecycle status rule per entry exactly as
    `upsert_front_from_analysis` always did -- a better-informed pass may
    rewrite the reason and still must not reopen a filing."""
    s = make_store()
    s.create_case("c1", {})
    s.upsert_front("c1", {"front": "audit", "applicable": True, "status": "open"})
    s.set_front_status("c1", "audit", "filed")

    s.write_analysis(
        "c1",
        evidence=_evidence("d1", "d2"),
        fronts=[
            {"front": "audit", "applicable": True, "status": "open", "reason": "6 findings"},
            {"front": "ppdr", "applicable": True, "status": "open", "reason": "GFE delta $700"},
        ],
    )
    by_front = {f["front"]: f for f in s.get_case("c1")["fronts"]}
    assert by_front["audit"]["status"] == "filed"
    assert by_front["audit"]["reason"] == "6 findings"
    assert by_front["ppdr"]["status"] == "open"


def test_a_superseded_pass_lands_none_of_its_answer_not_half():
    """One transaction for the whole pass. `fronts[]`, the two money figures
    and `denial_flag` all come from one snapshot, so a superseded pass must not
    leave half its answer beside a better pass's other half -- a contradiction
    inside one case, which is exactly what the live defect produced."""
    s = make_store()
    s.create_case("c1", {})
    s.write_analysis(
        "c1",
        evidence=_evidence("d1", "d2"),
        patch={"savings_found_cents": 262_500, "audit_findings_cents": 21_000},
        fronts=[{"front": "audit", "applicable": True, "status": "open", "reason": "6 findings"}],
    )
    s.write_analysis(
        "c1",
        evidence=_evidence("d1"),
        patch={"savings_found_cents": 0, "audit_findings_cents": 0},
        fronts=[{"front": "audit", "applicable": False, "status": "na", "reason": "no line items"}],
    )
    case = s.get_case("c1")
    assert case["savings_found_cents"] == 262_500
    assert case["audit_findings_cents"] == 21_000
    assert case["fronts"][0]["reason"] == "6 findings"


def test_an_unguarded_analysis_write_behaves_exactly_as_it_did():
    """`upsert_front_from_analysis` without `evidence` is the pre-2026-08-27
    method, unchanged -- the guard is opt-in, so no existing caller changes
    behaviour by accident."""
    s = make_store()
    s.create_case("c1", {})
    s.write_analysis(
        "c1", evidence=_evidence("d1", "d2"), fronts=[{"front": "audit", "status": "open"}]
    )
    s.upsert_front_from_analysis("c1", {"front": "audit", "status": "open", "reason": "unguarded"})
    assert s.get_case("c1")["fronts"][0]["reason"] == "unguarded"


def test_an_analysis_write_never_resurrects_a_purged_case():
    s = make_store()
    s.create_case("c1", {})
    s._cases.pop("c1")
    outcome = s.write_analysis(
        "c1", evidence=_evidence("d1"), patch={"status": "strategy_ready"}, fronts=[]
    )
    assert outcome["case"] is None
    assert outcome["written"] is False
    assert s.get_case("c1") is None
