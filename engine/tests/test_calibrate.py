"""
Tests for the multi-point calibration solver.

The central claim is that three captures at known targets recover a head's
transform INCLUDING its invert flags -- the thing a single ball reading cannot
determine, and which shipped wrong once already. So the main test synthesises
captures from a head whose signs are known, hands them to the solver with those
signs withheld, and checks it finds them.

Captures are generated at 8-bit resolution because that is what an operator
dials, which puts up to one coarse step -- 2.1 deg on a 540 deg Pan -- of
quantisation into every reading. That is real, not test noise, so the
tolerances here are the tolerances the field gets.

Run: python engine/tests/test_calibrate.py
"""

import math
import sys
import tempfile
from datetime import datetime
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import calibrate, geometry as geo
from engine.rig import load_rig

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


rig = load_rig(REPO / "events" / "despacio")
venue = rig.venue
BALL = venue.ball
ROOM_W, ROOM_D = venue.width, venue.depth


def synth_captures(head: geo.Head, mount_mode: str, targets) -> list[calibrate.Capture]:
    """What a perfect operator would record, aiming this head at each target.

    Encoded through the engine's own geometry and then truncated to 8 bits,
    which is what a fader gives you.
    """
    truth = geo.RigGeometry(heads=(head,), ball=BALL, mount_mode=mount_mode)
    out = []
    for label, target in targets:
        pan16, tilt16 = truth.encode(0, truth.aim_at_point(0, *target))
        out.append(calibrate.Capture(target=target,
                                     pan=geo.split16(pan16)[0],
                                     tilt=geo.split16(tilt16)[0],
                                     label=label))
    return out


# Three targets spanning both bearing and elevation, which is what the solver
# needs: the ball (up and central), a floor corner well off to one side, and a
# wall point at head height. This is the capture set the operator would be
# talked through.
def spread_targets(head):
    far_x = 400.0 if head.x > ROOM_W / 2 else ROOM_W - 400.0
    far_z = 400.0 if head.z > ROOM_D / 2 else ROOM_D - 400.0
    return [("ball", BALL),
            ("far floor corner", (far_x, 0.0, far_z)),
            ("wall at head height", (far_x, head.height, ROOM_D / 2))]


# -- 1. the solver recovers known signs ---------------------------------------
print("\n1. sign recovery from three captures")
recovered = 0
attempted = 0
worst_residual = 0.0
worst_ball_delta = 0

for mount_mode in ("table", "venue", "hung"):
    for pan_invert in (False, True):
        for tilt_invert in (False, True):
            for base in rig.geometry.heads:
                truth_head = geo.Head(**{**base.__dict__,
                                         "calibrated_ball_dmx": None,
                                         "pan_invert": pan_invert,
                                         "tilt_invert": tilt_invert})
                # Give it a real mount facing and elevation error to find, so
                # this is not just recovering zeros.
                truth_rig = geo.RigGeometry(heads=(truth_head,), ball=BALL,
                                            mount_mode=mount_mode)
                ball_pan, ball_tilt = truth_rig.encode(0, truth_rig.aim_at_ball(0))
                truth_head = geo.Head(**{**truth_head.__dict__,
                                         "calibrated_ball_dmx": (
                                             geo.split16(ball_pan)[0],
                                             geo.split16(ball_tilt)[0])})

                captures = synth_captures(truth_head, mount_mode,
                                          spread_targets(truth_head))
                blind = geo.Head(**{**truth_head.__dict__,
                                    "calibrated_ball_dmx": None,
                                    "pan_invert": False, "tilt_invert": False})
                solution = calibrate.solve_head(blind, BALL, mount_mode, captures)

                attempted += 1
                signs_ok = (solution.pan_invert == pan_invert
                            and solution.tilt_invert == tilt_invert)
                ball_delta = max(
                    abs(solution.ball_dmx[0] - truth_head.calibrated_ball_dmx[0]),
                    abs(solution.ball_dmx[1] - truth_head.calibrated_ball_dmx[1]))
                if signs_ok and ball_delta <= 2:
                    recovered += 1
                else:
                    if attempted <= 200 and not signs_ok:
                        print(f"    miss: {mount_mode} {truth_head.name} "
                              f"pan={pan_invert} tilt={tilt_invert} -> "
                              f"pan={solution.pan_invert} tilt={solution.tilt_invert} "
                              f"(residual {solution.residual_deg:.2f})")
                worst_residual = max(worst_residual, solution.residual_deg)
                worst_ball_delta = max(worst_ball_delta, ball_delta)

