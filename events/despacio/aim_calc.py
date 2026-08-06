"""
Corner-rig aim calculator for despacio.qxw.

The 4 moving heads sit in the 4 corners of the room and are aimed at shared
targets (the disco ball, the dancefloor center, or outward toward the crowd).

TWO SWITCHABLE MOUNTING MODES (split 2026-07-22) -- set MOUNT_MODE below:

  "table" -- bench-test rig: fixtures sit BASE-DOWN on a table/desk, feet on
  the surface, in a square arrangement. This *simulates* hanging upside-down
  from a ceiling: flip the whole world upside-down, and a fixture hanging
  base-up-at-the-ceiling with its head dangling below becomes -- in the
  flipped frame -- a fixture sitting base-down on a surface with its head
  rising up from it. Same mechanical relationship, easier to bench-test.
  Pan keeps its normal role (base spinning around a vertical-ish shaft ->
  BEARING, center-anchored, confirmed hardware to be a bounded ~540 deg
  channel) and Tilt keeps its normal role (head pivoting on the yoke hinge ->
  ELEVATION, zero-anchored: DMX 0 = local "down"). Pan defaults INVERTED
  (`PAN_INVERT["table"]` = True per head) -- that hardware inversion is what
  handles the bearing half of the "simulate upside-down while sitting
  right-side-up" trick. Tilt does NOT default inverted
  (`TILT_INVERT["table"]` = False, confirmed on hardware 2026-07-22): the
  ELEVATION half of the upside-down simulation is carried separately by
  `world_flip_elevation` in MOUNT_PROFILES, not by inverting the Tilt channel
  (see bug #3 in the README, and the MOUNT_PROFILES comment below, for why
  these are distinct).

  "venue" -- real final installation: the base is tipped onto its side so
  the shaft is HORIZONTAL (specifically so cables exit the top). That swaps
  which real-world quantity each channel controls:
    * Pan (540 deg range) now controls ELEVATION (vertical arc) -- chosen
      deliberately for its huge +-270 deg throw around level, so beams can
      swing well above *and* well below the heads' mounting plane.
    * Tilt (270 deg range) now controls BEARING (horizontal arc) -- +-135
      deg around center, comfortably covers this room's +-45/+-135 deg
      corner-to-ball bearings.
  Both channels center-anchored (confirmed on hardware for Pan; ASSUMED,
  unverified, for Tilt's new bearing role). PAN_INVERT/TILT_INVERT default
  False per head here, but confirmed 2026-07-29: a MIRRORED sideways mount
  (a head tipped the opposite way from its neighbors) DOES invert that head's
  elevation, per-head, via PAN_INVERT -- since Pan carries elevation in this
  mode, not bearing, that's a legitimate per-head difference, unlike Circle's
  uniform-bearing-invert requirement (see the Circle/Neighbor Scan note in
  README.md's "Pan direction / inter-head geometry" section -- in "venue"
  that's a TILT_INVERT uniformity requirement, not PAN_INVERT's).

SHARED, mode-independent: room geometry (ROOM_WIDTH/DEPTH, HEAD_INSET,
HEADS positions), mounting heights (HEAD_HEIGHT/BALL_HEIGHT/BALL_X/BALL_Z),
and the actual bearing/elevation trig (bearing_to_ball, elev_to_ball_geom,
etc.) -- these describe WHERE the ball/floor/crowd targets are relative to
each head, which has nothing to do with which channel encodes what. A fix to
this shared geometry/trig automatically applies to both modes. Only the
final encode/decode step (which DMX channel gets which value, and which
anchor convention) is mode-specific, driven by MOUNT_PROFILES below.

CALIBRATED_BALL_DMX is per-mode (a "table" reading and a "venue" reading are
NOT interchangeable -- they calibrate two physically different transforms).
For each head, visually aim it at the ball with its Pan knob and Tilt knob
and enter the two 0-255 values shown, in that order: (pan_reading,
tilt_reading), into despacio_config.json's calibrated_ball_dmx[MOUNT_MODE]
(directly, or via aim_calc_gui.py). The script backs out that head's true
mount angle + elevation zero-error from the one verified point, then
computes Floor/Crowd/Diagonal/Chase as geometric offsets from it. Leave a
head as null to fall back to the assumed model.

Running `python aim_calc.py` writes whichever MOUNT_MODE is currently active
into despacio.qxw -- it's a build-time selector, not a live in-show toggle.
Switch mount_mode in despacio_config.json and rerun to switch which target
the workspace reflects.

HEAD_HEIGHT (per head) / BALL_HEIGHT still set how far everything tilts
toward the floor vs. the ball -- edit despacio_config.json and rerun
`python aim_calc.py` after measuring on site. HEAD_HEIGHT defaults to the
same 10ft for all 4 heads, but is a per-head list: if the real truss/ceiling
attachment points end up at slightly different heights, set each head's
entry independently.

All of the site-measured values above (mount_mode, room/head/ball geometry,
calibration DMX readings, mount facing overrides, invert flags) live in
despacio_config.json next to this script, not as literals in this file --
edit that JSON directly, or use the aim_calc_gui.py desktop app, then rerun
this script (the GUI does that for you).
"""
import json
import math
import re
import shutil
from datetime import datetime
from pathlib import Path

QXW = r"despacio.qxw"
CONFIG_PATH = Path(__file__).with_name("despacio_config.json")
AIM_REPORT_PATH = Path(__file__).with_name("aim_report.json")

# Fallback used only if despacio_config.json is missing (also written out as
# that file's initial content in that case). Mirrors the 2026-07-22 bench
# test config -- see module docstring for what each field means.
DEFAULT_CONFIG = {
    "mount_mode": "table",
    "room_width": 9144,
    "room_depth": 9144,
    "head_inset": 500,
    "head_height": [3048, 3048, 3048, 3048],
    "ball_height": 3048,
    "ball_x": 4572,
    "ball_z": 4572,
    "apex_height": 4600,
    "floor_sweep_radius": 2500,
    "canopy_sweep_radius": 2000,
    # CEILING on how far Zenith/Crowd reach from level, in degrees. 90 is true
    # vertical and is what you want whenever the rig can manage it;
    # _fit_elev_extreme() automatically backs this off to whatever the
    # tightest-mounted head can actually reach, so it needs no venue-day
    # tuning. Lower it only to deliberately keep the vertical poses shallower
    # than the hardware allows.
    "elev_extreme_deg": 90,
    "calibrated_ball_dmx": {
        "table": [[22, 0], [127, 0], [22, 0], [127, 0]],
        "venue": [None, None, None, None],
        "hung": [None, None, None, None],
    },
    "mount_facing_override": [None, None, None, None],
    "pan_invert": {
        "table": [True, True, True, True],
        "venue": [False, False, False, False],
        # "hung" inherits "table"'s bearing inversion: the physical fixture is
        # the same way up in both (that IS what "table" simulates), so the Pan
        # channel's handedness is the same. Confirm per head via Corner Test.
        "hung": [True, True, True, True],
    },
    "tilt_invert": {
        # NOT inverted in "table" mode: confirmed on hardware 2026-07-22
        # (turning the Tilt knob up from the calibrated point moves the beam
        # up). The "simulate upside-down" flip of the elevation axis is carried
        # by MOUNT_PROFILES["table"]["world_flip_elevation"], NOT by this flag
        # -- see the PAN_INVERT/TILT_INVERT comment and MOUNT_PROFILES below.
        "table": [False, False, False, False],
        "venue": [False, False, False, False],
        "hung": [False, False, False, False],
    },
}


def load_config(path=CONFIG_PATH):
    """Loads despacio_config.json, merging over DEFAULT_CONFIG so a config file
    missing newer keys doesn't break. Creates the file from DEFAULT_CONFIG if
    it doesn't exist yet.

    The merge goes TWO levels deep for dict-valued keys. The per-mode settings
    (calibrated_ball_dmx / pan_invert / tilt_invert) are keyed by mount mode,
    so a shallow update() would let an on-disk copy written before a new mode
    existed silently drop that mode's defaults -- and the failure would surface
    as a KeyError only once someone switched to that mode, i.e. at the venue."""
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
        return json.loads(json.dumps(DEFAULT_CONFIG))
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    for key, value in on_disk.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def _as_tuple_or_none(v):
    return None if v is None else tuple(v)


# ---------------------------------------------------------------- CONFIG ---
# All site-measured values below come from despacio_config.json -- see the
# module docstring and README for what each one means and how to edit it
# (directly, or via aim_calc_gui.py).
_cfg = load_config()

MOUNT_MODE = _cfg["mount_mode"]  # "table" (bench test) or "venue" (real sideways install)

# Room geometry (mm). Fixed once the 4 corner mounting points are chosen.
# Shared by both modes -- see module docstring for why this is safe (bearing
# is scale/orientation-invariant; elevation only depends on the head/ball
# height difference, which is the same assumed-equal relationship in both
# modes' default config).
ROOM_WIDTH = _cfg["room_width"]   # 30 ft square
ROOM_DEPTH = _cfg["room_depth"]
HEAD_INSET = _cfg["head_inset"]  # how far each head sits in from its literal wall corner

# Per-head mounting height (mm) -- default assumes all 4 truss/ceiling points
# are the same 10ft, but real mounting points are rarely perfectly even, so
# each head can be set independently once measured on site.
HEAD_HEIGHT = _cfg["head_height"]  # ID0-3 = back-right/front-right/front-left/back-left
BALL_HEIGHT = _cfg["ball_height"]   # 10 ft -- disco ball height (mm); one ball, one height
BALL_X = _cfg["ball_x"]   # ball's horizontal position; default = room center
BALL_Z = _cfg["ball_z"]

# Height (mm) of the point the aerial "Apex" pose converges on, directly above
# the ball -- all 4 beams meet there, making a teepee of light over the floor.
# SET THIS AT THE VENUE: the parachute canopy's height if a canopy goes up
# (beams land ON it and it glows), otherwise the ceiling height. It is the one
# geometry value the mounting/rigging decision actually changes.
APEX_HEIGHT = _cfg["apex_height"]

# Radius (mm) of the floor-sweep patterns around room center. The pools stay
# well inside a 30 ft room at 2500; raise it to throw them nearer the walls,
# lower it to keep the action tight under the ball.
FLOOR_SWEEP_RADIUS = _cfg["floor_sweep_radius"]

# Near/far radius fractions of FLOOR_SWEEP_RADIUS for the Floor Breathe
# pattern (a pool that grows and shrinks in place rather than travelling) --
# see compute_poses()'s floor_breathe_near/far. Not its own config key: it's
# a stylistic multiple of the one radius everything else here already shares,
# not an independent site measurement.
FLOOR_BREATHE_NEAR_FRAC = 0.4
FLOOR_BREATHE_FAR_FRAC = 1.6

# Radius (mm) of the canopy-sweep ring, measured on the canopy/ceiling plane at
# APEX_HEIGHT rather than on the floor -- the aerial mirror of
# FLOOR_SWEEP_RADIUS. Smaller than the floor radius by default because the
# canopy is closer to the heads than the floor is, so the same radius would ask
# for a much steeper swing.
CANOPY_SWEEP_RADIUS = _cfg["canopy_sweep_radius"]

# Ceiling on how far Zenith (up) and Crowd (down) reach from level, in degrees.
# The value actually used is _fit_elev_extreme()'s, which reduces this to what
# every head can reach under the current calibration.
ELEV_EXTREME_DEG = _cfg["elev_extreme_deg"]

# THE main on-site/bench calibration step, PER MODE. For each head, visually
# aim it at the ball with its Pan knob and Tilt knob and enter the two 0-255
# values shown, in that order: (pan_reading, tilt_reading). Leave a head as
# None until you've calibrated it in THAT mode's actual orientation.
CALIBRATED_BALL_DMX = {
    mode: [_as_tuple_or_none(v) for v in heads]
    for mode, heads in _cfg["calibrated_ball_dmx"].items()
}

# Only needed for a head you deliberately DON'T calibrate (rare -- calibrating
# is easier and more reliable than guessing these). Shared across modes --
# it's a fallback bearing-home guess, not an orientation-specific quantity.
MOUNT_FACING_OVERRIDE = _cfg["mount_facing_override"]  # degrees, None = auto (bearing to ball)

# Per-mode, per-head invert flags. These describe the FIXTURE's own DMX-to-
# rotation wiring (a hardware fact), NOT the world-flip simulation -- the
# "simulate upside-down" flip of the ELEVATION axis lives in
# MOUNT_PROFILES[...]["world_flip_elevation"], and is combined with these
# flags via XOR at encode time (see compute_poses).
#   "table": Pan defaults True (inverts the BEARING channel, the horizontal
#     half of the upside-down trick); Tilt defaults False -- confirmed on
#     hardware 2026-07-22 that Tilt is NOT inverted, and the elevation half of
#     the flip is carried by world_flip_elevation instead (README bug #3).
#   "venue": both default False, but a per-head PAN_INVERT is legitimate (and
#     confirmed needed, 2026-07-29: MH1/MH4 mounted mirrored) here -- Pan
#     carries ELEVATION in this mode, so a per-head flip just corrects that
#     one head's up/down handedness, unlike a bearing-channel invert (see
#     below). Confirm/flip per head via Corner Test once installed.
#
# IMPORTANT: "flip must stay uniform across all 4 heads or Circle breaks"
# (README's "Pan direction / inter-head geometry") applies to the BEARING
# channel specifically, NOT always PAN_INVERT -- it's PAN_INVERT in
# "table"/"hung" (Pan carries bearing there) but TILT_INVERT in "venue" (Tilt
# carries bearing there instead). The OTHER channel (elevation) is fine, even
# expected, to differ per head. preflight.py warns if the active mode's
# bearing-channel invert list is mixed.
PAN_INVERT = _cfg["pan_invert"]
TILT_INVERT = _cfg["tilt_invert"]

