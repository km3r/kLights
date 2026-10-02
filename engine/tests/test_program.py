"""
Tests for the lights compiler: timelines, routines and blocks turned into what
the despacio rig's fixtures do.

Offline -- a context, a rig, a beat -- so every check names its instant and
reads the evaluated states directly. The rules decided with the user each get a
check by name: per-fixture fall-through, the rest state (rest point, dark), a
non-looping routine running its end. Then the invariants that make it safe: no
double movement offsets, safety and the strobe policy still last, the Show
object never swapped.

Run: python engine/tests/test_program.py
"""

import json
import math
import sys
import time
from dataclasses import replace
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import blocks as blocksmod  # noqa: E402
from engine import library as libmod  # noqa: E402
from engine import program as programmod  # noqa: E402
from engine import safety as safetymod  # noqa: E402
from engine import showfiles as sf  # noqa: E402
from engine import state as statemod  # noqa: E402
from engine import timeline as tl  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


RIGGING = programmod.load_rigging(REPO / "events" / "despacio")
RIG = RIGGING.rig
GEO = RIG.geometry
MOVERS = [f for f in RIG.fixtures if f.head is not None]
PINS = [f for f in RIG.fixtures if "pinspots" in f.tags]
FALLBACK_RGB = (0.1, 0.2, 0.3)
FALLBACK = libmod.compose(None, [], [], FALLBACK_RGB)


def ctx_for(venue=None):
    return statemod.EvalContext(rig=RIG, venue=venue or RIG.venue)


def routine(rid, rows, bars=8, loop=True, params=None, variations=None,
            roles=None, rig=None):
    doc = {"kind": "klights.routine", "version": 1, "id": rid, "bars": bars,
           "loop": loop,
           "roles": roles or {"movers": {"default": "movers"},
                              "pins": {"default": "pinspots", "optional": True}},
           "params": params or {}, "rows": rows}
    if variations:
        doc["variations"] = variations
    if rig:
        doc["rig"] = rig
    result = sf.validate("routine", doc)
    assert result.ok, (rid, result.errors)
    return doc


def clips(rid, target, items, gap="fill"):
    return {"id": rid, "type": "clips", "target": target, "gap": gap, "items": items}


def use(iid, name, at, length, fade=0, **extra):
    return {"id": iid, "kind": "routine", "routine": name, "at": at, "len": length,
            "fade": fade, **extra}


def block(iid, name, at, length, fade=0, **args):
    return {"id": iid, "at": at, "len": length, "fade": fade, "block": name,
            "args": args}


def build(rows, routines=(), meta=None):
    timeline = tl.Timeline.from_rows(rows, sf.timeline_channels, meta)
    return programmod.compile(timeline, {r["id"]: r for r in routines}, RIGGING)


def frame(prog, beat, ctx=None, safe=False, **kw):
    ctx = ctx or ctx_for()
    prog.begin(beat, fallback=FALLBACK, **kw)
    if safe:
        return statemod.evaluate(ctx, prog.show)
    return statemod.evaluate_stack(ctx, prog.show)


def close(a, b, tol=1e-6):
    return abs(a - b) <= tol


def rgb_close(a, b, tol=1e-3):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


def offset(state, f):
    ball = GEO.aim_at_ball(f.head)
    return (state.aim.bearing_delta - ball.bearing_delta,
            state.aim.elev_deg - ball.elev_deg)


SOLID = lambda color, role="movers": routine(  # noqa: E731
    f"solid-{role}", [clips("c", "color", [block("s", "solid", 0, 32, color=color)])
                      | {"role": role}])


# -- 0. the parts agree -------------------------------------------------------
print("\n0. the parts agree")
check("blocks.py builds exactly the blocks the format allows",
      blocksmod.BLOCKS == sf.BLOCK_NAMES)
folder = sf.load_folder(REPO / "shared" / "show-example")
example_tl = tl.Timeline.from_doc(folder.timelines["synth-128"],
                                  sf.timeline_channels)
example = programmod.compile(example_tl, folder.routines, RIGGING)
check("the example show compiles against despacio with no problems",
      example.problems == [], f"{example.problems}")


# -- 1. the example, at the first chorus --------------------------------------
print("\n1. the example at bar 41")
s = frame(example, 160)
hot = blocksmod.parse_hex("#ff2d6f")
check("the movers take the Hot palette's primary from the chorus routine",
      all(rgb_close(s[f.fid].color, hot) for f in MOVERS),
      f"{[s[f.fid].color for f in MOVERS]}")
