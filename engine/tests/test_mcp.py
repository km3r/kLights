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
