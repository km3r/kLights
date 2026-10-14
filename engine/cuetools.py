"""
The cue list as tools: read it and edit it in conversation.

`mcp/klights_mcp.py` exposes these to an assistant, beside the look tools
(`looktools`); they are plain functions returning plain dicts. Like those,
this module only translates: `cuestore` decides what a valid cue is and writes
the file -- the same functions the engine runs for Studio's Cues page -- so
"is this cue list legal" has one answer whether it is asked in chat or clicked.

**Writes are dry runs unless asked**, and report what would change. **Writes
quote the rev they read** (`base_rev`, from `list_cues` or `get_cue`): a file
that changed since is refused rather than overwritten.

**A running engine reads these edits by itself.** It watches cues.json, so a
cue saved from here is on the console's GO card a second or two later, with no
restart -- and the list keeps its place: the cue that is current stays current
by name, and nothing on stage changes until a cue is taken.

A cue that names a look the library lacks is written all the same, and said
(`flags`): a cue list outlives any one port of the library.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

from . import config as configmod
from . import cues as cuesmod
from . import cuestore
from . import library as libmod
from . import patch as patchmod
from . import rig as rigmod
from . import showfiles


def _error(text: str, **extra) -> dict:
    return {"ok": False, "errors": [text], **extra}


def _event_dir(event: str) -> Path:
    path = Path(event)
    if path.is_dir():
        return path
    return Path(showfiles.REPO) / "events" / event


class _List:
    """One read of an event's cue list, and of what a cue is judged against:
    the look library and the rig's fixture groups."""

    def __init__(self, event: str):
        self.event = event
        self.dir = _event_dir(event)
        if not self.dir.is_dir():
            raise ValueError(f"no event {event!r}: {self.dir} is not a folder")
        self.cues, self.rev = cuestore.read_list(self.dir)
        # What a cue is flagged against. Neither is needed to edit the list:
        # an event whose looks do not load still has cues, and says so.
        self.notes: list[str] = []
        try:
            self.by_name = {e.name: e for e in libmod.load_library(self.dir)}
        except (ValueError, configmod.ConfigError) as exc:
            self.by_name = {}
            self.notes.append(f"the look library does not load, so every look "
                              f"reads as missing: {str(exc).splitlines()[0]}")
        try:
            self.groups: Optional[set[str]] = cuestore.known_groups(
                rigmod.load_rig(self.dir))
        except (ValueError, FileNotFoundError, configmod.ConfigError):
            self.groups = None              # no rig to ask: nothing to flag
        self.held = patchmod.held_by(str(self.dir))

    def flags(self, cue: Any) -> list[str]:
        return cuestore.flags(cue, self.by_name, self.groups)

    def scope(self) -> dict:
        out: dict[str, Any] = {"event": self.event, "file": cuestore.FILE,
                               "rev": self.rev or ""}
        if self.notes:
            out["note"] = " ".join(self.notes)
        if self.held is not None:
            out["engine"] = (f"{self.held} is running this event: it watches "
                             f"{cuestore.FILE} and reads an edit by itself, "
                             f"keeping its place in the list")
        return out


def _open(event: str) -> "_List | dict":
    try:
        return _List(event)
    except (ValueError, configmod.ConfigError) as exc:
        return _error(str(exc))


def _line(index: int, cue: cuesmod.Cue, flags: list[str]) -> dict:
    """A cue in a line: where it is, what it puts in each slot, how it comes
    in and whether it leaves by itself -- and only what it sets beyond that."""
    full = cuestore.cue_dict(cue)
    line: dict[str, Any] = {"index": index, "name": cue.name}
    for key in cuestore.SLOTS:
        if full[key]:
            line[key] = full[key]
    line["fade"], line["hold"] = full["fade"], full["hold"]
    for key in ("speed", "master", "macro", "rates", "params"):
        if full[key] is not None:
            line[key] = full[key]
    if flags:
        line["flags"] = flags
    return line


