"""Which ported looks a parametric routine already covers, with a number.

Retirement has to be a measurement, not a hunch. "Floor Wave is just Ball Wave
lower down" is the kind of claim that is obviously true until you check it and
find the two disagree by fifteen degrees on one head. So this fits, reports a
residual in DEGREES OF AIM, and leaves the decision to a person reading it.

Two kinds of redundancy, because the library has two:

  **A path that is a block.** `Lazy Circle` stores eight steps for four
  heads; sampled, it is four heads ninety degrees apart on a twenty-degree
  circle -- which is the `orbit` block at full spread. Fitted by grid search
  over each movement block's own declared ranges (`blocks.PARAMS`), so the
  search space is exactly what the console and the routine editor can express.

  **A pose that is another pose, moved.** The shape macros scale about each
  head's own ball aim and offset the result, so pose A is redundant with pose B
  whenever `A ≈ size * B + centre`. That is a linear least-squares fit with
  three unknowns and it is solved directly rather than searched.

Degrees, never DMX. One 8-bit pan step is 2.1 degrees on these heads, so a
residual quoted in bytes would hide exactly the errors that matter, and the
whole engine works in aim space for the same reason.

    python shared/tools/audit_library.py
    python shared/tools/audit_library.py --event events/despacio --threshold 2
"""

from __future__ import annotations

import argparse
import math
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import blocks as blocksmod                               # noqa: E402
from engine import library as libmod                                 # noqa: E402
from engine import motion                                            # noqa: E402
from engine import params as parammod                                # noqa: E402

# How many phases to compare a path at. 24 is well above the step count of
# anything in the library, so a fit cannot pass by landing only on the
# waypoints and missing the route between them.
SAMPLES = 24

# The macro ranges a fit is allowed to use, taken from the declarations the
# console clamps to rather than repeated here -- a fit that needed size 6 would
# be describing something the operator cannot actually dial in.
SIZE_RANGE = (parammod.SIZE.min, parammod.SIZE.max)
#
# The CENTRE range is the rig's reach when there is a rig to ask (see `main`),
# because that is what the centre macro is bounded by on stage. The declared
# fallback is only for an event with no moving heads.
CENTRE_REACH: dict = {
    "bearing": (parammod.CENTER_BEARING.min, parammod.CENTER_BEARING.max),
    "elevation": (parammod.CENTER_ELEV.min, parammod.CENTER_ELEV.max),
}


def _within_centre(bearing: float, elev: float) -> bool:
    (blo, bhi), (elo, ehi) = CENTRE_REACH["bearing"], CENTRE_REACH["elevation"]
    return blo <= bearing <= bhi and elo <= elev <= ehi


class _Ctx:
    """The two fields a ported offset function reads. Not an EvalContext.

    `path_offsets` wants `ctx.motion_bar`, `ctx.move_spread` and `ctx.geometry`;
    geometry is only consulted for looks that bind columns by fixture NAME, and
    every candidate here binds positionally. Standing up a whole rig to fit a
    curve would make this tool need a venue.
    """

    def __init__(self, bar: float = 0.0, spread: float = 0.0) -> None:
        self.motion_bar = bar
        self.move_spread = spread
        self.geometry = None


@dataclass
class Fit:
    """One candidate replacement and how wrong it is."""
    look: str
    replacement: str
    rms_deg: float
    worst_deg: float
    detail: str

    def line(self) -> str:
        return (f"  {self.look:24s} -> {self.replacement:44s} "
                f"rms {self.rms_deg:6.2f}°  worst {self.worst_deg:6.2f}°")


# ------------------------------------------------------------------- paths --

def sample_path(entry: libmod.LibraryEntry, heads: int) -> list[list[tuple]]:
    """What a ported path actually renders, per phase per head, in degrees."""
    bars = entry.bars or libmod.DEFAULT_BARS
    offset_for = libmod.path_offsets(entry.steps, bars, fixtures=entry.fixtures)
    out = []
    for step in range(SAMPLES):
        ctx = _Ctx(bar=step / SAMPLES * bars, spread=0.0)
        out.append([offset_for(ctx, head) for head in range(heads)])
    return out


class _BlockCtx:
    """What a block's offset function reads. The operator's Spread macro is
    held at 0 so the block's OWN spread argument is the only one being fitted
    -- otherwise the two would trade off against each other and the fit would
    report whichever happened to win."""

    def __init__(self, bar: float) -> None:
        self.motion_bar = bar
        self.move_spread = 0.0


