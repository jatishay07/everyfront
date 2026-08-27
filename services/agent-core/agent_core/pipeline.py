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
import hashlib
import json
import uuid

from . import config, delivery_bridge, factmerge, pubsub_client, rules_bridge
from .agents import auditor, clock, filer, lookup, reader, strategist, verifier
from .casedata import parse_bill_dates
from .store import store

#: The two §3.1 document types this system PRODUCES rather than receives.
#: `agent_core/document_storage.py`'s `doc_type_for_front` writes exactly
#: these, and nothing else in the codebase ever does -- Reader classifies an
#: INCOMING document into one of `bill`/`itemized_bill`/`denial_letter`/
#: `collection_notice`/`gfe`/`income_proof`, never into either of these.
AGENT_GENERATED_TYPES = {"generated_application", "generated_letter"}


def _fact_event_id(case_id: str, agent: str, action: str, fact: object) -> str:
    """A deterministic `events/{event_id}` for one FACT (contract §3.1 gives
    the event an explicit id precisely so a caller can choose it).

    Hashed over the case, the agent, the action and a caller-supplied
    description of the fact itself -- never over the LLM's narration, which is
    the thing that varies between two runs that established exactly the same
    thing. `json.dumps(sort_keys=True)` so a dict's key order cannot change
    the id; `default=str` so a stray date object degrades to something stable
    rather than raising inside the audit log.
    """
    raw = json.dumps(
        [case_id, agent, action, fact], sort_keys=True, default=str, separators=(",", ":")
    )
    return "fact-" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:40]


def _log(
    case_id: str,
    agent: str,
    action: str,
    detail: str,
    citations: list[str] | None = None,
    *,
    fact: object = None,
):
    """Append one row to the audit log / activity feed.

    Pass `fact` for anything ANALYSIS establishes, and the event gets a
    deterministic id derived from it, so re-establishing the same fact is a
    no-op instead of a new row (§2.3: idempotent handlers). Leave it off for
    the filing lifecycle, where each event records a distinct thing that
    happened in the world.

    WHAT MAKES TWO EVENTS THE SAME EVENT. Not `(agent, action, detail)`:
    `detail` is frequently the LLM's freeform narration (`lookup`'s
    `resolve_hospital` logs `lookup_turn["answer"]`, Reader logs its own), and
    two runs that resolve the same hospital narrate it differently every
    time -- content-hashing the sentence would have deduplicated nothing,
    which is exactly why `ef-2026-0001` carries six DIFFERENTLY-WORDED
    `resolve_hospital` rows saying one thing. An event's identity is the fact
    it records; the narration is presentation. So `fact` is the deterministic,
    LLM-free description of what was established -- the resolved EIN, the
    serialized deadline, the audit finding including its own line references.

    THE TRAP ON THE OTHER SIDE: two genuinely distinct events must not
    collapse. `rules.audit._cash_price_findings` runs PER LINE, so a bill with
    the same overcharged code on two lines yields two findings whose kind,
    description and citation are byte-identical and which are nonetheless two
    separate overcharges. Their `line_refs` differ, so each finding's whole
    dict -- not its prose -- is what identifies it, and both rows survive.
    Reader's per-document events are keyed on `doc_id` for the same reason:
    "classified as a bill" about document A and about document B is two facts.

    AND A FACT THAT LEGITIMATELY CHANGES STILL SHOWS. A deadline recomputed
    after a corrected statement date serializes differently, so it hashes
    differently and lands as a new row next to the old one -- which is what an
    audit log is for. Nothing is ever rewritten or removed; `append_event`
    simply declines to write a row that is already there.
    """
    return store.append_event(
        case_id,
        agent,
        action,
        detail,
        citations or [],
        event_id=None if fact is None else _fact_event_id(case_id, agent, action, fact),
    )


