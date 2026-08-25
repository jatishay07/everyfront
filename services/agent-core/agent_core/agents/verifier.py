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
"""

from __future__ import annotations

from .. import config
from ..store import store
from . import common

NAME = "verifier"

INSTRUCTION = (
    "You are Verifier, the last check before a filing goes out. Call "
    "get_verifier_result exactly once and report, in 1-2 sentences, whether this "
    "front is clear to file. If it is not, state the exact reason -- a human "
    "advocate reads this before deciding what to do next."
)


def _facts(case_id: str, case: dict, front: str) -> dict:
    patient = case.get("patient") or {}
    # §3.1 renamed annual_income -> annual_income_cents (2026-08-25); accept
    # both, same as rules.fronts._income_cents, since not every upstream
    # source (e.g. PROOF's fixture corpus) has caught up to the new key yet.
    stated_income = patient.get("annual_income_cents") or patient.get("annual_income")
    stated_household = patient.get("household_size")
    issues: list[str] = []

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
            doc_income = ext.get("annual_income_cents")
            if doc_income is not None and stated_income:
                tolerance = stated_income * config.VERIFIER_INCOME_TOLERANCE_PCT / 100
                if abs(doc_income - stated_income) > tolerance:
                    issues.append(
                        f"document {d.get('doc_id')} reports income {doc_income} cents vs. "
                        f"stated {stated_income} cents -- outside the "
                        f"{config.VERIFIER_INCOME_TOLERANCE_PCT:.0f}% tolerance"
                    )
            doc_household = ext.get("household_size")
            if (
                doc_household is not None
                and stated_household is not None
                and doc_household != stated_household
            ):
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
    prompt = (
        f"Verify case {case_id}'s {front!r} front before filing. Call get_verifier_result first."
    )
    turn = await common.run_agent_turn(NAME, config.GEMINI_MODEL, INSTRUCTION, [tool], prompt)
    return {"fact": fact, **turn}