PAN_MAX = 540.0   # from the .qxf Physical/Focus -- Pan channel's mechanical range
TILT_MAX = 270.0  # Tilt channel's mechanical range

WAVE_ELEV_SWING = 20.0  # degrees above/below the ball's elevation for Ball Wave

# Radii (degrees) of the two circular orbits traced around the ball by Lazy
# Circle and Grand Sweep -- see compute_poses()'s ring_small_*/ring_big_*.
# Both routines used to be absolute-DMX EFX centred on channel 127, which
# cannot work on a rig whose heads have different calibrated ball points (a
# QLC+ EFX has one global centre and no per-fixture offset), so they are now
# calibration-derived chasers walking these rings instead. Small is the ambient
# drift; big reaches the wall-graze bearings at its extremes.
LAZY_ORBIT_RADIUS_DEG = 20.0
GRAND_ORBIT_RADIUS_DEG = 45.0
BALL_ORBIT_STEPS = 8  # points per revolution; also the chasers' step count

# Which physical channel carries bearing vs elevation, and which anchor
# convention applies to elevation, per mode. Bearing is ALWAYS center-anchored
# in both modes (confirmed on hardware for the Pan channel in "table" mode;
# assumed for whichever channel carries bearing in either mode otherwise).
#
# world_flip_elevation: "table" mode simulates hanging upside-down by
# flipping the whole world (see module docstring) -- under that flip, up and
# down literally swap meaning, so a geometric elevation target computed in
# real-venue terms (e.g. "-27.9 deg = look down at the real floor") must be
# NEGATED to know what it looks like on the table rig ("+27.9 deg = look up
# at the room's actual ceiling" -- confirmed 2026-07-22, "floor is the
# ceiling" for this test). This is DIFFERENT from PAN_INVERT/TILT_INVERT
# (which describe the FIXTURE's own DMX-to-rotation wiring, a hardware fact
# independent of which mode is active) -- it's a property of the SIMULATION
# itself. It only applies to elevation: bearing (the room's X/Z, horizontal
# plane) is unaffected by a purely vertical flip -- the ball is still "at
# room center" bearing-wise either way. "venue" is the real installation, no
# simulation trick, so this is False there.
MOUNT_PROFILES = {
    "table": {
        "bearing_channel": "pan", "bearing_max": PAN_MAX,
        "elevation_channel": "tilt", "elevation_max": TILT_MAX,
        "elevation_anchor": "zero",    # DMX 0 = local "down"
        "world_flip_elevation": True,
    },
    "venue": {
        "bearing_channel": "tilt", "bearing_max": TILT_MAX,
        "elevation_channel": "pan", "elevation_max": PAN_MAX,
        "elevation_anchor": "center",  # DMX 128 = level
        "world_flip_elevation": False,
    },
    # "hung" -- an ACTUAL upside-down hang at the venue, as opposed to "table"
    # which is the bench SIMULATION of one. Identical channel roles and anchor
    # (the fixture is the same way up), but no world flip: the simulation trick
    # only exists because the bench rig sits base-down on a desk with the room
    # conceptually flipped around it. At a real hung install there is no
    # flipped frame -- "down" is down -- so elevation targets are used as
    # computed. Everything else (geometry, trig, calibration back-solving) is
    # shared, so this profile costs one dict entry and is covered by
    # _self_test()'s existing per-mode loops automatically.
    "hung": {
        "bearing_channel": "pan", "bearing_max": PAN_MAX,
        "elevation_channel": "tilt", "elevation_max": TILT_MAX,
        "elevation_anchor": "zero",    # DMX 0 = straight down
        "world_flip_elevation": False,
    },
}

# Head corner positions, indexed by fixture ID (= DMX patch order: ID0=addr1,
# ID1=addr12, ID2=addr23, ID3=addr34). Confirmed 2026-07-22 against the real
# patch intent: ID0=back-right, ID1=front-right, ID2=front-left, ID3=back-left
# (NOT the earlier front-left/front-right/back-left/back-right-in-ID-order
# assumption -- that had 3 of 4 addresses on the wrong corner, which didn't
# affect the uncalibrated Ball pose (same pan/tilt sent to every ID) but would
# have broken Floor/Crowd/Diagonal/Corner-Chase, which are computed per-corner).
HEADS = [
    (ROOM_WIDTH - HEAD_INSET, ROOM_DEPTH - HEAD_INSET),  # ID0 (addr 1)  = back-right
    (ROOM_WIDTH - HEAD_INSET, HEAD_INSET),                # ID1 (addr 12) = front-right
    (HEAD_INSET, HEAD_INSET),                             # ID2 (addr 23) = front-left
    (HEAD_INSET, ROOM_DEPTH - HEAD_INSET),                # ID3 (addr 34) = back-left
]

FLOOR_X, FLOOR_Z = ROOM_WIDTH / 2, ROOM_DEPTH / 2


def _norm(deg):
    return ((deg + 180) % 360) - 180


def _bearing(hx, hz, tx, tz):
    return math.degrees(math.atan2(tx - hx, tz - hz))


def _to_dmx_centered(delta_deg, max_deg, invert):
    """Center-anchored: DMX 128 = home, +-max_deg/2 either side. Used for
    BEARING in both modes, and for ELEVATION in "venue" mode."""
    if invert:
        delta_deg = -delta_deg
    v = 128 + (delta_deg / (max_deg / 2)) * 128
    return max(0, min(255, round(v)))


def _from_dmx_centered(dmx, max_deg, invert):
    delta_deg = (dmx - 128) / 128 * (max_deg / 2)
    if invert:
        delta_deg = -delta_deg
    return delta_deg


def _to_dmx_zero_elev(el_deg, max_deg, invert):
    """Zero-anchored ELEVATION only, positive=up (matching the rest of this
    module, incl. compute_poses()'s stated convention): DMX 0 = straight down
    (el=-90 with invert=False), sweeping up through level (el=0) and beyond
    across the full max_deg mechanical range. Only ever used for "table"
    mode's Tilt-as-elevation role."""
    if invert:
        el_deg = -el_deg
    v = (el_deg + 90) / max_deg * 255
    return max(0, min(255, round(v)))


def _from_dmx_zero_elev(dmx, max_deg, invert):
    el_deg = (dmx / 255) * max_deg - 90
    if invert:
        el_deg = -el_deg
    return el_deg


def _encode_elev(el_deg, max_deg, invert, anchor):
    if anchor == "center":
        return _to_dmx_centered(el_deg, max_deg, invert)
    return _to_dmx_zero_elev(el_deg, max_deg, invert)


def _decode_elev(dmx, max_deg, invert, anchor):
    if anchor == "center":
        return _from_dmx_centered(dmx, max_deg, invert)
    return _from_dmx_zero_elev(dmx, max_deg, invert)


def _elevation_invert(mode, head_i):
    """The single source of truth for a head's EFFECTIVE elevation handedness in
    `mode`: the FIXTURE's own hardware invert flag on whichever channel carries
    elevation there (PAN_INVERT/TILT_INVERT, a per-head hardware fact), XORed
    with that mode's `world_flip_elevation` (a property of the table-mode
    upside-down SIMULATION, not the hardware -- see MOUNT_PROFILES comment).

    Both compute_poses() (to encode every pose) and _self_test() (to predict
    which way the DMX must move) MUST call this instead of re-deriving it, so a
    per-head invert can never desync the two. Before this helper existed,
    _self_test() assumed direction from world_flip_elevation alone -- true only
    when every head shares the same elevation invert, which broke the instant a
    real mirrored-mount head (e.g. a sideways "venue" head tipped the opposite
    way from its neighbors) needed PAN_INVERT set per-head (2026-07-29)."""
    profile = MOUNT_PROFILES[mode]
    inv = {"pan": PAN_INVERT[mode][head_i], "tilt": TILT_INVERT[mode][head_i]}
    return inv[profile["elevation_channel"]] != profile["world_flip_elevation"]


def _elevation_frame(mode, head_i):
    """(elev_to_ball_geom, elevation_offset, elevation_invert) for one head.

    Extracted from compute_poses() so `_fit_elev_extreme()` below can work out
    how much elevation travel each head actually has WITHOUT calling
    compute_poses() (which would need the answer first -- a cycle). Both callers
    share this one implementation deliberately: a second copy of the
    "decode the calibration reading, subtract the geometric elevation" algebra
    is exactly the kind of duplicate that drifts silently, and the elevation
    frame is the single most bug-prone quantity in this file (three separate
    production bugs, see README's Correctness note)."""
    profile = MOUNT_PROFILES[mode]
    hx, hz = HEADS[head_i]
    head_height = HEAD_HEIGHT[head_i]
    horiz = math.hypot(BALL_X - hx, BALL_Z - hz)
    elev_to_ball_geom = math.degrees(math.atan2(BALL_HEIGHT - head_height, horiz))
    elevation_invert = _elevation_invert(mode, head_i)

    cal = CALIBRATED_BALL_DMX[mode][head_i]
    if cal is None:
        return elev_to_ball_geom, 0.0, elevation_invert
    cal_dmx = {"pan": cal[0], "tilt": cal[1]}[profile["elevation_channel"]]
    achieved_elev = _decode_elev(cal_dmx, profile["elevation_max"],
                                 elevation_invert, profile["elevation_anchor"])
    return elev_to_ball_geom, achieved_elev - elev_to_ball_geom, elevation_invert


# Below this many degrees a fitted vertical extreme has stopped being a
# vertical pose at all (it would collapse onto Ball). If no value this large
# fits, _fit_elev_extreme() gives up and returns the requested extreme so the
# pose clamps at the rail and gets REPORTED by unreachable_poses(), rather than
# silently degenerating into a duplicate of Ball. This is the "table" bench
# mode's normal state: its calibration reading is taken at Tilt=0, a mechanical
# stop, so that channel has no travel left on one side at all (the documented
# zero-anchor limitation, not a bug).
ELEV_EXTREME_MIN_USEFUL_DEG = 30.0


def _fit_elev_extreme(mode, direction, desired_deg=None):
    """The largest elevation extreme (degrees off level, magnitude) that EVERY
    head in `mode` can reach in `direction` (+1 up / -1 down), capped at
    `desired_deg`.

    Zenith (+) and Crowd (-) are the show's two vertical extremes. Asking for a
    literal 90 deg makes them a lottery: a head whose calibrated ball point sits
    far from its elevation channel's centre runs out of travel first and clamps,
    so it ends up several degrees shy of vertical while its neighbours hit the
    target exactly -- four heads visibly not doing the same thing, which is the
    one thing these unison poses must not do.

    Rather than a magic constant tuned to whatever calibration happened to be in
    the config that day (worthless the moment real venue readings land), this
    probes downward from `desired_deg` and returns the first value that encodes
    round-trip-clean for ALL heads. Probing instead of solving the inequality
    keeps it correct for both elevation anchor conventions (centre-anchored in
    "venue", zero-anchored in "table"/"hung") without duplicating either one's
    algebra.

    The two directions are fitted INDEPENDENTLY on purpose. A zero-anchored
    channel calibrated near one of its stops has plenty of travel one way and
    none the other; a single shared symmetric value would drag the good
    direction down to the bad one's limit and flatten both poses."""
    profile = MOUNT_PROFILES[mode]
    desired = ELEV_EXTREME_DEG if desired_deg is None else desired_deg
    sign = 1.0 if direction >= 0 else -1.0
    frames = [_elevation_frame(mode, i) for i in range(4)]

    def reaches(extreme):
        for _elev_ball, offset, invert in frames:
            target = sign * extreme + offset
            dmx = _encode_elev(target, profile["elevation_max"], invert,
                               profile["elevation_anchor"])
            back = _decode_elev(dmx, profile["elevation_max"], invert,
                                profile["elevation_anchor"])
            # One DMX step is elevation_max/255 deg; allow a step of rounding
            # but nothing like a clamp.
            if abs(back - target) > profile["elevation_max"] / 255.0:
                return False
        return True

    extreme = float(desired)
    while extreme > ELEV_EXTREME_MIN_USEFUL_DEG and not reaches(extreme):
        extreme -= 1.0
    return float(desired) if not reaches(extreme) else extreme


