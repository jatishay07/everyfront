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

ADDED (persona 5, "capture what the patient says without letting it pass for
what a document proves"): two more checks, both about the email body that now
reaches the pipeline as a `patient_statement` document.

  3. A front whose determination RESTS ON a fact the patient merely stated
     (`fronts[].rests_on`, computed by leave-one-out in
     `agent_core.statedfacts`) is blocked, naming the fact and quoting the
     patient's own words. This is the deliberate answer to "do not silently
     make charity care applicable": the determination is computed and shown,
     the front is marked provisional, and it stops HERE -- at the gate a human
     is already standing at -- rather than being suppressed upstream where
     nobody would learn what the missing number is worth.
  4. The patient's stated income is cross-checked against every income_proof
     document, ±15%. This is the check §4 persona 5 WO1 always described and
     which, until the body reached the pipeline, was comparing a document
     against a value copied off that same document.
"""

from __future__ import annotations

from .. import config, delivery_bridge, statedfacts
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


#: Which patient facts a given front's determination is allowed to rest on
#: unverified. The answer is none, for every front -- this map exists to name
#: the fact per front rather than to admit exceptions.
def _front_entry(case: dict, front: str) -> dict:
    return next((f for f in (case.get("fronts") or []) if f.get("front") == front), {})


def _statement_issues(case_id: str, case: dict, front: str) -> tuple[list[str], list[dict]]:
    """Refuse to file a determination that rests on a sentence somebody typed,
    and cross-check the ones that CAN be checked.

    THE FILING GATE (persona 5, this work order). `agent_core.statedfacts`
    lets a fact the patient stated in their email fill a gap no document can
    fill -- household size, which no bill, GFE, denial letter, collection
    notice or pay stub carries and without which charity care cannot be
    screened at all. The determination that follows is real arithmetic over
    STATUTE's real thresholds, and it is worth showing: on the live case it is
    the difference between $210 of duplicate billing and the whole $2,625.

    What it is not is something this system may act on unattended. A filed FAP
    application is a claim made in a patient's name to a hospital; the number
    that decides it must be one a human has stood behind. So the front stays
    APPLICABLE and visibly provisional, and dies here -- which is the honest
    place for it to die, because this is the gate a human is already standing
    at (§3.3 `POST /cases/{id}/approve_filing`), and the refusal tells them
    the one fact to confirm rather than a generic "insufficient data".

    TWO CHECKS, not one:

      1. `fronts[].rests_on` -- what the analysis pass itself computed, by
         leave-one-out over `select_fronts`, as load-bearing for THIS front.
      2. An independent guard on charity care specifically: an applicable
         charity-care front whose `patient.household_size` is not established
         cannot have got there any way except through the overlay. This does
         not consult `rests_on` at all, so a front written before this
         existed -- or by a pass whose flags were lost -- still cannot be
         filed on a household size that is not on the case.

    Also returns the corroborations found (never an issue, always worth
    saying): a stated income that MATCHES the pay stub is the first time this
    Verifier has had two genuinely independent readings of the same fact to
    compare, which is what the ±15% tolerance in §4 persona 5 WO1 was for.
    """
    issues: list[str] = []
    stated = case.get("patient_stated") or {}
    facts = statedfacts.facts(stated)
    patient = case.get("patient") or {}

    rests_on = list(_front_entry(case, front).get("rests_on") or [])
    if front == "charity_care" and "household_size" in facts and "household_size" not in rests_on:
        # Check 2. Belt-and-suspenders, deliberately not routed through
        # `rests_on`: see the docstring.
        established = patient.get("household_size")
        if established is None or established == "":
            rests_on.append("household_size")

    for field in rests_on:
        record = facts.get(field) or {}
        quote = (record.get("quote") or "").strip()
        said = f' -- their words were "{quote}"' if quote else ""
        issues.append(
            f"this {front!r} determination rests on the patient's own statement of "
            f"{statedfacts.label_for(field)} ({record.get('value')!r}){said}, which no "
            "document on file establishes and nothing has verified. A filing that asserts "
            "it to a hospital must be confirmed by a human first: supply "
            f"patient.{field} on the case (§3.3 POST /cases) and this front clears"
        )

    mismatches, corroborations = _cross_check_stated_income(case_id, facts)
    return issues + mismatches, corroborations


def _cross_check_stated_income(case_id: str, facts: dict) -> tuple[list[str], list[dict]]:
    """The patient's SAID income against every income_proof document on file.

    THE CHECK §4 PERSONA 5 WO1 ALWAYS DESCRIBED, finally with two independent
    numbers in it. "Cross-check extracted income docs vs stated income (±15%
    tolerance -> flag)" was, until the email body reached the pipeline,
    comparing a document against `patient.annual_income_cents` -- a value the
    merge had just copied off that same document. It could not disagree with
    itself, so the check had nothing to say on any case that had an income
    proof at all. A figure the patient typed is a genuinely separate reading:
    "$32,000 a year" against a pay stub's $32,000.00 is corroboration, and
    "$32,000 a year" against a pay stub reading $58,000 is a real conflict
    that must stop a charity-care filing, because whichever number is wrong,
    the FAP application is about to assert one of them to a hospital.

    Tolerance is `config.VERIFIER_INCOME_TOLERANCE_PCT` -- the same ±15% the
    document check uses, applied to the STATED figure, because the patient's
    "about $32,000" is the approximate half of the pair.
    """
    stated_income = (facts.get("annual_income_cents") or {}).get("value")
    if not stated_income:
        return [], []
    mismatches: list[str] = []
    corroborations: list[dict] = []
    tolerance = stated_income * config.VERIFIER_INCOME_TOLERANCE_PCT / 100
    for doc in store.list_documents(case_id):
        if doc.get("type") != "income_proof":
            continue
        extracted = doc.get("extracted") or {}
        if extracted.get("is_income_proof") is False:
            continue
        doc_income = extracted.get("annual_income_cents")
        if not doc_income:
            continue
        record = {
            "doc_id": doc.get("doc_id"),
            "document_income_cents": doc_income,
            "stated_income_cents": stated_income,
        }
        if abs(doc_income - stated_income) <= tolerance:
            corroborations.append(record)
        else:
            mismatches.append(
                f"the patient's message states an annual income of {stated_income} cents, "
                f"but income document {doc.get('doc_id')} reports {doc_income} cents -- "
                f"outside the {config.VERIFIER_INCOME_TOLERANCE_PCT:.0f}% tolerance. One of "
                "the two is wrong and a filing would assert one of them"
            )
    return mismatches, corroborations


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

    # Runs for EVERY front, not just charity_care: `rests_on` is computed per
    # front by leave-one-out, so a stated fact that turns out to be
    # load-bearing for PPDR (a stated coverage status on a case whose GFE
    # never said) blocks that filing too, on exactly the same reasoning.
    statement_issues, corroborations = _statement_issues(case_id, case, front)
    issues.extend(statement_issues)

    return {
        "case_id": case_id,
        "front": front,
        "passed": not issues,
        "issues": issues,
        # Not an issue and not a pass/fail input -- the Verifier's positive
        # finding, so an advocate reading the event can see that the pay stub
        # and the patient's own account of their income agree, rather than
        # only ever hearing from this agent when something is wrong.
        "corroborations": corroborations,
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