# -- reading ------------------------------------------------------------------

def list_cues(event: str = "despacio") -> dict:
    """The cue list in running order, a line each, with `get_cue` for the rest."""
    lst = _open(event)
    if isinstance(lst, dict):
        return lst
    if lst.cues is None:
        return {"ok": True, **lst.scope(), "name": None, "cues": [],
                "hint": f'this event has no {cuestore.FILE} yet: put_cue with '
                        f'base_rev="" makes it'}
    cues = [_line(i, c, lst.flags(cuestore.cue_dict(c)))
            for i, c in enumerate(lst.cues.cues)]
    return {"ok": True, **lst.scope(), "name": lst.cues.name, "cues": cues}


def get_cue(name: str, event: str = "despacio") -> dict:
    """One cue in full: every field, where it is in the order, and what is
    wrong with it."""
    lst = _open(event)
    if isinstance(lst, dict):
        return lst
    cues = lst.cues.cues if lst.cues is not None else []
    index = next((i for i, c in enumerate(cues) if c.name == name), None)
    if index is None:
        return _error(f"no cue called {name!r} in {event!r}", **lst.scope())
    return {"ok": True, **lst.scope(), "index": index, "count": len(cues),
            "cue": cuestore.describe(cues[index], lst.by_name, lst.groups)}


# -- writing ------------------------------------------------------------------

def _result(lst: _List, write: bool, base_rev: Optional[str], run) -> dict:
    """One edit: a dry run against the file as it is, or -- with `write` and
    the rev that was read -- the edit itself. `run(base_rev, dry_run)` does it
    and returns what to report."""
    out: dict[str, Any] = {"ok": True, **lst.scope(), "current_rev": lst.rev or "",
                           "dry_run": not write, "written": False}
    if write and base_rev is None:
        return {**out, "ok": False, "errors": [
            f"base_rev is required to write: {(lst.rev or '')!r}, the rev you "
            f"read" + ("" if lst.rev else ' (there is no file yet, so "")')]}
    try:
        out.update(run(base_rev if write else (lst.rev or ""), not write))
    except (ValueError, configmod.ConfigError) as exc:
        return {**out, "ok": False, "errors": [str(exc)]}
    out["rev"] = out.get("rev") or ""
    if write:
        out["written"] = True
    else:
        out["hint"] = f'call again with write=true and base_rev="{lst.rev or ""}"'
    return out


def put_cue(cue: Any, was: Optional[str] = None, at: Optional[int] = None,
            base_rev: Optional[str] = None, write: bool = False,
            event: str = "despacio") -> dict:
    """Check a cue and, with `write`, save it: a new one at place `at` (last,
    when not said), or (`was`) a change to the cue of that name -- a rename
    when the name differs, and a move when `at` is given."""
    lst = _open(event)
    if isinstance(lst, dict):
        return lst
    try:
        clean = cuestore.clean_cue(cue)
    except ValueError as exc:
        return _error(str(exc), **lst.scope())

    def run(base: Optional[str], dry_run: bool) -> dict:
        done = cuestore.save(lst.dir, clean, was, base, at=at, dry_run=dry_run)
        if was is not None and was != clean["name"]:
            done["renamed"] = {"from": was, "to": clean["name"]}
        return done

    out = _result(lst, write, base_rev, run)
    return {**out, "cue": clean, "warnings": lst.flags(clean)}


def delete_cue(name: str, base_rev: Optional[str] = None, write: bool = False,
               event: str = "despacio") -> dict:
    """Delete a cue. Not the last one: a cue list cannot be empty."""
    lst = _open(event)
    if isinstance(lst, dict):
        return lst

    def run(base: Optional[str], dry_run: bool) -> dict:
        return {**cuestore.delete(lst.dir, name, base, dry_run=dry_run),
                "deleted": name}

    return _result(lst, write, base_rev, run)
