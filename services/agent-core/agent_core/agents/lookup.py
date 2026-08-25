"""Lookup: resolve the hospital record for a case's bill.

Playbook §4 persona 5, WO1: "tool-calls into Firestore `hospitals/` + LEDGER's
MRF fetcher; resolves EIN/CCN; writes hospital facts + 'nonprofit: false -> no
501(r) front' honesty path."

DEFECT FIX (persona 5 WO2, 2026-08-25): this used to resolve by EIN only, via
`store.get_hospital(bill["hospital_ein"])`. That works for `/demo/inject_bill`
(the fixture already carries a real hospital_ein straight into the bill), but
it is exactly backwards for a real bill: a scanned/printed statement almost
never prints the hospital's EIN, so Reader's extraction leaves
`hospital_ein` blank and every real-world case dead-ended on "no EIN" even
though LEDGER seeded 200 real hospitals by NAME (`hospitals/{ein}.name`) that
the bill's `provider_name` should match directly (the fixtures use real
hospital names for exactly this reason: "Sutter Bay Hospitals", "Advocate
Christ Medical Center", "Stanford Health Care").

Resolution order now:
  1. EIN, when the bill has one -- fastest, most authoritative, unchanged.
  2. Provider name, normalized and matched against every seeded hospital's
     name (see `_HospitalDirectory` below) -- the realistic path.

A resolved-by-name hospital's EIN is written back onto the case's `bill` by
`agent_core.pipeline` (not here -- Lookup only reports facts) so later
lookups, the `/hospitals/{ein}` API, and the dashboard's per-hospital stat all
see a consistent EIN.

LEDGER's MRF fetcher (packages/datapipes) still is not wired into the
Firestore-lookup half here -- that is `auditor.py`'s cash-price-delta check,
which optionally reads `hospital["mrf_url"]` once Lookup has resolved it.
"""

from __future__ import annotations

import difflib
import re
import time

from .. import config
from ..store import store
from . import common

NAME = "lookup"

INSTRUCTION = (
    "You are Lookup, responsible for resolving which hospital a bill belongs to. "
    "Call get_lookup_result exactly once, then state in 1-2 sentences whether the "
    "hospital was resolved and whether it is nonprofit (and therefore subject to "
    "26 CFR 1.501(r)) or for-profit (no charity-care front)."
)

# Defect #3 (speed): a fresh Firestore read of all 200 seeded hospitals on
# every single lookup call is the kind of "redundant round-trip" the demo's
# 45s budget cannot afford when a case has several documents (each of which
# used to re-trigger the WHOLE Lookup->Clock->Auditor->Strategist cascade,
# see pipeline.py). This directory is loaded once per process and reused for
# every case until it goes stale -- hospital records change on the order of
# LEDGER's re-seed cadence, not per-request, so a short TTL is generous, not
# reckless.
_CACHE_TTL_S = 300.0

_PARENTHETICAL = re.compile(r"\([^)]*\)")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")

# Below this similarity score, a fuzzy name match is more likely to be a false
# positive (accidentally matching an unrelated hospital) than a real OCR/
# extraction variance -- and a WRONG hospital match is worse than "unresolved,
# say so honestly," per this whole codebase's "never guess" ethos.
_FUZZY_MATCH_THRESHOLD = 0.72


def _normalize_name(name: str) -> str:
    """Case/punctuation/parenthetical-insensitive key for hospital names.

    Strips bracketed annotations first (`"... (SYNTHETIC demo data)"`,
    `"... (FOR-PROFIT) -- SYNTHETIC FIXTURE"`) since those are fixture/demo
    provenance notes, not part of the hospital's actual name, and would
    otherwise sink an exact match.
    """
    s = _PARENTHETICAL.sub(" ", name.lower())
    s = _NON_ALNUM.sub(" ", s)
    return " ".join(s.split())


