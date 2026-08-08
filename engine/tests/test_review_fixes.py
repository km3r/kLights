"""
Regression guards for the F3-F5 code review (2026-08-06).

Each block names the finding it exists for. They are grouped here rather than
scattered into the per-module suites because what they have in common is that
none of them produced a wrong number -- they were latent traps, and a trap needs
a test more than a visible bug does.

Run: python engine/tests/test_review_fixes.py
"""

import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import geometry as geo
from engine import rig
from engine import state as statemod
from engine.rig import load_rig
from engine.runner import Runner

EVENT = REPO / "events" / "despacio"
failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def event_copy(tmp: str, **edits) -> Path:
    """A throwaway event folder, optionally with edited JSON.

    The venue is copied in as the event's own `venue.json` and the `venue` key
    is dropped from the copied rig, so the temp event is self-contained however
    the real one resolves its room. That also keeps this exercising the
    per-event fallback, which is the path an event written before shared venues
    existed still takes.
    """
    d = Path(tmp)
    for name in ("rig.json", "calibration.json"):
        shutil.copy(EVENT / name, d / name)
    shutil.copy(rig.venue_path(EVENT, json.loads(
        (EVENT / "rig.json").read_text(encoding="utf-8"))), d / "venue.json")
    rig_cfg = json.loads((d / "rig.json").read_text(encoding="utf-8"))
    rig_cfg.pop("venue", None)
    (d / "rig.json").write_text(json.dumps(rig_cfg, indent=2), encoding="utf-8")

    for name, mutate in edits.items():
        path = d / name
        data = json.loads(path.read_text(encoding="utf-8"))
        mutate(data)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return d


# -- 1. a calibration measured in another mount mode must not load ------------
#
# The readings are mode-specific. The failure is invisible without this check:
# the back-solve is self-consistent in any mode, so the ball keeps aiming
# perfectly while every other pose is silently wrong.
print("\n1. mount_mode mismatch between rig.json and calibration.json")
with tempfile.TemporaryDirectory() as tmp:
    d = event_copy(tmp, **{"calibration.json":
                           lambda c: c.__setitem__("mount_mode", "table")})
    try:
        load_rig(d)
        check("mismatch is rejected", False, "loaded without complaint")
    except ValueError as exc:
        check("mismatch is rejected", "not interchangeable" in str(exc))
        check("the error names both modes",
              "'venue'" in str(exc) and "'table'" in str(exc), str(exc)[:90])

with tempfile.TemporaryDirectory() as tmp:
    d = event_copy(tmp, **{"calibration.json": lambda c: c.pop("mount_mode", None)})
    try:
        load_rig(d)
        check("a calibration with no declared mode still loads", True)
    except ValueError as exc:
        check("a calibration with no declared mode still loads", False, str(exc))


# -- 2. a measured channel range can be recorded ------------------------------
#
# The solver can determine that a head claiming 540 deg of Pan really does 500.
# Before this there was nowhere to put that, so it was a finding the system
# could not act on.
print("\n2. rig.json can override the .qxf's declared channel range")
with tempfile.TemporaryDirectory() as tmp:
    def shrink(rig_cfg):
        rig_cfg["fixtures"][0]["pan_range_deg"] = 500.0
    d = event_copy(tmp, **{"rig.json": shrink})
    overridden = load_rig(d)
    stock = load_rig(EVENT)

    check("override reaches the head",
          overridden.geometry.heads[0].pan_range_deg == 500.0,
          f"{overridden.geometry.heads[0].pan_range_deg}")
    check("other heads keep the profile's value",
          overridden.geometry.heads[1].pan_range_deg == stock.geometry.heads[1].pan_range_deg)

    # And it must actually change the aiming, or recording it would be theatre.
    # In "venue" Pan carries elevation, so a different Pan range moves the
    # elevation encoding of any aim away from the calibrated ball point.
    bx, _by, bz = overridden.venue.ball
    a = overridden.geometry.encode(0, overridden.geometry.aim_at_point(0, bx, 0.0, bz))
    b = stock.geometry.encode(0, stock.geometry.aim_at_point(0, bx, 0.0, bz))
    check("the override changes where the head aims", a != b,
          f"500 deg -> {geo.split16(a[0])[0]}, 540 deg -> {geo.split16(b[0])[0]} (pan)")


# -- 3. layer callbacks get a consistent, meaningful argument -----------------
#
# intensity_layer used to pass the position in the FILTERED list, which looks
# like a head index and is not: the pinspots arrived as 0 and 1 while their head
# indices do not exist at all.
print("\n3. intensity_layer passes the fixture, not a filtered-list index")
rig = load_rig(EVENT)
ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
seen = []
show = statemod.Show()
show.base.append(statemod.on_layer(1.0))
show.fx.append(statemod.intensity_layer(
    lambda c, fixture: (seen.append(fixture), 1.0)[1], tags=("pinspots",)))
statemod.evaluate(ctx, show)

check("callback receives PatchedFixture objects",
      all(hasattr(f, "fid") and hasattr(f, "head") for f in seen),
      f"{[type(f).__name__ for f in seen]}")
check("they are the tagged fixtures, by real id",
      [f.fid for f in seen] == [f.fid for f in rig.by_tag("pinspots")],
      f"{[f.fid for f in seen]}")
check("non-movers report no head index",
      all(f.head is None for f in seen))

# The mover-only layers keep passing a head index, which is the documented
# split. Guard that too, so a later tidy-up does not "unify" them wrongly.
head_args = []
show2 = statemod.Show()
show2.base.append(statemod.pose_layer(
    lambda c, h: (head_args.append(h), c.geometry.aim_at_ball(h))[1]))
statemod.evaluate(ctx, show2)
check("pose_layer still passes head indices", head_args == [0, 1, 2, 3],
      f"{head_args}")


# -- 4. a show bug and a timing miss are counted separately -------------------
#
# They need opposite responses -- fix the look, versus fix the machine -- so one
# counter covering both cannot answer the question you actually have at 2am.
print("\n4. evaluation errors are not counted as dropped frames")


def explode(ctx_, out):
    raise RuntimeError("deliberate show bug")


broken = statemod.Show()
broken.base.append(statemod.pose_layer(lambda c, h: c.geometry.aim_at_ball(h)))
broken.fx.append(explode)

runner = Runner(ctx=statemod.EvalContext(rig=rig, venue=rig.venue), show=broken)
stats = runner.run(seconds=0.5)
check("evaluation errors counted", stats.eval_errors > 0, f"{stats.eval_errors}")
check("not counted as drops", stats.drops == 0, f"{stats.drops}")
check("the traceback is kept for the operator",
      runner.last_error is not None and "deliberate show bug" in runner.last_error)
check("frames kept flowing despite the bug", stats.frames > 10, f"{stats.frames}")

# A broken show must hold the last good frame, not go black -- a wrong colour is
# recoverable mid-set, a dead room is a stopped show.
good = statemod.Show()
good.base.append(statemod.pose_layer(lambda c, h: c.geometry.aim_at_ball(h)))
good.color.append(statemod.color_layer((1.0, 1.0, 1.0)))
runner2 = Runner(ctx=statemod.EvalContext(rig=rig, venue=rig.venue), show=good)
first = runner2.render_once()
runner2.show = broken
after = runner2.render_once()
check("last good frame is held on evaluation failure", after == first,
      f"eval_errors={runner2.stats.eval_errors}")
check("the held frame is not blank", any(any(f) for f in after.values()))


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("review fixes: all checks pass")
