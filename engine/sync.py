"""Tempo, bar phase and phrase, ingested from something that already knows.

**This module analyses nothing.** No beat tracking, no DSP, no audio anywhere in
the chain. Pro DJ Link has been reverse-engineered thoroughly enough (dysentery,
beat-link, Crate Digger) that beat position and rekordbox's own phrase analysis
are both a READ rather than a derivation -- the players broadcast the beat grid
and the analysis files carry the phrase structure. Approximating either from a
room mic would be worse data, obtained harder.

So the engine's whole part in this is a seam: a `sync` command, and an opt-in
UDP port that speaks it. Everything that knows about Pro DJ Link lives in a
sidecar under `bridges/`, in its own environment, and can be swapped without the
engine noticing. That is what keeps the zero-dependency rule honest -- reading
the CDJ protocol properly needs libraries the show laptop must never depend on.

    python -m engine.server --sync-port 9000

## The security shape of this

An open UDP port that can move the show clock is a write path with no
authentication -- there is no handshake to carry a token, and a datagram cannot
be challenged. Three things follow, and all three are deliberate:

  * It is **off unless asked for**. No port, no listener, no thread.
  * It parses into a FIXED set of clock fields and constructs the command
    itself. It never forwards arbitrary JSON into `apply()`, so this port can
    only ever move the tempo -- it cannot patch a fixture, write a calibration
    or blackout the rig, whatever is sent to it.
  * It is a trusted-LAN facility, same as the rest of the console. `--sync-bind`
    exists so it can be pinned to a loopback or a single interface when the
    bridge runs on the show machine, which is the normal case.

## Two wire formats, because the good bridge speaks the other one

JSON is what our own sidecar sends and what anything hand-written should send.

OSC is what both of the tools worth using emit, and speaking it here is what
turns "point it at this port" into the entire integration for each:

  * **beat-link-trigger** for CDJs -- the reference implementation of Pro DJ
    Link, with phrase triggers already built in;
  * **rkbx_link** for rekordbox and a DDJ controller, which is USB and never
    speaks Pro DJ Link at all. It reads transport position and beatgrid out of
    rekordbox's memory and the phrase structure out of its analysis files.

Two tools, two very different mechanisms, one wire format. The alternative was
writing a shim per tool whose only job was to re-encode a message we were
perfectly capable of reading.
"""

from __future__ import annotations

import json
import socket
import struct
import threading
import time
from typing import Any, Callable, Optional

# Only these ever reach the clock. A field not in here is ignored rather than
# rejected: a bridge that sends extra keys should keep working, and a bridge
# that sends a key we later add should not have needed a coordinated release.
FIELDS = ("bpm", "beat", "beat_in_bar", "phrase_measured", "phrase_label",
          "phrase_ends_in", "source", "deck", "track")

# OSC address suffix -> field, matched LONGEST FIRST.
#
# Two components, not one. The first version of this matched only the last
# component so a bridge could namespace freely -- and rkbx_link, the tool that
# makes the rekordbox path work at all, sends `/master/bpm/current` AND
# `/master/phrase/current`. Both end in "current", so last-component matching
# would have read a phrase label as a tempo. Suffix matching keeps the
# rename-friendly behaviour for flat senders and gets the nested ones right.
OSC_FIELDS = {
    # rkbx_link -- rekordbox, read out of its memory. `/[deck]/...`
    "bpm/current": "bpm",
    "phrase/current": "phrase_label",
    "phrase/countin": "phrase_ends_in",
    "track/title": "track",
    # beat-link-trigger and anything hand-rolled, which are flat
    "bpm": "bpm", "tempo": "bpm",
    "beat": "beat_in_bar", "beat-within-bar": "beat_in_bar",
    "phrase": "phrase_label", "phrase-label": "phrase_label",
    "deck": "deck", "track": "track",
}

