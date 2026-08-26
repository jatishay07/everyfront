"""Clock: thin LLM wrapper around STATUTE's `compute_deadlines`.

Playbook §4 persona 5, WO1: "thin LLM wrappers that call STATUTE's pure
functions and write results + citations to the case. The LLM narrates, the
code computes." This module is the literal, minimal reading of that sentence:
`_facts` calls `rules_bridge.compute_deadlines` (packages/rules, zero LLM
calls inside it) and the ADK agent's only tool hands that exact list back.
"""

from __future__ import annotations

from .. import config, rules_bridge
from ..casedata import parse_bill_dates, serialize_deadline
from . import common

NAME = "clock"

INSTRUCTION = (
    "You are Clock. You must never compute a date yourself -- always call "
    "get_clock_result, which has already run the statutory deadline math, and "
    "report the deadlines (with citations) it returns. If a deadline's due date "
    "is null, say plainly that no deadline applies and why."
)


def _facts(case: dict) -> list[dict]:
    bill = parse_bill_dates(case.get("bill") or {})
    patient = case.get("patient") or {}
    deadlines = rules_bridge.compute_deadlines(
        bill, patient.get("state", ""), insured=patient.get("insured")
    )
    return [serialize_deadline(d) for d in deadlines]


async def run(case_id: str, case: dict) -> dict:
    deadlines = _facts(case)
    fact = {"case_id": case_id, "deadlines": deadlines}
    tool = common.make_fact_tool(
        "get_clock_result",
        "Return every statutory deadline computed for this case's bill.",
        fact,
    )
    # No raw case_id in the prompt -- see reader.py's docstring note (bug
    # found live 2026-08-25): an LLM's freeform narration lands verbatim in
    # `events/{id}.detail`, which a case rename cannot scrub after the fact.
    prompt = "Report the deadlines for this case. Call get_clock_result first."
    turn = await common.run_agent_turn(NAME, config.GEMINI_MODEL, INSTRUCTION, [tool], prompt)
    return {"fact": fact, **turn}
