"""
The cue list as Studio sees it: what each cue holds, what is wrong with it,
and the edits to `cues.json`.

An event's cue list is one file (`cues.py` loads it): the night in running
order, each cue the three slots a preset has -- a look for each fixture group
in movement, color and level -- plus a fade and an optional hold in beats, and
whatever else it restores (a shape, slot rates, per-look tuning, a speed, a
master). Hand-authored until now; this module is how Studio's Cues page, and
the MCP server, author it:

  save        a new cue, at a place in the order -- or a change to one, which
              renames it when its name differs
  duplicate   a cue, with every key its entry has, under another name beside it
  move        a cue to another place in the running order
  delete      a cue (never the last: a cue list cannot be empty)
  rename_list the list itself, which is the title of the console's GO card

Everything that writes takes `base_rev`, the file's rev as the editor read it
("" when there was no file), and is refused if the file has changed since --
the rule every show-folder save and every look edit follows, for the same
reason. The document is held to exactly what a load would hold it to
(`cues.parse`) BEFORE it is written, so no save can write a cues.json the next
start refuses. `dry_run` does all of that and writes nothing.

The file is edited as the JSON it is, not through its loader: every key it has
-- its `$schema`, its `_comment` lines, a field this engine does not know --
goes back as it came, and it is written in the layout it has by hand
(`render`). `lookstore.rename` rewrites the same file when a look a cue names
is renamed, through the same layout.

A cue naming a look the library lacks is FLAGGED, never refused (`flags`): a
cue list outlives any one port of the library (`cues.missing_looks`).

No engine state in here. The functions that write run on the engine's worker
(the controller installs the list on the output thread afterwards, keeping its
place in it -- `carry`) or in the MCP server's own process.
"""

from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

from . import clock as clockmod
from . import config as configmod
from . import cues as cuesmod
from . import lookstore
from . import params as parammod
from . import showfiles

FILE = lookstore.CUES_FILE
SLOTS = showfiles.SLOTS

MAX_NAME = lookstore.MAX_NAME
MAX_NOTES = lookstore.MAX_NOTES
MAX_GROUPS = lookstore.MAX_GROUPS
# A night is a dozen cues, and a long one a hundred. The snapshot every
# console receives ten times a second lists each by name, so the list has a
# ceiling no person would reach by hand.
MAX_CUES = 500

# The beats a cue fades in over when it does not say (`cues.Cue.fade`).
DEFAULT_FADE = 8.0
# What a slot's chase rate may be (`state.Phases.set_rate`).
MAX_RATE = 8.0
# The lane a look with no groups of its own fills (`ShowController`).
DEFAULT_GROUP = "movers"

# The keys of a cue this module writes, in the order the file has always had
# them. Anything else on an entry (a hand-added comment) is kept, after them.
_CUE_KEYS = ("name", "movement", "color", "level", "fade", "hold", "speed",
             "master", "macro", "rates", "params", "notes")
_MACRO_KEYS = ("size", "spread", "center", "reset")

DEFAULT_NAME = "Cues"
_FRESH_COMMENT = [
    "The night, as one GO button: an ordered list of cues. A cue fills the same",
    "three slots a preset does -- a look for each fixture group in movement,",
    "color and level -- plus a fade and an optional hold, both in BEATS.",
    "",
    "A cue with no hold waits for GO. Written by Studio's Cues page; safe to",
    "hand-edit.",
]
# The top-level keys a file this module starts is set apart at.
_FRESH_GAPS = frozenset({"name", "cues"})


def path_for(event_dir: Path) -> Path:
    return Path(event_dir) / FILE


def file_rev(event_dir: Path) -> Optional[str]:
    """The file's rev on disk now, or None when the event has no cue list."""
    return showfiles.doc_rev(path_for(event_dir))


def signature(event_dir: Path) -> tuple[tuple[str, int, int], ...]:
    """(name, mtime_ns, size) of the file, if it is there: what the engine's
    watcher compares, one stat and no read (`lookstore.signature`)."""
    try:
        st = path_for(event_dir).stat()
    except OSError:
        return ()
    return ((FILE, st.st_mtime_ns, st.st_size),)


