"""
A timeline, evaluated: at a beat of the track, what each lane is showing, what
each automation curve is at, and which hits are firing.

This is the core every output shares. It knows rows, items, curves and hits; it
does not know what a mover is. The lights compiler (F19h) asks it "what is on
the movement channel at beat 161.5" and turns the answer into layers; a VJ
adapter (milestone 3) will ask the same question of its own rows. So it imports
nothing from the lights -- nothing from the engine but `waves.py`, which is
standard-library-only itself -- and a test holds both to that.

**Positions are beats** on the track's grid (`tracktime.py` turns audio
seconds into them). Every query is a pure function of the beat, so whatever the
DJ does -- a loop, a hot cue, a scrub in the designer -- the lights at beat 161.5
are the lights at beat 161.5, however the deck got there.

**Lanes and precedence** (decided with the user, F19g):

- Each clips row drives one or more CHANNELS. Which ones is the caller's
  business: for lights the scene lane drives movement, color and level at
  once (`showfiles.timeline_channels`).
- **The higher lane wins.** For a channel, the rows that drive it are asked
  top to bottom, scene lanes included; the first with something there wins.
- A row whose gap mode is **fill** has nothing to say in its gaps, so the next
  row down -- and below them all the template or the fallback show -- shows
  through. A row that **owns the track** (exclusive) is BLANK in its gaps,
  before its first clip and after its last: nothing drives that channel there,
  not the rows below it and not the template.

**Clips in time.** Within one row, overlapping clips go to the one that started
earlier, until it ends. A clip fades IN over its `fade` beats from whatever was
under it -- the clip just before it on the same row if the two touch, otherwise
whatever is below the row. A clip that ends into a gap fades OUT over the same
`fade`, in its last `fade` beats, so `fade: 0` cuts on its end beat (decided
with the user). A clip followed directly by another does not fade out; the next
one's fade-in is the crossfade.

So a channel at a beat is a short stack, top first: clips with their weights,
ending in either BLANK or `rest` (the template/fallback underneath). Blending
the stack is the caller's job; this only says what is in it.

**Automation** is a curve through points `[beat, value]` or `[beat, value,
curve]`. The curve named on a point shapes the segment ARRIVING at it:
`linear`, `step` (hold the previous value until this point, then jump) or
`ease` (smoothstep). Before the first point and after the last the value holds.
`integral` is exact and closed-form, because a rate curve's integral is a
phase: computing it from the beat rather than accumulating it per frame is what
makes a loop land on the same phase every pass.

A row may also carry a `wave` (`waves.Wave`): a musical shape added on top of
its points -- `depth` times a sine, a triangle, a ramp... over `bars` -- so a
lane can breathe without a point per bar. Its integral is exact too, which is
why the shapes live in `waves.py` with their areas, and why that module is as
standard-library-only as this one.

**Hits** (flash, strobe, blackout) are windows: on from `at` for `len` beats.
Jump into the middle of one and it shows from there; jump over one and it never
fires. A hit shorter than a frame would fall between two frames, so in forward
play one that STARTED since the last frame is reported once even if it has
already ended -- never after a jump, which did not play through it.

**External rows** (milestone 3) belong to other outputs -- OSC, MIDI, the
built-in visuals. Their items are windows exactly like hits, and a row may
carry a curve too; what an item says is that output's business
(`outputs.py`). So a VJ cue a loop jumps into is on, and one a hot cue jumps
over never fires.

Pure: no I/O, no clock, no threads. Build on the worker; query anywhere.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Union

from .waves import Wave

CURVES = ("linear", "step", "ease")
FILL = "fill"
EXCLUSIVE = "exclusive"
EPS = 1e-9


class TimelineError(ValueError):
    """Rows that cannot be evaluated. `showfiles` validates first, so this is
    the backstop for a document that skipped it."""


def _num(value: Any, what: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TimelineError(f"{what} must be a number, got {value!r}")
    return float(value)


# -- items --------------------------------------------------------------------

@dataclass(frozen=True)
class Item:
    """One thing on a row, as authored. `data` is the whole item, read-only:
    what a clip IS (a routine, a look, a palette) is the caller's business."""
    id: str
    at: float
    len: float
    fade: float
    data: Mapping[str, Any]

    @property
    def end(self) -> float:
        return self.at + self.len

    @classmethod
    def from_dict(cls, d: Mapping, where: str = "") -> "Item":
        if not isinstance(d, Mapping):
            raise TimelineError(f"{where}: an item must be an object")
        ident = d.get("id")
        if not isinstance(ident, str) or not ident:
            raise TimelineError(f"{where}: an item needs an id")
        at = _num(d.get("at"), f"{where} item {ident!r} at")
        length = _num(d.get("len"), f"{where} item {ident!r} len")
        if length <= 0:
            raise TimelineError(f"{where} item {ident!r} has length {length:g}")
        fade = d.get("fade")
        fade = 0.0 if fade is None else max(0.0, min(_num(fade, "fade"), length))
        return cls(ident, at, length, fade, MappingProxyType(dict(d)))


