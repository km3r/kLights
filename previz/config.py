"""Which event the editor previz is showing.

Both used to be constants in the middle of scripts. `go.py` called
`build_level.main()` with no argument, so the documented one-command path was
hardwired to despacio even though the function it called had taken an event
since the day it was written. (The room's optics used to live here too; they
are part of the venue file now, under `previz.optics`, read by engine.scene.)

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
            "previz_json": str(PREVIZ_JSON)}


if __name__ == "__main__":
    info = describe()
    print(f"event      {info['event']}  (from {info['source']})")
    print(f"directory  {info['event_dir']}"
          f"{'' if info['exists'] else '   -- DOES NOT EXIST'}")
    if info["exists"]:
        # A room's optics are part of its venue file now (`previz.optics`), and
        # engine.scene is what merges them over the defaults.
        import sys
        sys.path.insert(0, str(REPO))
        from engine import scene
        for key, value in sorted(scene.build_for(Path(info["event_dir"])).manifest["optics"].items()):
            print(f"  optics   {key:28} {value}")