check("and the flash on beat 160", all(close(s[f.fid].intensity, 1.0) for f in MOVERS))
check("the routine colours only the movers, so the pinspots' colour FALLS "
      "THROUGH to the fallback (decided with the user)",
      all(rgb_close(s[f.fid].color, FALLBACK_RGB) for f in PINS),
      f"{[s[f.fid].color for f in PINS]}")
s = frame(example, 32)
teal = tuple(RIGGING.entries["Pin Teal"].color)
check("bar 9: the intro snapshot's preset paints the pins Pin Teal",
      all(rgb_close(s[f.fid].color, teal) for f in PINS),
      f"{[s[f.fid].color for f in PINS]}")
s = frame(example, 160 + 28.5)
check("the chorus routine's own strobe hit fires from inside it",
      all(close(s[f.fid].strobe, 0.7) for f in MOVERS),
      f"{[s[f.fid].strobe for f in MOVERS]}")
s150 = frame(example, 150)
check("master automation scales everything (0.6 easing to 1.0 into the drop)",
      all(0.6 < s150[f.fid].intensity < 1.0 for f in MOVERS),
      f"{[round(s150[f.fid].intensity, 3) for f in MOVERS]}")
shows = {id(example.show)}
for b in (0, 64, 160, 300, 383):
    frame(example, b)
    shows.add(id(example.show))
check("the program's Show is one object for every frame -- nothing swaps it",
      len(shows) == 1)


# -- 2. blocks ----------------------------------------------------------------
print("\n2. blocks")
fan = routine("fan", [clips("m", "movement", [block(
    "f", "fan_sweep", 0, 32, width=60, sweep=0)]) | {"role": "movers"}])
s = frame(build([clips("scene", "scene", [use("a", "fan", 0, 32)])], [fan]), 4)
bearings = [offset(s[f.fid], f)[0] for f in MOVERS]
want = [(k / 3 - 0.5) * 60 for k in range(4)]
check("fan_sweep fans the movers evenly across its width, from each head's "
      "own ball aim", all(close(a, b, 1e-6) for a, b in zip(bearings, want)),
      f"{[round(b, 2) for b in bearings]}")

orbit = routine("orb", [clips("m", "movement", [block(
    "o", "orbit", 0, 32, radius=20, bars=1, spread=0)]) | {"role": "movers"}])
s = frame(build([clips("scene", "scene", [use("a", "orb", 0, 32)])], [orbit]), 1)
d = offset(s[MOVERS[0].fid], MOVERS[0])
check("an orbit a quarter of the way round is a radius out in bearing",
      close(d[0], 20.0, 1e-6) and close(d[1], 0.0, 1e-6), f"{d}")

chase = routine("ch", [clips("l", "level", [block(
    "c", "chase", 0, 32, order="x", bars=1, width=0.25)]) | {"role": "movers"}])
s = frame(build([clips("scene", "scene", [use("a", "ch", 0, 32)])], [chase]), 0)
lit = sorted(f.position[0] for f in MOVERS if s[f.fid].intensity > 0.99)
check("a chase ordered by x starts at the lowest x", lit and
      lit[0] == min(f.position[0] for f in MOVERS), f"{lit}")

bad = routine("bad", [clips("m", "color", [block("o", "orbit", 0, 32)])
                      | {"role": "movers"}])
p = build([clips("scene", "scene", [use("a", "bad", 0, 32)])], [bad])
check("a movement block on a colour row is a problem, and claims nothing",
      any("movement block, on a color row" in x for x in p.problems)
      and all(rgb_close(frame(p, 4)[f.fid].color, FALLBACK_RGB) for f in MOVERS),
      f"{p.problems}")


# -- 3. lanes, per fixture ----------------------------------------------------
print("\n3. lanes, fixture by fixture")
movers_red = SOLID("#ff0000", "movers")
pins_green = SOLID("#00ff00", "pins")
p = build([clips("scene", "scene", [use("a", "solid-movers", 0, 32)]),
           clips("col", "color", [use("b", "solid-pins", 0, 32)])],
          [movers_red, pins_green])
s = frame(p, 4)
check("a lower colour lane shows on the fixtures the scene routine leaves alone",
      all(rgb_close(s[f.fid].color, (0, 1, 0)) for f in PINS)
      and all(rgb_close(s[f.fid].color, (1, 0, 0)) for f in MOVERS))

