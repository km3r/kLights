"""Modulator shapes, and the properties that make them safe to run a show on.

A modulator is the one thing in this engine whose output nobody sets. That makes
two properties matter more than the exact waveform:

  * it must be a pure function of musical position, so the rig, the plan view
    and previz still agree after any of them drops a frame;
  * it must be bounded by its target's own declaration, so it cannot reach a
    value a finger could not have dragged to.

Both are asserted here. `test_safety.py` sections 8b and 8c cover the other
half -- that nothing modulated can outrank the taper.

Run: python engine/tests/test_modulate.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import blocks as blocksmod
from engine import modulate as modmod
from engine import params as parammod
from engine.modulate import Modulator, ModulatorError, Rack

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


BARS = [b / 32.0 for b in range(0, 32 * 4 + 1)]      # four cycles at bars=1


def sweep(shape, **kwargs):
    mod = Modulator(param="size", shape=shape, bars=1.0, **kwargs)
    return [mod.unit(b) for b in BARS]


print(f"\n1. every shape stays in 0..1 across a whole cycle")
for shape in modmod.SHAPES:
    values = ([Modulator(param="size", shape=shape, bars=1.0).unit(b, e)
               for b in BARS for e in (0.0, 0.5, 1.0)]
              if shape == "energy" else sweep(shape))
    check(f"{shape} stays within 0..1", all(0.0 <= v <= 1.0 for v in values),
          f"{min(values):.3f}..{max(values):.3f}")

print("\n2. each shape is actually the shape it claims")
sine = sweep("sine")
# Starts AT the bottom rather than mid-swing. A breathing size that begins
# halfway open reads as a glitch on the bar it is switched on.
check("sine starts at its low end", abs(sine[0]) < 1e-9, f"{sine[0]:.4f}")
check("sine reaches its high end", abs(max(sine) - 1.0) < 1e-9)
check("sine returns to the bottom by the end of the cycle",
      abs(sine[32]) < 1e-9, f"{sine[32]:.4f}")
# Smooth: no single step is a jump. This is what separates it from square.
steps = [abs(b - a) for a, b in zip(sine, sine[1:])]
check("and it never jumps", max(steps) < 0.12, f"biggest step {max(steps):.3f}")

tri = sweep("triangle")
check("triangle peaks halfway through", abs(tri[16] - 1.0) < 1e-9, f"{tri[16]}")
check("triangle is symmetrical",
      all(abs(tri[i] - tri[32 - i]) < 1e-9 for i in range(17)))

ramp, saw = sweep("ramp"), sweep("saw")
check("ramp climbs", ramp[0] < ramp[8] < ramp[16] < ramp[24])
check("saw is its mirror",
      all(abs(r + s - 1.0) < 1e-9 for r, s in zip(ramp[:32], saw[:32])))

square = sweep("square")
check("square only ever takes two values", set(square) == {0.0, 1.0},
      f"{sorted(set(square))}")

print("\n3. hold is random-looking and is not random")
# The same argument as the scatter block: a stateful RNG would make the
# output depend on how many frames had been rendered, so previz and the rig
# would diverge the moment either dropped one.
a = Modulator(param="size", shape="hold", bars=1.0, seed=3)
b = Modulator(param="size", shape="hold", bars=1.0, seed=3)
c = Modulator(param="size", shape="hold", bars=1.0, seed=4)
check("the same seed gives identical values",
      [a.unit(x) for x in BARS] == [b.unit(x) for x in BARS])
check("a different seed gives different ones",
      [a.unit(x) for x in BARS] != [c.unit(x) for x in BARS])
# Held flat within a cycle, and a new level at each boundary -- that is the
# whole difference between "hold" and "noise".
within = {round(a.unit(0.1 + i / 100.0), 9) for i in range(80)}
check("it holds one level for a whole cycle", len(within) == 1, f"{within}")
check("and picks a new one at the boundary",
      a.unit(0.5) != a.unit(1.5))

print("\n4. energy ignores time and follows the room")
energy = Modulator(param="size", shape="energy", bars=1.0)
check("the same energy gives the same value whenever it is asked",
      energy.unit(0.0, 0.3) == energy.unit(37.5, 0.3))
check("and it tracks the energy it is given",
      energy.unit(0.0, 0.0) < energy.unit(0.0, 0.5) < energy.unit(0.0, 1.0))
check("out-of-range energy is clamped rather than escaping the unit interval",
      energy.unit(0.0, 5.0) == 1.0 and energy.unit(0.0, -5.0) == 0.0)

print("\n5. phase offsets are in CYCLES, so two modulators stay related")
# Same convention as `move_spread` and `motion.phase`: a half-cycle apart means
# the same thing at any period, which it would not if this were in bars.
for bars in (1.0, 4.0, 16.0):
    first = Modulator(param="size", shape="sine", bars=bars)
    second = Modulator(param="size", shape="sine", bars=bars, phase=0.5)
    check(f"half a cycle apart at {bars:g} bars",
          abs(first.unit(0.0) - second.unit(bars / 2.0)) < 1e-9)

print("\n6. bounds come from the target and cannot be escaped")
radius = blocksmod.param("orbit", "radius")
plain = modmod.build({}, radius)
check("with no numbers at all it sweeps the target's full range",
      (plain.low, plain.high) == (radius.min, radius.max),
      f"{plain.low}..{plain.high}")
wild = modmod.build({"low": -1e6, "high": 1e6}, radius)
check("absurd bounds are clamped to the declared range",
      (wild.low, wild.high) == (radius.min, radius.max))
# Inverted bounds are KEPT: "run it backwards" is a legitimate thing to say, and
# silently swapping them would make an authored fall quietly climb.
inverted = modmod.build({"low": 40, "high": 10}, radius)
check("inverted bounds are kept, not corrected",
      (inverted.low, inverted.high) == (40.0, 10.0))
check("and an inverted ramp really does fall",
      inverted.value(0.0) > inverted.value(0.9))

print("\n7. malformed modulators are refused at construction")
for label, spec in (("an unknown shape", {"shape": "wobble"}),
                    ("a zero cycle", {"shape": "sine", "bars": 0}),
                    ("a negative cycle", {"shape": "ramp", "bars": -4})):
    try:
        modmod.build(spec, radius)
        check(f"{label} is refused", False, "accepted")
    except ModulatorError:
        check(f"{label} is refused", True)
# `energy` is not a function of time, so it is the one shape a zero cycle is
# meaningless for rather than wrong.
try:
    modmod.build({"shape": "energy", "bars": 0}, radius)
    check("energy does not need a cycle", True)
except ModulatorError as exc:
    check("energy does not need a cycle", False, str(exc))

print("\n8. the rack replaces rather than stacking")
rack = Rack()
rack.add(modmod.build({"shape": "sine", "bars": 4}, radius))
rack.add(modmod.build({"shape": "square", "bars": 8}, radius))
check("binding the same target twice leaves one modulator", len(rack) == 1)
check("and it is the second one",
      next(iter(rack.by_key.values())).shape == "square")

rack.add(modmod.build({"look": "Ball Orbit", "shape": "ramp", "bars": 2},
                      radius))
check("a routine target is distinct from the macro of the same name",
      len(rack) == 2)
macros, looks = rack.resolve(1.0)
check("resolve splits macros from routine parameters",
      set(macros) == {"radius"} and set(looks) == {"Ball Orbit"},
      f"{macros} / {looks}")
check("and the routine's parameter is under its own name",
      set(looks["Ball Orbit"]) == {"radius"})

check("removing one reports that it did", rack.remove("Ball Orbit", "radius"))
check("removing it again reports that it did not",
      not rack.remove("Ball Orbit", "radius"))
rack.clear()
check("clear empties the rack", len(rack) == 0)
check("and an empty rack resolves to nothing", rack.resolve(1.0) == ({}, {}))

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("modulate: all checks pass")
