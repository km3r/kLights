"""An MCP server for the rig and the show, so both can be worked on in
conversation.

    python mcp/klights_mcp.py

Speaks MCP over stdio: JSON-RPC 2.0, one object per line, on stdin and stdout.
Written against the protocol directly rather than against an SDK, because the
whole engine is standard library only and a tool for editing the show's config
is a poor place to introduce the first dependency. The protocol surface actually
needed is three methods.

**Everything it can change goes through `engine.patch`** (the rig) **or
`engine.showtools`** (the show folder: tracks, timelines, routines, template
sets). This file is a
translation layer and nothing else: no validation rules live here, so the answer
to "is this patch legal" cannot drift between saying it in chat, typing it at
the CLI, and tapping it in the UI.

Two deliberate restrictions:

  * **Writes are opt-in per call.** Every editing tool takes `write`, defaulting
    to false, and a dry run reports exactly what would change. Describing a rig
    out loud is a lossy process and the first attempt is usually wrong.
  * **It refuses to write the RIG while a show is running.** The engine does
    not watch rig.json -- it reloads it only when its own Setup tab applies an
    edit -- so an edit from here mid-show leaves the file saying one thing and
    the rig doing another, with nothing on screen to explain it.
    The SHOW FOLDER is different: the engine reloads it, a playing track keeps
    its version until its next play, and every write quotes the rev it read, so
    a change made elsewhere is refused rather than overwritten.

Stdout is the transport, so nothing may print to it. Diagnostics go to stderr.
"""

from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from engine import patch                                   # noqa: E402
from engine import showtools                               # noqa: E402

PROTOCOL_VERSION = "2024-11-05"

_STR = {"type": "string"}
_INT = {"type": "integer"}
_NUM = {"type": "number"}
_BOOL = {"type": "boolean"}


def _schema(properties: dict, required: list[str]) -> dict:
    return {"type": "object", "properties": properties, "required": required}


_EVENT = {"event": {**_STR, "description":
                    "event name under events/, or a path. Default 'despacio'."}}
_WRITE = {"write": {**_BOOL, "description":
                    "save the change. Default false, which reports what would "
                    "happen without touching anything."}}

_SHOW = {"show_dir": {**_STR, "description":
                      "the show folder. Default: KLIGHTS_SHOW_DIR, then "
                      "show_dir in klights.local.json."}}
_REV = {"base_rev": {**_STR, "description":
                     "the rev you read (from a get_ tool), so a change made "
                     "elsewhere since is refused rather than overwritten. \"\" "
                     "for a new file. Required to write."}}
_DOC = {"doc": {"type": "object", "description": "the whole document"}}
_BEAT = {"beat": {**_NUM, "description":
                  "beats from the track's first downbeat; bar n starts at "
                  "beat 4*(n-1)"}}

