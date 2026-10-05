"""
The building blocks routines are made of: parametric, written against ROLES
rather than fixtures, and portable between rigs.

A routine row says "the movers do an orbit of radius `$radius`". This turns
that into a layer over the fixtures the role is bound to on THIS rig, reading
its arguments when it runs -- because an argument can be a parameter the
timeline automates, or a palette role whose colour changes when the palette
does.

Every block is one slot's worth of layer, and reads that slot's phase from the
context (`motion_bar`, `color_bar`, `level_bar`): the compiler (`program.py`)
sets it to the block's own item-local phase, so a block starts its cycle where
its item starts and lands on the same frame at the same beat however the deck
got there.

  slot       blocks
  ---------------------------------------------------------------------------
  movement   orbit, pendulum, fan_sweep, aim_points     offsets from the ball
  color      solid, color_chase                         sets colour
  level      chase, pulse, dim, strobe                  multiplies / shutters
  any        look, snapshot                             THIS RIG ONLY

Movement offsets go through `state.offset_aim`, the one place the size and
centre macros apply, so a block moves exactly as a ported look of the same
shape would. `aim_points` takes points as FRACTIONS of the room (0..1 on each
axis), which is what keeps it portable: "the front-left corner of the floor"
means the same thing in every venue; a millimetre coordinate does not.

`look` and `snapshot` adapt the existing library and presets. They are why a
routine can say "this rig only": a look name means something only on the rig
it was ported from.

A block that cannot be built -- an unknown look, an argument of the wrong kind,
a movement block on a colour row -- is reported and becomes an empty block that
claims nothing, so the lane underneath shows through and the show runs on.
"""

from __future__ import annotations

import colorsys
import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from . import library as libmod
from . import motion
from . import params as parammod
from . import rig as rigmod
from . import state as statemod
from .params import Param

BLOCKS = ("offset", "fan_sweep", "orbit", "pendulum", "figure8", "spiral",
          "scatter", "aim_points", "solid", "color_chase", "hue_cycle", "duo",
          "chase", "pulse", "breathe", "dim", "strobe", "look", "snapshot")

SLOT_OF: dict[str, Optional[str]] = {
    "offset": "movement",
    "orbit": "movement", "pendulum": "movement", "fan_sweep": "movement",
    "figure8": "movement", "spiral": "movement", "scatter": "movement",
    "aim_points": "movement",
    "solid": "color", "color_chase": "color", "hue_cycle": "color",
    "duo": "color",
    "chase": "level", "pulse": "level", "breathe": "level", "dim": "level",
    "strobe": "level",
    "look": None, "snapshot": None,                  # whichever slot they are on
}

RGB = tuple[float, float, float]
WHITE: RGB = (1.0, 1.0, 1.0)


# -- what each block takes ------------------------------------------------------
#
# Every argument a block reads, declared ONCE: its default, its range, its step,
# its unit and the sentence an editor shows under it.
#
# Before this, an argument was written out three times. The default was a
# literal inside the builder (`env.number(args.get("radius"), 20.0)`); the
# routine editor repeated it, with a step and a unit, in a hand-typed TypeScript
# table (`BLOCK_ARGS`); and `_NUMERIC` repeated the names a third time. A test
# held the NAMES together, but nothing held the defaults or ranges, so the
# editor could show 20 for a radius the engine had since started defaulting to
# something else.
#
# Now the builders read their defaults from here, `_NUMERIC` is derived from
# here, and `dump_designer_fixtures.py` writes this table out for the UI -- the
# designer's editor and the console's Tweak card both render from it.
#
# The RANGES are descriptive: what a slider spans, what a modulator may swing
# through, what `vary` may perturb within. They do NOT clamp a value written in
# a routine file -- a routine that asks for a 120-degree orbit gets one, as it
# did before ranges existed. Clamping what an author wrote would change shows
# that already play.

_ORDERS = ("x", "-x", "y", "-y", "z", "-z", "index")
_EASINGS = ("linear", "ease_in_out", "ease_out")


def _bars(default: float) -> Param:
    return Param("bars", "Cycle", default, min=0.25, max=64.0, step=0.25,
                 unit=" bars", musical=True,
                 help="How long one time round takes, in bars. Musical, so it "
                      "is right at any tempo.")


def _phase_spread(default: float) -> Param:
    # Added to the operator's Spread macro, not instead of it -- see `_spread`.
    return Param("spread", "Spread", default, min=-1.0, max=1.0, step=0.05,
                 help="Phase offset across the fixtures, in cycles. 0 moves "
                      "them together; 1 spaces them evenly round one cycle.")


