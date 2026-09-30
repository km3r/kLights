"""Feed the engine tempo, bar phase and phrase -- with or without CDJs.

    python bridges/prolink/bridge.py --fake
    python bridges/prolink/bridge.py --replay captures/warehouse.jsonl
    python bridges/prolink/bridge.py --live          # not built yet; see README

A SIDECAR. It talks to the engine over UDP and shares no code path with it, so
the real Pro DJ Link implementation can arrive as whatever it needs to be -- a
Java app, a pip-installed Python package, someone else's program entirely --
without the show laptop growing a dependency. The engine's whole side of this
is `engine/sync.py`, and it never learns what a CDJ is.

`--fake` and `--replay` are why this exists NOW rather than when the hardware
does. The entire downstream path -- clock, cue list, phrase-driven changes,
the console's sync row -- is provable at a desk with no players in the room, and
the thing that will actually be wrong on the night is a network setting, not the
part nobody could test.

This file is stdlib-only, like everything that might end up on the show machine
in a hurry. The real `--live` mode will not be, which is exactly why it lives
here behind a process boundary.
"""

from __future__ import annotations

import argparse
import json
import socket
import struct
import sys
import time
from pathlib import Path
from typing import Iterator, Optional

# The shape of a track, in the vocabulary rekordbox's phrase analysis actually
# uses: it says Up, Chorus and Down, never Build or Drop, and it numbers repeats
# ("Verse 1", "Up 2"). An earlier version of this list used Build/Drop, which
# no rekordbox export will ever send -- so anything keyed on those names would
# have passed here and done nothing at a venue. Not a guess at any particular
# track: it is a scripted timeline whose job is to make every downstream branch
# happen -- an Up that arms, a Chorus that fires, an Outro that releases -- in
# three minutes rather than an hour. Units are bars; 96 bars at 128 BPM is 3:00.
FAKE_PHRASES = [
    ("Intro", 16), ("Verse 1", 16), ("Up 1", 8), ("Chorus", 16),
    ("Down", 8), ("Up 2", 8), ("Chorus", 16), ("Outro", 8),
]


def osc_message(address: str, value) -> bytes:
    """One OSC message. Encoded here rather than imported from the engine so the
    bridge stays a genuinely separate program -- if this file and the engine
    ever disagree about the wire format, that is a bug worth catching."""
    def pad(raw: bytes) -> bytes:
        return raw + b"\0" * (4 - len(raw) % 4)
    if isinstance(value, str):
        return pad(address.encode()) + pad(b",s") + pad(value.encode())
    return pad(address.encode()) + pad(b",f") + struct.pack(">f", float(value))


def as_osc(fields: dict) -> list[bytes]:
    """A field set as the OSC rkbx_link would have sent for it.

    So `--fake --osc` exercises the engine's OSC decoder, the deck filter and
    the subdiv conversion -- the parts that only the rekordbox path uses, and
    that would otherwise go untested until someone had a DDJ, rekordbox and a
    licensed copy of rkbx_link in one room.
    """
    out = []
    if "bpm" in fields:
        out.append(osc_message("/master/bpm/current", fields["bpm"]))
    if "beat_in_bar" in fields:
        # rkbx_link sends a 0..1 ramp looping every n beats, not a beat number.
        out.append(osc_message("/master/beat/subdiv/4",
                               float(fields["beat_in_bar"]) / 4.0))
    if "phrase_label" in fields:
        out.append(osc_message("/master/phrase/current", fields["phrase_label"]))
    if "phrase_ends_in" in fields:
        out.append(osc_message("/master/phrase/countin",
                               fields["phrase_ends_in"]))
    if "track" in fields:
        out.append(osc_message("/master/track/title", fields["track"]))
    return out


def emit(sock: socket.socket, host: str, port: int, fields: dict,
         verbose: bool, use_osc: bool = False) -> None:
    if use_osc:
        for message in as_osc(fields):
            sock.sendto(message, (host, port))
    else:
        sock.sendto(json.dumps(fields).encode("utf-8"), (host, port))
    if verbose:
        print(f"  -> {'osc  ' if use_osc else ''}{json.dumps(fields)}",
              flush=True)


