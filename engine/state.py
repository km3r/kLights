"""
The parameter store and the layered evaluation that turns it into a DMX frame.

The engine holds **parameters**, not stored DMX values. That single difference
is why QLC+ is being retired: a per-fixture color picker, phrase-aware
automation, smooth interpolated motion and one show running in two rooms are
each combinatorially explosive as stored scenes, and all four were wanted.

Layers evaluate in a fixed order, every frame:

    base look -> color -> movement -> FX -> master -> SAFETY

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
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from typing import Callable, Iterator, Optional, Sequence

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
    # Shutter: 0 is open, above 0 is a position in the fixture's own slow-to-fast
    # strobe band. Not in Hz -- the profile declares "Strobe slow to fast" and no
    # frequency at either end, so a Hz figure here would be invented.
    strobe: float = 0.0
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

    # Motion phase, ONE PER SLOT, integrated separately from `bar` so a slot's
    # rate can vary without moving musical position. A layer reads the phase of
    # the slot it was composed into; anything that must land ON the music --
    # look changes, cue holds, boundary hits -- reads `bar`. Same split as tempo
    # versus speed on the clock.
    #
    # Three instead of one because the slots are independent everywhere else and
    # were not here: a color chase at half speed under a move at double is a
    # combination the old console needed a separate stored chase for, and with a
    # single shared phase the engine could not express it either.
    #
    # `motion_bar` keeps its name rather than becoming `movement_bar`. It is
    # what movement has always read, and renaming it would touch every ported
    # look, the parity sweep and the previz decoder to say nothing new.
    motion_bar: float = 0.0
    color_bar: float = 0.0
    level_bar: float = 0.0

    # Auto mode's continuous outputs. Defaults are the identity, so a show that
    # ignores auto mode behaves identically whether or not a director is
    # attached.
    energy: float = 0.0
    energy_rate: float = 1.0
    auto_intensity: float = 1.0
    strobe: bool = False
    taper: safetymod.TaperConfig = field(default_factory=safetymod.TaperConfig)
    strobe_policy: safetymod.StrobeConfig = field(
        default_factory=safetymod.StrobeConfig)

    # Live shape controls over whatever movement look is up.
    #
    # The ported library has 103 poses and 26 paths because QLC+ stored DMX
    # values and had no parameters, so every variation of a move had to be a
    # separate scene. The engine holds parameters, and these are the ones that
    # actually vary: how far a move travels, whether the heads do it together,
    # and where the whole thing is centred. One "Ball Wave" with a centre offset
    # is the "Floor Wave" that used to be its own entry.
    #
    # They live on the context rather than in the composed Show for the same
    # reason `energy` does: auto mode rebuilds the layer stack on every look
    # change, and a size the operator dialled in has to survive that. Identity
    # defaults, so a show that never touches them behaves exactly as before.
    move_size: float = 1.0
    # Degrees added to every head's offset -- moves the whole look off the ball.
    move_center: tuple[float, float] = (0.0, 0.0)
    # Phase spread across the heads, in CYCLES. 0 is unison; 1.0 spreads n heads
    # evenly around one cycle, which is the idiom every multi-head move in the
    # old library hand-encoded.
    move_spread: float = 0.0

    # This frame's MODULATED routine parameters, by look name then parameter.
    # Written once per frame by the modulator rack; read by the generator
    # layers, which fall back to the values the routine was composed with.
    #
    # On the context for the same reason `move_size` is: the composed Show is
    # rebuilt whenever a look changes, and a value that moves every frame must
    # not require rebuilding the layer stack forty times a second to express.
    # Empty by default, so a show with no modulators behaves identically and
    # pays only a dict lookup for it.
    live_params: dict[str, dict[str, float]] = field(default_factory=dict)

    # Last frame's safety multiplier per fixture, and when it was computed.
    # State the safety layer needs and nothing else may touch -- slew limiting
    # is inherently temporal, and the alternative (making clearance() stateful)
    # would make it untestable as a pure function of geometry.
    _taper_prev: dict[int, float] = field(default_factory=dict, repr=False)
    _taper_time: float = field(default=0.0, repr=False)
    # How long each fixture's shutter has been strobing continuously, and how
    # long it has been open since being cut off. Same argument as the taper's
    # slew memory: the limit is inherently temporal, and making the policy
    # stateful would make it untestable as a pure function.
    _strobe_since: dict[int, float] = field(default_factory=dict, repr=False)
    _strobe_rest: dict[int, float] = field(default_factory=dict, repr=False)
    _strobe_time: float = field(default=0.0, repr=False)

    @property
    def geometry(self) -> Optional[geo.RigGeometry]:
        return self.rig.geometry

    def set_phase(self, bars: float) -> None:
        """Put every slot at the same phase.

        What a test, a parity sweep or a previz decoder wants: they ask "what
        does this look do at phase p", and p is one number. Per-slot rate is a
        performance control, not something a round-trip check should have to
        model.
        """
        self.motion_bar = self.color_bar = self.level_bar = bars

    @contextmanager
    def scoped(self, **fields) -> Iterator["EvalContext"]:
        """Set some of the context for the duration of a block, then put it
        back exactly.

        For a timeline (F19h), where one frame evaluates several sources each
        on its OWN time: a routine clip runs on its clip-local phase with its
        own size, while the fallback show under it keeps the show's. Layers
        read the context, so the context is what has to change -- and it must
        come back even if a layer raises, or the next source would inherit it.
        """
        saved = {name: getattr(self, name) for name in fields}
        try:
            for name, value in fields.items():
                setattr(self, name, value)
            yield self
        finally:
            for name, value in saved.items():
                setattr(self, name, value)


SLOTS = ("movement", "color", "level")


class SlotPhases:
    """The three motion phases, and the rate each one runs at.

    Integrated as `rate * d(bar)`, never computed as `rate * bar`. That is the
    whole reason this is a class and not two multiplications: the naive form
    jumps every time a rate changes, and it jumps by more the longer the show
    has been running. At bar 40 a rate going 1.0 -> 1.5 moves the phase 20 bars
    in a single frame and snaps every move on stage. Same reasoning as the
    clock's re-anchoring, and the same failure if it is skipped.

    Rates are per slot and MULTIPLY the common rate rather than replacing it, so
    auto mode's energy response still drives everything and a slot rate is the
    operator saying "that one, relatively faster".
    """

    def __init__(self) -> None:
        self.bars: dict[str, float] = {s: 0.0 for s in SLOTS}
        self.rate: dict[str, float] = {s: 1.0 for s in SLOTS}
        self._last: Optional[float] = None

    def set_rate(self, slot: str, value: float) -> None:
        if slot not in self.rate:
            raise ValueError(f"no slot {slot!r} -- one of {', '.join(SLOTS)}")
        # 0 freezes that slot, which is a real thing to want: a color chase
        # parked on its current frame under a move that keeps running.
        #
        # Negative is NOT allowed, and the reason is the cued chases. Those
        # travel dark and light on arrival, so running one backwards means
        # holding first and travelling second -- which reads as a broken
        # routine rather than a reversed one. Reverse is a real feature and it
        # needs its own thinking about `cue_path`, not a sign flip here.
        if not 0.0 <= value <= 8.0:
            raise ValueError(f"rate must be 0-8, not {value}")
        self.rate[slot] = float(value)

    def reset(self) -> None:
        for slot in SLOTS:
            self.rate[slot] = 1.0

    @property
    def changed(self) -> bool:
        return any(r != 1.0 for r in self.rate.values())

    def advance(self, bar: float, *, running: bool = True,
                common: float = 1.0) -> None:
        """Move every phase on by this frame's musical delta.

        `running` false freezes every phase where it stands while musical
        position keeps advancing underneath -- so turning it back on resumes
        rather than snapping forward to where the move would have been. That is
        the auto `timing` axis, and it is why `_last` is updated either way.
        """
        if self._last is None:
            for slot in SLOTS:
                self.bars[slot] = bar
        elif running:
            delta = bar - self._last
            if delta < 0:                     # a phase nudge ran time backwards
                delta = 0.0
            for slot in SLOTS:
                self.bars[slot] += delta * common * self.rate[slot]
        self._last = bar

    def apply(self, ctx: "EvalContext") -> None:
        ctx.motion_bar = self.bars["movement"]
        ctx.color_bar = self.bars["color"]
        ctx.level_bar = self.bars["level"]

    def status(self) -> dict[str, float]:
        return {slot: round(self.rate[slot], 3) for slot in SLOTS}


Layer = Callable[[EvalContext, dict[int, FixtureState]], None]


# ------------------------------------------------------------------ layers --

def _targets(ctx: EvalContext, tags: Optional[Sequence[str]]) -> list[rigmod.PatchedFixture]:
    """Fixtures a layer applies to. `None` means every fixture.

    Selection is by TAG for anything a look does -- that is what lets a look
    built for four corner heads run unchanged on a club rig with eight, and a
    look should never name a specific unit.

    A fixture NAME also matches, and only for the operator's sake: soloing one
    head or dimming one pinspot from the phone is inherently about that unit,
    and there is no tag for "this one". Matching names here rather than adding a
    parallel mechanism is what makes the color and level overrides work at all
    -- before it, every per-fixture target in the UI silently did nothing,
    because the name matched no tag and the layer applied to an empty set.
    """
    if tags is None:
        return list(ctx.rig.fixtures)
    wanted = set(tags)
    return [f for f in ctx.rig.fixtures
            if wanted & set(f.tags) or f.name in wanted]


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
                white: Optional[float] = None) -> Layer:
    """Set color. Separate from the pose layer on purpose: color and position
    were entangled in the old workspace because both lived in the same Scene,
    which is why changing one meant authoring a new scene for every value of the
    other.

    `white` is None by default rather than 0.0, and left untouched in that case
    -- a caller that only knows RGB (the common case, since not every fixture
    has a white channel) should not silently zero out a white an earlier layer
    set, e.g. a look's own blended white on a pinspot."""
    def layer(ctx: EvalContext, out: dict[int, FixtureState]) -> None:
        for f in _targets(ctx, tags):
            out[f.fid].color = color
            if white is not None:
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
            offset_aim(ctx, state, *offset(ctx, f.head))
    return layer


