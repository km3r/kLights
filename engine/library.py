"""
Load a ported look library into runnable looks.

`looks.json` is produced by `shared/tools/port_library.py` from the QLC+
workspace. This turns each entry into an `auto.Look` -- a factory that takes the
current palette colour and returns a layer stack.

The kinds map onto the engine's layers rather than onto QLC+'s flat namespace,
and each occupies exactly one of three independent SLOTS:

  slot        kinds                    what it sets
  ----------------------------------------------------------------------------
  movement    pose, path, mixed        where the heads point
  color       color, color_path        what colour everything is
  level       intensity, level_path    a brightness MULTIPLIER over the above

**The three slots are filled independently.** Picking a colour does not disturb
the movement, and picking a movement does not disturb the colour -- which is the
entire point of having split the scenes during the port, and was not true while
selecting any look replaced the whole show. A `mixed` entry fills the movement
and colour slots together, because it genuinely states both; either can then be
changed without losing the other.

**The level slot MULTIPLIES.** It is never a base layer, so a level chase dims
whatever colour and position are running rather than replacing them, and it
composes with the master and with the safety taper instead of fighting them. A
level look that replaced the base would blank the colour the moment it was
selected -- which is what "we lost the actual dimming" was describing.

Offsets are relative to each head's calibrated ball aim, so every ported look
tracks recalibration automatically. That is a property the stored DMX could not
have had, and it is why re-aiming a nudged head no longer invalidates the
library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

from . import auto as autom
from . import geometry as geo
from . import motion
from . import state as statemod

EASINGS = {"linear": motion.linear, "ease_in_out": motion.ease_in_out,
           "ease_out": motion.ease_out}


@dataclass(frozen=True)
class LibraryEntry:
    """One ported look, as data. Kept separate from the runnable `auto.Look` so
    the UI can list and group the library without building every layer stack."""
    name: str
    kind: str
    tags: tuple[str, ...]
    # The rig groups this look writes ("corner movers", "pinspots"). Layers are
    # scoped to these: without it a pinspot-only scene ported as an untagged
    # uniform colour and repainted the movers as well. It is also the axis the
    # UI filters on, because a pinspot palette and a mover palette are two
    # different decisions and were sharing one list.
    groups: tuple[str, ...] = ()
    # Which head each column of `offsets` / `steps` was authored for, in order.
    #
    # Without this those arrays are positional, so they bind to whatever head
    # happens to sit at that index. Re-hang the rig with the heads in a
    # different order, or add a fifth, and every look silently re-points --
    # silently because each head still moves smoothly to a position that was
    # authored, just not the one authored for it. Naming the heads makes the
    # binding survive a re-patch, and makes a look that no longer fits say so.
    #
    # Optional, and absent from everything ported before it existed. When it is
    # absent the arrays stay positional, which is what those looks were authored
    # against and therefore still correct for them.
    fixtures: Optional[tuple[str, ...]] = None
    offsets: Optional[list[list[float]]] = None
    steps: Optional[list[list[list[float]]]] = None
    # A CUED movement chase: per step, (fade ms, hold ms) from the source, and
    # per step the dimmer each fixture holds there. Present only where the
    # source chase alternates dark travel with lit holds -- the Dark Moves
    # family -- because there the timing IS the look and interpolating it away
    # turns a teleport into an ordinary sweep. See `motion.cue_path`.
    step_spans: Optional[list[list[float]]] = None
    step_levels: Optional[list[dict[str, float]]] = None
    frames: Optional[list[dict[str, list[float]]]] = None
    # A level chase: per step, {fixture: multiplier}, and optionally the shutter
    # alongside it (the Breathe pair chases the shutter, not the dimmer).
    levels: Optional[list[dict[str, float]]] = None
    strobe_steps: Optional[list[dict[str, float]]] = None
    strobes: Optional[dict[str, float]] = None
    # Set when this entry is one step of a chase that also ported. Reachable,
    # but filed under the parent rather than listed beside it -- four
    # "Spotlight Step N" entries next to "Spotlight" is the flat-list problem.
    step_of: Optional[str] = None
    color: Optional[list[float]] = None
    colors: Optional[dict[str, list[float]]] = None
    # Per fixture 0..1. The pinspots are RGBW and several looks blend real white
    # into the colour -- dropping it made them cooler and dimmer than authored.
    whites: Optional[dict[str, float]] = None
    bars: Optional[float] = None
    intensity: Optional[float] = None
    # Per fixture, present only where the scene dimmed fixtures differently.
    intensities: Optional[dict[str, float]] = None
    source: str = ""

    @property
    def is_movement(self) -> bool:
        return self.kind in ("pose", "path", "mixed")

    @property
    def is_cued(self) -> bool:
        """A movement chase that carries its own dimmer and its own timing.

        Still a MOVEMENT entry, and it still fills only the movement slot: the
        darkness belongs to the move, it is not a level look the operator
        chose. Putting it in the level slot instead would evict whatever level
        chase is running the moment a dark move is selected, and give it back
        when one is picked -- silently breaking the routine.
        """
        return bool(self.step_spans and self.steps)

    @property
    def is_color(self) -> bool:
        return self.kind in ("color", "color_path", "mixed")

    @property
    def is_level(self) -> bool:
        return self.kind in ("intensity", "level_path")

    @property
    def slot(self) -> str:
        """Which of the three slots this entry fills.

        `mixed` lands in movement and is ALSO applied to colour when selected --
        see `ShowController`. One entry, two slots, because it really does state
        both; the slots stay independently changeable afterwards.
        """
        if self.is_level:
            return "level"
        if self.is_movement:
            return "movement"
        return "color"


def load_entries(path: Path) -> list[LibraryEntry]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for raw in data.get("looks", []):
        out.append(LibraryEntry(
            name=raw["name"], kind=raw["kind"], tags=tuple(raw.get("tags", [])),
            groups=tuple(raw.get("groups", [])),
            fixtures=(tuple(raw["fixtures"]) if raw.get("fixtures") else None),
            offsets=raw.get("offsets"), steps=raw.get("steps"),
            step_spans=raw.get("step_spans"), step_levels=raw.get("step_levels"),
            frames=raw.get("frames"), levels=raw.get("levels"),
            strobe_steps=raw.get("strobe_steps"), strobes=raw.get("strobes"),
            step_of=raw.get("step_of"),
            color=raw.get("color"),
            colors=raw.get("colors"), whites=raw.get("whites"),
            bars=raw.get("bars"), intensity=raw.get("intensity"),
            intensities=raw.get("intensities"), source=raw.get("source", "")))
    return out


# ------------------------------------------------------------------ layers --

def column_for(fixtures: Optional[tuple[str, ...]], width: int):
    """Which column of a per-head array a given rig head should read.

    Returns `pick(ctx, head) -> int`.

    Two behaviours, and which one applies is a property of the look:

      * **No `fixtures`** -- positional, `head % width`. Every look ported from
        QLC+ is this, and it is right for them: they were authored against a
        rig whose head order is the order they were written down in. Wrapping
        rather than failing is deliberate, so an 8-head club rig can run a
        4-head look instead of refusing to.

      * **With `fixtures`** -- by name. The head's own name is looked up in the
        authored list, so re-ordering the patch, or inserting a head, moves the
        offsets with the fixture instead of leaving them behind. A head the look
        does not name falls back to positional, which is what lets a 4-head look
        still fill an 8-head rig.

    The resolution is cached per rig, because it is a name lookup on every head
    on every frame otherwise, and the answer only changes when the patch does.
    """
    if not fixtures:
        def pick_positional(ctx, head: int) -> int:
            return head % width
        return pick_positional

    index_of = {name: i for i, name in enumerate(fixtures)}
    # Keyed by the head NAMES, not by id(geometry). CPython reuses an id once
    # the object behind it is collected, so a rig reload -- which frees the old
    # geometry and builds a new one -- can land the replacement at the same
    # address and get the previous rig's mapping back. Harmless while there is
    # exactly one geometry per process, which is why it survived review; a
    # silently mis-pointed show the moment reloading exists. The names are what
    # the mapping actually depends on, so they are the honest key.
    cache: dict[tuple[str, ...], list[int]] = {}

    def pick_named(ctx, head: int) -> int:
        geometry = ctx.geometry
        if geometry is None:
            return head % width
        key = tuple(h.name for h in geometry.heads)
        mapping = cache.get(key)
        if mapping is None:
            mapping = [index_of.get(name, i % width)
                       for i, name in enumerate(key)]
            cache[key] = mapping
        return mapping[head] if head < len(mapping) else head % width
    return pick_named


def pose_offsets(offsets: list[list[float]],
                 fixtures: Optional[tuple[str, ...]] = None):
    """A held position: each head sits at its own stored offset."""
    pick = column_for(fixtures, len(offsets))

    def offset_for(ctx, head: int) -> tuple[float, float]:
        pair = offsets[pick(ctx, head)]
        return (pair[0], pair[1])
    return offset_for


def path_offsets(steps: list[list[list[float]]], bars: float,
                 easing=motion.ease_in_out,
                 fixtures: Optional[tuple[str, ...]] = None):
    """A route: per head, interpolate through that head's column of the steps.

    The transpose matters. The port stores steps as [step][head] because that is
    how a chaser reads; `motion.path` wants one head's whole route, so each head
    gets its own path built from its own column.
    """
    per_head = [motion.path([tuple(step[h % len(step)]) for step in steps],
                            easing=easing)
                for h in range(len(steps[0]))]
    pick = column_for(fixtures, len(per_head))
    count = len(per_head)

    def offset_for(ctx, head: int) -> tuple[float, float]:
        column = pick(ctx, head)
        # SPREAD lags each head along its own route. `motion.phase` takes its
        # offset in cycles already, so spreading n heads evenly is spread * i/n
        # regardless of how many bars the cycle is -- the idiom every multi-head
        # move in the old library re-derived by hand for each chase length.
        # 0 is unison, which is what every ported look was authored as.
        return per_head[column](motion.phase(
            ctx.motion_bar, bars, offset=ctx.move_spread * column / count))
    return offset_for


def cue_offsets(steps: list[list[list[float]]], spans: list[list[float]],
                bars: float, easing=motion.ease_in_out,
                fixtures: Optional[tuple[str, ...]] = None):
    """A route whose steps keep their own travel and hold times.

    Same transpose as `path_offsets` -- the port stores [step][head] because
    that is how a chaser reads -- but each head walks the cue list rather than a
    cycle divided into equal segments.
    """
    pairs = [(float(f), float(h)) for f, h in spans]
    per_head = [motion.cue_path([tuple(step[h % len(step)]) for step in steps],
                                pairs, easing=easing)
                for h in range(len(steps[0]))]
    pick = column_for(fixtures, len(per_head))
    count = len(per_head)

    def offset_for(ctx, head: int) -> tuple[float, float]:
        column = pick(ctx, head)
        # Spread applies here too, but note what it does to a CUED chase: these
        # travel dark and light on arrival, so spreading them staggers the
        # arrivals rather than smearing a continuous move. That is a real
        # effect and not a bug, but it is a different one.
        return per_head[column](motion.phase(
            ctx.motion_bar, bars, offset=ctx.move_spread * column / count))
    return offset_for


def cue_level_layer(step_levels: list[dict[str, float]],
                    spans: list[list[float]], bars: float,
                    groups: Sequence[str] = ()):
    """The dimmer half of a cued chase: dark to travel, lit on arrival.

    This is the half the port dropped. The source scenes wrote Pan/Tilt on every
    step and the Dimmer only on the arrival steps; the porter read position and
    nothing else, so every one of these routines came through as a lit sweep
    through the same poses -- the one thing they were each written NOT to be.

    A fixture the step does not name is DARK for that step, not left alone,
    exactly as in `level_frames_layer`: the source console merged Intensity HTP,
    so an unwritten dimmer contributed nothing. Here that omission is the entire
    effect rather than an accident of it -- and it is passed to `cue_value` as
    None rather than 0, because an unwritten channel is RELEASED at the step
    boundary while a written one fades. A head that dimmed out gradually across
    its travel would be a beam you watch swing away, which is the opposite of
    what these routines do.

    A MULTIPLIER, like every other level layer, so a dark move still obeys the
    master and the safety taper, and so a level chase selected on top of it
    composes instead of fighting.
    """
    scope = set(groups)
    pairs = [(float(f), float(h)) for f, h in spans]

    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        p = motion.phase(ctx.motion_bar, bars)
        for fixture in ctx.rig.fixtures:
            if not (scope & set(fixture.tags)):
                continue
            values = [step.get(fixture.name) for step in step_levels]
            out[fixture.fid].intensity *= motion.cue_value(values, pairs, p)
    return layer


def color_frames_layer(frames: list[dict[str, list[float]]], bars: float):
    """A stepped colour sequence, held per step rather than interpolated.

    A frame value is [r, g, b], or [r, g, b, w] where the fixture is RGBW and
    the step blends real white -- "Pin Drift" walks the same warm pastels the
    Pin scenes hold, and reading only three components made every step of it
    colder than authored.
    """
    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        index = int(motion.phase(ctx.motion_bar, bars) * len(frames)) % len(frames)
        for fixture in ctx.rig.fixtures:
            rgb = frames[index].get(fixture.name)
            if rgb is not None:
                out[fixture.fid].color = (rgb[0], rgb[1], rgb[2])
                out[fixture.fid].white = rgb[3] if len(rgb) > 3 else 0.0
    return layer


def per_fixture_color_layer(colors: dict[str, list[float]],
                            whites: Optional[dict[str, float]] = None):
    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        for fixture in ctx.rig.fixtures:
            rgb = colors.get(fixture.name)
            if rgb is not None:
                out[fixture.fid].color = (rgb[0], rgb[1], rgb[2])
            if whites is not None and fixture.name in whites:
                out[fixture.fid].white = whites[fixture.name]
    return layer


def white_layer(whites: dict[str, float]):
    """The W of an RGBW fixture, where the look sets one.

    Separate from the colour layer because a uniform-colour look can still have
    per-fixture white -- "Pin Ball Glow" gives both pinspots the same RGB and
    the same W, but the W is per fixture in the source and only the pinspots
    have the channel at all.
    """
    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        for fixture in ctx.rig.fixtures:
            if fixture.name in whites:
                out[fixture.fid].white = whites[fixture.name]
    return layer


def per_fixture_intensity_layer(levels: dict[str, float],
                                groups: Sequence[str] = ()):
    """Scale intensity per fixture, where a look dims them differently.

    A fixture INSIDE the look's own groups that the look does not name is taken
    to be OFF. That is what the source means: QLC+ merged Intensity as HTP, so
    an unwritten dimmer contributed nothing and the fixture went dark. Reading
    "unnamed" as "leave alone" instead made three of the five ported level
    chases completely inert -- "Dim Chase" writes three heads at full and omits
    the fourth, which is the entire chase.

    A fixture OUTSIDE the groups is genuinely untouched, so a mover chase does
    not blank the pinspots.
    """
    scope = set(groups)

    def level_for(ctx, fixture) -> float:
        if fixture.name in levels:
            return levels[fixture.name]
        if scope & set(fixture.tags):
            return 0.0
        return 1.0
    return statemod.intensity_layer(level_for)


def level_frames_layer(levels: list[dict[str, float]],
                       strobes: Optional[list[dict[str, float]]],
                       bars: float, groups: Sequence[str] = ()):
    """A stepped level chase, applied as a MULTIPLIER.

    Stepped rather than faded because the source is a step list -- inventing a
    ramp between two dimmer values asserts a shape the original never had.

    Multiplying is the whole point. "Spotlight" puts one head at full and its
    neighbours at 43%; as a base layer that would blank whatever colour was
    running and ignore the master, which is what made the level looks read as
    broken. As a multiplier it dims the picture that is already there.

    A fixture inside the chase's own groups that a step does not name is OFF for
    that step, not left alone. That is what the source means -- QLC+ merged
    Intensity as HTP, so an unwritten dimmer contributed nothing -- and it is
    the whole shape of these chases: "Dim Chase" writes three heads at full and
    omits the fourth, "Crowd Cascade" writes two of four. Reading the omission
    as "leave alone" made every one of them a no-op that changed nothing.

    Fixtures outside the groups are untouched, so a mover chase leaves the
    pinspots to whatever else is driving them.
    """
    scope = set(groups)
    empty: dict[str, float] = {}

    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        index = int(motion.phase(ctx.motion_bar, bars) * len(levels)) % len(levels)
        frame = levels[index]
        strobe = (strobes or [empty] * len(levels))[index]
        for fixture in ctx.rig.fixtures:
            if fixture.name in frame:
                out[fixture.fid].intensity *= frame[fixture.name]
            elif scope & set(fixture.tags):
                out[fixture.fid].intensity = 0.0
            if fixture.name in strobe:
                out[fixture.fid].strobe = strobe[fixture.name]
    return layer


def strobe_layer(strobes: dict[str, float]):
    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        for fixture in ctx.rig.fixtures:
            if fixture.name in strobes:
                out[fixture.fid].strobe = strobes[fixture.name]
    return layer


# ------------------------------------------------------------------- slots --

DEFAULT_BARS = 8.0


def base_layers(show: statemod.Show) -> None:
    """Point everything at the ball and open it up.

    Always present, whatever is selected, so that a colour with no movement --
    or nothing at all -- still produces a picture instead of leaving the heads
    wherever the last look happened to stop.

    Full brightness, deliberately. Everything that dims lives downstream: the
    level slot, the master, and the safety taper. A pinspot has no dimmer
    channel, so `render` scales its RGB by this level -- which means a colour
    look's authored bytes ARE its brightness, and seeding anything below 1.0
    here would silently scale every ported colour.
    """
    show.base.append(statemod.pose_layer(
        lambda ctx, head: ctx.geometry.aim_at_ball(head), tags=("movers",),
        intensity=1.0))
    show.base.append(statemod.on_layer(1.0, tags=("pinspots",)))


def movement_layers(show: statemod.Show, entry: Optional[LibraryEntry]) -> None:
    """One movement look. A cued chase also contributes its own dimmer.

    That level layer goes in `movement`, not `fx`, so it lands before the level
    slot and the master: a dark move dims the picture the look established, and
    anything the operator selects afterwards still multiplies on top of it.
    """
    if entry is None:
        return
    if entry.offsets is not None:
        show.movement.append(statemod.move_layer(
            pose_offsets(entry.offsets, entry.fixtures), tags=("movers",)))
    elif entry.is_cued:
        bars = entry.bars or DEFAULT_BARS
        show.movement.append(statemod.move_layer(
            cue_offsets(entry.steps, entry.step_spans, bars,
                        fixtures=entry.fixtures), tags=("movers",)))
        if entry.step_levels:
            show.movement.append(cue_level_layer(
                entry.step_levels, entry.step_spans, bars, entry.groups))
    elif entry.steps is not None:
        show.movement.append(statemod.move_layer(
            path_offsets(entry.steps, entry.bars or DEFAULT_BARS,
                         fixtures=entry.fixtures),
            tags=("movers",)))


def color_layers(show: statemod.Show, entry: LibraryEntry) -> None:
    """One colour look, scoped to the fixtures it actually writes.

    The scoping is the fix for a real defect: "Pin Ball Glow" writes two
    pinspots in the workspace, ported as a uniform colour with no tags, and so
    repainted all four movers amber as well. A look now only touches its own
    group, which is also what lets a pinspot colour and a mover colour be up at
    the same time.
    """
    tags = tuple(entry.groups) or None
    if entry.color is not None:
        show.color.append(statemod.color_layer(tuple(entry.color), tags=tags))
    elif entry.colors is not None:
        show.color.append(per_fixture_color_layer(entry.colors, entry.whites))
    elif entry.frames is not None:
        show.color.append(color_frames_layer(entry.frames,
                                             entry.bars or DEFAULT_BARS))
    # White rides after the colour layer, because `color_layer` resets it.
    if entry.whites and entry.colors is None:
        show.color.append(white_layer(entry.whites))


def level_layers(show: statemod.Show, entry: LibraryEntry) -> None:
    """One level look. Everything here goes in `fx`, and everything multiplies.

    `fx` rather than `base` is the fix for "we lost the actual dimming": a level
    look must scale the colour and position already established, and then be
    scaled itself by the master and the safety taper. Anything in `base` would
    instead wipe them.
    """
    tags = tuple(entry.groups) or None
    if entry.levels is not None:
        show.fx.append(level_frames_layer(entry.levels, entry.strobe_steps,
                                          entry.bars or DEFAULT_BARS,
                                          entry.groups))
        return
    if entry.intensities:
        show.fx.append(per_fixture_intensity_layer(entry.intensities,
                                                   entry.groups))
    elif entry.intensity is not None:
        level = entry.intensity
        show.fx.append(statemod.intensity_layer(
            lambda ctx, fixture: level, tags=tags))
    if entry.strobes:
        show.fx.append(strobe_layer(entry.strobes))


def compose(movement: Optional[LibraryEntry],
            colors: Sequence[LibraryEntry] = (),
            levels: Sequence[LibraryEntry] = (),
            palette_color: tuple[float, float, float] = (1.0, 1.0, 1.0)
            ) -> statemod.Show:
    """The slots, plus the base and the auto-mode effects, as one Show.

    `colors` and `levels` are LISTS because each slot is filled per fixture
    group: the pinspots can be on their own colour while the movers are on
    another, which is the whole point of splitting them. Each entry is scoped to
    its own group, so they cannot fight.

    The palette goes down first and unscoped, so any group with no colour look
    of its own still gets a colour rather than rendering whatever the last look
    left behind.
    """
    show = statemod.Show()
    base_layers(show)
    show.color.append(statemod.color_layer(palette_color))
    for entry in colors:
        color_layers(show, entry)
    movement_layers(show, movement)
    for entry in levels:
        level_layers(show, entry)
    show.fx.append(autom.energy_intensity_layer())
    show.fx.append(autom.energy_strobe_layer())
    return show


def build_look(entry: LibraryEntry) -> autom.Look:
    """One entry as a standalone Look, for auto mode's set list.

    Auto mode advances the MOVEMENT slot, so this is what a movement entry looks
    like on its own; the controller re-composes it with whatever colour and
    level are selected.
    """
    return autom.Look(
        name=entry.name,
        make=lambda color: compose(entry if entry.is_movement else None,
                                   [entry] if entry.is_color else [],
                                   [entry] if entry.is_level else [], color),
        # Levels and resets stay reachable by hand but must never be picked by a
        # timer -- the same reasoning as a blackout parked in the set list.
        manual_only=entry.is_level or "reset" in entry.name.lower()
        or entry.step_of is not None)


def load_setlist(path: Path) -> tuple[autom.SetList, list[LibraryEntry]]:
    entries = load_entries(path)
    if not entries:
        raise ValueError(f"{path} contains no looks")
    return autom.SetList([build_look(e) for e in entries]), entries


def encode_entry(rig_geo: geo.RigGeometry, entry: LibraryEntry,
                 head: int) -> Optional[tuple[int, int]]:
    """The DMX a pose entry produces for one head.

    Used by the round-trip test to prove a ported look still aims where the
    workspace aimed. Only meaningful for `pose`; a path has no single position.
    """
    if entry.offsets is None:
        return None
    d_bearing, d_elev = entry.offsets[head % len(entry.offsets)]
    return rig_geo.encode(head, rig_geo.aim_offset(head, d_bearing, d_elev))
