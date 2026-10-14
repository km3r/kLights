"""
The cue list, cues.json: the night as an ordered list, walked by GO. A cue
fills the slots a preset does and crossfades over its own fade; a cue with a
hold advances by itself.
"""

from __future__ import annotations

from typing import Optional

from .. import config as configmod
from .. import cues as cuesmod
from .. import state as statemod
from ..commands import OPERATE, command
from .common import S


class Cues:
    """The cue list, and taking a cue."""

    def _init_cues(self) -> None:
        # The cue list, if this event has one. Optional: a show driven entirely
        # by hand off the look picker is still a show, and the despacio night
        # was one for its whole first run.
        self.cues: Optional[cuesmod.CueList] = None
        cue_path = self.event_dir / "cues.json"
        if cue_path.exists():
            try:
                self.cues = cuesmod.load(cue_path)
            except configmod.ConfigError as exc:
                self.notices.append(f"cues.json not loaded: {exc}")

    def _advance_due_cue(self) -> None:
        """Fire a cue's own hold, the same way an operator's GO would.

        `CueList.due()` has existed since the cue list was ported, but nothing
        ever called it -- a cue authored with a hold sat there until someone
        pressed GO by hand, silently contradicting the "auto after N" the UI
        shows next to it. One check at the top of the frame, same place a
        queued GO gets applied, is enough to make the hold real.
        """
        if self.cues is None or not self.cues.due(self.ctx.beat):
            return
        cue = self.cues.go(self.ctx.beat)
        if cue is not None:
            # Without grabbing. Taking a cue by hand is an operator overriding
            # the timeline, and main's rule is that the grab sticks until they
            # release it. A hold running out is nobody's decision: grabbing on
            # it would take all three lanes from a running timeline, and keep
            # them, with no one having touched anything. The slots still
            # change underneath, so the cue is there in any lane already held.
            self.take_cue(cue, grab=False)

    def take_cue(self, cue: "cuesmod.Cue", *, grab: bool = True) -> None:
        """Put a cue on stage, crossfading over its own fade time.

        Fills the same three slots a preset does, because a cue IS a preset
        with somewhere to go next -- a cue list with its own private notion of a
        look would be a second way to say the same thing, and the two would
        drift.

        A look a cue names but the library does not have is skipped with a
        notice rather than aborting the cue. A cue list outlives any one port of
        the library, and taking four of five slots is a recoverable night;
        refusing the cue is not.

        `grab` False changes the slots without taking lanes from a running
        timeline -- for a cue nobody chose this instant (`_advance_due_cue`).
        """
        self._clear_pad()
        for slot in ("color", "level"):
            wanted = getattr(cue, slot)
            if wanted:
                self.slots[slot] = {}
                for group, look in wanted.items():
                    if self.entry(look) is None:
                        self.note(f"cue {cue.name!r}: no look {look!r}")
                        continue
                    self.slots[slot][group] = look
        for look in cue.movement.values():
            if self.entry(look) is None:
                self.note(f"cue {cue.name!r}: no look {look!r}")
                continue
            # hold=True: a cue is an explicit decision, and auto mode advancing
            # off it two bars later would make the cue list look broken.
            self.director.select(look, hold=True)
            break

        if cue.speed is not None:
            self.clock.set_speed(cue.speed, self.runner.now())
        if cue.master is not None:
            self.master = max(0.0, min(1.0, cue.master))
        if cue.macro is not None:
            self._cmd_macro(dict(cue.macro), 0.0)
        if cue.rates is not None:
            self.apply_rates(cue.rates)
        self.apply_look_params(
            cue.params,
            self.looks_named({"movement": cue.movement, "color": cue.color,
                              "level": cue.level}),
            f"cue {cue.name!r}")

        if grab:
            self._grab(statemod.SLOTS)
        self._recompose(fade_beats=cue.fade)
        self.note(f"cue {self.cues.index + 1}/{len(self.cues.cues)}: "
                  f"{cue.name}" + (f" (fade {cue.fade:g} beats)"
                                   if cue.fade else " (cut)"))

    @command("go", tier=OPERATE)
    def _cmd_go(self, m: dict, now: float) -> None:
        if self.cues is None:
            raise ValueError("this event has no cues.json")
        cue = self.cues.go(self.ctx.beat)
        if cue is None:
            self.note("end of the cue list -- it does not wrap")
            return
        self.take_cue(cue)

    @command("cue_back", tier=OPERATE)
    def _cmd_cue_back(self, m: dict, now: float) -> None:
        if self.cues is None:
            raise ValueError("this event has no cues.json")
        cue = self.cues.back(self.ctx.beat)
        if cue is None:
            self.note("already at the first cue")
            return
        self.take_cue(cue)

    @command("cue", tier=OPERATE, args={"index": S(int, required=True)})
    def _cmd_cue(self, m: dict, now: float) -> None:
        """Jump straight to a cue. Fades like any other take."""
        if self.cues is None:
            raise ValueError("this event has no cues.json")
        self.take_cue(self.cues.jump(int(m["index"]), self.ctx.beat))

    @command("cue_reset", tier=OPERATE)
    def _cmd_cue_reset(self, m: dict, now: float) -> None:
        if self.cues is None:
            raise ValueError("this event has no cues.json")
        self.cues.reset()
        self.note("cue list rewound -- nothing taken")

    def _public_cues(self) -> dict:
        """Where the cue list is."""
        return {
            "cues": self.cues.status() if self.cues else None,
        }
