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
  False per head here -- sideways mounting has no known inversion requirement
  (the upside-down "invert both" reasoning is a different transform, doesn't
  carry over).

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

QXW = r"C:\Users\maxti\Documents\code\cosmos\lights\despacio\despacio.qxw"
CONFIG_PATH = Path(__file__).with_name("despacio_config.json")

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
    "calibrated_ball_dmx": {
        "table": [[22, 0], [127, 0], [22, 0], [127, 0]],
        "venue": [None, None, None, None],
    },
    "mount_facing_override": [None, None, None, None],
    "pan_invert": {
        "table": [True, True, True, True],
        "venue": [False, False, False, False],
    },
    "tilt_invert": {
        # NOT inverted in "table" mode: confirmed on hardware 2026-07-22
        # (turning the Tilt knob up from the calibrated point moves the beam
        # up). The "simulate upside-down" flip of the elevation axis is carried
        # by MOUNT_PROFILES["table"]["world_flip_elevation"], NOT by this flag
        # -- see the PAN_INVERT/TILT_INVERT comment and MOUNT_PROFILES below.
        "table": [False, False, False, False],
        "venue": [False, False, False, False],
    },
}


def load_config(path=CONFIG_PATH):
    """Loads despacio_config.json, merging over DEFAULT_CONFIG per top-level
    key so a config file missing newer keys doesn't break. Creates the file
    from DEFAULT_CONFIG if it doesn't exist yet."""
    if not path.exists():
        path.write_text(json.dumps(DEFAULT_CONFIG, indent=2) + "\n", encoding="utf-8")
        return json.loads(json.dumps(DEFAULT_CONFIG))
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    merged = json.loads(json.dumps(DEFAULT_CONFIG))
    merged.update(on_disk)
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
#   "venue": both default False -- sideways mounting has no known inversion
#     requirement (a different transform from upside-down); confirm/flip per
#     head via Corner Test once installed.
PAN_INVERT = _cfg["pan_invert"]
TILT_INVERT = _cfg["tilt_invert"]

PAN_MAX = 540.0   # from the .qxf Physical/Focus -- Pan channel's mechanical range
TILT_MAX = 270.0  # Tilt channel's mechanical range

WAVE_ELEV_SWING = 20.0  # degrees above/below the ball's elevation for Ball Wave

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
        # is needed only for elevation, not bearing).
        elevation_invert = invert_for_channel[profile["elevation_channel"]] != profile["world_flip_elevation"]

        cal = cal_list[i]
        if cal is not None:
            # Back out this head's true mount angle + elevation zero-error
            # from the one empirically-verified "aimed at the ball" point,
            # instead of assuming it matches the assumed mounting convention.
            cal_pan_dmx, cal_tilt_dmx = cal
            dmx_for_channel = {"pan": cal_pan_dmx, "tilt": cal_tilt_dmx}
            achieved_bearing_delta = _from_dmx_centered(
                dmx_for_channel[profile["bearing_channel"]], profile["bearing_max"], bearing_invert)
            achieved_elev = _decode_elev(
                dmx_for_channel[profile["elevation_channel"]], profile["elevation_max"],
                elevation_invert, profile["elevation_anchor"])
            mount_facing = bearing_to_ball - achieved_bearing_delta
            elevation_offset = achieved_elev - elev_to_ball_geom
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
            elevation_offset = 0.0
            # No exact delta to preserve here -- mount_facing is just a raw
            # angle guess, so reducing to the shortest real-world rotation is
            # the sensible (and only sensible) choice.
            bearing_delta_ball = _norm(bearing_to_ball - mount_facing)

        def _encode(bearing_delta, elev_target):
            bearing_dmx = _to_dmx_centered(bearing_delta, profile["bearing_max"], bearing_invert)
            elev_dmx = _encode_elev(elev_target + elevation_offset, profile["elevation_max"],
                                     elevation_invert, profile["elevation_anchor"])
            channel_val = {profile["bearing_channel"]: bearing_dmx, profile["elevation_channel"]: elev_dmx}
            return (channel_val["pan"], channel_val["tilt"])

        # Ball: look at the mirrorball
        ball = _encode(bearing_delta_ball, elev_to_ball_geom)

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
        floor = _encode(bearing_delta_f, elev_f)

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
        walls = _encode(bearing_delta_walls, 0)

        # Crowd (NEW 2026-07-22, replaces the old outward meaning): point
        # straight down at the people on the dancefloor below this head.
        # Bearing doesn't meaningfully matter pointing straight down, so it
        # just keeps Ball's -- only elevation changes, all the way down.
        crowd = _encode(bearing_delta_ball, -90)

        # Orbit +-90: quarter-turn either side of Ball, same elevation as
        # Ball. Building blocks for the Ball Spiral chaser (each head sweeps
        # through Ball / Orbit+90 / Walls / Orbit-90 in turn, phase-offset
        # from the others so they take turns grazing the ball). Deliberately
        # not range-fitted like Walls above -- these are stylistic sweep
        # positions, not a validated reference to preserve exactly, so
        # ordinary clamping if a head's calibration pushes one near the edge
        # is an acceptable, graceful outcome.
        orbit_p90 = _encode(bearing_delta_ball + 90, elev_to_ball_geom)
        orbit_m90 = _encode(bearing_delta_ball - 90, elev_to_ball_geom)

        # Wave up/down: same bearing as Ball (beam stays pointed at the
        # ball's compass direction), elevation nudged a fixed swing above/
        # below the ball's own elevation. Building blocks for the Ball Wave
        # chaser -- a gentle bob, phase-offset per head like Ball Spiral, so
        # the up/down motion ripples across the 4 heads rather than moving
        # in lockstep. Same "ordinary clamping is fine" reasoning as orbit
        # +-90 above: a stylistic offset from a validated reference, not a
        # reference to preserve exactly itself.
        wave_up = _encode(bearing_delta_ball, elev_to_ball_geom + WAVE_ELEV_SWING)
        wave_down = _encode(bearing_delta_ball, elev_to_ball_geom - WAVE_ELEV_SWING)

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
        circle = _encode(bearing_delta_circle, elev_circle)

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
        scan_prev = _encode(bearing_delta_scan_prev, elev_to_ball_geom)
        scan_next = _encode(bearing_delta_scan_next, elev_to_ball_geom)

        poses[i] = {"ball": ball, "floor": floor, "walls": walls, "crowd": crowd,
                    "orbit_p90": orbit_p90, "orbit_m90": orbit_m90,
                    "wave_up": wave_up, "wave_down": wave_down,
                    "circle": circle, "scan_prev": scan_prev, "scan_next": scan_next,
                    "mount_facing": mount_facing}
    return poses


