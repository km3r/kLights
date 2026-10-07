"""
Musical waveforms: the shapes a value can follow, as pure functions of where
in its cycle it is -- and their exact integrals.

Two things swing values along these shapes. A console modulator
(`modulate.py`) swings a live parameter; an automation row's `wave`
(`timeline.Curve`) swings a show-folder lane on top of its points. They are one
set of shapes so a "sine over 4 bars" on the console and in a routine are the
same sine.

**Why the integrals.** A `rate.*` lane's integral is a PHASE: the compiler
asks "how many beats of motion has this clip run by beat 161.5" and answers
from `Curve.integral`, computed from the beat rather than accumulated per
frame. That is what makes a loop, a hot cue or a scrub land on the frame that
was authored there. A wave on a rate lane has to keep that promise, so each
shape's area is closed-form -- and `hold`, whose levels are a hash per cycle, is
an exact sum of them, memoised.

**Why this is its own module.** `timeline.py` imports only the standard
library, so a VJ output can use it without the lights coming along, and a wave
is part of a curve. So the shapes and the hash they need (`sampled`, once in
`motion.py`) live here, below everything; `motion` and `modulate` import them.

Positions are in CYCLES. Every shape is 0..1 and starts its cycle at 0, so a
wave switched on at the top of a bar starts from where its lane already is.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

SHAPES = ("sine", "triangle", "ramp", "saw", "square", "hold")

# Every grid here is 4/4 (`tracktime.BEATS_PER_BAR`, held equal by a test --
# this module cannot import it).
BEATS_PER_BAR = 4.0

_MASK = 0xFFFFFFFFFFFFFFFF


def sampled(seed: int, *key: int) -> float:
    """A stable pseudo-random -1..1 for a (seed, ...) tuple.

    A HASH, not a stream, and deliberately not `random`. A stateful RNG makes
    the output depend on how many frames have been rendered, so the rig and the
    previz diverge the instant either drops a frame and no parity sweep can
    reproduce anything. `random.Random` also refuses a tuple seed on 3.11+, and
    constructing a Mersenne Twister per sample costs ~600 operations inside a
    25 ms frame budget the F2 timing spike showed is not generous.

    This is splitmix64's finalizer: a few multiplies and shifts, with the
    avalanche behaviour the looks need -- adjacent heads and adjacent stations
    must not land near each other, or a scatter reads as a wave.
    """
    x = seed & _MASK
    for k in key:
        x = (x * 0x9E3779B97F4A7C15 + (k & _MASK) + 0x165667B19E3779F9) & _MASK
        x ^= x >> 30
        x = (x * 0xBF58476D1CE4E5B9) & _MASK
        x ^= x >> 27
        x = (x * 0x94D049BB133111EB) & _MASK
        x ^= x >> 31
    return (x / _MASK) * 2.0 - 1.0


def _held(seed: int, cycle: int) -> float:
    return (sampled(seed, cycle) + 1.0) * 0.5


def unit(shape: str, p: float, seed: int = 0) -> float:
    """The shape at `p` cycles, 0..1."""
    f = p - math.floor(p)
    if shape == "sine":
        # Starts at 0 and returns to it, rather than starting mid-swing: a
        # breathing size that begins halfway open looks like a glitch on the
        # bar it is switched on.
        return 0.5 - 0.5 * math.cos(2.0 * math.pi * f)
    if shape == "triangle":
        return 2.0 * f if f < 0.5 else 2.0 - 2.0 * f
    if shape == "ramp":
        return f
    if shape == "saw":
        # ramp's mirror. Both snap once per cycle, which is the point -- it is
        # the shape of a build.
        return 1.0 - f
    if shape == "square":
        return 0.0 if f < 0.5 else 1.0
    if shape == "hold":
        # A fresh level each cycle, held flat. The cycle number is hashed, not
        # accumulated, so the same bar always gives the same level.
        return _held(seed, math.floor(p))
    raise ValueError(f"no shape {shape!r} -- one of {', '.join(SHAPES)}")


def _cycle_area(shape: str, f: float) -> float:
    """∫ unit over [0, f] of one cycle, f in 0..1, for the periodic shapes."""
    if shape == "sine":
        return 0.5 * f - math.sin(2.0 * math.pi * f) / (4.0 * math.pi)
    if shape == "triangle":
        return f * f if f < 0.5 else 2.0 * f - f * f - 0.5
    if shape == "ramp":
        return 0.5 * f * f
    if shape == "saw":
        return f - 0.5 * f * f
    if shape == "square":
        return 0.0 if f < 0.5 else f - 0.5
    raise ValueError(f"no shape {shape!r} -- one of {', '.join(SHAPES)}")


# seed -> running sums of `hold`'s levels: [0, h0, h0+h1, ...] forwards and
# [0, h-1, h-1+h-2, ...] backwards. A memo of a pure function, replaced whole
# rather than appended to, so the worker and the output thread can both extend
# it and neither ever reads a half-built list.
_HOLD_UP: dict[int, list[float]] = {}
_HOLD_DOWN: dict[int, list[float]] = {}


def _sums(table: dict[int, list[float]], seed: int, n: int, step: int) -> float:
    """The sum of `n` held levels from cycle 0 (step 1) or -1 (step -1)."""
    sums = table.get(seed) or [0.0]
    if len(sums) <= n:
        sums = list(sums)
        while len(sums) <= n:
            k = len(sums) - 1
            sums.append(sums[-1] + _held(seed, k if step > 0 else -1 - k))
        table[seed] = sums
    return sums[n]


def area(shape: str, p: float, seed: int = 0) -> float:
    """∫ unit from 0 to `p` cycles (negative before 0). Exact: closed-form for
    the periodic shapes, an exact sum of levels for `hold`."""
    n = math.floor(p)
    f = p - n
    if shape == "hold":
        within = f * _held(seed, n)
        if n >= 0:
            return _sums(_HOLD_UP, seed, n, 1) + within
        return within - _sums(_HOLD_DOWN, seed, -n, -1)
    # Every periodic shape averages 1/2 over a cycle.
    return 0.5 * n + _cycle_area(shape, f)


@dataclass(frozen=True)
class Wave:
    """A lane's wave: its points' value plus `depth` times the shape.

    Additive and one-sided -- the points are where the lane rests and the wave
    lifts it by up to `depth` (or lowers it, with a negative depth) -- so a
    validator can bound the whole swing from the points alone, and an existing
    lane gains a wave without its resting values moving.

    For a color lane, `toward` is the color it swings to and `depth` (0..1)
    how far: at the top of the cycle the lane's color is blended that far
    towards it.
    """
    shape: str
    bars: float
    depth: float = 1.0
    phase: float = 0.0           # cycles, like every other offset here
    seed: int = 0
    toward: Any = None

    @property
    def period(self) -> float:
        """One cycle, in beats."""
        return self.bars * BEATS_PER_BAR

    def cycles(self, beat: float) -> float:
        return beat / self.period + self.phase

    def level(self, beat: float) -> float:
        """What the wave adds at `beat`: depth times the shape."""
        return self.depth * unit(self.shape, self.cycles(beat), self.seed)

    def integral(self, beat: float) -> float:
        """∫ level from beat 0 to `beat`. Exact."""
        return self.depth * self.period * (
            area(self.shape, self.cycles(beat), self.seed)
            - area(self.shape, self.phase, self.seed))

    @classmethod
    def from_spec(cls, spec: Any, where: str = "wave") -> "Wave":
        """From an automation row's `wave` object. Raises ValueError with what
        is wrong; `showfiles` validates first, so this is the backstop."""
        if not isinstance(spec, dict):
            raise ValueError(f"{where} must be an object")
        shape = spec.get("shape", "sine")
        if shape not in SHAPES:
            raise ValueError(f"{where} shape {shape!r} is not one of "
                             f"{', '.join(SHAPES)}")
        nums = {}
        for key, default in (("bars", None), ("depth", 1.0), ("phase", 0.0),
                             ("seed", 0)):
            value = spec.get(key, default)
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"{where} {key} must be a number, got {value!r}")
            nums[key] = value
        if nums["bars"] <= 0:
            raise ValueError(f"{where} bars must be more than 0")
        return cls(shape, float(nums["bars"]), float(nums["depth"]),
                   float(nums["phase"]), int(nums["seed"]), spec.get("toward"))