def compute_poses(mode=None):
    """Returns {head_index: {'ball':(pan_dmx,tilt_dmx), 'floor':(...), 'crowd':(...)}}.
    `pan_dmx`/`tilt_dmx` always refer to the literal Pan/Tilt DMX channel slots
    (0 and 2); WHICH of bearing/elevation each one carries depends on `mode`
    (defaults to the module-level MOUNT_MODE) via MOUNT_PROFILES. Elevation
    convention: positive = up."""
    mode = mode or MOUNT_MODE
    profile = MOUNT_PROFILES[mode]
    pan_invert_list = PAN_INVERT[mode]
    tilt_invert_list = TILT_INVERT[mode]
    cal_list = CALIBRATED_BALL_DMX[mode]
    # One shared vertical extreme per direction for all 4 heads, fitted to the
    # tightest head's actual remaining travel -- see _fit_elev_extreme().
    # Computed once per call, outside the loop, precisely because these must NOT
    # vary per head.
    elev_extreme_up = _fit_elev_extreme(mode, +1)
    elev_extreme_down = _fit_elev_extreme(mode, -1)

    poses = {}
    for i, (hx, hz) in enumerate(HEADS):
        head_height = HEAD_HEIGHT[i]
        horiz = math.hypot(BALL_X - hx, BALL_Z - hz)  # same for ball/floor targets (same X,Z)
        bearing_to_ball = _bearing(hx, hz, BALL_X, BALL_Z)
        elev_to_ball_geom = math.degrees(math.atan2(BALL_HEIGHT - head_height, horiz))

        invert_for_channel = {"pan": pan_invert_list[i], "tilt": tilt_invert_list[i]}
        bearing_invert = invert_for_channel[profile["bearing_channel"]]
        # XOR the hardware invert flag with the mode's world-flip: this
        # single combined flag is used for BOTH decoding the calibration
        # reading and encoding every pose's elevation, so the two stay in the
        # same frame automatically (see MOUNT_PROFILES comment for why this
        # is needed only for elevation, not bearing). Computed by
        # _elevation_invert() -- the same helper _self_test() calls to predict
        # direction, so the two can never disagree.
        # Elevation frame (geometric elevation to the ball, this head's
        # calibration offset, and its effective invert) comes from the shared
        # _elevation_frame() helper so that _fit_elev_extreme() above and this
        # loop can never disagree about how much travel a head has.
        _elev_geom_check, elevation_offset, elevation_invert = _elevation_frame(mode, i)
        assert abs(_elev_geom_check - elev_to_ball_geom) < 1e-9

        cal = cal_list[i]
        if cal is not None:
            # Back out this head's true mount angle from the one
            # empirically-verified "aimed at the ball" point, instead of
            # assuming it matches the assumed mounting convention. (The
            # elevation half of the same back-out is _elevation_frame()'s
            # `elevation_offset`, above.)
            cal_pan_dmx, cal_tilt_dmx = cal
            dmx_for_channel = {"pan": cal_pan_dmx, "tilt": cal_tilt_dmx}
            achieved_bearing_delta = _from_dmx_centered(
                dmx_for_channel[profile["bearing_channel"]], profile["bearing_max"], bearing_invert)
            mount_facing = bearing_to_ball - achieved_bearing_delta
            # Ball's bearing delta MUST be used exactly as backed out above,
            # not rederived via (bearing_to_ball - mount_facing). Algebraically
            # that recomputation is identical to achieved_bearing_delta before
            # any wrapping -- but achieved_bearing_delta can legitimately
            # exceed +-180 deg (up to +-bearing_max/2, e.g. +-270 on the
            # 540-range Pan channel), and _norm() would silently substitute a
            # DIFFERENT, wrapped-into-180 delta, corrupting the calibration
            # round-trip. (Found via _self_test()'s large-offset stress case.)
            bearing_delta_ball = achieved_bearing_delta
        else:
            mount_facing = MOUNT_FACING_OVERRIDE[i]
            if mount_facing is None:
                mount_facing = bearing_to_ball
            # elevation_offset is already 0.0 from _elevation_frame() for an
            # uncalibrated head -- nothing to back out.
            # No exact delta to preserve here -- mount_facing is just a raw
            # angle guess, so reducing to the shortest real-world rotation is
            # the sensible (and only sensible) choice.
            bearing_delta_ball = _norm(bearing_to_ball - mount_facing)

        # Requested-vs-achieved log for every named pose this head encodes,
        # keyed by the same pose name used in `poses[i]` -- lets
        # unreachable_poses() report which poses got clamped/rounded away
        # from their geometric target and by how much, instead of that
        # information silently vanishing into a clamped DMX byte (see
        # _self_test()'s "rail-clamped, direction not recoverable" skips,
        # which are correct for a PASS/FAIL assertion but leave no record).
        reach = {}

        def _encode(bearing_delta, elev_target, name=None):
            bearing_dmx = _to_dmx_centered(bearing_delta, profile["bearing_max"], bearing_invert)
            elev_dmx = _encode_elev(elev_target + elevation_offset, profile["elevation_max"],
                                     elevation_invert, profile["elevation_anchor"])
            if name is not None:
                reach[name] = {
                    "requested_bearing": bearing_delta,
                    "achieved_bearing": _from_dmx_centered(
                        bearing_dmx, profile["bearing_max"], bearing_invert),
                    "requested_elev": elev_target + elevation_offset,
                    "achieved_elev": _decode_elev(
                        elev_dmx, profile["elevation_max"], elevation_invert, profile["elevation_anchor"]),
                }
            channel_val = {profile["bearing_channel"]: bearing_dmx, profile["elevation_channel"]: elev_dmx}
            return (channel_val["pan"], channel_val["tilt"])

        # Ball: look at the mirrorball
        ball = _encode(bearing_delta_ball, elev_to_ball_geom, "ball")

        # Floor: in this room, the floor target is the SAME bearing as the
        # ball (FLOOR_X/Z == BALL_X/Z, both room center) -- so its bearing
        # delta is bearing_delta_ball, verbatim, no arithmetic at all. This
        # is deliberate: computing it independently via
        # _norm(bearing_to_floor - mount_facing) is ALGEBRAICALLY identical
        # before wrapping, but mount_facing can be a value far outside a
        # normal compass bearing once calibrated (e.g. -358 deg), and
        # _norm()'s modular reduction can land on a delta that's mathematically
        # congruent mod 360 but NOT the same real-world servo position on this
        # hardware -- confirmed by bench test (Ball correct, Floor visibly
        # wrong, built the old way). Written generally via _norm() of the
        # (typically zero) bearing difference so it still does the right
        # thing if a future config ever gives Floor a different target than
        # the ball.
        bearing_delta_f = bearing_delta_ball + _norm(_bearing(hx, hz, FLOOR_X, FLOOR_Z) - bearing_to_ball)
        elev_f = math.degrees(math.atan2(0 - head_height, horiz))
        floor = _encode(bearing_delta_f, elev_f, "floor")

        # Walls (renamed from "Crowd" 2026-07-22 -- it faces outward toward
        # the walls, not down at the actual crowd of people, which is what
        # "Crowd" now means below): turn 180 deg away from the ball, as a
        # DIRECT (non-wrapped) half-rotation from Ball's own validated delta
        # -- not a fixed +-180 from the servo's raw DMX-center (which would
        # ignore this head's calibration entirely: every head would land on
        # the same position regardless of where its ball calibration put
        # it), and not via a modular-360 reduction either (unsafe here, per
        # the Floor comment above). +180 and -180 are both legitimate direct
        # half-turns from a known-good reference -- either one independently
        # faces the opposite direction, unlike a 360-different value (which
        # is not a rotation at all, just an alternate encoding of the SAME
        # direction that turned out not to be interchangeable on this
        # hardware). Pick whichever fits the channel's actual range,
        # preferring the smaller magnitude if both (or neither) fit, to
        # favor predictable behavior over unnecessary clipping.
        bearing_max_half = profile["bearing_max"] / 2
        candidates = [bearing_delta_ball + 180, bearing_delta_ball - 180]
        in_range = [d for d in candidates if abs(d) <= bearing_max_half]
        bearing_delta_walls = min(in_range or candidates, key=abs)
        walls = _encode(bearing_delta_walls, 0, "walls")

        # Crowd (NEW 2026-07-22, replaces the old outward meaning): point
        # (nearly) straight down at the floor below this head. Bearing doesn't
        # meaningfully matter pointing down, so it just keeps Ball's -- only
        # elevation changes.
        # NOT a literal -90: ELEV_EXTREME_DEG backs every head off to an
        # elevation all four can actually reach, because a head whose
        # calibrated ball point sits far from its elevation channel's centre
        # clamps before 90 and would sit several degrees shy of vertical while
        # its neighbours hit it exactly (see the config comment). Note also
        # that with heads in the room CORNERS this lands a pool in each corner,
        # not on the dancefloor -- the floor sweeps are what put light where
        # people actually stand.
        crowd = _encode(bearing_delta_ball, -elev_extreme_down, "crowd")

        # Orbit +-90: quarter-turn either side of Ball, same elevation as
        # Ball. Building blocks for the Ball Spiral chaser (each head sweeps
        # through Ball / Orbit+90 / Walls / Orbit-90 in turn, phase-offset
        # from the others so they take turns grazing the ball). Deliberately
        # not range-fitted like Walls above -- these are stylistic sweep
        # positions, not a validated reference to preserve exactly, so
        # ordinary clamping if a head's calibration pushes one near the edge
        # is an acceptable, graceful outcome.
        orbit_p90 = _encode(bearing_delta_ball + 90, elev_to_ball_geom, "orbit_p90")
        orbit_m90 = _encode(bearing_delta_ball - 90, elev_to_ball_geom, "orbit_m90")

        # Orbit +-45: the Iris chaser's mid-points, between Ball (0 deg off)
        # and the +-90 quarter-turns. Same stylistic-position treatment as
        # orbit_p90/m90 above (clamping near a range edge is acceptable).
        orbit_p45 = _encode(bearing_delta_ball + 45, elev_to_ball_geom, "orbit_p45")
        orbit_m45 = _encode(bearing_delta_ball - 45, elev_to_ball_geom, "orbit_m45")

        # Wave up/down: same bearing as Ball (beam stays pointed at the
        # ball's compass direction), elevation nudged a fixed swing above/
        # below the ball's own elevation. Building blocks for the Ball Wave
        # chaser -- a gentle bob, phase-offset per head like Ball Spiral, so
        # the up/down motion ripples across the 4 heads rather than moving
        # in lockstep. Same "ordinary clamping is fine" reasoning as orbit
        # +-90 above: a stylistic offset from a validated reference, not a
        # reference to preserve exactly itself.
        wave_up = _encode(bearing_delta_ball, elev_to_ball_geom + WAVE_ELEV_SWING, "wave_up")
        wave_down = _encode(bearing_delta_ball, elev_to_ball_geom - WAVE_ELEV_SWING, "wave_down")

        # Circle (NEW 2026-07-23): head i aims at head (i+1)%4's own position
        # (HEADS is already in rotational order around the room, so this is
        # simply "the next corner"), using THAT head's own HEAD_HEIGHT (not
        # the ball's) -- a genuinely different target than Ball, not just a
        # bearing offset dressed up as one. Same safe idiom as everything
        # else here: anchor to bearing_delta_ball, add a _norm()-wrapped
        # correction, never rederive a fresh delta from scratch.
        next_i = (i + 1) % 4
        nx, nz = HEADS[next_i]
        horiz_circle = math.hypot(nx - hx, nz - hz)
        bearing_delta_circle = bearing_delta_ball + _norm(_bearing(hx, hz, nx, nz) - bearing_to_ball)
        elev_circle = math.degrees(math.atan2(HEAD_HEIGHT[next_i] - head_height, horiz_circle))
        circle = _encode(bearing_delta_circle, elev_circle, "circle")

        # Neighbor Scan (NEW 2026-07-23): each head scans between its two
        # neighbors -- (i-1)%4 and (i+1)%4 -- at the BALL's own elevation
        # (elev_to_ball_geom, reused verbatim: this is a horizontal sweep,
        # not a new elevation target), so the ball sits roughly at the
        # sweep's angular midpoint (verified empirically against the
        # current bench config in the build script, see project memory --
        # true by this room's corner-square symmetry, not assumed). Same
        # anchor-to-bearing_delta_ball idiom as Circle above.
        prev_i = (i - 1) % 4
        px, pz = HEADS[prev_i]
        bearing_delta_scan_prev = bearing_delta_ball + _norm(_bearing(hx, hz, px, pz) - bearing_to_ball)
        bearing_delta_scan_next = bearing_delta_ball + _norm(_bearing(hx, hz, nx, nz) - bearing_to_ball)
        scan_prev = _encode(bearing_delta_scan_prev, elev_to_ball_geom, "scan_prev")
        scan_next = _encode(bearing_delta_scan_next, elev_to_ball_geom, "scan_next")

        # WALL GRAZE (2026-07-30): scan_prev/scan_next are ALSO the two
        # wall-graze directions, and that is worth writing down because it was
        # not obvious. A head in a corner, inset HEAD_INSET from both its walls,
        # sits on the line between its two wall-sharing neighbours -- so aiming
        # at a neighbour aims along that shared wall, offset by HEAD_INSET, at
        # head height. The beam then rakes the whole length of the wall instead
        # of terminating on it.
        # This matters because every OTHER outward-facing pose does the
        # opposite: `walls` (180 deg from the ball) and orbit_p90/orbit_m90
        # (quarter-turns) all point into the head's own corner and hit a wall
        # within ~HEAD_INSET*sqrt(2) -- under a metre in this room. Those are
        # fine as park/"get off the ball" positions but they are not wall
        # washes, and nothing before this used the directions that are.
        # Held statically these give four beams down the four walls; alternated
        # per diagonal pair (Heads Cross A/B) they pinwheel.

        # --- Ball orbits (2026-07-30) ------------------------------------
        # A circle in (bearing, elevation) space centred on this head's own
        # calibrated ball point: bearing offset = R*sin(theta), elevation
        # offset = R*cos(theta). Anchored to bearing_delta_ball and
        # elev_to_ball_geom, so recalibrating a head re-centres its orbit
        # automatically -- which is the entire point, and exactly what the
        # absolute-DMX EFX these replace could not do.
        # Two radii: LAZY (small, the ambient signature drift) and GRAND (big,
        # reaching the wall-graze bearings at its horizontal extremes).
        # Sign-tolerant, not sign-insensitive: the bearing-channel invert is
        # uniform across heads within a mode, so a flipped sign just runs the
        # circle the other way round -- same shape either way.
        def _ball_orbit(radius_deg, k, steps, name,
                        _bdb=bearing_delta_ball, _eb=elev_to_ball_geom, _enc=_encode):
            theta = math.radians(360.0 * k / steps)
            return _enc(_bdb + radius_deg * math.sin(theta),
                        _eb + radius_deg * math.cos(theta), name)

        ring_small = [_ball_orbit(LAZY_ORBIT_RADIUS_DEG, k, BALL_ORBIT_STEPS,
                                  f"ring_small_{k}") for k in range(BALL_ORBIT_STEPS)]
        ring_big = [_ball_orbit(GRAND_ORBIT_RADIUS_DEG, k, BALL_ORBIT_STEPS,
                                f"ring_big_{k}") for k in range(BALL_ORBIT_STEPS)]

        # --- Aerial poses (NEW 2026-07-25) -------------------------------
        # Until now the show's highest elevation target anywhere was Ball Wave's
        # +20 deg: everything pointed at, below, or level with the mounting
        # plane. These three are the first poses to aim genuinely UP, which is
        # the throw the sideways ("venue") mount was chosen for in the first
        # place, and which "hung" reaches equally well (Tilt's zero-anchored
        # 270 deg range covers -90..+180).
        #
        # All three are deliberately at a 0 deg or 180 deg bearing offset from
        # Ball. A 0 or 180 deg turn lands on the same servo position no matter
        # which way the head rotates as DMX increases -- so unlike Circle /
        # Neighbor Scan / orbit+-90, these do NOT depend on the pan rotation
        # SIGN, which a single ball-calibration point cannot pin down (see the
        # 2026-07-23 Circle finding). They are trustworthy the moment each head
        # is ball-calibrated, with no second data point required.

        # Apex: a point directly ABOVE the ball, so it shares the ball's exact
        # bearing (bearing_delta_ball verbatim, no correction term needed --
        # APEX_X/Z == BALL_X/Z by construction) and differs only in elevation.
        # All 4 beams converge there: a teepee of light over the dancefloor,
        # or a hot spot on the parachute canopy if one is rigged.
        elev_apex = math.degrees(math.atan2(APEX_HEIGHT - head_height, horiz))
        apex = _encode(bearing_delta_ball, elev_apex, "apex")

        # Zenith: (nearly) straight up. Four vertical columns standing in the
        # corners. Bearing is meaningless pointing at the zenith, so it keeps
        # Ball's -- the same reasoning (and the same code shape) as Crowd's
        # straight-down, including the ELEV_EXTREME_DEG back-off: this is the
        # pose that clamped on the heads whose ball point sits far from their
        # elevation centre, exactly mirroring Crowd clamping on the others.
        zenith = _encode(bearing_delta_ball, elev_extreme_up, "zenith")

        # Sky Out: up AND outward, over the crowd toward the far upper corners,
        # so the four beams cross high overhead. Reuses bearing_delta_walls --
        # the already range-fitted half-turn computed above -- rather than
        # rederiving another one, so it inherits Walls' range handling for free.
        sky_out = _encode(bearing_delta_walls, 45, "sky_out")

        # --- Floor sweeps (NEW 2026-07-26) -------------------------------
        # Everything floor-facing so far aimed at exactly ONE spot: `floor`,
        # the point under the ball at room center (and `crowd`, straight down).
        # These aim at ARBITRARY points on the floor, which is what makes a
        # sweep possible -- a pool of light that travels instead of sitting.
        #
        # Same safe idiom as Floor/Circle: anchor the bearing to
        # bearing_delta_ball and add a _norm()-wrapped correction, never
        # rederive a delta from scratch. Elevation is straight geometry down to
        # y=0 at that point's own horizontal distance, so pools further from a
        # head sit shallower -- which is correct, not a bug.
        # Generalised 2026-07-30 from the original floor-only version to take an
        # arbitrary target HEIGHT, so the same helper aims at the canopy/ceiling
        # plane as well as the floor (y=0). `_floor_point` is kept as a thin
        # wrapper at y=0 so every existing floor sweep goes through byte-for-byte
        # unchanged code -- verified by diffing the generated scenes before and
        # after the refactor.
        def _point_at(fx, fz, fy, name=None, _hx=hx, _hz=hz, _hh=head_height,
                      _bdb=bearing_delta_ball, _btb=bearing_to_ball, _enc=_encode):
            bearing_delta = _bdb + _norm(_bearing(_hx, _hz, fx, fz) - _btb)
            horiz_fp = math.hypot(fx - _hx, fz - _hz)
            return _enc(bearing_delta, math.degrees(math.atan2(fy - _hh, horiz_fp)), name)

        def _floor_point(fx, fz, name=None):
            return _point_at(fx, fz, 0, name)

        # Ring: four points on a circle around room center, at 0/90/180/270.
        # A head stepping through them in order walks the full circle, so
        # assigning each head a rotating offset makes 4 pools carousel around
        # the room together. Angles use the module's bearing convention
        # (0 deg = +z, 90 deg = +x) so they read the same as everything else.
        floor_ring = []
        for ring_i, ang in enumerate((0, 90, 180, 270)):
            rad = math.radians(ang)
            floor_ring.append(_floor_point(
                FLOOR_X + FLOOR_SWEEP_RADIUS * math.sin(rad),
                FLOOR_Z + FLOOR_SWEEP_RADIUS * math.cos(rad),
                f"floor_ring_{ring_i}"))

        # Wipe: a straight ROW of 4 pools that marches front <-> back. Each head
        # keeps its own x lane and only z changes, so the row stays a row. Lanes
        # are handed out by the head's own x (then z) so the two left-hand heads
        # take the two left lanes and the two right-hand heads the right ones --
        # nobody has to swing a pool across the whole room to hold formation.
        lane_order = sorted(range(4), key=lambda h: (HEADS[h][0], HEADS[h][1]))
        lane_x = FLOOR_X + FLOOR_SWEEP_RADIUS * ((lane_order.index(i) / 1.5) - 1.0)
        wipe_front = _floor_point(lane_x, FLOOR_Z - FLOOR_SWEEP_RADIUS, "wipe_front")
        wipe_mid = _floor_point(lane_x, FLOOR_Z, "wipe_mid")
        wipe_back = _floor_point(lane_x, FLOOR_Z + FLOOR_SWEEP_RADIUS, "wipe_back")

        # Cross: each head sweeps its pool between the floor points directly
        # under its two NEIGHBOURING heads, so the beams rake low across the
        # floor and cross each other in the middle. Reuses the neighbour indices
        # already established by Circle/Neighbor Scan. Like Iris's +-45, this is
        # sign-TOLERANT rather than sign-insensitive: the bearing-channel
        # invert is uniform across heads within a mode (see PAN_INVERT's
        # comment above), so a flipped sign just mirrors the sweep.
        floor_cross_prev = _floor_point(px, pz, "floor_cross_prev")
        floor_cross_next = _floor_point(nx, nz, "floor_cross_next")

        # Breathe (NEW): same fixed compass angle as this head's own "home"
        # Ring position (90*i -- 0/90/180/270, matching floor_ring's angle
        # set), but the RADIUS grows and shrinks instead of the position
        # travelling -- so the 4 pools form a diamond around room center that
        # breathes in and out together, a third distinct sweep character
        # alongside Ring's carousel (position travels, radius fixed) and
        # Wipe's march (only depth changes, lockstep). Reuses _floor_point
        # like everything above, just at two different radii.
        breathe_rad = math.radians(90 * i)
        floor_breathe_near = _floor_point(
            FLOOR_X + FLOOR_SWEEP_RADIUS * FLOOR_BREATHE_NEAR_FRAC * math.sin(breathe_rad),
            FLOOR_Z + FLOOR_SWEEP_RADIUS * FLOOR_BREATHE_NEAR_FRAC * math.cos(breathe_rad),
            "floor_breathe_near")
        floor_breathe_far = _floor_point(
            FLOOR_X + FLOOR_SWEEP_RADIUS * FLOOR_BREATHE_FAR_FRAC * math.sin(breathe_rad),
            FLOOR_Z + FLOOR_SWEEP_RADIUS * FLOOR_BREATHE_FAR_FRAC * math.cos(breathe_rad),
            "floor_breathe_far")

        # --- Canopy ring (NEW 2026-07-30) --------------------------------
        # The aerial mirror of Floor Ring: four pools on a circle drawn on the
        # canopy/ceiling plane at APEX_HEIGHT instead of on the floor. Only
        # exists because _floor_point was generalised to _point_at -- same
        # helper, same safe bearing idiom, different target height.
        # With a parachute canopy rigged this is the payoff pose family: the
        # canopy is a large diffuse reflector, so four pools carouselling across
        # it light the whole room indirectly rather than throwing hard beams.
        canopy_ring = []
        for ring_i, ang in enumerate((0, 90, 180, 270)):
            rad = math.radians(ang)
            canopy_ring.append(_point_at(
                BALL_X + CANOPY_SWEEP_RADIUS * math.sin(rad),
                BALL_Z + CANOPY_SWEEP_RADIUS * math.cos(rad),
                APEX_HEIGHT,
                f"canopy_ring_{ring_i}"))

        poses[i] = {"ball": ball, "floor": floor, "walls": walls, "crowd": crowd,
                    "floor_ring_0": floor_ring[0], "floor_ring_1": floor_ring[1],
                    "floor_ring_2": floor_ring[2], "floor_ring_3": floor_ring[3],
                    "wipe_front": wipe_front, "wipe_mid": wipe_mid,
                    "wipe_back": wipe_back,
                    "floor_cross_prev": floor_cross_prev,
                    "floor_cross_next": floor_cross_next,
                    "floor_breathe_near": floor_breathe_near,
                    "floor_breathe_far": floor_breathe_far,
                    "orbit_p90": orbit_p90, "orbit_m90": orbit_m90,
                    "orbit_p45": orbit_p45, "orbit_m45": orbit_m45,
                    "wave_up": wave_up, "wave_down": wave_down,
                    "circle": circle, "scan_prev": scan_prev, "scan_next": scan_next,
                    "apex": apex, "zenith": zenith, "sky_out": sky_out,
                    **{f"ring_small_{k}": ring_small[k] for k in range(BALL_ORBIT_STEPS)},
                    **{f"ring_big_{k}": ring_big[k] for k in range(BALL_ORBIT_STEPS)},
                    "canopy_ring_0": canopy_ring[0], "canopy_ring_1": canopy_ring[1],
                    "canopy_ring_2": canopy_ring[2], "canopy_ring_3": canopy_ring[3],
                    "mount_facing": mount_facing, "reach": reach}
    return poses


