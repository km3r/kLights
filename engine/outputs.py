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

import socket
import struct
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional, Sequence

from . import showfiles
from . import timeline as timelinemod

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


class OscOut:
    """A frame's OSC items, sent to one address."""

    def __init__(self, host: str, port: int, sock: Optional[socket.socket] = None):
        self.host = "127.0.0.1" if host == "localhost" else host
        self.port = int(port)
        self.target = (self.host, self.port)
        if sock is None:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setblocking(False)
            try:     # a .255 address reaches a whole subnet of VJ machines
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            except OSError:
                pass
        self.sock = sock
        self._on: dict[str, Active] = {}            # items that are on
        self._last: dict[str, tuple[bytes, float]] = {}   # continuous: last sent
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
        except OSError as exc:              # never into the output thread
            self.errors += 1
            self.last_error = str(exc) or type(exc).__name__

    def _message(self, msg: Optional[Mapping], active: Active,
                 frame: ProgramFrame) -> None:
        if msg and msg.get("address"):
            self._send(osc_encode(msg["address"], render(msg.get("args"), active,
                                                         frame)))

    def _continuous(self, key: str, msg: Optional[Mapping], active: Active,
                    frame: ProgramFrame, now: float) -> None:
        if not msg or not msg.get("address"):
            return
        data = osc_encode(msg["address"], render(msg.get("args"), active, frame))
        last = self._last.get(key)
        if last is not None and (data == last[0] or now - last[1] < 1.0 / RATE_HZ):
            return
        self._send(data)
        self._last[key] = (data, now)

    def send(self, frame: Optional[ProgramFrame], now: float) -> None:
        """This frame's messages. `frame` None: nothing is on any more."""
        empty = ProgramFrame(mode="off")
        current = {a.key: a for a in frame.of("osc")} if frame is not None else {}
        frame = frame if frame is not None else empty
        # Off first, so a cue that replaces another ends it before it begins.
        for key in [k for k in self._on if k not in current]:
            was = self._on.pop(key)
            self._message(was.item.data.get("off"), was, frame)
            self._last.pop(key, None)
        for key, a in current.items():
            if a.item is None:                       # a row's curve
                self._continuous(key, {"address": a.row.data.get("address"),
                                       "args": a.row.data.get("args", ["$value"])},
                                 a, frame, now)
                continue
            data = a.item.data
            if a.crossed:                            # on and over between frames
                self._message(data.get("on"), a, frame)
                self._message(data.get("off"), a, frame)
                continue
            if key not in self._on:
                self._on[key] = a
                self._message(data.get("on"), a, frame)
            else:
                self._on[key] = a                    # its latest progress, for off
            self._continuous(key, data.get("while"), a, frame, now)
        for key in [k for k in self._last if k not in current]:
            del self._last[key]

    def public(self) -> dict:
        return {"target": f"{self.host}:{self.port}", "sent": self.sent,
                "errors": self.errors, "last_error": self.last_error,
                "on": len(self._on)}


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


class Outputs:
    """Every output this engine sends to. Configured from the show folder on
    each load; fed a ProgramFrame every frame on the output thread."""

    def __init__(self) -> None:
        self.osc: Optional[OscOut] = None
        self.config: dict = {}
        self.problems: list[str] = []

    @property
    def active(self) -> bool:
        return self.osc is not None

    def configure(self, show: Optional[Mapping], local: Optional[Mapping] = None
                  ) -> Optional[str]:
        """Point the outputs where the folder (and this machine) say. Returns a
        line saying what changed, or None if nothing did."""
        config = merge(show, local)
        if config == self.config:
            return None
        self.config = config
        self.problems = []
        osc = config.get("osc")
        old = self.osc
        self.osc = None
        if osc:
            host, port = osc.get("host", "127.0.0.1"), osc.get("port")
            problem = showfiles.host_problem(host)
            if problem is None and not (isinstance(port, int)
                                        and not isinstance(port, bool)
                                        and 1 <= port <= 65535):
                problem = f"port {port!r} must be a whole number from 1 to 65535"
            if problem is not None:
                self.problems.append(f"outputs.osc: {problem}; OSC is off")
            elif old is not None and old.target == (
                    "127.0.0.1" if host == "localhost" else host, port):
                self.osc, old = old, None             # same place: keep its state
            else:
                self.osc = OscOut(host, port)
        if old is not None:
            old.close()
        return "outputs: " + self.describe()

    def describe(self) -> str:
        parts = []
        if self.osc is not None:
            parts.append(f"OSC to {self.osc.host}:{self.osc.port}")
        parts.extend(self.problems)
        return "; ".join(parts) if parts else "none"

    def send(self, frame: Optional[ProgramFrame], now: float) -> None:
        if self.osc is not None:
            self.osc.send(frame, now)

    def public(self) -> Optional[dict]:
        if self.osc is None and not self.problems:
            return None
        return {"osc": self.osc.public() if self.osc is not None else None,
                "problems": list(self.problems)}

    def close(self) -> None:
        if self.osc is not None:
            self.osc.close()
            self.osc = None
