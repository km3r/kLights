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
    sub_uni = ((universe >> 4) & 0xF0) | (universe & 0x0F)
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


class ArtNetOutput:
    """UDP sender, one socket for all universes.

    Sequence numbers are per universe and wrap 1..255 (0 means "sequencing
    disabled" in the spec, so it is skipped). Nodes use them to drop packets
    that arrive out of order, which UDP permits and which shows up as a head
    twitching back a frame.
    """

    def __init__(self, target: str = "255.255.255.255", port: int = ARTNET_PORT,
                 bind: str = "0.0.0.0") -> None:
        self.target = target
        self.port = port
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        if target.endswith(".255") or target == "<broadcast>":
            self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock.bind((bind, 0))
        self._sequence: dict[int, int] = {}

    def send(self, universe: int, frame: bytes) -> None:
        seq = self._sequence.get(universe, 0) % 255 + 1
        self._sequence[universe] = seq
        self.sock.sendto(build_artdmx(universe, frame, seq), (self.target, self.port))

    def close(self) -> None:
        self.sock.close()
