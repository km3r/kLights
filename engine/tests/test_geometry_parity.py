"""Prove engine/geometry.py aims where events/despacio/aim_calc.py aimed.

The engine's geometry is a port, and the thing a port has to earn is that it
did not quietly change the show. This drives every named pose in aim_calc's
vocabulary -- 47 per head, across all three mount modes -- through the engine's
primitives instead, and compares the recovered AIM ANGLES, each side decoded by
its own code at its own resolution.

Angles, not DMX bytes, because the engine encodes at 16 bits: its value is the
floor of aim_calc's rounded 8-bit one with the remainder in the fine byte, so
the coarse bytes legitimately differ by one. Agreement to within a single 8-bit
step means the same aim, at the finest resolution aim_calc could express.

This test is EXPECTED TO BE DELETED once QLC+ is retired and aim_calc.py goes
with it -- at that point engine/geometry.py's own self-test is the guard. Until
then it is the evidence that the two agree. It skips rather than fails if
aim_calc is not importable, so archiving that event does not break the suite.

Run: python engine/tests/test_geometry_parity.py
"""
import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent   # lights/
DESPACIO = REPO / "events" / "despacio"

sys.path.insert(0, str(REPO))
sys.path.insert(0, str(DESPACIO))

from engine import geometry as new

if not (DESPACIO / "aim_calc.py").is_file():
    print(f"SKIP: {DESPACIO / 'aim_calc.py'} is gone -- QLC+ retired, "
          f"so there is nothing left to compare against.")
    raise SystemExit(0)

import aim_calc as old

ROOM = old.ROOM_WIDTH
FLOOR_X, FLOOR_Z = old.FLOOR_X, old.FLOOR_Z

fails, checks = [], 0
worst = [0.0, 0.0, ""]


def make_old_decoder(mode, i):
    """Decode a pair of aim_calc 8-bit values back to (bearing_delta, elev),
    using aim_calc's OWN functions and globals -- not the engine's -- so the
    comparison is genuinely independent."""
    prof = old.MOUNT_PROFILES[mode]
    bch, ech = prof["bearing_channel"], prof["elevation_channel"]
    bmax, emax = prof["bearing_max"], prof["elevation_max"]
    binv = (old.PAN_INVERT if bch == "pan" else old.TILT_INVERT)[mode][i]
    einv = old._elevation_invert(mode, i)

    def decode(pan8, tilt8):
        v = {"pan": pan8, "tilt": tilt8}
        return (old._from_dmx_centered(v[bch], bmax, binv),
                old._decode_elev(v[ech], emax, einv, prof["elevation_anchor"]))
    return decode, bmax, emax


def compare(mode, head, label, want, got, decode_old, bmax, emax, rig, i):
    """want = aim_calc (pan8, tilt8); got = engine (pan16, tilt16).

    Compared as ANGLES, each side decoded by its own code at its own
    resolution. The engine's 16-bit value is the floor of aim_calc's rounded
    8-bit one with the remainder in the fine byte, so the coarse bytes
    legitimately differ by one; the recovered aim must not.
    """
    global checks
    checks += 1
    b_old, e_old = decode_old(*want)
    f = rig.frame(i)
    aim_new = rig.decode(i, *got)
    b_new, e_new = aim_new.bearing_delta, aim_new.elev_deg + f.elevation_offset

    db = abs(new.norm180(b_new - b_old))
    de = abs(e_new - e_old)
    if db > worst[0]:
        worst[0], worst[2] = db, f"{mode} head{head} {label}"
    worst[1] = max(worst[1], de)

    # One 8-bit step on each channel: that is the resolution aim_calc could
    # express at all, so agreeing to within it means the same aim.
    if db > bmax / 255.0 or de > emax / 255.0:
        fails.append(f"{mode} head{head} {label}: aim_calc=({b_old:.3f},{e_old:.3f}) "
                     f"engine=({b_new:.3f},{e_new:.3f}) deg  "
                     f"delta=({db:.3f},{de:.3f})")