def _decode(data: bytes, path: Path) -> tuple[Any, str]:
    """A file's bytes as a document and as text, or why not -- in the words
    `config.load` has for it."""
    try:
        text = data.decode("utf-8")
        return json.loads(text), text
    except json.JSONDecodeError as exc:
        raise configmod.ConfigError(path, [
            f"is not valid JSON: {exc.msg} at line {exc.lineno}, "
            f"column {exc.colno}\n      fix: a trailing comma after the last "
            f"item in a list or object is the usual cause"]) from None
    except UnicodeDecodeError as exc:
        raise configmod.ConfigError(path, [f"is not UTF-8 text: {exc}"]) from None


def read_list(event_dir: Path) -> tuple[Optional[cuesmod.CueList], Optional[str]]:
    """The cue list as the file is now, and the rev a save of it must quote --
    both from ONE read of the file, so the rev is never that of a file other
    than the one that was loaded. (None, None) when the event has no cue list.
    Raises `ConfigError` for a file that does not load."""
    path = path_for(event_dir)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return None, None
    cfg, _ = _decode(data, path)
    return cuesmod.parse(cfg, path), showfiles.rev_of(data)


def _read(event_dir: Path) -> tuple[dict, Optional[str], Optional[str]]:
    """The document to edit, its rev and its text -- or, with no file, the
    document a first cue starts, and no rev.

    As plain JSON, so every key comes back as it went; but only a file that
    LOADS is edited. One that does not is not the list that is running, and an
    edit laid over a mistake would bury it."""
    path = path_for(event_dir)
    try:
        data = path.read_bytes()
    except FileNotFoundError:
        return ({"$schema": "../../schemas/cues.schema.json",
                 "_comment": list(_FRESH_COMMENT), "name": DEFAULT_NAME,
                 "cues": []}, None, None)
    doc, text = _decode(data, path)
    cuesmod.parse(json.loads(text), path)
    return doc, showfiles.rev_of(data), text.replace("\r\n", "\n")


def _guard(rev: Optional[str], base_rev: Any) -> None:
    if not isinstance(base_rev, str):
        raise ValueError('base_rev is required: the rev you opened, or "" when '
                         "the event has no " + FILE + " yet")
    if base_rev != (rev or ""):
        raise showfiles.StaleEdit(
            f"{FILE} changed since you opened it (another tab, MCP, a look "
            f"renamed, or an edit by hand). Reload the cues and make the "
            f"change again")


def _check(event_dir: Path, doc: dict) -> None:
    """Hold a document to what a load would hold it to. Raises why not."""
    try:
        # A copy: a load drops a null it reads as absent, and what is written
        # is the document as it was handed in.
        copy = json.loads(json.dumps(doc, allow_nan=False))
    except ValueError:
        raise ValueError(f"{FILE} would hold a number that is not finite, "
                         f"which no JSON reader takes back") from None
    cuesmod.parse(copy, path_for(event_dir))


def _write(event_dir: Path, doc: dict, original: Optional[str],
           dry_run: bool = False) -> Optional[str]:
    """Check the document, then write it. Returns its rev -- or, for a dry
    run, the rev of the file as it is, having written nothing."""
    _check(event_dir, doc)
    path = path_for(event_dir)
    if dry_run:
        return showfiles.doc_rev(path)
    configmod.write_json_atomic(path, doc, text=render(doc, gaps_of(original)))
    rev = showfiles.doc_rev(path)
    assert rev is not None
    return rev


# -- the file's layout ------------------------------------------------------------

_TOP_KEY = re.compile(r'^  ("(?:[^"\\]|\\.)*"):')


def gaps_of(original: Optional[str]) -> frozenset[str]:
    """The top-level keys a file sets apart with a blank line before them --
    for a file that is not there yet, the ones this module would."""
    if original is None:
        return _FRESH_GAPS
    found: set[str] = set()
    blank = False
    for line in original.replace("\r\n", "\n").split("\n"):
        match = _TOP_KEY.match(line)
        if match and blank:
            try:
                found.add(json.loads(match.group(1)))
            except ValueError:
                pass
        blank = not line.strip()
    return frozenset(found)


