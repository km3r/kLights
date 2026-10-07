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
from engine import motion, servo as servomod, state as statemod
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


# -- 2b. ROUND-TRIP: a ported COLOR renders the workspace's color bytes -----
#
# The position round-trip above is what the port was originally guarded by, and
# it is blind to color by construction. That is exactly how the pinspots' white
# channel went missing: RGB matched, W was never read, and nothing complained.
# So drive each color look through the REAL renderer and diff the color bytes.
print("\n2b. color round-trip (the gap that lost the white channel)")
from engine import rig as rigmod                                    # noqa: E402

ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
COLOR_ROLES = (rigmod.RED, rigmod.GREEN, rigmod.BLUE, rigmod.WHITE,
                rigmod.COLOR_WHEEL)

color_compared = 0
color_bad: list[str] = []
white_seen = 0
for entry in entries:
    if not entry.is_color or entry.name not in scenes:
        continue
    values_by_fixture = {int(fv.get("ID")): parse_values(fv.text or "")
                         for fv in scenes[entry.name].findall(NS + "FixtureVal")}
    # The palette color must not be able to mask a failure, so feed a color
    # nothing in the library uses -- if a look falls through to the palette its
    # bytes will not match and we want to hear about it.
    frames = statemod.frame(ctx, libmod.build_look(entry).make((0.13, 0.29, 0.71)))
    for fixture in rig.fixtures:
        original = values_by_fixture.get(fixture.fid)
        if not original:
            continue
        offsets = fixture.profile.offsets(fixture.mode)
        for role in COLOR_ROLES:
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
                color_bad.append(f"{entry.name}/{fixture.name}/{role}: "
                                  f"{got} vs {want}")
            color_compared += 1

check("every ported color renders the workspace's own bytes", not color_bad,
      f"{color_compared} channels compared"
      + (f"; e.g. {color_bad[0]} ({len(color_bad)} bad)" if color_bad else ""))
check("the white channel is actually exercised", white_seen >= 8,
      f"{white_seen} non-zero W channels compared -- this is what regressed")
check("enough color channels were compared", color_compared > 200,
      f"{color_compared}")

# Color CHASES too. They are Chasers, not Scenes, so the loop above never sees
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
        # set_phase, not motion_bar: color chases read the COLOR slot's
        # phase now, and a test that moved only the movement phase would
        # sample frame 0 of every chase forever and pass on nothing.
        ctx.set_phase(bars * (index + 0.5) / len(entry.frames))
        rendered = statemod.frame(ctx, look)
        for fixture in rig.fixtures:
            original = values_by_fixture.get(fixture.fid)
            if not original:
                continue
            offsets = fixture.profile.offsets(fixture.mode)
            for role in COLOR_ROLES:
                off = offsets.get(role)
                if off is None or off not in original:
                    continue
                got = rendered[fixture.universe][fixture.address - 1 + off]
                if abs(got - original[off]) > 1:
                    step_bad.append(f"{entry.name}[{index}]/{fixture.name}/{role}: "
                                    f"{got} vs {original[off]}")
                step_compared += 1
ctx.set_phase(0.0)

