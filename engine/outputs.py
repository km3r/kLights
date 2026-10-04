"""
The other outputs (milestone 3): what the show says to things that are not
lights -- a VJ app over OSC, MIDI gear through the sidecar, timecode, the
built-in visuals -- from the same timelines, routines and templates.

**One frame, many outputs.** Each frame the player says which programs are on
stage (`TrackPlayer.stage`); `gather` collects every external row they hold
into one immutable `ProgramFrame`; each output turns that into its own
messages. Nothing here knows about fixtures, and no output can change what the
lights do.

**Edges by key.** An item is on while its window covers the beat
(`timeline.ExternalRow`), so an output compares one frame's items with the
last's: a key that appears comes ON, one that disappears goes OFF. A jump into
a cue turns it on and a jump out of one turns it off, exactly as playing
through would have left them; a cue too short for any frame to see still fires
once (on, then off) in forward play, and never after a jump. A key names the
program, the routine clip and its loop pass, the row and the item -- so a new
track, a new template pick or the next pass of a looping routine fires again.

**Who owns an output** (settled like the lanes): a track's timeline that has
rows for an output owns it for that track, and the template's rows for that
output are silent; a timeline with none leaves the output to the template.

**Timecode** (`TimecodeOut`, decided with the user): Art-Net ArtTimeCode
carrying the matched track's position -- so it jumps with loops and hot cues,
and a VJ app with its own per-track timeline follows the DJ. It is sent when
its frame changes, and not at all when nothing matched is playing, when the
deck is paused, or while Follow is disarmed (the designer's preview counts as
armed: someone with the token is driving).

**OSC** (`OscOut`): one UDP socket, non-blocking, to an IPv4 address (never a
name: resolving one could block the output thread). An item sends `on` when it
comes on, `off` when it goes off, and `while` as it plays -- only when the
bytes change and at most 30 times a second, which is also how a row's curve is
sent. Arguments are numbers and text, or `$beat`, `$bar`, `$phase` (0-1
through the bar), `$progress` (0-1 through the item) and `$value` (the row's
curve). A send that fails is counted and shown, never raised: the lights must
not stop because the VJ laptop went away.

Stdlib only.
"""

from __future__ import annotations

import json
import socket
import struct
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from . import showfiles
from . import timeline as timelinemod
from .output import artnet as artnetmod

RATE_HZ = 30.0              # continuous sends (while, curves) per key, at most
INT32 = (-2 ** 31, 2 ** 31 - 1)


# -- the frame ------------------------------------------------------------------

@dataclass(frozen=True)
class Active:
    """One external item on this frame -- or a row's curve (`item` None)."""
    key: str
    output: str
    row: timelinemod.ExternalRow
    item: Optional[timelinemod.Item]
    progress: float = 0.0
    value: Optional[float] = None
    crossed: bool = False
    source: str = "timeline"


