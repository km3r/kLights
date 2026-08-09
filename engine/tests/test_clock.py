"""
Tests for the musical clock and the motion primitives.

The central one is PHASE CONTINUITY: changing tempo, nudging speed, or swapping
source must not move the current beat position. If it does, every running look
jumps at once -- which is a worse problem than the wrong tempo you were fixing,
and it is the failure the whole anchor design exists to prevent.

The second is that motion is genuinely continuous. A stepped chase slowed down
gets steppier; a path slowed down gets smoother. That is the difference the
night's "some routines were too fast or slow" complaint actually needs, so it is
asserted rather than assumed.

Run: python engine/tests/test_clock.py
"""

import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import clock as clockmod
from engine import motion
from engine import state as statemod
from engine.rig import load_rig
from engine.runner import Runner

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# -- 1. the timeline runs at the tempo ----------------------------------------
print("\n1. free-running timeline")
c = clockmod.MasterClock(bpm=120.0, now=0.0)
check("120 bpm advances 2 beats per second", abs(c.beat(1.0) - 2.0) < 1e-9,
      f"{c.beat(1.0):.6f}")
check("4 beats is one bar", abs(c.position(2.0).bar - 1.0) < 1e-9,
      f"bar {c.position(2.0).bar:.4f}")
check("32 beats is one phrase", abs(c.position(16.0).phrase - 1.0) < 1e-9,
      f"phrase {c.position(16.0).phrase:.4f}")
check("beat is cumulative, never wrapped", c.beat(60.0) > 100,
      f"{c.beat(60.0):.1f} beats after a minute")
p = c.position(2.5)
check("wrapped views are available", abs(p.beat_in_bar - 1.0) < 1e-9,
      f"beat {p.beat:.2f} -> beat_in_bar {p.beat_in_bar:.2f}")


# -- 2. PHASE CONTINUITY ------------------------------------------------------
#
# Every mutator, checked the same way: read the position, mutate, read it again
# at the same instant. Any difference is a jump every running look would show.
print("\n2. phase continuity across every mutation")


def continuity(label, mutate, bpm=128.0, at=7.3):
    c = clockmod.MasterClock(bpm=bpm, now=0.0)
    before = c.beat(at)
    mutate(c, at)
    after = c.beat(at)
    check(f"{label} does not move the beat", abs(after - before) < 1e-9,
          f"{before:.9f} -> {after:.9f}")
    return c


continuity("set_bpm", lambda c, t: c.set_bpm(140.0, t))
continuity("set_speed", lambda c, t: c.set_speed(0.5, t))
continuity("nudge_speed", lambda c, t: c.nudge_speed(2.0, t))
continuity("sync with a new bpm", lambda c, t: c.sync(t, bpm=132.0))
continuity("stop", lambda c, t: c.stop(t))
continuity("attaching a manual source",
           lambda c, t: clockmod.attach(c, clockmod.ManualSource(150.0), t))
continuity("attaching a tap source",
           lambda c, t: clockmod.attach(c, clockmod.TapSource(), t))

# ...and the rate really did change afterwards, or continuity would be trivial.
c = clockmod.MasterClock(bpm=120.0, now=0.0)
c.set_bpm(240.0, 5.0)
check("after set_bpm the rate has actually changed",
      abs((c.beat(6.0) - c.beat(5.0)) - 4.0) < 1e-9,
      f"{c.beat(6.0) - c.beat(5.0):.4f} beats in the next second")

c = clockmod.MasterClock(bpm=120.0, now=0.0)
c.set_speed(0.5, 5.0)
check("speed halves the rate without changing the tempo",
      abs((c.beat(6.0) - c.beat(5.0)) - 1.0) < 1e-9 and c.bpm == 120.0,
      f"bpm {c.bpm}, effective {c.effective_bpm}")


# -- 3. tap tempo -------------------------------------------------------------
print("\n3. tap tempo")
c = clockmod.MasterClock(bpm=100.0, now=0.0)
interval = 60.0 / 128.0
for k in range(8):
    c.tap(10.0 + k * interval)
check("eight steady taps find the tempo", abs(c.bpm - 128.0) < 0.5,
      f"{c.bpm:.2f} bpm")
check("a tap lands on a beat",
      abs(c.beat(10.0 + 7 * interval) - round(c.beat(10.0 + 7 * interval))) < 1e-9)

# The median is what makes this survive a real hand. One badly late tap should
# barely move the estimate; under a mean it drags it several bpm.
c = clockmod.MasterClock(bpm=100.0, now=0.0)
times, t = [], 10.0
for k in range(8):
    t += interval * (2.2 if k == 4 else 1.0)      # one very late tap
    times.append(t)
for t in times:
    c.tap(t)
check("one wild tap barely moves the estimate", abs(c.bpm - 128.0) < 4.0,
      f"{c.bpm:.2f} bpm with a 2.2x outlier")

