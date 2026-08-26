"""Deterministic orchestration of the agent hierarchy.

This is where §2.1 ("the LLM narrates, the code computes") extends from
individual agents to the *sequence* they run in: the human-in-the-loop filing
gate (playbook §4 persona 5: "the Strategist may only emit filing.requested
AFTER POST /cases/{id}/approve_filing") is enforced here, in plain Python
control flow, not left to an LLM's discretion inside a single freewheeling
conversation. Every step appends to `cases/{id}/events` -- this is the audit
trail the UI activity feed and the demo's "soul" (persona 5's own words) are
built from.

Defect #3 (speed, persona 5 WO2): Lookup, Clock, and Auditor used to run
strictly sequentially, and -- worse -- the ENTIRE Lookup->Clock->Auditor->
Strategist cascade re-ran after every single document a case had (three
documents meant three full cascades, most of the work thrown away each time).
`_run_cascade` below runs exactly once per call and parallelizes what has no
ordering dependency: Clock needs only `bill`/`patient` and Auditor needs the
resolved `hospital` only for its denial-lawfulness sub-check, so both run
concurrently once Lookup has resolved the hospital (Lookup must go first --
Auditor's denial check reads `case["hospital"]`). `process_case_documents`
gives a caller who already knows every document up front (the demo's
`/demo/inject_bill`) a way to run Reader for all of them CONCURRENTLY and then
call `_run_cascade` exactly once, instead of once per document.
`on_document_added` keeps the original one-document-at-a-time contract for
the real, genuinely asynchronous Gmail-intake path, where "how many documents
this case will ever have" is not knowable in advance.
"""

from __future__ import annotations

import asyncio
import uuid

from . import config, pubsub_client, rules_bridge
from .agents import auditor, clock, filer, lookup, reader, strategist, verifier
from .casedata import parse_bill_dates
from .store import store

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


def _log(case_id: str, agent: str, action: str, detail: str, citations: list[str] | None = None):
    return store.append_event(case_id, agent, action, detail, citations or [])


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _merge_bill_fields(case_id: str, case: dict, reader_fact: dict) -> dict:
    """Fold one document's extraction into the case's `bill`. Returns the
    (possibly unchanged) case -- callers that process several documents
    should re-fetch or thread this return value rather than the stale `case`
    they started with.

    TWO RULES, both learned the hard way on the first live run:

    1. Only BILL-SHAPED documents contribute bill fields. An income proof or
       a cat photo has nothing to say about the amount owed, and letting it
       speak means the last document processed decides the case.
    2. The extractor returns 0 and "" for fields it did not find, NOT None.
       Filtering on `is not None` therefore let those sentinels through, and
       they overwrote real values: a $2,625 bill with a $1,925 estimate became
       amount=0, gfe=0, first_statement_date="". Every downstream number went
       to zero -- PPDR eligibility, the deadlines, the savings banner -- while
       each individual document's extraction remained perfectly correct. The
       per-document facts and the case disagreed, which is the worst shape a
       bug can take here: nothing errored, the numbers were just wrong.
    """
    extraction = reader_fact["extraction"] or {}
    if not (
        isinstance(extraction, dict)
        and "_extraction_error" not in extraction
        and reader_fact["label"] in BILL_BEARING_LABELS
    ):
        return case

    bill_fields: dict = {}
    for k in _STR_FIELDS:
        v = extraction.get(k)
        if isinstance(v, str) and v.strip():
            bill_fields[k] = v.strip()
    for k in _INT_FIELDS:
        v = extraction.get(k)
        if _is_plain_int(v) and v > 0:
            bill_fields[k] = v
    for k in _BOOL_FIELDS:
        v = extraction.get(k)
        if isinstance(v, bool):
            bill_fields[k] = v
    if reader_fact["label"] == "itemized_bill":
        bill_fields["has_itemized_bill"] = True
    if not bill_fields:
        return case
    return store.update_case(case_id, {"bill": {**(case.get("bill") or {}), **bill_fields}})


