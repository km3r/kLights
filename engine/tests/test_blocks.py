"""Every block, against its own declaration, swept across a whole cycle.

`blocks.py` is the one building-block system: a show folder's routines are made
of these, and so are the console's parametric looks. Its arguments are declared
once, in `blocks.PARAMS`, and three things now depend on that declaration being
TRUE -- the builders read their defaults from it, the routine editor and the
console render their controls from it, and modulators and `vary` stay inside
its ranges. So the claims here are about the declaration as much as the shapes:

  * a builder given no arguments behaves exactly as it does given the declared
    defaults -- the declaration is not a description of some other number;
  * every declared default sits inside its declared range, or a slider could
    not show it;
  * every shape is finite at every phase and head, every level block is a
    multiplier, every colour block is a colour.

The sweep is over EVERY block rather than a hand-picked few. A registry whose
entries are only checked when someone remembers to add a case grows broken
entries, and the failure is a control that renders, accepts a value, and
produces NaN at one phase in sixty-four.

Run: python engine/tests/test_blocks.py
"""

import math
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import blocks as blocksmod
from engine import params as parammod
from engine import rig as rigmod
from engine import state as statemod
from engine.blocks import BLOCKS, OFFSETS, PARAMS, SLOT_OF

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class Ctx:
    """The fields a movement offset reads -- and nothing else, so a shape can be
    swept without a rig."""

    def __init__(self, bar=0.0, spread=0.0):
        self.motion_bar = bar
        self.move_spread = spread


HEADS = 4
PHASES = [step / 64.0 * 16.0 for step in range(65)]          # four 4-bar cycles
rig = rigmod.load_rig(REPO / "events" / "despacio")
rigging = blocksmod.Rigging(rig=rig, entries={})
everyone = tuple(rig.fixtures)


def sweep_offsets(name, args):
    fn = OFFSETS[name](args, blocksmod.Env())
    return [fn(Ctx(bar), k, HEADS) for bar in PHASES for k in range(HEADS)]


def sweep_layer(name, args):
    """What a colour or level block does to every fixture across the sweep."""
    slot = SLOT_OF[name]
    env = blocksmod.Env()
    block = blocksmod.make(name, args, everyone, slot, env, rigging)
    out = []
    for bar in PHASES:
        ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
        ctx.set_phase(bar)
        states = {f.fid: statemod.FixtureState(intensity=1.0) for f in everyone}
        for layer in block.layers:
            layer(ctx, states)
        out.append({fid: (s.intensity, s.color) for fid, s in states.items()})
    return block, out


print("\n1. every block is declared, and nothing is declared that is not a block")
check("each block has a declaration", set(BLOCKS) == set(PARAMS),
      f"missing {set(BLOCKS) - set(PARAMS)}, extra {set(PARAMS) - set(BLOCKS)}")
check("each declared argument has a label",
      all(p.label for params in PARAMS.values() for p in params))
check("every block has a slot, or is a rig-bound adapter",
      all(SLOT_OF[b] in ("movement", "color", "level", None) for b in BLOCKS))
# `_NUMERIC` was a hand-written list until it was derived; the routine editor's
# fixture still reads it, so it must still say exactly what the declarations do.
check("the numeric arguments are derived from the declarations",
      all(set(blocksmod._NUMERIC.get(b, ())) ==
          {p.name for p in PARAMS[b] if p.kind in ("number", "integer")}
          for b in BLOCKS))

print("\n2. a declared default is the default -- not a description of another number")
# The whole point of declaring: the builder, the editor and the console must
# agree. Build with nothing, build with every declared default spelled out, and
# the two must be indistinguishable.
for name in OFFSETS:
    explicit = {p.name: p.default for p in PARAMS[name] if p.default is not None}
    check(f"{name} with no arguments moves exactly as with its declared defaults",
          sweep_offsets(name, {}) == sweep_offsets(name, explicit))
