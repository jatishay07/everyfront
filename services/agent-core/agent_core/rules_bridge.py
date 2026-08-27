"""Bridge to packages/rules (owned by STATUTE, persona 3).

REWRITTEN 2026-08-26 (FORGE directive, persona 5 WO8, "delegate, don't
reimplement"): this module used to carry its own vendored dataclasses
(`FrontDecision`/`AuditFinding`/`DenialCheck`) and a full fallback
reimplementation of `select_fronts`/`audit_line_items`/
`check_denial_lawfulness`, used only if the real `rules.*` import ever
failed. That fallback INDEPENDENTLY carried the exact "an unresolved
hospital (`{}`) defaults to nonprofit=True" bug STATUTE just fixed in the
real `rules.fronts._select_charity_care` (ef-2026-0006) -- proof, in the
same PR that fixed it, of the failure mode §2.1 warns against: "all
front-selection logic lives in packages/rules as pure functions" is not a
style preference, it is what keeps a fix like STATUTE's from being undone
by a second, forgotten copy of the same decision living in agent-core.

This module now does nothing but import and re-export STATUTE's real
functions directly -- exactly what `compute_deadlines`/`screen_eligibility`
already did below from the start ("re-exported directly, no bridging
needed"). No fallback, no vendored dataclasses, no conditional import: if
`rules.fronts`/`rules.audit`/`rules.denial` ever fail to import, this fails
LOUDLY at import time (the same posture `delivery_bridge.py` already takes
for RELAY's package, per that module's own docstring) instead of silently
degrading to a second, divergent copy of the law that can only ever be
wrong in the same way a real fix is right.
"""

from __future__ import annotations

from rules.audit import audit_line_items, total_savings_cents  # noqa: F401 -- re-exported
from rules.deadlines import compute_deadlines  # noqa: F401 -- re-exported
from rules.denial import check_denial_lawfulness  # noqa: F401 -- re-exported
from rules.eligibility import screen_eligibility  # noqa: F401 -- re-exported
from rules.fronts import (
    describe_patient_data_gap,  # noqa: F401 -- re-exported
    select_fronts,  # noqa: F401 -- re-exported
)


def bridge_sources() -> dict[str, str]:
    """For the events log / debugging -- which STATUTE module backs each of
    these. There is no fallback any more (see module docstring): every
    caller (strategist.py, auditor.py) already reads this into its fact's
    `source` field for the audit trail, so it stays a function -- returning
    which real module answered, not just the constant string "STATUTE" --
    rather than forcing those callers to change.
    """
    return {
        "select_fronts": "rules.fronts.select_fronts (STATUTE)",
        "audit_line_items": "rules.audit.audit_line_items (STATUTE)",
        "check_denial_lawfulness": "rules.denial.check_denial_lawfulness (STATUTE)",
        "total_savings_cents": "rules.audit.total_savings_cents (STATUTE)",
    }


__all__ = [
    "audit_line_items",
    "bridge_sources",
    "check_denial_lawfulness",
    "compute_deadlines",
    "screen_eligibility",
    "describe_patient_data_gap",
    "select_fronts",
    "total_savings_cents",
]