async def _run_reader(case_id: str, doc_id: str) -> dict:
    """Reader for one already-added document: run, persist, log. Returns the
    reader turn (same shape `agents.reader.run` returns)."""
    doc = store.get_document(case_id, doc_id)
    if doc is None:
        return {"error": f"no such document {doc_id} on case {case_id}"}
    reader_turn = await reader.run(case_id, doc_id, doc.get("raw_text", ""), doc.get("type"))
    rf = reader_turn["fact"]
    store.update_document(case_id, doc_id, {"type": rf["label"], "extracted": rf["extraction"]})
    _log(
        case_id,
        "reader",
        "classify_and_extract",
        reader_turn["answer"] or f"classified as {rf['label']}",
    )
    # Defect fix (persona 5 WO8, "never invent, always say so"): reader.py's
    # `_scrub_ungrounded` discards any extracted value that matched a known
    # fabrication pattern (a fake EIN, an epoch date, an "Unknown" name --
    # exactly what ef-2026-0006's deliberately-corrupted bill.pdf used to
    # produce). When that happens the case's own audit trail must say so
    # plainly -- "we could not read this" is an honest outcome; silence is not.
    if rf.get("scrubbed_fields"):
        _log(
            case_id,
            "reader",
            "extraction_scrubbed",
            f"discarded {len(rf['scrubbed_fields'])} implausible placeholder value(s) instead of "
            f"reporting them as facts: {', '.join(rf['scrubbed_fields'])}. This document could not "
            "be fully read; those fields are treated as unknown, not zero/epoch/placeholder.",
        )
    return reader_turn


def _charity_care_erasure_cents(case: dict) -> tuple[int, str]:
    """The amount a GRANTED charity-care determination would erase, or 0 with
    an honest reason it could not be computed.

    Deliberately conservative (defect #1's "never invent or inflate" rule):
    only the "free" determination has an unambiguous dollar figure -- 100% of
    the billed amount, per 26 CFR 1.501(r)-4(b)(2) free care. "Discounted"
    tiers have no single percentage this system is told (a hospital's FAP can
    set its own sliding scale STATUTE's `screen_eligibility` does not model),
    so claiming a number there would be a guess, not a fact -- exactly the
    kind of arithmetic a judge could catch as invented. This calls STATUTE's
    own `screen_eligibility` directly (never recomputes eligibility) per §2.1.
    """
    patient = case.get("patient") or {}
    bill = case.get("bill") or {}
    hospital = case.get("hospital") or {}
    if not hospital:
        return 0, "no hospital resolved -- cannot screen eligibility"
    if hospital.get("nonprofit", True) is False:
        return 0, "hospital is for-profit -- no charity-care determination applies"

    income = patient.get("annual_income_cents")
    if income is None:
        income = patient.get("annual_income")
    household = patient.get("household_size")
    state = str(patient.get("state") or "").strip()
    amount_cents = bill.get("amount_cents")
    if not _is_plain_int(income) or not _is_plain_int(household) or not state:
        return 0, "insufficient patient data to screen eligibility"
    if not _is_plain_int(amount_cents) or amount_cents <= 0:
        return 0, "no billed amount on file to erase"

    elig = rules_bridge.screen_eligibility(income, household, state, hospital)
    if elig.determination != "free":
        return 0, (
            f"eligibility determination is {elig.determination!r}, not 'free' -- {elig.explain()}"
        )
    return amount_cents, elig.explain()


