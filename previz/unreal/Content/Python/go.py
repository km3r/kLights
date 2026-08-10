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
import klights_live

# Reload both, so editing either one and re-running actually takes effect --
# the editor caches imported modules for its whole session.
klights_live.stop()
# engine.config first: engine.venue and engine.rig both import it, and reloading
# a module without reloading what it imported leaves the old objects in place --
# so a change to a config schema would not take effect until the editor was
# restarted, which is exactly the thing this list exists to avoid.
for name in ("engine.config", "engine.geometry", "engine.venue", "engine.rig",
             "engine.servo", "previz.config", "previz.scene",
             "previz.mirrorball"):
    if name in sys.modules:
        importlib.reload(sys.modules[name])
importlib.reload(build_level)
importlib.reload(klights_live)

# Resolved ONCE and handed to both, rather than letting each default. This
# script used to call these with no argument at all, which hardwired the
# documented one-command path to despacio even though both functions had taken
# an event since the day they were written.
from previz import config as previz_config    # noqa: E402  (after the reloads)

_event = previz_config.event()
print(f"[kLights] event {_event} "
      f"(from {previz_config.describe()['source']})")

build_level.main(_event, restart_hint=False)  # we restart it ourselves, below
klights_live.start(_event)

print("[kLights] ready. Now send it some DMX, e.g.")
print("[kLights]   python previz/ball_check.py --artnet 127.0.0.1 --seconds 300")
print("[kLights]   python -m engine.demo --artnet 127.0.0.1 --seconds 300")