def sample_block(name: str, args: dict, heads: int, bars: float
                 ) -> list[list[tuple]]:
    """What a movement block renders, per phase per head, in degrees."""
    fn = blocksmod.OFFSETS[name](args, blocksmod.Env())
    out = []
    for step in range(SAMPLES):
        ctx = _BlockCtx(step / SAMPLES * bars)
        out.append([fn(ctx, head, heads) for head in range(heads)])
    return out


def compare(a: Sequence[Sequence[tuple]], b: Sequence[Sequence[tuple]]
            ) -> tuple[float, float]:
    """(rms, worst) distance in degrees between two sampled routes."""
    total = 0.0
    worst = 0.0
    count = 0
    for row_a, row_b in zip(a, b):
        for (ab, ae), (bb, be) in zip(row_a, row_b):
            d = math.hypot(ab - bb, ae - be)
            total += d * d
            worst = max(worst, d)
            count += 1
    return (math.sqrt(total / count) if count else float("inf"), worst)


def grid(param: parammod.Param, steps: int) -> list[float]:
    """Candidate values for one parameter, across its declared range."""
    if param.min is None or param.max is None:
        return [param.default]
    if param.kind == "integer":
        lo, hi = int(param.min), int(param.max)
        span = hi - lo
        if span <= steps:
            return list(range(lo, hi + 1))
        return [lo + round(i * span / steps) for i in range(steps + 1)]
    return [param.min + (param.max - param.min) * i / steps
            for i in range(steps + 1)]


def fit_path(entry: libmod.LibraryEntry, heads: int, coarse: int = 12
             ) -> Optional[Fit]:
    """The closest movement block to a ported path, over declared ranges.

    Coarse grid then one refinement pass around the winner. A single fine grid
    over four arguments is minutes of Python for an answer that is a judgement
    call anyway; two passes get within a few hundredths of a degree of the same
    place in seconds.
    """
    target = sample_path(entry, heads)
    # The ported path's own cycle length is kept: a block that matched only by
    # running at a different speed is not the same look.
    bars = float(entry.bars or libmod.DEFAULT_BARS)
    best: Optional[Fit] = None

    for name in sorted(blocksmod.OFFSETS):
        declared = blocksmod.PARAMS[name]
        # Excluded, and not as an optimisation:
        #   `seed` indexes a hash -- no ordering, no meaning between values -- so
        #   searching it fits NOISE. Left in, the first version reported "Corner
        #   Chase is scatter(seed=5833)" at 122 degrees of error, having found
        #   the least-bad of ten thousand random patterns.
        #   `bars` is fixed to the ported cycle, above.
        #   `spread` is searched on its own short list, below: it is the
        #   argument that turns a four-head ring into one orbit, and it matters
        #   too much to leave to a coarse grid.
        #   An argument with no fixed default (fan_sweep's `sweep`, half the
        #   width unless given) is searched like any other.
        tunable = [q for q in declared
                   if q.name not in ("bars", "seed", "spread")
                   and q.kind in ("number", "integer")]
        options = [q for q in declared if q.kind in ("choice", "bool")]
        has_spread = blocksmod.param(name, "spread") is not None
        spreads = (0.0, 0.25, 0.5, 0.75, 1.0, -0.25, -0.5, -0.75) if has_spread else (None,)

        def axes_for(centres: dict, width: float, steps: int):
            axes = []
            for q in tunable:
                if q.name in centres and q.min is not None and q.max is not None:
                    span = (q.max - q.min) * width
                    lo = max(q.min, centres[q.name] - span / 2)
                    hi = min(q.max, centres[q.name] + span / 2)
                    axes.append([lo + (hi - lo) * i / steps for i in range(steps + 1)])
                else:
                    axes.append(grid(q, steps))
            return axes

        def combos(axes):
            out = [{}]
            for q, values in zip(tunable, axes):
                out = [{**c, q.name: v} for c in out for v in values]
            return out

        option_sets = [{}]
        for q in options:
            values = list(q.choices or ()) if q.kind == "choice" else [False, True]
            option_sets = [{**o, q.name: v} for o in option_sets for v in values]

        found = None
        for combo in combos(axes_for({}, 1.0, coarse)):
            for option in option_sets:
                for spread in spreads:
                    args = {**combo, **option, "bars": bars}
                    if spread is not None:
                        args["spread"] = spread
                    rms, worst = compare(target, sample_block(name, args, heads, bars))
                    if found is None or rms < found[0]:
                        found = (rms, worst, args)
        if found is None:
            continue

        rms, worst, args = found
        fixed = {k: v for k, v in args.items()
                 if k not in {q.name for q in tunable}}
        for combo in combos(axes_for({q.name: args[q.name] for q in tunable},
                                     0.25, 6)):
            fine = {**fixed, **combo}
            r, w = compare(target, sample_block(name, fine, heads, bars))
            if r < rms:
                rms, worst, args = r, w, fine

        detail = ", ".join(
            f"{k}={v:g}" if isinstance(v, float) else f"{k}={v}"
            for k, v in args.items() if k != "bars")
        candidate = Fit(entry.name, f"{name}({detail})", rms, worst,
                        f"{bars:g} bars")
        if best is None or candidate.rms_deg < best.rms_deg:
            best = candidate
    return best


