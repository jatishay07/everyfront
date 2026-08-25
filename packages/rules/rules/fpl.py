"""Federal Poverty Level tables.

Source: HHS annual poverty guidelines. Values are annual income in whole
dollars for the 48 contiguous states and DC; Alaska and Hawaii have separate,
higher schedules by statute (42 USC 9902(2)).

Every figure below is pinned to its Federal Register notice and verified
against the primary source (see `tests/test_fpl.py::TestTableMatchesPrimarySource`,
which cites the same notices) -- not copied from a secondary summary:

  * 2026: 91 FR 1797 ("Annual Update of the HHS Poverty Guidelines", HHS,
    Jan. 15, 2026), also mirrored at aspe.hhs.gov. Verified 2026-08-25
    against the Federal Register PDF (FR Doc. 2026-00755).
  * 2025: 90 FR 5917 (Jan. 17, 2025, FR Doc. 2025-01377), also mirrored at
    aspe.hhs.gov/sites/default/files/documents/.../detailed-guidelines-2025.pdf.
    Verified 2026-08-25 against that ASPE PDF's per-household-size table.

    BUG FOUND AND FIXED 2026-08-25 (STATUTE, wo6): the 2025 Alaska and Hawaii
    per-additional-person increments were each entered $10 low (AK $6,870,
    HI $6,320) -- likely transposed from a neighboring column or an old
    guidelines year. The correct, published increments are AK $6,880 and
    HI $6,330 (confirmed by differencing consecutive household sizes in the
    ASPE detailed table: $19,550 -> $26,430 -> $33,310 -> $40,190 is a
    constant +$6,880 step, and correspondingly for HI). LEDGER flagged this
    while building the datapipes ingestion rather than silently patching
    this package's table -- see the persona-3 work order for the story.
    A $10/person error is small in isolation but compounds with household
    size and would have under-stated the true poverty line (and so
    over-stated a household's %FPL) for every AK/HI household of 3+ screened
    against the 2025 table.

LEDGER (persona 2, work order 4) hands this table over; STATUTE owns the code
and is responsible for verifying it against the primary source before it ships.
"""

from __future__ import annotations

from typing import Literal

StateGroup = Literal["48", "AK", "HI"]

# year -> state group -> (first_person, each_additional_person)
_FPL: dict[int, dict[StateGroup, tuple[int, int]]] = {
    2026: {"48": (15_960, 5_680), "AK": (19_950, 7_100), "HI": (18_360, 6_530)},
    2025: {"48": (15_650, 5_500), "AK": (19_550, 6_880), "HI": (17_990, 6_330)},
}

# 42 USC 1395ww(d) treats AK and HI separately; everything else uses the 48-state table.
_NON_CONTIGUOUS: dict[str, StateGroup] = {"AK": "AK", "HI": "HI"}


def state_group(state: str) -> StateGroup:
    """Map a two-letter state code to its FPL schedule."""
    return _NON_CONTIGUOUS.get(state.strip().upper(), "48")


def fpl_annual_cents(household_size: int, state: str, year: int = 2026) -> int:
    """Annual federal poverty level for a household, in cents.

    Args:
        household_size: number of people in the household; must be >= 1.
        state: two-letter state code.
        year: guideline year. 2026 guidelines are 91 FR 1797.

    Raises:
        ValueError: on a household size below 1 or an unsupported year, rather
            than silently extrapolating -- a wrong FPL silently mis-screens a
            patient for charity care.
    """
    if household_size < 1:
        raise ValueError(f"household_size must be >= 1, got {household_size}")
    if year not in _FPL:
        raise ValueError(f"no FPL table for {year}; have {sorted(_FPL)}")

    first, additional = _FPL[year][state_group(state)]
    return (first + additional * (household_size - 1)) * 100


def income_as_fpl_pct(
    income_cents: int, household_size: int, state: str, year: int = 2026
) -> float:
    """Express an income as a percentage of the federal poverty level.

    A patient at exactly the poverty line returns 100.0. Hospital financial
    assistance policies are written in these terms ("free care up to 250% FPL"),
    so this is the number every eligibility threshold compares against.
    """
    base = fpl_annual_cents(household_size, state, year)
    return round(income_cents / base * 100, 4)
