"""
Tests for the show server.

Driven over a REAL socket against a REAL server -- handshake, framing, masking
and all -- because the parts most likely to be wrong are exactly the parts a
mock would skip. The client below is a deliberately independent implementation
of the framing, so a bug shared between encoder and decoder cannot hide.

Run: python engine/tests/test_server.py
"""

import atexit
import base64
import copy
import errno
import json
import math
import os
import shutil
import socket
import struct
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import patch as patchmod
from engine import rig as rigmod
from engine import state as statemod
from engine import websocket as wsmod
from engine import blocks as blocksmod
from engine import params as parammod
from engine import server as servermod
from engine.server import ShowController, ShowServer

# Every controller here runs against a COPY of the event, never the real one.
# These tests save presets, save the venue and attempt calibration writes
# through the same paths the UI uses, and each of those rewrites a real show's
# file: cleaning up after itself left the content right but the line endings
# changed, and a test that crashed between a save and its delete would have
# left junk presets in tonight's show. The room is copied too -- the despacio
# rig names a venue in shared/venues/, which venue_save writes -- and both
# library lookups are pointed at the copy. Safe to patch module globals: every
# suite runs in its own interpreter (see engine/tests/__main__.py).
#
# Named "despacio" because the engine reports the folder name as the event.
EVENT_TMP = Path(tempfile.mkdtemp(prefix="klights-server-"))
atexit.register(shutil.rmtree, EVENT_TMP, ignore_errors=True)
_SKIP = shutil.ignore_patterns("__pycache__", "*.bak", ".engine.lock", "backups")
EVENT = EVENT_TMP / "despacio"
shutil.copytree(REPO / "events" / "despacio", EVENT, ignore=_SKIP)
shutil.copytree(rigmod.VENUE_LIBRARY, EVENT_TMP / "venues", ignore=_SKIP)
rigmod.VENUE_LIBRARY = patchmod.VENUES = EVENT_TMP / "venues"

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        # With the detail: `python -m engine.tests` (and so CI) shows only the
        # tail of a failing suite, which is this list -- a label alone says
        # which check failed but not by how much.
        failures.append(label + (f"  -- {detail}" if detail else ""))


# -- an independent client ----------------------------------------------------

class Client:
    """Minimal WebSocket client, written separately from the server's framing."""

    def __init__(self, port: int, name: str = "test", path: str = "/",
                 origin: str = ""):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.settimeout(5)
        self.buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        lines = [
            f"GET {path} HTTP/1.1",
            "Host: localhost",
            "Upgrade: websocket",
            "Connection: Upgrade",
            f"Sec-WebSocket-Key: {key}",
            "Sec-WebSocket-Version: 13",
        ]
        if origin:
            lines.append(f"Origin: {origin}")
        self.sock.sendall(("\r\n".join(lines) + "\r\n\r\n").encode())
        header = self._read_until(b"\r\n\r\n")
        self.status = header.split(b"\r\n")[0].decode()
        self.accept = wsmod.accept_key(key)
        self.header = header.decode(errors="replace")

    def _read_until(self, marker: bytes) -> bytes:
        while marker not in self.buf:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed during handshake")
            self.buf += chunk
        idx = self.buf.index(marker) + len(marker)
        out, self.buf = self.buf[:idx], self.buf[idx:]
        return out

    def _exact(self, n: int) -> bytes:
        while len(self.buf) < n:
            chunk = self.sock.recv(65536)
            if not chunk:
                raise ConnectionError("closed")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def recv(self) -> dict:
        while True:
            first, second = self._exact(2)
            opcode = first & 0x0F
            length = second & 0x7F
            if length == 126:
                (length,) = struct.unpack(">H", self._exact(2))
            elif length == 127:
                (length,) = struct.unpack(">Q", self._exact(8))
            masked = bool(second & 0x80)
            if masked:                                  # servers must not mask
                raise AssertionError("server masked a frame")
            payload = self._exact(length)
            if opcode == wsmod.OP_TEXT:
                return json.loads(payload.decode())
            if opcode == wsmod.OP_CLOSE:
                raise ConnectionError("server closed")

    def send(self, obj: dict) -> None:
        payload = json.dumps(obj).encode()
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        header = bytearray([0x81])
        n = len(payload)
        if n < 126:
            header.append(0x80 | n)
        elif n < 1 << 16:
            header.append(0x80 | 126)
            header += struct.pack(">H", n)
        else:
            header.append(0x80 | 127)
            header += struct.pack(">Q", n)
        self.sock.sendall(bytes(header) + mask + masked)

    def wait_for(self, predicate, timeout=6.0):
        """Next state frame satisfying `predicate`. State is broadcast, so a
        command's effect arrives on a later frame, not as a reply."""
        deadline = time.time() + timeout
        last = None
        while time.time() < deadline:
            msg = self.recv()
            if msg.get("type") != "state":
                continue
            last = msg
            if predicate(msg):
                return msg
        raise AssertionError(f"timed out; last state: "
                             f"{json.dumps(last)[:400] if last else None}")

    def close(self):
        try:
            self.sock.close()
        except OSError:
            pass


# -- the handshake, in isolation ----------------------------------------------
print("\n1. RFC 6455 handshake")
# The canonical example from the RFC itself, so this checks the implementation
# against the spec rather than against itself.
check("accept key matches the RFC's worked example",
      wsmod.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=",
      wsmod.accept_key("dGhlIHNhbXBsZSBub25jZQ=="))

frame = wsmod.encode_frame(b"hi")
check("a short frame is FIN|text, unmasked", frame[0] == 0x81 and frame[1] == 2,
      frame.hex())
big = wsmod.encode_frame(b"x" * 200)
check("a 200-byte frame uses the 16-bit length", big[1] == 126
      and struct.unpack(">H", big[2:4])[0] == 200)
huge = wsmod.encode_frame(b"x" * 70000)
check("a 70k frame uses the 64-bit length", huge[1] == 127
      and struct.unpack(">Q", huge[2:10])[0] == 70000)


# -- a live server ------------------------------------------------------------
print("\n2. a live server, over a real socket")
controller = ShowController(EVENT, fps=40.0)
server = ShowServer(controller, port=0)
server.start()
port = server.httpd.server_address[1]

# A second engine on this port must be refused, not join it. http.server sets
# SO_REUSEADDR, which on Windows lets a second socket bind a port another
# process is LISTENING on -- silently, so two engines answer one port and which
# one a phone reaches is luck.
#
# Its own controller, never started, as a second engine would have. Sharing the
# live one is not harmless: a ShowServer takes its controller's `reply_to` when
# it is built, so this one would leave every later reply going nowhere. And
# built BEFORE the live clock starts, as a second engine would be: loading an
# event in this process holds the GIL long enough to drop a frame on a slow
# runner.
second = ShowServer(ShowController(EVENT), port=port)
try:
    second.start()
    refused = None
except OSError as exc:
    refused = exc
finally:
    second.stop()
check("a second server on a taken port is refused",
      refused is not None and refused.errno == errno.EADDRINUSE,
      repr(refused) if refused else "the second bind succeeded")

controller.start()
time.sleep(0.3)

# And the engine itself says so and leaves before it touches the rig: no
# traceback, and the lock the running engine wrote is neither rewritten nor
# deleted by the one that lost.
from engine import patch as patchmod  # noqa: E402
from engine import sync as syncmod  # noqa: E402


def second_engine(*args):
    """`python -m engine.server` on this event: its exit code and output."""
    try:
        run = subprocess.run(
            [sys.executable, "-m", "engine.server", "--no-token",
             "--event", str(controller.event_dir), *args],
            cwd=REPO, capture_output=True, text=True, errors="replace", timeout=30)
        return run.returncode, run.stdout + run.stderr
    except subprocess.TimeoutExpired:
        return None, "still running after 30 s"


lock = patchmod.lock_path(str(controller.event_dir))
lock_before = lock.stat().st_mtime_ns if lock.exists() else None
code, output = second_engine("--port", str(port))
check("a second engine on a taken port exits saying why",
      code not in (0, None) and f"port {port} is already in use" in output
      and "Traceback" not in output, f"exit {code}: {output.strip()[-300:]}")
check("... before touching the rig: the running engine's lock is untouched",
      lock_before is not None and lock.exists()
      and lock.stat().st_mtime_ns == lock_before)

# The tempo port too: an engine sharing it hears none of the DJ.
holder = syncmod.SyncListener(on_sync=lambda fields: None, port=0, bind="127.0.0.1")
holder.start()
sync_port = holder.sock.getsockname()[1]
try:
    code, output = second_engine("--port", "0", "--sync-port", str(sync_port))
finally:
    holder.stop()
check("a second engine on a taken sync port exits saying why",
      code not in (0, None) and f"sync port {sync_port} is already in use" in output
      and "Traceback" not in output, f"exit {code}: {output.strip()[-300:]}")
check("... also before touching the rig",
      lock.exists() and lock.stat().st_mtime_ns == lock_before)

client = Client(port)
check("server returned 101", "101" in client.status, client.status)
check("Sec-WebSocket-Accept is correct", client.accept in client.header)

welcome = client.recv()
check("welcome carries a client id", welcome["type"] == "welcome" and welcome["id"])

state = client.recv()
check("first state frame arrives immediately", state["type"] == "state")
check("it describes the rig",
      len(state["fixtures"]) == len(controller.rig.fixtures)
      and state["event"] == "despacio",
      f"{len(state['fixtures'])} fixtures")
check("frames are flowing", state["stats"]["frames"] > 0,
      f"{state['stats']['frames']} frames at {state['stats']['fps']} fps")


# -- commands take effect -----------------------------------------------------
print("\n3. commands")
client.send({"type": "hello", "name": "phone"})
after = client.wait_for(lambda s: any(p["name"] == "phone" for p in s["presence"]))
check("presence shows the client by name",
      [p["name"] for p in after["presence"]] == ["phone"],
      f"{[p['name'] for p in after['presence']]}")

# Names come from the loaded library rather than being hardcoded, so this
# survives a re-port. One of each slot, because the whole point is that they are
# independent.
MOVE_A, MOVE_B = [e.name for e in controller.library if e.is_movement][:2]
COLOR_A, COLOR_B = [e.name for e in controller.library if e.is_color][:2]
LEVEL_A = next(e.name for e in controller.library if e.kind == "level_path")
LOOK_A, LOOK_B = MOVE_A, MOVE_B

def loaded(state, slot):
    """Slot selections as a flat set of names -- selection is per fixture GROUP
    now (a pinspot colour and a mover colour are held separately), and most of
    these assertions only care about which looks are up."""
    return set(state["selection"][slot].values())


client.send({"type": "select_look", "name": MOVE_A})
after = client.wait_for(lambda s: MOVE_A in loaded(s, "movement"))
check("selecting a look takes effect", MOVE_A in loaded(after, "movement"))
check("the ported library is what is on offer", len(after["looks"]) > 100,
      f"{len(after['looks'])} looks")
check("and each carries the kind, slot and groups the UI files it by",
      all({"kind", "slot", "groups"} <= set(l) for l in after["looks"]),
      f"slots {sorted({l['slot'] for l in after['looks']})}, "
      f"groups {sorted({g for l in after['looks'] for g in l['groups']})}")
check("and it is held, so auto cannot steal it", after["auto"]["held"] is True)

# -- the three slots do not overwrite each other -----------------------------
# This is the bug the slot model exists to fix: while one selection replaced the
# whole show, picking a colour discarded the move you had running.
client.send({"type": "select_look", "name": COLOR_A})
after = client.wait_for(lambda s: COLOR_A in loaded(s, "color"))
check("picking a colour keeps the movement",
      loaded(after, "movement") == {MOVE_A} and loaded(after, "color") == {COLOR_A},
      f"{after['selection']}")

client.send({"type": "select_look", "name": MOVE_B})
after = client.wait_for(lambda s: MOVE_B in loaded(s, "movement"))
check("changing the movement keeps the colour",
      loaded(after, "color") == {COLOR_A}, f"{after['selection']}")

client.send({"type": "select_look", "name": LEVEL_A})
after = client.wait_for(lambda s: LEVEL_A in loaded(s, "level"))
check("and a level chase sits alongside both",
      loaded(after, "movement") == {MOVE_B} and loaded(after, "color") == {COLOR_A}
      and loaded(after, "level") == {LEVEL_A}, f"{after['selection']}")

# -- and looks scoped to different fixture types coexist ----------------------
# "Pin Ball Glow" writes only the two pinspots in the workspace. It used to port
# as an untagged uniform colour, so selecting it repainted the four movers as
# well -- and one colour slot for the whole rig meant it also discarded whatever
# the movers were on.
MOVER_COLOR = next(e.name for e in controller.library
                   if e.is_color and "pinspots" not in e.groups)
PIN_COLOR = next(e.name for e in controller.library
                 if e.is_color and "pinspots" in e.groups)
client.send({"type": "select_look", "name": MOVER_COLOR})
client.send({"type": "select_look", "name": PIN_COLOR})
after = client.wait_for(lambda s: PIN_COLOR in loaded(s, "color"))
check("a pinspot colour and a mover colour are up at the same time",
      loaded(after, "color") == {MOVER_COLOR, PIN_COLOR}, f"{after['selection']['color']}")

movers = [f for f in after["fixtures"] if f["is_mover"]]
pins = [f for f in after["fixtures"] if not f["is_mover"]]
check("and neither repaints the other's fixtures",
      all(m["color"] != pins[0]["color"] for m in movers),
      f"movers {movers[0]['color']}, pinspots {pins[0]['color']}")

client.send({"type": "clear_slot", "slot": "color", "group": "pinspots"})
after = client.wait_for(lambda s: PIN_COLOR not in loaded(s, "color"))
check("one group's colour clears without touching the other",
      loaded(after, "color") == {MOVER_COLOR}, f"{after['selection']['color']}")

client.send({"type": "clear_slot", "slot": "level"})
after = client.wait_for(lambda s: not loaded(s, "level"))
check("a slot can be emptied without disturbing the others",
      loaded(after, "movement") == {MOVE_B}, f"{after['selection']}")

client.send({"type": "clear_slot", "slot": "movement"})
after = client.wait_for(lambda s: any("cannot be empty" in n for n in s["notices"]))
check("but movement cannot be emptied -- the heads must point somewhere",
      loaded(after, "movement") == {MOVE_B}, f"{after['selection']['movement']}")

# -- presets restore every slot and every group at once -----------------------
client.send({"type": "select_look", "name": PIN_COLOR})
client.send({"type": "preset_save", "name": "test preset"})
after = client.wait_for(lambda s: any(p["name"] == "test preset" for p in s["presets"]))
saved = next(p for p in after["presets"] if p["name"] == "test preset")
check("a preset saves the whole picture, per group",
      set(saved["color"].values()) == {MOVER_COLOR, PIN_COLOR}, f"{saved['color']}")

client.send({"type": "select_look", "name": MOVE_A})
client.send({"type": "clear_slot", "slot": "color"})
after = client.wait_for(lambda s: not loaded(s, "color"))
client.send({"type": "preset_apply", "name": "test preset"})
after = client.wait_for(lambda s: PIN_COLOR in loaded(s, "color"))
check("and applying it restores every slot at once",
      loaded(after, "movement") == {MOVE_B}
      and loaded(after, "color") == {MOVER_COLOR, PIN_COLOR},
      f"{after['selection']}")