# ------------------------------------------------------------------- poses --

def has_shape(entry: libmod.LibraryEntry, tol: float = 0.5) -> bool:
    """Does this pose point the heads differently, or all the same way?

    A pose whose heads all share one offset has no shape to scale -- it IS a
    centre offset, and the macro system already covers it exactly.
    """
    offsets = entry.offsets or []
    if len(offsets) < 2:
        return False
    first = offsets[0]
    return any(math.hypot(o[0] - first[0], o[1] - first[1]) > tol
               for o in offsets[1:])


# Below this, the fit has scaled the reference down to nothing and is really
# just saying "this pose is a uniform offset" -- which is true of any reference
# you care to name, so it carries no information about THIS one. Uniform poses
# are reported separately, against the ball, where the statement is meaningful.
MIN_SIZE = 0.05


def fit_pose(entry: libmod.LibraryEntry, other: libmod.LibraryEntry
             ) -> Optional[Fit]:
    """Is `entry` just `other` under some size and centre?

    Solved, not searched. The macros are `aim = size * offset + centre`, which
    is linear in all three unknowns, so the least-squares answer falls straight
    out of the centred sums -- and a direct solution cannot miss a fit that a
    grid would have stepped over.
    """
    a = entry.offsets or []
    b = other.offsets or []
    if len(a) != len(b) or not a:
        return None
    # Both sides must have shape. Without this the fit degenerates: scaling any
    # reference by zero produces the ball, so every uniform pose "matches"
    # everything at 0.00 degrees and the report is all false positives.
    if not (has_shape(entry) and has_shape(other)):
        return None

    n = len(a)
    xs = [p[0] for p in b]
    ys = [p[1] for p in b]
    ps = [p[0] for p in a]
    qs = [p[1] for p in a]
    mx, my = sum(xs) / n, sum(ys) / n
    mp, mq = sum(ps) / n, sum(qs) / n
    num = sum((x - mx) * (p - mp) for x, p in zip(xs, ps)) \
        + sum((y - my) * (q - mq) for y, q in zip(ys, qs))
    den = sum((x - mx) ** 2 for x in xs) + sum((y - my) ** 2 for y in ys)
    if den < 1e-9:
        return None
    size = num / den
    if size < MIN_SIZE:
        return None
    size = max(SIZE_RANGE[0], min(SIZE_RANGE[1], size))
    cb = mp - size * mx
    ce = mq - size * my
    if not _within_centre(cb, ce):
        return None

    total = 0.0
    worst = 0.0
    for (ab, ae), (bb, be) in zip(a, b):
        d = math.hypot(ab - (size * bb + cb), ae - (size * be + ce))
        total += d * d
        worst = max(worst, d)
    return Fit(entry.name, other.name, math.sqrt(total / n), worst,
               f"size {size:.2f}, centre ({cb:.0f}°, {ce:.0f}°)")


# ------------------------------------------------------------------- report --

