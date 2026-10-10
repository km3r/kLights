"""
Tests for the other outputs (milestone 3): external rows in the timeline core,
collected through routines and templates into a ProgramFrame, and sent as OSC
and as MIDI (through the sidecar in bridges/midi, run here with --fake); and
Art-Net timecode carrying the matched track's position.

The OSC goes over a real UDP socket to a listener in this process, and is
decoded by the engine's own OSC reader. The last section runs a whole
ShowController over a copy of the example show folder, pointed at that
listener, and plays the synthetic track and a guest through it.

Run: python engine/tests/test_outputs.py
"""

import contextlib
import io
import json
import queue
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bridges" / "prolink"))
sys.path.insert(0, str(REPO / "bridges" / "midi"))

import bridge as bridgemod  # noqa: E402
import midi_out as sidecarmod  # noqa: E402
from engine import outputs as outputsmod  # noqa: E402
from engine.output import artnet as artnetmod  # noqa: E402
from engine import program as programmod  # noqa: E402
from engine import server as servermod  # noqa: E402
from engine import showfiles as sf  # noqa: E402
from engine import sync as syncmod  # noqa: E402
from engine import timeline as tl  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class Listener:
    """A UDP socket in this process, standing in for the VJ app."""

    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("127.0.0.1", 0))
        self.sock.settimeout(0.05)
        self.port = self.sock.getsockname()[1]

    def raw(self, wait=0.05):
        out = []
        deadline = time.monotonic() + wait
        while True:
            try:
                data, _ = self.sock.recvfrom(65536)
                out.append(data)
            except (socket.timeout, BlockingIOError):
                if time.monotonic() >= deadline:
                    return out

    def drain(self, wait=0.05):
        out = []
        deadline = time.monotonic() + wait
        while True:
            try:
                data, _ = self.sock.recvfrom(65536)
            except (socket.timeout, BlockingIOError):
                if time.monotonic() >= deadline:
                    return out
                continue
            out.append(syncmod.decode_osc(data))

    def close(self):
        self.sock.close()


ROWS = [
    {"id": "osc", "type": "external", "output": "osc", "items": [
        {"id": "a", "at": 0, "len": 8,
         "on": {"address": "/a/on", "args": [1, "$bar"]},
         "off": {"address": "/a/off", "args": ["$progress"]},
         "while": {"address": "/a/p", "args": ["$progress"]}},
        {"id": "b", "at": 9, "len": 0.01,
         "on": {"address": "/b/on"}, "off": {"address": "/b/off"}},
        {"id": "c", "at": 16, "len": 8, "on": {"address": "/c/on", "args": ["club"]},
         "while": {"address": "/c/still", "args": [1]}}]},
    {"id": "curve", "type": "external", "output": "osc", "address": "/v",
     "args": ["$value", "$phase"], "points": [[0, 0.0], [8, 1.0]]},
]
CORE = tl.Timeline.from_rows(ROWS)


class Prog:
    """Just enough of a Program for `gather`: its external rows this frame."""

    def __init__(self, timeline):
        self.timeline, self.sources, self.external = timeline, {}, ()

    def at(self, beat, prev=None, jumped=False):
        self.external = tuple(("", e) for e in
                              self.timeline.external_at(beat, prev, jumped))
        return self


def frame(prog, beat, prev=None, jumped=False, source="timeline"):
    prog.at(beat, prev, jumped)
    return outputsmod.ProgramFrame(
        mode="timeline", beat=beat,
        active=outputsmod.gather(((source, "t", prog),)))


