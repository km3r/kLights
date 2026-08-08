"""What the yoke does with the number you sent it: a head takes time to get there.

Everything else in this engine treats a position as instantaneous, and for the
SHOW that is correct -- DMX is a command, and the fixture's own servo is what
turns it into motion. It is wrong for a PREVIZ, which is supposed to answer
"what will the room look like", and the room contains four heads that need the
better part of a second to cross it.

Without this the previz teleports. Change routine and the beams are simply
somewhere else on the next frame, so the one thing a previz is best placed to
show -- that switching from a crowd pose to a zenith pose drags four lit beams
across every face in the room on the way -- is invisible, and every dark move is
invisible too, because a dark move IS its travel time.

**The limit is applied in RAW CHANNEL SPACE, not to bearing and elevation.**
That is what the motors turn, and it is the only frame in which the two axes
have independent speeds. It also means this needs to know nothing about the
mount: on this rig the heads are bolted sideways so Pan carries elevation, and a
model that rate-limited "elevation" would be attributing the pan motor's speed
to the tilt axis and vice versa. Feed it the same 16-bit words the fixture gets
and the mounting cannot enter into it.

Both axes run at once, each at its own limit -- which is what a real head does,
and is why a diagonal move finishes when the SLOWER axis arrives rather than
after two moves in sequence.

**The speeds are an assumption**, and the one number here worth being suspicious
of. No `.qxf` declares a slew rate (QLC+ has nowhere to put one) and no
measurement of these fixtures exists, so the defaults below are this class of
60 W beam's usual published figures. `rig.json` can override per unit, the same
lever `lumens` and `beam_deg` already have -- and when someone finally times a
head crossing the room, that is where the answer goes.

What is deliberately NOT modelled: acceleration. A real yoke ramps up and brakes,
so this arrives slightly early on a long move. Modelling it would mean inventing
a second unmeasured number to correct an error smaller than the one already in
the first. What IS modelled matters far more: that the move takes roughly a
second at all.

Run: python engine/servo.py
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

# 540 deg in 2.5 s and 270 deg in 1.5 s -- the figures a 60 W spot of this class
# publishes. Not measured on ours. See the module docstring.
DEFAULT_PAN_DEG_PER_S = 216.0
DEFAULT_TILT_DEG_PER_S = 180.0

FULL_SCALE = 65535


@dataclass
class Servo:
    """One head's yoke, following a commanded position at a finite speed.

    Holds its own current position, in the same 16-bit units the fixture is
    addressed in, as a FLOAT: a servo that rounded to a DMX step each frame
    would quantise its own motion at 40 fps, which is the steppiness the whole
    engine goes to some trouble to avoid.
    """
    pan_range_deg: float = 540.0
    tilt_range_deg: float = 270.0
    pan_deg_per_s: float = DEFAULT_PAN_DEG_PER_S
    tilt_deg_per_s: float = DEFAULT_TILT_DEG_PER_S

    pan: Optional[float] = None
    tilt: Optional[float] = None
    # The last position commanded, remembered so that "be where you were told
    # to be" is answerable without the caller having to keep the DMX frame.
    target: Optional[tuple[float, float]] = None

    def _rates(self) -> tuple[float, float]:
        """Max words per second on each axis."""
        return (self.pan_deg_per_s / self.pan_range_deg * FULL_SCALE,
                self.tilt_deg_per_s / self.tilt_range_deg * FULL_SCALE)

    def snap(self, pan: float, tilt: float) -> tuple[float, float]:
        """Be there now. For the first frame, and for anything that renders a
        still -- a snapshot taken mid-travel would otherwise photograph the head
        wherever it had crept to, which is not what the caller asked to see."""
        self.pan, self.tilt = float(pan), float(tilt)
        self.target = (self.pan, self.tilt)
        return self.pan, self.tilt

    def settle(self) -> tuple[float, float]:
        """Jump to the last commanded position. A no-op until one arrives."""
        if self.target is None:
            return (0.0, 0.0)
        return self.snap(*self.target)

    def follow(self, pan: float, tilt: float, dt: float) -> tuple[float, float]:
        """Advance toward the commanded position over `dt` seconds.

        Returns where the head actually IS, which is what previz must draw. The
        first call snaps: a previz starting up should show the rig where the
        console has it, not sweep in from wherever zero happens to be.
        """
        self.target = (float(pan), float(tilt))
        if self.pan is None or self.tilt is None:
            return self.snap(pan, tilt)
        if dt <= 0:
            return self.pan, self.tilt
        pan_rate, tilt_rate = self._rates()
        self.pan = _step(self.pan, float(pan), pan_rate * dt)
        self.tilt = _step(self.tilt, float(tilt), tilt_rate * dt)
        return self.pan, self.tilt

    def arrived(self, pan: float, tilt: float, tolerance: float = 8.0) -> bool:
        """Within a couple of DMX steps of the command. The tolerance is in
        16-bit words -- 8 is well under one coarse step, so this is "the beam is
        where it was told to be" and not "the maths converged"."""
        if self.pan is None or self.tilt is None:
            return False
        return (abs(self.pan - pan) <= tolerance
                and abs(self.tilt - tilt) <= tolerance)

    def travel_time(self, pan_from: float, tilt_from: float,
                    pan_to: float, tilt_to: float) -> float:
        """Seconds this head needs for that move. The slower axis decides.

        Used to ask the question a dark move depends on and that nothing else
        can answer: is the travel long enough for the head to actually arrive
        before the dimmer comes back up?
        """
        pan_rate, tilt_rate = self._rates()
        return max(abs(pan_to - pan_from) / pan_rate if pan_rate > 0 else 0.0,
                   abs(tilt_to - tilt_from) / tilt_rate if tilt_rate > 0 else 0.0)


