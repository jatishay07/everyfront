"""Auditor: thin LLM wrapper around STATUTE's `audit_line_items` and
`check_denial_lawfulness`.

Playbook §4 persona 5, WO1 + the "Denial Triage" feature in §1.2: cross-checks
demanded documents against the hospital's published FAP list under 26 CFR
1.501(r)-4(b)(3). That check needs the hospital's actual FAP document list;
`hospitals/{ein}` records generally carry no `fap_required_documents` field
(only two of PROOF's demo fixtures inject one -- see `_denial_check` below).
Rather than guess at a list and risk a false "unlawful denial" finding -- the
single most reputationally expensive kind of bug this product can ship -- the
Auditor honestly reports the check as unavailable when that data is missing,
instead of running it against an empty list (which would flag every demanded
document as unlisted).

Defect #1 (persona 5 WO2): `total_findings_cents` now includes a real
cash-price-delta check, not just exact-duplicate lines. `_facts` is async
because building the `cash_price_lookup` STATUTE's `audit_line_items` accepts
means a bounded, cached MRF fetch (see `agent_core.mrf_cache`) -- gated
entirely on the resolved hospital already carrying an `mrf_url` (Lookup, this
same pipeline run, earlier) and on `packages/datapipes` being importable in
this process; either being absent degrades to skipping the cash-price check,
never to inventing one.
"""

from __future__ import annotations

from .. import config, mrf_cache, rules_bridge
from ..store import store
from . import common

NAME = "auditor"

INSTRUCTION = (
    "You are Auditor. Call get_auditor_result exactly once, then summarize any "
    "billing findings (duplicates, unit/MUE flags) and the denial-lawfulness check "
    "in 1-3 sentences. If the denial check is 'unavailable', say so plainly rather "
    "than guessing."
)


def _all_line_items(case_id: str) -> list[dict]:
    items: list[dict] = []
    for doc in store.list_documents(case_id):
        items.extend((doc.get("extracted") or {}).get("line_items") or [])
    return items


def _denial_check(case_id: str, case: dict) -> dict:
    denial_docs = [d for d in store.list_documents(case_id) if d.get("type") == "denial_letter"]
    if not denial_docs:
        return {"ran": False, "reason": "no denial_letter document on file"}
    demanded: list[str] = []
    for d in denial_docs:
        demanded.extend((d.get("extracted") or {}).get("demanded_documents") or [])
    if not demanded:
        return {"ran": False, "reason": "denial letter carries no demanded_documents"}

    hospital = case.get("hospital") or {}
    fap_docs = hospital.get("fap_required_documents") or []
    # rules.denial.check_denial_lawfulness degrades to insufficient_data=True on
    # an empty fap_doc_list rather than flagging every demand as unlisted, so
    # it is safe to always call this -- no need to guard on fap_docs ourselves.
    result = rules_bridge.check_denial_lawfulness(demanded, fap_docs)
    return {
        "ran": True,
        "violation": result.violation,
        "insufficient_data": result.insufficient_data,
        "unlisted_docs": list(result.unlisted_docs),
        "detail": result.explain(),
        "citation": result.citation,
    }


async def _facts(case_id: str, case: dict) -> dict:
    items = _all_line_items(case_id)
    cash_price_lookup = None
    mrf_source = "no items to audit"
    if items:
        hospital = case.get("hospital") or {}
        codes = sorted({item.get("code") for item in items if item.get("code")})
        cash_price_lookup = await mrf_cache.cash_price_lookup_for(hospital.get("mrf_url"), codes)
        if cash_price_lookup is not None:
            mrf_source = f"live MRF fetch ({hospital.get('mrf_url')})"
        elif not mrf_cache.available():
            mrf_source = (
                f"skipped -- packages/datapipes not importable: {mrf_cache.unavailable_reason()}"  # noqa: E501
            )
        elif not hospital.get("mrf_url"):
            mrf_source = "skipped -- resolved hospital has no mrf_url on file"
        else:
            mrf_source = "skipped -- MRF fetch found no matching cash price, timed out, or failed"
    findings = (
        rules_bridge.audit_line_items(items, cash_price_lookup=cash_price_lookup) if items else []
    )
    denial = _denial_check(case_id, case)
    total_findings_cents = sum(
        f.potential_savings_cents or 0 for f in findings if f.potential_savings_cents
    )
    return {
        "case_id": case_id,
        "findings": [
            {
                "kind": f.kind,
                "detail": f.description,
                "codes": list(f.codes),
                "line_refs": list(f.lines),
                "amount_cents": f.potential_savings_cents,
                "citation": f.citation,
            }
            for f in findings
        ],
        "total_findings_cents": total_findings_cents,
        "denial_check": denial,
        "cash_price_source": mrf_source,
        "source": rules_bridge.bridge_sources(),
    }


async def run(case_id: str, case: dict) -> dict:
    fact = await _facts(case_id, case)
    tool = common.make_fact_tool(
        "get_auditor_result",
        "Return billing audit findings and the denial-lawfulness check for this case.",
        fact,
    )
    prompt = (
        f"Audit the line items and any denial for case {case_id}. Call get_auditor_result first."
    )
    turn = await common.run_agent_turn(NAME, config.GEMINI_MODEL, INSTRUCTION, [tool], prompt)
    return {"fact": fact, **turn}