# Poses that clamp against a hardware rail on their OWN account, every time,
# regardless of calibration -- not a symptom of a bad calibration reading.
# Currently just orbit_m90 in "venue" mode: it asks for a full -90 deg turn on
# Tilt's bearing role there, which only has +-135 deg of range to begin with
# (see README's "Walls pose is capped at 135 deg off center in venue mode").
# unreachable_poses() still reports these (silently dropping them would hide
# a real range limit), but tags them "known" so a human skimming the report
# isn't alarmed by the one clamp that's expected on every run.
_KNOWN_RAIL_POSES = {"venue": {"orbit_m90"}}


def unreachable_poses(mode=None, poses=None):
    """Every (head, pose) whose ENCODED dmx, decoded back, lands meaningfully
    off its geometric target -- i.e. it got clamped against 0/255 and/or
    rounded away from what compute_poses() actually asked for. Returns a list
    of dicts: {head, pose, known, severity, errorBearingDeg, errorElevDeg,
    requestedBearingDeg, achievedBearingDeg, requestedElevDeg, achievedElevDeg}
    sorted by descending max error.

    This is NOT a math bug detector (that's _self_test()'s job, which knows
    the model's own invariants); it is a REACHABILITY detector, purely about
    whether this mode/calibration/geometry combination gives a head enough
    channel range to actually hit a pose. `_self_test()` deliberately skips a
    rail-clamped pose ("direction not recoverable") because that's correct for
    a directional assertion -- but skipping means the clamp leaves no record
    anywhere. This is that record, surfaced in aim_calc.py's own output,
    preflight.py, and (via the function-ID lookup a caller can layer on top)
    the phone UI.

    Tolerance is 1.5 DMX steps of rounding slack on each channel (matching
    _self_test()'s TWO_DMX_DEG idiom) -- below that is just integer rounding,
    not a real reachability problem. `severity` is "significant" at >=10 deg
    of either error, "minor" otherwise (still worth listing, not worth
    panicking over)."""
    mode = mode or MOUNT_MODE
    profile = MOUNT_PROFILES[mode]
    poses = poses if poses is not None else compute_poses(mode=mode)
    tol_bearing = 1.5 * profile["bearing_max"] / 255.0
    tol_elev = 1.5 * profile["elevation_max"] / 255.0
    known = _KNOWN_RAIL_POSES.get(mode, set())

    out = []
    for i, p in poses.items():
        for name, r in p["reach"].items():
            err_b = abs(_norm(r["achieved_bearing"] - r["requested_bearing"]))
            err_e = abs(r["achieved_elev"] - r["requested_elev"])
            if err_b <= tol_bearing and err_e <= tol_elev:
                continue
            out.append({
                "head": i,
                "pose": name,
                "known": name in known,
                "severity": "significant" if (err_b >= 10 or err_e >= 10) else "minor",
                "errorBearingDeg": round(err_b, 1),
                "errorElevDeg": round(err_e, 1),
                "requestedBearingDeg": round(r["requested_bearing"], 1),
                "achievedBearingDeg": round(r["achieved_bearing"], 1),
                "requestedElevDeg": round(r["requested_elev"], 1),
                "achievedElevDeg": round(r["achieved_elev"], 1),
            })
    out.sort(key=lambda e: max(e["errorBearingDeg"], e["errorElevDeg"]), reverse=True)
    return out


