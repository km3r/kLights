"""
Golden vectors the previz app's C++ is held to.

    python shared/tools/gen_previz_parity.py            # write them
    python shared/tools/gen_previz_parity.py --check    # exit 1 if stale

The previz app decodes DMX in C++, and the show decodes it in Python. Two
implementations of one decode is how drift starts, so the Python stays THE
DEFINITION and this file records what it says, case by case, for the C++
automation tests (`KLights.Parity.*`) to reproduce:

  * decode  -- `engine.scene.decode_aim` + `beam_direction` over (pan, tilt)
               words, for the despacio reference heads AND synthetic heads in
               every mount mode, both elevation anchors, every invert and the
               uncalibrated branch. Despacio alone is four calibrated heads in
               one mode, which is how a C++ port passes every real case and
               still fails three synthetic heads by exactly 360 degrees.
  * servo   -- `engine.servo.Servo.follow` stepped at fixed dt, from the rates
               the manifest carries (16-bit words per second).
  * color   -- `engine.scene.fixture_output`: level, color and split for the
               real profiles' color systems, every wheel value.
  * words   -- `engine.scene.channel_word`: coarse/fine assembly.
  * manifest -- the whole manifest of `engine/tests/data/events/sample`, so
               the C++ parser is tested against a real one.

Deliberately built from FROZEN inputs -- `geometry.despacio_reference_rig`, the
test sample event, the shared profiles -- never from `events/despacio`. A
golden file that went stale every time a head was recalibrated would be
regenerated without being read, which is a guard that guards nothing.

`engine/tests/test_scene.py` runs `--check`, so CI fails when this is stale.
"""

from __future__ import annotations

import json
import math
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine import geometry as geo          # noqa: E402
from engine import rig as rigmod            # noqa: E402
from engine import scene                    # noqa: E402
from engine import servo as servomod        # noqa: E402

OUT = REPO / "engine" / "tests" / "data" / "previz_parity.json"
SAMPLE = REPO / "engine" / "tests" / "data" / "events" / "sample"

# Where the edges are: both ends, either side of the 8-bit coarse boundary,
# either side of centre, and a few interior points.
WORDS = [0, 1, 255, 256, 12288, 32767, 32768, 32769, 47000, 65279, 65535]


def decode_vectors() -> list[dict]:
    rigs = []
    reference = geo.despacio_reference_rig("venue")
    for i in range(len(reference.heads)):
        rigs.append((f"despacio-reference {reference.heads[i].name}",
                     rigmod.Rig(name="ref", fixtures=(), geometry=reference), i))
    for mode in geo.MOUNT_PROFILES:
        for pan_invert in (False, True):
            for tilt_invert in (False, True):
                for calibrated in (True, False):
                    head = geo.Head(name="synthetic", x=1000.0, z=2000.0, height=3000.0,
                                    calibrated_ball_dmx=(200, 30) if calibrated else None,
                                    pan_invert=pan_invert, tilt_invert=tilt_invert,
                                    mount_facing_override=None if calibrated else -358.0)
                    g = geo.RigGeometry(heads=(head,), ball=(4572.0, 2743.0, 4572.0),
                                        mount_mode=mode)
                    rigs.append((f"synthetic {mode} pan_invert={pan_invert} "
                                 f"tilt_invert={tilt_invert} calibrated={calibrated}",
                                 rigmod.Rig(name="syn", fixtures=(), geometry=g), 0))
    out = []
    for name, rig, head in rigs:
        block = scene._decode_block(rig, head)
        cases = []
        for pan in WORDS:
            for tilt in WORDS:
                bearing, elev = scene.decode_aim(block, pan, tilt)
                d = scene.beam_direction(bearing, elev)
                cases.append([pan, tilt, float(bearing), float(elev), *map(float, d)])
        out.append({"name": name, "decode": block, "cases": cases})
    return out


def servo_vectors() -> list[dict]:
    rng = random.Random(20261001)
    out = []
    for pan_speed, tilt_speed, pan_range, tilt_range in (
            (servomod.DEFAULT_PAN_DEG_PER_S, servomod.DEFAULT_TILT_DEG_PER_S, 540.0, 270.0),
            (150.0, 90.0, 500.0, 270.0)):
        s = servomod.Servo(pan_range_deg=pan_range, tilt_range_deg=tilt_range,
                           pan_deg_per_s=pan_speed, tilt_deg_per_s=tilt_speed)
        steps = []
        for _ in range(60):
            target = (rng.choice([0, 65535, 32768, rng.randrange(65536)]),
                      rng.choice([0, 65535, 32768, rng.randrange(65536)]))
            for dt in (rng.choice([0.0, 1 / 60, 1 / 40, 0.1, 0.5]) for _ in range(rng.randrange(1, 6))):
                pan, tilt = s.follow(target[0], target[1], dt)
                steps.append([target[0], target[1], dt, float(pan), float(tilt)])
        out.append({"rates": [float(r) for r in s._rates()], "steps": steps})
    return out


