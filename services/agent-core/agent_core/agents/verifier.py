"""Verifier: pre-filing sanity checks. Blocks filing, never files.

Playbook §4 persona 5, WO1: "before any filing -- cross-check extracted income
docs vs stated income (+/-15% tolerance -> flag), household size consistency,
'is this document even an income proof' (the cat-photo check). Blocks filing
on mismatch with a human-readable reason."

The cat-photo check rides on Reader's `is_income_proof` extraction field
(agents/reader.py) rather than a separate vision call -- Gemini's structured
extraction already looked at the document and was asked to flag exactly this
case, so a second model call to ask the same question again would just be
burning quota for no new signal.

DEFECT FIX (persona 5 WO8, "never file a case whose facts were never
established"): two more checks, both traced live on `ef-2026-0006` (PROOF's
deliberately-unparseable-bill fixture), whose corrupted PDF left Reader with
nothing real to extract:

  1. `filer.py` addresses every non-fax filing (charity_care, debt_validation,
     audit) to `(case["hospital"] or {}).get("name", "unknown hospital")` --
     a records-request letter or a FAP application literally addressed to
     "unknown hospital" is not a graceful degradation, it is paperwork sent
     against a party this system never identified. Any front whose channel
     is not fax now requires a resolved hospital to pass Verifier.
  2. `audit` was applicable, approved, and FILED on ef-2026-0006 despite zero
     line items ever being extracted from any document -- there was nothing
     for the records-request letter to actually request. `audit` now also
     requires at least one real line item on file.

Both are genuine "insufficient data" outcomes, not false positives: a case
with a resolved hospital and real line items is completely unaffected.
"""

from __future__ import annotations

from .. import config, delivery_bridge
from ..store import store
from . import auditor, common

NAME = "verifier"

INSTRUCTION = (
    "You are Verifier, the last check before a filing goes out. Call "
    "get_verifier_result exactly once and report, in 1-2 sentences, whether this "
    "front is clear to file. If it is not, state the exact reason -- a human "
    "advocate reads this before deciding what to do next."
)


def _hospital_resolved(case: dict) -> bool:
    hospital = case.get("hospital")
    return isinstance(hospital, dict) and bool(hospital)


def _facts(case_id: str, case: dict, front: str) -> dict:
    patient = case.get("patient") or {}
    # §3.1 renamed annual_income -> annual_income_cents (2026-08-25); accept
    # both, same as rules.fronts._income_cents, since not every upstream
    # source (e.g. PROOF's fixture corpus) has caught up to the new key yet.
    stated_income = patient.get("annual_income_cents") or patient.get("annual_income")
    stated_household = patient.get("household_size")
    issues: list[str] = []

    # Check 1 (persona 5 WO8): `filer.py` addresses every mail-channel filing
    # to the resolved hospital's name -- never fax, which routes to CMS's C2C
    # contractor regardless of which hospital this is. A mail-channel front
    # with no resolved hospital would file paperwork addressed to a party
    # this system never identified (live: "unknown hospital" on
    # ef-2026-0006's audit filing).
    if delivery_bridge.channel_for_front(front) != "fax" and not _hospital_resolved(case):
        issues.append(
            f"no hospital could be resolved for this case -- the {front!r} filing would be "
            "addressed to an unconfirmed hospital, which this system will not send"
        )

    # Check 2 (persona 5 WO8): an `audit` filing with zero real line items on
    # file has nothing to actually request -- exactly ef-2026-0006's
    # unparseable bill, where `audit` still came back applicable (a document
    # was CLASSIFIED as an itemized bill) and got filed even though nothing
    # was ever actually extracted from it.
    if front == "audit" and not auditor.all_line_items(case_id):
        issues.append(
            "no line items were ever extracted for this case -- there is nothing on file "
            "for an audit filing to report"
        )

    if front == "charity_care":
        income_docs = [d for d in store.list_documents(case_id) if d.get("type") == "income_proof"]
        if not income_docs:
            issues.append("no income_proof document on file for a charity-care filing")
        for d in income_docs:
            ext = d.get("extracted") or {}
            if ext.get("is_income_proof") is False:
                issues.append(
                    f"document {d.get('doc_id')} does not appear to actually be an income "
                    "document (Reader's cat-photo check failed)"
                )
                continue
            # BUG (verified live on case_01, the demo's own happy path, 2026-08-25):
            # Reader's JSON-schema extraction returns 0 -- not null -- as its
            # "field not found" sentinel for an integer field the document never
            # states (a pay stub has no reason to mention household size at all).
            # `doc_household is not None` let that 0 sentinel through as if it
            # were a REAL stated value of zero, so `0 != 3` tripped a false-
            # positive block on a document that never claimed anything about
            # household size in the first place. Guarding on truthiness (like
            # the income check below already does) treats 0/absent as "this
            # document said nothing" -- the same fix, applied to both fields,
            # since income has the identical 0-sentinel exposure.
            doc_income = ext.get("annual_income_cents")
            if doc_income and stated_income:
                tolerance = stated_income * config.VERIFIER_INCOME_TOLERANCE_PCT / 100
                if abs(doc_income - stated_income) > tolerance:
                    issues.append(
                        f"document {d.get('doc_id')} reports income {doc_income} cents vs. "
                        f"stated {stated_income} cents -- outside the "
                        f"{config.VERIFIER_INCOME_TOLERANCE_PCT:.0f}% tolerance"
                    )
            doc_household = ext.get("household_size")
            if doc_household and stated_household is not None and doc_household != stated_household:
                issues.append(
                    f"document {d.get('doc_id')} states household size {doc_household}, "
                    f"case states {stated_household}"
                )

    return {
        "case_id": case_id,
        "front": front,
        "passed": not issues,
        "issues": issues,
    }


async def run(case_id: str, case: dict, front: str) -> dict:
    fact = _facts(case_id, case, front)
    tool = common.make_fact_tool(
        "get_verifier_result",
        "Return whether this case+front passes pre-filing verification, and why not if it fails.",
        fact,
    )
    # DEFECT found live 2026-08-25 (SWARM WO7, "events leak across cases"):
    # this used to be f"Verify case {case_id}'s {front!r} front before
    # filing." -- the model reliably echoed the raw case_id back into its
    # narration ("The 'audit' front for case `demo-case_02_...` has passed
    # ..."), which `_log` copies verbatim into `events/{id}.detail`. PROOF's
    # demo_reset.py runs each background fixture through this live pipeline
    # under a throwaway `demo-{fixture}-{uuid}` id and only renames the case
    # to its human-plausible `ef-2026-000N` id AFTERWARDS (rename_case);
    # rename only rewrites each event's *structured* `case_id` field, not
    # prose an LLM chose to compose that happens to contain the old one. An
    # audit trail that names the wrong case is worse than no audit trail --
    # this is the log a judge reads and a hospital would receive. `front` is
    # safe to keep: it is never renamed and names no case.
    prompt = (
        f"Verify the {front!r} front on this case before filing. Call get_verifier_result first."
    )
    turn = await common.run_agent_turn(NAME, config.GEMINI_MODEL, INSTRUCTION, [tool], prompt)
    return {"fact": fact, **turn}
