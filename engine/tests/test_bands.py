"""
Tests for a track's audio as levels over its beats -- what a lane follows when
its row carries `audio`.

Three things have to hold. The bytes are read as rekordbox wrote them: the
waveforms here are built byte by byte from the documented layouts, not with
anything from the decoder. A level is a pure function of the BEAT, on the
track's own grid, so a column lands under the beat it was played on at any
tempo. And the integral is exact, because a `rate.*` lane's integral is a
phase.

Run: python engine/tests/test_bands.py
"""

import ast
import base64
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import bands  # noqa: E402
from engine import tracktime  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def close(a, b, tol=1e-9):
    return abs(a - b) <= tol


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def pwv7(columns) -> dict:
    """columns: [(low, mid, high)], each 0-127."""
    return {"bands": {"format": "pwv7", "rate": 150,
                      "data": b64(bytes(v for c in columns for v in c))}}


def pwv5(columns) -> dict:
    """columns: [(red, green, blue, height)] -- 3, 3, 3 and 5 bits."""
    raw = bytearray()
    for r, g, b, h in columns:
        v = (r << 13) | (g << 10) | (b << 7) | (h << 2)
        raw += bytes([v >> 8, v & 0xFF])
    return {"detail": {"format": "pwv5", "rate": 150, "data": b64(bytes(raw))}}


# -- 1. the bytes ---------------------------------------------------------------
print("\n1. reading rekordbox's analysis")
lv = bands.decode(pwv7([(10, 20, 30), (127, 0, 5), (0, 64, 0)]))
check("the three-band waveform is low, mid, high per column",
      lv is not None and lv.columns["low"] == bytes([10, 127, 0])
      and lv.columns["mid"] == bytes([20, 0, 64])
      and lv.columns["high"] == bytes([30, 5, 0]) and lv.exact)
check("and the overall level is the loudest of the three",
      lv.columns["all"] == bytes([30, 127, 64]))
lv = bands.decode(pwv5([(7, 1, 0, 31), (0, 3, 7, 10), (2, 2, 2, 0)]))
check("the colour waveform gives a band as its colour times the height: red "
      "is low, green mid, blue high",
      lv is not None and lv.columns["low"] == bytes([7 * 31, 0, 0])
      and lv.columns["mid"] == bytes([31, 30, 0])
      and lv.columns["high"] == bytes([0, 70, 0]))
check("its overall level is the height, and it says the bands are an estimate",
      lv.columns["all"] == bytes([31, 10, 0]) and not lv.exact)
both = {**pwv5([(7, 0, 0, 31)]), **pwv7([(1, 2, 3)])}
check("the three-band analysis wins over the colours when a file has both",
      bands.decode(both).columns["low"] == bytes([1]) and bands.decode(both).exact)
lv = bands.decode({"detail": {"format": "pwv3", "rate": 150,
                              "data": b64(bytes([0xE0 | 31, 5, 0]))}})
check("the blue waveform has a height and nothing else: only the overall level",
      lv is not None and set(lv.columns) == {"all"}
      and lv.columns["all"] == bytes([31, 5, 0]))
check("the 400-column preview alone is nothing a lane could follow",
      bands.decode({"preview": b64(bytes(400))}) is None)
check("neither is a file whose data is not base64, or not a waveform at all",
      bands.decode({"bands": {"format": "pwv7", "data": "@@@"}}) is None
      and bands.decode({"detail": {"format": "pwv9", "data": b64(b"abc")}}) is None
      and bands.decode("nope") is None and bands.decode({}) is None)


# -- 2. on the beats ------------------------------------------------------------
print("\n2. laid on the track's beats")
# 150 bpm: a beat is 0.4 s, 60 columns; a cell (1/32 beat) is 1.875 columns.
# The first downbeat is a second in, so the audio starts at beat -2.5.
GRID = tracktime.Grid.from_segments([[0, 1000.0, 150.0]])
N = 150 * 6                                           # six seconds
low = [0] * N
for beat in range(0, 12):                             # a kick on every beat
    at = 150 + beat * 60
    if at + 3 <= N:
        low[at:at + 3] = [100, 60, 30]
# Hats through the second half of every beat, and one crash on beat 1.
high = [(20 if i >= 150 and (i - 150) % 60 >= 30 else 0) for i in range(N)]
high[150 + 60] = 80
doc = pwv7([(low[i], 0, high[i]) for i in range(N)])
audio = bands.Audio(bands.decode(doc), GRID.time_at, GRID.beat_at)
env = audio.envelope("low")
check("a kick is under the beat it was played on",
      all(close(env.level(b), 1.0) for b in range(0, 12))
      and all(env.level(b + 0.5) == 0.0 for b in range(0, 11)),
      f"{[env.level(b) for b in range(4)]} {[env.level(b + .5) for b in range(4)]}")
check("a level is a fraction of the band's loudest in this track",
      close(audio.envelope("high").level(1.0), 1.0)          # the crash
      and close(audio.envelope("high").level(0.75), 20 / 80),
      f"{audio.envelope('high').level(1.0)} {audio.envelope('high').level(0.75)}")
check("a cell is its loudest column, so no transient falls between two cells",
      close(env.level(1.0 + bands.STEP), 0.6)             # of the 60 and the 30
      and env.level(1.0 + 2 * bands.STEP) == 0.0, f"{env.level(1.0 + bands.STEP)}")
check("the audio before the first downbeat is on negative beats",
      audio.first == -80 and env.level(-2.4) == 0.0 and audio.first * bands.STEP == -2.5)
