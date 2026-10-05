"""
The show folder: tracks, timelines, routines and template sets, and the one
authoring API every surface goes through.

A show folder is the part of a timecoded show that outlives an event and moves
between machines -- the operator designs on a desktop and plays on the show
laptop, and the two share a Dropbox/OneDrive/NAS folder. So it lives OUTSIDE the
repo and the event directory, and the engine is pointed at it:

    <show-dir>/show.json               which template set, pause policy, sources
    <show-dir>/tracks/<id>.json        one prepped track: identity, grid, phrases
    <show-dir>/waveforms/<id>.json     its waveform, read on demand, never pushed
    <show-dir>/timelines/<track>.json  the hand-built show for one track
    <show-dir>/routines/<id>.json      reusable routines
    <show-dir>/templates/<id>.json     template sets: rekordbox phrase -> routine
    <show-dir>/schemas/*.schema.json   written by `init`, for editor completion

**One API, every surface.** The designer, the MCP server and the prep tool all
validate and write through this module, the way the patch editor, the CLI and
MCP all go through `patch.py`. Two copies of "what is a valid timeline" would
agree on the day they were written and not afterwards.

**Reject what is impossible, warn about what is suspicious.** A grid that runs
backwards, a clip with a fade longer than itself, a `$param` the routine never
declared: errors, and the file is not loaded or written. An item past the end of
the track, two clips overlapping on one lane, a timeline drawn on a grid that
has since changed: warnings, because each has a legitimate reason and refusing
it would make the designer fight the operator.

**One file per thing.** A shared folder syncs files, and two machines editing
the same file is how sync services make "conflicted copy" files. Keeping every
track and timeline in its own file makes that rare and small; when it happens,
the copy is never loaded, and the notice says what it is.

**Nothing here is authored in milliseconds.** Positions are beats on the track's
grid (`tracktime.py`); only physical durations -- a pause grace, a latency --
are in seconds. That is the engine's rule since F6 and it is why a timeline
survives the DJ's pitch fader.

Pure except for the file functions at the bottom, which do plain I/O on the
calling thread. The engine calls them from its worker, never the output thread.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional

from . import config as configmod
from . import timeline as timelinemod
from . import tracktime

REPO = Path(__file__).resolve().parent.parent
LOCAL_CONFIG = REPO / "klights.local.json"
ENV_VAR = "KLIGHTS_SHOW_DIR"

S = configmod.Spec
N = configmod.Number

# Every kind has its own format version. See config.validate: a timeline change
# must not force a version bump on every venue.json in every checkout.
KINDS = ("show", "track", "timeline", "routine", "template_set", "waveform")
FORMAT_VERSION = {k: 1 for k in KINDS}

SUBDIR = {"track": "tracks", "timeline": "timelines", "routine": "routines",
          "template_set": "templates", "waveform": "waveforms"}

# File names are ids. Lower case, so two machines with different
# case-sensitivity cannot disagree about whether "Fan-Drop" and "fan-drop" are
# one routine or two.
# `\Z`, not `$`: `$` also matches before a trailing newline, and "fan\n" would
# pass as an id -- and become a file name with a newline in it.
ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}\Z")
ID_FIX = ("lower-case letters, digits, '-' and '_', starting with a letter or "
          "digit, at most 64 characters. It is also the file name")

PALETTE_ROLES = ("primary", "secondary", "accent")
HITS = ("flash", "strobe", "blackout")
CURVES = timelinemod.CURVES
PARAM_TYPES = ("color", "number", "rate", "look")
SLOTS = ("movement", "color", "level")

# What a timeline lane can hold. `scene` takes whole routines and snapshots,
# which fill all three slots; the slot lanes take one slot each; `palette` is
# where the palette changes.
CLIP_TARGETS = ("scene",) + SLOTS + ("palette",)


def timeline_channels(row: Mapping) -> tuple[str, ...]:
    """The channels a timeline clips row drives, for `timeline.Timeline`: a
    scene lane drives all three slots, any other lane its own target. With the
    higher lane winning, a movement lane ABOVE a scene lane overrides the
    scene's movement and leaves its colour and level alone; below it, it only
    shows where the scene lane has nothing."""
    target = row["target"]
    return SLOTS if target == "scene" else (target,)

# Continuous values a timeline can automate, and their ranges. Size and spread
# are the ranges `ShowController._cmd_macro` clamps to, and rate is
# SlotPhases' 0-8 -- a curve that asks for more would be clamped at run time,
# so it is refused at authoring time instead.
#
# The CENTRE ranges are the portable fallback (`params.CENTER_*`), not the
# run-time clamp. At run time the centre is bounded by the playing rig's reach
# (`server.rig_reach`), which on a real rig is often wider on one side and
# narrower on the other. A show folder does not know which rig will play it, so
# it is authored against the range every rig can be expected to understand.
AUTOMATION_RANGES: dict[str, tuple[float, float]] = {
    "master": (0.0, 1.0),
    "size": (0.0, 3.0),
    "spread": (-1.0, 1.0),
    "center.bearing": (-180.0, 180.0),
    "center.elevation": (-90.0, 90.0),
    "rate.movement": (0.0, 8.0),
    "rate.color": (0.0, 8.0),
    "rate.level": (0.0, 8.0),
}

# The building blocks a routine's rows are made of. `engine/blocks.py` (F19h)
# implements exactly these; a test holds the two lists together. `look` and
# `snapshot` are the rig-bound adapters -- a routine using either must name its
# rig, which is what "this rig only" means.
BLOCK_NAMES = ("offset", "fan_sweep", "orbit", "pendulum", "figure8", "spiral",
               "scatter", "aim_points", "solid", "color_chase", "hue_cycle", "duo",
               "chase", "pulse", "breathe", "dim", "strobe",
               "look", "snapshot")
RIG_BOUND_BLOCKS = ("look", "snapshot")

COLOR_FIX = ('a palette role ("@primary", "@secondary", "@accent"), a hex '
             'colour like "#ff2d6f", [r, g, b] from 0 to 1, or a colour look '
             'name')

_SIGNATURE_RE = re.compile(r"^[0-9a-f]{40}\Z")
_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}\Z")


def _kind(k: str) -> S:
    return S(str, required=True, choices=(f"klights.{k}",),
             fix=f'"klights.{k}" -- it says which format the file is')


# -- the formats --------------------------------------------------------------

SHOW = {
    "kind": _kind("show"),
    "template_set": S(str, fix="the id of a file in templates/"),
    "fallback": S(str, choices=("auto", "operator"),
                  fix="what runs when a track has no timeline, no phrases and "
                      "no grid: today's auto mode, or nothing but the operator"),
    "pause": S(dict, of={
        "policy": S(str, choices=("idle", "freeze", "continue"),
                    fix="idle: hand over to idle_routine after grace_s. freeze: "
                        "hold still. continue: keep moving at the last tempo"),
        "grace_s": S(N, min=0, max=60, fix="seconds of silence before a deck "
                                           "counts as paused"),
        "idle_routine": S(str, fix="the id of a file in routines/"),
        "fade_beats": S(N, min=0, max=64),
    }),
    "sources": S(dict, fix='per source, e.g. {"rkbx": {"latency_ms": -15}}'),
    "follow": S(dict, of={
        "default": S(str, choices=("disarmed", "armed"),
                     fix="disarmed: the DJ feed shows but drives nothing until "
                         "someone arms it"),
        "min_track_change_s": S(N, min=0, max=60),
    }),
}

TRACK = {
    "kind": _kind("track"),
    "id": S(str, required=True, non_empty=True, fix=ID_FIX),
    "identity": S(dict, required=True, of={
        "title": S(str, required=True, non_empty=True),
        "artist": S(str), "album": S(str),
        "duration_s": S(N, min=0, max=14400),
        "bpm": S(N, min=20, max=400, fix="the track's own tempo, before pitch"),
    }),
    "ids": S(dict, of={
        "rekordbox": S(list, each=S(dict, of={
            "db": S(str, required=True, non_empty=True,
                    fix='where the id is valid, e.g. "usb:SANDISK" -- a '
                        'rekordbox id means nothing outside its database'),
            "id": S(int, required=True, min=0)})),
        "blt_signatures": S(list, each=S(str)),
    }),
    "aliases": S(list, each=S(dict, of={
        "title": S(str, required=True, non_empty=True),
        "artist": S(str), "album": S(str), "via": S(str), "added": S(str)})),
    "grid": S(dict, required=True, of={
        "rev": S(str),
        "segments": S(list, required=True, non_empty=True,
                      fix="[[beat, time_ms, bpm], ...]; beat 0 is the first "
                          "downbeat"),
    }),
    "phrases": S(dict, of={
        "mood": S(str, choices=("low", "mid", "high")),
        "items": S(list, required=True,
                   fix='[[start_beat, end_beat, "Label"], ...] with '
                       "rekordbox's labels as it shows them"),
    }),
    "cues": S(list, each=S(dict, of={
        "beat": S(N, required=True), "name": S(str),
        "kind": S(str, choices=("hot", "memory", "loop")), "slot": S(str)})),
    "audio": S(list, each=S(dict, of={
        "host": S(str), "path": S(str, required=True, non_empty=True),
        "size": S(int, min=0)})),
    "source": S(dict),
}

# The longest a timeline reaches, in beats: nine hours at 120 bpm. No track is
# that long; the bound is there so a typo ("len": 3200000) is refused with a
# reason instead of asking the compiler to build a clip that long.
MAX_BEATS = 65536

_ITEM = {
    "id": S(str, required=True, non_empty=True),
    "at": S(N, required=True, min=-64, max=MAX_BEATS,
            fix="beats from the track's first downbeat, not seconds"),
    "len": S(N, required=True, min=0, max=MAX_BEATS, fix="length in beats"),
    "fade": S(N, min=0, max=MAX_BEATS, fix="beats to fade in over, not seconds"),
}

_CLIP = S(dict, of=_ITEM, variants=("kind", {
    "routine": {"routine": S(str, required=True, non_empty=True),
                "variation": S(str), "params": S(dict), "bind": S(dict)},
    "look": {"look": S(str, required=True, non_empty=True),
             "groups": S(list, each=S(str))},
    "snapshot": {"preset": S(str), "movement": S(dict), "color": S(dict),
                 "level": S(dict)},
    "palette": {"palette": S(str, required=True, non_empty=True)},
}), fix="an item on a lane: a routine, a look, a snapshot or a palette change")

_HIT = S(dict, of={
    **_ITEM,
    "hit": S(str, required=True, choices=HITS),
    "role": S(str, fix="which fixtures; none means all of them"),
    "level": S(N, min=0, max=1),
    "envelope": S(str, choices=("hold", "decay")),
})

_ROW_COMMON = {"id": S(str, required=True, non_empty=True), "label": S(str)}

# `external` rows belong to outputs other than the lights -- VJ, in milestone 3.
# They are carried and preserved, never evaluated here, so a timeline written by
# a newer designer with a video lane still loads and saves on this engine.
_ROW = S(dict, of=_ROW_COMMON, variants=("type", {
    "clips": {"target": S(str, required=True, choices=CLIP_TARGETS),
              "gap": S(str, choices=("fill", "exclusive"),
                       fix="fill: lanes below, then the template, show "
                           "through gaps. exclusive: this lane owns the track "
                           "-- in its gaps nothing drives it"),
              "role": S(str),
              "items": S(list, required=True, each=_CLIP)},
    "hits": {"items": S(list, required=True, each=_HIT)},
    "automation": {"target": S(str, required=True, non_empty=True),
                   "points": S(list, required=True,
                               fix='[[beat, value], [beat, value, "ease"], ...]')},
    "external": {"output": S(str, required=True, non_empty=True)},
}))

_PALETTES = S(dict, fix='{"Cool": {"primary": "#3b82f6", "secondary": ..., '
                        '"accent": ...}}')

TIMELINE = {
    "kind": _kind("timeline"),
    "track": S(str, required=True, non_empty=True,
               fix="the id of the track in tracks/ this timeline belongs to"),
    "grid_rev": S(str, fix="the grid rev it was drawn on; set by the designer"),
    "palettes": _PALETTES,
    "palette": S(str, fix="the palette in force wherever no palette clip is"),
    "rows": S(list, required=True, each=_ROW),
}

_BLOCK_ITEM = S(dict, of={
    **_ITEM,
    "at": S(N, required=True, min=0, fix="beats from the routine's start"),
    "block": S(str, required=True, choices=BLOCK_NAMES),
    "args": S(dict, fix='arguments, and "$name" for an open parameter'),
})

_ROUTINE_ROW = S(dict, of=_ROW_COMMON, variants=("type", {
    "clips": {"target": S(str, required=True, choices=SLOTS),
              "role": S(str, required=True, non_empty=True,
                        fix="one of this routine's roles"),
              "items": S(list, required=True, each=_BLOCK_ITEM)},
    "hits": {"items": S(list, required=True, each=_HIT)},
    "automation": {"target": S(str, required=True, non_empty=True),
                   "points": S(list, required=True)},
}))

ROUTINE = {
    "kind": _kind("routine"),
    "id": S(str, required=True, non_empty=True, fix=ID_FIX),
    "name": S(str),
    "bars": S(N, required=True, min=0.25, max=256),
    "loop": S(bool),
    "rig": S(str, fix="the event a rig-bound routine was built for -- set when "
                      "any row uses a look or snapshot"),
    "roles": S(dict, required=True, non_empty=True,
               fix='{"movers": {"default": "movers"}} -- a role binds to a '
                   'rig tag unless the use site says otherwise'),
    "params": S(dict),
    "variations": S(dict),
    "rows": S(list, required=True, each=_ROUTINE_ROW),
    "folder": S(str, fix='where Studio files it, e.g. "Drops"; it changes '
                         'nothing about how it plays'),
}

# One open parameter of a routine. `default` is checked against `type` in
# meaning, since what it may be depends on the type.
_PARAM = S(dict, of={"type": S(str, required=True, choices=PARAM_TYPES,
                               fix="what kind of value the parameter takes"),
                     "min": S(N), "max": S(N), "unit": S(str)})

_PICK = S(dict, of={"routine": S(str, required=True, non_empty=True),
                    "variation": S(str), "params": S(dict),
                    "palette": S(str)})

TEMPLATE_SET = {
    "kind": _kind("template_set"),
    "id": S(str, required=True, non_empty=True, fix=ID_FIX),
    "name": S(str),
    "palettes": _PALETTES,
    "palette": S(str),
    "phrases": S(dict, required=True, non_empty=True,
                 fix='rekordbox label -> {"routine": id}. "Up" covers "Up 1" '
                     'and "Up 2"; "*" covers anything not listed'),
    "bars": S(dict, of={
        "every": S(N, required=True, min=1, max=256,
                   fix="bars per change when a track has a grid and no phrases"),
        "cycle": S(list, required=True, non_empty=True, each=_PICK),
    }),
    "transition": S(dict, of={"fade_beats": S(N, min=0, max=64)}),
}

WAVEFORM = {
    "kind": _kind("waveform"),
    "track": S(str, required=True, non_empty=True),
    "preview": S(str, fix="base64 of rekordbox's PWAV preview"),
    "detail": S(dict, of={"format": S(str, required=True), "rate": S(N, min=1),
                          "data": S(str, required=True)}),
}

SCHEMAS: dict[str, dict[str, S]] = {
    "show": SHOW, "track": TRACK, "timeline": TIMELINE, "routine": ROUTINE,
    "template_set": TEMPLATE_SET, "waveform": WAVEFORM,
}


# -- validation -----------------------------------------------------------------

@dataclass
class Result:
    """What validation found. `doc` is returned untouched: unknown keys, a
    `_comment`, a field a newer designer added -- all survive a round trip,
    because nothing here rebuilds a document from a model."""
    kind: str
    doc: Any
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


def kind_of(doc: Any) -> Optional[str]:
    """The kind a document says it is, or None."""
    if isinstance(doc, dict):
        k = doc.get("kind")
        if isinstance(k, str) and k.startswith("klights."):
            k = k[len("klights."):]
            return k if k in KINDS else None
    return None


def validate(kind: str, doc: Any, where: str = "") -> Result:
    """Shape, then meaning. Never raises for a bad document."""
    if kind not in SCHEMAS:
        raise ValueError(f"unknown kind {kind!r}")
    result = Result(kind, doc)
    try:
        configmod.validate(doc, SCHEMAS[kind], Path(where or f"{kind}.json"),
                           current=FORMAT_VERSION[kind])
    except configmod.ConfigError as exc:
        result.errors.extend(exc.problems)
        return result                  # meaning checks assume the shape holds
    _SEMANTIC[kind](doc, result)
    return result


def color_problem(value: Any) -> Optional[str]:
    """Why `value` is not a colour, or None if it is one."""
    if isinstance(value, str):
        if value.startswith("@"):
            if value[1:] not in PALETTE_ROLES:
                return (f"{value!r} is not a palette role; use @"
                        + ", @".join(PALETTE_ROLES))
            return None
        if value.startswith("#"):
            return None if _HEX_RE.match(value) else f"{value!r} is not #rrggbb"
        return None if value else "an empty colour"
    if (isinstance(value, list) and len(value) == 3
            and all(_num(v) and 0.0 <= v <= 1.0 for v in value)):
        return None
    return f"{value!r} is not a colour: {COLOR_FIX}"


def _num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _param_refs(value: Any) -> Iterable[str]:
    """Every "$name" inside an args value, however deeply nested."""
    if isinstance(value, str) and value.startswith("$") and len(value) > 1:
        yield value[1:]
    elif isinstance(value, list):
        for v in value:
            yield from _param_refs(v)
    elif isinstance(value, dict):
        for v in value.values():
            yield from _param_refs(v)


def _unique_ids(rows: list, result: Result, what: str) -> None:
    seen_rows: set = set()
    seen_items: set = set()
    for row in rows:
        if row["id"] in seen_rows:
            result.errors.append(f"two {what} rows share the id {row['id']!r}")
        seen_rows.add(row["id"])
        for item in row.get("items") or []:
            if item["id"] in seen_items:
                result.errors.append(
                    f"two items share the id {item['id']!r}; ids are how the "
                    f"designer and MCP address an item, so they must be unique")
            seen_items.add(item["id"])


def _shadowed_automation(rows: list, result: Result) -> None:
    """Two automation rows for one target: legal, but only the higher one is
    ever heard, which is worth saying."""
    first: dict[str, str] = {}
    for row in rows:
        if row["type"] != "automation" or not isinstance(row.get("target"), str):
            continue
        target = row["target"]
        if target in first:
            result.warnings.append(
                f"row {row['id']!r} automates {target}, as {first[target]!r} "
                f"above it already does; the higher row wins, so this one is "
                f"never heard")
        else:
            first[target] = row["id"]


def _check_items(row: dict, result: Result, where: str) -> None:
    """Length, fade, and overlap within one row of items."""
    spans = []
    for item in row.get("items") or []:
        at = f"{where} item {item['id']!r}"
        if item["len"] <= 0:
            result.errors.append(f"{at} has length {item['len']}; it must be "
                                 f"longer than nothing")
        if (item.get("fade") or 0) > item["len"]:
            result.errors.append(f"{at} fades in over {item['fade']} beats but "
                                 f"is only {item['len']} long")
        spans.append((item["at"], item["at"] + item["len"], item["id"]))
    spans.sort()
    for (a0, a1, aid), (b0, b1, bid) in zip(spans, spans[1:]):
        if b0 < a1:
            result.warnings.append(
                f"{where}: {aid!r} and {bid!r} overlap from beat {b0:g}; the "
                f"earlier one wins until it ends")


def _check_points(row: dict, result: Result, where: str,
                  allow_params: bool) -> None:
    target = row["target"]
    rng = AUTOMATION_RANGES.get(target)
    is_param = target.startswith("param.") and len(target) > len("param.")
    if rng is None and not (allow_params and is_param):
        allowed = ", ".join(AUTOMATION_RANGES) + (
            ", param.<name>" if allow_params else "")
        result.errors.append(f"{where}: {target!r} is not something that can be "
                             f"automated; one of {allowed}")
        return
    prev = None
    for i, point in enumerate(row["points"]):
        at = f"{where} point {i}"
        if (not isinstance(point, list) or len(point) not in (2, 3)
                or not _num(point[0])):
            result.errors.append(f"{at} must be [beat, value] or [beat, value, "
                                 f"curve], got {point!r}")
            continue
        beat, value = point[0], point[1]
        if len(point) == 3 and point[2] not in CURVES:
            result.errors.append(f"{at} curve {point[2]!r} must be one of "
                                 + ", ".join(CURVES))
        if prev is not None and beat <= prev:
            result.errors.append(f"{at} is at beat {beat:g}, not after the "
                                 f"previous point at {prev:g}")
        prev = beat
        if rng is not None:
            if not _num(value) or not rng[0] <= value <= rng[1]:
                result.errors.append(f"{at} {target} must be a number from "
                                     f"{rng[0]:g} to {rng[1]:g}, got {value!r}")
        elif not _num(value) and color_problem(value) is not None:
            result.errors.append(f"{at} must be a number or a colour, got "
                                 f"{value!r}")


def _check_palettes(doc: dict, result: Result) -> None:
    palettes = doc.get("palettes") or {}
    for name, pal in palettes.items():
        if not isinstance(pal, dict):
            result.errors.append(f"palette {name!r} must be an object of "
                                 f"primary, secondary and accent")
            continue
        for role in PALETTE_ROLES:
            if role not in pal:
                result.errors.append(f"palette {name!r} has no {role}")
                continue
            problem = color_problem(pal[role])
            if problem:
                result.errors.append(f"palette {name!r} {role}: {problem}")
            elif isinstance(pal[role], str) and pal[role].startswith("@"):
                result.errors.append(f"palette {name!r} {role} is {pal[role]!r}; "
                                     f"a palette is where roles get their "
                                     f"colour, so it cannot name one")
    default = doc.get("palette")
    if default is not None and default not in palettes:
        result.errors.append(f"palette {default!r} is not one of this file's "
                             f"palettes ({', '.join(palettes) or 'none'})")


def _semantic_show(doc: dict, result: Result) -> None:
    for name, src in (doc.get("sources") or {}).items():
        if not isinstance(src, dict):
            result.errors.append(f"sources.{name} must be an object")
            continue
        lat = src.get("latency_ms")
        if lat is not None and (not _num(lat) or not -2000 <= lat <= 2000):
            result.errors.append(f"sources.{name}.latency_ms must be a number "
                                 f"from -2000 to 2000, got {lat!r}")


def _semantic_track(doc: dict, result: Result) -> None:
    if not ID_RE.match(doc["id"]):
        result.errors.append(f"id {doc['id']!r} is not usable: {ID_FIX}")
    segments = doc["grid"]["segments"]
    problems = tracktime.grid_problems(segments)
    result.errors.extend(f"grid: {p}" for p in problems)
    if not problems:
        grid = tracktime.Grid.from_segments(segments)
        result.warnings.extend(f"grid: {w}" for w in grid.warnings())
        stated = doc["grid"].get("rev")
        if stated is not None and stated != grid.rev:
            result.warnings.append(
                f"grid.rev says {stated} but the segments are {grid.rev}; it was "
                f"hand-edited or re-gridded. Timelines drawn on {stated} will be "
                f"flagged")
    phrases = doc.get("phrases")
    if phrases is not None:
        result.errors.extend(f"phrases: {p}" for p in
                             tracktime.phrase_problems(phrases["items"]))
    for sig in (doc.get("ids") or {}).get("blt_signatures") or []:
        if not _SIGNATURE_RE.match(sig):
            result.errors.append(f"blt signature {sig!r} is not 40 lower-case "
                                 f"hex characters")


def _semantic_timeline(doc: dict, result: Result) -> None:
    _check_palettes(doc, result)
    palettes = doc.get("palettes") or {}
    rows = doc["rows"]
    _unique_ids(rows, result, "timeline")
    _shadowed_automation(rows, result)
    for row in rows:
        where = f"row {row['id']!r}"
        kind = row["type"]
        if kind == "external":
            continue                               # another output's business
        if kind == "automation":
            _check_points(row, result, where, allow_params=True)
            continue
        _check_items(row, result, where)
        if kind == "hits":
            continue
        target = row["target"]
        for item in row["items"]:
            at = f"{where} item {item['id']!r}"
            ik = item["kind"]
            if (target == "palette") != (ik == "palette"):
                result.errors.append(
                    f"{at}: a {ik} cannot go on the {target} lane"
                    + (" -- palette changes live on the palette lane"
                       if ik == "palette" else
                       " -- the palette lane holds only palette changes"))
            if ik == "snapshot" and target != "scene":
                result.errors.append(f"{at}: a snapshot sets all three slots, so "
                                     f"it belongs on the scene lane")
            if ik == "snapshot" and not any(
                    item.get(k) for k in ("preset",) + SLOTS):
                result.errors.append(f"{at}: a snapshot needs a preset or at "
                                     f"least one of {', '.join(SLOTS)}")
            if ik == "palette" and item["palette"] not in palettes:
                result.errors.append(f"{at}: palette {item['palette']!r} is not "
                                     f"defined in this timeline's palettes")
            if ik == "routine":
                for name, value in (item.get("params") or {}).items():
                    if isinstance(value, str) and value.startswith("@"):
                        problem = color_problem(value)
                        if problem:
                            result.errors.append(f"{at} param {name}: {problem}")


def _semantic_routine(doc: dict, result: Result) -> None:
    if not ID_RE.match(doc["id"]):
        result.errors.append(f"id {doc['id']!r} is not usable: {ID_FIX}")
    roles = doc["roles"]
    for name, role in roles.items():
        if not isinstance(role, dict) or not isinstance(role.get("default"), str) \
                or not role.get("default"):
            result.errors.append(f"role {name!r} needs a default tag, e.g. "
                                 f'{{"default": "movers"}}')
    params = doc.get("params") or {}
    usable: dict = {}
    for name, p in params.items():
        where = f"param {name!r}"
        if not isinstance(p, dict) or p.get("type") not in PARAM_TYPES:
            result.errors.append(f"{where} needs a type: "
                                 + ", ".join(PARAM_TYPES))
            continue
        problems: list[str] = []
        configmod._check(p, _PARAM, where, problems)
        if problems:
            result.errors.extend(problems)
            continue
        usable[name] = p
        if "default" in p and p["default"] is not None:
            problem = _param_value_problem(p, p["default"])
            if problem:
                result.errors.append(f"{where} default: {problem}")
    for vname, values in (doc.get("variations") or {}).items():
        if not isinstance(values, dict):
            result.errors.append(f"variation {vname!r} must be an object of "
                                 f"param values")
            continue
        for pname, value in values.items():
            if pname not in params:
                result.errors.append(f"variation {vname!r} sets {pname!r}, which "
                                     f"is not one of this routine's params")
                continue
            problem = _param_value_problem(usable.get(pname), value)
            if problem:
                result.errors.append(f"variation {vname!r} {pname}: {problem}")

    rows = doc["rows"]
    _unique_ids(rows, result, "routine")
    _shadowed_automation(rows, result)
    length = doc["bars"] * tracktime.BEATS_PER_BAR
    rig_bound = False
    for row in rows:
        where = f"row {row['id']!r}"
        if row["type"] == "automation":
            _check_points(row, result, where, allow_params=False)
            continue
        _check_items(row, result, where)
        role = row.get("role")
        if role is not None and role not in roles:
            result.errors.append(f"{where} uses role {role!r}, which this "
                                 f"routine does not declare")
        for item in row["items"]:
            at = f"{where} item {item['id']!r}"
            if item.get("role") is not None and item["role"] not in roles:
                result.errors.append(f"{at} uses role {item['role']!r}, which "
                                     f"this routine does not declare")
            if item["at"] + item["len"] > length + 1e-9:
                result.warnings.append(f"{at} runs to beat "
                                       f"{item['at'] + item['len']:g}, past the "
                                       f"routine's {length:g}")
            if item.get("block") in RIG_BOUND_BLOCKS:
                rig_bound = True
            for ref in _param_refs(item.get("args") or {}):
                if ref not in params:
                    result.errors.append(f"{at} refers to ${ref}, which this "
                                         f"routine does not declare in params")
    if rig_bound and not doc.get("rig"):
        result.errors.append(
            "this routine uses a look or snapshot, which only exist on one rig; "
            'say which with "rig": "<event>" so it is shown as this-rig-only')


def _param_value_problem(param: Any, value: Any) -> Optional[str]:
    """Why `value` cannot be given to `param`, or None. A param whose own
    definition is broken is reported where it is defined; here it accepts
    anything, so one mistake is not reported at every place it is used -- and
    so `validate` keeps its promise never to raise."""
    if not isinstance(param, dict) or param.get("type") not in PARAM_TYPES:
        return None
    kind = param["type"]
    if kind == "color":
        return color_problem(value)
    if kind == "look":
        return None if isinstance(value, str) and value else "must be a look name"
    if not _num(value):
        return f"must be a number, got {value!r}"
    lo = param.get("min")
    hi = param.get("max")
    lo = lo if _num(lo) else (0.0 if kind == "rate" else None)
    hi = hi if _num(hi) else (8.0 if kind == "rate" else None)
    if lo is not None and value < lo:
        return f"{value} is below the minimum {lo}"
    if hi is not None and value > hi:
        return f"{value} is above the maximum {hi}"
    return None


def _semantic_template_set(doc: dict, result: Result) -> None:
    if not ID_RE.match(doc["id"]):
        result.errors.append(f"id {doc['id']!r} is not usable: {ID_FIX}")
    _check_palettes(doc, result)
    palettes = doc.get("palettes") or {}
    picks = [(f"phrase {label!r}", pick)
             for label, pick in doc["phrases"].items()]
    picks += [(f"bars.cycle[{i}]", pick)
              for i, pick in enumerate((doc.get("bars") or {}).get("cycle") or [])]
    for where, pick in picks:
        problems: list[str] = []
        configmod._check(pick, _PICK, where, problems)
        result.errors.extend(problems)
        if problems:
            continue
        if pick.get("palette") is not None and pick["palette"] not in palettes:
            result.errors.append(f"{where}: palette {pick['palette']!r} is not "
                                 f"defined in this template set")


def _semantic_waveform(doc: dict, result: Result) -> None:
    pass


_SEMANTIC = {"show": _semantic_show, "track": _semantic_track,
             "timeline": _semantic_timeline, "routine": _semantic_routine,
             "template_set": _semantic_template_set,
             "waveform": _semantic_waveform}


# -- a whole folder -----------------------------------------------------------

@dataclass
class Folder:
    """Everything valid in a show folder, plus what was wrong with the rest.

    A snapshot: built in one pass and never mutated afterwards, so it can be
    handed to the output thread by assigning one reference.
    """
    root: Path
    show: Optional[dict] = None
    tracks: dict[str, dict] = field(default_factory=dict)
    timelines: dict[str, dict] = field(default_factory=dict)   # by track id
    routines: dict[str, dict] = field(default_factory=dict)
    templates: dict[str, dict] = field(default_factory=dict)
    waveforms: set[str] = field(default_factory=set)            # ids only
    revs: dict[str, str] = field(default_factory=dict)          # rel path -> rev
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    # rel path -> why, for files that failed and are running on their last good
    # version instead (see load_folder's `previous`)
    failed: dict[str, str] = field(default_factory=dict)


_CONFLICT_RE = re.compile(
    r"conflicted copy|case conflict|\.sync-conflict-|\(\d+\)$", re.IGNORECASE)


def is_conflict_copy(stem: str) -> bool:
    """A duplicate a sync service made when two machines edited one file:
    Dropbox's "(conflicted copy ...)", Syncthing's ".sync-conflict-", or a
    numbered "name (1)". Never loaded -- which of the two is right is a
    decision for a person, and loading either would make it silently."""
    return bool(_CONFLICT_RE.search(stem))


def rev_of(data: bytes) -> str:
    """A file revision: a hash of its bytes, not its mtime. Sync services touch
    mtimes on files they did not change, and a rev that moved on its own would
    refuse a perfectly good save."""
    return "r:" + hashlib.sha1(data).hexdigest()[:12]


def doc_rev(path: Path) -> Optional[str]:
    try:
        return rev_of(Path(path).read_bytes())
    except FileNotFoundError:
        return None


def path_for(root: Path, kind: str, ident: str) -> Path:
    """Where a document of this kind and id lives. Refuses an id that is not a
    plain file name, so no caller can be talked into writing outside the
    folder."""
    if kind == "show":
        return Path(root) / "show.json"
    if not ID_RE.match(ident or ""):
        raise ValueError(f"{ident!r} is not a usable id: {ID_FIX}")
    return Path(root) / SUBDIR[kind] / f"{ident}.json"


def doc_ident(kind: str, doc: dict) -> Optional[str]:
    """The id a document's file is named after. A timeline is named after its
    track, because there is one per track."""
    if kind == "timeline":
        return doc.get("track")
    if kind == "waveform":
        return doc.get("track")
    return doc.get("id")


def read_doc(path: Path, kind: str) -> tuple[Result, Optional[str]]:
    """Read, parse and validate one file. Returns (result, rev)."""
    path = Path(path)
    try:
        data = path.read_bytes()
    except OSError as exc:
        return Result(kind, None, [f"{path.name}: cannot read: {exc}"]), None
    try:
        doc = json.loads(data.decode("utf-8"))
    except UnicodeDecodeError:
        return Result(kind, None, [f"{path.name}: is not UTF-8 text"]), None
    except json.JSONDecodeError as exc:
        return Result(kind, None, [
            f"{path.name}: is not valid JSON: {exc.msg} at line {exc.lineno}, "
            f"column {exc.colno}"]), None
    result = validate(kind, doc, path.name)
    result.errors = [f"{path.name}: {e}" for e in result.errors]
    result.warnings = [f"{path.name}: {w}" for w in result.warnings]
    if result.ok and kind != "show":
        ident = doc_ident(kind, doc)
        if ident != path.stem:
            result.errors.append(
                f"{path.name}: says it is {ident!r} but is named {path.stem!r}; "
                f"the file name is the id")
    return result, rev_of(data)


def load_folder(root: Path, previous: Optional[Folder] = None) -> Folder:
    """Read a whole show folder. Invalid files are reported and left out;
    nothing is raised for a bad file, because one bad timeline must not stop
    the other forty loading.

    Given the `previous` load, a file that WAS good and now is not keeps its
    last good version, and `failed` says so. That is a reload during a show: a
    timeline half-written by a sync, or saved mid-edit with a stray comma, must
    not make the track it belongs to go dark. A file that was never good has
    nothing to keep and is simply left out, as on a first load.

    Cross-file problems -- a timeline for a track not in the library, a routine
    clip naming a routine that is not there -- are warnings: in a folder that
    syncs one file at a time, the other half of a pair can simply not have
    arrived yet.
    """
    root = Path(root)
    folder = Folder(root=root)
    before = _by_rel(previous) if previous is not None else {}
    if not root.is_dir():
        folder.errors.append(f"show folder {root} does not exist; create it "
                             f"with `python -m engine.showfiles init {root}`")
        return folder

    show_path = root / "show.json"
    if show_path.exists():
        result, rev = read_doc(show_path, "show")
        folder.errors.extend(result.errors)
        folder.warnings.extend(result.warnings)
        if result.ok:
            folder.show = result.doc
            folder.revs["show.json"] = rev
        elif "show.json" in before:
            folder.show = before["show.json"][0]
            _kept(folder, "show.json", before["show.json"][1], result)

    targets = {"track": folder.tracks, "timeline": folder.timelines,
               "routine": folder.routines, "template_set": folder.templates}
    for kind, sub in SUBDIR.items():
        directory = root / sub
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob("*.json")):
            rel = f"{sub}/{path.name}"
            if is_conflict_copy(path.stem):
                folder.warnings.append(
                    f"{rel} looks like a sync conflict copy and is not loaded; "
                    f"merge it into the original by hand and delete it")
                continue
            if not ID_RE.match(path.stem):
                folder.warnings.append(f"{rel} is not loaded: file names are "
                                       f"ids -- {ID_FIX}")
                continue
            if kind == "waveform":
                folder.waveforms.add(path.stem)     # read on demand, never here
                continue
            result, rev = read_doc(path, kind)
            folder.errors.extend(f"{sub}/{e}" for e in result.errors)
            folder.warnings.extend(f"{sub}/{w}" for w in result.warnings)
            if result.ok:
                targets[kind][path.stem] = result.doc
                folder.revs[rel] = rev
            elif rel in before:
                targets[kind][path.stem] = before[rel][0]
                _kept(folder, rel, before[rel][1], result)

    _cross_check(folder)
    return folder


def _by_rel(folder: Folder) -> dict[str, tuple[dict, str]]:
    """A loaded folder's documents by relative path, with their revs."""
    out: dict[str, tuple[dict, str]] = {}
    if folder.show is not None and "show.json" in folder.revs:
        out["show.json"] = (folder.show, folder.revs["show.json"])
    for kind, docs in (("track", folder.tracks), ("timeline", folder.timelines),
                       ("routine", folder.routines),
                       ("template_set", folder.templates)):
        for ident, doc in docs.items():
            rel = f"{SUBDIR[kind]}/{ident}.json"
            if rel in folder.revs:
                out[rel] = (doc, folder.revs[rel])
    return out


