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

import math
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping, Optional, Sequence

from . import library as libmod
from . import motion
from . import rig as rigmod
from . import state as statemod

BLOCKS = ("fan_sweep", "orbit", "pendulum", "aim_points", "solid",
          "color_chase", "chase", "pulse", "dim", "strobe", "look", "snapshot")

SLOT_OF: dict[str, Optional[str]] = {
    "orbit": "movement", "pendulum": "movement", "fan_sweep": "movement",
    "aim_points": "movement", "solid": "color", "color_chase": "color",
    "chase": "level", "pulse": "level", "dim": "level", "strobe": "level",
    "look": None, "snapshot": None,                  # whichever slot they are on
}

RGB = tuple[float, float, float]
WHITE: RGB = (1.0, 1.0, 1.0)


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


_NUMERIC = {
    "orbit": ("radius", "bars", "elongation", "spread"),
    "pendulum": ("width", "bars", "spread"),
    "fan_sweep": ("width", "bars", "sweep", "spread", "rate"),
    "aim_points": ("bars",),
    "color_chase": ("bars", "spread", "fade"),
    "chase": ("bars", "width"),
    "pulse": ("depth", "bars", "spread"),
    "dim": ("level",),
    "strobe": ("level",),
}
_ORDERS = ("x", "-x", "y", "-y", "z", "-z", "index")


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
    if name == "chase" and args.get("order", "index") not in _ORDERS:
        out.append(f"order must be one of {', '.join(_ORDERS)}")
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


def _orbit(args, movers, env, rigging):
    def offset_for(ctx, k, n):
        radius = env.number(args.get("radius"), 20.0)
        bars = max(1e-6, env.number(args.get("bars"), 8.0))
        elong = env.number(args.get("elongation"), 1.0) or 1.0
        p = ctx.motion_bar / bars + _spread(k, n, env.number(args.get("spread"), 1.0), ctx)
        theta = 2.0 * math.pi * (p % 1.0)
        return radius * math.sin(theta), radius * math.cos(theta) / elong
    return _move(movers, offset_for)


def _pendulum(args, movers, env, rigging):
    vertical = bool(args.get("vertical"))

    def offset_for(ctx, k, n):
        width = env.number(args.get("width"), 30.0)
        bars = max(1e-6, env.number(args.get("bars"), 4.0))
        p = ctx.motion_bar / bars + _spread(k, n, env.number(args.get("spread"), 0.0), ctx)
        value = width * math.sin(2.0 * math.pi * (p % 1.0))
        return (0.0, value) if vertical else (value, 0.0)
    return _move(movers, offset_for)


def _fan_sweep(args, movers, env, rigging):
    """The heads fanned across `width` degrees of bearing, the whole fan
    swinging `sweep` degrees side to side every `bars`."""
    def offset_for(ctx, k, n):
        width = env.number(args.get("width"), 40.0)
        sweep = env.number(args.get("sweep"), width / 2.0)
        bars = max(1e-6, env.number(args.get("bars"), 4.0))
        rate = env.number(args.get("rate"), 1.0)
        fan = (k / (n - 1) - 0.5) * width if n > 1 else 0.0
        p = ctx.motion_bar * rate / bars + _spread(k, n, env.number(args.get("spread"), 0.0), ctx)
        return fan + sweep * math.sin(2.0 * math.pi * (p % 1.0)), 0.0
    return _move(movers, offset_for)


def _aim_points(args, movers, env, rigging):
    geometry, venue = rigging.rig.geometry, rigging.rig.venue
    if geometry is None or venue is None:
        return None
    world = [(x * venue.width, y * venue.height, z * venue.depth)
             for x, y, z in args["points"]]
    easing = libmod.EASINGS.get(args.get("easing", "ease_in_out"), motion.ease_in_out)
    paths = {f.fid: motion.path(motion.points_to_offsets(geometry, f.head, world),
                                easing=easing)
             for f in movers}

    def offset_for(ctx, k, n):
        bars = max(1e-6, env.number(args.get("bars"), 8.0))
        f = movers[k]
        return paths[f.fid]((ctx.motion_bar / bars) % 1.0)
    return _move(movers, offset_for)


_MOVES = {"orbit": _orbit, "pendulum": _pendulum, "fan_sweep": _fan_sweep,
          "aim_points": _aim_points}


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
        bars = max(1e-6, env.number(args.get("bars"), 2.0))
        spread = env.number(args.get("spread"), 0.0)
        fade = max(0.0, min(1.0, env.number(args.get("fade"), 0.0)))
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
    ordered = _ordered(fixtures, args.get("order", "index"))
    rank = {f.fid: i for i, f in enumerate(ordered)}

    def layer(ctx, out):
        bars = max(1e-6, env.number(args.get("bars"), 1.0))
        width = env.number(args.get("width"), 0.5)
        level_at = motion.chase(len(ordered), bars=bars, width=width)
        p = (ctx.level_bar / bars) % 1.0
        for f in fixtures:
            out[f.fid].intensity *= level_at(rank[f.fid], p)
    return layer


def _pulse(args, fixtures, env, rigging):
    def layer(ctx, out):
        bars = max(1e-6, env.number(args.get("bars"), 1.0))
        depth = env.number(args.get("depth"), 1.0)
        spread = env.number(args.get("spread"), 0.0)
        n = len(fixtures)
        for k, f in enumerate(fixtures):
            t = (ctx.level_bar / bars + (spread * k / n if n else 0.0)) % 1.0
            out[f.fid].intensity *= 1.0 - depth * motion.ease_out(t)
    return layer


def _dim(args, fixtures, env, rigging):
    def layer(ctx, out):
        level = max(0.0, env.number(args.get("level"), 1.0))
        for f in fixtures:
            out[f.fid].intensity *= level
    return layer


def _strobe(args, fixtures, env, rigging):
    """Open the shutter into a strobe at `level` across the fixture's own
    slow-to-fast band. The strobe policy still caps it, after everything."""
    def layer(ctx, out):
        level = max(0.0, min(1.0, env.number(args.get("level"), 0.5)))
        for f in fixtures:
            out[f.fid].strobe = level
    return layer


_LAYERS = {"solid": _solid, "color_chase": _color_chase, "chase": _chase,
           "pulse": _pulse, "dim": _dim, "strobe": _strobe}


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
