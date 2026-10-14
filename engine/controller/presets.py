"""
The preset pads, saved to presets.json: a picture of all three slots and the
tempo feel at a fixed place on a page of eight. A pad can also carry a routine
from the show folder (milestone 2), which lands whole on the clock's next
downbeat.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

from .. import clock as clockmod
from .. import config as configmod
from .. import program as programmod
from .. import state as statemod
from .. import templates as templatesmod
from ..commands import OPERATE, command
from .common import S, _NAME, _TAGS, _finite

if TYPE_CHECKING:                   # the class these helpers are given
    from . import ShowController


BANK_SIZE = configmod.BANK_SIZE


class Presets:
    """Presets, and routines on pads."""

    def _init_presets(self) -> None:
        self.presets = load_presets(self.event_dir)
        # Routines on preset pads (milestone 2): the one playing, the one
        # waiting for its downbeat, and every routine pad built for this rig.
        self.pad: Optional[dict] = None
        self._pad_pending: Optional[dict] = None
        # A routine that could not be built is kept here as its exception:
        # the pad then lands its looks alone, rather than waiting for ever.
        self._pad_programs: dict[str, Any] = {}
        self._pad_gen = 0               # bumped when the rig or folder changes

    @staticmethod
    def _pad_key(routine: dict) -> str:
        return templatesmod.pick_key({"routine": routine.get("id"),
                                      "variation": routine.get("variation"),
                                      "params": routine.get("params") or None})

    def _compile_pad(self, preset: dict) -> None:
        """Build a routine pad's program for this rig, on the worker."""
        routine = preset.get("routine") or {}
        library = self.show_library
        if not routine or library is None:
            return
        key = self._pad_key(routine)
        if key in self._pad_programs:
            return
        pick = {"routine": routine.get("id"), "variation": routine.get("variation"),
                "params": routine.get("params")}
        routines, rigging, name = library.folder.routines, self._rigging(), preset["name"]
        gen = self._pad_gen

        def build():
            # Returned, not raised: the worker never calls `done` for a job
            # that raised, and a pad waiting on it would wait for ever.
            try:
                if pick["routine"] not in routines:
                    raise ValueError(f"routine {pick['routine']!r} is not in routines/")
                return programmod.compile(templatesmod.pick_timeline({}, pick),
                                          routines, rigging, f"preset {name!r}")
            except Exception as exc:                        # noqa: BLE001
                return exc

        def done(prog) -> None:
            if gen != self._pad_gen:
                return                  # built for a rig or load since replaced
            self._pad_programs[key] = prog
            if isinstance(prog, Exception):
                self.note(f"preset {name!r}: its routine could not be built "
                          f"({prog}); the pad applies its looks")
            elif prog.problems:
                self.note(f"preset {name!r}: {prog.problems[0]}")

        self.worker.submit(build, done, label=f"building preset {name!r}'s routine")

    def _compile_pads(self) -> None:
        self._pad_gen += 1
        self._pad_programs = {}
        for preset in self.presets:
            if preset.get("routine"):
                self._compile_pad(preset)

    def _clear_pad(self) -> None:
        self.pad = None
        self._pad_pending = None

    def _pad_frame(self, now: float) -> None:
        """A pressed routine pad lands on its downbeat (decided with the
        user): the whole picture at once, its looks and its routine, from
        beat 0 of the routine."""
        pending = self._pad_pending
        if pending is None:
            return
        beat = self.clock.beat(now)
        if beat < pending["start"] - 1e-9:
            return
        prog = self._pad_programs.get(pending["key"])
        if prog is None:
            # Not built yet (just saved, or the rig just changed): the next
            # downbeat instead, rather than starting it mid-bar.
            pending["start"] = templatesmod.next_downbeat(beat + 1e-6)
            return
        self._pad_pending = None
        self._apply_preset_looks(pending["preset"], now)
        if isinstance(prog, Exception):
            # Its routine could not be built: the looks alone, on the same
            # downbeat, and said -- each press, as each press is a choice.
            self.note(f"preset {pending['preset']['name']!r}: its routine could "
                      f"not be built ({prog}); applied its looks")
            return
        self.pad = {"name": pending["preset"]["name"],
                    "routine": pending["preset"]["routine"].get("id"),
                    "start": pending["start"], "program": prog}

    @command("preset_save", tier=OPERATE, args={
        "name": _NAME,
        "bank": S(int),
        "cell": S(int),
        "tags": _TAGS,
        "routine": S(dict, fix='{"id": "<routine id>", "variation"?, "params"?}, '
                               'or null for a pad of looks only')})
    def _cmd_preset_save(self, m: dict, now: float) -> None:
        """Snapshot all three slots plus the tempo feel under one name.

        A preset is the thing a slot-based console loses: with color, movement
        and level independent you can build a picture in three taps, and then
        have no way to get back to it. Saved to the event so it survives a
        restart -- a preset that lives in memory is a preset you rebuild.

        Saving over an existing name KEEPS ITS CELL. Re-recording a preset is
        the most common thing anyone does with one, and having it jump to the
        end of the grid every time would destroy the only thing a fixed cell is
        for.
        """
        name = str(m["name"]).strip()[:40]
        if not name:
            raise ValueError("a preset needs a name")
        previous = next((p for p in self.presets if p["name"] == name), None)
        others = [p for p in self.presets if p["name"] != name]
        where = _cell_request(m)
        if where is None and previous is not None:
            where = (previous["bank"], previous["cell"])
        if where is None:
            where = free_cell(others)
        elif any(p["bank"] == where[0] and p["cell"] == where[1]
                 for p in others):
            raise ValueError(f"bank {where[0]} cell {where[1]} is already taken")
        tags = m.get("tags")
        # Absent keeps the pad's routine (re-recording its looks is the
        # common case); null clears it, making it a pad of looks again.
        routine = (self._preset_routine(m["routine"]) if "routine" in m
                   else previous.get("routine") if previous is not None else None)
        captured = self.capture_look_params(self.looks_named(self.selection))
        # Built whole and rebound once. Appending to the live list and then
        # sorting it in place is what let the broadcast thread serialise an
        # EMPTY preset list -- CPython empties a list for the duration of
        # `sort()`, so a console mid-save could show "Presets - 0".
        self.presets = sorted(others + [{
            "name": name, **self.selection,
            "speed": round(self.clock.speed, 3),
            "master": round(self.master, 3),
            # Stored only when something has been dialled off 1x. A preset that
            # carried `{1, 1, 1}` would silently RESET rates the operator set
            # after saving it, which is the same trap as a cue that always
            # restores a macro -- absent has to mean "leave it alone".
            **({"rates": self.director.phases.status()}
               if self.director.phases.changed else {}),
            # Per-routine tuning for the looks this preset names -- see
            # `capture_look_params` for why this one is NOT stored only when it
            # differs from the authored values, unlike `rates` above.
            **({"params": captured} if captured is not None else {}),
            "bank": where[0], "cell": where[1],
            "tags": [str(t) for t in tags] if tags is not None
                    else list(previous.get("tags", [])) if previous else [],
            **({"routine": routine} if routine else {}),
        }], key=_at)
        save_presets(self.event_dir, self.presets)
        if routine:
            self._compile_pad(next(p for p in self.presets if p["name"] == name))
        self.note(f"saved preset {name!r} to {where[0]}.{where[1] + 1}"
                  + (f" with routine {routine['id']!r}" if routine else ""))

    def _preset_routine(self, raw) -> Optional[dict]:
        """A routine for a pad: {id, variation?, params?}, checked against the
        show folder. None for a pad of looks only."""
        if raw is None:
            return None
        library = self.show_library
        if library is None:
            raise ValueError("a routine pad needs a show folder -- start the "
                             "engine with --show-dir")
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            raise ValueError('routine must be {"id": "<routine id>", ...}')
        doc = library.folder.routines.get(raw["id"])
        if doc is None:
            raise ValueError(f"there is no routine {raw['id']!r} in routines/")
        out: dict = {"id": raw["id"]}
        variation = raw.get("variation")
        if variation:
            if variation not in (doc.get("variations") or {}):
                raise ValueError(f"routine {raw['id']!r} has no variation "
                                 f"{variation!r}")
            out["variation"] = variation
        params = raw.get("params")
        if params:
            if not isinstance(params, dict):
                raise ValueError("routine params must be an object")
            out["params"] = dict(params)
        return out

    @command("preset_move", tier=OPERATE, args={
        "name": _NAME,
        "bank": S(int),
        "cell": S(int)})
    def _cmd_preset_move(self, m: dict, now: float) -> None:
        """Put a preset on a different pad, SWAPPING with whatever is there.

        Swap rather than refuse, because the thing anyone actually wants from
        this is to reorder a bank, and refusing on collision would mean shuffling
        via an empty cell to get two presets to trade places.
        """
        name = m["name"]
        preset = next((p for p in self.presets if p["name"] == name), None)
        if preset is None:
            raise KeyError(f"no preset named {name!r}")
        where = _cell_request(m)
        if where is None:
            raise ValueError("preset_move needs a bank and a cell")
        occupant = next((p for p in self.presets
                         if p["bank"] == where[0] and p["cell"] == where[1]), None)
        if occupant is preset:
            return
        # Replaced, not edited in place: the snapshot thread walks these dicts
        # while this runs, and editing two of them in sequence would let it
        # serialise a moment where both claim the same pad.
        moved = {**preset, "bank": where[0], "cell": where[1]}
        swapped = (None if occupant is None
                   else {**occupant, "bank": preset["bank"],
                         "cell": preset["cell"]})
        self.presets = sorted(
            [moved if p is preset else swapped if p is occupant else p
             for p in self.presets], key=_at)
        save_presets(self.event_dir, self.presets)
        self.note(f"moved preset {name!r} to {where[0]}.{where[1] + 1}")

    @command("preset_tag", tier=OPERATE, args={"name": _NAME, "tags": _TAGS})
    def _cmd_preset_tag(self, m: dict, now: float) -> None:
        name = m["name"]
        preset = next((p for p in self.presets if p["name"] == name), None)
        if preset is None:
            raise KeyError(f"no preset named {name!r}")
        tags = [str(t).strip()[:24] for t in m.get("tags", []) if str(t).strip()]
        self.presets = [{**p, "tags": tags} if p is preset else p
                        for p in self.presets]
        save_presets(self.event_dir, self.presets)

    @command("preset_apply", tier=OPERATE, args={"name": _NAME})
    def _cmd_preset_apply(self, m: dict, now: float) -> None:
        name = m["name"]
        preset = next((p for p in self.presets if p["name"] == name), None)
        if preset is None:
            raise KeyError(f"no preset named {name!r}")
        self._preset_numbers(preset)               # refused before anything changes
        self._clear_pad()
        routine = preset.get("routine")
        if routine and self.show_library is not None:
            # On the next downbeat (decided with the user): the whole picture
            # lands then, so the routine's first bar is the music's.
            key = self._pad_key(routine)
            if key not in self._pad_programs:
                self._compile_pad(preset)
            self._pad_pending = {
                "preset": preset, "key": key,
                "start": templatesmod.next_downbeat(self.clock.beat(now))}
            return
        if routine:
            self.note(f"preset {name!r}: its routine needs a show folder; "
                      f"applying its looks")
        self._apply_preset_looks(preset, now)

    @staticmethod
    def _preset_numbers(preset: dict) -> tuple[Optional[float], Optional[float]]:
        """A preset's speed and master, refused if the clock or the master
        would refuse them.

        presets.json is hand-editable and not schema-checked. Checked before
        anything changes: a speed the clock refuses used to raise with the
        slots already replaced, leaving the rig half on the new preset; and a
        NaN master clamped to full.
        """
        name = preset["name"]
        speed = float(preset["speed"]) if preset.get("speed") else None
        if speed is not None and not clockmod.MIN_SPEED <= speed <= clockmod.MAX_SPEED:
            raise ValueError(f"preset {name!r}: speed {speed!r} is outside "
                             f"{clockmod.MIN_SPEED:g}-{clockmod.MAX_SPEED:g}")
        master = (None if preset.get("master") is None
                  else _finite(preset["master"], f"the master of preset {name!r}"))
        return speed, master

    def _apply_preset_looks(self, preset: dict, now: float) -> None:
        name = preset["name"]
        speed, master = self._preset_numbers(preset)
        # A preset naming a look that has since been re-ported away applies the
        # rest rather than failing whole -- a preset is a shortcut, and half a
        # shortcut beats an error message mid-set.
        missing = []
        for slot in ("color", "level"):
            self.slots[slot] = {}
            for group, value in (preset.get(slot) or {}).items():
                if value in self.by_name:
                    self.slots[slot][group] = value
                elif value:
                    missing.append(value)
        movement = next(iter((preset.get("movement") or {}).values()), None)
        if movement in self.by_name:
            self.director.select(movement, hold=True)
        elif movement:
            missing.append(movement)
        if missing:
            self.note(f"preset {name!r}: {', '.join(missing)} no longer exist(s)")
        self.apply_look_params(preset.get("params"), self.looks_named(preset),
                               f"preset {name!r}")
        if preset.get("rates"):
            self.apply_rates(preset["rates"])
        if speed is not None:
            self.clock.set_speed(speed, now)
        if master is not None:
            self.master = max(0.0, min(1.0, master))
        self.director.held = True
        self._grab(statemod.SLOTS)
        self._recompose()

    @command("preset_delete", tier=OPERATE, args={"name": _NAME})
    def _cmd_preset_delete(self, m: dict, now: float) -> None:
        name = m["name"]
        before = len(self.presets)
        self.presets = [p for p in self.presets if p["name"] != name]
        if len(self.presets) == before:
            raise KeyError(f"no preset named {name!r}")
        save_presets(self.event_dir, self.presets)
        self.note(f"deleted preset {name!r}")

    def _public_presets(self) -> dict:
        """The preset pads, and a routine pad that is up."""
        return {
            "presets": self.presets,
            # How many pages the grid needs. Sent rather than derived, so the UI
            # and the engine cannot disagree about how many banks exist -- and
            # always at least one, because a bank selector that vanishes when
            # the last preset is deleted takes the "save here" pads with it.
            "preset_banks": {
                "size": BANK_SIZE,
                "count": max((p["bank"] for p in self.presets), default=1),
            },
            "pad": _pad_status(self),
        }