class _Blank:
    """Nothing drives this channel here, by an owning row's choice."""
    _instance: Optional["_Blank"] = None

    def __new__(cls) -> "_Blank":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __repr__(self) -> str:
        return "BLANK"


BLANK = _Blank()


@dataclass(frozen=True)
class Clip:
    """An item showing on a channel at one beat."""
    item: Item
    row: str
    local: float              # beats since the item started
    weight: float             # 0..1, how much of it shows over what is under it
    fading: Optional[str] = None    # "in", "out", or None at full weight
    held: bool = False        # past its end: held under the next clip's fade-in

    def public(self) -> dict:
        out = {"row": self.row, "item": self.item.id,
               "local": round(self.local, 3), "weight": round(self.weight, 3)}
        if self.fading:
            out["fading"] = self.fading
        if self.held:
            out["held"] = True
        return out


Layer = Union[Clip, _Blank]


@dataclass(frozen=True)
class Entry:
    """One row's say at a beat: its clip, the clip held under it during a
    crossfade, and whether the row owns its channel."""
    clip: Clip
    under: Optional[Clip]
    exclusive: bool


@dataclass(frozen=True)
class Channel:
    """What drives one channel at one beat: a stack, top first. `rest` means
    the template or fallback show is underneath; a stack ending in BLANK means
    an owning row has said "nothing here"."""
    layers: tuple[Layer, ...] = ()
    rest: bool = True

    @property
    def top(self) -> Optional[Clip]:
        first = self.layers[0] if self.layers else None
        return first if isinstance(first, Clip) else None

    @property
    def blank(self) -> bool:
        return bool(self.layers) and self.layers[-1] is BLANK

    @property
    def driven(self) -> bool:
        """Does the timeline say anything here at all? False means the
        template or fallback has the channel to itself."""
        return bool(self.layers)

    def public(self) -> list:
        out: list = [l.public() if isinstance(l, Clip) else "blank"
                     for l in self.layers]
        if self.rest:
            out.append("rest")
        return out


NO_CHANNEL = Channel()


@dataclass(frozen=True)
class _Segment:
    start: float
    end: float
    index: int                 # into the row's items


