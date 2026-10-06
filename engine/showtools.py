"""
The show folder as tools: read, check, edit and explain it in conversation.

`mcp/klights_mcp.py` exposes these to an assistant; they are plain functions
returning plain dicts, so a CLI or the designer could call the same ones. Like
`patch.py` for the rig, every rule lives elsewhere -- `showfiles` decides what a
valid document is and writes it, `program` decides what works on a rig -- and
this module only translates. So "is this timeline valid" has one answer whether
it is asked in chat, in the designer or by the engine loading the folder.

**Writes are dry runs unless asked**, and report exactly what would change.
**Writes quote the rev they read** (`base_rev`): a file that changed since --
the designer saved it, another machine synced it -- is refused rather than
overwritten. Unlike the rig, the show folder may be written while a show runs:
the engine reloads it, and a playing track keeps the version it started with
until its next play.

Positions are beats from the track's first downbeat; bar n starts at beat
4 * (n - 1).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from . import program as programmod
from . import showfiles
from . import showlibrary
from . import timeline as timelinemod
from . import tracktime

def _root(show_dir: Optional[str]) -> Path:
    root = showfiles.resolve_show_dir(show_dir)
    if root is None:
        raise ValueError("no show folder: pass show_dir, set KLIGHTS_SHOW_DIR, "
                         "or set show_dir in klights.local.json")
    return root


def _error(text: str, **extra) -> dict:
    return {"ok": False, "errors": [text], **extra}


# -- reading ------------------------------------------------------------------

def status(show_dir: Optional[str] = None) -> dict:
    root = _root(show_dir)
    folder = showfiles.load_folder(root)
    return {"ok": not folder.errors, "dir": str(root),
            "tracks": sorted(folder.tracks), "timelines": sorted(folder.timelines),
            "routines": sorted(folder.routines),
            "template_sets": sorted(folder.templates),
            "show": folder.show, "errors": folder.errors,
            "warnings": folder.warnings}


def list_tracks(show_dir: Optional[str] = None) -> dict:
    root = _root(show_dir)
    folder = showfiles.load_folder(root)
    out = []
    for tid, doc in sorted(folder.tracks.items()):
        ident = doc.get("identity") or {}
        phrases = (doc.get("phrases") or {}).get("items") or []
        out.append({"id": tid, "title": ident.get("title"),
                    "artist": ident.get("artist"),
                    "duration_s": ident.get("duration_s"), "bpm": ident.get("bpm"),
                    "phrases": [f"{p[2]} @{p[0]:g}-{p[1]:g}" for p in phrases],
                    "has_timeline": tid in folder.timelines})
    return {"tracks": out}


def list_routines(show_dir: Optional[str] = None) -> dict:
    folder = showfiles.load_folder(_root(show_dir))
    return {"routines": [
        {"id": rid, "name": doc.get("name"), "bars": doc.get("bars"),
         "loop": doc.get("loop", True), "rig": doc.get("rig"),
         "roles": doc.get("roles"), "params": doc.get("params") or {},
         "variations": sorted((doc.get("variations") or {}).keys())}
        for rid, doc in sorted(folder.routines.items())]}


def list_template_sets(show_dir: Optional[str] = None) -> dict:
    folder = showfiles.load_folder(_root(show_dir))
    return {"template_sets": [{"id": tid, "name": doc.get("name"),
                               "phrases": sorted(doc.get("phrases") or {})}
                              for tid, doc in sorted(folder.templates.items())]}


def get_doc(kind: str, ident: str, show_dir: Optional[str] = None) -> dict:
    """One document and its rev -- the rev a later write must quote."""
    root = _root(show_dir)
    path = showfiles.path_for(root, kind, ident)
    if not path.exists():
        return _error(f"no {kind} {ident!r} in {root}", rev="")
    result, rev = showfiles.read_doc(path, kind)
    return {"ok": result.ok, "doc": result.doc, "rev": rev,
            "errors": result.errors, "warnings": result.warnings}


# -- writing ------------------------------------------------------------------

def put_doc(kind: str, doc: Any, base_rev: Optional[str] = None,
            write: bool = False, show_dir: Optional[str] = None) -> dict:
    """Validate a whole document and, with `write`, save it. A file that exists
    must be written with the rev it was read at."""
    root = _root(show_dir)
    if not isinstance(doc, dict):
        return _error(f"a {kind} must be a JSON object")
    ident = showfiles.doc_ident(kind, doc)
    try:
        path = showfiles.path_for(root, kind, ident or "")
    except ValueError as exc:
        return _error(str(exc))
    result = showfiles.validate(kind, doc)
    if kind == "timeline" and result.ok:
        # The designer's draft says these too; an assistant should hear them
        # before it writes, not only from a later lint.
        result.warnings += showfiles.param_lane_problems(
            doc, showfiles.load_folder(root).routines)
    current = showfiles.doc_rev(path)
    out = {"ok": result.ok, "errors": result.errors, "warnings": result.warnings,
           "path": str(path.relative_to(root)), "exists": current is not None,
           "current_rev": current, "dry_run": not write, "written": False}
    if not result.ok:
        return out
    if not write:
        out["hint"] = ("call again with write=true" +
                       (f' and base_rev="{current}"' if current else
                        ' and base_rev=""'))
        return out
    if base_rev is None:
        return {**out, "ok": False, "errors": [
            f"base_rev is required to write: {current!r} if you read the "
            f"current file" + ("" if current else ', or "" for a new one')]}
    try:
        out["rev"] = showfiles.write_doc(path, doc, kind, base_rev=base_rev)
    except showfiles.StaleEdit as exc:
        return {**out, "ok": False, "errors": [str(exc)]}
    out["written"] = True
    return out


def edit_timeline(track: str, ops: Sequence[Mapping], base_rev: Optional[str] = None,
                  write: bool = False, show_dir: Optional[str] = None) -> dict:
    """Apply a list of small edits to a track's timeline -- creating it if the
    track has none -- validate the result, and with `write` save it.

    ops, each a dict with "op":
      add_row      {row: {...}, index?}           a lane; index 0 is the top
      remove_row   {row: id}
      add_item     {row: id, item: {...}}         a clip or hit on a lane
      update_item  {id, set: {...}}               e.g. {"at": 160, "fade": 4}
      remove_item  {id}
      set_points   {row: id, points: [[beat, value, curve?], ...]}
      set_wave     {row: id, wave: {shape, bars, depth, ...} | null}
                                                  a wave on top of the points
      set          {key: "palette" | "palettes" | "grid_rev", value}
    """
    root = _root(show_dir)
    path = showfiles.path_for(root, "timeline", track)
    track_path = showfiles.path_for(root, "track", track)
    track_result, _ = showfiles.read_doc(track_path, "track")
    if not track_result.ok:
        return _error(f"track {track!r} is not usable: "
                      f"{(track_result.errors or ['missing'])[0]}")
    if path.exists():
        result, rev = showfiles.read_doc(path, "timeline")
        if not result.ok:
            return _error(f"timelines/{track}.json does not load: "
                          f"{result.errors[0]}; fix or replace it with "
                          f"put_timeline")
        doc = copy.deepcopy(result.doc)
    else:
        grid = tracktime.Grid.from_segments(track_result.doc["grid"]["segments"])
        doc = showfiles.new_doc("timeline", track=track, grid_rev=grid.rev,
                                rows=[])
        rev = None
    changes, problems = apply_ops(doc, ops)
    if problems:
        return {"ok": False, "errors": problems, "changes": changes,
                "dry_run": not write, "written": False}
    out = put_doc("timeline", doc, base_rev, write, show_dir=str(root))
    out["changes"] = changes
    out["created"] = rev is None
    if not write:
        out["doc"] = doc
    return out


def apply_ops(doc: dict, ops: Sequence[Mapping]) -> tuple[list[str], list[str]]:
    """Edit a timeline document in place. Returns (what changed, what could
    not be done); nothing is validated here -- that is `showfiles`' job."""
    changes: list[str] = []
    problems: list[str] = []
    rows: list = doc.setdefault("rows", [])
    # The document is edited in place; the ops are not. Without a copy, a row
    # added with "items": [] put the caller's own list into the document, the
    # next add_item appended to it, and the same ops applied a second time --
    # a dry run, then the write -- found their item "already exists".
    ops = copy.deepcopy(list(ops))

    def row(rid):
        return next((r for r in rows if r.get("id") == rid), None)

    def item(iid):
        for r in rows:
            for it in r.get("items") or ():
                if it.get("id") == iid:
                    return r, it
        return None, None

    for n, op in enumerate(ops):
        kind = op.get("op") if isinstance(op, Mapping) else None
        at = f"op {n} ({kind})"
        if kind == "add_row":
            new = op.get("row")
            if not isinstance(new, dict) or not new.get("id"):
                problems.append(f"{at}: needs a row with an id")
                continue
            if row(new["id"]) is not None:
                problems.append(f"{at}: there is already a row {new['id']!r}")
                continue
            index = op.get("index", len(rows))
            if isinstance(index, bool) or not isinstance(index, (int, float)):
                problems.append(f"{at}: index must be a number, 0 for the top "
                                f"-- got {index!r}")
                continue
            rows.insert(max(0, min(len(rows), int(index))), dict(new))
            changes.append(f"added lane {new['id']!r} at position {index}")
        elif kind == "remove_row":
            r = row(op.get("row"))
            if r is None:
                problems.append(f"{at}: no row {op.get('row')!r}")
                continue
            rows.remove(r)
            changes.append(f"removed lane {r['id']!r}")
        elif kind == "add_item":
            r = row(op.get("row"))
            new = op.get("item")
            if r is None or not isinstance(new, dict):
                problems.append(f"{at}: needs an existing row and an item")
                continue
            if item(new.get("id"))[1] is not None:
                problems.append(f"{at}: an item {new.get('id')!r} already exists")
                continue
            r.setdefault("items", []).append(dict(new))
            changes.append(f"added {new.get('kind') or new.get('hit') or 'item'} "
                           f"{new.get('id')!r} at beat {new.get('at')} on "
                           f"{r['id']!r}")
        elif kind == "update_item":
            r, it = item(op.get("id"))
            fields = op.get("set")
            if it is None or not isinstance(fields, Mapping):
                problems.append(f"{at}: needs an existing item id and set")
                continue
            before = {k: it.get(k) for k in fields}
            it.update(fields)
            changes.append(f"changed {op['id']!r} on {r['id']!r}: "
                           + ", ".join(f"{k} {before[k]!r} -> {v!r}"
                                       for k, v in fields.items()))
        elif kind == "remove_item":
            r, it = item(op.get("id"))
            if it is None:
                problems.append(f"{at}: no item {op.get('id')!r}")
                continue
            r["items"].remove(it)
            changes.append(f"removed {op['id']!r} from {r['id']!r}")
        elif kind == "set_points":
            r = row(op.get("row"))
            if r is None or r.get("type") != "automation":
                problems.append(f"{at}: needs an existing automation row")
                continue
            r["points"] = op.get("points")
            changes.append(f"set {len(r['points'] or [])} points on {r['id']!r}")
        elif kind == "set_wave":
            r = row(op.get("row"))
            if r is None or r.get("type") != "automation":
                problems.append(f"{at}: needs an existing automation row")
                continue
            if op.get("wave") is None:
                r.pop("wave", None)
                changes.append(f"removed the wave on {r['id']!r}")
            else:
                r["wave"] = op["wave"]
                changes.append(f"set a {op['wave'].get('shape', 'sine')} wave on "
                               f"{r['id']!r}" if isinstance(op["wave"], dict)
                               else f"set a wave on {r['id']!r}")
        elif kind == "set":
            key = op.get("key")
            if key not in ("palette", "palettes", "grid_rev"):
                problems.append(f"{at}: only palette, palettes or grid_rev")
                continue
            doc[key] = op.get("value")
            changes.append(f"set {key}")
        else:
            problems.append(f"{at}: unknown op; one of add_row, remove_row, "
                            f"add_item, update_item, remove_item, set_points, set_wave, set")
    return changes, problems


