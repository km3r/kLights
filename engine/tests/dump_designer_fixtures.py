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
    __fixtures__/wave-vectors.json   every wave shape at sample positions,
                                     `hold`'s hashed levels included
    __fixtures__/audio-vectors.json  three small waveforms, one in each of
                                     rekordbox's formats, laid on a grid and
                                     shaped: the levels a lane following a
                                     band of the audio is drawn from
    __fixtures__/outputs.json        where OSC, MIDI and timecode go when
                                     show.json does not say, and timecode's
                                     frame rates: what Show settings offers
    __fixtures__/lane-slots.json     which automation can be attached to which
                                     kind of clips lane, as the engine's own
                                     validation decides it

And one file that is not a fixture but the UI's own source of truth for what a
block takes, read by the routine editor AND the console's Tweak card:

    ui/src/blocks.generated.json     every block's declared arguments
                                     (`blocks.PARAMS`), the shape macros
                                     (`params.MACROS`), the modulator shapes
                                     and the shapes a lane's wave can take
    ui/src/showfiles.generated.ts    the show-folder format's lists and ranges:
                                     automation targets, parameter types,
                                     curves, hits, visuals, the id pattern
    ui/src/commands.generated.ts     every command the engine takes: its
                                     arguments, what it answers with and
                                     who may send it (`@command`, see
                                     engine/commands.py)

Before it, the routine editor carried a hand-typed copy of every block's
defaults, steps and units (`BLOCK_ARGS`), and only the argument NAMES were held
to the engine. Generated, there is nothing left to drift.

    python engine/tests/dump_designer_fixtures.py

Commit the result. `test_api` fails if a fixture is stale, so a change to the
engine's lists cannot land without the designer's test seeing it.
"""

import base64
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import bands, blocks, library, modulate, params, showfiles, tracktime, waves  # noqa: E402
from engine import outputs as outputsmod  # noqa: E402

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
    }


# A parameter stands for every `param.<name>`: the lane rule for it does not
# depend on the name, and a timeline does not range-check one in-file.
LANE_SAMPLE_PARAM = "param.width"


def lane_slots() -> dict:
    """For each automation target and each kind of clips lane, whether a row
    attached to such a lane passes `showfiles.validate` -- the engine's
    verdict, not a copy of its rule -- and the slot `lane_slot` names."""
    targets = sorted(showfiles.AUTOMATION_RANGES) + [LANE_SAMPLE_PARAM]
    allowed: dict[str, list[str]] = {}
    for kind in showfiles.CLIP_TARGETS:
        ok = []
        for target in targets:
            lo = showfiles.AUTOMATION_RANGES.get(target, (0.0, 1.0))[0]
            doc = {"kind": "klights.timeline", "version": 1, "track": "t",
                   "rows": [{"id": "lane", "type": "clips", "target": kind,
                             "items": []},
                            {"id": "auto", "type": "automation", "target": target,
                             "points": [[0, lo]], "lane": "lane"}]}
            errors = showfiles.validate("timeline", doc).errors
            if not errors:
                ok.append(target)
            elif not any("lane" in e for e in errors):
                raise AssertionError(f"{target} on a {kind} lane: {errors}")
        allowed[kind] = ok
    return {"slots": {t: showfiles.lane_slot(t) for t in targets},
            "attachable": allowed}


def wave_vectors() -> dict:
    """The designer draws a lane's wave with its own copy of the shapes, and
    `hold` with its own copy of the hash -- these are what both must give."""
    positions = [-1.75, -0.5, 0.0, 0.1, 0.25, 0.49, 0.5, 0.75, 0.99, 1.0, 2.3, 17.6]
    # Rounded: a sine's last bits come from the platform's libm, which differs
    # between Windows and Linux, and test_api compares this file byte for byte
    # on both. Ten places is still far finer than any shape could drift by.
    return {shape: [[p, seed, round(waves.unit(shape, p, seed), 10)]
                    for p in positions for seed in (0, 7)]
            for shape in waves.SHAPES}


def _noise(seed: int, i: int, top: int) -> int:
    """A stable byte 0..top for column `i`: mostly quiet, with peaks."""
    x = (waves.sampled(seed, i) + 1.0) * 0.5
    return int(x * x * x * (top + 0.999))


def _waveform(fmt: str, columns: int) -> dict:
    if fmt == "pwv7":
        data = bytes(_noise(k, i, 127) for i in range(columns) for k in (1, 2, 3))
        return {"bands": {"format": fmt, "rate": 150,
                          "data": base64.b64encode(data).decode("ascii")}}
    if fmt == "pwv5":
        raw = bytearray()
        for i in range(columns):
            v = ((_noise(1, i, 7) << 13) | (_noise(2, i, 7) << 10)
                 | (_noise(3, i, 7) << 7) | (_noise(4, i, 31) << 2))
            raw += bytes([v >> 8, v & 0xFF])
        data = bytes(raw)
    else:
        data = bytes((_noise(5, i, 7) << 5) | _noise(4, i, 31) for i in range(columns))
    return {"detail": {"format": fmt, "rate": 150,
                       "data": base64.b64encode(data).decode("ascii")}}


AUDIO_CASES = (
    ("three bands, with a pickup before the first downbeat", "pwv7", 330,
     [[0, 250.0, 128.0]]),
    ("the color waveform, through a tempo change", "pwv5", 300,
     [[0, 0.0, 120.0], [2, 1000.0, 174.0]]),
    ("the blue waveform: the overall level alone", "pwv3", 240,
     [[-2, 0.0, 100.0]]),
)
AUDIO_SHAPES = ((0.0, 1.0, 0.0), (0.25, 0.8, 0.0), (0.0, 1.0, 0.5), (0.1, 0.6, 2.0))


def audio_vectors() -> dict:
    """The designer draws a lane that follows the audio from its own copy of
    `bands.py` -- the decoding, the pooling onto beats and the shaping. These
    are what both must give. Only + - * / and max, so the numbers are the same
    on every platform; rounded only to keep the file short."""
    cases = []
    for name, fmt, columns, segments in AUDIO_CASES:
        doc = _waveform(fmt, columns)
        grid = tracktime.Grid.from_segments(segments)
        audio = bands.Audio(bands.decode(doc), grid.time_at, grid.beat_at)
        cases.append({
            "name": name, "doc": doc, "segments": segments,
            "exact": audio.exact, "bands": list(audio.bands), "first": audio.first,
            "pooled": {b: [round(v, 12) for v in audio.pooled(b)] for b in audio.bands},
            "envelopes": [
                {"band": b, "floor": floor, "ceiling": ceiling, "release": release,
                 "cells": [round(v, 12) for v in
                           audio.envelope(b, floor, ceiling, release).cells]}
                for b in audio.bands[:1] + audio.bands[-1:]
                for floor, ceiling, release in AUDIO_SHAPES],
        })
    return {"step": bands.STEP, "bands": list(bands.BANDS), "cases": cases}


def render() -> dict[str, str]:
    """Each fixture's file name and exact contents."""
    return {name: json.dumps(data, indent=1) + "\n" for name, data in (
        ("grid-vectors.json", grid_vectors()), ("blocks.json", block_lists()),
        ("wave-vectors.json", wave_vectors()),
        ("outputs.json", outputsmod.defaults()),
        ("lane-slots.json", lane_slots()))} | {
        # One line a case: a thousand numbers down the page help nobody.
        "audio-vectors.json": json.dumps(audio_vectors(), separators=(",", ":"))
        .replace('{"name"', '\n{"name"') + "\n"}


