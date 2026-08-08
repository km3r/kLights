"""
Tests for the ported look library.

The claim that has to hold is ROUND-TRIP: a pose ported out of the workspace,
loaded back, and encoded must produce the DMX the workspace stored. If that
fails, the library aims somewhere the show was never focused, and every look
built on it is subtly wrong in a way nobody would notice until the room.

Run: python engine/tests/test_library.py
"""

import json
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import geometry as geo
from engine import library as libmod
from engine import motion, state as statemod
from engine.rig import load_rig
from shared.tools.port_library import NS, Porter, parse_values, snap_bars

EVENT = REPO / "events" / "despacio"
failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


rig = load_rig(EVENT)
entries = libmod.load_entries(EVENT / "looks.json")
by_name = {e.name: e for e in entries}

print("\n1. the library loaded")
kinds: dict[str, int] = {}
for e in entries:
    kinds[e.kind] = kinds.get(e.kind, 0) + 1
print(f"  {len(entries)} entries: " + ", ".join(f"{v} {k}" for k, v in sorted(kinds.items())))
check("the port is broad, not a hand-picked handful", len(entries) > 150,
      f"{len(entries)} entries")
check("every kind is represented",
      {"pose", "path", "color", "color_path"} <= set(kinds), f"{sorted(kinds)}")


# -- 2. ROUND-TRIP: a ported pose aims where the workspace aimed --------------
print("\n2. round-trip against the original workspace")
root = ET.parse(next(EVENT.glob("*.qxw"))).getroot()
engine_el = root.find(NS + "Engine")
scenes = {f.get("Name"): f for f in engine_el.findall(NS + "Function")
          if f.get("Type") == "Scene"}

compared = 0
worst = 0
mismatches: list[str] = []
for entry in entries:
    if entry.kind != "pose" or entry.name not in scenes:
        continue
    values_by_fixture = {int(fv.get("ID")): parse_values(fv.text or "")
                         for fv in scenes[entry.name].findall(NS + "FixtureVal")}
    for fixture in rig.movers:
        original = values_by_fixture.get(fixture.fid)
        if not original:
            continue
        offsets = fixture.profile.offsets(fixture.mode)
        pan_o, tilt_o = offsets[geo.__name__ and "pan"], offsets["tilt"]
        got = libmod.encode_entry(rig.geometry, entry, fixture.head)
        if got is None:
            continue
        want = ((original[pan_o] << 8) | original.get(offsets.get("pan_fine", -1), 0),
                (original[tilt_o] << 8) | original.get(offsets.get("tilt_fine", -1), 0))
        for axis, g, w in (("pan", got[0], want[0]), ("tilt", got[1], want[1])):
            delta = abs(g - w)
            worst = max(worst, delta)
            if delta > 256:            # one 8-bit step; the port rounds offsets
                mismatches.append(f"{entry.name}/{fixture.name}/{axis}: "
                                  f"{g} vs {w}")
        compared += 1

check("every ported pose round-trips to its original DMX", not mismatches,
      f"{compared} head-poses compared, worst delta {worst} of 65535"
      + (f"; e.g. {mismatches[0]}" if mismatches else ""))
check("enough poses were actually compared", compared > 200, f"{compared}")


# -- 2b. ROUND-TRIP: a ported COLOUR renders the workspace's colour bytes -----
#
# The position round-trip above is what the port was originally guarded by, and
# it is blind to colour by construction. That is exactly how the pinspots' white
# channel went missing: RGB matched, W was never read, and nothing complained.
# So drive each colour look through the REAL renderer and diff the colour bytes.
print("\n2b. colour round-trip (the gap that lost the white channel)")
from engine import rig as rigmod                                    # noqa: E402

ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
COLOUR_ROLES = (rigmod.RED, rigmod.GREEN, rigmod.BLUE, rigmod.WHITE,
                rigmod.COLOR_WHEEL)

