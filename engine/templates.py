"""
Templates: what the lights do on a track nobody drew a timeline for.

A template set (`templates/<id>.json`) maps rekordbox's phrase labels to
routines -- Intro to an idle orbit, Chorus to a fan sweep -- and says what to
cycle through, every N bars, when a track has no phrases at all. It is the
middle of the fallback chain (decided with the user, F19):

    timeline  >  template  >  the operator's or auto mode's show

and it runs only while a DJ track plays with Follow armed (milestone 2, with
the user); otherwise the rig is the operator's, as it always was.

**Where a template gets its phrase.** Three places, best first:

- a prepped track's own phrases (its rekordbox analysis), in TRACK beats --
  known in advance, so a phrase change lands exactly on its boundary;
- the deck, live, for a guest's track the folder does not know: rkbx_link's
  phrase label, or the phrase our beat-link-trigger expressions read off the
  DJ's USB -- in CLOCK beats, from the moment it changes;
- neither: a cycle through `bars.cycle`, one entry every `bars.every` bars, on
  the track's grid when it has one and on the clock's bars when it does not.

A label is looked up exactly ("Verse 2"), then without its number ("Verse"),
then as `"*"` (decided with the user, F19).

**How a pick plays.** Each distinct pick -- a routine, its variation, its
params, its palette -- is compiled once, on the worker, into a `Program` of its
own: one scene clip from beat 0, as long as it needs to be (the idle routine's
shape). It runs on its own beat, counted from where its phrase (or block)
began, so a routine's phrasing lines up with the music's. A pick that comes
round again with the same routine simply carries on -- Verse 1 into Verse 2 does
not restart the movement.

**Changing pick** crossfades over the set's `transition.fade_beats`, in
parameter space (`state.blend`), slot by slot -- so the result is still a Show
whose colour, movement and level lists a timeline can sit on top of, gap by
gap. A jump (a loop, a hot cue) that lands in another phrase cuts, as every
jump does.

**Switching set** takes over at the next downbeat (decided with the user),
crossfading over the NEW set's fade.

Pure apart from compiling: no clock, no I/O. `TemplateRunner.begin` is called
once a frame on the output thread; `compile_set` runs on the worker.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field, replace
from typing import Any, Mapping, Optional

from . import program as programmod
from . import showfiles
from . import state as statemod
from . import timeline as timelinemod
from . import tracktime

LONG = 1e7                    # beats: a pick's clip never runs out
BEATS_PER_BAR = tracktime.BEATS_PER_BAR
SLOT_LISTS = ("color", "movement", "fx")


# -- choosing ---------------------------------------------------------------------

def pick(doc: Mapping, label: Optional[str]) -> Optional[Mapping]:
    """The set's entry for a phrase: the exact label, then its family, then
    `"*"`. None when the set says nothing for it."""
    phrases = doc.get("phrases") or {}
    if label:
        if isinstance(phrases.get(label), Mapping):
            return phrases[label]
        fam = tracktime.family(label)
        if isinstance(phrases.get(fam), Mapping):
            return phrases[fam]
    star = phrases.get("*")
    return star if isinstance(star, Mapping) else None


def bars_block(doc: Mapping) -> Optional[float]:
    """Beats per bar-count block, or None when the set has no `bars`."""
    bars = doc.get("bars") or {}
    every = bars.get("every")
    cycle = bars.get("cycle")
    if not cycle or not isinstance(every, (int, float)) or every <= 0:
        return None
    return float(every) * BEATS_PER_BAR


def bars_pick(doc: Mapping, block: int) -> Optional[Mapping]:
    cycle = (doc.get("bars") or {}).get("cycle") or []
    return cycle[block % len(cycle)] if cycle else None


def pick_key(p: Mapping) -> str:
    """What makes two picks the same program."""
    return json.dumps({k: p.get(k) for k in ("routine", "variation", "params",
                                              "palette")}, sort_keys=True)


@dataclass(frozen=True)
class Cue:
    """What the template plays now: which pick, from which beat (its routine's
    beat 0), and what chose it -- a phrase label, or "bars"."""
    key: str
    start: float
    label: str
    routine: str


def cue_for_phrase(cset: "CompiledSet", label: Optional[str], start: float
                   ) -> Optional[Cue]:
    p = pick(cset.doc, label)
    if p is None or pick_key(p) not in cset.programs:
        return None
    return Cue(pick_key(p), float(start), label or "*", str(p.get("routine")))


def cue_for_bars(cset: "CompiledSet", beat: float) -> Optional[Cue]:
    """The bar-count cycle at a beat: block `n` covers bars n*every onwards,
    beat 0 the first downbeat (the track's, or the clock's)."""
    size = bars_block(cset.doc)
    if size is None:
        return None
    block = math.floor(beat / size)
    p = bars_pick(cset.doc, block)
    if p is None or pick_key(p) not in cset.programs:
        return None
    return Cue(pick_key(p), block * size, "bars", str(p.get("routine")))


def cue_in_track(cset: "CompiledSet", phrases: Optional[tracktime.PhraseMap],
                 beat: float) -> Optional[Cue]:
    """For a prepped track: its phrase at `beat`, else the bar-count cycle on
    its grid. Outside every phrase (a pickup, a tail) is `"*"`, else bars."""
    if phrases is not None and len(phrases):
        ph = phrases.at(beat)
        if ph is not None:
            cue = cue_for_phrase(cset, ph.label, ph.start)
            if cue is not None:
                return cue
        else:
            star = cue_for_phrase(cset, None, _gap_start(phrases, beat))
            if star is not None:
                return star
    return cue_for_bars(cset, beat)


def _gap_start(phrases: tracktime.PhraseMap, beat: float) -> float:
    """Where the gap containing `beat` began: the end of the phrase before it,
    else the bar the beat is in."""
    before = [p.end for p in phrases.phrases if p.end <= beat]
    return max(before) if before else math.floor(beat / BEATS_PER_BAR) * BEATS_PER_BAR


def next_downbeat(beat: float) -> float:
    """The first bar line at or after `beat` -- strictly after, unless `beat`
    is already on one."""
    bar = math.ceil(beat / BEATS_PER_BAR - 1e-9)
    return bar * BEATS_PER_BAR


# -- compiling, on the worker -------------------------------------------------------

@dataclass
class CompiledSet:
    """A template set built for one rig: a program per distinct pick."""
    id: str
    doc: Mapping
    programs: dict[str, programmod.Program]
    fade: float
    problems: list[str] = field(default_factory=list)

    @property
    def name(self) -> str:
        return str(self.doc.get("name") or self.id)


def pick_timeline(doc: Mapping, p: Mapping) -> timelinemod.Timeline:
    """A pick as a timeline: one scene clip from beat 0, and the set's
    palettes with the pick's (else the set's) as the default."""
    item: dict[str, Any] = {"id": "pick", "kind": "routine",
                            "routine": p.get("routine"), "at": 0, "len": LONG}
    for k in ("variation", "params", "bind"):
        if p.get(k):
            item[k] = p[k]
    meta: dict[str, Any] = {"rows": [{"id": "pick", "type": "clips",
                                      "target": "scene", "gap": "fill",
                                      "items": [item]}]}
    if doc.get("palettes"):
        meta["palettes"] = doc["palettes"]
    palette = p.get("palette") or doc.get("palette")
    if palette:
        meta["palette"] = palette
    return timelinemod.Timeline.from_doc(meta, showfiles.timeline_channels)


def all_picks(doc: Mapping) -> list[tuple[str, Mapping]]:
    """Every pick a set can make, with what names it, once each."""
    out: dict[str, tuple[str, Mapping]] = {}
    for label, p in (doc.get("phrases") or {}).items():
        if isinstance(p, Mapping):
            out.setdefault(pick_key(p), (str(label), p))
    for i, p in enumerate((doc.get("bars") or {}).get("cycle") or []):
        if isinstance(p, Mapping):
            out.setdefault(pick_key(p), (f"bars {i + 1}", p))
    return list(out.values())


def compile_set(set_id: str, doc: Mapping, routines: Mapping[str, Mapping],
                rigging) -> CompiledSet:
    """Build every pick of a set for this rig. Never raises for a problem in
    the set: a pick that cannot be built is left out and listed, and where the
    set would have played it the lanes below show instead."""
    programs: dict[str, programmod.Program] = {}
    problems: list[str] = []
    for label, p in all_picks(doc):
        where = f"template {set_id!r} {label}"
        if p.get("routine") not in routines:
            problems.append(f"{where}: routine {p.get('routine')!r} is not in "
                            f"routines/")
            continue
        try:
            prog = programmod.compile(pick_timeline(doc, p), routines, rigging, where)
        except timelinemod.TimelineError as exc:
            problems.append(f"{where}: {exc}")
            continue
        problems += prog.problems
        programs[pick_key(p)] = prog
    fade = float(((doc.get("transition") or {}).get("fade_beats")) or 0.0)
    return CompiledSet(set_id, doc, programs, max(0.0, fade),
                       list(dict.fromkeys(problems)))


# -- playing, on the output thread ---------------------------------------------------

@dataclass
class _Playing:
    cue: Cue
    prog: programmod.Program


class TemplateRunner:
    """The template on stage: ONE Show, whatever it plays, so the runner sets
    it once -- and so a timeline can take it as its fallback, gap by gap.

    Call `begin()` every frame with the set and the cue for this beat; then
    evaluate `show` (or hand it to a Program as its fallback)."""

    def __init__(self) -> None:
        self.show = statemod.Show(
            base=[self._base_layer],
            color=[self._slot_layer("color")],
            movement=[self._slot_layer("movement")],
            fx=[self._slot_layer("fx")])
        self.grabbed: frozenset[str] = frozenset()
        self.current: Optional[_Playing] = None
        self.outgoing: Optional[_Playing] = None
        self._set: Optional[CompiledSet] = None
        self._fade_from = 0.0
        self._fade_beats = 0.0
        self._weight = 1.0
        self._fallback = statemod.Show()

    @property
    def cue(self) -> Optional[Cue]:
        return self.current.cue if self.current else None

    def reset(self) -> None:
        """Forget what was playing: the next cue starts clean (a cut)."""
        self.current = self.outgoing = None
        self._set = None

    def begin(self, cset: Optional[CompiledSet], cue: Optional[Cue], beat: float,
              prev: Optional[float] = None, jumped: bool = False,
              fallback: Optional[statemod.Show] = None,
              base_palette: Optional[Mapping[str, tuple]] = None,
              fade: Optional[float] = None) -> None:
        """This frame: play `cue` from `cset` at `beat`. `fade` overrides the
        set's transition for a change that happens now (a set switch fades
        over the NEW set's)."""
        self._fallback = fallback if fallback is not None else statemod.Show()
        prog = cset.programs.get(cue.key) if (cset and cue) else None
        if prog is None:
            self.current = self.outgoing = None
            self._set = cset
            return
        cur = self.current
        if cur is not None and cur.cue.key == cue.key and self._set is cset:
            # The same pick again (Verse 1 into Verse 2, a one-entry cycle):
            # it carries on from where it started rather than restarting --
            # under the new phrase's name.
            if cue.label != cur.cue.label:
                self.current = _Playing(replace(cue, start=cur.cue.start), cur.prog)
        else:
            beats = cset.fade if fade is None else fade
            if cur is None or jumped or beats <= 0:
                self.outgoing = None
            else:
                self.outgoing = cur
                self._fade_from, self._fade_beats = beat, beats
            self.current = _Playing(cue, prog)
            self._set = cset
        if self.outgoing is not None:
            t = (beat - self._fade_from) / self._fade_beats
            if t >= 1.0 or self.outgoing.prog is self.current.prog:
                self.outgoing = None
                self._weight = 1.0
            else:
                self._weight = max(0.0, t)
        else:
            self._weight = 1.0
        for playing in filter(None, (self.current, self.outgoing)):
            start = playing.cue.start
            playing.prog.grabbed = self.grabbed
            playing.prog.begin(beat - start,
                               prev=None if prev is None else prev - start,
                               jumped=jumped, fallback=self._fallback,
                               base_palette=base_palette)

    # -- the layers --------------------------------------------------------

    def _base_layer(self, ctx, out) -> None:
        # Every pick's program runs the same fallback base, so one will do.
        for layer in self._fallback.base:
            layer(ctx, out)

    def _slot_layer(self, name: str) -> statemod.Layer:
        def layer(ctx, out):
            cur, old = self.current, self.outgoing
            if cur is None:
                for fb in getattr(self._fallback, name):
                    fb(ctx, out)
                return
            if old is None:
                for lay in getattr(cur.prog.show, name):
                    lay(ctx, out)
                return
            a = {fid: replace(st) for fid, st in out.items()}
            for lay in getattr(old.prog.show, name):
                lay(ctx, a)
            b = {fid: replace(st) for fid, st in out.items()}
            for lay in getattr(cur.prog.show, name):
                lay(ctx, b)
            out.update(statemod.blend(a, b, self._weight))
        return layer

    def status(self) -> Optional[dict]:
        cue = self.cue
        if cue is None:
            return None
        return {"set": self._set.id if self._set else None, "label": cue.label,
                "routine": cue.routine, "start": cue.start,
                "fading": self.outgoing is not None}