for mode in ("table", "venue", "hung"):
    old_poses = old.compute_poses(mode=mode)
    rig = new.despacio_reference_rig(mode)
    n = len(rig.heads)
    up = rig.fit_elev_extreme(+1)
    down = rig.fit_elev_extreme(-1)
    # The original fits its extremes the same way; confirm before using them.
    assert abs(up - old._fit_elev_extreme(mode, +1)) < 1e-9, (mode, "up extreme")
    assert abs(down - old._fit_elev_extreme(mode, -1)) < 1e-9, (mode, "down extreme")

    for i in range(n):
        f = rig.frame(i)
        p = old_poses[i]
        nb, prv = rig.heads[(i + 1) % n], rig.heads[(i - 1) % n]
        bx, by, bz = rig.ball

        cases = {
            "ball": rig.aim_at_ball(i),
            "floor": rig.aim_at_point(i, FLOOR_X, 0.0, FLOOR_Z),
            "walls": new.Aim(rig.half_turn(i), 0.0),
            "crowd": new.Aim(f.bearing_delta_ball, -down),
            "zenith": new.Aim(f.bearing_delta_ball, up),
            "sky_out": new.Aim(rig.half_turn(i), 45.0),
            "apex": rig.aim_at_point(i, bx, old.APEX_HEIGHT, bz),
            "circle": rig.aim_at_point(i, nb.x, nb.height, nb.z),
            "scan_next": rig.aim_offset(
                i, new.norm180(new.bearing_between(rig.heads[i].x, rig.heads[i].z,
                                                   nb.x, nb.z) - f.bearing_to_ball), 0.0),
            "scan_prev": rig.aim_offset(
                i, new.norm180(new.bearing_between(rig.heads[i].x, rig.heads[i].z,
                                                   prv.x, prv.z) - f.bearing_to_ball), 0.0),
            "orbit_p90": rig.aim_offset(i, 90.0, 0.0),
            "orbit_m90": rig.aim_offset(i, -90.0, 0.0),
            "orbit_p45": rig.aim_offset(i, 45.0, 0.0),
            "orbit_m45": rig.aim_offset(i, -45.0, 0.0),
            "wave_up": rig.aim_offset(i, 0.0, old.WAVE_ELEV_SWING),
            "wave_down": rig.aim_offset(i, 0.0, -old.WAVE_ELEV_SWING),
            "floor_cross_next": rig.aim_at_point(i, nb.x, 0.0, nb.z),
            "floor_cross_prev": rig.aim_at_point(i, prv.x, 0.0, prv.z),
        }

        # Ball orbits: bearing = R sin(theta), elevation = R cos(theta)
        for ring, radius in (("ring_small", old.LAZY_ORBIT_RADIUS_DEG),
                             ("ring_big", old.GRAND_ORBIT_RADIUS_DEG)):
            for k in range(old.BALL_ORBIT_STEPS):
                th = math.radians(360.0 * k / old.BALL_ORBIT_STEPS)
                cases[f"{ring}_{k}"] = rig.aim_offset(
                    i, radius * math.sin(th), radius * math.cos(th))

        # Floor ring and canopy ring: points on a circle at two heights
        for ring_i, ang in enumerate((0, 90, 180, 270)):
            rad = math.radians(ang)
            cases[f"floor_ring_{ring_i}"] = rig.aim_at_point(
                i, FLOOR_X + old.FLOOR_SWEEP_RADIUS * math.sin(rad), 0.0,
                FLOOR_Z + old.FLOOR_SWEEP_RADIUS * math.cos(rad))
            cases[f"canopy_ring_{ring_i}"] = rig.aim_at_point(
                i, bx + old.CANOPY_SWEEP_RADIUS * math.sin(rad), old.APEX_HEIGHT,
                bz + old.CANOPY_SWEEP_RADIUS * math.cos(rad))

        # Wipe: each head keeps its own x lane, only z changes
        lane_order = sorted(range(n), key=lambda h: (old.HEADS[h][0], old.HEADS[h][1]))
        lane_x = FLOOR_X + old.FLOOR_SWEEP_RADIUS * ((lane_order.index(i) / 1.5) - 1.0)
        for label, fz in (("wipe_front", FLOOR_Z - old.FLOOR_SWEEP_RADIUS),
                          ("wipe_mid", FLOOR_Z),
                          ("wipe_back", FLOOR_Z + old.FLOOR_SWEEP_RADIUS)):
            cases[label] = rig.aim_at_point(i, lane_x, 0.0, fz)

        # Breathe: fixed compass angle, radius grows and shrinks
        brad = math.radians(90 * i)
        for label, frac in (("floor_breathe_near", old.FLOOR_BREATHE_NEAR_FRAC),
                            ("floor_breathe_far", old.FLOOR_BREATHE_FAR_FRAC)):
            r = old.FLOOR_SWEEP_RADIUS * frac
            cases[label] = rig.aim_at_point(i, FLOOR_X + r * math.sin(brad), 0.0,
                                            FLOOR_Z + r * math.cos(brad))

        decode_old, bmax, emax = make_old_decoder(mode, i)
        for label, aim in cases.items():
            compare(mode, i, label, p[label], rig.encode(i, aim),
                    decode_old, bmax, emax, rig, i)

