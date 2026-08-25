"""Makes `import intake` / `import main` resolve when running
`pytest services/intake` from anywhere (e.g. the repo root).

`pyproject.toml`'s `pythonpath` list (root-owned, outside RELAY's paths)
puts `services` on `sys.path`, which is enough for a flat module like
`services/agent-core/main.py` but not for this service's nested
`services/intake/intake/` package -- that needs `services/intake` itself on
the path. This file is self-contained so `pytest services/intake` works
whether or not the root config ever adds this exact directory.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