client.send({"type": "preset_delete", "name": "test preset"})
after = client.wait_for(lambda s: not any(p["name"] == "test preset" for p in s["presets"]))
check("and it can be deleted again", True)

# save_presets rewrites the file in full, so every key that should outlive a
# save has to be named in it. The first version did not name $schema, and the
# first preset saved from the UI silently stripped the editor's completion out
# of the file. Same trap as calibration.json, which is written the same way.
presets_file = json.loads(
    (EVENT / "presets.json").read_text(encoding="utf-8"))
check("saving presets keeps the file's $schema",
      presets_file.get("$schema", "").endswith("presets.schema.json"),
      f"{presets_file.get('$schema')!r}")
check("saving presets keeps the file's explanatory comment",
      "_comment" in presets_file)

# -- the snapshot must never see a container mid-edit --------------------------
#
# `snapshot()` runs on the BROADCAST thread; commands run on the output thread;
# there is no lock between them. So anything the snapshot reads has to be
# replaced wholesale rather than edited in place -- rebinding is atomic, and a
# reader then sees the state before or the state after and never one being
# changed underneath it.
#
# This was not theoretical. `presets.sort()` in place is what made it real:
# CPython empties a list for the duration of a sort, so a console mid-save could
# broadcast "Presets - 0" and an empty grid. Asserting the identity of the
# container is the deterministic way to test a race that is otherwise a
# microsecond wide.
held_presets = controller.presets
held_flashing = controller.flashing
controller.apply({"type": "preset_save", "name": "rebind check"}, None)
check("saving a preset rebinds the list rather than sorting it in place",
      controller.presets is not held_presets
      and not any(p["name"] == "rebind check" for p in held_presets),
      f"{len(held_presets)} -> {len(controller.presets)}")
controller.apply({"type": "preset_move", "name": "rebind check",
                  "bank": 4, "cell": 7}, None)
check("and so does moving one", controller.presets is not held_presets)
controller.apply({"type": "preset_tag", "name": "rebind check",
                  "tags": ["x"]}, None)
check("and tagging one -- the dict is replaced, not edited",
      all(p.get("tags") != ["x"] for p in held_presets))
controller.apply({"type": "preset_delete", "name": "rebind check"}, None)

controller.apply({"type": "flash", "target": "all"}, None)
check("flashing rebinds too -- a set that changes size mid-iteration raises, "
      "and takes the whole broadcast with it",
      controller.flashing is not held_flashing and not held_flashing)
controller.apply({"type": "flash_clear"}, None)


# -- preset banks: a pad is a PLACE, and it does not move ---------------------
#
# The flat uncapped grid was finding #17 of the review: presets accumulate, the
# grid grows, and nothing is where it was last week. Pages of eight with fixed
# positions is what the APC40 taught -- "the drop is bottom-right of bank 2" is
# muscle memory that already exists here.

# The pure layout rules first, where every branch is reachable without a socket.
check("a preset with no bank lands on the first free pad",
      [(p["bank"], p["cell"]) for p in servermod.arrange_presets(
          [{"name": "a"}, {"name": "b"}])] == [(1, 0), (1, 1)])
check("one that names a pad keeps it, and is not shuffled by its neighbours",
      [(p["name"], p["bank"], p["cell"]) for p in servermod.arrange_presets(
          [{"name": "a"}, {"name": "pinned", "bank": 1, "cell": 0}])]
      == [("pinned", 1, 0), ("a", 1, 1)])
check("two presets claiming one pad: first claim wins, second is rehomed",
      {p["name"]: p["cell"] for p in servermod.arrange_presets(
          [{"name": "first", "bank": 1, "cell": 2},
           {"name": "second", "bank": 1, "cell": 2}])}
      == {"first": 2, "second": 0})
# Being told "your banks are full" while building a show is not a thing a
# console gets to do, so the last bank grows instead.
nine = servermod.arrange_presets([{"name": f"p{i}"} for i in range(9)])
check("a ninth preset opens a second bank rather than refusing",
      (nine[-1]["bank"], nine[-1]["cell"]) == (2, 0),
      f"{nine[-1]['bank']}.{nine[-1]['cell']}")
check("garbage in the presets list is skipped, not crashed on",
      [p["name"] for p in servermod.arrange_presets(
          [{"name": "ok"}, {"nameless": True}, "not a dict", None])] == ["ok"])
# `bank: true` is an int to isinstance and would sail through as bank 1.
check("a boolean where a bank number should be is rehomed",
      servermod.arrange_presets(
          [{"name": "a", "bank": True, "cell": 0}])[0]["bank"] == 1)

# Then the behaviour that actually matters at the desk: re-recording a preset
# must not move it. A pad that wanders on every save is worse than no pad.
client.send({"type": "preset_save", "name": "banked", "bank": 3, "cell": 5,
             "tags": ["drop"]})
after = client.wait_for(lambda s: any(p["name"] == "banked" for p in s["presets"]))
banked = next(p for p in after["presets"] if p["name"] == "banked")
check("a preset can be saved onto a named pad",
      (banked["bank"], banked["cell"], banked["tags"]) == (3, 5, ["drop"]),
      f"{banked['bank']}.{banked['cell']} {banked['tags']}")
check("and the bank count grows to include it",
      after["preset_banks"]["count"] >= 3, f"{after['preset_banks']}")

client.send({"type": "master", "value": 0.42})
client.send({"type": "preset_save", "name": "banked"})
after = client.wait_for(
    lambda s: any(p["name"] == "banked" and p.get("master") == 0.42
                  for p in s["presets"]))
banked = next(p for p in after["presets"] if p["name"] == "banked")
check("re-recording it keeps its pad and its tags",
      (banked["bank"], banked["cell"], banked["tags"]) == (3, 5, ["drop"]),
      f"{banked['bank']}.{banked['cell']} {banked['tags']}")

client.send({"type": "preset_move", "name": "banked", "bank": 1, "cell": 0})
after = client.wait_for(
    lambda s: next(p for p in s["presets"] if p["name"] == "banked")["bank"] == 1)
moved = {p["name"]: (p["bank"], p["cell"]) for p in after["presets"]}
check("moving onto an occupied pad SWAPS, so a bank can be reordered",
      moved["banked"] == (1, 0) and moved.get("Phase a") == (3, 5),
      f"{moved}")

client.send({"type": "preset_delete", "name": "banked"})
client.wait_for(lambda s: not any(p["name"] == "banked" for p in s["presets"]))
client.send({"type": "preset_move", "name": "Phase a", "bank": 1, "cell": 0})
client.wait_for(
    lambda s: next(p for p in s["presets"] if p["name"] == "Phase a")["bank"] == 1)

client.send({"type": "master", "value": 0.25})
after = client.wait_for(lambda s: abs(s["master"] - 0.25) < 1e-6)
check("master level applies", abs(after["master"] - 0.25) < 1e-6)

client.send({"type": "bpm", "value": 140})
after = client.wait_for(lambda s: abs(s["clock"]["bpm"] - 140) < 1e-6)
check("tempo applies", abs(after["clock"]["bpm"] - 140) < 1e-6)

client.send({"type": "auto", "axis": "palette", "on": True})
after = client.wait_for(lambda s: s["auto"]["axes"]["palette"])
check("auto axes toggle independently",
      after["auto"]["axes"]["palette"] and not after["auto"]["axes"]["energy"],
      f"{after['auto']['axes']}")

client.send({"type": "color", "target": "pinspots", "color": [0.0, 1.0, 0.0]})
after = client.wait_for(lambda s: "pinspots" in s["color_overrides"])
pins = [f for f in after["fixtures"] if "pinspots" in f["tags"]]
check("a colour override reaches the fixtures",
      all(f["color"][1] > f["color"][0] for f in pins),
      f"{[f['color'] for f in pins]}")

# The override must survive an auto-mode look change -- that is the whole
# reason overrides are a separate list the controller re-attaches.
client.send({"type": "select_look", "name": LOOK_B})
after = client.wait_for(lambda s: s["auto"]["look"] == LOOK_B)
pins = [f for f in after["fixtures"] if "pinspots" in f["tags"]]
check("the override survives a look change",
      all(f["color"][1] > f["color"][0] for f in pins),
      f"{[f['color'] for f in pins]}")

# -- hand dimming, per group and per fixture ---------------------------------
print("\n3b. dimming by fixture and by group")
client.send({"type": "select_look", "name": MOVE_A})
client.send({"type": "master", "value": 1.0})
client.send({"type": "clear_slot", "slot": "level"})
after = client.wait_for(lambda s: abs(s["master"] - 1.0) < 1e-6 and not loaded(s, "level"))


def levels(state):
    return {f["name"]: round(f.get("intensity", 0), 2) for f in state["fixtures"]}


PIN = next(f["name"] for f in after["fixtures"] if not f["is_mover"])
HEAD = next(f["name"] for f in after["fixtures"] if f["is_mover"])

client.send({"type": "level", "target": "pinspots", "value": 0.3})
after = client.wait_for(lambda s: abs(levels(s)[PIN] - 0.3) < 0.02)
check("a GROUP can be dimmed on its own",
      abs(levels(after)[PIN] - 0.3) < 0.02 and levels(after)[HEAD] > 0.5,
      f"{PIN} {levels(after)[PIN]}, {HEAD} {levels(after)[HEAD]}")

# Per-FIXTURE targeting silently did nothing before: `_targets` matched tags
# only, so a fixture name selected an empty set and the layer was a no-op --
# which also meant every per-fixture colour in the UI did nothing.
client.send({"type": "level", "target": HEAD, "value": 0.1})
after = client.wait_for(lambda s: levels(s)[HEAD] < 0.2)
others = [v for k, v in levels(after).items()
          if k != HEAD and k in {f["name"] for f in after["fixtures"] if f["is_mover"]}]
check("a single FIXTURE can be dimmed, leaving its neighbours alone",
      levels(after)[HEAD] < 0.2 and all(v > 0.9 for v in others),
      f"{HEAD} {levels(after)[HEAD]}, others {others}")

client.send({"type": "color", "target": PIN, "color": [1.0, 0.0, 1.0]})
after = client.wait_for(lambda s: any(
    f["name"] == PIN and f["color"] == [1.0, 0.0, 1.0] for f in s["fixtures"]))
pins = [f for f in after["fixtures"] if not f["is_mover"]]
check("and a single fixture can be coloured -- the same fix",
      pins[0]["color"] != pins[1]["color"],
      f"{pins[0]['name']} {pins[0]['color']}, {pins[1]['name']} {pins[1]['color']}")
client.send({"type": "color", "target": PIN, "clear": True})

# The trim is a MULTIPLIER over the pattern, not a replacement for it. Checked
# in-process rather than over the socket: a level chase runs over 8 BARS, so
# sampling the broadcast would need a fifteen-second test to see two steps, and
# what actually matters here is the arithmetic, not the timing.
client.send({"type": "select_look", "name": LEVEL_A})
client.wait_for(lambda s: LEVEL_A in loaded(s, "level"))
time.sleep(0.2)


def head_level_at(bar: float) -> float:
    # On a private copy of the context: the engine's own frame thread is
    # running, and sets the shared context's phase every frame -- landing
    # between set_phase() and evaluate(), it put every sample on the live bar
    # (seen on Windows/3.10 as "trimmed [0.1]"). The safety taper's memory is
    # copied too, as evaluate() writes it.
    ctx = copy.copy(controller.ctx)
    ctx._taper_prev = dict(controller.ctx._taper_prev)
    ctx.set_phase(bar)
    show = controller.director._show
    controller._attach_overrides(show)
    states = statemod.evaluate(ctx, show)
    fid = next(f.fid for f in controller.rig.fixtures if f.name == HEAD)
    return states[fid].intensity


BARS = (1.0, 3.0, 5.0, 7.0)
with_trim = {round(head_level_at(b), 4) for b in BARS}
controller.apply({"type": "level", "target": HEAD, "clear": True}, None)
without = {round(head_level_at(b), 4) for b in BARS}
check("a level PATTERN still varies under a hand trim", len(with_trim) > 1,
      f"trimmed {sorted(with_trim)}")
check("and the trim multiplies it rather than replacing it",
      all(any(abs(t - w * 0.1) < 1e-6 for w in without) for t in with_trim),
      f"trimmed {sorted(with_trim)} == 0.1 x {sorted(without)}")
controller.apply({"type": "level", "target": HEAD, "value": 0.1}, None)

client.send({"type": "level", "target": HEAD, "clear": True})
client.send({"type": "level", "target": "pinspots", "clear": True})
after = client.wait_for(lambda s: not s["level_overrides"])
check("clearing a trim hands the fixture back to the pattern",
      not after["level_overrides"], f"{after['level_overrides']}")
# Waited for: section 2c below swaps in a stand-in timeline, and this
# operator choice landing after that grabbed its level lane -- one run in a
# dozen under load failed "an expiring hold advances without grabbing".
client.send({"type": "clear_slot", "slot": "level", "id": "level-cleared"})
_deadline = time.time() + 6.0
while True:
    _msg = client.recv()
    if _msg.get("type") == "reply" and _msg.get("id") == "level-cleared":
        break
    if time.time() > _deadline:
        raise AssertionError("clear_slot was never applied")

# A cue with a hold is supposed to advance on its own -- that is the entire
# meaning of "hold", and the Show tab tells the operator "auto after N" on the
# strength of it. Swapped in here rather than authored into despacio's own
# cues.json, which runs the real show and deliberately holds nothing (every
# cue there waits for GO by design).
print("\n2c. a cue's hold actually fires (F19 -- CueList.due() was dead code)")
from engine import cues as cuesmod

real_cues, real_beat = controller.cues, controller.ctx.beat
try:
    controller.cues = cuesmod.CueList(name="due-test", cues=[
        cuesmod.Cue(name="one", fade=0.0, hold=0.0),
        cuesmod.Cue(name="two", fade=0.0, hold=4.0),
        cuesmod.Cue(name="three", fade=0.0, hold=0.0),
    ])
    controller.ctx.beat = 0.0
    controller.cues.go(controller.ctx.beat)             # manual GO onto "one"
    controller.cues.go(controller.ctx.beat)             # manual GO onto "two"
    controller._drain()
    check("not due yet: index unchanged", controller.cues.index == 1,
          controller.cues.index)
    controller.ctx.beat = 5.0                            # 5 beats into a 4-beat hold
    controller._drain()
    check("due: advanced onto the next cue on its own",
          controller.cues.index == 2 and controller.cues.current.name == "three",
          f"index={controller.cues.index}")
    controller._drain()
    check("holding at the end: no cue after the last one to advance to",
          controller.cues.index == 2)

    # A hold running out is nobody's decision, so it must not take lanes from
    # a running timeline. Through `take_cue` it grabbed all three, and a grab
    # lasts until the operator releases it -- Follow DJ armed, a timeline
    # driving, and an expiring hold would have taken the whole stage with no
    # one touching anything. A stand-in timeline, engaged, records any grab.
    class EngagedTimeline:
        engaged, preview = True, None

        def __init__(self):
            self.grabbed = set()

        def grab(self, slots):
            self.grabbed |= set(slots)

    timeline = controller.player = EngagedTimeline()
    controller.cues.reset()
    controller.ctx.beat = 0.0
    controller.cues.go(controller.ctx.beat)              # onto "one"
    controller.cues.go(controller.ctx.beat)              # onto "two", 4-beat hold
    controller.ctx.beat = 5.0
    controller._drain()
    check("an expiring hold advances without grabbing from the timeline",
          controller.cues.index == 2 and not timeline.grabbed,
          f"index={controller.cues.index} grabbed={sorted(timeline.grabbed)}")
    controller.apply({"type": "cue_back"}, None)
    check("while an operator's own GO still grabs every lane",
          timeline.grabbed == set(statemod.SLOTS), sorted(timeline.grabbed))
