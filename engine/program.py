"""
The lights compiler: a track's timeline, turned into what the fixtures do.

`timeline.py` says, for a beat, which clip is on which lane with what weight.
This is the only place that meets `state.py`: it builds every clip into layers
for THIS rig, and evaluates them per fixture each frame. The result is a
`Program` with one `Show` that never changes object -- the runner (F19i) sets it
once and calls `begin()` every frame -- so a cue GO cannot swap it out from
under the track, and `finish()` (safety, then the strobe policy) still runs
after everything here, exactly as it does for any other show.

**Per fixture, per slot** (decided with the user, F19h). A lane's clip drives
only the fixtures it actually uses -- a routine that colors the movers says
nothing about the pinspots' color -- so each fixture walks the lanes on its
own, top first:

- a clip that drives it: that clip's content;
- a clip that does not: transparent -- the next lane down, or, on a lane that
  OWNS the track, rest;
- a clip mid-fade: blended (`state.blend`, in parameter space) over whatever is
  under it -- the clip before it on the same lane while they crossfade, else the
  lanes below;
- a gap on a lane that owns the track: rest;
- under every lane: the fallback show -- the template in milestone 2, today the
  operator's or auto mode's show -- slot for slot (color, movement, and the
  level slot's `fx`).

**Rest** is: movers on the venue's rest point (its `rest_point`, else the
ball), color white, level DARK.

Each source evaluates ONCE per slot per frame into its own scratch copy of the
states, and only the fixtures it claims are taken from it. That is what keeps
two movement sources from adding their offsets together: `move_layer` is
additive, and two of them run over one state would put a head somewhere
neither meant.

**Time.** A clip runs on its own time: its phase starts where it starts, and is
a pure function of the beat -- the timeline's `rate.<slot>` curve times the
routine's own, integrated (exactly where possible, from a table built at compile
time where both vary). So a loop or a hot cue lands on the frame that was
authored there, every pass.

Automation: the timeline's `size` multiplies the operator's, `spread` and
`center` add to them, `master` multiplies the final intensity; a routine's own
automation stacks on top for its own fixtures only. The timeline's
`param.<name>` overrides that parameter on every routine clip that has it; a
routine's own `param.<name>` lane drives it in that routine's beats wherever
the timeline does not.

Hits -- flash, strobe, blackout -- come from the timeline's hit rows and from
the hit rows of any routine clip on top of a lane, scaled by its weight. Flash
sets intensity up, strobe opens the shutter, blackout goes last and takes
everything down; the strobe policy caps the shutter afterwards whatever asked.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Sequence

from . import blocks as blocksmod
from . import geometry as geo
from . import library as libmod
from . import rig as rigmod
from . import routines as routinesmod
from . import showfiles
from . import state as statemod
from . import timeline as timelinemod
from . import tracktime

SLOTS = statemod.SLOTS
PHASE_FIELD = {"movement": "motion_bar", "color": "color_bar", "level": "level_bar"}
FALLBACK_LIST = {"movement": "movement", "color": "color", "level": "fx"}
BAR = float(tracktime.BEATS_PER_BAR)
TABLE_STEP = 1.0 / 16.0           # beats, for a rate warp with two curves
# How far such a table reaches. Past it the warp runs on at its last rate --
# exact once both curves have settled, which a curve does after its last
# point. Bounded so one long clip cannot ask for millions of entries. A curve
# with a wave never settles, so on a clip longer than this with rate lanes in
# both the timeline and its routine, the far end drifts; the horizon is the
# clip's own length, so that is a clip of over half an hour.
TABLE_MAX_BEATS = 8192.0
ROLES = showfiles.PALETTE_ROLES
WHITE = blocksmod.WHITE

Palette = dict[str, tuple[float, float, float]]


# -- time ---------------------------------------------------------------------

class Warp:
    """Beats of motion a clip has run, `local` beats after it started:
    ∫ T(at + x) · R(x) dx, T the timeline's rate curve and R the routine's.

    Exact (closed-form `Curve.integral`) when either is absent, which is
    nearly always; tabulated at compile time when both vary, since their
    product has no closed form. Either way a pure function of the beat."""

    def __init__(self, at: float, timeline_rate: Optional[timelinemod.Curve],
                 routine_rate: Optional[timelinemod.Curve], loop_len: Optional[float],
                 horizon: float):
        self.at = at
        self.T = timeline_rate
        self.R = routine_rate
        self.loop_len = loop_len if loop_len and loop_len > 0 else None
        self._table: Optional[list[float]] = None
        if self.T is not None and self.R is not None:
            self._build(min(max(horizon, 1.0), TABLE_MAX_BEATS))

    def _r(self, x: float) -> float:
        if self.loop_len is not None:
            x = x % self.loop_len
        return self.R.value(x)

    def _r_integral(self, x: float) -> float:
        R = self.R
        if self.loop_len is None:
            return R.integral(x) - R.integral(0.0)
        n = math.floor(x / self.loop_len)
        full = R.integral(self.loop_len) - R.integral(0.0)
        return n * full + R.integral(x - n * self.loop_len) - R.integral(0.0)

    def _build(self, horizon: float) -> None:
        steps = int(math.ceil(horizon / TABLE_STEP)) + 1
        acc, out = 0.0, [0.0]
        prev = self.T.value(self.at) * self._r(0.0)
        for i in range(1, steps + 1):
            x = i * TABLE_STEP
            now = self.T.value(self.at + x) * self._r(x)
            acc += (prev + now) * 0.5 * TABLE_STEP
            out.append(acc)
            prev = now
        self._table = out
        self._last_rate = prev

    def __call__(self, local: float) -> float:
        if self.T is None and self.R is None:
            return local
        if self.R is None:
            return self.T.integral(self.at + local) - self.T.integral(self.at)
        if self.T is None:
            return self._r_integral(local)
        table = self._table
        if local <= 0:
            return local * self.T.value(self.at) * self._r(0.0)
        pos = local / TABLE_STEP
        i = int(pos)
        if i + 1 >= len(table):
            end = (len(table) - 1) * TABLE_STEP
            return table[-1] + (local - end) * self._last_rate
        return table[i] + (table[i + 1] - table[i]) * (pos - i)


# -- sources ------------------------------------------------------------------

class _Memo:
    """A thunk evaluated at most once -- the lanes below a fixture are asked
    for both above and below a fade, and must not be built twice."""
    __slots__ = ("fn", "done", "value")

    def __init__(self, fn: Callable[[], statemod.FixtureState]):
        self.fn, self.done, self.value = fn, False, None

    def __call__(self) -> statemod.FixtureState:
        if not self.done:
            self.value, self.done = self.fn(), True
        return self.value


@dataclass
class LeafSource:
    """A look or snapshot clip: one block per slot, on its clip's own time."""
    blocks: dict[str, blocksmod.Block]
    warps: dict[str, Warp]

    def content(self, prog: "Program", ctx, slot, f, clip, incoming, fallback, cache):
        block = self.blocks.get(slot)
        if block is None or f.fid not in block.claims:
            return fallback()
        key = (id(self), slot)
        states = cache.get(key)
        if states is None:
            phase = self.warps[slot](clip.local) / BAR
            states = cache[key] = prog._run(ctx, block, slot, phase, incoming, {})
        return states[f.fid]


