"""Bridge to packages/rules (owned by STATUTE, persona 3).

STATUTE has since shipped and merged `select_fronts` (`rules.fronts`),
`audit_line_items` (`rules.audit`), and `check_denial_lawfulness`
(`rules.denial`) -- this bridge's defensive `try/except` probes (written
before that landed, per this session's "code against those names
defensively" instruction) now resolve to STATUTE's real implementations, and
`bridge_sources()` reports so. The fallbacks below are kept, updated to match
STATUTE's real dataclass shapes field-for-field, purely as a resilience seam:
if one of these three names ever moves or fails to import (a bad merge, a
rename), agent_core keeps running on a conservative approximation instead of
crashing the whole pipeline, and the event log says so honestly rather than
silently.

`compute_deadlines` and `screen_eligibility` are re-exported directly, no
bridging needed -- STATUTE shipped those first and they have not moved.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from rules.deadlines import compute_deadlines  # noqa: F401  -- re-exported
from rules.eligibility import screen_eligibility  # noqa: F401  -- re-exported

# --------------------------------------------------------------------------
# select_fronts
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class FrontDecision:
    """Mirrors rules.fronts.FrontDecision field-for-field (see that module)."""

    front: str
    applicable: bool
    reason: str
    citation: str
    deadline: date | None = None

    def explain(self) -> str:
        state = "applicable" if self.applicable else "not applicable"
        tail = f" Deadline: {self.deadline.isoformat()}." if self.deadline is not None else ""
        return f"{self.front}: {state} -- {self.reason} ({self.citation}).{tail}"


def _fallback_select_fronts(case: dict, *, today: date | None = None) -> list[FrontDecision]:
    """Conservative front selector -- see module docstring. Does not implement
    STATUTE's debt-validation-first reordering or deadline-window checks;
    only used if `rules.fronts` fails to import at all.
    """
    del today
    bill = case.get("bill") or {}
    hospital = case.get("hospital") or {}
    out: list[FrontDecision] = []

    in_collections = bool(bill.get("in_collections"))
    has_validation_notice = bill.get("validation_notice_date") is not None
    out.append(
        FrontDecision(
            "debt_validation",
            in_collections and has_validation_notice,
            "in collections with an active validation notice"
            if in_collections and has_validation_notice
            else "not in collections, or no validation notice on file",
            "12 CFR 1006.34(b); 15 USC 1692g(a)",
        )
    )

    insured = (case.get("patient") or {}).get("insured")
    gfe_cents = bill.get("gfe_amount_cents")
    amount_cents = bill.get("amount_cents")
    delta_ok = (
        gfe_cents is not None and amount_cents is not None and (amount_cents - gfe_cents) >= 400_00
    )
    out.append(
        FrontDecision(
            "ppdr",
            insured is False and delta_ok,
            "uninsured, billed >= $400 over the good-faith estimate"
            if insured is False and delta_ok
            else "not uninsured, or delta under the $400 PPDR floor",
            "45 CFR 149.620",
        )
    )

    nonprofit = hospital.get("nonprofit", True)
    out.append(
        FrontDecision(
            "charity_care",
            bool(nonprofit),
            "hospital is nonprofit, subject to 26 CFR 1.501(r)"
            if nonprofit
            else "hospital is for-profit -- no 501(r) obligation, no charity-care front",
            "26 CFR 1.501(r)-4",
        )
    )

    has_itemized = any(
        isinstance(d, dict) and d.get("type") == "itemized_bill"
        for d in (case.get("documents") or [])
    ) or bool(bill.get("line_items"))
    out.append(
        FrontDecision(
            "audit",
            has_itemized,
            "itemized bill on file, always audited"
            if has_itemized
            else "no itemized bill on file yet",
            "42 USC 1395b-7(b)",
        )
    )
    return out


try:
    from rules.fronts import select_fronts as _select_fronts

    SELECT_FRONTS_SOURCE = "rules.fronts.select_fronts (STATUTE)"
except ImportError:
    _select_fronts = _fallback_select_fronts
    SELECT_FRONTS_SOURCE = "SWARM fallback -- rules.fronts.select_fronts failed to import"


def select_fronts(case: dict, *, today: date | None = None) -> list:
    return _select_fronts(case, today=today)


# --------------------------------------------------------------------------
# audit_line_items
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class AuditFinding:
    """Mirrors rules.audit.AuditFinding field-for-field (see that module)."""

    kind: str
    codes: tuple[str, ...]
    description: str
    citation: str
    lines: tuple[int, ...] = field(default_factory=tuple)
    potential_savings_cents: int | None = None

    def explain(self) -> str:
        savings = (
            f" Potential overcharge: ${self.potential_savings_cents / 100:,.2f}."
            if self.potential_savings_cents
            else ""
        )
        return f"[{self.kind}] {self.description} ({self.citation}).{savings}"


def _fallback_audit_line_items(items: list[dict], **_lookups) -> list[AuditFinding]:
    """Exact-duplicate-line check only -- see module docstring. Only used if
    `rules.audit` fails to import at all; ignores the PTP/MUE/cash-price
    lookups since there is no NCCI logic here to feed them.
    """
    findings: list[AuditFinding] = []
    seen: dict[tuple, list[int]] = {}
    for i, item in enumerate(items):
        key = (item.get("code"), item.get("units"), item.get("charge_cents"))
        seen.setdefault(key, []).append(i)
    for key, idxs in seen.items():
        if len(idxs) > 1 and key[0]:
            savings = (key[2] or 0) * (len(idxs) - 1) if key[2] else None
            findings.append(
                AuditFinding(
                    "duplicate",
                    (key[0],),
                    f"code {key[0]!r} billed {len(idxs)} times with identical units/charge",
                    "42 USC 1395b-7(b)",
                    lines=tuple(idxs),
                    potential_savings_cents=savings,
                )
            )
    return findings


try:
    from rules.audit import audit_line_items as _audit_line_items

    AUDIT_SOURCE = "rules.audit.audit_line_items (STATUTE)"
except ImportError:
    _audit_line_items = _fallback_audit_line_items
    AUDIT_SOURCE = "SWARM fallback -- rules.audit.audit_line_items failed to import"


def audit_line_items(
    items: list[dict],
    *,
    ptp_lookup=None,
    mue_lookup=None,
    cash_price_lookup=None,
) -> list:
    """Contract §3.5. `*_lookup` callables come from LEDGER's NCCI/MRF tables
    (packages/datapipes) when available; `audit_line_items` degrades
    gracefully (skips the check that lookup feeds) when a lookup is None --
    see rules.audit's docstring. LEDGER has not shipped those tables in this
    repo yet, so agent_core always calls this with the defaults (None) today.
    """
    return _audit_line_items(
        items,
        ptp_lookup=ptp_lookup,
        mue_lookup=mue_lookup,
        cash_price_lookup=cash_price_lookup,
    )


# --------------------------------------------------------------------------
# check_denial_lawfulness
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class DenialCheck:
    """Mirrors rules.denial.DenialCheck field-for-field (see that module)."""

    violation: bool
    unlisted_docs: tuple[str, ...]
    demanded_docs: tuple[str, ...]
    fap_doc_list: tuple[str, ...]
    insufficient_data: bool
    citation: str
    drafted_citation: str

    def explain(self) -> str:
        if self.insufficient_data:
            return "Cannot assess denial lawfulness: no FAP documentation list is on file."
        if not self.violation:
            return "No violation: every demanded document appears on the FAP's published list."
        return (
            "Violation: the hospital demanded "
            + "; ".join(self.unlisted_docs)
            + f", which its published FAP does not list ({self.citation})."
        )


def _fallback_check_denial_lawfulness(
    demanded_docs: list[str], fap_doc_list: list[str]
) -> DenialCheck:
    """Set-difference check -- see module docstring. Only used if
    `rules.denial` fails to import at all."""
    demanded = [d for d in (demanded_docs or []) if isinstance(d, str) and d.strip()]
    fap_list = [d for d in (fap_doc_list or []) if isinstance(d, str) and d.strip()]
    if not fap_list:
        return DenialCheck(
            violation=False,
            unlisted_docs=(),
            demanded_docs=tuple(demanded),
            fap_doc_list=(),
            insufficient_data=True,
            citation="26 CFR 1.501(r)-4(b)(3)",
            drafted_citation="No FAP documentation list is on file for this hospital.",
        )
    fap_set = {d.strip().lower() for d in fap_list}
    unlisted = tuple(d for d in demanded if d.strip().lower() not in fap_set)
    return DenialCheck(
        violation=bool(unlisted),
        unlisted_docs=unlisted,
        demanded_docs=tuple(demanded),
        fap_doc_list=tuple(fap_list),
        insufficient_data=False,
        citation="26 CFR 1.501(r)-4(b)(3)",
        drafted_citation=(
            "The hospital's published FAP does not list: " + "; ".join(unlisted) + "."
            if unlisted
            else "Every demanded document appears on the published FAP list."
        ),
    )


try:
    from rules.denial import check_denial_lawfulness as _check_denial_lawfulness

    DENIAL_SOURCE = "rules.denial.check_denial_lawfulness (STATUTE)"
except ImportError:
    _check_denial_lawfulness = _fallback_check_denial_lawfulness
    DENIAL_SOURCE = "SWARM fallback -- rules.denial.check_denial_lawfulness failed to import"


def check_denial_lawfulness(demanded_docs: list[str], fap_doc_list: list[str]):
    return _check_denial_lawfulness(demanded_docs, fap_doc_list)


def bridge_sources() -> dict[str, str]:
    """For the events log / debugging: which parts are real vs. fallback."""
    return {
        "select_fronts": SELECT_FRONTS_SOURCE,
        "audit_line_items": AUDIT_SOURCE,
        "check_denial_lawfulness": DENIAL_SOURCE,
    }


__all__ = [
    "AuditFinding",
    "DenialCheck",
    "FrontDecision",
    "audit_line_items",
    "bridge_sources",
    "check_denial_lawfulness",
    "compute_deadlines",
    "screen_eligibility",
    "select_fronts",
]
