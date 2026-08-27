"""What the patient SAYS -- kept, used, and never allowed to pass for what a
document PROVES.

WHY THIS MODULE EXISTS. A real emailed bill (Sutter Bay / CA / self-pay,
$2,625 billed against a $1,925 Good Faith Estimate, a $32,000 pay stub)
reached `select_fronts` with income and state established and exactly one
fact missing: household size. No document on file states it and none can --
a pay stub names an employee's earnings, never who else lives in their home
(`factmerge.UNSOURCEABLE_PATIENT_FACTS`). Charity care therefore, correctly,
refused. And the patient had written the answer in the covering email:

    "I'm uninsured and paying out of pocket. Household of three,
     I make about $32,000 a year."

At household 3, $32,000 is 117% of the 2026 federal poverty level ($27,320),
far under California's 400% statutory floor (Cal. Health & Safety Code
§127405(a)(1)(A)) -- free care, the entire bill erased. Without that one
number the same case is worth $210 of duplicate-billing findings. One
sentence is the difference, and the system was throwing it away.

THE THING THAT MUST NOT HAPPEN. This project's worst defect (HANDOFF #5) was
the Reader inventing an EIN and epoch dates, from which the Clock computed
real regulatory deadlines and the Filer mailed a letter. The recovery from
that -- declining rather than guessing -- is now a demo asset. Reading facts
out of free-text prose with an LLM is that same hazard's natural habitat, and
a patient-stated household size that becomes indistinguishable from a
document-proven one, three writes later, is defect #5 with better manners.

THE FOUR RULES
--------------

**1. A stated fact never enters `cases/{id}.patient`.** `factmerge` runs the
document merge; this module runs *after* it and produces an OVERLAY -- a
derived view of the patient handed to `rules.select_fronts` for the duration
of one call, and then discarded. `patient` on the case keeps meaning exactly
what it meant before: what a document or a human established. That is what
lets the Verifier, the API and a judge tell the two apart at any point later,
because the distinction is structural rather than a flag somebody has to
remember to carry.

It also keeps the merge honest in the other direction. `factmerge`'s
fill-a-gap rule for `patient` means a value already on the case is never
replaced by a document -- so writing a statement's `insured` into `patient`
would let a sentence pre-empt the Good Faith Estimate that follows it and
actually establishes coverage under 45 CFR 149.610(a). A weaker source that
occupies the field blocks the stronger one behind it. It does not get the
field.

**2. Third tier, and only in a gap.** Human-entered (§3.3 `POST /cases`) beats
document beats statement. The overlay fills a field only when `patient` has
nothing usable in it, so a statement can never contradict a fact the case
already holds -- it can only speak where the record is silent.

**3. Agreement is corroboration; disagreement is logged, never resolved.**
When the patient says "I'm uninsured" and the GFE already established the
same thing, that is not a new fact and nothing is written -- it is recorded as
corroboration. When they disagree, the document wins and the disagreement
becomes an event on the case for a human to look at. This module never picks
a winner silently, exactly as `factmerge` rule 3 refuses to.

**4. A front that rests on a statement says so, everywhere.** `decide_fronts`
returns, per front, the stated facts that were actually load-bearing for its
outcome -- computed by leave-one-out over `select_fronts` (pure, cheap, no
LLM), not by guessing which fact mattered. That list becomes `fronts[].
rests_on` / `provisional` in §3.1, the Verifier's refusal to file, and the
reason string on screen.

WHY NOT JUST REFUSE. Three designs were on the table.

  * *Ignore the body.* Honest and useless: household size is unobtainable
    from any document, so charity care -- the front that erases whole bills --
    could never be screened for anyone, ever.
  * *Merge it into `patient` and screen normally.* The $2,625 shows up, and
    nothing on the case distinguishes the number that came off a pay stub from
    the number that came out of a sentence. That is the fabrication defect.
  * *Screen it, mark it provisional, block it at filing.* What this does.

The third is also what the law actually looks like: a hospital's FAP
application asks the PATIENT for household size and takes their attestation
(26 CFR 1.501(r)-4(b)(2) screens against thresholds, it does not demand a
census). A patient's statement is the normal evidence for this fact -- but it
is evidence a human advocate signs off on, not something an autonomous filer
should send unattended. So the determination is computed and shown with its
provenance attached, the savings figure it implies is NOT counted as found
money, and the Verifier stops the filing until a human confirms the number.
"""