class ClipRow:
    """One clips row, cut at build time into non-overlapping segments so a
    query is a bisect."""

    def __init__(self, row_id: str, items: Sequence[Item], exclusive: bool,
                 channels: tuple[str, ...]):
        self.id = row_id
        self.items = tuple(items)
        self.exclusive = exclusive
        self.channels = channels
        self.segments = _segments(self.items)
        self._starts = [s.start for s in self.segments]

    def at(self, beat: float):
        """BLANK, None, or (clip, the clip held under it or None)."""
        i = bisect.bisect_right(self._starts, beat) - 1
        if i < 0 or beat >= self.segments[i].end:
            return BLANK if self.exclusive else None
        seg = self.segments[i]
        item = self.items[seg.index]
        w_in = 1.0
        if item.fade > 0 and beat < item.at + item.fade:
            w_in = max(0.0, (beat - item.at) / item.fade)
        w_out = 1.0
        followed = (i + 1 < len(self.segments)
                    and self.segments[i + 1].start <= seg.end + EPS)
        if item.fade > 0 and not followed and beat > seg.end - item.fade:
            w_out = max(0.0, (seg.end - beat) / item.fade)
        weight = min(w_in, w_out)
        fading = None
        if weight < 1.0:
            fading = "in" if w_in <= w_out else "out"
        clip = Clip(item, self.id, beat - item.at, weight, fading)
        under = None
        if fading == "in" and i > 0 and self.segments[i - 1].end >= seg.start - EPS:
            prev = self.items[self.segments[i - 1].index]
            under = Clip(prev, self.id, beat - prev.at, 1.0, None, held=True)
        return clip, under


def _segments(items: Sequence[Item]) -> tuple[_Segment, ...]:
    """Who shows when, on one row: at any beat, of the items covering it, the
    one that started earliest (file order breaks a tie). Small rows, built
    once on the worker, so the plain way is fine."""
    edges = sorted({i.at for i in items} | {i.end for i in items})
    out: list[_Segment] = []
    for a, b in zip(edges, edges[1:]):
        mid = (a + b) / 2
        best = None
        for n, item in enumerate(items):
            if item.at <= mid < item.end and (
                    best is None or item.at < items[best].at):
                best = n
        if best is None:
            continue
        if out and out[-1].index == best and abs(out[-1].end - a) <= EPS:
            out[-1] = _Segment(out[-1].start, b, best)
        else:
            out.append(_Segment(a, b, best))
    return tuple(out)


# -- automation ---------------------------------------------------------------

def _shape(curve: str, x: float) -> float:
    if curve == "step":
        return 0.0
    if curve == "ease":
        return x * x * (3.0 - 2.0 * x)
    return x


def _shape_area(curve: str, x: float) -> float:
    """The integral of `_shape` from 0 to x."""
    if curve == "step":
        return 0.0
    if curve == "ease":
        return x ** 3 - x ** 4 / 2.0
    return x * x / 2.0