def _kept(folder: Folder, rel: str, rev: str, result: Result) -> None:
    why = result.errors[0] if result.errors else "could not be read"
    folder.failed[rel] = why
    folder.revs[rel] = rev
    folder.warnings.append(f"{rel}: kept the last good version until the "
                           f"file is fixed")


def _cross_check(folder: Folder) -> None:
    warn = folder.warnings.append
    for track_id, tl in folder.timelines.items():
        track = folder.tracks.get(track_id)
        if track is None:
            warn(f"timelines/{track_id}.json: track {track_id!r} is not in "
                 f"tracks/ -- prep it, or it can never match")
            continue
        grid = tracktime.Grid.from_segments(track["grid"]["segments"])
        if tl.get("grid_rev") and tl["grid_rev"] != grid.rev:
            warn(f"timelines/{track_id}.json was drawn on grid {tl['grid_rev']} "
                 f"and the track is now {grid.rev}: it was re-gridded since, "
                 f"so every item may be off. Re-anchor it in the designer")
        duration = track["identity"].get("duration_s")
        end_beat = grid.beat_at(duration) if duration else None
        for row in tl["rows"]:
            for item in row.get("items") or []:
                if end_beat is not None and item["at"] >= end_beat:
                    warn(f"timelines/{track_id}.json item {item['id']!r} starts "
                         f"at beat {item['at']:g}, after the track ends "
                         f"(beat {end_beat:.0f})")
                if item.get("kind") == "routine":
                    _check_use(folder, f"timelines/{track_id}.json item "
                                       f"{item['id']!r}", item)
    for set_id, ts in folder.templates.items():
        picks = list(ts["phrases"].values()) + list(
            (ts.get("bars") or {}).get("cycle") or [])
        for pick in picks:
            _check_use(folder, f"templates/{set_id}.json", pick)
    if folder.show:
        ts = folder.show.get("template_set")
        if ts and ts not in folder.templates:
            warn(f"show.json names template set {ts!r}, which is not in "
                 f"templates/")
        idle = (folder.show.get("pause") or {}).get("idle_routine")
        if idle and idle not in folder.routines:
            warn(f"show.json names idle routine {idle!r}, which is not in "
                 f"routines/")


