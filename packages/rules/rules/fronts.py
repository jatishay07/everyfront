"""Front selector -- the decision tree over the four legal fronts (§1.2).

Working agreement §2.1: pure functions, zero LLM calls. This module does not
reimplement deadline math or eligibility math -- it composes `deadlines.py`
(work order 1) and `eligibility.py` (work order 2), which already hold that
logic and its citations. Front selection is orchestration, not a fifth set of
rules.

THE ORDERING IS LOAD-BEARING. When a case is `in_collections` and within 30
days of its validation notice, `select_fronts` returns `debt_validation`
FIRST, ahead of every other applicable front, and appends a note to every
other applicable front's `.reason` explaining that it is sequenced behind the
dispute. This mirrors the real legal effect of 12 CFR 1006.34 / 15 USC
1692g(a): a timely written dispute obliges the collector to cease collection
activity until it produces verification, so nothing else should be filed --
and possibly none of it should even be *sent* -- until that resolves. See
`tests/test_fronts.py::TestOrdering` for the regression this protects.

Front-by-front basis:

  * PPDR ................. 45 CFR 149.620(a)(2)(ii) (>= $400 "substantially in
    excess" of the Good Faith Estimate) and (c)(1) (120-day window); gated on
    uninsured/self-pay, per 45 CFR 149.610(a) (definition of an "uninsured
    (or self-pay) individual"). CORRECTED 2026-08-25 (STATUTE, wo6 citation
    audit): previously cited (b) and (c); verified against
    law.cornell.edu/cfr/text/45/149.620 -- the $400 definition sits in the
    definitions paragraph at (a)(2)(ii), and the 120-day filing requirement
    at (c)(1) specifically.
  * Charity care ......... 26 CFR 1.501(r)-4(b)(2) (hospital's own published
    FPL thresholds, screened by `eligibility.screen_eligibility`) and the FAP
    application window from `deadlines.compute_deadlines` (26 CFR
    1.501(r)-1(b)(3), not -4(b)(1)(iv) -- see that module's docstring for the
    correction). A for-profit hospital owes no 1.501(r) duty at all -- 26 CFR
    1.501(r)-1(b)(18) limits the whole subchapter to a "hospital
    organization" as there defined. CORRECTED 2026-08-25: previously cited
    (b)(20); verified against law.cornell.edu/cfr/text/26/1.501(r)-1 -- the
    "hospital organization" definition is at (b)(18).
  * Debt validation ...... 12 CFR 1006.34(b); 15 USC 1692g(a) -- 30 days from
    the validation notice, and it runs FIRST (above).
  * Audit ................ 42 USC 1395b-7(b) (itemized statement) and 45 CFR
    Part 180 (hospital price transparency) -- performed whenever an itemized
    bill is on file, unconditionally.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date

from rules.deadlines import PPDR_MIN_DELTA_CENTS, compute_deadlines
from rules.eligibility import screen_eligibility

# Canonical order (matches the §1.2 fronts table). `select_fronts` reorders
# around this only to put an applicable debt_validation first.
FRONT_ORDER: tuple[str, ...] = ("charity_care", "ppdr", "debt_validation", "audit")

_DEBT_VALIDATION_CITATION = "12 CFR 1006.34(b); 15 USC 1692g(a)"


@dataclass(frozen=True)
class FrontDecision:
    """One front's applicability, with the reasoning and the law behind it.

    `front` matches the §3.1 `fronts[].front` enum. `deadline` is the due date
    that governs *this* front specifically (None when the front carries no
    deadline of its own, e.g. audit, or when the underlying date is missing).
    """

    front: str
    applicable: bool
    reason: str
    citation: str
    deadline: date | None = None

    def explain(self) -> str:
        """Human-readable version, for the audit log and the strategist's plan."""
        state = "applicable" if self.applicable else "not applicable"
        tail = f" Deadline: {self.deadline.isoformat()}." if self.deadline is not None else ""
        return f"{self.front}: {state} -- {self.reason} ({self.citation}).{tail}"


def _get(case: dict, key: str) -> dict:
    val = case.get(key)
    return val if isinstance(val, dict) else {}


def _is_plain_int(value: object) -> bool:
    """True for a real int, false for bool (a bool is technically an int) or anything else."""
    return isinstance(value, int) and not isinstance(value, bool)


