"""
A track's own musical time: where its beats are, and what its phrases are.

`clock.py` is the SHOW's time -- a beat count that starts when the engine starts
and follows whatever tempo it is told. This is a TRACK's time, read out of
rekordbox's analysis: beat 0 is the track's first downbeat, and a position in
the audio maps to a beat through the beat grid rekordbox drew. The two are kept
apart on purpose. A timeline authored "at bar 41 of this track" must land on bar
41 whatever the DJ has done to the tempo, and the only thing that knows where
bar 41 is, is the grid.

**The grid is a list of anchors**, `(beat, time_ms, bpm)`, beat-ascending:

- between two anchors, beat is linear in time -- the tempo there is whatever
  the two anchors imply, not the `bpm` written on the first. That makes the map
  continuous and monotonic by construction, which a per-segment stated tempo
  would not be: rounding in the stated bpm opens a gap or an overlap at every
  anchor, and a transport crossing one would see the beat jump.
- before the first anchor and after the last, it extrapolates at the stated
  `bpm` of that anchor. A track with a steady tempo is one anchor.

rekordbox's PQTZ tag stores every beat; the prep tool compresses runs of steady
beats into single anchors, so a four-minute house track is one or two anchors
rather than five hundred.

Positions in the file are milliseconds because that is what rekordbox stores and
what a person comparing against rekordbox reads. Positions in the API are
seconds, because that is what the DJ sources send. Nothing here is authored in
either: timelines are authored in beats, and this module is the only thing that
converts.

Pure: no I/O, no threads, no clock.
"""

from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass
from typing import Iterable, Optional, Sequence

BEATS_PER_BAR = 4       # rekordbox grids are 4/4; so is everything here

# The prep tool's tolerance when it compresses a beat grid, and the tolerance a
# hand-edited anchor's stated bpm may disagree with what its neighbours imply
# before it is worth a warning. Not an error: the map is built from the anchors,
# so a stale bpm on an interior anchor changes nothing.
BPM_WARN_TOLERANCE = 0.5


class GridError(ValueError):
    """A grid that cannot be a map from time to beats."""


@dataclass(frozen=True)
class Grid:
    """Beat <-> time for one track. Build with `Grid.from_segments`."""

    beats: tuple[float, ...]        # anchor beat indices, strictly ascending
    times: tuple[float, ...]        # anchor times in SECONDS, strictly ascending
    bpms: tuple[float, ...]         # stated tempo at each anchor

    # -- construction --------------------------------------------------------

    @classmethod
    def from_segments(cls, segments: Sequence[Sequence[float]]) -> "Grid":
        """From the file's `[[beat, time_ms, bpm], ...]`. Raises GridError."""
        problems = grid_problems(segments)
        if problems:
            raise GridError("; ".join(problems))
        return cls(beats=tuple(float(s[0]) for s in segments),
                   times=tuple(float(s[1]) / 1000.0 for s in segments),
                   bpms=tuple(float(s[2]) for s in segments))

    @classmethod
    def steady(cls, bpm: float, first_downbeat_s: float = 0.0) -> "Grid":
        """A constant-tempo grid. What the synthetic track and most house
        records are."""
        return cls.from_segments([[0, first_downbeat_s * 1000.0, bpm]])

    def segments(self) -> list[list[float]]:
        """Back to the file's form."""
        return [[_tidy(b), _tidy(t * 1000.0), _tidy(bpm)]
                for b, t, bpm in zip(self.beats, self.times, self.bpms)]

    @property
    def rev(self) -> str:
        """A short fingerprint of the grid. A timeline records the rev it was
        authored against, so re-gridding a track in rekordbox after the
        timeline was drawn is caught rather than silently shifting every cue.

        Rounded to 0.1 ms and 0.001 bpm first, so re-running the prep tool on
        an unchanged track does not change the rev through float noise."""
        canon = [[round(b, 3), round(t * 1000.0, 1), round(bpm, 3)]
                 for b, t, bpm in zip(self.beats, self.times, self.bpms)]
        digest = hashlib.sha1(json.dumps(canon).encode("ascii")).hexdigest()
        return "g:" + digest[:6]

    # -- the map -------------------------------------------------------------

    def beat_at(self, seconds: float) -> float:
        """The beat at a position in the audio. Continuous and monotonic."""
        times, beats = self.times, self.beats
        if seconds <= times[0] or len(times) == 1:
            return beats[0] + (seconds - times[0]) * self.bpms[0] / 60.0
        if seconds >= times[-1]:
            return beats[-1] + (seconds - times[-1]) * self.bpms[-1] / 60.0
        i = bisect.bisect_right(times, seconds) - 1
        span = (seconds - times[i]) / (times[i + 1] - times[i])
        return beats[i] + span * (beats[i + 1] - beats[i])

    def time_at(self, beat: float) -> float:
        """The position in the audio, in seconds, of a beat. The inverse of
        `beat_at`, exactly, including outside the anchors."""
        times, beats = self.times, self.beats
        if beat <= beats[0] or len(beats) == 1:
            return times[0] + (beat - beats[0]) * 60.0 / self.bpms[0]
        if beat >= beats[-1]:
            return times[-1] + (beat - beats[-1]) * 60.0 / self.bpms[-1]
        i = bisect.bisect_right(beats, beat) - 1
        span = (beat - beats[i]) / (beats[i + 1] - beats[i])
        return times[i] + span * (times[i + 1] - times[i])

    def bpm_at(self, seconds: float) -> float:
        """The tempo the grid implies at a position -- the rate `beat_at`
        advances at there, in beats per minute. The TRACK's tempo; the DJ's
        pitch fader is applied by whoever reports the position."""
        times, beats = self.times, self.beats
        if len(times) == 1 or seconds < times[0]:
            return self.bpms[0]
        if seconds >= times[-1]:
            return self.bpms[-1]
        i = bisect.bisect_right(times, seconds) - 1
        return (beats[i + 1] - beats[i]) / (times[i + 1] - times[i]) * 60.0

    def warnings(self) -> list[str]:
        """Suspicious but usable: interior anchors whose stated bpm disagrees
        with the tempo their neighbours imply."""
        out = []
        for i in range(len(self.beats) - 1):
            implied = ((self.beats[i + 1] - self.beats[i])
                       / (self.times[i + 1] - self.times[i]) * 60.0)
            if abs(implied - self.bpms[i]) > BPM_WARN_TOLERANCE:
                out.append(f"grid anchor {i} says {self.bpms[i]:g} bpm but the "
                           f"next anchor implies {implied:.2f}; the anchors "
                           f"win, so this is only a stale label")
        return out