try:
    # -- 1. the timeline core ---------------------------------------------------
    print("\n1. external rows in the timeline core")
    rows = {e.row.id: e for e in CORE.external_at(4.0)}
    check("an item is on through its window, with its progress",
          [(h.item.id, h.progress) for h in rows["osc"].items] == [("a", 0.5)])
    check("and a row's curve gives its value", rows["curve"].value == 0.5)
    on = CORE.external_at(9.2, prev=8.9)
    check("a cue shorter than a frame is still seen once in forward play",
          [(h.item.id, h.crossed) for h in on[0].items] == [("b", True)])
    check("but not after a jump, which never played through it",
          CORE.external_at(9.2, prev=8.9, jumped=True)[0].items == ())
    check("a jump INTO a cue finds it on, exactly as playing through would",
          [h.item.id for h in CORE.external_at(18.0, prev=2.0, jumped=True)[0].items]
          == ["c"])
    check("explain shows what is on, for the designer and MCP",
          CORE.explain(4.0)["external"][0] == {
              "row": "osc", "output": "osc",
              "items": [{"item": "a", "progress": 0.5}]})
    check("an external row's span counts toward the timeline's", CORE.span == (0, 24))
    try:
        tl.Timeline.from_rows([{"id": "x", "type": "external"}])
        refused = False
    except tl.TimelineError as exc:
        refused = "names no output" in str(exc)
    check("a row with no output is refused by the core too", refused)

    # -- 2. through the compiler: timelines, routines, loops ----------------------
    print("\n2. through the compiler")
    rigging = programmod.load_rigging(REPO / "events" / "despacio")
    ping = {"kind": "klights.routine", "version": 1, "id": "ping", "bars": 1,
            "loop": True, "roles": {"movers": {"default": "movers"}},
            "rows": [{"id": "vj", "type": "external", "output": "osc",
                      "items": [{"id": "hit", "at": 0, "len": 1,
                                 "on": {"address": "/ping", "args": ["$bar"]}}]}]}
    check("a routine with only an OSC row validates", sf.validate("routine", ping).ok)
    timeline = tl.Timeline.from_rows(
        [{"id": "scene", "type": "clips", "target": "scene",
          "items": [{"id": "p", "kind": "routine", "routine": "ping", "at": 8,
                     "len": 16}]},
         *ROWS], sf.timeline_channels)
    prog = programmod.compile(timeline, {"ping": ping}, rigging)
    check("it compiles clean", prog.problems == [], f"{prog.problems}")
    prog.begin(8.5)
    where = sorted({w for w, _ in prog.external})
    check("a program's frame holds its own rows and its routine clip's",
          where == ["", "scene/p#0"], f"{where}")
    keys = []
    for beat in (8.5, 9.5, 12.5, 13.0):
        prog.begin(beat, prev=beat - 0.5)
        keys.append(sorted(a.key for a in outputsmod.gather((("timeline", "t", prog),))
                           if a.item is not None and a.item.id == "hit"))
    check("each pass of a looping routine is its own cue, so it fires again",
          keys[0] and keys[2] and keys[0] != keys[2] and keys[1] == [],
          f"{keys}")
    plain = programmod.compile(tl.Timeline.from_rows([]), {}, rigging)
    plain.begin(4.0)
    check("a program with no external rows does no external work",
          plain.external == () and plain._has_external is False)

    # -- 3. who owns an output ---------------------------------------------------
    print("\n3. who owns an output")
    template_prog = Prog(CORE).at(4.0)
    lights_only = Prog(tl.Timeline.from_rows([]))
    lights_only.at(4.0)
    both = outputsmod.gather((("timeline", "t", lights_only),
                              ("template", "p", template_prog)))
    check("a timeline with no OSC rows leaves OSC to the template",
          {a.source for a in both} == {"template"} and len(both) == 2)
    drawn = Prog(CORE).at(4.0)
    owned = outputsmod.gather((("timeline", "t", drawn),
                               ("template", "p", Prog(CORE).at(4.0))))
    check("a timeline with OSC rows owns OSC for its track: the template's are silent",
          {a.source for a in owned} == {"timeline"})

    # -- 4. the wire --------------------------------------------------------------
    print("\n4. OSC encoding")
    for args in ([1, 2.5, "text"], [], ["ü"]):
        check(f"the engine's encoder matches the bridge's, byte for byte: {args}",
              outputsmod.osc_encode("/x/y", args) == bridgemod.osc_args("/x/y", *args))
    check("and the engine's reader reads it back",
          syncmod.decode_osc(outputsmod.osc_encode("/x", [7, 0.5, "hi"]))
          == ("/x", [7, 0.5, "hi"]))
    check("an integer too big for 32 bits goes as a float, not an exception",
          syncmod.decode_osc(outputsmod.osc_encode("/x", [2 ** 40]))[1]
          == [float(2 ** 40)])
    big = outputsmod.osc_encode("/x", [1e39, 10 ** 40, -1e300, float("inf")])
    check("a number past a 32-bit float's range is sent as its largest, not an "
          "exception out of a frame -- a curve of $value * 1e40 is a typo, not a crash",
          syncmod.decode_osc(big)[1] == [outputsmod.FLOAT32_MAX] * 2
          + [-outputsmod.FLOAT32_MAX, outputsmod.FLOAT32_MAX], f"{syncmod.decode_osc(big)}")
    f = outputsmod.ProgramFrame(mode="timeline", beat=161.0)
    act = outputsmod.Active("k", "osc", CORE.external_rows[0], None, 0.25, 0.75)
    check("tokens: $beat, $bar, $phase, $progress, $value",
          outputsmod.render(["$beat", "$bar", "$phase", "$progress", "$value", 3, "s"],
                            act, f) == [161.0, 41, 0.25, 0.25, 0.75, 3, "s"])

    # -- 5. OSC over a real socket ------------------------------------------------
    print("\n5. OSC out")
    ear = Listener()
    out = outputsmod.OscOut("127.0.0.1", ear.port)
    p = Prog(CORE)
    now = 1000.0
    out.send(frame(p, 0.05), now)
    got = ear.drain()
    addrs = [m[0] for m in got]
    check("a cue coming on sends its on message, tokens filled",
          ("/a/on", [1, 1]) in got, f"{got}")
    check("its while message and the row's curve go at once too",
          "/a/p" in addrs and "/v" in addrs)
    whiles = 0
    beat = 0.05
    for i in range(40):               # one second at 40 frames a second
        now += 0.025
        prev, beat = beat, beat + 0.025 * (128 / 60)
        out.send(frame(p, beat, prev), now)
    got = ear.drain()
    whiles = sum(1 for m in got if m[0] == "/a/p")
    check("while a cue plays, its changing value goes at most 30 times a second",
          15 <= whiles <= 31, f"{whiles}")
    check("and the cue's on message is not sent again", all(m[0] != "/a/on" for m in got))
    prev, beat = beat, 8.95
    out.send(frame(p, beat, prev), now := now + 0.025)
    got = ear.drain()
    check("leaving a cue sends its off message, with its progress",
          any(m[0] == "/a/off" for m in got), f"{got}")
    prev, beat = beat, 9.05
    out.send(frame(p, beat, prev), now := now + 0.025)
    got = ear.drain()
    check("a cue too short for a frame: on, then off, in that order",
          [m[0] for m in got if m[0].startswith("/b")] == ["/b/on", "/b/off"], f"{got}")
    out.send(frame(p, 18.0, beat, jumped=True), now := now + 0.025)
    got = ear.drain()
    check("a jump into a cue turns it on", ("/c/on", ["club"]) in got, f"{got}")
    for _ in range(10):
        out.send(frame(p, 18.2, 18.0), now := now + 0.05)
    got = ear.drain()
    check("a while message that never changes is sent once, not every frame",
          sum(1 for m in got if m[0] == "/c/still") == 0, f"{got}")
    out.send(frame(p, 2.0, 18.2, jumped=True), now := now + 0.025)
    got = [m[0] for m in ear.drain()]
    check("a jump back: the cue we left has no off of its own to send, the one "
          "we land in comes on again", "/a/on" in got and "/b/on" not in got, f"{got}")
    out.send(None, now := now + 0.025)
    got = [m[0] for m in ear.drain()]
    check("when nothing drives any more, every cue still on goes off",
          got == ["/a/off"], f"{got}")
    check("and the counts say what was sent", out.public()["sent"] > 40
          and out.public()["errors"] == 0 and out.public()["on"] == 0)

    class Broken:
        def sendto(self, data, target):
            raise OSError("network is unreachable")

        def close(self):
            pass

    sick = outputsmod.OscOut("10.0.0.9", 7000, sock=Broken())
    sick.send(frame(Prog(CORE), 1.0), 0.0)
    check("a send that fails is counted, never raised into the output thread",
          sick.errors >= 2 and "unreachable" in sick.last_error, f"{sick.public()}")

    # -- 6. where it goes ------------------------------------------------------------
    print("\n6. configuring")
    outs = outputsmod.Outputs()
    said = outs.configure({"osc": {"host": "10.0.0.5", "port": 7000}},
                          {"osc": {"host": "127.0.0.1"}})
    check("show.json says where; this machine's klights.local.json wins, key by key",
          outs.osc.target == ("127.0.0.1", 7000) and said == "outputs: OSC to 127.0.0.1:7000",
          f"{said}")
    local, why = outputsmod.local_override({"osc": {"host": "127.0.0.1"}})
    check("this machine's override is checked for shape only: a host with "
          "show.json's port is a fine override", local == {"osc": {"host": "127.0.0.1"}}
          and why is None, f"{why}")
    local, why = outputsmod.local_override({"osc": {"host": "vj.local"},
                                            "timecode": {"fps": 31}})
    check("and a bad field in it is left for configure to turn off that one output, "
          "not the whole override",
          local is not None and why is None)
    for bad in (["osc"], {"osc": "127.0.0.1"}, "127.0.0.1"):
        check(f"one that is not outputs at all is ignored, said why: {bad!r}",
              outputsmod.local_override(bad)[0] is None
              and "must be" in (outputsmod.local_override(bad)[1] or ""))
    check("and none is none", outputsmod.local_override(None) == (None, None))
    kept = outs.osc
    check("the same again changes nothing", outs.configure(
        {"osc": {"host": "10.0.0.5", "port": 7000}}, {"osc": {"host": "127.0.0.1"}})
        is None and outs.osc is kept)
    outs.configure({"osc": {"host": "127.0.0.1", "port": 7000}}, None)
    check("a reload naming the same place keeps the cues it knows are on",
          outs.osc is kept)
    outs.configure({"osc": {"host": "vj-laptop.local", "port": 7000}}, None)
    check("a host NAME is refused -- resolving one could stall the lights -- and "
          "OSC is off, said why", outs.osc is None
          and "not an IPv4 address" in outs.public()["problems"][0], f"{outs.public()}")
    check("show.json says the same when it is checked",
          not sf.validate("show", {"kind": "klights.show", "version": 1,
                                   "outputs": {"osc": {"host": "vj.local",
                                                       "port": 7000}}}).ok)
    outs.configure({}, None)
    check("and no outputs at all: nothing in the snapshot",
          outs.public() is None and not outs.active)
    outs.close()

    # -- 6b. timecode ----------------------------------------------------------------
    print("\n6b. ArtTimeCode")
    golden = bytes.fromhex("4172742d4e657400" "0097" "000e" "0000" "04030201" "03")
    check("the packet, byte for byte, as the Art-Net 4 spec lays it out "
          "(01:02:03:04 at SMPTE 30)",
          artnetmod.build_arttimecode(4, 3, 2, 1, 3) == golden
          and len(golden) == 19)
    tc = outputsmod.timecode_at
    check("whole-frame rates count plainly",
          tc(75.0, 30) == (0, 15, 1, 0) and tc(3599.99, 25) == (24, 59, 59, 0)
          and tc(1.5, 24) == (12, 1, 0, 0))
    check("hours wrap at 24, as timecode does", tc(86401.0, 30) == (0, 1, 0, 0))

    def df(n):          # the time of drop-frame frame n, safely inside it
        return outputsmod.timecode_text(tc((n + 0.5) * 1001 / 30000, 29.97), 29.97)

    check("29.97 is drop-frame: ;00 and ;01 are skipped at each new minute...",
          df(1799) == "00:00:59;29" and df(1800) == "00:01:00;02", f"{df(1800)}")
    check("...except every tenth minute, so ten minutes is ten minutes",
          df(17981) == "00:09:59;29" and df(17982) == "00:10:00;00"
          and df(17982 + 1800) == "00:11:00;02", f"{df(17982)}")

    clock_ear = Listener()
    tco = outputsmod.TimecodeOut("127.0.0.1", clock_ear.port, fps=25)

    def at(time_s, playing=True, armed=True):
        return outputsmod.ProgramFrame(mode="timeline", armed=armed, time_s=time_s,
                                       playing=playing)

    for i in range(40):                       # one second of 40 fps frames
        tco.send(at(75.0 + i * 0.025), 0.0)
    packets = [artnetmod.build_arttimecode(*tc(75.0 + i * 0.025, 25), 1)
               for i in range(40)]
    got = clock_ear.raw()
    check("sent each time its frame changes -- 25 a second at 25 fps, never twice "
          "for one frame", len(got) == len(set(packets)) and 24 <= len(got) <= 26
          and got == list(dict.fromkeys(packets)), f"{len(got)}")
    tco.send(at(200.0), 0.0)
    check("a hot cue jumps it at once", clock_ear.raw()
          == [artnetmod.build_arttimecode(*tc(200.0, 25), 1)])
    for frame_ in (at(201.0, playing=False), at(201.0, armed=False), at(None),
                   at(-0.5), None):
        tco.send(frame_, 0.0)
    check("silent while paused, disarmed, unmatched, before the track's start, or "
          "with nothing on stage", clock_ear.raw() == [] and tco.last is None)
    tco.send(at(201.0), 0.0)
    check("and playing again it goes at once, even on the frame it stopped on",
          len(clock_ear.raw()) == 1)
    check("its status says where, at what rate, and the time it last sent",
          tco.public() == {"target": f"127.0.0.1:{clock_ear.port}", "fps": 25,
                           "sent": tco.sent, "errors": 0, "last_error": None,
                           "now": "00:03:21:00"}, f"{tco.public()}")
    tco.close()

    outs = outputsmod.Outputs()
    said = outs.configure({"timecode": {}}, None)
    check("timecode: {} turns it on -- broadcast, Art-Net's port, 30 fps",
          outs.timecode.target == ("255.255.255.255", 6454) and outs.timecode.fps == 30
          and "ArtTimeCode (30 fps) to 255.255.255.255:6454" in said, f"{said}")
    outs.configure({"timecode": {"fps": 60}}, None)
    check("a rate timecode does not have is refused, said why",
          outs.timecode is None and "fps 60" in outs.public()["problems"][0])
    check("and show.json says so when it is checked",
          not sf.validate("show", {"kind": "klights.show", "version": 1,
                                   "outputs": {"timecode": {"fps": 60}}}).ok)
    outs.close()

    # -- 6c. MIDI ----------------------------------------------------------------------
    print("\n6c. MIDI, through the sidecar")
    MIDI_ROWS = [
        {"id": "keys", "type": "external", "output": "midi", "channel": 2, "items": [
            {"id": "n", "at": 0, "len": 4, "note": 60, "velocity": 90},
            {"id": "c", "at": 4, "len": 4, "cc": 7, "value": 100, "off_value": 0},
            {"id": "p", "at": 8, "len": 0.01, "pc": 5, "channel": 10},
            {"id": "z", "at": 12, "len": 4, "note": 64}]},
        {"id": "fader", "type": "external", "output": "midi", "cc": 1,
         "points": [[0, 0.0], [16, 1.0]]},
    ]
    for row in MIDI_ROWS:
        check(f"the MIDI rows validate: {row['id']}",
              sf.validate("timeline", {"kind": "klights.timeline", "version": 1,
                                       "track": "x", "rows": [row]}).ok)
    midi_tl = tl.Timeline.from_rows(MIDI_ROWS)
    mp = Prog(midi_tl)
    midi_ear = Listener()
    mo = outputsmod.MidiOut("127.0.0.1", midi_ear.port)

    def batches():
        return [json.loads(d) for d in midi_ear.raw()]

    mo.send(frame(mp, 0.5), 0.0)
    got = batches()
    check("one datagram a frame, in the sidecar's wire format",
          len(got) == 1 and got[0]["klights"] == "klights.midi/1", f"{got}")
    msgs = got[0]["messages"]
    check("a note cue starts its note, on the lane's channel, at its velocity",
          {"type": "note_on", "channel": 2, "note": 60, "velocity": 90} in msgs, f"{msgs}")
    check("the curve's 0-1 goes to its CC as 0-127",
          any(m["type"] == "control_change" and m["control"] == 1
              and m["value"] == 4 for m in msgs), f"{msgs}")
    mo.send(frame(mp, 0.6, 0.5), 0.01)
    check("nothing new, nothing sent -- the curve is still on 4 and too soon anyway",
          batches() == [])
    mo.send(frame(mp, 4.5, 0.6), 1.0)
    msgs = [m for b in batches() for m in b["messages"]]
    check("leaving the note stops it; the CC cue sets its value",
          {"type": "note_off", "channel": 2, "note": 60, "velocity": 0} in msgs
          and {"type": "control_change", "channel": 2, "control": 7, "value": 100} in msgs,
          f"{msgs}")
    mo.send(frame(mp, 8.5, 4.5), 2.0)
    msgs = [m for b in batches() for m in b["messages"]]
    check("a CC cue's off_value is what it leaves behind",
          {"type": "control_change", "channel": 2, "control": 7, "value": 0} in msgs)
    check("a program change shorter than a frame still goes, on its own channel",
          {"type": "program_change", "channel": 10, "program": 5} in msgs, f"{msgs}")
    mo.send(frame(mp, 13.0, 8.5), 3.0)
    batches()
    mo.close()
    msgs = [m for b in batches() for m in b["messages"]]
    check("closing stops every note still sounding -- a stuck note outlives us",
          msgs == [{"type": "note_off", "channel": 2, "note": 64, "velocity": 0}], f"{msgs}")
    midi_ear.close()

    outs = outputsmod.Outputs()
    said = outs.configure({"midi": {}}, None)
    check("midi: {} is the sidecar on this machine at its default port",
          outs.midi.target == ("127.0.0.1", 9123)
          and "MIDI to the sidecar at 127.0.0.1:9123" in said, f"{said}")
    outs.close()

    print("\n6d. the sidecar")

    class Played:
        name = "recorder"

        def __init__(self):
            self.messages = []

        def send(self, m):
            self.messages.append(m)

        def close(self):
            pass

    rec = Played()
    car = sidecarmod.Sidecar(rec)
    wire = lambda *ms: json.dumps({"klights": "klights.midi/1",  # noqa: E731
                                   "messages": list(ms)}).encode()
    car.handle(wire({"type": "note_on", "channel": 1, "note": 60, "velocity": 100},
                    {"type": "note_on", "channel": 3, "note": 67, "velocity": 80}))
    car.handle(wire({"type": "note_off", "channel": 1, "note": 60, "velocity": 0}))
    check("the sidecar plays what arrives, and keeps track of what is sounding",
          len(rec.messages) == 3 and car.sounding == {(3, 67)})
    bad = [
        (b"not json", "not JSON"),
        (json.dumps({"klights": "other", "messages": []}).encode(), "not a klights.midi/1"),
        (wire({"type": "sysex", "channel": 1}), "type must be"),
        (wire({"type": "note_on", "channel": 0, "note": 60, "velocity": 1}), "channel must be 1-16"),
        (wire({"type": "note_on", "channel": 1, "note": 128, "velocity": 1}), "note must be 0-127"),
        (wire({"type": "control_change", "channel": 1, "control": 7, "value": True}),
         "value must be 0-127"),
        (wire(*[{"type": "program_change", "channel": 1, "program": 1}] * 257),
         "at most 256"),
    ]
    for data, why in bad:
        messages, reason = sidecarmod.decode(data)
        check(f"the sidecar refuses: {why}", messages is None and why in reason, f"{reason}")
    before = len(rec.messages)
    car.handle(wire({"type": "note_on", "channel": 1, "note": 61, "velocity": 1},
                    {"type": "note_on", "channel": 1, "note": 999, "velocity": 1}))
    check("a datagram with one bad message plays none of it -- never half-played",
          len(rec.messages) == before and car.rejected == 1)
    car.panic()
    check("on the way out it stops every note it started",
          rec.messages[-1] == {"type": "note_off", "channel": 3, "note": 67, "velocity": 0}
          and car.sounding == set())

    class Unplugged(Played):
        """A port whose driver refuses notes above 64, as an unplugged or
        confused one might -- every one of them, each time."""

        def send(self, m):
            if m.get("note", 0) > 64:
                raise OSError("device unplugged")
            super().send(m)

    flaky = Unplugged()
    car = sidecarmod.Sidecar(flaky)
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        car.handle(wire({"type": "note_on", "channel": 1, "note": 70, "velocity": 9},
                        {"type": "note_on", "channel": 1, "note": 60, "velocity": 9},
                        {"type": "note_on", "channel": 2, "note": 71, "velocity": 9}))
        car.handle(wire({"type": "note_on", "channel": 1, "note": 62, "velocity": 9}))
        car.panic()
    check("a send the driver refuses costs that message, never the sidecar: the "
          "rest still play, and the next datagram too",
          [m.get("note") for m in flaky.messages[:2]] == [60, 62]
          and car.played == 2 and car.failed >= 2, f"{flaky.messages} {car.failed}")
    check("said once per reason, not once a message",
          err.getvalue().count("device unplugged") == 1, err.getvalue())
    check("and on the way out every note is still tried, whatever the one before did",
          {(m["channel"], m["note"]) for m in flaky.messages[2:]} == {(1, 60), (1, 62)}
          and car.sounding == set(), f"{flaky.messages}")

    proc = subprocess.Popen([sys.executable, str(REPO / "bridges" / "midi" / "midi_out.py"),
                             "--fake", "--port", "0"],
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    lines: "queue.Queue[str]" = queue.Queue()
    threading.Thread(target=lambda: [lines.put(l.rstrip()) for l in proc.stdout],
                     daemon=True).start()
    try:
        first = lines.get(timeout=10)
        port = int(first.split()[2].rstrip(",").rsplit(":", 1)[1])
        check("the sidecar runs with --fake, no MIDI library, and says where it listens",
              first.startswith("listening on 127.0.0.1:") and "playing to fake" in first,
              first)
        engine_side = outputsmod.MidiOut("127.0.0.1", port)
        engine_side.send(frame(Prog(midi_tl), 0.5), 0.0)
        heard = []
        try:
            while len(heard) < 2:
                heard.append(lines.get(timeout=5))
        except queue.Empty:
            pass
        check("and plays what the engine's MidiOut sends it",
              "note_on ch2 note=60 velocity=90" in heard
              and "control_change ch1 control=1 value=4" in heard, f"{heard}")
        engine_side.sock.close()
    finally:
        proc.terminate()
        proc.wait(timeout=10)

    # -- 6e. visuals -------------------------------------------------------------------
    print("\n6e. the built-in visuals")

    def vis_errors(*items, warnings=False, **row):
        r = sf.validate("timeline", {"kind": "klights.timeline", "version": 1,
                                     "track": "x", "rows": [
                                         {"id": "v", "type": "external",
                                          "output": "visuals", "items": list(items),
                                          **row}]})
        return r.warnings if warnings else r.errors

    def item(scene=None, **params):
        out = {"id": "i", "at": 0, "len": 8, "params": params}
        if scene:
            out["scene"] = scene
        return out

    check("a scene with its parameters validates",
          vis_errors(item("tunnel", color="@primary", speed=2, depth=12, opacity=0.8)) == [])
    for label, it, needle in (
            ("no scene", item(color="@primary"), "needs a scene"),
            ("a scene that does not exist", item("lasers"), "must be one of"),
            ("a look name for a color", item("wash", color="MH Red"), "a visuals color is"),
            ("a palette role that does not exist", item("wash", color="@tertiary"),
             "not a palette role"),
            ("a number out of range", item("bars", count=500), "from 1 to 64"),
            ("a video without a file", item("video"), "needs a file"),
            ("a video file in a folder", item("video", file="../x.mp4"), "no folders"),
            ("a video file that is not a video", item("video", file="x.gif"), "ending"),
            ("rate as a number", item("video", file="x.mp4", rate=2), "beat, normal")):
        errs = vis_errors(it)
        check(f"visuals refuse {label}", any(needle in e for e in errs), f"{errs}")
    check("a parameter the scene does not read is a warning, not an error",
          any("does not use it" in w for w in vis_errors(item("wash", speed=2),
                                                          warnings=True)))
    check("a visuals row's points are said to be unused",
          any("points are not used" in w for w in vis_errors(
              item("wash"), warnings=True, points=[[0, 1]])))
    club = json.loads((REPO / "shared" / "show-example" / "templates" / "club.json")
                      .read_text())
    club["phrases"]["Chorus"]["visuals"] = {"scene": "lasers"}
    check("a template pick's visuals are checked too",
          not sf.validate("template_set", club).ok)

    folder_dir = Path(tempfile.mkdtemp(prefix="klights-media-"))
    shutil.copytree(REPO / "shared" / "show-example", folder_dir / "show")
    tdoc = json.loads((folder_dir / "show" / "timelines" / "synth-128.json").read_text())
    tdoc["rows"].append({"id": "film", "type": "external", "output": "visuals",
                         "items": [item("video", file="intro.mp4", loop=True)]})
    (folder_dir / "show" / "timelines" / "synth-128.json").write_text(json.dumps(tdoc))
    warned_missing = any("media/intro.mp4, which is not in the show folder" in w
                         for w in sf.load_folder(folder_dir / "show").warnings)
    (folder_dir / "show" / "media").mkdir()
    (folder_dir / "show" / "media" / "intro.mp4").write_bytes(b"\0" * 64)
    check("a video the show folder does not have is said when it loads -- and "
          "not once it is there", warned_missing and not any(
              "intro.mp4" in w for w in sf.load_folder(folder_dir / "show").warnings))
    shutil.rmtree(folder_dir, ignore_errors=True)

    vis_tl = tl.Timeline.from_rows([{"id": "v", "type": "external", "output": "visuals",
                                     "items": [{"id": "a", "at": 0, "len": 8,
                                                "scene": "bars", "params": {"count": 4}},
                                               {"id": "z", "at": 9, "len": 0.01,
                                                "scene": "strobe"}]}])
    vp = Prog(vis_tl).at(9.1, prev=8.95)
    vf = outputsmod.ProgramFrame(mode="timeline", beat=9.1, bpm=128.0,
                                 palette={"primary": (1.0, 0.176, 0.435)},
                                 active=outputsmod.gather((("timeline", "t", vp),)))
    pub = outputsmod.visuals_public(vf)
    check("the snapshot's visuals: the beat, how fast it runs, the palette as hex",
          pub["beat"] == 9.1 and pub["bpm"] == 128.0
          and pub["palette"] == {"primary": "#ff2d6f"}, f"{pub}")
    check("and no cue a frame was too short to show -- a projector cannot flash "
          "for zero frames", pub["items"] == [])
    vp.at(4.0)
    pub = outputsmod.visuals_public(outputsmod.ProgramFrame(
        mode="timeline", beat=4.0, active=outputsmod.gather((("timeline", "t", vp),))))
    check("an item on says its scene, params, and how far into it the show is",
          [(i["scene"], i["params"], i["elapsed"], i["len"]) for i in pub["items"]]
          == [("bars", {"count": 4}, 4.0, 8)], f"{pub}")

    # -- 7. a whole engine ----------------------------------------------------------
    print("\n7. through a whole engine")
    tmp = Path(tempfile.mkdtemp(prefix="klights-outputs-"))
    shows = tmp / "shows"
    shutil.copytree(REPO / "shared" / "show-example", shows)
    show_doc = json.loads((shows / "show.json").read_text())
    show_doc["outputs"] = {"osc": {"host": "127.0.0.1", "port": ear.port}}
    (shows / "show.json").write_text(json.dumps(show_doc, indent=2))
    fan = json.loads((shows / "routines" / "fan-drop.json").read_text())
    fan["rows"].append({"id": "vj", "type": "external", "output": "osc",
                        "items": [{"id": "drop", "at": 0, "len": 4,
                                   "on": {"address": "/template/drop",
                                          "args": ["$bar"]}}]})
    (shows / "routines" / "fan-drop.json").write_text(json.dumps(fan, indent=2))
    sc = servermod.ShowController(
        REPO / "events" / "despacio", show_dir=shows,
        local_outputs={"osc": {"port": ear.port},
                       "timecode": {"host": "127.0.0.1", "port": clock_ear.port,
                                    "fps": 30}})
    sc.worker.start()
    BEAT_S = 60.0 / 128.0
    t = 100.0

    def settle():
        for _ in range(4):
            assert sc.worker.wait_idle(5.0)
            sc._drain()

    def blt(at, title, artist="", album="", duration=0.0, rid=1):
        sc.apply({"type": "sync", "source": "blt", "deck": "1", "title": title,
                  "artist": artist, "album": album, "duration": duration,
                  "rekordbox_id": rid}, None, at)

    def play(start_beat, seconds, hz=25):
        global t
        for i in range(int(seconds * hz)):
            sc.apply({"type": "sync", "source": "blt", "deck": "1",
                      "track_time": (start_beat + (i / hz) / BEAT_S) * BEAT_S,
                      "playing": True, "pitch": 1.0,
                      "beat_number": int(start_beat) + 1}, None, t)
            sc.ctx.time = t
            sc._before_frame()
            sc.runner.sync_clock()
            t += 1.0 / hz

    check("the engine points OSC where the folder says", sc.outputs.osc is not None
          and sc.outputs.osc.target == ("127.0.0.1", ear.port))
    blt(t, "synthetic 128", "kLights", "test track", 180.0)
    t += 0.01
    play(150, 0.2)
    settle()
    ear.drain()
    play(150.2, 0.3)
    check("disarmed, nothing is sent: Follow gates the other outputs as it does "
          "the lights", ear.drain() == [] and clock_ear.raw() == [])
    sc.apply({"type": "follow", "armed": True}, None, t)
    play(159.5, 1.0)
    got = ear.drain()
    check("armed, the drawn track's OSC lane cues the VJ app on the drop",
          ("/composition/layers/1/clips/3/connect", [1]) in got,
          f"{[m[0] for m in got][:6]}")
    check("and its opacity curve is sent", any(
        m[0] == "/composition/layers/1/video/opacity" for m in got))
    clock = clock_ear.raw()
    last = clock[-1] if clock else b""
    check("ArtTimeCode carries the track's position: beat 160.5 at 128 bpm is "
          "1:15.2, so 00:01:15 and some frames",
          len(clock) >= 20 and last[14:19][1:4] == bytes([15, 1, 0])
          and last[18] == 3, f"{len(clock)} {last.hex()}")
    vis = sc.snapshot()["visuals"]
    check("the snapshot's visuals: the drawn track's projector lane on the drop, "
          "at the track's tempo", [(i["scene"], i["source"]) for i in vis["items"]]
          == [("tunnel", "timeline")] and vis["bpm"] == 128.0
          and vis["palette"]["primary"] == "#ff2d6f", f"{vis}")
    check("the snapshot says where OSC goes and how it is doing",
          sc.snapshot()["outputs"]["osc"]["target"] == f"127.0.0.1:{ear.port}"
          and sc.snapshot()["outputs"]["osc"]["sent"] > 0)
    check("and which outputs this machine's klights.local.json sets, over "
          "whatever show.json says",
          sc.snapshot()["outputs"]["local"] == ["osc", "timecode"],
          f"{sc.snapshot()['outputs'].get('local')}")

    t += 2.5
    blt(t, "Unknown Guest Tune", "Guest DJ", "", 240.0, rid=2)
    t += 0.01
    sc.apply({"type": "sync", "source": "rkbx", "phrase_label": "Chorus",
              "phrase_ends_in": 32}, None, t)
    play(10, 0.6)
    got = ear.drain()
    check("a guest the folder does not know sends no timecode -- there is no "
          "track position to share", clock_ear.raw() == [])
    check("leaving the drawn track turns its cue off (the clear message)",
          ("/composition/layers/1/clear", [1]) in got, f"{[m[0] for m in got][:6]}")
    check("and a guest's chorus, from the template, cues the VJ app through the "
          "routine it picks", any(m[0] == "/template/drop" for m in got),
          f"{[m[0] for m in got][:6]}")
    vis = sc.snapshot()["visuals"]
    check("and the template set's chorus scene is on the projector, at the clock's "
          "tempo", [(i["scene"], i["source"]) for i in vis["items"]]
          == [("tunnel", "template")]
          and abs(vis["bpm"] - sc.clock.effective_bpm) < 1e-3, f"{vis}")
    sc.apply({"type": "follow", "armed": False}, None, t)
    play(12, 0.2)
    check("disarming turns nothing on; the cue on goes off quietly (it has no "
          "off message)", not any(m[0] == "/template/drop" for m in ear.drain())
          and sc.outputs.osc.public()["on"] == 0)
    check("and the projector goes dark with the lights' show",
          sc.snapshot()["visuals"]["items"] == [])
    sc.worker.stop()
    sc.outputs.close()
    shutil.rmtree(tmp, ignore_errors=True)
    ear.close()
    clock_ear.close()
finally:
    pass


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("outputs: all checks pass")