def _income_cents(patient: dict) -> object:
    """Read the patient's annual income in cents.

    §3.1 names the field `annual_income` without a `_cents` suffix, unlike
    every other money field in this repo's contracts (`amount_cents`,
    `gfe_amount_cents`, `savings_found_cents`). We treat that as the same
    cents convention rather than dollars -- flagged here as an assumption the
    Strategist should confirm; `annual_income_cents` is accepted first if a
    caller already disambiguated it.
    """
    if "annual_income_cents" in patient:
        return patient.get("annual_income_cents")
    return patient.get("annual_income")


def _first_deadline(bill: dict, state: str, front: str, name: str | None, insured: bool | None):
    for d in compute_deadlines(bill, state, insured=insured):
        if d.front == front and (name is None or d.name == name):
            return d
    return None


def _select_charity_care(case: dict, today: date) -> FrontDecision:
    patient = _get(case, "patient")
    bill = _get(case, "bill")
    hospital = _get(case, "hospital")
    state = str(patient.get("state") or "").strip().upper()
    insured = patient.get("insured")

    if hospital.get("nonprofit", True) is False:
        return FrontDecision(
            "charity_care",
            False,
            "hospital is for-profit; no 26 CFR 1.501(r) charity-care obligation applies",
            "26 CFR 1.501(r)-1(b)(18)",
        )

    income = _income_cents(patient)
    household = patient.get("household_size")
    if not _is_plain_int(income) or not _is_plain_int(household) or not state:
        return FrontDecision(
            "charity_care",
            False,
            "insufficient patient data (income, household size, or state) to screen eligibility",
            "26 CFR 1.501(r)-4(b)(2)",
        )

    elig = screen_eligibility(income, household, state, hospital)
    fap_deadline = _first_deadline(bill, state, "charity_care", "Charity care application", insured)
    due = fap_deadline.due if fap_deadline is not None else None
    window_open = due is None or not fap_deadline.is_expired(
        today
    )  # None due => no deadline exists
    fap_citation = fap_deadline.citation if fap_deadline is not None else "26 CFR 1.501(r)-1(b)(3)"

    if elig.determination == "unknown":
        return FrontDecision(
            "charity_care",
            False,
            f"eligibility unknown: {elig.explain()}",
            "26 CFR 1.501(r)-4(b)(2)",
            due,
        )
    if elig.determination == "ineligible":
        return FrontDecision(
            "charity_care",
            False,
            f"income exceeds every published threshold: {elig.explain()}",
            "26 CFR 1.501(r)-4(b)(2)",
            due,
        )
    if not window_open:
        return FrontDecision(
            "charity_care", False, "the FAP application window has expired", fap_citation, due
        )
    return FrontDecision("charity_care", True, elig.explain(), "26 CFR 1.501(r)-4(b)(2)", due)


def _select_ppdr(case: dict, today: date) -> FrontDecision:
    patient = _get(case, "patient")
    bill = _get(case, "bill")
    state = str(patient.get("state") or "").strip().upper()
    insured = patient.get("insured")

    if insured is not False:
        reason = "coverage status unknown" if insured is None else "patient is insured"
        return FrontDecision(
            "ppdr",
            False,
            f"PPDR requires an uninsured/self-pay patient ({reason})",
            "45 CFR 149.610(a)",
        )

    gfe = bill.get("gfe_amount_cents")
    amount = bill.get("amount_cents")
    if not _is_plain_int(gfe) or not _is_plain_int(amount):
        return FrontDecision(
            "ppdr",
            False,
            "no Good Faith Estimate on file to compare against the bill",
            "45 CFR 149.620(a)(2)(ii)",
        )

    delta = amount - gfe
    if delta < PPDR_MIN_DELTA_CENTS:
        return FrontDecision(
            "ppdr",
            False,
            f"billed amount exceeds the GFE by ${delta / 100:,.2f}, "
            "below the $400 'substantially in excess' floor",
            "45 CFR 149.620(a)(2)(ii)",
        )

    ppdr_deadline = _first_deadline(bill, state, "ppdr", None, insured)
    if ppdr_deadline is None or ppdr_deadline.due is None:
        return FrontDecision(
            "ppdr",
            False,
            "no initial-bill date on file; cannot start the 120-day PPDR clock",
            "45 CFR 149.620(c)(1)",
        )
    if ppdr_deadline.is_expired(today):
        return FrontDecision(
            "ppdr",
            False,
            "the 120-day PPDR initiation window has expired",
            ppdr_deadline.citation,
            ppdr_deadline.due,
        )

    return FrontDecision(
        "ppdr",
        True,
        f"uninsured with a bill ${delta / 100:,.2f} above the Good Faith Estimate (>= $400 floor)",
        "45 CFR 149.620(a)(2)(ii), (c)(1)",
        ppdr_deadline.due,
    )


