"""
Tests for a track's musical time: the beat grid and the phrase map.

Everything a timeline does rests on one conversion -- a position in the audio
to a beat on the track's grid -- so the properties checked here are the ones a
transport would trip over: continuity across a tempo change, the two directions
agreeing exactly, extrapolation outside the anchors, and a grid that cannot
exist being refused rather than producing a map that runs backwards.

Run: python engine/tests/test_tracktime.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import tracktime as tt  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def close(a, b, eps=1e-9):
    return abs(a - b) <= eps


print("\n1. a steady grid")
g = tt.Grid.steady(128.0, first_downbeat_s=0.25)
check("beat 0 is the first downbeat", close(g.beat_at(0.25), 0.0), f"{g.beat_at(0.25)}")
check("one beat later is beat 1", close(g.beat_at(0.25 + 60 / 128), 1.0))
check("a pickup before the downbeat is a negative beat, not clamped to 0",
      close(g.beat_at(0.0), -0.25 * 128 / 60), f"{g.beat_at(0.0)}")
check("bar 41 starts at beat 160", close(g.time_at(160), 0.25 + 160 * 60 / 128))
check("and bar_of counts from the downbeat",
      tt.bar_of(160) == 40 and tt.bar_of(-0.5) == -1 and tt.bar_of(3.99) == 0)
check("the track's own tempo is reported", close(g.bpm_at(30.0), 128.0))


print("\n2. a tempo change")
# 120 bpm for 64 beats, then 130 from beat 64 onwards.
t64 = 0.5 + 64 * 60 / 120
seg = [[0, 500, 120], [64, t64 * 1000, 130]]
g2 = tt.Grid.from_segments(seg)
check("before the change, 120 bpm", close(g2.beat_at(0.5 + 32 * 0.5), 32.0))
check("exactly at the anchor, the anchor's beat", close(g2.beat_at(t64), 64.0))
check("after the last anchor, it runs at that anchor's tempo",
      close(g2.beat_at(t64 + 60 / 130 * 10), 74.0), f"{g2.beat_at(t64 + 60 / 130 * 10)}")
# Continuity: approach the anchor from both sides.
eps = 1e-6
left, right = g2.beat_at(t64 - eps), g2.beat_at(t64 + eps)
check("the map is continuous across the anchor", abs(right - left) < 1e-4,
      f"{left} -> {right}")
ok = all(close(g2.beat_at(g2.time_at(b)), b, 1e-6)
         for b in [-10, -0.5, 0, 1, 31.5, 63.999, 64, 64.001, 100, 500])
check("time_at and beat_at are exact inverses, inside and outside the anchors", ok)
samples = [g2.beat_at(t / 10) for t in range(0, 600)]
check("and monotonic", all(b2 > b1 for b1, b2 in zip(samples, samples[1:])))
check("bpm_at reports the tempo in force", close(g2.bpm_at(10), 120)
      and close(g2.bpm_at(t64 + 5), 130))

# Interior anchors whose stated bpm is stale: the anchors win, and it warns.
stale = tt.Grid.from_segments([[0, 0, 125], [64, 32000, 130], [128, 61538.46, 130]])
check("the anchors define the tempo between them, not the stated bpm",
      close(stale.beat_at(16.0), 32.0), f"{stale.beat_at(16.0)}")
check("and a stated bpm that disagrees is a warning, not an error",
      any("125" in w for w in stale.warnings()), f"{stale.warnings()}")
check("a consistent grid has no warnings", g2.warnings() == [], f"{g2.warnings()}")


print("\n3. grids that cannot exist are refused")
for label, bad in [
    ("no anchors", []),
    ("time running backwards", [[0, 1000, 120], [64, 900, 120]]),
    ("beats running backwards", [[64, 0, 120], [0, 1000, 120]]),
    ("a repeated beat", [[0, 0, 120], [0, 1000, 120]]),
    ("a bpm of 2", [[0, 0, 2]]),
    ("a string where a number belongs", [[0, "0", 120]]),
    ("true where a number belongs", [[0, True, 120]]),
    ("a short anchor", [[0, 0]]),
]:
    try:
        tt.Grid.from_segments(bad)
        check(f"refused: {label}", False, "it was accepted")
    except tt.GridError as exc:
        check(f"refused: {label}", True, str(exc)[:70])


print("\n4. the rev")
a = tt.Grid.from_segments([[0, 212.5, 124.0]])
b = tt.Grid.from_segments([[0, 212.50000001, 124.0000000001]])
c = tt.Grid.from_segments([[0, 230.0, 124.0]])
check("re-running prep on an unchanged track keeps the rev (float noise)",
      a.rev == b.rev, f"{a.rev} {b.rev}")
check("moving the grid by 17 ms changes it", a.rev != c.rev, f"{a.rev} {c.rev}")
check("it round-trips through the file form unchanged",
      tt.Grid.from_segments(a.segments()).rev == a.rev and a.segments() == [[0, 212.5, 124]],
      f"{a.segments()}")


print("\n5. phrases")
pm = tt.PhraseMap.from_items([[0, 64, "Intro"], [64, 128, "Verse 1"],
                              [128, 160, "Up 2"], [192, 256, "Chorus"]], mood="high")
check("a beat inside a phrase finds it", pm.at(100).label == "Verse 1")
check("the boundary belongs to the phrase starting there", pm.at(128).label == "Up 2")
check("a gap between phrases is None, not the previous phrase", pm.at(170) is None)
check("before the first and after the last is None",
      pm.at(-1) is None and pm.at(256) is None)
check("the family drops rekordbox's number",
      pm.at(100).family == "Verse" and pm.at(130).family == "Up"
      and pm.at(0).family == "Intro" and pm.at(200).family == "Chorus")
check("but not a number that is part of the name",
      tt.Phrase("Chorus2", 0, 1, 0).family == "Chorus2")
for label, bad in [
    ("overlapping phrases", [[0, 64, "Intro"], [60, 128, "Verse 1"]]),
    ("a phrase that ends before it starts", [[64, 0, "Intro"]]),
    ("an unlabelled phrase", [[0, 64, ""]]),
]:
    try:
        tt.PhraseMap.from_items(bad)
        check(f"refused: {label}", False, "it was accepted")
    except tt.GridError as exc:
        check(f"refused: {label}", True, str(exc)[:70])

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("tracktime: all checks pass")
