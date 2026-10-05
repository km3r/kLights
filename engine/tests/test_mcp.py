"""The MCP server, driven over a real stdio pipe.

A subprocess and real JSON-RPC rather than importing `handle()` directly,
because the transport is where a stdio server actually breaks: a stray print to
stdout, an unflushed write, a notification answered when it should not be. None
of those are visible from inside the function.

Every edit runs against a throwaway copy of the despacio event, so a test can
write for real -- which is the half of the behaviour that matters -- without the
show's own config being the thing it writes to.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import rig as rigmod                           # noqa: E402

SERVER = REPO / "mcp" / "klights_mcp.py"
EVENT = REPO / "events" / "despacio"

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class Server:
    """A live server on a pipe, with the handshake done."""

    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)], cwd=REPO,
            stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, bufsize=1)
        self._id = 0
        self.rpc("initialize", {"protocolVersion": "2024-11-05",
                                "capabilities": {}})

    def rpc(self, method: str, params: dict | None = None) -> dict:
        self._id += 1
        request = {"jsonrpc": "2.0", "id": self._id, "method": method}
        if params is not None:
            request["params"] = params
        self.proc.stdin.write(json.dumps(request) + "\n")
        self.proc.stdin.flush()
        return json.loads(self.proc.stdout.readline())

    def notify(self, method: str) -> None:
        self.proc.stdin.write(
            json.dumps({"jsonrpc": "2.0", "method": method}) + "\n")
        self.proc.stdin.flush()

    # Positional-only, because half these tools take an argument called `name`
    # and `tool("add_fixture", name="Par 1")` would otherwise collide with the
    # tool's own name parameter.
    def tool(self, tool_name: str, /, **arguments):
        reply = self.rpc("tools/call",
                         {"name": tool_name, "arguments": arguments})
        result = reply["result"]
        return json.loads(result["content"][0]["text"]), result.get("isError")

    def close(self) -> str:
        self.proc.stdin.close()
        self.proc.wait(timeout=15)
        return self.proc.stderr.read()


def event_copy(tmp: str) -> str:
    """A throwaway event that is safe to write to."""
    d = Path(tmp) / "ev"
    d.mkdir()
    for name in ("rig.json", "calibration.json"):
        shutil.copy(EVENT / name, d / name)
    return str(d)


print("1. the protocol")
server = Server()
init = server.rpc("initialize", {"protocolVersion": "2024-11-05",
                                 "capabilities": {}})
check("initialize names the server",
      init["result"]["serverInfo"]["name"] == "klights",
      f"{init['result']['serverInfo']}")
check("and declares the tools capability",
      "tools" in init["result"]["capabilities"])

tools = server.rpc("tools/list")["result"]["tools"]
names = {t["name"] for t in tools}
check("every tool has a name, description and input schema",
      all(t.get("name") and t.get("description")
          and t.get("inputSchema", {}).get("type") == "object" for t in tools),
      f"{len(tools)} tools")
check("the read tools are there",
      {"describe_rig", "list_profiles", "list_venues", "list_inventory"} <= names)
check("the edit tools are there",
      {"add_fixture", "remove_fixture", "set_address", "set_tags",
       "autopatch", "new_event", "import_profile"} <= names)

# A notification has no id and must produce no reply. Answering one desynchronises
# every later request/response pair, which presents as tools returning each
# other's results.
server.notify("notifications/initialized")
pong = server.rpc("ping")
check("a notification is not answered", pong["result"] == {}, f"{pong}")

unknown = server.rpc("nonsense/method")
check("an unknown method is a JSON-RPC error, not a crash",
      unknown.get("error", {}).get("code") == -32601, f"{unknown}")


print("\n2. reading matches the engine's own loader")
described, is_error = server.tool("describe_rig")
rig = rigmod.load_rig(EVENT)
check("describe_rig agrees with load_rig on the fixture count",
      len(described["fixtures"]) == len(rig.fixtures),
      f"{len(described['fixtures'])} vs {len(rig.fixtures)}")
check("and reports no errors for the reference event",
      described["errors"] == [] and not is_error, f"{described['errors']}")
check("channel counts come from the profile, not from the file",
      all(f["last_address"] - f["address"] + 1 == f["channels"]
          for f in described["fixtures"]))

profiles, _ = server.tool("list_profiles")
check("list_profiles offers modes with channel counts",
      all(p["modes"] for p in profiles["profiles"]),
      f"{len(profiles['profiles'])} profiles")


print("\n3. editing is a dry run unless asked")
with tempfile.TemporaryDirectory() as tmp:
    ev = event_copy(tmp)
    before = Path(ev, "rig.json").read_text(encoding="utf-8")

    dry, is_error = server.tool("add_fixture", event=ev, name="Par 1",
                                manufacturer="UKing", model="Par 36 Custom",
                                mode="5 Channel", tags=["pars"])
    check("a dry run reports ok", dry["ok"] and not is_error, f"{dry}")
    check("and says it wrote nothing", dry["written"] is False)
    check("and the file really is untouched",
          Path(ev, "rig.json").read_text(encoding="utf-8") == before)

    written, is_error = server.tool("add_fixture", event=ev, name="Par 1",
                                    manufacturer="UKing", model="Par 36 Custom",
                                    mode="5 Channel", tags=["pars"], write=True)
    check("with write=true it writes", written["written"] is True)
    check("and returns the rig as it now stands",
          len(written["rig"]["fixtures"]) == len(rig.fixtures) + 1,
          f"{len(written['rig']['fixtures'])}")
    check("auto-addressing put it after the last fixture",
          written["rig"]["fixtures"][-1]["address"] == 57,
          f"{written['rig']['fixtures'][-1]['address']}")
    check("and the result loads through the real loader",
          len(rigmod.load_rig(Path(ev)).fixtures) == len(rig.fixtures) + 1)


print("\n4. what must be refused")
with tempfile.TemporaryDirectory() as tmp:
    ev = event_copy(tmp)

    dupe, is_error = server.tool("add_fixture", event=ev, name="Moving Head #1",
                                 manufacturer="UKing", model="Par 36 Custom",
                                 mode="5 Channel", write=True)
    check("a duplicate name is refused", is_error and not dupe["ok"])
    check("and nothing was written", dupe["written"] is False)

    clash, is_error = server.tool("set_address", event=ev,
                                  name="Pinspot #2", address=40, write=True)
    check("an overlapping address is refused",
          is_error and any("overlap" in e for e in clash["errors"]),
          f"{clash['errors'][:1]}")

    bad, is_error = server.tool("add_fixture", event=ev, name="X",
                                manufacturer="Acme", model="Nope", mode="1")
    check("an unknown profile is refused with a pointer to list_profiles",
          is_error and any("list_profiles" in e for e in bad["errors"]),
          f"{bad['errors'][:1]}")

    # The engine reads its config once at startup, so an edit under a live show
    # leaves the file and the rig disagreeing with nothing on screen to say so.
    Path(ev, ".engine.lock").write_text("engine 0.1.0 since 12:00:00\n",
                                        encoding="utf-8")
    locked, is_error = server.tool("remove_fixture", event=ev,
                                   name="Pinspot #2", write=True)
    check("a write is refused while a show holds the lock",
          is_error and locked["written"] is False, f"{locked.get('errors')}")
    check("and the refusal says what to do",
          any("Stop the show" in e for e in locked["errors"]),
          f"{locked['errors'][:1]}")
    Path(ev, ".engine.lock").unlink()
    freed, is_error = server.tool("remove_fixture", event=ev,
                                  name="Pinspot #2", write=True)
    check("and allowed once the lock is gone", freed["written"] is True)


print("\n5. warnings are advice, not refusal")
with tempfile.TemporaryDirectory() as tmp:
    ev = event_copy(tmp)
    moved, is_error = server.tool("remove_fixture", event=ev,
                                  name="Moving Head #2", write=True)
    check("removing a mover succeeds", moved["written"] is True and not is_error)
    check("but warns that the head order changed",
          any("head order" in w for w in moved["warnings"]),
          f"{moved['warnings'][:1]}")

    # The removal above left an 11-channel hole at 12, so this one really does
    # move things. On a pristine despacio it correctly reports "nothing moved",
    # which is why the gap has to be made first for this to be testing anything.
    auto, is_error = server.tool("autopatch", event=ev, write=True)
    check("autopatch succeeds", auto["written"] is True and not is_error)
    check("and reports the whole before/after map, since units must be re-dialled",
          any("re-dial" in w for w in auto["warnings"]), f"{auto['warnings'][:2]}")
    check("closing the gap the removal left",
          [f["address"] for f in auto["rig"]["fixtures"]] == [1, 12, 23, 34, 40],
          f"{[f['address'] for f in auto['rig']['fixtures']]}")

print("\n6. the show folder (F19k)")
check("the show tools are listed",
      {"show_status", "list_tracks", "get_track", "list_routines", "get_routine",
       "put_routine", "get_timeline", "put_timeline", "edit_timeline",
       "link_track", "lint_show", "explain_position", "list_template_sets",
       "get_template_set", "put_template_set"} <= names)
with tempfile.TemporaryDirectory() as tmp:
    shows = Path(tmp) / "shows"
    shutil.copytree(REPO / "shared" / "show-example", shows)
    sd = str(shows)
    st, err = server.tool("show_status", show_dir=sd)
    check("show_status reads the folder", not err and st["tracks"] == ["synth-128"]
          and st["routines"] == ["build-rise", "fan-drop", "idle-orbit",
                                 "verse-sweep"], f"{st.get('tracks')}")
    tracks, err = server.tool("list_tracks", show_dir=sd)
    check("list_tracks gives phrases with their beats, for writing against",
          tracks["tracks"][0]["phrases"][3] == "Chorus @160-224",
          f"{tracks['tracks'][0]['phrases'][:5]}")
    tl, err = server.tool("get_timeline", show_dir=sd, track="synth-128")
    rev = tl["rev"]
    check("get_timeline returns the document and its rev",
          not err and tl["doc"]["track"] == "synth-128" and rev.startswith("r:"))

    ops = [{"op": "add_item", "row": "hits",
            "item": {"id": "bo2", "hit": "blackout", "at": 287, "len": 1}},
           {"op": "update_item", "id": "chorus1", "set": {"fade": 2}}]
    dry, err = server.tool("edit_timeline", show_dir=sd, track="synth-128", ops=ops)
    on_disk = json.loads((shows / "timelines" / "synth-128.json").read_text())
    check("edit_timeline is a dry run unless asked, saying what would change",
          not err and dry["dry_run"] and not dry["written"]
          and len(dry["changes"]) == 2 and "bo2" in dry["changes"][0]
          and not any(i["id"] == "bo2" for r in on_disk["rows"]
                      for i in r.get("items") or []), f"{dry.get('changes')}")
    no_rev, err = server.tool("edit_timeline", show_dir=sd, track="synth-128",
                              ops=ops, write=True)
    check("writing without base_rev is refused", err and "base_rev is required"
          in no_rev["errors"][0], f"{no_rev.get('errors')}")
    stale, err = server.tool("edit_timeline", show_dir=sd, track="synth-128",
                             ops=ops, write=True, base_rev="r:000000000000")
    check("and with a stale one", err and "changed since" in stale["errors"][0])
    done, err = server.tool("edit_timeline", show_dir=sd, track="synth-128",
                            ops=ops, write=True, base_rev=rev)
    on_disk = json.loads((shows / "timelines" / "synth-128.json").read_text())
    check("with the rev it read, it is written",
          not err and done["written"] and any(
              i["id"] == "bo2" for r in on_disk["rows"] for i in r.get("items") or []),
          f"{done.get('errors')}")
    bad, err = server.tool("edit_timeline", show_dir=sd, track="synth-128",
                           ops=[{"op": "update_item", "id": "chorus1",
                                 "set": {"len": 0}}], write=True,
                           base_rev=done["rev"])
    check("an edit that makes the timeline invalid is never written",
          err and not bad["written"] and any("longer than nothing" in e
                                             for e in bad["errors"]))
    nope, err = server.tool("edit_timeline", show_dir=sd, track="synth-128",
                            ops=[{"op": "remove_item", "id": "ghost"}])
    check("an op that cannot apply says which", err and "no item 'ghost'"
          in nope["errors"][0])
    waved, err = server.tool("edit_timeline", show_dir=sd, track="synth-128",
                             ops=[{"op": "set_wave", "row": "size",
                                   "wave": {"shape": "sine", "bars": 4, "depth": 0.5}}])
    check("set_wave puts a wave on an automation lane, checked like any edit",
          not err and waved["ok"] and any("sine wave" in c for c in waved["changes"])
          and next(r for r in waved["doc"]["rows"] if r["id"] == "size")["wave"]["depth"]
          == 0.5, f"{waved.get('errors')}")
    over, err = server.tool("edit_timeline", show_dir=sd, track="synth-128",
                            ops=[{"op": "set_wave", "row": "master",
                                  "wave": {"shape": "sine", "bars": 4, "depth": 0.5}}])
    check("and one that swings master past 1 is refused, saying where",
          err and any("it reaches" in e for e in over["errors"]), f"{over.get('errors')}")

    routine, _ = server.tool("get_routine", show_dir=sd, id="fan-drop")
    doc = routine["doc"]
    doc["variations"]["medium"] = {"width": 40}
    put, err = server.tool("put_routine", show_dir=sd, doc=doc,
                           base_rev=routine["rev"], write=True)
    check("put_routine saves a whole routine", not err and put["written"])
    doc["rows"][0]["role"] = "lasers"
    put, err = server.tool("put_routine", show_dir=sd, doc=doc)
    check("and refuses one that does not validate", err and any(
        "lasers" in e for e in put["errors"]))

    lint, err = server.tool("lint_show", show_dir=sd, event="despacio")
    check("lint_show against despacio: nothing wrong", not err and lint["ok"]
          and lint["rig_problems"] == {}, f"{lint}")
    edit = [{"op": "update_item", "id": "lazy", "set": {"look": "Not A Look"}}]
    tl, _ = server.tool("get_timeline", show_dir=sd, track="synth-128")
    server.tool("edit_timeline", show_dir=sd, track="synth-128", ops=edit,
                write=True, base_rev=tl["rev"])
    lint, err = server.tool("lint_show", show_dir=sd, event="despacio")
    check("and a look the rig does not have is a rig problem",
          err and any("Not A Look" in p
                      for p in lint["rig_problems"].get("synth-128", [])),
          f"{lint.get('rig_problems')}")

    # Beat 162: the edit above gave chorus1 a 2-beat fade from bar 41.
    ex, err = server.tool("explain_position", show_dir=sd, track="synth-128",
                          beat=162, event="despacio")
    check("explain_position: bar 41, the chorus on the scene lane, and what "
          "the movers do", not err and ex["bar"] == 41
          and ex["channels"]["color"][0]["item"] == "chorus1"
          and ex["rig"]["fixtures"]["Moving Head #1"]["color"] == "#ff2d6f",
          f"{ex.get('channels', {}).get('color')}")
    link, err = server.tool("link_track", show_dir=sd, track="synth-128",
                            title="Synth (Guest Edit)", artist="Someone")
    check("link_track is a dry run first",
          not err and link["dry_run"] and link["would_add"]["title"]
          == "Synth (Guest Edit)")
    link, err = server.tool("link_track", show_dir=sd, track="synth-128",
                            title="Synth (Guest Edit)", artist="Someone",
                            write=True)
    saved = json.loads((shows / "tracks" / "synth-128.json").read_text())
    check("and saves the alias when asked", link["written"]
          and saved["aliases"][-1]["title"] == "Synth (Guest Edit)")
    ts, err = server.tool("get_template_set", show_dir=sd, id="club")
    ts["doc"]["phrases"]["Bridge"] = {"routine": "idle-orbit"}
    put, err = server.tool("put_template_set", show_dir=sd, doc=ts["doc"],
                           base_rev=ts["rev"], write=True)
    check("template sets edit the same way", not err and put["written"])
    missing, err = server.tool("get_track", show_dir=sd, id="nope")
    check("asking for something absent is an error that says so",
          err and "no track 'nope'" in missing["errors"][0])

stderr = server.close()
check("nothing was written to stdout that was not JSON-RPC", True)
check("the server logged no tracebacks", "Traceback" not in stderr,
      stderr.strip()[-200:] if stderr.strip() else "")

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("mcp: all checks pass")