def _at(preset: dict) -> tuple[int, int]:
    return (preset["bank"], preset["cell"])


def _cell_request(m: dict) -> Optional[tuple[int, int]]:
    """The (bank, cell) a command asked for, or None if it did not ask."""
    if m.get("bank") is None and m.get("cell") is None:
        return None
    bank, cell = int(m.get("bank", 1)), int(m.get("cell", 0))
    if bank < 1:
        raise ValueError("banks count from 1")
    if not 0 <= cell < BANK_SIZE:
        raise ValueError(f"a bank holds {BANK_SIZE}; cell must be "
                         f"0-{BANK_SIZE - 1}")
    return (bank, cell)


def free_cell(presets: list[dict]) -> tuple[int, int]:
    """The first empty pad, scanning banks upward.

    Never returns a cell that is taken, and never runs out: the last bank grows
    a new one rather than refusing the save. Being told "your preset banks are
    full" while building a show is not a thing a console should ever do.
    """
    taken = {_at(p) for p in presets}
    bank = 1
    while True:
        for cell in range(BANK_SIZE):
            if (bank, cell) not in taken:
                return (bank, cell)
        bank += 1


def arrange_presets(presets: list[dict]) -> list[dict]:
    """Give every preset a real pad, keeping the ones that already have one.

    Runs on load, so a presets.json written before banks existed -- or one
    hand-edited into a collision -- opens as a working grid instead of an error.
    A preset with no home is placed rather than dropped: it is somebody's saved
    picture, and the worst honest outcome is that it is not where they expected.
    """
    out: list[dict] = []
    placed: list[dict] = []
    homeless: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for p in presets:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        p.setdefault("tags", [])
        bank, cell = p.get("bank"), p.get("cell")
        ok = (isinstance(bank, int) and not isinstance(bank, bool) and bank >= 1
              and isinstance(cell, int) and not isinstance(cell, bool)
              and 0 <= cell < BANK_SIZE and (bank, cell) not in seen)
        if ok:
            seen.add((bank, cell))
            placed.append(p)
        else:
            # Includes the collision case: first claim of a pad wins, the
            # second is rehomed. Order in the file decides, which is at least
            # something a person can look at and predict.
            homeless.append(p)
    out.extend(placed)
    for p in homeless:
        p["bank"], p["cell"] = free_cell(out)
        out.append(p)
    out.sort(key=_at)
    return out


