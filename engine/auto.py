"""
Auto mode: let the show run itself, one axis at a time.

Every axis is independently toggleable, because "automatic" is not one feature.
Some nights you want the timing handled and you pick the looks; some nights you
want to walk away from it. The axes:

  timing        movement follows the clock                  (F6; on by default)
  look_changes  advance a set list on musical boundaries
  palette       rotate colour over time
  energy        build/drop inference driving level and rate

**Changes land on musical boundaries, not on timers.** A look change that
arrives 300 ms after the drop reads as a mistake; the same change on the
boundary reads as intentional. So the director watches musical position and
fires exactly once per crossing.

**Degradation is designed, not accidental.** Phrase position is only MEASURED
when a source supplies it -- Pro DJ Link on newer players. Otherwise it is
COUNTED from wherever the operator last tapped a downbeat, which is a guess that
decays: eight bars of counting from a slightly-wrong downbeat puts a "phrase
boundary" in the middle of a phrase. So when phrase is merely counted, the
director lands changes on BARS instead, at an equivalent interval. Being one bar
early is a small error; being half a phrase out is a visible one. The
venue-without-CDJs case is a first-class mode, not a fallback.

**Manual always wins.** Picking a look holds it until released. An auto mode you
cannot override is one you turn off and never turn back on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Optional, Sequence

from . import state as statemod
from .clock import MasterClock, Position

# A look is a factory, not a stored scene: it is handed the current palette
# colour and returns the layer stack. That is what lets colour rotate
# independently of which look is running, instead of every combination being
# its own stored scene.
LookFactory = Callable[[tuple[float, float, float]], statemod.Show]


@dataclass(frozen=True)
class Look:
    name: str
    make: LookFactory
    # Looks that should not be auto-selected -- blinders, a blackout, anything
    # you want reachable by hand but never by a timer.
    manual_only: bool = False


@dataclass
class SetList:
    """An ordered list of looks. The shape the Night cue list should take."""
    looks: list[Look] = field(default_factory=list)
    index: int = 0

    @property
    def auto_looks(self) -> list[Look]:
        return [l for l in self.looks if not l.manual_only]

    def current(self) -> Optional[Look]:
        return self.looks[self.index] if self.looks else None

    def advance(self) -> Optional[Look]:
        """Next auto-eligible look, wrapping. Skips manual-only entries so a
        blackout parked in the list cannot be selected by a timer."""
        eligible = self.auto_looks
        if not eligible:
            return self.current()
        here = self.current()
        if here in eligible:
            nxt = eligible[(eligible.index(here) + 1) % len(eligible)]
        else:
            nxt = eligible[0]
        self.index = self.looks.index(nxt)
        return nxt

    def select(self, name: str) -> Optional[Look]:
        for i, look in enumerate(self.looks):
            if look.name == name:
                self.index = i
                return look
        raise KeyError(f"no look named {name!r} "
                       f"(have: {', '.join(l.name for l in self.looks)})")


@dataclass
class Palette:
    """Colours to rotate through. Linear 0..1 RGB."""
    colors: list[tuple[float, float, float]] = field(
        default_factory=lambda: [(1.0, 1.0, 1.0)])
    index: int = 0

    def current(self) -> tuple[float, float, float]:
        return self.colors[self.index % len(self.colors)]

    def advance(self) -> tuple[float, float, float]:
        self.index = (self.index + 1) % len(self.colors)
        return self.current()


# ------------------------------------------------------------------ energy --

class EnergySource:
    """Where an energy level (0..1) comes from.

    Kept a seam rather than a single implementation because this is the least
    well-defined part of the whole plan and the most likely to disappoint. It is
    off by default and should be judged on a real set before being trusted.
    """
    name = "energy"

    def level(self, position: Position) -> float:
        return 0.5


class ManualEnergy(EnergySource):
    """A fader the operator moves. The only source that is actually a
    measurement of anything, since the operator can hear the room."""
    name = "manual"

    def __init__(self, value: float = 0.5):
        self.value = value

    def level(self, position: Position) -> float:
        return max(0.0, min(1.0, self.value))


class PhraseEnergy(EnergySource):
    """Energy inferred from position within the phrase.

    Dance music tends to build across a phrase and reset at its boundary, so
    ramping energy over the phrase captures a real and useful amount with no
    audio at all. Be clear about what this is: a guess about the MUSIC, not a
    measurement OF it. It will be confidently wrong through a long breakdown,
    and it is worthless when phrase is merely counted rather than measured --
    which is why the director refuses to use it in that case.

    `build_bars` is how much of the phrase's end is treated as the build. The
    rest sits at `floor`.
    """
    name = "phrase"

    def __init__(self, floor: float = 0.35, peak: float = 1.0,
                 build_bars: float = 4.0):
        self.floor = floor
        self.peak = peak
        self.build_bars = build_bars

    def level(self, position: Position) -> float:
        bars_in = position.bar_in_phrase
        total = position.meter.bars_per_phrase
        remaining = total - bars_in
        if remaining > self.build_bars:
            return self.floor
        # Ramp over the last build_bars, easing up so the last bar climbs
        # fastest -- a linear build reads as a slope, a curved one as a build.
        t = 1.0 - remaining / self.build_bars
        return self.floor + (self.peak - self.floor) * (t * t)


class AudioEnergy(EnergySource):
    """Audio-in energy -- NOT IMPLEMENTED.

    Marks the seam. Finishing it needs: an input device, a band-split (kick
    energy tracks build and drop far better than broadband level), and a slow
    normaliser so the level means "loud for this set" rather than "loud in
    absolute terms" -- otherwise it reads every quiet track as a breakdown.
    Judge it on a real set before wiring it to anything that dims the rig.
    """
    name = "audio"

    def level(self, position: Position) -> float:
        raise NotImplementedError(
            "Audio energy is not implemented -- see AudioEnergy's docstring. "
            "Use ManualEnergy or PhraseEnergy.")


@dataclass(frozen=True)
class EnergyResponse:
    """What energy actually does. Each pair is (at energy 0, at energy 1).

    Ranges rather than gains, so the extremes are stated outright and someone
    reading this can see that the rig never goes below 45% or above double rate,
    instead of having to reason about a multiplier chain.
    """
    intensity: tuple[float, float] = (0.45, 1.0)
    rate: tuple[float, float] = (0.6, 1.8)
    strobe_above: float = 0.95

    def intensity_at(self, energy: float) -> float:
        return _lerp(self.intensity, energy)

    def rate_at(self, energy: float) -> float:
        return _lerp(self.rate, energy)

    def strobe_at(self, energy: float) -> bool:
        return energy >= self.strobe_above


def _lerp(pair: tuple[float, float], t: float) -> float:
    t = max(0.0, min(1.0, t))
    return pair[0] + (pair[1] - pair[0]) * t


# ------------------------------------------------------------------ config --

@dataclass
class AutoConfig:
    """Which axes are on, and how often each fires.

    Intervals are in PHRASES. When phrase is only counted rather than measured
    the director converts them to the equivalent number of bars and lands on
    those instead -- see the module docstring.
    """
    timing: bool = True
    look_changes: bool = False
    palette: bool = False
    energy: bool = False

    change_every_phrases: float = 2.0
    palette_every_phrases: float = 4.0

    response: EnergyResponse = field(default_factory=EnergyResponse)


# ---------------------------------------------------------------- director --

def _boundary_index(position: Position, every_phrases: float,
                    phrase_measured: bool) -> float:
    """Which boundary period we are in.

    On a measured phrase this is phrase/every. On a counted one it is the same
    interval expressed in BARS, because a counted phrase drifts and a bar does
    not -- landing a change one bar early is a small error, landing it half a
    phrase out is a visible one.
    """
    if phrase_measured:
        return position.phrase / every_phrases
    return position.bar / (every_phrases * position.meter.bars_per_phrase)


class AutoDirector:
    """Runs the show's automatic axes and hands back a Show to evaluate.

    Call `update(position)` once per frame. It returns the current Show,
    rebuilding it only when the look or the palette actually changed -- movement
    and energy are continuous and flow through the context instead, so a rebuild
    per frame would be pure waste.
    """

    def __init__(self, setlist: SetList, config: AutoConfig,
                 palette: Optional[Palette] = None,
                 energy: Optional[EnergySource] = None):
        self.setlist = setlist
        self.config = config
        self.palette = palette or Palette()
        self.energy_source = energy or ManualEnergy()
        # How a chosen look becomes a Show. Overridable because the director
        # owns WHICH movement look is up, not what the whole show is: the
        # controller composes the movement slot with the separately-selected
        # colour and level slots, so an auto look change no longer discards
        # them. Default is the look on its own.
        self.compose: Callable[[Look, tuple[float, float, float]],
                               statemod.Show] = lambda look, color: look.make(color)

        self.held = False
        self.energy = 0.0
        self.rate = 1.0
        self.strobe = False
        # Motion phase, integrated separately from musical position -- see
        # `motion_bar`. Starts wherever the music is so the first frame is not a
        # jump from zero.
        self.motion_bar = 0.0
        self._last_bar: Optional[float] = None

        self._show: Optional[statemod.Show] = None
        self._change_mark: Optional[float] = None
        self._palette_mark: Optional[float] = None
        self.changes = 0
        self.palette_changes = 0
        self.last_change_reason = "start"

    # -- manual override ---------------------------------------------------

    def select(self, name: str, hold: bool = True) -> statemod.Show:
        """Pick a look by hand. Holds it until released by default, because an
        auto mode that overrides the operator is one they switch off."""
        self.setlist.select(name)
        self.held = hold
        self.last_change_reason = "manual"
        self._show = None
        return self.rebuild()

    def release(self) -> None:
        """Resume automatic look changes at the next boundary.

        The mark is cleared rather than left stale. While a look is held the
        boundary counter is not advancing, so releasing at phrase 8.75 against a
        mark last written at phrase 4 would read as a crossing and change the
        look on the very next frame -- which is precisely the jump the operator
        was holding to avoid. Clearing re-baselines on the next update, so the
        change lands where the docstring says it does.
        """
        self.held = False
        self._change_mark = None

    def rebuild(self) -> statemod.Show:
        look = self.setlist.current()
        if look is None:
            raise ValueError("set list is empty")
        self._show = self.compose(look, self.palette.current())
        return self._show

    # -- per frame ---------------------------------------------------------

    def update(self, position: Position, phrase_measured: bool = False
               ) -> statemod.Show:
        changed = self._show is None

        # Motion phase. Integrated as rate * d(bar) rather than computed as
        # rate * bar, because the latter jumps every time the rate changes: at
        # bar 40 a rate going 1.0 -> 1.5 would move the motion phase by 20 bars
        # in one frame and snap every running move. Same reasoning as the
        # clock's re-anchoring, and the same failure if it is skipped.
        #
        # The `timing` axis is what gates this. Off means movement stops
        # following the clock and every move freezes where it stands -- which is
        # the literal reading of the axis, and a useful hold in its own right.
        # Musical position keeps advancing regardless, so anything landing ON
        # the music is unaffected and turning timing back on resumes from where
        # the rig froze rather than snapping to where it would have been.
        if self._last_bar is None:
            self.motion_bar = position.bar
        elif self.config.timing:
            delta = position.bar - self._last_bar
            if delta < 0:                      # a phase nudge ran time backwards
                delta = 0.0
            self.motion_bar += delta * self.rate
        self._last_bar = position.bar

        if self.config.energy:
            self.energy = max(0.0, min(1.0, self.energy_source.level(position)))
            self.rate = self.config.response.rate_at(self.energy)
            self.strobe = self.config.response.strobe_at(self.energy)
        else:
            self.energy, self.rate, self.strobe = 0.0, 1.0, False

        if not (self.config.look_changes and not self.held):
            # Gate shut. Forget the mark so re-opening it re-baselines rather
            # than comparing against a boundary index from minutes ago -- see
            # `release`. Same reasoning for the axis being toggled off and on.
            self._change_mark = None
        else:
            mark = _boundary_index(position, self.config.change_every_phrases,
                                   phrase_measured)
            if self._crossed("_change_mark", mark):
                self.setlist.advance()
                self.changes += 1
                self.last_change_reason = (
                    "phrase boundary" if phrase_measured else "bar boundary (phrase counted)")
                changed = True

        if not self.config.palette:
            self._palette_mark = None          # re-baseline on re-enable
        else:
            mark = _boundary_index(position, self.config.palette_every_phrases,
                                   phrase_measured)
            if self._crossed("_palette_mark", mark):
                self.palette.advance()
                self.palette_changes += 1
                changed = True

        if changed:
            self.rebuild()
        assert self._show is not None
        return self._show

    def _crossed(self, attr: str, mark: float) -> bool:
        """True exactly once per boundary crossing.

        Edge-detected on the floored period, so a variable frame interval cannot
        fire twice or miss one. Time running BACKWARDS -- which a phase nudge
        does deliberately -- re-baselines instead of firing, since a nudge to
        line up with the track is not a musical event and should not advance the
        set list.
        """
        index = math.floor(mark)
        previous = getattr(self, attr)
        setattr(self, attr, index)
        if previous is None:
            return False
        return index > previous

    # -- context -----------------------------------------------------------

    def apply(self, ctx: statemod.EvalContext) -> None:
        """Publish the director's continuous outputs onto the context.

        Movement reads `motion_bar`; anything that must stay locked to the music
        -- look changes, boundary-aligned hits -- reads `bar`. Keeping them
        separate is the same split as tempo versus speed on the clock: one is
        what the music is doing, the other is what the lights are doing about it.
        """
        ctx.motion_bar = self.motion_bar
        ctx.energy = self.energy
        ctx.energy_rate = self.rate
        ctx.strobe = self.strobe
        if self.config.energy:
            ctx.auto_intensity = self.config.response.intensity_at(self.energy)
        else:
            ctx.auto_intensity = 1.0

    def status(self) -> dict:
        look = self.setlist.current()
        return {
            "look": None if look is None else look.name,
            "held": self.held,
            "color": self.palette.current(),
            "energy": round(self.energy, 3),
            "rate": round(self.rate, 3),
            "strobe": self.strobe,
            "changes": self.changes,
            "palette_changes": self.palette_changes,
            "last_change": self.last_change_reason,
            "axes": {"timing": self.config.timing,
                     "look_changes": self.config.look_changes,
                     "palette": self.config.palette,
                     "energy": self.config.energy},
            # How often each timed axis fires, in phrases. Reported so the UI
            # can show and change it: `auto_interval` has had a handler since
            # F7 and nothing that sent one, which made the rate at which a show
            # rearranges itself the one auto setting only editable in code.
            "intervals": {"looks": self.config.change_every_phrases,
                          "palette": self.config.palette_every_phrases},
        }


def energy_intensity_layer(tags: Optional[Sequence[str]] = None):
    """Scale intensity by the director's energy response.

    A layer rather than something baked into each look, so switching energy off
    removes it entirely instead of leaving every look carrying a multiplier of
    one.
    """
    return statemod.intensity_layer(
        lambda ctx, fixture: getattr(ctx, "auto_intensity", 1.0), tags=tags)


def energy_strobe_layer(tags: Optional[Sequence[str]] = ("movers",),
                        rate: float = 0.75):
    """Open the shutter into a strobe when energy crosses `strobe_above`.

    Paired with `energy_intensity_layer` for the same reason: switching the
    energy axis off should remove the behaviour rather than leave every look
    carrying a disabled branch. `rate` is 0..1 across the fixture's own slow-to-
    fast band -- the profile gives no Hz calibration, so quoting a frequency
    would be a number the hardware never agreed to.
    """
    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        if not getattr(ctx, "strobe", False):
            return
        for f in statemod._targets(ctx, tags):
            out[f.fid].strobe = max(0.0, min(1.0, rate))
    return layer
