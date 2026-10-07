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

import contextlib
import io
import json
import math
import shutil
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


# -- 6. the load-in workflow, from the command line ---------------------------
# Every step an operator runs at a load-in before the web form existed, in the
# order they run it, against a throwaway copy of the event: the CLI writes
# calibration.json and archives snapshots, and neither belongs in the real show.
print("\n6. the load-in workflow from the command line")


def check_cli(label, ok, output=""):
    """check(), with a command's output attached only when it failed -- a passing
    check that prints a screen of CLI output buries the next failure."""
    check(label, ok, "" if ok else output[-400:])


def cli(*argv) -> tuple[int, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            code = calibrate.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue() + err.getvalue()


with tempfile.TemporaryDirectory() as tmp:
    event = Path(tmp) / "despacio"
    shutil.copytree(REPO / "events" / "despacio", event,
                    ignore=shutil.ignore_patterns("__pycache__", "calibration_history",
                                                  "*.bak", ".engine.lock"))
    ev = ["--event", str(event)]
    heads = rig.geometry.heads
    stored = {h.name: tuple(h.calibrated_ball_dmx) for h in heads}

    code, out = cli(*ev, "status")
    check_cli("status names every head with its stored ball reading and inverts",
          code == 0 and all(h.name in out and str(stored[h.name]) in out for h in heads)
          and "0 snapshot(s)" in out, out[-300:])

    code, out = cli(*ev, "jog", "--head", "1", "--pan", "100", "--tilt", "50",
                    "--seconds", "0.2")
    check_cli("jog holds one head (to a null output here) and says the taper is bypassed",
          code == 0 and f"holding {heads[1].name} at pan=100 tilt=50" in out
          and "BYPASSED" in out, out[-300:])
    code, out = cli(*ev, "jog", "--head", "9", "--pan", "0", "--tilt", "0")
    check_cli("jog refuses a head index the rig does not have", code == 2 and "0..3" in out, out)

    code, out = cli(*ev, "solve", "--example")
    template = json.loads(out)
    check("solve --example prints a captures template: three targets per head, "
          "pre-filled from the venue",
          code == 0 and set(template["heads"]) == {h.name for h in heads}
          and all(len(v) == 3 for v in template["heads"].values())
          and template["heads"][heads[0].name][0]["target"] == list(BALL))
    code, out = cli(*ev, "solve")
    check("solve with no file says what to give it", code == 2 and "--example" in out)

    # What a careful operator would record: the stored calibration's own aims,
    # through the engine's geometry, at 8-bit resolution. One head left out, so
    # the "keep what is stored" path runs too.
    captures = {"mount_mode": "venue", "heads": {
        h.name: [c.to_json() for c in synth_captures(h, "venue", spread_targets(h))]
        for h in heads[:3]}}
    cap_file = Path(tmp) / "captures.json"
    cap_file.write_text(json.dumps(captures), encoding="utf-8")
    before = (event / "calibration.json").read_text(encoding="utf-8")

    code, out = cli(*ev, "solve", str(cap_file))
    check_cli("solve without --write reports per head and writes nothing",
          code == 0 and "nothing written" in out and "residual" in out
          and (event / "calibration.json").read_text(encoding="utf-8") == before, out[-300:])
    check("a head with no captures keeps its stored calibration, and says so",
          f"{heads[3].name}: no captures, keeping stored calibration" in out)

    code, out = cli(*ev, "solve", str(cap_file), "--write")
    written = json.loads((event / "calibration.json").read_text(encoding="utf-8"))
    solved = {e["fixture"]: e for e in written["heads"]}
    check("solve --write replaces calibration.json, naming where it came from",
          code == 0 and written["source"] == "solved from captures.json"
          and written["mount_mode"] == "venue" and set(solved) == {h.name for h in heads})
    worst = max(max(abs(a - b) for a, b in zip(solved[h.name]["ball_dmx"], stored[h.name]))
                for h in heads)
    check("and recovers the calibration the captures were made from, within one "
          "coarse DMX step per axis", worst <= 1, f"worst {worst} steps")
    check("including every head's inverts",
          all(solved[h.name]["pan_invert"] == h.pan_invert
              and solved[h.name]["tilt_invert"] == h.tilt_invert for h in heads))
    snaps = calibrate.load_snapshots(event)
    check("having archived the calibration before AND after, newest first",
          [s_[1].get("note") for s_ in snaps] == ["after solve", "before solve"],
          f"{[s_[1].get('note') for s_ in snaps]}")

    code, out = cli(*ev, "history")
    check_cli("history lists both snapshots", code == 0 and out.count("solve") == 2, out)

    code, out = cli(*ev, "diff")
    check_cli("diff of the latest two: a faithful re-solve moves nothing significantly",
          code == 0 and "0 head(s) moved significantly" in out, out[-300:])

    code, out = cli(*ev, "snapshot", "--note", "doors")
    check("snapshot archives the current calibration with a note",
          code == 0 and calibrate.load_snapshots(event)[0][1].get("note") == "doors")

    # The overnight check: re-read each head at the ball and compare.
    code, out = cli(*ev, "drift", *[f"{p},{t}" for p, t in stored.values()])
    check_cli("drift with this morning's readings unchanged: nothing moved, exit 0",
          code == 0 and "0 head(s) moved" in out and out.count("[  ok ]") == 4, out[-300:])
    knocked = [f"{p},{t}" for p, t in stored.values()]
    p2, t2 = stored[heads[2].name]
    knocked[2] = f"{p2 + 6},{t2}"
    code, out = cli(*ev, "drift", *knocked)
    check_cli("a knocked head is flagged MOVED and the exit code says so",
          code == 1 and "1 head(s) moved" in out and f"[MOVED] {heads[2].name}" in out,
          out[-300:])
    code, out = cli(*ev, "drift", "1,2")
    check_cli("drift with the wrong number of readings is refused", code == 2
          and "expected 4 readings, got 1" in out, out)

    empty = Path(tmp) / "fresh"
    empty.mkdir()
    (empty / "calibration_history").mkdir()
    check("an event with no snapshots has an empty history",
          calibrate.load_snapshots(empty) == [])
    (empty / "calibration_history" / "2026-08-01T00-00-00.json").write_text(
        "{ truncated", encoding="utf-8")
    check("and a corrupt snapshot is skipped rather than fatal",
          calibrate.load_snapshots(empty) == [])
    one = Path(tmp) / "one"
    shutil.copytree(event, one, ignore=shutil.ignore_patterns("calibration_history"))
    code, out = cli("--event", str(one), "diff")
    check_cli("diff with fewer than two snapshots says it needs two", code == 2
          and "two snapshots" in out, out)
    code, out = cli("--event", str(one), "history")
    check("history with none says where it looked", code == 0 and "no snapshots" in out)


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("calibrate: all checks pass")
