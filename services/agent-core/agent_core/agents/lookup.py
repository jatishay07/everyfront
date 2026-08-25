"""Lookup: resolve the hospital record for a case's bill.

Playbook §4 persona 5, WO1: "tool-calls into Firestore `hospitals/` + LEDGER's
MRF fetcher; resolves EIN/CCN; writes hospital facts + 'nonprofit: false -> no
501(r) front' honesty path."

LEDGER's MRF fetcher (packages/datapipes) does not exist yet -- that package
is still an empty stub -- so this agent only does the Firestore half today.
The honesty path is real: a for-profit hospital record produces a fact that
says, in plain language, that charity care does not apply, rather than
silently omitting the front.
"""

from __future__ import annotations

from .. import config
from ..store import store
from . import common

NAME = "lookup"

INSTRUCTION = (
    "You are Lookup, responsible for resolving which hospital a bill belongs to. "
    "Call get_lookup_result exactly once, then state in 1-2 sentences whether the "
    "hospital was resolved and whether it is nonprofit (and therefore subject to "
    "26 CFR 1.501(r)) or for-profit (no charity-care front)."
)


def _resolve_fact(case: dict) -> dict:
    bill = case.get("bill") or {}
    ein = bill.get("hospital_ein")
    if not ein:
        return {
            "resolved": False,
            "hospital": None,
            "citations": [],
            "note": "no hospital_ein on the bill yet -- cannot look up hospitals/{ein}",
        }
    hospital = store.get_hospital(ein)
    if hospital is None:
        return {
            "resolved": False,
            "hospital": None,
            "citations": [],
            "note": f"no hospitals/{ein} record on file "
            "(LEDGER's 200-hospital seed may not cover this EIN)",
        }
    nonprofit = hospital.get("nonprofit", True)
    if nonprofit:
        note = (
            f"{hospital.get('name', ein)} is a nonprofit hospital, subject to "
            "26 CFR 1.501(r) financial-assistance obligations."
        )
        citations = ["26 CFR 1.501(r)-1(b)(29)(i)"]
    else:
        note = (
            f"{hospital.get('name', ein)} is FOR-PROFIT: it has no 26 CFR 1.501(r) "
            "obligation, so the charity-care front does not apply here. Other fronts "
            "(PPDR, debt validation, audit) are unaffected."
        )
        citations = []
    return {
        "resolved": True,
        "hospital": hospital,
        "nonprofit": nonprofit,
        "citations": citations,
        "note": note,
    }


async def run(case_id: str, case: dict) -> dict:
    fact = _resolve_fact(case)
    fact["case_id"] = case_id
    tool = common.make_fact_tool(
        "get_lookup_result",
        "Return the resolved hospital record (or the honest reason it could not be resolved).",
        fact,
    )
    prompt = f"Resolve the hospital for case {case_id}. Call get_lookup_result and report back."
    turn = await common.run_agent_turn(NAME, config.GEMINI_MODEL, INSTRUCTION, [tool], prompt)
    return {"fact": fact, **turn}
