"""Fold every document's extraction into the canonical `patient` / `bill`.

WHY THIS MODULE EXISTS. Reader classifies and extracts one document at a
time and writes the result to `cases/{id}/documents/{doc_id}.extracted`
(§3.1). Nothing carried those facts up into `cases/{id}.patient` and
`cases/{id}.bill` -- which is where `rules.select_fronts`, `compute_deadlines`
and `screen_eligibility` actually read from. `pipeline._merge_bill_fields`
carried seven bill SCALARS and stopped: no `line_items`, nothing about the
patient at all.

Measured live on a real emailed bill (case `case-1a0412ccfef90917`,
2026-08-26): all three PDFs classified correctly, all three extracted
cleanly, and the case still came out with `patient` entirely null and a
`bill` with no `line_items`. That produced a *contradiction inside one
case* -- the kind PROOF's banner reconciliation exists to catch:

  * the Auditor scans `documents[].extracted.line_items` directly
    (`auditor.all_line_items`), found `2 identical lines for 80053`, and
    booked $210.00 into `audit_findings_cents`;
  * `rules.fronts._select_audit` reads `case["bill"]["line_items"]`, saw
    nothing, and reported the audit front `applicable=False` -- "an itemized
    bill document is on file but no usable line items were extracted from
    it".

Both were behaving exactly as written. The demo path never showed it because
`/demo/inject_bill` writes a fixture's `patient`/`bill` straight onto the
case, so the merge was never on the critical path and every test passed.

THE FOUR RULES THIS MODULE IS BUILT ON
--------------------------------------

**1. Never invent.** A fact no document states stays absent. This project's
worst defect (HANDOFF #5) was a fabricated EIN and epoch dates that the Clock
then turned into real regulatory deadlines; declining rather than guessing is
now a demo asset. So: no field is ever defaulted. `household_size` is not 1,
`insured` is not False, a missing income is not 0. `_UNSOURCEABLE` below names
the facts NO document type in §3.1 can establish, and they are never written
here at all.

**2. Precedence is explicit, per field, and independent of arrival order.**
This function is a pure function of *every* document on file, not an
incremental fold of one document into whatever the last one left behind --
that older shape made "which document wins" depend on Pub/Sub delivery order,
which is not a decision anybody made. `_SPECS` names, for each canonical
field, the document types that may establish it, **highest authority first**.
The reasoning is per field and written next to it; the recurring principle is
that a document is authoritative for the thing it *is*: an itemized bill for
what was charged, a Good Faith Estimate for the estimate and for the 45 CFR
149.610 coverage statement it is issued under, a collection notice for the
collector and the validation-notice date, a pay stub for income.

**3. A merge never overwrites a better-established fact with a weaker one.**
Two separate guards:

  * *Weaker value:* a null, an empty string, a non-positive money amount, a
    non-ISO date, an empty line-item table and a bogus state code are all
    rejected before precedence is even consulted, so a failed extraction can
    never displace a good one from a lower-precedence document. (The
    extractor returns `0` and `""` for "not found" as well as `null` -- that
    sentinel leak is exactly how a $2,625 bill once became `amount=0`; see
    `pipeline._merge_bill_fields`'s history.)
  * *Weaker source:* `bill` fields are a projection of the documents and
    documents win outright -- a re-read of a corrected statement MUST be able
    to move the amount. `patient` fields are different: no medical-billing
    document is a record of a *person*, it only mentions one. A human intake
    form (§3.3 `POST /cases`) or a curated fixture is the stronger source, so
    the merge only FILLS A GAP in `patient` and never replaces a value already
    on the case. The Verifier (§4 persona 5) is what surfaces a
    document-vs-case disagreement (income +/-15%), deliberately and visibly,
    instead of this module resolving it in silence.

**4. Idempotent, and convergent.** §2.3: re-analysis runs this repeatedly.
The result is a pure function of `(case, documents)`; running it again over
an unchanged corpus produces an identical merged value, so `merge()` returns
an empty patch and writes nothing. The `patient` fill-a-gap rule is what makes
that true in both directions -- were it "documents always win", a case whose
patient facts came from a human form would be rewritten on every redelivery.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date

#: §3.1 `documents[].type` values this system RECEIVES. Anything else (the
#: `generated_application` / `generated_letter` this system produces, or an
#: unclassified `""`) contributes nothing: a filing we wrote is not evidence
#: about the bill, and an unread document has no extraction to offer.
INCOMING_DOC_TYPES = frozenset(
    {"bill", "itemized_bill", "denial_letter", "collection_notice", "gfe", "income_proof"}
)

#: The §3.1 `documents[].type` the patient's own words arrive as -- the body
#: of the email they attached their bill to (`services/intake`), proposed as a
#: contract amendment in this PR's HANDOFF to FORGE.
#:
#: **IT IS DELIBERATELY NOT IN `INCOMING_DOC_TYPES` ABOVE, AND THAT ABSENCE IS
#: THE WHOLE DESIGN.** `_usable_extraction` gates on that set, so nothing
#: extracted from a patient statement can reach `cases/{id}.patient` or
#: `.bill` through this module, ever, by construction rather than by every
#: `_SPECS` entry remembering to leave it out. A pay stub PROVES an income; a
#: sentence someone typed CLAIMS one, and the two must not become
#: indistinguishable the moment they are both stored on the same case.
#:
#: What happens to it instead lives in `agent_core.statedfacts`: a strictly
#: third tier, below a human-entered value and below a document, applied as a
#: derived overlay when the front selector is called and never written into
#: `patient`. Rule 3 above says a merge never overwrites a better-established
#: fact with a weaker one; this is that rule taken one step further, to a
#: source so weak it does not get to occupy the field at all.
PATIENT_STATEMENT_TYPE = "patient_statement"

#: The canonical patient facts NO §3.1 document type can establish, with the
#: reason -- reported to the case's own audit trail so the gap is named
#: precisely rather than left as a generic "insufficient patient data".
#:
#: `household_size` is the honest boundary of this whole pipeline. An FPL
#: percentage is a function of household size (`rules.eligibility`), so
#: charity care genuinely cannot be screened without it -- and a pay stub
#: states an employee's earnings, never who else lives in their home. A bill,
#: a GFE, a denial letter and a collection notice say nothing about it either.
#: Guessing "1" would manufacture the single most consequential number in a
#: charity-care determination out of nothing, which is defect #5 wearing a
#: different hat. It stays unknown until a human supplies exactly this one
#: fact (§3.3 `POST /cases`, or CANVAS's intake form) -- and rule 3 above
#: guarantees the merge will not then overwrite what they entered.
#:
#: HANDOFF -> STATUTE (persona 3), `packages/rules/rules/fronts.py`,
#: `_select_charity_care`. Not edited here: §0 rule 2, that package is yours.
#:
#: With this merge in place, `case-1a0412ccfef90917` reaches `select_fronts`
#: with income $32,000.00 and state CA both KNOWN and only `household_size`
#: missing -- and the front still refuses with:
#:
#:     "insufficient patient data (income, household size, or state) to
#:      screen eligibility"
#:
#: One string for three different absences. It sends a human to check three
#: facts when exactly one is missing, and it reads to a judge as "we could not
#: read this bill" on a case where we read it perfectly. The refusal itself is
#: right -- an FPL percentage is a function of household size and cannot be
#: computed without it (that is this pipeline's honest boundary, and it must
#: stay). Only the naming is wrong.
#:
#: Proposed: report only what is actually absent, e.g.
#:
#:     missing = [name for name, ok in (("annual income", _is_plain_int(income)),
#:                                      ("household size", _is_plain_int(household)),
#:                                      ("state", bool(state))) if not ok]
#:     reason = ("cannot screen eligibility: " + " and ".join(missing) +
#:               (" was" if len(missing) == 1 else " were") +
#:               " not stated in any document on file and not supplied by a human")
#:
#: which yields, for this case: "cannot screen eligibility: household size was
#: not stated in any document on file and not supplied by a human".
#:
#: Worth considering alongside it: a structured `missing_facts: tuple[str, ...]`
#: on `FrontDecision`, so CANVAS's intake form (§4 persona 6 WO4) can ask for
#: exactly the one field that is blocking rather than parsing prose. That
#: changes a §3.5 dataclass, so it is STATUTE's call, not this module's.
UNSOURCEABLE_PATIENT_FACTS: dict[str, str] = {
    "household_size": (
        "household size was not stated in any document on file -- no bill, Good Faith "
        "Estimate, denial letter, collection notice or pay stub states it, and it is not "
        "inferable from any of them. It is required to compute a federal-poverty-level "
        "percentage, so charity care cannot be screened until a human supplies it"
    ),
}

_US_STATES_RAW = """
    AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT
    NE NV NH NJ NM NY NC ND OH OK OR PA RI SC SD TN TX UT VT VA WA WV WI WY
