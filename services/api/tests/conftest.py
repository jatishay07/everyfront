"""Local pytest bootstrap for services/api -- see
services/agent-core/tests/conftest.py for why this isn't wired into root
`pyproject.toml` (not SWARM's file to edit; flagged as a HANDOFF instead).

Run explicitly:
    .venv/bin/pytest services/api/tests
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