for name in [b for b in BLOCKS if SLOT_OF[b] in ("color", "level")]:
    explicit = {p.name: p.default for p in PARAMS[name] if p.default is not None}
    # A colour block needs its colours; left out is a build problem, so the
    # "no arguments" side gets the declared ones too and the comparison is of
    # everything else.
    required = {k: v for k, v in explicit.items()
                if k in ("color", "colors", "color_a", "color_b")}
    _, bare = sweep_layer(name, required)
    _, full = sweep_layer(name, explicit)
    check(f"{name} with no arguments renders as with its declared defaults",
          bare == full)

print("\n3. every declared default sits inside its declared range")
outside = [f"{b}.{p.name}={p.default}" for b, params in PARAMS.items()
           for p in params
           if p.kind in ("number", "integer") and p.default is not None
           and p.min is not None and p.max is not None
           and not (p.min <= p.default <= p.max)]
check("a slider can show every default", not outside, f"{outside}")

print(f"\n4. all {len(OFFSETS)} movement shapes are finite everywhere")
for name in sorted(OFFSETS):
    values = [v for pair in sweep_offsets(name, {}) for v in pair]
    check(f"{name} is finite at every phase and head",
          all(math.isfinite(v) for v in values))

# Size scales about zero, and zero is each head's own calibrated ball aim -- so
# a shape whose zero excursion was not zero would drift off the ball as it came
# down. Every shape with a size-like argument must collapse at 0.
for name, key in (("orbit", "radius"), ("pendulum", "width"),
                  ("figure8", "width"), ("spiral", "radius"),
                  ("scatter", "radius")):
    args = {key: 0.0}
    if name == "figure8":
        args["height"] = 0.0
    at_zero = [v for pair in sweep_offsets(name, args) for v in pair]
    check(f"{name} at {key}=0 produces no offset",
          all(abs(v) < 1e-9 for v in at_zero),
          f"max {max(abs(v) for v in at_zero):.4f}")

print("\n5. scatter is random-looking and reproducible")
# Not random: a hash of (seed, head, station), so previz and the rig agree after
# any of them drops a frame.
check("the same seed gives byte-identical offsets",
      sweep_offsets("scatter", {"seed": 7}) == sweep_offsets("scatter", {"seed": 7}))
check("a different seed gives different ones",
      sweep_offsets("scatter", {"seed": 7}) != sweep_offsets("scatter", {"seed": 8}))
reach = blocksmod.param("scatter", "radius").default
check("and it never leaves its declared reach",
      all(abs(b) <= reach + 1e-9 for b, _ in sweep_offsets("scatter", {})))
check("heads wander independently",
      len({OFFSETS["scatter"]({}, blocksmod.Env())(Ctx(1.0), k, HEADS)
           for k in range(HEADS)}) > 1)
# Seamless: the last station is the first, so the loop has no jump at the wrap.
fn = OFFSETS["scatter"]({"bars": 4.0}, blocksmod.Env())
end, start = fn(Ctx(4.0 - 1e-9), 0, HEADS), fn(Ctx(4.0), 0, HEADS)
check("the loop closes without a jump",
      math.hypot(end[0] - start[0], end[1] - start[1]) < 1e-6)

print("\n6. spiral winds out, and in is the same curve read backwards")
out_fn = OFFSETS["spiral"]({"direction": "out", "bars": 1.0}, blocksmod.Env())
in_fn = OFFSETS["spiral"]({"direction": "in", "bars": 1.0}, blocksmod.Env())
radius = lambda xy: math.hypot(xy[0], xy[1] * 1.5)       # undo the default flatten
check("out starts at the aim point", radius(out_fn(Ctx(0.0), 0, 1)) < 1e-9)
check("in finishes at it", radius(in_fn(Ctx(1.0 - 1e-9), 0, 1)) < 1e-3)
check("out reaches further as the cycle goes on",
      radius(out_fn(Ctx(0.25), 0, 1)) < radius(out_fn(Ctx(0.75), 0, 1)))

