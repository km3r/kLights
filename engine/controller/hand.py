"""
What the operator sets by hand over whatever look is up: the master,
blackout, panic, and color, level and flash per target -- a tag, a fixture
name, or "all".

These are layers re-attached to every Show the runner plays
(`_attach_overrides`), so they survive an auto-mode look change, and they sit
before the master and the safety pass, so neither is outrun.
"""

from __future__ import annotations

from .. import state as statemod
from ..commands import OPERATE, command, nullable
from .common import N, S, _TARGET


class Hand:
    """Overrides by hand, re-attached to whatever Show is up."""

    def _init_hand(self) -> None:
        self.master = 0.9
        self.blackout = False

        # Live operator overrides, re-attached to whatever Show is running so
        # they survive an auto-mode look change.
        self.override_layers: list[statemod.Layer] = []
        # Hand color, white, level and flash, per target. Every one of these
        # is published by the 10 Hz snapshot, which walks them on the
        # broadcast thread while commands land on this one, so each is REBOUND
        # by a write and never edited: a dict that changes size under a
        # comprehension raises, and that broadcast is lost on every console.
        # `test_snapshot_threads` makes the two threads collide on purpose.
        self.color_overrides: dict[str, tuple[float, float, float]] = {}
        # RGBW white, per target -- separate from color_overrides because not
        # every fixture has a white channel, and a target that mixes RGB-only
        # and RGBW fixtures should not force an opinion on the ones without one.
        self.white_overrides: dict[str, float] = {}
        # Hand dimming, per fixture or per group. A multiplier, applied after
        # the level slot and before the master -- so it trims a running pattern
        # rather than replacing it, and the master still governs the lot.
        self.level_overrides: dict[str, float] = {}
        # Momentary bumps, by target. Held while a finger is down and released
        # when it lifts -- the one control here that is not a state you leave
        # somewhere, which is why it is a set rather than a level.
        self.flashing: set[str] = set()

    def _attach_overrides(self, show: statemod.Show) -> None:
        show.master = 0.0 if self.blackout else self.master
        show.overrides = self.override_layers
        self._run_modulators()

    @command("master", tier=OPERATE, args={
        "value": S(N, required=True, fix="0 to 1, the show's overall level")})
    def _cmd_master(self, m: dict, now: float) -> None:
        self.master = max(0.0, min(1.0, float(m["value"])))

    @command("blackout", tier=OPERATE, args={"on": S(bool)})
    def _cmd_blackout(self, m: dict, now: float) -> None:
        self.blackout = bool(m.get("on", not self.blackout))

    @command("panic", tier=OPERATE)
    def _cmd_panic(self, m: dict, now: float) -> None:
        self.runner.panic()
        self.note("PANIC -- output forced to zero")

    @command("clear_panic", tier=OPERATE)
    def _cmd_clear_panic(self, m: dict, now: float) -> None:
        self.runner.clear_panic()
        self.note("panic cleared")

    @command("color", tier=OPERATE, args={
        "target": _TARGET,
        "color": S(list, required=True, each=S(N), fix="[r, g, b], each 0 to 1"),
        "white": nullable(S(N, fix="0 to 1; null puts the white back to the look's")),
        "clear": S(bool)})
    def _cmd_color(self, m: dict, now: float) -> None:
        """Per-fixture picker and global quick palette, in one command.

        `target` is a tag, a fixture name, or "all". Applied as an override
        layer rather than by rebuilding the look, so a color picked by hand
        survives an auto-mode look change -- which is what "everyone controls
        everything" needs to mean in practice.
        """
        raw = m["color"]
        # Exactly three. An empty list was stored as an empty color, render()
        # then raised on every frame, and the runner held the last good frame:
        # the rig frozen on one command until someone cleared that color.
        if len(raw) != 3:
            raise ValueError(f"color must be [r, g, b], each 0 to 1, got {raw!r}")
        color = tuple(max(0.0, min(1.0, float(c))) for c in raw)
        target = _target(m)
        # Both REBOUND, never edited: the snapshot publishes them from the
        # broadcast thread (see where they are declared).
        colors = {k: v for k, v in self.color_overrides.items() if k != target}
        whites = {k: v for k, v in self.white_overrides.items() if k != target}
        if m.get("clear"):
            self.color_overrides = colors
            self.white_overrides = whites
        else:
            self.color_overrides = {**colors, target: color}
            # "white" absent (the quick palette, most color picks) leaves any
            # white already overridden for this target alone -- a palette tap
            # is a statement about RGB only, not an instruction to forget the
            # white the operator dialed in a moment ago. `white: null` (the
            # picker's own clear) is what actually resets it.
            if "white" in m:
                white = m.get("white")
                if white is None:
                    self.white_overrides = whites
                else:
                    self.white_overrides = {
                        **whites, target: max(0.0, min(1.0, float(white)))}
        self._rebuild_overrides()

    @command("level", tier=OPERATE, args={
        "target": _TARGET,
        "value": S(N, fix="0-1, a multiplier on the look"),
        "clear": S(bool)})
    def _cmd_level(self, m: dict, now: float) -> None:
        """Dim one fixture, one group, or everything, by hand.

        The counterpart to the color picker, and the same shape: an override
        layer keyed by target, so a level trimmed by hand survives an auto-mode
        look change. Distinct from the Bright slot, which holds a PATTERN from
        the library -- this is the operator saying "that head is too hot right
        now", which no stored look can anticipate.

        A multiplier, not an absolute. It sits after the level pattern and
        before the master, so pattern x hand-trim x master x safety all compose
        in the order an operator would expect: the pattern keeps running, the
        trim rides on top of it, and the master still takes everything down.
        """
        target = _target(m)
        # Rebound, never edited -- published, like the color overrides.
        rest = {k: v for k, v in self.level_overrides.items() if k != target}
        if m.get("clear"):
            self.level_overrides = rest
        elif m.get("value") is None:
            raise ValueError("level needs value, or clear: true")
        else:
            self.level_overrides = {
                **rest, target: max(0.0, min(1.0, float(m["value"])))}
        self._rebuild_overrides()

    @command("flash", tier=OPERATE, args={"target": _TARGET, "on": S(bool)})
    def _cmd_flash(self, m: dict, now: float) -> None:
        """Momentary bump on a group, a fixture, or everything.

        Held rather than latched: `on: false` on release. A latching flash is a
        level, and there is already a level.

        It sets intensity rather than multiplying it, so it works from a group
        that is trimmed to zero -- which is the case it exists for. The safety
        taper still runs after it, so a bump cannot put a beam anywhere a look
        could not.
        """
        target = _target(m)
        # Rebound, not mutated. `snapshot()` runs on the broadcast thread and
        # reads this set while commands run on the output thread; a set that
        # changes size mid-iteration raises, and the broadcast that was building
        # the frame is lost. Rebinding is atomic, so a reader sees the set
        # before or the set after and never one being edited. Same reasoning
        # everywhere else this file hands a live container to the snapshot.
        if m.get("on", True):
            self.flashing = self.flashing | {target}
        else:
            self.flashing = self.flashing - {target}
        self._rebuild_overrides()

    @command("flash_clear", tier=OPERATE)
    def _cmd_flash_clear(self, m: dict, now: float) -> None:
        """Release every bump.

        A pointer that leaves the button while down, or a phone that locks
        mid-press, never sends the release -- and a flash stuck on is a group
        stuck at full. The UI sends this on reconnect for that reason.
        """
        self.flashing = set()
        self._rebuild_overrides()

    def _rebuild_overrides(self) -> None:
        layers: list[statemod.Layer] = []
        for target, color in self.color_overrides.items():
            tags = None if target == "all" else (target,)
            layers.append(statemod.color_layer(
                color, tags=tags, white=self.white_overrides.get(target)))
        for target, level in self.level_overrides.items():
            tags = None if target == "all" else (target,)
            layers.append(statemod.intensity_layer(
                lambda ctx, fixture, level=level: level, tags=tags))
        # Flash last, so a bump outranks a hand trim that is dimming the same
        # group -- pressing flash on something you just pulled down should make
        # it bright, not multiply two numbers together and produce nothing.
        # Still under the master and under safety, like every other override.
        for target in self.flashing:
            tags = None if target == "all" else (target,)
            layers.append(statemod.on_layer(1.0, tags=tags))
        for name, (pan, tilt) in self.jog.items():
            layers.append(statemod.raw_pose_layer({name: (pan, tilt)}, intensity=1.0))
        self.override_layers = layers

    def _public_hand(self) -> dict:
        """What the operator has set by hand."""
        return {
            "master": round(self.master, 3),
            "blackout": self.blackout,
            "panicked": self.runner.panicked,
            "flashing": sorted(self.flashing),
            "color_overrides": {k: list(v) for k, v in self.color_overrides.items()},
            "white_overrides": dict(self.white_overrides),
            "level_overrides": dict(self.level_overrides),
        }


def _target(m: dict) -> str:
    """The `target` of a color, level or flash: a tag, a fixture name, or "all".

    Declared text (`_TARGET`) because it becomes a dict key and a set member
    that the snapshot publishes. A number there broke the flash set's sorted()
    on every broadcast, and a NaN or infinite one is a key JSON cannot carry --
    either way every console stopped updating until someone cleared it.
    """
    target = m.get("target")
    return "all" if target is None else target
