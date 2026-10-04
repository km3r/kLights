"""
Tests for the other outputs (milestone 3): external rows in the timeline core,
collected through routines and templates into a ProgramFrame, and sent as OSC.

The OSC goes over a real UDP socket to a listener in this process, and is
decoded by the engine's own OSC reader. The last section runs a whole
ShowController over a copy of the example show folder, pointed at that
listener, and plays the synthetic track and a guest through it.

Run: python engine/tests/test_outputs.py
"""

import json
import shutil
import socket
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bridges" / "prolink"))

import bridge as bridgemod  # noqa: E402
from engine import outputs as outputsmod  # noqa: E402
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
    sc = servermod.ShowController(REPO / "events" / "despacio", show_dir=shows,
                                  local_outputs={"osc": {"port": ear.port}})
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
          "the lights", ear.drain() == [])
    sc.apply({"type": "follow", "armed": True}, None, t)
    play(159.5, 1.0)
    got = ear.drain()
    check("armed, the drawn track's OSC lane cues the VJ app on the drop",
          ("/composition/layers/1/clips/3/connect", [1]) in got,
          f"{[m[0] for m in got][:6]}")
    check("and its opacity curve is sent", any(
        m[0] == "/composition/layers/1/video/opacity" for m in got))
    check("the snapshot says where OSC goes and how it is doing",
          sc.snapshot()["outputs"]["osc"]["target"] == f"127.0.0.1:{ear.port}"
          and sc.snapshot()["outputs"]["osc"]["sent"] > 0)

    t += 2.5
    blt(t, "Unknown Guest Tune", "Guest DJ", "", 240.0, rid=2)
    t += 0.01
    sc.apply({"type": "sync", "source": "rkbx", "phrase_label": "Chorus",
              "phrase_ends_in": 32}, None, t)
    play(10, 0.6)
    got = ear.drain()
    check("leaving the drawn track turns its cue off (the clear message)",
          ("/composition/layers/1/clear", [1]) in got, f"{[m[0] for m in got][:6]}")
    check("and a guest's chorus, from the template, cues the VJ app through the "
          "routine it picks", any(m[0] == "/template/drop" for m in got),
          f"{[m[0] for m in got][:6]}")
    sc.apply({"type": "follow", "armed": False}, None, t)
    play(12, 0.2)
    check("disarming turns nothing on; the cue on goes off quietly (it has no "
          "off message)", not any(m[0] == "/template/drop" for m in ear.drain())
          and sc.outputs.osc.public()["on"] == 0)
    sc.worker.stop()
    sc.outputs.close()
    shutil.rmtree(tmp, ignore_errors=True)
    ear.close()
finally:
    pass


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("outputs: all checks pass")