PARAMS: dict[str, tuple[Param, ...]] = {
    # -- movement ------------------------------------------------------------
    # A held PLACE, not a move: every head at the same offset from its own
    # calibrated ball aim. Both arguments are absolute angles, so they are
    # bounded by what the rig's heads can actually reach (`reach`), not by a
    # number here; the declared range -- a typical 540/270-degree head's travel
    # -- is only what an editor with no rig to ask falls back to.
    "offset": (
        Param("bearing", "Round", 0.0, min=-270.0, max=270.0, step=1.0,
              unit="°", reach="bearing",
              help="How far round from the ball, in the room's own sense of "
                   "round."),
        Param("elevation", "Up", 0.0, min=-135.0, max=135.0, step=1.0,
              unit="°", reach="elevation",
              help="How far up from the ball. Past 90 goes over the top, which "
                   "is how a head on a short bearing range reaches behind it."),
    ),
    "orbit": (
        Param("radius", "Radius", 20.0, min=0.0, max=90.0, step=1.0, unit="°",
              help="How far out from the aim point it travels."),
        _bars(8.0),
        Param("elongation", "Flatten", 1.0, min=0.2, max=6.0, step=0.1,
              unit="×",
              help="Squashes elevation against bearing. Above 1 is a wide flat "
                   "oval; a corner rig has far more bearing than elevation."),
        _phase_spread(1.0),
    ),
    "pendulum": (
        Param("width", "Swing", 30.0, min=0.0, max=90.0, step=1.0, unit="°",
              help="How far it travels either side of centre."),
        _bars(4.0),
        Param("vertical", "Vertical", False, kind="bool",
              help="Up and down instead of across the room."),
        _phase_spread(0.0),
    ),
    "fan_sweep": (
        Param("width", "Fan", 40.0, min=0.0, max=180.0, step=1.0, unit="°",
              help="How wide the heads are fanned across the room."),
        _bars(4.0),
        # No fixed default: when left out it is half the width, worked out per
        # use. Declared with None so an editor says "auto" instead of a number
        # that would be wrong as soon as the width changed.
        Param("sweep", "Sweep", None, min=0.0, max=180.0, step=1.0, unit="°",
              help="How far the fan swings; half the width when left out."),
        _phase_spread(0.0),
        Param("rate", "Rate", 1.0, min=0.25, max=8.0, step=0.25, unit="×",
              help="How many swings per cycle."),
    ),
    "figure8": (
        Param("width", "Width", 25.0, min=0.0, max=90.0, step=1.0, unit="°"),
        Param("height", "Height", 10.0, min=0.0, max=60.0, step=1.0, unit="°"),
        _bars(8.0),
        _phase_spread(0.0),
    ),
    "spiral": (
        Param("radius", "Radius", 20.0, min=0.0, max=90.0, step=1.0, unit="°",
              help="How far out it reaches by the end of the cycle."),
        Param("turns", "Turns", 2.0, min=0.25, max=8.0, step=0.25,
              help="How many times round on the way out."),
        Param("elongation", "Flatten", 1.5, min=0.2, max=6.0, step=0.1,
              unit="×"),
        Param("direction", "Direction", "out", kind="choice",
              choices=("out", "in"),
              help="Wind out from the aim point, or converge onto it."),
        _bars(16.0),
        _phase_spread(0.0),
    ),
    "scatter": (
        Param("radius", "Reach", 25.0, min=0.0, max=90.0, step=1.0, unit="°",
              help="The widest a head will wander from its aim."),
        Param("stations", "Stops", 4, kind="integer", min=2, max=16,
              help="How many places each head visits per cycle. Fewer is more "
                   "deliberate; more is restless."),
        Param("seed", "Seed", 1, kind="integer", min=0, max=9999,
              help="A different set of directions. The same number always "
                   "gives the same ones -- on the rig and in previz."),
        Param("easing", "Easing", "ease_in_out", kind="choice",
              choices=_EASINGS),
        _bars(4.0),
        _phase_spread(0.0),
    ),
    "aim_points": (
        Param("points", "Points", [[0.5, 0.5, 0.0]], kind="points",
              help="Room fractions, 0..1 on each axis, so it works in any "
                   "room."),
        _bars(8.0),
        Param("easing", "Easing", "ease_in_out", kind="choice",
              choices=_EASINGS),
    ),
    # -- colour --------------------------------------------------------------
    "solid": (
        Param("color", "Colour", "@primary", kind="color"),
    ),
    "color_chase": (
        Param("colors", "Colours", ["@primary", "@secondary"], kind="colors"),
        _bars(2.0),
        _phase_spread(0.0),
        Param("fade", "Fade", 0.0, min=0.0, max=1.0, step=0.05,
              help="How much of each step blends into the next; 0 cuts."),
    ),
    "hue_cycle": (
        Param("hue", "From", 0.0, min=0.0, max=1.0, step=0.01,
              help="Where on the wheel it starts. 0 is red, 0.33 green, "
                   "0.66 blue."),
        Param("span", "Span", 1.0, min=-2.0, max=2.0, step=0.05,
              help="How much of the wheel one cycle covers. A small span "
                   "drifts within a family; negative runs backwards."),
        Param("saturation", "Saturation", 1.0, min=0.0, max=1.0, step=0.02),
        Param("brightness", "Brightness", 1.0, min=0.0, max=1.0, step=0.02),
        _bars(16.0),
        _phase_spread(0.0),
    ),
    "duo": (
        Param("color_a", "Colour A", "@primary", kind="color"),
        Param("color_b", "Colour B", "@secondary", kind="color"),
        Param("blend", "Swap", 0.0, min=0.0, max=1.0, step=0.02,
              help="How far the two trade places over the cycle. 0 holds them "
                   "where they are."),
        _bars(8.0),
    ),
    # -- level ---------------------------------------------------------------
    "chase": (
        Param("order", "Order", "index", kind="choice", choices=_ORDERS,
              help="Along the room's x, y or z by where the fixtures hang, or "
                   "patch order."),
        _bars(1.0),
        Param("width", "Width", 0.5, min=0.05, max=1.0, step=0.05,
              help="The bump's size as a fraction of the cycle."),
    ),
    "pulse": (
        Param("depth", "Depth", 1.0, min=0.0, max=1.0, step=0.05,
              help="1 goes to black between pulses; 0.3 just breathes."),
        _bars(1.0),
        _phase_spread(0.0),
    ),
    "breathe": (
        Param("depth", "Depth", 0.4, min=0.0, max=1.0, step=0.05,
              help="How far down it dips. 1 reaches black."),
        _bars(8.0),
        _phase_spread(0.0),
    ),
    "dim": (
        Param("level", "Level", 1.0, min=0.0, max=1.0, step=0.05),
    ),
    "strobe": (
        Param("level", "Level", 0.5, min=0.0, max=1.0, step=0.05,
              help="Where in the fixture's slow-to-fast band. The strobe "
                   "policy still caps it, after everything."),
    ),
    # -- rig-bound adapters --------------------------------------------------
    "look": (
        Param("look", "Look", None, kind="look",
              help="A look from THIS rig's library."),
    ),
    "snapshot": (
        Param("preset", "Preset", None, kind="preset",
              help="A preset from THIS event."),
    ),
}