def _check_use(folder: Folder, where: str, use: dict) -> None:
    """A routine used somewhere: that it exists, and that what the use site
    sets is something the routine actually has."""
    routine = folder.routines.get(use["routine"])
    if routine is None:
        folder.warnings.append(f"{where} uses routine {use['routine']!r}, which "
                               f"is not in routines/")
        return
    variation = use.get("variation")
    if variation and variation not in (routine.get("variations") or {}):
        folder.warnings.append(f"{where}: routine {use['routine']!r} has no "
                               f"variation {variation!r}")
    params = routine.get("params") or {}
    for name, value in (use.get("params") or {}).items():
        if name not in params:
            folder.warnings.append(f"{where} sets {name!r}, which routine "
                                   f"{use['routine']!r} does not have")
            continue
        problem = _param_value_problem(params[name], value)
        if problem:
            folder.warnings.append(f"{where} {name}: {problem}")
    for role in (use.get("bind") or {}):
        if role not in routine["roles"]:
            folder.warnings.append(f"{where} binds role {role!r}, which routine "
                                   f"{use['routine']!r} does not have")


# -- writing ------------------------------------------------------------------

# -- where a routine is used, and renaming one everywhere ---------------------

def routine_usage(folder: "Folder") -> dict[str, dict]:
    """Every place each routine is used, by routine id: the timelines that
    place it (with how many clips, and which variations), the template sets
    that pick it (and for which phrases), and show.json's idle routine.
    A routine with no entry is used nowhere."""
    out: dict[str, dict] = {}

    def entry(rid: str) -> dict:
        return out.setdefault(rid, {"timelines": [], "templates": [], "show": []})

    for track_id, tl in sorted(folder.timelines.items()):
        found: dict[str, dict] = {}
        for row in tl.get("rows") or ():
            for item in (row.get("items") or ()) if isinstance(row, dict) else ():
                rid = item.get("routine") if item.get("kind") == "routine" else None
                if not isinstance(rid, str):
                    continue
                use = found.setdefault(rid, {"clips": 0, "variations": set()})
                use["clips"] += 1
                if isinstance(item.get("variation"), str):
                    use["variations"].add(item["variation"])
        title = ((folder.tracks.get(track_id) or {}).get("identity") or {}).get("title")
        for rid, use in found.items():
            entry(rid)["timelines"].append({"track": track_id, "title": title,
                                            "clips": use["clips"],
                                            "variations": sorted(use["variations"])})
    for set_id, ts in sorted(folder.templates.items()):
        where: dict[str, list[str]] = {}
        for label, pick in (ts.get("phrases") or {}).items():
            if isinstance(pick, dict) and isinstance(pick.get("routine"), str):
                where.setdefault(pick["routine"], []).append(
                    "anything else" if label == "*" else label)
        for i, pick in enumerate((ts.get("bars") or {}).get("cycle") or ()):
            if isinstance(pick, dict) and isinstance(pick.get("routine"), str):
                where.setdefault(pick["routine"], []).append(f"bar cycle step {i + 1}")
        for rid, labels in where.items():
            entry(rid)["templates"].append({"id": set_id, "name": ts.get("name"),
                                            "where": labels})
    idle = ((folder.show or {}).get("pause") or {}).get("idle_routine")
    if isinstance(idle, str) and idle:
        entry(idle)["show"].append("the idle routine, when the decks pause")
    return out


