"""
The MIDI sidecar: kLights' MIDI cues, out of a real MIDI port.

The engine is stdlib-only, and its output thread must never wait on a driver,
so it never touches MIDI itself. Each frame it sends that frame's MIDI
messages here as one small JSON datagram over local UDP; this process owns the
port. If it is not running, the engine's datagrams go nowhere and the lights
carry on.

    pip install -r bridges/midi/requirements.txt
    python bridges/midi/midi_out.py --list                 # the MIDI outputs here
    python bridges/midi/midi_out.py --midi "loopMIDI"      # the first whose name
                                                           # contains this
    python bridges/midi/midi_out.py --virtual kLights      # macOS / Linux: a port
                                                           # other software opens
    python bridges/midi/midi_out.py --fake                 # print them instead;
                                                           # needs no MIDI library

Then tell the engine, in show.json or klights.local.json:

    "outputs": {"midi": {}}                                # this machine, port 9123

**The wire** (`klights.midi/1`): one JSON object per datagram,

    {"klights": "klights.midi/1", "messages": [
        {"type": "note_on", "channel": 1, "note": 60, "velocity": 100},
        {"type": "note_off", "channel": 1, "note": 60, "velocity": 0},
        {"type": "control_change", "channel": 2, "control": 7, "value": 127},
        {"type": "program_change", "channel": 1, "program": 3}]}

Channels are 1-16 here, as on every piece of gear; the 0-15 of the wire
protocol is this file's business. Anything else -- another shape, a number out
of range, true where a number belongs -- rejects the whole datagram, which is
counted and said, never half-played.

It listens on 127.0.0.1 by default: the engine is on this machine. `--bind`
can open it to the network, but nothing authenticates a datagram, so anyone
who can reach the port can then play notes.

On the way out (Ctrl-C) every note it started is stopped: a stuck note on a
synth outlives the program that started it.
"""

from __future__ import annotations

import argparse
import json
import signal
import socket
import sys
from typing import Any, Optional

WIRE = "klights.midi/1"
DEFAULT_PORT = 9123
MAX_MESSAGES = 256

# type -> the fields it must have, each 0-127 (velocity of a note_on 1-127 is
# not enforced: a note_on at velocity 0 is a note_off by MIDI's own rule).
FIELDS = {
    "note_on": ("note", "velocity"),
    "note_off": ("note", "velocity"),
    "control_change": ("control", "value"),
    "program_change": ("program",),
}


def _byte(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= 127


def decode(data: bytes) -> tuple[Optional[list[dict]], Optional[str]]:
    """A datagram's messages, or (None, why not). All or nothing."""
    try:
        doc = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError):
        return None, "not JSON"
    if not isinstance(doc, dict) or doc.get("klights") != WIRE:
        return None, f"not a {WIRE} datagram"
    messages = doc.get("messages")
    if not isinstance(messages, list) or len(messages) > MAX_MESSAGES:
        return None, f"messages must be a list of at most {MAX_MESSAGES}"
    out = []
    for n, m in enumerate(messages):
        if not isinstance(m, dict) or m.get("type") not in FIELDS:
            return None, f"message {n}: type must be one of {', '.join(FIELDS)}"
        ch = m.get("channel")
        if not (isinstance(ch, int) and not isinstance(ch, bool) and 1 <= ch <= 16):
            return None, f"message {n}: channel must be 1-16, got {ch!r}"
        clean = {"type": m["type"], "channel": ch}
        for field in FIELDS[m["type"]]:
            if not _byte(m.get(field)):
                return None, f"message {n}: {field} must be 0-127, got {m.get(field)!r}"
            clean[field] = m[field]
        out.append(clean)
    return out, None


def describe(m: dict) -> str:
    rest = " ".join(f"{k}={v}" for k, v in m.items() if k not in ("type", "channel"))
    return f"{m['type']} ch{m['channel']} {rest}"


class FakePort:
    """Prints what it would send. For tests, and for checking a show's MIDI
    lanes on a machine with no MIDI at all."""
    name = "fake"

    def send(self, m: dict) -> None:
        print(describe(m), flush=True)

    def close(self) -> None:
        pass


