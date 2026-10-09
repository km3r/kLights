"""
The look library as tools: read it and edit it in conversation.

`mcp/klights_mcp.py` exposes these to an assistant, as `showtools` is exposed
for the show folder; they are plain functions returning plain dicts. Like
`showtools`, this module only translates: `lookstore` decides what a valid look
is, what names one, and writes the file -- the same functions the engine runs
for Studio's Looks page -- so "may this look be deleted" has one answer whether
it is asked in chat or clicked.

**Writes are dry runs unless asked**, and report what would change. **Writes
quote the rev they read** (`base_rev`, from `list_looks` or `get_look`): a
file that changed since is refused rather than overwritten.

**A running engine reads these edits by itself.** It watches the event's look
files, so a look saved from here is on the console's picker a second or two
later, with no restart. The one edit refused while an engine runs is a RENAME:
it has to move the presets and the cue list the engine is holding in memory,
which only the engine can do -- rename on Studio's Looks page instead.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Optional

from . import config as configmod
from . import library as libmod
from . import lookstore
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


def _json_list(path: Path, key: str) -> list:
    """cues.json's cues or presets.json's presets, as plain objects: what
    names a look is asked of the file, since nothing here is running it."""
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return []
    entries = doc.get(key) if isinstance(doc, dict) else None
    return [e for e in entries if isinstance(e, dict)] if isinstance(entries, list) else []


class _Library:
    """One read of an event's looks and of everything that names them."""

    def __init__(self, event: str, show_dir: Optional[str] = None):
        self.event = event
        self.dir = _event_dir(event)
        if not self.dir.is_dir():
            raise ValueError(f"no event {event!r}: {self.dir} is not a folder")
        self.rev = lookstore.file_rev(self.dir)
        self.entries = libmod.load_library(self.dir)
        self.by_name = {e.name: e for e in self.entries}
        self.cues = _json_list(self.dir / "cues.json", "cues")
        self.presets = _json_list(self.dir / "presets.json", "presets")
        # The show folder, if there is one to ask: without it a look's uses
        # are the event's own, and the answer says so.
        self.root = showfiles.resolve_show_dir(show_dir)
        self.folder = showfiles.load_folder(self.root) if self.root is not None else None
        self.use = lookstore.usage(self.cues, self.presets, self.folder, self.entries)
        self.held = patchmod.held_by(str(self.dir))

    def describe(self, entry: libmod.LibraryEntry) -> dict:
        return lookstore.describe(entry, libmod.build_look(entry).manual_only,
                                  self.use.get(entry.name))

    def scope(self) -> dict:
        out: dict[str, Any] = {"event": self.event, "file": lookstore.FILE,
                               "rev": self.rev or ""}
        out["show_dir"] = str(self.root) if self.root is not None else None
        if self.root is None:
            out["note"] = ("no show folder was given or configured, so routines "
                           "and timelines that play a look are not counted")
        if self.held is not None:
            out["engine"] = (f"{self.held} is running this event: it watches the "
                             f"look files and reads an edit by itself")
        return out


def _open(event: str, show_dir: Optional[str]) -> "_Library | dict":
    try:
        return _Library(event, show_dir)
    except (ValueError, configmod.ConfigError) as exc:
        return _error(str(exc))


# -- reading ------------------------------------------------------------------

def list_looks(event: str = "despacio", show_dir: Optional[str] = None) -> dict:
    """Every look, a line each: enough to pick one, with `get_look` for the rest."""
    lib = _open(event, show_dir)
    if isinstance(lib, dict):
        return lib
    looks = []
    for entry in lib.entries:
        full = lib.describe(entry)
        line: dict[str, Any] = {"name": entry.name, "slot": entry.slot,
                                "source": full["source"], "groups": full["groups"]}
        if entry.is_parametric:
            line["block"] = entry.block
            line["args"] = full["args"]
        else:
            line["what"] = full["stored"]["what"]
            if "block_version" in full:
                line["block_version"] = full["block_version"]["block"]
        if entry.retired:
            line["hidden"] = True
        if entry.step_of:
            line["step_of"] = entry.step_of
        used = lookstore.uses(lib.use.get(entry.name))
        if used:
            line["used_by"] = used
        looks.append(line)
    return {"ok": True, **lib.scope(), "looks": looks}


def get_look(name: str, event: str = "despacio",
             show_dir: Optional[str] = None) -> dict:
    lib = _open(event, show_dir)
    if isinstance(lib, dict):
        return lib
    entry = lib.by_name.get(name)
    if entry is None:
        return _error(f"no look named {name!r} in {event!r}")
    return {"ok": True, **lib.scope(), "look": lib.describe(entry)}


# -- writing ------------------------------------------------------------------