@dataclass(frozen=True)
class Curve:
    """Automation: values at beats, shaped between them, plus an optional wave
    on top (`waves.Wave`). Values are numbers, or anything else (a color) for
    `segment` and `pull` alone."""
    beats: tuple[float, ...]
    values: tuple[Any, ...]
    shapes: tuple[str, ...]          # shapes[i] shapes the segment INTO point i
    areas: Optional[tuple[float, ...]]   # integral up to each point; numeric only
    wave: Optional[Wave] = None

    @classmethod
    def from_points(cls, points: Sequence, where: str = "curve",
                    wave: Optional[Wave] = None) -> "Curve":
        if not points:
            raise TimelineError(f"{where} has no points")
        beats, values, shapes = [], [], []
        for n, p in enumerate(points):
            if not isinstance(p, (list, tuple)) or len(p) not in (2, 3):
                raise TimelineError(f"{where} point {n} must be [beat, value] "
                                    f"or [beat, value, curve]")
            beat = _num(p[0], f"{where} point {n} beat")
            if beats and beat <= beats[-1]:
                raise TimelineError(f"{where} point {n} is not after the one "
                                    f"before it")
            shape = p[2] if len(p) == 3 else "linear"
            if shape not in CURVES:
                raise TimelineError(f"{where} point {n} curve {shape!r} is not "
                                    f"one of {', '.join(CURVES)}")
            beats.append(beat)
            values.append(p[1])
            shapes.append(shape)
        numeric = all(isinstance(v, (int, float)) and not isinstance(v, bool)
                      for v in values)
        areas = None
        if numeric:
            values = [float(v) for v in values]
            acc = [0.0]
            for i in range(1, len(beats)):
                w = beats[i] - beats[i - 1]
                a, b = values[i - 1], values[i]
                acc.append(acc[-1] + w * (a + (b - a) * _shape_area(shapes[i], 1.0)))
            areas = tuple(acc)
        return cls(tuple(beats), tuple(values), tuple(shapes), areas, wave)

    @property
    def numeric(self) -> bool:
        return self.areas is not None

    def segment(self, beat: float) -> tuple[Any, Any, float]:
        """(from, to, t): the value is `from` blended towards `to` by `t`,
        already shaped. For a color the caller blends; for a number `value`
        does it."""
        i = bisect.bisect_right(self.beats, beat)
        if i == 0:
            return self.values[0], self.values[0], 0.0
        if i == len(self.beats):
            return self.values[-1], self.values[-1], 0.0
        x = (beat - self.beats[i - 1]) / (self.beats[i] - self.beats[i - 1])
        return self.values[i - 1], self.values[i], _shape(self.shapes[i], x)

    def value(self, beat: float) -> float:
        if not self.numeric:
            raise TimelineError("value() needs a numeric curve; use segment()")
        a, b, t = self.segment(beat)
        base = a + (b - a) * t
        return base if self.wave is None else base + self.wave.level(beat)

    def pull(self, beat: float) -> Optional[tuple[Any, float]]:
        """A color curve's wave at `beat`: (the color it swings toward, how
        far, 0..1), or None without one. The caller blends, as for `segment`."""
        if self.wave is None or self.numeric:
            return None
        return self.wave.toward, max(0.0, min(1.0, self.wave.level(beat)))

    def integral(self, beat: float) -> float:
        """The area under the curve to `beat`, from a fixed origin -- only
        differences of it mean anything. Exact, wave included (`waves.area`)."""
        if self.areas is None:
            raise TimelineError("only a numeric curve has an integral")
        wave = self.wave.integral(beat) if self.wave is not None else 0.0
        beats, values = self.beats, self.values
        if beat <= beats[0]:
            return values[0] * (beat - beats[0]) + wave
        if beat >= beats[-1]:
            return self.areas[-1] + values[-1] * (beat - beats[-1]) + wave
        i = bisect.bisect_right(beats, beat)
        w = beats[i] - beats[i - 1]
        x = (beat - beats[i - 1]) / w
        a, b = values[i - 1], values[i]
        return (self.areas[i - 1] + w * (a * x + (b - a) * _shape_area(self.shapes[i], x))
                + wave)


# -- hits ---------------------------------------------------------------------

@dataclass(frozen=True)
class Hit:
    """A hit firing at one beat."""
    item: Item
    row: str
    progress: float           # 0..1 through its window
    level: float              # after its envelope
    crossed: bool = False     # shorter than a frame: reported for the one frame

    def public(self) -> dict:
        out = {"row": self.row, "item": self.item.id,
               "hit": self.item.data.get("hit"),
               "level": round(self.level, 3), "progress": round(self.progress, 3)}
        if self.item.data.get("role"):
            out["role"] = self.item.data["role"]
        if self.crossed:
            out["crossed"] = True
        return out


class HitRow:
    def __init__(self, row_id: str, items: Sequence[Item]):
        self.id = row_id
        self.items = tuple(sorted(items, key=lambda i: i.at))
        self._starts = [i.at for i in self.items]
        self._longest = max((i.len for i in self.items), default=0.0)

    def _hit(self, item: Item, beat: float, crossed: bool = False) -> Hit:
        progress = 0.0 if crossed else min(1.0, max(0.0, (beat - item.at) / item.len))
        level = item.data.get("level")
        level = 1.0 if level is None else float(level)
        if item.data.get("envelope") == "decay":
            level *= 1.0 - progress
        return Hit(item, self.id, progress, level, crossed)

    def active(self, beat: float) -> list[Hit]:
        hi = bisect.bisect_right(self._starts, beat)
        lo = bisect.bisect_left(self._starts, beat - self._longest)
        return [self._hit(i, beat) for i in self.items[lo:hi]
                if i.at <= beat < i.end]

    def crossed(self, prev: float, beat: float) -> list[Hit]:
        """Started after `prev`, already over by `beat`: never active on any
        frame, so reported once, at its start."""
        lo = bisect.bisect_right(self._starts, prev)
        hi = bisect.bisect_right(self._starts, beat)
        return [self._hit(i, beat, crossed=True) for i in self.items[lo:hi]
                if i.end <= beat]


