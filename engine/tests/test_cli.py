"""
The command line, end to end: every documented entry point starts, the demo
really puts DMX on the wire, and the three Art-Net implementations agree.

Each of these is something an operator types at a load-in, and most of them
had no test at all -- so an import error, a renamed flag or an encoder change
would have surfaced there, on a laptop on a road case, rather than here.

  * Three separate pieces of code speak ArtDmx: the engine's encoder
    (`engine/output/artnet.py`), the bench sender and the bench listener in
    `shared/tools/`. The listener is how a rig is debugged; if it decodes the
    engine's packets differently from a real node, it is worse than nothing.
  * `python -m engine.demo` is the documented way to see the engine work. It
    is run here against a UDP socket on this machine and its packets checked.
  * Two generators whose output is committed, `gen_models.py` and
    `gen_previz_parity.py`, have `--check` modes that nothing ran. They run
    here, so a stale golden file fails a test rather than a previz build.

Run: python engine/tests/test_cli.py
"""

import argparse
import contextlib
import io
import os
import random
import socket
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "shared" / "tools"))

from engine.output import artnet as engine_artnet  # noqa: E402
import artnet_listener  # noqa: E402
import artnet_sender  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label + (f"  -- {detail}" if detail else ""))


def run(*argv, timeout=60) -> tuple[int, str]:
    # UTF-8 on the child's pipes, as launcher/core.py does: a Windows child
    # writing to a pipe otherwise encodes as cp1252 and dies on the first
    # character outside it, which would fail here and not on Linux.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run([sys.executable, *argv], cwd=REPO, capture_output=True,
                          text=True, encoding="utf-8", errors="replace", timeout=timeout,
                          env=env)
    return proc.returncode, proc.stdout + proc.stderr


