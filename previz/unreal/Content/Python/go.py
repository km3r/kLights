"""Build the level and start the live driver. The one command to run.

    python previz/ue_remote.py previz/unreal/Content/Python/go.py

Safe to re-run at any time: it rebuilds from the event config and re-attaches
the driver to the new actors. Run it after editing venue.json / rig.json /
calibration.json, or after editing anything under Content/Python.

You do NOT need to press Play. The driver runs on the editor's tick, so the
previz is live in the ordinary editor viewport. Play works too, but it is not
how this is meant to be used and it puts the editor in a mode where you cannot
edit anything.
"""

import importlib
import sys

import build_level
import cosmos_live

# Reload both, so editing either one and re-running actually takes effect --
# the editor caches imported modules for its whole session.
cosmos_live.stop()
# engine.config first: engine.venue and engine.rig both import it, and reloading
# a module without reloading what it imported leaves the old objects in place --
# so a change to a config schema would not take effect until the editor was
# restarted, which is exactly the thing this list exists to avoid.
for name in ("engine.config", "engine.geometry", "engine.venue", "engine.rig",
             "engine.servo", "previz.scene", "previz.mirrorball"):
    if name in sys.modules:
        importlib.reload(sys.modules[name])
importlib.reload(build_level)
importlib.reload(cosmos_live)

build_level.main(restart_hint=False)      # we restart it ourselves, below
cosmos_live.start()

print("[cosmos] ready. Now send it some DMX, e.g.")
print("[cosmos]   python previz/ball_check.py --artnet 127.0.0.1 --seconds 300")
print("[cosmos]   python -m engine.demo --artnet 127.0.0.1 --seconds 300")
