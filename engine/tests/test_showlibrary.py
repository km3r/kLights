"""
Tests for the show folder as the running engine sees it: loading, noticing
changes, keeping the last good version, pinning the playing track's match, and
linking a track by hand.

Every check runs against a COPY of shared/show-example in a temporary folder,
because half of them break files on purpose.

Run: python engine/tests/test_showlibrary.py
"""

import json
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import showfiles  # noqa: E402
from engine import showlibrary as sl  # noqa: E402
from engine import tracks as tr  # noqa: E402
from engine import transport as tp  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


EXAMPLE = REPO / "shared" / "show-example"
tmp = Path(tempfile.mkdtemp(prefix="klights-showlib-"))


def fresh(name: str) -> Path:
    root = tmp / name
    shutil.copytree(EXAMPLE, root)
    return root


def touch_json(path: Path, mutate) -> None:
    """Edit a JSON file in place and make sure its mtime moves -- some
    filesystems keep mtimes to the second."""
    doc = json.loads(path.read_text(encoding="utf-8"))
    mutate(doc)
    before = path.stat().st_mtime_ns
    path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    if path.stat().st_mtime_ns == before:
        import os
        os.utime(path, ns=(before + 1_000_000_000, before + 1_000_000_000))


def sample_of(title="", artist="", album="", duration=None, rekordbox_id=None,
              signature=None, track_seq=1):
    ident = tp.Identity(title=title, artist=artist, album=album,
                        duration=duration, rekordbox_id=rekordbox_id,
                        signature=signature)
    return tp.TrackSample(state=tp.PLAYING, identity=ident, time_s=1.0, rate=1.0,
                          source="rkbx", deck=None, track_seq=track_seq,
                          jump_seq=0, age=0.0)