# What the old `_NUMERIC` table said by hand, now derived. Integers count:
# `_check_args` only needs to know a value must be a number.
_NUMERIC: dict[str, tuple[str, ...]] = {
    name: tuple(p.name for p in params if p.kind in ("number", "integer"))
    for name, params in PARAMS.items()
    if any(p.kind in ("number", "integer") for p in params)
}

_DEFAULTS: dict[str, dict[str, Any]] = {
    name: {p.name: p.default for p in params} for name, params in PARAMS.items()
}


def _d(block: str, name: str) -> Any:
    """A block argument's declared default. One lookup, so a builder never
    carries its own copy of a number the editor also shows."""
    return _DEFAULTS[block][name]


def param(block: str, name: str) -> Optional[Param]:
    """One argument's declaration, or None if this block has no such thing."""
    return next((p for p in PARAMS.get(block, ()) if p.name == name), None)


def publish() -> dict:
    """Every block, as an editor needs it: its slot and its arguments."""
    return {name: {"slot": SLOT_OF[name],
                   "params": [p.public() for p in PARAMS[name]]}
            for name in BLOCKS}


# -- arguments ----------------------------------------------------------------

@dataclass(frozen=True)
class Blend:
    """A non-numeric automation value mid-curve: `a` becoming `b` by `t`.
    What a colour parameter's automation is between two points."""
    a: Any
    b: Any
    t: float


def parse_hex(text: str) -> Optional[RGB]:
    if (isinstance(text, str) and len(text) == 7 and text[0] == "#"):
        try:
            return tuple(int(text[i:i + 2], 16) / 255.0 for i in (1, 3, 5))  # type: ignore[return-value]
        except ValueError:
            return None
    return None


def mix(a: RGB, b: RGB, t: float) -> RGB:
    return tuple(x + (y - x) * t for x, y in zip(a, b))  # type: ignore[return-value]


class Env:
    """What a block's arguments are resolved against while it runs: its
    routine's parameters, the timeline's automation of them, and the palette.

    The compiler owns one per routine instance (and one for the timeline's own
    clips) and points `palette` and `automate` at the current frame before
    anything evaluates. Blocks hold the Env, never a copy of what it said."""

    def __init__(self, params: Optional[Mapping[str, Any]] = None,
                 look_colors: Optional[Mapping[str, RGB]] = None):
        self.params = dict(params or {})
        self.look_colors = dict(look_colors or {})
        self.palette: dict[str, RGB] = {"primary": WHITE, "secondary": WHITE,
                                        "accent": WHITE}
        # name -> the timeline's automation of `param.<name>` at this beat, or
        # None. Set per frame.
        self.automate: Callable[[str], Any] = lambda name: None

    def param(self, name: str) -> Any:
        auto = self.automate(name)
        return self.params.get(name) if auto is None else auto

    def raw(self, value: Any) -> Any:
        if isinstance(value, str) and value.startswith("$"):
            return self.param(value[1:])
        return value

    def number(self, value: Any, default: float) -> float:
        value = self.raw(value)
        if value is None or isinstance(value, bool):
            return default
        if isinstance(value, (int, float)):
            return float(value)
        return default

    def color(self, value: Any) -> RGB:
        value = self.raw(value)
        if isinstance(value, Blend):
            return mix(self.color(value.a), self.color(value.b), value.t)
        if isinstance(value, str):
            if value.startswith("@"):
                return self.palette.get(value[1:], WHITE)
            rgb = parse_hex(value)
            if rgb is not None:
                return rgb
            return self.look_colors.get(value, WHITE)
        if isinstance(value, (list, tuple)) and len(value) == 3:
            return tuple(max(0.0, min(1.0, float(c))) for c in value)  # type: ignore[return-value]
        return WHITE