def _step(current: float, target: float, limit: float) -> float:
    delta = target - current
    if abs(delta) <= limit:
        return target
    return current + (limit if delta > 0 else -limit)


@dataclass
class Rack:
    """A servo per fixture, keyed however the caller keys its fixtures.

    Separate from `Servo` so that the thing holding per-frame state across a
    whole rig is one object with one lifetime. In previz that matters: the
    driver is reloaded on every restart, and state that lives in a module-level
    dict quietly survives -- or quietly does not -- depending on which.
    """
    servos: dict = field(default_factory=dict)

    def of(self, key, fixture=None, head=None) -> Servo:
        servo = self.servos.get(key)
        if servo is None:
            servo = (for_fixture(fixture, head) if fixture is not None
                     else Servo())
            self.servos[key] = servo
        return servo

    def settle(self) -> None:
        """Put every head where it was last told to be.

        What a still wants. A snapshot is asking "what does this look
        LIKE", and photographing four heads a third of the way through a
        travel answers a question nobody asked -- and answers it differently
        depending on how long ago the frame was written.
        """
        for servo in self.servos.values():
            servo.settle()


def for_fixture(fixture, head=None) -> Servo:
    """A servo sized from a patched fixture's profile and rig overrides.

    `head` is that fixture's `geometry.Head` where it has one, and its ranges
    WIN. A profile's declared range can be optimistic -- rig.json already
    overrides it for aiming, and a servo working from the profile's 540 while
    the aim maths works from a measured 500 would convert words to degrees on a
    different scale from everything else.
    """
    profile = getattr(fixture, "profile", None)
    return Servo(
        pan_range_deg=float(getattr(head, "pan_range_deg", None)
                            or getattr(profile, "pan_max_deg", 0) or 540.0),
        tilt_range_deg=float(getattr(head, "tilt_range_deg", None)
                             or getattr(profile, "tilt_max_deg", 0) or 270.0),
        pan_deg_per_s=float(getattr(fixture, "pan_speed_deg_s", None)
                            or DEFAULT_PAN_DEG_PER_S),
        tilt_deg_per_s=float(getattr(fixture, "tilt_speed_deg_s", None)
                             or DEFAULT_TILT_DEG_PER_S))