# -- other outputs ------------------------------------------------------------

class ExternalRow:
    """A row for an output other than the lights -- OSC, MIDI, visuals
    (milestone 3). Its items are windows, like hits: on from `at` for `len`
    beats, whatever the deck did to get there. It may also carry a curve
    (`points`), sent as a value. What an item SAYS -- an OSC address, a note,
    a scene -- is the output's business; `data` is the whole row, read-only."""

    def __init__(self, row_id: str, output: str, data: Mapping,
                 items: Sequence[Item], curve: Optional[Curve]):
        self.id = row_id
        self.output = output
        self.data = MappingProxyType(dict(data))
        self.windows = HitRow(row_id, items)
        self.curve = curve

    @property
    def items(self) -> tuple[Item, ...]:
        return self.windows.items


@dataclass(frozen=True)
class ExternalFrame:
    """One external row at one beat: the items on (and, in forward play, any
    too short to have been on for a whole frame), and its curve's value."""
    row: ExternalRow
    items: tuple[Hit, ...]
    value: Optional[float]

    def public(self) -> dict:
        out: dict = {"row": self.row.id, "output": self.row.output,
                     "items": [{"item": h.item.id, "progress": round(h.progress, 3),
                                **({"crossed": True} if h.crossed else {})}
                               for h in self.items]}
        if self.value is not None:
            out["value"] = round(self.value, 4)
        return out


# -- the whole thing ----------------------------------------------------------

def _external_row(row: Mapping) -> ExternalRow:
    rid = row.get("id") or "external"
    where = f"row {rid!r}"
    output = row.get("output")
    if not isinstance(output, str) or not output:
        raise TimelineError(f"{where} names no output")
    items = [Item.from_dict(i, where) for i in row.get("items") or ()]
    points = row.get("points")
    curve = Curve.from_points(points, where) if points else None
    if curve is not None and not curve.numeric:
        raise TimelineError(f"{where}: an external curve's values must be numbers")
    return ExternalRow(rid, output, row, items, curve)


def _own_target(row: Mapping) -> tuple[str, ...]:
    return (row["target"],)


@dataclass(frozen=True)
class Frame:
    """Everything a timeline says at one beat."""
    beat: float
    channels: Mapping[str, Channel]
    automation: Mapping[str, Any]    # a number, or (from, to, t) for non-numbers,
                                     # with (toward, pull) after it when a wave swings it
    hits: tuple[Hit, ...]