class _HospitalDirectory:
    """In-process cache of every seeded `hospitals/{ein}` record, indexed by
    EIN and by normalized name, refreshed at most every `_CACHE_TTL_S`.
    """

    def __init__(self) -> None:
        self._loaded_at = 0.0
        self._by_ein: dict[str, dict] = {}
        self._by_normalized_name: dict[str, str] = {}  # normalized name -> ein

    def _ensure_fresh(self) -> None:
        now = time.monotonic()
        if self._by_ein and (now - self._loaded_at) < _CACHE_TTL_S:
            return
        by_ein: dict[str, dict] = {}
        by_name: dict[str, str] = {}
        for record in store.list_hospitals():
            ein = record.get("ein")
            if not ein:
                continue
            by_ein[ein] = record
            norm = _normalize_name(record.get("name") or "")
            if norm:
                by_name[norm] = ein
        self._by_ein = by_ein
        self._by_normalized_name = by_name
        self._loaded_at = now

    def by_ein(self, ein: str) -> dict | None:
        self._ensure_fresh()
        record = self._by_ein.get(ein)
        # Firestore is the source of truth even inside the TTL window: an EIN
        # this in-process snapshot has never seen (e.g. seeded moments ago,
        # or a demo run's own `/demo/inject_bill` self-seed) must not read as
        # "not found" for up to 5 minutes. get_hospital is a single point
        # read, not the O(200) scan list_hospitals() does, so this stays
        # cheap.
        if record is not None:
            return record
        return store.get_hospital(ein)

    def by_name(self, provider_name: str) -> tuple[str, dict] | None:
        self._ensure_fresh()
        norm = _normalize_name(provider_name)
        if not norm:
            return None
        ein = self._by_normalized_name.get(norm)
        if ein is not None:
            return ein, self._by_ein[ein]

        best_ein: str | None = None
        best_score = 0.0
        for candidate_norm, candidate_ein in self._by_normalized_name.items():
            if norm in candidate_norm or candidate_norm in norm:
                score = min(len(norm), len(candidate_norm)) / max(len(norm), len(candidate_norm))
            else:
                score = difflib.SequenceMatcher(None, norm, candidate_norm).ratio()
            if score > best_score:
                best_score, best_ein = score, candidate_ein
        if best_ein is not None and best_score >= _FUZZY_MATCH_THRESHOLD:
            return best_ein, self._by_ein[best_ein]
        return None


_directory = _HospitalDirectory()


def _resolve_fact(case: dict) -> dict:
    bill = case.get("bill") or {}
    ein = (bill.get("hospital_ein") or "").strip()
    provider_name = (bill.get("provider_name") or "").strip()

    hospital: dict | None = None
    resolved_ein: str | None = None
    method: str | None = None
    attempts: list[str] = []

    if ein:
        hospital = _directory.by_ein(ein)
        if hospital is not None:
            resolved_ein, method = ein, "ein"
        else:
            attempts.append(f"no hospitals/{ein} record on file for the bill's own EIN")

    if hospital is None and provider_name:
        match = _directory.by_name(provider_name)
        if match is not None:
            resolved_ein, hospital = match
            method = "provider_name"
        else:
            attempts.append(f"no hospital record's name matched provider_name {provider_name!r}")

    if hospital is None:
        if not ein and not provider_name:
            note = (
                "bill carries neither a hospital_ein nor a provider_name -- "
                "cannot look up hospitals/{ein}"
            )
        else:
            note = (
                "hospital could not be resolved: "
                + "; ".join(attempts)
                + (" (LEDGER's 200-hospital seed may not cover this hospital)")
            )
        return {
            "resolved": False,
            "hospital": None,
            "ein": None,
            "method": None,
            "citations": [],
            "note": note,
        }

    nonprofit = hospital.get("nonprofit", True)
    method_note = "matched by provider name" if method == "provider_name" else "matched by EIN"
    if nonprofit:
        note = (
            f"{hospital.get('name', resolved_ein)} is a nonprofit hospital ({method_note}), "
            "subject to 26 CFR 1.501(r) financial-assistance obligations."
        )
        citations = ["26 CFR 1.501(r)-1(b)(29)(i)"]
    else:
        note = (
            f"{hospital.get('name', resolved_ein)} is FOR-PROFIT ({method_note}): it has no "
            "26 CFR 1.501(r) obligation, so the charity-care front does not apply here. Other "
            "fronts (PPDR, debt validation, audit) are unaffected."
        )
        citations = []
    return {
        "resolved": True,
        "hospital": hospital,
        "ein": resolved_ein,
        "method": method,
        "nonprofit": nonprofit,
        "citations": citations,
        "note": note,
    }


async def run(case_id: str, case: dict) -> dict:
    fact = _resolve_fact(case)
    fact["case_id"] = case_id
    tool = common.make_fact_tool(
        "get_lookup_result",
        "Return the resolved hospital record (or the honest reason it could not be resolved).",
        fact,
    )
    prompt = f"Resolve the hospital for case {case_id}. Call get_lookup_result and report back."
    turn = await common.run_agent_turn(NAME, config.GEMINI_MODEL, INSTRUCTION, [tool], prompt)
    return {"fact": fact, **turn}
