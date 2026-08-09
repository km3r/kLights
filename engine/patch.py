"""Editing a rig, as functions rather than as a UI.

Three surfaces need to change a patch -- a CLI at load-in, an MCP server so the
rig can be described in conversation, and a tab in the show UI with the rig in
front of you -- and the one thing worse than none of them is three that disagree
about what a legal patch is.

So the rules live here, once, and the surfaces are thin. Every function takes a
config dict and returns a new one; **nothing here writes a file**. The caller
decides whether an edit is worth persisting, which is what lets the UI preview a
change, the MCP server refuse one while a show is running, and the CLI print a
diff before touching anything.

Every function returns `Result(config, errors, warnings)`:

  * `errors` means the edit was rejected and `config` is unchanged. Two
    fixtures cannot share a channel; an address cannot be 700.
  * `warnings` means it was applied and you should know something. The
    inventory says we own fewer of these than you just patched.

The distinction is the whole design. A patch editor that refuses everything
questionable is one you fight at 4pm; one that accepts everything silently is
how a fixture ends up dark. Reject what is impossible, warn about what is
merely suspicious.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

from . import config as configmod
from . import rig as rigmod

REPO = Path(__file__).resolve().parent.parent
EVENTS = REPO / "events"
VENUES = REPO / "shared" / "venues"
FIXTURES = REPO / "shared" / "fixtures"
INVENTORY = REPO / "shared" / "inventory.json"

MAX_CHANNEL = 512


@dataclass
class Result:
    """The outcome of one edit. `ok` is the only thing a caller must check."""
    config: dict
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    def as_dict(self) -> dict:
        """For the MCP server and the WebSocket, which speak JSON."""
        return {"ok": self.ok, "errors": self.errors, "warnings": self.warnings}


# ------------------------------------------------------------------ reading --

def library(roots: Optional[Iterable[Path]] = None) -> rigmod.ProfileLibrary:
    return rigmod.ProfileLibrary(roots)


def load_inventory() -> dict:
    try:
        return configmod.load(INVENTORY, configmod.INVENTORY)
    except configmod.ConfigError:
        return {"fixtures": []}


def list_venues() -> list[dict]:
    """Rooms available to point an event at."""
    out = []
    for path in sorted(VENUES.glob("*.json")):
        try:
            cfg = configmod.load(path, configmod.VENUE)
        except configmod.ConfigError:
            continue
        out.append({"name": path.stem, "title": cfg.get("name", path.stem),
                    "width": cfg.get("width"), "depth": cfg.get("depth"),
                    "height": cfg.get("height")})
    return out


def list_profiles(lib: Optional[rigmod.ProfileLibrary] = None) -> list[dict]:
    """Fixture definitions in `shared/fixtures/`, with their modes.

    Deliberately not every profile the deeper roots could reach: those number in
    the thousands and answering "what can I patch" with 1700 entries is not an
    answer. `import_profile` is how one of those becomes available.
    """
    lib = lib or library()
    out = []
    for (manufacturer, model), profile in sorted(lib.indexed().items()):
        out.append({"manufacturer": manufacturer, "model": model,
                    "type": profile.type,
                    "modes": {name: len(channels)
                              for name, channels in profile.modes.items()}})
    return out


def describe(event: str) -> dict:
    """What is patched, resolved through the real loader.

    Goes through `load_rig` rather than reading rig.json, so what this reports
    is what the engine would actually run -- including the channel count each
    fixture's mode really has, which is the number an address clash depends on
    and the one a hand-written summary gets wrong.
    """
    rig = rigmod.load_rig(event_dir(event))
    return {
        "name": rig.name,
        "venue": rig.venue.name if rig.venue else None,
        "venue_file": str(rig.venue_file) if rig.venue_file else None,
        "errors": rig.validate(),
        "warnings": rig.warnings(),
        "fixtures": [{
            "id": f.fid, "name": f.name,
            "manufacturer": f.profile.manufacturer, "model": f.profile.model,
            "mode": f.mode, "universe": f.universe,
            "address": f.address, "last_address": f.last_address,
            "channels": f.channel_count, "tags": list(f.tags),
            "head": f.head, "position": f.position, "is_mover": f.is_mover,
        } for f in rig.fixtures],
    }


def event_dir(event: str) -> Path:
    """An event name or a path. Names are the common case; paths let a tool work
    on a copy without it having to live under events/."""
    as_path = Path(event)
    if as_path.is_dir() and (as_path / "rig.json").exists():
        return as_path
    return EVENTS / event


def load_rig_config(event: str) -> dict:
    return configmod.load(event_dir(event) / "rig.json", configmod.RIG)


# ------------------------------------------------------------------ helpers --

def _channel_count(cfg_entry: dict, lib: rigmod.ProfileLibrary) -> Optional[int]:
    profile = lib.get(cfg_entry["manufacturer"], cfg_entry["model"])
    if profile is None:
        return None
    mode = profile.modes.get(cfg_entry.get("mode", ""))
    return None if mode is None else len(mode)


def _spans(cfg: dict, lib: rigmod.ProfileLibrary
           ) -> list[tuple[int, int, int, str]]:
    """(universe, first, last, name) for every fixture that resolves."""
    out = []
    for entry in cfg.get("fixtures", []):
        count = _channel_count(entry, lib)
        if count is None:
            continue
        first = int(entry["address"])
        out.append((int(entry.get("universe", 0)), first, first + count - 1,
                    entry.get("name", "?")))
    return out


def check_addresses(cfg: dict, lib: rigmod.ProfileLibrary) -> list[str]:
    """Overlaps and out-of-range addresses.

    Per universe: the same channel in two universes is not a conflict, and
    treating it as one reports a mixed-universe rig -- a house rig alongside
    ours -- as one giant clash. `shared/tools/validate_patch.py` applies the
    same rule to `patch_sheet.csv`, which the engine does not read; that copy
    is still separate, and the two agreeing is currently a matter of care
    rather than of construction.
    """
    errors: list[str] = []
    occupied: dict[tuple[int, int], str] = {}
    for universe, first, last, name in _spans(cfg, lib):
        if first < 1 or last > MAX_CHANNEL:
            errors.append(f"{name}: channels {first}-{last} fall outside "
                          f"1-{MAX_CHANNEL} (universe {universe})")
        clashes = sorted({occupied[(universe, ch)]
                          for ch in range(max(first, 1), min(last, MAX_CHANNEL) + 1)
                          if (universe, ch) in occupied})
        for other in clashes:
            errors.append(f"{name} (universe {universe}, ch {first}-{last}) "
                          f"overlaps {other}")
        for ch in range(max(first, 1), min(last, MAX_CHANNEL) + 1):
            occupied.setdefault((universe, ch), name)
    return errors


def _validated(cfg: dict, lib: rigmod.ProfileLibrary,
               warnings: Optional[list[str]] = None) -> Result:
    """Shared exit path: schema, then addresses, then inventory.

    Schema first because an address check on a config with a string where a
    number belongs reports nonsense about the address.
    """
    problems: list[str] = []
    try:
        configmod.validate(cfg, configmod.RIG, Path("rig.json"))
    except configmod.ConfigError as exc:
        problems.extend(exc.problems)
    if problems:
        return Result(cfg, problems, list(warnings or []))

    problems.extend(check_addresses(cfg, lib))
    warn = list(warnings or [])
    for entry in cfg.get("fixtures", []):
        if lib.get(entry["manufacturer"], entry["model"]) is None:
            problems.append(
                f"{entry.get('name', '?')}: no .qxf for "
                f"{entry['manufacturer']} {entry['model']!r} in "
                f"{[str(r) for r in lib.roots]}")
        elif _channel_count(entry, lib) is None:
            modes = sorted(lib.get(entry["manufacturer"],
                                   entry["model"]).modes)
            problems.append(
                f"{entry.get('name', '?')}: {entry['model']!r} has no mode "
                f"{entry.get('mode')!r} -- it has {modes}")
    return Result(cfg, problems, warn)


def inventory_warnings(cfg: dict, lib: rigmod.ProfileLibrary) -> list[str]:
    """Patch versus what we own. Warnings only -- see Rig.inventory_warnings."""
    owned = {(e["manufacturer"], e["model"]): e
             for e in load_inventory().get("fixtures", [])}
    used: dict[tuple[str, str], int] = {}
    for entry in cfg.get("fixtures", []):
        key = (entry["manufacturer"], entry["model"])
        used[key] = used.get(key, 0) + 1
    out = []
    for (manufacturer, model), count in sorted(used.items()):
        record = owned.get((manufacturer, model))
        if record is None:
            out.append(f"{manufacturer} {model}: not in shared/inventory.json")
        elif count > record.get("count", 0):
            out.append(f"{manufacturer} {model}: patching {count}, "
                       f"inventory says we own {record.get('count', 0)}")
    return out


def _next_id(cfg: dict) -> int:
    return max((int(f["id"]) for f in cfg.get("fixtures", [])), default=-1) + 1


def _find(cfg: dict, name: str) -> Optional[dict]:
    for entry in cfg.get("fixtures", []):
        if entry.get("name") == name:
            return entry
    return None


def _copy(cfg: dict) -> dict:
    import copy
    return copy.deepcopy(cfg)


# -------------------------------------------------------------------- edits --

def add_fixture(cfg: dict, *, name: str, manufacturer: str, model: str,
                mode: str, address: Optional[int] = None, universe: int = 0,
                tags: Sequence[str] = (), position: Optional[dict] = None,
                beam_deg: Optional[float] = None,
                hold: Optional[dict] = None, notes: str = "",
                lib: Optional[rigmod.ProfileLibrary] = None) -> Result:
    """Add one unit. With no address, the first gap that fits is used.

    Names must be unique, and that is an error rather than a warning: a look's
    per-fixture colours and a calibration's per-head readings are both keyed by
    name, so two fixtures sharing one would make both files ambiguous in a way
    neither would report.
    """
    lib = lib or library()
    cfg = _copy(cfg)
    if _find(cfg, name) is not None:
        return Result(cfg, [f"a fixture called {name!r} is already patched -- "
                            f"names key both looks and calibration, so they "
                            f"have to be unique"])

    entry: dict[str, Any] = {
        "id": _next_id(cfg), "name": name, "manufacturer": manufacturer,
        "model": model, "mode": mode, "universe": int(universe),
        "address": 1, "tags": list(tags),
    }
    count = _channel_count(entry, lib)
    if count is None:
        profile = lib.get(manufacturer, model)
        if profile is None:
            return Result(cfg, [f"no .qxf for {manufacturer} {model!r}. "
                                f"`list_profiles` shows what is available, and "
                                f"`import_profile` adds one"])
        return Result(cfg, [f"{model!r} has no mode {mode!r} -- "
                            f"it has {sorted(profile.modes)}"])

    if address is None:
        found = first_free(cfg, count, universe=universe, lib=lib)
        if found is None:
            return Result(cfg, [f"no free run of {count} channels in universe "
                                f"{universe}"])
        entry["address"] = found
    else:
        entry["address"] = int(address)

    if position is not None:
        entry["position"] = position
    if beam_deg is not None:
        entry["beam_deg"] = float(beam_deg)
    if hold:
        entry["hold"] = dict(hold)
    if notes:
        entry["notes"] = notes

    cfg.setdefault("fixtures", []).append(entry)
    return _validated(cfg, lib, inventory_warnings(cfg, lib))


def remove_fixture(cfg: dict, name: str,
                   lib: Optional[rigmod.ProfileLibrary] = None) -> Result:
    """Remove one unit.

    Warns when it was a mover, because the heads are an ordered,
    calibration-bearing index: dropping one shifts every head after it, and the
    calibration entries -- which are keyed by name, so they survive -- will no
    longer line up with the positions any look was authored for.
    """
    lib = lib or library()
    cfg = _copy(cfg)
    entry = _find(cfg, name)
    if entry is None:
        return Result(cfg, [f"no fixture called {name!r}"])
    cfg["fixtures"] = [f for f in cfg["fixtures"] if f.get("name") != name]

    warnings = []
    profile = lib.get(entry["manufacturer"], entry["model"])
    if profile is not None and "position" in entry:
        offsets = profile.offsets(entry.get("mode", ""))
        if rigmod.PAN in offsets and rigmod.TILT in offsets:
            warnings.append(
                f"{name} was a moving head, so the head order has changed. "
                f"Re-check calibration.json and any look whose 'fixtures' list "
                f"names it -- `python -m engine.calibrate drift` is the check")
    return _validated(cfg, lib, warnings)


def set_address(cfg: dict, name: str, address: int, universe: Optional[int] = None,
                lib: Optional[rigmod.ProfileLibrary] = None) -> Result:
    lib = lib or library()
    cfg = _copy(cfg)
    entry = _find(cfg, name)
    if entry is None:
        return Result(cfg, [f"no fixture called {name!r}"])
    entry["address"] = int(address)
    if universe is not None:
        entry["universe"] = int(universe)
    return _validated(cfg, lib)


def set_tags(cfg: dict, name: str, tags: Sequence[str],
             lib: Optional[rigmod.ProfileLibrary] = None) -> Result:
    """Retag one unit.

    Tags are how looks find fixtures -- they never name a fixture directly --
    so removing the last member of a group silently disables every look scoped
    to it. Warned about, not blocked: emptying a group on purpose is a normal
    thing to do when a rig shrinks.
    """
    lib = lib or library()
    cfg = _copy(cfg)
    entry = _find(cfg, name)
    if entry is None:
        return Result(cfg, [f"no fixture called {name!r}"])
    was = set(entry.get("tags", []))
    entry["tags"] = list(tags)

    warnings = []
    remaining: set[str] = set()
    for f in cfg["fixtures"]:
        remaining.update(f.get("tags", []))
    for gone in sorted(was - remaining):
        warnings.append(f"no fixture is tagged {gone!r} any more -- every look "
                        f"scoped to that group will do nothing")
    return _validated(cfg, lib, warnings)


def set_position(cfg: dict, name: str, x: float, y: float, z: float,
                 lib: Optional[rigmod.ProfileLibrary] = None) -> Result:
    """Move a fixture in the room. Millimetres, y up, origin front-left floor."""
    lib = lib or library()
    cfg = _copy(cfg)
    entry = _find(cfg, name)
    if entry is None:
        return Result(cfg, [f"no fixture called {name!r}"])
    entry["position"] = {"x": float(x), "y": float(y), "z": float(z)}
    return _validated(cfg, lib, [
        f"{name} moved. Its calibration was measured where it used to be, so "
        f"re-run `python -m engine.calibrate drift` before trusting a pose"])


def first_free(cfg: dict, channels: int, universe: int = 0,
               lib: Optional[rigmod.ProfileLibrary] = None) -> Optional[int]:
    """Lowest address with `channels` free channels after it, or None.

    First fit rather than append-to-the-end, so removing a fixture and adding
    another reuses the hole instead of pushing the patch ever higher.
    """
    lib = lib or library()
    taken = set()
    for u, first, last, _ in _spans(cfg, lib):
        if u == universe:
            taken.update(range(first, last + 1))
    for start in range(1, MAX_CHANNEL - channels + 2):
        if not any(ch in taken for ch in range(start, start + channels)):
            return start
    return None


def autopatch(cfg: dict, universe: Optional[int] = None, start: int = 1,
              lib: Optional[rigmod.ProfileLibrary] = None) -> Result:
    """Re-address every fixture end to end, in file order, leaving no gaps.

    The one edit that touches fixtures you did not name, so it warns with the
    whole before/after map rather than reporting success. Every address on the
    physical units has to be re-dialled to match, and a patch that is correct in
    the file and wrong on the hardware is worse than one that is obviously wrong
    in both.
    """
    lib = lib or library()
    cfg = _copy(cfg)
    moved: list[str] = []
    next_free: dict[int, int] = {}
    for entry in cfg.get("fixtures", []):
        u = int(entry.get("universe", 0)) if universe is None else int(universe)
        count = _channel_count(entry, lib)
        if count is None:
            return Result(cfg, [f"{entry.get('name', '?')}: cannot size "
                                f"{entry['model']!r} in mode "
                                f"{entry.get('mode')!r}, so it cannot be "
                                f"auto-addressed"])
        at = next_free.get(u, start)
        if at + count - 1 > MAX_CHANNEL:
            return Result(cfg, [f"universe {u} ran out of channels at "
                                f"{entry.get('name', '?')}"])
        if entry.get("address") != at or entry.get("universe", 0) != u:
            moved.append(f"{entry.get('name', '?')}: "
                         f"u{entry.get('universe', 0)}/{entry.get('address')} "
                         f"-> u{u}/{at}")
        entry["universe"], entry["address"] = u, at
        next_free[u] = at + count
    warnings = ([f"{len(moved)} fixture(s) re-addressed -- re-dial the units to "
                 f"match:"] + moved) if moved else ["nothing moved"]
    return _validated(cfg, lib, warnings)


# ------------------------------------------------------------------- venues --

def set_venue(cfg: dict, venue: str,
              lib: Optional[rigmod.ProfileLibrary] = None) -> Result:
    """Point the event at a room in shared/venues/."""
    lib = lib or library()
    cfg = _copy(cfg)
    if not (VENUES / f"{venue}.json").exists():
        return Result(cfg, [f"no room called {venue!r} in {VENUES}. "
                            f"Available: {[v['name'] for v in list_venues()]}"])
    cfg["venue"] = venue
    return _validated(cfg, lib, [
        "the room changed, so every head's calibration now describes a "
        "different space. Re-calibrate before the show"])


# ------------------------------------------------------------------ profiles --

def import_profile(source: Path, lib: Optional[rigmod.ProfileLibrary] = None
                   ) -> Result:
    """Copy a .qxf into shared/fixtures/ so a fresh clone can resolve it.

    The one function here that touches the filesystem, because the thing being
    asked for IS a file. `shared/fixtures/` is the only search root that
    survives a clone -- the others are a machine-local QLC+ install and a
    gitignored vendor tree -- so a rig referencing a profile from either would
    load here and fail at the venue on a different laptop.
    """
    source = Path(source)
    if not source.exists():
        return Result({}, [f"no such file: {source}"])
    if source.suffix.lower() != ".qxf":
        return Result({}, [f"{source.name} is not a .qxf"])
    try:
        profile = rigmod.parse_qxf(source)
    except Exception as exc:                                  # noqa: BLE001
        return Result({}, [f"{source.name} is not a usable fixture "
                           f"definition: {exc}"])

    FIXTURES.mkdir(parents=True, exist_ok=True)
    target = FIXTURES / source.name
    warnings = []
    if target.exists():
        if target.read_bytes() == source.read_bytes():
            return Result({"path": str(target),
                           "manufacturer": profile.manufacturer,
                           "model": profile.model},
                          [], [f"{target.name} was already there, identical"])
        warnings.append(f"{target.name} existed with different content and was "
                        f"overwritten -- the previous one is at "
                        f"{target.name}.bak")
        shutil.copy2(target, target.with_suffix(target.suffix + ".bak"))
    shutil.copy2(source, target)
    warnings.append(f"{profile.manufacturer} {profile.model!r} is now "
                    f"patchable. Add it to shared/inventory.json if we own it")
    return Result({"path": str(target), "manufacturer": profile.manufacturer,
                   "model": profile.model, "modes": sorted(profile.modes)},
                  [], warnings)


# ------------------------------------------------------------------- events --

def new_event(name: str, venue: str, *, force: bool = False) -> Result:
    """Scaffold `events/<name>/` with an empty, valid rig pointing at a room."""
    target = EVENTS / name
    if target.exists() and not force and (target / "rig.json").exists():
        return Result({}, [f"{target} already has a rig.json. Pick another "
                           f"name, or edit that one"])
    if not (VENUES / f"{venue}.json").exists():
        return Result({}, [f"no room called {venue!r} in {VENUES}. "
                           f"Available: {[v['name'] for v in list_venues()]}"])
    cfg = {
        "$schema": "../../schemas/rig.schema.json",
        "_comment": [
            f"WHAT IS PLUGGED IN for {name}. Selects units from",
            "shared/inventory.json and gives each one an address, a position",
            "and a set of tags. It does not describe the hardware (that is the",
            f".qxf) or the room ('venue' names {venue} in shared/venues/).",
            "",
            "Looks reference TAGS, never fixture ids or names -- that is what",
            "lets a look built for four heads run on a rig with eight.",
            "",
            "'position' is millimetres, y up, origin at the room's front-left",
            "floor corner: the same frame the venue file uses. A fixture with a",
            "position AND pan/tilt becomes a geometry head, in file order.",
        ],
        "name": name,
        "venue": venue,
        "mount_mode": "venue",
        "fixtures": [],
    }
    return Result(cfg, [], [
        f"{name} has no fixtures yet -- add some before running it",
        "mount_mode defaults to 'venue' (heads on their side, Pan carrying "
        "elevation). Change it to 'hung' or 'table' if that is not this rig",
    ])


def write_rig(event: str, cfg: dict) -> Path:
    """Persist a rig config. Atomic, with a .bak, like every other config write."""
    path = event_dir(event) / "rig.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    configmod.write_json_atomic(path, cfg)
    return path


# -------------------------------------------------------------------- locks --

LOCK_NAME = ".engine.lock"


def lock_path(event: str) -> Path:
    return event_dir(event) / LOCK_NAME


def held_by(event: str) -> Optional[str]:
    """Who is running a show against this event, if anyone.

    Editing a patch under a live show does not corrupt anything -- the engine
    read its config at startup and will not read it again -- but it produces the
    worst kind of confusion: the file says one thing, the rig does another, and
    nothing on screen explains why. So the writing surfaces check this and
    refuse, and say what to do about it.
    """
    path = lock_path(event)
    if not path.exists():
        return None
    try:
        return path.read_text(encoding="utf-8").strip() or "an engine"
    except OSError:
        return "an engine"
