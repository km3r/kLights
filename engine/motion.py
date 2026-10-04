"""
Movement as a path evaluated at the current musical phase.

The old workspace moved heads by stepping between stored positions. That has
two consequences that were both complained about on the night: slowing a routine
down makes it *steppier* rather than smoother, because the steps stay the same
size and just take longer; and every variation of a move costs another stored
scene, which is why 179 of them exist and only a handful got used.

Here a move is a function of phase. Slowing it down samples the same path more
finely, so slow looks get smoother instead of worse, and a variation is an
argument rather than a new scene.

Everything is measured in **bars**, never seconds -- see `engine.clock`.

`path()` is the important one. It takes the list of poses an old chaser stepped
through and interpolates between them, which turns the existing 179-scene
library into continuous motion without re-authoring any of it. That is the
bridge F9 needs to port the library broadly rather than hand-picking survivors.

`cue_path()` is the deliberate exception, for the chases that travel DARK -- see
the "cues" section below. When the audience cannot see the move, its duration
stops being a rendering choice and becomes the effect itself, so that timing is
carried over literally instead of being smoothed away.

Interpolation happens in AIM space -- unwrapped bearing delta and elevation in
degrees -- never in DMX. Interpolating DMX would quantise every intermediate
position to a byte, which is the steppiness this module exists to remove, and
would break outright across the 0/255 boundary of a centre-anchored channel.
"""

from __future__ import annotations

import math
from typing import Callable, Optional, Sequence

from . import geometry as geo

# A movement function: (phase 0..1) -> (d_bearing, d_elevation) in degrees.
#
# Each carries its own natural cycle length on a `bars` attribute, which
# `as_move` uses unless the caller overrides it. Without that the `bars`
# argument on a pattern was silently discarded -- `orbit(15, bars=16)` read as
# a 16-bar orbit and ran at whatever `as_move` defaulted to.
Offset = Callable[[float], tuple[float, float]]

DEFAULT_BARS = 8.0


def _with_bars(fn: Offset, bars: float) -> Offset:
    if bars <= 0:
        raise ValueError("bars must be positive")
    fn.bars = bars                                          # type: ignore[attr-defined]
    return fn


# ------------------------------------------------------------------ easing --

def linear(t: float) -> float:
    return t


def ease_in_out(t: float) -> float:
    """Cosine ease. The default for stepped-library ports, because a chaser's
    stored poses were authored as places to BE, not as waypoints to pass
    through at speed -- easing to a near-stop at each one keeps the original
    look's punctuation while removing its jumps."""
    return 0.5 - 0.5 * math.cos(math.pi * max(0.0, min(1.0, t)))


def ease_out(t: float) -> float:
    return 1.0 - (1.0 - t) ** 2


Easing = Callable[[float], float]


# ------------------------------------------------------------------- phase --

def phase(bar: float, bars: float, offset: float = 0.0) -> float:
    """Position within a cycle of `bars` bars, wrapped to 0..1.

    `offset` is in CYCLES, not bars, so `offset=head/n` spreads n heads evenly
    around one cycle regardless of how long that cycle is -- the phase-offset
    idiom every multi-head move in the old library used, and which had to be
    re-derived by hand for each different chase length.
    """
    if bars <= 0:
        raise ValueError("bars must be positive")
    return (bar / bars + offset) % 1.0


# ---------------------------------------------------------------- patterns --

def orbit(radius_deg: float, bars: float = 8.0, offset: float = 0.0,
          elongation: float = 1.0) -> Offset:
    """A circle in (bearing, elevation) around wherever the base layer aimed.

    Relative, so it tracks each head's own calibration -- an absolute version
    cannot, since one centre cannot serve four heads with four different
    calibrated ball points, and that defect broke 3 of 6 show looks.

    `elongation` squashes elevation against bearing. Above 1 it is a wide flat
    oval, which on a corner rig reads as a horizontal sweep rather than a
    circle, and which the room's geometry usually wants -- there is far more
    bearing available than elevation.
    """
    def offset_at(p: float) -> tuple[float, float]:
        theta = 2.0 * math.pi * ((p + offset) % 1.0)
        return (radius_deg * math.sin(theta),
                radius_deg * math.cos(theta) / elongation)
    return _with_bars(offset_at, bars)


