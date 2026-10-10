"""
Load a ported look library into runnable looks.

`looks.json` is produced by `shared/tools/port_library.py` from the QLC+
workspace. This turns each entry into an `auto.Look` -- a factory that takes the
current palette color and returns a layer stack.

The kinds map onto the engine's layers rather than onto QLC+'s flat namespace,
and each occupies exactly one of three independent SLOTS:

  slot        kinds                    what it sets
  ----------------------------------------------------------------------------
  movement    pose, path, mixed        where the heads point
  color       color, color_path        what color everything is
  level       intensity, level_path    a brightness MULTIPLIER over the above

**The three slots are filled independently.** Picking a color does not disturb
the movement, and picking a movement does not disturb the color -- which is the
entire point of having split the scenes during the port, and was not true while
selecting any look replaced the whole show. A `mixed` entry fills the movement
and color slots together, because it genuinely states both; either can then be
changed without losing the other.

**The level slot MULTIPLIES.** It is never a base layer, so a level chase dims
whatever color and position are running rather than replacing them, and it
composes with the master and with the safety taper instead of fighting them. A
level look that replaced the base would blank the color the moment it was
selected -- which is what "we lost the actual dimming" was describing.

Offsets are relative to each head's calibrated ball aim, so every ported look
tracks recalibration automatically. That is a property the stored DMX could not
have had, and it is why re-aiming a nudged head no longer invalidates the
library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Optional, Sequence

from . import auto as autom
from . import config as configmod
from . import geometry as geo
from . import motion
from . import state as statemod

EASINGS = {"linear": motion.linear, "ease_in_out": motion.ease_in_out,
           "ease_out": motion.ease_out}

# Which `kind` a parametric look gets. Derived from its block's slot rather
# than declared, so a look cannot claim a kind its block contradicts -- the file
# says `"block": "orbit"` and the taxonomy follows, which is what keeps
# `LookPicker`'s grouping and `LibraryEntry.slot` working unchanged.
KIND_FOR_SLOT = {"movement": "path", "color": "color_path",
                 "level": "level_path"}
# Except a block that holds still: an `offset` is a place, so it files under
# "Positions" with the poses it replaces rather than under "Moves" -- and a
# `solid` is a color and a `dim` a level, not chases of either. It matters most
# for a stored look remade as its block (`lookstore.block_version`): the new
# look belongs under the heading the original was found under.
KIND_FOR_BLOCK = {"offset": "pose", "solid": "color", "dim": "intensity",
                  "strobe": "intensity"}


@dataclass(frozen=True)
class LibraryEntry:
    """One ported look, as data. Kept separate from the runnable `auto.Look` so
    the UI can list and group the library without building every layer stack."""
    name: str
    kind: str
    tags: tuple[str, ...]
    # The rig groups this look writes ("corner movers", "pinspots"). Layers are
    # scoped to these: without it a pinspot-only scene ported as an untagged
    # uniform color and repainted the movers as well. It is also the axis the
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
    # into the color -- dropping it made them cooler and dimmer than authored.
    whites: Optional[dict[str, float]] = None
    bars: Optional[float] = None
    intensity: Optional[float] = None
    # Per fixture, present only where the scene dimmed fixtures differently.
    intensities: Optional[dict[str, float]] = None
    source: str = ""

    # ------------------------------------------------------------ parametric --
    # Set when this entry is a BLOCK rather than a table: one of the building
    # blocks in `blocks.py` -- the same ones a show folder's routines are made
    # of -- and the arguments to build it with. Everything above is data ported
    # out of QLC+; these two are what a look authored in this engine looks
    # like. One building-block system, used two ways: routines play blocks on a
    # timeline, and the console plays them as looks you can turn live.
    block: Optional[str] = None
    args: dict = field(default_factory=dict)
    # Hidden from the picker by name, from `parametric_looks.json`'s `retired`
    # list. A
    # retired entry is still loaded, still evaluable and still round-trips --
    # `looks.json` is a generated artifact whose parity proof depends on every
    # entry staying in it. This only says the operator has something better.
    retired: bool = False
    replaced_by: Optional[str] = None
    # Why it was hidden, from the `retired` list. Apart from `notes`, which is
    # what the look's own author said about the look: a hidden block look has
    # both, and saving it must not write one over the other.
    retired_note: str = ""
    notes: str = ""
    # Takes over the ported look of the same name -- see `merge`.
    supersedes: bool = False
    # Whether a superseding look still REPRODUCES the look whose name it took.
    # False once it has been changed away from it on purpose (Studio writes
    # that as it saves the change -- `lookstore.save`): it keeps the name, and
    # every cue that names it, but is no longer claimed, or tested, to be the
    # original.
    exact: bool = True

    @property
    def is_parametric(self) -> bool:
        return self.block is not None

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

        `mixed` lands in movement and is ALSO applied to color when selected --
        see `ShowController`. One entry, two slots, because it really does state
        both; the slots stay independently changeable afterwards.
        """
        if self.is_level:
            return "level"
        if self.is_movement:
            return "movement"
        return "color"