def _fixture_val(pan_dmx, tilt_dmx, dim=None):
    """Pan/Tilt (+ optional Dimmer) as QLC+'s flat `ch,val,...` CSV.

    `dim` is only emitted when not None. That distinction is load-bearing for
    the dimmer-driven routines (Prowl, Crowd Cascade, Dim Chase, Spotlight):
    Dimmer is HTP, so a Scene can only ever push a head BRIGHTER than the MH
    Dim fader's baseline, never darker. "Dark" is expressed by leaving channel
    7 unwritten, not by writing a low value -- see the README's dimmer note.

    Channel 10 (Reset, a "Maintenance"-group/LTP channel) is ALWAYS pinned to
    0 here -- a safety baseline for "Reset Heads" (see despacio.qxw's Function
    170/177/178). Reset is a hold-5s-to-home command on this fixture and
    nothing else in the show ever touches channel 10, so without a continuous
    0-writer somewhere, stopping the Reset Heads function -- whether it
    finishes its own timed cycle OR gets stopped early (a second Toggle press
    before the 6s hold elapses) -- leaves channel 10 stuck at whatever it last
    wrote (255), putting every head into a permanent reset loop. This is the
    exact "stuck LTP channel" bug this codebase already hit once for Strobe
    (see README) and fixed the same way: have whatever's continuously running
    also assert the safe value, so it reasserts the instant Reset Heads' own
    contribution is removed, however that removal happens. Every pose-managed
    scene runs through this function, so as long as some pose is selected
    (true for essentially the whole show), this holds regardless of Reset
    Heads' own step timing."""
    csv = f"0,{pan_dmx},1,0,2,{tilt_dmx},3,0,10,0"
    if dim is not None:
        csv += f",7,{dim}"
    return csv


def _unpack_fixval(val):
    """TARGETS entries are (pan, tilt) or (pan, tilt, dim). Accept both so the
    21 position-only entries stay untouched by the dimmer-aware ones."""
    return val[0], val[1], (val[2] if len(val) > 2 else None)


def build_scene_block(fid, name, fixvals, fadein=3000, fadeout=0):
    lines = [f'  <Function ID="{fid}" Type="Scene" Name="{name}">',
             f'   <Speed FadeIn="{fadein}" FadeOut="{fadeout}" Duration="0"/>']
    for fxid, val in fixvals.items():
        lines.append(f'   <FixtureVal ID="{fxid}">{_fixture_val(*_unpack_fixval(val))}</FixtureVal>')
    lines.append('  </Function>')
    return "\n".join(lines)


