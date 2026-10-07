"""
A track's audio as levels over its beats: what an automation lane follows when
its row carries `audio` -- the bass lifting the master, the highs opening a
strobe's rate.

**Not live audio.** The levels are rekordbox's own analysis of the track, kept
by the prep tool in `waveforms/<id>.json`. So a lane that follows the bass is,
like everything else on a timeline, a pure function of the beat: a loop, a hot
cue or a scrub in the designer lands on the level that was authored there, and
nothing here needs a sound card.

**Which analysis.** rekordbox writes three, and they are used in this order:

- `bands` (`PWV7`, the CDJ-3000's three-band waveform): a byte each for LOW,
  MID and HIGH per column, 0-127. The order was read off real files rather
  than the documentation, two ways that agree across a 6,455-track collection:
  byte 0 follows the colour waveform's red times its height (r 0.82-0.91),
  byte 1 its green, byte 2 its blue -- and byte 2 peaks between the beats,
  where the hi-hats are, while bytes 0 and 1 peak on them.
- `detail` in `pwv5` (the colour waveform): one height and one colour per
  column. Red, green and blue are low, mid and high, so a band is its colour
  times the height -- an estimate, and `Levels.exact` says so.
- `detail` in `pwv3` (the blue waveform): a height alone, so only the overall
  level, `all`.

**Levels are relative to the track.** Each band is scaled to its own loudest
moment in this track, so `depth` means "at the band's peak" whatever the
mastering. `floor` and `ceiling` then pick the part of that range the lane
listens to -- under the floor is silence, at the ceiling it is all the way
there -- and `release` is how many beats a full level takes to fall away.

**Cells.** The columns (150 a second) are pooled onto the track's beats,
`STEP` of a beat to a cell, each cell the loudest column in it: finer than a
frame at any tempo a DJ plays, and coarse enough that a lane is a short list.
A level is its cell's -- a step, not a slope -- which keeps the integral an
exact prefix sum, as `timeline.Curve.integral` promises a `rate.*` lane.

Standard library only, like `timeline.py` and `waves.py`, and for the same
reason: a curve is part of the core every output shares.
"""

from __future__ import annotations

import base64
import binascii
import math
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional

BANDS = ("low", "mid", "high", "all")
STEP = 1.0 / 32.0            # beats to a cell
RATE = 150.0                 # rekordbox's scrolling waveforms: columns a second
RELEASE_MAX = 64.0           # beats


@dataclass(frozen=True)
class Levels:
    """One track's analysis, decoded: a level per column for each band it has.
    Raw -- whatever scale the format used -- since each band is scaled to its
    own peak when it is laid on the beats."""
    rate: float
    columns: Mapping[str, bytes]
    exact: bool               # a real three-band analysis, not read off colours


def _data(part: Any) -> Optional[bytes]:
    if not isinstance(part, Mapping) or not isinstance(part.get("data"), str):
        return None
    try:
        return base64.b64decode(part["data"], validate=True)
    except (binascii.Error, ValueError):
        return None


def _rate(part: Mapping) -> float:
    rate = part.get("rate")
    if isinstance(rate, bool) or not isinstance(rate, (int, float)) or rate <= 0:
        return RATE
    return float(rate)


def decode(doc: Any) -> Optional[Levels]:
    """A waveform document's levels, or None if it holds nothing a lane could
    follow -- the 400-column preview alone is a column every second or so."""
    if not isinstance(doc, Mapping):
        return None
    bands, detail = doc.get("bands"), doc.get("detail")
    raw = _data(bands)
    if raw is not None and bands.get("format") == "pwv7" and len(raw) >= 3:
        n = len(raw) // 3 * 3
        low, mid, high = raw[0:n:3], raw[1:n:3], raw[2:n:3]
        return Levels(_rate(bands), {"low": low, "mid": mid, "high": high,
                                     "all": bytes(map(max, low, mid, high))}, True)
    raw = _data(detail)
    if raw is None:
        return None
    if detail.get("format") == "pwv5" and len(raw) >= 2:
        # Two bytes a column, big-endian: rrrgggbb bhhhhh--.
        pairs = list(zip(raw[0::2], raw[1::2]))
        height = [(b >> 2) & 31 for _, b in pairs]
        return Levels(_rate(detail), {
            "low": bytes(((a >> 5) & 7) * h for (a, _), h in zip(pairs, height)),
            "mid": bytes(((a >> 2) & 7) * h for (a, _), h in zip(pairs, height)),
            "high": bytes((((a & 3) << 1) | (b >> 7)) * h
                          for (a, b), h in zip(pairs, height)),
            "all": bytes(height)}, False)
    if detail.get("format") == "pwv3" and raw:
        return Levels(_rate(detail), {"all": bytes(b & 31 for b in raw)}, True)
    return None


