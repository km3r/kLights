"""
Tests for the musical waveforms a lane's wave and a console modulator share.

What matters is that the AREAS are right: a wave on a rate lane is integrated
into a phase, and if `waves.area` and `waves.unit` disagree the heads land
somewhere a scrub to the same beat does not. So every shape's closed form (and
hold's exact sum) is held against a numerical integral of its own `unit`, over
ranges that start before zero and cross many cycles.

Run: python engine/tests/test_waves.py
"""

import math
import random
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import modulate  # noqa: E402
from engine import motion  # noqa: E402
from engine import tracktime  # noqa: E402
from engine import waves  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def numeric_area(shape, a, b, seed=0, steps_per_cycle=4000):
    """∫ unit from a to b by the midpoint rule, split at every half cycle so
    the square's and hold's jumps never fall inside a step."""
    edges = sorted({a, b} | {k / 2 for k in range(math.floor(2 * a), math.ceil(2 * b) + 1)
                             if a < k / 2 < b})
    total = 0.0
    for lo, hi in zip(edges, edges[1:]):
        n = max(1, int((hi - lo) * steps_per_cycle))
        h = (hi - lo) / n
        total += sum(waves.unit(shape, lo + (i + 0.5) * h, seed) for i in range(n)) * h
    return total


print("\n1. the areas are the integrals of the shapes")
rng = random.Random(7)
for shape in waves.SHAPES:
    worst = 0.0
    for _ in range(6):
        a = rng.uniform(-5.0, 3.0)
        b = a + rng.uniform(0.1, 9.0)
        exact = waves.area(shape, b, 3) - waves.area(shape, a, 3)
        worst = max(worst, abs(exact - numeric_area(shape, a, b, 3)))
    check(f"{shape}: closed-form area matches the numerical integral, across "
          f"zero and many cycles", worst < 1e-5, f"worst {worst:.2e}")
check("every shape stays within 0..1",
      all(0.0 <= waves.unit(s, x / 37.0, 5) <= 1.0
          for s in waves.SHAPES for x in range(-200, 200)))
check("and starts its cycle at 0 (but hold, which picks a level)",
      all(waves.unit(s, 0.0) == 0.0 for s in waves.SHAPES if s not in ("hold", "saw")))
waves._HOLD_UP.clear()
waves._HOLD_DOWN.clear()
forward = (waves.area("hold", 30.5, 11), waves.area("hold", -40.3, 11))
waves._HOLD_UP.clear()
waves._HOLD_DOWN.clear()
backward = tuple(reversed((waves.area("hold", -40.3, 11), waves.area("hold", 30.5, 11))))
check("hold's area is the same whichever way its memo was filled -- it is a "
      "pure function of the beat, the memo only saves the sum",
      forward == backward, f"{forward} vs {backward}")


print("\n2. a Wave in beats")
w = waves.Wave("sine", bars=2, depth=0.5, phase=0.25)
check("a cycle is bars of 4/4 beats, the grid every track is on",
      waves.BEATS_PER_BAR == tracktime.BEATS_PER_BAR and w.period == 8.0)
check("level is depth times the shape, phase in cycles",
      abs(w.level(0.0) - 0.5 * waves.unit("sine", 0.25)) < 1e-12
      and abs(w.level(4.0) - 0.5 * waves.unit("sine", 0.75)) < 1e-12)
fd = (w.integral(13.001) - w.integral(12.999)) / 0.002
check("its integral's slope is its level (so a rate lane's phase follows it)",
      abs(fd - w.level(13.0)) < 1e-6, f"{fd} vs {w.level(13.0)}")
check("and the integral from beat 0 is 0", w.integral(0.0) == 0.0)


def refused(label, spec, needle):
    try:
        waves.Wave.from_spec(spec)
    except ValueError as exc:
        check(label, needle in str(exc), str(exc))
    else:
        check(label, False, "accepted")


refused("a shape that does not exist", {"shape": "wobble", "bars": 1}, "wobble")
refused("a cycle of no length", {"shape": "sine", "bars": 0}, "more than 0")
refused("a depth that is text", {"shape": "sine", "bars": 1, "depth": "lots"}, "number")
refused("no cycle length at all", {"shape": "sine"}, "bars")


print("\n3. one set of shapes, for the console and the show folder")
check("motion.sampled is waves.sampled, so a scatter's stations did not move",
      motion.sampled is waves.sampled)
check("the console's modulator offers every lane shape, plus energy",
      modulate.SHAPES == waves.SHAPES + ("energy",))
m = modulate.Modulator("size", shape="hold", bars=2.0, seed=9)
check("a hold modulator picks the levels it always picked",
      all(abs(m.unit(bar) - (motion.sampled(9, int(bar / 2.0)) + 1.0) * 0.5) < 1e-12
          for bar in (0.0, 1.9, 2.0, 7.5, 31.0)))
m = modulate.Modulator("size", shape="triangle", bars=4.0, phase=0.1)
check("a triangle modulator is the same triangle",
      all(abs(m.unit(bar) - (lambda p: 2 * p if p < 0.5 else 2 - 2 * p)(
          (bar / 4.0 + 0.1) % 1.0)) < 1e-12 for bar in (0.0, 1.3, 2.2, 9.9)))


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("waves: all checks pass")