p = build([clips("scene", "scene", [use("a", "solid-movers", 0, 32)],
                 gap="exclusive")], [movers_red])
s = frame(p, 4)
check("on a lane that OWNS the track, fixtures its clip leaves alone rest "
      "(white) rather than showing the fallback",
      all(rgb_close(s[f.fid].color, (1, 1, 1)) for f in PINS))
s = frame(p, 40)
check("and in its gap: rest -- movers on the rest point (the ball by default), "
      "dark", all(close(s[f.fid].intensity, 0.0) for f in RIG.fixtures)
      and all(close(s[f.fid].aim.elev_deg, GEO.aim_at_point(
          f.head, *RIG.venue.ball).elev_deg, 1e-9) for f in MOVERS))
moved = replace(RIG.venue, rest_point=(2000.0, 0.0, 2000.0))
p2 = build([clips("scene", "scene", [use("a", "solid-movers", 0, 32)],
                  gap="exclusive")], [movers_red])
s = frame(p2, 40, ctx=ctx_for(moved))
want = GEO.aim_at_point(MOVERS[0].head, 2000.0, 0.0, 2000.0)
check("a venue with its own rest_point rests the movers there instead",
      close(s[MOVERS[0].fid].aim.bearing_delta, want.bearing_delta, 1e-9)
      and close(s[MOVERS[0].fid].aim.elev_deg, want.elev_deg, 1e-9))


# -- 4. fades and crossfades --------------------------------------------------
print("\n4. fades")
p = build([clips("col", "color", [use("a", "solid-movers", 0, 16, fade=4)])],
          [movers_red])
s = frame(p, 2)
mid = tuple((a + b) / 2 for a, b in zip(FALLBACK_RGB, (1, 0, 0)))
check("half way through a fade-in, half way from the fallback to the clip",
      rgb_close(s[MOVERS[0].fid].color, mid), f"{s[MOVERS[0].fid].color}")
s = frame(p, 15)
q = tuple(a + (b - a) * 0.25 for a, b in zip(FALLBACK_RGB, (1, 0, 0)))
check("and ending into a gap it fades back out over the same fade",
      rgb_close(s[MOVERS[0].fid].color, q), f"{s[MOVERS[0].fid].color}")

orb_a = routine("oa", [clips("m", "movement", [block(
    "o", "orbit", 0, 32, radius=20, bars=2, spread=0)]) | {"role": "movers"}])
orb_b = routine("ob", [clips("m", "movement", [block(
    "o", "pendulum", 0, 32, width=30, bars=4)]) | {"role": "movers"}])
both = build([clips("scene", "scene", [use("a", "oa", 0, 16),
                                       use("b", "ob", 16, 16, fade=8)])],
             [orb_a, orb_b])
only_a = build([clips("scene", "scene", [use("a", "oa", 0, 64)])], [orb_a])
only_b = build([clips("scene", "scene", [use("b", "ob", 16, 16)])], [orb_b])
beat = 19.0
w = 3.0 / 8.0
sa, sb, sx = frame(only_a, beat), frame(only_b, beat), frame(both, beat)
f0 = MOVERS[1]
want = (sa[f0.fid].aim.bearing_delta
        + (sb[f0.fid].aim.bearing_delta - sa[f0.fid].aim.bearing_delta) * w)
check("a movement crossfade blends the two whole aims -- never adds two "
      "offsets onto one head", close(sx[f0.fid].aim.bearing_delta, want, 1e-9),
      f"{sx[f0.fid].aim.bearing_delta} vs {want}")


# -- 5. routines: params, variations, the end ---------------------------------
print("\n5. routines")
fan_doc = folder.routines["fan-drop"]
spans = {}
for variation in ("wide", "tight"):
    p = build([clips("scene", "scene", [use("a", "fan-drop", 0, 32,
                                            variation=variation)])], [fan_doc])
    s = frame(p, 0)
    xs = [offset(s[f.fid], f)[0] for f in MOVERS]
    spans[variation] = max(xs) - min(xs)
check("a variation changes the routine: the wide fan is wider than the tight",
      spans["wide"] > spans["tight"] * 3, f"{spans}")

tinted = routine("tint", [clips("c", "color", [block("s", "solid", 0, 32,
                                                     color="$color")])
                          | {"role": "movers"}],
                 params={"color": {"type": "color", "default": "@primary"}})
