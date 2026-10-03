"""
Tests for playback: when the timeline drives the rig, and when it hands back.

A real ShowController over the despacio rig and a copy of the example show
folder, driven frame by frame by hand: each "frame" is what the runner's loop
does -- the commands, the transport, the runner's sync_clock with its show hook
-- at a stated time, and the states are evaluated from whatever Show the runner
then holds. The worker is the real one, waited for.

The rules decided with the user are each checked by name: Follow starts
disarmed; every hand-over is a cut; a grab lasts until Release, across tracks;
latency is saved to the show folder.

Run: python engine/tests/test_playback.py
"""

import json
import shutil
import sys
import tempfile
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import blocks as blocksmod  # noqa: E402
from engine import playback as playbackmod  # noqa: E402
from engine import program as programmod  # noqa: E402
from engine import server as servermod  # noqa: E402
from engine import state as statemod  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


tmp = Path(tempfile.mkdtemp(prefix="klights-playback-"))
shows = tmp / "shows"
shutil.copytree(REPO / "shared" / "show-example", shows)
# A second prepped track with phrases and NO timeline: milestone 2's templates
# play it from its own phrases.
_other = json.loads((shows / "tracks" / "synth-128.json").read_text())
_other.update(id="no-timeline")
_other["identity"] = {**_other["identity"], "title": "no timeline tune"}
_other.pop("ids", None)
_other.pop("aliases", None)
(shows / "tracks" / "no-timeline.json").write_text(json.dumps(_other, indent=2))

compile_threads: list[str] = []
real_compile = programmod.compile


def spy_compile(*args, **kwargs):
    compile_threads.append(threading.current_thread().name)
    return real_compile(*args, **kwargs)


programmod.compile = spy_compile

sc = servermod.ShowController(REPO / "events" / "despacio", show_dir=shows)
sc.worker.start()
BEAT_S = 60.0 / 128.0
HOT = blocksmod.parse_hex("#ff2d6f")
MOVERS = [f for f in sc.rig.fixtures if f.head is not None]
t = 100.0


def settle():
    for _ in range(4):
        assert sc.worker.wait_idle(5.0)
        sc._drain()


def frame(at):
    """One pass of the runner's loop at engine time `at`, then evaluate."""
    sc.ctx.time = at
    sc._before_frame()
    sc.runner.sync_clock()
    return statemod.evaluate_stack(sc.ctx, sc.runner.show)


def blt(at, title, artist="", album="", duration=0.0, rid=1):
    sc.apply({"type": "sync", "source": "blt", "deck": "1", "title": title,
              "artist": artist, "album": album, "duration": duration,
              "rekordbox_id": rid}, None, at)


def pos(at, beat, playing=True):
    sc.apply({"type": "sync", "source": "blt", "deck": "1",
              "track_time": beat * BEAT_S, "playing": playing, "pitch": 1.0,
              "beat_number": int(beat) + 1}, None, at)


def play(start_beat, seconds, hz=25, playing=True):
    """The deck playing from `start_beat` for `seconds`, a frame per packet."""
    global t
    states = None
    for i in range(int(seconds * hz)):
        # A paused deck reports the same position until it plays again.
        pos(t, start_beat + ((i / hz) / BEAT_S if playing else 0.0),
            playing=playing)
        states = frame(t)
        t += 1.0 / hz
    return states


def synth():
    global t
    blt(t, "synthetic 128", "kLights", "test track", 180.0)
    t += 0.01


def status():
    return sc.snapshot()["program"]