def render(doc: Any, gaps: Iterable[str] = _FRESH_GAPS) -> str:
    """The file's text, in the layout it has always been written by hand in:
    a cue to a block of lines, each slot's looks on one, and a blank line
    before the top-level keys in `gaps`.

    For the reason `lookstore.render` gives: the file is in git beside a show,
    and the default dump puts every group of every slot on a line of its own
    -- so the first save from Studio would turn a changed fade into a diff of
    the whole night. Rendered this way, the file as it was written by hand
    comes back byte for byte (`test_cuestore`), and it is the layout
    `lookstore.relaid` keeps when a look is renamed.
    """
    gaps = frozenset(gaps)
    out: list[str] = []
    for line in lookstore._block(doc, 0).split("\n"):
        match = _TOP_KEY.match(line)
        if match and out and json.loads(match.group(1)) in gaps:
            out.append("")
        out.append(line)
    return "\n".join(out) + "\n"


def render_like(original: str, before: Any, after: Any) -> Optional[str]:
    """`after` as text in the layout `original` has, when that layout is this
    module's -- so a change that adds or removes a line (which
    `lookstore.relaid` cannot follow) still leaves the file laid out as it
    was. None for a file laid out some other way: the caller writes the plain
    dump, which is always correct."""
    gaps = gaps_of(original)
    if render(before, gaps) != original.replace("\r\n", "\n"):
        return None
    return render(after, gaps)


# -- a cue, as a client sends it ----------------------------------------------------

def clean_name(raw: Any, what: str = "a cue") -> str:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError(f"{what} needs a name")
    name = " ".join(raw.split())
    if len(name) > MAX_NAME:
        raise ValueError(f"{what}'s name is at most {MAX_NAME} characters")
    if any(ord(c) < 32 for c in name):
        raise ValueError(f"{what}'s name cannot hold control characters")
    return name


def _number(value: Any, cue: str, what: str, lo: Optional[float] = None,
            hi: Optional[float] = None, fix: str = "") -> Any:
    """One number of a cue, as the file stores it: finite, in range, and a
    whole number written as one (32, not 32.0)."""
    try:
        ok = (not isinstance(value, bool) and isinstance(value, (int, float))
              and math.isfinite(value))
    except OverflowError:               # an int too big to be any of these
        ok = False
    if not ok:
        raise ValueError(f"{cue!r}: {what} must be a number, got {value!r:.60}"
                         + (f" -- {fix}" if fix else ""))
    if (lo is not None and value < lo) or (hi is not None and value > hi):
        span = (f"between {lo:g} and {hi:g}" if lo is not None and hi is not None
                else f"at least {lo:g}" if lo is not None else f"at most {hi:g}")
        raise ValueError(f"{cue!r}: {what} must be {span}, got {value:g}"
                         + (f" -- {fix}" if fix else ""))
    return int(value) if float(value).is_integer() else value


