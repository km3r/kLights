"""Art-Net output: the packet, and sending one frame to several places.

Several because a show laptop is usually two destinations at once -- the rig's
node and a previz on the same machine -- and on Windows two listeners on one
machine do not both receive a unicast packet. Each target gets its own copy, so
these checks bind real UDP sockets and look at what actually arrives.

Run: python engine/tests/test_artnet.py
"""

import socket
import struct
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine.output.artnet import ARTNET_PORT, ArtNetOutput, build_artdmx, parse_targets
from engine.output.base import FanOut, NullOutput

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def listener() -> socket.socket:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.bind(("127.0.0.1", 0))
    s.settimeout(2.0)
    return s


print("\n1. the packet")
packet = build_artdmx(0x123, bytes([1, 2, 3]), sequence=7)
check("ArtDmx header", packet[:8] == b"Art-Net\x00" and struct.unpack("<H", packet[8:10])[0] == 0x5000)
check("universe split into SubUni and Net", packet[14] == 0x23 and packet[15] == 0x01, packet[14:16].hex())
check("payload padded to 512", len(packet) == 18 + 512 and packet[18:21] == bytes([1, 2, 3]))
# What every receiver here does (the C++ app's ParseArtDmx, klights_live.py's
# _decode_universe): Net << 8 | SubUni. Universe 16 once went out as universe 0.
decoded = [(lambda p: p[15] << 8 | p[14])(build_artdmx(u, b"")) for u in (0, 1, 15, 16, 255, 256, 0x7FFF)]
check("every universe decodes back to itself", decoded == [0, 1, 15, 16, 255, 256, 0x7FFF], str(decoded))

print("\n2. --artnet targets")
check("a bare host gets the Art-Net port", parse_targets("10.0.0.5") == [("10.0.0.5", ARTNET_PORT)])
check("host:port, comma separated, spaces allowed",
      parse_targets("10.0.0.5, 127.0.0.1:6455") == [("10.0.0.5", ARTNET_PORT), ("127.0.0.1", 6455)])
check("a repeated target is sent once", parse_targets("127.0.0.1,127.0.0.1:6454") == [("127.0.0.1", ARTNET_PORT)])
for bad in ("", " , ", "host:", ":6454", "host:0", "host:65536", "host:port"):
    try:
        parse_targets(bad)
        check(f"{bad!r} is refused", False, "accepted")
    except ValueError:
        check(f"{bad!r} is refused", True)

print("\n3. one frame, every target")
a, b = listener(), listener()
out = ArtNetOutput(f"127.0.0.1:{a.getsockname()[1]},127.0.0.1:{b.getsockname()[1]}")
frame = bytes(range(256)) * 2
out.send(3, frame)
out.send(3, frame)
got_a = [a.recvfrom(1024)[0] for _ in range(2)]
got_b = [b.recvfrom(1024)[0] for _ in range(2)]
check("both targets receive both frames", len(got_a) == len(got_b) == 2)
check("...as identical packets, sequence included", got_a == got_b)
check("sequence advances per frame, not per target", [p[12] for p in got_a] == [1, 2],
      str([p[12] for p in got_a]))
check("...and the data is the frame", got_a[0][18:] == frame)
check("a single target still works the old way", ArtNetOutput("127.0.0.1").targets == [("127.0.0.1", ARTNET_PORT)])
bcast = ArtNetOutput("127.0.0.1,255.255.255.255")
check("broadcast is enabled when any target is a broadcast address",
      bcast.sock.getsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST) != 0)
check("...and not when none is",
      out.sock.getsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST) == 0)
for s in (a, b):
    s.close()
out.close()
bcast.close()

print("\n4. the output interface: a null output, and fanning out")
null = NullOutput()
frame = bytearray([10, 20, 30])
null.send(0, frame)
frame[0] = 99
null.send(1, bytes([1]))
check("NullOutput counts frames and keeps the last per universe",
      null.frames == 2 and set(null.last) == {0, 1})
check("...as a copy, so a buffer the caller reuses cannot rewrite history",
      null.last[0] == bytes([10, 20, 30]))
null.close()


class Broken:
    """An output whose network went away: what a previz on a dropped Wi-Fi is."""

    def __init__(self, exc):
        self.exc, self.sent, self.closed = exc, 0, False

    def send(self, universe, data):
        self.sent += 1
        raise self.exc

    def close(self):
        self.closed = True
        raise self.exc


rig_out, previz, also = NullOutput(), Broken(OSError(101, "Network is unreachable")), NullOutput()
fan = FanOut([rig_out, previz, also])
for n in range(3):
    fan.send(0, bytes([n]))
check("a failing output does not stop the others: every frame reached the rig",
      rig_out.frames == 3 and also.frames == 3 and also.last[0] == bytes([2]))
check("the failure is recorded once, by index, for the UI to show",
      list(fan.failures) == [1] and "OSError" in fan.failures[1], f"{fan.failures}")
check("and the failed output is dropped, not retried every frame", previz.sent == 1)
fan.close()
check("close reaches every output and swallows a failing close", previz.closed)
check("an empty fan-out is a valid output", (FanOut([]).send(0, b"x"), True)[1])
program_bug = FanOut([Broken(ValueError("bad frame"))])
try:
    program_bug.send(0, b"x")
    escaped = False
except ValueError:
    escaped = True
check("only OSError is treated as an output dropping out -- a ValueError is a bug "
      "in the caller and is not hidden", escaped)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("artnet: all checks pass")
