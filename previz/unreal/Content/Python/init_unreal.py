"""
Runs automatically when the editor opens this project.

Its only job is to make the repo importable, so editor Python can `import
engine` and `import previz` and therefore decode pan/tilt with the show's own
calibration rather than a reimplementation of it. Deliberately tiny and
exception-safe: anything that throws in here fires on every editor start and is
easy to miss in the log.
"""

import sys
import traceback
from pathlib import Path

try:
    # .../previz/unreal/Content/Python/init_unreal.py -> repo root
    REPO = Path(__file__).resolve().parents[4]
    if (REPO / "engine" / "geometry.py").exists():
        if str(REPO) not in sys.path:
            sys.path.insert(0, str(REPO))
        print(f"[kLights] repo on sys.path: {REPO}")
    else:
        print(f"[kLights] WARNING: {REPO} does not look like the lights repo; "
              f"`import engine` will fail and previz cannot decode aims.")
except Exception:                                  # noqa: BLE001 - see docstring
    traceback.print_exc()
