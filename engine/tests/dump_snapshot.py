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

from engine import server as servermod
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
        # All three slots filled, so the fixture proves they coexist -- a
        # snapshot with only a movement look would let a regression that wipes
        # the colour slot on selection pass unnoticed, which is the exact bug
        # the slot model was introduced to fix.
        for command in (
            {"type": "select_look", "name": "Lazy Circle"},      # movement
            {"type": "select_look", "name": "MH Red"},           # colour
            {"type": "select_look", "name": "Spotlight"},        # level chase
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
    # Presets, so the Show tab's bank grid renders in tests. Built from the
    # snapshot's OWN selection rather than typed out, because the hand-written
    # version of this had drifted: it still carried `"movement": "Lazy Circle"`
    # from before slots went per fixture group, so every UI test rendered a
    # preset shape the engine had not produced in months -- the exact failure
    # this file exists to prevent, in the one part that was not captured.
    #
    # Deliberately NOT saved through the controller: that would rewrite the real
    # events/despacio/presets.json, and a test fixture generator must not edit
    # the show. `arrange_presets` is the same function load_presets runs, so the
    # bank layout is still the engine's, not this file's.
    live = snapshot["selection"]
    slots = {s: dict(live[s]) for s in ("movement", "color", "level")}
    snapshot["presets"] = servermod.arrange_presets([
        # Bank 1 with a gap in it, and a second bank: between them these cover
        # every branch of the grid -- occupied pad, empty pad, page turn.
        {"name": "opener", **slots, "level": {}, "speed": 1.0, "master": 0.9,
         "bank": 1, "cell": 0, "tags": ["intro"]},
        {"name": "peak", **slots, "speed": 2.0, "master": 1.0,
         "bank": 1, "cell": 3, "tags": ["drop", "build"]},
        {"name": "landing", **slots, "color": {}, "speed": 0.5, "master": 0.6,
         "bank": 2, "cell": 0, "tags": ["ambient"]},
    ])
    snapshot["preset_banks"] = {"size": servermod.BANK_SIZE, "count": 2}

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