def cue_margins(geometry, steps, spans, cycle_seconds,
                servo: Optional[Servo] = None) -> list[tuple[float, float]]:
    """Per step: (seconds the chase allows for the move, seconds it needs).

    The question a dark move lives or dies on, and one nothing else in the
    engine can answer. A cued chase states its travel in milliseconds; whether a
    head can cross that much room in that long is a fact about the yoke. Where
    it cannot, the dimmer comes back up on a head still swinging and the
    teleport turns back into the lit sweep it was written to replace.

    It moves with TEMPO, which is the trap: the port puts every routine on a bar
    count, so speeding the show up shortens every travel while the heads stay
    exactly as fast as they were. `cycle_seconds` is therefore the cycle at the
    tempo being asked about, not at the one it was authored at.

    `steps` is [step][head] aim offsets, as the library stores them.
    """
    servo = servo or Servo()
    total = sum(fade + hold for fade, hold in spans)
    out = []
    for index, (fade, hold) in enumerate(spans):
        allowed = fade / total * cycle_seconds if total > 0 else 0.0
        previous, current = steps[index - 1], steps[index]
        needed = 0.0
        for head in range(len(current)):
            a = geometry.encode(head, geometry.aim_offset(head, *previous[head]))
            b = geometry.encode(head, geometry.aim_offset(head, *current[head]))
            needed = max(needed, servo.travel_time(a[0], a[1], b[0], b[1]))
        out.append((allowed, needed))
    return out


# ---------------------------------------------------------------- self-test --

def _self_test() -> None:
    failures = []

    def check(label, ok, detail=""):
        print(f"  {'PASS' if ok else 'FAIL'}  {label}"
              + (f"  -- {detail}" if detail else ""))
        if not ok:
            failures.append(label)

    print("\n1. a head takes time to cross its range")
    s = Servo()
    s.snap(0, 0)
    # Half of pan is 270 deg, which at 216 deg/s is 1.25 s.
    frames = 0
    while not s.arrived(FULL_SCALE // 2, 0) and frames < 1000:
        s.follow(FULL_SCALE // 2, 0, 1 / 40.0)
        frames += 1
    check("half a pan sweep takes about 1.25 s", 1.2 <= frames / 40.0 <= 1.35,
          f"{frames / 40.0:.2f} s over {frames} frames at 40 fps")

    print("\n2. the axes run at once, and the slower one decides")
    s = Servo()
    s.snap(0, 0)
    together = s.travel_time(0, 0, FULL_SCALE // 2, FULL_SCALE // 2)
    check("a diagonal is not two moves in sequence",
          abs(together - max(270 / 216.0, 135 / 180.0)) < 1e-3,
          f"{together:.3f} s = the slower of pan {270 / 216.0:.3f} "
          f"and tilt {135 / 180.0:.3f}")

    print("\n3. it converges exactly, and does not overshoot")
    s = Servo()
    s.snap(FULL_SCALE, FULL_SCALE)
    for _ in range(400):
        s.follow(0, 0, 1 / 40.0)
    check("a long move lands exactly on target", (s.pan, s.tilt) == (0.0, 0.0),
          f"({s.pan}, {s.tilt})")
    s.snap(1000, 1000)
    s.follow(1001, 1001, 1 / 40.0)
    check("a move shorter than one frame's step does not overshoot",
          (s.pan, s.tilt) == (1001.0, 1001.0), f"({s.pan}, {s.tilt})")

    print("\n4. the first frame snaps rather than sweeping in from zero")
    s = Servo()
    check("a fresh servo adopts the first command",
          s.follow(40000, 20000, 1 / 40.0) == (40000.0, 20000.0))

    print("\n5. dt <= 0 holds position, and settling gives up and arrives")
    s = Servo()
    s.snap(0, 0)
    check("a zero-length frame moves nothing", s.follow(FULL_SCALE, 0, 0.0)
          == (0.0, 0.0))
    Rack({"h0": s}).settle()
    check("settling puts it where it was last told to be",
          (s.pan, s.tilt) == (float(FULL_SCALE), 0.0), f"({s.pan}, {s.tilt})")
    check("settling a servo that has heard nothing is harmless",
          Servo().settle() == (0.0, 0.0))

    print("\n6. a real dark move's travel is long enough to arrive")
    # Teleport's widest hop on this rig: the ball to straight up. 800 ms of dark
    # travel is the number the workspace settled on, and the README records that
    # going below it left heads still moving when the dimmer came back.
    s = Servo()
    quarter_pan = FULL_SCALE * (92.8 / 540.0)
    needed = s.travel_time(0, 0, quarter_pan, 0)
    check("800 ms covers a 93 deg hop", needed <= 0.8,
          f"needs {needed * 1000:.0f} ms")

    print()
    if failures:
        print(f"{len(failures)} FAILURE(S)")
        raise SystemExit(1)
    print("servo: all checks pass")


if __name__ == "__main__":
    _self_test()
