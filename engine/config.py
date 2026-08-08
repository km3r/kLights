"""Reading and writing the show's config files, safely.

Two jobs, both learned from the same place: a config file is edited by hand, at
a venue, on a laptop, by someone who is already behind.

**Validation.** Every loader used to read its fields positionally --
`cfg["width"]`, `float(c["head_band_min"])` -- so a typo surfaced as
`KeyError: 'head_band_min'` with a traceback through three modules, and a
string where a number belonged surfaced as a `TypeError` somewhere later, or
did not surface at all. The spec lived entirely in the `_comment` blocks. This
module checks a file against a declared shape and reports *every* problem at
once, naming the file, the path within it, what was found and what to do:

    venue.json: crowd_zone.head_band_min must be a number, got "1400" (str)
      fix: remove the quotes -- these are millimetres, not text

Deliberately NOT a JSON Schema implementation. The `schemas/` directory holds
real JSON Schema for editors to consume, and duplicating it here would mean two
sources of truth that drift. This is the narrow subset the loaders actually
need, with error messages written for the person at the venue rather than for a
validator. Where the two disagree, the loader is the one that runs the show.

**Atomic writes.** `venue.json`, `calibration.json` and `presets.json` are
written from the running show -- the UI saves a taper policy mid-set and the
solver overwrites a calibration between doors and the first track. They were
written with a plain `write_text`, which truncates the file and then writes: a
crash, a full disk or a laptop lid closed at the wrong instant leaves a
zero-length `calibration.json`, and the show does not start. Writing beside the
target and renaming makes the swap atomic on every platform we run on, and one
`.bak` means a bad write is recoverable without git.
"""

from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence, Union

REPO = Path(__file__).resolve().parent.parent

# The version every file is at today. A file with no `version` is treated as 1
# rather than rejected: every config written before this existed is a valid 1,
# and refusing to load them would make the guard its own outage.
CURRENT_VERSION = 1

Number = (int, float)


class ConfigError(ValueError):
    """A config file that cannot be used. Carries every problem, not the first.

    One exception with a list beats failing on the first bad key, because a
    hand-edited file usually has the same mistake in several places and fixing
    them one restart at a time is how a load-in runs late.
    """

    def __init__(self, path: Path, problems: Sequence[str]):
        self.path = Path(path)
        self.problems = list(problems)
        body = "\n".join(f"  {p}" for p in self.problems)
        super().__init__(f"{self.path.name} is not usable:\n{body}")


@dataclass(frozen=True)
class Spec:
    """What one key is allowed to be.

    `fix` is the point of this class. A validator that says "expected number,
    got str" has told you what it saw; the file's own `_comment` block already
    told you the units. What is missing at 4pm is what to *do*, so every spec
    that can plausibly be got wrong carries a sentence of it.
    """
    types: Union[type, tuple[type, ...]] = object
    required: bool = False
    min: Optional[float] = None
    max: Optional[float] = None
    choices: Optional[tuple[Any, ...]] = None
    of: Optional[dict[str, "Spec"]] = None        # nested object
    each: Optional["Spec"] = None                 # every element of a list
    non_empty: bool = False
    fix: str = ""

    def describe(self) -> str:
        if self.choices:
            return "one of " + ", ".join(repr(c) for c in self.choices)
        names = (self.types if isinstance(self.types, tuple) else (self.types,))
        pretty = {int: "a whole number", float: "a number", str: "text",
                  bool: "true or false", list: "a list", dict: "an object"}
        if names == Number:
            return "a number"
        return " or ".join(pretty.get(t, getattr(t, "__name__", str(t)))
                           for t in names)


def _type_name(value: Any) -> str:
    return type(value).__name__


def _check(value: Any, spec: Spec, where: str, out: list[str]) -> None:
    if spec.choices is not None and value not in spec.choices:
        out.append(f"{where} must be {spec.describe()}, got {value!r}"
                   + (f"\n      fix: {spec.fix}" if spec.fix else ""))
        return

    # bool is a subclass of int in Python, so a bare isinstance check lets
    # `"enabled": true` satisfy a numeric field and `"height": true` sail past.
    wants_number = spec.types == Number or spec.types is float or spec.types is int
    if wants_number and isinstance(value, bool):
        out.append(f"{where} must be a number, got {value!r} (true/false)"
                   + (f"\n      fix: {spec.fix}" if spec.fix else ""))
        return

    if spec.types is not object and not isinstance(value, spec.types):
        out.append(f"{where} must be {spec.describe()}, got {value!r} "
                   f"({_type_name(value)})"
                   + (f"\n      fix: {spec.fix}" if spec.fix else ""))
        return

    if isinstance(value, Number) and not isinstance(value, bool):
        if spec.min is not None and value < spec.min:
            out.append(f"{where} must be at least {spec.min}, got {value}"
                       + (f"\n      fix: {spec.fix}" if spec.fix else ""))
        if spec.max is not None and value > spec.max:
            out.append(f"{where} must be at most {spec.max}, got {value}"
                       + (f"\n      fix: {spec.fix}" if spec.fix else ""))

    if spec.non_empty and isinstance(value, (list, dict, str)) and not value:
        out.append(f"{where} must not be empty"
                   + (f"\n      fix: {spec.fix}" if spec.fix else ""))

    if spec.of is not None and isinstance(value, dict):
        _check_object(value, spec.of, where, out)

    if spec.each is not None and isinstance(value, list):
        for i, item in enumerate(value):
            _check(item, spec.each, f"{where}[{i}]", out)


