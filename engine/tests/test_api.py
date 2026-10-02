"""
Tests for what the designer talks to: the HTTP reads, audio with Range, and the
draft / save / preview commands that answer from the worker.

The HTTP half runs a real ShowServer on a free port over a copy of the example
show folder, and reads with urllib. The command half drives the controller's
queue directly with a client whose replies are captured, so every answer --
including the ones that arrive after the worker finishes -- is checked exactly.

Run: python engine/tests/test_api.py
"""

import json
import pathlib
import re
import shutil
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import api as apimod  # noqa: E402
from engine import server as servermod  # noqa: E402
from engine import showfiles  # noqa: E402
from engine import state as statemod  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


tmp = Path(tempfile.mkdtemp(prefix="klights-api-"))
shows = tmp / "shows"
shutil.copytree(REPO / "shared" / "show-example", shows)
music = tmp / "music"
music.mkdir()
AUDIO = bytes(range(256)) * 40                       # 10240 bytes of "audio"
(music / "synthetic-128.mp3").write_bytes(AUDIO)
track_path = shows / "tracks" / "synth-128.json"
track = json.loads(track_path.read_text())
track["audio"] = [{"host": "elsewhere", "path": "/nowhere/synthetic-128.mp3"}]
track_path.write_text(json.dumps(track, indent=2))

sc = servermod.ShowController(REPO / "events" / "despacio", show_dir=shows)
srv = servermod.ShowServer(sc, port=0, token="tok")
srv.audio_roots = [str(music)]
srv.start()
port = srv.httpd.server_address[1]
sc.worker.start()


def get(path, token=None, headers=None):
    url = f"http://127.0.0.1:{port}{path}" + (f"?token={token}" if token else "")
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def jget(path, **kw):
    status, headers, body = get(path, **kw)
    return status, json.loads(body or b"null")