def _fixture_val(pan_dmx, tilt_dmx):
    return f"0,{pan_dmx},1,0,2,{tilt_dmx},3,0"


def build_scene_block(fid, name, fixvals, fadein=3000, fadeout=0):
    lines = [f'  <Function ID="{fid}" Type="Scene" Name="{name}">',
             f'   <Speed FadeIn="{fadein}" FadeOut="{fadeout}" Duration="0"/>']
    for fxid, (pan_dmx, tilt_dmx) in fixvals.items():
        lines.append(f'   <FixtureVal ID="{fxid}">{_fixture_val(pan_dmx, tilt_dmx)}</FixtureVal>')
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
            expect_greater = profile["world_flip_elevation"]  # table flips down->up
            ok = floor_e > ball_e if expect_greater else floor_e < ball_e
            assert ok, (
                f"mode={mode} head={i}: Floor elevation-channel DMX {floor_e} is "
                f"not {'>' if expect_greater else '<'} Ball's {ball_e} "
                f"(Floor target elev {elev_floor:.1f}deg vs Ball {elev_ball:.1f}deg "
                f"-- elevation invert/world-flip is wrong; this is the bug #3 regression)")

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
                               "circle", "scan_prev", "scan_next"):
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


def apply_to_qxw(path=QXW):
    """Regex-replace the FixtureVal lines of the pose-dependent scenes in place,
    using whichever MOUNT_MODE is currently active. Only touches Pan/Tilt
    (channels 0-3); leaves everything else in each Function block untouched.
    Function IDs must already exist in the file."""
    _self_test()
    poses = compute_poses()

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
    }

    text = open(path, encoding="utf-8").read()
    changed = []
    missing = []
    for fid, fixvals in TARGETS.items():
        pat = re.compile(
            rf'(<Function ID="{fid}" Type="Scene"[^>]*>\s*<Speed[^/]*/>\s*)'
            rf'((?:<FixtureVal ID="\d+">[^<]*</FixtureVal>\s*)+)'
            rf'(</Function>)')
        m = pat.search(text)
        if not m:
            missing.append(fid)
            continue
        new_vals = "\n".join(
            f'   <FixtureVal ID="{fxid}">{_fixture_val(pan_dmx, tilt_dmx)}</FixtureVal>'
            for fxid, (pan_dmx, tilt_dmx) in sorted(fixvals.items())
        ) + "\n"
        text = text[:m.start()] + m.group(1) + new_vals + "   " + m.group(3) + text[m.end():]
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

    # Loud active-mode banner -- printed LAST so it's the final thing on screen.
    # The #1 venue-day footgun is going live in the wrong mode (still 'table'
    # from bench testing) or in 'venue' mode with heads not yet calibrated.
    mode_label = "BENCH TEST MODE" if MOUNT_MODE == "table" else "REAL VENUE INSTALL"
    uncal = [i + 1 for i in range(4) if CALIBRATED_BALL_DMX[MOUNT_MODE][i] is None]
    print("\n" + "=" * 56)
    print(f"  MOUNT_MODE = {MOUNT_MODE!r}   <-- {mode_label}")
    if uncal:
        print(f"  WARNING: heads {uncal} UNCALIBRATED in {MOUNT_MODE!r} mode "
              f"-- calibrate before the show")
    print("=" * 56)


if __name__ == "__main__":
    apply_to_qxw()