def fake(bpm: float, beats_per_bar: int = 4) -> Iterator[tuple[float, dict]]:
    """A synthetic feed: one packet per beat, forever.

    Sends `beat_in_bar` and never absolute `beat`, which is the discipline a
    real per-beat source needs -- see `MasterClock.sync`. A generator that
    cheated here would prove the seam works in a way the real bridge cannot
    reproduce, which is worse than not testing it.
    """
    beat = 0
    phrase_at = 0
    while True:
        label, length = FAKE_PHRASES[phrase_at % len(FAKE_PHRASES)]
        start = beat
        for offset in range(length * beats_per_bar):
            fields = {
                "bpm": bpm,
                "beat_in_bar": (beat % beats_per_bar),
                "source": "fake",
                "phrase_measured": True,
                "deck": "1",
                "track": "synthetic 128",
            }
            # The label and the countdown ride on the DOWNBEAT of each bar only.
            # Re-sending a countdown every beat would work and would also hide a
            # real bug: the engine stores an absolute end beat precisely so a
            # sparse sender is enough, and a chatty test would never exercise
            # that.
            if beat % beats_per_bar == 0:
                fields["phrase_label"] = label
                fields["phrase_ends_in"] = (
                    (start + length * beats_per_bar) - beat)
            yield 60.0 / bpm, fields
            beat += 1
            del offset
        phrase_at += 1


def replay(path: Path) -> Iterator[tuple[float, dict]]:
    """A captured session: one JSON object per line, each with `dt` seconds.

    The format is deliberately the dullest thing that works, because the point
    is that a capture taken at a venue on a phone-tethered laptop can be replayed
    at a desk six weeks later. Anything needing a parser is a capture nobody
    takes.
    """
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            packet = json.loads(line)
        except json.JSONDecodeError as exc:
            print(f"  skipped a bad line: {exc}", file=sys.stderr)
            continue
        dt = float(packet.pop("dt", 0.5))
        yield dt, packet


def run(source: Iterator[tuple[float, dict]], host: str, port: int,
        verbose: bool, limit: Optional[int] = None,
        use_osc: bool = False) -> int:
    sent = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for dt, fields in source:
            emit(sock, host, port, fields, verbose, use_osc)
            sent += 1
            if limit is not None and sent >= limit:
                return sent
            time.sleep(dt)
    return sent


def main(argv: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--fake", action="store_true",
                      help="synthetic feed with a scripted phrase timeline")
    mode.add_argument("--replay", type=Path, metavar="FILE",
                      help="replay a captured JSONL session")
    mode.add_argument("--live", action="store_true",
                      help="real Pro DJ Link (not implemented -- see README)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=9000,
                        help="the engine's --sync-port")
    parser.add_argument("--bpm", type=float, default=128.0,
                        help="tempo for --fake")
    parser.add_argument("--beats", type=int, metavar="N",
                        help="stop after N packets. For tests and for checking "
                             "a venue's network without leaving a feed running")
    parser.add_argument("--osc", action="store_true",
                        help="send rkbx_link-shaped OSC instead of JSON, to "
                             "exercise the rekordbox path's decoder")
    parser.add_argument("-q", "--quiet", action="store_true")
    args = parser.parse_args(argv)

    if args.live:
        # An honest refusal beats a stub that half-works. The README says what
        # this needs and why it is not stdlib.
        print("--live is not implemented. Two routes, both documented in\n"
              "bridges/prolink/README.md:\n"
              "  1. beat-link-trigger, pointed at this engine's --sync-port.\n"
              "     It is the reference implementation and already has phrase\n"
              "     triggers; the engine speaks its OSC directly.\n"
              "  2. a Python sidecar on python-prodj-link plus a Kaitai-\n"
              "     generated ANLZ parser. One process, no JVM, but we would\n"
              "     own the track-identification and PSSI edges.\n"
              "Prove the path with --fake first; it exercises everything\n"
              "downstream of this file.", file=sys.stderr)
        return 2

    if args.replay:
        if not args.replay.is_file():
            print(f"no such capture: {args.replay}", file=sys.stderr)
            return 2
        source = replay(args.replay)
        what = f"replaying {args.replay.name}"
    else:
        source = fake(args.bpm)
        what = f"faking {args.bpm:g} bpm, phrases: " + " ".join(
            f"{n}x{b}" for n, b in FAKE_PHRASES)

    print(f"{what}\n  -> {args.host}:{args.port}  (Ctrl-C to stop)")
    try:
        sent = run(source, args.host, args.port, not args.quiet, args.beats,
                   use_osc=args.osc)
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0
    print(f"sent {sent} packet(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
