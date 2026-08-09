"""
Tests for the show server.

Driven over a REAL socket against a REAL server -- handshake, framing, masking
and all -- because the parts most likely to be wrong are exactly the parts a
mock would skip. The client below is a deliberately independent implementation
of the framing, so a bug shared between encoder and decoder cannot hide.

Run: python engine/tests/test_server.py
"""

import base64
import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import rig as rigmod
from engine import state as statemod
from engine import websocket as wsmod
from engine import server as servermod
from engine.server import ShowController, ShowServer, load_presets

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


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
controller = ShowController(REPO / "events" / "despacio", fps=40.0)
server = ShowServer(controller, port=0)
server.start()
port = server.httpd.server_address[1]
controller.start()
time.sleep(0.3)

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
    (REPO / "events" / "despacio" / "presets.json").read_text(encoding="utf-8"))
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

# The test writes into the real event directory; leave it as it was found.
presets_path = REPO / "events" / "despacio" / "presets.json"
if presets_path.exists() and not load_presets(presets_path.parent):
    presets_path.unlink()

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
    controller.ctx.set_phase(bar)
    show = controller.director._show
    controller._attach_overrides(show)
    states = statemod.evaluate(controller.ctx, show)
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
client.send({"type": "clear_slot", "slot": "level"})



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
print("\n6. bad input")
before_frames = after["stats"]["frames"]
client.send({"type": "no_such_command"})
client.send({"type": "select_look", "name": "does not exist"})
client.send({"type": "master"})                       # missing value
after = client.wait_for(lambda s: s["stats"]["frames"] > before_frames + 20)
check("the show survives unknown and malformed commands",
      after["stats"]["eval_errors"] == 0 and after["stats"]["drops"] == 0,
      f"errors {after['stats']['eval_errors']}, drops {after['stats']['drops']}")
check("and each failure is reported",
      sum(1 for n in after["notices"] if "failed" in n) >= 2,
      f"{[n for n in after['notices'] if 'failed' in n][:3]}")

# Raw junk that is not even JSON must not kill the connection.
client.sock.sendall(bytes([0x81, 0x83]) + b"\x00\x00\x00\x00" + b"abc")
after = client.wait_for(lambda s: s["stats"]["frames"] > after["stats"]["frames"] + 5)
check("non-JSON text is ignored, connection survives", True)


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
# another show, so this test edits whatever the engine actually loaded -- and
# restores it in the `finally` below, which matters more now that the file is
# not this event's private property.
venue_path = rigmod.venue_path(REPO / "events" / "despacio", json.loads(
    (REPO / "events" / "despacio" / "rig.json").read_text(encoding="utf-8")))
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
    reloaded = ShowController(REPO / "events" / "despacio")
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
cal_before = (REPO / "events" / "despacio" / "calibration.json").read_text(encoding="utf-8")
try:
    controller.apply({"type": "solve", "write": True}, None)
    check("a badly-fitting solve is not written", False, "it wrote")
except ValueError as exc:
    check("a badly-fitting solve is not written", "refusing to write" in str(exc),
          str(exc)[:90])
check("the stored calibration is untouched",
      (REPO / "events" / "despacio" / "calibration.json").read_text(encoding="utf-8")
      == cal_before)
controller.apply({"type": "capture_clear"}, None)
controller.apply({"type": "jog_clear"}, None)


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


client.close()
server.stop()
controller.stop()


# -- 12. access tiers ---------------------------------------------------------
#
# The rig became editable from the UI in F14, so "anyone on the venue wifi can
# do anything" stopped being an acceptable default. The threat is not an
# attacker; it is the guest who opens the URL you showed someone and starts
# pressing things between sets.
print("\n12. access tiers")
guarded = ShowController(REPO / "events" / "despacio")
guarded_server = ShowServer(guarded, port=8788, token="secret123")
guarded.start()
guarded_server.start()
time.sleep(0.3)
try:
    # One client at a time, and closed before the next. Three left connected and
    # unread is what surfaced the broadcast stall fixed in send_all -- worth
    # knowing, but not what this section is testing.
    def tier_of(path):
        c = Client(8788, path=path)
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
    hostile = socket.create_connection(("127.0.0.1", 8788), timeout=5)
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
    idle = Client(8788, path="/ws")
    idle.recv()                        # welcome, then never read again
    idle.sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 2048)
    time.sleep(2.5)                    # ~25 unread snapshots

    live = Client(8788, path="/ws?token=secret123")
    live.recv()
    started = time.time()
    live.send({"type": "master", "value": 0.55})
    seen = live.wait_for(lambda s: abs(s["master"] - 0.55) < 1e-6)
    elapsed = time.time() - started
    check("a stuck client does not stall the broadcast for a live one",
          elapsed < 2.0, f"{elapsed:.2f}s to see a change land")
    check("and the stuck one was dropped, with a reason",
          any("stalls the broadcast" in n for n in seen["notices"])
          or len(seen["presence"]) <= 1,
          f"presence={len(seen['presence'])}, notices={seen['notices'][-1:]}")
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
open_ctl = ShowController(REPO / "events" / "despacio")
open_server = ShowServer(open_ctl, port=8787, token=None)
open_ctl.start()
open_server.start()
time.sleep(0.3)
try:
    c = Client(8787, path="/ws")
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
        shutil.copy(REPO / "events" / "despacio" / name, ev / name)

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

        live.apply({"type": "patch_apply"}, None)
        check("applying it swaps the rig in place",
              len(live.rig.fixtures) == before + 1, f"{len(live.rig.fixtures)}")
        check("and clears the pending flag", not live.pending_patch)
        check("the context sees the new rig too",
              live.ctx.rig is live.rig and len(live.ctx.rig.fixtures) == before + 1)
        check("intensity is seeded dark so the safety slew fades it in, "
              "rather than snapping",
              set(live.ctx._taper_prev.values()) == {0.0},
              f"{sorted(set(live.ctx._taper_prev.values()))}")

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

# -- 15. the DJ tempo seam, end to end ----------------------------------------
#
# A datagram in, a moved show clock out, with no CDJs in the room. Proving the
# whole downstream path at the desk is the point of building the seam before
# the hardware exists.
print("\n15. tempo ingest")
from engine import sync as syncmod          # noqa: E402

djs = ShowController(REPO / "events" / "despacio")
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
finally:
    djs.stop()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("server: all checks pass")