def _check_object(cfg: dict, schema: dict[str, Spec], prefix: str,
                  out: list[str]) -> None:
    for key, spec in schema.items():
        where = f"{prefix}.{key}" if prefix else key
        if key not in cfg or cfg[key] is None:
            if spec.required:
                out.append(f"{where} is required but missing"
                           + (f"\n      fix: {spec.fix}" if spec.fix else ""))
            continue
        _check(cfg[key], spec, where, out)


def validate(cfg: Any, schema: dict[str, Spec], path: Path) -> None:
    """Raise ConfigError listing everything wrong with `cfg`.

    Unknown keys are allowed on purpose. Every one of these files carries
    `_comment` arrays, and several carry keys read by tools rather than by the
    engine -- rejecting what this module does not recognise would make adding a
    note to a file break the show.
    """
    problems: list[str] = []
    if not isinstance(cfg, dict):
        raise ConfigError(path, [f"the file must contain an object, "
                                 f"got {_type_name(cfg)}"])

    version = cfg.get("version", CURRENT_VERSION)
    if not isinstance(version, int) or isinstance(version, bool):
        problems.append(f"version must be a whole number, got {version!r}")
    elif version > CURRENT_VERSION:
        problems.append(
            f"version {version} is newer than this engine understands "
            f"({CURRENT_VERSION})\n      fix: update the engine, or remove the "
            f"key if the file was hand-copied from a newer checkout")

    _check_object(cfg, schema, "", problems)
    if problems:
        raise ConfigError(path, problems)


def load(path: Path, schema: dict[str, Spec]) -> dict:
    """Read and validate one config file."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise ConfigError(path, [f"no such file: {path}"]) from None
    try:
        cfg = json.loads(text)
    except json.JSONDecodeError as exc:
        # json's own message names the offset, which is useless in a 128-line
        # file; the line and column are what an editor can jump to.
        raise ConfigError(path, [
            f"is not valid JSON: {exc.msg} at line {exc.lineno}, "
            f"column {exc.colno}\n      fix: a trailing comma after the last "
            f"item in a list or object is the usual cause"]) from None
    validate(cfg, schema, path)
    return cfg


# ------------------------------------------------------------------ writing --

def write_json_atomic(path: Path, data: Any, *, backup: bool = True) -> None:
    """Write JSON so the file is never observed half-written.

    Same directory for the temp file, because `os.replace` is only atomic within
    a filesystem and a temp dir can easily be on another one -- which would turn
    the atomic rename back into a copy, silently, on exactly the machine that
    has a separate drive for the show folder.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if backup and path.exists():
        shutil.copy2(path, path.with_suffix(path.suffix + ".bak"))

    tmp = path.with_name(path.name + ".tmp")
    text = json.dumps(data, indent=2) + "\n"
    try:
        with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
            fh.flush()
            # Without this the rename can land before the bytes do, and a power
            # cut in between leaves a correctly-named, empty file -- the exact
            # failure the rename was supposed to prevent.
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        # A leftover .tmp is not dangerous -- the real file is untouched, which
        # is the guarantee -- but it looks like a half-finished write to the
        # next person to list the directory, and that is the wrong thing to
        # believe about a calibration at 4pm.
        tmp.unlink(missing_ok=True)
        raise


# ------------------------------------------------------------------ schemas --

_POINT = {"x": Spec(Number, required=True), "y": Spec(Number, required=True),
          "z": Spec(Number, required=True)}