def is_agent_generated(doc: dict | None) -> bool:
    """True if this document is one THIS SYSTEM produced (a filled application
    or a letter the Filer just sent), rather than evidence about the bill.

    Everything else -- including a document whose `type` is still `""` or None
    because Reader has not classified it yet -- is treated as incoming
    evidence and re-runs the full analysis, which is the product working as
    designed: an income proof the patient uploads a day later, or a denial
    letter that arrives in week three, MUST re-open the question of which
    fronts apply. The test is what the document IS, not when it arrived.
    """
    return bool(doc) and (doc.get("type") or "") in AGENT_GENERATED_TYPES


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _render_unknown(unknown: list[dict]) -> str:
    """The "what is still missing" half of the merge event.

    Spelled out per fact, with the full reason for anything NO document type
    can ever establish (`factmerge.UNSOURCEABLE_PATIENT_FACTS`) and a bare
    name for the rest. A case that cannot be screened must say which single
    fact is blocking it, not hand a human a list of three things to go and
    check when exactly one of them is actually absent.
    """
    spelled = [
        f"{u['field']} -- {u['reason']}"
        for u in unknown
        if u["field"].split(".", 1)[-1] in factmerge.UNSOURCEABLE_PATIENT_FACTS
    ]
    bare = [
        u["field"]
        for u in unknown
        if u["field"].split(".", 1)[-1] not in factmerge.UNSOURCEABLE_PATIENT_FACTS
    ]
    parts = list(spelled)
    if bare:
        parts.append("also not stated by any document on file: " + ", ".join(bare))
    return " | ".join(parts)


def _merge_document_facts(case_id: str, case: dict) -> dict:
    """THE MERGE STEP: every document's extraction -> canonical
    `patient`/`bill`. Returns the (possibly unchanged) case.

    A real step in the pipeline, run once per analysis pass between Reader and
    the Lookup->Clock/Auditor->Strategist cascade, over EVERY document on file
    rather than folding in whichever one just arrived. The precedence rules,
    and the reasoning behind each, live in `agent_core/factmerge.py`; this
    function is the part that touches Firestore and the audit trail.

    WHY IT HAS TO EXIST AT ALL. `select_fronts`, `compute_deadlines` and
    `screen_eligibility` all read `case["patient"]` and `case["bill"]`.
    Reader writes `documents[].extracted`. The old `_merge_bill_fields`
    carried seven bill scalars across that gap and nothing else -- no
    `line_items`, nothing about the patient -- so a real emailed bill
    (`case-1a0412ccfef90917`) whose three PDFs all classified and extracted
    perfectly still reached `select_fronts` as an empty patient and a
    line-item-less bill, and every front came back inapplicable. The Auditor,
    which reads the documents directly, had already booked $210.00 of
    duplicate 80053 on the same case: one case, two contradictory answers.

    BOTH OUTCOMES ARE LOGGED, and that is the point. What the documents
    established, with the document type each fact came from; what a document
    claims that the case already knows differently (recorded, never silently
    applied -- see factmerge rule 3); and what is still unknown, named one
    fact at a time. `fact=` on each event keeps re-analysis from re-logging a
    merge that established the same things (§2.3).
    """
    documents = store.list_documents(case_id)
    patch, report = factmerge.merge_document_facts(case, documents)
    if patch:
        # `or case`: as everywhere else in this pipeline, a case purged
        # mid-run (demo reset, manual delete) writes nothing and returns None
        # rather than being resurrected -- finish against the local copy.
        case = store.update_case(case_id, patch) or case

    if report["established"]:
        _log(
            case_id,
            "reader",
            "merge_document_facts",
            "merged "
            + ", ".join(
                f"{e['field']}={e['value']!r} (from the {e['source_type']} document)"
                for e in report["established"]
            )
            + " into the case. Nothing was inferred: every value above is stated by a "
            "document on file.",
            fact=report["established"],
        )
    if report["deferred"]:
        _log(
            case_id,
            "verifier",
            "document_disagrees_with_case",
            "; ".join(
                f"the {d['source_type']} document states {d['field']}={d['document_value']!r} "
                f"but the case already records {d['case_value']!r}; the case value was KEPT "
                "(a document mentions a person, it is not a record of one -- a value entered "
                "by a human is not overwritten by an extraction)"
                for d in report["deferred"]
            ),
            fact=report["deferred"],
        )
    if report["unknown"]:
        _log(
            case_id,
            "reader",
            "facts_not_established",
            _render_unknown(report["unknown"]),
            fact=[u["field"] for u in report["unknown"]],
        )
    return case


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
        # One document, one classification. Keyed on the document AND the
        # label, so a re-read that reaches a different conclusion (a bill on
        # the second pass, having been unreadable on the first) is a new,
        # visible fact rather than a silent overwrite -- but a re-read that
        # agrees with itself is not a second row.
        fact=[doc_id, rf["label"]],
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
            fact=[doc_id, sorted(rf["scrubbed_fields"])],
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
    # NOT a local three-way guard with its own sentence. `packages/rules`
    # decides "can this be screened, and if not, what is missing?" -- this
    # module asks it. A second copy of that judgement is what let a fixed bug
    # keep answering from a stale reimplementation once already (526a8b9).
    gap = rules_bridge.describe_patient_data_gap(patient)
    if gap is not None:
        return 0, gap
    if not _is_plain_int(amount_cents) or amount_cents <= 0:
        return 0, "no billed amount on file to erase"

    elig = rules_bridge.screen_eligibility(income, household, state, hospital)
    if elig.determination != "free":
        return 0, (
            f"eligibility determination is {elig.determination!r}, not 'free' -- {elig.explain()}"
        )
    return amount_cents, elig.explain()


