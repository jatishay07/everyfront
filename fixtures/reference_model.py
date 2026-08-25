"""Provisional stand-ins for rules functions STATUTE has not landed yet.

**HANDOFF -> STATUTE (persona 3), WO3-5:** `packages/rules/` currently ships
`compute_deadlines` and `screen_eligibility` only (contract §3.5). This repo
has no `select_fronts`, `audit_line_items`, or `check_denial_lawfulness` yet.
PROOF owns `fixtures/` and `tests/` only and cannot add them to
`packages/rules/` -- doing so would violate the "never touch another
persona's directory" rule (BUILD_PLAYBOOK.md §0.2).

Since the fixture corpus (`fixtures/cases_data.py`) is useless as a test
oracle without SOME implementation of "what front decisions/audit findings/
denial-lawfulness verdicts should this case produce", this module encodes the
playbook's own decision rules (§4 persona 3 WO3-5) as a reference model, used
ONLY by `tests/` to check the corpus against itself. It is deliberately
simple, deliberately not the real thing, and every function below says so.

When STATUTE ships the real `select_fronts` / `audit_line_items` /
`check_denial_lawfulness`, the tests importing this module should switch to
importing the real ones and this module can be deleted. Each test that uses
it does `try: from rules.<mod> import <fn> / except ImportError: use the
reference model here` -- so the switch is automatic the day those functions
land under their contract names, with no import to fix.
"""

from __future__ import annotations

from dataclasses import dataclass

from fixtures.cases_data import Hospital, LineItem

PPDR_MIN_DELTA_CENTS = 400_00  # 45 CFR 149.620(b) -- kept in sync with deadlines.py


@dataclass(frozen=True)
class FrontDecisionRef:
    front: str  # "charity_care" | "ppdr" | "debt_validation" | "audit"
    applicable: bool
    reason: str
    citation: str = ""


def select_fronts_reference(
    patient: dict,
    bill: dict,
    line_items: tuple[LineItem, ...],
    hospital: Hospital | None,
    eligibility_determination: str | None,
) -> list[FrontDecisionRef]:
    """Playbook §4 persona 3 WO3's decision tree, spelled out literally.

    `bill` must be the FULLY ASSEMBLED bill dict (contract §3.1 shape,
    including computed `amount_cents` / `gfe_amount_cents`) -- not the raw
    `CaseFixture.bill` from cases_data.py, which lacks those derived keys.

    - debt_validation first when in_collections + a validation notice exists
      (encodes the ordering: it freezes everything else).
    - charity_care applicable when the hospital is nonprofit AND the
      eligibility screen returned free/discounted. A for-profit hospital
      gets an explicit inapplicable decision citing the absence of a
      26 CFR 1.501(r) duty -- the honest path, not silence.
    - ppdr applicable when uninsured, a GFE exists, and the delta is >= $400.
    - audit always applies when an itemized bill exists (every case here has
      one, even if its extraction failed -- case 6 is the only exception,
      since there is no bill data at all to audit).
    """
    out: list[FrontDecisionRef] = []

    if bill.get("in_collections") and bill.get("validation_notice_date") is not None:
        out.append(
            FrontDecisionRef(
                "debt_validation",
                True,
                "in_collections with an open validation window -- sequences "
                "before every other front.",
                "12 CFR 1006.34(b); 15 USC 1692g(a)",
            )
        )

    if hospital is not None:
        if not hospital.nonprofit:
            out.append(
                FrontDecisionRef(
                    "charity_care",
                    False,
                    f"{hospital.name} is for-profit -- no 26 CFR 1.501(r) "
                    "financial-assistance obligation exists.",
                    "26 CFR 1.501(r)-1(a)",
                )
            )
        elif eligibility_determination in ("free", "discounted"):
            out.append(
                FrontDecisionRef(
                    "charity_care",
                    True,
                    f"Screened {eligibility_determination} against {hospital.name}'s FAP.",
                    "26 CFR 1.501(r)-4(b)(2)",
                )
            )
        else:
            out.append(
                FrontDecisionRef(
                    "charity_care",
                    False,
                    f"Screened {eligibility_determination or 'unknown'}; no charity-care front.",
                )
            )

    gfe = bill.get("gfe_amount_cents")
    amount = bill.get("amount_cents")
    if (
        patient.get("insured") is False
        and gfe is not None
        and amount is not None
        and amount - gfe >= PPDR_MIN_DELTA_CENTS
    ):
        out.append(
            FrontDecisionRef(
                "ppdr",
                True,
                f"Bill exceeds GFE by ${(amount - gfe) / 100:,.2f}, over the $400 floor.",
                "45 CFR 149.620(c)",
            )
        )

    if line_items:  # an itemized bill was actually extracted
        out.append(
            FrontDecisionRef(
                "audit",
                True,
                "Itemized bill present -- billing audit always runs.",
                "42 USC 1395b-7(b); 45 CFR Part 180",
            )
        )
    else:
        out.append(
            FrontDecisionRef(
                "audit",
                False,
                "No itemized line items were extractable from this document.",
            )
        )

    return out


@dataclass(frozen=True)
class AuditFindingRef:
    kind: str
    code: str
    description: str
    amount_cents: int
    note: str


def audit_line_items_reference(line_items: tuple[LineItem, ...]) -> list[AuditFindingRef]:
    """Read the `finding` tag PROOF seeded on each LineItem (cases_data.py).

    This is NOT an NCCI/MUE engine -- packages/datapipes has no NCCI pipeline
    yet (LEDGER WO3) and packages/rules has no audit_line_items yet (STATUTE
    WO4). It just surfaces the findings the fixture corpus was deliberately
    built to contain, so tests can check the corpus's own arithmetic (WO5:
    "the numbers must add up") without waiting on either.
    """
    return [
        AuditFindingRef(
            li.finding, li.code, li.description, li.finding_amount_cents, li.finding_note
        )
        for li in line_items
        if li.finding
    ]


@dataclass(frozen=True)
class DenialCheckRef:
    unlawful: bool
    undisclosed_docs: tuple[str, ...]
    citation: str = "26 CFR 1.501(r)-4(b)(3)"


def check_denial_lawfulness_reference(
    demanded_docs: tuple[str, ...], fap_published_docs: tuple[str, ...]
) -> DenialCheckRef:
    """Set-difference of demanded vs. published docs, per 1.501(r)-4(b)(3).

    A hospital may not deny financial assistance for missing documentation
    that its own published FAP doesn't list.
    """
    undisclosed = tuple(d for d in demanded_docs if d not in fap_published_docs)
    return DenialCheckRef(unlawful=bool(undisclosed), undisclosed_docs=undisclosed)