@dataclass(frozen=True)
class Envelope:
    """One band of one track, shaped, as a level per cell: 0..1. Cell `j`
    covers beats `(first + j) * STEP` up to the next; outside them it is 0."""
    first: int
    cells: tuple[float, ...]
    sums: tuple[float, ...]       # sums[j]: the area of every cell before j

    def _at(self, beat: float) -> tuple[int, float]:
        pos = beat / STEP
        k = math.floor(pos)
        return k - self.first, pos - k

    def level(self, beat: float) -> float:
        j, _ = self._at(beat)
        return self.cells[j] if 0 <= j < len(self.cells) else 0.0

    def integral(self, beat: float) -> float:
        """∫ level, from the first cell's start to `beat`. Exact."""
        j, frac = self._at(beat)
        if j < 0:
            return 0.0
        if j >= len(self.cells):
            return self.sums[-1]
        return self.sums[j] + frac * STEP * self.cells[j]


@dataclass(frozen=True)
class Follow:
    """A lane following a band: its points' value plus `depth` times the
    band's level. Additive and one-sided, like `waves.Wave`, so a validator
    bounds the whole swing from the points alone."""
    envelope: Envelope
    depth: float
    band: str

    def level(self, beat: float) -> float:
        return self.depth * self.envelope.level(beat)

    def integral(self, beat: float) -> float:
        return self.depth * self.envelope.integral(beat)


def _number(spec: Mapping, key: str, default: Optional[float], where: str) -> float:
    value = spec.get(key, default)
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        raise ValueError(f"{where} {key} must be a number, got {value!r}")
    return float(value)


def shape(spec: Any, where: str = "audio") -> tuple[str, float, float, float, float]:
    """(band, depth, floor, ceiling, release) from a row's `audio` object.
    Raises ValueError with what is wrong; `showfiles` validates first, so this
    is the backstop."""
    if not isinstance(spec, Mapping):
        raise ValueError(f"{where} must be an object")
    band = spec.get("band")
    if band not in BANDS:
        raise ValueError(f"{where} band {band!r} is not one of {', '.join(BANDS)}")
    depth = _number(spec, "depth", None, where)
    floor = _number(spec, "floor", 0.0, where)
    ceiling = _number(spec, "ceiling", 1.0, where)
    release = _number(spec, "release", 0.0, where)
    if not 0.0 <= floor < ceiling <= 1.0:
        raise ValueError(f"{where} needs 0 <= floor < ceiling <= 1, got "
                         f"{floor:g} and {ceiling:g}")
    if not 0.0 <= release <= RELEASE_MAX:
        raise ValueError(f"{where} release must be 0 to {RELEASE_MAX:g} beats")
    return band, depth, floor, ceiling, release


class Audio:
    """One track's levels laid on its beats, ready to be followed. Build on
    the worker; what it hands out is immutable and read anywhere.

    `time_at` is the track's grid (`tracktime.Grid.time_at`), passed in as a
    function so this module needs nothing but the standard library."""

    def __init__(self, levels: Levels, time_at: Callable[[float], float],
                 beat_at: Callable[[float], float]):
        self.levels = levels
        self.exact = levels.exact
        self.bands = tuple(b for b in BANDS if b in levels.columns)
        n = max((len(c) for c in levels.columns.values()), default=0)
        self.first = math.floor(beat_at(0.0) / STEP)
        last = max(self.first, math.ceil(beat_at(n / levels.rate) / STEP))
        # Each cell's first column; one more, for where the last cell ends.
        self._edges = [min(n, max(0, int(time_at((self.first + j) * STEP)
                                         * levels.rate)))
                       for j in range(last - self.first + 1)]
        self._pooled: dict[str, tuple[float, ...]] = {}
        self._envelopes: dict[tuple, Envelope] = {}

    def pooled(self, band: str) -> tuple[float, ...]:
        """A band on the beats, before any shaping: each cell its loudest
        column, as a fraction of the band's loudest in the whole track."""
        got = self._pooled.get(band)
        if got is None:
            raw = self.levels.columns[band]
            peak = max(raw, default=0)
            edges = self._edges
            out = []
            for a, b in zip(edges, edges[1:]):
                # A cell narrower than a column still has the column it is in.
                top = max(raw[a:b]) if b > a else (raw[a] if a < len(raw) else 0)
                out.append(top / peak if peak else 0.0)
            got = self._pooled[band] = tuple(out)
        return got

    def envelope(self, band: str, floor: float = 0.0, ceiling: float = 1.0,
                 release: float = 0.0) -> Envelope:
        key = (band, floor, ceiling, release)
        got = self._envelopes.get(key)
        if got is None:
            span = ceiling - floor
            fall = STEP / release if release > 0 else math.inf
            cells, sums, held, acc = [], [0.0], 0.0, 0.0
            for x in self.pooled(band):
                held = max(min(1.0, max(0.0, (x - floor) / span)), held - fall)
                cells.append(held)
                acc += held * STEP
                sums.append(acc)
            got = self._envelopes[key] = Envelope(self.first, tuple(cells),
                                                  tuple(sums))
        return got

    def follow(self, spec: Any, where: str = "audio") -> Optional[Follow]:
        """A row's `audio` as something a curve adds, or None when this
        track's analysis has no such band (said by `showfiles`, as a warning:
        the waveform is another file, and the lane plays its points alone)."""
        band, depth, floor, ceiling, release = shape(spec, where)
        if band not in self.levels.columns:
            return None
        return Follow(self.envelope(band, floor, ceiling, release), depth, band)
