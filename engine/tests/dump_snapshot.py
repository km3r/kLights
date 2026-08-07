"""
Capture a real engine snapshot as a UI test fixture.

The UI tests need a realistic state to render, and a hand-written one is a
liability: it drifts from the protocol silently, and then the tests pass against
a shape the engine no longer produces. Generating it from a running engine means
the fixture is wrong only if the engine is wrong, and regenerating is one
command when the protocol changes.

    python engine/tests/dump_snapshot.py

Writes ui/src/__fixtures__/despacio.json. Commit the result -- the UI tests must
not need a Python engine to run.
"""

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine.server import ShowController

OUT = REPO / "ui" / "src" / "__fixtures__" / "despacio.json"


def main() -> int:
    controller = ShowController(REPO / "events" / "despacio")
    controller.start()
    try:
        # Let a few frames run so the snapshot carries real evaluated state --
        # aims, safety tapers, landings -- rather than the zeroth frame, which
        # would exercise none of the UI's interesting branches.
        time.sleep(0.6)

        # Drive it into a state that covers the cases the UI has to handle:
        # a held look, an auto axis on, a colour override, and a jogging head
        # with its safety bypass. A snapshot of an idle engine would leave all
        # of those untested.
        for command in (
            {"type": "select_look", "name": "Lazy Circle"},
            {"type": "auto", "axis": "palette", "on": True},
            {"type": "color", "target": "pinspots", "color": [1.0, 0.2, 0.1]},
            {"type": "jog", "fixture": "Moving Head #1", "pan": 47, "tilt": 69},
            {"type": "capture", "fixture": "Moving Head #1",
             "target": list(controller.rig.venue.ball), "label": "mirror ball"},
            {"type": "hello", "name": "phone"},
        ):
            controller.submit(command, None)
        time.sleep(0.4)

        snapshot = controller.snapshot()
    finally:
        controller.stop()

    # Presence is per-connection and there is no socket here, so synthesise the
    # two peers the multi-user UI needs to render.
    snapshot["presence"] = [
        {"id": "c1", "name": "phone", "connected_for": 41.2,
         "last_action": "selected 'sweep'", "last_action_ago": 3.1},
        {"id": "c2", "name": "tablet", "connected_for": 12.8,
         "last_action": "coloured pinspots", "last_action_ago": 0.9},
    ]
    # Frame counters vary per run; pin them so a regenerated fixture produces a
    # clean diff instead of noise on every line.
    snapshot["stats"] = {"fps": 40.0, "frames": 1000, "drops": 0,
                         "eval_errors": 0, "worst_error_ms": 0.31}
    snapshot["rev"] = 7

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(snapshot, indent=2) + "\n", encoding="utf-8")

    movers = sum(1 for f in snapshot["fixtures"] if f.get("is_mover"))
    print(f"wrote {OUT.relative_to(REPO)}")
    print(f"  {len(snapshot['fixtures'])} fixtures ({movers} movers), "
          f"{len(snapshot['looks'])} looks, {len(snapshot['palette'])} colours")
    print(f"  look={snapshot['auto']['look']!r} held={snapshot['auto']['held']} "
          f"overrides={list(snapshot['color_overrides'])}")
    print(f"  jogging={[f['name'] for f in snapshot['fixtures'] if f.get('jogging')]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