pals = {"P": {"primary": "#ff0000", "secondary": "#00ff00", "accent": "#0000ff"}}
for value, want, label in (("@secondary", (0, 1, 0), "a palette role"),
                           ("#336699", blocksmod.parse_hex("#336699"), "a hex colour"),
                           ("Pin Teal", teal, "a colour look's name"),
                           (None, (1, 0, 0), "nothing (the default, @primary)")):
    extra = {"params": {"color": value}} if value else {}
    p = build([clips("scene", "scene", [use("a", "tint", 0, 32, **extra)])],
              [tinted], meta={"palettes": pals, "palette": "P"})
    s = frame(p, 4)
    check(f"a colour param can be {label}",
          rgb_close(s[MOVERS[0].fid].color, want) and not p.problems,
          f"{s[MOVERS[0].fid].color} {p.problems}")
p = build([clips("scene", "scene", [use("a", "tint", 0, 32,
                                        params={"color": "Nope"})])], [tinted])
check("a colour that is not on this rig is a problem, and the default plays",
      any("not a palette role" in x for x in p.problems), f"{p.problems}")

build_up = routine("build", [
    clips("m", "movement", [block("o", "orbit", 0, 8, radius=20, bars=1,
                                  spread=0)]) | {"role": "movers"},
    {"id": "size", "type": "automation", "target": "size",
     "points": [[0, 1.0], [8, 0.5, "ease"]]}], bars=2, loop=False)
p = build([clips("scene", "scene", [use("a", "build", 0, 32)])], [build_up])
s1, s2 = frame(p, 12.0), frame(p, 12.25)
r1 = math.hypot(*offset(s1[MOVERS[0].fid], MOVERS[0]))
moved_on = abs(offset(s1[MOVERS[0].fid], MOVERS[0])[0]
               - offset(s2[MOVERS[0].fid], MOVERS[0])[0]) > 1.0
check("a routine that does not loop keeps running its END past it (decided "
      "with the user): still orbiting, at its final size",
      moved_on and close(r1, 10.0, 1e-6), f"radius {r1:.3f}, moving {moved_on}")

owned = routine("owned", [clips("m", "movement", [block("o", "orbit", 0, 32)])
                          | {"role": "lasers"}],
                roles={"lasers": {"default": "lasers"}}, rig="elsewhere")
p = build([clips("scene", "scene", [use("a", "owned", 0, 32)])], [owned])
check("a routine built for another rig says so", any(
    "built for rig 'elsewhere'" in x for x in p.problems), f"{p.problems}")
check("and a required role with no fixtures here says so", any(
    "role 'lasers'" in x for x in p.problems))
check("an optional role absent on this rig is no problem at all", not any(
    "'pins'" in x for x in build([clips("scene", "scene", [
        use("a", "fan-drop", 0, 32)])], [fan_doc]).problems))
p = build([clips("scene", "scene", [use("a", "missing", 0, 32)])])
check("a routine that is not there is a problem, and the lane falls through",
      any("'missing' is not in routines/" in x for x in p.problems)
      and rgb_close(frame(p, 4)[MOVERS[0].fid].color, FALLBACK_RGB))
p = build([clips("col", "color", [{"id": "l", "kind": "look", "look": "Nope",
                                   "at": 0, "len": 16}])])
check("an unknown look is a problem, and the lane falls through",
      any("'Nope'" in x for x in p.problems)
      and rgb_close(frame(p, 4)[MOVERS[0].fid].color, FALLBACK_RGB))


# -- 6. palette and param automation ------------------------------------------
print("\n6. palettes and automation")
pals = {"A": {"primary": "#ff0000", "secondary": "#000000", "accent": "#000000"},
        "B": {"primary": "#0000ff", "secondary": "#000000", "accent": "#000000"}}
p = build([clips("scene", "scene", [use("a", "tint", 0, 64)]),
           clips("pal", "palette", [
               {"id": "a", "kind": "palette", "palette": "A", "at": 0, "len": 16},
               {"id": "b", "kind": "palette", "palette": "B", "at": 16, "len": 16,
                "fade": 4}], gap="exclusive")],
          [tinted], meta={"palettes": pals, "palette": "A"})
s = frame(p, 18)
check("a palette clip crossfades every role-coloured fixture",
      rgb_close(s[MOVERS[0].fid].color, (0.5, 0, 0.5)), f"{s[MOVERS[0].fid].color}")
p = build([clips("scene", "scene", [use("a", "tint", 0, 64)]),
           {"id": "pc", "type": "automation", "target": "param.color",
            "points": [[0, "#ff0000"], [8, "#0000ff"]]}], [tinted])