VENUE = {
    "name": Spec(str),
    "width": Spec(Number, required=True, min=1,
                  fix="the room's width in MILLIMETRES, e.g. 18288 for 60 ft"),
    "depth": Spec(Number, required=True, min=1, fix="millimetres"),
    "height": Spec(Number, required=True, min=1, fix="millimetres"),
    "ball": Spec(dict, required=True, of=_POINT,
                 fix="the mirror ball's centre, in millimetres from the "
                     "front-left floor corner"),
    "ball_radius": Spec(Number, min=1, max=2000,
                        fix="the ball's RADIUS in millimetres. This one is "
                            "safety-relevant and errs the unsafe way when too "
                            "big -- see docs/SAFETY.md"),
    "apex_height": Spec(Number, min=1),
    "elev_extreme_deg": Spec(Number, min=0, max=90),
    "crowd_zone": Spec(dict, of={
        "min_x": Spec(Number, required=True), "max_x": Spec(Number, required=True),
        "min_z": Spec(Number, required=True), "max_z": Spec(Number, required=True),
        "head_band_min": Spec(Number, required=True, min=0,
                              fix="the bottom of the band the taper protects, "
                                  "in millimetres -- roughly seated-tall"),
        "head_band_max": Spec(Number, required=True, min=0,
                              fix="the top of the band, in millimetres -- be "
                                  "generous, someone on shoulders is who gets hit"),
    }),
    "canopy": Spec(dict, of={
        "enabled": Spec(bool),
        "height": Spec(Number, required=True, min=0),
        "radius": Spec(Number, required=True, min=0),
        "center": Spec(dict, of={"x": Spec(Number, required=True),
                                 "z": Spec(Number, required=True)}),
    }),
    "truss": Spec(dict, of={
        "enabled": Spec(bool),
        "min_x": Spec(Number, required=True), "max_x": Spec(Number, required=True),
        "min_z": Spec(Number, required=True), "max_z": Spec(Number, required=True),
        "height": Spec(Number, required=True, min=0),
        "bar": Spec(Number, min=0),
    }),
    "taper": Spec(dict, of={
        "crowd_level": Spec(Number, min=0, max=1,
                            fix="what a beam over the crowd is dimmed TO, 0-1. "
                                "0 is a hard guard and costs the floor sweeps"),
        "margin_deg": Spec(Number, min=0, max=90),
        "slew_per_second": Spec(Number, min=0),
        "enabled": Spec(bool),
    }),
}

RIG = {
    "name": Spec(str),
    "venue": Spec(str, fix="the name of a file in shared/venues/, without the "
                           ".json -- or omit it to use this event's own venue.json"),
    "mount_mode": Spec(str, choices=("table", "venue", "hung"),
                       fix="how the heads are physically mounted. 'venue' is "
                           "the sideways corner install where Pan carries "
                           "elevation"),
    "fixtures": Spec(list, required=True, non_empty=True, each=Spec(dict, of={
        "id": Spec(int, required=True, min=0),
        "name": Spec(str, required=True, non_empty=True),
        "manufacturer": Spec(str, required=True),
        "model": Spec(str, required=True),
        "mode": Spec(str, required=True,
                     fix="must match a <Mode Name=...> in the fixture's .qxf"),
        "universe": Spec(int, min=0),
        "address": Spec(int, required=True, min=1, max=512,
                        fix="the DMX start channel, 1-512"),
        "tags": Spec(list, each=Spec(str)),
        "beam_deg": Spec(Number, min=0, max=180,
                         fix="beam cone angle. Safety-relevant: too small and "
                             "the taper under-protects. See docs/SAFETY.md"),
        "lumens": Spec(Number, min=0),
        "pan_range_deg": Spec(Number, min=0), "tilt_range_deg": Spec(Number, min=0),
        "pan_speed_deg_s": Spec(Number, min=0),
        "tilt_speed_deg_s": Spec(Number, min=0),
        "position": Spec(dict, of=_POINT),
        "hold": Spec(dict),
        "notes": Spec(str),
    })),
}

CALIBRATION = {
    "measured": Spec(str),
    "source": Spec(str),
    "mount_mode": Spec(str, choices=("table", "venue", "hung"),
                       fix="must match rig.json's mount_mode -- a reading taken "
                           "in one mode calibrates a physically different "
                           "transform and is not convertible"),
    "venue": Spec(str),
    "rig": Spec(str),
    "heads": Spec(list, each=Spec(dict, of={
        "fixture": Spec(str, required=True,
                        fix="must match a fixture 'name' in rig.json exactly"),
        "ball_dmx": Spec(list, each=Spec(int, min=0, max=255),
                         fix="the two 0-255 readings with the head aimed at "
                             "the ball, as [pan, tilt]"),
        "pan_invert": Spec(bool), "tilt_invert": Spec(bool),
        "mount_facing_override": Spec(Number),
    })),
}

PRESETS = {
    "presets": Spec(list, each=Spec(dict, of={
        "name": Spec(str, required=True, non_empty=True),
        "movement": Spec(dict), "color": Spec(dict), "level": Spec(dict),
        "speed": Spec(Number, min=0), "master": Spec(Number, min=0, max=1),
    })),
}

LOOKS = {
    "reference_bpm": Spec(Number, min=1),
    "source": Spec(str),
    "looks": Spec(list, required=True, non_empty=True, each=Spec(dict, of={
        "name": Spec(str, required=True, non_empty=True),
        "kind": Spec(str, required=True, choices=(
            "pose", "path", "mixed", "color", "color_path", "intensity",
            "level_path")),
        "groups": Spec(list, each=Spec(str)),
        "bars": Spec(Number, min=0),
    })),
}

INVENTORY = {
    "fixtures": Spec(list, required=True, each=Spec(dict, of={
        "id": Spec(str, required=True, non_empty=True),
        "manufacturer": Spec(str, required=True),
        "model": Spec(str, required=True),
        "qxf": Spec(str),
        "count": Spec(int, required=True, min=0),
        "modes": Spec(list, each=Spec(str)),
        "type": Spec(str),
        "status": Spec(str, choices=("owned", "unverified", "retired")),
        "notes": Spec(str),
    })),
}