colour_compared = 0
colour_bad: list[str] = []
white_seen = 0
for entry in entries:
    if not entry.is_color or entry.name not in scenes:
        continue
    values_by_fixture = {int(fv.get("ID")): parse_values(fv.text or "")
                         for fv in scenes[entry.name].findall(NS + "FixtureVal")}
    # The palette colour must not be able to mask a failure, so feed a colour
    # nothing in the library uses -- if a look falls through to the palette its
    # bytes will not match and we want to hear about it.
    frames = statemod.frame(ctx, libmod.build_look(entry).make((0.13, 0.29, 0.71)))
    for fixture in rig.fixtures:
        original = values_by_fixture.get(fixture.fid)
        if not original:
            continue
        offsets = fixture.profile.offsets(fixture.mode)
        for role in COLOUR_ROLES:
            off = offsets.get(role)
            if off is None or off not in original:
                continue
            got = frames[fixture.universe][fixture.address - 1 + off]
            want = original[off]
            if role is rigmod.WHITE and want > 0:
                white_seen += 1
            # One byte of slack: the port stores 0..1 rounded to 4 places and the
            # renderer scales back through 255.
            if abs(got - want) > 1:
                colour_bad.append(f"{entry.name}/{fixture.name}/{role}: "
                                  f"{got} vs {want}")
            colour_compared += 1

check("every ported colour renders the workspace's own bytes", not colour_bad,
      f"{colour_compared} channels compared"
      + (f"; e.g. {colour_bad[0]} ({len(colour_bad)} bad)" if colour_bad else ""))
check("the white channel is actually exercised", white_seen >= 8,
      f"{white_seen} non-zero W channels compared -- this is what regressed")
check("enough colour channels were compared", colour_compared > 200,
      f"{colour_compared}")

# Colour CHASES too. They are Chasers, not Scenes, so the loop above never sees
# them -- and "Pin Drift" walks the same RGBW pastels the Pin scenes hold, so a
# gap here loses exactly what the scene fix just recovered.
chasers = {f.get("Name"): f for f in engine_el.findall(NS + "Function")
           if f.get("Type") == "Chaser"}
scenes_by_id = {int(f.get("ID")): f for f in engine_el.findall(NS + "Function")
                if f.get("Type") == "Scene"}
step_compared = 0
step_bad: list[str] = []
for entry in entries:
    if entry.kind != "color_path" or entry.name not in chasers:
        continue
    originals = []
    for step in sorted(chasers[entry.name].findall(NS + "Step"),
                       key=lambda s: int(s.get("Number", 0))):
        scene = scenes_by_id.get(int((step.text or "0").strip()))
        if scene is None:
            continue
        values = {int(fv.get("ID")): parse_values(fv.text or "")
                  for fv in scene.findall(NS + "FixtureVal")}
        if any(values.values()):
            originals.append(values)
    look = libmod.build_look(entry).make((0.13, 0.29, 0.71))
    bars = entry.bars or 8.0
    for index, values_by_fixture in enumerate(originals[:len(entry.frames or [])]):
        # Land the phase in the middle of this frame's slot.
        ctx.motion_bar = bars * (index + 0.5) / len(entry.frames)
        rendered = statemod.frame(ctx, look)
        for fixture in rig.fixtures:
            original = values_by_fixture.get(fixture.fid)
            if not original:
                continue
            offsets = fixture.profile.offsets(fixture.mode)
            for role in COLOUR_ROLES:
                off = offsets.get(role)
                if off is None or off not in original:
                    continue
                got = rendered[fixture.universe][fixture.address - 1 + off]
                if abs(got - original[off]) > 1:
                    step_bad.append(f"{entry.name}[{index}]/{fixture.name}/{role}: "
                                    f"{got} vs {original[off]}")
                step_compared += 1
ctx.motion_bar = 0.0

check("each step of a colour chase renders its own step's bytes", not step_bad,
      f"{step_compared} channels across {len([e for e in entries if e.kind == 'color_path'])} chases"
      + (f"; e.g. {step_bad[0]} ({len(step_bad)} bad)" if step_bad else ""))


