"""
Art-Net ArtDmx sender — broadcasts DMX values to a universe.
Use this to test BlenderDMX reception independently of QLC+.

Art-Net primer:
  - Protocol: UDP on port 6454
  - Universe 0 = QLC+ Universe 1 (both BlenderDMX and QLC+ use 0-indexed Art-Net)
  - ArtDmx packet: 8-byte header "Art-Net\\0", OpCode 0x5000 (LE),
    ProtVer 14 (BE), Sequence, Physical, SubUni, Net, Length (BE), 512 bytes data
  - SubUni = (subnet << 4) | (universe & 0x0F); Net = (universe >> 8) & 0x7F

Usage examples:
  # Turn all channels off (blackout)
  python artnet_sender.py --blackout

  # Set channel 1 to 255 and channel 3 to 128 on universe 0
  python artnet_sender.py -c 1=255 -c 3=128

  # Set channels 1–5 to full (Par 36 #1 on), hold for 5 seconds
  python artnet_sender.py -c 1=255 -c 2=255 -c 3=255 -c 4=255 -c 5=255 --hold 5

  # Continuously sweep all channels 0->255->0 (catches timing issues)
  python artnet_sender.py --sweep

  # Loop-send current values at 40 Hz until Ctrl+C
  python artnet_sender.py -c 1=255 --loop

  # Target a specific IP instead of broadcast
  python artnet_sender.py -c 1=255 --ip 192.168.1.100

  # Send on universe 1
  python artnet_sender.py -u 1 -c 1=255
"""

import argparse
import socket
import struct
import time


ARTNET_PORT = 6454
ARTNET_HEADER = b"Art-Net\x00"
ARTNET_OP_DMX = 0x5000
ARTNET_PROT_VER = 14


def build_artdmx(universe: int, data: bytes) -> bytes:
    """Build an Art-Net ArtDmx UDP packet for the given universe and DMX data."""
    dmx = (data + b"\x00" * 512)[:512]  # pad/truncate to exactly 512 bytes
    sub_uni = ((universe >> 4) & 0xF0) | (universe & 0x0F)
    net = (universe >> 8) & 0x7F
    return (
        ARTNET_HEADER
        + struct.pack("<H", ARTNET_OP_DMX)    # OpCode little-endian
        + struct.pack(">H", ARTNET_PROT_VER)  # ProtVer big-endian
        + bytes([0, 0])                        # Sequence=0, Physical=0
        + bytes([sub_uni, net])               # Universe addressing
        + struct.pack(">H", 512)              # Length big-endian
        + dmx
    )


def make_socket(ip: str) -> tuple[socket.socket, str]:
    """Create a UDP socket. Returns (socket, resolved_target_ip)."""
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if ip == "broadcast":
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        target = "255.255.255.255"
    else:
        target = ip
    return sock, target


def send_once(sock: socket.socket, target: str, universe: int, dmx: bytearray):
    packet = build_artdmx(universe, bytes(dmx))
    sock.sendto(packet, (target, ARTNET_PORT))


def cmd_send(args):
    dmx = bytearray(512)

    if args.full:
        for i in range(512):
            dmx[i] = 255
    elif args.blackout:
        pass  # already zeros
    else:
        for pair in (args.channel or []):
            try:
                ch_str, val_str = pair.split("=")
                ch = int(ch_str)
                val = int(val_str)
                if not (1 <= ch <= 512):
                    print(f"  Channel {ch} out of range 1-512, skipping")
                    continue
                if not (0 <= val <= 255):
                    print(f"  Value {val} out of range 0-255, clipping")
                    val = max(0, min(255, val))
                dmx[ch - 1] = val
            except ValueError:
                print(f"  Bad channel spec '{pair}' — expected CH=VAL (e.g. 1=255)")
                continue

    active = [(i + 1, v) for i, v in enumerate(dmx) if v]
    print(f"Universe {args.universe} | {len(active)} non-zero channel(s)")
    if active:
        parts = [f"ch{ch}={val}" for ch, val in active[:20]]
        if len(active) > 20:
            parts.append(f"... +{len(active) - 20} more")
        print("  " + "  ".join(parts))

    sock, target = make_socket(args.ip)
    print(f"Sending to {target}:{ARTNET_PORT}", end="")

    if args.loop:
        print(" (loop mode, Ctrl+C to stop)")
        try:
            while True:
                send_once(sock, target, args.universe, dmx)
                time.sleep(1 / 40)  # 40 Hz
        except KeyboardInterrupt:
            print("\nStopped.")
    else:
        send_once(sock, target, args.universe, dmx)
        if args.hold > 0:
            print(f" — holding for {args.hold}s")
            time.sleep(args.hold)
            # Send blackout when hold expires
            send_once(sock, target, args.universe, bytearray(512))
            print("Hold expired — blackout sent.")
        else:
            print(" — sent once.")

    sock.close()


def cmd_sweep(args):
    sock, target = make_socket(args.ip)
    dmx = bytearray(512)
    step_ms = args.step_ms / 1000.0
    print(
        f"Sweeping all 512 channels on universe {args.universe} "
        f"(step {args.step_ms}ms, Ctrl+C to stop)"
    )
    try:
        val = 0
        direction = 1
        while True:
            for i in range(512):
                dmx[i] = val
            send_once(sock, target, args.universe, dmx)
            print(f"\r  All channels = {val:3d}   ", end="", flush=True)
            time.sleep(step_ms)
            val += direction
            if val >= 255:
                direction = -1
            elif val <= 0:
                direction = 1
    except KeyboardInterrupt:
        send_once(sock, target, args.universe, bytearray(512))
        print("\nStopped — blackout sent.")
    finally:
        sock.close()


def main():
    parser = argparse.ArgumentParser(
        description="Art-Net ArtDmx sender for testing DMX reception"
    )
    parser.add_argument("-u", "--universe", type=int, default=0,
                        help="Art-Net universe (0-indexed; default 0 = QLC+ Universe 1)")
    parser.add_argument("--ip", default="broadcast",
                        help="Target IP or 'broadcast' (default: broadcast)")
    parser.add_argument("-c", "--channel", action="append", metavar="CH=VAL",
                        help="Set channel CH (1-indexed) to value VAL (0-255). Repeatable.")
    parser.add_argument("--blackout", action="store_true",
                        help="Send all channels at 0")
    parser.add_argument("--full", action="store_true",
                        help="Send all channels at 255")
    parser.add_argument("--loop", action="store_true",
                        help="Continuously send at 40 Hz until Ctrl+C")
    parser.add_argument("--hold", type=float, default=0,
                        metavar="SECONDS",
                        help="Hold values for N seconds then send blackout")
    parser.add_argument("--sweep", action="store_true",
                        help="Sweep all channels 0→255→0 continuously")
    parser.add_argument("--step-ms", type=int, default=30, dest="step_ms",
                        help="Milliseconds per sweep step (default 30)")

    args = parser.parse_args()

    if args.sweep:
        cmd_sweep(args)
    else:
        cmd_send(args)


if __name__ == "__main__":
    main()
