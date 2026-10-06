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
    check("each line carries what Studio's library shows: the phrases, the "
          "timeline in a line, when it was edited",
          line.get("phrase_items", [None])[0] == [0, 64, "Intro"]
          and len(line.get("phrase_items", [])) == 8
          and line.get("timeline", {}).get("rows", 0) > 0
          and line.get("timeline", {}).get("items", 0) > 0
          and isinstance(line.get("edited"), float),
          f"{line.get('phrase_items')} {line.get('timeline')} {line.get('edited')}")
    check("audio_here only looks at the paths the track names: this one names "
          "a path that is not on this machine, and the list does not search "
          "audio_roots for it", line.get("audio_here") is False,
          f"{line.get('audio_here')}")
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

    # -- 3b. a routine's place in the folder: where used, rename, delete ------
    print("\n3b. where a routine is used; renaming and deleting one")
    status, body = jget("/api/routines")
    idle_line = next(r for r in body["routines"] if r["id"] == "idle-orbit")
    used = idle_line["used_by"]
    check("the routine list says where each is used: timelines, sets, show.json",
          [t["track"] for t in used["timelines"]] == ["synth-128"]
          and used["timelines"][0]["clips"] >= 1
          and [t["id"] for t in used["templates"]] == ["club"]
          and "Intro" in used["templates"][0]["where"]
          and used["show"] != [], f"{used}")
    check("and what each of its rows drives, for a thumbnail",
          any(lane["target"] == "movement" and lane["blocks"]
              for lane in idle_line["lanes"]) and idle_line["rev"].startswith("r:"),
          f"{idle_line['lanes']}")
    rev = idle_line["rev"]
    r = ask({"type": "routine_delete", "routine": "idle-orbit", "base_rev": rev, "id": 20})
    check("a routine still in use is not deleted, and the refusal says where",
          r["ok"] is False and "timelines/synth-128.json" in r["error"]
          and "templates/club.json" in r["error"] and "show.json" in r["error"]
          and (shows / "routines" / "idle-orbit.json").is_file(), f"{r}")
    r = ask({"type": "routine_rename", "routine": "idle-orbit", "to": "fan-drop",
             "base_rev": rev, "id": 21})
    check("a rename onto a routine that exists is refused",
          r["ok"] is False and "already" in r["error"], f"{r}")
    r = ask({"type": "routine_rename", "routine": "idle-orbit", "to": "orbit-idle",
             "base_rev": "r:000000000000", "id": 22})
    check("a rename of a routine changed since it was read is refused, and "
          "writes nothing", r["ok"] is False and "changed since" in r["error"]
          and not (shows / "routines" / "orbit-idle.json").exists(), f"{r}")
    r = ask({"type": "routine_rename", "routine": "idle-orbit", "to": "orbit-idle",
             "base_rev": rev, "id": 23}, client=viewer)
    check("renames are configure-tier", r["ok"] is False and "needs configure" in r["error"])
    r = ask({"type": "routine_rename", "routine": "idle-orbit", "to": "orbit-idle",
             "base_rev": rev, "id": 24})
    tl_text = (shows / "timelines" / "synth-128.json").read_text()
    show_doc = json.loads((shows / "show.json").read_text())
    club = json.loads((shows / "templates" / "club.json").read_text())
    check("a rename writes the routine under its new name and moves every "
          "reference: the timeline, the set, show.json's idle routine",
          r["ok"] and r["data"]["written"][0] == "routines/orbit-idle.json"
          and {"timelines/synth-128.json", "templates/club.json", "show.json"}
          <= set(r["data"]["written"])
          and '"idle-orbit"' not in tl_text and '"orbit-idle"' in tl_text
          and show_doc["pause"]["idle_routine"] == "orbit-idle"
          and club["phrases"]["Intro"]["routine"] == "orbit-idle", f"{r}")
    check("and the old file is gone last",
          not (shows / "routines" / "idle-orbit.json").exists()
          and (shows / "routines" / "orbit-idle.json").is_file())
    check("the folder is still whole: nothing names a routine that is gone",
          not any("not in routines/" in w for w in sc.show_library.folder.warnings),
          f"{sc.show_library.folder.warnings}")
    back = next(r for r in jget("/api/routines")[1]["routines"] if r["id"] == "orbit-idle")
    r = ask({"type": "routine_rename", "routine": "orbit-idle", "to": "idle-orbit",
             "base_rev": back["rev"], "id": 25})
    check("and renamed back the same way", r["ok"]
          and (shows / "routines" / "idle-orbit.json").is_file(), f"{r}")
    spare = {**routine, "id": "spare", "name": "Spare"}
    r = ask({"type": "routine_save", "doc": spare, "base_rev": "", "id": 26})
    spare_rev = r["data"]["rev"] if r and r["ok"] else ""
    r = ask({"type": "routine_delete", "routine": "spare", "base_rev": "r:000000000000", "id": 27})
    check("a delete of a routine changed since it was read is refused",
          r["ok"] is False and "changed since" in r["error"]
          and (shows / "routines" / "spare.json").is_file(), f"{r}")
    r = ask({"type": "routine_delete", "routine": "spare", "base_rev": spare_rev, "id": 28})
    check("a routine nothing uses is deleted",
          r["ok"] and r["data"]["deleted"] == "routines/spare.json"
          and not (shows / "routines" / "spare.json").exists(), f"{r}")

    # -- 3c. template sets and show.json ---------------------------------------
    print("\n3c. template sets, and the show's settings")
    status, body = jget("/api/templates")
    club_line = body["templates"][0] if status == 200 else {}
    check("the template sets, each saying whether it is the show's set",
          club_line.get("id") == "club" and club_line.get("show") is True
          and club_line.get("rev", "").startswith("r:")
          and club_line.get("phrases", 0) > 0, f"{club_line}")
    show_rev = jget("/api/show")[1].get("show_rev")
    check("/api/show carries show.json's own rev, for a save to quote",
          isinstance(show_rev, str) and show_rev.startswith("r:"), f"{show_rev}")
    club = json.loads((shows / "templates" / "club.json").read_text())
    r = ask({"type": "template_draft", "doc": club, "id": 30})
    check("a template set draft is checked: clean",
          r and r["ok"] and r["data"] == {"errors": [], "warnings": [], "problems": []}, f"{r}")
    odd = json.loads(json.dumps(club))
    odd["phrases"]["Verse"] = {"routine": "no-such-routine"}
    odd["phrases"]["Chorus"]["variation"] = "huge"
    r = ask({"type": "template_draft", "doc": odd, "id": 31})
    check("and says what it asks of the routines that they do not have",
          r["ok"] and r["data"]["errors"] == []
          and any("no-such-routine" in p for p in r["data"]["problems"])
          and any("'huge'" in p for p in r["data"]["problems"]), f"{r}")
    odd["phrases"]["Up"]["palette"] = "Nowhere"
    r = ask({"type": "template_draft", "doc": odd, "id": 32})
    check("a palette the set does not define is an error, not a note",
          r["ok"] and any("Nowhere" in e for e in r["data"]["errors"]), f"{r}")
    copy = {**club, "id": "club-2", "name": "Club 2"}
    r = ask({"type": "template_save", "doc": copy, "base_rev": "", "id": 33})
    check("a set is saved as a new file", r and r["ok"]
          and (shows / "templates" / "club-2.json").is_file(), f"{r}")
    club_rev = sc.show_library.folder.revs["templates/club.json"]
    r = ask({"type": "template_delete", "template": "club", "base_rev": club_rev, "id": 34})
    check("the show's own set is not deleted",
          r["ok"] is False and "show's" in r["error"]
          and (shows / "templates" / "club.json").is_file(), f"{r}")
    show_doc = json.loads((shows / "show.json").read_text())
    r = ask({"type": "show_save", "doc": {**show_doc, "template_set": "club-2"},
             "base_rev": show_rev, "id": 35}, client=viewer)
    check("show.json saves are configure-tier", r["ok"] is False and "needs configure" in r["error"])
    r = ask({"type": "show_save", "doc": {**show_doc, "template_set": "club-2"},
             "base_rev": "r:000000000000", "id": 36})
    check("a show.json save that has not seen the latest (the phone's latency "
          "slider writes it too) is refused", r["ok"] is False and "changed since" in r["error"],
          f"{r}")
    r = ask({"type": "show_save", "doc": {**show_doc, "template_set": "club-2"},
             "base_rev": show_rev, "id": 37})
    check("making another set the show's is a show.json save",
          r["ok"] and json.loads((shows / "show.json").read_text())["template_set"] == "club-2",
          f"{r}")
    rev2 = sc.show_library.folder.revs["templates/club-2.json"]
    r = ask({"type": "template_rename", "template": "club-2", "to": "late-night",
             "base_rev": rev2, "id": 38})
    check("renaming the show's set renames it in show.json too",
          r["ok"] and r["data"]["written"] == ["templates/late-night.json", "show.json"]
          and json.loads((shows / "show.json").read_text())["template_set"] == "late-night"
          and not (shows / "templates" / "club-2.json").exists(), f"{r}")
    club_rev = sc.show_library.folder.revs["templates/club.json"]
    r = ask({"type": "template_delete", "template": "club", "base_rev": club_rev, "id": 39})
    check("a set that is no longer the show's can be deleted",
          r["ok"] and not (shows / "templates" / "club.json").exists(), f"{r}")

    # -- 3d. the palette library -------------------------------------------------
    print("\n3d. the palette library, and its copies")
    status, body = jget("/api/palettes")
    found = {f["name"]: f for f in body.get("found", [])} if status == 200 else {}
    check("with no library yet, every palette is one that lives inside files",
          status == 200 and body["palettes"] == [] and "Hot" in found
          and any(p["file"] == "timelines/synth-128.json" for p in found["Hot"]["places"]),
          f"{status} {body}")
    hot = {"kind": "klights.palette", "version": 1, "id": "hot", "name": "Hot",
           "primary": "#ff0000", "secondary": "#ff8800", "accent": "#ffffff"}
    r = ask({"type": "palette_save", "doc": {**hot, "primary": "@primary"}, "base_rev": "", "id": 40})
    check("a library palette is plain colours", r["ok"] is False and "#rrggbb" in r["error"], f"{r}")
    r = ask({"type": "palette_save", "doc": hot, "base_rev": "", "id": 41})
    check("a palette is saved into the library as its own file",
          r and r["ok"] and (shows / "palettes" / "hot.json").is_file(), f"{r}")
    status, body = jget("/api/palettes")
    lib = body["palettes"][0] if status == 200 and body["palettes"] else {}
    copies = {c["file"]: c for c in lib.get("copies", [])}
    check("it lists its copies -- every timeline and set with a palette of its "
          "name -- and whether each still has its colours",
          lib.get("name") == "Hot" and "timelines/synth-128.json" in copies
          and copies["timelines/synth-128.json"]["same"] is False
          and "Hot" not in {f["name"] for f in body["found"]}, f"{lib}")
    r = ask({"type": "palette_sync", "palette": "hot", "files": ["timelines/nope.json"], "id": 42})
    check("an update names only files the folder has", r["ok"] is False and "nope" in r["error"], f"{r}")
    r = ask({"type": "palette_sync", "palette": "hot", "files": sorted(copies), "id": 43},
            client=viewer)
    check("updates are configure-tier", r["ok"] is False and "needs configure" in r["error"])
    r = ask({"type": "palette_sync", "palette": "hot", "files": sorted(copies), "id": 44})
    tl_doc = json.loads((shows / "timelines" / "synth-128.json").read_text())
    check("updating the copies gives each the library's colours, and nothing else changes",
          r["ok"] and set(r["data"]["written"]) == set(copies)
          and tl_doc["palettes"]["Hot"] == {"primary": "#ff0000", "secondary": "#ff8800",
                                            "accent": "#ffffff"}
          and "Cool" in tl_doc["palettes"], f"{r}")
    lib = jget("/api/palettes")[1]["palettes"][0]
    check("and then every copy is the same", all(c["same"] for c in lib["copies"]), f"{lib}")
    r = ask({"type": "palette_delete", "palette": "hot", "base_rev": lib["rev"], "id": 45})
    check("deleting a library palette leaves its copies where they are",
          r["ok"] and not (shows / "palettes" / "hot.json").exists()
          and "Hot" in json.loads((shows / "timelines" / "synth-128.json").read_text())["palettes"],
          f"{r}")

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
    check("and every console is told", any("STUDIO (designer) is driving"
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

# The block table is not a test fixture but the UI's source for every block's
# controls, so a stale one is a console offering the wrong ranges, not just a
# test comparing against the wrong numbers.
ui_src = REPO / "ui" / "src"
stale_ui = [name for name, text in dump_designer_fixtures.render_ui().items()
            if not (ui_src / name).is_file()
            or (ui_src / name).read_text(encoding="utf-8") != text]
check("the UI's block table is what blocks.py declares today (else run "
      "engine/tests/dump_designer_fixtures.py)", not stale_ui,
      f"stale: {stale_ui}")

dist = REPO / "ui" / "dist"
html = (dist / "index.html").read_text(encoding="utf-8")
entries = re.findall(r'<script[^>]*\bsrc="\.?/?([^"]+\.js)"', html)
marker = b"klights-designer"
check("the console page loads exactly one entry script", len(entries) == 1, f"{entries}")
check("and a phone never downloads the designer: its marker is not in the entry",
      entries and marker not in (dist / entries[0]).read_bytes(), f"{entries}")
check("it is in a chunk of its own, loaded only from #studio",
      any(marker in js.read_bytes() for js in (dist / "assets").glob("*.js")
          if js.name != Path(entries[0]).name) if entries else False)


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("api: all checks pass")