def clean_cue(raw: Any) -> dict:
    """A cue from a client, as the file stores it -- or why not.

    STRICT, unlike reading the file: a field a cue does not have means the
    editor and the engine disagree about what a cue is, and dropping it would
    save a cue that is not the one on screen. And stricter than the loader on
    what it restores -- a shape, a rate, a speed: taking a cue is forgiving
    (it clamps, it skips, it leaves a notice), which is right at GO and wrong
    for a save, where the author is still there to be told.

    What it does NOT check is whether the looks exist. That is `flags`: said,
    never refused.

    A slot with nothing in it, a hold of 0 and whatever is left unset are left
    out, as they are by hand. `params` is the exception: an empty one is not
    the same as none (`cues.Cue.params`), so it is kept as it came.
    """
    if not isinstance(raw, dict):
        raise ValueError("a cue is an object: name, the looks in movement, "
                         "color and level, fade, hold")
    unknown = sorted(str(k) for k in raw if k not in _CUE_KEYS)
    if unknown:
        raise ValueError(f"a cue has no field {', '.join(repr(k) for k in unknown)}"
                         f" -- it has {', '.join(_CUE_KEYS)}")
    name = clean_name(raw.get("name"))
    out: dict[str, Any] = {"name": name}

    for slot in SLOTS:
        held = raw.get(slot)
        if held is None:
            continue
        if not isinstance(held, dict) or not all(
                isinstance(g, str) and g.strip() and isinstance(look, str)
                and look.strip() for g, look in held.items()):
            raise ValueError(f"{name!r}: {slot} is an object of fixture group "
                             f'to look name, e.g. {{"corner movers": "Ball Orbit"}}')
        if len(held) > MAX_GROUPS or any(
                len(g) > MAX_NAME or len(look) > MAX_NAME for g, look in held.items()):
            raise ValueError(f"{name!r}: {slot} holds at most {MAX_GROUPS} groups, "
                             f"each name at most {MAX_NAME} characters")
        if held:
            out[slot] = dict(held)

    fade = raw.get("fade")
    out["fade"] = _number(DEFAULT_FADE if fade is None else fade, name, "fade", 0,
                          fix="beats to crossfade in; 0 is a cut")
    hold = _number(raw["hold"] if raw.get("hold") is not None else 0, name, "hold", 0,
                   fix="beats before it moves on by itself; 0 waits for GO")
    if hold:
        out["hold"] = hold
    if raw.get("speed") is not None:
        out["speed"] = _number(raw["speed"], name, "speed", clockmod.MIN_SPEED,
                               clockmod.MAX_SPEED,
                               fix="a multiplier: 1 is the music's own tempo")
    if raw.get("master") is not None:
        out["master"] = _number(raw["master"], name, "master", 0, 1)

    macro = raw.get("macro")
    if macro is not None:
        if not isinstance(macro, dict):
            raise ValueError(f"{name!r}: macro is an object, e.g. "
                             f'{{"size": 1.2, "spread": 0.5}}')
        odd = sorted(str(k) for k in macro if k not in _MACRO_KEYS)
        if odd:
            raise ValueError(f"{name!r}: macro has no {', '.join(repr(k) for k in odd)}"
                             f" -- it has {', '.join(_MACRO_KEYS)}")
        shape: dict[str, Any] = {}
        for key, param in (("size", parammod.SIZE), ("spread", parammod.SPREAD)):
            if macro.get(key) is not None:
                shape[key] = _number(macro[key], name, f"macro {key}",
                                     param.min, param.max)
        center = macro.get("center")
        if center is not None:
            if not isinstance(center, (list, tuple)) or len(center) != 2:
                raise ValueError(f"{name!r}: macro center is [bearing, elevation] "
                                 f"in degrees")
            # Bounded by the rig's reach when the cue is taken, which only a
            # loaded rig knows: here, by what any head could mean by it.
            shape["center"] = [
                _number(center[0], name, "macro center bearing", -360, 360),
                _number(center[1], name, "macro center elevation", -180, 180)]
        if macro.get("reset") is not None:
            if not isinstance(macro["reset"], bool):
                raise ValueError(f"{name!r}: macro reset must be true or false")
            if macro["reset"]:
                shape["reset"] = True
        if shape:
            out["macro"] = shape

    rates = raw.get("rates")
    if rates is not None:
        if not isinstance(rates, dict):
            raise ValueError(f'{name!r}: rates is an object, e.g. {{"color": 0.5}}')
        odd = sorted(str(k) for k in rates if k not in SLOTS)
        if odd:
            raise ValueError(f"{name!r}: no slot {', '.join(repr(k) for k in odd)} "
                             f"to set a rate for -- one of {', '.join(SLOTS)}")
        kept = {slot: _number(rates[slot], name, f"{slot} rate", 0, MAX_RATE)
                for slot in SLOTS if rates.get(slot) is not None}
        if kept:
            out["rates"] = kept

    params = raw.get("params")
    if params is not None:
        if not isinstance(params, dict) or not all(
                isinstance(look, str) and look.strip() and isinstance(values, dict)
                and all(isinstance(k, str) and k for k in values)
                for look, values in params.items()):
            raise ValueError(f"{name!r}: params is tuning by look name, e.g. "
                             f'{{"Ball Orbit": {{"radius": 24}}}}')
        if len(params) > MAX_GROUPS * len(SLOTS):
            raise ValueError(f"{name!r}: params tunes more looks than a cue can name")
        for look, values in params.items():
            for key, value in values.items():
                if value is None or lookstore._oversize(value):
                    raise ValueError(f"{name!r}: {look}.{key} is not a value a "
                                     f"look's argument takes")
        if lookstore._non_finite(params):
            raise ValueError(f"{name!r}: every number in params must be finite")
        out["params"] = {look: dict(values) for look, values in params.items()}

    notes = raw.get("notes") or ""
    if not isinstance(notes, str):
        raise ValueError(f"{name!r}: notes must be text")
    if notes.strip():
        out["notes"] = notes.strip()[:MAX_NOTES]
    return _ordered(out)


