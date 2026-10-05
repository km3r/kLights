"""Double-click to open the kLights launcher (no console window).

The same as `python -m launcher`. Everything is in launcher/.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from launcher.gui import main  # noqa: E402

raise SystemExit(main())
