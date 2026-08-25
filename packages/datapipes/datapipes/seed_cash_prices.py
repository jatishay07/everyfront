"""Pre-cache a hospital's attested cash prices onto its own `hospitals/{ein}`
Firestore record, at seed time -- so a live case never depends on fetching a
(possibly 150+ MB) MRF file inside a request.

WO6 (LEDGER): confirmed live 2026-08-25 that `agent-core`'s cash-price-delta
check depended entirely on a synchronous, 4-second-bounded MRF fetch
(`agent_core.mrf_cache`), and that this was silently returning nothing in
production because `packages/datapipes` wasn't even bundled into that
service's Cloud Run build context (fixed separately, `infra/deploy.sh`). Even
with that fixed, a live per-request fetch is strictly worse than a value
computed once, offline, and stored where the pipeline already reads hospital
facts from (Lookup already returns the whole `hospitals/{ein}` record into
`case["hospital"]`) -- so this module does the fetch here instead, writes
`cash_prices: {code: cents}` onto the hospital doc with `merge=True` (every
other field on that document is untouched), and `agent_core.agents.auditor`
now prefers this pre-cache over the live fetch (falling back to the live
fetch only for codes this doesn't cover -- see that module).

Deliberately narrow scope: this is NOT part of the 200-hospital bulk seed
(`seed.py`) -- pre-fetching cash prices for 200 hospitals' full chargemasters
would be slow, mostly wasted (~1/3 of MRFs are unusable per GAO, and the
demo only ever bills a small fixed set of CPT codes), and outside what this
work order needs. It targets one hospital + one explicit code list at a
time, run by hand (or by a small wrapper script) against the specific
hospitals/codes PROOF's fixture corpus actually bills.
"""

from __future__ import annotations

import argparse
import logging

from datapipes.mrf import fetch_cash_prices

log = logging.getLogger("datapipes.seed_cash_prices")


def build_cash_prices(mrf_url: str, codes: list[str], *, timeout: int = 60) -> dict[str, int]:
    """`{code: cash_price_cents}` for every requested code the MRF actually
    has a discounted-cash-price row for. Never raises -- `fetch_cash_prices`
    already returns `[]` on any failure (unreachable, unusable, no match)."""
    prices = fetch_cash_prices(mrf_url, codes, timeout=timeout)
    return {p.code: round(p.cash * 100) for p in prices if p.cash is not None}


def enrich_hospital_cash_prices(
    ein: str,
    codes: list[str],
    *,
    project: str | None = None,
    mrf_url_override: str | None = None,
    dry_run: bool = False,
) -> dict[str, int]:
    """Fetch cash prices for `codes` and merge them onto `hospitals/{ein}`.

    Reads the hospital's own `mrf_url` from its existing Firestore record
    unless `mrf_url_override` is given (useful when the record predates a
    resolved MRF pointer, or for a hospital -- like Sutter Bay in PROOF's
    corpus -- whose `cms-hpt.txt` resolves but names no MRF at all, i.e. the
    GAO's "~1/3 unusable" case: there is nothing to fetch, and this function
    returns `{}` rather than writing anything).

    `merge=True` on the Firestore write: every other field on the hospital
    document (name, fap_url, ...) is left exactly as-is.
    """
    from google.cloud import firestore

    client = firestore.Client(project=project)
    doc_ref = client.collection("hospitals").document(ein)

    mrf_url = mrf_url_override
    if mrf_url is None:
        snap = doc_ref.get()
        if not snap.exists:
            raise ValueError(f"hospitals/{ein} does not exist -- seed it first")
        mrf_url = (snap.to_dict() or {}).get("mrf_url")

    if not mrf_url:
        log.warning("%s: no mrf_url on file and none provided -- nothing to fetch", ein)
        return {}

    cash_prices = build_cash_prices(mrf_url, codes)
    if not cash_prices:
        log.warning("%s: MRF fetch returned no usable cash prices for %s", ein, codes)
        return {}

    log.info("%s: fetched cash prices for %d/%d code(s)", ein, len(cash_prices), len(codes))
    if not dry_run:
        doc_ref.set({"cash_prices": cash_prices}, merge=True)
    return cash_prices


def main(argv: list[str] | None = None) -> int:
    """`python -m datapipes.seed_cash_prices --ein EIN --codes A,B,C [--mrf-url URL] [--dry-run]`"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    p = argparse.ArgumentParser(description=main.__doc__)
    p.add_argument("--ein", required=True, help="hospital EIN, e.g. 36-2169147")
    p.add_argument("--codes", required=True, help="comma-separated CPT/HCPCS codes")
    p.add_argument("--mrf-url", default=None, help="override the hospital's stored mrf_url")
    p.add_argument("--project", default=None, help="GCP project (defaults to ADC's)")
    p.add_argument("--dry-run", action="store_true", help="fetch but don't write to Firestore")
    args = p.parse_args(argv)

    codes = [c.strip() for c in args.codes.split(",") if c.strip()]
    result = enrich_hospital_cash_prices(
        args.ein,
        codes,
        project=args.project,
        mrf_url_override=args.mrf_url,
        dry_run=args.dry_run,
    )
    for code, cents in sorted(result.items()):
        log.info("  %s: $%.2f", code, cents / 100)
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
