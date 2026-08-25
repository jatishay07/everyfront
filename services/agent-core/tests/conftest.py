"""Local pytest bootstrap for services/agent-core.

Root `pyproject.toml` scopes CI's `pytest -m "not e2e"` to `testpaths =
["tests", "packages"]`, so this directory is not auto-collected by the root
CI command -- that root config is FORGE's to own, not SWARM's (§0 rule 2:
"never modify files outside your owned paths"). Run these explicitly:

    .venv/bin/pytest services/agent-core/tests

HANDOFF -> FORGE: please add `services/agent-core` and `services/api` to
root `pyproject.toml`'s `pythonpath`/`testpaths` so CI covers both services
automatically -- flagged in the PR description too.

Historical note: this directory briefly shipped a hand-vendored copy of
packages/rules/rules (worked around `gcloud run deploy --source=` only
uploading one directory). That copy is gone -- FORGE's fix to
`infra/deploy.sh` (staging each service's declared `packages/` dependencies
into its own build context, see `pkgs_for`) made it unnecessary, and it was
actively dangerous while it existed: sys.path ordering let an unqualified
`import rules` in these tests silently resolve to the stale vendored copy
instead of STATUTE's real, actively-developed package. The explicit insert
below still forces `packages/rules` (the one true copy) to the front of
sys.path -- cheap insurance against anything else ever shadowing it again.
"""

from __future__ import annotations

import sys
from pathlib import Path

_AGENT_CORE_DIR = Path(__file__).resolve().parent.parent
_REPO_ROOT = _AGENT_CORE_DIR.parent.parent

sys.path.insert(0, str(_AGENT_CORE_DIR))
sys.path.insert(0, str(_REPO_ROOT / "packages" / "rules"))