def pendulum(swing_deg: float, bars: float = 4.0, offset: float = 0.0,
             vertical: bool = False) -> Offset:
    """Back and forth, easing to a stop at each end like a real pendulum.

    A sine, not a triangle: a triangle reverses at full speed, which on a head
    with mechanical inertia produces an audible clack and a visible overshoot.
    """
    def offset_at(p: float) -> tuple[float, float]:
        value = swing_deg * math.sin(2.0 * math.pi * ((p + offset) % 1.0))
        return (0.0, value) if vertical else (value, 0.0)
    return _with_bars(offset_at, bars)


def path(poses: Sequence[tuple[float, float]], easing: Easing = ease_in_out,
         closed: bool = True, offset: float = 0.0,
         bars: float = DEFAULT_BARS) -> Offset:
    """Interpolate through a list of (bearing, elevation) offsets.

    This is the stepped-chaser bridge: hand it the poses an old chase stepped
    through and it becomes a continuous move over the same route.

    `closed` wraps the last pose back to the first, which is what a looping
    chase did. Set it False for a one-shot sweep, where the path holds at the
    final pose rather than racing back to the start.
    """
    if not poses:
        raise ValueError("path needs at least one pose")
    if len(poses) == 1:
        single = poses[0]
        return _with_bars(lambda p: single, bars)

    n = len(poses)
    segments = n if closed else n - 1

    def offset_at(p: float) -> tuple[float, float]:
        t = (p + offset) % 1.0
        scaled = t * segments
        index = min(int(scaled), segments - 1)
        local = easing(scaled - index)
        a = poses[index]
        b = poses[(index + 1) % n]
        return (a[0] + (b[0] - a[0]) * local,
                a[1] + (b[1] - a[1]) * local)
    return _with_bars(offset_at, bars)


# ------------------------------------------------------------------- cues --
#
# Everything above turns a stepped chase into continuous motion, which is this
# module's whole premise and is right for a chase that moves LIT: you watch the
# beam travel, so a smooth route is strictly better than a jump.
#
# It is wrong for a chase that goes DARK to travel. There the travel time is not
# a rendering detail, it is the effect: the head is unlit for exactly as long as
# it takes to get there, snaps on on arrival, and holds. Spread that evenly over
# the cycle and the beam is never still and never absent -- which is how the
# whole "Dark Moves" family (Teleport, Apparition, Stutter, Glitch, Ascension,
# Blink, Freeze Frame) came through the port as ordinary lit sweeps, the exact
# look each of them was written to be the opposite of.
#
# So a cue keeps its source timing literally: per step, how long the move in
# takes and how long it is held afterwards. `path()` remains the default; this
# is for the chases whose shape lives in their timing.


def cue_at(spans: Sequence[tuple[float, float]], p: float) -> tuple[int, float]:
    """(step index, progress 0..1 into that step's fade; 1.0 once holding).

    `spans` is per step (fade, hold) in any single unit -- ms straight off the
    source chaser is the intended one -- and is normalised here, so the caller
    states durations once and never has to keep a set of fractions summing to 1.
    """
    total = sum(fade + hold for fade, hold in spans)
    if total <= 0:
        raise ValueError("a cue list needs at least one non-zero span")
    x = (p % 1.0) * total
    for index, (fade, hold) in enumerate(spans):
        if x < fade:
            return index, (x / fade if fade > 0 else 1.0)
        x -= fade
        if x < hold:
            return index, 1.0
        x -= hold
    # Only reachable on the last step by floating-point drift at p ~ 1.
    return len(spans) - 1, 1.0


def cue_path(poses: Sequence[tuple[float, float]],
             spans: Sequence[tuple[float, float]],
             easing: Easing = ease_in_out, offset: float = 0.0,
             bars: float = DEFAULT_BARS) -> Offset:
    """Move into each pose over its own fade, then hold it for its own hold.

    Wraps: step 0 is entered from the LAST pose, because a chase loops.
    """
    if len(poses) != len(spans):
        raise ValueError(f"{len(poses)} poses but {len(spans)} spans")

    def offset_at(p: float) -> tuple[float, float]:
        index, t = cue_at(spans, p + offset)
        a = poses[index - 1]                       # -1 wraps to the last pose
        b = poses[index]
        local = easing(t)
        return (a[0] + (b[0] - a[0]) * local,
                a[1] + (b[1] - a[1]) * local)
    return _with_bars(offset_at, bars)