def _ordered(cue: Mapping) -> dict:
    """A cue with this module's keys first, in the file's order."""
    return {**{k: cue[k] for k in _CUE_KEYS if k in cue},
            **{k: v for k, v in cue.items() if k not in _CUE_KEYS}}


# -- the edits -------------------------------------------------------------------

def _find(cues: Sequence[Mapping], name: Any, what: str) -> int:
    """Where the cue of this name is. The first, if a file edited by hand has
    two: renaming that one is how the second becomes reachable."""
    if not isinstance(name, str):
        raise ValueError(f"which cue to {what}: its name, as you read it")
    for i, cue in enumerate(cues):
        if cue.get("name") == name:
            return i
    raise ValueError(f"no cue called {name!r} in {FILE} to {what}")


def _free(cues: Sequence[Mapping], name: str, but: Optional[int] = None) -> None:
    for i, cue in enumerate(cues):
        if i != but and cue.get("name") == name:
            raise ValueError(f"there is already a cue called {name!r}: the "
                             f"console lists the cues by name, and the list "
                             f"keeps its place by it, so each needs its own")


def _place(at: Any, last: int, what: str) -> int:
    if isinstance(at, bool) or not isinstance(at, int) or not 0 <= at <= last:
        raise ValueError(f"{what} is a place in the running order: 0 is first, "
                         f"{last} is last here -- got {at!r}")
    return at


def save(event_dir: Path, cue: dict, was: Optional[str], base_rev: Any,
         at: Optional[int] = None, dry_run: bool = False) -> dict:
    """Write one cue (already through `clean_cue`). Returns `{rev, index,
    count}`: the file's rev, where the cue is now, and how many there are.

    `was` None makes a NEW cue, refused if the name is taken, at place `at`
    in the running order -- last, when not said. `was` names the cue being
    changed, which a different `cue.name` renames; with `at` it moves as well.
    Nothing else names a cue, so a rename is this one write.

    Whatever else the cue's entry holds in the file -- a comment added by
    hand, a field this engine does not know -- stays on it.
    """
    doc, rev, original = _read(event_dir)
    _guard(rev, base_rev)
    cues = list(doc.get("cues") or [])
    name = cue["name"]
    if was is None:
        _free(cues, name)
        if len(cues) >= MAX_CUES:
            raise ValueError(f"a cue list holds at most {MAX_CUES} cues")
        index = len(cues) if at is None else _place(at, len(cues), "at")
        cues.insert(index, _ordered(cue))
    else:
        index = _find(cues, was, "change")
        _free(cues, name, but=index)
        cues[index] = _ordered({**cue, **{k: v for k, v in cues[index].items()
                                          if k not in _CUE_KEYS}})
        if at is not None:
            to = _place(at, len(cues) - 1, "at")
            cues.insert(to, cues.pop(index))
            index = to
    doc["cues"] = cues
    return {"rev": _write(event_dir, doc, original, dry_run), "index": index,
            "count": len(cues)}