def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--event", type=Path,
                    default=REPO / "events" / "despacio")
    ap.add_argument("--threshold", type=float, default=2.0,
                    help="degrees of RMS aim error to call a match (default 2)")
    ap.add_argument("--heads", type=int, default=4)
    args = ap.parse_args(argv)

    # What the centre macro really spans on this rig -- see CENTRE_REACH.
    try:
        from engine import rig as rigmod
        from engine import server as servermod
        reach = servermod.rig_reach(rigmod.load_rig(args.event))
        if reach:
            CENTRE_REACH.update(reach)
    except Exception as exc:                                       # noqa: BLE001
        print(f"(no rig to read reach from -- using the fallback: {exc})")
    print("centre reach: " + ", ".join(
        f"{axis} {lo:g}..{hi:g}°" for axis, (lo, hi) in CENTRE_REACH.items()))

    entries = libmod.load_entries(args.event / "looks.json")
    parametric_path = args.event / "parametric_looks.json"
    if parametric_path.exists():
        parametric, retired = libmod.load_parametric(parametric_path)
        # With the retired list applied, so an entry already retired is not
        # suggested for retirement again.
        entries = libmod.merge(entries, parametric, retired)

    # Chase STEPS are excluded throughout. They are already filed under their
    # parent and hidden by the picker, so retiring one shortens nothing -- and
    # there are 86 of them, which is most of the "103 poses" the roadmap cites.
    poses = [e for e in entries if e.offsets and not e.step_of and not e.retired]
    paths = [e for e in entries
             if e.steps and not e.step_spans and not e.step_of and not e.retired]

    print(f"\n{args.event.name}: {len(poses)} standalone poses, "
          f"{len(paths)} standalone paths")
    print(f"a match is RMS aim error under {args.threshold:g}°\n")

    print("PATHS vs movement blocks")
    print("-" * 96)
    path_hits: list[Fit] = []
    for entry in sorted(paths, key=lambda e: e.name):
        if entry.is_parametric:
            continue
        fit = fit_path(entry, args.heads)
        if fit is None:
            continue
        mark = "MATCH" if fit.rms_deg <= args.threshold else "     "
        print(f"{mark}{fit.line()}   [{fit.detail}]")
        if fit.rms_deg <= args.threshold:
            path_hits.append(fit)

    # Poses with no shape at all: every head on the same offset. These are not
    # "like some other pose" -- they ARE the centre macro, exactly, and saying
    # so is more useful than reporting a 0.00 degree fit against an arbitrary
    # reference that was scaled to nothing.
    print("\nPOSES that are just the ball, moved")
    print("-" * 96)
    uniform: list[Fit] = []
    for entry in sorted(poses, key=lambda e: e.name):
        if has_shape(entry) or not entry.offsets:
            continue
        bearing, elev = entry.offsets[0]
        within = _within_centre(bearing, elev)
        fit = Fit(entry.name, "Heads - Ball + centre", 0.0, 0.0,
                  f"centre ({bearing:.0f}°, {elev:.0f}°)"
                  + ("" if within else "  -- OUTSIDE the centre range"))
        print(f"{'MATCH' if within else '     '}{fit.line()}   [{fit.detail}]")
        if within:
            uniform.append(fit)

    print("\nPOSES vs other poses under size and centre")
    print("-" * 96)
    pose_hits: list[Fit] = []
    shaped = [e for e in poses if has_shape(e)]
    for entry in sorted(shaped, key=lambda e: e.name):
        best = None
        for other in shaped:
            if other.name == entry.name:
                continue
            fit = fit_pose(entry, other)
            if fit is not None and (best is None or fit.rms_deg < best.rms_deg):
                best = fit
        if best is None:
            print(f"      {entry.name:24s} -> no other pose fits it")
            continue
        mark = "MATCH" if best.rms_deg <= args.threshold else "     "
        print(f"{mark}{best.line()}   [{best.detail}]")
        if best.rms_deg <= args.threshold:
            pose_hits.append(best)

    print("\n" + "=" * 96)
    print(f"{len(path_hits)} path(s), {len(uniform)} uniform pose(s) and "
          f"{len(pose_hits)} shaped pose(s) under {args.threshold:g}° RMS")
    if pose_hits:
        # Pose matches are SYMMETRIC -- if A is B moved, then B is A moved --
        # so a naive retirement of every match would retire both halves of each
        # pair and leave neither. Say so rather than emitting a broken list.
        print("\nNote: pose matches are mutual. Retiring one of each pair is a\n"
              "judgement about which name an operator reaches for, not\n"
              "something this tool can decide.")
    if path_hits:
        print("\nSuggested `retired` entries for parametric_looks.json:")
        for fit in path_hits:
            print(f'  {{ "name": {fit.look!r}, "replaced_by": "<parametric look>", '
                  f'"note": "{fit.replacement} within {fit.rms_deg:.1f}°" }}'
                  .replace("'", '"'))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
