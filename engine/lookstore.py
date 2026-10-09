"""
The look library as Studio sees it: what each look is, where it is used, and
the edits to `parametric_looks.json`.

An event's looks come from two files (`library.py`):

  looks.json               STORED looks -- tables ported out of QLC+ by
                           `shared/tools/port_library.py`. Generated, and held
                           to the workspace by `test_qlc_parity.py`, so nothing
                           here ever writes it.
  parametric_looks.json    BLOCK looks -- one block from `blocks.py` and its
                           arguments -- and the list of looks hidden from the
                           picker. Hand-authored until now; this module is how
                           Studio, and the MCP server, author it.

So every edit is an edit to the second file:

  save      a block look: a new one, or a change to one (its arguments, its
            groups, its notes)
  rename    a block look, and everything that names it: the show folder's
            routines, timelines and template sets, the cue list, the presets
  delete    a block look. A stored look cannot be deleted, only hidden
  hide      any look, stored or block, from the picker -- with the look that
            covers it now, if there is one -- and show it again

A stored look that one block can state exactly (one color, one level, every
head at the same offset) is offered AS that block (`block_version`), which is
how a table becomes something with knobs: Studio makes the block look from it
and hides the original.

Everything that writes takes `base_rev`, the file's rev as the editor read it
("" when there was no file), and is refused if the file has changed since --
the rule every show-folder save follows (`showfiles.write_doc`), for the same
reason. And the document is held to exactly what a load would hold it to
BEFORE it is written: a save that passed a weaker check would write a file the
next start refuses. `dry_run` does all of that and writes nothing.

No engine state in here. The functions that write run on the engine's worker
(the controller installs the reloaded library on the output thread afterwards)
or in the MCP server's own process.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Optional, Sequence

from . import blocks as blocksmod
from . import config as configmod
from . import library as libmod
from . import showfiles

FILE = libmod.PARAMETRIC_FILE
MAX_NAME = 80
MAX_NOTES = 2000

# The keys of a block look this module writes, in the order the file has
# always had them. Anything else on an entry (a hand-added comment) is kept.
_LOOK_KEYS = ("name", "block", "groups", "args", "supersedes", "exact", "notes")

_FRESH_COMMENT = [
    "Looks built from blocks -- the same building blocks a show folder's",
    "routines are made of (engine/blocks.py) -- and the looks hidden from the",
    "picker. Written by Studio's Looks page; safe to hand-edit.",
    "",
    "A look names a block and its arguments. An argument left out takes the",
    "block's declared default.",
]


def path_for(event_dir: Path) -> Path:
    return Path(event_dir) / FILE


def file_rev(event_dir: Path) -> Optional[str]:
    """The file's rev on disk now, or None when the event has none yet."""
    return showfiles.doc_rev(path_for(event_dir))