def _pad_status(controller: "ShowController") -> Optional[dict]:
    """A routine pad: playing, or waiting for its downbeat."""
    pad, pending = controller.pad, controller._pad_pending
    if pending is not None:
        return {"name": pending["preset"]["name"],
                "routine": pending["preset"]["routine"].get("id"),
                "waiting": True}
    if pad is not None:
        return {"name": pad["name"], "routine": pad["routine"], "waiting": False}
    return None


def load_presets(event_dir: Path) -> list[dict]:
    path = Path(event_dir) / "presets.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("presets", [])
    except (json.JSONDecodeError, OSError):
        # A corrupt presets file must not stop the show starting. Presets are a
        # convenience; the rig is not.
        return []
    return arrange_presets(raw if isinstance(raw, list) else [])


def save_presets(event_dir: Path, presets: list[dict]) -> None:
    configmod.write_json_atomic(Path(event_dir) / "presets.json", {
        # Rewritten in full on every save, so anything not named here is lost --
        # which is how the first version of this quietly stripped the $schema
        # line the moment the UI saved a preset, taking the editor's completion
        # with it.
        "$schema": "../../schemas/presets.schema.json",
        "_comment": [
            "Named combinations of the three slots -- movement, color and",
            "level -- plus the speed and master they were built at.",
            "",
            "Written by the UI. Safe to hand-edit; a preset naming a look",
            "that no longer exists applies the rest and skips that slot.",
            "",
            "'bank' and 'cell' are a fixed position on a page of eight, not a",
            "sort order -- a preset does not move when its neighbours change.",
            "Two presets claiming one pad, or a preset with neither key, is",
            "not an error: the engine rehomes them at load.",
        ],
        "presets": presets,
    })