try:
    # -- 1. reads ------------------------------------------------------------
    print("\n1. reading the show folder over HTTP")
    status, body = jget("/api/tracks")
    line = body["tracks"][0] if status == 200 else {}
    check("the track list, one line each", status == 200
          and line.get("id") == "synth-128" and line.get("has_timeline") is True
          and line.get("phrases") == 8 and line.get("rev", "").startswith("r:")
          and line.get("grid_rev") == "g:834af7", f"{status} {line}")
    status, body = jget("/api/tracks/synth-128")
    check("one track, with the rev a save must quote",
          status == 200 and body["doc"]["id"] == "synth-128"
          and body["rev"] == sc.show_library.folder.revs["tracks/synth-128.json"])
    status, body = jget("/api/timelines/synth-128")
    check("its timeline", status == 200 and body["doc"]["track"] == "synth-128"
          and body["rev"].startswith("r:"))
    status, body = jget("/api/routines")
    check("the routine shelf, with params and variations",
          status == 200 and {r["id"] for r in body["routines"]}
          == {"idle-orbit", "verse-sweep", "build-rise", "fan-drop"}
          and next(r for r in body["routines"] if r["id"] == "fan-drop")
          ["variations"] == ["tight", "wide"])
    status, body = jget("/api/show")
    check("the folder: show.json, counts, problems",
          status == 200 and body["show"]["kind"] == "klights.show"
          and body["tracks"] == 1 and body["errors"] == [])
    check("an id that is not there is a 404 that says so",
          jget("/api/timelines/nope") == (404, {"error": "no timeline 'nope'"}))
    for bad in ("/api/tracks/..%2Fshow", "/api/tracks/UPPER", "/api/whatever"):
        check(f"{bad} is refused", get(bad)[0] == 404)
    status, _, _ = get("/api/tracks", headers={"Origin": "http://evil.example"})
    check("a page on another site cannot read it", status == 403)

    # -- 2. audio ------------------------------------------------------------
    print("\n2. audio")
    status, body = jget("/api/audio/synth-128")
    check("audio needs the token", status == 401 and "token" in body["error"])
    status, headers, data = get("/api/audio/synth-128", token="tok")
    check("with it, the whole file -- found under audio_roots by name, since "
          "the prepped path is another machine's",
          status == 200 and data == AUDIO
          and headers.get("Content-Type") == "audio/mpeg"
          and headers.get("Accept-Ranges") == "bytes", f"{status} {headers}")
    status, headers, data = get("/api/audio/synth-128", token="tok",
                                headers={"Range": "bytes=100-199"})
    check("a Range gets a 206 with exactly those bytes",
          status == 206 and data == AUDIO[100:200]
          and headers.get("Content-Range") == f"bytes 100-199/{len(AUDIO)}",
          f"{status} {headers.get('Content-Range')}")
    status, _, data = get("/api/audio/synth-128", token="tok",
                          headers={"Range": "bytes=-16"})
    check("and a suffix range the last bytes", status == 206 and data == AUDIO[-16:])
    status, _, _ = get("/api/audio/synth-128", token="tok",
                       headers={"Range": f"bytes={len(AUDIO) + 5}-"})
    check("a range past the end is a 416", status == 416)
    odd = music / "crate" / "Night Drive [Extended Mix].mp3"
    odd.parent.mkdir()
    odd.write_bytes(AUDIO)
    named = {"audio": [{"path": "D:\\Music\\Night Drive [Extended Mix].mp3"}]}
    check("a file name with [brackets] is found by name, not read as a pattern",
          apimod.find_audio(named, [str(music)]) == odd,
          f"{apimod.find_audio(named, [str(music)])}")
    searched: list[str] = []
    real_rglob = pathlib.Path.rglob
    pathlib.Path.rglob = lambda self, pat: (searched.append(pat), real_rglob(self, pat))[1]
    try:
        for _ in range(5):
            apimod.find_audio(named, [str(music)])
    finally:
        pathlib.Path.rglob = real_rglob
    check("and found once: a player's Range requests do not each search the "
          "music folder again", searched == [], f"{searched}")
    odd.unlink()
    check("a remembered file that has gone is looked for again, not served",
          apimod.find_audio(named, [str(music)]) is None)
    track["audio"] = [{"path": str(music / "notes.txt")}]
    (music / "notes.txt").write_text("not audio")
    track_path.write_text(json.dumps(track, indent=2))
    sc.reload_library()
    assert sc.worker.wait_idle(5.0)
    sc._drain()
    status, _ = jget("/api/audio/synth-128", token="tok")
    check("a file without an audio extension is never served", status == 404)

    # -- 3. commands that answer from the worker -----------------------------
    print("\n3. draft, save")
    replies: list[dict] = []
    sc.reply_to = lambda cid, payload: replies.append(payload)
    designer = servermod.Client(id="d1", name="designer", tier="configure")
    other = servermod.Client(id="d2", name="someone", tier="configure")
    viewer = servermod.Client(id="v1", name="phone", tier="operate")

    def ask(msg, client=designer):
        replies.clear()
        sc.submit(msg, client)
        sc._drain()
        for _ in range(4):
            assert sc.worker.wait_idle(5.0)
            sc._drain()
        return replies[-1] if replies else None

    doc = json.loads((shows / "timelines" / "synth-128.json").read_text())
    r = ask({"type": "timeline_draft", "doc": doc, "id": 1})
    check("a draft is checked on the worker and answered later: clean",
          r and r["ok"] and r["data"] == {"errors": [], "warnings": [],
                                          "problems": []}, f"{r}")
    broken = json.loads(json.dumps(doc))
    broken["rows"][1]["items"][0]["len"] = 0
    r = ask({"type": "timeline_draft", "doc": broken, "id": 2})
    check("a broken draft says what is wrong",
          r["ok"] and any("longer than nothing" in e for e in r["data"]["errors"]),
          f"{r}")
    looks = json.loads(json.dumps(doc))
    looks["rows"][0]["items"][0]["look"] = "Not A Look"
    r = ask({"type": "timeline_draft", "doc": looks, "id": 3})
    check("and one that is valid but will not work on THIS rig says that",
          r["ok"] and r["data"]["errors"] == []
          and any("Not A Look" in p for p in r["data"]["problems"]), f"{r}")
    r = ask({"type": "timeline_draft", "doc": doc, "id": 4}, client=viewer)
    check("drafts and saves are configure-tier",
          r and r["ok"] is False and "needs configure" in r["error"])

    rev = sc.show_library.folder.revs["timelines/synth-128.json"]
    edited = json.loads(json.dumps(doc))
    edited["palette"] = "Hot"
    r = ask({"type": "timeline_save", "doc": edited, "base_rev": "r:000000000000",
             "id": 5})
    check("a save against a rev that is not the file's is refused",
          r["ok"] is False and "changed since you opened it" in r["error"], f"{r}")
    r = ask({"type": "timeline_save", "doc": edited, "id": 6})
    check("a save without a base_rev is refused",
          r["ok"] is False and "base_rev is required" in r["error"])
    r = ask({"type": "timeline_save", "doc": edited, "base_rev": rev, "id": 7})
    on_disk = json.loads((shows / "timelines" / "synth-128.json").read_text())
    check("with the right rev it is written, and answered with the new rev",
          r["ok"] and r["data"]["rev"] != rev and on_disk["palette"] == "Hot",
          f"{r}")
    check("and the folder reloads with it",
          sc.show_library.folder.timelines["synth-128"]["palette"] == "Hot")
    bad = json.loads(json.dumps(edited))
    bad["rows"][0]["gap"] = "sometimes"
    r = ask({"type": "timeline_save", "doc": bad, "base_rev": r["data"]["rev"],
             "id": 8})
    check("an invalid document is never written",
          r["ok"] is False and "gap" in r["error"], f"{r}")
    routine = json.loads((shows / "routines" / "idle-orbit.json").read_text())
    routine["name"] = "Idle orbit, edited"
    r = ask({"type": "routine_save", "doc": routine,
             "base_rev": sc.show_library.folder.revs["routines/idle-orbit.json"],
             "id": 9})
    check("routines save the same way", r["ok"] and json.loads(
        (shows / "routines" / "idle-orbit.json").read_text())["name"]
        == "Idle orbit, edited", f"{r}")
    r = ask({"type": "routine_draft", "doc": routine, "id": 11})
    check("a routine draft is checked the same way: clean",
          r and r["ok"] and r["data"] == {"errors": [], "warnings": [],
                                          "problems": []}, f"{r}")
    lost = json.loads(json.dumps(routine))
    first = next(iter(lost["roles"]))
    lost["roles"][first] = {"default": "no-such-tag"}
    lost["variations"] = {"loud": {}}
    r = ask({"type": "routine_draft", "doc": lost, "id": 12})
    check("and says what will not work on this rig, once, not per variation",
          r["ok"] and r["data"]["errors"] == []
          and sum("no-such-tag" in p for p in r["data"]["problems"]) == 1,
          f"{r}")
    lost["bars"] = 0
    r = ask({"type": "routine_draft", "doc": lost, "id": 13})
    check("a broken routine draft lists its errors and nothing compiles",
          r["ok"] and r["data"]["errors"] and r["data"]["problems"] == [],
          f"{r}")
    r = ask({"type": "routine_draft", "doc": routine, "id": 14}, client=viewer)
    check("routine drafts are configure-tier too",
          r and r["ok"] is False and "needs configure" in r["error"])
    r = ask({"type": "routine_save", "doc": {**routine, "id": "../escape"},
             "base_rev": "", "id": 10})
    check("and an id that is not a file name is refused before anything runs",
          r["ok"] is False and "not a usable id" in r["error"], f"{r}")

    # -- 4. preview ----------------------------------------------------------
    print("\n4. the designer driving the rig")
    sc.apply({"type": "sync", "source": "blt", "deck": "1",
              "title": "Unknown Guest Tune", "artist": "Guest DJ",
              "rekordbox_id": 2, "duration": 240.0}, None, 50.0)
    for i in range(10):
        sc.apply({"type": "sync", "source": "blt", "deck": "1",
                  "track_time": 10 + i / 25, "playing": True, "pitch": 1.0},
                 None, 50.0 + i / 25)
    sc.ctx.time = 50.4
    r = ask({"type": "preview_arm", "track_id": "synth-128", "id": 11})
    check("previewing while a DJ is playing is refused, and says how to insist",
          r["ok"] is False and "force" in r["error"], f"{r}")
    r = ask({"type": "preview_arm", "track_id": "synth-128", "force": True,
             "id": 12})
    check("forced, the designer takes the stage",
          r["ok"] and sc.player.preview is not None
          and sc.snapshot()["preview"]["name"] == "designer", f"{r}")
    check("and every console is told", any("DESIGNER (designer) is driving"
                                            in n for n in sc.notices))
    r = ask({"type": "preview_transport", "time_s": 75.0, "playing": False,
             "id": 13}, client=other)
    check("only the console that armed it can move it",
          r["ok"] is False and "designer is driving" in r["error"], f"{r}")
    r = ask({"type": "preview_transport", "time_s": 75.0, "playing": False,
             "id": 14})
    sc.ctx.time = 51.0
    sc._before_frame()
    sc.runner.sync_clock()
    states = statemod.evaluate_stack(sc.ctx, sc.runner.show)
    st = sc.snapshot()["program"]
    check("its transport drives the timeline it is editing -- bar 41 at 75 s",
          r["ok"] and st["mode"] == "preview" and st["bar"] == 41
          and sc.runner.show is sc.player.preview.program.show, f"{st}")
    movers = [f for f in sc.rig.fixtures if f.head is not None]
    check("and the rig shows it: the saved timeline's Hot palette from bar 1",
          abs(states[movers[0].fid].color[0] - 1.0) < 1e-3, f"{states[movers[0].fid].color}")
    cool = json.loads(json.dumps(edited))
    cool["palette"] = "Cool"
    cool["rows"] = [r_ for r_ in cool["rows"] if r_["id"] != "palette"]
    ask({"type": "timeline_draft", "doc": cool, "id": 15})
    sc._before_frame()
    sc.runner.sync_clock()
    states = statemod.evaluate_stack(sc.ctx, sc.runner.show)
    check("a draft replaces what the preview plays, unsaved",
          sc.snapshot()["preview"]["draft"] is True
          and abs(states[movers[0].fid].color[2] - 0xf6 / 255) < 1e-3,
          f"{states[movers[0].fid].color}")
    srv.drop("d1")
    sc._drain()
    check("the designer's browser going away lets go of the rig",
          sc.player.preview is None and sc.snapshot()["preview"] is None)
    check("and says so", any("disconnected" in n for n in sc.notices))
    before = list(sc.notices)
    for _ in range(5):
        sc.submit({"type": "preview_transport", "time_s": 80.0, "playing": True},
                  designer)
    sc._drain()
    check("its transport arriving after that is ignored, not five notices",
          list(sc.notices) == before, f"{list(sc.notices)[len(before):]}")