def signature(event_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """(name, mtime_ns, size) of each look file that is there: what the
    engine's watcher compares, one stat a file and no reads -- the shape
    `showlibrary.scan` gives for the show folder."""
    found = []
    for name in (libmod.PORTED_FILE, FILE):
        try:
            st = (Path(event_dir) / name).stat()
        except OSError:
            continue
        found.append((name, st.st_mtime_ns, st.st_size))
    return tuple(found)


def _read(event_dir: Path) -> tuple[dict, Optional[str]]:
    path = path_for(event_dir)
    rev = showfiles.doc_rev(path)
    if rev is None:
        return {"$schema": "../../schemas/parametric_looks.schema.json",
                "_comment": list(_FRESH_COMMENT), "version": 1,
                "looks": [], "retired": []}, None
    return configmod.load(path, configmod.PARAMETRIC_LOOKS), rev


def _guard(rev: Optional[str], base_rev: Any) -> None:
    if not isinstance(base_rev, str):
        raise ValueError('base_rev is required: the rev you opened, or "" when '
                         "the event has no " + FILE + " yet")
    if base_rev != (rev or ""):
        raise showfiles.StaleEdit(
            f"{FILE} changed since you opened it (another tab, MCP, or an "
            f"edit by hand). Reload the looks and make the change again")


def _ported(event_dir: Path) -> list[libmod.LibraryEntry]:
    path = Path(event_dir) / libmod.PORTED_FILE
    return libmod.load_entries(path) if path.exists() else []


def _check(event_dir: Path, cfg: dict) -> None:
    """Hold a document to what a load would hold it to. Raises why not."""
    path = path_for(event_dir)
    configmod.validate(cfg, configmod.PARAMETRIC_LOOKS, path)
    parametric, retired = libmod.parse_parametric(cfg, path)
    seen: set[str] = set()
    for entry in parametric:
        if entry.name in seen:
            raise ValueError(f"{FILE} would have two looks called {entry.name!r}")
        seen.add(entry.name)
    libmod.merge(_ported(event_dir), parametric, retired)


def _write(event_dir: Path, cfg: dict, dry_run: bool = False) -> Optional[str]:
    """Check the document, then write it. Returns its rev -- or, for a dry
    run, the rev of the file as it is, having written nothing."""
    _check(event_dir, cfg)
    path = path_for(event_dir)
    if dry_run:
        return showfiles.doc_rev(path)
    configmod.write_json_atomic(path, cfg, text=render(cfg))
    rev = showfiles.doc_rev(path)
    assert rev is not None
    return rev


_INLINE_DEPTH = 3
_INLINE_WIDTH = 100


def _kind_of(look: Any) -> Any:
    """The picker heading a look files under, for grouping the file's lines."""
    block = look.get("block") if isinstance(look, dict) else None
    slot = blocksmod.SLOT_OF.get(block) if isinstance(block, str) else None
    if slot is None:
        return block
    return libmod.KIND_FOR_BLOCK.get(block, libmod.KIND_FOR_SLOT[slot])


def render(cfg: dict) -> str:
    """The file's text, in the layout it has always been written by hand in:
    a look to a block of lines, its `groups` and `args` each on one, a blank
    line before each list of entries and between the looks of one picker
    heading and the next.

    Not vanity. The file is in git beside a show, and the default dump puts
    every argument on a line of its own -- so the first save from Studio would
    turn a one-number change into a diff of the whole library, and nobody
    reviewing it could see what was changed. Rendered this way, the file as it
    was written by hand comes back byte for byte (`test_looks`)."""
    def inline(value: Any) -> str:
        if isinstance(value, dict):
            if not value:
                return "{}"
            return "{ " + ", ".join(f"{json.dumps(k)}: {inline(v)}"
                                    for k, v in value.items()) + " }"
        if isinstance(value, (list, tuple)):
            return "[" + ", ".join(inline(v) for v in value) + "]"
        return json.dumps(value)

    def block(value: Any, depth: int) -> str:
        pad, inner = "  " * depth, "  " * (depth + 1)
        if isinstance(value, (dict, list, tuple)) and value:
            if depth >= _INLINE_DEPTH:
                flat = inline(value)
                if len(flat) <= _INLINE_WIDTH:
                    return flat
            if isinstance(value, dict):
                rows = [f"{inner}{json.dumps(k)}: {block(v, depth + 1)}"
                        for k, v in value.items()]
                return "{\n" + ",\n".join(rows) + f"\n{pad}}}"
            rows = [f"{inner}{block(v, depth + 1)}" for v in value]
            return "[\n" + ",\n".join(rows) + f"\n{pad}]"
        return inline(value)

    if not isinstance(cfg, dict) or not cfg:
        return block(cfg, 0) + "\n"
    entries: list[str] = []
    for key, value in cfg.items():
        listed = (isinstance(value, list) and value
                  and all(isinstance(v, dict) for v in value))
        if not listed:
            entries.append(f"  {json.dumps(key)}: {block(value, 1)}")
            continue
        rows: list[str] = []
        before: Any = None
        for item in value:
            heading = _kind_of(item) if key == "looks" else None
            gap = "\n" if rows and heading != before else ""
            rows.append(f"{gap}    {block(item, 2)}")
            before = heading
        gap = "\n" if entries else ""
        entries.append(f"{gap}  {json.dumps(key)}: [\n" + ",\n".join(rows) + "\n  ]")
    return "{\n" + ",\n".join(entries) + "\n}\n"


# -- a look, as a client sends it ------------------------------------------------

def clean_name(raw: Any, what: str = "a look") -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{what} needs a name")
    name = " ".join(raw.split())
    if len(name) > MAX_NAME:
        raise ValueError(f"a look's name is at most {MAX_NAME} characters")
    if any(ord(c) < 32 for c in name):
        raise ValueError("a look's name cannot hold control characters")
    return name


def clean_look(raw: Any) -> dict:
    """A block look from a client, as the file stores it -- or why not.

    STRICT, unlike reading the file: an argument the block does not have means
    the editor and the engine disagree about the block, and dropping it would
    save a look that is not the one on screen. Numbers are NOT clamped to the
    declared range, as a routine file's are not (`blocks.PARAMS`): the range
    describes the controls, and what an author wrote is what plays.

    Checked as the file's own loader checks an entry (`blocks._check_args`
    on the arguments as written), so a cycle of no bars or a color that is not
    one is refused here, by name, rather than written to a file the next start
    would refuse whole.

    Whether the look still reproduces a stored look it took over (`exact`) is
    never the client's to say: `save` works it out.
    """
    if not isinstance(raw, dict):
        raise ValueError("a look is an object: name, block, args, groups")
    name = clean_name(raw.get("name"))
    block = raw.get("block")
    if not isinstance(block, str) or block not in blocksmod.BLOCKS:
        raise ValueError(f"{name!r} needs a block: one of "
                         + ", ".join(b for b in blocksmod.BLOCKS
                                     if blocksmod.SLOT_OF[b] is not None))
    if blocksmod.SLOT_OF[block] is None:
        raise ValueError(f"{block!r} plays a look or a preset rather than "
                         f"being one: it belongs in a routine")
    args_in = raw.get("args")
    if args_in is None:
        args_in = {}
    if not isinstance(args_in, dict):
        raise ValueError(f"{name!r}: args must be an object")
    declared = {p.name: p for p in blocksmod.PARAMS[block]}
    unknown = sorted(str(k) for k in args_in if k not in declared)
    if unknown:
        raise ValueError(f"{name!r}: {block} has no argument "
                         f"{', '.join(repr(k) for k in unknown)} -- it has "
                         f"{', '.join(declared)}")
    args: dict[str, Any] = {}
    for key, value in args_in.items():
        if value is None:
            continue                        # left out: the block's default
        param = declared[key]
        if param.kind in ("number", "integer"):
            if isinstance(value, bool) or not isinstance(value, (int, float)) \
                    or not math.isfinite(value):
                raise ValueError(f"{name!r}: {key} must be a number, got {value!r}")
            value = int(round(value)) if param.kind == "integer" else value
        elif param.kind == "bool" and not isinstance(value, bool):
            raise ValueError(f"{name!r}: {key} must be true or false, got {value!r}")
        args[key] = value
    if _non_finite(args):
        # A color or a point is a list, and a NaN inside one passes every
        # range test: it would be written to the file as a bare NaN, which no
        # strict JSON reader -- a browser among them -- will parse again.
        raise ValueError(f"{name!r}: every number must be finite")

    groups_in = raw.get("groups")
    if groups_in is None:
        groups_in = []
    if not isinstance(groups_in, list) or not all(
            isinstance(g, str) and g.strip() for g in groups_in):
        raise ValueError(f"{name!r}: groups is a list of rig tags, e.g. "
                         f'["movers"]')
    groups = list(dict.fromkeys(g.strip() for g in groups_in))
    notes = raw.get("notes") or ""
    if not isinstance(notes, str):
        raise ValueError(f"{name!r}: notes must be text")
    supersedes = raw.get("supersedes", False)
    if not isinstance(supersedes, bool):
        raise ValueError(f"{name!r}: supersedes must be true or false")

    problems = blocksmod._check_args(
        block, args, blocksmod.Env(params={}),
        blocksmod.Rigging(rig=libmod._NoRig(), entries={}))
    if problems:
        raise ValueError(f"{name!r}: {'; '.join(problems)}")

    out: dict[str, Any] = {"name": name, "block": block, "groups": groups,
                           "args": args}
    if supersedes:
        out["supersedes"] = True
    if notes.strip():
        out["notes"] = notes.strip()[:MAX_NOTES]
    return out


def _non_finite(value: Any) -> bool:
    if isinstance(value, float):
        return not math.isfinite(value)
    if isinstance(value, dict):
        return any(_non_finite(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_non_finite(v) for v in value)
    return False


def unknown_groups(look: Mapping, rig) -> list[str]:
    """Groups the look names that nothing on this rig carries. Not an error --
    a look can be written ahead of the fixtures it is for -- but worth saying:
    until something has the tag, the look moves and lights nothing."""
    have = set(rig.tags()) | {f.name for f in rig.fixtures}
    return [g for g in look.get("groups") or () if g not in have]


def _ordered(look: Mapping) -> dict:
    """A look with this module's keys first, in the file's order."""
    return {**{k: look[k] for k in _LOOK_KEYS if k in look},
            **{k: v for k, v in look.items() if k not in _LOOK_KEYS}}


# -- the edits -------------------------------------------------------------------

def save(event_dir: Path, look: dict, was: Optional[str], base_rev: Any,
         dry_run: bool = False) -> Optional[str]:
    """Write one block look (already through `clean_look`). Returns the rev.

    `was` None makes a NEW look, refused if the name is taken -- by a block
    look or by a stored one. `was` names the look being changed, under the
    same name: a change of name is `rename`, which has more to move.

    A look that took over a stored look (`supersedes`) stays that through an
    edit -- the flag is what the look IS, decided where it was made -- but
    whether it still REPRODUCES the stored look is worked out here each time
    its block, arguments or groups change, and written as `exact: false` when
    it no longer does. That is the record that the difference was made on
    purpose: `test_library` holds only the exact ones to their originals.
    """
    cfg, rev = _read(event_dir)
    _guard(rev, base_rev)
    looks = cfg.setdefault("looks", [])
    name = look["name"]
    block_names = [l.get("name") for l in looks]
    stored = {e.name: e for e in _ported(event_dir)}

    if was is None:
        if name in block_names:
            raise ValueError(f"there is already a look called {name!r}")
        if name in stored and not look.get("supersedes"):
            raise ValueError(f"there is already a stored look called {name!r}: "
                             f"a look is addressed by name from cues, presets "
                             f"and the picker, so each needs its own")
        fresh = dict(look)
        if fresh.get("supersedes") and name in stored \
                and not reproduces(fresh, stored[name]):
            fresh["exact"] = False
        looks.append(_ordered(fresh))
        return _write(event_dir, cfg, dry_run)

    if name != was:
        raise ValueError(f"{was!r} to {name!r} is a rename: it has more to "
                         f"move than this file (lookstore.rename)")
    if was not in block_names:
        raise ValueError(f"no look {was!r} in {FILE} to change"
                         + (" -- it is a stored look, which can be hidden but "
                            "not edited" if was in stored else ""))
    index = block_names.index(was)
    existing = looks[index]
    merged = {**look, **{k: v for k, v in existing.items()
                         if k not in _LOOK_KEYS}}
    merged.pop("supersedes", None)
    if existing.get("supersedes"):
        merged["supersedes"] = True
        moved = (existing.get("block") != look["block"]
                 or (existing.get("args") or {}) != look["args"]
                 or list(existing.get("groups") or []) != look["groups"])
        if not moved:
            if existing.get("exact") is False:
                merged["exact"] = False
        elif was not in stored or not reproduces(look, stored[was]):
            merged["exact"] = False
    looks[index] = _ordered(merged)
    return _write(event_dir, cfg, dry_run)


def rename(event_dir: Path, look: dict, was: str, base_rev: Any, *,
           show: Optional[tuple[Path, "showfiles.Folder"]] = None,
           presets: bool = False, dry_run: bool = False) -> dict:
    """Rename a block look, and everything that names it. `look` is the look
    as it should be under its new name (already through `clean_look`), so a
    rename can carry an edit with it. Returns `{rev, written, cues, presets}`:
    the show-folder files rewritten, and how many cues and presets changed.

    What names a look, and so what is rewritten:

      * `show` -- the show folder (its root, and the folder read from disk
        NOW): routines, timelines and template sets (`_refs`)
      * cues.json
      * presets.json, when `presets` is set. A RUNNING engine holds the
        presets in memory and writes that file whole, so it renames them
        itself and passes False; only an edit made with no engine running
        (MCP) rewrites the file here.
      * in this file, whatever is hidden in the look's favour

    A folder of files has no transaction, so, as `showfiles.rename_routine`
    does, the order is what keeps it whole: the look is written under BOTH
    names first, then each reference is moved over, and the old name goes
    last. Stopped anywhere, every reference still names a look that exists,
    and the error says how far it got. Each show-folder write quotes the rev
    the folder was read at. With nothing to move, it is one write.

    A look that took over a stored look gives the name back as it leaves it:
    the stored look returns under the old name, hidden in favour of the new
    one -- which is where a look that was renamed, not unmade, belongs.
    """
    cfg, rev = _read(event_dir)
    _guard(rev, base_rev)
    looks = cfg.setdefault("looks", [])
    name = look["name"]
    block_names = [l.get("name") for l in looks]
    stored_names = {e.name for e in _ported(event_dir)}
    if name == was:
        raise ValueError("that is its name already")
    if was not in block_names:
        raise ValueError(f"no look {was!r} in {FILE} to rename"
                         + (" -- it is a stored look: make a block look from "
                            "it under the new name, and hide it"
                            if was in stored_names else ""))
    if name in block_names or name in stored_names:
        raise ValueError(f"there is already a look called {name!r}")
    index = block_names.index(was)
    existing = looks[index]
    took_over = bool(existing.get("supersedes"))
    fresh = {**look, **{k: v for k, v in existing.items() if k not in _LOOK_KEYS}}
    fresh.pop("supersedes", None)
    fresh.pop("exact", None)
    fresh = _ordered(fresh)

    both = json.loads(json.dumps(cfg))
    both["looks"].insert(index + 1, fresh)
    final = json.loads(json.dumps(cfg))
    final["looks"][index] = fresh
    rows = list(final.get("retired") or [])
    for row in rows:
        if row.get("replaced_by") == was:
            row["replaced_by"] = name
    if took_over:
        hidden = next((r for r in rows if r.get("name") == was), None)
        rows = [r for r in rows if r.get("name") != was]
        if hidden is not None:
            rows.append({**hidden, "name": name})
        rows.append({"name": was, "replaced_by": name,
                     "note": f"The stored look whose name {name!r} had taken, "
                             f"before it was renamed."})
    else:
        for row in rows:
            if row.get("name") == was:
                row["name"] = name
    final["retired"] = rows
    _check(event_dir, both)
    _check(event_dir, final)

    # Everything that will be written, worked out -- and held to its format --
    # before anything is.
    docs = _folder_renames(show[1], was, name) if show is not None else []
    for kind, ident, doc, rel in docs:
        result = showfiles.validate(kind, doc, rel)
        if result.errors:
            raise ValueError(f"{rel} names {was!r} and would not be valid "
                             f"rewritten: {result.errors[0]}")
    cue_doc, cue_count = _renamed_file(Path(event_dir) / "cues.json", "cues",
                                       was, name)
    preset_doc, preset_count = (
        _renamed_file(Path(event_dir) / "presets.json", "presets", was, name)
        if presets else (None, 0))
    out = {"written": [rel for _, _, _, rel in docs], "cues": cue_count,
           "presets": preset_count}
    if dry_run:
        return {**out, "rev": rev}

    if docs or cue_doc is not None or preset_doc is not None:
        _write(event_dir, both)
        done: list[str] = []
        try:
            for kind, ident, doc, rel in docs:
                showfiles.write_doc(showfiles.path_for(show[0], kind, ident),
                                    doc, kind, show[1].revs.get(rel, ""))
                done.append(rel)
            if cue_doc is not None:
                configmod.write_json_atomic(Path(event_dir) / "cues.json", cue_doc)
                done.append("cues.json")
            if preset_doc is not None:
                configmod.write_json_atomic(Path(event_dir) / "presets.json",
                                            preset_doc)
                done.append("presets.json")
        except (ValueError, OSError) as exc:
            raise ValueError(
                f"renamed as far as {', '.join(done) or 'the look itself'}, then "
                f"stopped: {exc}. {was!r} is still in the library beside "
                f"{name!r}, so nothing names a look that is gone; move the "
                f"rest by hand, then delete {was!r}") from exc
    out["rev"] = _write(event_dir, final)
    return out


def delete(event_dir: Path, name: str, base_rev: Any,
           dry_run: bool = False) -> Optional[str]:
    """Remove one block look. Returns the rev.

    One that took over a stored look gives the stored one back, under the same
    name. A hidden block look takes its line in the hidden list with it."""
    cfg, rev = _read(event_dir)
    _guard(rev, base_rev)
    looks = cfg.get("looks") or []
    if not any(l.get("name") == name for l in looks):
        stored = name in {e.name for e in _ported(event_dir)}
        raise ValueError(f"no look {name!r} in {FILE} to delete"
                         + (" -- it is a stored look: hide it instead"
                            if stored else ""))
    took_over = any(l.get("name") == name and l.get("supersedes") for l in looks)
    cfg["looks"] = [l for l in looks if l.get("name") != name]
    if not took_over:
        cfg["retired"] = [r for r in cfg.get("retired") or ()
                          if r.get("name") != name]
    return _write(event_dir, cfg, dry_run)


def hide(event_dir: Path, name: str, hidden: bool, base_rev: Any,
         known: Iterable[str], replaced_by: Optional[str] = None,
         note: Optional[str] = None, dry_run: bool = False) -> Optional[str]:
    """Hide a look from the picker, or show it again. Returns the rev.

    Hidden, never removed: a stored look stays in looks.json, still plays for
    anything that names it, and is one click from coming back."""
    known = set(known)
    if name not in known:
        raise ValueError(f"no look named {name!r}")
    cfg, rev = _read(event_dir)
    _guard(rev, base_rev)
    rows = [r for r in cfg.get("retired") or () if r.get("name") != name]
    if hidden:
        row: dict[str, Any] = {"name": name}
        if replaced_by:
            if replaced_by == name:
                raise ValueError("a look cannot be replaced by itself")
            if replaced_by not in known:
                raise ValueError(f"no look named {replaced_by!r} to point at")
            row["replaced_by"] = replaced_by
        if isinstance(note, str) and note.strip():
            row["note"] = note.strip()[:MAX_NOTES]
        rows.append(row)
    cfg["retired"] = rows
    return _write(event_dir, cfg, dry_run)


# -- a rename, everywhere else a look is named --------------------------------------

def renamed(block: Mapping, old: str, new: str) -> dict:
    """A preset or a cue with look `old` called `new`: in its three slots, and
    as the key of its per-look tuning. Equal to `block` if it never names it."""
    out = dict(block)
    for slot in ("movement", "color", "level"):
        if isinstance(out.get(slot), dict):
            out[slot] = {group: (new if name == old else name)
                         for group, name in out[slot].items()}
    if isinstance(out.get("params"), dict):
        out["params"] = {(new if name == old else name): values
                         for name, values in out["params"].items()}
    return out


def _renamed_file(path: Path, key: str, old: str, new: str
                  ) -> tuple[Optional[dict], int]:
    """cues.json or presets.json with look `old` called `new`: the document to
    write and how many entries changed, or (None, 0) when none do -- or when
    there is no such file, or it does not parse.

    Read as plain JSON, not through its loader: every key the file has -- its
    comments, a field this engine does not know -- goes back as it came."""
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None, 0
    entries = doc.get(key) if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        return None, 0
    after = [renamed(e, old, new) if isinstance(e, dict) else e for e in entries]
    changed = sum(a != b for a, b in zip(after, entries))
    if not changed:
        return None, 0
    return {**doc, key: after}, changed


_NAMING_ARGS = ("color", "colors", "look")


def _refs(kind: str, doc: Mapping) -> Iterator[tuple[Any, Any]]:
    """Every place a show-folder document can hold a look's name, as
    `(container, key)` with the name at `container[key]` -- one walk, shared
    by "what names this look" (`usage`) and "call it something else"
    (`_folder_renames`), so a rename can never miss a place the usage shows,
    nor the other way round.

    A routine names a look as a block's argument -- only an argument that can
    BE a look: a `look`, or a color, which may be a single-color look's name
    (a choice like `"easing": "linear"` is a string too, and is not one) -- as
    a parameter's default, and as a variation's value. A timeline places one
    as a clip, holds them in a snapshot's slots, and passes them to a routine
    clip's parameters; a template set's picks pass parameters too. A string
    in a parameter is a color or a look, whichever it is.
    """
    def strings(values: Any, only: Optional[Iterable[str]] = None
                ) -> Iterator[tuple[Any, Any]]:
        if not isinstance(values, dict):
            return
        for key, value in values.items():
            if only is not None and key not in only:
                continue
            if isinstance(value, str):
                yield values, key
            elif isinstance(value, list):
                for i, item in enumerate(value):
                    if isinstance(item, str):
                        yield value, i

    def items(document: Mapping) -> Iterator[dict]:
        for row in document.get("rows") or ():
            for item in (row.get("items") or ()) if isinstance(row, dict) else ():
                if isinstance(item, dict):
                    yield item

    if kind == "routine":
        for param in (doc.get("params") or {}).values():
            if isinstance(param, dict) and isinstance(param.get("default"), str):
                yield param, "default"
        for values in (doc.get("variations") or {}).values():
            yield from strings(values)
        for item in items(doc):
            naming = [p.name for p in blocksmod.PARAMS.get(item.get("block") or "", ())
                      if p.kind in _NAMING_ARGS]
            yield from strings(item.get("args"), naming)
    elif kind == "timeline":
        for item in items(doc):
            what = item.get("kind")
            if what == "look" and isinstance(item.get("look"), str):
                yield item, "look"
            elif what == "routine":
                yield from strings(item.get("params"))
            elif what == "snapshot":
                for slot in ("movement", "color", "level"):
                    yield from strings(item.get(slot))
    elif kind == "template_set":
        picks = list((doc.get("phrases") or {}).values()) + list(
            (doc.get("bars") or {}).get("cycle") or ())
        for pick in picks:
            if isinstance(pick, dict):
                yield from strings(pick.get("params"))


def _folder_docs(folder: "showfiles.Folder"
                 ) -> Iterator[tuple[str, str, Mapping]]:
    for kind, docs in (("routine", folder.routines),
                       ("timeline", folder.timelines),
                       ("template_set", folder.templates)):
        for ident, doc in sorted(docs.items()):
            yield kind, ident, doc


def _folder_renames(folder: "showfiles.Folder", old: str, new: str
                    ) -> list[tuple[str, str, dict, str]]:
    """The show-folder documents that name look `old`, each rewritten to name
    `new`: (kind, id, document, path within the folder)."""
    out = []
    for kind, ident, doc in _folder_docs(folder):
        copy = json.loads(json.dumps(doc))
        hits = 0
        for container, key in _refs(kind, copy):
            if container[key] == old:
                container[key] = new
                hits += 1
        if hits:
            out.append((kind, ident, copy,
                        f"{showfiles.SUBDIR[kind]}/{ident}.json"))
    return out


# -- what each look is -------------------------------------------------------------

def _rgb(value: Any) -> Optional[list[float]]:
    if isinstance(value, str):
        value = blocksmod.parse_hex(value)
    if isinstance(value, (list, tuple)) and len(value) >= 3 and all(
            isinstance(c, (int, float)) and not isinstance(c, bool)
            for c in value[:3]):
        return [round(float(c), 4) for c in value[:3]]
    return None


def stored_summary(entry: libmod.LibraryEntry) -> dict:
    """A stored look in a line and a few swatches: enough to tell which one it
    is without playing it. The table itself stays in looks.json."""
    swatches: list[list[float]] = []
    steps: Optional[int] = None
    if entry.steps is not None:
        steps = len(entry.steps)
        what = ("a dark move: travels unlit, lights on arrival"
                if entry.is_cued else "a route through stored positions")
    elif entry.offsets is not None:
        same = _one_offset(entry) is not None
        what = ("every head at one offset from the ball" if same
                else "a stored position for each head")
        if entry.intensity is not None:
            what += f", at {round(entry.intensity * 100)}%"
    elif entry.frames is not None:
        steps = len(entry.frames)
        what = "a stepped color chase"
        seen: list[list[float]] = []
        for frame in entry.frames:
            for value in frame.values():
                rgb = _rgb(value)
                if rgb is not None and rgb not in seen:
                    seen.append(rgb)
        swatches = seen[:8]
    elif entry.colors is not None:
        what = "a color for each fixture"
        for value in entry.colors.values():
            rgb = _rgb(value)
            if rgb is not None and rgb not in swatches:
                swatches.append(rgb)
        swatches = swatches[:8]
    elif entry.color is not None:
        what = "one color" + (", with white" if entry.whites else "")
        rgb = _rgb(entry.color)
        swatches = [rgb] if rgb is not None else []
    elif entry.levels is not None:
        steps = len(entry.levels)
        what = "a stepped level chase"
    elif entry.intensities:
        what = "a level for each fixture"
    elif entry.intensity is not None:
        what = f"one level: {round(entry.intensity * 100)}%"
    elif entry.strobes:
        what = "a strobe"
    else:
        what = "a stored look"
    out: dict[str, Any] = {"what": what}
    if swatches:
        out["swatches"] = swatches
    if steps is not None:
        out["steps"] = steps
        out["bars"] = entry.bars or libmod.DEFAULT_BARS
    if entry.source:
        out["source"] = entry.source
    return out


def _one_offset(entry: libmod.LibraryEntry) -> Optional[tuple[float, float]]:
    """The one offset every head of a pose holds, or None if they differ."""
    offsets = entry.offsets or []
    if not offsets:
        return None
    first = offsets[0]
    if any(abs(o[0] - first[0]) > 0.01 or abs(o[1] - first[1]) > 0.01
           for o in offsets):
        return None
    return (round(sum(o[0] for o in offsets) / len(offsets), 3),
            round(sum(o[1] for o in offsets) / len(offsets), 3))


def block_version(entry: libmod.LibraryEntry) -> Optional[dict]:
    """The one block that states this stored look, or None if none does.

    Only where a block says the SAME thing -- one color, one level, one offset
    for every head. A route, a chase or a color per fixture is a table, and
    offering "an orbit, roughly" as its block version would be a different
    look under the same name."""
    if entry.is_parametric:
        return None
    if entry.kind == "color" and entry.color is not None and not entry.whites \
            and entry.colors is None and entry.intensity is None:
        rgb = _rgb(entry.color)
        return {"block": "solid", "args": {"color": rgb}} if rgb else None
    if entry.kind == "intensity" and entry.intensity is not None \
            and not entry.intensities and not entry.strobes \
            and entry.levels is None:
        return {"block": "dim", "args": {"level": round(entry.intensity, 4)}}
    if entry.kind == "pose" and entry.intensity is None:
        one = _one_offset(entry)
        if one is not None:
            return {"block": "offset",
                    "args": {"bearing": one[0], "elevation": one[1]}}
    return None


# How far a block look's numbers may sit from a stored look's and still be
# the same look: a hundredth of a degree of aim (what `test_library` measures
# the positions to), and half a DMX step of color or level.
_SAME_DEGREES = 0.01
_SAME_LEVEL = 1.0 / 510.0


def reproduces(look: Mapping, stored: libmod.LibraryEntry) -> bool:
    """Whether a block look says what the stored look says: the block that
    states it (`block_version`), the same fixtures, the same numbers.

    False for a stored look no single block states. That is the honest
    answer for a route or a chase too: a block may fit one closely (the
    retirement audit measures how closely), but this cannot vouch for it."""
    version = block_version(stored)
    if version is None or look.get("block") != version["block"]:
        return False
    if set(look.get("groups") or ()) != set(stored.groups):
        return False
    declared = {p.name: p.default for p in blocksmod.PARAMS[version["block"]]}
    given = look.get("args") or {}
    within = _SAME_DEGREES if version["block"] == "offset" else _SAME_LEVEL
    for key, want in version["args"].items():
        have = given.get(key, declared.get(key))
        if isinstance(want, list):
            have = _rgb(have)
            if have is None or any(abs(a - b) > within for a, b in zip(have, want)):
                return False
        elif isinstance(have, bool) or not isinstance(have, (int, float)) \
                or abs(have - want) > within:
            return False
    return True


# -- where each look is used -------------------------------------------------------

def usage(cues: Sequence[Any], presets: Sequence[Mapping],
          folder: Optional["showfiles.Folder"],
          entries: Sequence[libmod.LibraryEntry]) -> dict[str, dict]:
    """Every place each look is named, by look name: the cues and presets that
    put it in a slot, the show folder's routines, timelines and template sets
    that play it (`_refs`), and the looks hidden in its favour. A look with no
    entry is named nowhere.

    All of those are by NAME, which is why a rename or a delete has to know
    them. `cues` are `cues.Cue`s or plain cue objects from the file."""
    out: dict[str, dict] = {}
    names = {e.name for e in entries}

    def add(name: Any, key: str, item: Any) -> None:
        # Only looks: an argument is any string, and "@primary" is not one.
        if not isinstance(name, str) or name not in names:
            return
        use = out.setdefault(name, {"cues": [], "presets": [], "routines": [],
                                    "timelines": [], "templates": [],
                                    "hides": []})
        if item not in use[key]:
            use[key].append(item)

    def field(obj: Any, key: str) -> Any:
        return obj.get(key) if isinstance(obj, Mapping) else getattr(obj, key, None)

    for kind, things in (("cues", cues), ("presets", presets)):
        for thing in things:
            for slot in ("movement", "color", "level"):
                for name in (field(thing, slot) or {}).values():
                    add(name, kind, field(thing, "name"))
    if folder is not None:
        for kind, ident, doc in _folder_docs(folder):
            if kind == "routine":
                key, where = "routines", {"id": ident, "name": doc.get("name")}
            elif kind == "timeline":
                title = ((folder.tracks.get(ident) or {}).get("identity")
                         or {}).get("title")
                key, where = "timelines", {"track": ident, "title": title}
            else:
                key, where = "templates", {"id": ident, "name": doc.get("name")}
            for container, at in _refs(kind, doc):
                add(container[at], key, where)
    for e in entries:
        if e.retired and e.replaced_by:
            add(e.replaced_by, "hides", e.name)
    return out


def uses(use: Optional[Mapping]) -> list[str]:
    """Where a look is named, as the files that name it, in words -- for a
    refusal. Being hidden in its favour is not a use: nothing plays through it."""
    if not use:
        return []
    return ([f"cues.json ({', '.join(use['cues'])})"] if use["cues"] else []) \
        + ([f"presets.json ({', '.join(use['presets'])})"] if use["presets"] else []) \
        + [f"routines/{r['id']}.json" for r in use["routines"]] \
        + [f"timelines/{t['track']}.json" for t in use["timelines"]] \
        + [f"templates/{t['id']}.json" for t in use["templates"]]


def describe(entry: libmod.LibraryEntry, manual_only: bool,
             use: Optional[Mapping]) -> dict:
    """One look, as Studio's Looks page needs it."""
    out: dict[str, Any] = {
        "name": entry.name, "slot": entry.slot, "kind": entry.kind,
        "groups": list(entry.groups),
        "source": "block" if entry.is_parametric else "stored",
        "retired": entry.retired, "replaced_by": entry.replaced_by,
        "retired_note": entry.retired_note,
        "manual_only": manual_only, "cued": entry.is_cued,
        "step_of": entry.step_of, "notes": entry.notes,
        "used_by": dict(use) if use else {"cues": [], "presets": [],
                                          "routines": [], "timelines": [],
                                          "templates": [], "hides": []},
    }
    if entry.is_parametric:
        out["block"] = entry.block
        out["args"] = json.loads(json.dumps(entry.args))
        out["supersedes"] = entry.supersedes
        if entry.supersedes:
            # Whether it still says what the stored look of its name says.
            out["exact"] = entry.exact
    else:
        out["stored"] = stored_summary(entry)
        version = block_version(entry)
        if version is not None:
            out["block_version"] = version
    return out