finally:
    controller.player = None
    controller.cues, controller.ctx.beat = real_cues, real_beat


# -- safety is visible, and jog says it is bypassed ----------------------------
print("\n4. safety and jog")
state = client.wait_for(lambda s: any("safety" in f for f in s["fixtures"]))
movers = [f for f in state["fixtures"] if f.get("is_mover")]
check("every mover reports a taper and a reason",
      all("safety" in f and f["safety"]["reason"] for f in movers),
      f"{movers[0]['safety']}")
check("movers report where the beam lands",
      all("lands_on" in f for f in movers),
      f"{[f.get('lands_on') for f in movers]}")

client.send({"type": "jog", "fixture": "Moving Head #1", "pan": 47, "tilt": 69})
after = client.wait_for(lambda s: any(f.get("jogging") for f in s["fixtures"]))
check("jog flags the fixture", any(f.get("jogging") for f in after["fixtures"]))
check("and warns the taper is bypassed",
      any("bypassed" in n for n in after["notices"]), f"{after['notices'][-1:]}")

client.send({"type": "jog_clear", "fixture": "Moving Head #1"})
after = client.wait_for(lambda s: not any(f.get("jogging") for f in s["fixtures"]))
check("jog can be cleared", True)


# -- panic --------------------------------------------------------------------
print("\n5. panic")
client.send({"type": "panic"})
after = client.wait_for(lambda s: s["panicked"])
check("panic is reported", after["panicked"])
check("frames keep flowing while panicked",
      after["stats"]["frames"] > state["stats"]["frames"],
      "a blackout that stops sending is not a blackout")
client.send({"type": "clear_panic"})
after = client.wait_for(lambda s: not s["panicked"])
check("panic clears", not after["panicked"])


# -- a bad command must not take the show down --------------------------------
#
# Surviving is two things: no command reached a frame as an exception, and the
# clock kept going. It is NOT "no dropped frames". `drops` counts frames that
# overran a whole period, since the clock started, and on a shared CI runner
# that is the machine -- a vCPU descheduled for 75 ms -- not anything a command
# did. Asserting it here made this check flake on py3.10 runners ("errors 0,
# drops 1"), while repeated local py3.10 runs pinned to two cores never saw an
# eval error. Drop-freedom is spike/timing/soak.py's to prove, on a machine
# where it means something.
print("\n6. bad input")
before_frames = after["stats"]["frames"]
client.send({"type": "no_such_command"})
client.send({"type": "select_look", "name": "does not exist"})
client.send({"type": "master"})                       # missing value
after = client.wait_for(lambda s: s["stats"]["frames"] > before_frames + 20)
stats = after["stats"]
raised = (after["last_error"] or "").strip().splitlines()[-1:]
check("the show survives unknown and malformed commands",
      stats["eval_errors"] == 0 and stats["frames"] > before_frames + 20,
      f"errors {stats['eval_errors']}{': ' + raised[0] if raised else ''}, "
      f"{stats['frames'] - before_frames} frames since, "
      f"drops {stats['drops']} (the runner's, not asserted)")
check("and each failure is reported",
      sum(1 for n in after["notices"] if "failed" in n) >= 2,
      f"{[n for n in after['notices'] if 'failed' in n][:3]}")

# Raw junk that is not even JSON must not kill the connection.
client.sock.sendall(bytes([0x81, 0x83]) + b"\x00\x00\x00\x00" + b"abc")
after = client.wait_for(lambda s: s["stats"]["frames"] > after["stats"]["frames"] + 5)
check("non-JSON text is ignored, connection survives", True)

# Valid JSON that is not an object used to reach `.get` and end the connection.
client.sock.sendall(bytes([0x81, 0x86]) + b"\x00\x00\x00\x00" + b"[1, 2]")
client.send({"type": "master", "value": 0.8})
after = client.wait_for(lambda s: abs(s["master"] - 0.8) < 1e-9)
check("a JSON array is ignored too, and the connection still takes commands",
      True)


# -- multi-user ---------------------------------------------------------------
print("\n7. two clients, no locking")
second = Client(port)
second.recv()                                          # welcome
second.send({"type": "hello", "name": "tablet"})
after = client.wait_for(lambda s: len(s["presence"]) == 2)
check("both clients appear in presence",
      sorted(p["name"] for p in after["presence"]) == ["phone", "tablet"],
      f"{[p['name'] for p in after['presence']]}")

second.send({"type": "select_look", "name": LOOK_A})
after = client.wait_for(lambda s: s["auto"]["look"] == LOOK_A)
check("either client can drive the show", after["auto"]["look"] == LOOK_A)
who = [p for p in after["presence"] if p["name"] == "tablet"][0]
check("and the UI can see who did it", LOOK_A in (who["last_action"] or ""),
      who["last_action"])

second.close()
after = client.wait_for(lambda s: len(s["presence"]) == 1, timeout=8)
check("a disconnect is noticed", len(after["presence"]) == 1)


# -- static file serving ------------------------------------------------------
print("\n8. HTTP")
import urllib.request
with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
    body = resp.read().decode()
# Either the built UI or the "here is how to build it" placeholder, depending on
# whether ui/dist exists. Both are correct; asserting on only one made this test
# fail the moment the bundle was first built, which is a test tracking an
# implementation detail rather than the behaviour.
served_ui = '<div id="root">' in body
served_placeholder = "Engine is running" in body
check("GET / serves the UI, or explains how to build it",
      resp.status == 200 and (served_ui or served_placeholder),
      f"{resp.status}, {len(body)} bytes, "
      f"{'bundle' if served_ui else 'placeholder'}")

# A single-page app owns its own routing, so an unknown path must return the
# app rather than a 404 -- otherwise a phone reloading on /#setup gets nothing.
with urllib.request.urlopen(f"http://127.0.0.1:{port}/some/client/route", timeout=5) as resp:
    spa = resp.read().decode()
check("an unknown path falls through to the app",
      resp.status == 200 and ('<div id="root">' in spa or "Engine is running" in spa),
      f"{resp.status}, {len(spa)} bytes")

try:
    urllib.request.urlopen(f"http://127.0.0.1:{port}/../../../etc/passwd", timeout=5)
    traversal_blocked = True     # urllib normalises it away; the check below is the real one
except Exception:
    traversal_blocked = True
check("path traversal cannot escape the bundle directory", traversal_blocked)

# Caching, and the two halves are opposites on purpose. Found the hard way: with
# no headers at all a browser applies its own heuristic to index.html, so a
# phone that had the console open before an engine update keeps asking for an
# asset the rebuild deleted -- and gets index.html back as JavaScript, which is
# a white screen. `version` in the snapshot detects that; these headers are what
# stop it happening.
with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
    index_cache = resp.headers.get("Cache-Control", "")
check("index.html is revalidated every load", "no-cache" in index_cache,
      f"{index_cache!r}")

asset = next(iter((REPO / "ui" / "dist" / "assets").glob("*.js")), None)
if asset is None:
    check("a hashed asset is cached hard", False, "no bundle built")
else:
    with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/assets/{asset.name}", timeout=5) as resp:
        asset_cache = resp.headers.get("Cache-Control", "")
    # Safe precisely because the name is a content hash: that file can never
    # mean something different.
    check("a hashed asset is cached hard", "immutable" in asset_cache,
          f"{asset_cache!r}")

try:
    urllib.request.urlopen(
        f"http://127.0.0.1:{port}/assets/index-GONE12345.js", timeout=5)
    check("a missing asset is a 404, not the app", False, "it served something")
except urllib.error.HTTPError as exc:
    # Serving index.html here hands the browser HTML where it asked for
    # JavaScript. It refuses to execute it, and the operator gets a blank
    # console and an obscure error instead of an obvious one.
    check("a missing asset is a 404, not the app", exc.code == 404,
          f"{exc.code} {exc.reason}")


# -- the previz app's reads ---------------------------------------------------
print("\n8b. the standalone previz's scene and models")
from dataclasses import replace as dc_replace  # noqa: E402
from engine import scene as scenemod  # noqa: E402

with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/previz/scene", timeout=5) as resp:
    scene_body = resp.read()
    etag = resp.headers.get("ETag", "")
manifest = json.loads(scene_body)
check("GET /api/previz/scene is the manifest",
      resp.status == 200 and manifest["format"] == scenemod.FORMAT
      and manifest["event"] == "despacio", f"{resp.status} {manifest.get('format')}")
check("it describes the rig this engine is driving",
      len(manifest["fixtures"]) == sum(1 for f in controller.rig.fixtures if f.position))
check("its ETag is the scene's revision", etag == f'"{manifest["rev"]}"', etag)

request = urllib.request.Request(f"http://127.0.0.1:{port}/api/previz/scene",
                                 headers={"If-None-Match": etag})
try:
    urllib.request.urlopen(request, timeout=5)
    check("an unchanged scene is a 304", False, "it sent the body again")
except urllib.error.HTTPError as exc:
    # The app polls once a second; this is what makes that free.
    check("an unchanged scene is a 304", exc.code == 304, f"{exc.code}")

# A live edit is what the poll exists to notice. `previz` is the one venue
# field nothing in the show reads, so changing it here disturbs nothing else.
live_venue = controller.rig.venue
controller.rig.venue = dc_replace(live_venue, previz={"optics": {"fog_density": 0.9}})
try:
    with urllib.request.urlopen(request, timeout=5) as resp:
        edited = json.loads(resp.read())
    check("a live venue edit changes the scene", edited["optics"]["fog_density"] == 0.9
          and edited["rev"] != manifest["rev"])
except urllib.error.HTTPError as exc:
    check("a live venue edit changes the scene", False, f"{exc.code}")
finally:
    controller.rig.venue = live_venue

# Models are served by hash, and only the ones the current scene names. The
# despacio room has none, so this uses the test sample event's scene.
sample = scenemod.build_for(REPO / "engine" / "tests" / "data" / "events" / "sample")
real_scene = server.previz_scene
server.previz_scene = lambda: sample
try:
    sha, path = next(iter(sample.files.items()))
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/previz/model/{sha}.glb",
                                timeout=5) as resp:
        model_bytes = resp.read()
        model_cache = resp.headers.get("Cache-Control", "")
    check("a named model is served, byte for byte", model_bytes == path.read_bytes()
          and resp.headers.get("Content-Type") == "model/gltf-binary")
    check("...and cached hard, since its name is its hash", "immutable" in model_cache)
    for bad in ("0" * 64 + ".glb", "../../rig.json", f"{sha}.gltf", sha):
        try:
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/previz/model/{bad}", timeout=5)
            check(f"model route refuses {bad[:20]}", False, "it served something")
        except urllib.error.HTTPError as exc:
            check(f"model route refuses {bad[:20]}", exc.code == 404, f"{exc.code}")

    def broken():
        raise ValueError("simulated")
    server.previz_scene = broken
    try:
        urllib.request.urlopen(f"http://127.0.0.1:{port}/api/previz/scene", timeout=5)
        check("a scene that cannot be built is a 503", False)
    except urllib.error.HTTPError as exc:
        check("a scene that cannot be built is a 503", exc.code == 503, f"{exc.code}")
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
        check("...and the console is still served", resp.status == 200)
finally:
    server.previz_scene = real_scene


# -- venue and taper editing --------------------------------------------------
print("\n9. venue and taper, edited live")
client.send({"type": "taper", "crowd_level": 0.25})
after = client.wait_for(lambda s: abs(s["taper"]["crowd_level"] - 0.25) < 1e-9)
check("taper policy applies", abs(after["taper"]["crowd_level"] - 0.25) < 1e-9)

client.send({"type": "venue", "crowd": {"head_band_min": 1600}})
after = client.wait_for(lambda s: s["venue"]["crowd"]["head_band_min"] == 1600)
check("crowd zone edits apply", after["venue"]["crowd"]["head_band_min"] == 1600)
check("and the rest of the zone is untouched",
      after["venue"]["crowd"]["head_band_max"] == 2000,
      f"{after['venue']['crowd']}")

# The band must take effect on the NEXT FRAME, not on a restart -- being able to
# stand in the room and watch the beams respond is the entire point of putting
# this on a phone.
client.send({"type": "taper", "crowd_level": 0.0})
client.send({"type": "venue", "crowd": {"min_x": 0, "max_x": 9144,
                                        "min_z": 0, "max_z": 9144,
                                        "head_band_min": 100,
                                        "head_band_max": 4500}})
after = client.wait_for(
    lambda s: any(f.get("safety", {}).get("taper") == 0.0
                  for f in s["fixtures"] if f.get("is_mover")), timeout=8)
check("a wider crowd zone dims beams on the next frame",
      any(f.get("safety", {}).get("taper") == 0.0
          for f in after["fixtures"] if f.get("is_mover")),
      "beams now cross the enlarged band")

client.send({"type": "venue", "crowd": {"head_band_min": 3000, "head_band_max": 1000}})
after = client.wait_for(lambda s: any("failed" in n and "head band" in n
                                      for n in s["notices"]))
check("an inverted head band is rejected, not stored",
      after["venue"]["crowd"]["head_band_min"] == 100,
      f"{after['venue']['crowd']}")

client.send({"type": "taper", "enabled": False})
after = client.wait_for(lambda s: not s["taper"]["enabled"])
check("disabling the taper is announced in capitals",
      any("SAFETY TAPER DISABLED" in n for n in after["notices"]),
      f"{[n for n in after['notices'] if 'TAPER' in n]}")

# Saving must keep the file's explanatory comments -- they carry the reasoning
# for every number in it, and rewriting from the dataclass would discard them.
# Resolved, not assumed: the room may live in shared/venues/ and be shared with
# another show, so this test edits whatever the engine actually loaded (the
# temp copy of the library, see the top of this file) -- and restores it in the
# `finally` below, so later sections still see the room as committed.
venue_path = rigmod.venue_path(EVENT, json.loads(
    (EVENT / "rig.json").read_text(encoding="utf-8")))
original = venue_path.read_text(encoding="utf-8")
try:
    client.send({"type": "taper", "enabled": True, "crowd_level": 0.4})
    client.send({"type": "venue", "crowd": {"head_band_min": 1450,
                                            "head_band_max": 2050}})
    client.send({"type": "venue_save"})
    # The notice names the file actually written, which is the shared room when
    # the event uses one -- the operator needs to know a save reaches beyond
    # tonight's event folder.
    client.wait_for(lambda s: any(f"saved {venue_path.name}" in n
                                  for n in s["notices"]))

    saved = json.loads(venue_path.read_text(encoding="utf-8"))
    check("saved values round-trip",
          saved["crowd_zone"]["head_band_min"] == 1450
          and abs(saved["taper"]["crowd_level"] - 0.4) < 1e-9,
          f"{saved['crowd_zone']['head_band_min']}, {saved['taper']}")
    check("the file's explanatory comments survive",
          "_crowd_zone_comment" in saved and "_ball_radius_comment" in saved,
          f"{[k for k in saved if k.startswith('_')]}")
    check("untouched keys survive too",
          saved["ball"]["y"] == 2743 and saved["apex_height"] == 4600)

    # ...and a fresh engine picks the saved policy back up, or saving is theatre.
    reloaded = ShowController(EVENT)
    check("a restart honours the saved taper policy",
          abs(reloaded.ctx.taper.crowd_level - 0.4) < 1e-9,
          f"{reloaded.ctx.taper.crowd_level}")