def _self_test():
    """Regression guard for BOTH modes' math, run automatically before every
    apply_to_qxw(). Bug fixes to shared code (geometry, encode/decode) must
    keep both modes passing; a fix that only helps one mode and breaks the
    other should fail here before it ever reaches despacio.qxw."""
    # Center-anchor round-trips to 128 at delta=0, for every (max_deg, invert)
    # combination actually used by either mode's bearing role.
    for max_deg in (PAN_MAX, TILT_MAX):
        for invert in (False, True):
            assert _to_dmx_centered(0, max_deg, invert) == 128
            assert abs(_from_dmx_centered(128, max_deg, invert)) < 1e-9

    # "table" mode's zero-anchored elevation, positive=up (matching
    # compute_poses()'s stated convention -- this consistency is exactly what
    # broke on 2026-07-22: the formula used to assume positive=down while
    # compute_poses fed it positive=up values, sending Floor the wrong way
    # on the bench test). DMX0=straight down (el=-90), level (el=0) should be
    # 85 for TILT_MAX=270, and "more up" must give a STRICTLY LARGER dmx than
    # level, not smaller -- that direction check is the actual invariant that
    # was broken, not just the specific numbers.
    assert _to_dmx_zero_elev(-90, TILT_MAX, False) == 0
    assert _to_dmx_zero_elev(0, TILT_MAX, False) == 85
    assert _to_dmx_zero_elev(45, TILT_MAX, False) > _to_dmx_zero_elev(0, TILT_MAX, False), (
        "elevation direction inverted: 'more up' must yield a larger DMX than level")

    # Calibration must round-trip EXACTLY in both modes, for EVERY head (not
    # just head 0 -- catches per-head indexing bugs), across a spread of
    # values including both extremes (0/255) and large near-boundary deltas
    # (the case that originally broke this -- see comment in compute_poses).
    saved_cal = {m: list(CALIBRATED_BALL_DMX[m]) for m in MOUNT_PROFILES}
    test_points = [(140, 60), (30, 200), (128, 128), (0, 0), (255, 255), (255, 0), (0, 255)]
    try:
        for mode in MOUNT_PROFILES:
            for head_i in range(4):
                for test_dmx in test_points:
                    CALIBRATED_BALL_DMX[mode][head_i] = test_dmx
                    poses = compute_poses(mode=mode)
                    assert poses[head_i]["ball"] == test_dmx, (
                        f"calibration round-trip failed for mode={mode} head={head_i}: "
                        f"expected {test_dmx}, got {poses[head_i]['ball']}")
                CALIBRATED_BALL_DMX[mode][head_i] = saved_cal[mode][head_i]
    finally:
        for m in MOUNT_PROFILES:
            CALIBRATED_BALL_DMX[m][:] = saved_cal[m]

    # Since FLOOR_X/Z == BALL_X/Z (both room center) by default, Floor's
    # bearing-carrying channel value MUST exactly equal Ball's, for every
    # head/mode -- this is precisely the invariant the production bug broke
    # (Ball correct, Floor visibly wrong on the bench test) before the fix.
    for mode in MOUNT_PROFILES:
        profile = MOUNT_PROFILES[mode]
        bearing_idx = 0 if profile["bearing_channel"] == "pan" else 1
        for i, p in compute_poses(mode=mode).items():
            assert p["ball"][bearing_idx] == p["floor"][bearing_idx], (
                f"mode={mode} head={i}: Floor bearing-channel value "
                f"{p['floor'][bearing_idx]} != Ball's {p['ball'][bearing_idx]} "
                f"(FLOOR_X/Z == BALL_X/Z, so these must match exactly)")

    # ...and the ELEVATION channel must move the RIGHT WAY off Ball -- this is
    # the direct guard for bug #3 (table-mode Floor pointing the wrong way and
    # clipping onto Ball's tilt). The bearing check above can't catch it: bug #3
    # left the bearing correct and only broke elevation. Whenever Floor's target
    # elevation genuinely differs from Ball's (the default same-height rig:
    # Floor looks down, Ball is level), Floor's elevation-channel DMX must land
    # STRICTLY on the expected side of Ball's:
    #   * "table" (world_flip_elevation=True): real-down reads as bench-UP, so
    #     Floor's elevation DMX must be > Ball's. The regression made them EQUAL.
    #   * "venue" (no world flip): Floor looks physically down, so its
    #     center-anchored elevation DMX must be < Ball's (level).
    # A legitimate far-boundary clip still passes (it saturates in the correct
    # direction, away from Ball); only a wrong-direction collapse onto Ball --
    # exactly the bug -- fails here.
    for mode in MOUNT_PROFILES:
        profile = MOUNT_PROFILES[mode]
        elev_idx = 0 if profile["elevation_channel"] == "pan" else 1
        for i, (hx, hz) in enumerate(HEADS):
            horiz = math.hypot(BALL_X - hx, BALL_Z - hz)
            elev_ball = math.degrees(math.atan2(BALL_HEIGHT - HEAD_HEIGHT[i], horiz))
            elev_floor = math.degrees(math.atan2(0 - HEAD_HEIGHT[i], horiz))
            if abs(elev_floor - elev_ball) < 1e-6:
                continue  # ball sitting at floor height -- no direction to check
            p = compute_poses(mode=mode)[i]
            ball_e, floor_e = p["ball"][elev_idx], p["floor"][elev_idx]
            # Per-head, not just per-mode: a mirrored-mount head's own
            # PAN_INVERT/TILT_INVERT flag can flip its effective elevation
            # handedness independently of world_flip_elevation (see
            # _elevation_invert() -- this replaces the old mode-only
            # `profile["world_flip_elevation"]` assumption, which asserted a
            # direction the config couldn't actually satisfy once a real head
            # needed a per-head invert, 2026-07-29).
            expect_greater = _elevation_invert(mode, i)  # effective flip -> down reads as up
            ok = floor_e > ball_e if expect_greater else floor_e < ball_e
            assert ok, (
                f"mode={mode} head={i}: Floor elevation-channel DMX {floor_e} is "
                f"not {'>' if expect_greater else '<'} Ball's {ball_e} "
                f"(Floor target elev {elev_floor:.1f}deg vs Ball {elev_ball:.1f}deg "
                f"-- elevation invert/world-flip is wrong; this is the bug #3 regression)")

    # AERIAL POSES must actually point UP -- the same class of invariant as the
    # Floor check above, in the opposite direction. Apex/Zenith/Sky Out are the
    # first poses in this show to target positive elevation at all, so nothing
    # previously exercised the "up" half of the elevation channel; a sign error
    # here would silently aim them at the floor (exactly how bug #3 presented).
    # Encoded as a direction assertion rather than specific DMX numbers so it
    # stays valid across modes and recalibration.
    for mode in MOUNT_PROFILES:
        profile = MOUNT_PROFILES[mode]
        elev_idx = 0 if profile["elevation_channel"] == "pan" else 1
        for i, p in compute_poses(mode=mode).items():
            # Per-head (see the Floor block above for why this moved off a
            # single per-mode value): up -> larger DMX unless THIS head's
            # effective elevation invert is set.
            expect_greater = not _elevation_invert(mode, i)
            ball_e = p["ball"][elev_idx]
            # canopy_ring_* joins the aerial group: the canopy plane sits at
            # APEX_HEIGHT, above the heads, so every pool on it must encode
            # upward exactly like apex/zenith. This is the guard that catches
            # _point_at() being handed the wrong height argument (or reverting
            # to the y=0 floor behaviour it was generalised out of).
            for pose_name in ("apex", "zenith", "sky_out",
                              "canopy_ring_0", "canopy_ring_1",
                              "canopy_ring_2", "canopy_ring_3"):
                e = p[pose_name][elev_idx]
                if e in (0, 255) or ball_e in (0, 255):
                    # Rail-clamped: a head whose calibration sits at a mechanical
                    # stop (as the "table" bench reading does) has no headroom
                    # left on that side, so direction is not recoverable. This is
                    # the documented zero-anchor range limitation, not a bug.
                    continue
                ok = e > ball_e if expect_greater else e < ball_e
                assert ok, (
                    f"mode={mode} head={i} pose={pose_name} elevation DMX {e} is not "
                    f"{'>' if expect_greater else '<'} Ball's {ball_e} -- the aerial "
                    f"poses are aiming DOWN, not up (elevation sign/world-flip error)")
            # ...and the floor sweeps must aim DOWN, the exact mirror. The ball
            # sits at head height here, so every floor point is strictly below
            # it; a sweep landing level or above means the elevation term was
            # dropped or negated somewhere in _floor_point.
            for pose_name in ("floor_ring_0", "floor_ring_1", "floor_ring_2",
                              "floor_ring_3", "wipe_front", "wipe_mid", "wipe_back",
                              "floor_cross_prev", "floor_cross_next",
                              "floor_breathe_near", "floor_breathe_far"):
                e = p[pose_name][elev_idx]
                if e in (0, 255) or ball_e in (0, 255):
                    continue
                ok = e < ball_e if expect_greater else e > ball_e
                assert ok, (
                    f"mode={mode} head={i} pose={pose_name} elevation DMX {e} is not "
                    f"{'<' if expect_greater else '>'} Ball's {ball_e} -- a floor "
                    f"sweep is aiming UP, not down")

            # BALL ORBITS must actually be circles centred on Ball. Two
            # independent properties, both of which a sign/index slip breaks:
            #   * step 0 is the top of the circle (elevation offset +R, bearing
            #     offset 0) and step steps/2 is the bottom (-R) -- so they must
            #     straddle Ball in elevation, one each side.
            #   * steps steps/4 and 3*steps/4 are the horizontal extremes
            #     (elevation offset 0), so their elevation must land back ON
            #     Ball's.
            # Written as relations to Ball rather than absolute DMX so it holds
            # across modes, radii and recalibration.
            quarter = BALL_ORBIT_STEPS // 4
            for ring, radius in (("ring_small", LAZY_ORBIT_RADIUS_DEG),
                                 ("ring_big", GRAND_ORBIT_RADIUS_DEG)):
                top = p[f"{ring}_0"][elev_idx]
                bottom = p[f"{ring}_{2 * quarter}"][elev_idx]
                if 0 in (top, bottom, ball_e) or 255 in (top, bottom, ball_e):
                    continue  # rail-clamped: direction not recoverable
                hi, lo = (top, bottom) if expect_greater else (bottom, top)
                assert hi > ball_e > lo, (
                    f"mode={mode} head={i} {ring}: orbit top/bottom DMX "
                    f"({top}/{bottom}) do not straddle Ball's {ball_e} -- the "
                    f"{radius:.0f}deg circle is not centred on the ball point")
                for side in (quarter, 3 * quarter):
                    e = p[f"{ring}_{side}"][elev_idx]
                    assert e == ball_e, (
                        f"mode={mode} head={i} {ring}_{side} elevation DMX {e} != "
                        f"Ball's {ball_e} -- the orbit's horizontal extremes must "
                        f"sit at Ball's own elevation")

    # ZENITH/CROWD must reach the SAME angle on every head. These are unison
    # poses -- all 4 beams vertical together -- so a head that runs out of
    # travel and clamps a few degrees short while its neighbours hit the target
    # is the specific failure _fit_elev_extreme() exists to prevent (and which
    # a hardcoded extreme caused: with the venue calibration, heads 1 and 3 sit
    # ~184 deg from their elevation centre and clamp on one side each).
    # Asserted on the DECODED angle relative to each head's own ball point,
    # not on raw DMX -- the heads legitimately have different DMX for the same
    # real-world angle, which is exactly why a DMX comparison wouldn't work.
    for mode in MOUNT_PROFILES:
        profile = MOUNT_PROFILES[mode]
        elev_idx = 0 if profile["elevation_channel"] == "pan" else 1
        poses = compute_poses(mode=mode)
        step_deg = profile["elevation_max"] / 255.0
        for pose_name in ("zenith", "crowd"):
            deltas = []
            for i in range(4):
                _elev_ball, offset, invert = _elevation_frame(mode, i)
                dec = lambda d: _decode_elev(d, profile["elevation_max"], invert,
                                             profile["elevation_anchor"])
                deltas.append(dec(poses[i][pose_name][elev_idx])
                              - dec(poses[i]["ball"][elev_idx]))
            spread = max(deltas) - min(deltas)
            # In a mode where the fit had to give up entirely (see
            # ELEV_EXTREME_MIN_USEFUL_DEG -- "table" bench calibration sits on a
            # mechanical stop), the poses clamp by design and the spread is
            # meaningless; unreachable_poses() reports those instead.
            fitted = _fit_elev_extreme(mode, +1 if pose_name == "zenith" else -1)
            if fitted >= ELEV_EXTREME_DEG and not all(
                    0 < poses[i][pose_name][elev_idx] < 255 for i in range(4)):
                continue
            assert spread <= 2 * step_deg, (
                f"mode={mode} pose={pose_name}: heads reach different angles "
                f"({[round(d, 1) for d in deltas]} deg off Ball, spread "
                f"{spread:.1f} deg) -- the shared vertical extreme is being "
                f"applied per-head or one head is clamping")

    # DIRECTION-SENSITIVE POSES must aim at their intended geometric target.
    # Circle / Neighbor Scan / Ball-Spiral orbits are the FIRST poses that use
    # a non-trivial bearing OFFSET from the calibrated ball point (not 0deg
    # like Ball/Floor/Crowd, not 180deg like Walls). A 0deg or 180deg offset
    # comes out to the SAME servo position regardless of which way pan rotates,
    # so none of the earlier-tested poses could ever catch a wrong bearing
    # turn -- Circle was the first, and it shipped pointing at the wrong
    # fixture (2026-07-23). This block decodes each such pose's bearing DMX
    # back through the same calibration model and asserts it lands on the
    # bearing to the ACTUAL intended target (correct neighbor index, correct
    # offset magnitude AND sign-in-model). It would have caught a wrong
    # (i+1)/(i-1) neighbor pick or a flipped correction term.
    #
    # IMPORTANT / what this canNOT catch: it is self-consistent for EITHER
    # pan_invert value (encode and decode share the sign), so it does NOT
    # verify that the configured per-head pan_invert matches the real hardware
    # rotation direction. That is a physical fact, unknowable from one ball
    # reading -- pin it with an on-hardware per-head direction check (see the
    # pan_invert notes in despacio_config.json / README) and by watching
    # Circle actually hit the neighbor fixture. This guard protects the MATH;
    # the config value protects the PHYSICS.
    TWO_DMX_DEG = lambda bmax: bmax / 255.0  # deg per DMX step; rounding is +-0.5 step
    for mode in MOUNT_PROFILES:
        profile = MOUNT_PROFILES[mode]
        bearing_ch = profile["bearing_channel"]
        bmax = profile["bearing_max"]
        bidx = 0 if bearing_ch == "pan" else 1
        bearing_invert_list = (PAN_INVERT if bearing_ch == "pan" else TILT_INVERT)[mode]
        tol = TWO_DMX_DEG(bmax) * 1.5  # allow 1.5 DMX of rounding slack
        poses = compute_poses(mode=mode)
        for i, (hx, hz) in enumerate(HEADS):
            mount_facing = poses[i]["mount_facing"]
            binv = bearing_invert_list[i]
            bearing_to_ball = _bearing(hx, hz, BALL_X, BALL_Z)
            nx, nz = HEADS[(i + 1) % 4]
            px, pz = HEADS[(i - 1) % 4]
            # (pose_name -> intended absolute world bearing)
            intended = {
                "circle":    _bearing(hx, hz, nx, nz),
                "scan_next": _bearing(hx, hz, nx, nz),
                "scan_prev": _bearing(hx, hz, px, pz),
                "orbit_p90": bearing_to_ball + 90,
                "orbit_m90": bearing_to_ball - 90,
                "orbit_p45": bearing_to_ball + 45,
                "orbit_m45": bearing_to_ball - 45,
                # The two ball orbits' horizontal extremes: at steps/4 and
                # 3*steps/4 the sine term is +-1, so the bearing offset is
                # exactly +-radius. These are the direction-sensitive part of
                # the new Lazy Circle / Grand Sweep chasers.
                f"ring_small_{BALL_ORBIT_STEPS // 4}": bearing_to_ball + LAZY_ORBIT_RADIUS_DEG,
                f"ring_small_{3 * BALL_ORBIT_STEPS // 4}": bearing_to_ball - LAZY_ORBIT_RADIUS_DEG,
                f"ring_big_{BALL_ORBIT_STEPS // 4}": bearing_to_ball + GRAND_ORBIT_RADIUS_DEG,
                f"ring_big_{3 * BALL_ORBIT_STEPS // 4}": bearing_to_ball - GRAND_ORBIT_RADIUS_DEG,
            }
            for pose_name, want in intended.items():
                dmx = poses[i][pose_name][bidx]
                if dmx <= 0 or dmx >= 255:
                    continue  # clamped at a rail -- exact geometry not recoverable
                got = mount_facing + _from_dmx_centered(dmx, bmax, binv)
                assert abs(_norm(got - want)) < tol, (
                    f"mode={mode} head={i} pose={pose_name} aims at world "
                    f"bearing {got:.1f}deg but its intended target is {want:.1f}deg "
                    f"(off by {_norm(got - want):.1f}deg -- wrong neighbor index or "
                    f"wrong offset sign in compute_poses, NOT a hardware-invert issue)")

    # Every mode must produce valid, in-range DMX for all 4 heads/poses, both
    # with its actual configured calibration data AND under an extreme
    # calibration on every head at once (stresses Floor/Crowd, which are
    # never round-trip-checked directly, only clamped).
    for mode in MOUNT_PROFILES:
        for i, p in compute_poses(mode=mode).items():
            for pose_name in ("ball", "floor", "crowd", "wave_up", "wave_down",
                               "circle", "scan_prev", "scan_next",
                               "orbit_p45", "orbit_m45",
                               "apex", "zenith", "sky_out",
                               "floor_ring_0", "floor_ring_1", "floor_ring_2",
                               "floor_ring_3", "wipe_front", "wipe_mid",
                               "wipe_back", "floor_cross_prev", "floor_cross_next",
                               "floor_breathe_near", "floor_breathe_far",
                               "canopy_ring_0", "canopy_ring_1", "canopy_ring_2",
                               "canopy_ring_3",
                               *(f"ring_small_{k}" for k in range(BALL_ORBIT_STEPS)),
                               *(f"ring_big_{k}" for k in range(BALL_ORBIT_STEPS))):
                pan_dmx, tilt_dmx = p[pose_name]
                assert 0 <= pan_dmx <= 255 and 0 <= tilt_dmx <= 255, (
                    f"mode={mode} head={i} pose={pose_name} out of range: {p[pose_name]}")
    try:
        for mode in MOUNT_PROFILES:
            for extreme in [(0, 0), (255, 255)]:
                CALIBRATED_BALL_DMX[mode][:] = [extreme, extreme, extreme, extreme]
                for i, p in compute_poses(mode=mode).items():
                    for pose_name in ("ball", "floor", "crowd"):
                        pan_dmx, tilt_dmx = p[pose_name]
                        assert 0 <= pan_dmx <= 255 and 0 <= tilt_dmx <= 255, (
                            f"mode={mode} head={i} pose={pose_name} out of range "
                            f"under extreme calibration {extreme}: {p[pose_name]}")
    finally:
        for m in MOUNT_PROFILES:
            CALIBRATED_BALL_DMX[m][:] = saved_cal[m]


