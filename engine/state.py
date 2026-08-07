"""
The parameter store and the layered evaluation that turns it into a DMX frame.

The engine holds **parameters**, not stored DMX values. That single difference
is why QLC+ is being retired: a per-fixture colour picker, phrase-aware
automation, smooth interpolated motion and one show running in two rooms are
each combinatorially explosive as stored scenes, and all four were wanted.

Layers evaluate in a fixed order, every frame:

    base look -> colour -> movement -> FX -> master -> SAFETY

Later layers see what earlier ones produced and may replace or modulate it.
Two properties fall out of that ordering, both of which the old rig had to fake:

  * **Exclusivity is real state.** Selecting a look replaces the base layer.
    QLC+ had no such concept, so the workspace emulated radio buttons with 207
    hidden `~`-prefixed mirror buttons -- a state machine hand-encoded in XML.
    None of that survives here.

  * **Safety is last and unconditional.** It is not a layer you can put
    something after; `evaluate()` runs it after the stack regardless of what the
    stack contains. A guard that a look can outrank is not a guard.

On merge semantics: QLC+ merged HTP for Intensity and LTP for everything else,
at the channel level, because independent functions could write the same channel
with no ordering between them. Here layers compose in parameter space in a
defined order, so that arbitration is not needed -- and the HTP consequence goes
with it. In QLC+ a Scene could only push a head BRIGHTER than the dimmer fader,
never darker, which is why 13 routines were silent no-ops at high fader values
and why the web UI carried a whole auto-park subsystem to yank the fader down
and back. The rig still records each channel's merge rule (it is needed to diff
frames against QLC+ during the parity check), but the engine does not arbitrate.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from typing import Callable, Optional, Sequence

from . import geometry as geo
from . import rig as rigmod
from . import safety as safetymod
from .venue import Venue


# ------------------------------------------------------------------- state --

@dataclass
class FixtureState:
    """One fixture's parameters for one frame. Everything normalised 0..1 except
    the aim, which stays in degrees until the very last step -- quantising to
    DMX early is what makes motion steppy."""
    aim: Optional[geo.Aim] = None
    intensity: float = 0.0
    color: tuple[float, float, float] = (1.0, 1.0, 1.0)   # linear 0..1 RGB
    white: float = 0.0
    strobe_hz: float = 0.0
    gobo: Optional[int] = None
    # Literal 16-bit pan/tilt, bypassing the aim maths. Only calibration sets
    # this, and only because the aim maths is the thing being calibrated -- you
    # cannot aim by geometry at a head whose geometry you do not yet trust.
    raw_position: Optional[tuple[int, int]] = None
    # Set by the safety layer so the UI can explain a dimmed beam rather than
    # leaving the operator to wonder why a head went dark.
    safety: Optional[safetymod.Clearance] = None


@dataclass
class EvalContext:
    """Everything a layer is allowed to read."""
    rig: rigmod.Rig
    venue: Optional[Venue]
    # Wall time, and musical position. Everything a look reads should be
    # musical: `time` is here for the safety slew limiter and for anything that
    # genuinely is a physical duration, not for authoring movement.
    time: float = 0.0            # seconds since start
    beat: float = 0.0            # cumulative fractional beats, never wraps
    bar: float = 0.0
    phrase: float = 0.0
    bpm: float = 0.0
    taper: safetymod.TaperConfig = field(default_factory=safetymod.TaperConfig)

    # Last frame's safety multiplier per fixture, and when it was computed.
    # State the safety layer needs and nothing else may touch -- slew limiting
    # is inherently temporal, and the alternative (making clearance() stateful)
    # would make it untestable as a pure function of geometry.
    _taper_prev: dict[int, float] = field(default_factory=dict, repr=False)
    _taper_time: float = field(default=0.0, repr=False)

    @property
    def geometry(self) -> Optional[geo.RigGeometry]:
        return self.rig.geometry


Layer = Callable[[EvalContext, dict[int, FixtureState]], None]


# ------------------------------------------------------------------ layers --

def _targets(ctx: EvalContext, tags: Optional[Sequence[str]]) -> list[rigmod.PatchedFixture]:
    """Fixtures a layer applies to. `None` means every fixture.

    Selection is by TAG, never by fixture id -- that is what lets a look built
    for four corner heads run unchanged on a club rig with eight.
    """
    if tags is None:
        return list(ctx.rig.fixtures)
    wanted = set(tags)
    return [f for f in ctx.rig.fixtures if wanted & set(f.tags)]


def pose_layer(aim_for: Callable[[EvalContext, int], geo.Aim],
               tags: Optional[Sequence[str]] = ("movers",),
               intensity: float = 1.0) -> Layer:
    """Base look: point the movers somewhere and turn them on.

    `aim_for(ctx, head_index)` is a FUNCTION of time, not a stored pose. A held
    pose is the constant case; a sweep is the same thing with `ctx.beat` in it.
    That is the whole of what makes motion continuous instead of stepped.
    """
    def layer(ctx: EvalContext, out: dict[int, FixtureState]) -> None:
        for f in _targets(ctx, tags):
            state = out[f.fid]
            state.intensity = intensity
            if f.head is not None and ctx.geometry is not None:
                state.aim = aim_for(ctx, f.head)
    return layer


def on_layer(level: float = 1.0, tags: Optional[Sequence[str]] = None) -> Layer:
    """Turn fixtures on without aiming them -- pinspots, pars, anything static.

    A base layer, not an intensity modulator: intensity starts at 0 each frame,
    so a fixture nothing claims stays dark. Defaulting to dark rather than lit
    means forgetting a layer loses a fixture, which is obvious, instead of
    leaving one blazing, which is not.
    """
    def layer(ctx: EvalContext, out: dict[int, FixtureState]) -> None:
        for f in _targets(ctx, tags):
            out[f.fid].intensity = level
    return layer


def raw_pose_layer(values: dict, intensity: float = 1.0,
                   bits: int = 8) -> Layer:
    """Drive named fixtures at literal pan/tilt DMX. Calibration only.

    `values` maps a fixture name or id to (pan, tilt). `bits` says what those
    numbers are: 8 for the coarse readings an operator dials and records, 16 for
    a fine jog.

    **This bypasses the safety taper**, and not by oversight. The taper works
    from the aim, the aim comes from the geometry, and the geometry is exactly
    what has not been established yet -- so there is nothing trustworthy to
    guard with. It is also what you want during a re-aim: a beam that dims as
    you swing it toward the middle of the room is a beam you cannot see well
    enough to point.

    The consequence is that calibration belongs in an empty room, and anything
    driving this layer should say so on screen. `FixtureState.raw_position`
    being set is the flag to check.
    """
    shift = 8 if bits == 8 else 0

    def layer(ctx: EvalContext, out: dict[int, FixtureState]) -> None:
        for f in ctx.rig.fixtures:
            if f.name in values:
                pan, tilt = values[f.name]
            elif f.fid in values:
                pan, tilt = values[f.fid]
            else:
                continue
            out[f.fid].raw_position = (int(pan) << shift, int(tilt) << shift)
            out[f.fid].intensity = intensity
    return layer


def color_layer(color: tuple[float, float, float],
                tags: Optional[Sequence[str]] = None,
                white: float = 0.0) -> Layer:
    """Set colour. Separate from the pose layer on purpose: colour and position
    were entangled in the old workspace because both lived in the same Scene,
    which is why changing one meant authoring a new scene for every value of the
    other."""
    def layer(ctx: EvalContext, out: dict[int, FixtureState]) -> None:
        for f in _targets(ctx, tags):
            out[f.fid].color = color
            out[f.fid].white = white
    return layer


def move_layer(offset: Callable[[EvalContext, int], tuple[float, float]],
               tags: Optional[Sequence[str]] = ("movers",)) -> Layer:
    """Add a (bearing, elevation) offset in degrees to whatever the base aimed.

    Relative, not absolute. An absolute movement effect cannot track per-head
    calibration -- one global centre cannot serve four heads with four different
    calibrated ball points -- and that defect broke 3 of 6 show looks and forced
    every effect in the workspace to be rebuilt as relative.
    """
    def layer(ctx: EvalContext, out: dict[int, FixtureState]) -> None:
        for f in _targets(ctx, tags):
            state = out[f.fid]
            if state.aim is None or f.head is None:
                continue
            d_bearing, d_elev = offset(ctx, f.head)
            state.aim = geo.Aim(state.aim.bearing_delta + d_bearing,
                                state.aim.elev_deg + d_elev)
    return layer


def intensity_layer(level: Callable[[EvalContext, "rigmod.PatchedFixture"], float],
                    tags: Optional[Sequence[str]] = None) -> Layer:
    """Multiply intensity -- dimmer chases, strobes, pulses.

    The callback receives the FIXTURE, not an index. The convention across this
    module: a layer restricted to movers passes a head index (always defined,
    and what the geometry wants), while a layer that runs on anything passes the
    fixture, because there is no index that means the same thing for a mover and
    a pinspot. It used to pass the position in the filtered list, which looked
    like a head index and was not -- the pinspots came through as 0 and 1 while
    their head indices do not exist at all.

    For a phase-offset chase use `fixture.head` on movers, or the fixture's
    position in `ctx.rig.by_tag(...)` for anything else.
    """
    def layer(ctx: EvalContext, out: dict[int, FixtureState]) -> None:
        for f in _targets(ctx, tags):
            out[f.fid].intensity *= level(ctx, f)
    return layer


def master_layer(level: float) -> Layer:
    def layer(ctx: EvalContext, out: dict[int, FixtureState]) -> None:
        for state in out.values():
            state.intensity *= level
    return layer


def apply_safety(ctx: EvalContext, out: dict[int, FixtureState]) -> None:
    """The last word. Not exported as a layer because it must not be orderable
    -- `evaluate()` calls it after the stack, always."""
    if ctx.geometry is None or ctx.venue is None:
        return

    # Clamped: a paused or rewound clock must not licence an unlimited jump.
    dt = max(0.0, min(1.0, ctx.time - ctx._taper_time))
    ctx._taper_time = ctx.time
    max_step = (ctx.taper.slew_per_second * dt
                if ctx.taper.slew_per_second > 0 else None)

    for f in ctx.rig.fixtures:
        state = out[f.fid]
        if f.head is None or state.aim is None:
            continue
        clear = safetymod.clearance(ctx.geometry, f.head, state.aim, ctx.venue,
                                    ctx.taper)

        value = clear.taper
        previous = ctx._taper_prev.get(f.fid)
        if max_step is not None and previous is not None:
            # Rate-limit in BOTH directions. Limiting only the rise would be
            # the safer-sounding choice and is wrong for this goal: the whole
            # point is that the level changes smoothly, and a beam snapping
            # down as it reaches the crowd reads as a flicker just as much as
            # one snapping up.
            value = max(previous - max_step, min(previous + max_step, value))
        ctx._taper_prev[f.fid] = value

        state.safety = replace(clear, taper=value)
        state.intensity *= value


# ------------------------------------------------------------------- render --

def _nearest_slot(slots, rgb: tuple[float, float, float]) -> int:
    """The colour-wheel slot closest to a requested RGB.

    Plain Euclidean distance in RGB. Not perceptually correct, but a 14-slot
    mechanical wheel of saturated primaries has no near-ties for it to get
    wrong, and a perceptual metric here would be precision no wheel can use.
    """
    want = tuple(max(0.0, min(1.0, c)) * 255.0 for c in rgb)
    best = min(slots, key=lambda s: sum((a - b) ** 2 for a, b in zip(s.rgb, want)))
    return best.mid


def render(ctx: EvalContext, states: dict[int, FixtureState]) -> dict[int, bytearray]:
    """Parameters -> one 512-byte frame per universe.

    Starts from `rig.baseline()`, so every held channel is asserted before
    anything else writes. That is the structural fix for the stuck-LTP-channel
    bug: a value that must always be present no longer depends on some function
    happening to be running.
    """
    frames = {u: ctx.rig.baseline(u) for u in ctx.rig.universes}

    for f in ctx.rig.fixtures:
        state = states[f.fid]
        frame = frames[f.universe]
        level = max(0.0, min(1.0, state.intensity))

        # Position. 16 bits wherever the fixture offers it -- one 8-bit Pan step
        # is 2.1 degrees on these heads, which is visible on a slow move.
        position = None
        if state.raw_position is not None:
            position = state.raw_position          # calibration; see raw_pose_layer
        elif state.aim is not None and f.head is not None and ctx.geometry is not None:
            position = ctx.geometry.encode(f.head, state.aim)

        if position is not None:
            pan16, tilt16 = position
            pan_hi, pan_lo = geo.split16(pan16)
            tilt_hi, tilt_lo = geo.split16(tilt16)
            for role, value in ((rigmod.PAN, pan_hi), (rigmod.TILT, tilt_hi),
                                (rigmod.PAN_FINE, pan_lo), (rigmod.TILT_FINE, tilt_lo)):
                idx = f.index_of(role)
                if idx is not None:
                    frame[idx] = value

        # Intensity and colour. A fixture with a real dimmer takes the level
        # there and keeps its colour at full; an RGBW fixture with no dimmer
        # channel scales its colour by the level instead, which is the only
        # place brightness can live on the pinspot.
        dim_idx = f.index_of(rigmod.DIMMER)
        has_dimmer = dim_idx is not None
        if has_dimmer:
            frame[dim_idx] = round(level * 255)

        rgb_scale = 1.0 if has_dimmer else level
        for role, component in ((rigmod.RED, state.color[0]),
                                (rigmod.GREEN, state.color[1]),
                                (rigmod.BLUE, state.color[2]),
                                (rigmod.WHITE, state.white)):
            idx = f.index_of(role)
            if idx is not None:
                frame[idx] = round(max(0.0, min(1.0, component)) * rgb_scale * 255)

        wheel_idx = f.index_of(rigmod.COLOR_WHEEL)
        if wheel_idx is not None:
            slots = f.profile.channels[
                f.profile.modes[f.mode][f.offset_of(rigmod.COLOR_WHEEL)]].color_slots
            if slots:
                frame[wheel_idx] = _nearest_slot(slots, state.color)

        gobo_idx = f.index_of(rigmod.GOBO)
        if gobo_idx is not None and state.gobo is not None:
            frame[gobo_idx] = max(0, min(255, state.gobo))

    return frames


# ------------------------------------------------------------------ engine --

@dataclass
class Show:
    """The layer stack, evaluated in order. Replacing the base layer is how a
    look changes -- there is no hidden mirror mesh to keep in sync."""
    base: list[Layer] = field(default_factory=list)
    color: list[Layer] = field(default_factory=list)
    movement: list[Layer] = field(default_factory=list)
    fx: list[Layer] = field(default_factory=list)
    master: float = 1.0

    def stack(self) -> list[Layer]:
        return [*self.base, *self.color, *self.movement, *self.fx,
                master_layer(self.master)]


def evaluate(ctx: EvalContext, show: Show) -> dict[int, FixtureState]:
    """One frame of parameters. Safety runs after the stack, unconditionally."""
    states = {f.fid: FixtureState() for f in ctx.rig.fixtures}
    for layer in show.stack():
        layer(ctx, states)
    apply_safety(ctx, states)
    return states


def frame(ctx: EvalContext, show: Show) -> dict[int, bytearray]:
    """One frame of DMX, ready for the wire."""
    return render(ctx, evaluate(ctx, show))