def link_track(track: str, title: str, artist: str = "", album: str = "",
               signature: Optional[str] = None, write: bool = False,
               show_dir: Optional[str] = None) -> dict:
    """Teach a prepped track to answer to another description (a guest's copy
    with different tags). Applies from the track's next play."""
    root = _root(show_dir)
    path = showfiles.path_for(root, "track", track)
    result, _ = showfiles.read_doc(path, "track")
    if not result.ok:
        return _error(f"no usable track {track!r}")
    from . import tracks as tracksmod
    alias = tracksmod.alias_for(title, artist, album)
    already = tracksmod.has_description(result.doc, alias)
    if not write:
        return {"ok": True, "dry_run": True, "written": False,
                "would_add": None if already else alias,
                "hint": "call again with write=true"}
    what = showlibrary.link(root, track, title, artist, album, signature,
                            showlibrary.default_added())
    return {"ok": True, "dry_run": False, "written": what != "already",
            "added": what}


# -- checking against a rig ---------------------------------------------------

def lint(show_dir: Optional[str] = None, event: Optional[str] = None) -> dict:
    """Everything wrong with the folder, and -- given an event -- everything
    in it that will not work on that rig."""
    root = _root(show_dir)
    folder = showfiles.load_folder(root)
    out = {"ok": not folder.errors, "errors": folder.errors,
           "warnings": folder.warnings, "rig_problems": {}}
    if event:
        rigging = programmod.load_rigging(_event_dir(event))
        for tid, doc in sorted(folder.timelines.items()):
            timeline = timelinemod.Timeline.from_doc(doc, showfiles.timeline_channels)
            prog = programmod.compile(timeline, folder.routines, rigging,
                                      f"timelines/{tid}.json")
            if prog.problems:
                out["rig_problems"][tid] = prog.problems
        out["ok"] = out["ok"] and not out["rig_problems"]
        out["event"] = rigging.event
    return out