def build_targets(poses):
    """Pure function: QLC+ Function ID -> {head_index: fixture value} for every
    pose-dependent scene/chaser-step in the show, built from an already-computed
    `poses` (see compute_poses()). No I/O -- extracted out of apply_to_qxw() so
    the function-ID mapping can be reused (e.g. by the reachability report,
    which needs to say WHICH functions a clamped pose actually reaches) without
    duplicating this dict by hand."""

    def fixvals_for(pose_name, heads):
        return {h: poses[h][pose_name] for h in heads}

    # Ball Spiral's 4-step rotation per head: each head steps through
    # ball -> orbit+90 -> walls (opposite) -> orbit-90 -> repeat, phase-offset
    # by its own index so all 4 heads are never at the same point in the
    # cycle at once -- one is always at/near Ball while the others sweep.
    SPIRAL_SEQ = ["ball", "orbit_p90", "walls", "orbit_m90"]

    # Ball Wave's 4-step rotation per head: ball -> wave_up -> ball ->
    # wave_down -> repeat, phase-offset by index like Ball Spiral above, so
    # the up/down bob ripples across the 4 heads instead of all bobbing in
    # lockstep.
    WAVE_SEQ = ["ball", "wave_up", "ball", "wave_down"]

    # Prowl reuses Ball Spiral's positions verbatim and adds Dimmer=full to
    # whichever head is at the Ball phase that step -- exactly one head lit at
    # a time, guaranteed by construction rather than by hand-maintained values.
    PROWL_SEQ = SPIRAL_SEQ

    # Crowd Cascade's per-head 4-state cycle, phase-offset by head index like
    # every other SEQ here. `None` means the head's FixtureVal is OMITTED
    # entirely that step -- that omission is the mechanism, not an oversight:
    # a Chaser step that doesn't write a head's Dimmer leaves only the MH Dim
    # fader's low baseline active, i.e. the head genuinely goes dark, while
    # position holds LTP from the previous step. Exactly 2 of 4 heads are lit
    # at any instant (states A+B).
    CASCADE_SEQ = [
        ("ball", 255),    # A: lit, on the ball
        ("crowd", 255),   # B: lit, swept down at the crowd
        None,             # C: dark, holds Crowd from the previous step
        ("ball", None),   # D: dark, position resets to Ball while unlit
    ]

    def prowl_step(k):
        out = {}
        for i in range(4):
            pose = PROWL_SEQ[(k - i) % 4]
            out[i] = poses[i][pose] + ((255,) if pose == "ball" else (None,))
        return out

    def cascade_step(k):
        out = {}
        for i in range(4):
            state = CASCADE_SEQ[(k - i) % 4]
            if state is None:
                continue          # head omitted -> dark this step
            pose, dim = state
            out[i] = poses[i][pose] + (dim,)
        return out

    # --- Dark-move routines (2026-07-29): the moving heads go dark
    # (Dimmer unwritten) for every travel step and snap/fade back on only
    # once they've arrived and are holding still -- the inverse of every
    # earlier routine, which moves lit. Teleport/Apparition/Freeze Frame/
    # Stutter all reuse existing pose keys (no new geometry); see the
    # matching Chaser XML in despacio.qxw for the per-step fade timing that
    # actually makes the dark travel invisible and the lit arrival a snap.

    # Teleport / Apparition share this exact 4-pose cycle, all 4 heads in
    # unison: centre -> straight up -> outward -> straight down. Each pose
    # gets a dark step (travelling into it) then a lit step (holding it),
    # so the two chasers just replay the same 8 scenes at different speeds.
    TELEPORT_SEQ = ["ball", "zenith", "walls", "crowd"]

    # Freeze Frame: the two diagonal pairs (0+2 vs 1+3) alternate which one
    # is lit-and-still vs dark-and-travelling. (pose_A, lit_A, pose_B, lit_B).
    FREEZE_SEQ = [
        ("ball",  True,  "walls", False),
        ("walls", False, "walls", True),
        ("walls", True,  "ball",  False),
        ("ball",  False, "ball",  True),
    ]

    # Stutter: unison stop-motion bob between the same +-20 deg wave points
    # Ball Wave uses, but hopped dark instead of crossfaded lit -- small
    # moves on purpose, so each hop finishes inside a short dark step.
    STUTTER_SEQ = ["wave_up", "wave_down"]

    # Ascension (new 2026-07-30): a unison dark-travel climb through the
    # vertical range Teleport never reaches -- straight down, to the ball, up
    # to the aerial apex, straight up. Same unison_step() construction as
    # Teleport, just a different (and directional, not arbitrary-looped) SEQ.
    ASCENSION_SEQ = ["crowd", "ball", "apex", "zenith"]

    # Blink (new 2026-07-30): reuses the already-computed floor_ring_0..3
    # points (the same 4 pools Floor Ring sweeps through) as a unison
    # dark-travel/lit-arrive cycle instead of a continuous crossfade -- "the
    # carousel that teleports" rather than sweeps. No new geometry.
    BLINK_SEQ = ["floor_ring_0", "floor_ring_1", "floor_ring_2", "floor_ring_3"]

    def unison_step(pose, lit):
        return {i: poses[i][pose] + (255 if lit else None,) for i in range(4)}

    # --- Rebuilt movement blocks (2026-07-30) -------------------------------
    # Lazy Circle, Slow Sweep and Grand Sweep used to be absolute-DMX EFX
    # centred on channel 127, and Heads Cross A/B were hand-picked absolute
    # scenes. None of them could track the rig: a QLC+ EFX has ONE global
    # centre with no per-fixture offset, and the heads' calibrated ball points
    # differ (in "venue" the two mirrored pairs sit ~103 DMX apart on the
    # elevation channel), so two heads orbited the ball while the other two
    # pointed ~180 deg away into their own corners -- and Grand Sweep/Cross
    # Weave pushed past the pan rail and sat pinned there. Because they fed
    # Warm Up / Idle / Deep, that was three of the six SHOW looks.
    # All four are now derived from the calibrated poses like everything else.

    # Heads sit a quarter-revolution apart on the orbit, reproducing the
    # original EFX's per-fixture StartOffset 0/90/180/270.
    ORBIT_PHASE = BALL_ORBIT_STEPS // 4

    def orbit_step(ring, k):
        return {i: poses[i][f"{ring}_{(k + ORBIT_PHASE * i) % BALL_ORBIT_STEPS}"]
                for i in range(4)}

    # Slow Sweep: a horizontal rake between this head's two wall-graze
    # bearings, passing through the ball on the way (hence the two "ball"
    # entries). The diagonal pairs run half a cycle apart -- the i%2 antiphase
    # convention Floor Cross uses -- which preserves the original EFX's
    # per-fixture Forward/Backward/Forward/Backward opposition.
    SWEEP_SEQ = ["scan_prev", "ball", "scan_next", "ball"]

    def sweep_step(k):
        return {i: poses[i][SWEEP_SEQ[(k + 2 * (i % 2)) % 4]] for i in range(4)}

    def freeze_step(k):
        pose_a, lit_a, pose_b, lit_b = FREEZE_SEQ[k]
        out = {}
        for i in (0, 2):
            out[i] = poses[i][pose_a] + (255 if lit_a else None,)
        for i in (1, 3):
            out[i] = poses[i][pose_b] + (255 if lit_b else None,)
        return out

    # Function ID -> which heads get which pose
    TARGETS = {
        6:  fixvals_for("ball",  [0, 1, 2, 3]),   # Heads - Ball
        7:  fixvals_for("floor", [0, 1, 2, 3]),   # Heads - Floor
        8:  fixvals_for("walls", [0, 1, 2, 3]),   # Heads - Walls (renamed from Crowd)
        51: fixvals_for("crowd", [0, 1, 2, 3]),   # Heads - Crowd (NEW: straight down)
        # Diagonal pairs per the real corner mapping: ID0(back-right)+ID2(front-left)
        # are opposite corners; ID1(front-right)+ID3(back-left) are the other pair.
        42: {0: poses[0]["ball"], 2: poses[2]["ball"],
             1: poses[1]["walls"], 3: poses[3]["walls"]},  # Diagonal A
        43: {1: poses[1]["ball"], 3: poses[3]["ball"],
             0: poses[0]["walls"], 2: poses[2]["walls"]},  # Diagonal B
        45: {0: poses[0]["ball"], 1: poses[1]["walls"],
             2: poses[2]["walls"], 3: poses[3]["walls"]},  # Chase Pos 1
        46: {1: poses[1]["ball"], 0: poses[0]["walls"],
             2: poses[2]["walls"], 3: poses[3]["walls"]},  # Chase Pos 2
        47: {2: poses[2]["ball"], 0: poses[0]["walls"],
             1: poses[1]["walls"], 3: poses[3]["walls"]},  # Chase Pos 3
        48: {3: poses[3]["ball"], 0: poses[0]["walls"],
             1: poses[1]["walls"], 2: poses[2]["walls"]},  # Chase Pos 4
        52: {i: poses[i][SPIRAL_SEQ[(0 - i) % 4]] for i in range(4)},  # Ball Spiral Step 1
        53: {i: poses[i][SPIRAL_SEQ[(1 - i) % 4]] for i in range(4)},  # Ball Spiral Step 2
        54: {i: poses[i][SPIRAL_SEQ[(2 - i) % 4]] for i in range(4)},  # Ball Spiral Step 3
        55: {i: poses[i][SPIRAL_SEQ[(3 - i) % 4]] for i in range(4)},  # Ball Spiral Step 4
        63: {i: poses[i][WAVE_SEQ[(0 - i) % 4]] for i in range(4)},   # Ball Wave Step 1
        64: {i: poses[i][WAVE_SEQ[(1 - i) % 4]] for i in range(4)},   # Ball Wave Step 2
        65: {i: poses[i][WAVE_SEQ[(2 - i) % 4]] for i in range(4)},   # Ball Wave Step 3
        66: {i: poses[i][WAVE_SEQ[(3 - i) % 4]] for i in range(4)},   # Ball Wave Step 4
        78: fixvals_for("circle", [0, 1, 2, 3]),       # Heads - Circle
        79: fixvals_for("scan_prev", [0, 1, 2, 3]),    # Neighbor Scan Step 1
        80: fixvals_for("scan_next", [0, 1, 2, 3]),    # Neighbor Scan Step 2
        # Prowl / Crowd Cascade (added 2026-07-25). These 8 scenes were
        # originally hand-built by copying table-mode values out of Ball
        # Spiral / Ball / Crowd, and were NOT in TARGETS -- so a venue-day
        # recalibration would have updated every other pose and silently left
        # these two routines aiming at stale bench-rig positions, with no
        # error. They are derived here for the same reason everything else is.
        82: prowl_step(0),   # Prowl Step 1
        83: prowl_step(1),   # Prowl Step 2
        84: prowl_step(2),   # Prowl Step 3
        85: prowl_step(3),   # Prowl Step 4
        87: cascade_step(0),  # Crowd Cascade Step 1
        88: cascade_step(1),  # Crowd Cascade Step 2
        89: cascade_step(2),  # Crowd Cascade Step 3
        90: cascade_step(3),  # Crowd Cascade Step 4
        # Aerial poses (2026-07-25) -- the first looks in this show to aim
        # above the mounting plane. Rise and Build reuse these as chaser steps
        # rather than duplicating the positions into scenes of their own.
        92: fixvals_for("apex", [0, 1, 2, 3]),      # Heads - Apex
        93: fixvals_for("sky_out", [0, 1, 2, 3]),   # Heads - Cathedral
        94: fixvals_for("zenith", [0, 1, 2, 3]),    # Heads - Zenith
        96: fixvals_for("orbit_p45", [0, 1, 2, 3]),  # Iris Out A
        97: fixvals_for("orbit_m45", [0, 1, 2, 3]),  # Iris Out B
        # Floor sweeps (2026-07-26), col 4. Ring rotates each head's assignment
        # by one quarter per step so all 4 pools carousel around the room; Wipe
        # moves every head in lockstep so the row of pools holds formation;
        # Cross phases the diagonal pairs against each other so they rake past.
        117: {i: poses[i][f"floor_ring_{(i + 0) % 4}"] for i in range(4)},
        118: {i: poses[i][f"floor_ring_{(i + 1) % 4}"] for i in range(4)},
        119: {i: poses[i][f"floor_ring_{(i + 2) % 4}"] for i in range(4)},
        120: {i: poses[i][f"floor_ring_{(i + 3) % 4}"] for i in range(4)},
        122: fixvals_for("wipe_front", [0, 1, 2, 3]),  # Floor Wipe Step 1
        123: fixvals_for("wipe_mid", [0, 1, 2, 3]),    # Floor Wipe Step 2
        124: fixvals_for("wipe_back", [0, 1, 2, 3]),   # Floor Wipe Step 3
        125: fixvals_for("wipe_mid", [0, 1, 2, 3]),    # Floor Wipe Step 4
        127: {i: poses[i]["floor_cross_prev" if i % 2 == 0 else "floor_cross_next"]
              for i in range(4)},                       # Floor Cross Step 1
        128: {i: poses[i]["floor_cross_next" if i % 2 == 0 else "floor_cross_prev"]
              for i in range(4)},                       # Floor Cross Step 2
        # Floor Bounce (2026-07-30): reuses Wipe's own lane points (no new
        # geometry) but swaps the diagonal-pair parity each step (same i%2
        # antiphase convention as Floor Cross) instead of Wipe's lockstep
        # march -- two pools converge toward the middle while the other two
        # retreat, then swap, a "breathing diamond" along the depth axis
        # rather than a row holding formation.
        171: {i: poses[i]["wipe_front" if i % 2 == 0 else "wipe_back"]
              for i in range(4)},                       # Floor Bounce Step 1
        172: {i: poses[i]["wipe_back" if i % 2 == 0 else "wipe_front"]
              for i in range(4)},                       # Floor Bounce Step 2
        # Floor Breathe (2026-07-30): all 4 heads step near-radius -> far-radius
        # together at their own fixed compass angle (see floor_breathe_near/far
        # in compute_poses) -- the pools grow and shrink in place as one
        # diamond, the one sweep character not yet covered (Ring travels,
        # Wipe marches, Cross rakes, Bounce pulses along an axis, Breathe
        # pulses radially).
        174: fixvals_for("floor_breathe_near", [0, 1, 2, 3]),  # Floor Breathe Step 1
        175: fixvals_for("floor_breathe_far", [0, 1, 2, 3]),   # Floor Breathe Step 2
        # Dark-move routines (2026-07-26). Teleport/Apparition share these 8
        # scenes (dark travel step, then lit hold step, per TELEPORT_SEQ
        # pose) -- Apparition is a chaser-only reuse, no scenes of its own.
        147: unison_step(TELEPORT_SEQ[0], False),  # Teleport Step 1 (dark -> ball)
        148: unison_step(TELEPORT_SEQ[0], True),   # Teleport Step 2 (lit @ ball)
        149: unison_step(TELEPORT_SEQ[1], False),  # Teleport Step 3 (dark -> zenith)
        150: unison_step(TELEPORT_SEQ[1], True),   # Teleport Step 4 (lit @ zenith)
        151: unison_step(TELEPORT_SEQ[2], False),  # Teleport Step 5 (dark -> walls)
        152: unison_step(TELEPORT_SEQ[2], True),   # Teleport Step 6 (lit @ walls)
        153: unison_step(TELEPORT_SEQ[3], False),  # Teleport Step 7 (dark -> crowd)
        154: unison_step(TELEPORT_SEQ[3], True),   # Teleport Step 8 (lit @ crowd)
        157: freeze_step(0),  # Freeze Frame Step 1
        158: freeze_step(1),  # Freeze Frame Step 2
        159: freeze_step(2),  # Freeze Frame Step 3
        160: freeze_step(3),  # Freeze Frame Step 4
        162: unison_step(STUTTER_SEQ[0], False),  # Stutter Step 1 (dark -> wave_up)
        163: unison_step(STUTTER_SEQ[0], True),   # Stutter Step 2 (lit @ wave_up)
        164: unison_step(STUTTER_SEQ[1], False),  # Stutter Step 3 (dark -> wave_down)
        165: unison_step(STUTTER_SEQ[1], True),   # Stutter Step 4 (lit @ wave_down)
        # Ascension (2026-07-30): same unison_step() construction as Teleport,
        # climbing crowd -> ball -> apex -> zenith. Glitch (its own new
        # Chaser, not listed here) reuses Teleport's 147-154 verbatim instead
        # of needing scenes of its own.
        227: unison_step(ASCENSION_SEQ[0], False),  # Ascension Step 1 (dark -> crowd)
        228: unison_step(ASCENSION_SEQ[0], True),   # Ascension Step 2 (lit @ crowd)
        229: unison_step(ASCENSION_SEQ[1], False),  # Ascension Step 3 (dark -> ball)
        230: unison_step(ASCENSION_SEQ[1], True),   # Ascension Step 4 (lit @ ball)
        231: unison_step(ASCENSION_SEQ[2], False),  # Ascension Step 5 (dark -> apex)
        232: unison_step(ASCENSION_SEQ[2], True),   # Ascension Step 6 (lit @ apex)
        233: unison_step(ASCENSION_SEQ[3], False),  # Ascension Step 7 (dark -> zenith)
        234: unison_step(ASCENSION_SEQ[3], True),   # Ascension Step 8 (lit @ zenith)
        # Blink (2026-07-30): floor_ring_0..3 as a dark-travel/lit-arrive
        # cycle -- same points Floor Ring sweeps, no new geometry.
        236: unison_step(BLINK_SEQ[0], False),  # Blink Step 1 (dark -> floor_ring_0)
        237: unison_step(BLINK_SEQ[0], True),   # Blink Step 2 (lit @ floor_ring_0)
        238: unison_step(BLINK_SEQ[1], False),  # Blink Step 3 (dark -> floor_ring_1)
        239: unison_step(BLINK_SEQ[1], True),   # Blink Step 4 (lit @ floor_ring_1)
        240: unison_step(BLINK_SEQ[2], False),  # Blink Step 5 (dark -> floor_ring_2)
        241: unison_step(BLINK_SEQ[2], True),   # Blink Step 6 (lit @ floor_ring_2)
        242: unison_step(BLINK_SEQ[3], False),  # Blink Step 7 (dark -> floor_ring_3)
        243: unison_step(BLINK_SEQ[3], True),   # Blink Step 8 (lit @ floor_ring_3)
        # Cross Weave's two steps (2026-07-30): was hand-picked absolute DMX,
        # now the diagonal pairs alternating between their two WALL-GRAZE
        # bearings (scan_prev/scan_next -- see compute_poses' wall-graze note),
        # so the four beams rake the four walls and pinwheel past each other.
        24: {i: poses[i]["scan_prev" if i % 2 == 0 else "scan_next"]
             for i in range(4)},                        # Heads Cross A
        25: {i: poses[i]["scan_next" if i % 2 == 0 else "scan_prev"]
             for i in range(4)},                        # Heads Cross B
        # Lazy Circle (179-186) -- the small ambient orbit, and the show's
        # signature Idle look. 8 steps so the crossfaded motion reads as a
        # circle rather than a diamond.
        **{179 + k: orbit_step("ring_small", k) for k in range(BALL_ORBIT_STEPS)},
        # Grand Sweep (187-194) -- the same machinery at GRAND_ORBIT_RADIUS_DEG,
        # so its horizontal extremes land on the wall-graze bearings.
        **{187 + k: orbit_step("ring_big", k) for k in range(BALL_ORBIT_STEPS)},
        # Slow Sweep (195-198) -- horizontal rake through the ball, pairs opposed.
        **{195 + k: sweep_step(k) for k in range(4)},
        # Wall Graze (199): held static, the one pose that puts a beam down the
        # full length of each wall instead of into the head's own corner.
        199: fixvals_for("scan_next", [0, 1, 2, 3]),
        # Canopy Ring (200-203): Floor Ring's mirror on the canopy plane, with
        # the same rotating per-head assignment so the pools carousel.
        **{200 + k: {i: poses[i][f"canopy_ring_{(i + k) % 4}"] for i in range(4)}
           for k in range(4)},
    }
    return TARGETS