def load_entries(path: Path) -> list[LibraryEntry]:
    path = Path(path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        # Named, with the line: this file is 9000 lines, it is re-read while a
        # show runs (the engine watches it), and "Unterminated string" with no
        # file name was all a save refused on its account had to say.
        raise configmod.ConfigError(path, [
            f"is not valid JSON: {exc.msg} at line {exc.lineno}, column "
            f"{exc.colno}\n      fix: it is generated -- run "
            f"shared/tools/port_library.py again rather than mending it by "
            f"hand"]) from None
    if not isinstance(data, dict):
        raise configmod.ConfigError(path, ["the file must contain an object"])
    out = []
    for index, raw in enumerate(data.get("looks", [])):
        if not isinstance(raw, dict) or not isinstance(raw.get("name"), str) \
                or not isinstance(raw.get("kind"), str):
            raise configmod.ConfigError(path, [
                f"looks[{index}] needs a name and a kind"])
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
            intensities=raw.get("intensities"), source=raw.get("source", ""),
            # What the porter wrote down about it. Nothing plays from this; it
            # is for whoever is deciding what to do with the look (Studio).
            notes=_note(raw.get("notes"))))
    return out


def _note(raw) -> str:
    """A ported entry's notes as one string: the porter writes a list."""
    if isinstance(raw, str):
        return raw
    if isinstance(raw, (list, tuple)):
        return " ".join(str(n) for n in raw if n)
    return ""


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
        # motion_bar even though this writes intensity, because it is the
        # MOVEMENT slot's dimmer: it has to stay in lockstep with the
        # `cue_offsets` layer beside it or the head lights while it is still
        # travelling. Reading level_bar here would let the level rate desync a
        # routine from its own dimmer, which is the one thing that would make a
        # cued chase look broken.
        p = motion.phase(ctx.motion_bar, bars)
        for fixture in ctx.rig.fixtures:
            if not (scope & set(fixture.tags)):
                continue
            values = [step.get(fixture.name) for step in step_levels]
            out[fixture.fid].intensity *= motion.cue_value(values, pairs, p)
    return layer


def color_frames_layer(frames: list[dict[str, list[float]]], bars: float):
    """A stepped color sequence, held per step rather than interpolated.

    A frame value is [r, g, b], or [r, g, b, w] where the fixture is RGBW and
    the step blends real white -- "Pin Drift" walks the same warm pastels the
    Pin scenes hold, and reading only three components made every step of it
    colder than authored.
    """
    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        # color_bar, not motion_bar: this is the color slot, and the whole
        # point of per-slot rate is that a color chase can crawl under a move
        # that is running flat out.
        index = int(motion.phase(ctx.color_bar, bars) * len(frames)) % len(frames)
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

    Separate from the color layer because a uniform-color look can still have
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
    neighbours at 43%; as a base layer that would blank whatever color was
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
        # level_bar: this is the level slot's own phase, so a dim chase can be
        # slowed without touching the move it is dimming.
        index = int(motion.phase(ctx.level_bar, bars) * len(levels)) % len(levels)
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


# ------------------------------------------------------------- parametric --
#
# A parametric look is a block from `blocks.py` with its arguments, so a look
# authored here and a routine in a show folder are made of the same parts and
# move the same way. Everything downstream -- slots, cues, presets, the picker --
# treats one exactly like a ported look.

_NO_AUTOMATION = lambda name: None                      # noqa: E731