# -- 3. paths are continuous and musical --------------------------------------
print("\n3. paths")
paths = [e for e in entries if e.kind == "path"]
check("chaser durations became musical bar counts",
      all(e.bars in (0.25, 0.5, 1, 2, 4, 8, 16, 32) for e in paths),
      f"{sorted({e.bars for e in paths})}")

sample = next(e for e in paths if e.steps and len(e.steps) >= 4)
offset_fn = libmod.path_offsets(sample.steps, sample.bars or 8.0)


class FakeCtx:
    motion_bar = 0.0
    geometry = rig.geometry


ctx = FakeCtx()
values = []
for k in range(400):
    ctx.motion_bar = (sample.bars or 8.0) * k / 400
    values.append(offset_fn(ctx, 0))
biggest = max(abs(a[0] - b[0]) + abs(a[1] - b[1])
              for a, b in zip(values, values[1:]))
check(f"{sample.name!r} moves continuously", biggest < 5.0,
      f"largest step {biggest:.3f} deg over {len(sample.steps)} waypoints")

# The route must actually pass through the stored waypoints, or the port has
# preserved a shape that is not the one the chaser had.
first_waypoint = tuple(sample.steps[0][0])
closest = min(values, key=lambda v: abs(v[0] - first_waypoint[0])
              + abs(v[1] - first_waypoint[1]))
check("the route passes through its first waypoint",
      abs(closest[0] - first_waypoint[0]) < 0.5
      and abs(closest[1] - first_waypoint[1]) < 0.5,
      f"nearest {tuple(round(c, 2) for c in closest)} to {first_waypoint}")


# -- 4. per-fixture colour survived -------------------------------------------
print("\n4. colour")
splits = [e for e in entries if e.colors]
check("split/duo/quad colours ported as per-fixture", len(splits) >= 5,
      f"{len(splits)}: {[e.name for e in splits[:4]]}")
check("they really do differ between heads",
      all(len({tuple(c) for c in e.colors.values()}) > 1 for e in splits))

uniform = [e for e in entries if e.color]
check("uniform colours collapsed to one value", len(uniform) > 20,
      f"{len(uniform)}")

color_paths = [e for e in entries if e.kind == "color_path"]
check("colour chases ported as stepped frames", len(color_paths) >= 3,
      f"{[e.name for e in color_paths]}")


# -- 5. the library builds runnable looks -------------------------------------
print("\n5. every entry builds and evaluates")
setlist, _ = libmod.load_setlist(EVENT / "looks.json")
check("set list built", len(setlist.looks) == len(entries))

ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
ctx.motion_bar = 3.7
built = 0
broke: list[str] = []
for look in setlist.looks:
    try:
        show = look.make((1.0, 1.0, 1.0))
        states = statemod.evaluate(ctx, show)
        frames = statemod.render(ctx, states)
        assert any(any(f) for f in frames.values()) or look.manual_only
        built += 1
    except Exception as exc:                                   # noqa: BLE001
        broke.append(f"{look.name}: {type(exc).__name__} {exc}")
check("every look evaluates to a real frame", not broke,
      f"{built}/{len(setlist.looks)}" + (f"; first: {broke[0]}" if broke else ""))

# A look built for four heads must run on a rig with more, or the portability
# claim is empty. Indexing wraps rather than failing.
wide = [e for e in entries if e.offsets][0]
fn = libmod.pose_offsets(wide.offsets)
check("a 4-head look survives an 8-head rig",
      fn(ctx, 7) == fn(ctx, 7 % len(wide.offsets)),
      "head 7 wraps onto the library's own head 3")


# -- 6. the port is regenerable and stable ------------------------------------
print("\n6. regenerating is deterministic")
porter = Porter(EVENT)
again = porter.run(next(EVENT.glob("*.qxw")))
check("a fresh port produces the same count", len(again) == len(entries),
      f"{len(again)} vs {len(entries)}")
check("and the same names in the same order",
      [l.name for l in again] == [e.name for e in entries])
check("snap_bars picks the nearest musical length",
      snap_bars(3.9) == 4 and snap_bars(0.3) == 0.25 and snap_bars(30) == 32)


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("library: all checks pass")
