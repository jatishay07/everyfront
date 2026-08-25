"""Statutory deadline engine.

Working agreement §2.1: the LLM narrates, the code computes. Nothing in this
module calls a model. Every Deadline carries the citation that produced it, so
a judge reading the repo -- or a hospital reading our filing -- can check the law.

Federal floors implemented here:

  * 240-day FAP application window .... 26 CFR 1.501(r)-1(b)(3) (the defined
    term "application period"), running from the FIRST POST-DISCHARGE BILLING
    STATEMENT, not the date of service. That distinction is the most common
    way patients lose the right.
    CORRECTED 2026-08-25 (STATUTE, wo6 citation audit): this module and
    `fronts.py` previously cited "26 CFR 1.501(r)-4(b)(1)(iv)" for the
    240-day figure. Verified against law.cornell.edu/cfr/text/26/1.501(r)-4:
    that subsection doesn't even exist -- (b)(1) only runs through (iii), and
    covers the FAP's basic-requirements list (emergency/medically-necessary
    care, wide publicity, etc.), not the application deadline at all. The
    "later of the 240th day after the first post-discharge billing
    statement" language lives in the definition of "application period" at
    1.501(r)-1(b)(3), confirmed verbatim against the primary source.
  * 120-day ECA moratorium ............ 26 CFR 1.501(r)-6(c)(3)(i) -- verified
    verbatim against law.cornell.edu/cfr/text/26/1.501(r)-6.
  * 30-day pre-ECA written notice ..... 26 CFR 1.501(r)-6(c)(4)(i) -- the
    30-day, written-notice requirement is in (c)(4)(i) specifically; the
    parent (c)(4) also covers the plain-language summary that must accompany
    the notice.
  * PPDR, 120 calendar days ........... 45 CFR 149.620(c)(1) -- verified
    verbatim ("postmarked within 120 calendar days of receiving the initial
    bill...") against law.cornell.edu/cfr/text/45/149.620. CORRECTED
    2026-08-25: previously cited only the parent "(c)".
  * Debt validation, 30 days .......... 12 CFR 1006.34(b); 15 USC 1692g(a)
  * Itemized bill, 30 days ............ 42 USC 1395b-7(b)

State charity-care overrides are in STATE_FAP_WINDOWS below. A state rule may
only ever be MORE generous than the federal floor; 1.501(r) sets a minimum, not
a ceiling. `_resolve_fap_window` enforces that invariant rather than trusting
the table.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

# --- federal constants (days) ---
FAP_WINDOW_DAYS = 240
ECA_MORATORIUM_DAYS = 120
ECA_NOTICE_DAYS = 30
PPDR_WINDOW_DAYS = 120
VALIDATION_WINDOW_DAYS = 30
ITEMIZED_BILL_DAYS = 30

# PPDR eligibility floor: the bill must exceed the Good Faith Estimate by at
# least this much. 45 CFR 149.620(a)(2)(ii) -- "substantially in excess" is
# defined as at least $400 above the expected charges. CORRECTED 2026-08-25
# (STATUTE, wo6 citation audit): previously cited "(b)"; verified against
# law.cornell.edu/cfr/text/45/149.620 -- the $400 definition is in the
# definitions paragraph at (a)(2)(ii), not (b).
PPDR_MIN_DELTA_CENTS = 400_00


@dataclass(frozen=True)
class StateFAPRule:
    """A state charity-care window that displaces the federal 240-day floor."""

    days: int | None  # None == no deadline at all
    citation: str
    # Some states run the clock from the latest of several events rather than
    # from the first billing statement.
    runs_from_latest_of: tuple[str, ...] = ("first_statement_date",)


# Only states the product claims to support. An unlisted state falls back to the
# federal floor -- we never guess at a state rule we have not read.
#
# Every citation below was re-verified against the primary source on
# 2026-08-25 (STATUTE, wo6 citation audit):
#   * CA -- confirmed verbatim: HSC §127405(e)(3) reads "A hospital shall not
#     impose time limits for applying for charity care or discounted
#     payments, nor deny eligibility based on the timing of a patient's
#     application." (leginfo.legislature.ca.gov)
#   * NY -- confirmed: PHL §2807-k(9-a)(e) requires the hospital's financial
#     assistance policy "permit patients to apply for assistance at any time
#     during the collection process" -- pinned to paragraph (e), the operative
#     no-deadline clause within 9-a.
#   * WA -- CORRECTED. The 2-year window is NOT in (5) -- (5) is where the
#     two-tier income table lives (see eligibility.py STATE_FLOORS["WA"]).
#     The actual 2-year retroactive-application right is RCW
#     70.170.060(10)(b): charity care may be "applied for ... within two
#     years of the time of service" on a showing of good-faith payment
#     efforts. (app.leg.wa.gov)
#   * NJ -- CORRECTED. N.J.A.C. §10:52-11.8 is "Income eligibility criteria
#     and documentation" (the sliding-scale discount tiers) and contains no
#     deadline at all. The 1-year window is at §10:52-11.13(b) ("at any time
#     up to one year from the date of outpatient service or inpatient
#     discharge"), reinforced by (c)(6). (law.cornell.edu/regulations/new-jersey)
STATE_FAP_WINDOWS: dict[str, StateFAPRule] = {
    # No application deadline may be imposed. Demo state -- the "safe" case.
    "CA": StateFAPRule(None, "Cal. Health & Safety Code §127405(e)(3)"),
    "NY": StateFAPRule(None, "N.Y. Pub. Health Law §2807-k(9-a)(e)"),
    "WA": StateFAPRule(730, "Wash. Rev. Code §70.170.060(10)(b)"),
    "NJ": StateFAPRule(365, "N.J. Admin. Code §10:52-11.13(b)"),
    # NOTE: Illinois is deliberately ABSENT from this table. Its 90-day clock
    # belongs to a separate state program, not to the federal FAP window --
    # see STATE_UNINSURED_DISCOUNTS below.
}


@dataclass(frozen=True)
class StateUninsuredDiscount:
    """A state-created discount right that runs ALONGSIDE the federal FAP.

    This is not a modification of the 26 CFR 1.501(r) window. It is a separate
    statutory entitlement with its own clock, its own eligibility test, and --
    critically -- its own scope: state acts of this kind typically bind ALL
    hospitals in the state, including for-profits that owe no 501(r) duty at all.
    """

    days: int
    citation: str
    # Documentation only -- NOT read by compute_deadlines or screen_eligibility.
    # The authoritative, enforced FPL ceiling lives in eligibility.py's
    # STATE_FLOORS (which is hospital-class-dependent for IL: general hospitals
    # get a 600% discount / 200% free floor, rural/critical-access hospitals
    # get 300% / 125% -- 210 ILCS 89/10. This field is kept at the rural/CAH
    # figure because that is the more conservative number to surface in a
    # docstring-adjacent constant nobody currently reads for enforcement).
    max_fpl_pct: int
    uninsured_only: bool
    runs_from_latest_of: tuple[str, ...]
    # Triggering events we have confirmed against the statute text. Anything in
    # runs_from_latest_of but not here is carried from the playbook and still
    # needs a primary-source check (agreement §2.2).
    confirmed_triggers: tuple[str, ...]


STATE_UNINSURED_DISCOUNTS: dict[str, StateUninsuredDiscount] = {
    # Hospital Uninsured Patient Discount Act. Binds every Illinois hospital,
    # for-profit included -- so an IL patient at a for-profit facility still has
    # this right even though no 501(r) obligation exists (the Act's "Hospital"
    # definition at 210 ILCS 89/5 carries no nonprofit/tax-exempt qualifier).
    # Window was 60 days as enacted and was amended to 90 days by P.A. 102-581,
    # eff. 1/1/2022, which also added the screening/public-program-denial
    # triggers alongside discharge/service.
    #
    # CORRECTED 2026-08-25 (STATUTE, wo6 citation audit): the deadline and its
    # four triggering events live in 210 ILCS 89/15(b), not /25(a) (Sec. 25 is
    # the Act's enforcement provision -- Attorney General powers -- and says
    # nothing about the application window). Verified verbatim against
    # ilga.gov: "Hospitals shall permit an uninsured patient to apply for a
    # discount within 90 days of the date of discharge, date of service,
    # completion of the screening under the Fair Patient Billing Act, or
    # denial of an application for a public health insurance program." All
    # four events named there are now confirmed_triggers below -- previously
    # only two of the four had been checked against the statute text.
    "IL": StateUninsuredDiscount(
        days=90,
        citation="210 ILCS 89/15(b) (Hospital Uninsured Patient Discount Act)",
        max_fpl_pct=300,
        uninsured_only=True,
        runs_from_latest_of=(
            "discharge_date",
            "service_date",
            "screening_date",
            "public_program_denial_date",
        ),
        confirmed_triggers=(
            "discharge_date",
            "service_date",
            "screening_date",
            "public_program_denial_date",
        ),
    ),
}


@dataclass(frozen=True)
class Deadline:
    """A single computed deadline.

    `front` matches the §3.1 fronts enum. `basis_date` is the event the clock
    ran from -- surfaced because "240 days from WHAT" is the whole ballgame.
    """

    front: str
    name: str
    due: date | None  # None == no deadline exists (e.g. CA charity care)
    basis_date: date | None
    basis_field: str
    citation: str
    days: int | None

    def days_remaining(self, today: date) -> int | None:
        """Days left, negative once blown. None when no deadline exists."""
        return None if self.due is None else (self.due - today).days

    def is_expired(self, today: date) -> bool:
        """A deadline with no due date can never expire."""
        return False if self.due is None else today > self.due

    def explain(self, today: date | None = None) -> str:
        """Human-readable arithmetic, for the audit log and the filed letter."""
        if self.due is None:
            return f"{self.name}: no deadline applies ({self.citation})."
        base = (
            f"{self.name}: due {self.due.isoformat()} -- {self.days} days from "
            f"{self.basis_field} ({self.basis_date}), per {self.citation}"
        )
        if today is None:
            return base + "."
        # days_remaining cannot be None here: the due-is-None case returned above.
        left = self.days_remaining(today)
        assert left is not None
        state = f"{left} days remaining" if left >= 0 else f"EXPIRED {abs(left)} days ago"
        return f"{base}. {state}."


def _resolve_fap_window(state: str) -> tuple[int | None, str]:
    """Return (days, citation) for the charity-care window in `state`.

    26 CFR 1.501(r) is a floor: a state rule that would give the patient LESS
    time than the federal 240 days cannot narrow the federal right, so we take
    the more generous of the two. A state may only add time or remove the
    deadline entirely.
    """
    rule = STATE_FAP_WINDOWS.get(state.strip().upper())
    if rule is None:
        return FAP_WINDOW_DAYS, "26 CFR 1.501(r)-1(b)(3)"
    if rule.days is None:
        return None, rule.citation
    if rule.days < FAP_WINDOW_DAYS:
        # State clock is shorter than the federal floor. The patient keeps the
        # federal window; we cite both so the reasoning is auditable.
        return (
            FAP_WINDOW_DAYS,
            "26 CFR 1.501(r)-1(b)(3) (federal floor; "
            f"{rule.citation} is shorter at {rule.days} days)",
        )
    return rule.days, rule.citation


def _latest_of(bill: dict, fields: tuple[str, ...]) -> tuple[date | None, str]:
    """Pick the latest populated date among `fields`.

    Illinois's 90-day clock (210 ILCS 89/15(b)) names four possible triggers --
    discharge, service, Fair Patient Billing Act screening, or public-program
    denial -- but the statute text itself does not say "latest of": it lists
    the four events without an explicit ordering rule. CORRECTED 2026-08-25
    (STATUTE, wo6 citation audit): a prior version of this docstring claimed
    "latest of ... is what the statute says", which overstates what the text
    supports. Taking the latest of the four is this codebase's own
    interpretation -- it matches Illinois Health & Hospital Association
    compliance guidance and is the patient-favorable reading (an earlier
    basis date would expire the right sooner) -- but it is a gloss, not a
    verbatim requirement, and is documented as such rather than presented as
    settled statutory text.

    Returns (None, "") when the case carries none of the fields, so the caller
    can degrade to "unknown" instead of inventing a date.
    """
    best: date | None = None
    best_field = ""
    for f in fields:
        v = bill.get(f)
        if isinstance(v, date) and (best is None or v > best):
            best, best_field = v, f
    return best, best_field


def compute_deadlines(
    bill: dict, state: str, today: date | None = None, *, insured: bool | None = None
) -> list[Deadline]:
    """Every statutory deadline implied by this bill. Public API, contract §3.5.

    `bill` follows the §3.1 `cases/{case_id}.bill` shape. Missing dates yield a
    Deadline with `due=None` rather than a guess -- an invented deadline is
    worse than an absent one, because the Strategist would file against it.

    Args:
        bill: the case's bill sub-document.
        state: two-letter state code, used for charity-care overrides.
        today: injected for testability; defaults to the real current date.
        insured: coverage status, when known. Keyword-only so the positional
            signature still matches contract §3.5. Some state programs are
            uninsured-only; when this is None the deadline is still emitted and
            flagged, because a missed state clock is unrecoverable and a
            spurious one is merely noise the Strategist can drop.

    Returns:
        Deadlines in the order the fronts should be considered. Debt validation
        sorts first when it applies -- see `select_fronts` for why the ordering
        is load-bearing.
    """
    del today  # reserved: no rule currently depends on the current date
    out: list[Deadline] = []

    # --- charity care (26 CFR 1.501(r)-4 / state overrides) ---
    window, citation = _resolve_fap_window(state)
    rule = STATE_FAP_WINDOWS.get(state.strip().upper())
    if rule is not None and rule.days is not None and rule.days >= FAP_WINDOW_DAYS:
        basis, basis_field = _latest_of(bill, rule.runs_from_latest_of)
    else:
        basis, basis_field = bill.get("first_statement_date"), "first_statement_date"

    if window is None:
        out.append(
            Deadline(
                "charity_care", "Charity care application", None, basis, basis_field, citation, None
            )
        )
    elif isinstance(basis, date):
        out.append(
            Deadline(
                "charity_care",
                "Charity care application",
                basis + timedelta(days=window),
                basis,
                basis_field,
                citation,
                window,
            )
        )
    else:
        out.append(
            Deadline(
                "charity_care",
                "Charity care application",
                None,
                None,
                basis_field,
                citation,
                window,
            )
        )

    # --- ECA moratorium: collections barred for 120 days from first statement ---
    first = bill.get("first_statement_date")
    if isinstance(first, date):
        out.append(
            Deadline(
                "charity_care",
                "Extraordinary collection actions barred until",
                first + timedelta(days=ECA_MORATORIUM_DAYS),
                first,
                "first_statement_date",
                "26 CFR 1.501(r)-6(c)(3)(i)",
                ECA_MORATORIUM_DAYS,
            )
        )

    # --- PPDR: 120 calendar days from the initial bill (45 CFR 149.620(c)(1)) ---
    if isinstance(first, date):
        out.append(
            Deadline(
                "ppdr",
                "Patient-provider dispute resolution",
                first + timedelta(days=PPDR_WINDOW_DAYS),
                first,
                "first_statement_date",
                "45 CFR 149.620(c)(1)",
                PPDR_WINDOW_DAYS,
            )
        )

    # --- debt validation: 30 days from the validation notice ---
    notice = bill.get("validation_notice_date")
    if isinstance(notice, date):
        out.append(
            Deadline(
                "debt_validation",
                "Written dispute of the debt",
                notice + timedelta(days=VALIDATION_WINDOW_DAYS),
                notice,
                "validation_notice_date",
                "12 CFR 1006.34(b); 15 USC 1692g(a)",
                VALIDATION_WINDOW_DAYS,
            )
        )

    # --- state uninsured-discount programs, PARALLEL to the federal FAP ---
    # These do not displace the 501(r) window; a patient can hold both rights
    # at once with different clocks. Emitting only the longer one silently
    # drops the deadline that actually expires first.
    discount = STATE_UNINSURED_DISCOUNTS.get(state.strip().upper())
    if discount is not None and not (discount.uninsured_only and insured is True):
        basis_d, basis_d_field = _latest_of(bill, discount.runs_from_latest_of)
        unconfirmed = basis_d_field and basis_d_field not in discount.confirmed_triggers
        citation = discount.citation
        if unconfirmed:
            citation += f" [trigger {basis_d_field!r} unverified against statute text]"
        out.append(
            Deadline(
                "charity_care",
                f"{state.strip().upper()} uninsured discount application",
                basis_d + timedelta(days=discount.days) if isinstance(basis_d, date) else None,
                basis_d,
                basis_d_field or "discharge_date",
                citation,
                discount.days,
            )
        )

    return out