c = clockmod.MasterClock(bpm=100.0, now=0.0)
c.tap(1.0)
c.tap(1.0 + interval)
before = c.taps
c.tap(1.0 + interval + 5.0)                       # long gap: a new set
check("a long gap starts a new tap set", c.taps < before + 1,
      f"{before} taps -> {c.taps}")


# -- 4. phase nudge and downbeat ----------------------------------------------
print("\n4. phase nudge and downbeat")
c = clockmod.MasterClock(bpm=120.0, now=0.0)
before = c.beat(10.0)
c.nudge_phase(0.25, 10.0)
check("nudge_phase shifts position", abs(c.beat(10.0) - before - 0.25) < 1e-9)
check("nudge_phase leaves the tempo alone", c.bpm == 120.0)

c = clockmod.MasterClock(bpm=120.0, now=0.0)
c.set_downbeat(5.3)
check("set_downbeat lands on a bar",
      abs(c.position(5.3).beat_in_bar) < 1e-9,
      f"beat {c.beat(5.3):.4f}")
# It snaps to the NEAREST bar rather than resetting: resetting would also reset
# the phrase counter, shunting every upcoming phrase-boundary look change.
check("set_downbeat does not reset the phrase counter", c.beat(5.3) > 8.0,
      f"beat {c.beat(5.3):.2f} after 5.3 s at 120 bpm")


# -- 5. Pro DJ Link is a documented seam, not a silent stub -------------------
print("\n5. unimplemented source fails loudly")
c = clockmod.MasterClock(now=0.0)
try:
    clockmod.attach(c, clockmod.ProLinkSource(), 0.0)
    check("ProLinkSource refuses to pretend", False, "attached silently")
except NotImplementedError as exc:
    check("ProLinkSource refuses to pretend", "not implemented" in str(exc).lower())


# -- 6. motion is continuous --------------------------------------------------
#
# The point of the whole module: slowing a move down must make it SMOOTHER, not
# steppier. Sampling the same path over twice the bars must halve the largest
# per-frame change -- which a stepped chase cannot do, since its steps stay the
# same size and merely take longer.
print("\n6. motion is continuous, and slowing down smooths it")
poses = [(0.0, 0.0), (45.0, 10.0), (0.0, 20.0), (-45.0, 10.0)]


def largest_step(fn, bars, fps=40.0, bpm=120.0, beats_per_bar=4):
    """Biggest movement between consecutive OUTPUT FRAMES for one cycle.

    Sampled at a fixed frame rate, so a longer cycle gets proportionally more
    frames -- which is the actual situation. Sampling a fixed number of points
    per cycle instead would compare the same phase steps at both speeds and
    could never show the difference.
    """
    seconds = bars * beats_per_bar * 60.0 / bpm
    frames = int(seconds * fps)
    values = [fn(motion.phase(k / frames * bars, bars)) for k in range(frames + 1)]
    return max(math.dist(a, b) for a, b in zip(values, values[1:]))


p = motion.path(poses)
fast = largest_step(p, 4.0)
slow = largest_step(p, 8.0)
check("a path never jumps", fast < 5.0, f"largest step {fast:.3f} deg")
check("halving the speed halves the per-frame movement",
      abs(slow * 2 - fast) < fast * 0.15,
      f"4 bars {fast:.3f} deg/frame, 8 bars {slow:.3f} deg/frame")

# The contrast the module exists for: the same route as a STEPPED chase moves
# in the same total distance but delivers it in a handful of jumps, and slowing
# it down does not shrink them -- it just spaces them further apart.
def stepped(p_):
    return poses[min(int(p_ * len(poses)), len(poses) - 1)]


stepped_fast = largest_step(stepped, 4.0)
stepped_slow = largest_step(stepped, 8.0)
check("a stepped chase jumps, and slowing it down does not help",
      stepped_slow >= stepped_fast * 0.95 and stepped_fast > fast * 10,
      f"stepped {stepped_fast:.1f} -> {stepped_slow:.1f} deg/frame vs "
      f"path {fast:.3f} -> {slow:.3f}")

# The loop point is a seam, and a path that jumps there is a path that visibly
# snaps once per cycle.
p_closed = motion.path(poses, closed=True)
wrap = math.dist(p_closed(0.9999), p_closed(0.0))
check("a closed path joins up at the loop point", wrap < 0.5,
      f"{wrap:.4f} deg across the seam")

p_open = motion.path(poses, closed=False)
check("an open path ends on its last pose",
      math.dist(p_open(1.0 - 1e-9), poses[-1]) < 0.1,
      f"{p_open(1.0 - 1e-9)}")

o = motion.orbit(20.0, bars=8.0)
check("an orbit stays on its radius",
      all(abs(math.hypot(*o(k / 200)) - 20.0) < 1e-6 for k in range(200)))
