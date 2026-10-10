"""Parameters that move on their own.

Everything else in this engine is a parameter someone sets. A modulator is a
parameter someone *shapes*: bind one to a musical waveform and the size breathes
over sixteen bars, the spread opens across a phrase, the orbit's radius tracks
how hard the room is going.

This is the thing the old console could not express at all, and not for want of
trying -- a stored scene has no parameters, so "the same look but slowly
widening" was a chase of twenty scenes that stepped, and stepped visibly. It is
also the thing the engine could not express until parameters were *declared*:
without `params.Param` there was no range to swing through and no way to know
what a target even was.

**Musical position, not slot phase.** A modulator reads `ctx.bar`, so "one swell
every 16 bars" is true regardless of what the movement rate is set to. Reading
the slot phase instead would mean doubling the movement rate silently doubled
every LFO on it, and a breathing size that changes period when you speed the
route up is a different effect than the one that was dialled in.

**Bounded by the target's own declaration.** `low` and `high` default to the
target `Param`'s min and max and are clamped to them, so a modulator cannot
drive a parameter anywhere the operator could not have dragged it to. And since
the output is a parameter like any other, `apply_safety` still runs after the
whole stack: a modulated beam is tapered exactly as a static one is.

**Nothing here is stateful.** Every shape is a pure function of musical position
(and, for `hold`, a hash of the cycle number). That is what keeps the rig, the
plan view and previz agreeing after a dropped frame, and it is the same
reasoning as `waves.sampled`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace
from typing import Callable, Optional

from . import params as parammod
from . import waves


# The shapes, and what each is for. Kept small: a shape nobody can name the use
# of is a control that gets scrolled past.
# `energy` is the console's alone: a show folder has no room to listen to.
SHAPES = waves.SHAPES + ("energy",)

# Which of them ignore `bars` entirely, because they are not functions of
# musical position at all.
POSITIONAL = ("energy",)


class ModulatorError(ValueError):
    """A modulator that cannot be built -- an unknown shape or target."""


@dataclass(frozen=True)
class Modulator:
    """One parameter, swinging between two values.

    `look` None means this targets a shape macro (`size`, `spread`, `bearing`,
    `elev`); otherwise it names the routine whose parameter to drive. Two fields
    rather than one dotted string because look names contain spaces and slashes
    ("Duo Pink/Cyan"), and a delimiter that can appear in the data is a parser
    waiting to be wrong.
    """
    param: str
    look: Optional[str] = None
    shape: str = "sine"
    bars: float = 16.0
    low: float = 0.0
    high: float = 1.0
    # In CYCLES, like every other offset in this engine -- so two modulators a
    # half-cycle apart stay that way at any period.
    phase: float = 0.0
    seed: int = 0

    def __post_init__(self) -> None:
        if self.shape not in SHAPES:
            raise ModulatorError(
                f"no shape {self.shape!r} -- one of {', '.join(SHAPES)}")
        if self.bars <= 0 and self.shape not in POSITIONAL:
            raise ModulatorError(
                f"{self.shape!r} needs a positive cycle length, not {self.bars}")

    @property
    def key(self) -> tuple[str, str]:
        return (self.look or "", self.param)

    def unit(self, bar: float, energy: float = 0.0) -> float:
        """The waveform, 0..1, before it is mapped onto low..high."""
        if self.shape == "energy":
            # Not a function of time. Auto mode's energy axis as a control
            # voltage: the routine responds to the room rather than to a clock.
            return max(0.0, min(1.0, energy))

        # The same shapes a show-folder lane's wave follows (`waves`), so a
        # sine dialled in here and one drawn in a routine are one sine.
        return waves.unit(self.shape, bar / self.bars + self.phase, self.seed)

    def value(self, bar: float, energy: float = 0.0) -> float:
        return self.low + (self.high - self.low) * self.unit(bar, energy)

    def public(self) -> dict:
        out = {"param": self.param, "shape": self.shape, "bars": self.bars,
               "low": self.low, "high": self.high, "phase": self.phase}
        if self.look:
            out["look"] = self.look
        if self.shape == "hold":
            out["seed"] = self.seed
        return out


def build(spec: dict, target: parammod.Param,
          reach: Optional[parammod.Reach] = None) -> Modulator:
    """One modulator from a command or a config block, bounded by its target.

    `low` and `high` default to the target's full range and are clamped to it,
    which is what makes this safe to expose: a modulator can only sweep where a
    finger could have dragged. It also means "modulate Size" needs no numbers
    at all to do something sensible.

    "Its full range" is the RIG's for a reach-bounded target -- a centre swung
    by a modulator goes exactly as far as the slider for it would, which on a
    rig with more travel than the fallback is further, not less.

    Inverted bounds are kept rather than corrected. `low` above `high` is a
    legitimate way to say "run it backwards", and silently swapping them would
    make a ramp that was authored to fall quietly climb instead.
    """
    if target.kind not in ("number", "integer"):
        raise ModulatorError(
            f"{target.name!r} is a {target.kind}, and only numbers can be "
            f"modulated -- there is no halfway between two choices")

    floor, ceiling = parammod.bounds(target, reach)

    def number(key: str, fallback: float) -> float:
        # Finite, or refused. `json.loads` accepts NaN and Infinity, a NaN
        # phase makes every value NaN, and a macro left at NaN made every later
        # frame raise on its way to DMX -- surviving `modulate_clear`, since a
        # cleared macro keeps its last value on purpose.
        value = spec.get(key)
        if value is None:
            return fallback
        try:
            out = float(value)
        except (TypeError, ValueError):
            raise ModulatorError(f"{key} must be a number, got {value!r}") from None
        if isinstance(value, bool) or not math.isfinite(out):
            raise ModulatorError(f"{key} must be a finite number, got {value!r}")
        return out

    def bound(key: str, fallback: float) -> float:
        value = number(key, fallback)
        if floor is not None:
            value = max(floor, value)
        if ceiling is not None:
            value = min(ceiling, value)
        return value

    lo = floor if floor is not None else 0.0
    hi = ceiling if ceiling is not None else 1.0
    return Modulator(
        param=target.name,
        look=spec.get("look"),
        shape=str(spec.get("shape", "sine")),
        bars=number("bars", 16.0),
        low=bound("low", lo),
        high=bound("high", hi),
        phase=number("phase", 0.0),
        seed=int(number("seed", 0)))


class Rack:
    """Every modulator that is running, and what they resolved to this frame.

    One per show. Keyed by (look, param) so binding the same target twice
    replaces rather than stacking -- two LFOs fighting over one number is not a
    thing anyone means to ask for, and the result would depend on dict order.
    """

    def __init__(self) -> None:
        self.by_key: dict[tuple[str, str], Modulator] = {}

    # Every change REBINDS `by_key` rather than editing it: the server's 10 Hz
    # snapshot thread walks it (`status`) while commands land on the frame
    # thread, and a dict changing size mid-iteration fails that snapshot.

    def add(self, mod: Modulator) -> None:
        self.by_key = {**self.by_key, mod.key: mod}

    def remove(self, look: Optional[str], param: str) -> bool:
        key = (look or "", param)
        if key not in self.by_key:
            return False
        self.by_key = {k: v for k, v in self.by_key.items() if k != key}
        return True

    def clear(self) -> None:
        self.by_key = {}

    def rename(self, old: str, new: str) -> None:
        """A look was renamed: its modulators follow the name."""
        if any(mod.look == old for mod in self.by_key.values()):
            mods = [replace(mod, look=new) if mod.look == old else mod
                    for mod in self.by_key.values()]
            self.by_key = {mod.key: mod for mod in mods}

    def keep(self, wanted: Callable[[Modulator], bool]) -> None:
        """Drop every modulator `wanted` says no to -- the ones left aimed at
        a look, or a parameter, the library no longer has."""
        if not all(wanted(mod) for mod in self.by_key.values()):
            self.by_key = {k: mod for k, mod in self.by_key.items() if wanted(mod)}

    def __len__(self) -> int:
        return len(self.by_key)

    def resolve(self, bar: float, energy: float = 0.0
                ) -> tuple[dict[str, float], dict[str, dict[str, float]]]:
        """This frame's values: (macros by name, routine params by look).

        Split because the two land in different places -- a macro is a field on
        the context that `move_layer` already reads, while a routine's parameter
        has to reach the closure a generator was built into.
        """
        macros: dict[str, float] = {}
        looks: dict[str, dict[str, float]] = {}
        for (look, param), mod in self.by_key.items():
            value = mod.value(bar, energy)
            if look:
                looks.setdefault(look, {})[param] = value
            else:
                macros[param] = value
        return macros, looks

    def status(self) -> list[dict]:
        return [mod.public() for mod in self.by_key.values()]
