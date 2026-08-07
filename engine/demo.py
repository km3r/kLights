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
import math
from pathlib import Path

from . import geometry as geo
from . import safety as safetymod
from . import state as statemod
from .output import ArtNetOutput, NullOutput
from .rig import load_rig
from .runner import Runner

REPO = Path(__file__).resolve().parent.parent


def orbit_look(radius_deg: float = 20.0, bars: float = 8.0) -> statemod.Show:
    """The ambient signature: each head drifts in a slow circle around its own
    calibrated ball point, phase-offset so the four are never in lockstep.

    Two things are worth noticing about how this is written. It is a FUNCTION of
    beat, not a list of stored positions -- so slowing it down makes it slower,
    not steppier, which is what stepped chases could not do. And the offset is
    relative to each head's own ball point, so recalibrating a head re-centres
    its orbit automatically.
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

    def offset(ctx, head):
        n = len(ctx.geometry.heads)
        phase = ctx.bar / bars + head / n
        theta = 2.0 * math.pi * phase
        return (radius_deg * math.sin(theta), radius_deg * math.cos(theta))

    show.movement.append(statemod.move_layer(offset, tags=("movers",)))
    return show


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
    runner = Runner(ctx=ctx, show=show, output=output, fps=args.fps)

    # F6 will own this properly with a pluggable clock. Until then, beats are
    # derived from wall time at a fixed tempo -- enough to prove the look is a
    # function of musical position rather than of milliseconds.
    beats_per_second = args.bpm / 60.0
    tapered: list[float] = []

    def advance(states):
        ctx.beat = ctx.time * beats_per_second
        ctx.bar = ctx.beat / 4.0
        tapered.append(sum(1 for s in states.values()
                           if s.safety is not None and s.safety.taper < 1.0))

    runner.on_frame = advance

    print(f"rig {rig.name!r}: {len(rig.fixtures)} fixtures, "
          f"universes {rig.universes}")
    print(f"timing contract: {', '.join(runner.applied_timing)}")
    print(f"output: {'Art-Net -> ' + args.artnet if args.artnet else 'null (no wire)'}")
    print(f"taper: {'OFF' if args.no_taper else 'on'}  "
          f"tempo: {args.bpm:.0f} bpm\n")

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
