"""Reload `klights_live` and start it. The entry point the bridge sends.

The reload is the point: the editor caches imported modules for its whole
session, so without it an edit to `klights_live.py` would appear to do nothing
and the obvious conclusion -- "my change is wrong" -- would be false.

Stopping BEFORE the reload is equally deliberate. `klights_live_state` holds the
running driver precisely so a reload cannot orphan it, but stopping first keeps
the ordering honest even if that module is ever reloaded too: an orphaned
receive socket stays bound to 6454 with SO_REUSEADDR set, Windows keeps
delivering packets to it instead of to the new one, and the previz goes silent
while reporting itself perfectly healthy.
"""

import importlib
import sys

import klights_live

klights_live.stop()

for name in ("engine.config", "engine.geometry", "engine.venue", "engine.rig",
             "engine.servo", "previz.config", "previz.scene"):
    if name in sys.modules:
        importlib.reload(sys.modules[name])
importlib.reload(klights_live)

from previz import config as previz_config    # noqa: E402  (after the reloads)

klights_live.start(previz_config.event())