# Address components that name a deck. `master` is rkbx_link's "whichever deck
# is currently master", which is the only one this should ever follow: a rig
# taking `/1/bpm` and `/2/bpm` from a DJ mid-blend has two decks fighting over
# one clock, and the resulting tempo belongs to neither of them.
#
# A NUMERIC first component is dropped rather than accepted, so pointing a
# per-deck sender at this port degrades to "ignores everything" -- which is
# visible in the rejected count -- instead of "follows whichever deck spoke
# last", which is invisible and sounds like the engine is broken.
OSC_MASTER = "master"


def parse_osc(data: bytes) -> Optional[dict]:
    """One OSC message as a flat dict, or None if it is not one we understand.

    Deliberately partial. OSC is a big spec and this needs four scalar types off
    a single message -- bundles, blobs, arrays and timetags are things
    beat-link-trigger does not send for these triggers, and guessing at them
    would be inventing behaviour to match no sender.
    """
    if not data.startswith(b"/"):
        return None
    try:
        end = data.index(b"\0")
        address = data[:end].decode("ascii")
        pos = (end + 4) & ~3                      # OSC pads to 4-byte boundaries
        if pos >= len(data) or data[pos] != 0x2C:  # ','
            return None                            # no type tags: nothing to read
        end = data.index(b"\0", pos)
        tags = data[pos + 1:end].decode("ascii")
        pos = (end + 4) & ~3

        args: list[Any] = []
        for tag in tags:
            if tag == "i":
                args.append(struct.unpack_from(">i", data, pos)[0]); pos += 4
            elif tag == "f":
                args.append(struct.unpack_from(">f", data, pos)[0]); pos += 4
            elif tag == "s":
                end = data.index(b"\0", pos)
                args.append(data[pos:end].decode("utf-8", "replace"))
                pos = (end + 4) & ~3
            elif tag in "TF":
                args.append(tag == "T")            # no payload for booleans
            else:
                return None                        # a type we do not model
    except (ValueError, struct.error, UnicodeDecodeError):
        return None

    return osc_fields(address, args[0]) if args else None


def osc_fields(address: str, value: Any) -> Optional[dict]:
    """One OSC address and its first argument, as clock fields.

    Split out from the decoding so the address rules can be tested against a
    literal address string -- which is how they are written down in the tools'
    own documentation, and the form anyone debugging a bridge will be holding.
    """
    parts = [p for p in address.lower().strip("/").split("/") if p]
    if not parts:
        return None
    # Drop a leading deck component. `master` is the one to follow; a number is
    # a specific deck and following it would let two decks fight over the clock.
    if parts[0].isdigit():
        return None
    if parts[0] == OSC_MASTER:
        parts = parts[1:]

    # `/beat/subdiv/<n>` is a 0..1 ramp that loops every n beats -- rkbx_link's
    # bar phase, and the most useful thing it sends. Scaling it back up to beats
    # is what makes it the `beat_in_bar` the clock's align_bar wants; taking the
    # raw 0..1 would put every downbeat correction inside the first beat.
    if len(parts) >= 3 and parts[-3:-1] == ["beat", "subdiv"]:
        try:
            divisor = float(parts[-1])
        except ValueError:
            return None
        if divisor <= 0:
            return None
        return {"beat_in_bar": float(value) * divisor}

    for span in (2, 1):
        if len(parts) >= span:
            field = OSC_FIELDS.get("/".join(parts[-span:]))
            if field is not None:
                break
    else:
        return None
    if field is None:
        return None

    out: dict = {field: value}
    # A source that STATES the phrase is, by definition, measuring it. OSC has
    # no way to send the flag separately, so without this the rekordbox path
    # would report phrase labels while `phrase_measured` stayed false forever --
    # and auto look changes would quietly keep landing on bars, which is the
    # exact degradation this whole milestone exists to end.
    if field == "phrase_label":
        out["phrase_measured"] = True
    return out