check("every sign combination recovered", recovered == attempted,
      f"{recovered}/{attempted}")
check("ball reading reproduced within quantisation", worst_ball_delta <= 2,
      f"worst {worst_ball_delta} DMX")
check("residual stays inside 8-bit quantisation", worst_residual < 3.0,
      f"worst {worst_residual:.2f} deg")


# -- 2. the solver refuses to guess when the captures cannot answer -----------
#
# The ball and the floor directly beneath it share a bearing exactly, so these
# two captures say nothing about which way the head turns. A confident answer
# here would be a coin flip presented as a measurement.
print("\n2. ambiguous captures are reported, not guessed")
head = rig.geometry.heads[0]
ambiguous = synth_captures(head, "venue", [
    ("ball", BALL),
    ("floor under ball", (BALL[0], 0.0, BALL[2])),
])
solution = calibrate.solve_head(head, BALL, "venue", ambiguous)
check("bearing sign flagged as undetermined",
      not solution.determined.get("bearing_sign", True),
      f"spread {solution.bearing_spread_deg:.1f} deg")
check("a warning explains what to capture next",
      any("off to one side" in w for w in solution.warnings),
      solution.warnings[0] if solution.warnings else "no warning")
check("solution is marked unconfident", not solution.confident)

try:
    calibrate.solve_head(head, BALL, "venue", ambiguous[:1])
    check("one capture is rejected", False, "no error raised")
except ValueError as exc:
    check("one capture is rejected", "at least 2 captures" in str(exc))


# -- 3. a wrong channel range is recoverable ----------------------------------
#
# The .qxf claims 540 deg of Pan. A fixture that really does 520 calibrates
# perfectly at the ball and drifts further off the further an aim gets from it,
# which reads as random inaccuracy rather than a systematic error.
print("\n3. channel scale fitted from a wide capture spread")
TRUE_PAN = 500.0
NOMINAL_PAN = 540.0
base = rig.geometry.heads[0]
real = geo.Head(**{**base.__dict__, "pan_range_deg": TRUE_PAN,
                   "calibrated_ball_dmx": None})
# "hung" puts bearing on Pan, so the wide 540 deg channel is the one being
# scaled -- and the corners give ~90 deg of bearing spread to fit against.
wide = [("ball", BALL),
        ("front-left floor", (400.0, 0.0, 400.0)),
        ("front-right floor", (ROOM_W - 400.0, 0.0, 400.0)),
        ("back-left floor", (400.0, 0.0, ROOM_D - 400.0))]
captures = synth_captures(real, "hung", wide)
nominal = geo.Head(**{**base.__dict__, "pan_range_deg": NOMINAL_PAN,
                      "calibrated_ball_dmx": None})
solution = calibrate.solve_head(nominal, BALL, "hung", captures)

fitted_range = solution.bearing_scale * NOMINAL_PAN
check("scale fit lands near the true range", abs(fitted_range - TRUE_PAN) < 25.0,
      f"fitted {fitted_range:.0f} deg, true {TRUE_PAN:.0f}, "
      f"nominal {NOMINAL_PAN:.0f}")
check("scale was actually attempted", solution.determined.get("scale") is True,
      f"spread {solution.bearing_spread_deg:.0f} deg")

narrow = calibrate.solve_head(nominal, BALL, "hung", captures[:2])
check("scale not fitted from too few captures",
      narrow.bearing_scale == 1.0 and not narrow.determined.get("scale", True))


# -- 4. drift is reported in degrees ------------------------------------------
print("\n4. drift against a stored calibration")
head = rig.geometry.heads[0]
stored = head.calibrated_ball_dmx
nudged = (stored[0] + 4, stored[1] - 3)
d = calibrate.drift_for_head(head, BALL, "venue", nudged)
print(f"  {d.head_name}: {d.bearing_deg:+.2f} deg bearing, "
      f"{d.elevation_deg:+.2f} deg elevation "
      f"(DMX {d.pan_dmx:+d}/{d.tilt_dmx:+d})")
check("drift is non-zero and finite", 0.0 < d.magnitude < 90.0,
      f"magnitude {d.magnitude:.2f} deg")

same = calibrate.drift_for_head(head, BALL, "venue", stored)
check("no drift against itself", same.magnitude < 1e-9)

