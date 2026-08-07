"""
Tests for auto mode.

Three things carry the weight:

  * Boundary changes fire EXACTLY once per crossing, at any frame rate. Firing
    twice double-advances the set list; missing one stalls it, and both look
    like the show is broken rather than like a timing bug.
  * Varying movement rate must not jump the movement. Computing motion phase as
    rate * bar rather than integrating it would move a look 20 bars in one frame
    when the rate changes at bar 40 -- the same failure the clock's re-anchoring
    prevents, in a place where it is easy to reintroduce.
  * The axes are genuinely independent, and manual always wins.

Run: python engine/tests/test_auto.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import auto as autom
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


rig = load_rig(REPO / "events" / "despacio")


def simple_look(name):
    def make(color):
        show = statemod.Show()
        show.base.append(statemod.pose_layer(lambda c, h: c.geometry.aim_at_ball(h)))
        show.color.append(statemod.color_layer(color))
        return show
    return autom.Look(name=name, make=make)


def fresh(**cfg):
    setlist = autom.SetList([simple_look(n) for n in ("A", "B", "C")])
    palette = autom.Palette([(1, 0, 0), (0, 1, 0), (0, 0, 1)])
    return autom.AutoDirector(setlist, autom.AutoConfig(**cfg), palette)


def run_bars(director, bars, fps=40.0, bpm=120.0, phrase_measured=False):
    """Drive the director over a span of musical time at a frame rate."""
    c = clockmod.MasterClock(bpm=bpm, now=0.0)
    seconds = bars * 4 * 60.0 / bpm
    frames = int(seconds * fps)
    for k in range(frames + 1):
        director.update(c.position(k / fps), phrase_measured)
    return frames


# -- 1. boundaries fire exactly once ------------------------------------------
print("\n1. look changes fire exactly once per boundary")
# 32 bars is 4 phrases, so the span 0 -> 4.0 crosses boundaries 1, 2, 3 and 4.
d = fresh(look_changes=True, change_every_phrases=1.0)
run_bars(d, 32.0, phrase_measured=True)
check("crossing four phrase boundaries fires four times", d.changes == 4,
      f"{d.changes}")

# The count must not depend on the frame rate: a boundary is a musical event.
counts = {}
for fps in (25.0, 40.0, 120.0, 200.0):
    d = fresh(look_changes=True, change_every_phrases=1.0)
    run_bars(d, 32.0, fps=fps, phrase_measured=True)
    counts[fps] = d.changes
check("the count is the same at every frame rate",
      len(set(counts.values())) == 1, f"{counts}")

d = fresh(look_changes=True, change_every_phrases=2.0)
run_bars(d, 32.0, phrase_measured=True)
check("a 2-phrase interval halves the changes", d.changes == 2, f"{d.changes}")

# A phase nudge runs musical time backwards. That is an operator lining up with
# the track, not a musical event, and it must not advance the set list.
d = fresh(look_changes=True, change_every_phrases=1.0)
c = clockmod.MasterClock(bpm=120.0, now=0.0)
for k in range(200):
    d.update(c.position(k / 40.0), True)
before = d.changes
c.nudge_phase(-8.0, 5.0)
for k in range(200, 260):
    d.update(c.position(k / 40.0), True)
check("running time backwards does not fire a change", d.changes == before,
      f"{before} -> {d.changes}")


# -- 2. degradation when phrase is only counted -------------------------------
#
# A counted phrase drifts from a slightly-wrong downbeat, so the director lands
# changes on BARS at the equivalent interval instead. Being one bar early is a
# small error; being half a phrase out is a visible one.
print("\n2. degradation when phrase is counted rather than measured")
measured = fresh(look_changes=True, change_every_phrases=1.0)
run_bars(measured, 32.0, phrase_measured=True)
counted = fresh(look_changes=True, change_every_phrases=1.0)
run_bars(counted, 32.0, phrase_measured=False)
check("both modes still change the look",
      measured.changes > 0 and counted.changes > 0,
      f"measured {measured.changes}, counted {counted.changes}")
check("the counted mode says so in its reason",
      "counted" in counted.last_change_reason, counted.last_change_reason)
check("the measured mode names the phrase",
      "phrase" in measured.last_change_reason, measured.last_change_reason)


# -- 3. manual always wins ----------------------------------------------------
print("\n3. manual override")
d = fresh(look_changes=True, change_every_phrases=1.0)
d.select("C")
run_bars(d, 32.0, phrase_measured=True)
check("a held look survives every boundary", d.setlist.current().name == "C",
      f"now {d.setlist.current().name}, {d.changes} auto changes")
check("no auto changes happened while held", d.changes == 0, f"{d.changes}")

d.release()
run_bars(d, 32.0, phrase_measured=True)
check("releasing resumes automatic changes", d.changes > 0, f"{d.changes}")

# A manual-only look must never be reachable by a timer.
setlist = autom.SetList([simple_look("A"), simple_look("B"),
                         autom.Look("BLACKOUT", simple_look("x").make,
                                    manual_only=True)])
d = autom.AutoDirector(setlist, autom.AutoConfig(look_changes=True,
                                                 change_every_phrases=1.0))
seen = set()
c = clockmod.MasterClock(bpm=140.0, now=0.0)
for k in range(4000):
    d.update(c.position(k / 40.0), True)
    seen.add(d.setlist.current().name)
check("a manual-only look is never auto-selected", "BLACKOUT" not in seen,
      f"visited {sorted(seen)}")
check("but it can still be picked by hand",
      d.select("BLACKOUT") is not None and d.setlist.current().name == "BLACKOUT")


# -- 4. the axes are independent ----------------------------------------------
print("\n4. axes toggle independently")
d = fresh(look_changes=True, palette=False, change_every_phrases=1.0)
run_bars(d, 32.0, phrase_measured=True)
check("looks change with palette off",
      d.changes > 0 and d.palette_changes == 0,
      f"looks {d.changes}, palette {d.palette_changes}")

d = fresh(look_changes=False, palette=True, palette_every_phrases=1.0)
run_bars(d, 32.0, phrase_measured=True)
check("palette rotates with look changes off",
      d.palette_changes > 0 and d.changes == 0,
      f"looks {d.changes}, palette {d.palette_changes}")

d = fresh()
run_bars(d, 32.0, phrase_measured=True)
check("everything off changes nothing",
      d.changes == 0 and d.palette_changes == 0 and d.energy == 0.0)
check("energy off is the identity", d.rate == 1.0 and not d.strobe)


# -- 5. energy, and the rate-jump hazard --------------------------------------
print("\n5. energy response")
d = fresh(energy=True)
d.energy_source = autom.ManualEnergy(0.0)
c = clockmod.MasterClock(bpm=120.0, now=0.0)
d.update(c.position(0.0))
low = (d.energy, d.rate, d.strobe)
d.energy_source.value = 1.0
d.update(c.position(0.025))
high = (d.energy, d.rate, d.strobe)
check("energy drives rate", high[1] > low[1], f"{low[1]:.2f} -> {high[1]:.2f}")
check("strobe only at the top", not low[2] and high[2])

r = autom.EnergyResponse()
check("intensity stays inside its stated range",
      all(r.intensity[0] <= r.intensity_at(e / 20) <= r.intensity[1]
          for e in range(21)))
check("out-of-range energy is clamped",
      abs(r.rate_at(-5.0) - r.rate[0]) < 1e-9
      and abs(r.rate_at(99.0) - r.rate[1]) < 1e-9,
      f"{r.rate_at(-5.0)} .. {r.rate_at(99.0)}")

# THE hazard. Motion phase is integrated, so changing the rate mid-run moves
# the movement by one frame's worth, not by (new_rate - old_rate) * bars_so_far.
d = fresh(energy=True)
d.energy_source = autom.ManualEnergy(0.0)
c = clockmod.MasterClock(bpm=120.0, now=0.0)
for k in range(1600):                       # ~40 s in, so bar is large
    d.update(c.position(k / 40.0))
bar_now = c.position(1600 / 40.0).bar
before_phase = d.motion_bar
d.energy_source.value = 1.0                 # rate jumps 0.6 -> 1.8
d.update(c.position(1600 / 40.0 + 0.025))
jump = d.motion_bar - before_phase
check("a rate change does not jump the movement", jump < 0.05,
      f"moved {jump:.5f} bars at bar {bar_now:.1f} "
      f"(naive rate*bar would move ~{bar_now * 1.2:.0f})")
check("the rate really did change", abs(d.rate - 1.8) < 1e-9, f"{d.rate}")

# PhraseEnergy must be a ramp, and must be refused when phrase is only counted
# -- inferring build from a phrase position that is itself a guess compounds
# two guesses into a confident wrong answer.
pe = autom.PhraseEnergy()
meter = clockmod.Meter()
levels = [pe.level(clockmod.Position(beat=b * 4, bar=b, phrase=b / 8,
                                     bpm=120, meter=meter))
          for b in range(8)]
check("phrase energy is flat then builds",
      levels[0] == levels[3] < levels[6] < levels[7],
      " ".join(f"{v:.2f}" for v in levels))

try:
    autom.AudioEnergy().level(clockmod.Position(0, 0, 0, 120, meter))
    check("AudioEnergy refuses to pretend", False, "returned a value")
except NotImplementedError:
    check("AudioEnergy refuses to pretend", True)


# -- 6. end to end through the runner -----------------------------------------
print("\n6. the runner drives auto mode")
ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
setlist = autom.SetList([simple_look(n) for n in ("A", "B")])


def moving_look(name):
    def make(color):
        show = statemod.Show(master=0.9)
        show.base.append(statemod.pose_layer(lambda c, h: c.geometry.aim_at_ball(h)))
        show.color.append(statemod.color_layer(color))
        show.movement.append(statemod.move_layer(
            motion.as_move(motion.orbit(15.0), bars=4.0)))
        show.fx.append(autom.energy_intensity_layer())
        return show
    return autom.Look(name=name, make=make)


setlist = autom.SetList([moving_look("A"), moving_look("B")])
director = autom.AutoDirector(
    setlist,
    # 4 s at 140 bpm is only 0.29 phrases, so the intervals have to be short
    # enough to fire inside the window: 0.25 phrases is 2 bars, 0.125 is 1 bar.
    autom.AutoConfig(look_changes=True, palette=True, energy=True,
                     change_every_phrases=0.25, palette_every_phrases=0.125),
    autom.Palette([(1, 0, 0), (0, 0, 1)]),
    autom.PhraseEnergy())
c = clockmod.MasterClock(bpm=140.0, now=0.0)
runner = Runner(ctx=ctx, show=setlist.current().make((1, 1, 1)),
                clock=c, director=director)
stats = runner.run(seconds=4.0)

check("frames stayed on time", stats.drops == 0 and stats.eval_errors == 0,
      f"{stats.frames} frames, {stats.effective_fps:.2f} fps, "
      f"{stats.eval_errors} errors")
check("looks changed during the run", director.changes > 0, f"{director.changes}")
check("palette rotated", director.palette_changes > 0,
      f"{director.palette_changes}")
check("context carries auto outputs",
      ctx.energy > 0 and ctx.energy_rate > 0 and ctx.motion_bar > 0,
      f"energy {ctx.energy:.2f} rate {ctx.energy_rate:.2f} "
      f"motion_bar {ctx.motion_bar:.2f}")
check("movement phase tracks its own rate, not musical position",
      abs(ctx.motion_bar - ctx.bar) > 1e-6,
      f"motion_bar {ctx.motion_bar:.3f} vs bar {ctx.bar:.3f}")
print(f"  status: {director.status()}")


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("auto: all checks pass")
