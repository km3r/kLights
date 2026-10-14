"""
What the rig is showing, slot by slot: which look is up for movement, color
and level, what has been dialled into it, what moves on its own, and how fast
each slot runs. Auto mode is here too, since what it changes is which
movement look is up.

Movement, color and level are independent -- picking one does not disturb
the others. `_recompose` rebuilds the Show from the three slots whenever one
changes, and `entry` is where tuning meets a look's name, so everything that
resolves a name sees the tuned look.
"""

from __future__ import annotations

import random
from dataclasses import replace
from typing import Optional, Sequence

from .. import auto as autom
from .. import blocks as blocksmod
from .. import library as libmod
from .. import modulate as modmod
from .. import motion
from .. import params as parammod
from .. import state as statemod
from ..commands import OPERATE, command
from .common import N, S, _LOOK_NAME, _SLOT


AUTO_AXES = ("timing", "look_changes", "palette", "energy")


class Picture:
    """The three slots, and everything that turns what is in them."""

    def _init_auto(self) -> None:
        self.palette = default_palette()
        self.director = autom.AutoDirector(
            self.setlist,
            autom.AutoConfig(look_changes=False, palette=False, energy=False,
                             change_every_phrases=2.0, palette_every_phrases=4.0),
            self.palette, autom.PhraseEnergy())

    def _init_picture(self) -> None:
        # Per-routine tuning, by look name -- only the keys the operator has
        # actually moved. Stored as a sparse override rather than a full
        # resolved set so the three layers stay distinct and visible: the
        # generator's default, what the routine authored over it, and what is
        # dialled in now. A full snapshot here would silently freeze a routine's
        # authored value at the moment it was first touched.
        self.look_params: dict[str, dict] = {}
        # Parameters that move on their own. Empty by default, and an empty rack
        # costs one `if` per frame -- a show that never touches modulation runs
        # exactly as it did before this existed.
        self.modulators = modmod.Rack()
        # Movement routines stacked ON TOP of whatever the director has up.
        # Every movement layer adds a degree offset -- only the base pose layer
        # assigns -- so stacking is free and the sum is well defined. Held as
        # names rather than entries so `entry()` still applies tuning and
        # modulation to each on every recompose.
        self.movement_extra: list[str] = []
        # Bumped per `vary`, so pressing it twice gives two different results
        # without reaching for a clock or the module RNG. Published, so a
        # variation worth keeping can be written down.
        self.vary_seed = 0
        # The three independent slots. Selecting a color must not disturb the
        # movement and vice versa -- while one selection replaced the entire
        # show, picking a color threw away the move you had running, which is
        # the opposite of what splitting the scenes during the port was for.
        # Color and level are stored PER FIXTURE GROUP, so the pinspots can be
        # on their own color while the movers are on another. One slot for the
        # whole rig meant picking a pinspot palette threw away the movers', and
        # the two are simply different decisions.
        #
        # MOVEMENT is read through to the set list rather than stored, because
        # auto mode advances that itself; two copies of "which movement look is
        # up" would need syncing on every auto change, and the one that got
        # missed would be the one the UI displays.
        self.slots: dict[str, dict[str, str]] = {"color": {}, "level": {}}

    @property
    def selection(self) -> dict[str, dict[str, str]]:
        current = self.setlist.current()
        entry = self.entry(current.name) if current else None
        movement = ({g: current.name for g in (entry.groups or ("movers",))}
                    if current and entry else {})
        return {"movement": movement,
                "color": dict(self.slots["color"]),
                "level": dict(self.slots["level"])}

    def palette_roles(self, current: tuple[float, float, float]) -> dict:
        """The console's palette, as the roles a block's colors name.

        `primary` is the color auto mode (or the operator) has up right now;
        `secondary` and `accent` are the next two round the palette. So a duo
        built on "@primary"/"@secondary" turns when the palette does, the same
        way a routine's roles follow its show folder's palette -- the console
        has no named palette of its own, and the rotating list it does have is
        the closest honest equivalent.
        """
        colors = list(self.palette.colors) or [current]
        here = self.palette.index % len(colors)
        return {"primary": current,
                "secondary": colors[(here + 1) % len(colors)],
                "accent": colors[(here + 2) % len(colors)]}

    def slot_entries(self, slot: str) -> list[libmod.LibraryEntry]:
        """The distinct looks filling one slot across all groups.

        De-duplicated by name: a look covering two groups occupies both, and
        applying its layers twice would double its effect.
        """
        seen: dict[str, libmod.LibraryEntry] = {}
        for name in self.slots[slot].values():
            entry = self.entry(name)
            if entry is not None:
                seen[name] = entry
        return list(seen.values())

    def _base_palette(self) -> dict:
        """The engine's own palette, as roles, for a timeline that has none."""
        colors = self.palette.colors
        i = self.palette.index
        primary = tuple(colors[i % len(colors)]) if colors else (1.0, 1.0, 1.0)
        secondary = (tuple(colors[(i + 1) % len(colors)]) if colors
                     else (1.0, 1.0, 1.0))
        return {"primary": primary, "secondary": secondary,
                "accent": (1.0, 1.0, 1.0)}

    def _run_modulators(self) -> None:
        """Resolve every modulator onto the context, once per frame.

        Called from `on_show`, which the runner invokes at the END of
        `sync_clock` -- so musical position and the per-slot phases are already
        current. Doing it in `before_frame` instead would read last frame's bar
        and put every LFO one frame behind the music for no reason.

        Writes a MACRO straight onto the field `move_layer` already reads, and a
        routine's parameter into `ctx.live_params` where the generator layers
        pick it up. Two destinations because they are genuinely two things: a
        macro is one number for the whole movement slot, a routine's radius
        belongs to that routine.

        Nothing here can reach past the safety taper. These are parameters, and
        `apply_safety` runs after the whole stack unconditionally -- see
        `test_safety.py` section 8, which sweeps modulated values for exactly
        this reason.
        """
        if not self.modulators:
            # The overwhelmingly common case. Clearing rather than skipping,
            # because a modulator removed mid-show must stop driving the value
            # rather than leaving its last frame frozen on stage.
            if self.ctx.live_params:
                self.ctx.live_params = {}
            return
        macros, looks = self.modulators.resolve(self.ctx.bar, self.ctx.energy)
        self.ctx.live_params = looks
        # Named from the declarations, so a renamed macro cannot leave a
        # modulator that resolves and lands nowhere.
        size, spread = parammod.SIZE.name, parammod.SPREAD.name
        bearing_key, elev_key = parammod.CENTER_BEARING.name, parammod.CENTER_ELEV.name
        if size in macros:
            self.ctx.move_size = macros[size]
        if spread in macros:
            self.ctx.move_spread = macros[spread]
        if bearing_key in macros or elev_key in macros:
            bearing, elev = self.ctx.move_center
            self.ctx.move_center = (macros.get(bearing_key, bearing),
                                    macros.get(elev_key, elev))

    def entry(self, name: Optional[str]) -> Optional[libmod.LibraryEntry]:
        """A look by name, with whatever the operator has dialled into it.

        Tuning is keyed by look NAME rather than by (slot, group), because a
        look is addressed by name everywhere else -- cues, presets, the picker,
        auto mode's set list -- and because the movement slot has no per-group
        selection to key on. It also means a tweak survives switching away and
        back, which is what anyone would expect of a knob they just set.

        Applied HERE, at the one point every consumer resolves a name, so the
        composed show, the cue list and the set list all see the same tuned
        entry without any of them knowing tuning exists.
        """
        base = self.by_name.get(name) if name else None
        if base is None:
            return None
        overrides = self.look_params.get(base.name)
        if not overrides:
            return base
        return replace(base, args={**base.args, **overrides})

    def _set_look_params(self, name: str, values: Optional[dict]) -> None:
        """Replace one look's tuning; None or {} puts it back to authored.

        REBINDS the dict rather than editing it, the convention every
        collection the 10 Hz snapshot walks follows (`self.flashing` says why):
        that thread iterates `look_params` while a command lands on this one,
        and a dict changing size under it is a "snapshot failed" on every phone.
        """
        rest = {k: v for k, v in self.look_params.items() if k != name}
        self.look_params = {**rest, name: dict(values)} if values else rest

    def _recompose(self, fade_beats: Optional[float] = None) -> None:
        """Rebuild the Show from the three slots.

        The director keeps owning WHICH movement look is up (that is what auto
        look changes change), but it no longer owns the whole Show -- it
        delegates back here so the color and level slots survive a look change.

        `fade_beats` crossfades into the result instead of cutting. Only cues
        pass it today: a look picked by hand is an operator watching the rig and
        wanting it now, while a cue is a rehearsed transition that should look
        like one.
        """
        def compose(look, color: tuple[float, float, float]):
            movement = self.entry(look.name) if look is not None else None
            return libmod.compose(
                movement if movement is not None and movement.is_movement else None,
                self.slot_entries("color"), self.slot_entries("level"), color,
                # Resolved here rather than held as entries, so a stacked look
                # picks up its own tuning and modulation exactly as the base
                # route does -- `entry()` is the one place that applies it.
                #
                # The base route is left out of the stack. `movement_add`
                # refuses to stack the current base, but the base can change
                # AFTER -- picked by hand, by a cue, by auto mode -- and a look
                # that is both would run twice at double excursion. Filtered
                # here, where every route to a new base passes, rather than
                # unstacked: when the base moves on, the stacked look returns.
                movement_extra=[e for e in
                                (self.entry(n) for n in self.movement_extra
                                 if look is None or n != look.name)
                                if e is not None and e.is_movement],
                palette_roles=self.palette_roles(color))
        self.director.compose = compose
        show = self.director.rebuild()
        if self.player is not None and self.player.engaged:
            # The timeline is on stage: the rebuilt show is what the grabbed
            # lanes read, picked up next frame. Handing it to the runner here
            # would swap the timeline out for a frame and cut straight back.
            return
        if fade_beats is not None:
            # `is not None`, not truthiness: a cue with fade 0 is a CUT, and it
            # has to cancel a fade already in flight rather than letting the
            # previous one carry on underneath it. Taking the Drop mid-fade and
            # watching it dissolve is exactly the failure.
            #
            # The runner picks the director's show up on the next frame anyway;
            # handing it over here is what gives it something to fade FROM,
            # since by then the old one is gone.
            self.runner.set_show(show, fade_beats=fade_beats)

    @command("select_look", tier=OPERATE, args={
        "name": _LOOK_NAME,
        "slot": _SLOT,
        "hold": S(bool)})
    def _cmd_select_look(self, m: dict, now: float) -> None:
        """Select a look INTO ITS OWN SLOT, worked out from what it sets.

        The caller does not say which slot; the library already knows, because
        the port split every scene by which channels it touched. A `mixed` entry
        fills movement and color together, since it genuinely states both --
        and either stays independently changeable afterwards.
        """
        name = m["name"]
        entry = self.by_name.get(name)
        if entry is None:
            raise KeyError(f"no look named {name!r}")
        self._clear_pad()
        slot = m.get("slot") or entry.slot
        if slot not in ("movement", "color", "level"):
            raise ValueError(f"unknown slot {slot!r}")
        if slot == "movement":
            # bool(): `held` is published in the snapshot as it is stored, and
            # a client's -Infinity there was a snapshot JSON could not carry.
            self.director.select(name, hold=bool(m.get("hold", True)))
        else:
            # Fills its slot for every group it writes, and only those -- so a
            # pinspot color replaces the pinspot color and leaves the movers
            # alone. A look covering the whole rig naturally replaces both.
            for group in (entry.groups or ("movers",)):
                self.slots[slot][group] = name
        if entry.kind == "mixed":
            for group in (entry.groups or ("movers",)):
                self.slots["color"][group] = name
        self._grab({slot, "color"} if entry.kind == "mixed" else {slot})
        self._recompose()

    @command("clear_slot", tier=OPERATE, args={
        "slot": S(str, required=True, choices=statemod.SLOTS),
        "group": S(str)})
    def _cmd_clear_slot(self, m: dict, now: float) -> None:
        slot = m["slot"]
        self._clear_pad()
        if slot == "movement":
            raise ValueError(
                "the movement slot cannot be empty -- with nothing aiming the "
                "heads they would hold wherever the last look left them. Pick "
                "another position instead.")
        group = m.get("group")
        if group is None:
            self.slots[slot].clear()
        else:
            self.slots[slot].pop(group, None)
        self._grab({slot})
        self._recompose()

    @command("look_params", tier=OPERATE, args={
        "name": _LOOK_NAME,
        "values": S(dict, fix="argument -> value, as the block declares them"),
        "reset": S(bool)})
    def _cmd_look_params(self, m: dict, now: float) -> None:
        """Turn one routine's own knobs, live.

        This is the thing the ported library cannot have. A stored look is a
        table of DMX -- there is nothing in "Ball Wave" to turn, which is why
        103 poses and 26 paths accumulated as separate entries. A routine names
        a generator, so its radius, its cycle length and its flattening are all
        numbers, and one entry covers what a shelf of stored chases used to.

        STRICT about unknown keys, unlike loading a file. A typo'd key arriving
        from the console means the UI and the engine disagree about what this
        routine has, and silently dropping it produces a slider that appears to
        work and changes nothing -- the most confusing failure a console has. A
        stale key in a hand-edited `parametric_looks.json` is the opposite case and is
        dropped, so the room still lights.

        Out-of-range values are CLAMPED rather than refused, because one bad
        number must not discard the three good ones sent alongside it. Nothing
        here can reach past the safety taper: these are parameters, and
        `apply_safety` runs after the whole stack regardless.
        """
        name = m["name"]
        base = self.by_name.get(name)
        if base is None:
            raise KeyError(f"no look named {name!r}")
        if not base.is_parametric:
            raise ValueError(
                f"{name!r} is a ported look -- a stored table of DMX with no "
                f"arguments to turn. Parametric looks come from "
                f"parametric_looks.json and are the ones built from a block")

        if m.get("reset"):
            self._set_look_params(name, None)
            self._recompose()
            self.note(f"{name!r} back to its authored values")
            return

        values = m.get("values") or {}
        if not values:
            raise ValueError("look_params needs values, or reset: true")
        declared = blocksmod.PARAMS[base.block or ""]
        resolved = parammod.resolve(declared, values, strict=True,
                                    reach=self.reach)
        # Only the keys actually sent are recorded. Copying `resolved` whole
        # would bake every other parameter's current default into the override
        # and detach the routine from its own authored numbers.
        current = dict(self.look_params.get(name, {}))
        for key in values:
            current[key] = resolved[key]
        # A color or a list is passed through by `Param` -- whether "@accent"
        # or "#ff0080" means anything is a question for the rig -- so ask the
        # check the block itself would make, before storing rather than after.
        # Refused whole, unlike a clamped number: there is no nearest color to
        # "nonsense", and storing it builds an empty block that lights nothing.
        problems = libmod.arg_problems(
            replace(base, args={**base.args, **current}), self.rig)
        if problems:
            raise ValueError(f"{name!r}: {'; '.join(problems)}")
        self._set_look_params(name, current)
        self._recompose()

    def looks_named(self, block: dict) -> list[str]:
        """Every look a preset or cue names, across its three slots."""
        out: list[str] = []
        for slot in ("movement", "color", "level"):
            for name in (block.get(slot) or {}).values():
                if name and name not in out:
                    out.append(name)
        return out

    def capture_look_params(self, named: Sequence[str]) -> Optional[dict]:
        """The tuning to store with a preset, for the looks it names.

        Returns None when the preset names no tunable routine at all, so a
        preset over ported looks carries no empty block.

        Note this records an EMPTY dict for a routine sitting at its authored
        values, and that is the deliberate difference from how `rates` is
        stored. A rate is a performance ride the operator keeps their hand on,
        so a preset that always restored one would undo a change made after it
        was saved -- hence "absent means leave alone" there. A routine's radius
        is not a ride, it is part of the picture the preset exists to get back
        to. Recording only the non-default ones would mean a preset saved at
        authored values silently kept whatever was dialled in later.
        """
        tuned: dict[str, dict] = {}
        for name in named:
            entry = self.by_name.get(name)
            if entry is not None and entry.is_parametric:
                tuned[name] = dict(self.look_params.get(name, {}))
        return tuned or None

    def apply_look_params(self, params: Optional[dict], named: Sequence[str],
                          where: str) -> None:
        """Restore per-routine tuning from a preset or a cue.

        `params` None leaves every routine alone -- what a cue hand-written
        before this existed means, and what one that simply has no opinion about
        tuning means. A dict is exhaustive over the looks the cue NAMES: a
        routine it names but does not list goes back to its authored values, so
        "take this cue" cannot leave a radius from three cues ago on stage.

        Values are clamped and unknown keys DROPPED rather than refused. This is
        file-sourced, like `parametric_looks.json` and unlike a command from the console:
        a preset saved against a slightly different engine should put back what
        it still can and say what it could not, rather than refusing to load
        mid-set.
        """
        if params is None:
            return
        dropped: list[str] = []
        for name in named:
            entry = self.by_name.get(name)
            if entry is None or not entry.is_parametric:
                continue
            wanted = params.get(name) or {}
            declared = blocksmod.PARAMS.get(entry.block or "")
            if not declared:
                continue
            kept: dict = {}
            for key, value in wanted.items():
                param = parammod.find(declared, key)
                if param is None:
                    dropped.append(f"{name}.{key}")
                    continue
                try:
                    value = parammod.clamp(param, value, self.reach)
                except parammod.ParamError:
                    dropped.append(f"{name}.{key}")
                    continue
                # One key at a time against the authored look, so a color this
                # rig cannot resolve is dropped on its own and the rest of the
                # tuning still lands -- see `_cmd_look_params` for why it is
                # checked at all.
                if libmod.arg_problems(
                        replace(entry, args={**entry.args, key: value}), self.rig):
                    dropped.append(f"{name}.{key}")
                    continue
                kept[key] = value
            self._set_look_params(name, kept)
        if dropped:
            self.note(f"{where}: dropped {', '.join(dropped[:4])}"
                      f"{' and more' if len(dropped) > 4 else ''} -- "
                      f"not a parameter, or not a value this rig can use")

    def target_param(self, look: Optional[str], param: str) -> parammod.Param:
        """The `Param` a modulator is aimed at, whichever kind of target it is.

        One lookup for both, so a modulator is bounded by the same declaration
        the operator's own slider is bounded by. A modulator that could sweep
        outside its target's range would be a way to reach a value no finger
        could have dragged to -- which is precisely what the ranges are for.
        """
        if not look:
            found = parammod.find(parammod.MACROS, param)
            if found is None:
                raise ValueError(
                    f"no shape macro {param!r} -- one of "
                    f"{', '.join(p.name for p in parammod.MACROS)}")
            return found
        entry = self.by_name.get(look)
        if entry is None:
            raise KeyError(f"no look named {look!r}")
        if not entry.is_parametric:
            raise ValueError(
                f"{look!r} is a ported look -- a stored table of DMX with no "
                f"arguments to modulate")
        declared = blocksmod.PARAMS.get(entry.block or "", ())
        found = parammod.find(declared, param)
        if found is None:
            raise ValueError(
                f"{look!r} has no argument {param!r} -- its block takes "
                f"{', '.join(p.name for p in declared)}")
        return found

    @command("modulate", tier=OPERATE, args={
        "param": S(str, required=True, non_empty=True),
        "look": S(str),
        "shape": S(str, choices=modmod.SHAPES),
        "bars": S(N),
        "low": S(N),
        "high": S(N),
        "phase": S(N),
        "seed": S(N)})
    def _cmd_modulate(self, m: dict, now: float) -> None:
        """Bind a parameter to a musical waveform.

        The single biggest thing the old console could not say. A stored scene
        has no parameters, so "the same look, slowly widening" was a chase of
        twenty scenes that stepped between them -- visibly. Here it is one
        routine and one modulator.

        Binding the same target twice REPLACES rather than stacking. Two LFOs
        fighting over one number is not something anyone means to ask for, and
        which one won would depend on dict order.
        """
        param = m["param"]
        look = m.get("look")
        target = self.target_param(look, param)
        self.modulators.add(modmod.build(m, target, self.reach))

    @command("modulate_clear", tier=OPERATE, args={
        "param": S(str),
        "look": S(str),
        "all": S(bool)})
    def _cmd_modulate_clear(self, m: dict, now: float) -> None:
        """Stop one modulator, or all of them.

        A cleared macro modulator leaves the macro wherever its last frame put
        it, deliberately: that is where the picture currently is, and snapping
        back to the authored value on release would be a jump nobody asked for.
        `macro reset` is the way back to identity, and it already exists.
        """
        if m.get("all"):
            self.modulators.clear()
            self.ctx.live_params = {}
            self.note("all modulation stopped")
            return
        param = m.get("param")
        if not param:
            raise ValueError("modulate_clear needs param, or all: true")
        look = m.get("look")
        if not self.modulators.remove(look, param):
            raise KeyError(
                f"nothing is modulating {param!r}"
                + (f" on {look!r}" if look else ""))
        # A routine's parameter falls straight back to what the routine was
        # composed with, since `live_params` is rebuilt from the rack each
        # frame -- so releasing one does not need to restore anything by hand.

    # A cap, not a limit the maths needs. Offsets add, so ten would compose
    # fine and be unreadable -- and each one is another layer walked per frame.
    # Three is enough for "a slow route, a fast wobble, and a drift".
    MAX_STACK = 3

    @command("movement_add", tier=OPERATE, args={"name": _LOOK_NAME})
    def _cmd_movement_add(self, m: dict, now: float) -> None:
        """Stack another movement routine on top of the one that is up.

        Free, because every movement layer ADDS a degree offset -- only the base
        pose layer assigns. A slow orbit under a fast small jitter is two layers
        and nothing else, and it is a shape the old console could not express:
        it could store the sum of two moves as a third scene, but only at one
        relative phase, and that phase was baked in.

        The base route is NOT eligible. Stacking a look on itself doubles its
        excursion, which is what Size is for, and it would read as a routine
        that mysteriously got twice as big.
        """
        name = m["name"]
        entry = self.by_name.get(name)
        if entry is None:
            raise KeyError(f"no look named {name!r}")
        if not entry.is_movement:
            raise ValueError(
                f"{name!r} fills the {entry.slot} slot -- only movement looks "
                f"stack, because only movement offsets add")
        current = self.setlist.current()
        if current is not None and current.name == name:
            raise ValueError(
                f"{name!r} is already the base route -- stacking it on itself "
                f"would just double its excursion, which is what Size does")
        if name in self.movement_extra:
            raise ValueError(f"{name!r} is already stacked")
        if len(self.movement_extra) >= self.MAX_STACK:
            raise ValueError(
                f"{self.MAX_STACK} stacked routines is the limit -- past that "
                f"nobody can tell which one is doing what")
        # Rebound, never appended to -- the snapshot thread lists it.
        self.movement_extra = [*self.movement_extra, name]
        self._recompose()

    @command("movement_remove", tier=OPERATE, args={"name": S(str), "all": S(bool)})
    def _cmd_movement_remove(self, m: dict, now: float) -> None:
        if m.get("all"):
            self.movement_extra = []
            self._recompose()
            return
        name = m.get("name")
        if not name:
            raise ValueError("movement_remove needs name, or all: true")
        if name not in self.movement_extra:
            raise KeyError(f"{name!r} is not stacked")
        self.movement_extra = [n for n in self.movement_extra if n != name]
        self._recompose()

    @command("vary", tier=OPERATE, args={
        "name": _LOOK_NAME,
        "amount": S(N, fix="0-1: how much of each range to move within"),
        "seed": S(int)})
    def _cmd_vary(self, m: dict, now: float) -> None:
        """Nudge a routine's numbers around, reproducibly.

        Every parameter carries a declared range, which is what makes this
        possible at all: "somewhere else, but still sensible" needs to know what
        sensible is. `amount` is the fraction of each range to move within, so
        0.1 is a nudge and 1.0 can reach anywhere the slider goes.

        SEEDED, never the module RNG. `engine/` had no `random` import before
        this and the reason is reproducibility -- previz, the parity sweep and
        the port all depend on the same inputs giving the same frame. A seeded
        `random.Random` keeps that: the seed is published, so a variation worth
        keeping can be written down and reproduced exactly.

        A `musical` parameter (the cycle length, `bars`) is deliberately left
        alone -- it is how the routine sits against the track, while everything
        else is shape. Rolling a new cycle length is how you turn a 16-bar
        swell into a 3.75-bar one that fits nothing.

        MERGED over the operator's other settings, not instead of them. Vary
        only rolls shape numbers, so a direction or a cycle length dialled in by
        hand has nothing to do with it and must survive the press.
        """
        name = m["name"]
        entry = self.by_name.get(name)
        if entry is None:
            raise KeyError(f"no look named {name!r}")
        if not entry.is_parametric:
            raise ValueError(
                f"{name!r} is a ported look -- it has no parameters to vary")
        amount = max(0.0, min(1.0, float(m.get("amount", 0.3))))
        if m.get("seed") is not None:
            seed = int(m["seed"])
        else:
            self.vary_seed += 1
            seed = self.vary_seed
        # A string seed, not a tuple: `random.Random` refuses tuples on 3.11+.
        # Keyed by NAME as well as seed so the same seed gives each look its
        # own variation rather than moving them all the same way.
        rng = random.Random(f"{name}:{seed}")

        # From the look's AUTHORED values, not from whatever is currently
        # dialled in. Varying the varied compounds: press it four times and you
        # have random-walked to an extreme, and no seed describes where you are.
        base = libmod.resolve_args(entry)
        varied: dict = {}
        for param in blocksmod.PARAMS.get(entry.block or "", ()):
            if param.musical or param.kind not in ("number", "integer"):
                continue
            # An argument with no fixed default (a fan's sweep is half its
            # width unless given) has nothing to perturb FROM.
            lo, hi = parammod.bounds(param, self.reach)
            if lo is None or hi is None or base.get(param.name) is None:
                continue
            span = (hi - lo) * amount
            varied[param.name] = parammod.clamp(
                param, base[param.name] + rng.uniform(-0.5, 0.5) * span,
                self.reach)
        if not varied:
            raise ValueError(f"{name!r} has nothing that can be varied")
        # Rebound, never mutated: the 10 Hz snapshot walks this dict from
        # another thread (see `_set_look_params`).
        kept = {k: v for k, v in self.look_params.get(name, {}).items()
                if k not in varied}
        self._set_look_params(name, {**kept, **varied})
        self._recompose()
        self.note(f"varied {name!r} at {amount:.2f} from seed {seed}")

    @command("release", tier=OPERATE)
    def _cmd_release(self, m: dict, now: float) -> None:
        self.director.release()

    @command("next_look", tier=OPERATE)
    def _cmd_next_look(self, m: dict, now: float) -> None:
        self._clear_pad()
        self.setlist.advance()
        self._grab({"movement"})
        self._recompose()

    @command("auto", tier=OPERATE, args={
        "axis": S(str, required=True, choices=AUTO_AXES),
        "on": S(bool, required=True)})
    def _cmd_auto(self, m: dict, now: float) -> None:
        setattr(self.director.config, m["axis"], m["on"])

    @command("auto_interval", tier=OPERATE, args={
        "axis": S(str, required=True, choices=("looks", "palette")),
        "value": S(N, required=True, fix="phrases between changes")})
    def _cmd_auto_interval(self, m: dict, now: float) -> None:
        field_name = {"looks": "change_every_phrases",
                      "palette": "palette_every_phrases"}[m["axis"]]
        setattr(self.director.config, field_name, max(0.0625, float(m["value"])))

    @command("energy", tier=OPERATE, args={
        "source": S(str, choices=("manual", "phrase")),
        "value": S(N)})
    def _cmd_energy(self, m: dict, now: float) -> None:
        if m.get("source", "manual") == "manual":
            level = float(m.get("value", 0.5))
            if not isinstance(self.director.energy_source, autom.ManualEnergy):
                self.director.energy_source = autom.ManualEnergy(level)
            else:
                self.director.energy_source.value = level
        else:
            self.director.energy_source = autom.PhraseEnergy()

    @command("palette_select", tier=OPERATE, args={"index": S(int, required=True)})
    def _cmd_palette_select(self, m: dict, now: float) -> None:
        self.palette.index = int(m["index"]) % len(self.palette.colors)
        self.director.rebuild()

    @command("macro", tier=OPERATE, args={
        "size": S(N),
        "spread": S(N),
        "center": S(list, each=S(N), fix="[bearing, elevation] in degrees"),
        "reset": S(bool)})
    def _cmd_macro(self, m: dict, now: float) -> None:
        """Live shape controls over whatever movement look is up.

        Set on the context rather than recomposing, so they survive an auto look
        change -- and so they cost nothing per frame beyond the multiply.

        Ranges are clamped, not because a bigger number breaks anything, but
        because a size of 40 aims every head at the floor in a straight line and
        an operator who typed it meant 4. The safety taper still runs after the
        whole stack regardless, so no macro value can put a beam anywhere the
        taper would not have allowed a stored look to put it.

        The ranges come from `params.MACROS` rather than from literals here.
        They used to be written out three times -- this clamp, a `Spec`, and the
        arguments to a slider in `Move.tsx` -- with nothing keeping them in
        step, so the UI could offer a value the engine would quietly clamp. Now
        the snapshot publishes the same descriptors the clamp uses.
        """
        if "size" in m:
            self.ctx.move_size = parammod.SIZE.coerce(m["size"])
        if "spread" in m:
            self.ctx.move_spread = parammod.SPREAD.coerce(m["spread"])
        if "center" in m:
            c = m["center"]
            # Bounded by what THIS rig's heads can reach, not a fixed +-180 /
            # +-90. On despacio that is -77..198 degrees of bearing: the old
            # limit offered a hundred degrees no head could go to and refused
            # the eighteen past 180 that every one of them can.
            self.ctx.move_center = (
                parammod.clamp(parammod.CENTER_BEARING, c[0], self.reach),
                parammod.clamp(parammod.CENTER_ELEV, c[1], self.reach))
        if m.get("reset"):
            self.ctx.move_size = parammod.SIZE.default
            self.ctx.move_spread = parammod.SPREAD.default
            self.ctx.move_center = (parammod.CENTER_BEARING.default,
                                    parammod.CENTER_ELEV.default)

    @command("rate", tier=OPERATE, args={
        "slot": _SLOT,
        "value": S(N),
        "reset": S(bool)})
    def _cmd_rate(self, m: dict, now: float) -> None:
        """How fast one slot's chase runs, relative to everything else.

        Distinct from `speed`, which is the CLOCK: speed changes what the music
        is doing as far as the whole show is concerned, including cue holds and
        auto boundaries. A rate changes only how fast one slot's phase
        advances, so a color chase at 0.5x under a move at 2x is a thing that
        can now be said. The old console needed a separately stored chase for
        every combination, which is a large part of why it accumulated 206
        looks.

        Safe by construction for the same reason the macros are: this moves a
        phase, and every layer downstream -- including the unconditional safety
        pass -- runs exactly as it did.
        """
        if m.get("reset"):
            self.director.phases.reset()
            self.note("slot rates back to 1x")
            return
        if m.get("slot") is None or m.get("value") is None:
            raise ValueError("rate needs slot and value, or reset: true")
        self.director.phases.set_rate(m["slot"], float(m["value"]))

    def apply_rates(self, rates: dict) -> None:
        """Restore stored slot rates, skipping any the engine does not know.

        Same rule as a preset naming a look that has been re-ported away: a
        stored rate for a slot this build has never heard of is a note, not a
        refusal. Half a recall beats an error message mid-set.
        """
        for slot, value in rates.items():
            try:
                self.director.phases.set_rate(str(slot), float(value))
            except (ValueError, TypeError) as exc:
                self.note(f"rate {slot!r}: {exc}")

    def _public_picture(self) -> dict:
        """What is up in each slot, and everything turning it."""
        return {
            "selection": self.selection,
            "fading": self.runner.fading,
            "macro": {"size": round(self.ctx.move_size, 3),
                      "spread": round(self.ctx.move_spread, 3),
                      "center": [round(self.ctx.move_center[0], 2),
                                 round(self.ctx.move_center[1], 2)]},
            "auto": self.director.status(),
            # NOT here: the block descriptors, the shape macros' descriptors and
            # the modulator shapes. They are constants of this engine build, so
            # they reach the UI as a file generated from it
            # (`dump_designer_fixtures.py` -> ui/src/blocks.generated.json),
            # which `test_api` keeps current. Putting them in this snapshot
            # would resend the same few kilobytes ten times a second to every
            # phone in the room to say something that cannot change while the
            # engine is up.
            # What the operator has turned, by look name, over what the routine
            # authored. Sparse, and sent separately from `looks[].params` for
            # the same reason `color_overrides` is sent separately from the
            # color a look states: the UI needs to show both to be able to
            # offer a meaningful Reset.
            "look_params": {name: dict(values)
                            for name, values in self.look_params.items()},
            # Parameters that are moving on their own. A list rather than a map,
            # because the UI shows them as a rack of running modulators and a
            # (look, param) key is not a JSON object key.
            "modulators": self.modulators.status(),
            # Movement routines stacked over the base route, in the order they
            # were added. Their offsets ADD, which is why stacking works at all.
            "movement_extra": list(self.movement_extra),
            "movement_stack_max": self.MAX_STACK,
            # The seed the last `vary` used, so a variation worth keeping can be
            # written down and reproduced exactly.
            "vary_seed": self.vary_seed,
            "palette": [list(c) for c in self.palette.colors],
            "palette_index": self.palette.index,
        }