async def _run_cascade(case_id: str, case: dict) -> dict:
    """Lookup -> {Clock, Auditor} (parallel) -> Strategist, exactly once, then
    the case-level patch (status, savings, denial_flag). Returns the four
    agent turns, same shape `on_document_added` always has.
    """
    lookup_turn = await lookup.run(case_id, case)
    lf = lookup_turn["fact"]
    _log(
        case_id, "lookup", "resolve_hospital", lookup_turn["answer"] or lf["note"], lf["citations"]
    )
    if lf.get("resolved"):
        hospital = lf["hospital"]
        patch: dict = {
            "hospital": hospital,
            # Flattened for CANVAS's CaseSummary (web/lib/types.ts) -- the
            # frontend reads these two top-level rather than joining into the
            # nested `hospital` record itself.
            "hospital_name": hospital.get("name", ""),
            "hospital_nonprofit": hospital.get("nonprofit", True),
        }
        # Defect #2: a hospital resolved by provider-name match (bill had no
        # EIN) should still leave the case's own bill carrying that EIN, so
        # the `/hospitals/{ein}` API, the dashboard's per-hospital stat, and
        # any later re-lookup all see one consistent fact -- not just Lookup's
        # own in-memory answer for this one pipeline run.
        resolved_ein = lf.get("ein")
        bill_now = case.get("bill") or {}
        if resolved_ein and not (bill_now.get("hospital_ein") or "").strip():
            patch["bill"] = {**bill_now, "hospital_ein": resolved_ein}
        # `or case`: if this case was purged mid-run (a demo reset, a manual
        # delete), update_case now writes nothing and returns None rather than
        # resurrecting it. Finish the cascade against the local copy -- every
        # later write is a no-op too, so nothing is left behind.
        case = store.update_case(case_id, patch) or case

    # Clock needs only bill/patient; Auditor needs `case["hospital"]` (just
    # set above) solely for its denial-lawfulness sub-check. Neither depends
    # on the other's output, so they run concurrently -- defect #3.
    clock_turn, auditor_turn = await asyncio.gather(
        clock.run(case_id, case),
        auditor.run(case_id, case),
    )
    cf = clock_turn["fact"]
    for d in cf["deadlines"]:
        _log(case_id, "clock", "compute_deadline", d["explain"], [d["citation"]])

    af = auditor_turn["fact"]
    for finding in af["findings"]:
        _log(
            case_id,
            "auditor",
            f"audit_finding:{finding['kind']}",
            finding["detail"],
            [finding["citation"]] if finding["citation"] else [],
        )
    # DEFECT (persona 5 WO7, "ef-2026-0006 reports $0 savings"): with zero
    # findings, the loop above logs nothing at all -- a genuinely clean bill
    # (every line item audited, nothing wrong) and an unparseable bill (no
    # line items extracted at all, nothing COULD be audited) both produced
    # this exact same silence, and both reach the dashboard as an identical
    # "$0.00 audit findings" with no way for a judge -- or PROOF's own bug
    # bash -- to tell which one happened. Make the distinction an explicit
    # event either way, using `auditor.line_items_examined` (see that
    # module's `_facts`).
    if not af["findings"]:
        examined = af.get("line_items_examined", 0)
        if examined:
            _log(
                case_id,
                "auditor",
                "audit_finding:none",
                f"{examined} line item(s) examined -- no duplicate, NCCI, or cash-price "
                "findings. $0.00 audit findings reflects a clean bill.",
            )
        else:
            _log(
                case_id,
                "auditor",
                "audit_skipped",
                "no line items were extracted from any document on file -- $0.00 audit "
                "findings reflects missing/unparseable data, not a clean bill.",
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
        # NOT `upsert_front`: re-analysis must not reopen a front the filing
        # lifecycle already owns -- see that method's docstring for the live
        # ef-2026-0007 trace.
        store.upsert_front_from_analysis(case_id, front)
        _log(
            case_id,
            "strategist",
            f"select_front:{front['front']}",
            front["reason"],
            [front["citation"]] if front.get("citation") else [],
        )

    # Defect #1: savings_found_cents is built from real, auditable components,
    # never invented. Audit findings (duplicates, NCCI PTP/MUE when a table is
    # wired, cash-price delta when MRF data exists -- all summed by STATUTE's
    # `audit_line_items`) are overcharges on specific line items: a subset of
    # the total bill. A GRANTED "free" charity-care determination erases the
    # WHOLE bill: a superset of whatever the audit found on that same bill.
    # Summing them would double-count the audit's dollars (already inside the
    # erased total) and could overstate savings past the bill itself -- the
    # exact "a judge doing arithmetic must not catch a discrepancy" trap. MAX,
    # not SUM, is the honest combination of two overlapping paths to the same
    # money.
    audit_cents = af["total_findings_cents"]
    charity_erasure_cents, charity_explain = _charity_care_erasure_cents(case)
    combined_cents = max(audit_cents, charity_erasure_cents)
    # Same distinction as the audit_finding:none / audit_skipped events above,
    # folded into the one line the dashboard's $0 case actually gets read
    # from: "$0.00, 0 items examined" (nothing to audit) must not read the
    # same as "$0.00" on its own (which a clean, fully-audited bill would also
    # show).
    examined = af.get("line_items_examined", 0)
    audit_note = "" if examined else " (no line items were extracted -- nothing to audit)"
    _log(
        case_id,
        "auditor",
        "savings_summary",
        (
            f"Audit findings (duplicates/PTP/MUE/cash-price): ${audit_cents / 100:,.2f}"
            f"{audit_note}. "
            f"Charity-care free-tier erasure: ${charity_erasure_cents / 100:,.2f} "
            f"({charity_explain}). Reported savings for this pass: "
            f"${combined_cents / 100:,.2f} (max of the two -- charity-care erasure, when it "
            "applies, already subsumes any billing-error dollars on the same bill)."
        ),
        [],
    )

    # DEFECT (persona 5 WO6, idempotency): this used to be
    # `(case.get(...) or 0) + combined_cents` -- an ACCUMULATION, not a
    # recomputation. §2.3 requires every handler tolerate Pub/Sub redelivery,
    # and `_run_cascade` is exactly the kind of handler that can run twice for
    # the same case (a redelivered `case.document.added`, or simply a second
    # document arriving on `on_document_added`'s one-cascade-per-document
    # path): each run above already recomputes `combined_cents`/`audit_cents`
    # from the CURRENT, COMPLETE state of every document and the case's own
    # patient/bill/hospital fields (`auditor.all_line_items` scans every
    # document on file, `_charity_care_erasure_cents` reads the case fresh) --
    # so it is already the right total, not a delta. Adding it to whatever was stored
    # before double-counts on redelivery and inflates further with every extra
    # document. A straight assignment is the idempotent, honest number.
    case_patch = {
        "status": "strategy_ready",
        "savings_found_cents": combined_cents,
        # §3.1's own field for this repo's audit findings specifically (not
        # the combined savings figure above).
        "audit_findings_cents": audit_cents,
    }
    # denial_flag: contract §3.1 amendment says `bool`; CANVAS's already-merged
    # web/lib/types.ts types it as `{violated, reason, citation} | null` so
    # the Denial Triage chip has something to render (see web/README.md's own
    # HANDOFF item #4) -- changing the shape here would silently break that
    # UI. Defect #4's actual bug is narrower than the shape mismatch: this
    # used to leave `denial_flag` at its `None` default whenever the check
    # ran but `insufficient_data` was True (no FAP doc list on file for the
    # hospital) -- which, outside PROOF's two hand-seeded denial fixtures, is
    # EVERY real hospital in this system today (no `hospitals/{ein}` record
    # anywhere carries `fap_required_documents`; that is not part of §3.1's
    # schema). So in practice `denial_flag` stayed None for any real denial
    # case, not just for cases with no denial letter at all. Now: whenever a
    # denial-lawfulness check actually ran (a denial letter was on file and
    # something was demanded), the flag is ALWAYS a definite object --
    # `violated=False` when the data is insufficient to prove a violation,
    # never a bare None. Only "no denial letter at all" (nothing to assess)
    # still leaves it at the store's own `None` default -- a legitimate,
    # CANVAS-compatible "not applicable" state, not a bug.
    denial_check = af["denial_check"]
    if denial_check["ran"]:
        case_patch["denial_flag"] = {
            "violated": bool(denial_check["violation"]) and not denial_check["insufficient_data"],
            "reason": denial_check["detail"],
            "citation": denial_check["citation"],
        }
    store.update_case(case_id, case_patch)
    pubsub_client.publish(config.TOPIC_CASE_ANALYSIS_COMPLETE, {"case_id": case_id})

    return {
        "lookup": lookup_turn,
        "clock": clock_turn,
        "auditor": auditor_turn,
        "strategist": strategist_turn,
    }


async def on_document_added(case_id: str, doc_id: str) -> dict:
    """Reader classifies+extracts one document, then Lookup, Clock, Auditor,
    and Strategist re-run and the case moves to `strategy_ready`. Publishes
    `case.analysis.complete`.

    This is the real, genuinely-asynchronous path: a new document can arrive
    at any time (Gmail intake) with no way to know if it is the last one, so
    every document re-triggers the full cascade. For a caller that already
    knows every document up front, see `process_case_documents` below --
    it does the same work without paying for N cascades.
    """
    case = store.get_case(case_id)
    if case is None:
        return {"error": f"no such case {case_id}"}
    if store.get_document(case_id, doc_id) is None:
        return {"error": f"no such document {doc_id} on case {case_id}"}

    reader_turn = await _run_reader(case_id, doc_id)
    case = _merge_bill_fields(case_id, case, reader_turn["fact"])
    case = store.get_case(case_id)

    result = await _run_cascade(case_id, case)
    return {"reader": reader_turn, **result}


async def process_case_documents(case_id: str, doc_ids: list[str]) -> dict:
    """Batch entry point for a caller that already has every document for a
    case (the demo's `/demo/inject_bill`, which uploads a whole fixture in
    one call). Runs Reader for every document CONCURRENTLY (playbook §4
    persona 5 WO2: "parallelise independent agent calls -- Reader per
    document") and then runs the Lookup->Clock/Auditor->Strategist cascade
    exactly ONCE, instead of once per document like `on_document_added` (each
    of those documents has no ordering dependency on the others -- a GFE and
    an income-proof upload do not need to wait on each other to be read).
    """
    case = store.get_case(case_id)
    if case is None:
        return {"error": f"no such case {case_id}"}

    reader_turns = await asyncio.gather(*(_run_reader(case_id, doc_id) for doc_id in doc_ids))

    case = store.get_case(case_id)
    for reader_turn in reader_turns:
        if "fact" not in reader_turn:
            continue  # a missing-document error for this one doc_id; skip it, don't crash the batch
        case = _merge_bill_fields(case_id, case, reader_turn["fact"])
    case = store.get_case(case_id)

    result = await _run_cascade(case_id, case)
    return {"readers": dict(zip(doc_ids, reader_turns, strict=True)), **result}


def _has_completed_filing(case_id: str, front: str) -> bool:
    """True if `filings/` already has a real, sent filing for this case+front.

    Used only to decide whether a front stuck at status `"filing"` is safe to
    retry (see the guard in `approve_and_request_filing`) -- a genuinely
    completed filing must never be silently re-filed just because the
    `fronts[]` status patch that should have followed it (`run_filer`'s
    `status: "filed"` write) did not land for some unrelated reason.
    """
    return any(f.get("front") == front for f in store.list_filings(case_id))


async def approve_and_request_filing(case_id: str, front: str) -> dict:
    """Contract §3.3's `POST /cases/{id}/approve_filing` handler's core logic.

    Runs Verifier synchronously (fast: one LLM narration turn over an
    already-computed fact), then, on pass, publishes `filing.requested` and
    returns immediately -- Filer runs asynchronously off `filing.requested`,
    via `services/agent-core/main.py`'s `/pubsub/filing-requested` push
    subscriber calling `finalize_filing` below.

    CHANGED 2026-08-25 (SWARM WO7, "approval times out clients"): this used to
    ALSO call `run_filer` synchronously, in-process, right here -- meaning
    every `approve_filing` request paid for Verifier's LLM turn, Filer's LLM
    turn, real PDF rendering, a GCS upload, and a vendor round-trip, all
    inside one HTTP request. Measured live: over 6 minutes, well past
    services/api's own `AGENT_CORE_TIMEOUT_S` and Cloud Run's request
    timeout -- the client saw a timeout even on runs where Filer eventually
    succeeded server-side. That synchronous call existed only because, until
    today, ATLAS's five Pub/Sub subscriptions were provisioned as PULL with no
    subscriber (`infra/deploy.sh` never converted them to push) -- so
    `filing.requested` went into a queue nobody read, and the in-process call
    was the only thing that ever actually filed anything. `infra/deploy.sh`
    now wires `ef-filing-requested` to this same service's
    `/pubsub/filing-requested` push endpoint, so the event path is real:
    letting it do the work, instead of duplicating that work here, is what
    makes this endpoint fast again.
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
    status = matched.get("status")
    # BUG (SWARM WO7, "ef-2026-0001's charity_care front is stuck at status
    # filing"): before today's deploy.sh fix, `filing.requested` was
    # published into an unread PULL queue -- Filer never ran, the front never
    # reached "filed" NOR reverted to "open" (there was no exception to
    # revert on; the request had simply already returned or timed out), and
    # every later approval attempt hit this exact guard and was rejected as
    # "not open". A front that requested a filing and never got one must stay
    # retryable. `status == "filing"` is now accepted here too, UNLESS a real
    # filing already exists for this front (`_has_completed_filing`) -- in
    # which case this really is a completed filing whose `fronts[]` status
    # patch just did not land, and re-filing would double-file.
    if status not in ("open", "filing"):
        return {"ok": False, "reason": f"front {front!r} is not open (status={status})"}
    if status == "filing" and _has_completed_filing(case_id, front):
        return {
            "ok": False,
            "reason": f"front {front!r} already has a completed filing on record",
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
        store.set_front_status(case_id, front, "open")
        return {"ok": False, "reason": "; ".join(vf["issues"]), "front": matched}

    filing_id = str(uuid.uuid4())
    matched["status"] = "filing"
    store.set_front_status(case_id, front, "filing")
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
    return {"ok": True, "front": matched, "filing_id": filing_id, "status": "filing_requested"}


async def finalize_filing(case_id: str, front: str, filing_id: str) -> dict:
    """Run Filer for a `filing.requested` message and settle the front's
    status either way. The asynchronous counterpart of what
    `approve_and_request_filing` used to do in-process (see that function's
    docstring) -- called from `services/agent-core/main.py`'s
    `/pubsub/filing-requested` push subscriber.

    On failure, reverts the front to "open" (persona 5 WO6 task 1's fix,
    carried over unchanged) so the case stays retryable rather than wedged at
    "filing" forever, then re-raises so Pub/Sub's own redelivery/backoff sees
    a real failure instead of a silently-acked message.
    """
    try:
        return await run_filer(case_id, front, filing_id)
    except Exception as exc:  # noqa: BLE001 -- revert state, log, then re-raise honestly
        store.set_front_status(case_id, front, "open")
        _log(
            case_id,
            "filer",
            "file_failed",
            f"filing {front!r} failed before completion ({type(exc).__name__}: {exc}); "
            "front reverted to open for retry",
        )
        raise


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
        or (
            f"filed {front!r} via {ff['channel']} (vendor_id={ff['vendor_id']}, "
            f"{'SIMULATED' if ff['simulated'] else 'live'}); real-world destination would be "
            f"{ff.get('real_destination')!r}; generated PDF saved as document "
            f"{ff.get('doc_id')} ({ff.get('gcs_uri') or 'no GCS bucket configured'})"
        ),
    )

    # NOT `update_case(case_id, {"fronts": ...})` from the `case` read at the
    # top of this function: by the time Filer returns, that snapshot is however
    # many seconds stale, and writing the whole array back is exactly how a
    # sibling front's concurrently-written "filed" status got clobbered (PROOF,
    # ef-2026-0001/-0003/-0007). One front, one field, one transaction.
    store.set_front_status(case_id, front, "filed")
    pubsub_client.publish(
        config.TOPIC_FILING_COMPLETED, {"filing_id": filing_id, "status": ff["status"]}
    )
    return filer_turn
