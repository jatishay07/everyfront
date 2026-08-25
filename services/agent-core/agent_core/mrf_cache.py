"""Optional, bounded, cached cash-price lookups against a hospital's MRF.

Defect #1 (persona 5 WO2): `savings_found_cents` must be real, and one of its
three defensible components is "the cash-price delta where MRF data exists"
(`rules.audit.audit_line_items`'s `cash_price_delta` finding, gated on a
`cash_price_lookup` callable -- see `packages/rules/rules/audit.py`).
LEDGER's fetcher (`packages/datapipes/datapipes/mrf.py`) does the real work:
`cms-hpt.txt` -> MRF -> attested cash price, already proven live against real
hospital systems in docs/SPIKE.md gate (b).

Two things make calling it directly, on every case, unsafe for this repo's
other hard requirement (defect #3: a case under 45s):

  1. MRFs are tens-to-hundreds of MB, fetched over a live HTTP connection.
     `fetch_cash_prices` streams and stops early once it has what it needs,
     but "early" still means "however far into the file the first requested
     code is found" -- not bounded in the worst case (no match at all means
     scanning up to 2,000,000 rows). A single slow/unlucky fetch must never
     be allowed to eat the whole demo's time budget.
  2. `packages/datapipes` is not currently part of agent-core's Cloud Run
     build context (see infra/deploy.sh's `pkgs_for agent-core`, which is
     ATLAS's file, outside this persona's owned paths) -- so it may simply
     not be importable in the deployed container at all.

This module handles both honestly: the import is optional (falls back to "no
lookup available" if datapipes is not bundled -- flagged in `bridge_sources()`-
style fashion via `available()`), and every real fetch runs off the event
loop with a hard wall-clock deadline (`MRF_FETCH_DEADLINE_S`), after which it
is abandoned and the caller proceeds with cash-price checking simply skipped
for that hospital -- exactly `rules.audit.audit_line_items`'s own "a lookup
callable may be None" graceful-degradation contract, extended one layer up.
Results are cached per (mrf_url, sorted codes) for the life of the process so
a hospital seen twice in the same demo session (or the same case's multiple
documents) never pays the network cost twice.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger("agent_core.mrf_cache")

try:
    from datapipes.mrf import fetch_cash_prices as _fetch_cash_prices

    _IMPORT_ERROR: str | None = None
except ImportError as exc:  # pragma: no cover -- exercised only when datapipes isn't bundled
    _fetch_cash_prices = None
    _IMPORT_ERROR = str(exc)

# Wall-clock ceiling for one MRF fetch. Generous enough for a small/medium
# hospital CSV over a real network connection, short enough that even a
# total miss cannot meaningfully dent the 45s per-case budget (defect #3).
MRF_FETCH_DEADLINE_S = 4.0

_CACHE_TTL_S = 3600.0  # hospital chargemasters change quarterly, not per-demo
_cache: dict[tuple[str, tuple[str, ...]], tuple[float, dict[str, int]]] = {}


def available() -> bool:
    """True if `packages/datapipes` is importable in this process."""
    return _fetch_cash_prices is not None


def unavailable_reason() -> str | None:
    return _IMPORT_ERROR


def _fetch_sync(mrf_url: str, codes: tuple[str, ...]) -> dict[str, int]:
    prices = _fetch_cash_prices(mrf_url, list(codes), timeout=int(MRF_FETCH_DEADLINE_S))
    return {p.code: round(p.cash * 100) for p in prices if p.cash is not None}


async def cash_price_lookup_for(mrf_url: str | None, codes: list[str]):
    """Build a `(code) -> cents | None` callable for `rules.audit.audit_line_items`.

    Returns `None` (meaning: skip the cash-price check entirely) when there is
    no MRF URL, no `datapipes` in this process, no codes to look up, the
    fetch times out, or the fetch fails for any reason -- never raises, and
    never blocks the caller past `MRF_FETCH_DEADLINE_S`.
    """
    if not mrf_url or not codes or not available():
        return None

    key = (mrf_url, tuple(sorted(set(codes))))
    cached = _cache.get(key)
    now = time.monotonic()
    if cached is not None and (now - cached[0]) < _CACHE_TTL_S:
        table = cached[1]
        return lambda code: table.get(code)

    try:
        table = await asyncio.wait_for(
            asyncio.to_thread(_fetch_sync, key[0], key[1]), timeout=MRF_FETCH_DEADLINE_S
        )
    except TimeoutError:
        logger.info(
            "MRF fetch for %s timed out after %ss; skipping cash-price check",
            mrf_url,
            MRF_FETCH_DEADLINE_S,
        )
        return None
    except Exception:  # noqa: BLE001 -- ~1/3 of real MRFs are unusable (GAO); never crash the caseload
        logger.info("MRF fetch for %s failed; skipping cash-price check", mrf_url, exc_info=True)
        return None

    _cache[key] = (now, table)
    if not table:
        return None
    return lambda code: table.get(code)