check("and outside the track there is no level at all",
      env.level(-50.0) == 0.0 and env.level(500.0) == 0.0)
check("a band that is silent all through is 0, not a division by nothing",
      set(audio.envelope("mid").cells) == {0.0})
check("the same shape is built once", audio.envelope("low") is env)

# 100 bpm is 90 columns a beat; 200 bpm, 45.
fast = tracktime.Grid.from_segments([[0, 0.0, 100.0], [4, 2400.0, 200.0]])
ticks = [0] * (150 * 4)
for column in (0, 90, 180, 270, 360, 405, 450, 495):  # a beat each, then twice as fast
    ticks[column] = 90
fa = bands.Audio(bands.decode(pwv7([(v, 0, 0) for v in ticks])),
                 fast.time_at, fast.beat_at)
check("through a tempo change the columns stay on the grid's beats",
      all(close(fa.envelope("low").level(b), 1.0) for b in range(8))
      and fa.envelope("low").level(4.5) == 0.0,
      f"{[fa.envelope('low').level(b) for b in range(8)]}")


# -- 3. shaping -------------------------------------------------------------------
print("\n3. floor, ceiling and release")
windowed = audio.envelope("high", floor=0.25, ceiling=0.5)
check("under the floor is silence, and at the ceiling it is all the way there",
      windowed.level(0.75) == 0.0 and close(windowed.level(1.0), 1.0))
half = audio.envelope("high", floor=0.0, ceiling=0.5)
check("between them it is in proportion", close(half.level(0.75), 0.5))
held = audio.envelope("low", release=0.5)
check("a release lets a level fall away: half a beat from full to nothing",
      close(held.level(2.0), 1.0) and close(held.level(2.25), 0.5, 2 * bands.STEP)
      and held.level(2.5 + 2 * bands.STEP) == 0.0,
      f"{held.level(2.0)} {held.level(2.25)} {held.level(2.5 + 2 * bands.STEP)}")
check("but it comes up at once: the attack is the music's own",
      held.level(3.0 - bands.STEP) < 0.1 and close(held.level(3.0), 1.0))
slow = audio.envelope("low", release=4.0)
check("a release longer than the gap between kicks never reaches the bottom",
      min(slow.level(b + 0.99) for b in range(1, 8)) > 0.7)


# -- 4. the integral ------------------------------------------------------------
print("\n4. the integral is exact")
worst = 0.0
for e in (env, held, windowed, audio.envelope("all", 0.1, 0.9, 1.5)):
    for a, b in ((-5.0, 3.3), (0.0, 1.0), (1.26, 7.77), (9.5, 40.0)):
        steps = round((b - a) / (bands.STEP / 8))
        h = (b - a) / steps
        numeric = sum(e.level(a + (i + 0.5) * h) for i in range(steps)) * h
        worst = max(worst, abs((e.integral(b) - e.integral(a)) - numeric))
check("it matches a numeric integral of the level, across the start, the end "
      "and many cells", worst < 2e-3, f"worst {worst:.2e}")
check("before the track it is 0 and after it, flat",
      env.integral(-99.0) == 0.0 and env.integral(600.0) == env.integral(60.0))
fd = (held.integral(2.2 + 1e-6) - held.integral(2.2 - 1e-6)) / 2e-6
check("its slope is the level, so a rate lane's phase follows the band",
      close(fd, held.level(2.2), 1e-6), f"{fd} vs {held.level(2.2)}")


# -- 5. following -----------------------------------------------------------------
print("\n5. a lane following a band")
f = audio.follow({"band": "low", "depth": -0.4, "release": 0.5})
check("adds depth times the level -- below the points, for a negative depth",
      close(f.level(2.0), -0.4) and f.level(2.75) == 0.0
      and close(f.integral(8.0), -0.4 * held.integral(8.0)))
blue = bands.Audio(bands.decode({"detail": {
    "format": "pwv3", "rate": 150, "data": b64(bytes([31]) * 300)}}),
    GRID.time_at, GRID.beat_at)
check("a band this track's analysis does not have is None, for the caller to "
      "say -- the lane plays its points alone",
      blue.follow({"band": "low", "depth": 1}) is None
      and blue.follow({"band": "all", "depth": 1}) is not None
      and blue.bands == ("all",))


def refused(label, spec, needle):
    try:
        audio.follow(spec)
    except ValueError as exc:
        check(label, needle in str(exc), str(exc))
    else:
        check(label, False, "accepted")


refused("a band that does not exist", {"band": "sub", "depth": 1}, "sub")
refused("no depth at all", {"band": "low"}, "depth")
refused("a depth that is text", {"band": "low", "depth": "lots"}, "number")
refused("a floor above its ceiling", {"band": "low", "depth": 1, "floor": 0.8,
                                      "ceiling": 0.5}, "floor")
refused("a release of forever", {"band": "low", "depth": 1, "release": 1e9}, "release")
refused("something that is not an object", "low", "object")


# -- 6. standard library only -----------------------------------------------------
print("\n6. it knows nothing about lights")
tree = ast.parse((REPO / "engine" / "bands.py").read_text(encoding="utf-8"))
imported = set()
for node in ast.walk(tree):
    if isinstance(node, ast.Import):
        imported.update(a.name.split(".")[0] for a in node.names)
    elif isinstance(node, ast.ImportFrom):
        imported.add("." * node.level + (node.module or ""))
STDLIB = {"__future__", "base64", "binascii", "dataclasses", "math", "typing"}
check("bands.py imports only the standard library, as timeline.py needs of it",
      imported <= STDLIB, f"{sorted(imported - STDLIB)}")


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("bands: all checks pass")