SHOW_TOOLS = [
    {"name": "show_status",
     "description": "The show folder: which tracks, timelines, routines and "
                    "template sets it holds, show.json, and every error and "
                    "warning in it.",
     "inputSchema": _schema({**_SHOW}, [])},
    {"name": "list_tracks",
     "description": "Prepped tracks: title, artist, length, bpm, rekordbox's "
                    "phrases with their beats, and whether each has a timeline.",
     "inputSchema": _schema({**_SHOW}, [])},
    {"name": "get_track",
     "description": "One prepped track's document (identity, grid, phrases, "
                    "cues) and its rev.",
     "inputSchema": _schema({**_SHOW, "id": _STR}, ["id"])},
    {"name": "list_routines",
     "description": "Routines: bars, roles, open params, variations, and "
                    "whether one is rig-bound.",
     "inputSchema": _schema({**_SHOW}, [])},
    {"name": "get_routine",
     "description": "One routine's document and its rev.",
     "inputSchema": _schema({**_SHOW, "id": _STR}, ["id"])},
    {"name": "put_routine",
     "description": "Validate a whole routine and, with write=true and "
                    "base_rev, save it.",
     "inputSchema": _schema({**_SHOW, **_DOC, **_REV, **_WRITE}, ["doc"])},
    {"name": "get_timeline",
     "description": "A track's timeline document and its rev.",
     "inputSchema": _schema({**_SHOW, "track": _STR}, ["track"])},
    {"name": "put_timeline",
     "description": "Validate a whole timeline and, with write=true and "
                    "base_rev, save it. It applies to a playing track from "
                    "its next play.",
     "inputSchema": _schema({**_SHOW, **_DOC, **_REV, **_WRITE}, ["doc"])},
    {"name": "edit_timeline",
     "description": "Apply small edits to a track's timeline (creating it if "
                    "there is none), validate, and with write=true save. ops: "
                    "add_row {row, index?}, remove_row {row}, add_item {row, "
                    "item}, update_item {id, set}, remove_item {id}, "
                    "set_points {row, points}, set_wave {row, wave: {shape: "
                    "sine|triangle|ramp|saw|square|hold, bars, depth, phase?, "
                    "seed?, toward? (color lanes)} or null -- a wave added on "
                    "top of the points}, set_audio {row, audio: {band: "
                    "low|mid|high|all, depth, floor?, ceiling?, release? "
                    "(beats)} or null -- a frequency band of the track's own "
                    "audio added on top of a number lane's points; the track "
                    "needs a waveform}, set {key: palette|palettes|"
                    "grid_rev, value}. Rows are lanes, top first; the higher "
                    "lane wins.",
     "inputSchema": _schema({**_SHOW, "track": _STR,
                             "ops": {"type": "array", "items": {"type": "object"}},
                             **_REV, **_WRITE}, ["track", "ops"])},
    {"name": "link_track",
     "description": "Make a prepped track answer to another description -- a "
                    "guest's copy with different tags. Applies from its next "
                    "play.",
     "inputSchema": _schema({**_SHOW, "track": _STR, "title": _STR,
                             "artist": _STR, "album": _STR, "signature": _STR,
                             **_WRITE}, ["track", "title"])},
    {"name": "lint_show",
     "description": "Everything wrong in the show folder; given an event, also "
                    "everything in its timelines that will not work on that "
                    "rig (looks it does not have, roles with no fixtures).",
     "inputSchema": _schema({**_SHOW, **_EVENT}, [])},
    {"name": "explain_position",
     "description": "What a track's timeline says at a beat: each lane's clips "
                    "and weights, automation, hits. Given an event, also what "
                    "every fixture does there.",
     "inputSchema": _schema({**_SHOW, "track": _STR, **_BEAT, **_EVENT},
                            ["track", "beat"])},
    {"name": "list_template_sets",
     "description": "Template sets: rekordbox phrase -> routine mappings.",
     "inputSchema": _schema({**_SHOW}, [])},
    {"name": "get_template_set",
     "description": "One template set's document and its rev.",
     "inputSchema": _schema({**_SHOW, "id": _STR}, ["id"])},
    {"name": "put_template_set",
     "description": "Validate a whole template set and, with write=true and "
                    "base_rev, save it.",
     "inputSchema": _schema({**_SHOW, **_DOC, **_REV, **_WRITE}, ["doc"])},
]