def routine_uses(folder: "Folder", rid: str) -> list[str]:
    """Where a routine is used, as the files that name it, in words."""
    use = routine_usage(folder).get(rid)
    if use is None:
        return []
    return ([f"timelines/{t['track']}.json ({t['clips']} clip{'s' if t['clips'] != 1 else ''})"
             for t in use["timelines"]]
            + [f"templates/{t['id']}.json ({', '.join(t['where'])})" for t in use["templates"]]
            + (["show.json (idle routine)"] if use["show"] else []))


def _renamed(doc: dict, kind: str, old: str, new: str) -> Optional[dict]:
    """A copy of `doc` with routine `old` called `new` wherever it is named,
    or None if it never names it."""
    copy = json.loads(json.dumps(doc))
    hits = 0
    if kind == "timeline":
        for row in copy.get("rows") or ():
            for item in (row.get("items") or ()) if isinstance(row, dict) else ():
                if item.get("kind") == "routine" and item.get("routine") == old:
                    item["routine"] = new
                    hits += 1
    elif kind == "template_set":
        picks = list((copy.get("phrases") or {}).values()) + list(
            (copy.get("bars") or {}).get("cycle") or ())
        for pick in picks:
            if isinstance(pick, dict) and pick.get("routine") == old:
                pick["routine"] = new
                hits += 1
    elif kind == "show":
        pause = copy.get("pause") or {}
        if pause.get("idle_routine") == old:
            pause["idle_routine"] = new
            hits += 1
    return copy if hits else None