def explain(track: str, beat: float, show_dir: Optional[str] = None,
            event: Optional[str] = None) -> dict:
    """What a track's timeline says at a beat -- each lane's stack,
    automation, hits -- and, given an event, what every fixture does."""
    root = _root(show_dir)
    folder = showfiles.load_folder(root)
    doc = folder.timelines.get(track)
    if doc is None:
        return _error(f"no valid timeline for {track!r}",
                      folder_errors=folder.errors[:5])
    timeline = timelinemod.Timeline.from_doc(doc, showfiles.timeline_channels)
    out = {"ok": True, "track": track, "bar": int(beat // 4) + 1,
           **timeline.explain(beat)}
    if event:
        from . import library as libmod
        from . import state as statemod
        rigging = programmod.load_rigging(_event_dir(event))
        prog = programmod.compile(timeline, folder.routines, rigging)
        ctx = statemod.EvalContext(rig=rigging.rig, venue=rigging.rig.venue)
        out["rig"] = prog.explain(ctx, beat,
                                  libmod.compose(None, [], [], (1.0, 1.0, 1.0)))
        out["rig_problems"] = prog.problems
    return out


def _event_dir(event: str) -> Path:
    path = Path(event)
    if path.is_dir():
        return path
    return Path(showfiles.REPO) / "events" / event

