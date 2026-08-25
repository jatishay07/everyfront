"""Deterministic orchestration of the agent hierarchy.

This is where §2.1 ("the LLM narrates, the code computes") extends from
individual agents to the *sequence* they run in: the human-in-the-loop filing
gate (playbook §4 persona 5: "the Strategist may only emit filing.requested
AFTER POST /cases/{id}/approve_filing") is enforced here, in plain Python
control flow, not left to an LLM's discretion inside a single freewheeling
conversation. Every step appends to `cases/{id}/events` -- this is the audit
trail the UI activity feed and the demo's "soul" (persona 5's own words) are
built from.
"""

from __future__ import annotations

import uuid

from . import config, pubsub_client
from .agents import auditor, clock, filer, lookup, reader, strategist, verifier
from .casedata import parse_bill_dates
from .store import store


def _log(case_id: str, agent: str, action: str, detail: str, citations: list[str] | None = None):
    return store.append_event(case_id, agent, action, detail, citations or [])


async def on_document_added(case_id: str, doc_id: str) -> dict:
    """Reader classifies+extracts one document, then -- if the case now has
    enough to work with -- Lookup, Clock, Auditor, and Strategist run and the
    case moves to `strategy_ready`. Publishes `case.analysis.complete` when
    that happens.
    """
    case = store.get_case(case_id)
    if case is None:
        return {"error": f"no such case {case_id}"}
    doc = store.get_document(case_id, doc_id)
    if doc is None:
        return {"error": f"no such document {doc_id} on case {case_id}"}

    reader_turn = await reader.run(case_id, doc_id, doc.get("raw_text", ""), doc.get("type"))
    rf = reader_turn["fact"]
    store.update_document(
        case_id,
        doc_id,
        {"type": rf["label"], "extracted": rf["extraction"]},
    )
    _log(
        case_id,
        "reader",
        "classify_and_extract",
        reader_turn["answer"] or f"classified as {rf['label']}",
    )

    # Merge extracted bill-shaped fields into the case's bill, and note
    # whether we now have an itemized bill for select_fronts' fallback.
    #
    # TWO RULES, both learned the hard way on the first live run:
    #
    # 1. Only BILL-SHAPED documents contribute bill fields. An income proof or
    #    a cat photo has nothing to say about the amount owed, and letting it
    #    speak means the last document processed decides the case.
    # 2. The extractor returns 0 and "" for fields it did not find, NOT None.
    #    Filtering on `is not None` therefore let those sentinels through, and
    #    they overwrote real values: a $2,625 bill with a $1,925 estimate became
    #    amount=0, gfe=0, first_statement_date="". Every downstream number went
    #    to zero -- PPDR eligibility, the deadlines, the savings banner -- while
    #    each individual document's extraction remained perfectly correct. The
    #    per-document facts and the case disagreed, which is the worst shape a
    #    bug can take here: nothing errored, the numbers were just wrong.
    BILL_BEARING_LABELS = {"bill", "itemized_bill", "gfe", "collection_notice"}
    _STR_FIELDS = (
        "provider_name",
        "hospital_ein",
        "hospital_ccn",
        "service_date",
        "first_statement_date",
        "collector_name",
        "validation_notice_date",
    )
    _INT_FIELDS = ("amount_cents", "gfe_amount_cents")
    _BOOL_FIELDS = ("in_collections",)

    extraction = rf["extraction"] or {}
    if (
        isinstance(extraction, dict)
        and "_extraction_error" not in extraction
        and rf["label"] in BILL_BEARING_LABELS
    ):
        bill_fields: dict = {}
        for k in _STR_FIELDS:
            v = extraction.get(k)
            if isinstance(v, str) and v.strip():
                bill_fields[k] = v.strip()
        for k in _INT_FIELDS:
            v = extraction.get(k)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                bill_fields[k] = v
        for k in _BOOL_FIELDS:
            v = extraction.get(k)
            if isinstance(v, bool):
                bill_fields[k] = v
        if rf["label"] == "itemized_bill":
            bill_fields["has_itemized_bill"] = True
        if bill_fields:
            case = store.update_case(case_id, {"bill": {**(case.get("bill") or {}), **bill_fields}})

    case = store.get_case(case_id)

    lookup_turn = await lookup.run(case_id, case)
    lf = lookup_turn["fact"]
    _log(
        case_id, "lookup", "resolve_hospital", lookup_turn["answer"] or lf["note"], lf["citations"]
    )
    if lf.get("resolved"):
        hospital = lf["hospital"]
        case = store.update_case(
            case_id,
            {
                "hospital": hospital,
                # Flattened for CANVAS's CaseSummary (web/lib/types.ts) --
                # the frontend reads these two top-level rather than joining
                # into the nested `hospital` record itself.
                "hospital_name": hospital.get("name", ""),
                "hospital_nonprofit": hospital.get("nonprofit", True),
            },
        )

    clock_turn = await clock.run(case_id, case)
    cf = clock_turn["fact"]
    for d in cf["deadlines"]:
        _log(case_id, "clock", "compute_deadline", d["explain"], [d["citation"]])

    auditor_turn = await auditor.run(case_id, case)
    af = auditor_turn["fact"]
    for finding in af["findings"]:
        _log(
            case_id,
            "auditor",
            f"audit_finding:{finding['kind']}",
            finding["detail"],
            [finding["citation"]] if finding["citation"] else [],
        )
    if af["denial_check"]["ran"]:
        _log(
            case_id,
            "auditor",
            "denial_lawfulness_check",
            af["denial_check"]["detail"],
            [af["denial_check"].get("citation", "")],
        )
    elif af["denial_check"].get("reason"):
        _log(case_id, "auditor", "denial_lawfulness_check_skipped", af["denial_check"]["reason"])

    # select_fronts (via Strategist) needs two things Firestore's JSON-safe
    # case dict doesn't carry as-is: (1) `case["documents"]` to detect an
    # itemized bill (rules.fronts._has_itemized_bill), and (2) `bill`'s date
    # fields as real `date` objects, not ISO strings -- rules.fronts calls
    # compute_deadlines internally and type-checks with isinstance(v, date)
    # exactly like Clock does (see agent_core/casedata.py's docstring for why
    # getting this wrong is a silent-false-negative bug, not a crash: an
    # unparsed date just makes every front look inapplicable).
    case = {
        **case,
        "documents": store.list_documents(case_id),
        "bill": parse_bill_dates(case.get("bill") or {}),
    }

    strategist_turn = await strategist.run(case_id, case)
    sf = strategist_turn["fact"]
    for front in sf["fronts"]:
        store.upsert_front(case_id, front)
        _log(
            case_id,
            "strategist",
            f"select_front:{front['front']}",
            front["reason"],
            [front["citation"]] if front.get("citation") else [],
        )
    savings_cents = af["total_findings_cents"]
    patch = {
        "status": "strategy_ready",
        "savings_found_cents": (case.get("savings_found_cents") or 0) + savings_cents,
        # §3.1's own field for this repo's audit findings; mirrors
        # savings_found_cents today because audit is the only source of
        # quantified savings this pipeline tracks (charity-care and PPDR
        # outcomes are not yet expressed in cents anywhere in the contract).
        "audit_findings_cents": (case.get("audit_findings_cents") or 0) + savings_cents,
    }
    # denial_flag: contract §3.1 amendment says `bool`; CANVAS's
    # web/lib/types.ts (already merged) types it as `{violated, reason,
    # citation} | null` so the Denial Triage chip has something to render.
    # The richer shape is what the feature actually needs and a bool loses
    # information a bool can't get back, so this ships the object and flags
    # the mismatch as a HANDOFF for FORGE to reconcile in the playbook -- see
    # this PR's description. `bool(case["denial_flag"])` still answers the
    # literal §3.1 question for any caller that only wants a boolean.
    denial_check = af["denial_check"]
    if denial_check["ran"] and not denial_check["insufficient_data"]:
        patch["denial_flag"] = {
            "violated": denial_check["violation"],
            "reason": denial_check["detail"],
            "citation": denial_check["citation"],
        }
    store.update_case(case_id, patch)
    pubsub_client.publish(config.TOPIC_CASE_ANALYSIS_COMPLETE, {"case_id": case_id})

    return {
        "reader": reader_turn,
        "lookup": lookup_turn,
        "clock": clock_turn,
        "auditor": auditor_turn,
        "strategist": strategist_turn,
    }


