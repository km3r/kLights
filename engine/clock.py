"""
Musical time: beats, bars and phrases, from whatever source the venue offers.

"Tempo mattered more because some routines were too fast or slow" was the note
from the night, and the cause is that a QLC+ chase is a list of steps with
millisecond durations. A global speed dial cannot fix a per-routine rate, and a
stepped move that has been slowed down does not become smoother, it becomes
visibly steppier. Both problems go away if looks are written as functions of
musical position instead of functions of milliseconds.

So: **nothing downstream of here is authored in milliseconds.** "One cycle per 8
bars" stays right at any tempo, in any room, with no per-routine retuning.

**Phase continuity is the invariant.** Changing tempo, nudging the speed, or
swapping the clock source must never move the current beat position -- if it
does, every running look jumps at once, which is worse than the wrong tempo you
were trying to fix. That is guaranteed by construction here rather than tested
for afterwards: the timeline is an anchor (a time, and the beat position at that
time) plus a rate, and every rate change re-anchors at the current position
first. `_reanchor` is the whole trick and there is only one of it.

**Sources are swappable at runtime, and the no-CDJ case is first class.** You
have events with Pro DJ Link and events without. Rather than a polled interface
that every source must implement whether or not it makes sense, sources PUSH
into the clock -- `tap()`, `sync()`, `set_bpm()` -- and everything downstream
reads `position()`. A manual source is then genuinely nothing at all, which is
the right amount of machinery for "I typed 124".
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional, Sequence

DEFAULT_BPM = 124.0

# A tap this long after the previous one starts a new set rather than joining
# the old one. Two seconds is slower than any dance tempo's beat, so a gap this
# big means the operator stopped and started again, not that the track halved.
TAP_RESET_SECONDS = 2.0

# Taps kept for the estimate. Enough to average out a shaky hand, few enough to
# follow a real tempo change within a couple of bars.
TAP_WINDOW = 8

MIN_BPM, MAX_BPM = 40.0, 250.0


@dataclass(frozen=True)
class Meter:
    """How beats group. 4/4 with 8-bar phrases covers essentially all of this
    music; both are configurable because the phrase length is what F7's
    look changes will land on, and getting it wrong makes them land wrong."""
    beats_per_bar: int = 4
    bars_per_phrase: int = 8

    @property
    def beats_per_phrase(self) -> int:
        return self.beats_per_bar * self.bars_per_phrase


@dataclass(frozen=True)
class Position:
    """Where we are in the music, at one instant.

    `beat` is cumulative and fractional -- it never wraps -- because a look that
    runs over 8 bars needs a monotonic phase, and wrapping it would put a
    discontinuity in the middle of every long move. `beat_in_bar` and
    `bar_in_phrase` are the wrapped views, for anything that wants to land on a
    boundary.
    """
    beat: float
    bar: float
    phrase: float
    bpm: float
    meter: Meter

    @property
    def beat_in_bar(self) -> float:
        return self.beat % self.meter.beats_per_bar

    @property
    def bar_in_phrase(self) -> float:
        return self.bar % self.meter.bars_per_phrase

    @property
    def is_downbeat(self) -> bool:
        return self.beat_in_bar < 1e-6


class TapTempo:
    """Estimates BPM from tap times.

    Uses the MEDIAN interval, not the mean. A single late tap -- someone looking
    away, a missed beat -- drags a mean badly and a median barely at all, and
    tapping under club conditions produces exactly that kind of outlier.
    """

    def __init__(self, window: int = TAP_WINDOW):
        self.window = window
        self.times: list[float] = []

    def tap(self, now: float) -> Optional[float]:
        """Record a tap. Returns the BPM estimate, or None if there is not one
        yet (the first tap of a set establishes only a phase)."""
        if self.times and now - self.times[-1] > TAP_RESET_SECONDS:
            self.times = []
        self.times.append(now)
        if len(self.times) > self.window:
            self.times = self.times[-self.window:]
        if len(self.times) < 2:
            return None
        intervals = [b - a for a, b in zip(self.times, self.times[1:])]
        median = statistics.median(intervals)
        # Two taps at the same instant. Reachable whenever taps are timestamped
        # on a grid coarser than a double-tap -- and a divide by zero here would
        # surface as a failed command rather than as "you tapped twice".
        if median <= 0:
            return None
        bpm = 60.0 / median
        return bpm if MIN_BPM <= bpm <= MAX_BPM else None

    def reset(self) -> None:
        self.times = []

    @property
    def taps(self) -> int:
        return len(self.times)


class MasterClock:
    """The timeline everything musical reads.

    Held as (anchor_time, anchor_beat, bpm, speed). `beat(now)` is then
    anchor_beat plus elapsed time times the effective rate -- so the only way to
    change the rate without moving the current position is to re-anchor first,
    and that is exactly what every mutator does.
    """

    def __init__(self, bpm: float = DEFAULT_BPM, meter: Meter = Meter(),
                 now: float = 0.0, source: str = "manual"):
        self.meter = meter
        self._bpm = float(bpm)
        self._speed = 1.0
        self._anchor_time = float(now)
        self._anchor_beat = 0.0
        self._tap = TapTempo()
        self.source = source
        self.running = True
        # Whether phrase position is MEASURED or merely COUNTED.
        #
        # Counted phrase is bars-since-the-last-downbeat divided by the meter --
        # correct only if the operator's downbeat really was a phrase start and
        # the track has not changed since. That is a guess about the music, and
        # it decays: eight bars of counting from a slightly wrong downbeat puts
        # a "phrase boundary" in the middle of a phrase.
        #
        # A source with real phrase data (Pro DJ Link on newer players) sets
        # this. Nothing else should, and F7 uses it to decide whether landing a
        # look change on a phrase is trustworthy or whether to fall back to bars.
        self.phrase_measured = False

    # -- reading -----------------------------------------------------------

    @property
    def bpm(self) -> float:
        """The source tempo, before any speed nudge."""
        return self._bpm

    @property
    def speed(self) -> float:
        return self._speed

    @property
    def effective_bpm(self) -> float:
        return self._bpm * self._speed

    def beat(self, now: float) -> float:
        if not self.running:
            return self._anchor_beat
        return self._anchor_beat + (now - self._anchor_time) * self.effective_bpm / 60.0

    def position(self, now: float) -> Position:
        beat = self.beat(now)
        return Position(beat=beat,
                        bar=beat / self.meter.beats_per_bar,
                        phrase=beat / self.meter.beats_per_phrase,
                        bpm=self.effective_bpm, meter=self.meter)

    # -- the one place the rate may change ---------------------------------

    def _reanchor(self, now: float) -> None:
        """Freeze the current position, so whatever changes next cannot move it.

        Every mutator calls this first. It is the entire phase-continuity
        guarantee, and keeping it to one method is what stops that guarantee
        from being re-derived (and mis-derived) per mutator.
        """
        self._anchor_beat = self.beat(now)
        self._anchor_time = now

    # -- mutating ----------------------------------------------------------

    def set_bpm(self, bpm: float, now: float) -> None:
        if not MIN_BPM <= bpm <= MAX_BPM:
            raise ValueError(f"bpm {bpm} outside {MIN_BPM}-{MAX_BPM}")
        self._reanchor(now)
        self._bpm = float(bpm)

    def set_speed(self, multiplier: float, now: float) -> None:
        """Live speed nudge on whatever is running, without leaving the look.

        Deliberately separate from the tempo: the tempo is what the music is
        doing and the speed is what you want the lights to do about it. Halving
        the speed of a look during a breakdown should not be undone the moment
        the clock re-syncs to the track.
        """
        if multiplier <= 0:
            raise ValueError("speed multiplier must be positive")
        self._reanchor(now)
        self._speed = float(multiplier)

    def nudge_speed(self, factor: float, now: float) -> None:
        self.set_speed(self._speed * factor, now)

    def nudge_phase(self, beats: float, now: float) -> None:
        """Shift position without touching tempo -- for when the tempo is right
        but the lights are running slightly ahead of or behind the track."""
        self._reanchor(now)
        self._anchor_beat += beats

    def set_downbeat(self, now: float) -> None:
        """Declare this instant the start of a bar, and of a phrase.

        Snaps to the nearest bar rather than resetting to zero: resetting would
        also reset the phrase counter, and F7's look changes land on phrase
        boundaries, so a mid-set downbeat tap would shunt every upcoming change.
        """
        self._reanchor(now)
        bpb = self.meter.beats_per_bar
        self._anchor_beat = round(self._anchor_beat / bpb) * bpb

    def tap(self, now: float) -> Optional[float]:
        """Register a tap: sets the tempo, and puts a beat on this instant.

        Aligning phase to the tap is the point of tapping -- the operator is
        saying "here". The alignment is to the NEAREST beat, so a steady tapper
        barely moves the phase while a first tap after a gap grabs it.
        """
        bpm = self._tap.tap(now)
        if bpm is not None:
            self.set_bpm(bpm, now)
        self._reanchor(now)
        self._anchor_beat = round(self._anchor_beat)
        self.source = "tap"
        return bpm

    def reset_taps(self) -> None:
        self._tap.reset()

    @property
    def taps(self) -> int:
        return self._tap.taps

    def sync(self, now: float, bpm: Optional[float] = None,
             beat: Optional[float] = None, source: Optional[str] = None,
             phrase_measured: Optional[bool] = None) -> None:
        """Accept a position from an external source -- Pro DJ Link, MIDI clock,
        an audio beat tracker.

        `beat` is absolute musical position where the source has one. Applying
        it as a JUMP is correct for a re-sync and wrong for continuous tracking,
        so a source that fires every beat should pass bpm only and let the
        timeline free-run between corrections; otherwise it will judder.
        """
        self._reanchor(now)
        if bpm is not None:
            if not MIN_BPM <= bpm <= MAX_BPM:
                raise ValueError(f"bpm {bpm} outside {MIN_BPM}-{MAX_BPM}")
            self._bpm = float(bpm)
        if beat is not None:
            self._anchor_beat = float(beat)
        if source is not None:
            self.source = source
        if phrase_measured is not None:
            self.phrase_measured = phrase_measured

    def start(self, now: float) -> None:
        if not self.running:
            self._anchor_time = now
            self.running = True

    def stop(self, now: float) -> None:
        if self.running:
            self._reanchor(now)
            self.running = False


# ------------------------------------------------------------------ sources --

class ClockSource:
    """Something that decides what the tempo is.

    Sources PUSH into the clock rather than being polled. A polled interface
    would force every source to implement a method that means nothing for it --
    there is nothing to poll from a manual tempo -- and would make the
    no-CDJ case, which is a first-class mode here and not a fallback, carry
    machinery it does not need.
    """
    name = "source"

    def attach(self, clock: MasterClock, now: float) -> None:
        clock.source = self.name

    def detach(self, clock: MasterClock, now: float) -> None:
        pass


class ManualSource(ClockSource):
    """A typed-in tempo. Genuinely nothing to do -- the clock free-runs."""
    name = "manual"

    def __init__(self, bpm: float = DEFAULT_BPM):
        self.bpm = bpm

    def attach(self, clock: MasterClock, now: float) -> None:
        clock.set_bpm(self.bpm, now)
        clock.source = self.name


class TapSource(ClockSource):
    """Operator taps. `tap(clock, now)` per tap."""
    name = "tap"

    def attach(self, clock: MasterClock, now: float) -> None:
        clock.reset_taps()
        clock.source = self.name

    def tap(self, clock: MasterClock, now: float) -> Optional[float]:
        return clock.tap(now)


class ProLinkSource(ClockSource):
    """Pro DJ Link -- NOT IMPLEMENTED, and deliberately so at this stage.

    The protocol is unofficial and reverse-engineered, so it is real work with
    real uncertainty, and the plan is explicit that it should be scoped only
    once this abstraction exists -- so that the tap path is never blocked on it.
    This class marks the seam and documents what finishing it needs:

      * UDP on ports 50000-50002, announcing as a virtual CDJ so the players
        will talk to it at all.
      * Beat packets carry BPM and beat-within-bar; call `clock.sync(now,
        bpm=..., source="prolink")` on each, and `clock.set_downbeat(now)` when
        beat-within-bar is 1.
      * Do NOT pass `beat=` every packet. Applying an absolute position as a
        jump 2-4 times a second makes the timeline judder; let it free-run
        between corrections and only jump on a genuine re-sync.
      * Phrase data exists on newer players but not all, so phrase must still
        degrade to bars-since-downbeat. The venue-without-CDJs case stays
        first class either way.
    """
    name = "prolink"

    def attach(self, clock: MasterClock, now: float) -> None:
        raise NotImplementedError(
            "Pro DJ Link is not implemented yet -- see ProLinkSource's docstring "
            "for what it needs. Use TapSource or ManualSource.")


def attach(clock: MasterClock, source: ClockSource, now: float) -> ClockSource:
    """Swap the clock's source without moving the current beat position."""
    source.attach(clock, now)
    return source
