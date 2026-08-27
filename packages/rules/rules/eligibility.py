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


# Hospital-class-dependent floors (IL rural/critical-access vs. general; WA
# large-system vs. other) need a SECOND tier keyed off a hospital-record flag
# that the current `hospitals/{ein}` contract (§3.1) does not carry today --
# it has no rural/critical-access or system-size field. `screen_eligibility`
# resolves `STATE_FLOORS[state]` as the DEFAULT tier and only switches to the
# elevated tier (`_STATE_FLOOR_ELEVATED` below) when the hospital record
# explicitly says so via `_STATE_FLOOR_CLASSIFIER_KEYS[state]`. Each state's
# default was chosen as its more common hospital class (general/non-rural for
# IL; non-large-system for WA by facility count) rather than uniformly
# defaulting "low" or "high" -- but it is still a base-rate default, not a
# confirmed fact, so the note on the elevated tier's absence is always
# surfaced (see `screen_eligibility`). HANDOFF (LEDGER / contract owners):
# add `hospital["rural_or_critical_access"]` (IL, from CMS's CAH designation)
# and `hospital["large_system"]` (WA, from the RCW 70.170.060(5) size test) to
# the hospital record so this floor can resolve to its confirmed tier instead
# of the base-rate default.
STATE_FLOORS: dict[str, StateFloor] = {
    # Hospital Fair Pricing Policies. The 400% floor is why Sutter's filed 400%
    # is the statutory minimum rather than generosity -- spike gate (a).
    # Pinned to Cal. HSC §127405(a)(1)(A) (eligibility trigger) and (d)(1)
    # (payment-limit cap) -- CORRECTED 2026-08-25 (STATUTE, wo6 citation
    # audit): a prior version cited this floor to the bare section number,
    # which is where the NO-DEADLINE rule lives ((e)(3), see deadlines.py),
    # not the 400% figure. Verified against leginfo.legislature.ca.gov.
    "CA": StateFloor(
        free_pct=400,
        discounted_pct=400,
        citation="Cal. Health & Safety Code §127405(a)(1)(A), (d)(1)",
        note="CA sets one 400% FPL floor covering both tiers.",
    ),
    # Hospital Uninsured Patient Discount Act -- the same act that carries the
    # 90-day clock in deadlines.py (210 ILCS 89/15(b)). Binds for-profit
    # hospitals too (the Act's "Hospital" definition, 210 ILCS 89/5, carries
    # no nonprofit/tax-exempt qualifier).
    #
    # CORRECTED 2026-08-25 (STATUTE, wo6 citation audit): a prior version
    # applied a single 300%-discount / no-free floor to every IL hospital.
    # Verified verbatim against ilga.gov (210 ILCS 89/10): that 300%/125%
    # pairing is the RURAL / CRITICAL-ACCESS HOSPITAL tier specifically ("family
    # income of not more than 125% of the federal poverty" for 100% charity,
    # "not more than 300%" for the discount). GENERAL (non-rural) hospitals --
    # the large majority of IL facilities, including every real hospital in
    # this codebase's fixtures -- get a materially more generous floor: 100%
    # charity up to 200% FPL, discount up to 600% FPL. The old single-tier
    # value was quietly understating every general-hospital patient's floor by
    # 300 percentage points on the discount tier and by not enforcing a
    # free-care floor at all.
    "IL": StateFloor(
        free_pct=200,
        discounted_pct=600,
        citation="210 ILCS 89/10 (Hospital Uninsured Patient Discount Act; general/"
        "non-rural hospital tier)",
        note="General (non-rural) IL hospital floor: 100% charity <=200% FPL, "
        "discount <=600% FPL. Rural/critical-access hospitals get a LOWER "
        "floor (125%/300%) -- see STATE_FLOORS['IL_RURAL'] and "
        "_STATE_FLOOR_ELEVATED below; this default assumes a general hospital "
        "when classification is unknown, matching every real IL hospital in "
        "this repo's fixtures.",
    ),
    # Same Act, rural/critical-access tier -- lower than the general tier
    # above. Used by `screen_eligibility` only when the hospital record
    # explicitly says `rural_or_critical_access: True`; see
    # `_STATE_FLOOR_ELEVATED`.
    "IL_RURAL": StateFloor(
        free_pct=125,
        discounted_pct=300,
        citation="210 ILCS 89/10 (Hospital Uninsured Patient Discount Act; rural/"
        "critical-access hospital tier)",
        note="Rural/critical-access IL hospital floor: 100% charity <=125% FPL, "
        "discount <=300% FPL.",
    ),
    # Washington's Charity Care Act sets a two-tier floor by hospital/system
    # size (RCW 70.170.060(5)) -- verified verbatim against app.leg.wa.gov.
    # The statute actually grants a THIRD, intermediate 75%-of-charges partial
    # discount band (301-350% for large systems, 201-250% for others) that
    # this module cannot represent: `EligibilityResult.determination` is a
    # binary free/discounted/ineligible/unknown, with no partial-percentage
    # output. Both partial-discount bands are folded into "discounted" here,
    # using the OUTER edge of each state's discount range as `discounted_pct`
    # -- the correct binary answer to "is some discount available", even
    # though the exact percentage a patient receives inside the discounted
    # band needs the hospital's own schedule, not this floor. Flagged as a
    # known simplification rather than silently doing 3-tier math nobody asked
    # this API to return.
    "WA": StateFloor(
        free_pct=200,
        discounted_pct=300,
        citation="Wash. Rev. Code §70.170.060(5) (other/non-large-system hospital tier)",
        note="Non-large-system WA hospital floor: 100% charity <=200% FPL, "
        "some discount <=300% FPL (75%-of-charges band is 201-250%, 50% band "
        "is 251-300% -- collapsed to a single 'discounted' ceiling here). "
        "Large hospital systems (3+ acute hospitals in the system, or "
        "300+ beds in the state's most populous county, or 200+ beds in a "
        "border county over 450k population) get a higher floor -- see "
        "STATE_FLOORS['WA_LARGE_SYSTEM']; this default assumes NOT a large "
        "system when classification is unknown, the conservative-by-count "
        "reading (most individual WA hospitals are not part of a "
        "qualifying large system).",
    ),
    "WA_LARGE_SYSTEM": StateFloor(
        free_pct=300,
        discounted_pct=400,
        citation="Wash. Rev. Code §70.170.060(5) (large-system hospital tier)",
        note="Large-system WA hospital floor: 100% charity <=300% FPL, some "
        "discount <=400% FPL (75% band 301-350%, 50% band 351-400%, "
        "collapsed here).",
    ),
}


