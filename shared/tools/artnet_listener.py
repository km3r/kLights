"""
Art-Net ArtDmx listener — prints incoming DMX channel values.
Use this to confirm QLC+ (or any Art-Net source) is actually emitting.

Binds to UDP port 6454 on all interfaces (0.0.0.0) and decodes ArtDmx packets.
Works for both unicast and broadcast traffic on the local network.

Usage examples:
  # Print all non-zero channels from any universe
  python artnet_listener.py

  # Show only universe 0 traffic
  python artnet_listener.py -u 0

  # Only print lines when channel values change (quieter output)
  python artnet_listener.py --changed

  # Show the full 512-channel grid update (verbose mode)
  python artnet_listener.py --grid

  # Watch specific channels (e.g. Par 36 #1: ch 1-5)
  python artnet_listener.py --watch 1,2,3,4,5

  # Print ArtTimeCode instead -- what kLights sends a VJ app (milestone 3)
  python artnet_listener.py --timecode
"""

import argparse
import socket
import struct
import time
from datetime import datetime


ARTNET_PORT = 6454
ARTNET_HEADER = b"Art-Net\x00"
ARTNET_OP_DMX = 0x5000
ARTNET_OP_TIMECODE = 0x9700
TIMECODE_FPS = {0: "24", 1: "25", 2: "29.97 DF", 3: "30"}


def decode_universe(sub_uni: int, net: int) -> int:
    """Reconstruct the 15-bit Art-Net universe from SubUni and Net bytes."""
    subnet = (sub_uni >> 4) & 0x0F
    universe = sub_uni & 0x0F
    return (net << 8) | (subnet << 4) | universe


def parse_artdmx(data: bytes) -> tuple[int, bytes] | None:
    """
    Parse an Art-Net ArtDmx packet.
    Returns (universe, dmx_data) or None if not a valid ArtDmx packet.
    """
    if len(data) < 18:
        return None
    if data[:8] != ARTNET_HEADER:
        return None
    opcode = struct.unpack_from("<H", data, 8)[0]
    if opcode != ARTNET_OP_DMX:
        return None
    sub_uni = data[14]
    net = data[15]
    length = struct.unpack_from(">H", data, 16)[0]
    if len(data) < 18 + length:
        return None
    universe = decode_universe(sub_uni, net)
    dmx = data[18 : 18 + length]
    return universe, dmx


def parse_arttimecode(data: bytes) -> tuple[int, int, int, int, int] | None:
    """Parse an ArtTimeCode packet: (hours, minutes, seconds, frames, type),
    or None if it is not one."""
    if len(data) < 19 or data[:8] != ARTNET_HEADER:
        return None
    if struct.unpack_from("<H", data, 8)[0] != ARTNET_OP_TIMECODE:
        return None
    frames, seconds, minutes, hours, kind = data[14:19]
    return hours, minutes, seconds, frames, kind


def format_channels(dmx: bytes, threshold: int, watch: set[int] | None) -> str:
    """Return a compact string of non-zero (or watched) channel=value pairs."""
    pairs = []
    for i, val in enumerate(dmx):
        ch = i + 1
        if watch is not None:
            if ch not in watch:
                continue
            pairs.append(f"ch{ch}={val}")
        elif val >= threshold:
            pairs.append(f"ch{ch}={val}")
    return "  ".join(pairs) if pairs else "(all zero)"


def print_grid(dmx: bytes):
    """Print a 16-column grid of all 512 channel values."""
    cols = 16
    print(f"  {'ch':>4} " + "  ".join(f"{c*cols+1:>5}" for c in range(cols)))
    for row in range(32):
        vals = dmx[row * cols : row * cols + cols]
        row_str = "  ".join(f"{v:>5}" for v in vals)
        print(f"  row {row * cols + 1:>3}: {row_str}")


def main():
    parser = argparse.ArgumentParser(
        description="Art-Net listener — monitor incoming DMX channel values"
    )
    parser.add_argument("-u", "--universe", type=int, default=None,
                        help="Filter to this Art-Net universe only (default: show all)")
    parser.add_argument("--changed", action="store_true",
                        help="Only print output when channel values change")
    parser.add_argument("--threshold", type=int, default=1,
                        help="Minimum channel value to display (default 1, hides zeros)")
    parser.add_argument("--grid", action="store_true",
                        help="Print full 512-channel grid on each packet")
    parser.add_argument("--timecode", action="store_true",
                        help="Print ArtTimeCode packets instead of DMX")
    parser.add_argument("--watch", default=None,
                        help="Comma-separated channel numbers to always show "
                             "(e.g. --watch 1,2,3,4,5)")
    args = parser.parse_args()

    watch_channels: set[int] | None = None
    if args.watch:
        watch_channels = set(int(c.strip()) for c in args.watch.split(","))
        print(f"Watching channels: {sorted(watch_channels)}")

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("0.0.0.0", ARTNET_PORT))
    print(f"Listening on port {ARTNET_PORT}... (Ctrl+C to stop)\n")

    last_dmx: dict[int, bytes] = {}
    packet_counts: dict[int, int] = {}

    try:
        while True:
            raw, addr = sock.recvfrom(600)
            if args.timecode:
                tc = parse_arttimecode(raw)
                if tc is not None:
                    hours, minutes, seconds, frames, kind = tc
                    sep = ";" if kind == 2 else ":"
                    print(f"{hours:02d}:{minutes:02d}:{seconds:02d}{sep}{frames:02d}  "
                          f"{TIMECODE_FPS.get(kind, kind)} fps  src:{addr[0]}")
                continue
            result = parse_artdmx(raw)
            if result is None:
                continue

            universe, dmx = result

            if args.universe is not None and universe != args.universe:
                continue

            packet_counts[universe] = packet_counts.get(universe, 0) + 1

            changed = dmx != last_dmx.get(universe)
            last_dmx[universe] = dmx

            if args.changed and not changed:
                continue

            ts = datetime.now().strftime("%H:%M:%S.%f")[:-3]
            flag = "*" if changed else " "
            ch_str = format_channels(dmx, args.threshold, watch_channels)
            non_zero = sum(1 for v in dmx if v)

            print(
                f"[{ts}]{flag} Uni:{universe}  src:{addr[0]}  "
                f"pkt#{packet_counts[universe]}  "
                f"active:{non_zero:>3}ch  {ch_str}"
            )

            if args.grid and changed:
                print_grid(dmx)

    except KeyboardInterrupt:
        print("\nStopped.")
        if packet_counts:
            print("Packet counts by universe:")
            for uni, count in sorted(packet_counts.items()):
                print(f"  Universe {uni}: {count} packets received")
    finally:
        sock.close()


if __name__ == "__main__":
    main()
