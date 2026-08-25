"""Contract drift guard -- FORGE (persona 0) owns this file.

BUILD_PLAYBOOK.md §3 is the single source of truth for every interface between
agents, and §0 rule 3 says an agent that finds a contract wrong must *propose a
change*, not silently diverge. This test makes silent divergence fail CI.

PROOF (persona 7) owns the rest of tests/; this file is the bootstrap guard and
should be left in place.
"""

from __future__ import annotations

import pathlib
import re

REPO = pathlib.Path(__file__).resolve().parent.parent
PLAYBOOK = (REPO / "BUILD_PLAYBOOK.md").read_text(encoding="utf-8")
ENV_EXAMPLE = (REPO / ".env.example").read_text(encoding="utf-8")

# §3.2 Pub/Sub topics.
EXPECTED_TOPICS = {
    "intake.email.received",
    "case.document.added",
    "case.analysis.complete",
    "filing.requested",
    "filing.completed",
}

# §3.4 the demo stat object -- "the 40% criterion on screen".
EXPECTED_STAT_KEYS = {
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

# Every money field in §3.1 is denominated in CENTS. Mixing units silently is a
# 100x error in a number that decides whether someone's bill gets erased, so the
# suffix is load-bearing rather than stylistic.
MONEY_FIELDS = {
    "annual_income_cents",
    "amount_cents",
    "gfe_amount_cents",
    "savings_found_cents",
    "audit_findings_cents",
    "total_billed_cents",
}

# §1.2 the four legal fronts -- also the `fronts[].front` enum in §3.1.
EXPECTED_FRONTS = {"charity_care", "ppdr", "debt_validation", "audit"}


def test_playbook_still_declares_every_topic() -> None:
    for topic in EXPECTED_TOPICS:
        assert topic in PLAYBOOK, f"§3.2 topic {topic!r} vanished from the playbook"


def test_env_example_covers_every_topic() -> None:
    """Every §3.2 topic must be configurable; ATLAS creates these in setup.sh."""
    for topic in EXPECTED_TOPICS:
        assert topic in ENV_EXAMPLE, f"{topic!r} missing from .env.example"


def test_stat_object_keys_match_playbook() -> None:
    """The stats banner is the demo's headline number -- keys must not drift."""
    block = re.search(r'\{"open_cases".*?\}', PLAYBOOK, re.S)
    assert block, "§3.4 stat object not found in playbook"
    found = set(re.findall(r'"(\w+)":', block.group(0)))
    assert found == EXPECTED_STAT_KEYS, f"§3.4 drift: {found ^ EXPECTED_STAT_KEYS}"


def test_front_names_match_playbook() -> None:
    for front in EXPECTED_FRONTS:
        assert f'"{front}"' in PLAYBOOK, f"front {front!r} missing from §3.1 enum"


def test_locked_model_ids_are_declared() -> None:
    """§1.4 is locked. If these change, it is a playbook amendment, not a tweak.

    Both IDs were verified live against generativelanguage.googleapis.com on
    2026-08-21 (gate (c), model half). `gemma-3-27b-it` returned HTTP 404 and
    was amended to `gemma-4-26b-a4b-it`. See docs/SPIKE.md.
    """
    for model in ("gemini-3.7-flash", "gemma-4-26b-a4b-it"):
        assert model in PLAYBOOK, f"§1.4 model {model!r} missing from playbook"
        assert model in ENV_EXAMPLE, f"§1.4 model {model!r} missing from .env.example"


def test_every_money_field_is_denominated_in_cents() -> None:
    """A money field without a _cents suffix is a unit ambiguity waiting to bite.

    `annual_income` was exactly that until 2026-08-25 -- the only money field in
    §3.1 lacking the suffix, which invited a reader to pass dollars.
    """
    for field in MONEY_FIELDS:
        assert field in PLAYBOOK, f"§3 money field {field!r} missing from the playbook"
    assert "annual_income," not in PLAYBOOK, (
        "bare `annual_income` reintroduced -- every money field must be _cents"
    )
