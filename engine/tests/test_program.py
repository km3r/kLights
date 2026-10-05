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
from engine import waves  # noqa: E402

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


# A routine's own param lane: read in the ROUTINE's beats, wrapped with its
# loop, under the timeline's lane and over every fixed value.
grow = routine("grow", [
    clips("m", "movement", [block("o", "orbit", 0, 8, radius="$radius", bars=1,
                                  spread=0)]) | {"role": "movers"},
    {"id": "rad", "type": "automation", "target": "param.radius",
     "points": [[0, 10.0], [8, 30.0]]}], bars=2,
    params={"radius": {"type": "number", "default": 20, "min": 0, "max": 40}})


def bearing_at(prog, beat):
    return offset(frame(prog, beat)[MOVERS[0].fid], MOVERS[0])[0]


# A quarter of the way round, an orbit is exactly its radius out in bearing;
# one beat into the routine its lane is 10 + 20 * 1/8.
p = build([clips("scene", "scene", [use("a", "grow", 4, 32)])], [grow])
check("a routine's own param lane drives its param, in the routine's beats "
      "(placed at beat 4, read at routine beat 1)",
      close(bearing_at(p, 5), 12.5, 1e-6), f"{bearing_at(p, 5)}")
check("and lands on the same value every pass of the loop",
      close(bearing_at(p, 13), 12.5, 1e-6) and close(bearing_at(p, 21), 12.5, 1e-6),
      f"{bearing_at(p, 13)} {bearing_at(p, 21)}")
p = build([clips("scene", "scene", [use("a", "grow", 4, 32,
                                        params={"radius": 35})])], [grow])
check("the lane beats a use's fixed value (which the folder check warns of)",
      close(bearing_at(p, 5), 12.5, 1e-6), f"{bearing_at(p, 5)}")
p = build([clips("scene", "scene", [use("a", "grow", 4, 32)]),
           {"id": "tr", "type": "automation", "target": "param.radius",
            "points": [[0, 5.0]]}], [grow])
check("the timeline's lane for the same param beats the routine's own",
      close(bearing_at(p, 5), 5.0, 1e-6), f"{bearing_at(p, 5)}")
held = routine("held", grow["rows"], bars=2, loop=False, params=grow["params"])
p = build([clips("scene", "scene", [use("a", "held", 0, 32)])], [held])
check("a routine that does not loop holds its lane's last value past its end",
      close(bearing_at(p, 25), 30.0, 1e-3),
      f"{bearing_at(p, 25)}")
fade_tint = routine("ftint", tinted["rows"] + [
    {"id": "pc", "type": "automation", "target": "param.color",
     "points": [[0, "#ff0000"], [8, "#0000ff"]]}], params=tinted["params"])
s = frame(build([clips("scene", "scene", [use("a", "ftint", 16, 64)])],
                [fade_tint]), 20)
check("a colour param's own lane blends between its points",
      rgb_close(s[MOVERS[0].fid].color, (0.5, 0, 0.5)), f"{s[MOVERS[0].fid].color}")

# A routine on a LOWER lane still plays for the fixtures the top one leaves
# alone (per-fixture fall-through), so its own lanes must be read at its own
# beat there too -- not only when it is the lane on top.
pin_fade = routine("pinfade", [
    clips("c", "color", [block("s", "solid", 0, 32, color="$c")]) | {"role": "pins"},
    {"id": "pc", "type": "automation", "target": "param.c",
     "points": [[0, "#ff0000"], [8, "#0000ff"]]}],
    params={"c": {"type": "color", "default": "#ffffff"}})
p = build([clips("top", "color", [use("t", SOLID("#00ff00")["id"], 0, 64)]),
           clips("low", "color", [use("l", "pinfade", 0, 64)])],
          [SOLID("#00ff00"), pin_fade])
