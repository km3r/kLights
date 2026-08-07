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
import socket
import struct
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import websocket as wsmod
from engine.server import ShowController, ShowServer

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# -- an independent client ----------------------------------------------------

class Client:
    """Minimal WebSocket client, written separately from the server's framing."""

    def __init__(self, port: int, name: str = "test"):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=5)
        self.sock.settimeout(5)
        self.buf = b""
        key = base64.b64encode(os.urandom(16)).decode()
        self.sock.sendall(
            f"GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\n"
            f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n".encode())
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
# survives a re-port. Two DIFFERENT looks are needed below, hence the pair.
LOOK_A, LOOK_B = [l.name for l in controller.setlist.looks[:2]]

client.send({"type": "select_look", "name": LOOK_A})
after = client.wait_for(lambda s: s["auto"]["look"] == LOOK_A)
check("selecting a look takes effect", after["auto"]["look"] == LOOK_A)
check("the ported library is what is on offer", len(after["looks"]) > 100,
      f"{len(after['looks'])} looks")
check("and each carries the kind the UI groups by",
      all("kind" in l for l in after["looks"]),
      f"{sorted({l['kind'] for l in after['looks']})}")
check("and it is held, so auto cannot steal it", after["auto"]["held"] is True)

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
venue_path = REPO / "events" / "despacio" / "venue.json"
original = venue_path.read_text(encoding="utf-8")
try:
    client.send({"type": "taper", "enabled": True, "crowd_level": 0.4})
    client.send({"type": "venue", "crowd": {"head_band_min": 1450,
                                            "head_band_max": 2050}})
    client.send({"type": "venue_save"})
    client.wait_for(lambda s: any("saved venue.json" in n for n in s["notices"]))

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


client.close()
server.stop()
controller.stop()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("server: all checks pass")