def _patient_label(case: dict) -> str:
    """What a calendar event is titled with. Synthetic fixture names only
    (§0 rule 6); no case id, for the same reason `filer.py` keeps case ids out
    of prompts -- a Calendar event is another place a rename cannot scrub."""
    return str((case.get("patient") or {}).get("name") or "unnamed patient").strip()


async def _sync_deadlines_to_calendar(
    case_id: str, case: dict, deadlines: list[dict]
) -> list[dict]:
    """§4 persona 4 WO5, wired: every computed `Deadline` -> the demo Google
    Calendar, red inside 7 days, the regulation citation in the description.

    WHY HERE AND NOT IN THE FILER. A deadline exists the moment Clock computes
    it, which is *before* anyone approves anything -- the whole point of the
    240-day FAP window landing on a calendar is that a human sees it while
    there is still time to act. Hanging it off the filing would put the
    deadline on the calendar only for fronts that got filed, which is exactly
    the ones that no longer need the reminder.

    WHY IT CANNOT SLOW THE APPROVAL PATH. `_run_cascade` runs off
    `case.document.added` (Pub/Sub push), never inside
    `POST /cases/{id}/approve_filing` -- that endpoint's synchronous work was
    cut to Verifier + a publish for exactly this reason (a measured 6-minute
    timeout; see `approve_and_request_filing`). It is also the last thing the
    cascade does, after every Firestore write, and it runs in a worker thread:
    `sync_deadlines` is blocking `googleapiclient` I/O, and calling it inline
    would stall the event loop for every other case being processed on the
    same Cloud Run instance.

    AND IT CANNOT FAIL THE ANALYSIS. `MissingCredentialsError` is already
    handled inside `sync_deadlines` (returns `[]`), which is the state today
    and until a human mints the token in `infra/OAUTH.md`. Anything else --
    an expired refresh token, a 500 from Google, `google-api-python-client`
    missing from the image -- is caught here and logged as a failed sync.
    `google_auth.MissingCredentialsError`'s own docstring states the contract:
    "a missing calendar sync must never take down a filing that already
    succeeded." The same is true of an analysis that already succeeded.
    """
    configured = delivery_bridge.google_sync_configured()
    dated = [d for d in deadlines if d.get("due")]
    if not configured:
        # `fact=` (see `_log`) makes this deterministic: logged ONCE per case,
        # not once per cascade, so an unconfigured integration states itself
        # plainly without flooding the activity feed.
        if dated:
            _log(
                case_id,
                "clock",
                "calendar_sync_skipped",
                f"{len(dated)} deadline(s) computed but NOT written to Google Calendar: the "
                "demo account's OAuth refresh token is not configured in this environment "
                "(see infra/OAUTH.md). The deadlines themselves are unaffected.",
                fact="no-google-credentials",
            )
        return []

    try:
        synced = await asyncio.to_thread(
            delivery_bridge.sync_deadlines, case_id, _patient_label(case), deadlines
        )
    except Exception as exc:  # noqa: BLE001 -- a calendar copy is never worth failing analysis for
        _log(
            case_id,
            "clock",
            "calendar_sync_failed",
            f"Google Calendar sync failed ({type(exc).__name__}: {exc}); the computed "
            "deadlines are unaffected and remain on the case.",
            fact=f"calendar-error:{type(exc).__name__}",
        )
        return []

    if synced:
        _log(
            case_id,
            "clock",
            "calendar_sync",
            f"{len(synced)} deadline(s) written to Google Calendar: "
            + "; ".join(f"{s['front']} due {s['due']}" for s in synced)
            + ". Events carry the regulation citation and turn red inside 7 days.",
            [d["citation"] for d in dated if d.get("citation")],
            # The synced events themselves, so re-running an unchanged
            # analysis re-upserts the same events (calendar_sync's own stable
            # ids) without adding a second identical row to the feed.
            fact=synced,
        )
    return synced