s = frame(p, 4)
check("a routine under a full-weight lane reads its own lanes at its own beat "
      "for the fixtures that fall through to it",
      all(rgb_close(s[f.fid].color, (0.5, 0, 0.5)) for f in PINS)
      and all(rgb_close(s[f.fid].color, (0, 1, 0)) for f in MOVERS),
      f"{[s[f.fid].color for f in PINS]}")

# An argument lane: one item's argument, moved without declaring a param.
argo = routine("argo", [
    clips("m", "movement", [block("o", "orbit", 0, 8, radius=20, bars=1,
                                  spread=0)]) | {"role": "movers"},
    {"id": "rad", "type": "automation", "target": "arg.o.radius",
     "points": [[0, 10.0], [8, 30.0]]}], bars=2)
p = build([clips("scene", "scene", [use("a", "argo", 4, 32)])], [argo])
check("an argument lane drives that item's argument, in the routine's beats",
      close(bearing_at(p, 5), 12.5, 1e-6), f"{bearing_at(p, 5)}")
p = build([clips("scene", "scene", [use("a", "argo", 4, 32)]),
           {"id": "tr", "type": "automation", "target": "param.arg.o.radius",
            "points": [[0, 5.0]]}], [argo])
check("and no timeline can reach it, even by naming its hidden parameter",
      close(bearing_at(p, 5), 12.5, 1e-6), f"{bearing_at(p, 5)}")
red_solid = routine("rs", [
    clips("c", "color", [block("s", "solid", 0, 32, color="#ff0000")]) | {"role": "movers"},
    {"id": "sc", "type": "automation", "target": "arg.s.color",
     "points": [[0, "#ff0000"], [8, "#0000ff"]]}])
s = frame(build([clips("scene", "scene", [use("a", "rs", 0, 64)])], [red_solid]), 4)
check("a colour argument's lane blends between its points",
      rgb_close(s[MOVERS[0].fid].color, (0.5, 0, 0.5)), f"{s[MOVERS[0].fid].color}")

# Every argument the format lets a lane drive must really be read per frame:
# one read once at build time would accept a lane and ignore it. So for each,
# the same item under a lane held at one end of its range and then the other
# must put some fixture somewhere different at some beat.
NEEDS = {"solid": {"color": "#ffffff"}, "color_chase": {"colors": ["#ff0000", "#00ff00"]},
         "aim_points": {"points": [[0.2, 0.2, 0.0], [0.8, 0.8, 0.0]]},
         # its default roles are both white with no palette, and at blend 0
         # its cycle trades nothing
         "duo": {"color_a": "#ff0000", "color_b": "#0000ff", "blend": 0.5}}
SLOT_ROW = {"movement": "movement", "color": "color", "level": "level"}


def picture(prog):
    out = []
    for b in (0.3, 1.1, 2.7, 5.9, 9.4):
        s = frame(prog, b)
        out.append(tuple((round(s[f.fid].aim.bearing_delta, 6), round(s[f.fid].aim.elev_deg, 6),
                          tuple(round(c, 6) for c in s[f.fid].color),
                          round(s[f.fid].intensity, 6), round(s[f.fid].strobe, 6))
                         for f in MOVERS))
    return out


deaf = []
for name, declared in blocksmod.PARAMS.items():
    slot = blocksmod.SLOT_OF.get(name)
    if slot is None:
        continue
    for spec in declared:
        if spec.kind not in sf.LANE_ARG_KINDS:
            continue
        if spec.kind == "color":
            ends = ("#ff0000", "#0000ff")
        else:
            # The bottom and the MIDDLE of the range: the two ends of a hue
            # are the same colour.
            lo = spec.min if spec.min is not None else 0.0
            hi = spec.max if spec.max is not None else lo + 10.0
            ends = (lo, (lo + hi) / 2.0)
        pics = []
        for end in ends:
            doc = routine(f"deaf-{name}-{spec.name}", [
                clips("r", SLOT_ROW[slot], [block("i", name, 0, 32, **NEEDS.get(name, {}))])
                | {"role": "movers"},
                {"id": "a", "type": "automation", "target": f"arg.i.{spec.name}",
                 "points": [[0, end]]}])
            pics.append(picture(build([clips("scene", "scene", [use(
                "u", doc["id"], 0, 64)])], [doc])))
        if pics[0] == pics[1]:
            deaf.append(f"{name}.{spec.name}")