def block_table() -> dict:
    """What the UI renders block and macro controls from."""
    return {
        "blocks": blocks.publish(),
        "macros": params.publish(params.MACROS),
        "modulator_shapes": list(modulate.SHAPES),
        "wave_shapes": list(waves.SHAPES),
    }


def showfiles_typescript() -> str:
    """The show-folder format's vocabulary, as Studio checks and offers it: the
    lists and ranges `showfiles` validates against, so the editor cannot offer
    what a save would refuse. Literal types, so a name the format does not
    have does not compile."""
    def js(value) -> str:
        return json.dumps(value, ensure_ascii=False)

    ranges = ",\n".join(f"  {js(k)}: [{lo:g}, {hi:g}]"
                        for k, (lo, hi) in showfiles.AUTOMATION_RANGES.items())
    visuals = ",\n".join(
        f"  {scene}: {{ " + ", ".join(f"{k}: {js(list(v) if isinstance(v, tuple) else v)}"
                                       for k, v in rules.items()) + " }"
        for scene, rules in showfiles.VISUAL_PARAMS.items())
    return f'''/**
 * The show-folder format's vocabulary: the lists and ranges engine/showfiles.py
 * checks a timeline, a routine or a template set against, so Studio offers
 * only what a save will accept.
 *
 * GENERATED by engine/tests/dump_designer_fixtures.py. Do not edit: change
 * engine/showfiles.py and run that script. `test_api` fails while this file is
 * out of date.
 */

/** An id, which is also the file's name (`showfiles.ID_RE`): anchor it with
 *  `^` and `$`. */
export const ID_PATTERN = {js(showfiles.ID_PATTERN)};

/** What a timeline's automation lane may drive besides a routine's
 *  parameters, and the range each is held to. */
export const AUTOMATION_RANGES: Readonly<Record<string, readonly [number, number]>> = {{
{ranges},
}};

/** What a routine's open parameter may be. */
export const PARAM_TYPES = {js(list(showfiles.PARAM_TYPES))} as const;

/** How an automation point shapes the segment arriving at it. */
export const CURVES = {js(list(showfiles.CURVES))} as const;

/** What a hit can be. */
export const HIT_KINDS = {js(list(showfiles.HITS))} as const;

/** The roles a palette names its colors by. */
export const PALETTE_ROLES = {js(list(showfiles.PALETTE_ROLES))} as const;

/** The blocks that name something only one rig has: a routine using one is
 *  "this rig only". */
export const RIG_BOUND_BLOCKS = {js(list(showfiles.RIG_BOUND_BLOCKS))} as const;

/** The built-in visuals' scenes. */
export const VISUAL_SCENES = {js(list(showfiles.VISUAL_SCENES))} as const;

/** What a visuals scene's parameter is: a color, a media file, true/false, one
 *  of a list, or a [min, max] range. Every scene also takes `opacity`. */
export type VisualRule = "color" | "file" | "bool" | readonly string[] | readonly [number, number];

/** Each visuals scene's parameters. */
export const VISUAL_PARAMS: Readonly<Record<string, Readonly<Record<string, VisualRule>>>> = {{
{visuals},
}};
'''


def render_ui() -> dict[str, str]:
    """Generated UI source, by name under ui/src."""
    from engine import commands
    from engine.controller import ShowController
    return {"blocks.generated.json":
            json.dumps(block_table(), indent=1, ensure_ascii=False) + "\n",
            "commands.generated.ts": commands.typescript(ShowController.COMMANDS),
            "showfiles.generated.ts": showfiles_typescript()}


def main() -> int:
    FIXTURES.mkdir(parents=True, exist_ok=True)
    for name, text in render().items():
        # LF, as .gitattributes pins everything under ui/src.
        (FIXTURES / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {(FIXTURES / name).relative_to(REPO)}")
    for name, text in render_ui().items():
        (UI_SRC / name).write_text(text, encoding="utf-8", newline="\n")
        print(f"wrote {(UI_SRC / name).relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
