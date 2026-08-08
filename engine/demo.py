"""
End-to-end smoke run: load the despacio rig, evaluate a moving look, and put
real Art-Net frames on the wire.

Not a test -- `engine/tests/` holds those. This is the thing you run to see the
engine work, and to point `shared/tools/artnet_listener.py` at.

  python -m engine.demo                          # 5 s to a null output
  python -m engine.demo --artnet 127.0.0.1       # to a listener on this machine
  python -m engine.demo --seconds 30 --no-taper  # what the taper is holding back
"""

from __future__ import annotations

import argparse
from pathlib import Path

from . import auto as autom
from . import clock as clockmod
from . import geometry as geo
from . import motion
from . import safety as safetymod
from . import state as statemod
from .output import ArtNetOutput, NullOutput
from .rig import load_rig
from .runner import Runner

REPO = Path(__file__).resolve().parent.parent


def orbit_look(radius_deg: float = 20.0, bars: float = 8.0) -> statemod.Show:
    """The ambient signature: each head drifts in a slow circle around its own
    calibrated ball point, phase-offset so the four are never in lockstep.

    Three things worth noticing about how this is written. The cycle length is
    in BARS, so it stays musical at any tempo with no retuning -- "some routines
    were too fast or slow" was a symptom of millisecond-timed chases. It is a
    function of phase rather than a list of stored positions, so slowing it down
    makes it smoother, not steppier. And the offset is relative to each head's
    own ball point, so recalibrating a head re-centres its orbit automatically.
    """
    # 90% normal, so a beam over the crowd lands near half of that. Overall
    # level belongs on the master, not in the taper: the taper's job is the
    # RATIO between "over people" and "clear", and folding a global trim into it
    # would mean turning the show down also weakened the guard.
    show = statemod.Show(master=0.9)
    show.base.append(statemod.pose_layer(
        lambda ctx, head: ctx.geometry.aim_at_ball(head), tags=("movers",)))
    show.base.append(statemod.on_layer(0.6, tags=("pinspots",)))
    show.color.append(statemod.color_layer((0.2, 0.4, 1.0), tags=("movers",)))
    show.color.append(statemod.color_layer((1.0, 0.1, 0.0), tags=("pinspots",)))

    show.movement.append(statemod.move_layer(
        motion.as_move(motion.orbit(radius_deg, bars=bars, elongation=1.5)),
        tags=("movers",)))
    return show


def set_list() -> autom.SetList:
    """A few looks, in the shape the Night cue list should take.

    Each is a factory taking the current palette colour, so colour rotates
    independently of which look is running -- rather than every combination of
    look and colour being its own stored scene, which is how 179 of them
    accumulated and why only a handful got used.
    """
    # Cycle lengths are stated on the patterns themselves; `as_move` reads them.
    def look(name, offset_fn, tags=("movers",)):
        def make(color):
            show = statemod.Show(master=0.9)
            show.base.append(statemod.pose_layer(
                lambda ctx, head: ctx.geometry.aim_at_ball(head), tags=tags))
            show.base.append(statemod.on_layer(0.6, tags=("pinspots",)))
            show.color.append(statemod.color_layer(color, tags=tags))
            show.color.append(statemod.color_layer(color, tags=("pinspots",)))
            show.movement.append(statemod.move_layer(
                motion.as_move(offset_fn), tags=tags))
            show.fx.append(autom.energy_intensity_layer())
            show.fx.append(autom.energy_strobe_layer())
            return show
        return autom.Look(name=name, make=make)

    return autom.SetList([
        look("drift", motion.orbit(15.0, bars=16.0, elongation=1.5)),
        look("sweep", motion.pendulum(45.0, bars=8.0)),
        look("wide orbit", motion.orbit(40.0, bars=8.0)),
        look("bob", motion.pendulum(18.0, bars=4.0, vertical=True)),
    ])


