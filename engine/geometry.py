"""
Aim geometry for moving heads: where a head is, where it is pointed, and what
DMX makes it point there.

Lifted from `events/despacio/aim_calc.py`, which drove the despacio show. That
module's math is correct and was bought with three production bugs (see the
Correctness note in its README); none of it is retyped from scratch here. Two
things change, both deliberate:

1. **Rig state is a parameter, not a module global.** `aim_calc` loads one
   config at import and exposes HEADS / BALL_X / CALIBRATED_BALL_DMX as module
   constants. The engine has to hold several rigs at once (a room, a rebuild of
   that room after an overnight nudge, a club rig with eight heads), so every
   quantity moves onto `RigGeometry` and `Head`. The self-test at the bottom
   therefore builds rigs instead of mutating globals -- strictly safer, since a
   failed assertion can no longer leave a corrupted global behind.

2. **Encoding is 16-bit.** The MJ-OS-018 declares `PositionPanFine` and
   `PositionTiltFine` (channels 1 and 3) and the show pinned both to zero,
   throwing away eight bits. At 8 bits one step of Pan is 540/256 = 2.1 deg,
   which is plainly visible on a slow move -- exactly the "steppy" complaint.
   The scaling is chosen so the coarse byte of a 16-bit encode equals what
   `aim_calc` would have sent (asserted in the self-test), so this is strictly
   added resolution, not a different aim.

   If a fixture ignores its fine channels, sending them is still correct -- the
   fixture just drops the low byte and lands on the same coarse value. Whether
   the MJ-OS-018 actually honours them is unverified on hardware.

Conventions, all inherited unchanged:

  * World axes are millimetres, **y up**. Bearing 0 deg = +z, 90 deg = +x
    (`bearing_between`), matching `atan2(dx, dz)`.
  * **Elevation is positive = up.** This is the single most bug-prone
    convention in the original file; three separate production bugs came from
    disagreeing about it.
  * An `Aim` is (bearing_delta, elev_deg). `bearing_delta` is the servo's
    rotation from its own mount facing, deliberately **unwrapped** -- it can
    legitimately exceed +-180 deg (up to +-bearing_max/2, e.g. +-270 on a
    540 deg Pan channel). Reducing it modulo 360 lands on a value that is
    congruent but is NOT the same physical servo position, which is a real bug
    this code hit on the bench. Everything anchors to the calibrated ball
    delta and adds a wrapped *correction*; nothing rederives a delta from
    scratch.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional


# --------------------------------------------------------------- resolution --

@dataclass(frozen=True)
class Resolution:
    """How many DMX bits carry one position axis.

    `span` is the largest encodable value; `half` is the centre-anchored home
    value. The two scales differ on purpose, because the original code's two
    anchor conventions differ: centre-anchored divides the range into `half`
    steps each side (so home is 128 of 0..255, one step above true centre),
    while zero-anchored spreads the mechanical range across `span`. Both are
    kept exactly as they were rather than tidied, because "tidier" here means
    "every calibration reading taken at the venue shifts by half a step".
    """
    bits: int
    span: int
    half: int


DMX8 = Resolution(bits=8, span=255, half=128)
DMX16 = Resolution(bits=16, span=65535, half=32768)


def split16(value: int) -> tuple[int, int]:
    """A 16-bit position value as (coarse, fine) DMX bytes."""
    v = max(0, min(65535, int(value)))
    return v >> 8, v & 0xFF


# ------------------------------------------------------------ mount profiles --

# Which physical channel carries bearing vs elevation, and which anchor
# convention elevation uses. Bearing is ALWAYS centre-anchored.
#
# world_flip_elevation: "table" simulates hanging upside-down by flipping the
# whole world, so an elevation computed in real-venue terms ("-27.9 deg = look
# down at the floor") must be NEGATED to know what the bench rig does with it
# ("+27.9 deg = look up at the room's actual ceiling"). This is NOT the same as
# a fixture's pan_invert/tilt_invert, which describe the fixture's own
# DMX-to-rotation wiring -- a hardware fact, independent of which mode is
# active. The two are combined by XOR at encode time (see elevation_invert).
# It applies to elevation only: a purely vertical flip leaves bearing alone.
MOUNT_PROFILES: dict[str, dict] = {
    # Bench-test rig: fixtures sit base-down on a table, simulating a ceiling
    # hang. Pan keeps its normal bearing role, Tilt its normal elevation role
    # (zero-anchored, DMX 0 = local "down").
    "table": {
        "bearing_channel": "pan",
        "elevation_channel": "tilt",
        "elevation_anchor": "zero",
        "world_flip_elevation": True,
    },
    # Real sideways install: the base is tipped onto its side so the shaft is
    # horizontal (so cables exit the top). That swaps the channels' real-world
    # meaning -- Pan's 540 deg range becomes ELEVATION, chosen deliberately for
    # its +-270 deg throw around level, and Tilt's 270 deg becomes BEARING.
    "venue": {
        "bearing_channel": "tilt",
        "elevation_channel": "pan",
        "elevation_anchor": "center",
        "world_flip_elevation": False,
    },
    # An ACTUAL upside-down hang, as opposed to "table" which is the bench
    # SIMULATION of one. Identical channel roles (the fixture is the same way
    # up) but no world flip: at a real hung install "down" is down.
    "hung": {
        "bearing_channel": "pan",
        "elevation_channel": "tilt",
        "elevation_anchor": "zero",
        "world_flip_elevation": False,
    },
}

# Below this many degrees a fitted vertical extreme has stopped being a vertical
# pose at all -- it would collapse onto the ball point. If no value this large
# fits, fit_elev_extreme() gives up and returns what was asked for, so the aim
# clamps at the rail and gets REPORTED by reach_error() rather than silently
# degenerating into a duplicate of the ball pose. This is the "table" bench
# mode's normal state: its calibration is read at Tilt=0, a mechanical stop, so
# that channel has no travel left on one side at all.
ELEV_EXTREME_MIN_USEFUL_DEG = 30.0


# ------------------------------------------------------- pure angle <-> DMX --

def norm180(deg: float) -> float:
    """Reduce to (-180, 180]. Only ever applied to a *correction* between two
    world bearings, never to a servo delta -- see the module docstring."""
    return ((deg + 180) % 360) - 180


def bearing_between(hx: float, hz: float, tx: float, tz: float) -> float:
    """World bearing from (hx,hz) to (tx,tz). 0 deg = +z, 90 deg = +x."""
    return math.degrees(math.atan2(tx - hx, tz - hz))


def to_dmx_centered(delta_deg: float, max_deg: float, invert: bool,
                    res: Resolution = DMX16) -> int:
    """Centre-anchored: home = `res.half`, +-max_deg/2 either side. Used for
    BEARING in every mode, and for ELEVATION in "venue"."""
    if invert:
        delta_deg = -delta_deg
    v = res.half + (delta_deg / (max_deg / 2)) * res.half
    return max(0, min(res.span, round(v)))


def from_dmx_centered(dmx: float, max_deg: float, invert: bool,
                      res: Resolution = DMX16) -> float:
    delta_deg = (dmx - res.half) / res.half * (max_deg / 2)
    return -delta_deg if invert else delta_deg


def to_dmx_zero_elev(el_deg: float, max_deg: float, invert: bool,
                     res: Resolution = DMX16) -> int:
    """Zero-anchored ELEVATION, positive = up: DMX 0 = straight down
    (el = -90 with invert=False), sweeping up through level and beyond across
    the full mechanical range. Only used where Tilt carries elevation.

    The positive=up convention here must match compute-side callers exactly.
    It didn't once (2026-07-22): the formula assumed positive=down while its
    caller fed positive=up, and the floor pose aimed at the ceiling.
    """
    if invert:
        el_deg = -el_deg
    v = (el_deg + 90) / max_deg * res.span
    return max(0, min(res.span, round(v)))


def from_dmx_zero_elev(dmx: float, max_deg: float, invert: bool,
                       res: Resolution = DMX16) -> float:
    el_deg = (dmx / res.span) * max_deg - 90
    return -el_deg if invert else el_deg


def encode_elev(el_deg: float, max_deg: float, invert: bool, anchor: str,
                res: Resolution = DMX16) -> int:
    if anchor == "center":
        return to_dmx_centered(el_deg, max_deg, invert, res)
    return to_dmx_zero_elev(el_deg, max_deg, invert, res)


def decode_elev(dmx: float, max_deg: float, invert: bool, anchor: str,
                res: Resolution = DMX16) -> float:
    if anchor == "center":
        return from_dmx_centered(dmx, max_deg, invert, res)
    return from_dmx_zero_elev(dmx, max_deg, invert, res)


# ------------------------------------------------------------------- model --

@dataclass(frozen=True)
class Aim:
    """Where one head is pointed, in its own servo frame.

    `bearing_delta` is unwrapped rotation from the head's mount facing (see the
    module docstring on why it must not be reduced mod 360). `elev_deg` is
    world elevation, positive = up, BEFORE the head's calibration offset is
    applied -- the offset is added at encode time so that both halves of the
    round-trip use the one shared value.
    """
    bearing_delta: float
    elev_deg: float


@dataclass(frozen=True)
class Head:
    """One moving head: where it is, what it can reach, how it was calibrated."""
    name: str
    x: float
    z: float
    height: float

    pan_range_deg: float = 540.0
    tilt_range_deg: float = 270.0
    beam_angle_deg: float = 3.0          # full cone angle, from the .qxf Lens

    # THE calibration step, per mount mode: aim the head at the ball by hand and
    # record the two 0-255 readings, (pan, tilt). Everything else is derived as
    # a geometric offset from that one verified point. None = fall back to the
    # assumed mounting model, which is markedly less reliable.
    calibrated_ball_dmx: Optional[tuple[int, int]] = None

    # The FIXTURE's own DMX-to-rotation wiring, per channel. A hardware fact,
    # not a property of the mount mode.
    pan_invert: bool = False
    tilt_invert: bool = False

    # Only for a head deliberately left uncalibrated: a guess at its bearing
    # home. Calibrating is easier and more reliable than guessing this.
    mount_facing_override: Optional[float] = None


@dataclass(frozen=True)
class AimFrame:
    """Everything derived once per head from its calibration + the mount mode.

    Both the encoder and every consistency check read this one object, so a
    per-head invert can never desync the two. Before the equivalent helper
    existed in `aim_calc`, the self-test derived elevation direction from
    `world_flip_elevation` alone -- true only while every head shared an
    elevation invert, which broke the day a real mirrored-mount head needed a
    per-head flag (2026-07-29).
    """
    bearing_to_ball: float
    elev_to_ball: float
    mount_facing: float
    bearing_delta_ball: float
    elevation_offset: float
    bearing_invert: bool
    elevation_invert: bool
    bearing_max: float
    elevation_max: float
    elevation_anchor: str
    bearing_channel: str
    elevation_channel: str


@dataclass(frozen=True)
class RigGeometry:
    """A set of heads, a ball, and one mount mode -- enough to aim anything."""
    heads: tuple[Head, ...]
    ball: tuple[float, float, float]      # (x, y, z) mm
    mount_mode: str = "venue"
    elev_extreme_deg: float = 90.0
    resolution: Resolution = DMX16

    _frames: dict = field(default_factory=dict, repr=False, compare=False)

    def __post_init__(self):
        if self.mount_mode not in MOUNT_PROFILES:
            raise ValueError(
                f"unknown mount_mode {self.mount_mode!r} "
                f"(known: {', '.join(MOUNT_PROFILES)})")

    # -- per-head derived frame ------------------------------------------

    def frame(self, i: int) -> AimFrame:
        cached = self._frames.get(i)
        if cached is None:
            cached = self._build_frame(i)
            self._frames[i] = cached
        return cached

    def _build_frame(self, i: int) -> AimFrame:
        head = self.heads[i]
        profile = MOUNT_PROFILES[self.mount_mode]
        bx, by, bz = self.ball

        ranges = {"pan": head.pan_range_deg, "tilt": head.tilt_range_deg}
        inverts = {"pan": head.pan_invert, "tilt": head.tilt_invert}
        bearing_max = ranges[profile["bearing_channel"]]
        elevation_max = ranges[profile["elevation_channel"]]
        bearing_invert = inverts[profile["bearing_channel"]]

        # Effective elevation handedness: the fixture's own invert flag on
        # whichever channel carries elevation, XORed with the mode's world flip.
        elevation_invert = (
            inverts[profile["elevation_channel"]] != profile["world_flip_elevation"])

        horiz = math.hypot(bx - head.x, bz - head.z)
        bearing_to_ball = bearing_between(head.x, head.z, bx, bz)
        elev_to_ball = math.degrees(math.atan2(by - head.height, horiz))

        cal = head.calibrated_ball_dmx
        if cal is not None:
            # Back this head's true mount angle and elevation zero-error out of
            # the one empirically-verified point, rather than assuming it
            # matches the nominal mounting convention.
            cal_vals = {"pan": self._from_reading(cal[0]),
                        "tilt": self._from_reading(cal[1])}
            achieved_bearing_delta = from_dmx_centered(
                cal_vals[profile["bearing_channel"]], bearing_max,
                bearing_invert, self.resolution)
            achieved_elev = decode_elev(
                cal_vals[profile["elevation_channel"]], elevation_max,
                elevation_invert, profile["elevation_anchor"], self.resolution)

            mount_facing = bearing_to_ball - achieved_bearing_delta
            # Use the backed-out delta EXACTLY, never rederive it as
            # (bearing_to_ball - mount_facing). Those are algebraically equal
            # before wrapping, but the rederivation invites a norm180() that
            # would substitute a congruent-but-different servo position -- the
            # large-offset failure the original file documents.
            bearing_delta_ball = achieved_bearing_delta
            elevation_offset = achieved_elev - elev_to_ball
        else:
            mount_facing = (head.mount_facing_override
                            if head.mount_facing_override is not None
                            else bearing_to_ball)
            # No exact delta to preserve -- mount_facing is a raw angle guess,
            # so the shortest real rotation is the only sensible reading.
            bearing_delta_ball = norm180(bearing_to_ball - mount_facing)
            elevation_offset = 0.0

        return AimFrame(
            bearing_to_ball=bearing_to_ball,
            elev_to_ball=elev_to_ball,
            mount_facing=mount_facing,
            bearing_delta_ball=bearing_delta_ball,
            elevation_offset=elevation_offset,
            bearing_invert=bearing_invert,
            elevation_invert=elevation_invert,
            bearing_max=bearing_max,
            elevation_max=elevation_max,
            elevation_anchor=profile["elevation_anchor"],
            bearing_channel=profile["bearing_channel"],
            elevation_channel=profile["elevation_channel"],
        )

    def _from_reading(self, coarse: int) -> int:
        """A hand-recorded 0-255 calibration reading in this rig's resolution.

        Readings are taken off an 8-bit fader, so the fine byte is zero and the
        16-bit value is coarse<<8. Note this is *not* coarse*257, even though
        257 is the ratio that maps 0..255 onto 0..65535: the fixture receives
        (coarse<<8)|fine and nothing else, so coarse<<8 is literally what it
        saw when the head was aimed at the ball by hand. Using the 257 scaling
        would silently move the calibrated ball point by up to 0.4% of the
        channel's range (2.1 deg on a 540 deg Pan) -- small, but it would put
        the engine somewhere the show was never focused.
        """
        if self.resolution is DMX8 or self.resolution.bits == 8:
            return coarse
        return coarse << (self.resolution.bits - 8)

    # -- encode / decode --------------------------------------------------

    def encode(self, i: int, aim: Aim) -> tuple[int, int]:
        """`aim` -> (pan_value, tilt_value) in this rig's resolution.

        Always returns literal Pan/Tilt channel values; which of bearing and
        elevation each carries depends on the mount mode.
        """
        f = self.frame(i)
        res = self.resolution
        bearing_val = to_dmx_centered(aim.bearing_delta, f.bearing_max,
                                      f.bearing_invert, res)
        elev_val = encode_elev(aim.elev_deg + f.elevation_offset, f.elevation_max,
                               f.elevation_invert, f.elevation_anchor, res)
        by_channel = {f.bearing_channel: bearing_val, f.elevation_channel: elev_val}
        return by_channel["pan"], by_channel["tilt"]

    def decode(self, i: int, pan_val: int, tilt_val: int) -> Aim:
        """The inverse of `encode`, to within quantisation."""
        f = self.frame(i)
        res = self.resolution
        by_channel = {"pan": pan_val, "tilt": tilt_val}
        bearing_delta = from_dmx_centered(by_channel[f.bearing_channel],
                                          f.bearing_max, f.bearing_invert, res)
        elev = decode_elev(by_channel[f.elevation_channel], f.elevation_max,
                           f.elevation_invert, f.elevation_anchor, res)
        return Aim(bearing_delta, elev - f.elevation_offset)

    def reach_error(self, i: int, aim: Aim) -> tuple[float, float]:
        """(bearing_error, elevation_error) in degrees between what `aim` asked
        for and what the encoded DMX actually achieves.

        This is a REACHABILITY check, not a correctness check: it answers "does
        this head have enough channel range left to hit that", which is a
        property of the mount mode, the calibration and the room. A pose that
        clamps against a rail is skipped by directional assertions (correctly --
        direction is not recoverable from a saturated value), so without this
        the clamp would leave no record anywhere.
        """
        pan_val, tilt_val = self.encode(i, aim)
        got = self.decode(i, pan_val, tilt_val)
        return (abs(norm180(got.bearing_delta - aim.bearing_delta)),
                abs(got.elev_deg - aim.elev_deg))

    # -- aiming primitives -------------------------------------------------

    def aim_at_ball(self, i: int) -> Aim:
        f = self.frame(i)
        return Aim(f.bearing_delta_ball, f.elev_to_ball)

    def aim_at_point(self, i: int, x: float, y: float, z: float) -> Aim:
        """Aim head `i` at an arbitrary world point.

        The bearing is built as the calibrated ball delta plus a wrapped
        *correction* between two world bearings -- never as a fresh delta.
        Rederiving it is algebraically identical before wrapping, but
        `mount_facing` can sit far outside a normal compass bearing once
        calibrated (e.g. -358 deg), and the modular reduction then lands on a
        congruent value that is not the same servo position. Confirmed on the
        bench: ball correct, floor visibly wrong, built the other way.
        """
        head = self.heads[i]
        f = self.frame(i)
        correction = norm180(bearing_between(head.x, head.z, x, z) - f.bearing_to_ball)
        horiz = math.hypot(x - head.x, z - head.z)
        return Aim(f.bearing_delta_ball + correction,
                   math.degrees(math.atan2(y - head.height, horiz)))

    def aim_offset(self, i: int, d_bearing: float, d_elev: float) -> Aim:
        """Aim relative to this head's own calibrated ball point.

        This is what makes orbits track calibration: recalibrating a head
        re-centres every offset built on it automatically. The absolute-DMX EFX
        these replaced could not, because one global centre cannot serve four
        heads with four different ball points -- the defect that broke 3 of 6
        show looks.
        """
        f = self.frame(i)
        return Aim(f.bearing_delta_ball + d_bearing, f.elev_to_ball + d_elev)

    def half_turn(self, i: int) -> float:
        """A bearing delta facing directly away from the ball, range-fitted.

        +180 and -180 are both legitimate direct half-turns from a known-good
        reference, and either one independently faces the opposite way. They are
        NOT interchangeable with a 360-different value, which is not a rotation
        at all but an alternate encoding of the same direction -- and on this
        hardware not an equivalent one. Pick whichever fits the channel, the
        smaller magnitude if both or neither do.
        """
        f = self.frame(i)
        half_range = f.bearing_max / 2
        candidates = [f.bearing_delta_ball + 180, f.bearing_delta_ball - 180]
        fits = [d for d in candidates if abs(d) <= half_range]
        return min(fits or candidates, key=abs)

    def world_bearing(self, i: int, aim: Aim) -> float:
        """`aim`'s absolute world bearing, undoing the head's mount facing."""
        return self.frame(i).mount_facing + aim.bearing_delta

    def ray(self, i: int, aim: Aim) -> tuple[tuple[float, float, float],
                                             tuple[float, float, float]]:
        """(origin, unit direction) in world mm for the beam's centre line.

        The primitive F4's safety taper casts every frame: because it is
        computed from the *current* aim rather than from a stored scene, it
        covers movement THROUGH a danger band, which a scene-based rig
        structurally cannot.
        """
        head = self.heads[i]
        b = math.radians(self.world_bearing(i, aim))
        e = math.radians(aim.elev_deg)
        return ((head.x, head.height, head.z),
                (math.sin(b) * math.cos(e), math.sin(e), math.cos(b) * math.cos(e)))

    # -- shared vertical extremes ------------------------------------------

    def fit_elev_extreme(self, direction: int, desired_deg: Optional[float] = None) -> float:
        """The largest elevation extreme (degrees off level, magnitude) that
        EVERY head can reach in `direction` (+1 up, -1 down), capped at
        `desired_deg`.

        Zenith and Crowd are unison poses -- all beams vertical together -- so a
        head that runs out of travel and clamps several degrees short while its
        neighbours hit the target exactly is the one thing they must not do.
        Probing downward rather than solving the inequality keeps this correct
        for both anchor conventions without duplicating either one's algebra.

        The two directions are fitted INDEPENDENTLY on purpose: a zero-anchored
        channel calibrated near one of its stops has plenty of travel one way
        and none the other, and a single symmetric value would drag the good
        direction down to the bad one's limit and flatten both.
        """
        desired = self.elev_extreme_deg if desired_deg is None else desired_deg
        sign = 1.0 if direction >= 0 else -1.0
        frames = [self.frame(i) for i in range(len(self.heads))]

        def reaches(extreme: float) -> bool:
            for f in frames:
                target = sign * extreme + f.elevation_offset
                dmx = encode_elev(target, f.elevation_max, f.elevation_invert,
                                  f.elevation_anchor, self.resolution)
                back = decode_elev(dmx, f.elevation_max, f.elevation_invert,
                                   f.elevation_anchor, self.resolution)
                # One step is elevation_max/span deg; allow a step of rounding
                # but nothing resembling a clamp.
                if abs(back - target) > f.elevation_max / self.resolution.span:
                    return False
            return True

        extreme = float(desired)
        while extreme > ELEV_EXTREME_MIN_USEFUL_DEG and not reaches(extreme):
            extreme -= 1.0
        return float(desired) if not reaches(extreme) else extreme

    # -- convenience --------------------------------------------------------

    def with_calibration(self, readings: list[Optional[tuple[int, int]]]) -> "RigGeometry":
        """A copy with new per-head calibration readings. Used by the self-test
        and by F5's re-aim flow, which produces a new calibration rather than
        mutating the running one -- that is what makes an overnight nudge a
        diffable delta instead of a from-scratch re-aim."""
        heads = tuple(
            Head(**{**h.__dict__, "calibrated_ball_dmx": r})
            for h, r in zip(self.heads, readings))
        return RigGeometry(heads=heads, ball=self.ball, mount_mode=self.mount_mode,
                           elev_extreme_deg=self.elev_extreme_deg,
                           resolution=self.resolution)

    def with_mount_mode(self, mode: str) -> "RigGeometry":
        return RigGeometry(heads=self.heads, ball=self.ball, mount_mode=mode,
                           elev_extreme_deg=self.elev_extreme_deg,
                           resolution=self.resolution)

    def with_resolution(self, res: Resolution) -> "RigGeometry":
        return RigGeometry(heads=self.heads, ball=self.ball,
                           mount_mode=self.mount_mode,
                           elev_extreme_deg=self.elev_extreme_deg, resolution=res)


# ------------------------------------------------------------- reference rig --

def despacio_reference_rig(mount_mode: str = "venue",
                           resolution: Resolution = DMX16) -> RigGeometry:
    """The rig the despacio show actually ran on, from
    `events/despacio/despacio_config.json` as of 2026-08-01.

    Hardcoded here rather than read from that file on purpose: this is the
    self-test's fixture, and a regression guard that changes whenever a config
    file is edited guards nothing. Real rigs are loaded by `engine.rig`.
    """
    room = 9144.0
    inset = 500.0
    height = 2971.0
    # ID0 = back-right, ID1 = front-right, ID2 = front-left, ID3 = back-left,
    # in DMX patch order. Confirmed 2026-07-22 against the real patch intent --
    # the earlier assumption had 3 of 4 addresses on the wrong corner, which the
    # uncalibrated ball pose could not reveal (same values sent to every head)
    # but which broke every per-corner pose.
    corners = [(room - inset, room - inset), (room - inset, inset),
               (inset, inset), (inset, room - inset)]
    cal = {
        "table": [(215, 0), (127, 0), (22, 0), (127, 0)],
        # Heads 1 and 3 are mounted MIRRORED from their neighbours. Because Pan
        # carries elevation in "venue", that is a legitimate per-head flip of
        # one head's up/down handedness -- unlike a bearing-channel invert,
        # which must stay uniform or the inter-head poses break.
        "venue": [(47, 69), (126, 69), (42, 73), (133, 68)],
        "hung": [None, None, None, None],
    }
    pan_invert = {"table": [True] * 4, "venue": [False, True, False, True],
                  "hung": [True] * 4}
    tilt_invert = {"table": [False] * 4, "venue": [False] * 4, "hung": [False] * 4}

    heads = tuple(
        Head(name=f"MH{i + 1}", x=x, z=z, height=height,
             calibrated_ball_dmx=cal[mount_mode][i],
             pan_invert=pan_invert[mount_mode][i],
             tilt_invert=tilt_invert[mount_mode][i])
        for i, (x, z) in enumerate(corners))

    return RigGeometry(heads=heads, ball=(room / 2, 2743.0, room / 2),
                       mount_mode=mount_mode, resolution=resolution)


# ------------------------------------------------------------------ self-test --

def _self_test() -> None:
    """Regression guard for every mount mode's math.

    These are `aim_calc._self_test()`'s invariants, re-expressed against the
    aiming primitives rather than against a fixed vocabulary of named poses --
    so they now guard any aim the engine can produce, not just the 40-odd poses
    that happened to exist. Each block notes the production bug it exists for.
    """
    modes = list(MOUNT_PROFILES)

    # -- pure encode/decode ------------------------------------------------

    for res in (DMX8, DMX16):
        for max_deg in (540.0, 270.0):
            for invert in (False, True):
                assert to_dmx_centered(0, max_deg, invert, res) == res.half
                assert abs(from_dmx_centered(res.half, max_deg, invert, res)) < 1e-9

    # Zero-anchored elevation, positive = up. DMX 0 = straight down; level is
    # 85 of 255 for a 270 deg range; and "more up" must give a STRICTLY LARGER
    # value than level. That direction check is the invariant that broke on
    # 2026-07-22, not the specific numbers.
    assert to_dmx_zero_elev(-90, 270.0, False, DMX8) == 0
    assert to_dmx_zero_elev(0, 270.0, False, DMX8) == 85
    assert (to_dmx_zero_elev(45, 270.0, False, DMX8)
            > to_dmx_zero_elev(0, 270.0, False, DMX8)), (
        "elevation direction inverted: 'more up' must yield a larger DMX than level")

    # 16 bits must be a strict REFINEMENT of 8 bits -- the same aim, finer
    # steps -- not a different aim. Asserted in angle space rather than by
    # comparing DMX values, because the two anchors scale differently: a
    # centre-anchored 16-bit value is exactly 256x its 8-bit counterpart, while
    # a zero-anchored one is 257x (span 65535 vs 255). Comparing bytes across
    # that difference tests arithmetic; comparing recovered angles tests the
    # thing that actually matters, and does it uniformly for both anchors.
    for max_deg in (540.0, 270.0):
        step8 = max_deg / 255.0
        step16 = max_deg / 65535.0
        for invert in (False, True):
            for deg in (-120.0, -33.3, 0.0, 1.0, 47.5, 130.0):
                a8 = from_dmx_centered(to_dmx_centered(deg, max_deg, invert, DMX8),
                                       max_deg, invert, DMX8)
                a16 = from_dmx_centered(to_dmx_centered(deg, max_deg, invert, DMX16),
                                        max_deg, invert, DMX16)
                assert abs(a16 - deg) <= step16, (
                    f"16-bit centred encode of {deg} deg recovers {a16:.4f}, "
                    f"further than one 16-bit step ({step16:.4f})")
                assert abs(a16 - a8) <= step8, (
                    f"16-bit centred encode of {deg} deg recovers {a16:.4f} but "
                    f"8-bit recovers {a8:.4f} -- more than one 8-bit step apart, "
                    f"so this is a different aim, not a finer one")
            for el in (-89.0, -45.0, 0.0, 20.0, 88.0):
                a8 = from_dmx_zero_elev(to_dmx_zero_elev(el, max_deg, invert, DMX8),
                                        max_deg, invert, DMX8)
                a16 = from_dmx_zero_elev(to_dmx_zero_elev(el, max_deg, invert, DMX16),
                                         max_deg, invert, DMX16)
                assert abs(a16 - el) <= step16, (
                    f"16-bit zero-anchored encode of {el} deg recovers {a16:.4f}, "
                    f"further than one 16-bit step ({step16:.4f})")
                assert abs(a16 - a8) <= step8, (
                    f"16-bit zero-anchored encode of {el} deg recovers {a16:.4f} "
                    f"but 8-bit recovers {a8:.4f} -- more than one 8-bit step "
                    f"apart, so this is a different aim, not a finer one")

    # -- calibration round-trip -------------------------------------------

    # The ball aim must encode back to EXACTLY the reading it was calibrated
    # from, for every head (not just head 0 -- that catches per-head indexing
    # bugs) and across a spread including both rails and large near-boundary
    # deltas, which is the case that originally broke this.
    test_points = [(140, 60), (30, 200), (128, 128), (0, 0), (255, 255),
                   (255, 0), (0, 255)]
    for mode in modes:
        base = despacio_reference_rig(mode)
        for i in range(len(base.heads)):
            for reading in test_points:
                readings = [h.calibrated_ball_dmx for h in base.heads]
                readings[i] = reading
                rig = base.with_calibration(readings)
                got = rig.encode(i, rig.aim_at_ball(i))
                want = (reading[0] << 8, reading[1] << 8)
                assert got == want, (
                    f"calibration round-trip failed for mode={mode} head={i}: "
                    f"expected {want}, got {got}")

    # -- aim_at_point agrees with the world ---------------------------------

    # An aim built for a point must report that point's own world bearing back.
    # This is the guard on the anchor-plus-correction idiom: a fresh
    # norm180()-reduced delta would pass a same-bearing check but fail here for
    # a head whose mount facing sits outside +-180.
    for mode in modes:
        rig = despacio_reference_rig(mode)
        for i, head in enumerate(rig.heads):
            for (tx, ty, tz) in [(0.0, 0.0, 0.0), (9144.0, 0.0, 0.0),
                                 (4572.0, 4600.0, 4572.0), (1000.0, 1500.0, 8000.0)]:
                aim = rig.aim_at_point(i, tx, ty, tz)
                want = bearing_between(head.x, head.z, tx, tz)
                got = rig.world_bearing(i, aim)
                assert abs(norm180(got - want)) < 1e-9, (
                    f"mode={mode} head={i}: aim at ({tx},{ty},{tz}) reports world "
                    f"bearing {got:.3f} but the point is at {want:.3f}")

    # A point directly under the ball shares the ball's bearing exactly, so its
    # bearing-carrying channel value MUST equal the ball aim's -- precisely the
    # invariant the bench bug broke (ball correct, floor visibly wrong).
    for mode in modes:
        rig = despacio_reference_rig(mode)
        bx, _by, bz = rig.ball
        for i in range(len(rig.heads)):
            f = rig.frame(i)
            bidx = 0 if f.bearing_channel == "pan" else 1
            ball = rig.encode(i, rig.aim_at_ball(i))
            floor = rig.encode(i, rig.aim_at_point(i, bx, 0.0, bz))
            assert ball[bidx] == floor[bidx], (
                f"mode={mode} head={i}: floor bearing channel {floor[bidx]} != "
                f"ball's {ball[bidx]} (same x/z, so these must match exactly)")

    # -- elevation direction (bug #3) --------------------------------------

    # The elevation channel must move the RIGHT WAY. The bearing check above
    # cannot catch this: bug #3 left bearing correct and only broke elevation,
    # collapsing the floor aim onto the ball's tilt. A legitimate far-boundary
    # clip still passes -- it saturates in the correct direction; only a
    # wrong-direction collapse fails.
    #
    # Direction is taken per head from its own effective elevation invert. That
    # replaces a mode-only `world_flip_elevation` assumption which asserted a
    # direction the config could not satisfy once a real mirrored head needed a
    # per-head flag (2026-07-29).
    for mode in modes:
        rig = despacio_reference_rig(mode)
        bx, by, bz = rig.ball
        for i, head in enumerate(rig.heads):
            f = rig.frame(i)
            eidx = 0 if f.elevation_channel == "pan" else 1
            ball_e = rig.encode(i, rig.aim_at_ball(i))[eidx]
            up_reads_bigger = not f.elevation_invert

            below = rig.aim_at_point(i, bx, 0.0, bz)              # the floor
            above = rig.aim_at_point(i, bx, by + 2000.0, bz)      # the canopy
            if abs(below.elev_deg - f.elev_to_ball) > 1e-6:
                e = rig.encode(i, below)[eidx]
                ok = e < ball_e if up_reads_bigger else e > ball_e
                assert ok, (
                    f"mode={mode} head={i}: a point BELOW the ball encodes "
                    f"elevation {e}, not on the expected side of the ball's "
                    f"{ball_e} -- this is the bug #3 regression")
            e = rig.encode(i, above)[eidx]
            if e not in (0, rig.resolution.span) and ball_e not in (0, rig.resolution.span):
                ok = e > ball_e if up_reads_bigger else e < ball_e
                assert ok, (
                    f"mode={mode} head={i}: a point ABOVE the ball encodes "
                    f"elevation {e}, not on the expected side of the ball's "
                    f"{ball_e} -- the aerial aims are pointing down")

    # -- offsets are circles centred on the ball ----------------------------

    # aim_offset must trace a circle around each head's OWN ball point: the top
    # and bottom of the circle straddle it in elevation, and the horizontal
    # extremes land back on its elevation exactly. A sign or index slip breaks
    # one or the other. Stated as relations to the ball so it holds across
    # modes, radii and recalibration.
    for mode in modes:
        rig = despacio_reference_rig(mode)
        for i in range(len(rig.heads)):
            f = rig.frame(i)
            eidx = 0 if f.elevation_channel == "pan" else 1
            ball_e = rig.encode(i, rig.aim_at_ball(i))[eidx]
            up_reads_bigger = not f.elevation_invert
            for radius in (20.0, 45.0):
                steps = 8
                vals = [rig.encode(i, rig.aim_offset(
                    i, radius * math.sin(math.radians(360.0 * k / steps)),
                    radius * math.cos(math.radians(360.0 * k / steps))))[eidx]
                    for k in range(steps)]
                top, bottom = vals[0], vals[steps // 2]
                rail = (0, rig.resolution.span)
                if top in rail or bottom in rail or ball_e in rail:
                    continue   # clamped: direction not recoverable
                hi, lo = (top, bottom) if up_reads_bigger else (bottom, top)
                assert hi > ball_e > lo, (
                    f"mode={mode} head={i}: a {radius:.0f} deg orbit's top/bottom "
                    f"({top}/{bottom}) do not straddle the ball's {ball_e}")
                for side in (steps // 4, 3 * steps // 4):
                    assert vals[side] == ball_e, (
                        f"mode={mode} head={i}: orbit step {side} elevation "
                        f"{vals[side]} != ball's {ball_e} -- the horizontal "
                        f"extremes must sit at the ball's own elevation")

    # -- shared vertical extremes reach the same angle ----------------------

    # Asserted on the DECODED angle relative to each head's own ball point, not
    # on raw DMX: the heads legitimately hold different DMX for the same
    # real-world angle, which is exactly why a DMX comparison would not work.
    for mode in modes:
        rig = despacio_reference_rig(mode)
        for direction, label in ((1, "up"), (-1, "down")):
            fitted = rig.fit_elev_extreme(direction)
            deltas, clamped = [], False
            for i in range(len(rig.heads)):
                f = rig.frame(i)
                eidx = 0 if f.elevation_channel == "pan" else 1
                aim = Aim(f.bearing_delta_ball, direction * fitted)
                val = rig.encode(i, aim)[eidx]
                if val in (0, rig.resolution.span):
                    clamped = True
                deltas.append(rig.decode(i, *rig.encode(i, aim)).elev_deg
                              - rig.decode(i, *rig.encode(i, rig.aim_at_ball(i))).elev_deg)
            # Where the fit had to give up entirely (see
            # ELEV_EXTREME_MIN_USEFUL_DEG -- the "table" bench calibration sits
            # on a mechanical stop) the aims clamp by design and the spread is
            # meaningless; reach_error() reports those instead.
            if fitted >= rig.elev_extreme_deg and clamped:
                continue
            spread = max(deltas) - min(deltas)
            step_deg = rig.frame(0).elevation_max / rig.resolution.span
            assert spread <= 2 * step_deg, (
                f"mode={mode} extreme={label}: heads reach different angles "
                f"({[round(d, 1) for d in deltas]} deg off the ball, spread "
                f"{spread:.2f}) -- the shared extreme is being applied per head, "
                f"or one head is clamping")

    # -- direction-sensitive bearings ---------------------------------------

    # A 0 or 180 deg bearing offset lands on the same servo position whichever
    # way pan turns, so nothing built from those can catch a wrong turn. Aiming
    # at a NEIGHBOUR head can, and did: that aim shipped pointing at the wrong
    # fixture (2026-07-23).
    #
    # What this canNOT catch: it is self-consistent for either pan_invert value,
    # since encode and decode share the sign. It does not verify that the
    # configured invert matches the real hardware rotation -- a physical fact no
    # single ball reading can pin down. This guards the MATH; the config value
    # guards the PHYSICS.
    for mode in modes:
        rig = despacio_reference_rig(mode)
        n = len(rig.heads)
        for i, head in enumerate(rig.heads):
            f = rig.frame(i)
            bidx = 0 if f.bearing_channel == "pan" else 1
            tol = 1.5 * f.bearing_max / rig.resolution.span
            targets = {}
            for label, j in (("next", (i + 1) % n), ("prev", (i - 1) % n)):
                nb = rig.heads[j]
                targets[f"neighbour_{label}"] = (
                    rig.aim_at_point(i, nb.x, nb.height, nb.z),
                    bearing_between(head.x, head.z, nb.x, nb.z))
            for off in (90.0, -90.0, 45.0, -45.0, 20.0, -20.0):
                targets[f"offset_{off:+.0f}"] = (
                    rig.aim_offset(i, off, 0.0), f.bearing_to_ball + off)
            for label, (aim, want) in targets.items():
                val = rig.encode(i, aim)[bidx]
                if val <= 0 or val >= rig.resolution.span:
                    continue   # clamped at a rail: exact geometry not recoverable
                got = f.mount_facing + from_dmx_centered(
                    val, f.bearing_max, f.bearing_invert, rig.resolution)
                assert abs(norm180(got - want)) < tol, (
                    f"mode={mode} head={i} {label}: aims at world bearing "
                    f"{got:.2f} deg but its target is {want:.2f} deg (off by "
                    f"{norm180(got - want):.2f} -- wrong neighbour index or "
                    f"wrong offset sign, NOT a hardware-invert issue)")

    # -- the ray agrees with the aim ----------------------------------------

    # F4's safety taper is only as good as this: the ray must actually point
    # where the aim says, including through the mount mode's channel swap and
    # world flip. Checked by casting at a known point and recovering it.
    for mode in modes:
        rig = despacio_reference_rig(mode)
        for i, head in enumerate(rig.heads):
            target = (1234.0, 900.0, 7777.0)
            aim = rig.aim_at_point(i, *target)
            (ox, oy, oz), (dx, dy, dz) = rig.ray(i, aim)
            dist = math.dist((ox, oy, oz), target)
            hit = (ox + dx * dist, oy + dy * dist, oz + dz * dist)
            assert math.dist(hit, target) < 1e-6, (
                f"mode={mode} head={i}: ray cast {dist:.1f}mm lands at "
                f"{tuple(round(c, 3) for c in hit)}, not the aim point {target}")

    # -- everything stays in range under extreme calibration ----------------

    for mode in modes:
        for reading in [(0, 0), (255, 255), (0, 255), (255, 0)]:
            rig = despacio_reference_rig(mode).with_calibration([reading] * 4)
            bx, by, bz = rig.ball
            for i in range(len(rig.heads)):
                aims = [rig.aim_at_ball(i), rig.aim_at_point(i, bx, 0.0, bz),
                        rig.aim_offset(i, 45.0, 30.0), Aim(rig.half_turn(i), 0.0)]
                for aim in aims:
                    pan, tilt = rig.encode(i, aim)
                    assert 0 <= pan <= rig.resolution.span, (
                        f"mode={mode} head={i} pan {pan} out of range under "
                        f"calibration {reading}")
                    assert 0 <= tilt <= rig.resolution.span, (
                        f"mode={mode} head={i} tilt {tilt} out of range under "
                        f"calibration {reading}")


if __name__ == "__main__":
    _self_test()
    print("geometry self-test: PASS")

    rig = despacio_reference_rig("venue")
    print(f"\nreference rig: {len(rig.heads)} heads, mode={rig.mount_mode}, "
          f"{rig.resolution.bits}-bit")
    print(f"  fitted extremes: up {rig.fit_elev_extreme(1):.0f} deg, "
          f"down {rig.fit_elev_extreme(-1):.0f} deg")
    for i, head in enumerate(rig.heads):
        f = rig.frame(i)
        pan, tilt = rig.encode(i, rig.aim_at_ball(i))
        print(f"  {head.name}: facing {f.mount_facing:7.1f} deg, "
              f"ball delta {f.bearing_delta_ball:7.1f} deg, "
              f"elev offset {f.elevation_offset:6.1f} deg, "
              f"ball -> pan {split16(pan)} tilt {split16(tilt)}")