# --- the event config files must describe the rig the show actually ran ------
#
# rig.json / venue.json / calibration.json are a hand port of
# despacio_config.json + patch_sheet.csv. A transcription slip there would move
# a head or a calibration point without any of the checks above noticing, since
# they all run on the hardcoded reference rig.
from engine import rig as rigmod

loaded = rigmod.load_rig(DESPACIO)
reference = new.despacio_reference_rig("venue")
g = loaded.geometry

assert g is not None, "rig.json produced no geometry"
assert g.mount_mode == old.MOUNT_MODE, (
    f"rig.json mount_mode {g.mount_mode!r} != despacio_config.json's {old.MOUNT_MODE!r}")
assert g.ball == (old.BALL_X, old.BALL_HEIGHT, old.BALL_Z), (
    f"venue.json ball {g.ball} != despacio_config.json's "
    f"{(old.BALL_X, old.BALL_HEIGHT, old.BALL_Z)}")
assert len(g.heads) == len(old.HEADS), (
    f"rig.json has {len(g.heads)} positioned heads, despacio_config.json has "
    f"{len(old.HEADS)}")

for i, head in enumerate(g.heads):
    want_x, want_z = old.HEADS[i]
    assert (head.x, head.z) == (want_x, want_z), (
        f"head {i} ({head.name}) at ({head.x},{head.z}), "
        f"despacio_config.json says ({want_x},{want_z})")
    assert head.height == old.HEAD_HEIGHT[i], (
        f"head {i} height {head.height} != {old.HEAD_HEIGHT[i]}")
    assert head.calibrated_ball_dmx == old.CALIBRATED_BALL_DMX["venue"][i], (
        f"head {i} calibration {head.calibrated_ball_dmx} != "
        f"{old.CALIBRATED_BALL_DMX['venue'][i]}")
    assert head.pan_invert == old.PAN_INVERT["venue"][i], f"head {i} pan_invert"
    assert head.tilt_invert == old.TILT_INVERT["venue"][i], f"head {i} tilt_invert"
    # ...and the derived frame must match, which is the assertion that actually
    # matters: identical inputs are only interesting if they aim identically.
    assert loaded.geometry.encode(i, g.aim_at_ball(i)) == \
        reference.encode(i, reference.aim_at_ball(i)), (
        f"head {i} ball aim differs between rig.json and the reference rig")

patch_errors = loaded.validate()
assert not patch_errors, f"rig.json fails validation: {patch_errors}"

print(f"config port: rig.json + venue.json + calibration.json reproduce "
      f"despacio_config.json ({len(g.heads)} heads)")

print(f"compared {checks} aims across 3 mount modes x 4 heads")
print(f"worst disagreement: {worst[0]:.4f} deg bearing ({worst[2]}), "
      f"{worst[1]:.4f} deg elevation")
if fails:
    print(f"\n{len(fails)} MISMATCH(ES):")
    for line in fails[:40]:
        print("  " + line)
    sys.exit(1)
print("PARITY: every aim agrees with aim_calc.py to within one 8-bit step")