def rename_routine(root: Path, folder: "Folder", old: str, new: str,
                   base_rev: str) -> list[str]:
    """Rename a routine, and every reference to it: the timelines that place
    it, the template sets that pick it, show.json's idle routine. Returns the
    files written, the new routine's first.

    A folder of files has no transaction, so the order is what keeps it whole
    at every step: the routine is written under its new name FIRST, then each
    reference is moved over, and the old file goes LAST. Stopped anywhere --
    a file someone else changed meanwhile, a disk error -- every reference
    still names a routine that exists, and the error says what was done.
    Each write quotes the rev the folder was read at, so nothing changed since
    is overwritten."""
    if not ID_RE.match(new or ""):
        raise ValueError(f"{new!r} is not a usable id: {ID_FIX}")
    if new == old:
        raise ValueError("that is its name already")
    doc = folder.routines.get(old)
    if doc is None:
        raise ValueError(f"no routine {old!r}")
    if new in folder.routines:
        raise ValueError(f"there is already a routine {new!r}")
    old_path = path_for(root, "routine", old)
    if doc_rev(old_path) != base_rev:
        raise StaleEdit(f"{old_path.name} changed since you opened it (another "
                        f"machine, MCP, or another tab saved it). Reload and "
                        f"rename it again")
    renamed = json.loads(json.dumps(doc))
    renamed["id"] = new
    if renamed.get("name") in (None, "", old):
        renamed["name"] = new
    write_doc(path_for(root, "routine", new), renamed, "routine", "")
    written = [f"{SUBDIR['routine']}/{new}.json"]
    try:
        for kind, docs in (("timeline", folder.timelines), ("template_set", folder.templates)):
            for ident, ref in sorted(docs.items()):
                changed = _renamed(ref, kind, old, new)
                if changed is None:
                    continue
                rel = f"{SUBDIR[kind]}/{ident}.json"
                write_doc(path_for(root, kind, ident), changed, kind,
                          folder.revs.get(rel, ""))
                written.append(rel)
        if folder.show is not None:
            changed = _renamed(folder.show, "show", old, new)
            if changed is not None:
                write_doc(path_for(root, "show", ""), changed, "show",
                          folder.revs.get("show.json", ""))
                written.append("show.json")
    except (ValueError, OSError, configmod.ConfigError) as exc:
        raise ValueError(f"renamed as far as {', '.join(written)}, then stopped: {exc}. "
                         f"{old!r} is still there, so nothing names a routine "
                         f"that is gone; finish by hand or rename again") from exc
    old_path.unlink()
    return written


