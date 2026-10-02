"""Record what a DJ source actually sends, so it can be replayed at a desk.

    python bridges/prolink/capture.py --listen 9001 --forward 127.0.0.1:9000 --out venue.jsonl
    python bridges/prolink/bridge.py --replay venue.jsonl --port 9000

Point rkbx_link or beat-link-trigger at `--listen` instead of the engine, and
`--forward` passes every datagram straight on, so the show keeps running while
it records. Each line of the output is one datagram, base64, with the gap to the
next one in `dt` -- the format `bridge.py --replay` already reads, so a capture
from a venue becomes a regression test six weeks later.

It records the bytes, not a decoding of them. The point of a capture is to find
out what the source really sends -- message order during a master switch,
whether rkbx_link really goes silent on pause, the exact phrase strings -- and a
capture that went through our decoder first would only show what we already
believed.

Stdlib only, like everything that might end up on the show machine in a hurry.
"""

from __future__ import annotations

import argparse
import base64
import json
import socket
import sys
import time
from pathlib import Path
from typing import Optional


def record(listen: int, out: Path, forward: Optional[tuple[str, int]] = None,
           bind: str = "127.0.0.1", limit: Optional[int] = None,
           seconds: Optional[float] = None) -> int:
    """Record datagrams until Ctrl-C, `limit` packets, or `seconds` pass.

    Each line is written when the NEXT packet arrives, because `dt` is the gap
    after a packet -- the delay `--replay` waits before sending the next."""
    count = 0
    held: Optional[tuple[float, bytes]] = None
    started = time.monotonic()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock, \
            socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as fwd, \
            open(out, "w", encoding="utf-8", newline="\n") as fh:
        sock.bind((bind, listen))
        sock.settimeout(0.25)
        fh.write(f"# captured by capture.py on port {listen}, "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n")
        try:
            while True:
                if seconds is not None and time.monotonic() - started >= seconds:
                    break
                try:
                    data, _ = sock.recvfrom(4096)
                except socket.timeout:
                    continue
                now = time.monotonic()
                if forward is not None:
                    fwd.sendto(data, forward)
                if held is not None:
                    _write(fh, now - held[0], held[1])
                held = (now, data)
                count += 1
                if limit is not None and count >= limit:
                    break
        except KeyboardInterrupt:
            pass
        if held is not None:
            _write(fh, 0.0, held[1])
    return count


def _write(fh, dt: float, data: bytes) -> None:
    line = {"dt": round(dt, 6), "raw": base64.b64encode(data).decode("ascii")}
    # A readable hint beside the bytes, for a person reading the file. Replay
    # ignores it; the bytes are what is sent.
    if data.startswith(b"/"):
        line["address"] = data.split(b"\0", 1)[0].decode("ascii", "replace")
    fh.write(json.dumps(line) + "\n")
    fh.flush()


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--listen", type=int, required=True,
                        help="the port to point the DJ source at")
    parser.add_argument("--bind", default="127.0.0.1")
    parser.add_argument("--forward", metavar="HOST:PORT",
                        help="pass every datagram on, e.g. to the engine's "
                             "--sync-port, so the show keeps running")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seconds", type=float,
                        help="stop after this long (default: Ctrl-C)")
    parser.add_argument("--packets", type=int, help="stop after this many")
    args = parser.parse_args(argv)
    forward = None
    if args.forward:
        host, _, port = args.forward.rpartition(":")
        if not host or not port.isdigit():
            print(f"--forward wants HOST:PORT, got {args.forward!r}", file=sys.stderr)
            return 2
        forward = (host, int(port))
    print(f"recording port {args.listen} -> {args.out}"
          + (f", forwarding to {args.forward}" if forward else "")
          + "  (Ctrl-C to stop)")
    n = record(args.listen, args.out, forward, args.bind, args.packets, args.seconds)
    print(f"recorded {n} datagram(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