"""
_US_STATES = frozenset(_US_STATES_RAW.split())
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
#: Matches `reader._MIN_PLAUSIBLE_DATE`. Reader already scrubs epoch dates out
#: of an extraction; re-checking here is cheap and keeps this module correct on
#: its own terms rather than on a neighbour's.
_MIN_PLAUSIBLE_DATE = date(2000, 1, 1)


def _text(value: object) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _iso_date(value: object) -> str | None:
    """A real ISO-8601 calendar date, or None.

    A free-text date ("May 1, 2026") is rejected rather than stored: it would
    occupy the field, block a lower-precedence document that DID give an ISO
    date, and then be silently dropped by `casedata.parse_bill_dates` --
    leaving every clock that depends on it looking like the document simply
    never carried a date.
    """
    text = _text(value)
    if text is None or not _ISO_DATE_RE.match(text[:10]) or len(text) < 10:
        return None
    try:
        parsed = date.fromisoformat(text[:10])
    except ValueError:
        return None
    return text[:10] if parsed >= _MIN_PLAUSIBLE_DATE else None


def _positive_cents(value: object) -> int | None:
    """A money amount, or None.

    `> 0`, not `>= 0`, and that is a deliberate, load-bearing limitation for
    ONE field: a genuinely $0 annual income (the strongest possible
    charity-care case) is indistinguishable, in this schema, from an
    extraction that failed and emitted the integer sentinel `0`. Treating the
    sentinel as a fact would assert an income nobody read and screen the
    patient as free-care eligible on it -- inventing the determination, which
    is the one thing this pipeline may never do. Unknown is the safe error;
    an explicit "the document states zero income" signal would need its own
    schema field (HANDOFF, noted in the PR).
    """
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _flag(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _not_insured(value: object) -> bool | None:
    """`patient.insured` from the document's own coverage STATEMENT.

    The extraction field is `uninsured_self_pay` -- what the document says --
    rather than `insured`, so the model is never asked to infer coverage. A
    Good Faith Estimate exists *because* the recipient is uninsured or
    self-pay (45 CFR 149.610(a)) and this repo's fixtures print that citation
    verbatim; that is a statement, not an inference. `null` (the document does
    not say) stays absent -- `rules.fronts._select_ppdr` refuses on
    "coverage status unknown", which is the correct outcome, and defaulting to
    `False` would hand out a PPDR front nobody established a right to.
    """
    flag = _flag(value)
    return None if flag is None else not flag


def _state(value: object) -> str | None:
    """A two-letter USPS state code, or None.

    This is the state printed on the FACILITY's letterhead, and that is the
    right one: the state overrides in `rules.deadlines` are hospital-conduct
    statutes (Cal. Health & Safety Code §127405 binds California hospitals;
    Illinois' 90-day window binds Illinois hospitals), so the governing state
    is where the facility is, not where the patient sleeps. Only a bare
    two-letter code is accepted -- "California", "Sutter Bay" or "CA 94304"
    are rejected rather than normalized, because a wrong state silently swaps
    an entire deadline regime (California has no charity-care deadline at all;
    Illinois has a 90-day one) and a guess here is not visible anywhere
    downstream.
    """
    text = _text(value)
    if text is None:
        return None
    code = text.upper()
    return code if code in _US_STATES else None


def _line_items(value: object) -> list | None:
    """The itemized table, or None if nothing in it is usable.

    "Usable" mirrors `rules.fronts._usable_line_item_count` and
    `rules.audit`'s own line-validity rule exactly: a dict carrying a
    non-empty string `code`. An empty or all-junk table must not displace a
    real one, and must never be written as if it were a bill with no charges.
    """
    if not isinstance(value, list):
        return None
    usable = any(
        isinstance(item, dict) and isinstance(item.get("code"), str) and item["code"].strip()
        for item in value
    )
    return value if usable else None


@dataclass(frozen=True)
class _Spec:
    """One canonical field: where it lives, what reads it, who may set it."""

    target: str  # "bill" | "patient"
    field: str  # the canonical field name in §3.1
    source_key: str  # the key in `documents[].extracted`
    doc_types: tuple[str, ...]  # document types that may establish it, BEST FIRST
    normalize: object  # value -> normalized value | None ("None" == not established)


# THE PRECEDENCE TABLE. Read the `doc_types` tuple as "the first of these that
# has a usable value wins"; the comment above each block is why that order.
_SPECS: tuple[_Spec, ...] = (
    # --- bill: what was charged -------------------------------------------
    # The itemized statement is the instrument of the debt and enumerates it;
    # a summary `bill` is the same claim with less detail. A GFE is EXCLUDED
    # from `amount_cents` on purpose: it is an *estimate*, and letting it
    # write the amount owed would collapse the PPDR delta (bill - GFE) to
    # zero and silently erase the front it exists to prove.
    _Spec("bill", "amount_cents", "amount_cents", ("itemized_bill", "bill"), _positive_cents),
    # ...and symmetrically, the GFE owns the estimate. A bill that quotes an
    # earlier estimate is a second-hand mention, usable only if no GFE is on
    # file.
    _Spec(
        "bill",
        "gfe_amount_cents",
        "gfe_amount_cents",
        ("gfe", "itemized_bill", "bill"),
        _positive_cents,
    ),
    # Both dates come off the statement itself. `first_statement_date` starts
    # the 240-day FAP window, the 120-day ECA moratorium AND the 120-day PPDR
    # clock (§3.5) -- three regimes on one date, so only the document that
    # actually bears it may set it.
    _Spec("bill", "service_date", "service_date", ("itemized_bill", "bill"), _iso_date),
    _Spec(
        "bill",
        "first_statement_date",
        "first_statement_date",
        ("itemized_bill", "bill"),
        _iso_date,
    ),
    # The provider that issued the bill. A collection notice names the
    # provider only second-hand ("originally owed to ..."), so it ranks last;
    # Lookup resolves the hospital by this name, and a collector's rendering
    # of it is exactly the kind of near-miss that fails a name match.
    _Spec(
        "bill",
        "provider_name",
        "provider_name",
        ("itemized_bill", "bill", "gfe", "denial_letter", "collection_notice"),
        _text,
    ),
    # Identifiers. The bill and the GFE both print the EIN on the same
    # letterhead in this corpus and agree; the bill wins because it is the
    # document the money is owed on. (`_run_cascade` separately backfills an
    # EIN that Lookup resolved by NAME when no document carried one.)
    _Spec(
        "bill",
        "hospital_ein",
        "hospital_ein",
        ("itemized_bill", "bill", "gfe", "denial_letter"),
        _text,
    ),
    _Spec(
        "bill",
        "hospital_ccn",
        "hospital_ccn",
        ("itemized_bill", "bill", "gfe", "denial_letter"),
        _text,
    ),
    # Collections. Only a collection notice establishes that an account went
    # to a collector, who that collector is, and when the 12 CFR 1006.34
    # validation notice was dated -- the 30-day dispute clock runs off that
    # date and nothing else may set it.
    _Spec(
        "bill",
        "in_collections",
        "in_collections",
        ("collection_notice", "itemized_bill", "bill"),
        _flag,
    ),
    _Spec("bill", "collector_name", "collector_name", ("collection_notice",), _text),
    _Spec(
        "bill",
        "validation_notice_date",
        "validation_notice_date",
        ("collection_notice",),
        _iso_date,
    ),
    # The table itself -- the fact whose absence started all of this.
    _Spec("bill", "line_items", "line_items", ("itemized_bill", "bill"), _line_items),
    # --- patient: who this is ---------------------------------------------
    # A pay stub is EXCLUDED: it names an *employee*, and the earner in a
    # household is not necessarily the patient. A filed FAP application
    # carrying the wrong person's name is worse than one carrying none.
    _Spec(
        "patient",
        "name",
        "patient_name",
        ("itemized_bill", "bill", "gfe", "denial_letter", "collection_notice"),
        _text,
    ),
    # See `_state`: the facility's letterhead, in facility-authority order. A
    # collection notice is excluded -- its letterhead is the COLLECTOR's
    # address, which has nothing to do with which state's hospital-billing
    # statute governs. A pay stub's is the employer's, likewise irrelevant.
    _Spec(
        "patient",
        "state",
        "state",
        ("itemized_bill", "bill", "gfe", "denial_letter"),
        _state,
    ),
    # Coverage. The GFE is first because 45 CFR 149.610(a) is *why* it was
    # issued, so its statement is the strongest one available; a denial letter
    # or a bill may also state coverage explicitly. See `_not_insured`.
    _Spec(
        "patient",
        "insured",
        "uninsured_self_pay",
        ("gfe", "denial_letter", "itemized_bill", "bill"),
        _not_insured,
    ),
    # Income comes from an income document and nowhere else -- that is what
    # the document type IS. A bill stating a number about a patient's income
    # would be a misread, not a fact.
    _Spec(
        "patient",
        "annual_income_cents",
        "annual_income_cents",
        ("income_proof",),
        _positive_cents,
    ),
)


def _usable_extraction(doc: dict) -> dict | None:
    """This document's extraction, if it has one worth reading."""
    if (doc.get("type") or "") not in INCOMING_DOC_TYPES:
        return None
    extracted = doc.get("extracted")
    if not isinstance(extracted, dict) or "_extraction_error" in extracted:
        return None
    # The cat-photo case (§4 persona 7 WO1): an upload Reader itself judged
    # not to be an income document at all. Whatever numbers were pulled off it
    # are not this patient's income, and `annual_income_cents` is the only
    # thing an `income_proof` can set -- so drop the document entirely.
    if doc.get("type") == "income_proof" and extracted.get("is_income_proof") is False:
        return None
    return extracted


def _best(documents: list[dict], spec: _Spec) -> tuple[object, dict] | None:
    """The winning value for one field, plus the document it came from."""
    for doc_type in spec.doc_types:
        for doc in documents:
            if doc.get("type") != doc_type:
                continue
            extracted = _usable_extraction(doc)
            if extracted is None:
                continue
            value = spec.normalize(extracted.get(spec.source_key))
            if value is not None:
                return value, doc
    return None


def _has_value(container: dict, field: str) -> bool:
    """True if the case already carries a usable value for this field.

    `None`, `""` and `[]` all read as "not known" -- they are how an
    unpopulated case, an empty fixture and a cleared field all look, and none
    of them is a fact worth protecting from a document that has a real one.
    """
    value = container.get(field)
    return value is not None and value != "" and value != []


def state_from_hospital(hospital: dict | None) -> str | None:
    """`patient.state` from a RESOLVED `hospitals/{ein}` record (§3.1).

    A backstop for the letterhead read, used by `pipeline._run_cascade` after
    Lookup: the hospital record's `state` comes from LEDGER's IRS Schedule H /
    CMS pipeline, so it is a registry fact rather than a model's read of a
    line of text. It is applied under the same fill-a-gap rule as every other
    patient fact (it cannot arrive before Lookup has run, and it must not
    silently rewrite a state a human entered) -- the two sources describe the
    same facility and agree; this exists so a case is not left with no state
    at all when the letterhead read comes back empty.
    """
    return _state((hospital or {}).get("state")) if hospital else None


def merge_document_facts(case: dict, documents: list[dict]) -> tuple[dict, dict]:
    """Fold every document's extraction into canonical `patient` / `bill`.

    Args:
        case: the `cases/{case_id}` document (§3.1).
        documents: every `cases/{id}/documents/{doc_id}` record on file --
            ALL of them, not just the one that just arrived. The precedence
            in `_SPECS` is only meaningful over the whole corpus.

    Returns:
        `(patch, report)`.

        `patch` is a `store.update_case` patch containing only what actually
        CHANGES -- `{}` when the merge establishes nothing new, which is the
        normal steady state under re-analysis and is why this is safe to run
        on every redelivery (§2.3).

        `report` is for the audit trail: `established` (field, value, source
        document type and id), `deferred` (a document states a patient fact
        the case already knows -- recorded, never applied, see rule 3), and
        `unknown` (every canonical fact still missing, each with the reason).
        Nothing here is ever inferred, so the report is also the honest list
        of what a human still has to supply.
    """
    case_bill = dict(case.get("bill") or {})
    case_patient = dict(case.get("patient") or {})
    merged = {"bill": dict(case_bill), "patient": dict(case_patient)}
    established: list[dict] = []
    deferred: list[dict] = []

    for spec in _SPECS:
        found = _best(documents, spec)
        if found is None:
            continue
        value, doc = found
        target = merged[spec.target]
        if spec.target == "patient" and _has_value(case_patient, spec.field):
            if case_patient.get(spec.field) != value:
                deferred.append(
                    {
                        "field": f"patient.{spec.field}",
                        "document_value": value,
                        "case_value": case_patient.get(spec.field),
                        "source_type": doc.get("type"),
                        "source_doc_id": doc.get("doc_id"),
                    }
                )
            continue
        if target.get(spec.field) == value:
            continue
        target[spec.field] = value
        established.append(
            {
                "field": f"{spec.target}.{spec.field}",
                "value": value,
                "source_type": doc.get("type"),
                "source_doc_id": doc.get("doc_id"),
            }
        )

    # web/lib/types.ts's `Bill.has_itemized_bill` -- a presence flag CANVAS
    # renders, and distinct from whether anything was successfully READ out of
    # that document (`rules.fronts._select_audit` keeps those two apart on
    # purpose). Set from the document's TYPE, never from the extraction.
    if merged["bill"].get("has_itemized_bill") is not True and any(
        doc.get("type") == "itemized_bill" for doc in documents
    ):
        merged["bill"]["has_itemized_bill"] = True
        established.append(
            {
                "field": "bill.has_itemized_bill",
                "value": True,
                "source_type": "itemized_bill",
                "source_doc_id": None,
            }
        )

    patch = {key: value for key, value in merged.items() if value != (case.get(key) or {})}
    return patch, {
        "established": established,
        "deferred": deferred,
        "unknown": _unknown_facts(merged),
    }


def _unknown_facts(merged: dict) -> list[dict]:
    """Every canonical fact still missing after the merge, and why.

    The point of naming them one at a time: `rules.fronts._select_charity_care`
    currently refuses with "insufficient patient data (income, household size,
    or state) to screen eligibility" -- which, on a case where income and
    state are both known and only household size is not, tells a human to go
    and check three things when exactly one is actually missing. A future
    intake form should be able to ask for precisely the one fact that is
    blocking, and this list is what it would ask from.
    """
    patient, bill = merged["patient"], merged["bill"]
    unknown = [
        {"field": f"patient.{field}", "reason": reason}
        for field, reason in UNSOURCEABLE_PATIENT_FACTS.items()
        if not _has_value(patient, field)
    ]
    for spec in _SPECS:
        container = patient if spec.target == "patient" else bill
        if _has_value(container, spec.field):
            continue
        sources = ", ".join(spec.doc_types)
        unknown.append(
            {
                "field": f"{spec.target}.{spec.field}",
                "reason": (
                    f"not stated in any document on file that can establish it "
                    f"(would come from: {sources})"
                ),
            }
        )
    return unknown
