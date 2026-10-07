"""What a knob is: its range, its label, and the sentence under it.

The engine holds parameters rather than stored DMX -- that is the whole premise,
stated in `state.py` and `docs/engine.md`. It was true of the evaluation core and
false of everything the operator touched, because **nothing described a
parameter**. A range was three unrelated literals:

    engine/server.py   `_cmd_macro`   size clamped 0..3
    engine/config.py   `Spec`         min/max, for config files only
    ui/src/tabs/Move.tsx              `row("size", "Size", 0, 3, 0.05, ...)`

Nothing tied them together, so widening a range meant editing three files in two
languages and the UI could silently offer a value the engine would clamp. Worse,
adding a knob meant hand-writing a control for it, which is why 206 ported looks
have no tunable anything: there was no way to describe a number well enough for
the console to render it.

A `Param` is that description. It carries what a validator needs (type, range)
AND what a person needs (a label, a unit, a sentence), because the two were
never actually separate -- `config.Spec.fix` already discovered this for config
files, and `Param` is the same idea aimed at a live control surface.

The payoff is that a parameter is declared once and everything downstream is
derived:

  * the engine clamps against it,
  * the snapshot publishes it,
  * the UI renders a control it has never heard of,
  * `modulate` knows what range to swing through,
  * `vary` knows what range to perturb within.

Deliberately NOT `config.Spec`. `Spec` validates a *file* -- it is about what
may be loaded, reports every problem at once, and has no notion of a current
value, a step size or a display unit. `Param` describes a *live control*, where
the answer to an out-of-range value is to clamp it and carry on rather than to
refuse the show. They overlap on min/max and nothing else, and merging them
would give one class two jobs with different failure behaviour.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Iterable, Optional, Sequence

Number = (int, float)

# The kinds a control can be. Kept small on purpose: every kind here is a
# control the UI actually implements, and a kind with no renderer is a parameter
# that silently cannot be edited.
#
# The last four exist because block arguments need them, and the routine
# editor already renders each: `colors` is color_chase's list, `points` is
# aim_points' room fractions, `look` and `preset` name things in THIS rig's
# library. They are validated by `blocks._check_args`, which knows what a
# palette role or a look name is on a given rig -- `coerce` passes them through.
KINDS = ("number", "integer", "bool", "choice", "color",
         "colors", "points", "look", "preset")

# Values of these kinds are described here but checked where the rig is known.
PASSTHROUGH = ("colors", "points", "look", "preset")

# The two axes a head travels on, as offsets from its own calibrated ball aim.
AXES = ("bearing", "elevation")

# A rig's reach: per axis, the (lowest, highest) offset from the ball that ANY
# of its heads can physically get to. See `bounds`.
Reach = dict[str, tuple[float, float]]


class ParamError(ValueError):
    """A parameter value that cannot be used.

    Raised only for things clamping cannot fix -- an unknown key, a choice that
    is not among the choices, a number that is not finite. An out-of-range
    number is NOT an error: a fader
    pushed past its end is an operator asking for the end, and refusing the
    whole command because one number was 1.02 would drop the other three
    parameters in the same message.
    """


@dataclass(frozen=True)
class Param:
    """One knob.

    `label` and `help` are not decoration. A parameter published to the console
    with no label renders as `radius_deg`, and the whole point of publishing
    descriptors is that the UI can present a parameter it has never heard of.
    The name is for the wire; the label is for the person.
    """
    name: str
    label: str
    default: Any
    kind: str = "number"
    min: Optional[float] = None
    max: Optional[float] = None
    # The granularity of the control, not of the value. Motion is computed in
    # floats throughout -- this only says how far one notch of a slider moves,
    # which matters because a 0..3 size on a phone screen needs 0.05 and a
    # -180..180 bearing needs 1.
    step: Optional[float] = None
    unit: str = ""
    choices: Optional[tuple[Any, ...]] = None
    help: str = ""
    # An ABSOLUTE angle from the ball -- the centre macro, an `offset` block --
    # whose real range is what the rig's heads can physically reach on that
    # axis, not a number written here. `min`/`max` are then only the fallback
    # for when there is no rig to ask (the routine editor, which is rig-free on
    # purpose). An excursion like an orbit's radius is NOT reach-bounded: it is
    # a size, not a place, and a head that runs out of travel on one is caught
    # per head at the rail instead.
    reach: Optional[str] = None
    # A MUSICAL decision rather than a shape -- how the routine sits against
    # the track, like a cycle length in bars. `vary` leaves these alone: rolling
    # a new one turns a 16-bar swell into a 3.75-bar one that fits nothing. A
    # flag on the declaration rather than a list of names somewhere else, so a
    # new block's musical argument cannot be forgotten by a caller.
    musical: bool = False

    def __post_init__(self) -> None:
        # Checked at construction because every Param is a module-level
        # constant: a bad one is a programming error that should surface on
        # import, in a test, rather than as a control that renders wrongly at a
        # venue.
        if self.kind not in KINDS:
            raise ParamError(
                f"{self.name!r} has kind {self.kind!r} -- one of "
                f"{', '.join(KINDS)}")
        if self.kind == "choice" and not self.choices:
            raise ParamError(f"{self.name!r} is a choice with no choices")
        if self.reach not in (None, *AXES):
            raise ParamError(
                f"{self.name!r} is bounded by reach {self.reach!r} -- one of "
                f"{', '.join(AXES)}")
        if (self.min is not None and self.max is not None
                and self.min > self.max):
            raise ParamError(
                f"{self.name!r} has min {self.min} above max {self.max}")

    def coerce(self, value: Any) -> Any:
        """One value, forced into this parameter's shape.

        Clamps rather than refuses -- see `ParamError`. The exceptions are the
        kinds where there is no nearest legal value to clamp to: a choice that
        is not in the list, and a color that is not three numbers.
        """
        if self.kind == "bool":
            return bool(value)

        if self.kind in PASSTHROUGH:
            return value

        if self.kind == "choice":
            if value not in (self.choices or ()):
                raise ParamError(
                    f"{self.name!r} must be one of "
                    f"{', '.join(repr(c) for c in self.choices or ())}, "
                    f"got {value!r}")
            return value

        if self.kind == "color":
            # A block's color argument can also be a palette role ("@primary"),
            # a hex color, a `$parameter` or a single-color look's name.
            # Those resolve against a rig, in `blocks.Env.color`; only a literal
            # [r, g, b] is clamped here.
            if isinstance(value, str):
                return value
            if not isinstance(value, (list, tuple)) or len(value) < 3:
                raise ParamError(
                    f"{self.name!r} is a color and needs [r, g, b] in 0..1, "
                    f"got {value!r}")
            return tuple(max(0.0, min(1.0, float(c))) for c in value[:3])

        # bool before Number, because bool is a subclass of int in Python and
        # `True` would otherwise sail through as 1 -- the same trap
        # `config._check` documents.
        if isinstance(value, bool) or not isinstance(value, Number):
            raise ParamError(
                f"{self.name!r} must be a number, got {value!r} "
                f"({type(value).__name__})")

        return self.within(value, self.min, self.max)

    def within(self, value: Any, lo: Optional[float],
               hi: Optional[float]) -> Any:
        """A number, type-checked as `coerce` does, clamped to (lo, hi).

        Separate from `coerce` so a reach-bounded parameter can be clamped to
        the RIG's range without first being clamped to the declared fallback --
        which would cut a head that can reach 180.35 degrees off at 180.
        """
        if isinstance(value, bool) or not isinstance(value, Number):
            raise ParamError(
                f"{self.name!r} must be a number, got {value!r} "
                f"({type(value).__name__})")
        number = float(value)
        # Refused, not clamped: `json.loads` accepts the literals NaN and
        # Infinity, and `max`/`min` pass NaN straight through. One NaN on a
        # macro made every later frame raise on its way to a DMX integer, and
        # there is no "nearest legal value" to a number that is not one.
        if not math.isfinite(number):
            raise ParamError(
                f"{self.name!r} must be a finite number, got {value!r}")
        if lo is not None:
            number = max(lo, number)
        if hi is not None:
            number = min(hi, number)
        return int(round(number)) if self.kind == "integer" else number

    def public(self) -> dict:
        """The descriptor as the console receives it.

        Keys that are None are omitted rather than sent as null, because the UI
        branches on presence -- a `min` of null and a missing `min` would be two
        spellings of "unbounded" and the second one is the one TypeScript's
        optional-property types already express.
        """
        out: dict[str, Any] = {"name": self.name, "label": self.label,
                               "kind": self.kind, "default": self.default}
        for key in ("min", "max", "step"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.unit:
            out["unit"] = self.unit
        if self.choices:
            out["choices"] = list(self.choices)
        if self.reach:
            out["reach"] = self.reach
        if self.help:
            out["help"] = self.help
        return out


def bounds(param: Param, reach: Optional[Reach] = None
           ) -> tuple[Optional[float], Optional[float]]:
    """The range this parameter really has, on this rig.

    For a reach-bounded parameter that is the rig's reach on its axis, when the
    rig is known; otherwise the declared min and max. One function, so the
    console's slider, the engine's clamp, a modulator's swing and `vary` all ask
    the same question and get the same answer.
    """
    if param.reach and reach and param.reach in reach:
        return reach[param.reach]
    return param.min, param.max


def clamp(param: Param, value: Any, reach: Optional[Reach] = None) -> Any:
    """`coerce`, but against the range this rig really has."""
    if param.kind in ("number", "integer"):
        return param.within(value, *bounds(param, reach))
    return param.coerce(value)


def defaults(params: Sequence[Param]) -> dict[str, Any]:
    return {p.name: p.default for p in params}


def resolve(params: Sequence[Param], values: Optional[dict] = None, *,
            strict: bool = True, reach: Optional[Reach] = None) -> dict[str, Any]:
    """Declared defaults, with `values` layered over them and clamped.

    `strict` is the difference between two callers with genuinely different
    needs, and getting it wrong in either direction is a real failure:

      * **A command from the console** is strict. A typo'd key there means the
        UI and the engine disagree about what a routine has, and silently
        dropping it produces a slider that appears to work and changes nothing
        -- the single most confusing failure a console can have.

      * **A routine loaded from a file** is not, and is validated by
        `config.Spec` on the way in instead. A `parametric_looks.json` hand-edited at a
        venue against a slightly older engine should lose the key it does not
        understand and still light the room.
    """
    known = {p.name: p for p in params}
    out = {p.name: p.default for p in params}
    if not values:
        return out

    unknown = [k for k in values if k not in known]
    if unknown and strict:
        raise ParamError(
            f"no parameter named {', '.join(repr(k) for k in sorted(unknown))} "
            f"-- this one has {', '.join(sorted(known)) or 'none'}")

    for key, value in values.items():
        param = known.get(key)
        if param is not None:
            out[key] = clamp(param, value, reach)
    return out


def find(params: Sequence[Param], name: str) -> Optional[Param]:
    return next((p for p in params if p.name == name), None)


def publish(params: Iterable[Param]) -> list[dict]:
    return [p.public() for p in params]


# -------------------------------------------------------------- shape macros --
#
# The four live shape controls, declared here rather than as literals in
# `_cmd_macro` and again in `Move.tsx`. These ranges were already agreed -- this
# only moves them somewhere both ends can read.
#
# They are the variations that actually recurred across the ported library:
# how far a move travels, whether the heads do it together, and where the whole
# thing sits. One "Ball Wave" with the centre dropped IS the "Floor Wave" that
# used to be a separate stored scene.

# `unit` carries the whole suffix, including the words. "90° round" and
# "40° up/down" say which of the two centre axes you are looking at without
# having to read the label beside it, and both were in the hand-written controls
# these replaced -- a generic renderer that dropped them would be a regression
# dressed up as a refactor.
SIZE = Param(
    "size", "Size", 1.0, min=0.0, max=3.0, step=0.05, unit="×",
    help="How far the route travels. 0 parks every head on the mirror ball.")

SPREAD = Param(
    "spread", "Spread", 0.0, min=-1.0, max=1.0, step=0.02,
    help="Lags each head along its own route. 0 is unison, 1 spreads them "
         "evenly around one cycle.")

# The CENTRE macros are bounded by the rig's reach, not by these numbers.
# Different fixtures travel different distances -- a 540-degree pan and a
# 360-degree one are both common -- and one global limit either cut the better
# fixture short or offered the lesser one somewhere it cannot go. On a loaded
# rig the range is what its most capable head can reach; a head with less
# travel stops at its own rail and the console says so (`at_limit`). The
# numbers here are only the fallback when there is no rig to ask.
CENTER_BEARING = Param(
    "bearing", "Centre —", 0.0, min=-180.0, max=180.0, step=1.0, unit="° round",
    reach="bearing",
    help="Swings the whole look around the room.")

CENTER_ELEV = Param(
    "elev", "Centre |", 0.0, min=-90.0, max=90.0, step=1.0, unit="° up/down",
    reach="elevation",
    help="Drops or lifts the whole look. The safety taper still runs after "
         "this, so aiming down does not bypass it.")

MACROS: tuple[Param, ...] = (SIZE, SPREAD, CENTER_BEARING, CENTER_ELEV)