o_flat = motion.orbit(20.0, elongation=2.0)
check("elongation flattens elevation, not bearing",
      abs(max(abs(o_flat(k / 200)[1]) for k in range(200)) - 10.0) < 1e-6)

ch = motion.chase(4, width=0.25)
lit = [ch(i, 0.0) for i in range(4)]
check("a chase lights one fixture at its own phase",
      lit[0] == 1.0 and all(v == 0.0 for v in lit[1:]), f"{lit}")


# -- 7. the runner drives musical position ------------------------------------
print("\n7. the runner keeps the context in musical time")
rig = load_rig(REPO / "events" / "despacio")
ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
show = statemod.Show()
show.base.append(statemod.pose_layer(lambda c, h: c.geometry.aim_at_ball(h)))
show.movement.append(statemod.move_layer(
    motion.as_move(motion.orbit(15.0), bars=8.0)))

c = clockmod.MasterClock(bpm=120.0, now=0.0)
runner = Runner(ctx=ctx, show=show, clock=c)
stats = runner.run(seconds=1.0)
check("beat advanced over the run", ctx.beat > 1.5, f"beat {ctx.beat:.3f}")
check("bar and phrase follow the beat",
      abs(ctx.bar - ctx.beat / 4) < 1e-9 and abs(ctx.phrase - ctx.beat / 32) < 1e-9)
check("bpm is reported on the context", ctx.bpm == 120.0, f"{ctx.bpm}")
check("frames still on time", stats.drops == 0 and stats.eval_errors == 0,
      f"{stats.frames} frames, {stats.effective_fps:.2f} fps")

# With no clock at all the show holds still rather than inventing a tempo.
still = statemod.EvalContext(rig=rig, venue=rig.venue)
Runner(ctx=still, show=show).run(seconds=0.2)
check("no clock means no musical motion", still.beat == 0.0 and still.bar == 0.0)


# -- crossfading between two shows --------------------------------------------
#
# A look change used to be a hard cut on the frame it landed. The engine holds
# parameters, so a fade can blend the parameters rather than the DMX -- which is
# what makes it smooth rather than a dissolve between two quantised frames.
print("\n7. crossfade")
import engine.state as statemod
from engine import geometry as geo                                    # noqa: E402
from engine.rig import load_rig                                    # noqa: E402

fade_rig = load_rig(REPO / "events" / "despacio")
fctx = statemod.EvalContext(rig=fade_rig, venue=fade_rig.venue)


def flat_show(level, bearing, elev):
    show = statemod.Show()
    show.base.append(statemod.pose_layer(
        lambda c, head, b=bearing, e=elev: geo.Aim(b, e)))
    show.base.append(statemod.on_layer(level))
    return show


dim = flat_show(0.0, 0.0, 40.0)
bright = flat_show(1.0, 60.0, 40.0)
fid = fade_rig.movers[0].fid

ends = [statemod.evaluate_crossfade(fctx, dim, bright, t) for t in (0.0, 1.0)]
check("t=0 is the outgoing show", abs(ends[0][fid].intensity - 0.0) < 1e-9,
      f"{ends[0][fid].intensity}")
check("t=1 is the incoming show", abs(ends[1][fid].intensity - 1.0) < 1e-9,
      f"{ends[1][fid].intensity}")

mid = statemod.evaluate_crossfade(fctx, dim, bright, 0.5)
check("intensity blends", abs(mid[fid].intensity - 0.5) < 1e-6,
      f"{mid[fid].intensity:.4f}")
check("the aim blends in degrees, not in DMX",
      abs(mid[fid].aim.bearing_delta - 30.0) < 1e-6,
      f"{mid[fid].aim.bearing_delta:.4f}")

# The reason bearing_delta is unwrapped servo rotation: a head crossing the wrap
# has to travel the way it physically can, not teleport through the short way.
wide = statemod.evaluate_crossfade(fctx, flat_show(1.0, 170.0, 40.0),
                                   flat_show(1.0, -170.0, 40.0), 0.5)
check("a head crossing the wrap travels the long way, as a yoke must",
      abs(wide[fid].aim.bearing_delta - 0.0) < 1e-6,
      f"{wide[fid].aim.bearing_delta:.2f} deg (0 = through the middle)")

# Safety must see the BLENDED aim, once. Running it per side and blending the
# results would let a fade pass through a state neither show could produce.
steps = [statemod.evaluate_crossfade(fctx, dim, bright, k / 20.0)
         for k in range(21)]
check("every mid-fade frame carries a safety decision",
      all(s[fid].safety is not None for s in steps))
check("and none exceeds its own taper",
      all(s[fid].intensity <= s[fid].safety.taper + 1e-9 for s in steps))


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("clock and motion: all checks pass")
