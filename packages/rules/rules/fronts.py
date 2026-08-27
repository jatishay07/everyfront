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
    CORRECTED 2026-08-26 (STATUTE, wo7 -- ef-2026-0006): `hospital.get(
    "nonprofit", True)` used to default an UNRESOLVED hospital (Lookup could
    not identify one -- no EIN, no name, `hospital == {}`) to "nonprofit",
    which marked charity_care APPLICABLE for a facility nobody could name.
    26 CFR 1.501(r) attaches to one specific "hospital organization"
    (1.501(r)-1(b)(18)) -- if we do not know which hospital, we cannot
    assert what it owes. `nonprofit` must now be the literal `True` to
    proceed; anything else (missing, `None`, or a resolved-but-unconfirmed
    record) refuses rather than assumes. See `_select_charity_care` below.
  * Debt validation ...... 12 CFR 1006.34(b); 15 USC 1692g(a) -- 30 days from
    the validation notice, and it runs FIRST (above).
  * Audit ................ 42 USC 1395b-7(b) (itemized statement) and 45 CFR
    Part 180 (hospital price transparency) -- performed whenever an itemized
    bill was actually extracted into usable line items.
    CORRECTED 2026-08-26 (STATUTE, wo7 -- ef-2026-0006): this front used to
    go applicable=True off the mere presence of a `documents[].type ==
    "itemized_bill"` tag, even when zero line items had actually been
    extracted from it (the Reader failed to parse the document). Combined
    with `audit.audit_line_items` returning `[]` for an empty input, that
    silently rendered "we could not read this bill" identically to "we read
    it and it was clean" -- an absence dressed up as a finding. `_select_audit`
    now distinguishes three states: no itemized-bill evidence at all; an
    itemized-bill document on file but no usable line items extracted from
    it (a failed/incomplete read -- NOT applicable, and said so); and an
    itemized bill with actual line items (applicable, audit performed).

NAME THE MISSING FACT (ADDED 2026-08-26, STATUTE, wo8). Every "cannot
determine" reason in this module now names the SPECIFIC input it does not
have, rather than listing the category of inputs that might be missing, and
distinguishes "never stated in any document" from "on file but unreadable"
-- the same distinction `_has_itemized_bill_document` vs
`_usable_line_item_count` already drew for documents, extended to patient and
bill facts. See the block comment above `_patient_fact_status` for the live
case that forced it. No applicability outcome changed; only the reporting.
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


# --- naming the missing fact (ADDED 2026-08-26, STATUTE, wo8) -------------
#
# THE DEFECT THIS SECTION EXISTS TO PREVENT. A real emailed bill produced a
# case where the documents established provider, state (CA), self-pay status,
# income ($32,000/yr), bill total and five line items -- and left exactly ONE
# fact unstated anywhere: household size. `_select_charity_care` correctly
# declined, but said "insufficient patient data (income, household size, or
# state) to screen eligibility". That sentence is true and useless: it lists
# the category of things that might be missing instead of reporting the gap
# that actually exists. Income WAS established. State WAS established. And
# household size is decisive -- at household 3, $32,000 is 117% of the 2026
# FPL ($27,320) and clears California's 400% statutory floor (Cal. Health &
# Safety Code §127405(a)(1)(A)), erasing the whole bill. A patient told
# "insufficient patient data" learns nothing; a patient told "household size
# was not stated in any document on file" knows what to send next.
#
# Every "cannot determine" path below therefore names the specific input(s)
# it does not have, and -- extending the rigour `_has_itemized_bill_document`
# vs `_usable_line_item_count` already applies to DOCUMENTS -- distinguishes
# "the fact was never stated" from "we have a value but cannot use it".
# Applicability outcomes are unchanged; only the reporting is.

_WHY_INCOME = (
    "26 CFR 1.501(r)-4(b)(2) screens household income against the hospital's published "
    "percentage-of-poverty thresholds"
)
_WHY_HOUSEHOLD = (
    "the federal poverty level the hospital's thresholds are a percentage of is computed "
    "per household size (2026 HHS guidelines, 91 FR 1797)"
)
_WHY_STATE = (
    "the patient's state sets any statutory floor beneath the hospital's own thresholds "
    "(e.g. Cal. Health & Safety Code §127405(a)(1)(A))"
)


def _and_join(labels: list[str]) -> str:
    """ "a" / "a and b". Never sees more than two items -- see `_patient_fact_status`."""
    return " and ".join(labels)


@dataclass(frozen=True)
class _PatientFact:
    """One input a charity-care screen needs, and whether we actually have it.

    `gap` is None when the fact is established. `why` is the legal reason the
    screen cannot proceed without it -- kept per-fact so the reason names only
    the law that is actually load-bearing for the gap in front of us.
    """

    label: str
    gap: str | None
    why: str


