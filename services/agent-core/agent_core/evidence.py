"""What one analysis pass actually saw, so a worse-informed pass cannot
overwrite a better-informed one.

THE DEFECT THIS EXISTS FOR (measured live, `case-1a043f4f4ae26dfa`,
2026-08-26). An email carrying three PDFs -- an itemized bill, a Good Faith
Estimate and a pay stub -- publishes three `case.document.added` events, and
`ef-document-added`'s 60s ack deadline against a 60-130s cascade adds up to
five more redeliveries on top. Every one of those runs the WHOLE
Reader -> merge -> Lookup -> {Clock, Auditor} -> Strategist cascade, and every
one of them ends by writing the entire `fronts[]` reason/applicable set from
whatever documents happened to exist when IT started:

    16:03:21  reader      itemized_bill classified
    16:03:22  reader      merge_document_facts   (the bill, alone)
    16:03:46  strategist  charity_care: "annual household income was not
                          stated in any document on file..."
    16:04:00  reader      income_proof classified -- the pay stub, $32,000
    16:04:01  reader      merge_document_facts   (all three)
    16:04:30  strategist  charity_care: "household size was not stated..."
                          <- CORRECT: income IS on file, only household is not

and the reason **stored** afterwards was the 16:03:46 one, written by a pass
that finished later and had never seen the pay stub. `patient.
annual_income_cents` was 3,200,000 in the same Firestore document, so the case
contradicted itself on screen: the front said the income was unknown while the
patient record stated it.

Last writer wins, and "last" means last to finish -- not best informed. Both
passes were behaving exactly as written, which is this repo's signature failure
mode (HANDOFF.md, "THE BUG PATTERN"): nothing crashed, nothing logged an
error, and the tests were green throughout.

THE FIX, IN ONE SENTENCE. Every analysis write carries a description of the
evidence the writing pass consumed, and `CaseStore.write_analysis` refuses --
inside the same transaction that would have applied it -- any write whose
evidence is a strict subset of what is already recorded on the case.

WHY THE DOCUMENT'S CONTENT AND NOT JUST ITS ID. A count or a set of doc ids
would have been cheaper and would have been wrong. A cascade re-reads the
document store at its Auditor and Strategist steps, so it routinely SEES a
document that Reader has not classified yet: `type` is `""` and `extracted` is
absent, and `factmerge` skips it entirely (`_usable_extraction`). A pass
holding {d1-read, d2-unread} would then claim a superset of a pass holding
{d1-read} and be allowed to overwrite it with an answer built from strictly
less evidence -- the original bug, wearing an id-shaped disguise. So a
document's token is a fingerprint of what could actually be READ off it:
`(type, extracted)`. An unread document contributes nothing to any conclusion
and therefore contributes no evidence.

WHY GENERATED DOCUMENTS ARE EXCLUDED. `generated_application` /
`generated_letter` are documents this system PRODUCED (a filled FAP form, a
validation letter). `pipeline.is_agent_generated` already keeps them from
re-triggering analysis, and `factmerge.INCOMING_DOC_TYPES` already keeps them
out of the merge -- counting them here would let a stale cascade that happened
to start after a filing claim evidence it never used.

WHY A SUBSET TEST AND NOT A TIMESTAMP OR A COUNTER. A timestamp orders the
WRITES; the whole defect is that write order is the wrong order. A counter
would need a single writer to increment it. Evidence is the only thing that
orders the passes by how well-informed they were, which is the property that
actually matters -- and it is a partial order on purpose: two passes that saw
the same documents are interchangeable (so re-analysis with identical evidence
still writes, and §2.3 idempotency is unaffected), and a pass that saw a
document the stored answer did not is genuinely newer information and is let
through.
"""

from __future__ import annotations

import hashlib
import json

from .factmerge import INCOMING_DOC_TYPES, PATIENT_STATEMENT_TYPE

#: Everything an analysis pass can READ. Deliberately WIDER than
#: `factmerge.INCOMING_DOC_TYPES`, because the two sets answer different
#: questions: that one asks "may this document establish a canonical fact?"
#: (a patient's typed sentence may not -- see `factmerge.
#: PATIENT_STATEMENT_TYPE`), this one asks "did this pass see it?".
#:
#: Leaving `patient_statement` out here would reopen exactly the race this
#: module exists to close, one field over. A pass that read the bill and the
#: statement would carry the SAME evidence descriptor as one that read only
#: the bill, so the two would be interchangeable, equal evidence writes, and
#: last-to-finish would win again -- a cascade that never saw the email
#: overwriting the provisional charity-care determination of one that did.
ANALYSED_DOC_TYPES = INCOMING_DOC_TYPES | {PATIENT_STATEMENT_TYPE}

#: A pass that recorded no evidence at all. Distinct from "we did not look":
#: an empty list IS a strict subset of every non-empty one, so a cascade that
#: saw nothing can never overwrite one that saw something.
EMPTY: list[str] = []


def _fingerprint(doc: dict) -> str:
    """A stable digest of everything this document contributes to an analysis.

    `sort_keys` so a dict's key order (Firestore returns maps unordered)
    cannot change the digest; `default=str` so a stray date object degrades to
    something stable rather than raising -- the same two rules
    `pipeline._fact_event_id` follows, and for the same reason.
    """
    raw = json.dumps(
        [doc.get("type") or "", doc.get("extracted")],
        sort_keys=True,
        default=str,
        separators=(",", ":"),
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def from_documents(documents: list[dict]) -> list[str]:
    """The evidence descriptor for a pass that consumed exactly `documents`.

    Sorted, so it is a canonical value: two passes over the same corpus
    produce byte-identical lists regardless of the order Firestore streamed
    them back, which is what makes "identical evidence" recognisable and
    therefore what keeps re-analysis idempotent.
    """
    return sorted(
        f"{doc.get('doc_id')}#{_fingerprint(doc)}"
        for doc in documents
        if (doc.get("type") or "") in ANALYSED_DOC_TYPES
    )


def is_strictly_weaker(candidate: list[str] | None, recorded: list[str] | None) -> bool:
    """True if `candidate` saw strictly less than `recorded` already did.

    The one question `CaseStore.write_analysis` asks before applying anything.
    `None` means "this caller does not track evidence" and is never weaker --
    the guard is opt-in, so every existing caller of the unguarded
    `upsert_front_from_analysis` keeps its exact previous behaviour.

    Equality is deliberately NOT weaker: a redelivery of the same document set
    must still be allowed to write, or the second run of an unchanged analysis
    would behave differently from the first, which is the opposite of the
    idempotency §2.3 asks for.
    """
    if candidate is None or recorded is None:
        return False
    have, already = set(candidate), set(recorded)
    return have < already