try:
    # -- 1. disarmed, then armed ---------------------------------------------
    print("\n1. Follow starts disarmed")
    synth()
    play(150, 0.2)
    settle()
    s = play(150.2, 0.2)
    st = status()
    check("disarmed at startup, as show.json says -- and printed so",
          st["armed"] is False and st["reason"] == "disarmed", f"{st}")
    check("so the operator's show runs, though the track matched and compiled",
          sc.runner.show is not sc.player.program.show
          and sc.snapshot()["track"]["match"]["track_id"] == "synth-128")
    check("the program was compiled on the worker, never the output thread",
          compile_threads and set(compile_threads) == {"klights-worker"},
          f"{compile_threads}")

    sc.apply({"type": "follow", "armed": True}, None, t)
    s = play(160, 0.2)
    st = status()
    check("armed: the timeline drives", st["engaged"] and st["mode"] == "timeline"
          and sc.runner.show is sc.player.program.show, f"{st}")
    check("and the chorus routine is on the movers in the Hot palette",
          all(abs(s[f.fid].color[0] - HOT[0]) < 1e-3
              and abs(s[f.fid].color[1] - HOT[1]) < 1e-3 for f in MOVERS),
          f"{[s[f.fid].color for f in MOVERS]}")
    check("every lane says the timeline has it",
          st["lanes"] == {"movement": "timeline", "color": "timeline",
                          "level": "timeline"} and st["bar"] == 41, f"{st}")
    check("and the hand-over was a cut, not a fade (decided with the user)",
          sc.runner._fading_from is None)
    show_obj = sc.runner.show
    play(160.4, 0.4)
    check("the program's Show stays the same object frame after frame",
          sc.runner.show is show_obj)

    # -- 2. grabs ------------------------------------------------------------
    print("\n2. grabs")
    sc.apply({"type": "select_look", "name": "MH Blue"}, None, t)
    s = play(161, 0.2)
    st = status()
    check("picking a colour look takes the colour lane from the timeline",
          st["lanes"]["color"] == "operator" and st["grabbed"] == ["color"]
          and all(s[f.fid].color == (0.0, 0.0, 1.0) for f in MOVERS), f"{st}")
    check("and only the colour lane: movement is still the timeline's",
          st["lanes"]["movement"] == "timeline")
    t += 2.5
    blt(t, "Unknown Guest Tune", "Guest DJ", "", 240.0, rid=2)
    t += 0.01
    play(10, 0.3)
    check("an unmatched guest track gets the template set instead (milestone 2): "
          "with no phrase from the deck, the bar-count cycle on the clock",
          status()["mode"] == "template" and status()["template"]["label"] == "bars"
          and status()["lanes"]["movement"] == "template", f"{status()}")
    check("and the grab still holds the colour lane",
          status()["lanes"]["color"] == "operator")
    t += 2.5
    synth()
    play(162, 0.2)
    settle()
    s = play(162.2, 0.2)
    check("and the grab is still held when a matched track comes back "
          "(it lasts until Release, decided with the user)",
          status()["lanes"]["color"] == "operator"
          and all(s[f.fid].color == (0.0, 0.0, 1.0) for f in MOVERS),
          f"{status()}")
    sc.apply({"type": "program_release", "slot": "color"}, None, t)
    s = play(163, 0.2)
    check("Release gives the lane back",
          status()["lanes"]["color"] == "timeline" and status()["grabbed"] == []
          and abs(s[MOVERS[0].fid].color[0] - HOT[0]) < 1e-3, f"{status()}")

    cue_show = sc.runner.show
    sc.apply({"type": "go"}, None, t)
    play(163.2, 0.2)
    check("a cue GO while the timeline drives takes every lane -- and does not "
          "swap the timeline's show out",
          sc.runner.show is cue_show and sorted(status()["grabbed"])
          == ["color", "level", "movement"], f"{status()['grabbed']}")
    sc.apply({"type": "program_release"}, None, t)
    play(163.4, 0.1)
    check("and Release with no lane gives them all back",
          status()["grabbed"] == [])

    # -- 2b. templates (milestone 2) -------------------------------------------
    print("\n2b. templates")
    st = status()
    check("the show folder's set is active from the start",
          st["set"] == "club" and {"id": "club", "name": "Club"} in st["sets"],
          f"{st['set']} {st['sets']}")
    t += 2.5
    blt(t, "no timeline tune", "kLights", "test track", 180.0, rid=3)
    t += 0.01
    play(161, 0.2)
    settle()
    s = play(162, 0.3)
    st = status()
    check("a matched track with no timeline plays its own phrases' template: "
          "the chorus is fan-drop, from the chorus's first beat",
          st["mode"] == "template" and st["template"]["label"] == "Chorus"
          and st["template"]["routine"] == "fan-drop"
          and st["template"]["start"] == 160.0, f"{st}")
    check("in the pick's palette (Hot)",
          abs(s[MOVERS[0].fid].color[0] - HOT[0]) < 0.05, f"{s[MOVERS[0].fid].color}")
    check("every lane says the template has it",
          set(st["lanes"].values()) == {"template"}, f"{st['lanes']}")

    t += 2.5
    blt(t, "Another Guest", "Guest DJ", "", 200.0, rid=4)
    t += 0.01
    sc.apply({"type": "sync", "source": "rkbx", "phrase_label": "Chorus",
              "phrase_ends_in": 32}, None, t)
    play(10, 0.3)
    st = status()
    check("a guest track with a live phrase from the deck plays that phrase's pick, "
          "from where the deck said it began (not the last track's chorus)",
          st["mode"] == "template" and st["template"]["label"] == "Chorus"
          and st["template"]["routine"] == "fan-drop"
          and st["template"]["start"] == sc.clock.phrase_start, f"{st}")

    sc.apply({"type": "template_set", "id": None}, None, t)
    check("switching set waits for the next downbeat",
          sc.player.pending == playbackmod.NO_SET and sc.player.set_id == "club")
    play(10.3, 2.5)
    st = status()
    check("and then takes over: templates off, the operator's show again",
          st["mode"] == "fallback" and st["set"] is None and st["pending"] is None,
          f"{st}")
    try:
        sc.apply({"type": "template_set", "id": "nope"}, None, t)
        refused = False
    except ValueError as exc:
        refused = "no template set" in str(exc)
    check("an unknown set is refused", refused)
    sc.apply({"type": "template_set", "id": "club"}, None, t)
    settle()
    play(12, 2.5)
    check("and back on at the next downbeat", status()["set"] == "club"
          and status()["mode"] == "template", f"{status()}")
    sc.apply({"type": "follow", "armed": False}, None, t)
    play(14, 0.2)
    check("disarmed, no template runs (Follow gates it, decided with the user)",
          status()["mode"] == "fallback" and status()["reason"] == "disarmed")
    sc.apply({"type": "follow", "armed": True}, None, t)
    t += 2.5
    synth()
    play(168, 0.2)
    settle()
    play(168.2, 0.2)

    # -- 3. pause policies ---------------------------------------------------
    print("\n3. pausing")
    sc.player.policy = "freeze"
    play(170, 0.2)
    a = play(170.2, 0.2, playing=False)
    b = play(170.2, 0.4, playing=False)
    check("freeze: a paused deck holds the frame it stopped on",
          a[MOVERS[0].fid].aim == b[MOVERS[0].fid].aim and status()["mode"] == "timeline")
    sc.player.policy = "continue"
    play(171, 0.2)
    a = play(171.2, 0.1, playing=False)
    b = play(171.2, 0.3, playing=False)
    check("continue: a paused deck keeps the lights moving at its tempo",
          a[MOVERS[0].fid].aim != b[MOVERS[0].fid].aim)
    sc.player.policy = "idle"
    play(172, 0.2)
    play(172.2, 1.0, playing=False)
    check("idle: within the grace period it holds, like freeze",
          status()["mode"] == "timeline")
    play(172.2, 3.5, playing=False)
    st = status()
    check("past it, the show's idle routine takes over -- with a cut",
          st["mode"] == "idle" and sc.runner.show is sc.player.idle.show
          and sc.runner._fading_from is None, f"{st}")
    play(172.2, 0.2)
    check("and playing again hands straight back to the timeline",
          status()["mode"] == "timeline")

    # -- 4. disarm -----------------------------------------------------------
    print("\n4. disarming")
    sc.apply({"type": "follow", "armed": False}, None, t)
    play(180, 0.2)
    check("disarmed: the operator's show, at once (a cut)",
          status()["mode"] == "fallback" and sc.runner._fading_from is None
          and sc.runner.show is not sc.player.program.show)
    sc.apply({"type": "select_look", "name": "MH Red"}, None, t)
    sc.apply({"type": "follow", "armed": True}, None, t)
    play(181, 0.2)
    check("looks picked while disarmed grab nothing: arming hands every lane "
          "to the timeline", status()["grabbed"] == [], f"{status()}")

    # -- 5. latency ----------------------------------------------------------
    print("\n5. latency")
    operator = servermod.Client(id="op", name="op", tier="operate")
    try:
        sc.apply({"type": "show_latency", "source": "blt", "ms": 40}, operator, t)
        refused = False
    except ValueError as exc:
        refused = "needs configure" in str(exc)
    check("changing the latency is configure-tier", refused)
    reply = sc.apply({"type": "show_latency", "source": "blt", "ms": 40}, None, t)
    check("it applies at once", abs(sc.transport.latency_s["blt"] - 0.04) < 1e-9
          and reply == {"source": "blt", "ms": 40})
    settle()
    saved = json.loads((shows / "show.json").read_text())
    check("and is saved in the show folder's show.json (decided with the user), "
          "keeping the rest of the file",
          saved["sources"]["blt"]["latency_ms"] == 40
          and saved["pause"]["idle_routine"] == "idle-orbit", f"{saved['sources']}")
    check("the snapshot shows it", status()["latency_ms"]["blt"] == 40,
          f"{status()['latency_ms']}")
    for bad in ({"source": "blt", "ms": 5000}, {"source": "", "ms": 0},
                {"source": "blt", "ms": "40"}):
        try:
            sc.apply({"type": "show_latency", **bad}, None, t)
            ok = False
        except ValueError:
            ok = True
        check(f"refuses {bad}", ok)

    # -- 5b. a show that cannot be built --------------------------------------
    print("\n5b. a show that cannot be built")
    attempts: list[int] = []

    def broken_compile(timeline, routines, rigging, where="timeline"):
        if not str(where).startswith("timelines/"):
            return real_compile(timeline, routines, rigging, where)
        attempts.append(1)
        raise RuntimeError("a bug in the compiler")

    programmod.compile = broken_compile
    sc.player.recompile()
    play(190, 0.4)
    settle()
    play(191, 0.4)
    check("a compile that raises is said once, and the next layer down runs -- "
          "the template here",
          status()["mode"] == "template" and status()["reason"] == "compile failed"
          and any("could not be built" in n for n in sc.notices), f"{status()}")
    check("and it is not retried every frame", len(attempts) == 1, f"{len(attempts)}")
    programmod.compile = spy_compile
    sc.player.recompile()
    settle()
    play(192, 0.4)
    check("a rig or folder reload tries again", status()["mode"] == "timeline",
          f"{status()}")

    # -- 6. without a show folder --------------------------------------------
    print("\n6. without a show folder")
    plain = servermod.ShowController(REPO / "events" / "despacio")
    check("no player, no hook, no program section", plain.player is None
          and plain.runner.choose_show is None
          and plain.snapshot()["program"] is None)
    for cmd in ({"type": "follow", "armed": True}, {"type": "program_release"}):
        try:
            plain.apply(cmd, None)
            ok = False
        except ValueError as exc:
            ok = "--show-dir" in str(exc)
        check(f"{cmd['type']} says to start with --show-dir", ok)
finally:
    programmod.compile = real_compile
    sc.worker.stop()
    shutil.rmtree(tmp, ignore_errors=True)


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("playback: all checks pass")
