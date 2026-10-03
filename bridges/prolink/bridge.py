"""Feed the engine tempo, bar phase and phrase -- with or without CDJs.

    python bridges/prolink/bridge.py --fake
    python bridges/prolink/bridge.py --fake --track --osc   # a deck, rkbx_link-shaped
    python bridges/prolink/bridge.py --fake --track --blt --script "play:64,loop:4x3,hotcue:160"
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
import base64
import json
import math
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


def osc_args(address: str, *args) -> bytes:
    """An OSC message with several arguments: int -> i, float -> f, str -> s.
    Our /klights/v1 namespace needs these; rkbx_link sends one value each."""
    def pad(raw: bytes) -> bytes:
        return raw + b"\0" * (4 - len(raw) % 4)
    tags, payload = "", b""
    for arg in args:
        if isinstance(arg, str):
            tags += "s"
            payload += pad(arg.encode("utf-8"))
        elif isinstance(arg, bool) or isinstance(arg, int):
            tags += "i"
            payload += struct.pack(">i", int(arg))
        else:
            tags += "f"
            payload += struct.pack(">f", float(arg))
    return pad(address.encode()) + pad(b"," + tags.encode()) + payload


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
    title = fields.get("title", fields.get("track"))
    if title is not None:
        out.append(osc_message("/master/track/title", title))
    # rkbx_link sends who the track is a field at a time, which is exactly the
    # case the transport's settle window exists for -- so the fake does too.
    for key in ("artist", "album"):
        if key in fields:
            out.append(osc_message(f"/master/track/{key}", fields[key]))
    if "bpm_original" in fields:
        out.append(osc_message("/master/bpm/original", fields["bpm_original"]))
    # And it sends no position at all while the deck is paused: silence is its
    # only pause signal, so the fake is silent too.
    if "track_time" in fields and fields.get("playing", True):
        out.append(osc_message("/master/time", fields["track_time"]))
    return out


def as_blt(fields: dict) -> list[bytes]:
    """A field set as the beat-link-trigger expressions we ship send it: our
    /klights/v1 messages for position and identity, flat addresses for tempo
    and bar phase (which the clock path already reads)."""
    out = []
    deck = int(fields.get("deck", 1))
    if "title" in fields:
        out.append(osc_args("/klights/v1/track", deck,
                            int(fields.get("rekordbox_id", 0)),
                            fields.get("signature", ""), fields["title"],
                            fields.get("artist", ""), fields.get("album", ""),
                            float(fields.get("duration", 0.0))))
    if "track_time" in fields:
        out.append(osc_args("/klights/v1/pos", deck,
                            int(bool(fields.get("playing", True))),
                            float(fields["track_time"]), 1.0,
                            int(fields.get("beat_number", 0)), 1, 1))
    if "bpm" in fields:
        out.append(osc_message("/bpm", fields["bpm"]))
    if "beat_in_bar" in fields:
        out.append(osc_message("/beat", fields["beat_in_bar"]))
    if "phrase_label" in fields:
        # The tempo master's phrase from the USB's analysis (milestone 2): how
        # far into it and how long left, so the engine knows where it began.
        out.append(osc_args("/klights/v1/phrase", deck, fields["phrase_label"],
                            float(fields.get("phrase_into", 0.0)),
                            float(fields.get("phrase_ends_in", 0.0))))
    return out


def emit(sock: socket.socket, host: str, port: int, fields: dict,
         verbose: bool, use_osc: bool = False, shape: str = "") -> None:
    shape = shape or ("osc" if use_osc else "json")
    if "raw" in fields:
        # A captured datagram, replayed byte for byte -- see capture.py.
        sock.sendto(base64.b64decode(fields["raw"]), (host, port))
    elif shape == "osc":
        for message in as_osc(fields):
            sock.sendto(message, (host, port))
    elif shape == "blt":
        for message in as_blt(fields):
            sock.sendto(message, (host, port))
    else:
        sock.sendto(json.dumps(fields).encode("utf-8"), (host, port))
    if verbose:
        print(f"  -> {shape:<4} {json.dumps(fields)}", flush=True)


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
                fields["phrase_into"] = beat - start
            yield 60.0 / bpm, fields
            beat += 1
            del offset
        phrase_at += 1


# -- a deck: which track, and where in it (F19) ----------------------------------

# The two tracks the fake deck can play. The first is the show-example's
# synthetic track (prep.py synthetic writes exactly it), so a fake run plays its
# timeline. The second is NOT in any show folder: it is what a guest DJ's
# unknown track looks like, for testing the unmatched path.
DECK_TRACKS = [
    {"title": "synthetic 128", "artist": "kLights", "album": "test track",
     "duration": 180.0, "rekordbox_id": 1, "phrases": True},
    {"title": "Unknown Guest Tune", "artist": "Guest DJ", "album": "",
     "duration": 240.0, "rekordbox_id": 2, "phrases": False},
]


def parse_script(script: str) -> list[tuple[str, tuple]]:
    """`play:64,loop:4x3,hotcue:160,pause:3s,play,switch,scratch` -> steps.

    play[:BEATS]    play forwards (BEATS omitted: until the next step never comes)
    loop:BxK        play B beats and jump back to their start, K times
    hotcue:BEAT     jump to a beat of the track
    pause:SECONDS   stop (an "s" suffix is allowed)
    switch          the other deck becomes master, with the other track
    scratch         a second of back-and-forth on the platter
    """
    steps: list[tuple[str, tuple]] = []
    for raw in filter(None, (part.strip() for part in script.split(","))):
        name, _, arg = raw.partition(":")
        try:
            if name == "play":
                steps.append(("play", (float(arg),) if arg else ()))
            elif name == "loop":
                beats, _, times = arg.partition("x")
                steps.append(("loop", (float(beats), int(times or 1))))
            elif name == "hotcue":
                steps.append(("hotcue", (float(arg),)))
            elif name == "pause":
                steps.append(("pause", (float(arg.rstrip("s")),)))
            elif name in ("switch", "scratch") and not arg:
                steps.append((name, ()))
            else:
                raise ValueError(raw)
        except ValueError:
            raise ValueError(f"cannot read script step {raw!r}; see "
                             f"`--help` for the steps") from None
    return steps


def deck(bpm: float, script: str = "", hz: float = 30.0,
         identity_every_s: float = 4.0) -> Iterator[tuple[float, dict]]:
    """A deck playing a track, sending position `hz` times a second, with
    tempo and bar phase on each beat and the phrase on each downbeat, exactly
    as `fake` does. The script drives the transport; once it runs out the deck
    plays on forever."""
    steps = parse_script(script)
    beat_s = 60.0 / bpm
    tick = 1.0 / hz
    state = {"track": 0, "beat": 0.0, "playing": True, "since_id": None,
             "last_beat": None}

    def phrase_at(beat: float):
        start = 0
        for label, bars in FAKE_PHRASES:
            end = start + bars * 4
            if beat < end:
                return label, end - beat, beat - start
            start = end
        return None, None, None

    def packet() -> dict:
        track = DECK_TRACKS[state["track"]]
        beat = state["beat"]
        fields = {"source": "fake", "deck": str(state["track"] + 1),
                  "track_time": round(beat * beat_s, 4),
                  "playing": state["playing"],
                  "beat_number": max(1, int(math.floor(beat)) + 1)}
        if state["since_id"] is None or state["since_id"] >= identity_every_s:
            fields.update({k: track[k] for k in ("title", "artist", "album",
                                                 "duration", "rekordbox_id")})
            state["since_id"] = 0.0
        state["since_id"] += tick
        whole = int(math.floor(beat))
        if state["playing"] and whole != state["last_beat"]:
            state["last_beat"] = whole
            fields.update({"bpm": bpm, "bpm_original": bpm,
                           "beat_in_bar": float(whole % 4)})
            if whole % 4 == 0 and track["phrases"]:
                label, left, into = phrase_at(whole)
                if label is not None:
                    fields.update({"phrase_label": label, "phrase_ends_in": left,
                                   "phrase_into": into, "phrase_measured": True})
        return fields

    def play(beats: Optional[float]):
        ticks = None if beats is None else int(round(beats * beat_s * hz))
        n = 0
        while ticks is None or n < ticks:
            yield tick, packet()
            state["beat"] += tick / beat_s
            n += 1

    for name, args in steps:
        if name == "play":
            yield from play(args[0] if args else None)
            if not args:
                return
        elif name == "loop":
            beats, times = args
            for _ in range(times):
                start = state["beat"]
                yield from play(beats)
                state["beat"] = start
        elif name == "hotcue":
            state["beat"] = args[0]
            state["last_beat"] = None
        elif name == "pause":
            state["playing"] = False
            for _ in range(int(round(args[0] * hz))):
                yield tick, packet()
            state["playing"] = True
            state["last_beat"] = None
        elif name == "switch":
            state["track"] ^= 1
            state["beat"] = 0.0
            state["since_id"] = None
            state["last_beat"] = None
        elif name == "scratch":
            for k in range(int(hz)):
                # Two ticks back, one forward: the platter pushed and pulled.
                state["beat"] += (-0.6 if k % 3 < 2 else 0.9)
                yield tick, packet()
            state["last_beat"] = None
    yield from play(None)


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
        use_osc: bool = False, shape: str = "") -> int:
    sent = 0
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        for dt, fields in source:
            emit(sock, host, port, fields, verbose, use_osc, shape)
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
    shapes = parser.add_mutually_exclusive_group()
    shapes.add_argument("--osc", action="store_true",
                        help="send rkbx_link-shaped OSC instead of JSON, to "
                             "exercise the rekordbox path's decoder")
    shapes.add_argument("--blt", action="store_true",
                        help="send what our beat-link-trigger expressions send: "
                             "/klights/v1 OSC")
    parser.add_argument("--track", action="store_true",
                        help="with --fake: a deck playing a track, with its "
                             "position, not just the beat")
    parser.add_argument("--script", default="",
                        help="with --track: what the deck does, e.g. "
                             "'play:64,loop:4x3,hotcue:160,pause:3s,play:32,"
                             "switch,scratch'. Steps: play[:BEATS], loop:BxK, "
                             "hotcue:BEAT, pause:SECONDS, switch, scratch")
    parser.add_argument("--hz", type=float, default=30.0,
                        help="with --track: position packets per second")
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
    elif args.track:
        try:
            parse_script(args.script)
        except ValueError as exc:
            print(exc, file=sys.stderr)
            return 2
        source = deck(args.bpm, args.script, args.hz)
        what = (f"a deck at {args.bpm:g} bpm playing {DECK_TRACKS[0]['title']!r}"
                + (f", script {args.script!r}" if args.script else ""))
    else:
        source = fake(args.bpm)
        what = f"faking {args.bpm:g} bpm, phrases: " + " ".join(
            f"{n}x{b}" for n, b in FAKE_PHRASES)

    print(f"{what}\n  -> {args.host}:{args.port}  (Ctrl-C to stop)")
    try:
        sent = run(source, args.host, args.port, not args.quiet, args.beats,
                   use_osc=args.osc, shape="blt" if args.blt else "")
    except KeyboardInterrupt:
        print("\nstopped.")
        return 0
    print(f"sent {sent} packet(s).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