check("each step of a color chase renders its own step's bytes", not step_bad,
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
    """Just enough context to sample a movement offset function.

    Kept as a stand-in rather than a real EvalContext because this is checking
    continuity of the interpolation, not evaluation. It has to carry the shape
    macros at their identity values, though: `move_spread` is read on every
    sample, and defaulting it inside the offset function to accommodate a test
    double would put the default in the wrong place.
    """
    motion_bar = 0.0
    geometry = rig.geometry
    move_spread = 0.0
    move_size = 1.0
    move_center = (0.0, 0.0)


ctx = FakeCtx()
values = []
for k in range(400):
    ctx.motion_bar = (sample.bars or 8.0) * k / 400   # movement only: FakeCtx
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


# -- 3b. the dark moves ------------------------------------------------------
#
# A whole family of routines -- the ones that go dark to travel and snap on when
# they arrive -- ported as ordinary lit sweeps, because the porter read position
# and nothing else out of a chaser and then spread it evenly over the cycle.
# Every check here fails against that version of the port.
print("\n3b. dark moves keep their darkness and their timing")

DARK_MOVES = ["Teleport", "Apparition", "Freeze Frame", "Stutter", "Glitch",
              "Ascension", "Blink"]
cued = [e for e in entries if e.is_cued]
check("the whole Dark Moves family came through as cued chases",
      set(DARK_MOVES) <= {e.name for e in cued},
      f"{len(cued)} cued: {sorted(e.name for e in cued)}")

# The source's own timing, from the workspace rather than from the port, so this
# compares the port against the thing it was ported FROM.
chasers = {f.get("Name"): f for f in engine_el.findall(NS + "Function")
           if f.get("Type") == "Chaser"}
timing_bad = []
for entry in cued:
    func = chasers[entry.name]
    ordered = sorted(func.findall(NS + "Step"),
                     key=lambda s: int(s.get("Number", 0)))
    want = Porter(EVENT).step_spans_for(func, ordered)
    # Steps whose scene wrote no position are dropped, so compare what survived.
    if len(want) == len(entry.step_spans) and want != entry.step_spans:
        timing_bad.append(f"{entry.name}: {entry.step_spans} vs {want}")
check("each cued step keeps the source's own fade and hold", not timing_bad,
      timing_bad[0] if timing_bad else
      f"{len(cued)} chases, e.g. Teleport {by_name['Teleport'].step_spans[:2]} ms")

# The cycle is the sum of the steps, not the chaser's Duration times the step
# count. Teleport is 4 x (800 travel + 3200 hold) = 16 s; read as 8 x 800 it
# came out at 6.4 s and ran two and a half times too fast.
teleport = by_name["Teleport"]
check("a per-step chase's cycle is its real length",
      teleport.bars == snap_bars(16000 / (4 * 60_000 / 124)),
      f"{teleport.bars} bars for {sum(sum(s) for s in teleport.step_spans):.0f} ms")


def sample(entry, fraction):
    """Every fixture's intensity and every head's aim at a point in the cycle."""
    show = libmod.compose(entry)
    ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
    ctx.set_phase((entry.bars or 8.0) * fraction)
    states = {f.fid: statemod.FixtureState() for f in rig.fixtures}
    for layer in show.stack():                    # no safety: this is the look
        layer(ctx, states)
    return states


def at_step(entry, index, into=0.5):
    """The fraction of the cycle `into` the way through one step's fade (or its
    hold, when `into` is above 1)."""
    total = sum(f + h for f, h in entry.step_spans)
    before = sum(f + h for f, h in entry.step_spans[:index])
    fade, hold = entry.step_spans[index]
    offset = fade * into if into <= 1 else fade + hold * (into - 1)
    return (before + offset) / total


movers = [f for f in rig.fixtures if f.head is not None]
travelling = sample(teleport, at_step(teleport, 0, 0.5))
arrived = sample(teleport, at_step(teleport, 1, 1.5))
check("Teleport is dark while it travels",
      all(travelling[f.fid].intensity == 0.0 for f in movers),
      f"{[round(travelling[f.fid].intensity, 3) for f in movers]}")
check("and lit once it has arrived",
      all(arrived[f.fid].intensity == 1.0 for f in movers),
      f"{[round(arrived[f.fid].intensity, 3) for f in movers]}")

# The other half of the effect: it must be STILL while lit. A chase that eases
# through its poses is never still and never absent, which is precisely the
# ordinary lit sweep every one of these routines was written to be the opposite
# of -- and is what the port produced.
held = [sample(teleport, at_step(teleport, 1, 1.0 + k / 10.0))[movers[0].fid].aim
        for k in range(11)]
check("and it holds still while it is lit",
      max(abs(a.bearing_delta - held[0].bearing_delta)
          + abs(a.elev_deg - held[0].elev_deg) for a in held) < 1e-9,
      f"{len(held)} samples across the hold")
moved = travelling[movers[0].fid].aim
check("and it really does move during the dark stretch",
      abs(moved.elev_deg - held[0].elev_deg) > 10.0,
      f"{moved.elev_deg:.1f} deg halfway, {held[0].elev_deg:.1f} deg arrived")

# Teleport and Apparition are the same eight poses and the same dark travel;
# the ONLY difference in the source is that Apparition's arrival step fades its
# dimmer up over three seconds. If the port cannot tell them apart, it has not
# ported the dimmer at all -- which was the state of things.
apparition = by_name["Apparition"]
rising = [sample(apparition, at_step(apparition, 1, k / 4.0))[movers[0].fid].intensity
          for k in range(5)]
check("Apparition materialises where Teleport snaps",
      rising == sorted(rising) and rising[0] < 0.3 and rising[-1] > 0.9,
      f"{[round(v, 2) for v in rising]} across its arrival fade")

# Freeze Frame's whole claim is that no beam is ever caught mid-sweep: the pair
# that is moving is the dark one, every step.
freeze = by_name["Freeze Frame"]
caught = []
for k in range(1, 40):
    fraction = k / 40.0
    now = sample(freeze, fraction)
    then = sample(freeze, fraction + 0.004)
    for f in movers:
        drift = (abs(now[f.fid].aim.bearing_delta - then[f.fid].aim.bearing_delta)
                 + abs(now[f.fid].aim.elev_deg - then[f.fid].aim.elev_deg))
        if drift > 0.5 and now[f.fid].intensity > 0.01:
            caught.append(f"{f.name} at {fraction:.2f}")
check("Freeze Frame never lights a beam that is moving", not caught,
      f"{len(caught)} caught; first {caught[0]}" if caught else
      "39 samples across the cycle")

# Whether a head can cross that much room in the time the chase allows is a
# fact about the yoke, not about the port -- so this reports rather than
# insists, and only a travel that is not remotely long enough fails.
print("     dark travel vs what the heads need, at 124 bpm:")
short = []
for entry in cued:
    cycle = (entry.bars or 8.0) * (4 * 60.0 / 124)
    margins = servomod.cue_margins(rig.geometry, entry.steps, entry.step_spans,
                                   cycle)
    worst = max(((need - allow), allow, need, i)
                for i, (allow, need) in enumerate(margins))
    gap, allow, need, index = worst
    print(f"       {entry.name:<14} worst step {index + 1}: "
          f"{allow * 1000:>5.0f} ms allowed, {need * 1000:>5.0f} ms needed"
          + ("   <-- the dimmer returns mid-swing" if gap > 0 else ""))
    if allow > 0 and need > allow * 2:
        short.append(entry.name)
check("no dark move allows less than half the travel it needs", not short,
      f"{short}" if short else "against the assumed yoke speeds in engine.servo")


# -- 4. per-fixture color survived -------------------------------------------
print("\n4. color")
splits = [e for e in entries if e.colors]
check("split/duo/quad colors ported as per-fixture", len(splits) >= 5,
      f"{len(splits)}: {[e.name for e in splits[:4]]}")
check("they really do differ between heads",
      all(len({tuple(c) for c in e.colors.values()}) > 1 for e in splits))

uniform = [e for e in entries if e.color]
check("uniform colors collapsed to one value", len(uniform) > 20,
      f"{len(uniform)}")

color_paths = [e for e in entries if e.kind == "color_path"]
check("color chases ported as stepped frames", len(color_paths) >= 3,
      f"{[e.name for e in color_paths]}")


# -- 5. the library builds runnable looks -------------------------------------
print("\n5. every entry builds and evaluates")
setlist, _ = libmod.load_setlist(EVENT / "looks.json")
check("set list built", len(setlist.looks) == len(entries))

ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
ctx.set_phase(3.7)
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


# -- 7. each slot's layers read THEIR OWN phase -------------------------------
#
# The check above deliberately puts every slot at the same phase, because "what
# does this look emit at phase p" is a one-number question. That makes it blind
# to a color layer reading the movement phase -- so this is the test that
# actually pins the split down, by making the two phases disagree and asserting
# which one each layer followed.
print("\n7. per-slot phase")


def color_at(entry, color_bar, motion_bar):
    show = libmod.compose(None, [entry], [])
    ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
    ctx.motion_bar, ctx.color_bar, ctx.level_bar = motion_bar, color_bar, 0.0
    states = {f.fid: statemod.FixtureState() for f in rig.fixtures}
    for layer in show.stack():
        layer(ctx, states)
    return {f.name: states[f.fid].color for f in rig.fixtures}


chase = next(e for e in entries if e.kind == "color_path" and e.frames
             and len(e.frames) > 1)
bars = chase.bars or 8.0
# Two phases that land in different frames of the same chase.
first = bars * 0.5 / len(chase.frames)
second = bars * 1.5 / len(chase.frames)
check(f"{chase.name!r} has distinguishable frames",
      color_at(chase, first, first) != color_at(chase, second, second))
check("a color chase follows the COLOR phase, not the movement one",
      color_at(chase, second, first) == color_at(chase, second, second),
      "moving motion_bar under a fixed color_bar changed the color")
check("and moving the movement phase alone leaves the color where it was",
      color_at(chase, first, second) == color_at(chase, first, first))


def level_at(entry, level_bar, motion_bar):
    show = libmod.compose(None, [], [entry])
    ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
    ctx.motion_bar, ctx.color_bar, ctx.level_bar = motion_bar, 0.0, level_bar
    states = {f.fid: statemod.FixtureState() for f in rig.fixtures}
    for layer in show.stack():
        layer(ctx, states)
    return {f.name: round(states[f.fid].intensity, 6) for f in rig.fixtures}


dim = next(e for e in entries if e.kind == "level_path" and e.levels
           and len(e.levels) > 1)
lbars = dim.bars or 8.0
lfirst = lbars * 0.5 / len(dim.levels)
lsecond = lbars * 1.5 / len(dim.levels)
check(f"{dim.name!r} has distinguishable steps",
      level_at(dim, lfirst, lfirst) != level_at(dim, lsecond, lsecond))
check("a level chase follows the LEVEL phase",
      level_at(dim, lsecond, lfirst) == level_at(dim, lsecond, lsecond))

# The exception, and it is deliberate: a cued chase carries its own dimmer as
# part of the MOVEMENT slot. Letting the level rate move it would light the head
# before it had finished travelling, which is the one thing those routines exist
# not to do.
cued = next((e for e in entries if e.is_cued and e.step_levels), None)
if cued is None:
    check("a cued chase exists to check", False, "none in the library")
else:
    def cued_level(level_bar, motion_bar):
        show = libmod.compose(cued, [], [])
        ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
        ctx.motion_bar, ctx.color_bar, ctx.level_bar = motion_bar, 0.0, level_bar
        states = {f.fid: statemod.FixtureState() for f in rig.fixtures}
        for layer in show.stack():
            layer(ctx, states)
        return {f.name: round(states[f.fid].intensity, 6) for f in rig.fixtures}

    cbars = cued.bars or 8.0
    check(f"{cued.name!r} keeps its own dimmer on the MOVEMENT phase",
          cued_level(cbars * 0.6, cbars * 0.1)
          == cued_level(0.0, cbars * 0.1)
          and cued_level(0.0, cbars * 0.1) != cued_level(0.0, cbars * 0.6),
          "a dark move must not be desynced from its own travel by the "
          "level rate")


print("\n13. hand-authored parametric looks merge in beside the port")
# The separation is the whole design: looks.json is generated and its parity
# proof depends on every entry staying in it, so a look authored in this engine
# lives in parametric_looks.json and the two are merged at load.
from engine import blocks as blocksmod                              # noqa: E402

parametric_path = EVENT / "parametric_looks.json"
check("the event has a parametric looks file", parametric_path.exists())
parametric, retired_map = libmod.load_parametric(parametric_path)
check("it carries looks", len(parametric) > 0, f"{len(parametric)}")
check("every one names a block that exists",
      all(r.block in blocksmod.BLOCKS for r in parametric))
# A held place files with the poses; everything else by its slot.
check("their kind is derived from the block, never declared",
      all(r.kind == libmod.KIND_FOR_BLOCK.get(
              r.block, libmod.KIND_FOR_SLOT[blocksmod.SLOT_OF[r.block]])
          for r in parametric))
# One building-block system: the console's looks and a show folder's routines
# must be made of the same parts, or the two drift into dialects.
check("they are built from the same blocks a show folder's routines use",
      {r.block for r in parametric} <= set(blocksmod.BLOCKS))

merged = libmod.merge(entries, parametric, retired_map)
# A superseding look REPLACES its ported original rather than adding to it.
added = [r for r in parametric if not r.supersedes]
check("the merged library is the port plus the looks it does not replace",
      len(merged) == len(entries) + len(added),
      f"{len(entries)} + {len(added)} = {len(merged)}")
check("and looks.json itself is untouched by the merge",
      len(libmod.load_entries(EVENT / "looks.json")) == len(entries))

print("\n14. a name collision is refused, naming both files")
# Everything downstream addresses a look BY NAME -- cues, presets, the picker,
# auto mode's set list. Two entries with one name means the cue list and the
# operator can disagree about what "Ball Wave" is and neither would find out.
clash = libmod.LibraryEntry(name=entries[0].name, kind="path", tags=(),
                            block="orbit", args={})
try:
    libmod.merge(entries, [clash])
    check("a parametric look that shadows a ported one is refused", False,
          "accepted")
except ValueError as exc:
    check("a parametric look that shadows a ported one is refused", True)
    check("and the message names the collision",
          entries[0].name in str(exc) and "parametric_looks.json" in str(exc))

print("\n14b. a bad parametric looks file says what is wrong, by name")
import json as _json                                                # noqa: E402
import tempfile                                                     # noqa: E402

_tmp = Path(tempfile.mkdtemp())


def _load(doc):
    path = _tmp / "parametric_looks.json"
    path.write_text(_json.dumps(doc), encoding="utf-8")
    return libmod.load_parametric(path)


for label, doc, expect in (
    ("an unknown block", {"looks": [{"name": "X", "block": "swirl"}]},
     "does not exist"),
    ("a rig-bound adapter, which would tie the file to one rig",
     {"looks": [{"name": "X", "block": "look", "args": {"look": "MH Red"}}]},
     "select that look directly"),
    ("a choice that is not one of the choices",
     {"looks": [{"name": "X", "block": "spiral",
                 "args": {"direction": "sideways"}}]}, "direction must be"),
    ("a number that is not a number",
     {"looks": [{"name": "X", "block": "orbit", "args": {"radius": "big"}}]},
     "must be a number"),
    # Load time has no rig, so a look name cannot resolve -- which is the
    # point: a parametric look's colors are portable or they are refused.
    ("a color that only one rig's library could resolve",
     {"looks": [{"name": "X", "block": "duo", "args": {"color_a": "MH Red"}}]},
     "color_a"),
):
    try:
        _load(doc)
        check(f"{label} is refused", False, "accepted")
    except Exception as exc:                                        # noqa: BLE001
        check(f"{label} is refused", expect in str(exc), str(exc)[:90])
        check(f"and the message names the look and the file",
              "'X'" in str(exc) and "parametric_looks.json" in str(exc))
# Lenient on keys it does not know: a file hand-edited against a slightly older
# engine should drop what it does not understand and still light the room.
loaded, _ = _load({"looks": [{"name": "X", "block": "orbit",
                              "args": {"radius": 9, "wobble": 3}}]})
check("an argument this engine does not know is dropped, not fatal",
      "wobble" not in libmod.resolve_args(loaded[0])
      and libmod.resolve_args(loaded[0])["radius"] == 9)

print("\n15. every parametric look evaluates to a real frame")
# Same claim section 5 makes about ported entries. A block that renders NaN at
# one phase in sixty-four is a look that works in rehearsal and drops a head in
# the room.
generated = [e for e in merged if e.is_parametric]
check("there are parametric looks to check", len(generated) > 0)
broke: list[str] = []
static: list[str] = []
for entry in generated:
    show = libmod.build_look(entry).make((1.0, 1.0, 1.0))
    ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
    seen: set = set()
    bars = 16.0
    for step in range(65):
        ctx.set_phase(step / 64.0 * bars)
        ctx.time = step * 0.025
        try:
            states = statemod.evaluate(ctx, show)
            statemod.render(ctx, states)
        except Exception as exc:                                # noqa: BLE001
            broke.append(f"{entry.name}: {exc!r}")
            break
        for fixture in rig.fixtures:
            state = states[fixture.fid]
            if entry.slot == "movement" and state.aim is not None:
                seen.add((round(state.aim.bearing_delta, 3),
                          round(state.aim.elev_deg, 3)))
            elif entry.slot == "level":
                seen.add(round(state.intensity, 5))
            elif entry.slot == "color":
                seen.add(tuple(round(c, 4) for c in state.color))
    if len(seen) <= 1:
        static.append(entry.name)
check("every parametric look renders at every phase", not broke, str(broke[:2]))
check("and every one of them actually changes something", not static,
      f"static: {static}")

print("\n16. a parametric look obeys the same live controls a ported look does")
# Spread has to mean ONE thing. The block's own `spread` argument ADDS to the
# operator's Spread macro (`blocks._spread`), so with the look's set to 0 the
# single slider does exactly what it does on a ported path.
orbit_entry = next(e for e in generated if e.name == "Ball Orbit")
show = libmod.compose(orbit_entry, [], [])


def offsets_at(spread: float, size: float = 1.0, phase: float = 1.0):
    """Each head's aim MINUS its own calibrated ball aim.

    The offset is the thing under test, not the aim: four heads in four corners
    have four different ball aims, so comparing raw aims would report "the heads
    differ" for a route that has them in perfect unison.
    """
    ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
    ctx.move_spread, ctx.move_size = spread, size
    ctx.set_phase(phase)
    states = statemod.evaluate_stack(ctx, show)
    out = []
    for fixture in rig.fixtures:
        if fixture.head is None or states[fixture.fid].aim is None:
            continue
        base = rig.geometry.aim_at_ball(fixture.head)
        aim = states[fixture.fid].aim
        out.append((round(aim.bearing_delta - base.bearing_delta, 4),
                    round(aim.elev_deg - base.elev_deg, 4)))
    return out


unison = offsets_at(0.0)
spread_out = offsets_at(1.0)
check("there are several heads to compare", len(unison) > 1, f"{len(unison)}")
check("spread 0 puts every head on the same offset",
      len(set(unison)) == 1, str(unison))
check("spread 1 lags them apart", len(set(spread_out)) > 1, str(spread_out))
check("size 0 collapses the route onto each head's own ball aim",
      set(offsets_at(0.0, 0.0)) == {(0.0, 0.0)}, str(offsets_at(0.0, 0.0)))
check("and the route really does move between phases",
      offsets_at(0.0, 1.0, phase=0.0) != offsets_at(0.0, 1.0, phase=2.0))

print("\n17. retiring hides an entry without removing it")
sample = entries[0].name
hidden = libmod.merge(entries, [], {sample: {"replaced_by": "Ball Orbit",
                                            "note": "covered by the orbit"}})
found = next(e for e in hidden if e.name == sample)
check("the entry is still in the library", found is not None)
check("it is flagged retired", found.retired)
check("and points at what replaced it", found.replaced_by == "Ball Orbit")
# Reachable by hand, never by a timer -- the same rule a blackout parked in the
# set list follows. Auto mode selecting a retired look would be the timer
# undoing the retirement.
check("auto mode will not select it",
      libmod.build_look(found).manual_only)

print("\n17b. a superseding look IS the ported look it replaces")
# `supersedes` takes over a ported look's NAME -- cues, presets and the picker
# all now get the block instead -- so it is only allowed for an exact
# replacement, and this is where "exact" is measured rather than asserted:
# every head, through the real layers on the real rig, under the shape macros
# an operator might have up, to a hundredth of a degree.
ported_by_name = {e.name: e for e in entries}
superseding = [r for r in parametric if r.supersedes]
check("there are superseding looks to measure", len(superseding) >= 6,
      f"{len(superseding)}")


def aims(entry, size, centre):
    ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
    ctx.move_size, ctx.move_center = size, centre
    out = []
    for phase in (0.0, 3.0):
        ctx.set_phase(phase)
        states = statemod.evaluate_stack(ctx, libmod.compose(entry))
        out.extend((states[f.fid].aim.bearing_delta, states[f.fid].aim.elev_deg)
                   for f in rig.fixtures if states[f.fid].aim is not None)
    return out


worst_super = 0.0
worst_name = ""
for look in superseding:
    original = ported_by_name.get(look.name)
    if original is None:
        check(f"{look.name!r} supersedes a ported look", False, "none by that name")
        continue
    for size, centre in ((1.0, (0.0, 0.0)), (2.0, (30.0, -20.0)), (0.5, (-40.0, 10.0))):
        for (ab, ae), (bb, be) in zip(aims(original, size, centre),
                                      aims(look, size, centre)):
            err = max(abs(ab - bb), abs(ae - be))
            if err > worst_super:
                worst_super, worst_name = err, look.name
check("every superseding look reproduces its original on every head",
      worst_super < 0.01,
      f"worst {worst_super:.4f} degrees ({worst_name or 'none'})")

merged_view = libmod.merge(entries, parametric, retired_map)
names = [e.name for e in merged_view]
check("a superseded name appears once, as the block",
      names.count("Heads - Floor") == 1
      and next(e for e in merged_view if e.name == "Heads - Floor").block == "offset")
check("and in the position the ported look held",
      names.index("Heads - Floor") == [e.name for e in entries].index("Heads - Floor"))
check("a held position files under Positions, not Moves",
      next(e for e in merged_view if e.name == "Heads - Floor").kind == "pose")
check("looks.json itself still has the original",
      ported_by_name["Heads - Floor"].offsets is not None)

orphan = libmod.LibraryEntry(name="Heads - Nowhere", kind="pose", tags=(),
                             block="offset", args={}, supersedes=True)
try:
    libmod.merge(entries, [orphan])
    check("superseding a look that does not exist is refused", False, "accepted")
except ValueError as exc:
    check("superseding a look that does not exist is refused",
          "Heads - Nowhere" in str(exc) and "does not have" in str(exc))

print("\n18. the retirement audit refuses to report a fit that means nothing")
# The tool exists so retirement is a measurement rather than a hunch, which
# only helps if the measurement cannot be fooled. Both guards here were real
# false positives in its first run.
from shared.tools import audit_library as audit                      # noqa: E402


def pose(name, offsets):
    return libmod.LibraryEntry(name=name, kind="pose", tags=(),
                               offsets=offsets)


ball = pose("Ball", [[0.0, 0.0]] * 4)
floor = pose("Floor", [[0.0, -25.0]] * 4)
cross = pose("Cross", [[45.0, 0.0], [-45.0, 0.0], [45.0, 0.0], [-45.0, 0.0]])
wide = pose("Wide", [[90.0, 0.0], [-90.0, 0.0], [90.0, 0.0], [-90.0, 0.0]])

check("a pose whose heads all agree has no shape", not audit.has_shape(ball))
check("and one whose heads differ does", audit.has_shape(cross))

# Scaling ANY reference by zero produces the ball, so without a floor on size
# every uniform pose "matches" everything at 0.00 degrees. That is what the
# first run reported, and it was all false positives.
check("a uniform pose is not reported as a scaled version of a shaped one",
      audit.fit_pose(ball, cross) is None)
check("nor a shaped pose against a uniform reference",
      audit.fit_pose(cross, ball) is None)
check("two uniform poses are not fitted against each other",
      audit.fit_pose(ball, floor) is None)

# The fit that IS meaningful: same shape, different scale.
fit = audit.fit_pose(wide, cross)
check("a genuine scale relationship is found", fit is not None)
if fit is not None:
    check("and it recovers the right size", "size 2.00" in fit.detail,
          fit.detail)
    check("with no residual", fit.rms_deg < 1e-6, f"{fit.rms_deg}")

# A fit needing a centre the operator could not dial in is not a fit.
far = pose("Far", [[45.0 + 300.0, 0.0], [-45.0 + 300.0, 0.0],
                   [45.0 + 300.0, 0.0], [-45.0 + 300.0, 0.0]])
check("a fit outside the centre macro's range is refused",
      audit.fit_pose(far, cross) is None)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("library: all checks pass")