@dataclass(frozen=True)
class _ElevatedFloor:
    """Wiring for a state whose floor depends on a hospital classification.

    `hospital_key` is the boolean field on the `hospitals/{ein}` record (not
    yet in contract §3.1 -- see the HANDOFF note above `STATE_FLOORS`) that,
    when True, switches `screen_eligibility` from `STATE_FLOORS[state]` (the
    base-rate default) to `STATE_FLOORS[elevated_state_key]`.
    """

    hospital_key: str
    elevated_state_key: str
    applied_note: str


_STATE_FLOOR_ELEVATED: dict[str, _ElevatedFloor] = {
    "IL": _ElevatedFloor(
        "rural_or_critical_access", "IL_RURAL", "rural/critical-access hospital tier applied"
    ),
    "WA": _ElevatedFloor("large_system", "WA_LARGE_SYSTEM", "large-system hospital tier applied"),
}

# The real two-letter state codes `STATE_FLOORS` answers for -- the keys like
# "IL_RURAL"/"WA_LARGE_SYSTEM" are hospital-class TIERS of a state already in
# this set, never states a caller can pass in. Used only to say, honestly,
# which states this engine carries a floor for when it carries none for the
# one asked about.
_FLOOR_STATES: frozenset[str] = frozenset(
    key
    for key in STATE_FLOORS
    if key not in {e.elevated_state_key for e in _STATE_FLOOR_ELEVATED.values()}
)


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


def _resolve_state_floor(state: str, hospital: dict, notes: list[str]) -> StateFloor | None:
    """Pick the base-rate or elevated `StateFloor` for `state`.

    Some states' floors depend on a hospital classification
    (`_STATE_FLOOR_ELEVATED`) this codebase cannot always confirm -- see the
    HANDOFF note above `STATE_FLOORS`. `hospital[hospital_key] is True`
    switches to the elevated tier; anything else (missing, False, or None)
    keeps the base-rate default and, when the key is simply absent, notes the
    assumption so it doesn't read as a confirmed fact.
    """
    key = state.strip().upper()
    floor = STATE_FLOORS.get(key)
    if floor is None:
        # ADDED 2026-08-26 (STATUTE, wo8): this used to return None silently,
        # so when the hospital also published no usable threshold the only
        # thing the caller could say was "no usable threshold from the
        # hospital record or state law" -- a category, not a gap. Name which
        # of the two is missing and why. NOTE the wording: no state floor is
        # ON FILE HERE. That is a statement about this engine's coverage
        # (STATE_FLOORS, and only states this product claims to support), not
        # a legal assertion that the state imposes none.
        if not key:
            notes.append(
                "the patient's state is not recorded, so no state statutory floor could be "
                "applied; only the hospital's own published thresholds are in play"
            )
        else:
            notes.append(
                f"no state statutory floor is on file for {key} (this engine carries floors "
                f"for {', '.join(sorted(_FLOOR_STATES))}); only the hospital's own published "
                "thresholds are in play"
            )
        return None

    elevated = _STATE_FLOOR_ELEVATED.get(key)
    if elevated is None:
        return floor

    classification = hospital.get(elevated.hospital_key)
    if classification is True:
        notes.append(elevated.applied_note)
        return STATE_FLOORS[elevated.elevated_state_key]
    if classification is None:
        notes.append(
            f"hospital record does not say whether {elevated.hospital_key!r} applies; "
            f"assuming the more common class ({key} base-rate floor) -- confirm before relying "
            "on this for an atypical hospital"
        )
    return floor


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
            `free_care_max_fpl_pct`, `discounted_care_max_fpl_pct`, `nonprofit`,
            plus two keys NOT YET part of contract §3.1 (see the HANDOFF note
            above `STATE_FLOORS`) that this function reads defensively via
            `.get()` and treats as unknown when absent: `rural_or_critical_access`
            (IL) and `large_system` (WA).
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
    floor = _resolve_state_floor(state, hospital, notes)
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
        # Name the gap rather than the two places it could have come from.
        # The preceding notes already say WHICH -- "hospital reports no free
        # care threshold", "hospital does not offer discounted care (Schedule
        # H reports 0)", "no state statutory floor is on file for TX" -- so
        # this line reports the consequence and points at them.
        notes.append(
            "no eligibility threshold is established: the hospital record supplies neither a "
            "free-care nor a discounted-care FPL threshold, and no state statutory floor "
            "raised one (see the preceding notes for which)"
        )
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