def color_problem(value: Any, look_colors: Mapping[str, RGB],
                  params: Mapping[str, Any]) -> Optional[str]:
    """Why a literal colour argument cannot be resolved on this rig."""
    if isinstance(value, str) and value.startswith("$"):
        return None if value[1:] in params else f"{value} is not a parameter"
    if isinstance(value, str):
        if value.startswith("@") or parse_hex(value) is not None:
            return None
        if value in look_colors:
            return None
        return (f"{value!r} is not a palette role, a #hex colour, or a "
                f"single-colour look on this rig")
    if isinstance(value, (list, tuple)) and len(value) == 3 and all(
            isinstance(c, (int, float)) and not isinstance(c, bool) for c in value):
        return None
    return f"{value!r} is not a colour"


# -- a built block ------------------------------------------------------------

@dataclass
class Block:
    """One slot's layers over the fixtures it claims. Empty when it could not
    be built: it then claims nothing and the lane below shows through."""
    slot: str
    layers: tuple[statemod.Layer, ...] = ()
    claims: frozenset[int] = frozenset()
    problems: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Rigging:
    """What a block needs to know about the rig it is being built for."""
    rig: rigmod.Rig
    entries: Mapping[str, libmod.LibraryEntry]
    presets: Sequence[Mapping] = ()
    event: str = ""

    def tagged(self, tag: str) -> tuple[rigmod.PatchedFixture, ...]:
        """Fixtures with this tag -- or this name, as the operator's per-unit
        targets do (`state._targets`)."""
        return tuple(f for f in self.rig.fixtures
                     if tag in f.tags or f.name == tag)

    def look_colors(self) -> dict[str, RGB]:
        return {name: tuple(e.color) for name, e in self.entries.items()
                if e.color is not None}


def _fail(slot: str, problem: str) -> Block:
    return Block(slot, problems=[problem])


def make(name: str, args: Mapping[str, Any],
         fixtures: Sequence[rigmod.PatchedFixture], slot: str, env: Env,
         rigging: Rigging, where: str = "") -> Block:
    """Build one block for one slot over `fixtures` (a role's binding)."""
    label = f"{where}: {name}" if where else name
    if name not in BLOCKS:
        return _fail(slot, f"{label} is not a block; one of {', '.join(BLOCKS)}")
    native = SLOT_OF[name]
    if native is not None and native != slot:
        return _fail(slot, f"{label} is a {native} block, on a {slot} row")
    args = dict(args or {})
    if name in ("look", "snapshot"):
        return _ADAPTERS[name](args, fixtures, slot, env, rigging, label)
    problems = _check_args(name, args, env, rigging)
    if problems:
        return _fail(slot, f"{label}: {'; '.join(problems)}")
    fixtures = tuple(fixtures)
    if not fixtures:
        return Block(slot)                 # an optional role absent on this rig
    if slot == "movement":
        movers = tuple(f for f in fixtures if f.head is not None)
        if not movers:
            return Block(slot)
        layer = _MOVES[name](args, movers, env, rigging)
        if layer is None:
            return _fail(slot, f"{label}: no geometry to aim with on this rig")
        return Block(slot, (layer,), frozenset(f.fid for f in movers))
    layer = _LAYERS[name](args, fixtures, env, rigging)
    return Block(slot, (layer,), frozenset(f.fid for f in fixtures))




def _check_args(name: str, args: Mapping, env: Env, rigging: Rigging) -> list[str]:
    out = []
    for key in _NUMERIC.get(name, ()):
        value = args.get(key)
        if value is None:
            continue
        if isinstance(value, str) and value.startswith("$"):
            if value[1:] not in env.params:
                out.append(f"{key} uses {value}, which is not a parameter")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            out.append(f"{key} must be a number, got {value!r}")
        elif key == "bars" and value <= 0:
            out.append("bars must be more than 0")
    looks = rigging.look_colors()
    if name == "solid":
        if "color" not in args:
            out.append("solid needs a color")
        else:
            p = color_problem(args["color"], looks, env.params)
            if p:
                out.append(f"color: {p}")
    if name == "color_chase":
        colors = args.get("colors")
        if not isinstance(colors, list) or not colors:
            out.append("color_chase needs a list of colors")
        else:
            for c in colors:
                p = color_problem(c, looks, env.params)
                if p:
                    out.append(f"colors: {p}")
    # Every choice argument, from its declaration -- the chase order used to
    # be the only one, checked by hand; spiral, scatter and aim_points now have
    # their own and a hand-written check per block would be a list to forget.
    for spec in PARAMS.get(name, ()):
        if spec.kind == "choice" and spec.name in args:
            if args[spec.name] not in (spec.choices or ()):
                out.append(f"{spec.name} must be one of "
                           f"{', '.join(spec.choices or ())}")
    if name == "duo":
        for key in ("color_a", "color_b"):
            if key in args:
                p = color_problem(args[key], looks, env.params)
                if p:
                    out.append(f"{key}: {p}")
    if name == "aim_points":
        pts = args.get("points")
        if (not isinstance(pts, list) or not pts or not all(
                isinstance(p, list) and len(p) == 3 and all(
                    isinstance(c, (int, float)) and not isinstance(c, bool)
                    for c in p) for p in pts)):
            out.append("points must be a list of [x, y, z] room fractions, "
                       "0..1 on each axis")
    return out