def grid_problems(segments: Sequence[Sequence[float]]) -> list[str]:
    """Everything that stops a segment list being a usable grid."""
    if not isinstance(segments, (list, tuple)) or not segments:
        return ["the grid needs at least one [beat, time_ms, bpm] anchor"]
    out: list[str] = []
    for i, seg in enumerate(segments):
        if (not isinstance(seg, (list, tuple)) or len(seg) != 3
                or not all(_is_number(v) for v in seg)):
            out.append(f"anchor {i} must be [beat, time_ms, bpm], got {seg!r}")
            continue
        if not 20.0 <= float(seg[2]) <= 400.0:
            out.append(f"anchor {i} bpm {seg[2]} is outside 20-400")
    if out:
        return out
    for i in range(1, len(segments)):
        if float(segments[i][0]) <= float(segments[i - 1][0]):
            out.append(f"anchor {i} beat {segments[i][0]} does not come after "
                       f"{segments[i - 1][0]}; beats must strictly increase")
        if float(segments[i][1]) <= float(segments[i - 1][1]):
            out.append(f"anchor {i} time {segments[i][1]} ms does not come after "
                       f"{segments[i - 1][1]} ms; a grid cannot run backwards")
    return out


# -- phrases ------------------------------------------------------------------

@dataclass(frozen=True)
class Phrase:
    """One phrase, in track beats. `label` is rekordbox's, verbatim."""
    label: str
    start: float
    end: float
    index: int

    @property
    def family(self) -> str:
        """The label without its number: "Verse 2" -> "Verse", "Up 1" -> "Up".
        Templates look up the exact label first and this second."""
        return family(self.label)


def family(label: str) -> str:
    """A phrase label without its number: "Verse 2" -> "Verse". A label with no
    number is its own family."""
    head, _, tail = label.rpartition(" ")
    return head if head and tail.isdigit() else label


@dataclass(frozen=True)
class PhraseMap:
    """A track's phrases, as rekordbox analysed them."""
    phrases: tuple[Phrase, ...]
    mood: Optional[str] = None      # rekordbox's "low" / "mid" / "high"

    @classmethod
    def from_items(cls, items: Iterable[Sequence], mood: Optional[str] = None
                   ) -> "PhraseMap":
        items = list(items)
        problems = phrase_problems(items)
        if problems:
            raise GridError("; ".join(problems))
        return cls(tuple(Phrase(str(label), float(a), float(b), i)
                         for i, (a, b, label) in enumerate(items)), mood)

    def at(self, beat: float) -> Optional[Phrase]:
        """The phrase containing `beat`, or None in a gap or outside them."""
        starts = [p.start for p in self.phrases]
        i = bisect.bisect_right(starts, beat) - 1
        if i < 0:
            return None
        p = self.phrases[i]
        return p if beat < p.end else None

    def __len__(self) -> int:
        return len(self.phrases)


def phrase_problems(items: Sequence) -> list[str]:
    out: list[str] = []
    for i, item in enumerate(items):
        if (not isinstance(item, (list, tuple)) or len(item) != 3
                or not _is_number(item[0]) or not _is_number(item[1])
                or not isinstance(item[2], str) or not item[2]):
            out.append(f"phrase {i} must be [start_beat, end_beat, \"Label\"], "
                       f"got {item!r}")
            continue
        if float(item[1]) <= float(item[0]):
            out.append(f"phrase {i} ({item[2]}) ends at {item[1]}, not after it "
                       f"starts at {item[0]}")
    if out:
        return out
    for i in range(1, len(items)):
        if float(items[i][0]) < float(items[i - 1][1]):
            out.append(f"phrase {i} ({items[i][2]}) starts at {items[i][0]}, "
                       f"inside {items[i - 1][2]} which ends at {items[i - 1][1]}")
    return out


def bar_of(beat: float) -> int:
    """Zero-based bar containing `beat`. Beat 0 is the first downbeat, so bar
    0 starts there and a pickup before it is bar -1."""
    return int(beat // BEATS_PER_BAR)


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _tidy(x: float):
    """Write 128.0 as 128 and keep real fractions, so a regenerated file diffs
    cleanly against the one it replaced."""
    r = round(x, 3)
    return int(r) if r == int(r) else r