def duplicate(event_dir: Path, name: Any, to: Any, base_rev: Any,
              dry_run: bool = False) -> dict:
    """A copy of one cue, under the name `to`, straight after the original.
    The copy is of the entry as the FILE has it, every key. Returns `{rev,
    index, count, name}`."""
    doc, rev, original = _read(event_dir)
    _guard(rev, base_rev)
    cues = list(doc.get("cues") or [])
    index = _find(cues, name, "duplicate")
    fresh = clean_name(to, "the copy")
    _free(cues, fresh)
    if len(cues) >= MAX_CUES:
        raise ValueError(f"a cue list holds at most {MAX_CUES} cues")
    cues.insert(index + 1, {**json.loads(json.dumps(cues[index])), "name": fresh})
    doc["cues"] = cues
    return {"rev": _write(event_dir, doc, original, dry_run), "index": index + 1,
            "count": len(cues), "name": fresh}


def move(event_dir: Path, name: Any, to: Any, base_rev: Any,
         dry_run: bool = False) -> dict:
    """Move one cue to place `to` in the running order (0 is first), the
    others closing up around it. Returns `{rev, index, count}`."""
    doc, rev, original = _read(event_dir)
    _guard(rev, base_rev)
    cues = list(doc.get("cues") or [])
    index = _find(cues, name, "move")
    to = _place(to, len(cues) - 1, "to")
    if to == index:
        return {"rev": rev, "index": index, "count": len(cues)}
    cues.insert(to, cues.pop(index))
    doc["cues"] = cues
    return {"rev": _write(event_dir, doc, original, dry_run), "index": to,
            "count": len(cues)}


def delete(event_dir: Path, name: Any, base_rev: Any, dry_run: bool = False) -> dict:
    """Remove one cue. Returns `{rev, count}`.

    Never the last one: `cues.py` refuses a list of none, so the file this
    would write is one the next start would not load."""
    doc, rev, original = _read(event_dir)
    _guard(rev, base_rev)
    cues = list(doc.get("cues") or [])
    index = _find(cues, name, "delete")
    if len(cues) == 1:
        raise ValueError(f"{name!r} is the only cue, and a cue list cannot be "
                         f"empty: change it instead, or remove {FILE} from "
                         f"the event to have no cue list at all")
    del cues[index]
    doc["cues"] = cues
    return {"rev": _write(event_dir, doc, original, dry_run), "count": len(cues)}


def rename_list(event_dir: Path, name: Any, base_rev: Any,
                dry_run: bool = False) -> dict:
    """Give the list itself a name: the title of the console's GO card.
    Returns `{rev, name}`."""
    doc, rev, original = _read(event_dir)
    _guard(rev, base_rev)
    if rev is None:
        raise ValueError(f"there is no {FILE} to name yet: make a cue first")
    fresh = clean_name(name, "the cue list")
    if "name" in doc:
        doc["name"] = fresh
    else:
        # Before the cues, where a person would look for it.
        doc = {**{k: v for k, v in doc.items() if k != "cues"}, "name": fresh,
               **({"cues": doc["cues"]} if "cues" in doc else {})}
    return {"rev": _write(event_dir, doc, original, dry_run), "name": fresh}


# -- keeping the place ------------------------------------------------------------

def carry(old: Optional[cuesmod.CueList], cues: Sequence[cuesmod.Cue],
          renamed: Optional[tuple[str, str]] = None
          ) -> tuple[int, Optional[str]]:
    """Where a running list is, in the cues that replace it: `(index, lost)`.

    The list keeps its place BY NAME -- the cue that is current stays current
    wherever it has moved to, and under its new name after a rename
    (`renamed`: old, new). -1, before the first GO, stays -1.

    If the current cue is gone, `lost` is its name and the place is the
    nearest cue before it that is still there -- so GO takes what would have
    come next anyway -- or -1 if none is. What is on stage is not touched
    either way: an edit to the list is not a GO.
    """
    current = old.current if old is not None else None
    if old is None or current is None:
        return -1, None
    name = renamed[1] if renamed is not None and renamed[0] == current.name \
        else current.name
    names = [c.name for c in cues]
    if old.index < len(names) and names[old.index] == name:
        return old.index, None
    if name in names:
        return names.index(name), None
    for before in reversed(old.cues[:old.index]):
        if before.name in names:
            return names.index(before.name), current.name
    return -1, current.name