def _select_debt_validation(case: dict, today: date) -> FrontDecision:
    patient = _get(case, "patient")
    bill = _get(case, "bill")
    state = str(patient.get("state") or "").strip().upper()

    if not bill.get("in_collections"):
        return FrontDecision(
            "debt_validation",
            False,
            "account is not reported in collections",
            _DEBT_VALIDATION_CITATION,
        )

    notice = bill.get("validation_notice_date")
    if not isinstance(notice, date):
        return FrontDecision(
            "debt_validation",
            False,
            "in collections but no validation-notice date is on file",
            _DEBT_VALIDATION_CITATION,
        )

    dv_deadline = _first_deadline(bill, state, "debt_validation", None, None)
    due = dv_deadline.due if dv_deadline is not None else None
    if dv_deadline is not None and dv_deadline.is_expired(today):
        return FrontDecision(
            "debt_validation",
            False,
            "the 30-day written-dispute window has closed",
            _DEBT_VALIDATION_CITATION,
            due,
        )

    return FrontDecision(
        "debt_validation",
        True,
        "account is in collections and within 30 days of the validation notice -- a timely "
        "written dispute forces verification and pauses collection",
        _DEBT_VALIDATION_CITATION,
        due,
    )


def _has_itemized_bill(case: dict) -> bool:
    documents = case.get("documents")
    if isinstance(documents, list):
        for doc in documents:
            if isinstance(doc, dict) and doc.get("type") == "itemized_bill":
                return True
    bill = _get(case, "bill")
    items = bill.get("line_items")
    return bool(items)


def _select_audit(case: dict) -> FrontDecision:
    if _has_itemized_bill(case):
        return FrontDecision(
            "audit",
            True,
            "itemized bill on file; a billing audit is always performed",
            "42 USC 1395b-7(b); 45 CFR Part 180",
        )
    return FrontDecision("audit", False, "no itemized bill on file yet", "42 USC 1395b-7(b)")


def select_fronts(case: dict, *, today: date | None = None) -> list[FrontDecision]:
    """Decide which of the four legal fronts apply to `case`. Contract §3.5.

    Args:
        case: a `cases/{case_id}` document (§3.1), with `patient` and `bill`
            sub-documents, plus two joins the Strategist is expected to have
            already made: `hospital` (a `hospitals/{ein}` record, §3.1) and
            optionally `documents` (a list of `cases/{id}/documents/{doc_id}`
            records, used only to detect an itemized bill).
        today: injected for testability; defaults to the real current date.

    Returns:
        Exactly one `FrontDecision` per front in `FRONT_ORDER`, reordered so
        an applicable `debt_validation` comes first -- see the module
        docstring. Never raises: missing or malformed data degrades every
        front to `applicable=False` with a `reason` explaining why, the same
        philosophy `screen_eligibility` uses for "unknown".
    """
    if today is None:
        today = date.today()

    decisions = {
        "charity_care": _select_charity_care(case, today),
        "ppdr": _select_ppdr(case, today),
        "debt_validation": _select_debt_validation(case, today),
        "audit": _select_audit(case),
    }

    order = list(FRONT_ORDER)
    if decisions["debt_validation"].applicable:
        order.remove("debt_validation")
        order.insert(0, "debt_validation")
        note = (
            f" Sequenced after debt validation ({_DEBT_VALIDATION_CITATION}), which must be "
            "resolved first -- a pending validation dispute pauses collection activity."
        )
        for front in FRONT_ORDER:
            if front == "debt_validation":
                continue
            d = decisions[front]
            if d.applicable:
                decisions[front] = replace(d, reason=d.reason + note)

    return [decisions[f] for f in order]