def cue_value(values: Sequence[Optional[float]],
              spans: Sequence[tuple[float, float]], p: float,
              released: float = 0.0) -> float:
    """One scalar -- a dimmer level -- through the same cue list.

    `None` means the step does not SET this value, which is a different thing
    from setting it to zero and is the whole mechanism of a dark move. A fade
    belongs to the channels a step writes; anything it does not write is
    released the instant the step begins, and drops to `released` at once. So a
    dark step goes dark and THEN travels, rather than dimming out across the
    travel -- the difference between a beam that vanishes and one you watch
    fade away as it swings, which is the effect these routines are made of.

    Values that ARE set ramp LINEARLY over the fade, unlike position: this is a
    dimmer fade and the source console's are linear. That one fact is what
    separates Teleport from Apparition -- identical poses, identical dark
    travel, and Apparition's arrival step simply fades its dimmer up over three
    seconds instead of snapping it. Both fall out of the same data.

    `released` is 0 because this engine has no equivalent of the console's
    master dimmer fader. There, an unwritten dimmer fell back to whatever that
    fader was parked at, so "dark" was really "however low you left it" -- and
    the whole family silently stopped working if it was left high. Here dark is
    dark, which is what the routines were reaching for.
    """
    index, t = cue_at(spans, p)
    target = values[index]
    if target is None:
        return released
    previous = values[index - 1]
    previous = released if previous is None else previous
    return previous + (target - previous) * t


def points_to_offsets(rig_geo: geo.RigGeometry, head: int,
                      points: Sequence[tuple[float, float, float]]
                      ) -> list[tuple[float, float]]:
    """World points -> the aim offsets `path()` interpolates between.

    Offsets relative to this head's ball aim, not absolute aims, so the
    resulting path inherits calibration tracking. Converting to offsets once,
    up front, also keeps the per-frame cost to interpolation -- the trig runs
    when a look is built rather than 40 times a second.
    """
    frame = rig_geo.frame(head)
    out = []
    for x, y, z in points:
        aim = rig_geo.aim_at_point(head, x, y, z)
        out.append((aim.bearing_delta - frame.bearing_delta_ball,
                    aim.elev_deg - frame.elev_to_ball))
    return out


# --------------------------------------------------------------- intensity --

def pulse(depth: float = 1.0, bars: float = 1.0, offset: float = 0.0,
          easing: Easing = ease_out) -> Callable[[float], float]:
    """A brightness envelope that falls from full over each cycle.

    `depth` 1.0 goes to black between pulses, 0.3 just breathes. Returns a
    multiplier, so it composes with the master and the safety taper rather than
    fighting them.
    """
    def level_at(p: float) -> float:
        t = (p + offset) % 1.0
        return 1.0 - depth * easing(t)
    return _with_bars(level_at, bars)


def chase(count: int, bars: float = 1.0, width: float = 0.5,
          offset: float = 0.0) -> Callable[[int, float], float]:
    """A brightness bump travelling across `count` fixtures.

    Returns (index, phase) -> multiplier. `width` is the bump's size as a
    fraction of the cycle: at 1/count only one fixture is lit at a time, and
    wider values overlap into a smooth travelling wave.
    """
    def level_at(index: int, p: float) -> float:
        centre = ((p + offset) % 1.0) * count
        distance = abs(index - centre)
        distance = min(distance, count - distance)      # wrap around the ring
        span = width * count
        return max(0.0, 1.0 - distance / span) if span > 0 else 0.0
    return _with_bars(level_at, bars)


# ------------------------------------------------------------------ adapter --

def as_move(offset_fn: Offset, bars: Optional[float] = None,
            per_head_offset: bool = True) -> Callable:
    """Wrap a movement function for `state.move_layer`.

    Handles the two things every move needs and nothing else: reading musical
    position off the context, and spreading heads evenly around the cycle. The
    per-head spread is what stops four heads moving in lockstep, which reads as
    one big light rather than four.

    `bars` defaults to whatever the pattern was built with, so the cycle length
    is stated once at the place that knows it. Passing it here overrides that,
    for reusing one pattern at two lengths.
    """
    if bars is None:
        bars = getattr(offset_fn, "bars", DEFAULT_BARS)

    def offset_for(ctx, head: int) -> tuple[float, float]:
        n = len(ctx.geometry.heads) if ctx.geometry is not None else 1
        spread = (head / n) if (per_head_offset and n) else 0.0
        # motion_bar, not bar: auto mode varies movement rate by integrating a
        # separate phase, so that speeding movement up does not shift musical
        # position (and so that a rate change cannot jump the move). With no
        # director attached the runner keeps the two identical.
        return offset_fn(phase(ctx.motion_bar, bars, spread))
    return offset_for


# ------------------------------------------------------------------ hashing --

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

    Lives here, the bottom of the import graph, because both a block
    (`scatter`) and a modulator shape (`hold`) need it.
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
