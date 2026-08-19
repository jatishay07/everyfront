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

    NOTE: FORGE has not yet confirmed these IDs resolve against a live endpoint
    -- that is gate (c) of work order 1, blocked on GCP billing. See docs/SPIKE.md.
    """
    for model in ("gemini-3.7-flash", "gemma-3-27b-it"):
        assert model in PLAYBOOK, f"§1.4 model {model!r} missing from playbook"
        assert model in ENV_EXAMPLE, f"§1.4 model {model!r} missing from .env.example"