# -- movement -----------------------------------------------------------------

def _spread(k: int, n: int, spread: float, ctx: statemod.EvalContext) -> float:
    """This head's phase offset in cycles: the block's own spread plus the
    operator's spread macro, both spreading the role's heads evenly."""
    return (spread + ctx.move_spread) * (k / n) if n else 0.0


def _move(movers, offset_for: Callable) -> statemod.Layer:
    """A movement layer over exactly these heads, through `offset_aim`."""
    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        n = len(movers)
        for k, f in enumerate(movers):
            state = out[f.fid]
            if state.aim is None:
                continue
            statemod.offset_aim(ctx, state, *offset_for(ctx, k, n))
    return layer


# Each shape is an OFFSET FUNCTION first -- `(ctx, k, n) -> (bearing, elev)`
# for head k of n -- and a layer second, through `_move`. Split so a shape can
# be sampled without a rig: the retirement audit fits ported paths against
# these, and a test can sweep one across a cycle, without standing up fixtures
# whose only job would be to be moved.


def _offset_at(args, env):
    """Hold every head at one offset from its own ball aim.

    The shape of a ported "Heads - Floor": no motion at all, the same place
    for every head, relative to where each is calibrated -- so it tracks a
    recalibration exactly as every other look does.
    """
    def offset_for(ctx, k, n):
        return (env.number(args.get("bearing"), _d("offset", "bearing")),
                env.number(args.get("elevation"), _d("offset", "elevation")))
    return offset_for


def _orbit_at(args, env):
    def offset_for(ctx, k, n):
        radius = env.number(args.get("radius"), _d("orbit", "radius"))
        bars = max(1e-6, env.number(args.get("bars"), _d("orbit", "bars")))
        elong = env.number(args.get("elongation"), _d("orbit", "elongation")) or 1.0
        p = ctx.motion_bar / bars + _spread(
            k, n, env.number(args.get("spread"), _d("orbit", "spread")), ctx)
        theta = 2.0 * math.pi * (p % 1.0)
        return radius * math.sin(theta), radius * math.cos(theta) / elong
    return offset_for


def _pendulum_at(args, env):
    vertical = bool(args.get("vertical", _d("pendulum", "vertical")))

    def offset_for(ctx, k, n):
        width = env.number(args.get("width"), _d("pendulum", "width"))
        bars = max(1e-6, env.number(args.get("bars"), _d("pendulum", "bars")))
        p = ctx.motion_bar / bars + _spread(
            k, n, env.number(args.get("spread"), _d("pendulum", "spread")), ctx)
        value = width * math.sin(2.0 * math.pi * (p % 1.0))
        return (0.0, value) if vertical else (value, 0.0)
    return offset_for


def _fan_sweep_at(args, env):
    """The heads fanned across `width` degrees of bearing, the whole fan
    swinging `sweep` degrees side to side every `bars`."""
    def offset_for(ctx, k, n):
        width = env.number(args.get("width"), _d("fan_sweep", "width"))
        sweep = env.number(args.get("sweep"), width / 2.0)
        bars = max(1e-6, env.number(args.get("bars"), _d("fan_sweep", "bars")))
        rate = env.number(args.get("rate"), _d("fan_sweep", "rate"))
        fan = (k / (n - 1) - 0.5) * width if n > 1 else 0.0
        p = ctx.motion_bar * rate / bars + _spread(
            k, n, env.number(args.get("spread"), _d("fan_sweep", "spread")), ctx)
        return fan + sweep * math.sin(2.0 * math.pi * (p % 1.0)), 0.0
    return offset_for


def _figure8_at(args, env):
    """A horizontal eight: a 1:2 Lissajous. The vertical runs at twice the
    horizontal rate, which is what closes the curve into an eight rather than
    an ellipse -- and the heads cross the middle twice a cycle, so the shape
    reads even when the beams are the only thing visible."""
    def offset_for(ctx, k, n):
        width = env.number(args.get("width"), _d("figure8", "width"))
        height = env.number(args.get("height"), _d("figure8", "height"))
        bars = max(1e-6, env.number(args.get("bars"), _d("figure8", "bars")))
        p = ctx.motion_bar / bars + _spread(
            k, n, env.number(args.get("spread"), _d("figure8", "spread")), ctx)
        theta = 2.0 * math.pi * (p % 1.0)
        return width * math.sin(theta), height * math.sin(2.0 * theta) * 0.5
    return offset_for