async def _mirror_filing_to_drive(case_id: str, fact: dict, pdf: bytes | None) -> dict | None:
    """§4 persona 4 WO6, wired: the filing this system just generated ->
    a per-case Drive folder an advocate can be given access to.

    Called from `run_filer` AFTER `filings/{filing_id}` is written, after the
    front is marked `filed`, and after `filing.completed` is published -- so
    by the time Drive is touched the filing is already durable and complete,
    and a slow or dead Drive API can only ever delay this coroutine, never the
    filing. Blocking `googleapiclient` I/O goes to a worker thread for the
    same reason as the calendar sync.

    Returns `None` when credentials are absent (today's state) or when the
    mirror fails, having logged which -- never raises.
    """
    if pdf is None:
        return None
    filename = delivery_bridge.drive_filename(fact["front"], fact["form_id"])
    if not delivery_bridge.google_sync_configured():
        _log(
            case_id,
            "filer",
            "drive_mirror_skipped",
            f"{filename} was NOT mirrored to Google Drive: the demo account's OAuth refresh "
            "token is not configured in this environment (see infra/OAUTH.md). The filing "
            f"itself is unaffected and its PDF is on the case as document {fact.get('doc_id')}.",
            fact="no-google-credentials",
        )
        return None

    try:
        result = await asyncio.to_thread(
            delivery_bridge.mirror_case_filings,
            case_id,
            [{"filename": filename, "pdf_bytes": pdf, "front": fact["front"]}],
        )
    except Exception as exc:  # noqa: BLE001 -- a Drive copy never fails a completed filing
        _log(
            case_id,
            "filer",
            "drive_mirror_failed",
            f"Google Drive mirror of {filename} failed ({type(exc).__name__}: {exc}); the "
            "filing itself already completed and is unaffected.",
            fact=f"drive-error:{type(exc).__name__}",
        )
        return None

    if result:
        _log(
            case_id,
            "filer",
            "drive_mirror",
            f"{filename} mirrored to the case's Google Drive folder "
            f"(folder {result['case_folder_id']}), shareable with an advocate.",
            fact=result,
        )
    return result