@dataclass(frozen=True)
class ProgramFrame:
    """Everything the other outputs need from one frame. Replaced, never
    edited, so an output on another thread may hold one safely."""
    mode: str                            # the player's: timeline, template, ...
    armed: bool = False                  # Follow armed, or the designer driving
    beat: Optional[float] = None         # the show's beat (track's, else clock's)
    track_id: Optional[str] = None
    time_s: Optional[float] = None       # the matched track's position
    playing: bool = False
    phrase: Optional[str] = None
    palette: Mapping[str, tuple] = field(default_factory=dict)
    active: tuple[Active, ...] = ()

    @property
    def bar(self) -> Optional[int]:
        return None if self.beat is None else int(self.beat // 4) + 1

    @property
    def phase(self) -> Optional[float]:
        return None if self.beat is None else (self.beat % 4.0) / 4.0

    def of(self, output: str) -> tuple[Active, ...]:
        return tuple(a for a in self.active if a.output == output)


def outputs_used(prog) -> frozenset[str]:
    """Every output a program has rows for, in its timeline or in any routine
    it plays. Fixed once compiled."""
    used = getattr(prog, "_outputs_used", None)
    if used is None:
        rows = list(prog.timeline.external_rows)
        for src in prog.sources.values():
            inst = getattr(src, "inst", None)
            if inst is not None:
                rows.extend(inst.timeline.external_rows)
        used = frozenset(showfiles.OUTPUT_ALIASES.get(r.output, r.output)
                         for r in rows)
        prog._outputs_used = used
    return used


def gather(stage: Sequence[tuple[str, str, Any]]) -> tuple[Active, ...]:
    """The external items on stage this frame. `stage` is (source, key prefix,
    program) for each program that began this frame, the track's timeline
    first; each program's `external` was filled by its `begin`."""
    owned: set[str] = set()
    for source, _, prog in stage:
        if source in ("timeline", "preview"):
            owned |= outputs_used(prog)
    out: list[Active] = []
    for source, prefix, prog in stage:
        drop = owned if source not in ("timeline", "preview") else frozenset()
        for sub, ef in prog.external:
            output = showfiles.OUTPUT_ALIASES.get(ef.row.output, ef.row.output)
            if output in drop:
                continue
            base = f"{prefix}|{sub}|{ef.row.id}"
            for hit in ef.items:
                out.append(Active(f"{base}|{hit.item.id}", output, ef.row, hit.item,
                                  hit.progress, ef.value, hit.crossed, source))
            if ef.value is not None:
                out.append(Active(f"{base}~", output, ef.row, None, 0.0, ef.value,
                                  False, source))
    return tuple(out)


# -- OSC ------------------------------------------------------------------------

def _pad(raw: bytes) -> bytes:
    return raw + b"\0" * (4 - len(raw) % 4)


def osc_encode(address: str, args: Sequence[Any] = ()) -> bytes:
    """One OSC message: int -> i, float -> f, text -> s. An integer too big
    for 32 bits goes as a float rather than failing."""
    tags, payload = "", b""
    for arg in args:
        if isinstance(arg, bool):
            arg = int(arg)
        if isinstance(arg, int) and INT32[0] <= arg <= INT32[1]:
            tags += "i"
            payload += struct.pack(">i", arg)
        elif isinstance(arg, (int, float)):
            tags += "f"
            payload += struct.pack(">f", float(arg))
        else:
            tags += "s"
            payload += _pad(str(arg).encode("utf-8"))
    return _pad(address.encode("utf-8")) + _pad(("," + tags).encode("ascii")) + payload


def render(args: Optional[Sequence[Any]], active: Active,
           frame: ProgramFrame) -> list:
    """An item's arguments with the `$` tokens filled in for this frame."""
    out: list = []
    for arg in args or ():
        if arg == "$beat":
            out.append(float(frame.beat or 0.0))
        elif arg == "$bar":
            out.append(int(frame.bar or 0))
        elif arg == "$phase":
            out.append(float(frame.phase or 0.0))
        elif arg == "$progress":
            out.append(float(active.progress))
        elif arg == "$value":
            out.append(float(active.value if active.value is not None else 0.0))
        else:
            out.append(arg)
    return out


class _UdpOut:
    """One non-blocking UDP socket to one address, with its counts. A send
    that fails is counted, never raised into the output thread."""

    def __init__(self, host: str, port: int, sock: Optional[socket.socket] = None,
                 broadcast: bool = True):
        self.host = "127.0.0.1" if host == "localhost" else host
        self.port = int(port)
        self.target = (self.host, self.port)
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            if broadcast:     # a .255 address reaches a whole subnet
                try:
                    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                except OSError:
                    pass
        self.sock = sock
        self.sent = 0
        self.errors = 0
        self.last_error: Optional[str] = None

    def close(self) -> None:
        try:
            self.sock.close()
        except OSError:
            pass

    def _send(self, data: bytes) -> None:
        try:
            self.sock.sendto(data, self.target)
            self.sent += 1
        except OSError as exc:
            self.errors += 1
            self.last_error = str(exc) or type(exc).__name__


class _Edges(_UdpOut):
    """What OSC and MIDI share: one frame's items against the last's, by key.
    A subclass says what an item does when it starts, while it plays, and
    when it stops, and what a row's curve sends."""
    output = ""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._on: dict[str, Active] = {}                  # items that are on
        self._last: dict[str, tuple[Any, float]] = {}     # continuous: last sent

    def _start(self, a: Active, frame: ProgramFrame) -> None: ...
    def _stop(self, a: Active, frame: ProgramFrame) -> None: ...
    def _during(self, key: str, a: Active, frame: ProgramFrame, now: float) -> None: ...
    def _curve(self, key: str, a: Active, frame: ProgramFrame, now: float) -> None: ...
    def _flush(self) -> None: ...

    def _due(self, key: str, data: Any, now: float) -> bool:
        """Whether a continuous value should go now: it changed, and the last
        one went at least 1/RATE_HZ ago. Records it if so."""
        last = self._last.get(key)
        if last is not None and (data == last[0] or now - last[1] < 1.0 / RATE_HZ):
            return False
        self._last[key] = (data, now)
        return True

    def send(self, frame: Optional[ProgramFrame], now: float) -> None:
        """This frame's messages. `frame` None: nothing is on any more."""
        current = ({a.key: a for a in frame.of(self.output)} if frame is not None
                   else {})
        frame = frame if frame is not None else ProgramFrame(mode="off")
        # Off first, so a cue that replaces another ends it before it begins.
        for key in [k for k in self._on if k not in current]:
            self._stop(self._on.pop(key), frame)
            self._last.pop(key, None)
        for key, a in current.items():
            if a.item is None:                       # a row's curve
                self._curve(key, a, frame, now)
                continue
            if a.crossed:                            # on and over between frames
                self._start(a, frame)
                self._stop(a, frame)
                continue
            if key not in self._on:
                self._start(a, frame)
            self._on[key] = a                        # its latest progress, for off
            self._during(key, a, frame, now)
        for key in [k for k in self._last if k not in current]:
            del self._last[key]
        self._flush()

    def public(self) -> dict:
        return {"target": f"{self.host}:{self.port}", "sent": self.sent,
                "errors": self.errors, "last_error": self.last_error,
                "on": len(self._on)}


class OscOut(_Edges):
    """A frame's OSC items, sent to one address."""
    output = "osc"

    def _message(self, msg: Optional[Mapping], a: Active,
                 frame: ProgramFrame) -> None:
        if msg and msg.get("address"):
            self._send(osc_encode(msg["address"], render(msg.get("args"), a, frame)))

    def _continuous(self, key: str, msg: Optional[Mapping], a: Active,
                    frame: ProgramFrame, now: float) -> None:
        if not msg or not msg.get("address"):
            return
        data = osc_encode(msg["address"], render(msg.get("args"), a, frame))
        if self._due(key, data, now):
            self._send(data)

    def _start(self, a, frame):
        self._message(a.item.data.get("on"), a, frame)

    def _stop(self, a, frame):
        self._message(a.item.data.get("off"), a, frame)

    def _during(self, key, a, frame, now):
        self._continuous(key, a.item.data.get("while"), a, frame, now)

    def _curve(self, key, a, frame, now):
        self._continuous(key, {"address": a.row.data.get("address"),
                               "args": a.row.data.get("args", ["$value"])},
                         a, frame, now)


# -- MIDI, through the sidecar -----------------------------------------------------

MIDI_WIRE = "klights.midi/1"


class MidiOut(_Edges):
    """A frame's MIDI items, as one small JSON datagram to the MIDI sidecar
    (`bridges/midi/midi_out.py`), which owns the MIDI port -- so the engine
    stays stdlib-only and a MIDI driver can never stall the lights.

    A note item plays its note from start to end; a CC item sends its value
    at the start and `off_value`, if it has one, at the end; a program item
    sends its program at the start. A row's curve (0-1) goes to its `cc` as
    0-127, on change and at most 30 times a second. Channels are 1-16, the
    item's, else the row's, else 1."""
    output = "midi"

    def __init__(self, *args, **kwargs):
        kwargs.setdefault("broadcast", False)
        super().__init__(*args, **kwargs)
        self._batch: list[dict] = []

    @staticmethod
    def _channel(a: Active) -> int:
        item = a.item.data if a.item is not None else {}
        return int(item.get("channel") or a.row.data.get("channel") or 1)

    def _start(self, a, frame):
        d, ch = a.item.data, self._channel(a)
        if d.get("note") is not None:
            self._batch.append({"type": "note_on", "channel": ch, "note": d["note"],
                                "velocity": d.get("velocity", 100)})
        elif d.get("cc") is not None:
            self._batch.append({"type": "control_change", "channel": ch,
                                "control": d["cc"], "value": d.get("value", 127)})
        elif d.get("pc") is not None:
            self._batch.append({"type": "program_change", "channel": ch,
                                "program": d["pc"]})

    def _stop(self, a, frame):
        d, ch = a.item.data, self._channel(a)
        if d.get("note") is not None:
            self._batch.append({"type": "note_off", "channel": ch, "note": d["note"],
                                "velocity": 0})
        elif d.get("cc") is not None and d.get("off_value") is not None:
            self._batch.append({"type": "control_change", "channel": ch,
                                "control": d["cc"], "value": d["off_value"]})

    def _during(self, key, a, frame, now):
        pass

    def _curve(self, key, a, frame, now):
        cc = a.row.data.get("cc")
        if cc is None or a.value is None:
            return
        value = int(round(max(0.0, min(1.0, a.value)) * 127))
        if self._due(key, value, now):
            self._batch.append({"type": "control_change", "channel": self._channel(a),
                                "control": cc, "value": value})

    def _flush(self):
        if self._batch:
            batch, self._batch = self._batch, []
            self._send(json.dumps({"klights": MIDI_WIRE, "messages": batch},
                                  separators=(",", ":")).encode("utf-8"))

    def close(self) -> None:
        """Every note still sounding is stopped first: a stuck note on a
        synth outlives the engine."""
        self.send(None, 0.0)
        super().close()


# -- timecode ---------------------------------------------------------------------

def timecode_at(seconds: float, fps: float) -> tuple[int, int, int, int]:
    """(frames, seconds, minutes, hours) at `seconds` into the track. 29.97
    is drop-frame: frame numbers 0 and 1 are skipped at the start of every
    minute except each tenth, so the clock stays on wall time. Hours wrap at
    24, as timecode does."""
    if fps == 29.97:
        total = int(seconds * 30000 / 1001)
        tens, rest = divmod(total, 17982)          # frames per ten minutes
        skipped = 18 * tens + (2 * ((rest - 2) // 1798) if rest >= 2 else 0)
        n, base = total + skipped, 30
    else:
        base = int(fps)
        n = int(seconds * base)
    return (n % base, (n // base) % 60, (n // (base * 60)) % 60,
            (n // (base * 3600)) % 24)


def timecode_text(tc: tuple[int, int, int, int], fps: float) -> str:
    frames, secs, mins, hours = tc
    sep = ";" if fps == 29.97 else ":"
    return f"{hours:02d}:{mins:02d}:{secs:02d}{sep}{frames:02d}"


class TimecodeOut(_UdpOut):
    """ArtTimeCode, from the matched track's position."""

    def __init__(self, host: str = "255.255.255.255",
                 port: int = artnetmod.ARTNET_PORT, fps: float = 30,
                 sock: Optional[socket.socket] = None):
        super().__init__(host, port, sock)
        self.fps = fps
        self.kind = artnetmod.TIMECODE_TYPES[fps]
        self.last: Optional[tuple[int, int, int, int]] = None

    def send(self, frame: Optional[ProgramFrame], now: float) -> None:
        if (frame is None or not frame.armed or not frame.playing
                or frame.time_s is None or frame.time_s < 0):
            self.last = None                    # silent: a stopped clock
            return
        tc = timecode_at(frame.time_s, self.fps)
        if tc == self.last:
            return
        self.last = tc
        self._send(artnetmod.build_arttimecode(*tc, self.kind))

    def public(self) -> dict:
        return {"target": f"{self.host}:{self.port}", "fps": self.fps,
                "sent": self.sent, "errors": self.errors,
                "last_error": self.last_error,
                "now": timecode_text(self.last, self.fps) if self.last else None}


# -- the outputs together ---------------------------------------------------------

def merge(show: Optional[Mapping], local: Optional[Mapping]) -> dict:
    """show.json's `outputs`, with klights.local.json's over it, output by
    output and key by key: the venue's addresses win over the show's."""
    out: dict = {}
    for source in (show or {}, local or {}):
        for name, conf in source.items():
            if isinstance(conf, Mapping):
                out[name] = {**out.get(name, {}), **conf}
    return out


MIDI_PORT = 9123            # where the MIDI sidecar listens, by default

# name -> (label, default host, default port); timecode also takes an fps.
_PLACES = {
    "osc": ("OSC", "127.0.0.1", None),
    "midi": ("MIDI", "127.0.0.1", MIDI_PORT),
    "timecode": ("timecode", "255.255.255.255", artnetmod.ARTNET_PORT),
}


class Outputs:
    """Every output this engine sends to. Configured from the show folder on
    each load; fed a ProgramFrame every frame on the output thread."""

    def __init__(self) -> None:
        self.osc: Optional[OscOut] = None
        self.midi: Optional[MidiOut] = None
        self.timecode: Optional[TimecodeOut] = None
        self.config: dict = {}
        self.problems: list[str] = []

    @property
    def outs(self) -> tuple:
        return tuple(o for o in (self.osc, self.midi, self.timecode) if o is not None)

    @property
    def active(self) -> bool:
        return bool(self.outs)

    def configure(self, show: Optional[Mapping], local: Optional[Mapping] = None
                  ) -> Optional[str]:
        """Point the outputs where the folder (and this machine) say. Returns a
        line saying what changed, or None if nothing did. An output whose
        place is unchanged keeps its state -- the cues it knows are on."""
        config = merge(show, local)
        if config == self.config:
            return None
        self.config = config
        self.problems = []
        self.osc = self._place("osc", config.get("osc"), self.osc)
        self.midi = self._place("midi", config.get("midi"), self.midi)
        self.timecode = self._place("timecode", config.get("timecode"), self.timecode)
        return "outputs: " + self.describe()

    def _place(self, name: str, conf: Optional[Mapping], old):
        label, host_default, port_default = _PLACES[name]
        out = None
        if conf is not None:
            host = conf.get("host", host_default)
            port = conf.get("port", port_default)
            fps = conf.get("fps", 30)
            problem = showfiles.host_problem(host)
            if problem is None and not (isinstance(port, int)
                                        and not isinstance(port, bool)
                                        and 1 <= port <= 65535):
                problem = f"port {port!r} must be a whole number from 1 to 65535"
            if problem is None and name == "timecode" \
                    and fps not in artnetmod.TIMECODE_TYPES:
                problem = f"fps {fps!r} must be 24, 25, 29.97 or 30"
            if problem is not None:
                self.problems.append(f"outputs.{name}: {problem}; {label} is off")
            else:
                target = ("127.0.0.1" if host == "localhost" else host, port)
                same = (old is not None and old.target == target
                        and (name != "timecode" or old.fps == fps))
                if same:
                    out, old = old, None
                elif name == "osc":
                    out = OscOut(host, port)
                elif name == "midi":
                    out = MidiOut(host, port)
                else:
                    out = TimecodeOut(host, port, fps)
        if old is not None:
            old.close()
        return out

    def describe(self) -> str:
        parts = []
        if self.osc is not None:
            parts.append(f"OSC to {self.osc.host}:{self.osc.port}")
        if self.midi is not None:
            parts.append(f"MIDI to the sidecar at {self.midi.host}:{self.midi.port}")
        if self.timecode is not None:
            parts.append(f"ArtTimeCode ({self.timecode.fps:g} fps) to "
                         f"{self.timecode.host}:{self.timecode.port}")
        parts.extend(self.problems)
        return "; ".join(parts) if parts else "none"

    def send(self, frame: Optional[ProgramFrame], now: float) -> None:
        for out in self.outs:
            out.send(frame, now)

    def public(self) -> Optional[dict]:
        if not self.active and not self.problems:
            return None
        return {name: (out.public() if out is not None else None)
                for name, out in (("osc", self.osc), ("midi", self.midi),
                                  ("timecode", self.timecode))} | {
            "problems": list(self.problems)}

    def close(self) -> None:
        for out in self.outs:
            out.close()
        self.osc = self.midi = self.timecode = None