def _spiral_at(args, env):
    """Winds out from the aim point and snaps back; `in` is the same curve read
    backwards, which on a mirror ball converges rather than expands."""
    outward = args.get("direction", _d("spiral", "direction")) != "in"

    def offset_for(ctx, k, n):
        radius = env.number(args.get("radius"), _d("spiral", "radius"))
        turns = env.number(args.get("turns"), _d("spiral", "turns"))
        elong = env.number(args.get("elongation"), _d("spiral", "elongation")) or 1.0
        bars = max(1e-6, env.number(args.get("bars"), _d("spiral", "bars")))
        p = (ctx.motion_bar / bars + _spread(
            k, n, env.number(args.get("spread"), _d("spiral", "spread")), ctx)) % 1.0
        theta = 2.0 * math.pi * p * turns
        r = radius * (p if outward else 1.0 - p)
        return r * math.sin(theta), r * math.cos(theta) / elong
    return offset_for


def _scatter_at(args, env):
    """Every head wanders between its own set of directions, one set per seed.

    Random-looking and not random: each station is `motion.sampled`, a hash of
    (seed, head, station), so the same seed gives the same show on the rig, in
    the plan view and in previz, however many frames any of them dropped.

    Stations are WITHIN the cycle and the last lands back on the first, so the
    loop is seamless. One destination per cycle is the obvious design and it
    cannot work: a block only sees its phase, and phase carries no count of
    how many cycles have passed.
    """
    ease = libmod.EASINGS.get(args.get("easing", _d("scatter", "easing")),
                              motion.ease_in_out)

    def offset_for(ctx, k, n):
        radius = env.number(args.get("radius"), _d("scatter", "radius"))
        stations = max(2, int(env.number(args.get("stations"),
                                         _d("scatter", "stations"))))
        seed = int(env.number(args.get("seed"), _d("scatter", "seed")))
        bars = max(1e-6, env.number(args.get("bars"), _d("scatter", "bars")))
        p = (ctx.motion_bar / bars + _spread(
            k, n, env.number(args.get("spread"), _d("scatter", "spread")), ctx)) % 1.0
        x = p * stations
        here = int(x) % stations
        there = (here + 1) % stations
        t = ease(x - math.floor(x))
        b0, e0 = motion.sampled(seed, k, here, 0), motion.sampled(seed, k, here, 1)
        b1, e1 = motion.sampled(seed, k, there, 0), motion.sampled(seed, k, there, 1)
        # Half the excursion vertically: there is far less elevation than
        # bearing before a beam is in the ceiling or the floor, and the taper
        # would spend the difference dimming.
        return (radius * (b0 + (b1 - b0) * t),
                radius * 0.5 * (e0 + (e1 - e0) * t))
    return offset_for


# The shapes that need nothing but their arguments. `aim_points` is not here:
# it converts room fractions through each head's own geometry, so it needs the
# rig and is built by `_aim_points` below.
OFFSETS: dict[str, Callable] = {
    "offset": _offset_at, "orbit": _orbit_at, "pendulum": _pendulum_at, "fan_sweep": _fan_sweep_at,
    "figure8": _figure8_at, "spiral": _spiral_at, "scatter": _scatter_at,
}


def _via(name: str) -> Callable:
    def build(args, movers, env, rigging):
        return _move(movers, OFFSETS[name](args, env))
    return build


def _aim_points(args, movers, env, rigging):
    geometry, venue = rigging.rig.geometry, rigging.rig.venue
    if geometry is None or venue is None:
        return None
    world = [(x * venue.width, y * venue.height, z * venue.depth)
             for x, y, z in args["points"]]
    easing = libmod.EASINGS.get(args.get("easing", _d("aim_points", "easing")),
                                motion.ease_in_out)
    paths = {f.fid: motion.path(motion.points_to_offsets(geometry, f.head, world),
                                easing=easing)
             for f in movers}

    def offset_for(ctx, k, n):
        bars = max(1e-6, env.number(args.get("bars"), _d("aim_points", "bars")))
        f = movers[k]
        return paths[f.fid]((ctx.motion_bar / bars) % 1.0)
    return _move(movers, offset_for)


_MOVES = {**{name: _via(name) for name in OFFSETS}, "aim_points": _aim_points}


# -- colour -------------------------------------------------------------------

def _solid(args, fixtures, env, rigging):
    def layer(ctx, out):
        rgb = env.color(args["color"])
        for f in fixtures:
            out[f.fid].color = rgb
            out[f.fid].white = 0.0
    return layer