async def _run_cascade(case_id: str, case: dict) -> dict:
    """Lookup -> {Clock, Auditor} (parallel) -> Strategist, exactly once, then
    the case-level patch (status, savings, denial_flag). Returns the four
    agent turns, same shape `on_document_added` always has.
    """
    lookup_turn = await lookup.run(case_id, case)
    lf = lookup_turn["fact"]
    _log(
        case_id,
        "lookup",
        "resolve_hospital",
        lookup_turn["answer"] or lf["note"],
        lf["citations"],
        # `note` is built in code (agents/lookup.py's `_resolve_fact`), unlike
        # the `answer` above it, which is the model talking. Resolving the
        # same hospital the same way twice is one fact.
        fact=[lf.get("resolved"), lf.get("ein"), lf.get("note"), lf.get("citations")],
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
        # Same shape of backfill for `patient.state`, and it matters more:
        # state selects the entire deadline regime (California has NO
        # charity-care deadline, Illinois has a 90-day one -- §3.5's
        # `compute_deadlines(bill, state)`), so a case with no state gets the
        # federal floors and nothing else. `factmerge` reads it off the
        # facility letterhead, which is the document a patient actually holds;
        # the resolved `hospitals/{ein}` record carries the same fact from
        # LEDGER's IRS Schedule H / CMS pipeline and is available only here,
        # after Lookup. Fill-a-gap only, like every other patient fact -- it
        # can confirm what the letterhead said but never rewrite it, and never
        # a state a human entered.
        patient_now = case.get("patient") or {}
        hospital_state = factmerge.state_from_hospital(hospital)
        if hospital_state and not str(patient_now.get("state") or "").strip():
            patch["patient"] = {**patient_now, "state": hospital_state}
            _log(
                case_id,
                "lookup",
                "state_from_hospital_record",
                f"no document on file stated a state; taking {hospital_state} from the "
                f"resolved hospital record for {hospital.get('name', resolved_ein)}, which "
                "is where the facility is -- the state-law overrides in the deadline engine "
                "are hospital-conduct statutes, so the facility's state is the governing one.",
                fact=["state-from-hospital", hospital_state],
            )
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
        # The whole serialized deadline, so a due date recomputed from a
        # corrected statement date shows up as a new row (STATUTE's
        # `.explain()` spells out the arithmetic, so it changes with it),
        # while the same deadline recomputed unchanged does not.
        _log(case_id, "clock", "compute_deadline", d["explain"], [d["citation"]], fact=d)

    af = auditor_turn["fact"]
    for finding in af["findings"]:
        _log(
            case_id,
            "auditor",
            f"audit_finding:{finding['kind']}",
            finding["detail"],
            [finding["citation"]] if finding["citation"] else [],
            # The finding INCLUDING its `line_refs`. Two cash-price findings
            # on two different lines of the same bill carry identical prose
            # and are two real overcharges -- both must stay in the feed. See
            # `_log`'s docstring.
            fact=finding,
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
                fact=examined,
            )
        else:
            _log(
                case_id,
                "auditor",
                "audit_skipped",
                "no line items were extracted from any document on file -- $0.00 audit "
                "findings reflects missing/unparseable data, not a clean bill.",
                fact="no-line-items",
            )
    if af["denial_check"]["ran"]:
        _log(
            case_id,
            "auditor",
            "denial_lawfulness_check",
            af["denial_check"]["detail"],
            [af["denial_check"].get("citation", "")],
            # `detail` here is `DenialCheck.explain()` from packages/rules --
            # computed, not narrated, so it is itself the fact.
            fact=[af["denial_check"]["detail"], af["denial_check"].get("citation", "")],
        )
    elif af["denial_check"].get("reason"):
        _log(
            case_id,
            "auditor",
            "denial_lawfulness_check_skipped",
            af["denial_check"]["reason"],
            fact=af["denial_check"]["reason"],
        )

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
            # Everything analysis owns about the front, minus `status`, which
            # it does NOT own once a filing is under way (see
            # `store.upsert_front_from_analysis`). Including it would make the
            # same decision re-log itself the first time a front is filed.
            fact={k: v for k, v in front.items() if k != "status"},
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
    savings_detail = (
        f"Audit findings (duplicates/PTP/MUE/cash-price): ${audit_cents / 100:,.2f}"
        f"{audit_note}. "
        f"Charity-care free-tier erasure: ${charity_erasure_cents / 100:,.2f} "
        f"({charity_explain}). Reported savings for this pass: "
        f"${combined_cents / 100:,.2f} (max of the two -- charity-care erasure, when it "
        "applies, already subsumes any billing-error dollars on the same bill)."
    )
    # Built entirely from computed numbers and STATUTE's own explanation --
    # no narration in it -- so the sentence IS the fact. If any figure moves,
    # the text moves and the feed gets a new, correct row.
    _log(case_id, "auditor", "savings_summary", savings_detail, [], fact=savings_detail)

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

    # Calendar LAST: after every Firestore write AND after the analysis is
    # announced. The case is the product; the calendar is a copy of it, so
    # nothing -- not a subscriber, not `/demo/inject_bill`'s caller -- waits
    # on Google to learn that the analysis finished. See
    # `_sync_deadlines_to_calendar`; it cannot raise.
    await _sync_deadlines_to_calendar(case_id, case, cf["deadlines"])

    return {
        "lookup": lookup_turn,
        "clock": clock_turn,
        "auditor": auditor_turn,
        "strategist": strategist_turn,
    }


#: Fields a `case.document.added` payload carries when the publisher also has
#: the document itself -- i.e. the Gmail intake path
#: (services/intake/intake/pipeline.py publishes `gcs_uri`, `filename` and
#: `raw_text` alongside the ids). An event carrying NONE of them names records
#: it cannot reconstruct; see `ensure_case_and_document_from_event`.
EVENT_DOCUMENT_FIELDS = ("raw_text", "gcs_uri", "filename")


def event_carries_document(payload: dict) -> bool:
    """True if this `case.document.added` payload contains the document, not
    just a pointer to one somebody else was supposed to have stored."""
    return any(field in payload for field in EVENT_DOCUMENT_FIELDS)


def ensure_case_and_document_from_event(payload: dict) -> dict:
    """Create `cases/{case_id}` and `documents/{doc_id}` from a
    `case.document.added` payload that names records nothing has created yet.

    THE DEFECT THIS CLOSES: a bill that arrives by email produced nothing at
    all, and said "ok" at every step. `services/intake` has no Firestore
    grant -- the `ef-intake` service account has no `datastore.user` role, so
    it genuinely cannot write `cases/` itself -- and it derives the case id
    from the Gmail thread (`case-{thread_id}`). Nothing then created that
    case: `on_document_added` returned `{"error": "no such case ..."}`, the
    push handler discarded the error, returned HTTP 200 and marked the
    document processed, so Pub/Sub never retried it. intake's own docstring
    said agent-core "auto-creates both records on first sight". It never did.
    This is that code.

    WHAT IT MAY AND MAY NOT WRITE. Everything here comes out of the event; not
    one field is inferred:

    * `patient` and `bill` are `{}`. They are non-nullable in
      web/lib/types.ts's `CaseSummary` (and `store.create_case` defaults every
      OTHER CaseSummary field but these two), so the keys must exist or the
      dashboard reads `undefined` -- a silent blank, not a loud error. Their
      CONTENTS are unknown until Reader has read the PDF, and an emailed bill
      tells us nothing about the patient. Empty is the honest shape: HANDOFF
      defect #5 is this project's worst bug, an unreadable bill that produced
      an invented EIN and epoch dates which the Clock then turned into real
      regulatory deadlines. Absent stays absent.
    * `type` is `""`, not a guess. Reader takes the stored type as a
      classification HINT (`agents/reader.py`: `label = doc_type_hint or
      classification["label"]`), so writing "bill" here would override Gemma's
      first-pass classification with an assumption -- silently mislabelling a
      denial letter, and disabling the §1.3-bonus model's whole job. `""` is
      falsy, so Gemma decides. It is also a string, which matters: CANVAS's
      DocumentGallery renders `titleCase(d.type)`, and `titleCase(undefined)`
      throws and takes the case-detail page down with it (the same class of
      crash CaseList.tsx documents from a live stub row). Reader overwrites it
      with the real label seconds later.

    Returns `{"case_created": bool, "document_created": bool}`.
    """
    case_id, doc_id = payload["case_id"], payload["doc_id"]
    _, case_created = store.create_case_if_absent(case_id, {"patient": {}, "bill": {}})
    _, doc_created = store.add_document_if_absent(
        case_id,
        doc_id,
        {
            "type": "",
            "gcs_uri": payload.get("gcs_uri"),
            "filename": payload.get("filename"),
            "raw_text": payload.get("raw_text") or "",
        },
    )
    if case_created or doc_created:
        # Logged as "reader" because §3.1's `events[].agent` enum is closed and
        # CANVAS's AgentAvatar indexes a Record<AgentName, ...> by it -- an
        # invented "intake" agent renders an unstyled, unlabelled blank avatar
        # in the feed. Reader is the agent this document is on its way to.
        source = payload.get("gmail_message_id")
        origin = f"gmail message {source}" if source else "an intake event"
        name = payload.get("filename") or doc_id
        chars = len(payload.get("raw_text") or "")
        opened = (
            f"Opened case {case_id} from {origin}, on document {name}"
            if case_created
            else f"Attached document {name} from {origin} to existing case {case_id}"
        )
        _log(
            case_id,
            "reader",
            "case_opened_from_intake" if case_created else "document_attached_from_intake",
            f"{opened} ({chars} characters of extracted text). No patient or bill facts are "
            "known yet -- both are left empty rather than assumed, and stay that way until "
            "Reader has actually read the document.",
        )
    return {"case_created": case_created, "document_created": doc_created}


async def on_document_added(case_id: str, doc_id: str) -> dict:
    """Reader classifies+extracts one document, then Lookup, Clock, Auditor,
    and Strategist re-run and the case moves to `strategy_ready`. Publishes
    `case.analysis.complete`.

    This is the real, genuinely-asynchronous path: a new document can arrive
    at any time (Gmail intake) with no way to know if it is the last one, so
    every document re-triggers the full cascade. For a caller that already
    knows every document up front, see `process_case_documents` below --
    it does the same work without paying for N cascades.

    EXCEPT for a document this system generated itself (see
    `is_agent_generated`). The Filer stores every filled application and
    letter it sends as a case document (`document_storage`, §3.1's
    `generated_application`/`generated_letter`), so filing three fronts adds
    three documents to the case. Feeding those back into the analysis is a
    feedback loop, and it is the reason `ef-2026-0007`'s flagship audit
    finding was logged 14 times: producing a letter is not new evidence about
    the bill, so re-reading it (a Gemma call plus a Gemini extraction, on a
    $150 budget) and re-auditing the whole bill is pure waste -- and, worse,
    `select_fronts` is pure and hands every applicable front straight back at
    "open", which is what `store.upsert_front_from_analysis` exists to
    survive. Better not to run the analysis at all than to keep patching what
    it clobbers on the way out.
    """
    case = store.get_case(case_id)
    if case is None:
        return {"error": f"no such case {case_id}"}
    doc = store.get_document(case_id, doc_id)
    if doc is None:
        return {"error": f"no such document {doc_id} on case {case_id}"}
    if is_agent_generated(doc):
        return {
            "skipped": (
                f"{doc_id} is a {doc.get('type')} this system generated; a filing we produced "
                "is not new evidence about the bill and does not re-trigger analysis"
            ),
            "doc_id": doc_id,
        }

    reader_turn = await _run_reader(case_id, doc_id)
    case = _merge_document_facts(case_id, case)

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

    Documents this system generated itself are dropped from the batch for the
    same reason `on_document_added` skips them entirely (see there); if that
    leaves nothing to read, the cascade does not run either.
    """
    case = store.get_case(case_id)
    if case is None:
        return {"error": f"no such case {case_id}"}

    generated = [d for d in doc_ids if is_agent_generated(store.get_document(case_id, d))]
    doc_ids = [d for d in doc_ids if d not in set(generated)]
    if not doc_ids:
        return {
            "skipped": "every document in this batch was generated by this system, not received",
            "doc_ids": generated,
        }

    reader_turns = await asyncio.gather(*(_run_reader(case_id, doc_id) for doc_id in doc_ids))

    # ONE merge for the whole batch, not one per document: `_merge_document_facts`
    # reads every document on file and resolves precedence across all of them
    # (see factmerge rule 2), so folding them in one at a time would only make
    # "which document wins" depend on the order `gather` happened to return.
    # A reader turn that errored wrote no extraction, so it contributes
    # nothing here without needing to be filtered out.
    case = _merge_document_facts(case_id, store.get_case(case_id) or case)

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


#: Prefixed onto every `filer.file` event. Code-built, model-free, and FIRST
#: in the string so it survives truncation in the activity feed as well as
#: narration.
_SIMULATED_PREFIX = (
    "[SIMULATED] no live fax/mail vendor credentials are configured, so this filing was "
    "recorded by RELAY's fake vendor -- the form was really rendered and really passed the "
    "destination allowlist, but nothing left the building."
)
_LIVE_PREFIX = "[LIVE] transmitted by a real vendor."


def _filing_detail(front: str, ff: dict, answer: str | None) -> str:
    """The `events/` line for one filing. Whether the send was simulated is
    stated by CODE, before the model gets a word in.

    THE BUG THIS EXISTS FOR. This used to be `filer_turn["answer"] or
    (<fallback containing 'SIMULATED'>)`. The fallback was only ever reached
    when the LLM failed to narrate at all -- so in every healthy run the word
    "SIMULATED" was never written, because the model's one-sentence summary
    (which is told the channel, vendor id and status, and nothing about
    simulation) replaced the whole line. Live, right now, every filing in
    `filings/` is a fake-vendor send and not one `filer.file` event says so.
    That is HANDOFF.md defect #6 wearing a third hat: the fact was computed
    correctly and then dropped on the way to the only place a judge reads.

    `simulated` is a fact about the world, so it is not narration's to
    phrase, shorten, or omit -- the same rule §2.1 applies to deadline math.
    The model's sentence is appended as presentation, after it.
    """
    prefix = _SIMULATED_PREFIX if ff.get("simulated", True) else _LIVE_PREFIX
    narration = (answer or "").strip() or (
        f"filed {front!r} via {ff['channel']} (vendor_id={ff['vendor_id']}); real-world "
        f"destination would be {ff.get('real_destination')!r}; generated PDF saved as "
        f"document {ff.get('doc_id')} ({ff.get('gcs_uri') or 'no GCS bucket configured'})"
    )
    return f"{prefix} {narration}"


async def run_filer(case_id: str, front: str, filing_id: str) -> dict:
    case = store.get_case(case_id)
    if case is None:
        return {"error": f"no such case {case_id}"}

    filer_turn = await filer.run(case_id, case, front, filing_id=filing_id)
    ff = filer_turn["fact"]
    _log(case_id, "filer", "file", _filing_detail(front, ff, filer_turn.get("answer")))

    # NOT `update_case(case_id, {"fronts": ...})` from the `case` read at the
    # top of this function: by the time Filer returns, that snapshot is however
    # many seconds stale, and writing the whole array back is exactly how a
    # sibling front's concurrently-written "filed" status got clobbered (PROOF,
    # ef-2026-0001/-0003/-0007). One front, one field, one transaction.
    store.set_front_status(case_id, front, "filed")
    pubsub_client.publish(
        config.TOPIC_FILING_COMPLETED, {"filing_id": filing_id, "status": ff["status"]}
    )

    # The filing is durable and announced by this point; Drive is a copy of an
    # artifact that already exists. See `_mirror_filing_to_drive`.
    await _mirror_filing_to_drive(case_id, ff, filer_turn.get("pdf"))
    return filer_turn