def delete_doc(path: Path, base_rev: str) -> None:
    """Remove one document, refused if it changed since `base_rev` -- the
    same rule as a save, for the same reason."""
    current = doc_rev(path)
    if current is None:
        raise ValueError(f"{path.name} is not there")
    if current != base_rev:
        raise StaleEdit(f"{path.name} changed since you opened it (another "
                        f"machine, MCP, or another tab saved it). Reload it, "
                        f"and delete it then if you still mean to")
    path.unlink()


class StaleEdit(ValueError):
    """The file changed after the editor read it -- a sync from another machine,
    an MCP write, another designer tab. Refused, because saving over it would
    silently throw that change away."""


def write_doc(path: Path, doc: dict, kind: Optional[str] = None,
              base_rev: Optional[str] = None) -> str:
    """Validate and write one document atomically. Returns its new rev.

    `base_rev` is the rev the editor read: the write is refused with StaleEdit
    if the file has changed since. `""` means "this is new; refuse if a file is
    already there". `None` skips the check, for tools that own the file (the
    prep tool rewriting a track's analysis).

    The check and the write are not one atomic step -- a sync landing between
    them is possible, and a filesystem gives no compare-and-swap to close it.
    The window is the time to write one small file.
    """
    path = Path(path)
    kind = kind or kind_of(doc)
    if kind is None:
        raise configmod.ConfigError(path, ['kind must be "klights.<format>"'])
    result = validate(kind, doc, path.name)
    if result.errors:
        raise configmod.ConfigError(path, result.errors)
    if kind != "show":
        ident = doc_ident(kind, doc)
        if ident != path.stem:
            raise configmod.ConfigError(path, [
                f"says it is {ident!r} but would be written as {path.stem!r}; "
                f"the file name is the id"])
    current = doc_rev(path)
    if base_rev is not None:
        if base_rev == "" and current is not None:
            raise StaleEdit(f"{path.name} already exists; open it and edit it "
                            f"rather than creating it again")
        if base_rev != "" and base_rev != current:
            raise StaleEdit(f"{path.name} changed since you opened it (another "
                            f"machine, MCP, or another tab saved it). Reload it "
                            f"and make the change again")
    configmod.write_json_atomic(path, doc)
    rev = doc_rev(path)
    assert rev is not None
    return rev


