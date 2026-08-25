"""Line-item billing audit: unbundling, unit ceilings, duplicates, cash price.

Working agreement §2.1: pure functions, zero LLM calls. §2.2: every finding
cites its source.

Four checks, each independently gated on data availability so a caseload with
partial data still gets the checks it can support (§2's "graceful degradation"
rule, same philosophy as `eligibility.screen_eligibility`'s "unknown"):

  * NCCI Procedure-to-Procedure (PTP) edits -- flags a column-2 (component)
    code billed alongside its column-1 (comprehensive) code. Authority: CMS's
    National Correct Coding Initiative; codified for Medicaid at 42 CFR
    447.45(b) (eff. Apr. 1, 2011), implementing ACA §6507 / Social Security
    Act §1903(r)(1)(B); the edit pairs themselves come from CMS's quarterly
    NCCI Coding Policy Manual tables, not from the CFR text.
  * Medically Unlikely Edits (MUE) -- per-code unit-of-service ceilings, same
    NCCI authority as PTP.
  * Exact-duplicate lines -- not independently regulated; flagged as a
    billing-accuracy check under the same itemized-statement right that lets
    a patient see the lines at all (42 USC 1395b-7(b)).
  * Cash-price delta -- compares the billed rate against the hospital's own
    attested cash price from its Machine-Readable File. Authority: the
    Hospital Price Transparency Final Rule, 45 CFR 180.40, 180.50 (eff. Jan.
    1, 2021), which requires publishing that cash price in the first place.

LEDGER (persona 2, work order 3) is building the canonical NCCI PTP/MUE tables
in `packages/datapipes` in parallel. Rather than import a concrete class from
a package that may not be ready, this module defines the three narrow
callables it needs (`PTPLookup`, `MUELookup`, `CashPriceLookup`) and accepts
them as optional keyword arguments. Any lookup left as `None` simply skips the
check it feeds -- it never raises, and it never fabricates a finding it can't
support. Whatever LEDGER ships just needs to satisfy these call signatures;
duck typing means no import dependency in either direction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Literal

FindingKind = Literal["ptp_conflict", "mue_exceeded", "duplicate", "cash_price_delta"]

_PTP_MUE_CITATION = (
    "CMS National Correct Coding Initiative; 42 CFR 447.45(b) (eff. Apr. 1, 2011), "
    "implementing ACA §6507 / 42 USC 1396b(r)(1)(B)"
)
_DUPLICATE_CITATION = (
    "42 USC 1395b-7(b) (itemized statement); duplicate billing is not separately regulated"
)
_CASH_PRICE_CITATION = (
    "45 CFR 180.40, 180.50 (hospital price transparency; discounted cash price, eff. Jan. 1, 2021)"
)


@dataclass(frozen=True)
class PTPEdit:
    """One NCCI Procedure-to-Procedure edit pair, as a `PTPLookup` should return it.

    `column1_code` is the comprehensive code; `column2_code` is the component
    NCCI treats as bundled into it (CMS NCCI Coding Policy Manual, ch. 1).
    `modifier_allowed` is False for edit indicator 0 (the pair may never be
    billed together, even with a modifier) and True for indicator 1 (billable
    together only with an appropriate modifier and documentation).
    """

    column1_code: str
    column2_code: str
    modifier_allowed: bool


# LEDGER's lookup need only match these shapes -- no import of packages/datapipes here.
PTPLookup = Callable[[str, str], "PTPEdit | None"]
MUELookup = Callable[[str], "int | None"]
CashPriceLookup = Callable[[str], "int | None"]  # attested cash price per unit, in cents


@dataclass(frozen=True)
class AuditFinding:
    """One audit finding. `lines` indexes into the `items` list the caller passed in."""

    kind: FindingKind
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


def _is_plain_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _valid_lines(items: list) -> list[tuple[int, str, int, int | None]]:
    """(index, code, units, charge_cents) for every line with at least a usable code.

    `units` defaults to 1 when absent or malformed rather than dropping the
    line -- a missing unit count is not a reason to skip unbundling/duplicate
    checks that don't need it. `charge_cents` is None when absent or
    malformed; checks that need money skip that line rather than guessing.
    """
    out: list[tuple[int, str, int, int | None]] = []
    for i, item in enumerate(items or []):
        if not isinstance(item, dict):
            continue
        code = item.get("code")
        if not isinstance(code, str) or not code.strip():
            continue
        units = item.get("units")
        units = units if _is_plain_int(units) and units > 0 else 1
        charge = item.get("charge_cents")
        charge = charge if _is_plain_int(charge) and charge >= 0 else None
        out.append((i, code.strip(), units, charge))
    return out


def _ptp_findings(
    lines: list[tuple[int, str, int, int | None]], ptp_lookup: PTPLookup
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    seen: set[tuple[str, str]] = set()
    for a in range(len(lines)):
        for b in range(a + 1, len(lines)):
            idx_a, code_a, _, charge_a = lines[a]
            idx_b, code_b, _, charge_b = lines[b]
            if code_a == code_b:
                continue  # identical codes are a duplicate concern, not a PTP conflict
            key = tuple(sorted((code_a, code_b)))
            if key in seen:
                continue
            edit = ptp_lookup(code_a, code_b) or ptp_lookup(code_b, code_a)
            if edit is None:
                continue
            seen.add(key)
            # Which billed line actually carries the column-2 (component) code
            # -- needed to price the finding, not just narrate it.
            column2_charge = charge_a if code_a == edit.column2_code else charge_b
            if edit.modifier_allowed:
                desc = (
                    f"{edit.column2_code} is a component of {edit.column1_code} under NCCI PTP "
                    "(edit indicator 1) -- billable together only with an appropriate modifier "
                    "and supporting documentation."
                )
                # Indicator 1 pairs CAN be legitimately billed together with a
                # modifier; without evidence one was used, this is a review
                # flag, not a certain overcharge -- never assign a dollar
                # figure here (§4 persona 7 / this work order's #3: an
                # unsubstantiated finding must not be counted).
                savings = None
            else:
                desc = (
                    f"{edit.column2_code} is bundled into {edit.column1_code} under NCCI PTP "
                    "(edit indicator 0) -- these codes may never be billed together."
                )
                # Indicator 0: the component code should never have been
                # billed separately at all, so its own charge IS the
                # overcharge -- substantiated whenever that line has a usable
                # charge_cents.
                savings = column2_charge
            findings.append(
                AuditFinding(
                    "ptp_conflict",
                    (edit.column1_code, edit.column2_code),
                    desc,
                    _PTP_MUE_CITATION,
                    lines=(idx_a, idx_b),
                    potential_savings_cents=savings,
                )
            )
    return findings


def _mue_findings(
    lines: list[tuple[int, str, int, int | None]], mue_lookup: MUELookup
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for idx, code, units, charge in lines:
        ceiling = mue_lookup(code)
        if ceiling is None or units <= ceiling:
            continue
        savings = None
        if charge is not None:
            allowed_charge = charge * ceiling // units
            savings = charge - allowed_charge
        findings.append(
            AuditFinding(
                "mue_exceeded",
                (code,),
                f"{units} units of {code} billed; the Medically Unlikely Edit ceiling is {ceiling}",
                _PTP_MUE_CITATION,
                lines=(idx,),
                potential_savings_cents=savings,
            )
        )
    return findings


def _duplicate_findings(lines: list[tuple[int, str, int, int | None]]) -> list[AuditFinding]:
    groups: dict[tuple[str, int, int | None], list[int]] = {}
    for idx, code, units, charge in lines:
        groups.setdefault((code, units, charge), []).append(idx)

    findings: list[AuditFinding] = []
    for (code, units, charge), indices in groups.items():
        if len(indices) < 2:
            continue
        extra = len(indices) - 1
        savings = charge * extra if charge is not None else None
        findings.append(
            AuditFinding(
                "duplicate",
                (code,),
                f"{len(indices)} identical lines for {code} ({units} unit(s) each) -- "
                "likely duplicate billing",
                _DUPLICATE_CITATION,
                lines=tuple(indices),
                potential_savings_cents=savings,
            )
        )
    return findings


def _cash_price_findings(
    lines: list[tuple[int, str, int, int | None]], cash_price_lookup: CashPriceLookup
) -> list[AuditFinding]:
    findings: list[AuditFinding] = []
    for idx, code, units, charge in lines:
        if charge is None:
            continue
        cash_per_unit = cash_price_lookup(code)
        if cash_per_unit is None:
            continue
        expected = cash_per_unit * units
        if charge <= expected:
            continue
        findings.append(
            AuditFinding(
                "cash_price_delta",
                (code,),
                f"{code} billed at ${charge / 100:,.2f} vs the hospital's attested cash price of "
                f"${expected / 100:,.2f} for {units} unit(s)",
                _CASH_PRICE_CITATION,
                lines=(idx,),
                potential_savings_cents=charge - expected,
            )
        )
    return findings


def audit_line_items(
    items: list,
    *,
    ptp_lookup: PTPLookup | None = None,
    mue_lookup: MUELookup | None = None,
    cash_price_lookup: CashPriceLookup | None = None,
) -> list[AuditFinding]:
    """Audit a bill's line items. Contract §3.5.

    Args:
        items: line items, each expected to look like
            `{"code": str, "units": int, "charge_cents": int, "description": str}`.
            `units` defaults to 1 and `charge_cents` is treated as absent when
            missing or malformed; a line with no usable `code` is skipped
            entirely rather than raising.
        ptp_lookup: `(code_a, code_b) -> PTPEdit | None`. Tried in both code
            orders since a real NCCI table is directional (column1/column2)
            but callers should not have to pre-sort. Skipped when None --
            LEDGER's table may not exist yet.
        mue_lookup: `(code) -> int | None`, the MUE unit ceiling. Skipped
            when None.
        cash_price_lookup: `(code) -> int | None`, the hospital's attested
            cash price per unit in cents (from its MRF). Skipped when None or
            when the corresponding line has no usable charge.

    Returns:
        Every finding across all four checks, in check order (PTP, MUE,
        duplicates, cash-price). Never raises: a malformed item is dropped
        from analysis rather than crashing the caseload.
    """
    lines = _valid_lines(items)

    findings: list[AuditFinding] = []
    if ptp_lookup is not None and len(lines) >= 2:
        findings.extend(_ptp_findings(lines, ptp_lookup))
    if mue_lookup is not None:
        findings.extend(_mue_findings(lines, mue_lookup))
    findings.extend(_duplicate_findings(lines))
    if cash_price_lookup is not None:
        findings.extend(_cash_price_findings(lines, cash_price_lookup))

    return findings


def total_savings_cents(findings: list[AuditFinding]) -> int:
    """The dollar total to report for a caseload (§3.4 `audit_findings_cents`).

    NOT a naive `sum(f.potential_savings_cents for f in findings)` -- two of
    the four checks can name the *same* line item with two different
    theories of the same underlying overcharge. Concretely: a duplicate
    metabolic-panel line and a cash-price-delta finding on that same line
    both describe money owed back on that one line, computed two different
    ways (gross-charge-of-the-extra-copy vs. gross-minus-cash-per-copy).
    Summing both would over-claim -- exactly what this work order's #3
    forbids ("a judge doing arithmetic on screen must not catch a
    discrepancy... over-claiming is far worse than under-claiming").

    Instead: split each finding's savings evenly across the lines it names,
    then for every line take the SINGLE LARGEST substantiated theory that
    names it (never the sum of overlapping ones), and add up those per-line
    maxima. A line untouched by any dollar-bearing finding contributes 0.
    """
    per_line_max_cents: dict[int, int] = {}
    for f in findings:
        if not f.potential_savings_cents or not f.lines:
            continue
        share_cents = f.potential_savings_cents // len(f.lines)
        for idx in f.lines:
            per_line_max_cents[idx] = max(per_line_max_cents.get(idx, 0), share_cents)
    return sum(per_line_max_cents.values())