def _color_chase(args, fixtures, env, rigging):
    """Step through `colors` every `bars`, staggered across the fixtures by
    `spread` cycles. `fade` (0..1) is how much of each step is spent blending
    into the next; 0 cuts."""
    colors = list(args["colors"])

    def layer(ctx, out):
        bars = max(1e-6, env.number(args.get("bars"), _d("color_chase", "bars")))
        spread = env.number(args.get("spread"), _d("color_chase", "spread"))
        fade = max(0.0, min(1.0, env.number(args.get("fade"),
                                            _d("color_chase", "fade"))))
        resolved = [env.color(c) for c in colors]
        count = len(resolved)
        n = len(fixtures)
        for k, f in enumerate(fixtures):
            p = (ctx.color_bar / bars + (spread * k / n if n else 0.0)) % 1.0
            pos = p * count
            i = int(pos) % count
            t = pos - math.floor(pos)
            rgb = resolved[i]
            if fade > 0 and t > 1.0 - fade:
                rgb = mix(rgb, resolved[(i + 1) % count], (t - (1.0 - fade)) / fade)
            out[f.fid].color = rgb
            out[f.fid].white = 0.0
    return layer


def _hue_cycle(args, fixtures, env, rigging):
    """Walk the colour wheel. Under a full wheel of span it DRIFTS -- out to
    the far end of its span and back -- so a small span stays within a family
    and never jumps back to the start at the cycle boundary. At a full wheel or
    more it ROLLS, which is seamless on its own because the wheel wraps."""
    def layer(ctx, out):
        hue = env.number(args.get("hue"), _d("hue_cycle", "hue"))
        span = env.number(args.get("span"), _d("hue_cycle", "span"))
        sat = max(0.0, min(1.0, env.number(args.get("saturation"),
                                           _d("hue_cycle", "saturation"))))
        val = max(0.0, min(1.0, env.number(args.get("brightness"),
                                           _d("hue_cycle", "brightness"))))
        bars = max(1e-6, env.number(args.get("bars"), _d("hue_cycle", "bars")))
        spread = env.number(args.get("spread"), _d("hue_cycle", "spread"))
        n = len(fixtures)
        for k, f in enumerate(fixtures):
            cycles = ctx.color_bar / bars + (spread * k / n if n else 0.0)
            if abs(span) >= 1.0:
                h = hue + span * cycles
            else:
                p = cycles % 1.0
                h = hue + span * (2.0 * p if p < 0.5 else 2.0 - 2.0 * p)
            out[f.fid].color = colorsys.hsv_to_rgb(h % 1.0, sat, val)
            out[f.fid].white = 0.0
    return layer


def _duo(args, fixtures, env, rigging):
    """Two colours alternating across the fixtures, so on four corner heads
    they land diagonally opposite and read across the room -- a contiguous
    split reads as one half being wrong. `blend` trades them over the cycle."""
    def layer(ctx, out):
        a = env.color(args.get("color_a", _d("duo", "color_a")))
        b = env.color(args.get("color_b", _d("duo", "color_b")))
        blend = max(0.0, min(1.0, env.number(args.get("blend"), _d("duo", "blend"))))
        bars = max(1e-6, env.number(args.get("bars"), _d("duo", "bars")))
        t = blend * 0.5 * (1.0 - math.cos(2.0 * math.pi * ((ctx.color_bar / bars) % 1.0)))
        for k, f in enumerate(fixtures):
            near, far = (a, b) if k % 2 == 0 else (b, a)
            out[f.fid].color = mix(near, far, t)
            out[f.fid].white = 0.0
    return layer


# -- level --------------------------------------------------------------------

def _ordered(fixtures, order: str):
    if order == "index":
        return list(fixtures)
    axis = {"x": 0, "y": 1, "z": 2}[order[-1]]
    sign = -1.0 if order.startswith("-") else 1.0
    placed = [f for f in fixtures if f.position is not None]
    unplaced = [f for f in fixtures if f.position is None]
    placed.sort(key=lambda f: sign * f.position[axis])
    return placed + unplaced


def _chase(args, fixtures, env, rigging):
    """A brightness bump travelling across the fixtures in `order` -- along
    the room's x, y or z by where they hang, or patch order."""
    ordered = _ordered(fixtures, args.get("order", _d("chase", "order")))
    rank = {f.fid: i for i, f in enumerate(ordered)}

    def layer(ctx, out):
        bars = max(1e-6, env.number(args.get("bars"), _d("chase", "bars")))
        width = env.number(args.get("width"), _d("chase", "width"))
        level_at = motion.chase(len(ordered), bars=bars, width=width)
        p = (ctx.level_bar / bars) % 1.0
        for f in fixtures:
            out[f.fid].intensity *= level_at(rank[f.fid], p)
    return layer


def _pulse(args, fixtures, env, rigging):
    def layer(ctx, out):
        bars = max(1e-6, env.number(args.get("bars"), _d("pulse", "bars")))
        depth = env.number(args.get("depth"), _d("pulse", "depth"))
        spread = env.number(args.get("spread"), _d("pulse", "spread"))
        n = len(fixtures)
        for k, f in enumerate(fixtures):
            t = (ctx.level_bar / bars + (spread * k / n if n else 0.0)) % 1.0
            out[f.fid].intensity *= 1.0 - depth * motion.ease_out(t)
    return layer