def default_setlist() -> autom.SetList:
    """A starter set list, built from the motion primitives.

    Placeholder for the ported library (F9) -- deliberately small, so it is
    obvious this is scaffolding rather than the real show.
    """
    def look(name, offset_fn, bars):
        def make(color):
            show = statemod.Show()
            show.base.append(statemod.pose_layer(
                lambda ctx, head: ctx.geometry.aim_at_ball(head), tags=("movers",)))
            show.base.append(statemod.on_layer(0.7, tags=("pinspots",)))
            show.color.append(statemod.color_layer(color))
            if offset_fn is not None:
                show.movement.append(statemod.move_layer(
                    motion.as_move(offset_fn, bars=bars), tags=("movers",)))
            show.fx.append(autom.energy_intensity_layer())
            show.fx.append(autom.energy_strobe_layer())
            return show
        return autom.Look(name=name, make=make)

    # Cycle lengths live on the patterns themselves now, so they are stated once.
    return autom.SetList([
        look("ball", None, 0.0),
        look("drift", motion.orbit(15.0, bars=16.0, elongation=1.5), None),
        look("sweep", motion.pendulum(45.0, bars=8.0), None),
        look("wide orbit", motion.orbit(40.0, bars=8.0), None),
        look("bob", motion.pendulum(18.0, bars=4.0, vertical=True), None),
    ])


def default_palette() -> autom.Palette:
    return autom.Palette([
        (1.0, 1.0, 1.0), (1.0, 0.1, 0.05), (1.0, 0.55, 0.05), (1.0, 0.95, 0.15),
        (0.15, 1.0, 0.25), (0.1, 0.85, 1.0), (0.2, 0.35, 1.0), (1.0, 0.2, 0.75),
    ])