class Timeline:
    """A timeline's rows, ready to query. Immutable once built."""

    def __init__(self, clip_rows: Sequence[ClipRow], hit_rows: Sequence[HitRow],
                 curves: Mapping[str, tuple[str, Curve]],
                 external: Sequence[Mapping] = (),
                 meta: Optional[Mapping] = None):
        self.clip_rows = tuple(clip_rows)
        self.hit_rows = tuple(hit_rows)
        self.curves = MappingProxyType(dict(curves))
        self.external = tuple(MappingProxyType(dict(r)) for r in external)
        self.external_rows = tuple(_external_row(r) for r in self.external)
        self.meta = MappingProxyType(dict(meta or {}))
        order: list[str] = []
        by_channel: dict[str, list[ClipRow]] = {}
        for row in self.clip_rows:              # top to bottom: precedence
            for ch in row.channels:
                if ch not in by_channel:
                    order.append(ch)
                by_channel.setdefault(ch, []).append(row)
        self.channels = tuple(order)
        self._rows_for = {ch: tuple(rows) for ch, rows in by_channel.items()}

    # -- building ----------------------------------------------------------

    @classmethod
    def from_rows(cls, rows: Iterable[Mapping],
                  channels: Optional[Callable[[Mapping], Iterable[str]]] = None,
                  meta: Optional[Mapping] = None) -> "Timeline":
        """From rows as authored -- a timeline's, or a routine's in its own
        beats. `channels(row)` names what a clips row drives; by default, its
        target."""
        channels = channels or _own_target
        clip_rows: list[ClipRow] = []
        hit_rows: list[HitRow] = []
        curves: dict[str, tuple[str, Curve]] = {}
        external: list[Mapping] = []
        for n, row in enumerate(rows):
            if not isinstance(row, Mapping):
                raise TimelineError(f"row {n} must be an object")
            rid = row.get("id") or f"row{n}"
            kind = row.get("type")
            where = f"row {rid!r}"
            if kind == "clips":
                items = [Item.from_dict(i, where) for i in row.get("items") or ()]
                gap = row.get("gap") or FILL
                if gap not in (FILL, EXCLUSIVE):
                    raise TimelineError(f"{where} gap {gap!r} is not fill or "
                                        f"exclusive")
                clip_rows.append(ClipRow(rid, items, gap == EXCLUSIVE,
                                         tuple(channels(row))))
            elif kind == "hits":
                hit_rows.append(HitRow(rid, [Item.from_dict(i, where)
                                             for i in row.get("items") or ()]))
            elif kind == "automation":
                target = row.get("target")
                if not isinstance(target, str) or not target:
                    raise TimelineError(f"{where} automates nothing")
                wave = None
                if row.get("wave") is not None:
                    try:
                        wave = Wave.from_spec(row["wave"], f"{where} wave")
                    except ValueError as exc:
                        raise TimelineError(str(exc)) from None
                curve = Curve.from_points(row.get("points") or (), where, wave)
                curves.setdefault(target, (rid, curve))  # the higher row wins
            elif kind == "external":
                _external_row(row)              # refuse what cannot be built
                external.append(row)            # and keep it whole
            else:
                raise TimelineError(f"{where} has unknown type {kind!r}")
        return cls(clip_rows, hit_rows, curves, external, meta)

    @classmethod
    def from_doc(cls, doc: Mapping,
                 channels: Optional[Callable[[Mapping], Iterable[str]]] = None
                 ) -> "Timeline":
        """From a whole timeline document. Everything but its rows -- track,
        palettes, default palette -- is kept in `meta`."""
        if not isinstance(doc, Mapping) or not isinstance(doc.get("rows"), list):
            raise TimelineError("a timeline needs a list of rows")
        meta = {k: v for k, v in doc.items() if k != "rows"}
        return cls.from_rows(doc["rows"], channels, meta)

    # -- asking ------------------------------------------------------------

    def entries(self, channel: str, beat: float) -> tuple[Entry, ...]:
        """EVERY row with something to say about `channel` at `beat`, top
        first, ending at the first BLANK -- nothing below a blank matters.

        `channel()` stops at the first opaque clip, which is the whole answer
        when a clip covers the whole channel. A caller whose clips cover only
        PART of it -- the lights, where a routine may drive the movers' color
        and not the pinspots' -- needs the rows underneath too, for whatever
        the top one leaves uncovered. This is that list."""
        out: list[Entry] = []
        for row in self._rows_for.get(channel, ()):
            hit = row.at(beat)
            if hit is None:
                continue                         # a fill gap: ask the next row
            if hit is BLANK:
                out.append(BLANK)
                break
            clip, under = hit
            out.append(Entry(clip, under, row.exclusive))
        return tuple(out)

    def channel(self, channel: str, beat: float) -> Channel:
        """What drives `channel` at `beat`, top layer first, as far down as
        anything shows through."""
        layers: list[Layer] = []
        for entry in self.entries(channel, beat):
            if entry is BLANK:
                layers.append(BLANK)
                return Channel(tuple(layers), rest=False)
            clip = entry.clip
            layers.append(clip)
            if clip.weight >= 1.0:
                return Channel(tuple(layers), rest=False)
            if entry.under is not None:          # crossfading from the clip before
                layers.append(entry.under)
                return Channel(tuple(layers), rest=False)
            if entry.exclusive:                  # fading to or from nothing
                layers.append(BLANK)
                return Channel(tuple(layers), rest=False)
        return Channel(tuple(layers), rest=True)

    def automation(self, target: str, beat: float) -> Any:
        """The value of an automated target, None if nothing automates it: a
        number, or for a color (from, to, t) -- and, when a wave swings it,
        (toward, pull) after those, so an explanation shows the whole value."""
        entry = self.curves.get(target)
        if entry is None:
            return None
        curve = entry[1]
        if curve.numeric:
            return curve.value(beat)
        pull = curve.pull(beat)
        return curve.segment(beat) + (pull if pull is not None else ())

    def hits(self, beat: float, prev: Optional[float] = None,
             jumped: bool = False) -> tuple[Hit, ...]:
        """Hits firing at `beat`. Given the previous frame's beat and no jump
        between, also the ones too short to have been seen on any frame."""
        out: list[Hit] = []
        for row in self.hit_rows:
            out.extend(row.active(beat))
            if prev is not None and not jumped and prev < beat:
                out.extend(row.crossed(prev, beat))
        return tuple(out)

    def external_at(self, beat: float, prev: Optional[float] = None,
                    jumped: bool = False) -> tuple[ExternalFrame, ...]:
        """Every external row at `beat`: its items on, by window -- so a jump
        lands inside one exactly as continuous play would -- plus, in forward
        play since `prev`, the ones too short for any frame to have seen."""
        out: list[ExternalFrame] = []
        for row in self.external_rows:
            items = row.windows.active(beat)
            if prev is not None and not jumped and prev < beat:
                items.extend(row.windows.crossed(prev, beat))
            value = row.curve.value(beat) if row.curve is not None else None
            out.append(ExternalFrame(row, tuple(items), value))
        return tuple(out)

    def at(self, beat: float, prev: Optional[float] = None,
           jumped: bool = False) -> Frame:
        return Frame(
            beat=beat,
            channels={ch: self.channel(ch, beat) for ch in self.channels},
            automation={t: self.automation(t, beat) for t in self.curves},
            hits=self.hits(beat, prev, jumped))

    @property
    def span(self) -> Optional[tuple[float, float]]:
        """First and last beat anything happens at, or None if nothing does."""
        lo, hi = [], []
        for row in self.clip_rows:
            lo.extend(i.at for i in row.items)
            hi.extend(i.end for i in row.items)
        for row in self.hit_rows:
            lo.extend(i.at for i in row.items)
            hi.extend(i.end for i in row.items)
        for _, curve in self.curves.values():
            lo.append(curve.beats[0])
            hi.append(curve.beats[-1])
        for row in self.external_rows:
            lo.extend(i.at for i in row.items)
            hi.extend(i.end for i in row.items)
            if row.curve is not None:
                lo.append(row.curve.beats[0])
                hi.append(row.curve.beats[-1])
        return (min(lo), max(hi)) if lo else None

    def explain(self, beat: float) -> dict:
        """The frame at `beat` as plain JSON-able data -- for the designer, MCP
        and a person checking a timeline from the command line."""
        frame = self.at(beat)
        auto = {}
        for target, value in frame.automation.items():
            if isinstance(value, tuple):
                a, b, t = value[:3]
                auto[target] = {"from": a, "to": b, "t": round(t, 3)}
                if len(value) > 3:               # a wave swings it
                    toward, pull = value[3:]
                    auto[target].update(toward=toward, pull=round(pull, 3))
            else:
                auto[target] = round(value, 4)
        out = {"beat": beat,
               "channels": {ch: c.public() for ch, c in frame.channels.items()},
               "automation": auto,
               "hits": [h.public() for h in frame.hits]}
        external = [e.public() for e in self.external_at(beat)
                    if e.items or e.value is not None]
        if external:
            out["external"] = external
        return out
