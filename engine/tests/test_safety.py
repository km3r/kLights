"""
Tests for the beam-aware intensity taper.

The three the plan asks for -- a beam in the crowd zone reports zero, a beam
just outside reports full, and a sweep THROUGH the zone tapers rather than
flashing -- plus the two cases that decide whether the taper is usable at all:
the mirror ball must occlude (or the signature look dies), and a beam that never
enters the head band must not be touched.

Run: python engine/tests/test_safety.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import geometry as geo
from engine import safety
from engine.rig import load_rig

rig = load_rig(REPO / "events" / "despacio")
g, venue = rig.geometry, rig.venue
assert g is not None and venue is not None
crowd = venue.crowd_zone

CFG = safety.TaperConfig()
CROWD = CFG.crowd_level          # what a beam over the crowd is dimmed TO
band_mid = (crowd.head_band_min + crowd.head_band_max) / 2.0
center_x, center_z = venue.width / 2, venue.depth / 2

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# -- 1. a beam aimed into the crowd is held down to crowd_level ---------------
#
# Aimed at the middle of the room at eye level. Nothing subtle: this is the
# static-pose-too-low case from the night. The goal is "not blinding" rather
# than "off", so the assertion is that it lands exactly on crowd_level -- not
# above it (no guard) and not below it (the pose family dies).
print(f"\n1. beam aimed into the crowd at head height (expect {CROWD:.2f})")
for i, head in enumerate(g.heads):
    aim = g.aim_at_point(i, center_x, band_mid, center_z)
    c = safety.clearance(g, i, aim, venue)
    check(f"{head.name} -> room centre at {band_mid:.0f} mm",
          abs(c.taper - CROWD) < 1e-9, f"taper={c.taper:.3f} ({c.reason})")


# -- 2. a beam aimed at the mirror ball is untouched ---------------------------
#
# The ball sits above the head band and blocks the beam anyway. If this ever
# tapers, the taper is unusable -- every signature look in the show points here.
print("\n2. beam aimed at the mirror ball")
for i, head in enumerate(g.heads):
    c = safety.clearance(g, i, g.aim_at_ball(i), venue)
    check(f"{head.name} -> ball", c.taper == 1.0,
          f"taper={c.taper:.3f} ({c.reason})")


# -- 3. a beam aimed well outside the crowd footprint is at full --------------
#
# Aimed at the floor in this head's own corner, which is outside the crowd
# footprint by design -- nobody stands where the heads are mounted.
print("\n3. beam aimed outside the crowd footprint")
for i, head in enumerate(g.heads):
    corner_x = 150.0 if head.x < center_x else venue.width - 150.0
    corner_z = 150.0 if head.z < center_z else venue.depth - 150.0
    c = safety.clearance(g, i, g.aim_at_point(i, corner_x, 0.0, corner_z), venue)
    check(f"{head.name} -> own corner floor", c.taper == 1.0,
          f"taper={c.taper:.3f} ({c.reason})")


# -- 4. a beam aimed straight up is untouched ---------------------------------
print("\n4. beam aimed above the head band")
for i, head in enumerate(g.heads):
    f = g.frame(i)
    c = safety.clearance(g, i, geo.Aim(f.bearing_delta_ball, 80.0), venue)
    check(f"{head.name} -> near-zenith", c.taper == 1.0,
          f"taper={c.taper:.3f} ({c.reason})")


# -- 5. a sweep through the zone tapers, and never flashes --------------------
#
# THE test. A scene-based rig can only vet its stored poses; the transit between
# them is where most of the damage happened. Sweeping one head across the room
# at head height, the taper must reach 0 in the middle, come back to 1 at the
# ends, and change smoothly -- a large jump between adjacent frames is a flash,
# which is both ugly and a sign the guard is a mask wearing a taper's clothes.
print("\n5. sweep through the crowd zone")
HEAD = 0
STEPS = 240          # 6 seconds of sweep at 40 fps
sweep = []
for k in range(STEPS + 1):
    x = -2000.0 + (venue.width + 4000.0) * k / STEPS
    aim = g.aim_at_point(HEAD, x, band_mid, center_z)
    sweep.append(safety.taper(g, HEAD, aim, venue))

check("sweep bottoms out at crowd_level", abs(min(sweep) - CROWD) < 1e-9,
      f"min={min(sweep):.3f}")
check("sweep reaches full outside it", max(sweep) == 1.0,
      f"max={max(sweep):.3f}")
check("sweep never goes below crowd_level", min(sweep) >= CROWD - 1e-9,
      f"min={min(sweep):.3f}")

partials = [t for t in sweep if CROWD < t < 1.0]
check("sweep passes through intermediate values", len(partials) >= 4,
      f"{len(partials)} frame(s) partially tapered")

biggest_step = max(abs(sweep[k + 1] - sweep[k]) for k in range(len(sweep) - 1))
check("no frame-to-frame flash", biggest_step < 0.25,
      f"largest single-frame change {biggest_step:.3f}")

# The taper must be a genuine ramp on both sides, not a cliff with one sample
# on it. Count how many frames each edge takes.
ramp_in = sum(1 for k in range(len(sweep) - 1) if sweep[k] > sweep[k + 1])
ramp_out = sum(1 for k in range(len(sweep) - 1) if sweep[k] < sweep[k + 1])
check("both edges ramp over multiple frames", ramp_in >= 2 and ramp_out >= 2,
      f"in={ramp_in} frames, out={ramp_out} frames")


# -- 5b. the slew limit catches sweeps too fast for the spatial ramp ----------
#
# The spatial margin smooths an ordinary sweep. A fast one can cross the whole
# margin inside two frames, at which point the ramp reads as a step -- so the
# temporal limit is what actually delivers "smoothed" at every speed. Driven
# through the real evaluation path, since the limiter is stateful and lives
# there rather than in the pure clearance function.
print("\n5b. slew limit on a fast sweep")
from engine import state as statemod

FAST_STEPS = 20          # the same sweep in half a second instead of six
ctx = statemod.EvalContext(rig=rig, venue=venue)
positions = [-2000.0 + (venue.width + 4000.0) * k / FAST_STEPS
             for k in range(FAST_STEPS + 1)]

limited = []
for k, x in enumerate(positions):
    ctx.time = k / 40.0
    show = statemod.Show()
    show.base.append(statemod.pose_layer(
        lambda c, head, _x=x: c.geometry.aim_at_point(head, _x, band_mid, center_z)))
    states = statemod.evaluate(ctx, show)
    limited.append(states[rig.movers[HEAD].fid].safety.taper)

worst_jump = max(abs(limited[k + 1] - limited[k]) for k in range(len(limited) - 1))
allowed = ctx.taper.slew_per_second / 40.0
check("fast sweep respects the slew limit", worst_jump <= allowed + 1e-9,
      f"largest change {worst_jump:.4f} per frame, limit {allowed:.4f}")
check("fast sweep still moves", max(limited) - min(limited) > 0.01,
      f"range {min(limited):.3f}..{max(limited):.3f}")


# -- 6. floor sweeps: where the show actually put light -----------------------
#
# Not a pass/fail -- a record. Floor sweeps aim down at the dancefloor, which is
# exactly where people are, so they necessarily cross the head band on the way.
# This is the case the taper is going to affect most, and it needs to be seen
# rather than discovered live.
print("\n6. floor sweep poses (reported, not asserted)")
radius = 2500.0
dimmed = 0
total = 0
for k, (dx, dz) in enumerate(((0, radius), (radius, 0), (0, -radius), (-radius, 0))):
    tapers = []
    for i in range(len(g.heads)):
        aim = g.aim_at_point(i, center_x + dx, 0.0, center_z + dz)
        t = safety.taper(g, i, aim, venue)
        tapers.append(t)
        total += 1
        dimmed += (t < 1.0)
    print(f"  floor_ring_{k}: " + " ".join(f"{t:.2f}" for t in tapers))

# A head 2971 mm up aiming at the floor 5.8 m away descends at about 27 degrees,
# so it spends over a metre of horizontal travel inside the 1.4-2.0 m band --
# directly over the middle of the dancefloor. Every floor sweep is affected, and
# that is correct: this is the reported symptom, reproduced.
#
# The chosen policy keeps them all usable at crowd_level rather than removing
# them. Under the earlier zero-floor policy this whole family went dark.
print(f"\n  {dimmed}/{total} floor-sweep beams are dimmed, none removed "
      f"(crowd_level = {CROWD:.0%}).")
check("floor sweeps survive at crowd_level", dimmed == total,
      "every floor beam crosses the head band, as expected")


# -- 7. landing surfaces answer the parachute question ------------------------
print("\n7. where beams land")
f0 = g.frame(0)
for label, aim in (("ball", g.aim_at_ball(0)),
                   ("apex", g.aim_at_point(0, *venue.ball[:1], venue.apex_height,
                                           venue.ball[2])),
                   ("zenith", geo.Aim(f0.bearing_delta_ball, g.fit_elev_extreme(1))),
                   ("floor centre", g.aim_at_point(0, center_x, 0.0, center_z))):
    land = safety.landing(g, 0, aim, venue)
    print(f"  {label:<13} -> {land.surface:<8} at {land.distance:6.0f} mm "
          f"({land.point[0]:.0f}, {land.point[1]:.0f}, {land.point[2]:.0f})")

canopy_hits = 0
for i in range(len(g.heads)):
    aim = g.aim_at_point(i, venue.ball[0], venue.apex_height, venue.ball[2])
    if safety.landing(g, i, aim, venue).surface == "canopy":
        canopy_hits += 1
check("apex poses land on the rigged canopy", canopy_hits == len(g.heads),
      f"{canopy_hits}/{len(g.heads)} heads")


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("safety: all checks pass")