def schema_ref(kind: str) -> str:
    """The `$schema` a new document gets, relative to where it is written."""
    name = f"{kind}.schema.json"
    return f"schemas/{name}" if kind == "show" else f"../schemas/{name}"


def new_doc(kind: str, **fields: Any) -> dict:
    """A document with its `$schema`, kind and version filled in."""
    doc: dict = {"$schema": schema_ref(kind), "kind": f"klights.{kind}",
                 "version": FORMAT_VERSION[kind]}
    doc.update(fields)
    return doc


DEFAULT_SHOW = {
    "fallback": "auto",
    "pause": {"policy": "idle", "grace_s": 4, "fade_beats": 4},
    "sources": {"rkbx": {"latency_ms": 0}, "blt": {"latency_ms": 0},
                "designer": {"latency_ms": 0}},
    "follow": {"default": "disarmed", "min_track_change_s": 2},
}


def init(root: Path) -> list[str]:
    """Create or complete a show folder. Never overwrites a document; refreshes
    the schema copies, which are generated and belong to the engine."""
    root = Path(root)
    done: list[str] = []
    for sub in SUBDIR.values():
        d = root / sub
        if not d.is_dir():
            d.mkdir(parents=True)
            done.append(f"created {sub}/")
    schemas = root / "schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    for kind in KINDS:
        src = REPO / "schemas" / f"{kind}.schema.json"
        if src.exists():
            shutil.copyfile(src, schemas / src.name)
    done.append("refreshed schemas/")
    show = root / "show.json"
    if not show.exists():
        write_doc(show, new_doc("show", **json.loads(json.dumps(DEFAULT_SHOW))),
                  "show", base_rev="")
        done.append("wrote show.json")
    return done