finally:
    venue_path.write_text(original, encoding="utf-8")


# -- 10. calibration cannot be fed, or write, a reading nobody took -----------
print("\n10. captures the operator never actually took")
head_name = next(f.name for f in controller.rig.fixtures if f.is_mover)
ball = list(controller.rig.venue.ball)
controller.apply({"type": "capture_clear"}, None)
controller.apply({"type": "jog_clear"}, None)

try:
    controller.apply({"type": "capture", "fixture": head_name,
                      "target": ball, "label": "mirror ball"}, None)
    check("a capture with no jog is refused", False, "it was accepted")
except ValueError as exc:
    check("a capture with no jog is refused", "not jogging" in str(exc), str(exc)[:70])
check("and nothing was recorded", not controller.captures.get(head_name),
      f"{len(controller.captures.get(head_name, []))} stored")

# The reachable path was two good captures plus one taken before jogging: the
# solver fits all three, reports a large residual, and --write took it anyway.
controller.apply({"type": "jog", "fixture": head_name, "pan": 133, "tilt": 68}, None)
controller.apply({"type": "capture", "fixture": head_name, "target": ball,
                  "label": "mirror ball"}, None)
controller.apply({"type": "capture", "fixture": head_name, "pan": 0, "tilt": 0,
                  "target": [500.0, 0.0, 8644.0], "label": "bogus"}, None)
cal_before = (EVENT / "calibration.json").read_text(encoding="utf-8")
try:
    controller.apply({"type": "solve", "write": True}, None)
    check("a badly-fitting solve is not written", False, "it wrote")
except ValueError as exc:
    check("a badly-fitting solve is not written", "refusing to write" in str(exc),
          str(exc)[:90])
check("the stored calibration is untouched",
      (EVENT / "calibration.json").read_text(encoding="utf-8")
      == cal_before)
controller.apply({"type": "capture_clear"}, None)
controller.apply({"type": "jog_clear"}, None)


# -- 10b. a drift check reads the jog, and refuses a head nobody aimed --------
# The console's Check button sends no readings: it holds one pan/tilt pair for
# the selected head, not one per head, so the engine reads its own jog dict.
print("\n10b. drift check from the jog")
heads = controller.rig.geometry.heads
stored = {h.name: h.calibrated_ball_dmx for h in heads}
controller.last_drift = None
for h in heads[:-1]:
    controller.apply({"type": "jog", "fixture": h.name,
                      "pan": stored[h.name][0], "tilt": stored[h.name][1]}, None)
try:
    controller.apply({"type": "drift"}, None)
    check("a drift check with a head not jogging is refused", False, "it ran")
except ValueError as exc:
    check("a drift check with a head not jogging is refused, naming only it",
          "not jogging" in str(exc) and heads[-1].name in str(exc)
          and heads[0].name not in str(exc), str(exc)[:90])
check("and publishes no result", controller.snapshot()["drift"] is None)

last = heads[-1]
controller.apply({"type": "jog", "fixture": last.name,
                  "pan": stored[last.name][0] + 20,
                  "tilt": stored[last.name][1]}, None)
controller.apply({"type": "drift"}, None)
rows = controller.snapshot()["drift"]
check("one row per head, in rig order",
      [r["head"] for r in rows] == [h.name for h in heads], f"{rows}")
check("heads jogged onto their stored reading are ok",
      not any(r["significant"] for r in rows[:-1]), f"{rows[:-1]}")
check("the head jogged 20 DMX off it is MOVED -- the check read the jog",
      rows[-1]["significant"], f"{rows[-1]}")

# The CLI's shape, which used to drop the heads past the end of a short list
# and report a go for them without a reading.
try:
    controller.apply({"type": "drift",
                      "readings": [list(stored[h.name]) for h in heads[:-1]]}, None)
    check("a short readings list is refused", False, "it ran")
except ValueError as exc:
    check("a short readings list is refused",
          f"expected {len(heads)} readings" in str(exc), str(exc)[:70])
controller.apply({"type": "drift",
                  "readings": [list(stored[h.name]) for h in heads]}, None)
check("a full readings list still checks, regardless of the jog",
      not any(r["significant"] for r in controller.last_drift),
      f"{controller.last_drift}")
controller.apply({"type": "jog_clear"}, None)
controller.last_drift = None


# -- 11. a tap is timed when it ARRIVES, not at the next frame ----------------
print("\n11. tap timestamps")
controller.commands.queue.clear()
controller.submit({"type": "tap"}, None)
_msg, _client, at = controller.commands.get_nowait()
check("submit stamps the command with engine time, off the frame grid",
      at != controller.ctx.time or controller.runner._begin is None,
      f"arrival {at:.4f} vs last frame {controller.ctx.time:.4f}")


# -- flash is momentary, and outranks a trim ----------------------------------
print("\n11b. flash")


def frame_now():
    """Evaluate as the frame loop would.

    `_attach_overrides` is the runner's on_show hook, so a command applied
    directly does not reach the Show until a frame runs. Skipping this made the
    first version of these checks report the same number three times, which
    looked like a broken flash and was a broken harness.
    """
    controller.runner.sync_clock()
    return statemod.evaluate(controller.ctx, controller.runner.show)


controller.apply({"type": "level", "target": "corner movers", "value": 0.0}, None)
dark = frame_now()
movers = [f for f in controller.rig.fixtures if "corner movers" in f.tags]
check("a group trimmed to zero is dark",
      all(dark[f.fid].intensity < 1e-9 for f in movers),
      f"{[round(dark[f.fid].intensity, 3) for f in movers]}")

controller.apply({"type": "flash", "target": "corner movers"}, None)
lit = frame_now()
# The case flash exists for: bumping something you have pulled down. A
# multiplier could never do this, which is why it sets rather than multiplies.
# Full on, as limited by the master and the taper -- which for these heads is
# 0.9 * 0.5, since they are aimed over the crowd. Asserting a bare threshold
# instead would be asserting where the taper happens to be today.
check("flash bumps it anyway, from a trim of zero",
      all(abs(lit[f.fid].intensity
              - controller.master * lit[f.fid].safety.taper) < 1e-9
          for f in movers),
      f"{[round(lit[f.fid].intensity, 3) for f in movers]} "
      f"(master {controller.master} x taper "
      f"{lit[movers[0].fid].safety.taper})")
check("and the taper still applies after it",
      all(lit[f.fid].intensity <= (lit[f.fid].safety.taper + 1e-9)
          for f in movers if lit[f.fid].safety))

controller.apply({"type": "flash", "target": "corner movers", "on": False}, None)
released = frame_now()
check("releasing it goes back to the trim",
      all(released[f.fid].intensity < 1e-9 for f in movers),
      f"{[round(released[f.fid].intensity, 3) for f in movers]}")

# A pointer leaving the button while down never sends the release, and a flash
# stuck on is a group stuck at full.
controller.apply({"type": "flash", "target": "corner movers"}, None)
controller.apply({"type": "flash_clear"}, None)
check("flash_clear releases everything", controller.flashing == set(),
      f"{controller.flashing}")
controller.apply({"type": "level", "target": "corner movers", "clear": True}, None)


# -- 11c. per-slot rate, end to end -------------------------------------------
#
# The three slots were independent everywhere except in time: one shared motion
# phase meant a colour chase and a move could not run at different speeds, which
# is a large part of why the old library needed a stored chase per combination.
print("\n11c. per-slot rate")
phases = controller.director.phases
phases.reset()
controller.apply({"type": "rate", "slot": "color", "value": 0.25}, None)
check("the command reaches the phases", phases.rate["color"] == 0.25,
      f"{phases.rate}")

# Real frames from the running output thread, not hand-called sync_clock: the
# phases advance on the musical delta between frames, and calling sync_clock in
# a loop at one instant is forty frames of zero elapsed time.
start = dict(phases.bars)
time.sleep(0.6)
after = {slot: phases.bars[slot] - start[slot] for slot in start}
check("colour falls behind movement while the show runs",
      after["movement"] > 0 and after["color"] < after["movement"] / 2,
      f"movement {after['movement']:.3f} vs colour {after['color']:.3f} bars")
check("and the context the layers read agrees with it",
      (controller.ctx.motion_bar, controller.ctx.color_bar,
       controller.ctx.level_bar)
      == (phases.bars["movement"], phases.bars["color"], phases.bars["level"]))
check("and a frame still renders", len(controller.runner.render_once()) > 0)

# A stored rate is part of the picture, like speed and master already were.
client.send({"type": "preset_save", "name": "rated"})
after_save = client.wait_for(
    lambda s: any(p["name"] == "rated" for p in s["presets"]))
saved = next(p for p in after_save["presets"] if p["name"] == "rated")
check("a preset saves the slot rates it was built at",
      saved.get("rates", {}).get("color") == 0.25, f"{saved.get('rates')}")

controller.apply({"type": "rate", "reset": True}, None)
check("reset puts them back", not phases.changed, f"{phases.rate}")
client.send({"type": "preset_apply", "name": "rated"})
client.wait_for(lambda s: s["auto"]["slot_rates"]["color"] == 0.25)
check("and applying the preset restores them", phases.rate["color"] == 0.25)

# Absent has to mean "leave alone". A preset that always wrote 1x would
# silently undo a rate set after it was saved, which is the same trap the cue
# macros document.
controller.apply({"type": "rate", "reset": True}, None)
controller.apply({"type": "rate", "slot": "level", "value": 2.0}, None)
client.send({"type": "preset_save", "name": "unrated"})
client.wait_for(lambda s: any(p["name"] == "unrated" for p in s["presets"]))
controller.apply({"type": "rate", "reset": True}, None)
client.send({"type": "preset_save", "name": "unrated"})     # re-record at 1x
client.wait_for(lambda s: not any(
    "rates" in p for p in s["presets"] if p["name"] == "unrated"))
controller.apply({"type": "rate", "slot": "movement", "value": 3.0}, None)
controller.apply({"type": "preset_apply", "name": "unrated"}, None)
check("a preset saved with nothing dialled in leaves rates alone",
      phases.rate["movement"] == 3.0, f"{phases.rate}")

try:
    controller.apply({"type": "rate", "slot": "color", "value": -2}, None)
    check("a bad rate is refused at the command", False, "it was accepted")
except ValueError as exc:
    check("a bad rate is refused at the command", True, str(exc)[:60])

phases.reset()
for name in ("rated", "unrated"):
    controller.apply({"type": "preset_delete", "name": name}, None)


# -- 11d. replies, and work done off the output thread -------------------------
#
# F19a. A designer that saves a timeline needs to know whether THAT save worked,
# not scan a shared notices list for a line that might be someone else's. And
# anything slow must run on the worker, with its result installed on the output
# thread -- checked by thread name, because a version that quietly ran the work
# inline would pass every "did it happen" check and break the DMX clock.
print("\n11d. replies, submit_call and the worker")