print("\n7. a level block is a multiplier, never a base layer")
# The level slot MULTIPLIES -- that is what makes a dim chase compose with the
# master and the safety taper instead of fighting them.
for name in [b for b in BLOCKS if SLOT_OF[b] == "level" and b != "strobe"]:
    _, frames = sweep_layer(name, {})
    levels = [i for frame in frames for i, _ in frame.values()]
    check(f"{name} stays within 0..1", all(0.0 <= v <= 1.0 + 1e-12 for v in levels),
          f"{min(levels):.3f}..{max(levels):.3f}")

print("\n8. a colour block writes a colour in 0..1")
for name in [b for b in BLOCKS if SLOT_OF[b] == "color"]:
    defaults = {p.name: p.default for p in PARAMS[name] if p.default is not None}
    block, frames = sweep_layer(name, defaults)
    check(f"{name} builds", not block.problems, f"{block.problems}")
    colours = [c for frame in frames for _, c in frame.values()]
    check(f"{name} stays within 0..1",
          all(len(c) == 3 and all(0.0 <= x <= 1.0 for x in c) for c in colours))

print("\n9. hue_cycle under a full wheel drifts back rather than jumping")
# The first version walked `hue + span * phase`, which at a quarter-wheel span
# jumps straight back to the start at every cycle boundary -- a visible snap in
# a look whose whole point is a slow drift. Under a full wheel it now goes out
# and back; at a full wheel it rolls, which the wheel's own wrap makes seamless.


def hue_step(args):
    """Biggest change in hue between adjacent samples across the sweep."""
    import colorsys
    _, frames = sweep_layer("hue_cycle", {**args, "bars": 4.0, "spread": 0.0})
    first = min(frames[0])                       # one fixture, consistently
    hues = [colorsys.rgb_to_hsv(*frame[first][1])[0] for frame in frames]
    steps = [min(abs(b - a), 1.0 - abs(b - a)) for a, b in zip(hues, hues[1:])]
    return max(steps)


check("a quarter-wheel drift never jumps", hue_step({"span": 0.25}) < 0.05,
      f"biggest step {hue_step({'span': 0.25}):.3f} of the wheel")
check("a full-wheel roll never jumps either", hue_step({"span": 1.0}) < 0.1,
      f"biggest step {hue_step({'span': 1.0}):.3f} of the wheel")

print("\n10. choice arguments are checked from their declarations")
# Every choice argument, generically -- chase's order used to be the only one,
# checked by hand, and spiral, scatter and aim_points now have their own.
env = blocksmod.Env()
problems = blocksmod._check_args("spiral", {"direction": "sideways"}, env, rigging)
check("an unknown spiral direction is refused",
      any("direction must be one of" in p for p in problems), f"{problems}")
problems = blocksmod._check_args("chase", {"order": "diagonal"}, env, rigging)
check("chase's order is refused with the wording it always had",
      problems == ["order must be one of x, -x, y, -y, z, -z, index"],
      f"{problems}")
problems = blocksmod._check_args("duo", {"color_a": "beige"}, env, rigging)
check("a duo colour that resolves to nothing is refused",
      any("color_a" in p for p in problems), f"{problems}")
check("and a valid duo is not",
      not blocksmod._check_args("duo", {"color_a": "@primary",
                                        "color_b": "#00ff00"}, env, rigging))

print("\n11. the table the UI renders from")
table = blocksmod.publish()
check("every block is published", set(table) == set(BLOCKS))
check("with its slot", table["orbit"]["slot"] == "movement"
      and table["look"]["slot"] is None)
check("and its arguments, labelled, with their ranges",
      {p["name"] for p in table["orbit"]["params"]}
      == {"radius", "bars", "elongation", "spread"}
      and all("label" in p for p in table["orbit"]["params"]))
fan = {p["name"]: p for p in table["fan_sweep"]["params"]}
check("an argument with no fixed default publishes none, so an editor says 'auto'",
      fan["sweep"]["default"] is None)

print("\n12. a malformed declaration fails on import, not at a venue")
try:
    parammod.Param("x", "X", 0.0, kind="slider")
    check("an unknown kind is refused", False, "accepted")
except parammod.ParamError:
    check("an unknown kind is refused", True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("blocks: all checks pass")
