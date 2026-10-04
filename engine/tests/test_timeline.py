"""
Tests for the timeline core: what each lane, curve and hit says at a beat.

The rules being held here were decided with the user, so each is checked by
name: the higher lane wins (scene lanes included); a lane that owns the track
is BLANK in its gaps; a clip that ends into a gap fades out over its own fade.
Then the mechanics under them -- overlaps, crossfades, curves and their exact
integrals, hit windows and the hits too short for a frame.

Run: python engine/tests/test_timeline.py
"""

import ast
import json
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import showfiles  # noqa: E402
from engine import timeline as tl  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def clip(ident, at, length, fade=0, **extra):
    return {"id": ident, "at": at, "len": length, "fade": fade, **extra}


def clips(ident, target, items, gap="fill"):
    return {"id": ident, "type": "clips", "target": target, "gap": gap,
            "items": items}


def ids(channel):
    """A channel's stack as item ids, 'blank' and 'rest' -- what to compare."""
    return [l.item.id if isinstance(l, tl.Clip) else "blank"
            for l in channel.layers] + (["rest"] if channel.rest else [])


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


# -- 0. output-generic --------------------------------------------------------
print("\n0. it knows nothing about lights")
tree = ast.parse((REPO / "engine" / "timeline.py").read_text(encoding="utf-8"))
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(a.name.split(".")[0] for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        imported.add("." * node.level + (node.module or ""))
STDLIB = {"__future__", "bisect", "dataclasses", "types", "typing", "math"}
check("timeline.py imports only the standard library -- a VJ output must be "
      "able to use it without the lights coming along",
      imported <= STDLIB, f"{sorted(imported - STDLIB)}")


# -- 1. one row ---------------------------------------------------------------
print("\n1. one row: clips, overlaps, gaps")
t = tl.Timeline.from_rows([clips("r", "movement", [
    clip("a", 0, 16), clip("b", 16, 16), clip("c", 40, 8)])])
check("a clip shows inside its span, with its local beat",
      ids(t.channel("movement", 5)) == ["a"]
      and close(t.channel("movement", 5).top.local, 5.0))
check("its end beat belongs to the next clip, not to it",
      ids(t.channel("movement", 16)) == ["b"])
check("a fill gap has nothing to say: the template shows",
      ids(t.channel("movement", 35)) == ["rest"])
check("before the first clip and after the last, too",
      ids(t.channel("movement", -4)) == ["rest"]
      and ids(t.channel("movement", 60)) == ["rest"])
check("a channel no row drives is the template's",
      ids(t.channel("color", 5)) == ["rest"] and t.channels == ("movement",))

t = tl.Timeline.from_rows([clips("r", "movement", [
    clip("long", 0, 32), clip("late", 8, 32), clip("inside", 4, 8)])])
check("overlapping clips: the one that started earlier wins until it ends",
      ids(t.channel("movement", 10)) == ["long"])
check("then the next earliest still running takes over",
      ids(t.channel("movement", 33)) == ["late"]
      and close(t.channel("movement", 33).top.local, 25.0))
check("a clip covered for its whole length never shows",
      all("inside" not in ids(t.channel("movement", b)) for b in range(0, 41)))


# -- 2. owning the track ------------------------------------------------------
print("\n2. a lane that owns the track is blank in its gaps")
t = tl.Timeline.from_rows([clips("own", "movement", [
    clip("a", 16, 8), clip("b", 40, 8)], gap="exclusive")])
for beat, where in ((0, "before its first clip"), (30, "between clips"),
                    (60, "after its last")):
    c = t.channel("movement", beat)
    check(f"{where}: BLANK, and the template does NOT show",
          ids(c) == ["blank"] and c.blank and not c.rest, f"{ids(c)}")
check("and in a clip it is just the clip", ids(t.channel("movement", 18)) == ["a"])


# -- 3. the higher lane wins --------------------------------------------------
print("\n3. the higher lane wins, scene lanes included")
lights = showfiles.timeline_channels
move_above = tl.Timeline.from_rows([
    clips("move", "movement", [clip("look", 32, 16)]),
    clips("scene", "scene", [clip("routine", 0, 64)]),
], channels=lights)
check("a movement lane ABOVE the scene overrides the scene's movement",
      ids(move_above.channel("movement", 40)) == ["look"])
check("and only its movement: colour and level stay the scene's",
      ids(move_above.channel("color", 40)) == ["routine"]
      and ids(move_above.channel("level", 40)) == ["routine"])
check("outside its clip the scene shows through the movement lane's fill gap",
      ids(move_above.channel("movement", 10)) == ["routine"])

move_below = tl.Timeline.from_rows([
    clips("scene", "scene", [clip("routine", 0, 64)]),
    clips("move", "movement", [clip("look", 32, 16), clip("look2", 70, 8)]),
], channels=lights)
check("BELOW the scene, the movement clip loses while the scene has content",
      ids(move_below.channel("movement", 40)) == ["routine"])
check("and shows where the scene lane has nothing",
      ids(move_below.channel("movement", 72)) == ["look2"])

owner_above = tl.Timeline.from_rows([
    clips("own", "movement", [clip("x", 0, 8)], gap="exclusive"),
    clips("scene", "scene", [clip("routine", 0, 64)]),
], channels=lights)
check("an owning lane above blanks the lanes below it in its gaps",
      ids(owner_above.channel("movement", 20)) == ["blank"]
      and ids(owner_above.channel("color", 20)) == ["routine"])

two = tl.Timeline.from_rows([
    clips("top", "movement", [clip("t", 0, 8)]),
    clips("bottom", "movement", [clip("b", 0, 16)]),
])
check("two lanes of one kind: the higher one wins",
      ids(two.channel("movement", 4)) == ["t"]
      and ids(two.channel("movement", 12)) == ["b"])


# -- 4. fades -----------------------------------------------------------------
print("\n4. fades in, out, and across")
t = tl.Timeline.from_rows([clips("r", "color", [
    clip("a", 0, 16, fade=4), clip("b", 16, 16, fade=8), clip("c", 40, 8, fade=2)])])
c = t.channel("color", 1)
check("a first clip fades in from what is below the row",
      ids(c) == ["a", "rest"] and close(c.top.weight, 0.25)
      and c.top.fading == "in", f"{ids(c)} {c.top}")
c = t.channel("color", 14)
check("a clip followed directly by another does not fade out",
      ids(c) == ["a"] and c.top.weight == 1.0)
c = t.channel("color", 18)
check("the next one crossfades from it: the first is held under its fade-in",
      ids(c) == ["b", "a"] and close(c.top.weight, 0.25)
      and c.layers[1].held and close(c.layers[1].local, 18.0), f"{c}")
c = t.channel("color", 30)
check("a clip ending into a gap fades out over its own fade, in its last beats",
      ids(c) == ["b", "rest"] and close(c.top.weight, 0.25)
      and c.top.fading == "out", f"{c.top}")
check("and has gone by its end beat", ids(t.channel("color", 32)) == ["rest"])
c = t.channel("color", 47)
check("a fade-in and fade-out that meet take the lower of the two",
      close(c.top.weight, 0.5) and c.top.fading == "out", f"{c.top}")

t = tl.Timeline.from_rows([clips("r", "color", [clip("a", 0, 8), clip("b", 20, 8)])])
check("with no fade, a clip cuts in and out on its beats",
      ids(t.channel("color", 7.999)) == ["a"] and ids(t.channel("color", 8)) == ["rest"]
      and t.channel("color", 20).top.weight == 1.0)

t = tl.Timeline.from_rows([clips("own", "color", [clip("a", 8, 8, fade=4)],
                                 gap="exclusive")])
check("on an owning lane a clip fades in from blank, not from the template",
      ids(t.channel("color", 10)) == ["a", "blank"])
check("and fades out to blank", ids(t.channel("color", 15)) == ["a", "blank"])

t = tl.Timeline.from_rows([
    clips("top", "color", [clip("a", 8, 8, fade=4)]),
    clips("under", "color", [clip("u", 0, 32)]),
])
check("on a fill lane a clip fades in from the lane below it",
      ids(t.channel("color", 10)) == ["a", "u"])

t = tl.Timeline.from_rows([clips("r", "color", [
    clip("a", 0, 16), clip("b", 8, 16, fade=4)])])
c = t.channel("color", 17)
check("a clip shown late (an earlier one overlapped it) keeps its own fade "
      "clock -- already done here", ids(c) == ["b"] and c.top.weight == 1.0)


# -- 5. curves ----------------------------------------------------------------
print("\n5. automation curves")
curve = tl.Curve.from_points([[0, 0.0], [8, 1.0], [16, 0.5, "step"],
                              [24, 1.0, "ease"]])
check("linear between points", close(curve.value(4), 0.5))
check("a step holds the previous value until its point, then jumps",
      close(curve.value(15.99), 1.0) and close(curve.value(16), 0.5))
check("ease is smoothstep: slow at both ends, half way at the middle",
      close(curve.value(20), 0.75) and curve.value(17) - 0.5 < 0.5 * (1 / 8))
check("the value holds before the first point and after the last",
      close(curve.value(-10), 0.0) and close(curve.value(99), 1.0))
check("at a point, the point's value", close(curve.value(8), 1.0))
single = tl.Curve.from_points([[32, 0.7]])
check("one point is a constant", close(single.value(0), 0.7)
      and close(single.value(100), 0.7))


def numeric_integral(c, a, b, n=400):
    """Midpoint rule, split at every point so a step's jump is never inside
    an interval -- accurate to well under the tolerance below."""
    edges = [a] + [x for x in c.beats if a < x < b] + [b]
    total = 0.0
    for lo, hi in zip(edges, edges[1:]):
        h = (hi - lo) / n
        total += sum(c.value(lo + (i + 0.5) * h) for i in range(n)) * h
    return total


rnd = random.Random(19)
worst = 0.0
for _ in range(20):
    pts, beat = [], rnd.uniform(-8, 8)
    for _ in range(rnd.randint(1, 6)):
        pts.append([beat, rnd.uniform(0, 8), rnd.choice(tl.CURVES)])
        beat += rnd.uniform(0.5, 16)
    c = tl.Curve.from_points(pts)
    lo, hi = pts[0][0] - 5, pts[-1][0] + 5
    for b in (lo, (lo + hi) / 2, hi, pts[0][0] + 0.3):
        exact = c.integral(b)
        approx = numeric_integral(c, pts[0][0], b) if b >= pts[0][0] else \
            -numeric_integral(c, b, pts[0][0])
        worst = max(worst, abs(exact - approx))
check("the closed-form integral matches a numeric one on random curves",
      worst < 1e-4, f"worst error {worst:.2e}")
rate = tl.Curve.from_points([[0, 1.0], [16, 2.0]])
check("so a rate curve's integral is its phase: 1 then 2 per beat over 16 beats",
      close(rate.integral(16), 24.0) and close(rate.integral(20), 32.0)
      and close(rate.integral(-4), -4.0))

colour = tl.Curve.from_points([[0, "#ff0000"], [8, "@primary", "ease"]])
a, b, t_ = colour.segment(4)
check("a colour curve gives the two ends and the shaped blend, for the caller "
      "to mix", (a, b) == ("#ff0000", "@primary") and close(t_, 0.5))
try:
    colour.value(4)
    refused = False
except tl.TimelineError:
    refused = True
check("and refuses to be a number", refused)
for bad, why in (([], "no points"), ([[4, 1], [4, 2]], "not after"),
                 ([[0, 1, "bounce"]], "curve")):
    try:
        tl.Curve.from_points(bad)
        ok = False
    except tl.TimelineError as exc:
        ok = why in str(exc)
    check(f"a curve refuses {bad!r}", ok)

t = tl.Timeline.from_rows([
    {"id": "m1", "type": "automation", "target": "master", "points": [[0, 0.3]]},
    {"id": "m2", "type": "automation", "target": "master", "points": [[0, 0.9]]},
])
check("two curves for one target: the higher row wins",
      close(t.automation("master", 10), 0.3) and t.curves["master"][0] == "m1")
check("an unautomated target is None, not zero",
      t.automation("size", 10) is None)


# -- 6. hits ------------------------------------------------------------------
print("\n6. hits")
t = tl.Timeline.from_rows([{"id": "h", "type": "hits", "items": [
    {"id": "bo", "hit": "blackout", "at": 15, "len": 1},
    {"id": "fl", "hit": "flash", "at": 16, "len": 2, "envelope": "decay"},
    {"id": "st", "hit": "strobe", "at": 16, "len": 4, "level": 0.7},
    {"id": "tick", "hit": "flash", "at": 30.01, "len": 0.02},
]}])
names = lambda hs: sorted(h.item.id for h in hs)  # noqa: E731
check("a hit is on through its window", names(t.hits(15.5)) == ["bo"])
check("and off at its end beat", "bo" not in names(t.hits(16)))
hs = {h.item.id: h for h in t.hits(17)}
check("a decaying flash falls with its progress; a held one keeps its level",
      close(hs["fl"].level, 0.5) and close(hs["st"].level, 0.7)
      and close(hs["st"].progress, 0.25), f"{hs}")
check("jumping into the middle of a hit shows it from there",
      names(t.hits(18, prev=8, jumped=True)) == ["st"])
check("a hit shorter than a frame fires once when played through",
      [h.item.id for h in t.hits(30.05, prev=30.0) if h.crossed] == ["tick"])
check("but not when the deck jumped over it", t.hits(30.05, prev=30.0, jumped=True) == ())
check("nor when going backwards", t.hits(30.0, prev=30.05) == ())
check("and only once: the next frame does not see it again",
      t.hits(30.1, prev=30.05) == ())


# -- 7. the example show ------------------------------------------------------
print("\n7. the example show, explained")
doc = json.loads((REPO / "shared" / "show-example" / "timelines" /
                  "synth-128.json").read_text(encoding="utf-8"))
ex = tl.Timeline.from_doc(doc, showfiles.timeline_channels)
check("channels in lane order, palette included",
      ex.channels == ("movement", "color", "level", "palette"), f"{ex.channels}")
check("the OSC and visuals rows are carried, untouched", [r["output"] for r in ex.external]
      == ["osc", "osc", "visuals"] and ex.external[0]["items"][2]["off"]["args"] == [1])
check("and the document's own fields are kept",
      ex.meta["palette"] == "Cool" and ex.meta["track"] == "synth-128")
e = ex.explain(160)
check("bar 41: the chorus routine on every slot, Hot palette, the flash",
      e["channels"]["movement"] == [{"row": "scene", "item": "chorus1",
                                     "local": 0.0, "weight": 1.0}]
      and e["channels"]["palette"][0]["item"] == "hot1"
      and [h["item"] for h in e["hits"]] == ["fl1"]
      and e["automation"]["master"] == 1.0, json.dumps(e)[:300])
e = ex.explain(150)
check("bar 38: the build, palette lane blank (so the default palette), "
      "master easing up",
      e["channels"]["color"][0]["item"] == "up1"
      and e["channels"]["palette"] == ["blank"]
      and 0.6 < e["automation"]["master"] < 1.0, json.dumps(e)[:300])
e = ex.explain(370)
check("the outro: the movement lane's look wins movement, the routine keeps "
      "colour", e["channels"]["movement"][0]["item"] == "lazy"
      and e["channels"]["color"][0]["item"] == "outro", json.dumps(e)[:300])
check("span covers the whole show", ex.span == (0.0, 384.0), f"{ex.span}")
json.dumps(ex.explain(161.5))
check("explain is plain JSON", True)

for bad, why in (({"rows": "no"}, "list of rows"),
                 ({"rows": [{"id": "x", "type": "film"}]}, "unknown type"),
                 ({"rows": [clips("r", "movement", [clip("a", 0, 0)])]}, "length"),
                 ({"rows": [clips("r", "movement", [{"id": "a", "at": "0",
                                                      "len": 4}])]}, "number"),
                 ({"rows": [clips("r", "movement", [], gap="sometimes")]}, "gap")):
    try:
        tl.Timeline.from_doc(bad)
        ok = False
    except tl.TimelineError as exc:
        ok = why in str(exc)
    check(f"refuses {why}", ok)


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("timeline: all checks pass")