TOOLS = [
    {
        "name": "describe_rig",
        "description": "What is patched for an event, resolved through the real "
                       "loader: addresses, channel counts, modes, tags, head "
                       "indices, plus any validation errors and warnings.",
        "inputSchema": _schema({**_EVENT}, []),
    },
    {
        "name": "list_profiles",
        "description": "Fixture definitions available to patch, with their modes "
                       "and channel counts. These are the only manufacturer/model "
                       "pairs add_fixture accepts.",
        "inputSchema": _schema({}, []),
    },
    {
        "name": "list_venues",
        "description": "Rooms an event can be pointed at, from shared/venues/.",
        "inputSchema": _schema({}, []),
    },
    {
        "name": "list_inventory",
        "description": "Hardware actually owned, with counts. A patch may exceed "
                       "this; it produces a warning, not an error.",
        "inputSchema": _schema({}, []),
    },
    {
        "name": "add_fixture",
        "description": "Patch one more unit. With no address, the first gap that "
                       "fits is used. Fails if the name is taken, the profile or "
                       "mode is unknown, or the address would clash.",
        "inputSchema": _schema({
            **_EVENT, **_WRITE,
            "name": _STR, "manufacturer": _STR, "model": _STR, "mode": _STR,
            "address": {**_INT, "description": "1-512; omit to auto-assign"},
            "universe": _INT,
            "tags": {"type": "array", "items": _STR,
                     "description": "looks reference tags, never fixture names"},
            "position": _schema({"x": _NUM, "y": _NUM, "z": _NUM},
                                ["x", "y", "z"]),
            "beam_deg": {**_NUM, "description":
                         "beam cone angle. Safety-relevant: the taper sizes the "
                         "beam's half-width from it."},
            "notes": _STR,
        }, ["name", "manufacturer", "model", "mode"]),
    },
    {
        "name": "remove_fixture",
        "description": "Unpatch a unit. Warns if it was a moving head, since "
                       "that shifts every head index after it and invalidates "
                       "the calibration's alignment.",
        "inputSchema": _schema({**_EVENT, **_WRITE, "name": _STR}, ["name"]),
    },
    {
        "name": "set_address",
        "description": "Move a unit to another DMX address or universe.",
        "inputSchema": _schema({**_EVENT, **_WRITE, "name": _STR,
                                "address": _INT, "universe": _INT},
                               ["name", "address"]),
    },
    {
        "name": "set_tags",
        "description": "Replace a unit's tags. Warns if this empties a group, "
                       "because every look scoped to it would then do nothing.",
        "inputSchema": _schema({**_EVENT, **_WRITE, "name": _STR,
                                "tags": {"type": "array", "items": _STR}},
                               ["name", "tags"]),
    },
    {
        "name": "set_position",
        "description": "Move a unit in the room. Millimetres, y up, origin at "
                       "the room's front-left floor corner.",
        "inputSchema": _schema({**_EVENT, **_WRITE, "name": _STR,
                                "x": _NUM, "y": _NUM, "z": _NUM},
                               ["name", "x", "y", "z"]),
    },
    {
        "name": "autopatch",
        "description": "Re-address every fixture end to end with no gaps. "
                       "Reports the whole before/after map, because every "
                       "physical unit has to be re-dialled to match.",
        "inputSchema": _schema({**_EVENT, **_WRITE, "start": _INT,
                                "universe": _INT}, []),
    },
    {
        "name": "set_venue",
        "description": "Point an event at a room in shared/venues/.",
        "inputSchema": _schema({**_EVENT, **_WRITE, "venue": _STR}, ["venue"]),
    },
    {
        "name": "new_event",
        "description": "Scaffold events/<name>/ with an empty rig pointing at a "
                       "room. Add fixtures with add_fixture afterwards.",
        "inputSchema": _schema({**_WRITE, "name": _STR, "venue": _STR},
                               ["name", "venue"]),
    },
    {
        "name": "import_profile",
        "description": "Copy a .qxf fixture definition into shared/fixtures/ so "
                       "it survives a fresh clone and can be patched.",
        "inputSchema": _schema({"path": _STR}, ["path"]),
    },
] + SHOW_TOOLS


# ------------------------------------------------------------------- tools --

def _edit(name: str, args: dict, apply) -> dict:
    """Shared path for every editing tool: load, edit, report, maybe write."""
    event = args.get("event", "despacio")
    write = bool(args.get("write", False))
    lib = patch.library()
    cfg = patch.load_rig_config(event)
    result = apply(cfg, lib)

    out = result.as_dict()
    out["dry_run"] = not write
    if not result.ok:
        out["written"] = False
        return out

    if not write:
        out["written"] = False
        out["hint"] = "call again with write=true to save this"
        return out

    holder = patch.held_by(event)
    if holder is not None:
        out["written"] = False
        out["ok"] = False
        out["errors"] = [
            f"{holder} is running a show against {event!r}. The engine does not "
            f"watch rig.json, so writing now would leave the file and the rig "
            f"disagreeing. Make the edit on the console's Setup tab, which "
            f"applies it live, or stop the show, or edit a copy."]
        return out

    out["written"] = True
    out["path"] = str(patch.write_rig(event, result.config))
    out["rig"] = patch.describe(event)
    return out


def call_show_tool(name: str, args: dict) -> "dict | None":
    """The show-folder tools, or None for a name that is not one."""
    d = args.get("show_dir")
    write = bool(args.get("write", False))
    if name == "show_status":
        return showtools.status(d)
    if name == "list_tracks":
        return showtools.list_tracks(d)
    if name == "list_routines":
        return showtools.list_routines(d)
    if name == "list_template_sets":
        return showtools.list_template_sets(d)
    gets = {"get_track": ("track", "id"), "get_routine": ("routine", "id"),
            "get_timeline": ("timeline", "track"),
            "get_template_set": ("template_set", "id")}
    if name in gets:
        kind, key = gets[name]
        return showtools.get_doc(kind, args[key], d)
    puts = {"put_routine": "routine", "put_timeline": "timeline",
            "put_template_set": "template_set"}
    if name in puts:
        return showtools.put_doc(puts[name], args["doc"], args.get("base_rev"),
                                 write, d)
    if name == "edit_timeline":
        return showtools.edit_timeline(args["track"], args["ops"],
                                       args.get("base_rev"), write, d)
    if name == "link_track":
        return showtools.link_track(args["track"], args["title"],
                                    args.get("artist", ""), args.get("album", ""),
                                    args.get("signature"), write, d)
    if name == "lint_show":
        return showtools.lint(d, args.get("event"))
    if name == "explain_position":
        return showtools.explain(args["track"], float(args["beat"]), d,
                                 args.get("event"))
    return None