# -- what each cue is, and what is wrong with it --------------------------------------

def _tidy(value: Optional[float]) -> Any:
    if value is None:
        return None
    return int(value) if float(value).is_integer() else value


def cue_dict(cue: cuesmod.Cue) -> dict:
    """A loaded cue as an editor holds it: every field, unset ones null."""
    return {"name": cue.name, "movement": dict(cue.movement),
            "color": dict(cue.color), "level": dict(cue.level),
            "fade": _tidy(cue.fade), "hold": _tidy(cue.hold),
            "speed": _tidy(cue.speed), "master": _tidy(cue.master),
            "macro": json.loads(json.dumps(cue.macro)) if cue.macro is not None else None,
            "rates": dict(cue.rates) if cue.rates is not None else None,
            "params": (json.loads(json.dumps(cue.params))
                       if cue.params is not None else None),
            "notes": cue.notes}


def _slots(cue: Mapping) -> Iterable[tuple[str, str, str]]:
    """(slot, group, look) for every look a cue puts in a slot. A slot that
    is not an object -- a cue edited by hand -- holds nothing here."""
    for slot in SLOTS:
        held = cue.get(slot)
        if isinstance(held, Mapping):
            for group, look in held.items():
                yield slot, group, look


def missing(cue: Mapping, known: Iterable[str]) -> list[str]:
    """The looks a cue names that the library does not have, each once."""
    have = set(known)
    out: list[str] = []
    for _, _, look in _slots(cue):
        if look not in have and look not in out:
            out.append(look)
    return out


def known_groups(rig) -> set[str]:
    """Everything a slot's group can be on this rig: every tag any fixture
    carries (`Rig.tags` folds tags that hold the same fixtures into one, and a
    cue may name either), every fixture by name, and the lane a look with no
    groups of its own fills."""
    return ({tag for f in rig.fixtures for tag in f.tags}
            | {f.name for f in rig.fixtures} | {DEFAULT_GROUP})


def flags(cue: Mapping, by_name: Mapping[str, Any],
          groups: Optional[Iterable[str]] = None) -> list[str]:
    """What is wrong with a cue, in words. SAID, never refused: every one of
    these is a cue that loads and can be taken, and does less than it reads.

    `by_name` is the look library; `groups` what this rig's fixtures answer
    to (`known_groups`), when there is a rig to ask.
    """
    out: list[str] = []
    have = set(groups) if groups is not None else None
    named: list[str] = []
    movement = 0
    for slot, group, look in _slots(cue):
        entry = by_name.get(look) if isinstance(look, str) else None
        if isinstance(look, str) and look not in named:
            named.append(look)
        if entry is None:
            # The loader's own words for it (`cues.missing_looks`).
            out.append(f"{slot} wants {look!r} for {group!r}, which is not in "
                       f"the library: taking the cue skips it")
        elif entry.slot != slot:
            out.append(f"{slot} holds {look!r}, which is a {entry.slot} look")
        if slot == "movement":
            movement += 1
        if have is not None and group not in have:
            out.append(f"nothing on this rig is tagged {group!r} ({slot})")
    if movement > 1:
        out.append("movement names more than one look: the rig runs one "
                   "movement look, so only the first that exists is taken")
    params = cue.get("params")
    if isinstance(params, Mapping):
        strays = [look for look in params if look not in named]
        if strays:
            out.append(f"it carries tuning for {', '.join(repr(s) for s in strays)}, "
                       f"which it does not put in a slot: that tuning is ignored")
    return out


def describe(cue: cuesmod.Cue, by_name: Mapping[str, Any],
             groups: Optional[Iterable[str]] = None) -> dict:
    """One cue, as Studio's Cues page needs it."""
    out = cue_dict(cue)
    out["missing"] = missing(out, by_name)
    out["flags"] = flags(out, by_name, groups)
    return out