s = frame(p, 4)
check("param automation overrides a routine's colour, blended between points",
      rgb_close(s[MOVERS[0].fid].color, (0.5, 0, 0.5)), f"{s[MOVERS[0].fid].color}")

slow = build([clips("scene", "scene", [use("a", "orb", 0, 64)])], [orbit])
fast = build([clips("scene", "scene", [use("a", "orb", 0, 64)]),
              {"id": "r", "type": "automation", "target": "rate.movement",
               "points": [[0, 2.0]]}], [orbit])
check("a rate lane at 2 is where the plain clip would be at twice the beat",
      close(offset(frame(fast, 1.3)[MOVERS[0].fid], MOVERS[0])[0],
            offset(frame(slow, 2.6)[MOVERS[0].fid], MOVERS[0])[0], 1e-6))
ramp = build([clips("scene", "scene", [use("a", "build", 0, 64)]),
              {"id": "r", "type": "automation", "target": "rate.movement",
               "points": [[0, 1.0], [16, 3.0, "ease"]]}], [build_up])
direct = offset(frame(ramp, 21.7)[MOVERS[0].fid], MOVERS[0])
for b in [x * 0.025 for x in range(0, 868)]:
    ramp.begin(b, fallback=FALLBACK)
played = offset(frame(ramp, 21.7)[MOVERS[0].fid], MOVERS[0])
check("with both a timeline and a routine rate curve, a jump to a beat lands "
      "where playing to it does", close(direct[0], played[0], 1e-9),
      f"{direct} vs {played}")


# -- 7. hits, safety, the strobe policy ---------------------------------------
print("\n7. hits, and what still runs after them")
hits_row = {"id": "h", "type": "hits", "items": [
    {"id": "fl", "hit": "flash", "at": 0, "len": 4, "role": "pinspots"},
    {"id": "bo", "hit": "blackout", "at": 8, "len": 4},
    {"id": "st", "hit": "strobe", "at": 16, "len": 4, "level": 0.6,
     "role": "movers"}]}
dim = routine("dim", [clips("l", "level", [block("d", "dim", 0, 32, level=0.2)])
                      | {"role": "pins"}])
p = build([clips("scene", "scene", [use("a", "dim", 0, 64)]), hits_row], [dim])
s = frame(p, 1)
check("a flash bumps its role to full over a dimmed lane",
      all(close(s[f.fid].intensity, 1.0) for f in PINS))
s = frame(p, 9)
check("a blackout takes everything dark",
      all(close(s[f.fid].intensity, 0.0) for f in RIG.fixtures))
s = frame(p, 17)
check("a strobe hit opens the movers' shutter",
      all(close(s[f.fid].strobe, 0.6) for f in MOVERS))
ctx = ctx_for()
ctx.strobe_policy = safetymod.StrobeConfig(enabled=False)
s = frame(p, 17, ctx=ctx, safe=True)
check("and the strobe policy, after everything, still blocks it",
      all(close(s[f.fid].strobe, 0.0) for f in MOVERS))

crowd = RIG.venue.crowd_zone.footprint
point = [(crowd.min_x + 0.3 * (crowd.max_x - crowd.min_x)) / RIG.venue.width,
         1700.0 / RIG.venue.height,
         (crowd.min_z + 0.3 * (crowd.max_z - crowd.min_z)) / RIG.venue.depth]
glare = routine("glare", [clips("m", "movement", [block(
    "a", "aim_points", 0, 32, points=[point])]) | {"role": "movers"}])
p = build([clips("scene", "scene", [use("a", "glare", 0, 32)])], [glare])
raw = frame(p, 4)
safe = frame(p, 4, ctx=ctx_for(), safe=True)
check("a block aimed into the crowd is tapered by safety, which still runs last",
      any(safe[f.fid].intensity < raw[f.fid].intensity - 0.2 for f in MOVERS),
      f"{[round(safe[f.fid].intensity, 2) for f in MOVERS]}")


# -- 8. cost ------------------------------------------------------------------
print("\n8. cost")
ctx = ctx_for()
started = time.perf_counter()
for i in range(200):
    example.begin(100 + i * 0.4, fallback=FALLBACK)
    statemod.evaluate_stack(ctx, example.show)
per = (time.perf_counter() - started) / 200 * 1000
check("a frame of the example show costs a few milliseconds at most "
      "(the frame is 25)", per < 5.0, f"{per:.2f} ms")


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("program: all checks pass")
