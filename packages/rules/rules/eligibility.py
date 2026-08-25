"""Financial assistance eligibility screening.

Working agreement §2.1: pure functions, no LLM. §2.2: every threshold cites its
source. The output carries the exact arithmetic so the filed application -- and
the demo's activity feed -- can show its work.

Two federal facts frame everything here:

  * 26 CFR 1.501(r)-4(b)(2) requires a nonprofit hospital's FAP to state the
    eligibility criteria and the basis for calculating charges. The thresholds
    themselves are the hospital's own choice, which is why they come from the
    `hospitals/{ein}` record (contract §3.1) rather than being hardcoded.
  * A state may impose a FLOOR beneath which no hospital in that state may set
    its threshold. Those floors are STATE_FLOORS below.

THE ZERO SENTINEL -- read before touching this file.

IRS Schedule H reports an unoffered discount tier as ``0``, not as a blank. The
day-1 spike found Sutter Bay filing ``FPGFamilyIncmLmtDscntCarePct = 0`` across
all seven facilities, meaning "we do not offer discounted care" -- NOT "we offer
it to people at 0% of poverty". Read literally, a 0 threshold screens every
patient as ineligible while looking completely healthy: no crash, no exception,
just a quiet wrong answer that denies someone their bill relief. See
docs/SPIKE.md gate (a).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from rules.fpl import income_as_fpl_pct

Determination = Literal["free", "discounted", "ineligible", "unknown"]

# A threshold of 0 means the tier is NOT OFFERED. See module docstring.
NOT_OFFERED_SENTINEL = 0


@dataclass(frozen=True)
class StateFloor:
    """A statutory minimum threshold every hospital in the state must meet.

    `free_pct` / `discounted_pct` are floors: a hospital may be more generous,
    never less. `applies_to_for_profit` matters because several of these state
    acts bind every hospital, while 26 CFR 1.501(r) reaches only nonprofits.
    """

    free_pct: int | None
    discounted_pct: int | None
    citation: str
    applies_to_for_profit: bool = True
    note: str = ""


STATE_FLOORS: dict[str, StateFloor] = {
    # Hospital Fair Pricing Policies. The 400% floor is why Sutter's filed 400%
    # is the statutory minimum rather than generosity -- spike gate (a).
    "CA": StateFloor(
        free_pct=400,
        discounted_pct=400,
        citation="Cal. Health & Safety Code §127405",
        note="CA sets one 400% FPL floor covering both tiers.",
    ),
    # Hospital Uninsured Patient Discount Act -- the same act that carries the
    # 90-day clock in deadlines.py. Binds for-profit hospitals too.
    "IL": StateFloor(
        free_pct=None,
        discounted_pct=300,
        citation="210 ILCS 89/10 (Hospital Uninsured Patient Discount Act)",
        note="Uninsured patients at or below 300% FPL. Rural/critical-access "
        "facilities carry a higher ceiling; not yet encoded -- see TODO.",
    ),
}


@dataclass(frozen=True)
class EligibilityResult:
    """Outcome of a screen, with the arithmetic that produced it."""

    determination: Determination
    fpl_pct: float | None
    free_threshold_pct: int | None
    discounted_threshold_pct: int | None
    citations: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def explain(self) -> str:
        """Show the work. This text goes into the filing and the audit log."""
        if self.determination == "unknown":
            return "Eligibility unknown: " + "; ".join(self.notes or ["insufficient data"])
        head = f"Income is {self.fpl_pct}% of the federal poverty level. "
        if self.determination == "free":
            body = f"At or below the {self.free_threshold_pct}% threshold for free care."
        elif self.determination == "discounted":
            body = (
                f"Above the {self.free_threshold_pct}% free-care threshold but at or below "
                f"the {self.discounted_threshold_pct}% threshold for discounted care."
            )
        else:
            ceiling = self.discounted_threshold_pct or self.free_threshold_pct
            body = f"Above the highest applicable threshold ({ceiling}%)."
        tail = (" Basis: " + "; ".join(self.citations)) if self.citations else ""
        return head + body + tail


def _threshold(raw: int | None, tier: str, notes: list[str]) -> int | None:
    """Normalize a Schedule H threshold, honoring the zero sentinel.

    Returns None when the tier is not offered or not reported, so callers can
    never accidentally compare an income against a 0% ceiling.
    """
    if raw is None:
        notes.append(f"hospital reports no {tier} threshold")
        return None
    if raw == NOT_OFFERED_SENTINEL:
        notes.append(f"hospital does not offer {tier} (Schedule H reports 0)")
        return None
    if raw < 0:
        notes.append(f"invalid negative {tier} threshold ({raw}); ignored")
        return None
    return raw


def screen_eligibility(
    income_cents: int,
    household: int,
    state: str,
    hospital: dict,
    *,
    year: int = 2026,
) -> EligibilityResult:
    """Screen a patient against a hospital's FAP thresholds. Contract §3.5.

    Args:
        income_cents: annual household income in cents.
        household: household size, >= 1.
        state: two-letter state code.
        hospital: a `hospitals/{ein}` record (contract §3.1). Recognized keys:
            `free_care_max_fpl_pct`, `discounted_care_max_fpl_pct`, `nonprofit`.
        year: FPL guideline year.

    Returns:
        EligibilityResult. Never raises on a malformed hospital record -- a
        thrown exception mid-caseload is worse than an honest "unknown", and
        the Strategist is built to route unknowns to a human.
    """
    notes: list[str] = []
    citations: list[str] = []

    free = _threshold(hospital.get("free_care_max_fpl_pct"), "free care", notes)
    disc = _threshold(hospital.get("discounted_care_max_fpl_pct"), "discounted care", notes)

    # A state floor raises whatever the hospital published. 26 CFR 1.501(r)
    # leaves the numbers to the hospital; state law can set a minimum.
    floor = STATE_FLOORS.get(state.strip().upper())
    if floor is not None:
        nonprofit = hospital.get("nonprofit", True)
        if nonprofit or floor.applies_to_for_profit:
            if floor.free_pct is not None and (free is None or free < floor.free_pct):
                free = floor.free_pct
                notes.append(f"free-care threshold raised to the {state.upper()} statutory floor")
            if floor.discounted_pct is not None and (disc is None or disc < floor.discounted_pct):
                disc = floor.discounted_pct
                notes.append(
                    f"discounted-care threshold raised to the {state.upper()} statutory floor"
                )
            citations.append(floor.citation)
            if floor.note:
                notes.append(floor.note)

    if free is None and disc is None:
        notes.append("no usable threshold from the hospital record or state law")
        return EligibilityResult("unknown", None, None, None, citations, notes)

    try:
        pct = income_as_fpl_pct(income_cents, household, state, year)
    except ValueError as exc:
        notes.append(f"cannot compute FPL percentage: {exc}")
        return EligibilityResult("unknown", None, free, disc, citations, notes)

    if hospital.get("nonprofit", True):
        citations.append("26 CFR 1.501(r)-4(b)(2)")

    if free is not None and pct <= free:
        return EligibilityResult("free", pct, free, disc, citations, notes)
    if disc is not None and pct <= disc:
        return EligibilityResult("discounted", pct, free, disc, citations, notes)
    return EligibilityResult("ineligible", pct, free, disc, citations, notes)
