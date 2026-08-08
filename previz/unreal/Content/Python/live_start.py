"""Reload `cosmos_live` and start it. The entry point the bridge sends.

The reload is the point: the editor caches imported modules for its whole
session, so without it an edit to `cosmos_live.py` would appear to do nothing
and the obvious conclusion -- "my change is wrong" -- would be false.

Stopping BEFORE the reload is equally deliberate. `cosmos_live_state` holds the
running driver precisely so a reload cannot orphan it, but stopping first keeps
the ordering honest even if that module is ever reloaded too: an orphaned
receive socket stays bound to 6454 with SO_REUSEADDR set, Windows keeps
delivering packets to it instead of to the new one, and the previz goes silent
while reporting itself perfectly healthy.
"""

import importlib
import sys

import cosmos_live

cosmos_live.stop()

for name in ("engine.geometry", "engine.venue", "engine.rig", "previz.scene"):
    if name in sys.modules:
        importlib.reload(sys.modules[name])
importlib.reload(cosmos_live)

cosmos_live.start()