async def approve_and_request_filing(case_id: str, front: str) -> dict:
    """Contract §3.3's `POST /cases/{id}/approve_filing` handler's core logic.

    Runs Verifier; on pass, publishes `filing.requested` (Strategist's
    literal act of emitting it, per the playbook) and hands off to Filer
    immediately in-process as well, since the sole consumer of
    `filing.requested` in this repo is this same service's own Filer -- see
    services/agent-core/main.py's `/pubsub/filing-requested` route for the
    asynchronous (real Pub/Sub push) path this mirrors.
    """
    case = store.get_case(case_id)
    if case is None:
        return {"ok": False, "reason": f"no such case {case_id}"}

    fronts = case.get("fronts") or []
    matched = next((f for f in fronts if f.get("front") == front), None)
    if matched is None:
        return {"ok": False, "reason": f"case has no front {front!r}"}
    if not matched.get("applicable"):
        return {"ok": False, "reason": f"front {front!r} is not applicable to this case"}
    if matched.get("status") not in ("open",):
        return {
            "ok": False,
            "reason": f"front {front!r} is not open (status={matched.get('status')})",
        }

    verifier_turn = await verifier.run(case_id, case, front)
    vf = verifier_turn["fact"]
    _log(
        case_id,
        "verifier",
        "pre_filing_check",
        verifier_turn["answer"] or ("passed" if vf["passed"] else "; ".join(vf["issues"])),
    )
    if not vf["passed"]:
        matched["status"] = "open"
        store.upsert_front(case_id, matched)
        return {"ok": False, "reason": "; ".join(vf["issues"]), "front": matched}

    filing_id = str(uuid.uuid4())
    matched["status"] = "filing"
    store.upsert_front(case_id, matched)
    _log(
        case_id,
        "strategist",
        "filing_requested",
        f"human approval received for {front!r}; requesting filing",
        [],
    )
    pubsub_client.publish(
        config.TOPIC_FILING_REQUESTED, {"case_id": case_id, "front": front, "filing_id": filing_id}
    )

    filer_result = await run_filer(case_id, front, filing_id)
    return {"ok": True, "front": matched, "filer": filer_result}


async def run_filer(case_id: str, front: str, filing_id: str) -> dict:
    case = store.get_case(case_id)
    if case is None:
        return {"error": f"no such case {case_id}"}

    filer_turn = await filer.run(case_id, case, front, filing_id=filing_id)
    ff = filer_turn["fact"]
    _log(
        case_id,
        "filer",
        "file",
        filer_turn["answer"]
        or f"filed {front!r} via {ff['channel']} (vendor_id={ff['vendor_id']}, "
        f"{'SIMULATED' if ff['simulated'] else 'live'})",
    )

    fronts = case.get("fronts") or []
    for f in fronts:
        if f.get("front") == front:
            f["status"] = "filed"
    store.update_case(case_id, {"fronts": fronts})
    pubsub_client.publish(
        config.TOPIC_FILING_COMPLETED, {"filing_id": filing_id, "status": ff["status"]}
    )
    return filer_turn