def _dim(args, fixtures, env, rigging):
    def layer(ctx, out):
        level = max(0.0, env.number(args.get("level"), _d("dim", "level")))
        for f in fixtures:
            out[f.fid].intensity *= level
    return layer


def _strobe(args, fixtures, env, rigging):
    """Open the shutter into a strobe at `level` across the fixture's own
    slow-to-fast band. The strobe policy still caps it, after everything."""
    def layer(ctx, out):
        level = max(0.0, min(1.0, env.number(args.get("level"),
                                             _d("strobe", "level"))))
        for f in fixtures:
            out[f.fid].strobe = level
    return layer


def _breathe(args, fixtures, env, rigging):
    """A slow swell in and out. Unlike `pulse` it is symmetrical, so it has no
    attack -- the thing to reach for under a held colour. A MULTIPLIER, like
    every level block, so the master and the safety taper still govern it."""
    def layer(ctx, out):
        depth = max(0.0, min(1.0, env.number(args.get("depth"),
                                             _d("breathe", "depth"))))
        bars = max(1e-6, env.number(args.get("bars"), _d("breathe", "bars")))
        spread = env.number(args.get("spread"), _d("breathe", "spread"))
        n = len(fixtures)
        for k, f in enumerate(fixtures):
            p = (ctx.level_bar / bars + (spread * k / n if n else 0.0)) % 1.0
            out[f.fid].intensity *= 1.0 - depth * 0.5 * (1.0 - math.cos(2.0 * math.pi * p))
    return layer


_LAYERS = {"solid": _solid, "color_chase": _color_chase, "hue_cycle": _hue_cycle,
           "duo": _duo, "chase": _chase, "pulse": _pulse, "breathe": _breathe,
           "dim": _dim, "strobe": _strobe}


# -- the rig-bound adapters ---------------------------------------------------

def look_block(entry: libmod.LibraryEntry, slot: str,
               fixtures: Optional[Sequence[rigmod.PatchedFixture]],
               rigging: Rigging) -> Block:
    """A ported look's layers for one slot, claiming the fixtures it writes
    there -- narrowed to `fixtures` when given. Nothing for a slot the look
    does not fill."""
    show = statemod.Show()
    if slot == "movement" and entry.is_movement:
        libmod.movement_layers(show, entry)
        layers, writes = show.movement, rigging.tagged("movers")
    elif slot == "color" and entry.is_color:
        libmod.color_layers(show, entry)
        layers = show.color
        writes = (tuple(f for g in entry.groups for f in rigging.tagged(g))
                  or tuple(rigging.rig.fixtures))
    elif slot == "level" and entry.is_level:
        libmod.level_layers(show, entry)
        layers = show.fx
        writes = (tuple(f for g in entry.groups for f in rigging.tagged(g))
                  or tuple(rigging.rig.fixtures))
    else:
        return Block(slot)
    claims = {f.fid for f in writes}
    if fixtures is not None:
        claims &= {f.fid for f in fixtures}
    return Block(slot, tuple(layers), frozenset(claims))


def _look(args, fixtures, slot, env, rigging, label) -> Block:
    name = env.raw(args.get("look"))
    entry = rigging.entries.get(name) if isinstance(name, str) else None
    if entry is None:
        return _fail(slot, f"{label}: look {name!r} is not in this rig's "
                           f"library ({rigging.event or 'this event'})")
    return look_block(entry, slot, fixtures, rigging)


def snapshot_block(spec: Mapping[str, Any], slot: str,
                   fixtures: Optional[Sequence[rigmod.PatchedFixture]],
                   rigging: Rigging, label: str) -> Block:
    """A preset's looks for one slot: `{"preset": name}`, or its maps
    `{"movement": {group: look}, ...}`, the maps winning over the preset's.
    A preset's speed, rates and master are the operator's, not a slot's, and
    are not taken."""
    maps: dict[str, dict] = {}
    name = spec.get("preset")
    if name:
        preset = next((p for p in rigging.presets if p.get("name") == name), None)
        if preset is None:
            return _fail(slot, f"{label}: preset {name!r} is not in this "
                               f"event's presets")
        for s in statemod.SLOTS:
            maps[s] = dict(preset.get(s) or {})
    for s in statemod.SLOTS:
        if spec.get(s):
            maps.setdefault(s, {}).update(spec[s])
    layers: list = []
    claims: set[int] = set()
    problems: list[str] = []
    for group, look in (maps.get(slot) or {}).items():
        entry = rigging.entries.get(look)
        if entry is None:
            problems.append(f"{label}: look {look!r} is not in this rig's library")
            continue
        block = look_block(entry, slot, rigging.tagged(group) or None, rigging)
        layers.extend(block.layers)
        claims |= block.claims
    if fixtures is not None:
        claims &= {f.fid for f in fixtures}
    return Block(slot, tuple(layers), frozenset(claims), problems)


def _snapshot(args, fixtures, slot, env, rigging, label) -> Block:
    return snapshot_block(args, slot, fixtures, rigging, label)


_ADAPTERS = {"look": _look, "snapshot": _snapshot}
