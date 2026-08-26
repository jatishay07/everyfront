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

WO6 (LEDGER): two more fixes, both confirmed live against `demo/inject_bill`
before this change (see the PR description for the transcript):

1. **NCCI was never wired at all.** This module built a `cash_price_lookup`
   but simply never built (or passed) a `ptp_lookup` / `mue_lookup` --
   `audit_line_items` therefore always skipped both NCCI checks, regardless
   of whether LEDGER's PTP/MUE table existed or matched. Now sourced from
   `agent_core.ncci_cache`, which opens LEDGER's bundled sqlite snapshot
   once per process (no network, no per-hospital anything -- see that
   module's docstring).
2. **A live MRF fetch is not the only way to get a cash price, and shouldn't
   be the primary one.** `mrf_cache`'s 4s-bounded fetch is real but adds
   request latency and depends on `packages/datapipes` being bundled into
   this service's container. LEDGER's seed pipeline can pre-fetch a
   hospital's cash prices once, offline, straight from the same MRF, and
   write them onto the hospital's own Firestore record (`cash_prices:
   {code: cents}` -- see `packages/datapipes/datapipes/seed_cash_prices.py`).
   `_cash_price_lookup` below now prefers that pre-cache (instant, already
   in memory on `hospital` from Lookup) and only falls back to the live
   bounded fetch for codes the pre-cache doesn't cover.
3. `total_findings_cents` is now `rules.audit.total_savings_cents`, not a
   naive sum -- see that function's docstring for why summing every
   finding's `potential_savings_cents` would double-count overlapping
   theories about the same line (this work order's #3: "a judge doing
   arithmetic on screen must not catch a discrepancy").
"""

from __future__ import annotations

from .. import config, mrf_cache, ncci_cache, rules_bridge
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


async def _cash_price_lookup(hospital: dict, codes: list[str]):
    """Build a `(code) -> cents | None` cash-price lookup + a human-readable
    source string for the event log, preferring LEDGER's seed-time pre-cache
    (`hospital["cash_prices"]`, instant, no network) over a live bounded MRF
    fetch, which now only covers codes the pre-cache doesn't have.
    """
    pre_cached: dict = hospital.get("cash_prices") or {}
    missing = [c for c in codes if c not in pre_cached]

    live_table: dict = {}
    live_note: str | None = None
    if missing:
        live_lookup = await mrf_cache.cash_price_lookup_for(hospital.get("mrf_url"), missing)
        if live_lookup is not None:
            live_table = {c: live_lookup(c) for c in missing}
            live_table = {c: v for c, v in live_table.items() if v is not None}
            if live_table:
                live_note = (
                    f"live MRF fetch for {len(live_table)} code(s) ({hospital.get('mrf_url')})"  # noqa: E501
                )
        elif not mrf_cache.available():
            live_note = f"live fetch skipped -- packages/datapipes not importable: {mrf_cache.unavailable_reason()}"  # noqa: E501
        elif not hospital.get("mrf_url"):
            live_note = None  # nothing to report: no mrf_url and no pre-cache is just "no data"
        else:
            live_note = "live MRF fetch found no further matches, timed out, or failed"

    combined = {**pre_cached, **live_table}
    notes = []
    if pre_cached:
        notes.append(f"pre-cached at seed time ({len(pre_cached)} code(s))")
    if live_note:
        notes.append(live_note)
    source = (
        "; ".join(notes)
        if notes
        else "skipped -- no cash price data available (no pre-cache, no mrf_url)"
    )  # noqa: E501

    if not combined:
        return None, source
    return (lambda code: combined.get(code)), source


async def _facts(case_id: str, case: dict) -> dict:
    items = _all_line_items(case_id)
    hospital = case.get("hospital") or {}
    cash_price_lookup = None
    cash_price_source = "no items to audit"
    if items:
        codes = sorted({item.get("code") for item in items if item.get("code")})
        cash_price_lookup, cash_price_source = await _cash_price_lookup(hospital, codes)

    ptp_lookup = ncci_cache.ptp_lookup()
    mue_lookup = ncci_cache.mue_lookup()
    if ptp_lookup is not None or mue_lookup is not None:
        ncci_source = "bundled NCCI snapshot (datapipes.ncci, no network)"
    else:
        ncci_source = (
            f"skipped -- {ncci_cache.unavailable_reason() or 'bundled NCCI snapshot unavailable'}"  # noqa: E501
        )

    findings = (
        rules_bridge.audit_line_items(
            items,
            ptp_lookup=ptp_lookup,
            mue_lookup=mue_lookup,
            cash_price_lookup=cash_price_lookup,
        )
        if items
        else []
    )
    denial = _denial_check(case_id, case)
    total_findings_cents = rules_bridge.total_savings_cents(findings)
    return {
        "case_id": case_id,
        # DEFECT found live 2026-08-25 (SWARM WO7, "ef-2026-0006 reports $0
        # savings"): with zero line items (an unparseable bill -- Reader's
        # extraction returned only sentinel defaults, no line_items at all),
        # `findings` is `[]` and pipeline._run_cascade's per-finding loop logs
        # nothing -- the exact same silence a genuinely CLEAN bill with real,
        # fully-audited line items would produce. A judge (or this system's
        # own operator) reading "$0.00 audit findings" cannot tell "we
        # examined N line items and found nothing wrong" from "there was
        # nothing to examine" without this count. See pipeline.py's
        # `_run_cascade` for the event this now drives.
        "line_items_examined": len(items),
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
        "cash_price_source": cash_price_source,
        "ncci_source": ncci_source,
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
