"""
Tests for the template runtime (F19 milestone 2): which pick a phrase gets, the
bar-count cycle, a set compiled for a rig, and a TemplateRunner playing picks --
carrying one on across phrases, crossfading between two, cutting on a jump,
yielding grabbed slots, and sitting under a timeline as its fallback.

Against the despacio rig and the example show folder's routines and club set.

Run: python engine/tests/test_templates.py
"""

import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import library as libmod  # noqa: E402
from engine import program as programmod  # noqa: E402
from engine import showfiles as sf  # noqa: E402
from engine import state as statemod  # noqa: E402
from engine import templates as tm  # noqa: E402
from engine import timeline as tl  # noqa: E402
from engine import tracktime  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


RIGGING = programmod.load_rigging(REPO / "events" / "despacio")
RIG = RIGGING.rig
MOVERS = [f for f in RIG.fixtures if f.head is not None]
FALLBACK_RGB = (0.1, 0.2, 0.3)
FALLBACK = libmod.compose(None, [], [], FALLBACK_RGB)
FOLDER = sf.load_folder(REPO / "shared" / "show-example")
CLUB = FOLDER.templates["club"]


def ctx():
    return statemod.EvalContext(rig=RIG, venue=RIG.venue)


def states(show):
    return statemod.evaluate_stack(ctx(), show)


def same(a, b, tol=1e-6):
    for fid in a:
        x, y = a[fid], b[fid]
        if abs(x.intensity - y.intensity) > tol or any(
                abs(p - q) > tol for p, q in zip(x.color, y.color)):
            return False
        if (x.aim is None) != (y.aim is None):
            return False
        if x.aim is not None and (abs(x.aim.bearing_delta - y.aim.bearing_delta) > tol
                                  or abs(x.aim.elev_deg - y.aim.elev_deg) > tol):
            return False
    return True


# -- 1. choosing a pick ---------------------------------------------------------
print("\n1. which pick a phrase gets")
check("an exact label", tm.pick(CLUB, "Chorus")["routine"] == "fan-drop")
check("a numbered label falls back to its family: Verse 2 -> Verse",
      tm.pick(CLUB, "Verse 2")["routine"] == "verse-sweep")
check("and Up 3 -> Up", tm.pick(CLUB, "Up 3")["routine"] == "build-rise")
check("anything unlisted is '*'", tm.pick(CLUB, "Breakdown")["routine"] == "verse-sweep")
check("no label at all is '*' too", tm.pick(CLUB, None)["routine"] == "verse-sweep")
nostar = {**CLUB, "phrases": {"Chorus": {"routine": "fan-drop"}}}
check("and without '*' an unlisted label gets nothing",
      tm.pick(nostar, "Verse 1") is None)
check("the exact label wins over its family",
      tm.pick({"phrases": {"Verse": {"routine": "a"}, "Verse 2": {"routine": "b"}}},
              "Verse 2")["routine"] == "b")
check("a family lookup is not fooled by a label ending in a word",
      tracktime.family("Up") == "Up" and tracktime.family("Verse 10") == "Verse")
check("the next downbeat is the next bar line, or this one if on it",
      tm.next_downbeat(13.2) == 16 and tm.next_downbeat(16.0) == 16
      and tm.next_downbeat(-1.5) == 0)


# -- 2. compiling a set ----------------------------------------------------------
print("\n2. a set compiled for this rig")
started = time.perf_counter()
club = tm.compile_set("club", CLUB, FOLDER.routines, RIGGING)
took = time.perf_counter() - started
keys = {tm.pick_key(p) for _, p in tm.all_picks(CLUB)}
check("one program per DISTINCT pick (Intro and Outro share one)",
      set(club.programs) == keys and len(keys) < len(CLUB["phrases"]) + 2,
      f"{len(keys)} picks")
check("the club set compiles clean against despacio", club.problems == [],
      f"{club.problems}")
check("its transition fade is the set's", club.fade == 2.0)
check("compiling it is quick enough for the worker", took < 2.0, f"{took:.2f} s")
broken = {**CLUB, "phrases": {**CLUB["phrases"], "Chorus": {"routine": "no-such"}}}
bad = tm.compile_set("broken", broken, FOLDER.routines, RIGGING)
check("a pick naming a missing routine is listed and left out, not raised",
      any("no-such" in p for p in bad.problems)
      and tm.pick_key({"routine": "no-such"}) not in bad.programs)
check("and a phrase that would have played it gets no cue (the lanes below show)",
      tm.cue_for_phrase(bad, "Chorus", 160) is None)


# -- 3. cues ----------------------------------------------------------------------
print("\n3. cues from phrases and bars")
track = FOLDER.tracks["synth-128"]
phrases = tracktime.PhraseMap.from_items(track["phrases"]["items"])
chorus = next(p for p in phrases.phrases if p.label == "Chorus")
cue = tm.cue_in_track(club, phrases, chorus.start + 10)
check("in a prepped track's chorus: fan-drop, from the chorus's first beat",
      cue.routine == "fan-drop" and cue.start == chorus.start and cue.label == "Chorus",
      f"{cue}")
gappy = tracktime.PhraseMap.from_items([[16, 48, "Verse 1"], [64, 96, "Chorus"]])
in_gap = tm.cue_in_track(club, gappy, 52)
check("between phrases it is '*', from where the last one ended",
      in_gap.label == "*" and in_gap.start == 48, f"{in_gap}")
bars = tm.cue_for_bars(club, 70)
check("with no phrases, a 16-bar cycle: beat 70 is block 1 from beat 64",
      bars.start == 64 and bars.routine == "fan-drop" and bars.label == "bars",
      f"{bars}")