from __future__ import annotations

from dataclasses import dataclass

from .factmerge import PATIENT_STATEMENT_TYPE

#: A household bigger than this is a misread, not a household. The 2026 HHS
#: poverty guidelines (91 FR 1797) table out to 8 and then add per person, so
#: nothing here is a statutory ceiling -- it is the point past which "20" is
#: far more likely to be a stray number the model grabbed from the prose than
#: a family. Below 1 is not a household at all.
MIN_HOUSEHOLD, MAX_HOUSEHOLD = 1, 20

#: $1,000,000/yr. Same reasoning: not a threshold anything legal turns on
#: (every charity-care threshold is far below it), just the point where a
#: number extracted from prose is likelier to be a phone number, a case id or
#: a bill total than an annual income.
MAX_STATED_INCOME_CENTS = 100_000_000


def _household(value: object) -> int | None:
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value if MIN_HOUSEHOLD <= value <= MAX_HOUSEHOLD else None


def _income_cents(value: object) -> int | None:
    """A stated annual income, or None.

    `> 0`, for the same reason `factmerge._positive_cents` documents: the
    extractor's "not found" sentinel for an integer field is `0`, and a
    genuinely-zero income (the strongest possible charity-care case) is
    indistinguishable from it in this schema. Unknown is the safe error.
    """
    if not isinstance(value, int) or isinstance(value, bool):
        return None
    return value if 0 < value <= MAX_STATED_INCOME_CENTS else None


def _not_insured(value: object) -> bool | None:
    """`patient.insured` from what the patient SAID about their coverage.

    Mirrors `factmerge._not_insured`: the extracted field is
    `uninsured_self_pay` -- what was claimed -- so the model is never asked to
    infer coverage from silence.
    """
    return None if not isinstance(value, bool) else not value


@dataclass(frozen=True)
class _StatedSpec:
    """One patient fact a statement may claim, and how to read it."""

    field: str  # the canonical `patient` field name (§3.1)
    source_key: str  # the key in `documents[].extracted`
    quote_key: str  # the verbatim span backing it (see agents/reader.py)
    label: str  # how it is named to a human
    normalize: object


#: WHAT A PATIENT MAY BE HEARD ON. Three facts, and the list is short on
#: purpose: every one of them is something a person is the primary authority
#: about themselves. There is no `provider_name`, no `amount_cents`, no
#: `service_date` here -- those are printed on a document, a patient's
#: recollection of them is a worse copy of a better source, and admitting them
#: would turn this from "capture what no document can carry" into "let prose
#: rewrite the bill".
STATED_SPECS: tuple[_StatedSpec, ...] = (
    # The one fact no §3.1 document type can establish. This is why the
    # module exists.
    _StatedSpec(
        "household_size", "household_size", "household_size_quote", "household size", _household
    ),
    # Income IS documentable (a pay stub), so a stated income is rarely the
    # only source -- but it is the number the Verifier can finally cross-check
    # the pay stub AGAINST. Two independent readings of the same fact is what
    # the ±15% tolerance in §4 persona 5 WO1 was always for; until now both
    # sides of that comparison came from the same document.
    _StatedSpec(
        "annual_income_cents",
        "annual_income_cents",
        "annual_income_quote",
        "annual household income",
        _income_cents,
    ),
    # Coverage is normally established by the GFE (45 CFR 149.610(a)). A
    # patient saying "I'm uninsured" on a case that already knows it is
    # corroboration, not news -- and `establish` below is what keeps it that
    # way rather than letting it re-assert the fact from a weaker source.
    _StatedSpec(
        "insured", "uninsured_self_pay", "coverage_quote", "insurance status", _not_insured
    ),
)

_LABELS = {spec.field: spec.label for spec in STATED_SPECS}


def label_for(field: str) -> str:
    """How to name one stated fact to a human. Falls back to the raw field."""
    return _LABELS.get(field, field)


