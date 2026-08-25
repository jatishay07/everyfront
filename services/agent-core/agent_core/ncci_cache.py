"""Wires LEDGER's bundled NCCI PTP/MUE table into `rules.audit.audit_line_items`.

WO6 defect: the Auditor (`agents/auditor.py`) built and passed a
`cash_price_lookup` for the cash-price-delta check, but never built or passed
a `ptp_lookup` / `mue_lookup` at all -- `audit_line_items` was called with
both defaulted to `None`, so the NCCI checks were unconditionally skipped
regardless of whether the underlying data existed or matched. Confirmed live
2026-08-25 against `demo/inject_bill`: `bridge_sources()` reported
`rules.audit.audit_line_items (STATUTE)` (correctly wired for duplicates and
cash-price) while `cash_price_source` and the finding list showed no PTP/MUE
activity at all -- because nothing ever called into that half of the check.

Unlike the MRF cash-price lookup (`mrf_cache.py`), NCCI data is NOT per-
hospital and NOT huge: `datapipes.ncci` ships a pre-built sqlite snapshot
bundled inside the package itself (`datapipes/data/ncci.sqlite`, 2,881 PTP
edit rows + 15,112 MUE rows -- see `data/ncci_manifest.json` for exact
provenance). So this module needs none of `mrf_cache`'s per-request
network bounding: it opens the bundled table once per process (module-level
singleton) and every `lookup()`/`mue()` call after that is a local, read-only
sqlite query (<10ms, per `datapipes.ncci`'s own acceptance bar).

Two things can still make this unavailable, both handled the same honest way
as `mrf_cache.available()`/`unavailable_reason()` -- never fabricate a
finding, always say so in the auditor's returned fact:

  1. `packages/datapipes` isn't in this process's PYTHONPATH at all (the same
     Cloud Run build-context gap `mrf_cache.py` documents -- fixed for
     agent-core in this same work order by adding `datapipes` to
     `infra/deploy.sh`'s `pkgs_for agent-core`).
  2. `datapipes` is importable but the bundled snapshot file wasn't copied
     into the build context for some other reason (`FileNotFoundError` from
     `datapipes.ncci.load_default`).
"""

from __future__ import annotations

import logging

logger = logging.getLogger("agent_core.ncci_cache")

try:
    from datapipes.ncci import NCCITable, load_default

    _IMPORT_ERROR: str | None = None
except ImportError as exc:  # pragma: no cover -- exercised only when datapipes isn't bundled
    NCCITable = None  # type: ignore[assignment,misc]
    load_default = None  # type: ignore[assignment]
    _IMPORT_ERROR = str(exc)

_table: NCCITable | None = None
_open_error: str | None = None
_attempted = False


def _get_table():
    """Lazily open the bundled snapshot once per process; memoize failure too
    so a missing file doesn't retry the (cheap, but pointless) open on every
    case."""
    global _table, _open_error, _attempted
    if _attempted:
        return _table
    _attempted = True
    if load_default is None:
        return None
    try:
        _table = load_default()
    except Exception as exc:  # noqa: BLE001 -- never let a missing snapshot crash a caseload
        _open_error = str(exc)
        logger.warning("NCCI snapshot unavailable: %s", exc)
    return _table


def available() -> bool:
    """True if the bundled NCCI table is open and queryable in this process."""
    return _get_table() is not None


def unavailable_reason() -> str | None:
    if _IMPORT_ERROR is not None:
        return f"packages/datapipes not importable: {_IMPORT_ERROR}"
    if _open_error is not None:
        return f"bundled NCCI snapshot failed to open: {_open_error}"
    return None


def ptp_lookup():
    """`(code_a, code_b) -> PTPEdit | None` for `rules.audit.audit_line_items`,
    or `None` if the bundled table isn't available (caller then skips the
    PTP check entirely -- same graceful-degradation contract as every other
    lookup in this pipeline)."""
    table = _get_table()
    if table is None:
        return None

    def _lookup(code_a: str, code_b: str):
        from rules.audit import PTPEdit

        result = table.lookup(code_a, code_b)
        if not result.matched:
            return None
        return PTPEdit(
            column1_code=result.column1,
            column2_code=result.column2,
            modifier_allowed=bool(result.allowed_with_modifier),
        )

    return _lookup


def mue_lookup():
    """`(code) -> int | None` MUE ceiling for `rules.audit.audit_line_items`,
    or `None` if the bundled table isn't available."""
    table = _get_table()
    if table is None:
        return None

    def _mue(code: str):
        result = table.mue(code)
        return result.mue_value if result is not None else None

    return _mue
