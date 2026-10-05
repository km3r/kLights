"""
Art-Net (ArtDmx) output.

Packet layout follows the Art-Net 4 spec and matches `shared/tools/artnet_sender.py`
byte for byte, so `shared/tools/artnet_listener.py` decodes engine frames with no
changes -- which is what makes frame-level parity diffing against QLC+ possible
without writing any new tooling.

Universes are 0-indexed here and throughout the engine. QLC+ numbers its own
universes from 1, so QLC+ "Universe 1" is Art-Net universe 0; the patch sheets
carry both columns for exactly this reason.
"""

from __future__ import annotations

import socket
import struct

ARTNET_PORT = 6454
ARTNET_OP_DMX = 0x5000
ARTNET_OP_TIMECODE = 0x9700
ARTNET_PROT_VER = 14
# ArtTimeCode's Type byte, by frame rate: Film, EBU, drop-frame NTSC, SMPTE.
TIMECODE_TYPES = {24: 0, 25: 1, 29.97: 2, 30: 3}


def build_artdmx(universe: int, data: bytes, sequence: int = 0) -> bytes:
    """One ArtDmx packet. `data` is padded or truncated to 512 channels."""
    payload = bytes(data[:512]).ljust(512, b"\x00")
    # The 15-bit Port-Address is Net:SubNet:Universe, 7:4:4 bits. SubUni is its
    # low byte whole. This was once ((universe >> 4) & 0xF0) | ..., which drops
    # the SubNet: universe 16 went out as universe 0.
    sub_uni = universe & 0xFF
    net = (universe >> 8) & 0x7F
    return (
        b"Art-Net\x00"
        + struct.pack("<H", ARTNET_OP_DMX)      # OpCode, little-endian
        + struct.pack(">H", ARTNET_PROT_VER)    # ProtVer, big-endian
        + bytes([sequence & 0xFF, 0])           # Sequence, Physical
        + bytes([sub_uni, net])
        + struct.pack(">H", 512)                # Length, big-endian
        + payload
    )


def build_arttimecode(frames: int, seconds: int, minutes: int, hours: int,
                      kind: int) -> bytes:
    """One ArtTimeCode packet (Art-Net 4, OpCode 0x9700): 19 bytes. `kind`
    is the Type byte -- 0 Film 24, 1 EBU 25, 2 DF 29.97, 3 SMPTE 30."""
    return (
        b"Art-Net\x00"
        + struct.pack("<H", ARTNET_OP_TIMECODE)  # OpCode, little-endian
        + struct.pack(">H", ARTNET_PROT_VER)    # ProtVer, big-endian
        + bytes([0, 0])                         # Filler1, Filler2 (stream 0)
        + bytes([frames, seconds, minutes, hours, kind])
    )


def parse_targets(spec: str, default_port: int = ARTNET_PORT) -> list[tuple[str, int]]:
    """`--artnet`'s value: one or more `host[:port]`, comma separated.

    More than one because a show laptop is usually two destinations at once --
    the rig's node and the previz on this same machine. Broadcast reaches both,
    but it reaches everything else on the network too, and on Windows two
    listeners on one machine do NOT both get a unicast packet: only one of them
    does. Sending each its own copy is the way to feed the rig and a previz, or
    two previz listeners on different ports, without flooding the venue LAN.

    Raises ValueError on anything malformed, so a typo stops the engine at
    startup instead of sending a show to nowhere.
    """
    targets: list[tuple[str, int]] = []
    for part in (p.strip() for p in spec.split(",")):
        if not part:
            continue
        host, sep, port_text = part.rpartition(":")
        if not sep:
            host, port = part, default_port
        else:
            if not host or not port_text.isdigit() or not 0 < int(port_text) < 65536:
                raise ValueError(f"Art-Net target {part!r}: expected host or host:port")
            port = int(port_text)
        if (host, port) not in targets:
            targets.append((host, port))
    if not targets:
        raise ValueError(f"no Art-Net target in {spec!r}")
    return targets


class ArtNetOutput:
    """UDP sender, one socket for all universes and every target.

    Sequence numbers are per universe and wrap 1..255 (0 means "sequencing
    disabled" in the spec, so it is skipped). Nodes use them to drop packets
    that arrive out of order, which UDP permits and which shows up as a head
    twitching back a frame. Every target gets the same packet, sequence and
    all: they are copies of one frame, not separate streams.
    """

    def __init__(self, target: str = "255.255.255.255", port: int = ARTNET_PORT,
                 bind: str = "0.0.0.0") -> None:
        self.target = target
        self.targets = parse_targets(target, port)
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if any(host.endswith(".255") or host == "<broadcast>" for host, _ in self.targets):
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind((bind, 0))
        self._sequence: dict[int, int] = {}

    def send(self, universe: int, frame: bytes) -> None:
        seq = self._sequence.get(universe, 0) % 255 + 1
        self._sequence[universe] = seq
        packet = build_artdmx(universe, frame, seq)
        for address in self.targets:
            self.sock.sendto(packet, address)

    def close(self) -> None:
        self.sock.close()