check("every block argument a lane may drive moves the lights when it does",
      deaf == [], f"{deaf}")

# Waves: on top of the points, any lane.
p = build([{"id": "m", "type": "automation", "target": "master",
            "points": [[0, 0.5]], "wave": {"shape": "square", "bars": 1, "depth": 0.5}}])
low, high = frame(p, 1)[MOVERS[0].fid].intensity, frame(p, 3)[MOVERS[0].fid].intensity
check("a square wave on master: the points' 0.5 in the first half bar, 1.0 in "
      "the second", high > 0 and close(low / high, 0.5, 1e-9), f"{low} {high}")
red = {"id": "pc", "type": "automation", "target": "param.color",
       "points": [[0, "#ff0000"]]}
s1 = frame(build([clips("scene", "scene", [use("a", "tint", 0, 64)]),
                  red | {"wave": {"shape": "square", "bars": 1, "toward": "#0000ff"}}],
                 [tinted]), 1)
s3 = frame(build([clips("scene", "scene", [use("a", "tint", 0, 64)]),
                  red | {"wave": {"shape": "square", "bars": 1, "toward": "#0000ff",
                                  "depth": 0.5}}], [tinted]), 3)
check("a colour lane's wave swings toward its colour, as far as its depth",
      rgb_close(s1[MOVERS[0].fid].color, (1, 0, 0))
      and rgb_close(s3[MOVERS[0].fid].color, (0.5, 0, 0.5)),
      f"{s1[MOVERS[0].fid].color} {s3[MOVERS[0].fid].color}")
wobble = {"shape": "sine", "bars": 1, "depth": 1.0}
wavy = build([clips("scene", "scene", [use("a", "orb", 0, 64)]),
              {"id": "r", "type": "automation", "target": "rate.movement",
               "points": [[0, 1.0]], "wave": wobble}], [orbit])
direct = bearing_at(wavy, 13.3)
motion_beats = 13.3 + waves.Wave("sine", 1, 1.0).integral(13.3)
want = 20.0 * math.sin(2 * math.pi * (motion_beats / 4.0))
check("a wave on a rate lane is integrated exactly: the orbit is where the "
      "closed-form phase puts it", close(direct, want, 1e-6), f"{direct} vs {want}")
for b in [x * 0.025 for x in range(0, 533)]:
    wavy.begin(b, fallback=FALLBACK)
check("and playing up to the beat lands where jumping to it does",
      close(bearing_at(wavy, 13.3), direct, 1e-12))

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


# -- 7b. a long clip under two rate curves -------------------------------------
print("\n7b. bounded work for a long clip")
T = tl.Curve.from_points([[0, 1.0], [64, 2.0]])
R = tl.Curve.from_points([[0, 1.0], [16, 0.5]])
started = time.perf_counter()
long_warp = programmod.Warp(0.0, T, R, None, horizon=float(sf.MAX_BEATS))
built_in = time.perf_counter() - started
check("the warp table stops at TABLE_MAX_BEATS however long the clip",
      len(long_warp._table) <= programmod.TABLE_MAX_BEATS / programmod.TABLE_STEP + 2
      and built_in < 2.0, f"{len(long_warp._table)} entries in {built_in:.2f} s")
edge = programmod.TABLE_MAX_BEATS
step = long_warp(edge + 10) - long_warp(edge)
check("and past it the warp runs on at the curves' settled rate (2 x 0.5)",
      abs(step - 10 * 1.0) < 1e-6, f"{step}")


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