def _blocks():
    """`blocks`, imported when first needed rather than at module load.

    `blocks` imports this module (for `LibraryEntry`, `EASINGS` and the ported
    look adapters), so importing it at the top here would be a cycle. By the
    time a look is composed both modules are fully loaded, so a call-time
    import costs nothing and needs no restructuring of either.
    """
    from . import blocks as blocksmod
    return blocksmod


def resolve_args(entry: LibraryEntry) -> dict:
    """The look's arguments over its block's declared defaults.

    Unknown keys are DROPPED rather than refused: this is file-sourced, and a
    `parametric_looks.json` hand-edited at a venue against a slightly older
    engine should lose the key it does not understand and still light the
    room. A command from the console is the opposite case and is strict -- see
    `server._cmd_look_params`.

    Values are NOT clamped to the declared range, for the same reason a routine
    file's are not (`blocks.PARAMS`): the range describes the controls; what an
    author wrote is what plays.
    """
    declared = _blocks().PARAMS.get(entry.block or "", ())
    out = {p.name: p.default for p in declared}
    for key, value in (entry.args or {}).items():
        if key in out:
            out[key] = value
    return out


def _block_inputs(entry: LibraryEntry, roles: Optional[dict] = None):
    """The (args, Env) a parametric look is built from.

    One function for `block_layer` and `arg_problems`, so the check made when a
    value arrives is the same check `blocks.make` would make on the first frame
    -- a check that differed would pass a value the build then refuses.
    """
    blocksmod = _blocks()
    values = resolve_args(entry)
    numeric = {p.name for p in blocksmod.PARAMS.get(entry.block or "", ())
               if p.kind in ("number", "integer")}
    args = {k: (f"${k}" if k in numeric else v) for k, v in values.items()
            if v is not None or k in numeric}
    env = blocksmod.Env(params={k: v for k, v in values.items() if k in numeric})
    if roles:
        env.palette.update(roles)
    return args, env


def arg_problems(entry: LibraryEntry, rig) -> list[str]:
    """Why this look would build EMPTY on `rig`, or nothing if it would build.

    `blocks.make` answers a bad argument with an empty block -- right for a
    routine file, whose problems are reported when it loads, and silent for a
    value arriving from the console, a preset or a cue: a duo whose color was
    "nonsense" built nothing, and the movers sat in the palette's white with no
    word said. Callers on those paths ask here first.
    """
    blocksmod = _blocks()
    args, env = _block_inputs(entry)
    return blocksmod._check_args(entry.block or "", args, env,
                                 blocksmod.Rigging(rig=rig, entries={}))


def block_layer(entry: LibraryEntry, slot: str,
                roles: Optional[dict] = None):
    """One parametric look as a single layer, bound to whatever rig is running.

    Bound LAZILY, on the first frame and again whenever the patch changes, and
    cached on the fixture NAMES -- the reasoning `column_for` spells out about
    `id()` reuse after a rig reload applies here too. Composing a look must not
    need a rig: auto mode's set list builds every look up front, before there
    is necessarily one to bind to.

    Every NUMERIC argument is handed to the block as a `$name` reference, with
    the value itself in the block's Env. That is not indirection for its own
    sake: it is how the block reads the value per frame, which is what lets a
    modulator swing it -- through `Env.automate`, the very mechanism a show
    folder's automation rows use -- without rebuilding anything. A literal
    number would be read once and baked in.
    """
    blocksmod = _blocks()
    args, env = _block_inputs(entry, roles)
    name = entry.name
    cache: dict[tuple[str, ...], object] = {}

    def fixtures_for(rig) -> tuple:
        rigging = blocksmod.Rigging(rig=rig, entries={})
        wanted = entry.groups or (("movers",) if slot == "movement" else ())
        if not wanted:
            return tuple(rig.fixtures)
        seen: dict[int, object] = {}
        for group in wanted:
            for fixture in rigging.tagged(group):
                seen.setdefault(fixture.fid, fixture)
        return tuple(seen.values())

    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        live = ctx.live_params.get(name)
        env.automate = live.get if live else _NO_AUTOMATION
        key = tuple(f.name for f in ctx.rig.fixtures)
        built = cache.get(key)
        if built is None:
            built = blocksmod.make(entry.block or "", args, fixtures_for(ctx.rig),
                                   slot, env, blocksmod.Rigging(rig=ctx.rig, entries={}),
                                   where=name)
            cache[key] = built
        for sub in built.layers:
            sub(ctx, out)
    return layer