def _patient_fact_status(patient: dict) -> list[_PatientFact]:
    """Classify the three patient facts a charity-care screen needs.

    The three are income, household size and state -- exactly the inputs
    `screen_eligibility` (contract §3.5) takes. Each lands in one of three
    states, never collapsed:

      * established -- a usable value is on file;
      * never stated -- the field is absent or null, i.e. no document on file
        ever asserted it (this is the one a patient can FIX, by sending the
        document that states it);
      * on file but unreadable -- a value exists and is not usable, which is
        a failed extraction, not a missing document, and must not be reported
        as though the patient never provided it.

    At most two of the three can be established while any gap exists, so
    `_and_join` never needs an Oxford comma.
    """
    facts: list[_PatientFact] = []

    for label, value, why in (
        ("annual household income", _income_cents(patient), _WHY_INCOME),
        ("household size", patient.get("household_size"), _WHY_HOUSEHOLD),
    ):
        if _is_plain_int(value):
            facts.append(_PatientFact(label, None, why))
        elif value is None:
            facts.append(
                _PatientFact(label, f"{label} was not stated in any document on file", why)
            )
        else:
            facts.append(
                _PatientFact(
                    label,
                    f"{label} is on file but unreadable -- recorded as "
                    f"{type(value).__name__}, not a whole number",
                    why,
                )
            )

    # `state` has no "unreadable" state: `_select_charity_care` stringifies
    # whatever is there, so any non-empty value is usable. Kept as two cases
    # rather than inventing a third that cannot occur.
    if str(patient.get("state") or "").strip():
        facts.append(_PatientFact("state of residence", None, _WHY_STATE))
    else:
        facts.append(
            _PatientFact(
                "state of residence",
                "state of residence was not stated in any document on file",
                _WHY_STATE,
            )
        )

    return facts


def _patient_data_gap_reason(facts: list[_PatientFact]) -> str:
    """The `FrontDecision.reason` for a charity-care screen that cannot run.

    Names the actual gap(s) and, when some facts ARE established, says so --
    "it is the only missing input" is the sentence that tells a patient which
    one document to send. Keeps the law attached: 26 CFR 1.501(r)-4(b)(2) (the
    screen itself), 91 FR 1797 (the poverty table household size indexes) and
    the state-floor cite, each surfaced only when the fact it governs is the
    one missing.
    """
    gaps = [f for f in facts if f.gap is not None]
    established = [f.label for f in facts if f.gap is None]

    body = "cannot screen charity-care eligibility: " + "; ".join(f.gap or "" for f in gaps)
    if established:
        names = _and_join(established)
        verb = "is" if len(established) == 1 else "are"
        if len(gaps) == 1:
            body += f". It is the only missing input -- {names} {verb} established"
        else:
            body += f" ({names} {verb} established)"
    else:
        body += ". No patient fact this screen needs is established"

    needed = "Why it is needed" if len(gaps) == 1 else "Why they are needed"
    return f"{body}. {needed}: " + "; ".join(f.why for f in gaps) + "."


def _unusable_value_phrase(value: object, label: str, unit: str) -> str | None:
    """None when `value` is a usable whole number; otherwise a phrase naming
    the gap, distinguishing "never provided" from "on file but unreadable".

    Same three-state discipline as `_patient_fact_status`, for the money
    fields on the bill (`amount_cents`, `gfe_amount_cents`, §3.1).
    """
    if _is_plain_int(value):
        return None
    if value is None:
        return f"no {label} is on file"
    return f"the {label} on file is unreadable -- recorded as {type(value).__name__}, not {unit}"