try:
    # -- 1. a load -----------------------------------------------------------
    print("\n1. one load of the folder")
    root = fresh("load")
    lib = sl.load(root)
    check("the example loads clean", lib.folder.errors == []
          and lib.folder.warnings == [], f"{lib.folder.errors + lib.folder.warnings}")
    check("with its counts", lib.counts == {"tracks": 1, "timelines": 1,
                                            "routines": 4, "templates": 1, "palettes": 0},
          f"{lib.counts}")
    check("each track's grid built once, at load",
          set(lib.grids) == {"synth-128"}
          and abs(lib.grids["synth-128"].beat_at(60.0) - 128.0) < 1e-9)
    check("and the match index over its tracks", len(lib.index) == 1)
    check("and each timeline, compiled for querying, with the scene lane "
          "driving all three slots",
          set(lib.timelines) == {"synth-128"}
          and set(lib.timelines["synth-128"].channels)
          == {"movement", "color", "level", "palette"},
          f"{lib.timelines}")
    check("the rev is stable across loads of unchanged files",
          sl.load(root).rev == lib.rev, f"{lib.rev}")
    check("the signature lists every document the load read",
          {p for p, _, _ in lib.signature} >= {"show.json",
                                               "tracks/synth-128.json",
                                               "timelines/synth-128.json"},
          f"{[p for p, _, _ in lib.signature]}")

    touch_json(root / "routines" / "idle-orbit.json",
               lambda d: d.__setitem__("label", "Idle orbit (edited)"))
    edited = sl.load(root)
    check("an edit to any document moves the rev", edited.rev != lib.rev)
    check("missing folder: a load with an error, not an exception",
          sl.load(tmp / "nope").folder.errors != [])
    check("a scan of a missing folder is empty, not an exception",
          sl.scan(tmp / "nope") == ())

    # -- 2. settings ---------------------------------------------------------
    print("\n2. show.json settings reach the transport")
    root = fresh("settings")
    touch_json(root / "show.json", lambda d: (
        d["sources"]["rkbx"].__setitem__("latency_ms", -15),
        d["pause"].__setitem__("grace_s", 6),
        d["follow"].__setitem__("min_track_change_s", 3)))
    lib = sl.load(root)
    got = sl.transport_settings(lib)
    check("latency in ms becomes seconds, per source",
          abs(got["latency_s"]["rkbx"] + 0.015) < 1e-12, f"{got}")
    t = tp.TrackTransport()
    sl.apply_settings(t, lib)
    check("and lands on the transport, with the pause grace and the "
          "track-change limit", t.grace_s == 6.0 and t.min_track_change_s == 3.0
          and abs(t.latency_s["rkbx"] + 0.015) < 1e-12,
          f"{t.grace_s} {t.min_track_change_s} {t.latency_s}")
    check("no show.json changes nothing but latency",
          sl.transport_settings(None) == {"latency_s": {}})

    # -- 3. last good --------------------------------------------------------
    print("\n3. a broken edit keeps the last good version")
    root = fresh("lastgood")
    good = sl.load(root)
    tl = root / "timelines" / "synth-128.json"
    tl.write_text('{"kind": "klights.timeline", "version": 1, ', encoding="utf-8")
    after = sl.load(root, previous=good)
    check("a timeline broken mid-sync keeps running on its last good version",
          after.folder.timelines.get("synth-128") is good.folder.timelines["synth-128"])
    check("and says so: the error, the failure, and a warning",
          "timelines/synth-128.json" in after.folder.failed
          and any("not valid JSON" in e for e in after.folder.errors)
          and any("kept the last good version" in w for w in after.folder.warnings),
          f"{after.folder.failed}")
    check("the kept file keeps its old rev, so the library rev does not move",
          after.rev == good.rev, f"{after.rev} vs {good.rev}")
    cold = sl.load(root)
    check("a first load has nothing to keep: the timeline is left out",
          "synth-128" not in cold.folder.timelines and not cold.folder.failed)

    (root / "routines" / "new-one.json").write_text("{nope", encoding="utf-8")
    after2 = sl.load(root, previous=after)
    check("a new file that was never good is left out, not kept",
          "new-one" not in after2.folder.routines
          and "routines/new-one.json" not in after2.folder.failed)

    shutil.copyfile(EXAMPLE / "timelines" / "synth-128.json", tl)
    fixed = sl.load(root, previous=after2)
    check("fixing the file clears the failure",
          "timelines/synth-128.json" not in fixed.folder.failed
          and "synth-128" in fixed.folder.timelines)

    touch_json(root / "show.json", lambda d: d.__setitem__("fallback", "sometimes"))
    kept_show = sl.load(root, previous=fixed)
    check("show.json too: an invalid edit keeps the last good settings",
          kept_show.folder.show is fixed.folder.show
          and "show.json" in kept_show.folder.failed, f"{kept_show.folder.failed}")

    # -- 4. conflict copies --------------------------------------------------
    print("\n4. sync conflict copies are never loaded")
    root = fresh("conflict")
    doc = json.loads((root / "tracks" / "synth-128.json").read_text())
    doc["identity"]["title"] = "conflicted title"
    (root / "tracks" / "synth-128 (conflicted copy 2026-10-01).json").write_text(
        json.dumps(doc), encoding="utf-8")
    lib = sl.load(root)
    check("a conflicted copy of a track does not enter the match index",
          lib.index.match(title="conflicted title", artist="kLights").via == tr.NONE
          and len(lib.index) == 1)
    check("and the folder says why", any("conflict copy" in w
                                         for w in lib.folder.warnings))

    # -- 5. noticing changes -------------------------------------------------
    print("\n5. the watcher")
    root = fresh("watch")
    calls: list[float] = []
    w = sl.Watcher(root, on_change=lambda: calls.append(time.time()))
    w.seen = sl.scan(root)
    check("nothing changed, nothing called", w.poll() is False and calls == [])
    touch_json(root / "tracks" / "synth-128.json",
               lambda d: d.setdefault("aliases", []).append(
                   {"title": "x", "via": "manual"}))
    check("a change is not acted on while it might still be moving",
          w.poll() is False and calls == [])
    check("once it has held still for a poll, it is", w.poll() is True
          and len(calls) == 1)
    check("and only once", w.poll() is False and len(calls) == 1)

    (root / "routines" / "a.json").write_text("{}", encoding="utf-8")
    w.poll()
    (root / "routines" / "b.json").write_text("{}", encoding="utf-8")
    w.poll()
    (root / "routines" / "c.json").write_text("{}", encoding="utf-8")
    w.poll()
    w.poll()
    check("a sync landing files over several polls is one reload, after it "
          "stops", len(calls) == 2, f"{len(calls)} calls")
    (root / "routines" / "c.json").unlink()
    w.poll()
    w.poll()
    check("a deleted file is a change", len(calls) == 3)

    called = threading.Event()
    threads: list[str] = []
    live = sl.Watcher(root, on_change=lambda: (
        threads.append(threading.current_thread().name), called.set()),
        interval=0.05)
    live.start()
    (root / "routines" / "d.json").write_text("{}", encoding="utf-8")
    ok = called.wait(3.0)
    live.stop()
    check("the running watcher notices on its own thread",
          ok and threads[:1] == ["klights-showwatch"], f"{threads}")
    check("and stops", live._thread is None)

    reports: list[str] = []
    boom = sl.Watcher(root, on_change=lambda: 1 / 0, report=reports.append,
                      interval=0.02)
    boom.start()
    (root / "routines" / "e.json").write_text("{}", encoding="utf-8")
    time.sleep(0.4)
    alive = boom._thread is not None and boom._thread.is_alive()
    boom.stop()
    check("a failing hand-off is reported, once, and the watcher keeps "
          "watching", alive and len(reports) >= 1
          and "watch failed" in reports[0], f"{reports}")

    # -- 6. pinning ----------------------------------------------------------
    print("\n6. pinning the playing track")
    root = fresh("pin")
    lib = sl.load(root)
    p = sl.pin(lib, sample_of("synthetic 128", "kLights", "test track"))
    check("a known track pins its match, document, grid and timeline",
          p.match.track_id == "synth-128" and p.track["id"] == "synth-128"
          and p.grid is lib.grids["synth-128"] and p.library is lib
          and p.timeline is lib.timelines["synth-128"]
          and p.public(lib)["has_timeline"] is True, f"{p.match}")
    p2 = sl.pin(lib, sample_of("Unknown Guest Tune", "Guest DJ"))
    check("an unknown track pins 'none', with no grid and no timeline",
          p2.match.via == tr.NONE and p2.grid is None and p2.track is None
          and p2.timeline is None and p2.public(lib)["has_timeline"] is False)
    p3 = sl.pin(lib, sample_of())
    check("no identity pins no match at all", p3.match is None
          and p3.public(lib) is None)
    check("no library pins no match", sl.pin(None, sample_of("x")).match is None)
    check("public() says the folder has not changed since",
          p.public(lib)["stale"] is False and p.public(lib)["track_id"] == "synth-128")
    touch_json(root / "show.json", lambda d: d.__setitem__("fallback", "operator"))
    newer = sl.load(root, previous=lib)
    check("and says so once it has -- the playing track does not change",
          p.public(newer)["stale"] is True and p.library is lib)

    # -- 7. linking by hand --------------------------------------------------
    print("\n7. linking a track by hand")
    root = fresh("link")
    sig = "c" * 40
    what = sl.link(root, "synth-128", "Guest Copy", "Someone Else", "",
                   signature=sig, added="2026-10-01")
    check("a link records an alias and the signature", what == "alias+signature",
          what)
    result, _ = showfiles.read_doc(root / "tracks" / "synth-128.json", "track")
    check("and the file still validates", result.ok, f"{result.errors}")
    doc = result.doc
    check("the alias is the deck's description, marked manual",
          doc["aliases"][-1] == {"title": "Guest Copy", "artist": "Someone Else",
                                 "album": "", "via": "manual",
                                 "added": "2026-10-01"}, f"{doc['aliases']}")
    check("unknown keys in the file survive the write",
          doc.get("source", {}).get("from") == "synthetic")
    relinked = sl.load(root)
    m = relinked.index.match(title="guest copy", artist="someone else")
    check("the next load matches the description by alias",
          m.track_id == "synth-128" and m.via == tr.ALIAS, f"{m}")
    m = relinked.index.match(title="whatever", signature=sig)
    check("and the signature by signature", m.via == tr.SIGNATURE, f"{m}")
    check("linking again adds nothing",
          sl.link(root, "synth-128", "Guest Copy", "Someone Else", "",
                  signature=sig) == "already")
    check("nor does linking a track to its own identity",
          sl.link(root, "synth-128", "Synthetic 128", "KLIGHTS",
                  "test track") == "already")
    try:
        sl.link(root, "../escape", "x")
        escaped = True
    except ValueError:
        escaped = False
    check("an id that is not a file name is refused", not escaped)
    (root / "tracks" / "broken.json").write_text("{", encoding="utf-8")
    try:
        sl.link(root, "broken", "x")
        refused = False
    except ValueError as exc:
        refused = "cannot be linked" in str(exc)
    check("a track file that does not parse is not written over", refused)
finally:
    shutil.rmtree(tmp, ignore_errors=True)


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("showlibrary: all checks pass")