# ------------------------------------------------------------------- slots --

DEFAULT_BARS = 8.0


def base_layers(show: statemod.Show) -> None:
    """Point everything at the ball and open it up.

    Always present, whatever is selected, so that a color with no movement --
    or nothing at all -- still produces a picture instead of leaving the heads
    wherever the last look happened to stop.

    Full brightness, deliberately. Everything that dims lives downstream: the
    level slot, the master, and the safety taper. A pinspot has no dimmer
    channel, so `render` scales its RGB by this level -- which means a color
    look's authored bytes ARE its brightness, and seeding anything below 1.0
    here would silently scale every ported color.
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
    if entry.is_parametric:
        # Scoped to the look's own groups rather than to "movers" flat, so a rig
        # with two families of moving head can run a different route on each.
        # Ported entries keep the old unscoped behaviour, because that is what
        # they were authored against and changing it would move the parity
        # sweep.
        show.movement.append(block_layer(entry, "movement"))
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


def color_layers(show: statemod.Show, entry: LibraryEntry,
                 roles: Optional[dict] = None) -> None:
    """One color look, scoped to the fixtures it actually writes.

    The scoping is the fix for a real defect: "Pin Ball Glow" writes two
    pinspots in the workspace, ported as a uniform color with no tags, and so
    repainted all four movers amber as well. A look now only touches its own
    group, which is also what lets a pinspot color and a mover color be up at
    the same time.
    """
    tags = tuple(entry.groups) or None
    if entry.is_parametric:
        # `roles` is the console's palette as "@primary"/"@secondary"/"@accent",
        # so a parametric color follows the palette the operator is rotating,
        # the same way a routine's palette roles follow its show's palette.
        show.color.append(block_layer(entry, "color", roles))
        return
    if entry.color is not None:
        show.color.append(statemod.color_layer(tuple(entry.color), tags=tags))
    elif entry.colors is not None:
        show.color.append(per_fixture_color_layer(entry.colors, entry.whites))
    elif entry.frames is not None:
        show.color.append(color_frames_layer(entry.frames,
                                             entry.bars or DEFAULT_BARS))
    # White rides after the color layer, because `color_layer` resets it.
    if entry.whites and entry.colors is None:
        show.color.append(white_layer(entry.whites))


def level_layers(show: statemod.Show, entry: LibraryEntry) -> None:
    """One level look. Everything here goes in `fx`, and everything multiplies.

    `fx` rather than `base` is the fix for "we lost the actual dimming": a level
    look must scale the color and position already established, and then be
    scaled itself by the master and the safety taper. Anything in `base` would
    instead wipe them.
    """
    tags = tuple(entry.groups) or None
    if entry.is_parametric:
        show.fx.append(block_layer(entry, "level"))
        return
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
            palette_color: tuple[float, float, float] = (1.0, 1.0, 1.0),
            movement_extra: Sequence[LibraryEntry] = (),
            palette_roles: Optional[dict] = None) -> statemod.Show:
    """The slots, plus the base and the auto-mode effects, as one Show.

    `colors` and `levels` are LISTS because each slot is filled per fixture
    group: the pinspots can be on their own color while the movers are on
    another, which is the whole point of splitting them. Each entry is scoped to
    its own group, so they cannot fight.

    The palette goes down first and unscoped, so any group with no color look
    of its own still gets a color rather than rendering whatever the last look
    left behind.

    `movement_extra` STACKS more movement on top of the base route, and it works
    because every movement layer ADDS a degree offset -- only the base
    `pose_layer` assigns. A slow orbit under a fast small jitter is therefore
    just two layers, and it is a class of routine this rig has never produced:
    the old console could only store the sum of two moves as a third scene, and
    only at one relative phase.

    Order matters only for the level layers a cued chase contributes; the
    offsets themselves commute, because addition does.
    """
    show = statemod.Show()
    base_layers(show)
    show.color.append(statemod.color_layer(palette_color))
    # A parametric color look names palette ROLES ("@primary"). Without a
    # palette to read them from, every role is the current palette color --
    # which is what a single-color palette means anyway.
    roles = palette_roles or {"primary": palette_color,
                              "secondary": palette_color,
                              "accent": palette_color}
    for entry in colors:
        color_layers(show, entry, roles)
    movement_layers(show, movement)
    for entry in movement_extra:
        movement_layers(show, entry)
    for entry in levels:
        level_layers(show, entry)
    show.fx.append(autom.energy_intensity_layer())
    show.fx.append(autom.energy_strobe_layer())
    return show


def build_look(entry: LibraryEntry) -> autom.Look:
    """One entry as a standalone Look, for auto mode's set list.

    Auto mode advances the MOVEMENT slot, so this is what a movement entry looks
    like on its own; the controller re-composes it with whatever color and
    level are selected.
    """
    return autom.Look(
        name=entry.name,
        make=lambda color: compose(entry if entry.is_movement else None,
                                   [entry] if entry.is_color else [],
                                   [entry] if entry.is_level else [], color),
        # Levels and resets stay reachable by hand but must never be picked by a
        # timer -- the same reasoning as a blackout parked in the set list.
        # A RETIRED entry is manual-only for the same reason: it is still there
        # so an operator can go back to it deliberately, and auto mode selecting
        # one would be the timer undoing the retirement.
        manual_only=entry.is_level or "reset" in entry.name.lower()
        or entry.step_of is not None or entry.retired)


def load_parametric(path: Path) -> tuple[list[LibraryEntry], dict[str, dict]]:
    """Hand-authored parametric looks, and the ported entries they retire.

    Validated through `config.load` rather than read straight out of JSON,
    because this is the one library file a person edits by hand at a venue --
    which is exactly the case `config.py` exists for. A misspelled block should
    say so by name, once, with the alternatives listed, rather than surfacing as
    an empty layer on the first frame.

    The rig-bound adapters (`look`, `snapshot`) are refused here. A parametric
    look that only wraps a ported look IS that look, under a second name; and a
    look named in a block would tie this file to one rig, which is the property
    blocks exist not to have.
    """
    path = Path(path)
    return parse_parametric(configmod.load(path, configmod.PARAMETRIC_LOOKS), path)


def parse_parametric(cfg: dict, path: Path
                     ) -> tuple[list[LibraryEntry], dict[str, dict]]:
    """`load_parametric`, for a document already read and shape-checked.

    Split out so a document about to be WRITTEN is held to exactly what one
    being read is (`lookstore`): a save that passed a weaker check would write
    a file the next start refuses.
    """
    path = Path(path)
    blocksmod = _blocks()

    out: list[LibraryEntry] = []
    for raw in cfg.get("looks", []):
        name, block = raw["name"], raw["block"]
        if block not in blocksmod.BLOCKS:
            raise configmod.ConfigError(path, [
                f"{name!r} names block {block!r}, which does not exist"
                f"\n      fix: one of {', '.join(blocksmod.BLOCKS)}"])
        slot = blocksmod.SLOT_OF[block]
        if slot is None:
            raise configmod.ConfigError(path, [
                f"{name!r} uses {block!r}, which plays a ported look or preset "
                f"rather than being one\n      fix: select that look directly"])
        # Checked here, against the block's own declarations, so a bad value is
        # reported with the file name at load -- not built into an empty block
        # that quietly claims nothing on stage.
        args = dict(raw.get("args", {}))
        env = blocksmod.Env(params={})
        rigging = blocksmod.Rigging(rig=_NoRig(), entries={})
        problems = blocksmod._check_args(block, args, env, rigging)
        if problems:
            raise configmod.ConfigError(path, [
                f"{name!r}: {problem}" for problem in problems])
        out.append(LibraryEntry(
            name=name, kind=KIND_FOR_BLOCK.get(block, KIND_FOR_SLOT[slot]),
            tags=(), groups=tuple(raw.get("groups", [])),
            block=block, args=args, notes=raw.get("notes", ""),
            supersedes=bool(raw.get("supersedes", False)),
            exact=bool(raw.get("exact", True)),
            source=path.name))

    retired = {r["name"]: r for r in cfg.get("retired", [])}
    return out, retired


class _NoRig:
    """Enough of a rig for `_check_args` at load time: no fixtures, so no
    single-color looks, so a color argument has to be a palette role, a hex
    color or [r, g, b] -- which is what keeps a parametric look portable."""
    fixtures: tuple = ()


def merge(ported: Sequence[LibraryEntry], parametric: Sequence[LibraryEntry],
          retired: Optional[dict[str, dict]] = None) -> list[LibraryEntry]:
    """The library the show sees: the port, plus the parametric looks, minus
    nothing.

    Name collisions are a LOAD ERROR rather than a silent override. Everything
    downstream addresses a look by name -- cues, presets, the picker, auto
    mode's set list -- so two entries with one name means the cue list and the
    operator can disagree about what "Ball Wave" is, and neither would ever find
    out. Naming both sources is the whole content of the message.

    Retirement is applied here rather than at load, because it names entries in
    the OTHER file: `parametric_looks.json` says which ported looks it
    supersedes, and
    that can only be resolved once both are in hand.
    """
    seen = {e.name: e for e in ported}
    # A look that SUPERSEDES must name a ported look that exists. Otherwise a
    # typo in the name would quietly add a new button claiming to replace
    # nothing, and the original it was meant to take over would stay up beside
    # it.
    orphans = [r.name for r in parametric if r.supersedes and r.name not in seen]
    if orphans:
        raise ValueError(
            "parametric_looks.json supersedes "
            + ", ".join(repr(o) for o in sorted(orphans))
            + ", which looks.json does not have -- check the name, or drop "
              "\"supersedes\" to add it as a new look")
    taking_over = {r.name: r for r in parametric if r.supersedes}
    clashes = [r.name for r in parametric
               if r.name in seen and not r.supersedes]
    if clashes:
        raise ValueError(
            "parametric_looks.json and looks.json both define "
            + ", ".join(repr(c) for c in sorted(clashes))
            + " -- rename the parametric look; a look is addressed by name from "
              "cues, presets and the picker, so two with one name is ambiguous "
              "everywhere")

    # In place, so a superseded button keeps its position in the picker and
    # in auto mode's set list. The ported entry leaves the console's library
    # but not looks.json, so the port's round-trip proof is untouched.
    entries = [taking_over.get(e.name, e) for e in ported]
    entries += [r for r in parametric if not r.supersedes]
    if not retired:
        return entries
    return [replace(e, retired=True,
                    replaced_by=retired[e.name].get("replaced_by"),
                    retired_note=retired[e.name].get("note", ""))
            if e.name in retired else e
            for e in entries]


def load_setlist(path: Path, parametric_path: Optional[Path] = None
                 ) -> tuple[autom.SetList, list[LibraryEntry]]:
    """The whole library, from the generated port and the hand-authored file.

    `parametric_path` absent, or pointing at a file that is not there, is
    normal: every event that predates it has none and must keep loading.
    """
    entries = load_entries(path)
    if not entries:
        raise ValueError(f"{path} contains no looks")
    parametric: list[LibraryEntry] = []
    retired: dict[str, dict] = {}
    if parametric_path is not None and Path(parametric_path).exists():
        parametric, retired = load_parametric(Path(parametric_path))
    entries = merge(entries, parametric, retired)
    return autom.SetList([build_look(e) for e in entries]), entries


PORTED_FILE = "looks.json"
PARAMETRIC_FILE = "parametric_looks.json"


def load_library(event_dir: Path) -> list[LibraryEntry]:
    """An event's whole library, from whichever of its two files it has.

    `load_setlist` insists on `looks.json`, which is right for an event ported
    from QLC+ and wrong for one that never was: an event started from nothing
    has only the looks made here, in `parametric_looks.json`. Neither file is
    an empty library, not an error -- the caller decides what an event with no
    looks runs.
    """
    event_dir = Path(event_dir)
    ported_path = event_dir / PORTED_FILE
    parametric_path = event_dir / PARAMETRIC_FILE
    ported = load_entries(ported_path) if ported_path.exists() else []
    parametric: list[LibraryEntry] = []
    retired: dict[str, dict] = {}
    if parametric_path.exists():
        parametric, retired = load_parametric(parametric_path)
    return merge(ported, parametric, retired)


def setlist_for(entries: Sequence[LibraryEntry]) -> autom.SetList:
    return autom.SetList([build_look(e) for e in entries])


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
