"""An MCP server for the rig, so it can be described in conversation.

    python mcp/cosmos_mcp.py

Speaks MCP over stdio: JSON-RPC 2.0, one object per line, on stdin and stdout.
Written against the protocol directly rather than against an SDK, because the
whole engine is standard library only and a tool for editing the show's config
is a poor place to introduce the first dependency. The protocol surface actually
needed is three methods.

**Everything it can change goes through `engine.patch`.** This file is a
translation layer and nothing else: no validation rules live here, so the answer
to "is this patch legal" cannot drift between saying it in chat, typing it at
the CLI, and tapping it in the UI.

Two deliberate restrictions:

  * **Writes are opt-in per call.** Every editing tool takes `write`, defaulting
    to false, and a dry run reports exactly what would change. Describing a rig
    out loud is a lossy process and the first attempt is usually wrong.
  * **It refuses to write while a show is running.** The engine reads its config
    once at startup, so an edit mid-show leaves the file saying one thing and
    the rig doing another, with nothing on screen to explain it.

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
]


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
            f"{holder} is running a show against {event!r}. The engine reads its "
            f"config once at startup, so writing now would leave the file and "
            f"the rig disagreeing. Stop the show, or edit a copy."]
        return out

    out["written"] = True
    out["path"] = str(patch.write_rig(event, result.config))
    out["rig"] = patch.describe(event)
    return out


def call_tool(name: str, args: dict) -> dict:
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
            "serverInfo": {"name": "cosmos-lights", "version": "0.1.0"},
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
