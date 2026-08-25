"""Denial triage: is a documentation-based denial even lawful?

Working agreement §2.1: pure function, zero LLM calls.

26 CFR 1.501(r)-4(b)(3) requires a nonprofit hospital's Financial Assistance
Policy to describe, in writing, any information or documentation an applicant
may be required to submit. The regulation's own preamble (T.D. 9708) is
explicit that a facility may still grant assistance to an applicant who does
not provide requested documentation -- the FAP's published list is a ceiling
on what may be demanded, not a floor the patient must clear. A hospital that
denies -- or conditions -- financial assistance on a document outside that
published list is asking for more than 1.501(r) permits it to ask for. The
playbook's headline stat (§1.2): 24% of charity-care denials nationally are
exactly this kind of paperwork problem, which is why this is a demo
centerpiece rather than a footnote.

This module does the one thing it can do without a lawyer in the loop: the
set difference between what was demanded and what was published. That is
sufficient to *flag* a likely violation and draft the citation; it is not a
final legal determination, so the callers (Strategist, and the human at the
`POST /cases/{id}/approve_filing` gate) still have the last word.
"""

from __future__ import annotations

from dataclasses import dataclass

_CITATION = "26 CFR 1.501(r)-4(b)(3)"


def _normalize(doc: str) -> str:
    """Case- and whitespace-insensitive key for comparing document names."""
    return " ".join(doc.strip().lower().split())


def _clean(docs: list | None) -> list[str]:
    return [d for d in (docs or []) if isinstance(d, str) and d.strip()]


@dataclass(frozen=True)
class DenialCheck:
    """Result of comparing demanded documentation against the published FAP list.

    `insufficient_data` is True when no FAP document list was available at
    all -- that is a data gap, not proof the hospital's list is empty, so it
    is kept distinct from `violation`: this module would rather say "unknown"
    than manufacture a false accusation of unlawfulness from a missing
    extraction.
    """

    violation: bool
    unlisted_docs: tuple[str, ...]
    demanded_docs: tuple[str, ...]
    fap_doc_list: tuple[str, ...]
    insufficient_data: bool
    citation: str
    drafted_citation: str

    def explain(self) -> str:
        if self.insufficient_data:
            return "Cannot assess denial lawfulness: no FAP documentation list is on file."
        if not self.violation:
            return "No violation: every demanded document appears on the FAP's published list."
        return (
            "Violation: the hospital demanded "
            + "; ".join(self.unlisted_docs)
            + f", which its published FAP does not list ({self.citation})."
        )


def check_denial_lawfulness(demanded_docs: list, fap_doc_list: list) -> DenialCheck:
    """Flag documentation demands that exceed the hospital's published FAP list. Contract §3.5.

    Args:
        demanded_docs: document names/descriptions the hospital demanded of
            the applicant (e.g. from a denial letter).
        fap_doc_list: document names/descriptions the hospital's published
            FAP says may be required (26 CFR 1.501(r)-4(b)(3)).

    Returns:
        A `DenialCheck`. Never raises: non-string or blank entries in either
        list are dropped rather than crashing the caseload, and an empty
        `fap_doc_list` is treated as missing data (`insufficient_data=True`)
        rather than as license to flag every demand as unlawful.
    """
    demanded = _clean(demanded_docs)
    fap_list = _clean(fap_doc_list)

    if not fap_list:
        return DenialCheck(
            violation=False,
            unlisted_docs=(),
            demanded_docs=tuple(demanded),
            fap_doc_list=(),
            insufficient_data=True,
            citation=_CITATION,
            drafted_citation=(
                "No FAP documentation list is on file for this hospital; "
                f"{_CITATION} compliance cannot be assessed until the FAP's published "
                "document list is captured."
            ),
        )

    fap_normalized = {_normalize(f) for f in fap_list}
    unlisted = tuple(d for d in demanded if _normalize(d) not in fap_normalized)

    if unlisted:
        listing = "; ".join(unlisted)
        drafted = (
            "The hospital's Financial Assistance Policy, as published, does not list "
            f"{listing} among the documentation an applicant may be required to submit. "
            f"Under {_CITATION}, the FAP must describe any required documentation, and a "
            "facility may not condition eligibility for assistance on information outside "
            "that published list. The denial on this basis is unlawful and must be reversed."
        )
    else:
        drafted = (
            "Every document demanded of the applicant appears on the hospital's published "
            f"FAP documentation list; no {_CITATION} violation is presented on this record."
        )

    return DenialCheck(
        violation=bool(unlisted),
        unlisted_docs=unlisted,
        demanded_docs=tuple(demanded),
        fap_doc_list=tuple(fap_list),
        insufficient_data=False,
        citation=_CITATION,
        drafted_citation=drafted,
    )
