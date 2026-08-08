"""
Hold every mover on the mirror ball and emit it as Art-Net.

    python previz/ball_check.py --artnet 127.0.0.1 --seconds 120

A previz fixture, not a look. The ball only does anything when a beam is
actually on it, and `engine.demo`'s orbit deliberately *circles* it at 15-40
degrees -- so the reflections are correctly absent almost the whole time, which
is indistinguishable from a broken mirror ball. This is what to run when the
question is "do the reflections work", and it is the pose the calibration was
taken at, so it doubles as a check that the previz agrees with the rig about
where the ball is.

Everything is built through the engine rather than by writing DMX directly, so
what previz sees is a look the engine could really run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine import clock as clockmod                 # noqa: E402
from engine import safety as safetymod               # noqa: E402
from engine import state as statemod                 # noqa: E402
from engine.output import ArtNetOutput, NullOutput   # noqa: E402
from engine.rig import load_rig                      # noqa: E402
from engine.runner import Runner                     # noqa: E402

COLORS = {
    "blue": (0.2, 0.4, 1.0),
    "red": (1.0, 0.1, 0.0),
    "green": (0.1, 1.0, 0.3),
    "white": (1.0, 1.0, 1.0),
}


def ball_look(color=COLORS["blue"]) -> statemod.Show:
    """Every mover aimed at its own calibrated ball point, and held there.

    Deliberately has no movement layer at all. The aim comes from
    `aim_at_ball`, which is anchored on each head's hand-measured reading, so
    all four converge on the real ball even though they hold four different DMX
    values to do it.
    """
    show = statemod.Show(master=0.9)
    show.base.append(statemod.pose_layer(
        lambda ctx, head: ctx.geometry.aim_at_ball(head), tags=("movers",)))
    show.base.append(statemod.on_layer(0.6, tags=("pinspots",)))
    show.color.append(statemod.color_layer(color, tags=("movers",)))
    show.color.append(statemod.color_layer(color, tags=("pinspots",)))
    return show


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    parser.add_argument("--event", type=Path, default=REPO / "events" / "despacio")
    parser.add_argument("--artnet", metavar="IP", default="127.0.0.1")
    parser.add_argument("--seconds", type=float, default=120.0)
    parser.add_argument("--fps", type=float, default=40.0)
    parser.add_argument("--color", choices=sorted(COLORS), default="blue")
    parser.add_argument("--no-taper", action="store_true")
    args = parser.parse_args()

    rig = load_rig(args.event)
    errors = rig.validate()
    if errors:
        raise SystemExit("rig does not validate:\n  " + "\n  ".join(errors))

    ctx = statemod.EvalContext(
        rig=rig, venue=rig.venue,
        taper=safetymod.TaperConfig(enabled=not args.no_taper))
    output = ArtNetOutput(args.artnet) if args.artnet else NullOutput()

    runner = Runner(ctx=ctx, show=ball_look(COLORS[args.color]), output=output,
                    fps=args.fps, clock=clockmod.MasterClock(bpm=124.0, now=0.0))

    ball = rig.venue.ball
    print(f"holding {len(rig.movers)} mover(s) on the ball at {ball} mm, "
          f"{args.color}, for {args.seconds:.0f}s -> Art-Net {args.artnet}")
    stats = runner.run(seconds=args.seconds)
    print(f"{stats.frames} frames at {stats.effective_fps:.3f} fps, "
          f"{stats.drops} drop(s), {stats.eval_errors} eval error(s)")


if __name__ == "__main__":
    main()