def call_tool(name: str, args: dict) -> dict:
    shown = call_show_tool(name, args)
    if shown is not None:
        return shown
    if name == "describe_rig":
        return patch.describe(args.get("event", "despacio"))
    if name == "list_profiles":
        return {"profiles": patch.list_profiles()}
    if name == "list_venues":
        return {"venues": patch.list_venues()}
    if name == "list_inventory":
        return patch.load_inventory()
    if name == "import_profile":
        return patch.import_profile(Path(args["path"])).as_dict()

    if name == "new_event":
        result = patch.new_event(args["name"], args["venue"])
        out = result.as_dict()
        out["dry_run"] = not args.get("write", False)
        if result.ok and args.get("write"):
            (patch.EVENTS / args["name"]).mkdir(parents=True, exist_ok=True)
            out["path"] = str(patch.write_rig(args["name"], result.config))
            out["written"] = True
        else:
            out["written"] = False
        return out

    edits = {
        "add_fixture": lambda cfg, lib: patch.add_fixture(
            cfg, name=args["name"], manufacturer=args["manufacturer"],
            model=args["model"], mode=args["mode"], address=args.get("address"),
            universe=args.get("universe", 0), tags=args.get("tags", ()),
            position=args.get("position"), beam_deg=args.get("beam_deg"),
            notes=args.get("notes", ""), lib=lib),
        "remove_fixture": lambda cfg, lib: patch.remove_fixture(
            cfg, args["name"], lib=lib),
        "set_address": lambda cfg, lib: patch.set_address(
            cfg, args["name"], args["address"], args.get("universe"), lib=lib),
        "set_tags": lambda cfg, lib: patch.set_tags(
            cfg, args["name"], args["tags"], lib=lib),
        "set_position": lambda cfg, lib: patch.set_position(
            cfg, args["name"], args["x"], args["y"], args["z"], lib=lib),
        "autopatch": lambda cfg, lib: patch.autopatch(
            cfg, universe=args.get("universe"), start=args.get("start", 1),
            lib=lib),
        "set_venue": lambda cfg, lib: patch.set_venue(
            cfg, args["venue"], lib=lib),
    }
    if name in edits:
        return _edit(name, args, edits[name])
    raise ValueError(f"unknown tool {name!r}")


# --------------------------------------------------------------- transport --

def handle(request: dict) -> "dict | None":
    """One JSON-RPC request to one response, or None for a notification."""
    method = request.get("method")
    request_id = request.get("id")

    def ok(result):
        return {"jsonrpc": "2.0", "id": request_id, "result": result}

    if method == "initialize":
        return ok({
            "protocolVersion": PROTOCOL_VERSION,
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "klights", "version": "0.1.0"},
        })
    if method in ("notifications/initialized", "initialized"):
        return None                      # a notification: no id, no reply
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "ping":
        return ok({})
    if method == "tools/call":
        params = request.get("params") or {}
        name = params.get("name", "")
        try:
            payload = call_tool(name, params.get("arguments") or {})
            failed = payload.get("ok") is False
        except Exception as exc:                            # noqa: BLE001
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            payload, failed = {"error": f"{type(exc).__name__}: {exc}"}, True
        # Tool failures are reported INSIDE the result with isError, not as a
        # JSON-RPC error: a rejected patch is a normal outcome the caller should
        # read and act on, not a transport fault.
        return ok({"content": [{"type": "text",
                                "text": json.dumps(payload, indent=2)}],
                   "isError": bool(failed)})

    if request_id is None:
        return None
    return {"jsonrpc": "2.0", "id": request_id,
            "error": {"code": -32601, "message": f"unknown method {method!r}"}}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            continue
        try:
            response = handle(request)
        except Exception:                                   # noqa: BLE001
            print(traceback.format_exc(), file=sys.stderr, flush=True)
            response = {"jsonrpc": "2.0", "id": request.get("id"),
                        "error": {"code": -32603, "message": "internal error"}}
        if response is not None:
            sys.stdout.write(json.dumps(response) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