def color_vectors(manifest: dict) -> list[dict]:
    """Every color system the shared profiles have, over its whole range."""
    out = []
    seen = set()
    for fixture in manifest["fixtures"]:
        key = (fixture["manufacturer"], fixture["model"], fixture["mode"])
        if key in seen:
            continue
        seen.add(key)
        spec = {"channels": fixture["channels"], "color_slots": fixture["color_slots"]}
        ch = fixture["channels"]
        frames = []
        if rigmod.COLOR_WHEEL in ch:
            for raw in range(256):
                for dim in (0, 1, 128, 255):
                    frame = {ch[rigmod.COLOR_WHEEL]: raw}
                    if rigmod.DIMMER in ch:
                        frame[ch[rigmod.DIMMER]] = dim
                    frames.append(frame)
        else:
            rng = random.Random(" / ".join(key))     # str seeds are stable across runs
            roles = [r for r in (rigmod.RED, rigmod.GREEN, rigmod.BLUE, rigmod.WHITE,
                                 rigmod.DIMMER) if r in ch]
            levels = [0, 1, 127, 128, 254, 255]
            for _ in range(300):
                frames.append({ch[r]: rng.choice(levels + [rng.randrange(256)]) for r in roles})
            frames.append({ch[r]: 0 for r in roles})
        cases = []
        for sparse in frames:
            buffer = [0] * 512
            for index, value in sparse.items():
                buffer[index] = value
            level, color, split = scene.fixture_output(spec, buffer)
            cases.append([[[i, v] for i, v in sorted(sparse.items())], float(level),
                          [float(c) for c in color],
                          None if split is None else [[float(c) for c in half] for half in split]])
        out.append({"name": " / ".join(key), "fixture": spec, "cases": cases})
    # And a fixture with no color system at all, which is white.
    spec = {"channels": {rigmod.DIMMER: 0}, "color_slots": []}
    cases = []
    for v in (0, 64, 255):
        level, color, split = scene.fixture_output(spec, [v] + [0] * 511)
        cases.append([[[0, v]], float(level), [float(c) for c in color], split])
    out.append({"name": "dimmer only", "fixture": spec, "cases": cases})
    return out


def word_vectors() -> list[list]:
    fixture = {"channels": {rigmod.PAN: 0, rigmod.PAN_FINE: 1, rigmod.TILT: 2}}
    out = []
    for coarse in (0, 1, 127, 128, 255):
        for fine in (0, 1, 255):
            frame = [coarse, fine, coarse] + [0] * 509
            out.append([coarse, fine,
                        scene.channel_word(fixture, frame, rigmod.PAN, rigmod.PAN_FINE),
                        scene.channel_word(fixture, frame, rigmod.TILT, rigmod.TILT_FINE)])
    return out


def build() -> bytes:
    manifest = scene.build_for(SAMPLE).manifest
    data = {
        "_comment": "Generated by shared/tools/gen_previz_parity.py -- do not edit.",
        "format": scene.FORMAT,
        "version": scene.VERSION,
        "decode": decode_vectors(),
        "servo": servo_vectors(),
        "color": color_vectors(manifest),
        "words": word_vectors(),
        "manifest": manifest,
    }
    return (json.dumps(data, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def first_difference(a, b, path: str = "") -> str:
    """Where `a` and `b` disagree, or '' if nowhere.

    Numbers agree within 1e-12. The file is written on one machine and checked
    on others, and libm differs between them in the last bit: Windows' sin and
    atan2 are not glibc's, so exact equality called the file stale on every
    Linux CI run. A real change -- a recalibration, a new head -- moves values
    by many orders of magnitude more than this.

    The manifest's `rev` is skipped: it is a hash OF those values, so the same
    last-bit wobble turns it into a different string, and everything it hashes
    is compared here anyway.
    """
    if isinstance(a, bool) or isinstance(b, bool):
        return "" if a == b else f"{path}: {a!r} != {b!r}"
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return "" if math.isclose(a, b, rel_tol=1e-12, abs_tol=1e-12) else f"{path}: {a!r} != {b!r}"
    if isinstance(a, dict) and isinstance(b, dict):
        if a.keys() != b.keys():
            return f"{path}: keys {sorted(a.keys() ^ b.keys())} differ"
        for key in a:
            if key == "rev":
                continue
            found = first_difference(a[key], b[key], f"{path}.{key}")
            if found:
                return found
        return ""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} entries != {len(b)}"
        for i, (x, y) in enumerate(zip(a, b)):
            found = first_difference(x, y, f"{path}[{i}]")
            if found:
                return found
        return ""
    return "" if a == b else f"{path}: {a!r} != {b!r}"


def main(argv: list[str]) -> int:
    data = build()
    if "--check" in argv:
        # Compared as JSON, not bytes: a Windows checkout with autocrlf hands
        # back CRLF and would otherwise read as stale on every machine but CI.
        current = json.loads(OUT.read_text(encoding="utf-8")) if OUT.is_file() else None
        moved = ("the file is missing" if current is None
                 else first_difference(current, json.loads(data)))
        if moved:
            print(f"stale: {OUT.relative_to(REPO)} ({moved}) -- run shared/tools/gen_previz_parity.py")
            return 1
        print(f"{OUT.relative_to(REPO)} is current")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_bytes(data)
    print(f"wrote {OUT.relative_to(REPO)} ({len(data) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