check("and the cycle wraps", tm.cue_for_bars(club, 130).routine == "verse-sweep")
check("no phrases and a set without bars: nothing",
      tm.cue_for_bars(tm.compile_set("nb", {**CLUB, "bars": None}, FOLDER.routines,
                                     RIGGING), 70) is None)


# -- 4. playing --------------------------------------------------------------------
print("\n4. a runner playing picks")
runner = tm.TemplateRunner()
verse = tm.cue_for_phrase(club, "Verse 1", 0)
runner.begin(club, verse, 4.0, fallback=FALLBACK)
alone = states(runner.show)
direct = club.programs[verse.key]
direct.begin(4.0, fallback=FALLBACK)
check("a pick plays its routine at its own beat (beat - start)",
      same(alone, states(direct.show)))
check("and is not the fallback", not same(alone, states(FALLBACK)))

again = tm.cue_for_phrase(club, "Verse 2", 32)
runner.begin(club, again, 36.0, fallback=FALLBACK)
check("Verse 1 into Verse 2 (the same pick) carries on rather than restarting",
      runner.cue.start == 0 and runner.outgoing is None, f"{runner.cue}")
check("under the new phrase's name", runner.cue.label == "Verse 2")
runner.begin(club, again, 40.0, jumped=True, fallback=FALLBACK)
check("but after a jump (or onto another track) it starts again from its phrase",
      runner.cue.start == 32 and runner.outgoing is None, f"{runner.cue}")

drop = tm.cue_for_phrase(club, "Chorus", 64)
runner.begin(club, drop, 64.0, prev=63.9, fallback=FALLBACK)
check("a new pick crossfades: the old one is still playing",
      runner.outgoing is not None and runner.cue.routine == "fan-drop")
runner.begin(club, drop, 65.0, prev=64.9, fallback=FALLBACK)
mid = states(runner.show)
old_p, new_p = club.programs[verse.key], club.programs[drop.key]
old_p.begin(65.0, fallback=FALLBACK)
a = states(old_p.show)
new_p.begin(1.0, fallback=FALLBACK)
b = states(new_p.show)
check("halfway through the set's 2-beat fade, it is the parameter-space midpoint",
      same(mid, statemod.blend(a, b, 0.5), tol=1e-5))
runner.begin(club, drop, 66.5, prev=66.4, fallback=FALLBACK)
check("and after the fade only the new pick plays", runner.outgoing is None)

runner.begin(club, verse, 200.0, jumped=True, fallback=FALLBACK)
check("a jump into another phrase cuts", runner.outgoing is None
      and runner.cue.routine == "verse-sweep")

runner.grabbed = frozenset({"color"})
runner.begin(club, drop, 300.0, jumped=True, fallback=FALLBACK)
grabbed = states(runner.show)
check("a grabbed slot shows the operator's selection instead",
      all(abs(grabbed[f.fid].color[i] - FALLBACK_RGB[i]) < 1e-6
          for f in MOVERS for i in range(3)),
      f"{grabbed[MOVERS[0].fid].color}")
runner.grabbed = frozenset()

runner.begin(club, None, 400.0, fallback=FALLBACK)
check("no cue: the fallback, untouched", same(states(runner.show), states(FALLBACK)))


# -- 5. under a timeline ------------------------------------------------------------
print("\n5. a template under a timeline's gaps")
SOLID_WHITE = {"kind": "klights.routine", "version": 1, "id": "white", "bars": 8,
               "roles": {"movers": {"default": "movers"}},
               "rows": [{"id": "c", "type": "clips", "target": "color",
                         "role": "movers",
                         "items": [{"id": "s", "at": 0, "len": 32, "block": "solid",
                                    "args": {"color": "#ffffff"}}]}]}
routines = {**FOLDER.routines, "white": SOLID_WHITE}
rows = [{"id": "col", "type": "clips", "target": "color", "gap": "fill",
         "items": [{"id": "w", "kind": "routine", "routine": "white", "at": 0,
                    "len": 16}]}]
timeline = programmod.compile(tl.Timeline.from_rows(rows, sf.timeline_channels),
                              routines, RIGGING)
fresh = tm.TemplateRunner()
cue = tm.cue_for_phrase(club, "Chorus", 0)
fresh.begin(club, cue, 8.0, fallback=FALLBACK)
timeline.begin(8.0, fallback=fresh.show)
layered = states(timeline.show)
check("where the timeline has a colour clip, its colour wins",
      all(abs(layered[f.fid].color[0] - 1.0) < 1e-6 for f in MOVERS))
fresh.begin(club, cue, 24.0, fallback=FALLBACK)
timeline.begin(24.0, fallback=fresh.show)
gap = states(timeline.show)
club.programs[cue.key].begin(24.0, fallback=FALLBACK)
template_only = states(club.programs[cue.key].show)
check("in its fill gap the template shows through, colour and all",
      same(gap, template_only, tol=1e-5))


# -- 6. cost --------------------------------------------------------------------------
print("\n6. cost")
c = ctx()
fading = tm.TemplateRunner()
fading.begin(club, verse, 0.0, fallback=FALLBACK)
fading.begin(club, drop, 64.0, fallback=FALLBACK)
started = time.perf_counter()
for i in range(200):
    fading.begin(club, drop, 64.0 + i * 0.008, fallback=FALLBACK)
    statemod.evaluate_stack(c, fading.show)
per = (time.perf_counter() - started) / 200 * 1000
check("a frame mid-crossfade (two picks) costs a few ms at most", per < 5.0,
      f"{per:.2f} ms")


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("templates: all checks pass")