def _result(lib: _Library, write: bool, base_rev: Optional[str], run) -> dict:
    """One edit: a dry run against the file as it is, or -- with `write` and
    the rev that was read -- the edit itself. `run(base_rev, dry_run)` does it
    and returns what to report."""
    out: dict[str, Any] = {"ok": True, **lib.scope(), "current_rev": lib.rev or "",
                           "dry_run": not write, "written": False}
    if write and base_rev is None:
        return {**out, "ok": False, "errors": [
            f"base_rev is required to write: {(lib.rev or '')!r}, the rev you "
            f"read" + ("" if lib.rev else ' (there is no file yet, so "")')]}
    try:
        out.update(run(base_rev if write else (lib.rev or ""), not write))
    except (ValueError, configmod.ConfigError) as exc:
        return {**out, "ok": False, "errors": [str(exc)]}
    if write:
        out["written"] = True
    else:
        out["hint"] = f'call again with write=true and base_rev="{lib.rev or ""}"'
    return out


def put_look(look: Any, was: Optional[str] = None, base_rev: Optional[str] = None,
             write: bool = False, event: str = "despacio",
             show_dir: Optional[str] = None) -> dict:
    """Check a block look and, with `write`, save it: a new one, or (`was`)
    a change to the look of that name -- a rename when the name differs."""
    lib = _open(event, show_dir)
    if isinstance(lib, dict):
        return lib
    try:
        clean = lookstore.clean_look(look)
    except ValueError as exc:
        return _error(str(exc), **lib.scope())
    name = clean["name"]
    renaming = was is not None and was != name
    if renaming and write and lib.held is not None:
        return _error(
            f"{lib.held} is running this event. A rename has to move the "
            f"presets and the cue list the engine holds in memory, which only "
            f"the engine can do: rename {was!r} on Studio's Looks page, or stop "
            f"the show first", **lib.scope())
    warnings: list[str] = []
    try:
        missing = lookstore.unknown_groups(clean, rigmod.load_rig(lib.dir))
    except (ValueError, FileNotFoundError, configmod.ConfigError):
        missing = []                        # no rig to ask: nothing to warn of
    if missing:
        warnings.append(f"nothing on this rig is tagged "
                        f"{', '.join(repr(g) for g in missing)}: until something "
                        f"is, {name!r} moves and lights nothing")

    def run(base: Optional[str], dry_run: bool) -> dict:
        if not renaming:
            rev = lookstore.save(lib.dir, clean, was, base, dry_run=dry_run)
            return {"rev": rev or ""}
        moved = lookstore.rename(
            lib.dir, clean, was, base, presets=True, dry_run=dry_run,
            show=(lib.root, lib.folder) if lib.folder is not None else None)
        return {"rev": moved["rev"] or "",
                "renamed": {"from": was, "to": name, "files": moved["written"],
                            "cues": moved["cues"], "presets": moved["presets"]}}

    out = _result(lib, write, base_rev, run)
    return {**out, "look": clean, "warnings": warnings}


def delete_look(name: str, base_rev: Optional[str] = None, write: bool = False,
                event: str = "despacio", show_dir: Optional[str] = None) -> dict:
    """Delete a block look nothing names; refused, with where, if anything
    does. A stored look cannot be deleted: hide it."""
    lib = _open(event, show_dir)
    if isinstance(lib, dict):
        return lib
    entry = lib.by_name.get(name)
    # One that took over a stored look hands the name back to it, so whatever
    # names it still has a look to play.
    if entry is not None and entry.is_parametric and not entry.supersedes:
        use = lib.use.get(name)
        named = lookstore.uses(use)
        if named:
            return _error(f"{name!r} is still used by {'; '.join(named)}: take "
                          f"it out of those first", **lib.scope())
        if use and use["hides"]:
            return _error(f"{', '.join(use['hides'])} is hidden in favour of "
                          f"{name!r}: show it again, or point it at another "
                          f"look, first", **lib.scope())

    def run(base: Optional[str], dry_run: bool) -> dict:
        return {"rev": lookstore.delete(lib.dir, name, base, dry_run=dry_run) or "",
                "deleted": name,
                **({"restores": f"the stored look {name!r} takes the name back"}
                   if entry is not None and entry.supersedes else {})}

    return _result(lib, write, base_rev, run)


def hide_look(name: str, hidden: bool = True, replaced_by: Optional[str] = None,
              note: Optional[str] = None, base_rev: Optional[str] = None,
              write: bool = False, event: str = "despacio") -> dict:
    """Hide any look from the picker -- with what covers it now, and why -- or
    show it again. Hidden is not removed: it still plays for what names it."""
    lib = _open(event, None)
    if isinstance(lib, dict):
        return lib

    def run(base: Optional[str], dry_run: bool) -> dict:
        rev = lookstore.hide(lib.dir, name, bool(hidden), base, lib.by_name,
                             replaced_by, note, dry_run=dry_run)
        return {"rev": rev or "", "look": name, "hidden": bool(hidden)}

    return _result(lib, write, base_rev, run)
