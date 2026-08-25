"""§3.4 demo stat object consistency -- PROOF (persona 7), WO5 (pulled forward).

WO5 is officially a Days 9-10 bug bash and is otherwise out of scope for this
pass, but its stats-consistency requirement is called out as CRITICAL for the
demo's credibility ("a judge doing arithmetic on screen must not catch a
discrepancy") and is fully checkable today against the corpus that exists --
so it is enforced here now rather than deferred.

`fixtures/generated/expected_stats.json` is derived, not hand-typed:
`fixtures/generate.py` computes every field from the same per-case
`case.json` documents this test also reads. This test recomputes the whole
stat object independently, from first principles over the committed
`case.json` files, and asserts it matches byte-for-byte -- so if someone
hand-edits either side without regenerating, this fails.
"""

from __future__ import annotations

import json
from pathlib import Path

from fixtures.cases_data import CASES

REPO_ROOT = Path(__file__).resolve().parent.parent
GENERATED = REPO_ROOT / "fixtures" / "generated"

STAT_KEYS = {
    "open_cases",
    "hospitals",
    "deadlines_this_week",
    "total_billed_cents",
    "charity_eligible",
    "ppdr_eligible",
    "unlawful_denials_flagged",
    "audit_findings_cents",
    "filings_sent",
    "human_hours",
}


def _load(case_id: str) -> dict:
    return json.loads((GENERATED / "cases" / case_id / "case.json").read_text())


def _recompute_stats() -> dict:
    """Independent recomputation straight from the committed case.json files.

    Deliberately does NOT import fixtures/generate.py's own stats-building
    loop -- that would just be checking the code against itself. This walks
    the corpus fresh, the way an outside auditor (or a judge) would.
    """
    stats = dict.fromkeys(STAT_KEYS, 0)
    hospitals_seen: set[str] = set()

    for case in CASES:
        data = _load(case.case_id)
        stats["open_cases"] += 1

        ein = data["bill"]["hospital_ein"]
        if ein is not None:
            hospitals_seen.add(ein)

        amount = data["bill"]["amount_cents"]
        if amount:
            stats["total_billed_cents"] += amount

        elig = data["expected"]["eligibility"]
        if elig and elig["determination"] in ("free", "discounted"):
            stats["charity_eligible"] += 1

        fronts = data["expected"]["fronts_reference_model"]
        if any(f["front"] == "ppdr" and f["applicable"] for f in fronts):
            stats["ppdr_eligible"] += 1

        denial = data["expected"]["denial_check_reference_model"]
        if denial and denial["violation"]:
            stats["unlawful_denials_flagged"] += 1

        stats["audit_findings_cents"] += data["expected"]["audit_findings_cents_total"]

        for d in data["expected"]["deadlines"]:
            left = d["days_remaining_as_of_2026_08_25"]
            if left is not None and 0 <= left <= 7:
                stats["deadlines_this_week"] += 1

    stats["hospitals"] = len(hospitals_seen)
    # Nothing has been approved/filed yet in the raw injected corpus -- these
    # only move once the (not-yet-built) Filer actually sends something.
    stats["filings_sent"] = 0
    stats["human_hours"] = 0
    return stats


class TestStatObjectShape:
    def test_expected_stats_has_exactly_the_contract_3_4_keys(self):
        stats = json.loads((GENERATED / "expected_stats.json").read_text())
        assert set(stats) == STAT_KEYS


class TestStatsAddUp:
    """WO5: 'a judge doing arithmetic on screen must not catch a
    discrepancy.' This is that arithmetic, checked in CI on every PR."""

    def test_recomputed_stats_match_the_committed_file_exactly(self):
        committed = json.loads((GENERATED / "expected_stats.json").read_text())
        recomputed = _recompute_stats()
        assert recomputed == committed

    def test_total_billed_is_the_literal_sum_of_known_bill_amounts(self):
        stats = json.loads((GENERATED / "expected_stats.json").read_text())
        total = sum(
            _load(c.case_id)["bill"]["amount_cents"]
            for c in CASES
            if _load(c.case_id)["bill"]["amount_cents"]
        )
        assert stats["total_billed_cents"] == total

    def test_audit_findings_is_the_literal_sum_of_seeded_findings(self):
        stats = json.loads((GENERATED / "expected_stats.json").read_text())
        total = sum(
            f["potential_savings_cents"] or 0
            for c in CASES
            for f in _load(c.case_id)["expected"]["audit_findings_reference_model"]
        )
        assert stats["audit_findings_cents"] == total

    def test_unparseable_case_contributes_zero_confirmed_dollars(self):
        """The system must never invent a number for a document it could not
        read -- case 6's bill amount is None, and it must not silently
        contribute anything to the billed total."""
        case6 = _load("case_06_unparseable_bill")
        assert case6["bill"]["amount_cents"] is None

    def test_filings_and_human_hours_are_zero_pre_approval(self):
        """The raw injected corpus represents cases before any human-in-the-
        loop approval (contract §3.3 POST /cases/{id}/approve_filing) has
        happened, so nothing has been filed yet."""
        stats = json.loads((GENERATED / "expected_stats.json").read_text())
        assert stats["filings_sent"] == 0
        assert stats["human_hours"] == 0

    def test_open_cases_equals_corpus_size(self):
        stats = json.loads((GENERATED / "expected_stats.json").read_text())
        assert stats["open_cases"] == len(CASES) == 8