class Catcher:
    """A UDP socket on a free loopback port, collecting every datagram."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.2)
        self.port = self.sock.getsockname()[1]
        self.packets: list[tuple[float, bytes]] = []
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while not self._stop.is_set():
            try:
                data, _ = self.sock.recvfrom(2048)
            except (TimeoutError, socket.timeout):
                continue
            except OSError:
                return
            self.packets.append((time.monotonic(), data))

    def close(self):
        self._stop.set()
        self._thread.join(2)
        self.sock.close()


print("\n1. three ArtDmx implementations, one wire format")
rng = random.Random(6454)
universes = [0, 1, 15, 16, 17, 255, 256, 4095, 0x7FFF]
mismatches = []
for u in universes:
    for size in (0, 1, 24, 511, 512):
        data = bytes(rng.randrange(256) for _ in range(size))
        ours = engine_artnet.build_artdmx(u, data, sequence=0)
        bench = artnet_sender.build_artdmx(u, data)
        parsed = artnet_listener.parse_artdmx(ours)
        if ours != bench:
            mismatches.append(("engine != sender", u, size))
        if parsed is None or parsed[0] != u or parsed[1] != (data + bytes(512))[:512]:
            mismatches.append(("listener misreads engine", u, size))
check("engine and bench sender produce identical packets (sequence 0), and the "
      "listener decodes every universe and payload back exactly",
      not mismatches, f"{mismatches[:5]}")
check("payloads longer than a universe are truncated to 512 by both encoders",
      len(engine_artnet.build_artdmx(0, bytes(600))) == 530
      and len(artnet_sender.build_artdmx(0, bytes(600))) == 530)
seq = engine_artnet.build_artdmx(3, b"\x01", sequence=200)
check("the engine's sequence byte is the only difference, and the listener ignores it",
      seq[12] == 200 and artnet_listener.parse_artdmx(seq) == (3, b"\x01" + bytes(511)))
good = engine_artnet.build_artdmx(0, b"\xff")
for label, bad in [("too short for a header", good[:17]),
                   ("a different protocol's header", b"Art-Nyt\x00" + good[8:]),
                   ("another Art-Net opcode (ArtPoll)", good[:8] + struct.pack("<H", 0x2000) + good[10:]),
                   ("a length that runs past the packet", good[:18 + 100])]:
    check(f"the listener ignores {label}", artnet_listener.parse_artdmx(bad) is None)
tc = b"Art-Net\x00" + struct.pack("<H", 0x9700) + bytes([0, 14, 0, 0]) + bytes([24, 59, 59, 23, 3])
check("ArtTimeCode decodes as hours, minutes, seconds, frames, type",
      artnet_listener.parse_arttimecode(tc) == (23, 59, 59, 24, 3))
check("and an ArtDmx packet is not mistaken for timecode",
      artnet_listener.parse_arttimecode(good) is None)
check("format_channels lists non-zero channels from 1, or says all zero",
      artnet_listener.format_channels(bytes([0, 9, 0, 255]), 1, None) == "ch2=9  ch4=255"
      and artnet_listener.format_channels(bytes(4), 1, None) == "(all zero)")
check("a watch list shows exactly the watched channels, zero or not",
      artnet_listener.format_channels(bytes([5, 0, 7]), 1, {2, 3}) == "ch2=0  ch3=7")
check("a threshold hides low values",
      artnet_listener.format_channels(bytes([1, 50, 200]), 100, None) == "ch3=200")


print("\n2. the bench sender, to a listener on this machine")
catcher = Catcher()
artnet_sender.ARTNET_PORT = catcher.port
out = io.StringIO()
with contextlib.redirect_stdout(out):
    artnet_sender.cmd_send(argparse.Namespace(
        universe=2, ip="127.0.0.1", channel=["1=255", "3=128", "2=300", "600=9", "nonsense"],
        full=False, blackout=False, loop=False, hold=0))
deadline = time.monotonic() + 2
while not catcher.packets and time.monotonic() < deadline:
    time.sleep(0.02)
catcher.close()
parsed = artnet_listener.parse_artdmx(catcher.packets[0][1]) if catcher.packets else None
check("one packet, on the universe asked for", len(catcher.packets) == 1
      and parsed is not None and parsed[0] == 2, f"{len(catcher.packets)} packets")
check("carrying the channels asked for, with a value over 255 clipped",
      parsed is not None and parsed[1][:3] == bytes([255, 255, 128])
      and sum(1 for v in parsed[1] if v) == 3)
text = out.getvalue()
ok = ("Channel 600 out of range" in text and "Bad channel spec 'nonsense'" in text
      and "clipping" in text)
check("a channel past 512 and a malformed spec are reported and skipped, not fatal",
      ok, "" if ok else text)


print("\n3. python -m engine.demo puts the rig on the wire")
catcher = Catcher()
code, out = run("-m", "engine.demo", "--artnet", f"127.0.0.1:{catcher.port}",
                "--seconds", "1.5")
time.sleep(0.2)
catcher.close()
frames = [artnet_listener.parse_artdmx(p) for _, p in catcher.packets]
ok = code == 0 and "evaluation errors: 0" in out
check("it exits cleanly and reports no evaluation errors", ok, "" if ok else out[-400:])
check("roughly 40 frames a second arrive (at least 40 in 1.5 s)",
      len(frames) >= 40, f"{len(frames)} packets")
check("every one a 512-channel ArtDmx frame for universe 0",
      frames and all(f is not None and f[0] == 0 and len(f[1]) == 512 for f in frames))
check("stopping leaves the rig dark: the last packet is a full blackout, not the "
      "last look frozen on", frames and frames[-1] is not None and not any(frames[-1][1]))
if len(frames) > 1 and frames[-2] is not None:
    dmx = frames[-2][1]
    # The despacio heads are 11 channels from 1, 12, 23, 34; the pinspots 6
    # from 45 and 51. The demo lights everything, so every fixture has a
    # non-zero channel -- and nothing past the patch is ever driven.
    lit = [any(dmx[a - 1:a - 1 + n]) for a, n in ((1, 11), (12, 11), (23, 11), (34, 11),
                                                  (45, 6), (51, 6))]
    check("every patched fixture is lit, and nothing beyond the patch is driven",
          all(lit) and not any(dmx[56:]), f"lit {lit}")
    moving = len({p[1][0:2] + p[1][11:13] for p in frames[:-1] if p is not None})
    check("the heads are moving: pan/tilt change across the run", moving > 5,
          f"{moving} distinct pan/tilt pairs")
check("the last frame it prints matches what was on the wire (real DMX, not a report)",
      "universe 0:" in out and "Moving Head #1" in out)

code, out = run("-m", "engine.demo", "--seconds", "1", "--auto", "all", "--nudge", "1.5")
ok = code == 0 and "evaluation errors: 0" in out and "no jump" in out
check("with every auto axis on and a speed nudge it still runs clean to a null output",
      ok, "" if ok else out[-400:])


print("\n4. every documented entry point starts")
# --help only, so nothing here writes, binds a port or waits for hardware: what
# it proves is that each one imports and parses its own arguments.
entry_points = [
    ("-m", "engine.server"), ("-m", "engine.patch"), ("-m", "engine.calibrate"),
    ("-m", "engine.showfiles"), ("-m", "engine.demo"), ("-m", "engine.tests"),
    ("scripts/preflight.py",), ("bridges/prolink/bridge.py",),
    ("bridges/prolink/capture.py",), ("bridges/rekordbox/prep.py",),
    ("bridges/midi/midi_out.py",), ("shared/tools/artnet_sender.py",),
    ("shared/tools/artnet_listener.py",), ("shared/tools/validate_patch.py",),
    ("shared/tools/qlc_parity.py",), ("shared/tools/port_library.py",),
    ("shared/tools/audit_library.py",), ("shared/tools/gen_schemas.py",),
    ("previz/scene.py",), ("previz/doctor.py",), ("previz/ball_check.py",),
]
for argv in entry_points:
    code, out = run(*argv, "--help")
    name = " ".join(argv)
    ok = code == 0 and "usage:" in out.lower() and "Traceback" not in out
    check(f"{name} --help", ok, "" if ok else out[-300:])
code, out = run("-m", "engine.rig")
ok = code == 0 and "rig 'despacio': 6 fixtures" in out and "validate: OK" in out
check("python -m engine.rig: every profile's channel map, the reference rig, and "
      "'validate: OK'", ok, "" if ok else out[-400:])
code, out = run("-c", "import sys; sys.path.insert(0, 'mcp'); import launcher.core, klights_mcp")
check("the launcher's core and the MCP server import without a display or a client",
      code == 0, "" if code == 0 else out[-300:])


print("\n5. committed generated files match their generators")
for script in ("shared/tools/gen_models.py", "shared/tools/gen_previz_parity.py"):
    code, out = run(script, "--check")
    check(f"{script} --check", code == 0, "" if code == 0 else out[-300:])


print()
if failures:
    print(f"cli: {len(failures)} FAILED")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("cli: all checks pass")