finally:
    srv.stop()
    sc.worker.stop()
    shutil.rmtree(tmp, ignore_errors=True)

# -- 5. the designer's side of the bargain -----------------------------------
print("\n5. what the designer agrees with, and what a phone downloads")
sys.path.insert(0, str(REPO / "engine" / "tests"))
import dump_designer_fixtures  # noqa: E402

fixtures = REPO / "ui" / "src" / "designer" / "__fixtures__"
stale = [name for name, text in dump_designer_fixtures.render().items()
         if not (fixtures / name).is_file()
         or (fixtures / name).read_text(encoding="utf-8") != text]
check("the designer's fixtures are what the engine says today (else run "
      "engine/tests/dump_designer_fixtures.py)", not stale, f"stale: {stale}")

dist = REPO / "ui" / "dist"
html = (dist / "index.html").read_text(encoding="utf-8")
entries = re.findall(r'<script[^>]*\bsrc="\.?/?([^"]+\.js)"', html)
marker = b"klights-designer"
check("the console page loads exactly one entry script", len(entries) == 1, f"{entries}")
check("and a phone never downloads the designer: its marker is not in the entry",
      entries and marker not in (dist / entries[0]).read_bytes(), f"{entries}")
check("it is in a chunk of its own, loaded only from #designer",
      any(marker in js.read_bytes() for js in (dist / "assets").glob("*.js")
          if js.name != Path(entries[0]).name) if entries else False)


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("api: all checks pass")