class MidoPort:
    """A real MIDI output, through mido and python-rtmidi."""

    def __init__(self, name: Optional[str], virtual: Optional[str]):
        import mido                                  # only when it is wanted
        self.mido = mido
        if virtual:
            self.port = mido.open_output(virtual, virtual=True)
        else:
            names = mido.get_output_names()
            match = [n for n in names if name and name.lower() in n.lower()]
            if not match:
                raise SystemExit(f"no MIDI output matches {name!r}; there are: "
                                 + (", ".join(names) or "none"))
            self.port = mido.open_output(match[0])
        self.name = self.port.name

    def send(self, m: dict) -> None:
        fields = {k: v for k, v in m.items() if k not in ("type", "channel")}
        self.port.send(self.mido.Message(m["type"], channel=m["channel"] - 1, **fields))

    def close(self) -> None:
        self.port.close()


class Sidecar:
    """Datagrams in, messages out, and which notes are sounding."""

    def __init__(self, port_out):
        self.out = port_out
        self.sounding: set[tuple[int, int]] = set()
        self.played = 0
        self.rejected = 0
        self.last_reject: Optional[str] = None

    def handle(self, data: bytes) -> None:
        messages, why = decode(data)
        if messages is None:
            self.rejected += 1
            if why != self.last_reject:          # say each new reason once
                print(f"rejected a datagram: {why}", file=sys.stderr, flush=True)
            self.last_reject = why
            return
        for m in messages:
            note = (m["channel"], m.get("note"))
            if m["type"] == "note_on" and m["velocity"] > 0:
                self.sounding.add(note)
            elif m["type"] in ("note_on", "note_off"):
                self.sounding.discard(note)
            self.out.send(m)
            self.played += 1

    def panic(self) -> None:
        """Stop every note this process started."""
        for ch, note in sorted(self.sounding):
            self.out.send({"type": "note_off", "channel": ch, "note": note,
                           "velocity": 0})
        self.sounding.clear()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="kLights MIDI sidecar")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT,
                        help=f"UDP port to listen on (default {DEFAULT_PORT}; "
                             f"0 picks a free one and says which)")
    parser.add_argument("--bind", default="127.0.0.1",
                        help="address to listen on (default 127.0.0.1, this "
                             "machine only)")
    out = parser.add_mutually_exclusive_group()
    out.add_argument("--midi", help="the MIDI output whose name contains this")
    out.add_argument("--virtual", metavar="NAME",
                     help="open a virtual output (macOS, Linux)")
    out.add_argument("--fake", action="store_true",
                     help="print the messages instead of sending them")
    out.add_argument("--list", action="store_true", help="list MIDI outputs and exit")
    args = parser.parse_args(argv)

    if args.list:
        try:
            import mido
        except ImportError:
            print("mido is not installed: pip install -r bridges/midi/requirements.txt",
                  file=sys.stderr)
            return 1
        try:
            names = mido.get_output_names()
        except Exception as exc:                    # noqa: BLE001 -- the driver's own
            print(f"MIDI is not available on this machine: {exc}", file=sys.stderr)
            return 1
        for name in names:
            print(name)
        return 0
    if not (args.fake or args.midi or args.virtual):
        parser.error("say where to send: --midi NAME, --virtual NAME or --fake")
    try:
        port_out = FakePort() if args.fake else MidoPort(args.midi, args.virtual)
    except ImportError:
        print("mido is not installed: pip install -r bridges/midi/requirements.txt",
              file=sys.stderr)
        return 1
    except SystemExit:
        raise
    except Exception as exc:                        # noqa: BLE001 -- the driver's own
        print(f"could not open the MIDI output: {exc}", file=sys.stderr)
        return 1

    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((args.bind, args.port))
    sock.settimeout(0.5)                  # so Ctrl-C is heard on Windows too
    host, port = sock.getsockname()
    print(f"listening on {host}:{port}, playing to {port_out.name}", flush=True)
    if args.bind not in ("127.0.0.1", "localhost"):
        print("warning: listening beyond this machine -- anyone who can reach "
              "the port can play notes", file=sys.stderr, flush=True)
    sidecar = Sidecar(port_out)

    def stop(*_):                         # a service manager's stop: as Ctrl-C
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    try:
        while True:
            try:
                data, _ = sock.recvfrom(65536)
            except socket.timeout:
                continue
            sidecar.handle(data)
    except KeyboardInterrupt:
        pass
    finally:
        sidecar.panic()
        port_out.close()
        sock.close()
        print(f"stopped: {sidecar.played} played, {sidecar.rejected} rejected",
              flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