def main() -> None:
    parser = argparse.ArgumentParser(description="Engine end-to-end smoke run")
    parser.add_argument("--event", type=Path, default=REPO / "events" / "despacio")
    parser.add_argument("--artnet", metavar="IP",
                        help="send Art-Net here (e.g. 127.0.0.1, or a broadcast address)")
    parser.add_argument("--seconds", type=float, default=5.0)
    parser.add_argument("--fps", type=float, default=40.0)
    parser.add_argument("--bpm", type=float, default=124.0)
    parser.add_argument("--no-taper", action="store_true",
                        help="disable the safety taper, to see what it is holding back")
    parser.add_argument("--nudge", type=float, default=1.0, metavar="MULT",
                        help="halfway through, nudge the speed by this much "
                             "(e.g. 0.5) and check the beat does not jump")
    parser.add_argument("--auto", action="append", default=[],
                        choices=["looks", "palette", "energy", "all"],
                        help="enable an auto-mode axis; repeatable")
    args = parser.parse_args()

    rig = load_rig(args.event)
    errors = rig.validate()
    if errors:
        raise SystemExit("rig does not validate:\n  " + "\n  ".join(errors))

    ctx = statemod.EvalContext(
        rig=rig, venue=rig.venue,
        taper=safetymod.TaperConfig(enabled=not args.no_taper))
    show = orbit_look()

    output = ArtNetOutput(args.artnet) if args.artnet else NullOutput()
    master = clockmod.MasterClock(bpm=args.bpm, now=0.0)

    director = None
    if args.auto:
        axes = set(args.auto)
        every = set(("looks", "palette", "energy")) if "all" in axes else axes
        looks = set_list()
        # Short intervals so a few-second demo shows the behaviour. A real set
        # wants 2 or 4 phrases, which is 16 or 32 bars.
        director = autom.AutoDirector(
            looks,
            autom.AutoConfig(look_changes="looks" in every,
                             palette="palette" in every,
                             energy="energy" in every,
                             change_every_phrases=0.25,
                             palette_every_phrases=0.125),
            autom.Palette([(0.2, 0.4, 1.0), (1.0, 0.1, 0.0), (0.1, 1.0, 0.3)]),
            autom.PhraseEnergy())
        show = looks.current().make(director.palette.current())

    runner = Runner(ctx=ctx, show=show, output=output, fps=args.fps,
                    clock=master, director=director)

    tapered: list[float] = []
    nudged_at: list[tuple[float, float]] = []

    def watch(states):
        tapered.append(sum(1 for s in states.values()
                           if s.safety is not None and s.safety.taper < 1.0))
        # Halfway through, nudge the speed the way you would mid-set. The
        # timeline re-anchors at the current position first, so the beat does
        # not jump and no running look snaps -- which is the property that makes
        # a live nudge usable at all rather than something you brace for.
        if args.nudge != 1.0 and not nudged_at and ctx.time >= args.seconds / 2:
            before = master.beat(ctx.time)
            master.set_speed(args.nudge, ctx.time)
            nudged_at.append((before, master.beat(ctx.time)))

    runner.on_frame = watch

    print(f"rig {rig.name!r}: {len(rig.fixtures)} fixtures, "
          f"universes {rig.universes}")
    print(f"timing contract: {', '.join(runner.applied_timing)}")
    print(f"output: {'Art-Net -> ' + args.artnet if args.artnet else 'null (no wire)'}")
    print(f"taper: {'OFF' if args.no_taper else 'on'}  "
          f"tempo: {args.bpm:.0f} bpm ({master.source})")
    if director is None:
        print("auto: off -- one 8-bar orbit, held\n")
    else:
        axes = ", ".join(k for k, v in director.status()["axes"].items() if v)
        phrase = "measured" if master.phrase_measured else "counted (lands on bars)"
        print(f"auto: {axes}   phrase: {phrase}   "
              f"set list: {', '.join(l.name for l in director.setlist.looks)}\n")

    stats = runner.run(seconds=args.seconds)

    print(f"{stats.frames} frames in {stats.elapsed:.2f} s "
          f"= {stats.effective_fps:.3f} fps (target {args.fps})")
    print(f"worst interval error: {stats.worst_error * 1000:.3f} ms")
    print(f"dropped frames: {stats.drops}  "
          f"evaluation errors: {stats.eval_errors}")
    if runner.last_error:
        print(f"last evaluation error:\n{runner.last_error}")
    if tapered:
        print(f"frames with at least one head tapered: "
              f"{sum(1 for n in tapered if n)}/{len(tapered)}")
    print(f"musical position reached: beat {ctx.beat:.2f}, bar {ctx.bar:.2f}, "
          f"phrase {ctx.phrase:.2f} at {ctx.bpm:.1f} bpm")
    if director is not None:
        s = director.status()
        print(f"auto: look {s['look']!r} after {s['changes']} change(s) "
              f"({s['last_change']}), {s['palette_changes']} palette rotation(s)")
        print(f"      energy {s['energy']:.2f} -> rate {s['rate']:.2f}x, "
              f"strobe {'on' if s['strobe'] else 'off'}; "
              f"motion phase {ctx.motion_bar:.2f} bars vs musical {ctx.bar:.2f}")
    if nudged_at:
        before, after = nudged_at[0]
        print(f"speed nudged to {args.nudge}x mid-run: beat {before:.6f} -> "
              f"{after:.6f} (moved {abs(after - before):.9f} -- no jump)")

    # Show the last frame's channel values, so it is visible that this is real
    # DMX and not a simulation reporting on itself.
    last = statemod.frame(ctx, show)
    for universe, data in sorted(last.items()):
        active = {i + 1: v for i, v in enumerate(data) if v}
        print(f"\nuniverse {universe}: {len(active)} non-zero channel(s)")
        for f in rig.fixtures:
            if f.universe != universe:
                continue
            vals = data[f.address - 1:f.last_address]
            print(f"  {f.name:<16} ch {f.address:>3}: "
                  + " ".join(f"{v:>3}" for v in vals))

    states = statemod.evaluate(ctx, show)
    print("\nsafety:")
    for f in rig.movers:
        clear = states[f.fid].safety
        if clear is not None:
            print(f"  {f.name:<16} taper {clear.taper:.2f}  {clear.reason}")

    runner.stop()
    output.close()


if __name__ == "__main__":
    main()
