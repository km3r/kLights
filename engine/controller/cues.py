"""
The cue list, cues.json: the night as an ordered list, walked by GO. A cue
fills the slots a preset does and crossfades over its own fade; a cue with a
hold advances by itself.

The list is also edited from Studio's Cues page, through `cuestore`. Every
edit is an edit to cues.json: written on the worker, quoting the rev the
editor read, then read again there and installed here. Installing is NOT
taking: the list keeps its place by name, and what is on stage stays as it is
until a cue is taken again.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from .. import config as configmod
from .. import cuestore
from .. import cues as cuesmod
from .. import showlibrary
from .. import state as statemod
from ..commands import CONFIGURE, OPERATE, command
from .common import S, _BASE_REV, _NAME

# What an edit to the cue list answers with: cues.json's rev now, and where the
# cue it changed sits.
_CUE_EDITED = {
    "rev": S(str, required=True, fix="cues.json's rev now"),
    "name": S(str), "warnings": S(list, each=S(str)), "deleted": S(str),
    "index": S(int, fix="where the cue is now in the running order, 0 first"),
    "count": S(int, fix="how many cues the list holds")}


class Cues:
    """The cue list, and taking a cue."""

    def _init_cues(self) -> None:
        # The cue list, if this event has one. Optional: a show driven entirely
        # by hand off the look picker is still a show, and the despacio night
        # was one for its whole first run.
        #
        # Read with the rev a save of it must quote, from the one read
        # (`cuestore.read_list`), and watched as the look files are: an edit
        # by hand, by the MCP server or by a look being renamed is read
        # without a restart, and the list keeps its place (`_install_cues`).
        self.cues: Optional[cuesmod.CueList] = None
        self.cues_rev: Optional[str] = None
        # Why cues.json on disk is not the list that is running, when it would
        # not load. None while they agree.
        self.cues_problem: Optional[str] = None
        cues_seen = cuestore.signature(self.event_dir)
        try:
            self.cues, self.cues_rev = cuestore.read_list(self.event_dir)
        except configmod.ConfigError as exc:
            self.cues_problem = str(exc)
            self.notices.append(f"cues.json not loaded: {exc}")
        self.cue_watcher = showlibrary.Watcher(
            self.event_dir, on_change=self.reload_cues,
            report=lambda text: self.submit_call(lambda: self.note(text)),
            scan=cuestore.signature, what="cue file", seen=cues_seen)

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

    def cues_public(self) -> dict:
        """The cue list, for `GET /api/cues`: every cue in full, in running
        order, with what is wrong with each (`cuestore.flags`).

        Built on the caller's thread (an HTTP one), from references the output
        thread only ever REBINDS -- the cue list, the library -- so it reads
        one whole list or the next, never half of each.
        """
        cues, by_name = self.cues, self.by_name
        groups = cuestore.known_groups(self.rig)
        return {
            "event": self.rig.name, "file": cuestore.FILE,
            # What a save must quote: the file this list was READ from. None
            # when the event has no cue list yet.
            "rev": self.cues_rev,
            # The file has changed since and the engine has not read it yet
            # (it watches, so that is a second or two) -- or it does not load,
            # and `problem` says why. Until they agree a save is refused.
            "stale": (self.cues_problem is not None
                      or cuestore.file_rev(self.event_dir) != self.cues_rev),
            "problem": self.cues_problem,
            "name": cues.name if cues is not None else None,
            # The lanes a slot can hold a look for: the rig's fixture groups.
            "groups": list(self.rig.tags()),
            "cues": [cuestore.describe(c, by_name, groups)
                     for c in (cues.cues if cues is not None else ())],
        }

    @staticmethod
    def _read_cues(event_dir: Path) -> tuple:
        """The cue list as the file is now: (the list or None, the rev a save
        must quote, what the watcher will see). On the worker. The signature
        is read BEFORE the file: one that changes in between then reads as
        out of date, and is read again."""
        seen = cuestore.signature(event_dir)
        cue_list, rev = cuestore.read_list(event_dir)
        return cue_list, rev, seen

    def _install_cues(self, cue_list: Optional[cuesmod.CueList],
                      rev: Optional[str], seen: Optional[tuple] = None, *,
                      renamed: Optional[tuple[str, str]] = None) -> None:
        """Make a freshly read cue list the one that is running, WITHOUT
        losing the place in it.

        On the output thread, at a frame boundary. The cue that is current
        stays current by name, wherever it moved to and whatever it was
        renamed (`cuestore.carry`), and its hold keeps counting from when it
        was taken. If it was deleted, the list sits on the nearest cue before
        it that is left, so GO takes what would have come next -- and a hold
        on that one counts from now, not from a GO that was for another cue.

        Nothing on stage changes: a cue that was edited is on stage as it was
        TAKEN, until it is taken again.

        A new list is bound, never the running one edited: the snapshot and
        `/api/cues` read it from other threads.
        """
        old = self.cues
        if cue_list is not None:
            index, lost = cuestore.carry(old, cue_list.cues, renamed)
            entered = (old.entered_beat if old is not None and lost is None
                       else self.ctx.beat)
            cue_list = cuesmod.CueList(name=cue_list.name, cues=cue_list.cues,
                                       index=index,
                                       entered_beat=entered if index >= 0 else 0.0)
            if lost is not None:
                after = cue_list.next
                self.note(f"cue {lost!r} was the current one and is no longer "
                          f"in the list: " + (
                              f"the list is at {cue_list.current.name!r}"
                              if cue_list.current is not None
                              else "the list is back before its first cue")
                          + (f", and GO takes {after.name!r}" if after is not None
                             else ", which is its end")
                          + ". What is on stage has not changed")
        self.cues = cue_list
        self.cues_rev = rev
        self.cues_problem = None
        if seen is not None:
            # What this load read, so the watcher does not read it again.
            self.cue_watcher.seen = seen

    def _cue_edit(self, label: str, work: Callable[[], Any], said: str, *,
                  renamed: Optional[tuple[str, str]] = None,
                  data: Optional[dict] = None) -> object:
        """One edit to the cue list: `work` writes the file on the worker
        (what it returns rides back in the reply), the list is read again
        there, and installed here."""
        event_dir = self.event_dir

        def job():
            info = work()
            return self._read_cues(event_dir), info if isinstance(info, dict) else {}

        def then(result, respond):
            loaded, info = result
            try:
                self._install_cues(*loaded, renamed=renamed)
            except Exception as exc:                        # noqa: BLE001
                # Written and not installed: said to whoever asked, rather
                # than leaving them to time out. The watcher sees the file is
                # not the list that is running and reads it again by itself.
                self.note(f"{label} wrote the file, but the engine could not "
                          f"take it up: {exc}")
                respond(False, error=f"{cuestore.FILE} was written, but the "
                                     f"engine could not take it up ({exc}). It "
                                     f"reads the file again by itself; check "
                                     f"the cue, then the notices")
                return
            respond(True, {**(data or {}), **info, "rev": loaded[1] or ""})
            self.note(said)

        return self._on_worker(label, job, then)

    def reload_cues(self) -> None:
        """cues.json changed on disk: read it, and make it the list. Called
        on the watcher's thread, so the read goes to the worker and the
        install to the output thread -- and a file that does not load changes
        nothing but what `/api/cues` says about it."""
        event_dir = self.event_dir

        def job():
            try:
                return self._read_cues(event_dir)
            except Exception as exc:                        # noqa: BLE001
                return exc

        def done(result) -> None:
            if isinstance(result, Exception):
                self.cues_problem = str(result)
                first = (result.problems[0] if isinstance(result, configmod.ConfigError)
                         and result.problems else str(result)).strip().splitlines()[0]
                self.note("cues.json changed on disk and does not load, so the "
                          "cue list that was running runs on: " + first)
                return
            self._install_cues(*result)
            self.note("cues read again: cues.json changed on disk"
                      if result[0] is not None
                      else "cues.json is gone: this event has no cue list now")

        self.worker.submit(job, done, label="reading the cues again")

    @command("cue_save", tier=CONFIGURE, reply=_CUE_EDITED, args={
        "cue": S(dict, required=True),
        "was": S(str, fix="the cue's name as you opened it"),
        "at": S(int, fix="a place in the running order, 0 for first; last when not said"),
        "base_rev": _BASE_REV})
    def _cmd_cue_save(self, m: dict, now: float) -> object:
        """Write one cue: a new one (`was` absent) at place `at` in the
        running order, last when not said -- or a change to the one called
        `was`, which renames it when `cue.name` differs and moves it when `at`
        is given.

        The looks it names are not required to exist: what is wrong with the
        cue rides back as `warnings`, and is on the Cues page.
        """
        cue = cuestore.clean_cue(m["cue"])
        was, base, at = m.get("was"), m["base_rev"], m.get("at")
        name = cue["name"]
        renamed = (was, name) if was is not None and was != name else None
        event_dir = self.event_dir
        warnings = cuestore.flags(cue, self.by_name, cuestore.known_groups(self.rig))
        said = (f"renamed cue {was!r} to {name!r}" if renamed
                else f"saved cue {name!r}" if was is not None
                else f"added cue {name!r}")
        return self._cue_edit(
            f"saving cue {name}",
            lambda: cuestore.save(event_dir, cue, was, base, at=at), said,
            renamed=renamed, data={"name": name, "warnings": warnings})

    @command("cue_copy", tier=CONFIGURE, reply=_CUE_EDITED, args={
        "cue": _NAME, "to": S(str), "base_rev": _BASE_REV})
    def _cmd_cue_copy(self, m: dict, now: float) -> object:
        """Duplicate the cue called `cue` as `to`, straight after it: the
        entry as the file has it, under the new name."""
        name, to, base = m["cue"], m.get("to"), m["base_rev"]
        fresh = cuestore.clean_name(to, "the copy")
        event_dir = self.event_dir
        return self._cue_edit(
            f"duplicating cue {name}",
            lambda: cuestore.duplicate(event_dir, name, fresh, base),
            f"duplicated cue {name!r} as {fresh!r}", data={"name": fresh})

    @command("cue_move", tier=CONFIGURE, reply=_CUE_EDITED, args={
        "cue": _NAME,
        "to": S(int, required=True, fix="a place in the running order, 0 for first"),
        "base_rev": _BASE_REV})
    def _cmd_cue_move(self, m: dict, now: float) -> object:
        """Move the cue called `cue` to place `to` in the running order (0 is
        first). The list keeps its place: the current cue stays current."""
        name, to, base = m["cue"], m["to"], m["base_rev"]
        event_dir = self.event_dir
        return self._cue_edit(
            f"moving cue {name}", lambda: cuestore.move(event_dir, name, to, base),
            f"moved cue {name!r} to {to + 1} in the running order",
            data={"name": name})

    @command("cue_delete", tier=CONFIGURE, reply=_CUE_EDITED,
             args={"cue": _NAME, "base_rev": _BASE_REV})
    def _cmd_cue_delete(self, m: dict, now: float) -> object:
        """Delete the cue called `cue`. Not the last one: a cue list cannot
        be empty. If it was the current cue, the list falls back to the cue
        before it and what is on stage stays."""
        name, base = m["cue"], m["base_rev"]
        event_dir = self.event_dir
        return self._cue_edit(
            f"deleting cue {name}", lambda: cuestore.delete(event_dir, name, base),
            f"deleted cue {name!r}", data={"deleted": name})

    @command("cues_name", tier=CONFIGURE, reply=_CUE_EDITED,
             args={"name": S(str), "base_rev": _BASE_REV})
    def _cmd_cues_name(self, m: dict, now: float) -> object:
        """Name the cue list itself: the title of the console's GO card."""
        fresh = cuestore.clean_name(m.get("name"), "the cue list")
        base, event_dir = m["base_rev"], self.event_dir
        return self._cue_edit(
            "naming the cue list",
            lambda: cuestore.rename_list(event_dir, fresh, base),
            f"the cue list is called {fresh!r}")

    @command("cues_reload", tier=CONFIGURE, reply=_CUE_EDITED)
    def _cmd_cues_reload(self, m: dict, now: float) -> object:
        """Read cues.json again now -- after an edit by hand, or by MCP. A
        file that does not load is said, and the list that is running runs
        on."""
        return self._cue_edit("reading the cues again", lambda: None,
                              "cues read again from the event folder")

    def _public_cues(self) -> dict:
        """Where the cue list is."""
        return {
            "cues": self.cues.status() if self.cues else None,
            # Which cues.json `cues` was read from, for Studio's Cues page
            # over /api/cues. "" when the event has no cue list.
            "cues_rev": self.cues_rev or "",
        }