def wait_reply(c, rid, timeout=6.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        msg = c.recv()
        if msg.get("type") == "reply" and msg.get("id") == rid:
            return msg
    raise AssertionError(f"no reply {rid!r}")


def replies_within(c, seconds):
    got = []
    deadline = time.time() + seconds
    while time.time() < deadline:
        msg = c.recv()
        if msg.get("type") == "reply":
            got.append(msg)
    return got


other = Client(port, "other")
other.recv()                                             # welcome
other.recv()                                             # first state

client.send({"type": "master", "value": 0.5, "id": "m1"})
reply = wait_reply(client, "m1")
check("a command with an id gets a reply", reply["ok"] is True
      and "error" not in reply, f"{reply}")
check("and it took effect", abs(controller.master - 0.5) < 1e-9)
check("the reply went to the sender only, not to everyone",
      replies_within(other, 0.5) == [])

seen_before = sum("no_such_command failed" in n for n in controller.notices)
client.send({"type": "no_such_command", "id": 7})
reply = wait_reply(client, 7)
check("a failure comes back in the reply, with the reason",
      reply["ok"] is False and "unknown command" in reply.get("error", ""),
      f"{reply}")
client.wait_for(lambda s: True)
check("and it does not ALSO land in everyone's notices",
      sum("no_such_command failed" in n for n in controller.notices) <= seen_before,
      f"{controller.notices[-2:]}")

client.send({"type": "master", "value": 0.6, "id": {"not": "an id"}})
client.wait_for(lambda s: abs(s["master"] - 0.6) < 1e-9)
check("an id that is not a short string or an integer means no reply, "
      "and the command still runs", replies_within(client, 0.4) == [])
client.send({"type": "master", "value": 0.7, "id": "x" * 65})
client.wait_for(lambda s: abs(s["master"] - 0.7) < 1e-9)
check("nor does an id too long to echo back", replies_within(client, 0.4) == [])

where: dict = {}
ran = threading.Event()
controller.submit_call(lambda: (where.__setitem__("call", threading.current_thread().name),
                                ran.set()))
ran.wait(2.0)
check("submit_call runs on the output thread, at a frame boundary",
      where.get("call") == "dmx-output", f"{where}")

finished = threading.Event()
controller.worker.submit(
    lambda: threading.current_thread().name,
    lambda job_thread: (where.update(job=job_thread,
                                     done=threading.current_thread().name),
                        finished.set()))
finished.wait(3.0)
check("worker jobs run on the worker",
      where.get("job") == "klights-worker", f"{where}")
check("and their result is installed on the output thread",
      where.get("done") == "dmx-output", f"{where}")


def parse_show():
    raise ValueError("timelines/x.json line 3: expected ','")


controller.worker.submit(parse_show, label="load timeline")
after = client.wait_for(lambda s: any("load timeline failed" in n
                                      for n in s["notices"]))
check("a failed worker job becomes a notice, not a dead thread",
      controller.worker.running, f"{after['notices'][-1:]}")
controller.submit_call(lambda: 1 / 0)
after = client.wait_for(lambda s: any("engine task failed" in n
                                      for n in s["notices"]))
check("and so does a posted call that raises -- the show keeps running",
      after["stats"]["eval_errors"] == 0, f"{after['notices'][-1:]}")

# The WebSocket route into the clock used to skip the checks the UDP port
# makes: a bpm of 900 went straight to the clock.
bpm_before = controller.clock.bpm
client.send({"type": "sync", "bpm": 900, "id": "s1"})
reply = wait_reply(client, "s1")
check("a sync over the WebSocket gets the UDP port's range checks",
      reply["ok"] is False and abs(controller.clock.bpm - bpm_before) < 1e-9,
      f"{reply}, bpm {controller.clock.bpm}")
client.send({"type": "sync", "bpm": 126, "track": "x" * 1000,
             "phrase_measured": "false", "id": "s2"})
reply = wait_reply(client, "s2")
check("the usable fields of a sync are applied",
      reply["ok"] is True and abs(controller.clock.bpm - 126) < 1e-6,
      f"{reply}, bpm {controller.clock.bpm}")
check("a 1000-character title is cut to the port's 64",
      len(controller.sync_track or "") == 64, f"{len(controller.sync_track or '')}")
check("and a quoted false is false", controller.clock.phrase_measured is False)
client.send({"type": "sync_off", "id": "s3"})
wait_reply(client, "s3")

# A handler whose answer JSON cannot carry must not stop the frame it was sent
# from -- the reply is built on the output thread.
controller._cmd_test_unserialisable = lambda m, now: {"when": object()}
client.send({"type": "test_unserialisable", "id": "u1"})
reply = wait_reply(client, "u1")
check("a reply whose data cannot be sent says so, and the show runs on",
      reply["ok"] is True and "not serialisable" in reply.get("error", ""),
      f"{reply}")
del controller._cmd_test_unserialisable
other.close()


client.close()
server.stop()
controller.stop()


# -- 12. access tiers ---------------------------------------------------------
#
# The rig became editable from the UI in F14, so "anyone on the venue wifi can
# do anything" stopped being an acceptable default. The threat is not an
# attacker; it is the guest who opens the URL you showed someone and starts
# pressing things between sets.
print("\n11d. tuning a routine's own parameters")
# The thing a ported look cannot have. A stored look is a table of DMX, so
# every variation of it had to be another stored entry -- which is how 103
# poses accumulated. A routine names a generator, so its numbers are knobs.


def orbit_offsets(ctrl):
    """Each head's aim minus its own calibrated ball aim, at a fixed phase.

    The offset rather than the aim: four heads in four corners have four
    different ball aims, so raw aims cannot show whether a route changed.
    """
    show = ctrl.director.rebuild()
    ctrl.ctx.set_phase(2.0)
    states = statemod.evaluate_stack(ctrl.ctx, show)
    out = []
    for fixture in ctrl.rig.fixtures:
        if fixture.head is None or states[fixture.fid].aim is None:
            continue
        base = ctrl.rig.geometry.aim_at_ball(fixture.head)
        aim = states[fixture.fid].aim
        out.append((round(aim.bearing_delta - base.bearing_delta, 2),
                    round(aim.elev_deg - base.elev_deg, 2)))
    return out


controller.apply({"type": "select_look", "name": "Ball Orbit"}, None)
authored = orbit_offsets(controller)
check("a generated routine is selectable like any other look",
      controller.setlist.current().name == "Ball Orbit")

controller.apply({"type": "look_params", "name": "Ball Orbit",
                  "values": {"radius": 30}}, None)
widened = orbit_offsets(controller)
check("turning a knob changes where the heads actually point",
      widened != authored, f"{authored[0]} -> {widened[0]}")
check("and it is the parameter that moved, not something else",
      abs(widened[0][0]) > abs(authored[0][0]),
      f"{authored[0][0]} -> {widened[0][0]}")

# Sparse, so a second edit does not discard the first and neither detaches the
# routine from the values it was authored with.
controller.apply({"type": "look_params", "name": "Ball Orbit",
                  "values": {"bars": 32}}, None)
check("a second parameter composes with the first",
      controller.look_params["Ball Orbit"] == {"radius": 30.0, "bars": 32.0},
      f"{controller.look_params['Ball Orbit']}")
check("the authored values are untouched underneath",
      controller.by_name["Ball Orbit"].args["radius"] == 10.0)
check("and the snapshot publishes the override separately from the authored",
      controller.snapshot()["look_params"]["Ball Orbit"]["radius"] == 30.0)

controller.apply({"type": "look_params", "name": "Ball Orbit", "reset": True},
                 None)
check("reset returns it to what parametric_looks.json authored",
      orbit_offsets(controller) == authored)
check("and drops the override entirely",
      "Ball Orbit" not in controller.snapshot()["look_params"])

# The failure paths. A typo'd key from the console means the UI and the engine
# disagree about what this routine has, and dropping it silently would give an
# operator a slider that appears to work and changes nothing.
for label, message, expect_in in (
    ("a typo'd parameter", {"type": "look_params", "name": "Ball Orbit",
                            "values": {"raduis": 5}}, "no parameter named"),
    ("a ported look, which has nothing to tune",
     {"type": "look_params", "name": "Ball Wave", "values": {"radius": 5}},
     "ported look"),
    ("an unknown look", {"type": "look_params", "name": "Nope",
                         "values": {"x": 1}}, "no look named"),
    ("no values and no reset", {"type": "look_params", "name": "Ball Orbit"},
     "needs values"),
    # `json.loads` accepts the literals NaN and Infinity, and min/max pass NaN
    # straight through a clamp. A NaN reaching a frame raised on its way to a
    # DMX integer, every frame after, until someone found the reset.
    ("NaN off the wire", json.loads('{"type": "look_params", "name": '
                                    '"Ball Orbit", "values": {"radius": NaN}}'),
     "finite"),
    ("Infinity off the wire", json.loads(
        '{"type": "look_params", "name": "Ball Orbit", '
        '"values": {"radius": Infinity}}'), "finite"),
    ("a NaN macro", json.loads('{"type": "macro", "size": NaN}'), "finite"),
    ("a NaN modulator phase", json.loads(
        '{"type": "modulate", "param": "size", "shape": "sine", "bars": 4, '
        '"phase": NaN}'), "finite"),
    ("an infinite modulator cycle", json.loads(
        '{"type": "modulate", "param": "size", "bars": Infinity}'), "finite"),
    ("a NaN vary amount", json.loads(
        '{"type": "vary", "name": "Ball Orbit", "amount": NaN}'), "finite"),
    # `Param` passes a colour string through -- whether it means anything is a
    # question for the rig -- and `blocks.make` answers a bad one with an empty
    # block. Accepted, it turned the duo plain palette white with no word said.
    ("a colour this rig cannot resolve",
     {"type": "look_params", "name": "Duo Pink/Cyan",
      "values": {"color_a": "nonsense"}}, "not a palette role"),
):
    try:
        controller.apply(message, None)
        check(f"{label} is refused", False, "accepted")
    except (ValueError, KeyError) as exc:
        check(f"{label} is refused", expect_in in str(exc), str(exc)[:70])

# Clamped, not refused: one bad number must not discard the good ones sent
# alongside it in the same message.
controller.apply({"type": "look_params", "name": "Ball Orbit",
                  "values": {"radius": 9999, "elongation": 2.0}}, None)
check("an out-of-range value is clamped rather than refusing the message",
      controller.look_params["Ball Orbit"] == {"radius": 90.0,
                                               "elongation": 2.0},
      f"{controller.look_params['Ball Orbit']}")
controller.apply({"type": "look_params", "name": "Ball Orbit", "reset": True},
                 None)
check("nothing refused above was stored, and every macro is still a number",
      "Duo Pink/Cyan" not in controller.look_params
      and "Ball Orbit" not in controller.look_params
      and not controller.modulators
      and all(math.isfinite(v) for v in (controller.ctx.move_size,
                                         controller.ctx.move_spread,
                                         *controller.ctx.move_center)),
      f"{controller.look_params} size={controller.ctx.move_size}")

# A preset or a cue is file-sourced, so a colour it carries that this rig cannot
# resolve is DROPPED on its own -- the good tuning beside it still lands, and
# the console is told which key went.
# (The newest notice, not a slice from a length taken before: the list is capped
# at twenty, so once it is full its length never moves.)
controller.apply_look_params(
    {"Duo Pink/Cyan": {"color_a": "nonsense", "bars": 8}},
    ["Duo Pink/Cyan"], "preset 'probe'")
check("a preset's unusable colour is dropped and its good tuning kept",
      controller.look_params.get("Duo Pink/Cyan") == {"bars": 8.0}
      and "Duo Pink/Cyan.color_a" in controller.notices[-1],
      f"{controller.look_params.get('Duo Pink/Cyan')} "
      f"{controller.notices[-1:]}")
controller.apply({"type": "look_params", "name": "Duo Pink/Cyan",
                  "reset": True}, None)

# Every change to the tuning REBINDS the dict, as every collection the 10 Hz
# snapshot thread walks must (see "the snapshot must never see a container
# mid-edit", above).
held_tuning, held_rack = controller.look_params, controller.modulators.by_key
controller.apply({"type": "look_params", "name": "Ball Orbit",
                  "values": {"radius": 20}}, None)
controller.apply({"type": "modulate", "param": "size", "bars": 4}, None)
check("tuning and the modulator rack are rebound, not edited in place",
      controller.look_params is not held_tuning and not held_tuning
      and controller.modulators.by_key is not held_rack and not held_rack)
controller.apply({"type": "modulate_clear", "all": True}, None)
controller.apply({"type": "macro", "reset": True}, None)
controller.apply({"type": "look_params", "name": "Ball Orbit", "reset": True},
                 None)

print("\n11d1. an RGBW white override, per target")
# A white channel is not on every fixture, so white rides beside the RGB
# override rather than inside it -- and a palette tap, which only knows RGB,
# must not forget a white the operator dialled in a moment ago.
held_whites = controller.white_overrides
controller.apply({"type": "color", "target": "movers", "color": [1, 0, 0],
                  "white": 0.6}, None)
check("a white override is stored, published, and rebound rather than edited",
      controller.white_overrides == {"movers": 0.6}
      and controller.snapshot()["white_overrides"] == {"movers": 0.6}
      and controller.white_overrides is not held_whites and not held_whites)
show = controller.director.rebuild()
controller._attach_overrides(show)
mover = next(f for f in controller.rig.fixtures if "movers" in f.tags)
check("and reaches the targeted fixtures' state",
      statemod.evaluate(controller.ctx, show)[mover.fid].white == 0.6)
controller.apply({"type": "color", "target": "movers", "color": [0, 1, 0]},
                 None)
check("a colour with no white leaves the white alone",
      controller.white_overrides == {"movers": 0.6})
controller.apply({"type": "color", "target": "movers", "color": [0, 1, 0],
                  "white": None}, None)
check("white: null clears just the white",
      controller.white_overrides == {} and "movers" in controller.color_overrides)
controller.apply({"type": "color", "target": "movers", "color": [0, 0, 0],
                  "white": 0.2}, None)
controller.apply({"type": "color", "target": "movers", "color": [0, 0, 0],
                  "clear": True}, None)
check("clearing the target clears both",
      controller.white_overrides == {}
      and "movers" not in controller.color_overrides)

print("\n11d2. a preset carries the tuning of the routines it names")
# A preset is "get back to this picture". A routine's radius IS the picture, so
# unlike `rates` -- a ride the operator keeps a hand on -- it is stored even when
# it sits at the authored value.
controller.apply({"type": "select_look", "name": "Ball Orbit"}, None)
controller.apply({"type": "look_params", "name": "Ball Orbit",
                  "values": {"radius": 28, "elongation": 2.5}}, None)
tuned = orbit_offsets(controller)
controller.apply({"type": "preset_save", "name": "Tuned Orbit"}, None)
saved = next(p for p in controller.presets if p["name"] == "Tuned Orbit")
check("the preset stored the routine's parameters",
      saved.get("params", {}).get("Ball Orbit")
      == {"radius": 28.0, "elongation": 2.5}, f"{saved.get('params')}")

# Dial it somewhere else, then recall.
controller.apply({"type": "look_params", "name": "Ball Orbit",
                  "values": {"radius": 5}}, None)
check("moving the knob afterwards really does change the picture",
      orbit_offsets(controller) != tuned)
controller.apply({"type": "preset_apply", "name": "Tuned Orbit"}, None)
check("applying the preset puts the tuning back",
      controller.look_params["Ball Orbit"]
      == {"radius": 28.0, "elongation": 2.5},
      f"{controller.look_params.get('Ball Orbit')}")
check("and the heads point where they did when it was saved",
      orbit_offsets(controller) == tuned)

# The case that decides whether this is trustworthy: a preset saved at the
# authored values must CLEAR tuning dialled in later, not leave it. Storing only
# non-default values would silently fail exactly here.
controller.apply({"type": "look_params", "name": "Ball Orbit", "reset": True},
                 None)
authored_again = orbit_offsets(controller)
controller.apply({"type": "preset_save", "name": "Plain Orbit"}, None)
controller.apply({"type": "look_params", "name": "Ball Orbit",
                  "values": {"radius": 50}}, None)
controller.apply({"type": "preset_apply", "name": "Plain Orbit"}, None)
check("a preset saved at authored values clears tuning set after it",
      "Ball Orbit" not in controller.look_params,
      f"{controller.look_params.get('Ball Orbit')}")
check("and really does restore the authored picture",
      orbit_offsets(controller) == authored_again)

# A preset over ported looks only carries no params block at all, so nothing
# about this feature can disturb a preset saved before it existed.
controller.apply({"type": "select_look", "name": "Ball Wave"}, None)
controller.apply({"type": "preset_save", "name": "Ported Only"}, None)
ported_preset = next(p for p in controller.presets if p["name"] == "Ported Only")
check("a preset naming no routine carries no params block",
      "params" not in ported_preset, f"{ported_preset.get('params')}")

# File-sourced, so a stale key is dropped with a notice rather than refusing the
# preset mid-set -- the opposite of a typo'd key from the console.
controller.apply({"type": "select_look", "name": "Ball Orbit"}, None)
stale = next(p for p in controller.presets if p["name"] == "Tuned Orbit")
stale["params"] = {"Ball Orbit": {"radius": 20, "wobble": 3}}
controller.apply({"type": "preset_apply", "name": "Tuned Orbit"}, None)
check("a preset with a parameter this engine no longer has still applies",
      controller.look_params["Ball Orbit"] == {"radius": 20.0},
      f"{controller.look_params.get('Ball Orbit')}")
check("and says what it dropped",
      any("wobble" in n for n in controller.notices),
      f"{controller.notices[-1:]}")

for name in ("Tuned Orbit", "Plain Orbit", "Ported Only"):
    controller.apply({"type": "preset_delete", "name": name}, None)
controller.apply({"type": "look_params", "name": "Ball Orbit", "reset": True},
                 None)

print("\n11d3. movement routines stack, because their offsets add")
# Only the base pose layer ASSIGNS an aim; every movement layer after it adds a
# degree offset. So stacking is free, and it is a shape the old console could
# only store as a third scene at one baked-in relative phase.


def track(ctrl, phases=(0.0, 1.0, 2.0, 3.0, 5.0)):
    out = []
    for phase in phases:
        show = ctrl.director.rebuild()
        ctrl.ctx.set_phase(phase)
        states = statemod.evaluate_stack(ctrl.ctx, show)
        fixture = next(f for f in ctrl.rig.fixtures if f.head is not None)
        base_aim = ctrl.rig.geometry.aim_at_ball(fixture.head)
        aim = states[fixture.fid].aim
        out.append((round(aim.bearing_delta - base_aim.bearing_delta, 3),
                    round(aim.elev_deg - base_aim.elev_deg, 3)))
    return out


controller.apply({"type": "select_look", "name": "Ball Orbit"}, None)
alone = track(controller)
controller.apply({"type": "movement_add", "name": "Nod"}, None)
stacked = track(controller)
check("stacking changes where the heads go", stacked != alone)
# "Nod" is a VERTICAL pendulum, so it must move elevation and leave bearing
# exactly alone. That is what proves the offsets are adding per axis rather
# than one layer replacing the other.
check("a vertical routine stacked on an orbit moves only elevation",
      all(a[0] == b[0] for a, b in zip(alone, stacked))
      and any(a[1] != b[1] for a, b in zip(alone, stacked)),
      f"{alone[1]} -> {stacked[1]}")
check("the stack is published", controller.snapshot()["movement_extra"] == ["Nod"])
controller.apply({"type": "movement_remove", "all": True}, None)
check("removing the stack restores the base route exactly",
      track(controller) == alone)

for label, message, expect in (
    ("stacking the base route on itself",
     {"type": "movement_add", "name": "Ball Orbit"}, "already the base"),
    ("stacking a colour look",
     {"type": "movement_add", "name": "MH Pink"}, "only movement looks stack"),
    ("stacking an unknown look",
     {"type": "movement_add", "name": "Nope"}, "no look named"),
    ("unstacking something that is not stacked",
     {"type": "movement_remove", "name": "Nod"}, "is not stacked"),
):
    try:
        controller.apply(message, None)
        check(f"{label} is refused", False, "accepted")
    except (ValueError, KeyError) as exc:
        check(f"{label} is refused", expect in str(exc), str(exc)[:60])

# The guard above only runs when a look is ADDED. The base can change after --
# by hand, by a cue, by auto mode -- and a look that was both stacked and the
# base ran twice at double excursion, which is exactly what the guard is for.
controller.apply({"type": "movement_add", "name": "Nod"}, None)
controller.apply({"type": "select_look", "name": "Nod"}, None)
check("a stacked look later made the base runs once, not twice",
      len(controller.director.rebuild().movement) == 1,
      f"{len(controller.director.rebuild().movement)} movement layers")
controller.apply({"type": "select_look", "name": "Ball Orbit"}, None)
check("and comes back as a stacked layer when the base moves on",
      len(controller.director.rebuild().movement) == 2
      and track(controller) == stacked)
controller.apply({"type": "movement_remove", "all": True}, None)

for name in ("Nod", "Drift", "Restless"):
    controller.apply({"type": "movement_add", "name": name}, None)
try:
    controller.apply({"type": "movement_add", "name": "Wide Sweep"}, None)
    check("the stack is capped", False, "accepted a fourth")
except ValueError as exc:
    check("the stack is capped", "limit" in str(exc))
controller.apply({"type": "movement_remove", "all": True}, None)

print("\n11d4. variation is random-looking and reproducible")
# engine/ had no `random` import before this, and the reason is that previz, the
# parity sweep and the port all depend on the same inputs giving the same frame.
# A seeded Random keeps that; the module RNG would not.
controller.apply({"type": "vary", "name": "Ball Orbit",
                  "amount": 0.4, "seed": 11}, None)
first = dict(controller.look_params["Ball Orbit"])
controller.apply({"type": "vary", "name": "Ball Orbit",
                  "amount": 0.4, "seed": 11}, None)
check("the same seed gives the same variation",
      controller.look_params["Ball Orbit"] == first, f"{first}")
controller.apply({"type": "vary", "name": "Ball Orbit",
                  "amount": 0.4, "seed": 12}, None)
check("a different seed gives a different one",
      controller.look_params["Ball Orbit"] != first)

# From the AUTHORED values every time, not from whatever is dialled in.
# Compounding would random-walk to an extreme in four presses, and no seed
# would describe where you had ended up.
controller.apply({"type": "vary", "name": "Ball Orbit",
                  "amount": 0.4, "seed": 11}, None)
check("varying twice from one seed lands in the same place, not further out",
      controller.look_params["Ball Orbit"] == first)

check("the cycle length is never varied -- it is a musical decision",
      "bars" not in controller.look_params["Ball Orbit"],
      f"{sorted(controller.look_params['Ball Orbit'])}")

# Merged over the operator's other settings, not instead of them: Vary rolls
# shape numbers only, so a cycle length or a direction set by hand has nothing
# to do with it. It used to replace the whole override and both were lost.
controller.apply({"type": "look_params", "name": "Wind Out",
                  "values": {"direction": "in", "bars": 32}}, None)
controller.apply({"type": "vary", "name": "Wind Out", "seed": 3}, None)
wind = controller.look_params["Wind Out"]
check("vary keeps what it does not vary",
      wind.get("direction") == "in" and wind.get("bars") == 32.0
      and len(wind) > 2, f"{wind}")
controller.apply({"type": "look_params", "name": "Wind Out", "reset": True},
                 None)

orbit_params = blocksmod.PARAMS["orbit"]
for amount in (0.1, 0.5, 1.0):
    for seed in range(6):
        controller.apply({"type": "vary", "name": "Ball Orbit",
                          "amount": amount, "seed": seed}, None)
        for key, value in controller.look_params["Ball Orbit"].items():
            spec = parammod.find(orbit_params, key)
            if not (spec.min <= value <= spec.max):
                check("variation stays inside every declared range", False,
                      f"{key}={value} outside {spec.min}..{spec.max}")
                break
        else:
            continue
        break
else:
    check("variation stays inside every declared range", True,
          "18 combinations of amount and seed")

before = controller.vary_seed
controller.apply({"type": "vary", "name": "Ball Orbit"}, None)
check("omitting the seed picks the next one and publishes it",
      controller.vary_seed == before + 1
      and controller.snapshot()["vary_seed"] == before + 1)

try:
    controller.apply({"type": "vary", "name": "Ball Wave"}, None)
    check("varying a ported look is refused", False, "accepted")
except ValueError as exc:
    check("varying a ported look is refused", "no parameters to vary" in str(exc))

controller.apply({"type": "look_params", "name": "Ball Orbit", "reset": True},
                 None)

print("\n11d5. the centre goes as far as the rig's heads can, and no further")
# Fixtures travel different distances, so a fixed +-180 / +-90 was wrong both
# ways: it offered places no head could reach and refused ones every head
# could. The range is now read off the rig's own geometry.
reach = controller.snapshot()["reach"]
check("the rig's reach is published, per axis",
      set(reach) == {"bearing", "elevation"}
      and all(lo < 0 < hi for lo, hi in reach.values()), f"{reach}")
geo = controller.rig.geometry
heads = range(len(geo.heads))
widest = max(geo.reach(i)[0][1] for i in heads)
check("it is the WIDEST any head can go, so a better fixture is not held back",
      reach["bearing"][1] >= widest - 1e-9, f"{reach['bearing'][1]} vs {widest:.2f}")

past_180 = min(reach["bearing"][1], 190.0)
controller.apply({"type": "macro", "center": [past_180, 0]}, None)
check("the centre can go past the old 180-degree cap when the rig can",
      controller.ctx.move_center[0] == past_180 > 180.0,
      f"{controller.ctx.move_center}")
controller.apply({"type": "macro", "center": [-170, 0]}, None)
check("and stops where the heads stop, rather than offering the impossible",
      controller.ctx.move_center[0] == reach["bearing"][0],
      f"{controller.ctx.move_center} vs reach {reach['bearing']}")

# A modulator on the centre swings as far as the slider for it would.
controller.apply({"type": "modulate", "param": "bearing", "shape": "ramp",
                  "bars": 4}, None)
swing = next(m for m in controller.snapshot()["modulators"]
             if m["param"] == "bearing")
check("a modulator on the centre spans the rig's reach, not the fallback",
      (swing["low"], swing["high"]) == tuple(reach["bearing"]), f"{swing}")
controller.apply({"type": "modulate_clear", "all": True}, None)
controller.apply({"type": "macro", "reset": True}, None)

print("\n11d6. a head asked past its own rail says so")
# The centre is bounded by the MOST capable head, so a less capable one can be
# asked for somewhere it cannot go. It stops at the rail -- the clamp in
# `geometry.encode` -- and this is the part that tells the operator, per head.
controller.apply({"type": "select_look", "name": "Heads - Ball"}, None)


def publish_now():
    """One evaluated frame into `latest_states`, as the frame clock would."""
    show = controller.director.rebuild()
    controller._attach_overrides(show)
    controller.latest_states = statemod.evaluate(controller.ctx, show)
    return {f["name"]: f for f in controller.snapshot()["fixtures"]}


fixtures_now = publish_now()
check("on the ball, no head is at a limit",
      not any("at_limit" in f for f in fixtures_now.values()))

# Find the head with the least bearing travel and send the centre just past it.
least = min(heads, key=lambda i: geo.reach(i)[0][1])
target = geo.reach(least)[0][1] + 3.0
if target <= reach["bearing"][1]:
    controller.apply({"type": "macro", "center": [target, 0]}, None)
    fixtures_now = publish_now()
    short = fixtures_now[geo.heads[least].name]
    check("the head that cannot reach is flagged at its bearing limit",
          short.get("at_limit") == ["bearing"], f"{short.get('at_limit')}")
    able = [fixtures_now[geo.heads[i].name] for i in heads
            if geo.reach(i)[0][1] >= target + 0.5]
    check("while the heads that can reach are not",
          able and not any("at_limit" in f for f in able),
          f"{len(able)} able heads")
else:
    check("this rig's heads differ enough to test a lesser one", False,
          "every head reaches equally far")
controller.apply({"type": "macro", "reset": True}, None)

print("\n11e. the descriptors the console renders controls from")
# They reach the UI as a generated file, not in this snapshot: they are
# constants of the build, and the snapshot goes out ten times a second.
sys.path.insert(0, str(REPO / "engine" / "tests"))
import dump_designer_fixtures  # noqa: E402

table = dump_designer_fixtures.block_table()
snap = controller.snapshot()
check("the snapshot no longer carries constants of the build",
      not {"generators", "macro_params", "modulator_shapes"} & set(snap))
check("every block is in the UI's table", set(table["blocks"])
      == set(blocksmod.BLOCKS))
check("each with its arguments, labelled",
      all(all("label" in p for p in b["params"])
          for b in table["blocks"].values()))
check("the shape macros are described the same way",
      {p["name"] for p in table["macros"]} == {"size", "spread", "bearing", "elev"})
# A parametric look in the snapshot names its block, which is what the console
# looks up in that table to render a control panel for it.
orbit_look = next(l for l in snap["looks"] if l["name"] == "Ball Orbit")
check("a parametric look names its block and carries its arguments",
      orbit_look["block"] == "orbit" and orbit_look["args"]["radius"] == 10.0)
# The whole point of publishing them: the clamp and the control now read the
# same declaration, so a range cannot drift between the two ends the way it did
# while it was a literal in each.
size_spec = next(p for p in table["macros"] if p["name"] == "size")
controller.apply({"type": "macro", "size": 99}, None)
check("the macro clamp agrees with the range it publishes",
      controller.ctx.move_size == size_spec["max"],
      f"clamped to {controller.ctx.move_size}, published max {size_spec['max']}")
controller.apply({"type": "macro", "reset": True}, None)
check("and reset returns the published default",
      controller.ctx.move_size == size_spec["default"])

print("\n12. access tiers")
guarded = ShowController(EVENT)
guarded_server = ShowServer(guarded, port=0, token="secret123")
guarded.start()
guarded_server.start()
guarded_port = guarded_server.httpd.server_address[1]
time.sleep(0.3)
try:
    # One client at a time, and closed before the next. Three left connected and
    # unread is what surfaced the broadcast stall fixed in send_all -- worth
    # knowing, but not what this section is testing.
    def tier_of(path):
        c = Client(guarded_port, path=path)
        return c, c.recv().get("tier")

    good, tier = tier_of("/ws?token=secret123")
    check("the right token grants configure", tier == "configure", f"{tier}")
    good.recv()
    good.send({"type": "master", "value": 0.25})
    good.wait_for(lambda s: abs(s["master"] - 0.25) < 1e-6)
    check("and it can drive the show", True)
    good.close()

    wrong, wrong_tier = tier_of("/ws?token=nope")
    check("a wrong token means view only", wrong_tier == "view", f"{wrong_tier}")
    wrong.close()

    anon, anon_tier = tier_of("/ws")
    check("no token means view only", anon_tier == "view", f"{anon_tier}")

    # A view client's commands must be REFUSED and said so, not silently
    # dropped: the failure mode being guarded against is controls that quietly
    # stop having an effect.
    anon.recv()
    anon.send({"type": "master", "value": 0.9})
    state = anon.wait_for(lambda s: any("needs operate" in n for n in s["notices"]))
    check("a view client cannot move the master",
          abs(state["master"] - 0.25) < 1e-6, f"master={state['master']}")
    check("and is told why, rather than ignored", True)

    anon.send({"type": "jog", "fixture": "Moving Head #1", "pan": 5, "tilt": 0})
    anon.wait_for(lambda s: any("needs configure" in n for n in s["notices"]))
    check("a view client cannot jog (which bypasses the safety taper)", True)
    anon.close()

    # Browsers send Origin on a WebSocket handshake and do not apply the
    # same-origin policy to it, so without this check any page the operator has
    # open could drive the rig.
    hostile = socket.create_connection(("127.0.0.1", guarded_port), timeout=5)
    key = base64.b64encode(os.urandom(16)).decode()
    hostile.sendall(("\r\n".join([
        "GET /ws HTTP/1.1", "Host: localhost", "Upgrade: websocket",
        "Connection: Upgrade", f"Sec-WebSocket-Key: {key}",
        "Sec-WebSocket-Version: 13", "Origin: http://evil.example",
    ]) + "\r\n\r\n").encode())
    reply = hostile.recv(200).decode(errors="replace")
    check("a cross-origin handshake is refused", "403" in reply.split("\r\n")[0],
          reply.split("\r\n")[0])
    hostile.close()

    # The stall this section accidentally found. One client that stops reading
    # used to block ws.send on the single broadcast thread, freezing the console
    # for everyone else -- at a venue that reads as the engine hanging, and the
    # cause (a phone that locked its screen) is nowhere near the symptom.
    # The idle client has to be genuinely backed up for this to test anything:
    # one 30 kB snapshot fits in a TCP window, so it takes a couple of seconds
    # of unread broadcasts before a send would actually have blocked. Without
    # the wait this check passes whether or not the bug is present, which is the
    # kind of test that is worse than none.
    idle = Client(guarded_port, path="/ws")
    idle.recv()                        # welcome, then never read again
    idle.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    time.sleep(2.5)                    # ~25 unread snapshots

    live = Client(guarded_port, path="/ws?token=secret123")
    live.recv()
    started = time.time()
    live.send({"type": "master", "value": 0.55})
    seen = live.wait_for(lambda s: abs(s["master"] - 0.55) < 1e-6)
    elapsed = time.time() - started
    check("a stuck client does not stall the broadcast for a live one",
          elapsed < 2.0, f"{elapsed:.2f}s to see a change land")

    # The drop is NOT on the same clock as the stall, and sampling one snapshot
    # at a fixed moment made this check mean different things on different
    # platforms. The queue only fills once the kernel stops absorbing writes,
    # and the buffer that has to fill first is the SERVER's send buffer, which
    # Linux auto-tunes into the megabytes -- so the same stuck client is dropped
    # in well under a second on Windows and around seven seconds on Linux. The
    # guarantee is that it is dropped and said so, not that it happens inside
    # the window the stall check happens to use, so wait for it.
    try:
        seen = live.wait_for(
            lambda s: len(s["presence"]) <= 1
            or any("not reading" in n for n in s["notices"]),
            timeout=30.0)
        dropped, why = True, f"dropped after {time.time() - started:.1f}s"
    except AssertionError:
        dropped, why = False, "still present after 30s"
    check("and the stuck one was dropped, with a reason", dropped,
          f"{why}; presence={len(seen['presence'])}, "
          f"notices={seen['notices'][-1:]}")
    idle.close()
    live.close()
finally:
    guarded_server.stop()
    guarded.stop()


# -- 13. with no token, everything is permitted -------------------------------
#
# The default is a token, but a laptop with no network is a real case and it
# must not need a query string to work.
print("\n13. --no-token")
open_ctl = ShowController(EVENT)
open_server = ShowServer(open_ctl, port=0, token=None)
open_ctl.start()
open_server.start()
open_port = open_server.httpd.server_address[1]
time.sleep(0.3)
try:
    c = Client(open_port, path="/ws")
    welcome = c.recv()
    check("with no token configured, a bare client gets configure",
          welcome.get("tier") == "configure", f"{welcome.get('tier')}")
    c.recv()
    c.send({"type": "master", "value": 0.4})
    c.wait_for(lambda s: abs(s["master"] - 0.4) < 1e-6)
    check("and can drive the show", True)
    c.close()
finally:
    open_server.stop()
    open_ctl.stop()

# -- 14. reloading the rig without restarting ---------------------------------
#
# A patch edit used to mean "saved, now restart". The engine resolves profiles,
# channel offsets and head indices once at startup, but commands drain at frame
# boundaries, so there is a safe moment to swap the whole rig -- and `render`
# rebuilds a full frame from the baseline every tick, so re-addressing does not
# leave the old channels stuck at their last value.
print("\n14. live rig reload")
with tempfile.TemporaryDirectory() as tmp:
    ev = Path(tmp) / "ev"
    ev.mkdir()
    for name in ("rig.json", "calibration.json"):
        shutil.copy(EVENT / name, ev / name)

    live = ShowController(ev)
    live.start()
    try:
        before = len(live.rig.fixtures)

        # A patch edit through the same path the UI uses.
        live.apply({"type": "patch_add", "name": "Par 1",
                    "manufacturer": "UKing", "model": "Par 36 Custom",
                    "mode": "5 Channel", "tags": ["pars"]}, None)
        check("an edit is saved but not yet live",
              live.pending_patch and len(live.rig.fixtures) == before,
              f"pending={live.pending_patch} fixtures={len(live.rig.fixtures)}")

        live.apply({"type": "drift", "readings": [
            list(h.calibrated_ball_dmx) for h in live.rig.geometry.heads]}, None)
        check("a saved, unapplied edit leaves the drift result alone",
              live.snapshot()["drift"] is not None)

        applied_at = time.monotonic()
        live.apply({"type": "patch_apply"}, None)
        check("applying it swaps the rig in place",
              len(live.rig.fixtures) == before + 1, f"{len(live.rig.fixtures)}")
        # Measured against the rig just replaced: a moved head decodes the same
        # DMX to different degrees, so its old "ok" would be a go nobody checked.
        check("and drops the drift result measured against the old rig",
              live.snapshot()["drift"] is None)
        check("and clears the pending flag", not live.pending_patch)
        check("the context sees the new rig too",
              live.ctx.rig is live.rig and len(live.ctx.rig.fixtures) == before + 1)
        # Not `== 0`: the runner is live, and a frame may already have run since
        # the reload, moving each value up by one slew step -- the fade-in this
        # checks for, working. (Asserting the instant raced the frame thread and
        # failed on a Windows runner.) What must hold is that nothing SNAPPED:
        # no value is beyond what the slew allows for the time that has passed.
        slew = live.ctx.taper.slew_per_second
        bound = slew * (time.monotonic() - applied_at + 0.05)
        seeded = list(live.ctx._taper_prev.values())
        check("intensity is seeded dark so the safety slew fades it in, "
              "rather than snapping",
              bound < 1.0 and len(seeded) == len(live.rig.fixtures)
              and all(0.0 <= v <= bound for v in seeded),
              f"bound {bound:.3f}, values {sorted(set(round(v, 3) for v in seeded))}")

        # Everything the reload clears is keyed by fixture ID. Trims, colours,
        # flashes, jogs and captures are keyed by NAME, and a patch edit can
        # delete the thing they name -- leaving a trim with no row to reset it
        # and a jog entry that would re-bypass the safety taper if that name
        # ever came back.
        live.apply({"type": "level", "target": "Par 1", "value": 0.3}, None)
        live.apply({"type": "level", "target": "corner movers", "value": 0.6}, None)
        live.apply({"type": "flash", "target": "corner movers"}, None)
        live.apply({"type": "flash", "target": "pars"}, None)
        live.apply({"type": "patch_remove", "name": "Par 1"}, None)
        live.apply({"type": "patch_apply"}, None)
        check("a reload drops settings for a fixture that no longer exists",
              "Par 1" not in live.level_overrides, f"{live.level_overrides}")
        # The half that would be easy to get wrong: a target is not only a
        # fixture name. `all` and every tag are equally valid, and pruning on
        # fixture names alone would throw away the group trims -- the common
        # case, and it would look exactly like the console forgetting itself.
        check("but a GROUP trim survives, because a group is a valid target too",
              abs(live.level_overrides.get("corner movers", 0) - 0.6) < 1e-9,
              f"{live.level_overrides}")
        # Removing the last fixture carrying a tag takes the TAG with it, so
        # `pars` stops being a target at all while `corner movers` does not.
        check("a flash on a group that survives is kept, one on a group that "
              "went with its last fixture is not",
              live.flashing == {"corner movers"}, f"{live.flashing}")
        check("with a notice, so it is not silent",
              any("no longer has" in n for n in live.notices),
              f"{live.notices[-2:]}")
        live.apply({"type": "level", "clear": True, "target": "corner movers"}, None)
        live.apply({"type": "flash_clear"}, None)

        # The frame path has to survive the swap: a reload that renders a broken
        # frame is worse than one that refuses.
        frame = live.runner.render_once()
        check("and the next frame still renders",
              frame and all(len(f) == 512 for f in frame.values()),
              f"{[len(f) for f in frame.values()]}")

        # THE one that matters. A bad edit must cost a notice, not the show.
        healthy = len(live.rig.fixtures)
        (ev / "rig.json").write_text('{"fixtures": [{"id": 0, "name": "X",'
                                     ' "manufacturer": "Nope", "model": "Nope",'
                                     ' "mode": "1", "address": 1}]}',
                                     encoding="utf-8")
        live.apply({"type": "patch_apply"}, None)
        check("a rig that cannot load is REFUSED, and the old one keeps running",
              len(live.rig.fixtures) == healthy, f"{len(live.rig.fixtures)}")
        check("with a notice saying so",
              any("still running the old rig" in n for n in live.notices),
              f"{live.notices[-1:]}")
        frame = live.runner.render_once()
        check("and the show is still rendering after the refusal",
              frame and all(len(f) == 512 for f in frame.values()))

        # Broken in a different way: loads fine, fails validation.
        (ev / "rig.json").write_text(json.dumps({
            "name": "clash", "venue": "despacio-room", "mount_mode": "venue",
            "fixtures": [
                {"id": 0, "name": "A", "manufacturer": "UKing",
                 "model": "Par 36 Custom", "mode": "5 Channel", "address": 1},
                {"id": 1, "name": "B", "manufacturer": "UKing",
                 "model": "Par 36 Custom", "mode": "5 Channel", "address": 3},
            ]}), encoding="utf-8")
        live.apply({"type": "patch_apply"}, None)
        check("an overlapping patch is refused too",
              len(live.rig.fixtures) == healthy, f"{len(live.rig.fixtures)}")
    finally:
        live.stop()

# -- 14b. a solved calibration applies live too -------------------------------
#
# Solve & write used to end in "restart the engine to load it", from before the
# rig could be reloaded at all. The rig is read from calibration.json as well as
# rig.json, so the same Apply loads it: saved first, live when applied.
print("\n14b. a solved calibration, applied without a restart")
with tempfile.TemporaryDirectory() as tmp:
    ev = Path(tmp) / "ev"
    ev.mkdir()
    for name in ("rig.json", "calibration.json"):
        shutil.copy(EVENT / name, ev / name)

    from engine import geometry as geo
    live = ShowController(ev)
    live.start()
    try:
        head = live.rig.geometry.heads[0]
        old_aim = tuple(head.calibrated_ball_dmx)
        # A head knocked a few steps since it was calibrated: what a perfect
        # operator would capture aiming it, through the engine's own geometry.
        knocked = geo.Head(**{**head.__dict__,
                              "calibrated_ball_dmx": (old_aim[0] + 6, old_aim[1] + 4)})
        truth = geo.RigGeometry(heads=(knocked,), ball=live.rig.venue.ball,
                                mount_mode=live.rig.geometry.mount_mode)
        venue = live.rig.venue
        far_x = 400.0 if head.x > venue.width / 2 else venue.width - 400.0
        far_z = 400.0 if head.z > venue.depth / 2 else venue.depth - 400.0
        for label, target in (("ball", venue.ball), ("floor", (far_x, 0.0, far_z)),
                              ("wall", (far_x, head.height, venue.depth / 2))):
            pan16, tilt16 = truth.encode(0, truth.aim_at_point(0, *target))
            live.apply({"type": "capture", "fixture": head.name,
                        "pan": geo.split16(pan16)[0], "tilt": geo.split16(tilt16)[0],
                        "target": list(target), "label": label}, None)
        live.apply({"type": "solve", "write": True}, None)
        written = next(h for h in json.loads((ev / "calibration.json").read_text(
            encoding="utf-8"))["heads"] if h["fixture"] == head.name)["ball_dmx"]
        check("solve & write saves the new calibration", tuple(written) != old_aim,
              f"{old_aim} -> {written}")
        check("but the running show keeps aiming from the old one",
              tuple(live.rig.geometry.heads[0].calibrated_ball_dmx) == old_aim)
        snap = live.snapshot()
        check("and says so: Apply now is offered, for calibration.json",
              snap["pending_patch"] and snap["pending_files"] == ["calibration.json"],
              f"{snap['pending_patch']} {snap['pending_files']}")
        check("with a notice that says apply, not restart",
              "Apply it" in live.notices[-1] and "restart" not in live.notices[-1],
              live.notices[-1])

        live.apply({"type": "patch_apply"}, None)
        check("applying loads it into the running show",
              tuple(live.rig.geometry.heads[0].calibrated_ball_dmx) == tuple(written),
              f"{live.rig.geometry.heads[0].calibrated_ball_dmx}")
        check("the context aims from it too",
              tuple(live.ctx.rig.geometry.heads[0].calibrated_ball_dmx) == tuple(written))
        check("and nothing is pending after",
              not live.pending_patch and live.snapshot()["pending_files"] == [])
        check("with a notice that the new calibration is live",
              any("calibration is live" in n for n in live.notices[-3:]),
              f"{live.notices[-3:]}")

        # A patch edit and a solve both waiting: one Apply loads both.
        live.apply({"type": "patch_tags", "name": head.name,
                    "tags": ["movers", "corner movers"]}, None)
        live.apply({"type": "solve", "write": True}, None)
        check("a patch and a calibration both waiting are listed in order",
              live.snapshot()["pending_files"] == ["rig.json", "calibration.json"],
              f"{live.snapshot()['pending_files']}")
        live.apply({"type": "patch_apply"}, None)
        check("and one Apply clears both", live.snapshot()["pending_files"] == [])
    finally:
        live.stop()

# -- 15. the DJ tempo seam, end to end ----------------------------------------
#
# A datagram in, a moved show clock out, with no CDJs in the room. Proving the
# whole downstream path at the desk is the point of building the seam before
# the hardware exists.
print("\n15. tempo ingest")
from engine import sync as syncmod          # noqa: E402

djs = ShowController(EVENT)
try:
    djs.enable_sync(port=0, bind="127.0.0.1")
    sync_port = djs.sync.sock.getsockname()[1]
    djs.start()
    time.sleep(0.3)

    before = djs.snapshot()
    check("the clock starts on tap, not on a bridge",
          before["clock"]["source"] != "prolink"
          and before["sync"]["driving"] is False,
          f"{before['clock']['source']}, {before['sync']}")
    check("but the port reports itself as listening",
          before["sync"]["listening"] is True)

    syncmod.send({"bpm": 132.0, "beat_in_bar": 0, "source": "prolink",
                  "phrase_measured": True, "phrase_label": "Build",
                  "phrase_ends_in": 32.0, "deck": "2", "track": "Cosmic Slop"},
                 port=sync_port)
    deadline = time.time() + 3
    while abs(djs.clock.bpm - 132.0) > 1e-6 and time.time() < deadline:
        time.sleep(0.02)
    after = djs.snapshot()
    check("a datagram moves the show tempo",
          abs(after["clock"]["bpm"] - 132.0) < 1e-6, f"{after['clock']['bpm']}")
    check("and names what is driving it", after["clock"]["source"] == "prolink",
          f"{after['clock']['source']}")
    # The thing the whole milestone is for: phrase stops being counted from a
    # tapped downbeat and starts being read off the player.
    check("phrase becomes MEASURED rather than counted",
          after["clock"]["phrase_measured"] is True)
    check("and the phrase itself is reported",
          after["sync"]["phrase"] == "Build"
          and after["sync"]["track"] == "Cosmic Slop",
          f"{after['sync']}")
    check("with a countdown the UI can run without the bridge re-sending",
          0 < after["sync"]["phrase_ends_in"] <= 32.0,
          f"{after['sync']['phrase_ends_in']}")

    # STALENESS. A bridge that dies leaves the timeline free-running at the last
    # tempo with `source` still naming it -- the console looks locked while it
    # drifts away from a DJ nobody is listening to.
    check("the age of the last packet is reported",
          after["sync"]["age"] is not None and after["sync"]["age"] < 5,
          f"{after['sync']['age']}")
    time.sleep(0.6)
    check("and it grows while the bridge is quiet",
          djs.snapshot()["sync"]["age"] > after["sync"]["age"],
          f"{after['sync']['age']} -> {djs.snapshot()['sync']['age']}")

    # Taking it back. The bridge must never silently own the clock.
    held = djs.ctx.beat
    djs.apply({"type": "sync_off"}, None)
    took = djs.snapshot()
    check("take-over returns the clock without moving the music",
          took["clock"]["source"] == "tap"
          and abs(took["clock"]["bpm"] - 132.0) < 1e-6
          and djs.ctx.beat >= held,
          f"{took['clock']['source']} at {took['clock']['bpm']}")
    check("and drops the phrase, which was the bridge's claim not ours",
          took["clock"]["phrase_measured"] is False
          and took["sync"]["phrase"] is None, f"{took['sync']}")

    # OSC on the same port, because beat-link-trigger speaks it and pointing it
    # here is meant to be the entire integration.
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(b"/beat-link/bpm\0\0,f\0\0" + struct.pack(">f", 140.0),
                 ("127.0.0.1", sync_port))
    deadline = time.time() + 3
    while abs(djs.clock.bpm - 140.0) > 1e-3 and time.time() < deadline:
        time.sleep(0.02)
    check("an OSC message from beat-link-trigger drives it too",
          abs(djs.clock.bpm - 140.0) < 1e-3, f"{djs.clock.bpm}")

    # And the one that must never work: this port is a tempo seam, not a command
    # channel. There is no token on a datagram, so the guard has to be that the
    # port simply cannot express anything else.
    fixtures_before = len(djs.rig.fixtures)
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.sendto(b'{"type": "panic"}', ("127.0.0.1", sync_port))
        s.sendto(b'{"type": "patch_remove", "name": "Pinspot #1"}',
                 ("127.0.0.1", sync_port))
    time.sleep(0.4)
    check("the tempo port cannot panic the rig or edit the patch",
          not djs.runner.panicked and len(djs.rig.fixtures) == fixtures_before,
          f"panicked={djs.runner.panicked}, {len(djs.rig.fixtures)} fixtures")

    # -- which track, and where in it (F19d) --------------------------------
    # rkbx_link's shapes, over the real port: the identity a field at a time,
    # then position at ~60 Hz. The snapshot is what the console will show.
    def osc_msg(address: str, tag: str, value) -> bytes:
        def pad(b: bytes) -> bytes:
            return b + b"\0" * (4 - len(b) % 4)
        payload = (struct.pack(">f", value) if tag == "f"
                   else pad(value.encode()) if tag == "s" else b"")
        return pad(address.encode()) + pad(b"," + tag.encode()) + payload

    ignored_before = djs.sync.ignored
    rejected_before = djs.sync.rejected
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        for address, value in (("/master/track/title", "Night Drive"),
                               ("/master/track/artist", "Someone"),
                               ("/master/track/album", "EP")):
            s.sendto(osc_msg(address, "s", value), ("127.0.0.1", sync_port))
        s.sendto(osc_msg("/master/phrase/next", "s", "Chorus"), ("127.0.0.1", sync_port))
        started = time.time()
        for i in range(40):
            s.sendto(osc_msg("/master/time", "f", 61.0 + i / 60), ("127.0.0.1", sync_port))
            time.sleep(1 / 60)
    first = djs.snapshot()["track"]
    time.sleep(0.1)
    later = djs.snapshot()["track"]
    check("the snapshot says which track and where",
          first["state"] == "playing" and first["title"] == "Night Drive"
          and first["artist"] == "Someone" and first["source"] == "rkbx"
          and 61.4 < first["time"] < 62.0, f"{first}")
    check("and the position keeps moving between snapshots",
          later["time"] > first["time"], f"{first['time']} -> {later['time']}")
    check("rkbx_link's phrase/next is counted as ignored, not rejected",
          djs.sync.ignored == ignored_before + 1
          and djs.sync.rejected == rejected_before,
          f"ignored {djs.sync.ignored}, rejected {djs.sync.rejected}")
    djs.apply({"type": "sync_off"}, None)
    check("taking the clock back forgets the track too",
          djs.snapshot()["track"]["state"] == "no_track")
finally:
    djs.stop()

# -- 16. the show folder: matching, linking, hot reload (F19f) ----------------
#
# Driven by hand rather than by the frame loop: every sync carries the arrival
# time it would have had, worker jobs are waited for, and their results are
# installed by calling `_drain` -- exactly what the frame loop does, minus the
# races. The worker is the real one, on its real thread.
print("\n16. the show folder: matching, linking, hot reload, the grid check")
from engine import showlibrary   # noqa: E402

shows_tmp = Path(tempfile.mkdtemp(prefix="klights-shows-"))
shows = shows_tmp / "shows"
shutil.copytree(REPO / "shared" / "show-example", shows)
load_threads: list[str] = []
real_load = showlibrary.load


def spy_load(*args, **kwargs):
    load_threads.append(threading.current_thread().name)
    return real_load(*args, **kwargs)


showlibrary.load = spy_load
sc = ShowController(EVENT, show_dir=shows)
sc.worker.start()
GUEST_SIG = "d" * 40


def settle():
    """Let the worker finish, then install what it handed back."""
    for _ in range(3):              # a job's result may queue another job
        assert sc.worker.wait_idle(5.0)
        sc._drain()


def at(t):
    sc.ctx.time = t                 # runner.now() before start
    return t


def blt_track(t, title, artist="", album="", duration=0.0, rid=1, sig=None):
    msg = {"type": "sync", "source": "blt", "deck": "1", "title": title,
           "artist": artist, "album": album, "duration": duration,
           "rekordbox_id": rid}
    if sig:
        msg["signature"] = sig
    sc.apply(msg, None, at(t))


def blt_play(t0, seconds, start_s=10.0, beat_offset=0, hz=25):
    """beat-link's /klights/v1/pos: position and beat count in one packet,
    counted on the synthetic track's grid as it is in the folder NOW."""
    grid = sc.show_library.grids["synth-128"]
    n = int(seconds * hz)
    for i in range(n):
        pos = start_s + i / hz
        count = int(grid.beat_at(pos) // 1) - int(grid.beats[0]) + 1
        sc.apply({"type": "sync", "source": "blt", "deck": "1",
                  "track_time": pos, "playing": True, "pitch": 1.0,
                  "beat_number": max(0, count + beat_offset)},
                 None, at(t0 + i / hz))
    end = t0 + n / hz
    sc._track_frame(at(end))
    return end


def track_now():
    return sc.snapshot()["track"]


try:
    plain = ShowController(EVENT)
    check("with no show folder, none of it exists",
          plain.snapshot()["show"] is None
          and plain.snapshot()["track"]["match"] is None
          and plain._track_frame() is None)
    try:
        plain.apply({"type": "track_link", "track_id": "synth-128"}, None)
        refused = False
    except ValueError as exc:
        refused = "--show-dir" in str(exc)
    check("and track_link says to start with --show-dir", refused)

    show = sc.snapshot()["show"]
    check("the snapshot describes the folder", show["dir"] == str(shows)
          and show["tracks"] == 1 and show["timelines"] == 1
          and show["errors"] == 0 and show["rev"].startswith("l:"), f"{show}")
    check("the first load happens at startup, before anything runs",
          load_threads == ["MainThread"], f"{load_threads}")
    check("and show.json's settings are on the transport",
          sc.transport.min_track_change_s == 2.0 and sc.transport.grace_s == 4.0)

    # The fake deck's first track: bridge.py --fake --blt plays exactly this.
    t = 100.0
    blt_track(t, "synthetic 128", "kLights", "test track", 180.0)
    t = blt_play(t, 3.0)
    tr_ = track_now()
    check("the synthetic track matches its prepped file",
          tr_["match"] is not None and tr_["match"]["track_id"] == "synth-128"
          and tr_["match"]["via"] == "title_artist_album"
          and tr_["match"]["stale"] is False, f"{tr_['match']}")
    check("and its timeline comes with it, compiled at load",
          tr_["match"]["has_timeline"] is True
          and sc.pinned.timeline is sc.show_library.timelines["synth-128"])
    check("and its beats agree with the grid: no warning",
          tr_["grid_warning"] is None, f"{tr_['grid_warning']}")
    synth_pin = sc.pinned

    # Hot reload, mid-song: the grid moves under a playing track.
    doc = json.loads((shows / "tracks" / "synth-128.json").read_text())
    doc["grid"]["segments"] = [[0, 250, 128]]
    doc["grid"].pop("rev", None)
    (shows / "tracks" / "synth-128.json").write_text(json.dumps(doc), "utf-8")
    sc.watcher.poll()
    sc.watcher.poll()                       # held still for a poll: reload
    settle()
    check("an edit is noticed and reloaded on the worker",
          load_threads[-1:] == ["klights-worker"]
          and sc.show_library.grids["synth-128"].times[0] == 0.25, f"{load_threads}")
    t = blt_play(t, 1.0, start_s=13.0)
    check("but the playing track keeps the grid it started with",
          sc.pinned is synth_pin and sc.pinned.grid.times[0] == 0.0)
    check("and the console says the folder changed since it matched",
          track_now()["match"]["stale"] is True)
    check("the snapshot's folder rev moves with the reload",
          sc.snapshot()["show"]["rev"] != show["rev"])

    # A guest's copy of something: unknown here.
    t += 2.5
    blt_track(t, "Unknown Guest Tune", "Guest DJ", "", 240.0, rid=2, sig=GUEST_SIG)
    t = blt_play(t, 1.0)
    tr_ = track_now()
    check("a guest's track is unmatched, and says so",
          tr_["match"] == {"track_id": None, "via": "none", "candidates": [],
                           "has_timeline": False, "stale": False},
          f"{tr_['match']}")
    check("and has no grid to check against", sc.grid_check is None
          and tr_["grid_warning"] is None)

    # Refusals first.
    for bad, why in (({"track_id": "nope"}, "no prepped track"),
                     ({"track_id": "../x"}, "no prepped track"),
                     ({}, "no prepped track")):
        try:
            sc.apply({"type": "track_link", **bad}, None, at(t))
            refused = False
        except ValueError as exc:
            refused = why in str(exc)
        check(f"track_link refuses {bad}", refused)
    operator = servermod.Client(id="op", name="op", tier="operate")
    try:
        sc.apply({"type": "track_link", "track_id": "synth-128"}, operator, at(t))
        refused = False
    except ValueError as exc:
        refused = "needs configure" in str(exc)
    check("linking is configure-tier: it writes the show folder", refused)

    reply = sc.apply({"type": "track_link", "track_id": "synth-128"}, None, at(t))
    check("a link is queued, and says when it applies",
          reply == {"queued": True, "track_id": "synth-128",
                    "applies": "next_play"}, f"{reply}")
    settle()
    linked = json.loads((shows / "tracks" / "synth-128.json").read_text())
    check("the guest's description is saved as an alias, with its signature",
          linked["aliases"][-1]["title"] == "Unknown Guest Tune"
          and linked["aliases"][-1]["via"] == "manual"
          and GUEST_SIG in linked["ids"]["blt_signatures"], f"{linked['aliases']}")
    check("a notice says so, and that it applies from the next play",
          any("linked 'Unknown Guest Tune' to synth-128" in n
              and "next play" in n for n in sc.notices), f"{sc.notices[-2:]}")
    t = blt_play(t, 1.0, start_s=20.0)
    check("the playing track is NOT re-matched mid-song",
          track_now()["match"]["via"] == "none"
          and track_now()["match"]["stale"] is True, f"{track_now()['match']}")

    t += 2.5
    blt_track(t, "synthetic 128", "kLights", "test track", 180.0)
    t = blt_play(t, 1.0)
    check("the next track matches against the new folder, new grid and all",
          sc.pinned.grid.times[0] == 0.25
          and track_now()["match"]["stale"] is False)
    t += 2.5
    blt_track(t, "Unknown Guest Tune", "Guest DJ", "", 240.0, rid=2, sig=GUEST_SIG)
    t = blt_play(t, 1.0)
    check("and the guest's track, played again, is linked -- by its signature",
          track_now()["match"]["track_id"] == "synth-128"
          and track_now()["match"]["via"] == "signature", f"{track_now()['match']}")

    # rkbx_link sends no signature: the alias is what matches it.
    sc.apply({"type": "sync_off"}, None, at(t))
    t += 0.5
    for i, (key, value) in enumerate((("title", "Unknown Guest Tune"),
                                      ("artist", "Guest DJ"), ("album", ""))):
        sc.apply({"type": "sync", "source": "rkbx", key: value}, None,
                 at(t + i * 0.002))
    sc.apply({"type": "sync", "source": "rkbx", "track_time": 30.0}, None,
             at(t + 0.2))
    sc._track_frame(at(t + 0.21))
    check("from rekordbox, the same track matches by the saved alias",
          track_now()["match"]["via"] == "alias", f"{track_now()['match']}")
    t += 0.3

    # The grid cross-check, from both sources.
    sc.apply({"type": "sync_off"}, None, at(t))
    t += 0.5
    blt_track(t, "synthetic 128", "kLights", "test track", 180.0)
    t = blt_play(t, 3.0, beat_offset=1)
    check("a beat count one ahead of the grid raises a warning saying so",
          track_now()["grid_warning"] == {"kind": "number", "offset_beats": 1.0},
          f"{track_now()['grid_warning']}")
    t = blt_play(t, 3.0, start_s=13.0)
    check("and in step again, it clears", track_now()["grid_warning"] is None,
          f"{track_now()['grid_warning']}")

    sc.apply({"type": "sync_off"}, None, at(t))
    t += 0.5
    for i, (key, value) in enumerate((("title", "synthetic 128"),
                                      ("artist", "kLights"),
                                      ("album", "test track"))):
        sc.apply({"type": "sync", "source": "rkbx", key: value}, None,
                 at(t + i * 0.002))
    grid = sc.show_library.grids["synth-128"]
    for i in range(int(3.0 * 60)):
        pos = 40.0 + i / 60
        sc.apply({"type": "sync", "source": "rkbx", "track_time": pos,
                  "bpm": 128.0, "bpm_original": 128.0}, None, at(t + 0.2 + i / 60))
        phase = (grid.beat_at(pos) + 0.5) % 4
        sc.apply({"type": "sync", "source": "rkbx", "beat_in_bar": phase},
                 None, at(t + 0.2 + i / 60 + 0.001))
    t += 3.3
    sc._track_frame(at(t))
    warning = track_now()["grid_warning"]
    check("rkbx_link's bar phase half a beat off the grid is caught too",
          warning is not None and warning["kind"] == "phase"
          and abs(warning["offset_beats"] - 0.5) < 0.05, f"{warning}")

    # A signature can belong to one track only.
    other = json.loads((shows / "tracks" / "synth-128.json").read_text())
    other.update(id="other", aliases=[])
    other["identity"]["title"] = "Other"
    other["ids"]["blt_signatures"] = ["e" * 40]
    (shows / "tracks" / "other.json").write_text(json.dumps(other), "utf-8")
    sc.apply({"type": "show_reload"}, None, at(t))
    settle()
    check("show_reload reads the folder now", sc.snapshot()["show"]["tracks"] == 2)
    sc.apply({"type": "sync_off"}, None, at(t))
    t += 0.5
    blt_track(t, "Some Copy", "Somebody", "", 200.0, rid=9, sig="e" * 40)
    t = blt_play(t, 0.5)
    try:
        sc.apply({"type": "track_link", "track_id": "synth-128"}, None, at(t))
        refused = False
    except ValueError as exc:
        refused = "already belongs to 'other'" in str(exc)
    check("a signature already linked elsewhere is not linked twice", refused)

    # A broken edit mid-show keeps the last good version.
    (shows / "timelines" / "synth-128.json").write_text("{", "utf-8")
    sc.apply({"type": "show_reload"}, None, at(t))
    settle()
    show = sc.snapshot()["show"]
    check("a timeline broken by a half-finished sync keeps its last good version",
          "synth-128" in sc.show_library.folder.timelines and show["failed"] == 1
          and show["errors"] >= 1, f"{show}")
    check("and the console is told", any("show folder:" in n for n in sc.notices),
          f"{sc.notices[-2:]}")
    check("and the problem is in the snapshot, first",
          "not valid JSON" in show["problems"][0], f"{show['problems'][:1]}")
finally:
    showlibrary.load = real_load
    sc.worker.stop()
    shutil.rmtree(shows_tmp, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("server: all checks pass")