def parse(data: bytes) -> Optional[dict]:
    """A datagram as clock fields, whichever format it arrived in."""
    stripped = data.lstrip()
    if stripped.startswith(b"{"):
        try:
            raw = json.loads(stripped.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None
        if not isinstance(raw, dict):
            return None
    elif stripped.startswith(b"/"):
        raw = parse_osc(data)
        if raw is None:
            return None
    else:
        return None
    return clean(raw)


def clean(raw: dict) -> Optional[dict]:
    """Whitelist, coerce and range-check. Returns None if nothing usable is left.

    Every value crossing this line came off a socket, so it is checked here
    rather than trusted to be the shape the bridge documentation promises. A
    bridge with a units bug that sends BPM in Hz should produce a rejected
    packet, not a show running at 2 BPM.
    """
    out: dict = {}
    for key in FIELDS:
        if key not in raw or raw[key] is None:
            continue
        value = raw[key]
        try:
            if key in ("bpm", "beat", "beat_in_bar", "phrase_ends_in"):
                out[key] = float(value)
            elif key == "phrase_measured":
                out[key] = bool(value)
            else:
                out[key] = str(value)[:64]
        except (TypeError, ValueError):
            continue
    if "bpm" in out and not 40.0 <= out["bpm"] <= 250.0:
        del out["bpm"]
    if "beat_in_bar" in out and not 0.0 <= out["beat_in_bar"] < 64.0:
        del out["beat_in_bar"]
    return out or None


class SyncListener:
    """A UDP socket, a thread, and one callback per usable datagram.

    Owns no clock and no controller. It hands a dict to `on_sync` and that is
    the whole of its authority -- which is what makes "this port cannot patch a
    fixture" a structural fact rather than a promise.
    """

    def __init__(self, on_sync: Callable[[dict], None], port: int,
                 bind: str = "127.0.0.1"):
        self.on_sync = on_sync
        self.port = port
        self.bind = bind
        self.sock: Optional[socket.socket] = None
        self.thread: Optional[threading.Thread] = None
        self.running = False
        # Counters, so the console can distinguish "no bridge" from "a bridge
        # sending something I cannot read" -- which look identical from the
        # operator's side and have completely different fixes.
        self.received = 0
        self.rejected = 0
        self.last_reject: Optional[str] = None

    def start(self) -> None:
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind((self.bind, self.port))
        # So `stop` can interrupt the read rather than waiting for a datagram
        # that may never come. Closing a socket blocked in recvfrom is not
        # reliably an error on Windows.
        self.sock.settimeout(0.25)
        self.running = True
        self.thread = threading.Thread(target=self._run, name="sync",
                                       daemon=True)
        self.thread.start()

    def _run(self) -> None:
        while self.running:
            try:
                data, addr = self.sock.recvfrom(2048)
            except socket.timeout:
                continue
            except OSError:
                if self.running:
                    continue
                return
            fields = parse(data)
            if fields is None:
                self.rejected += 1
                self.last_reject = f"{addr[0]}: {data[:48]!r}"
                continue
            self.received += 1
            try:
                self.on_sync(fields)
            except Exception as exc:          # noqa: BLE001 -- never die on one packet
                self.rejected += 1
                self.last_reject = f"{addr[0]}: {exc}"

    def stop(self) -> None:
        self.running = False
        if self.thread is not None:
            self.thread.join(timeout=1.0)
        if self.sock is not None:
            self.sock.close()
            self.sock = None

    def status(self) -> dict:
        return {"port": self.port, "bind": self.bind,
                "received": self.received, "rejected": self.rejected,
                "last_reject": self.last_reject}


def send(fields: dict, host: str = "127.0.0.1", port: int = 9000) -> None:
    """Fire one sync datagram. Here rather than in the bridge so anything --
    a test, a REPL, a shell one-liner -- can drive the seam without importing
    a sidecar that may need a virtualenv this process does not have."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(json.dumps(fields).encode("utf-8"), (host, port))


def now() -> float:
    """Wall time, for staleness. Separate from the engine's monotonic frame
    clock on purpose -- see `MasterClock.synced_at`."""
    return time.time()
