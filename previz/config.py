"""Which event the previz is showing, and what the room's optics look like.

Both used to be constants in the middle of scripts. `go.py` called
`build_level.main()` with no argument, so the documented one-command path was
hardwired to despacio even though the function it called had taken an event
since the day it was written; and the optics were eight numbers eyeballed
against one photo of one room, which is fine until there is a second room.

    python previz/config.py                 # what is configured now
    KLIGHTS_EVENT=cosmos26 python previz/ue_remote.py .../go.py

Resolution order, most specific first:

  1. an explicit argument (`--event`, or `main(event=...)`)
  2. `KLIGHTS_EVENT` in the environment
  3. `previz/previz.json`
  4. `despacio`

The environment variable sits above the file deliberately: the file is what the
repo is set up for, and the variable is one person on one machine looking at
something else for ten minutes. Making them the other way round would mean
editing a tracked file to do a temporary thing.

Stdlib only, and no import of anything Unreal, so `previz doctor` and the tests
can read it on a machine with no editor installed.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Optional

REPO = Path(__file__).resolve().parent.parent
PREVIZ_JSON = REPO / "previz" / "previz.json"
OPTICS_JSON = REPO / "previz" / "optics.json"

DEFAULT_EVENT = "despacio"


def _load(path: Path) -> dict:
    """A config file, or an empty one. Never raises for a missing file.

    A malformed file DOES raise: an empty previz.json is "nothing configured"
    and a broken one is a mistake someone should hear about, and silently
    falling back to despacio because of a stray comma is how you end up
    previzing the wrong room without noticing.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{path.name} is not valid JSON: {exc}") from None
    return data if isinstance(data, dict) else {}


def event(explicit: Optional[str] = None) -> str:
    """The event name to build. See the resolution order above."""
    if explicit:
        return explicit
    from_env = os.environ.get("KLIGHTS_EVENT")
    if from_env:
        return from_env
    return str(_load(PREVIZ_JSON).get("event") or DEFAULT_EVENT)


def event_dir(explicit: Optional[str] = None) -> Path:
    """The event's directory, whether it was named or pointed at.

    A path is accepted as well as a name so that an event living outside the
    repo -- somebody else's show, a copy on a stick at a venue -- needs no
    special case.
    """
    name = event(explicit)
    direct = Path(name)
    return direct if direct.exists() else REPO / "events" / name


# ------------------------------------------------------------------ optics --

# The despacio numbers, and the only reason they are the defaults is that they
# are the only ones anybody has measured against a real room. Every one was
# eyeballed against a single photograph, which is honest for one venue and
# nothing at all for a second -- so they live here as a NAMED profile that a
# venue can replace, rather than as constants a second room would have to be
# talked out of.
#
# `RAY_GAIN`'s own comment has always asked for a re-sweep whenever the room
# size or the beam angle changes. This is where that re-sweep gets recorded.
DEFAULTS: dict[str, float] = {
    # Fog extinction along a beam, per metre. Bigger = beams die sooner.
    "beam_extinction_per_m": 0.09,
    # Brightness of the beam shaft mesh.
    "beam_gain": 1.4,
    # Brightness of the bright dot where a beam lands.
    "dot_gain": 2.3,
    # Brightness of the light-shaft rays.
    "ray_gain": 1.8,
    # How hard relative brightness is compressed for the mesh emissive:
    # `ratio ** contrast`, so 1.0 is literal and lower is flatter.
    "mesh_contrast": 0.22,
    # Diffuse reflectance of the room's surfaces.
    "room_albedo": 0.16,
    # Volumetric fog density.
    "fog_density": 0.35,
}


