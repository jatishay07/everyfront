"""PROOF (persona 7) -- shared pytest configuration for tests/.

The repo's pyproject.toml puts `packages/rules` (etc.) on pythonpath but not
the repo root itself, and `tests/` has no `__init__.py` so pytest's default
"prepend" import mode only adds `tests/` to sys.path, not the repo root.
This conftest adds the repo root once so `import fixtures...` (PROOF's own
package, holding the synthetic corpus + generator + demo harness) resolves
from any test module without every test file repeating the same sys.path
hack.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