# -- where the folder is ------------------------------------------------------

def read_local_config(path: Path = LOCAL_CONFIG) -> dict:
    """`klights.local.json` at the repo root: per-machine settings that must
    not be committed, because a Dropbox path on the desktop is not the Dropbox
    path on the show laptop. Missing or unreadable means no settings."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def resolve_show_dir(cli: Optional[str] = None,
                     env: Optional[Mapping[str, str]] = None,
                     local: Optional[Path] = None) -> Optional[Path]:
    """Where the show folder is: `--show-dir`, then $KLIGHTS_SHOW_DIR, then
    `klights.local.json`'s "show_dir". None when nothing says -- a show without
    timecode is still a show, and the engine runs exactly as it did."""
    if cli:
        return Path(cli).expanduser()
    env = os.environ if env is None else env
    if env.get(ENV_VAR):
        return Path(env[ENV_VAR]).expanduser()
    configured = read_local_config(local or LOCAL_CONFIG).get("show_dir")
    if isinstance(configured, str) and configured:
        return Path(configured).expanduser()
    return None


# -- command line -------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(
        prog="python -m engine.showfiles",
        description="Create or check a show folder.")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_init = sub.add_parser("init", help="create or complete a show folder")
    p_init.add_argument("dir")
    p_check = sub.add_parser("check", help="validate every file in a folder")
    p_check.add_argument("dir", nargs="?",
                         help="defaults to the configured show folder")
    p_explain = sub.add_parser(
        "explain", help="what a track's timeline says at a beat")
    p_explain.add_argument("track", help="the track id")
    p_explain.add_argument("beat", type=float,
                           help="beats from the first downbeat (bar n starts "
                                "at beat 4*(n-1))")
    p_explain.add_argument("--dir", help="defaults to the configured show "
                                         "folder")
    args = parser.parse_args(argv)

    if args.cmd == "init":
        for line in init(Path(args.dir)):
            print(f"  {line}")
        return 0

    root = resolve_show_dir(args.dir)
    if root is None:
        print("no show folder: pass one, set KLIGHTS_SHOW_DIR, or set "
              "show_dir in klights.local.json", file=sys.stderr)
        return 2
    folder = load_folder(root)
    if args.cmd == "explain":
        doc = folder.timelines.get(args.track)
        if doc is None:
            print(f"no valid timeline for {args.track!r} in {root}/timelines",
                  file=sys.stderr)
            for e in folder.errors:
                print(f"  ERROR  {e}", file=sys.stderr)
            return 1
        timeline = timelinemod.Timeline.from_doc(doc, timeline_channels)
        print(json.dumps(timeline.explain(args.beat), indent=2))
        return 0
    print(f"{root}: {len(folder.tracks)} tracks, {len(folder.timelines)} "
          f"timelines, {len(folder.routines)} routines, "
          f"{len(folder.templates)} template sets")
    for e in folder.errors:
        print(f"  ERROR  {e}")
    for w in folder.warnings:
        print(f"  warn   {w}")
    return 1 if folder.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