def statement_documents(documents: list[dict]) -> list[dict]:
    """Every `patient_statement` document on file, oldest id first.

    Sorted by `doc_id` rather than left in Firestore's stream order for the
    same reason `evidence.from_documents` sorts: the result has to be a
    function of the corpus, not of the order a query happened to return it in,
    or "which statement wins" becomes a race exactly like the one
    `store.write_analysis` exists to close.
    """
    return sorted(
        (d for d in documents if (d.get("type") or "") == PATIENT_STATEMENT_TYPE),
        key=lambda d: str(d.get("doc_id") or ""),
    )


def collect(documents: list[dict]) -> dict[str, dict]:
    """Every fact the patient has stated, as `{field: {value, quote, ...}}`.

    Pure over the whole document corpus (the same discipline as
    `factmerge.merge_document_facts`), so re-running it on an unchanged case
    produces an identical result and §2.3 redelivery is a no-op.

    The FIRST statement to state a fact wins, and a later statement that
    disagrees is reported in `conflicts` rather than silently replacing it. A
    patient who writes twice is not a correction protocol; two different
    household sizes from the same person is something a human should see, not
    something this module should resolve by recency. (A genuine correction has
    a first-class path: a human enters it via §3.3 `POST /cases`, and rule 2
    means the entered value outranks every statement.)
    """
    stated: dict[str, dict] = {}
    conflicts: list[dict] = []
    for doc in statement_documents(documents):
        extracted = doc.get("extracted")
        if not isinstance(extracted, dict) or "_extraction_error" in extracted:
            continue
        for spec in STATED_SPECS:
            value = spec.normalize(extracted.get(spec.source_key))
            if value is None:
                continue
            quote = extracted.get(spec.quote_key)
            record = {
                "value": value,
                "quote": quote if isinstance(quote, str) else None,
                "source": PATIENT_STATEMENT_TYPE,
                "source_doc_id": doc.get("doc_id"),
            }
            existing = stated.get(spec.field)
            if existing is None:
                stated[spec.field] = record
            elif existing["value"] != value:
                conflicts.append({"field": spec.field, "kept": existing, "also_stated": record})
    if conflicts:
        stated["_conflicts"] = {"value": conflicts}
    return stated


def facts(stated: dict[str, dict]) -> dict[str, dict]:
    """`collect`'s output minus its bookkeeping keys."""
    return {k: v for k, v in (stated or {}).items() if not k.startswith("_")}


def conflicts(stated: dict[str, dict]) -> list[dict]:
    return list((stated or {}).get("_conflicts", {}).get("value") or [])


def _established(patient: dict, field: str) -> bool:
    """True if the case already holds a usable value for this patient field.

    Same emptiness rule as `factmerge._has_value` -- `None`, `""` and `[]` are
    all how an unpopulated field looks, and none of them is a fact.
    """
    value = (patient or {}).get(field)
    return value is not None and value != "" and value != []


def overlay(patient: dict, stated: dict[str, dict]) -> tuple[dict, tuple[str, ...]]:
    """`patient` plus every stated fact that fills a GAP in it.

    Returns `(view, filled)` -- a NEW dict (the caller's `patient` is never
    mutated; this value is handed to `select_fronts` and thrown away) and the
    names of the fields the statement supplied. Rule 2: a field the case
    already holds is left exactly as it is, whatever the patient said about it.
    """
    view = dict(patient or {})
    filled: list[str] = []
    for field, record in facts(stated).items():
        if _established(patient, field):
            continue
        view[field] = record["value"]
        filled.append(field)
    return view, tuple(sorted(filled))