def optics(venue: Optional[str] = None) -> dict[str, float]:
    """Optics for a venue, falling back to the defaults key by key.

    Per KEY rather than per profile, so a room that only needs different fog
    says only that. A venue block that had to restate all seven numbers would
    drift from the defaults the first time one of them was improved.
    """
    data = _load(OPTICS_JSON)
    # `_`-prefixed keys are notes. Every config file in this repo carries them
    # and they must never be mistaken for a setting -- least of all by the
    # unknown-key check below, whose whole job is to catch typos.
    def settings(block: Any) -> dict:
        return {k: v for k, v in (block or {}).items() if not k.startswith("_")}

    merged = dict(DEFAULTS)
    merged.update({k: v for k, v in settings(data.get("default")).items()
                   if k in DEFAULTS})
    if venue:
        room = settings((data.get("venues") or {}).get(venue))
        merged.update({k: v for k, v in room.items() if k in DEFAULTS})
    unknown = set(settings(data.get("default"))) - set(DEFAULTS)
    for room in (data.get("venues") or {}).values():
        unknown |= set(settings(room)) - set(DEFAULTS)
    if unknown:
        # Named rather than ignored: a typo in an optics key is otherwise a
        # value that silently does nothing, and the symptom is "my change had
        # no effect", which is the hardest kind of nothing to debug.
        raise ValueError(f"{OPTICS_JSON.name}: unknown optics key(s) "
                         f"{', '.join(sorted(unknown))}. "
                         f"Known: {', '.join(sorted(DEFAULTS))}")
    return merged


# ------------------------------------------------------------ render cvars --

DEFAULT_ENGINE_INI = REPO / "previz" / "unreal" / "Config" / "DefaultEngine.ini"
RENDER_SECTION = "[/Script/Engine.RendererSettings]"


def render_cvars(path: Optional[Path] = None) -> list[tuple[str, str]]:
    """The renderer settings from DefaultEngine.ini, as (cvar, value) pairs.

    These used to be written out TWICE -- once in the ini, once again as console
    commands in `build_level.apply_render_cvars` -- with a comment in each
    saying they must match. They drifted anyway, and the symptom was fog that
    looked different before and after the first rebuild of a session, which is
    about as hard to attribute as a symptom gets.

    The ini wins as the source because it is the one UE reads natively: a fresh
    editor has to start correct without anything of ours having run. This reads
    it so the running editor can be updated from the same numbers.

    Only `r.`-prefixed keys, because that section can legitimately hold settings
    that are not console variables, and `execute_console_command` on one of
    those is a silent no-op that looks exactly like the setting not taking.

    Lives here rather than beside its caller so it can be read -- and tested --
    on a machine with no Unreal installed.
    """
    ini = path or DEFAULT_ENGINE_INI
    if not ini.is_file():
        return []
    text = ini.read_text(encoding="utf-8")
    if RENDER_SECTION not in text:
        return []
    body = text.split(RENDER_SECTION, 1)[1].split("\n[", 1)[0]
    out = []
    for line in body.splitlines():
        line = line.strip()
        if not line or line.startswith(";") or "=" not in line:
            continue
        key, value = (part.strip() for part in line.split("=", 1))
        if key.startswith("r."):
            out.append((key, value))
    return out


def has_profile(venue: str) -> bool:
    """Whether this venue has been swept, as opposed to inheriting.

    Asked by NAME rather than by comparing the numbers to the defaults: a room
    whose sweep honestly landed on the same values as despacio's has still been
    swept, and telling its owner it is "using despacio's numbers" would send
    them to redo work they had already done.
    """
    return venue in (_load(OPTICS_JSON).get("venues") or {})


def describe(explicit: Optional[str] = None) -> dict[str, Any]:
    """What is configured, for `previz doctor` and for printing at startup.

    Reports WHERE the answer came from, not just what it is. "Why is it building
    despacio" has four possible answers and three of them are invisible.
    """
    if explicit:
        source = "asked for directly"
    elif os.environ.get("KLIGHTS_EVENT"):
        source = "KLIGHTS_EVENT"
    elif _load(PREVIZ_JSON).get("event"):
        source = "previz.json"
    else:
        source = "built-in default"
    where = event_dir(explicit)
    return {"event": event(explicit), "source": source,
            "event_dir": str(where), "exists": where.exists(),
            "previz_json": str(PREVIZ_JSON), "optics_json": str(OPTICS_JSON)}


if __name__ == "__main__":
    info = describe()
    print(f"event      {info['event']}  (from {info['source']})")
    print(f"directory  {info['event_dir']}"
          f"{'' if info['exists'] else '   -- DOES NOT EXIST'}")
    for key, value in optics().items():
        print(f"  optics   {key:24} {value}")