# Degrees per DMX step is a property of the fixture and mount mode, so four of
# the same head in the same mode share it exactly -- equal nudges give equal
# MAGNITUDES, and that is correct. What degrees add is the SIGN: heads 2 and 4
# are mounted mirrored, so in "venue" (where Pan carries elevation) the same DMX
# nudge tips them the opposite real-world way. DMX hides that; degrees show it.
nudged_all = [calibrate.drift_for_head(h, BALL, "venue",
                                       (h.calibrated_ball_dmx[0] + 4,
                                        h.calibrated_ball_dmx[1] + 4))
              for h in rig.geometry.heads]
elev_signs = [math.copysign(1.0, d.elevation_deg) for d in nudged_all]
print("  same +4 DMX nudge on every head: "
      + " ".join(f"{d.elevation_deg:+.2f}" for d in nudged_all) + " deg elevation")
check("mirrored heads tip the opposite way for the same nudge",
      len(set(elev_signs)) == 2,
      f"signs {elev_signs}, inverts "
      f"{[h.pan_invert for h in rig.geometry.heads]}")
check("magnitudes match across identical fixtures",
      max(d.magnitude for d in nudged_all) - min(d.magnitude for d in nudged_all) < 1e-9,
      "degrees per step is a fixture property, not a per-head one")


# -- 5. snapshots and diffing -------------------------------------------------
print("\n5. snapshots make an overnight nudge a delta")
with tempfile.TemporaryDirectory() as tmp:
    event = Path(tmp)
    last_night = {"measured": "2026-08-01", "mount_mode": "venue", "heads": [
        {"fixture": h.name, "ball_dmx": list(h.calibrated_ball_dmx),
         "pan_invert": h.pan_invert, "tilt_invert": h.tilt_invert}
        for h in rig.geometry.heads]}
    this_morning = {"measured": "2026-08-02", "mount_mode": "venue", "heads": [
        {"fixture": h.name,
         "ball_dmx": [h.calibrated_ball_dmx[0] + (5 if i == 2 else 0),
                      h.calibrated_ball_dmx[1]],
         "pan_invert": h.pan_invert, "tilt_invert": h.tilt_invert}
        for i, h in enumerate(rig.geometry.heads)]}

    p1 = calibrate.save_snapshot(event, last_night, note="load-in",
                                 when=datetime(2026, 8, 1, 18, 0, 0))
    p2 = calibrate.save_snapshot(event, this_morning, note="after the nudge",
                                 when=datetime(2026, 8, 2, 9, 30, 0))
    check("snapshots written", p1.exists() and p2.exists())

    history = calibrate.load_snapshots(event)
    check("history is newest first", len(history) == 2 and history[0][0] > history[1][0],
          f"{[h[0] for h in history]}")

    # Two snapshots in the SAME SECOND. `solve --write` does exactly this --
    # archive the old calibration, then the new one -- and second-resolution
    # filenames collided, so the second overwrote the first and the "before"
    # snapshot vanished. Then the suffixed name sorted BEFORE the unsuffixed one
    # ('-' is below '.'), so the diff ran backwards.
    same_second = datetime(2026, 8, 3, 12, 0, 0)
    a = calibrate.save_snapshot(event, last_night, note="first", when=same_second)
    b = calibrate.save_snapshot(event, this_morning, note="second", when=same_second)
    check("same-second snapshots do not clobber", a != b and a.exists() and b.exists(),
          f"{a.name} / {b.name}")

    collided = [h for h in calibrate.load_snapshots(event)
                if h[1].get("note") in ("first", "second")]
    check("same-second snapshots order by serial, not by filename",
          [h[1]["note"] for h in collided] == ["second", "first"],
          f"{[h[0] for h in collided]}")

    drifts = calibrate.diff_calibrations(rig.geometry.heads, BALL, "venue",
                                         last_night, this_morning)
    moved = [d for d in drifts if d.significant]
    for d in drifts:
        marker = "MOVED" if d.significant else "     "
        print(f"  {marker} {d.head_name:<16} {d.bearing_deg:+6.2f} deg bearing, "
              f"{d.elevation_deg:+6.2f} deg elevation")
    check("exactly the nudged head is flagged",
          len(moved) == 1 and moved[0].head_name == rig.geometry.heads[2].name,
          f"{[d.head_name for d in moved]}")


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("calibrate: all checks pass")