def offset_aim(ctx: EvalContext, state: FixtureState, d_bearing: float,
               d_elev: float) -> None:
    """Move one head's aim by an offset, through the shape macros.

    SIZE scales about zero, and zero is each head's own calibrated ball aim,
    because that is what these offsets are relative to. So size 0 collapses
    every head onto the ball and size 2 doubles the excursion -- about the
    look's own centre, per head, with no extra geometry. CENTRE then moves the
    whole thing off the ball, which is what turns one "Ball Wave" into the
    "Floor Wave" that used to need its own entry.

    The single point every movement offset passes through -- the ported looks
    via `move_layer`, the timeline's blocks (F19h) directly -- because two
    copies of it would be two chances to differ.
    """
    centre_b, centre_e = ctx.move_center
    state.aim = geo.Aim(
        state.aim.bearing_delta + d_bearing * ctx.move_size + centre_b,
        state.aim.elev_deg + d_elev * ctx.move_size + centre_e)


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
    """The color-wheel slot closest to a requested RGB.

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

        # Intensity and color. A fixture with a real dimmer takes the level
        # there and keeps its color at full; an RGBW fixture with no dimmer
        # channel scales its color by the level instead, which is the only
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

        # Shutter. Left alone at 0 unless something asked for a strobe, so the
        # baseline (open) stands -- writing an "open" value every frame would
        # override a profile that holds its shutter elsewhere.
        strobe_idx = f.index_of(rigmod.STROBE)
        if strobe_idx is not None and state.strobe > 0:
            band = f.strobe_band()
            if band is not None:
                lo, hi = band
                frame[strobe_idx] = lo + round(max(0.0, min(1.0, state.strobe)) * (hi - lo))

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
    # Live operator overrides -- a color picked on a phone, a head soloed.
    # A separate list because auto mode rebuilds the other four whenever the
    # look changes, and an override typed in by a human must survive that;
    # the controller keeps one list and re-attaches it to each new Show.
    # After `fx` and before `master`, so an override outranks the look but is
    # still subject to the master and to safety.
    overrides: list[Layer] = field(default_factory=list)

    def stack(self) -> list[Layer]:
        return [*self.base, *self.color, *self.movement, *self.fx,
                *self.overrides, master_layer(self.master)]


def apply_strobe_policy(ctx: EvalContext, out: dict[int, FixtureState]) -> None:
    """The last word on the shutter, for the same reason apply_safety is.

    Not a layer, so a look cannot reorder itself in front of it. Strobe reaches
    the rig from ported looks AND from auto mode's energy axis, so a policy that
    any of those could outrank would only be a policy for the paths that
    happened to respect it.

    See StrobeConfig for why this caps a band position and a duration rather
    than a frequency: the frequency is not knowable from the profile.
    """
    policy = ctx.strobe_policy
    dt = max(0.0, min(1.0, ctx.time - ctx._strobe_time))
    ctx._strobe_time = ctx.time

    for f in ctx.rig.fixtures:
        state = out[f.fid]
        if state.strobe <= 0:
            # Open. Accumulate rest, and forget the burst once rested enough.
            rest = ctx._strobe_rest.get(f.fid, 0.0) + dt
            ctx._strobe_rest[f.fid] = rest
            if rest >= policy.recover_seconds:
                ctx._strobe_since[f.fid] = 0.0
            continue

        if not policy.enabled:
            state.strobe = 0.0
            continue

        state.strobe = min(state.strobe, policy.ceiling)

        if policy.max_seconds > 0:
            elapsed = ctx._strobe_since.get(f.fid, 0.0)
            if elapsed >= policy.max_seconds:
                # Cut off. Rest accrues from here; the burst only clears once
                # recover_seconds of open shutter have passed, so this is a stop
                # rather than a duty cycle.
                state.strobe = 0.0
                ctx._strobe_rest[f.fid] = ctx._strobe_rest.get(f.fid, 0.0) + dt
                continue
            ctx._strobe_since[f.fid] = elapsed + dt
        ctx._strobe_rest[f.fid] = 0.0


def evaluate_stack(ctx: EvalContext, show: Show) -> dict[int, FixtureState]:
    """The layers, and nothing after them. Not safe to send on its own."""
    states = {f.fid: FixtureState() for f in ctx.rig.fixtures}
    for layer in show.stack():
        layer(ctx, states)
    return states


def finish(ctx: EvalContext, states: dict[int, FixtureState]) -> None:
    """Everything that runs after the stack, unconditionally, exactly once."""
    apply_safety(ctx, states)
    apply_strobe_policy(ctx, states)


def blend(a: dict[int, FixtureState], b: dict[int, FixtureState],
          t: float) -> dict[int, FixtureState]:
    """Interpolate two evaluated frames. `t` 0 is all `a`, 1 is all `b`.

    In PARAMETER space, which is the whole reason the engine holds parameters.
    Blending the rendered DMX instead would interpolate a color-wheel slot
    index -- halfway between "red" and "blue" being "orange" because those slots
    happen to be adjacent on the disc -- and would quantise the aim to 8 bits
    before smoothing it, which is what makes a fade look steppy.

    Aim blends in DEGREES, and `bearing_delta` is deliberately unwrapped servo
    rotation, so a head crossing from +170 to -170 travels the 340 degrees it
    physically has to rather than teleporting through the short way. That is the
    right answer for a yoke and the wrong one for an angle, which is exactly why
    the convention exists.

    Things that cannot be interpolated take `b` past the midpoint: a gobo is a
    slot, and there is no half of one.
    """
    out: dict[int, FixtureState] = {}
    for fid, first in a.items():
        second = b.get(fid)
        if second is None:
            out[fid] = first
            continue
        aim = None
        if first.aim is not None and second.aim is not None:
            aim = geo.Aim(
                first.aim.bearing_delta
                + (second.aim.bearing_delta - first.aim.bearing_delta) * t,
                first.aim.elev_deg
                + (second.aim.elev_deg - first.aim.elev_deg) * t)
        else:
            # One side has no aim at all -- a color-only look, or a fixture
            # that is not a mover. Holding the side that HAS one keeps the head
            # where it is instead of snapping it to a default.
            aim = second.aim if second.aim is not None else first.aim

        raw = second.raw_position if t >= 0.5 else first.raw_position
        out[fid] = FixtureState(
            aim=aim,
            intensity=first.intensity + (second.intensity - first.intensity) * t,
            color=tuple(c1 + (c2 - c1) * t
                        for c1, c2 in zip(first.color, second.color)),
            white=first.white + (second.white - first.white) * t,
            strobe=first.strobe + (second.strobe - first.strobe) * t,
            gobo=second.gobo if t >= 0.5 else first.gobo,
            raw_position=raw)
    return out


def evaluate(ctx: EvalContext, show: Show) -> dict[int, FixtureState]:
    """One frame of parameters. Safety runs after the stack, unconditionally."""
    states = evaluate_stack(ctx, show)
    finish(ctx, states)
    return states


def evaluate_crossfade(ctx: EvalContext, outgoing: Show, incoming: Show,
                       t: float) -> dict[int, FixtureState]:
    """One frame mid-fade between two shows.

    Both stacks are evaluated, blended, and THEN finished -- so safety and the
    strobe policy see the aim and intensity that are actually going to the wire,
    once, rather than each side separately. Running them per side and blending
    the results would let a fade pass through a state neither show would have
    been allowed to produce.
    """
    states = blend(evaluate_stack(ctx, outgoing), evaluate_stack(ctx, incoming),
                   max(0.0, min(1.0, t)))
    finish(ctx, states)
    return states


def frame(ctx: EvalContext, show: Show) -> dict[int, bytearray]:
    """One frame of DMX, ready for the wire."""
    return render(ctx, evaluate(ctx, show))