def reconcile(patient: dict, stated: dict[str, dict]) -> dict[str, list[dict]]:
    """Compare what the patient said against what the case already knows.

    Returns `{"corroborated": [...], "contradicted": [...]}` over exactly the
    fields where BOTH have a value -- the fields `overlay` leaves alone.
    Neither list changes any value: corroboration is not a second write of a
    fact already held, and a contradiction is a thing a human resolves, not a
    thing this module picks a winner in (see rule 3).
    """
    corroborated: list[dict] = []
    contradicted: list[dict] = []
    for field, record in facts(stated).items():
        if not _established(patient, field):
            continue
        entry = {
            "field": f"patient.{field}",
            "label": label_for(field),
            "stated_value": record["value"],
            "established_value": patient.get(field),
            "quote": record.get("quote"),
            "source_doc_id": record.get("source_doc_id"),
        }
        (corroborated if patient.get(field) == record["value"] else contradicted).append(entry)
    return {"corroborated": corroborated, "contradicted": contradicted}


def _decision_key(decision) -> tuple:
    """What counts as "the same front decision" for attribution purposes.

    Applicability, the reason a human reads, and the date a clock runs to.
    `citation` is derived from the branch the reason already names, so it adds
    nothing; `status` is not `select_fronts`'s to set.
    """
    return (decision.applicable, decision.reason, decision.deadline)


def decide_fronts(select_fronts, case: dict, stated: dict[str, dict]) -> tuple[list, dict]:
    """Run STATUTE's front selector over the overlaid patient, and report --
    per front -- which stated facts its outcome actually DEPENDS on.

    Returns `(decisions, rests_on)` where `rests_on[front]` is a tuple of
    canonical field names, empty for every front the statement did not change.

    HOW THE ATTRIBUTION IS COMPUTED, and why it is not a guess. Leave-one-out:
    the selector is run once with the full overlay, then once more with each
    single stated fact removed. A fact is load-bearing for a front exactly
    when removing it changes that front's decision. `select_fronts` is pure
    and does no I/O and no LLM call (§2.1), so this costs three or four
    dictionary-shaped function calls and nothing else -- and it is EXACT,
    where a rule of thumb like "charity care always rests on household size"
    would be wrong the moment a human enters the number by hand.

    Note that a fact can be load-bearing for a front it did not make
    applicable: a stated income that pushes the patient over every published
    threshold changes charity care from "cannot screen" to "ineligible", and
    that determination rests on the statement just as much as a favourable one
    does. `rests_on` marks the front as provisional either way, because what
    is provisional is the DETERMINATION, not the good news.
    """
    baseline_patient = dict(case.get("patient") or {})
    view, filled = overlay(baseline_patient, stated)
    decisions = list(select_fronts({**case, "patient": view}))
    if not filled:
        return decisions, {}

    rests_on: dict[str, list[str]] = {d.front: [] for d in decisions}
    for field in filled:
        without = {k: v for k, v in view.items() if k != field}
        # A field is only ever absent from `baseline_patient` here (overlay
        # fills gaps only), so removing it restores the case's real state for
        # that fact and nothing else.
        alternative = {
            d.front: _decision_key(d) for d in select_fronts({**case, "patient": without})
        }
        for decision in decisions:
            if alternative.get(decision.front) != _decision_key(decision):
                rests_on[decision.front].append(field)

    return decisions, {front: tuple(fields) for front, fields in rests_on.items() if fields}


def provisional_reason(reason: str, fields: tuple[str, ...], stated: dict[str, dict]) -> str:
    """Prefix a front's reason with the provenance of what it rests on.

    CODE-BUILT AND FIRST IN THE STRING, exactly like
    `pipeline._SIMULATED_PREFIX`, and for the identical reason: whether a
    determination rests on a document or on a sentence someone typed is a fact
    about the world, not something narration gets to phrase, shorten or drop.
    It leads so that it survives truncation in the activity feed, and it names
    the verbatim words so a human can judge the claim rather than take this
    system's word for it.
    """
    parts = []
    for field in fields:
        record = facts(stated).get(field) or {}
        quote = (record.get("quote") or "").strip()
        said = f' ("{quote}")' if quote else ""
        parts.append(f"{label_for(field)}{said}")
    claims = "; ".join(parts)
    return (
        f"[PROVISIONAL -- rests on the patient's own statement, not on a document] "
        f"This determination uses {claims}, taken from the patient's email rather than from "
        f"any document on file. No document this system holds states it, and nothing has "
        f"verified it. The determination below is what WOULD follow if it is true; a filing "
        f"on this front is blocked until a human confirms it. {reason}"
    )