@dataclass
class RoutineSource:
    """A routine clip: its own rows, walked per fixture like the timeline's,
    with the lanes below the clip as what shows through it."""
    inst: routinesmod.Instance
    warps: dict[str, Warp]

    def _fields(self, ctx, slot: str, sched: float) -> dict:
        if slot != "movement":
            return {}
        inst, out = self.inst, {}
        size = inst.curve("size")
        if size is not None:
            out["move_size"] = ctx.move_size * size.value(sched)
        spread = inst.curve("spread")
        if spread is not None:
            out["move_spread"] = ctx.move_spread + spread.value(sched)
        bearing, elev = inst.curve("center.bearing"), inst.curve("center.elevation")
        if bearing is not None or elev is not None:
            cb, ce = ctx.move_center
            out["move_center"] = (cb + (bearing.value(sched) if bearing else 0.0),
                                  ce + (elev.value(sched) if elev else 0.0))
        return out

    def content(self, prog: "Program", ctx, slot, f, clip, incoming, fallback, cache):
        inst = self.inst
        local = clip.local
        sched = inst.sched(local)
        # Where its own param and argument lanes are read, set where it is
        # EVALUATED. Setting it only for the routines `_visible_routines`
        # finds is not enough: that stops at the first full-weight clip, but a
        # routine on a lower lane still plays for the fixtures the top one
        # leaves alone, and read a stale beat there.
        inst.now = sched
        entries = inst.timeline.entries(slot, sched)
        if not entries:
            return fallback()
        fields = self._fields(ctx, slot, sched)
        warp = self.warps[slot]
        now = warp(local)

        def inner(ctx, slot, f, inner_clip, incoming, fb):
            block = inst.blocks.get((inner_clip.row, inner_clip.item.id))
            if block is None or f.fid not in block.claims:
                return fb()
            key = (id(self), clip.item.id, inner_clip.row, inner_clip.item.id, slot)
            states = cache.get(key)
            if states is None:
                start = inst.item_start(local, inner_clip.item.at)
                phase = (now - warp(start)) / BAR
                states = cache[key] = prog._run(ctx, block, slot, phase, incoming,
                                                fields)
            return states[f.fid]

        return prog._resolve(ctx, slot, f, entries, 0, incoming, fallback, inner)


