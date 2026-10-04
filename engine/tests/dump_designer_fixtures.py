"""
Capture what the designer must agree with the engine on, from the engine.

The designer draws a timeline in beats and plays audio in seconds, and the two
must convert exactly as the engine does -- a clip drawn on bar 41 has to be the
clip the engine plays on bar 41. Its routine editor offers the blocks the
engine builds, with the arguments the engine reads. So the designer's own code
(`ui/src/designer/model.ts`) is tested against files produced HERE, from
`engine/tracktime.py`, `engine/blocks.py` and `engine/showfiles.py`, rather
than against numbers and names written by hand:

    __fixtures__/grid-vectors.json   beat <-> seconds on three grids
    __fixtures__/blocks.json         blocks, their slots and numeric args,
                                     automation targets, param types

And one file that is not a fixture but the UI's own source of truth for what a
block takes, read by the routine editor AND the console's Tweak card:

    ui/src/blocks.generated.json     every block's declared arguments
                                     (`blocks.PARAMS`), the shape macros
                                     (`params.MACROS`), the modulator shapes

Before it, the routine editor carried a hand-typed copy of every block's
defaults, steps and units (`BLOCK_ARGS`), and only the argument NAMES were held
to the engine. Generated, there is nothing left to drift.

    python engine/tests/dump_designer_fixtures.py

Commit the result. `test_api` fails if a fixture is stale, so a change to the
engine's lists cannot land without the designer's test seeing it.
"""

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import blocks, library, modulate, params, showfiles, tracktime  # noqa: E402

FIXTURES = REPO / "ui" / "src" / "designer" / "__fixtures__"
UI_SRC = REPO / "ui" / "src"

GRIDS = {
    "steady": [[0, 250.0, 128.0]],
    "anacrusis": [[-2, 0.0, 120.0]],
    "tempo change": [[0, 0.0, 128.0], [64, 30000.0, 140.0], [128, 57428.57, 140.0]],
}


def grid_vectors() -> dict:
    out = {}
    for name, segments in GRIDS.items():
        grid = tracktime.Grid.from_segments(segments)
        times = [-1.0, 0.0, 0.1, 10.0, 29.9, 30.0, 45.0, 57.0, 80.0]
        beats = [-4.0, -1.0, 0.0, 0.5, 63.5, 64.0, 100.0, 128.0, 200.0]
        out[name] = {
            "segments": segments,
            "beat_at": [[t, grid.beat_at(t)] for t in times],
            "time_at": [[b, grid.time_at(b)] for b in beats],
            "bpm_at": [[t, grid.bpm_at(t)] for t in times],
        }
    return out


def block_lists() -> dict:
    return {
        "slots": dict(blocks.SLOT_OF),
        "numeric": {k: list(v) for k, v in blocks._NUMERIC.items()},
        "orders": list(blocks._ORDERS),
        "easings": list(library.EASINGS),
        "automation": {k: list(v) for k, v in showfiles.AUTOMATION_RANGES.items()},
        "param_types": list(showfiles.PARAM_TYPES),
    }


def render() -> dict[str, str]:
    """Each fixture's file name and exact contents."""
    return {name: json.dumps(data, indent=1) + "\n" for name, data in (
        ("grid-vectors.json", grid_vectors()), ("blocks.json", block_lists()))}


def block_table() -> dict:
    """What the UI renders block and macro controls from."""
    return {
        "blocks": blocks.publish(),
        "macros": params.publish(params.MACROS),
        "modulator_shapes": list(modulate.SHAPES),
    }


def render_ui() -> dict[str, str]:
    """Generated UI source, by name under ui/src."""
    return {"blocks.generated.json":
            json.dumps(block_table(), indent=1, ensure_ascii=False) + "\n"}


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, text in render().items():
        (FIXTURES / name).write_text(text, encoding="utf-8")
        print(f"wrote {(FIXTURES / name).relative_to(REPO)}")
    for name, text in render_ui().items():
        (UI_SRC / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {(UI_SRC / name).relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