def function_ids_for_pose(targets, head_i, pose_val):
    """Which QLC+ Function IDs (from a `build_targets()` dict) actually carry
    `pose_val` (a poses[i][pose_name] 2-tuple) for head `head_i` -- used to
    tell a human/UI which on-screen buttons a flagged pose in
    unreachable_poses() actually affects. Matches on the leading (pan, tilt)
    pair only, since dimmer-tagged entries (Prowl, Crowd Cascade, etc.) append
    a 3rd element the pose value itself never has."""
    return sorted(
        fid for fid, fixvals in targets.items()
        if head_i in fixvals and tuple(fixvals[head_i][:2]) == pose_val
    )


def apply_to_qxw(path=QXW):
    """Regex-replace the FixtureVal lines of the pose-dependent scenes in place,
    using whichever MOUNT_MODE is currently active. Only touches Pan/Tilt
    (channels 0-3); leaves everything else in each Function block untouched.
    Function IDs must already exist in the file."""
    _self_test()
    poses = compute_poses()
    TARGETS = build_targets(poses)

    text = open(path, encoding="utf-8").read()
    changed = []
    missing = []
    for fid, fixvals in TARGETS.items():
        pat = re.compile(
            rf'(<Function ID="{fid}" Type="Scene"[^>]*>\s*<Speed[^/]*/>)\s*'
            rf'((?:<FixtureVal ID="\d+">[^<]*</FixtureVal>\s*)+)'
            rf'(</Function>)')
        m = pat.search(text)
        if not m:
            missing.append(fid)
            continue
        # Emit the whitespace explicitly rather than carrying the old file's
        # indentation through group(1). The previous pattern absorbed the
        # newline+indent before the first FixtureVal into group(1) and then
        # prepended its own -- so every run added 3 more spaces, which is how
        # the file ended up with 96-space indents (and why commit 9ccb291
        # exists). Canonical form here matches what QLC+ itself writes on
        # save: 3 spaces for children, 2 for the closing tag.
        new_vals = "\n".join(
            f'   <FixtureVal ID="{fxid}">{_fixture_val(*_unpack_fixval(val))}</FixtureVal>'
            for fxid, val in sorted(fixvals.items())
        )
        text = (text[:m.start()] + m.group(1) + "\n" + new_vals
                + "\n  " + m.group(3) + text[m.end():])
        changed.append(fid)

    # Fail loud BEFORE writing anything. A missing pose function means the
    # workspace is out of sync with this script (a routine got renamed/removed,
    # or IDs shifted). The old behavior printed a WARNING and wrote the file
    # anyway -- silently shipping a stale pose with an exit code of 0. Refuse
    # instead: despacio.qxw is left exactly as it was, and the GUI's
    # non-zero-exit error dialog fires. (Monitor FxItem lookups below keep
    # their softer per-item warning -- those are previz-only, not show output.)
    if missing:
        raise SystemExit(
            f"ERROR: pose function ID(s) {missing} not found in the workspace.\n"
            f"       despacio.qxw was NOT modified. Reconcile the workspace with "
            f"aim_calc.py's TARGETS and rerun.")

    # ---- Monitor 3D preview: keep FxItem position/rotation/inversion in
    # sync with the same model driving the DMX values above. QLC+'s 3D
    # view (qmlui/mainview3d.cpp + engine/src/monitorproperties.cpp) reads
    # YRot (mounting yaw, degrees) off each <FxItem>, and treats the mere
    # PRESENCE of InvertedPan/InvertedTilt attributes as those flags being
    # set.
    # KNOWN PREVIZ LIMITATION in "venue" mode: QLC+'s engine has no idea Pan
    # is carrying elevation data and Tilt is carrying bearing data there -- it
    # always treats its Pan channel as yaw and Tilt channel as pitch. So the
    # 3D view's *motion* will look wrong in a more fundamental way than a
    # simple anchor mismatch. Per the standing 2026-07-22 decision, this is
    # accepted as a previz-only limitation -- do not chase it. YRot still
    # gives a rough positional/orientation reference regardless of mode.
    fx_changed = []
    pan_invert_list = PAN_INVERT[MOUNT_MODE]
    tilt_invert_list = TILT_INVERT[MOUNT_MODE]
    for i, (hx, hz) in enumerate(HEADS):
        yrot = poses[i]["mount_facing"]
        inv_attrs = ""
        if pan_invert_list[i]:
            inv_attrs += ' InvertedPan="1"'
        if tilt_invert_list[i]:
            inv_attrs += ' InvertedTilt="1"'
        new_fx = (f'<FxItem ID="{i}" XPos="{hx:g}" YPos="{HEAD_HEIGHT[i]:g}" ZPos="{hz:g}" '
                  f'YRot="{yrot:.1f}"{inv_attrs}/>')
        fx_pat = re.compile(rf'<FxItem ID="{i}"[^/]*/>')
        text, n = fx_pat.subn(new_fx, text)
        if n == 0:
            print(f"WARNING: Monitor FxItem ID {i} not found, skipped")
        else:
            fx_changed.append(i)

    # Auto-backup the current (pre-change) despacio.qxw before overwriting it.
    # The project's only undo beyond git is these copies, and a bad config/run
    # would otherwise be unrecoverable on venue day. The on-disk file is still
    # the original at this point (we've only mutated `text` in memory), so this
    # captures the pre-change state. Keep the newest ~20, prune older.
    backup_dir = Path(path).with_name("backups")
    backup_dir.mkdir(exist_ok=True)
    backup_path = backup_dir / f"despacio-{datetime.now():%Y%m%d-%H%M%S}.qxw"
    shutil.copy2(path, backup_path)
    for old in sorted(backup_dir.glob("despacio-*.qxw"))[:-20]:
        old.unlink()

    open(path, "w", encoding="utf-8", newline="\n").write(text)
    print(f"Backup of previous workspace: {backup_path}")
    print(f"Recalibrated functions: {changed}")
    print(f"Recalibrated Monitor FxItems (position/YRot/invert): {fx_changed}")
    for i, p in poses.items():
        tag = "" if CALIBRATED_BALL_DMX[MOUNT_MODE][i] is not None else "  [UNCALIBRATED -- geometry guess only]"
        print(f"  Head {i+1}: ball={p['ball']} floor={p['floor']} crowd={p['crowd']} "
              f"mount_facing={p['mount_facing']:.1f}deg{tag}")

    # ---- Pose reachability report ---------------------------------------
    # Which poses got clamped/rounded away from their geometric target under
    # THIS run's mode + calibration, and which on-screen buttons each one
    # actually feeds -- printed here every run, and written to
    # aim_report.json so preflight.py and the phone UI
    # (webui/gen_webui_config.py) can surface the same information without
    # recomputing it. See unreachable_poses()'s docstring for what this is
    # and isn't (a reachability check, not a math correctness check).
    unreachable = unreachable_poses(MOUNT_MODE, poses)
    for entry in unreachable:
        entry["functionIds"] = function_ids_for_pose(
            TARGETS, entry["head"], poses[entry["head"]][entry["pose"]])

    print("\nPOSE REACHABILITY:")
    if not unreachable:
        print("  all poses reachable within tolerance")
    else:
        for e in unreachable:
            tag = " [known/expected]" if e["known"] else ""
            print(f"  Head {e['head']+1} {e['pose']}: {e['severity'].upper()}{tag} -- "
                  f"bearing off by {e['errorBearingDeg']:.1f}deg, elev off by "
                  f"{e['errorElevDeg']:.1f}deg (functions {e['functionIds']})")

    AIM_REPORT_PATH.write_text(
        json.dumps({"mode": MOUNT_MODE, "qxw": str(path), "unreachable": unreachable},
                   indent=2) + "\n",
        encoding="utf-8")

    # Loud active-mode banner -- printed LAST so it's the final thing on screen.
    # The #1 venue-day footgun is going live in the wrong mode (still 'table'
    # from bench testing) or in 'venue' mode with heads not yet calibrated.
    mode_label = {
        "table": "BENCH TEST MODE",
        "venue": "REAL VENUE INSTALL (sideways mount)",
        "hung": "REAL VENUE INSTALL (upside-down hang)",
    }.get(MOUNT_MODE, "UNRECOGNIZED MODE")
    uncal = [i + 1 for i in range(4) if CALIBRATED_BALL_DMX[MOUNT_MODE][i] is None]
    print("\n" + "=" * 56)
    print(f"  MOUNT_MODE = {MOUNT_MODE!r}   <-- {mode_label}")
    if uncal:
        print(f"  WARNING: heads {uncal} UNCALIBRATED in {MOUNT_MODE!r} mode "
              f"-- calibrate before the show")
    print("=" * 56)


if __name__ == "__main__":
    apply_to_qxw()