# -- the program --------------------------------------------------------------

@dataclass
class Program:
    """One track's timeline, compiled for one rig. Build with `compile`; call
    `begin()` every frame, then evaluate `show` like any other Show."""
    timeline: timelinemod.Timeline
    rigging: blocksmod.Rigging
    sources: dict[tuple[str, str], Any]
    palettes: dict[str, Palette]
    default_palette: Optional[Palette]
    envs: list[blocksmod.Env]
    problems: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.show = statemod.Show(
            base=[self._base_layer],
            color=[self._slot_layer("color")],
            movement=[self._slot_layer("movement")],
            fx=[self._slot_layer("level"), self._hits_layer, self._master_layer])
        self._beat = 0.0
        self._fallback = statemod.Show()
        self._palette: Palette = {r: WHITE for r in ROLES}
        self._hits: list[tuple[str, float, frozenset[int]]] = []
        self._masters: dict[int, float] = {}
        self._master: Optional[float] = None
        self._rest_aims: dict[int, Optional[geo.Aim]] = {}
        self._all = frozenset(f.fid for f in self.rigging.rig.fixtures)
        # Slots the operator has taken (F19i): the fallback show -- the
        # operator's own selection -- runs there instead of the timeline.
        self.grabbed: frozenset[str] = frozenset()
        # Rows for the other outputs (milestone 3), this frame: (where, frame)
        # -- "" for the timeline's own, "row/item#pass" for a routine clip's.
        self.external: tuple[tuple[str, timelinemod.ExternalFrame], ...] = ()
        self._has_external = bool(self.timeline.external_rows) or any(
            isinstance(src, RoutineSource) and src.inst.timeline.external_rows
            for src in self.sources.values())

    # -- each frame --------------------------------------------------------

    def begin(self, beat: float, prev: Optional[float] = None, jumped: bool = False,
              fallback: Optional[statemod.Show] = None,
              base_palette: Optional[Mapping[str, tuple]] = None) -> None:
        """Set this frame's position. `beat` is the TRACK's beat (its grid,
        beat 0 the first downbeat); `prev` and `jumped` let a hit too short for
        a frame fire once in forward play; `fallback` is what shows wherever
        the timeline says nothing."""
        self._beat = beat
        self._fallback = fallback if fallback is not None else statemod.Show()
        base = {r: tuple(base_palette.get(r, WHITE)) for r in ROLES} \
            if base_palette else {r: WHITE for r in ROLES}
        default = self.default_palette or base
        self._palette = self._resolve_palette(
            self.timeline.entries("palette", beat), 0, default)
        for env in self.envs:
            env.palette = self._palette
            env.automate = self._param_automation
        self._collect(beat, prev, jumped)
        self._master = self.timeline.automation("master", beat)

    def _param_automation(self, name: str) -> Any:
        entry = self.timeline.curves.get(showfiles.PARAM_PREFIX + name)
        return blocksmod.automation_value(entry[1], self._beat) if entry else None

    def _resolve_palette(self, entries, i: int, default: Palette) -> Palette:
        if i >= len(entries) or entries[i] is timelinemod.BLANK:
            return default
        e = entries[i]
        top = self.palettes.get(e.clip.item.data.get("palette"), default)
        if e.clip.weight >= 1.0:
            return top
        if e.under is not None:
            bottom = self.palettes.get(e.under.item.data.get("palette"), default)
        elif e.exclusive:
            bottom = default
        else:
            bottom = self._resolve_palette(entries, i + 1, default)
        return {r: blocksmod.mix(bottom[r], top[r], e.clip.weight) for r in ROLES}

    def _visible_routines(self, beat: float):
        """Routine clips showing on some lane -- whose hits and master apply."""
        seen: dict[tuple[str, str], tuple[RoutineSource, timelinemod.Clip]] = {}
        for slot in SLOTS:
            for layer in self.timeline.channel(slot, beat).layers:
                if not isinstance(layer, timelinemod.Clip):
                    continue
                key = (layer.row, layer.item.id)
                src = self.sources.get(key)
                if isinstance(src, RoutineSource) and (
                        key not in seen or seen[key][1].weight < layer.weight):
                    seen[key] = (src, layer)
        return list(seen.values())

    def _collect(self, beat: float, prev: Optional[float], jumped: bool) -> None:
        external: list = []
        if self._has_external:
            external.extend(("", e) for e in self.timeline.external_at(beat, prev,
                                                                        jumped))
        hits: list[tuple[str, float, frozenset[int]]] = []
        for hit in self.timeline.hits(beat, prev, jumped):
            role = hit.item.data.get("role")
            fids = (frozenset(f.fid for f in self.rigging.tagged(role))
                    if role else self._all)
            hits.append((hit.item.data.get("hit"), hit.level, fids))
        masters: dict[int, float] = {}
        for src, clip in self._visible_routines(beat):
            inst = src.inst
            sched = inst.sched(clip.local)
            prev_sched = None
            again = jumped
            if prev is not None:
                prev_sched = inst.sched(prev - clip.item.at)
                again = again or prev_sched > sched
            for hit in inst.timeline.hits(sched, prev_sched, again):
                fids = inst.hit_fixtures.get((hit.row, hit.item.id), frozenset())
                hits.append((hit.item.data.get("hit"), hit.level * clip.weight, fids))
            master = inst.curve("master")
            if master is not None:
                factor = 1.0 - clip.weight + clip.weight * master.value(sched)
                for fid in inst.fixtures:
                    masters[fid] = masters.get(fid, 1.0) * factor
            if self._has_external and inst.timeline.external_rows:
                # Each pass of a looping routine is its own: its cues fire again.
                n = (int(max(clip.local, 0.0) // inst.length)
                     if inst.loop and inst.length > 0 else 0)
                where = f"{clip.row}/{clip.item.id}#{n}"
                external.extend((where, e) for e in inst.timeline.external_at(
                    sched, prev_sched, again))
        self._hits = hits
        self._masters = masters
        self.external = tuple(external)

    # -- the layers --------------------------------------------------------

    def _base_layer(self, ctx, out) -> None:
        for layer in self._fallback.base:
            layer(ctx, out)

    def _slot_layer(self, slot: str) -> statemod.Layer:
        def layer(ctx, out):
            fields = self._timeline_fields(ctx, slot)
            entries = self.timeline.entries(slot, self._beat)
            with ctx.scoped(**fields):
                if not entries or slot in self.grabbed:
                    # Nothing on any lane: the fallback has the slot to itself,
                    # run in place, exactly as it would run on its own.
                    for fallback in getattr(self._fallback, FALLBACK_LIST[slot]):
                        fallback(ctx, out)
                    return
                cache: dict = {}
                rest_cache: dict = {}
                result = {}
                for f in ctx.rig.fixtures:
                    below = _Memo(lambda f=f: self._rest(ctx, slot, f, out, rest_cache))

                    def content(ctx, slot, f, clip, incoming, fb, cache=cache):
                        src = self.sources.get((clip.row, clip.item.id))
                        if src is None:
                            return fb()
                        return src.content(self, ctx, slot, f, clip, incoming, fb, cache)

                    result[f.fid] = self._resolve(ctx, slot, f, entries, 0, out,
                                                  below, content)
                out.update(result)
        return layer

    def _timeline_fields(self, ctx, slot: str) -> dict:
        """The timeline's own size, spread and centre lanes: they scale the
        movement slot as a whole, fallback included, on top of the
        operator's."""
        if slot != "movement":
            return {}
        beat, out = self._beat, {}
        size = self.timeline.automation("size", beat)
        if size is not None:
            out["move_size"] = ctx.move_size * size
        spread = self.timeline.automation("spread", beat)
        if spread is not None:
            out["move_spread"] = ctx.move_spread + spread
        bearing = self.timeline.automation("center.bearing", beat)
        elev = self.timeline.automation("center.elevation", beat)
        if bearing is not None or elev is not None:
            cb, ce = ctx.move_center
            out["move_center"] = (cb + (bearing or 0.0), ce + (elev or 0.0))
        return out

    def _resolve(self, ctx, slot, f, entries, i, incoming, below, content):
        """One fixture's state for one slot, walking `entries` from `i`."""
        if i >= len(entries):
            return below()
        e = entries[i]
        if e is timelinemod.BLANK:
            return self._blank(ctx, slot, f, incoming)
        if e.exclusive:
            rest_of = _Memo(lambda: self._blank(ctx, slot, f, incoming))
        else:
            rest_of = _Memo(lambda: self._resolve(ctx, slot, f, entries, i + 1,
                                                  incoming, below, content))
        top = content(ctx, slot, f, e.clip, incoming, rest_of)
        weight = e.clip.weight
        if weight >= 1.0:
            return top
        bottom = (content(ctx, slot, f, e.under, incoming, rest_of)
                  if e.under is not None else rest_of())
        return _blend1(bottom, top, weight)

    def _run(self, ctx, block: blocksmod.Block, slot: str, phase: float,
             incoming, fields: dict) -> dict:
        """A block's layers over a scratch copy of the states, on its own
        phase. Only its claimed fixtures are ever read back out."""
        scratch = {fid: replace(st) for fid, st in incoming.items()}
        with ctx.scoped(**{PHASE_FIELD[slot]: phase}, **fields):
            for layer in block.layers:
                layer(ctx, scratch)
        return scratch

    def _rest(self, ctx, slot, f, incoming, cache) -> statemod.FixtureState:
        """Under every lane: the fallback show's own layers for this slot."""
        states = cache.get(slot)
        if states is None:
            states = {fid: replace(st) for fid, st in incoming.items()}
            for layer in getattr(self._fallback, FALLBACK_LIST[slot]):
                layer(ctx, states)
            cache[slot] = states
        return states[f.fid]

    def _blank(self, ctx, slot, f, incoming) -> statemod.FixtureState:
        """Nothing drives this slot here: the rest state (decided with the
        user, F19h) -- movers on the rest point, color white, level dark."""
        st = replace(incoming[f.fid])
        if slot == "movement":
            aim = self._rest_aim(ctx, f)
            if aim is not None:
                st.aim = aim
        elif slot == "color":
            st.color, st.white = WHITE, 0.0
        else:
            st.intensity, st.strobe = 0.0, 0.0
        return st

    def _rest_aim(self, ctx, f) -> Optional[geo.Aim]:
        if f.fid not in self._rest_aims:
            aim = None
            geometry, venue = ctx.geometry, ctx.venue
            if f.head is not None and geometry is not None:
                if venue is not None:
                    aim = geometry.aim_at_point(f.head, *venue.rest)
                else:
                    aim = geometry.aim_at_ball(f.head)
            self._rest_aims[f.fid] = aim
        return self._rest_aims[f.fid]

    def _hits_layer(self, ctx, out) -> None:
        later = []
        for kind, level, fids in self._hits:
            if kind == "blackout":
                later.append((level, fids))
                continue
            for fid in fids:
                st = out.get(fid)
                if st is None:
                    continue
                if kind == "flash":
                    st.intensity = max(st.intensity, level)
                elif kind == "strobe":
                    st.strobe = max(st.strobe, level)
        for level, fids in later:
            for fid in fids:
                if fid in out:
                    out[fid].intensity *= max(0.0, 1.0 - level)

    def _master_layer(self, ctx, out) -> None:
        for fid, factor in self._masters.items():
            if fid in out:
                out[fid].intensity *= factor
        if self._master is not None:
            for st in out.values():
                st.intensity *= self._master

    # -- reading -----------------------------------------------------------

    def explain(self, ctx, beat: float, fallback: Optional[statemod.Show] = None
                ) -> dict:
        """Every fixture at `beat`, safety applied, as plain data -- for a
        person checking a timeline against a rig, and later the designer."""
        self.begin(beat, fallback=fallback)
        states = statemod.evaluate(ctx, self.show)
        out = {}
        for f in ctx.rig.fixtures:
            st = states[f.fid]
            entry = {"intensity": round(st.intensity, 3),
                     "color": "#%02x%02x%02x" % tuple(
                         round(max(0.0, min(1.0, c)) * 255) for c in st.color)}
            if st.aim is not None:
                entry["aim"] = [round(st.aim.bearing_delta, 1),
                                round(st.aim.elev_deg, 1)]
            if st.strobe:
                entry["strobe"] = round(st.strobe, 3)
            out[f.name] = entry
        return {"beat": beat, "fixtures": out,
                "palette": {r: "#%02x%02x%02x" % tuple(round(c * 255) for c in v)
                            for r, v in self._palette.items()}}


def _blend1(a: statemod.FixtureState, b: statemod.FixtureState,
            t: float) -> statemod.FixtureState:
    return statemod.blend({0: a}, {0: b}, t)[0]


# -- compiling ----------------------------------------------------------------

def _warps(timeline: timelinemod.Timeline, item: timelinemod.Item,
           inst: Optional[routinesmod.Instance]) -> dict[str, Warp]:
    out = {}
    for slot in SLOTS:
        t_entry = timeline.curves.get(f"rate.{slot}")
        r_curve = inst.curve(f"rate.{slot}") if inst is not None else None
        out[slot] = Warp(item.at, t_entry[1] if t_entry else None, r_curve,
                         inst.length if inst is not None and inst.loop else None,
                         horizon=item.len + 64.0)
    return out


def compile(timeline: timelinemod.Timeline, routines: Mapping[str, Mapping],
            rigging: blocksmod.Rigging, where: str = "timeline") -> Program:
    """Build every clip of a timeline for one rig. Never raises for a problem
    in the show: an unknown look, a routine that is missing, a block with a
    bad argument -- each is listed in `problems` and compiles to nothing, so
    the lanes underneath show through and the show runs on. Slow-ish (it
    instantiates every routine): run it on the worker."""
    problems: list[str] = []
    env = blocksmod.Env(look_colors=rigging.look_colors())
    envs = [env]
    sources: dict[tuple[str, str], Any] = {}
    for row in timeline.clip_rows:
        slots = [ch for ch in row.channels if ch in SLOTS]
        for item in row.items:
            at = f"{where} row {row.id!r} item {item.id!r}"
            kind = item.data.get("kind")
            if kind == "routine":
                name = item.data.get("routine")
                doc = routines.get(name)
                if doc is None:
                    problems.append(f"{at}: routine {name!r} is not in routines/")
                    continue
                inst = routinesmod.instantiate(doc, item.data, rigging, at)
                problems += inst.problems
                envs.append(inst.env)
                sources[(row.id, item.id)] = RoutineSource(
                    inst, _warps(timeline, item, inst))
            elif kind == "look":
                entry = rigging.entries.get(item.data.get("look"))
                if entry is None:
                    problems.append(f"{at}: look {item.data.get('look')!r} is not "
                                    f"in this rig's library")
                    continue
                groups = item.data.get("groups")
                narrow = (tuple(f for g in groups for f in rigging.tagged(g))
                          if groups else None)
                built = {s: blocksmod.look_block(entry, s, narrow, rigging)
                         for s in slots}
                if not any(b.claims for b in built.values()):
                    problems.append(f"{at}: look {entry.name!r} has nothing for "
                                    f"the {row.channels[0]} lane")
                sources[(row.id, item.id)] = LeafSource(built,
                                                        _warps(timeline, item, None))
            elif kind == "snapshot":
                built = {s: blocksmod.snapshot_block(item.data, s, None, rigging, at)
                         for s in slots}
                for b in built.values():
                    problems += b.problems
                sources[(row.id, item.id)] = LeafSource(built,
                                                        _warps(timeline, item, None))
            # palette clips are read straight off the palette lane
    # A `hold` wave's integral is a running sum of its levels, memoised as it
    # is first asked for (`waves.area`). A rate lane is integrated every
    # frame, so take that first sum here, on the worker, out to the end of the
    # last clip -- not on the output thread on the first frame after a seek.
    end = max((i.at + i.len for row in timeline.clip_rows for i in row.items),
              default=0.0)
    for target, (_, curve) in timeline.curves.items():
        if target.startswith("rate.") and curve.wave is not None and curve.numeric:
            curve.integral(end)
    palettes: dict[str, Palette] = {}
    for name, pal in (timeline.meta.get("palettes") or {}).items():
        if isinstance(pal, Mapping):
            palettes[name] = {r: env.color(pal.get(r)) for r in ROLES}
    default = palettes.get(timeline.meta.get("palette"))
    return Program(timeline, rigging, sources, palettes, default, envs,
                   list(dict.fromkeys(problems)))


# -- loading a rig ------------------------------------------------------------

def load_rigging(event_dir: Path) -> blocksmod.Rigging:
    """An event's rig, look library and presets, as the compiler wants them."""
    event_dir = Path(event_dir)
    rig = rigmod.load_rig(event_dir)
    looks = event_dir / "looks.json"
    entries = libmod.load_entries(looks) if looks.exists() else []
    presets: list = []
    path = event_dir / "presets.json"
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
        presets = data.get("presets", []) if isinstance(data, dict) else data
    return blocksmod.Rigging(rig, {e.name: e for e in entries}, presets,
                             rig.name)


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m engine.program",
        description="Compile a show folder's timelines against a rig: list what "
                    "will not work there, and show what the fixtures do at a "
                    "beat.")
    parser.add_argument("--event", required=True, help="the event directory")
    parser.add_argument("--show-dir", help="defaults to the configured show "
                                           "folder")
    parser.add_argument("--track", help="only this track's timeline")
    parser.add_argument("--beat", type=float,
                        help="with --track: print every fixture at this beat")
    args = parser.parse_args(argv)
    root = showfiles.resolve_show_dir(args.show_dir)
    if root is None:
        print("no show folder: pass --show-dir", file=sys.stderr)
        return 2
    rigging = load_rigging(Path(args.event))
    folder = showfiles.load_folder(root)
    status = 0
    for track_id, doc in sorted(folder.timelines.items()):
        if args.track and track_id != args.track:
            continue
        timeline = timelinemod.Timeline.from_doc(doc, showfiles.timeline_channels)
        program = compile(timeline, folder.routines, rigging,
                          f"timelines/{track_id}.json")
        print(f"{track_id}: {len(program.sources)} clips on {rigging.event}"
              f"{', ' + str(len(program.problems)) + ' problem(s)' if program.problems else ''}")
        for p in program.problems:
            print(f"  {p}")
        status = status or (1 if program.problems else 0)
        if args.beat is not None and args.track:
            ctx = statemod.EvalContext(rig=rigging.rig, venue=rigging.rig.venue)
            fallback = libmod.compose(None, [], [], WHITE)
            print(json.dumps(program.explain(ctx, args.beat, fallback), indent=2))
    return status


if __name__ == "__main__":
    raise SystemExit(main())