def _select_charity_care(case: dict, today: date) -> FrontDecision:
    patient = _get(case, "patient")
    bill = _get(case, "bill")
    hospital = _get(case, "hospital")
    state = str(patient.get("state") or "").strip().upper()
    insured = patient.get("insured")

    nonprofit = hospital.get("nonprofit")
    if nonprofit is False:
        return FrontDecision(
            "charity_care",
            False,
            "hospital is for-profit; no 26 CFR 1.501(r) charity-care obligation applies",
            "26 CFR 1.501(r)-1(b)(18)",
        )
    if nonprofit is not True:
        return FrontDecision(
            "charity_care",
            False,
            "hospital's tax-exempt (nonprofit) status is not established -- the hospital "
            "record was never resolved to a specific facility, and 26 CFR 1.501(r) attaches "
            "to a named 'hospital organization' (1.501(r)-1(b)(18)); an unidentified hospital "
            "cannot be asserted to owe a charity-care obligation",
            "26 CFR 1.501(r)-1(b)(18)",
        )

    income = _income_cents(patient)
    household = patient.get("household_size")
    # The guard and the reason are derived from ONE source of truth
    # (`_patient_fact_status`) so the sentence can never drift from the test
    # that produced it -- the previous version repeated the predicate here and
    # described it, vaguely, in a separate literal.
    facts = _patient_fact_status(patient)
    if any(f.gap is not None for f in facts):
        return FrontDecision(
            "charity_care",
            False,
            _patient_data_gap_reason(facts),
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
        # `explain()` already opens "Eligibility unknown: " and then names the
        # specific gap (which threshold is missing, which state floor did not
        # resolve). Prefixing it again produced "eligibility unknown:
        # Eligibility unknown: ..." on screen and added no information.
        return FrontDecision(
            "charity_care",
            False,
            elig.explain(),
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
        # Three distinct causes, previously two. `insured is None` (absent or
        # null) means no document on file states a coverage status -- fixable
        # by sending an insurance card or a self-pay statement. A non-bool
        # value is a failed extraction, NOT a statement that the patient is
        # insured; the old `else` branch asserted "patient is insured" for
        # any garbage value, which is the same fabricate-a-fact defect
        # ef-2026-0006 hit on the hospital record.
        if insured is True:
            detail = "the documents on file state this patient is insured"
        elif insured is None:
            detail = "insurance status was not stated in any document on file"
        else:
            detail = (
                "insurance status is on file but unreadable -- recorded as "
                f"{type(insured).__name__}, not a yes/no value, so self-pay status is "
                "not established"
            )
        return FrontDecision(
            "ppdr",
            False,
            f"PPDR requires an uninsured (self-pay) patient: {detail}",
            "45 CFR 149.610(a)",
        )

    gfe = bill.get("gfe_amount_cents")
    amount = bill.get("amount_cents")
    # Two inputs, one comparison. The old single sentence blamed the Good
    # Faith Estimate even when the GFE was on file and the BILL TOTAL was the
    # missing number (live on fixture case_06, where both are null and only
    # the GFE was named). 45 CFR 149.620(a)(2)(ii) compares one against the
    # other; either one absent defeats it, and the patient is told which.
    money_gaps = [
        phrase
        for phrase in (
            _unusable_value_phrase(gfe, "Good Faith Estimate amount", "a whole-cents amount"),
            _unusable_value_phrase(amount, "billed amount", "a whole-cents amount"),
        )
        if phrase is not None
    ]
    if money_gaps:
        return FrontDecision(
            "ppdr",
            False,
            "cannot compare the bill against the Good Faith Estimate: " + "; ".join(money_gaps),
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

    in_collections = bill.get("in_collections")
    if not in_collections:
        # "not reported in collections" covered both a recorded `False` and a
        # field nobody ever filled in. Only one of those is fixable by the
        # patient, and it is the one that says what to send.
        if in_collections is None:
            detail = (
                "no collection status is recorded on the bill -- a collection notice or a "
                "debt-validation notice on file would establish it"
            )
        else:
            detail = "the bill record states this account is not in collections"
        return FrontDecision(
            "debt_validation",
            False,
            detail,
            _DEBT_VALIDATION_CITATION,
        )

    notice = bill.get("validation_notice_date")
    if not isinstance(notice, date):
        # 15 USC 1692g(a) runs the 30 days from the validation NOTICE, so the
        # notice's date is the one fact this front needs and cannot infer.
        if notice is None:
            detail = (
                "the account is in collections but no validation-notice date is on file -- "
                "the 30-day dispute window runs from that notice"
            )
        else:
            detail = (
                "the account is in collections and a validation-notice date is on file but "
                f"unreadable -- recorded as {type(notice).__name__}, not a date"
            )
        return FrontDecision(
            "debt_validation",
            False,
            detail,
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


def _has_itemized_bill_document(case: dict) -> bool:
    """True when a document tagged `itemized_bill` is on file.

    This says nothing about whether extraction from that document actually
    produced anything usable -- see `_usable_line_item_count`. Kept separate
    so `_select_audit` can tell "no such document was ever provided" apart
    from "we have it, but could not read it."
    """
    documents = case.get("documents")
    if isinstance(documents, list):
        for doc in documents:
            if isinstance(doc, dict) and doc.get("type") == "itemized_bill":
                return True
    return False


def _usable_line_item_count(bill: dict) -> int:
    """How many of `bill['line_items']` carry at least a usable `code`.

    Mirrors `audit.py`'s own line-validity rule (a dict with a non-empty
    string `code`) -- kept in lockstep by hand rather than importing that
    module's private helper, since this is the only place `fronts.py` needs
    it.
    """
    items = bill.get("line_items")
    if not isinstance(items, list):
        return 0
    count = 0
    for item in items:
        code = item.get("code") if isinstance(item, dict) else None
        if isinstance(code, str) and code.strip():
            count += 1
    return count


def _select_audit(case: dict) -> FrontDecision:
    bill = _get(case, "bill")
    usable = _usable_line_item_count(bill)
    if usable > 0:
        return FrontDecision(
            "audit",
            True,
            f"itemized bill on file with {usable} usable line item(s); a billing audit is "
            "always performed",
            "42 USC 1395b-7(b); 45 CFR Part 180",
        )
    if _has_itemized_bill_document(case):
        return FrontDecision(
            "audit",
            False,
            "an itemized bill document is on file but no usable line items were extracted "
            "from it -- this is a failed or incomplete read, not a clean bill, and must not "
            "be reported as an audit that found nothing",
            "42 USC 1395b-7(b)",
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
