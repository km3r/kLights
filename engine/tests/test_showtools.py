"""
Tests for the show folder as tools (`engine/showtools.py`), called directly.

`test_mcp.py` walks the main flows over a real stdio pipe. This suite covers
what a conversation can send that a happy path never does: every edit op on
the lane it cannot apply to, ops that depend on an earlier op in the same
list, an index that is not a number, an id that tries to leave the folder, a
timeline for a track that has none yet, a timeline file that no longer loads.
The contract under test is `apply_ops`' own -- what it cannot do it says, it
does not raise -- and edit_timeline's: all of a list of ops, or none of them.

Every write goes to a throwaway copy of shared/show-example.

Run: python engine/tests/test_showtools.py
"""

import copy
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import showfiles  # noqa: E402
from engine import showtools as st  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label + (f"  -- {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="klights-showtools-"))
SHOW = TMP / "shows"
shutil.copytree(REPO / "shared" / "show-example", SHOW)
SD = str(SHOW)
TL = SHOW / "timelines" / "synth-128.json"


def timeline_doc() -> dict:
    return json.loads(TL.read_text(encoding="utf-8"))


def row_of(doc, rid):
    return next((r for r in doc["rows"] if r["id"] == rid), None)


def item_ids(doc, rid):
    return [i["id"] for i in (row_of(doc, rid) or {}).get("items") or []]


try:
    print("\n1. finding the folder")
    saved_env = os.environ.pop(showfiles.ENV_VAR, None)
    real_local = showfiles.LOCAL_CONFIG
    showfiles.LOCAL_CONFIG = TMP / "no-such-local.json"
    try:
        st.status()
        raised = ""
    except ValueError as exc:
        raised = str(exc)
    check("with no show folder anywhere, it says how to give one",
          "show_dir" in raised and "KLIGHTS_SHOW_DIR" in raised, raised)
    os.environ[showfiles.ENV_VAR] = SD
    check("$KLIGHTS_SHOW_DIR is honoured when no folder is passed",
          st.status()["dir"] == SD)
    del os.environ[showfiles.ENV_VAR]
    if saved_env is not None:
        os.environ[showfiles.ENV_VAR] = saved_env
    showfiles.LOCAL_CONFIG = real_local

    print("\n2. reading")
    status = st.status(SD)
    check("status lists every kind of document and reports the folder clean",
          status["ok"] and status["tracks"] == ["synth-128"]
          and status["timelines"] == ["synth-128"] and status["template_sets"] == ["club"]
          and len(status["routines"]) == 4, f"{status['errors']}")
    routines = {r["id"]: r for r in st.list_routines(SD)["routines"]}
    check("list_routines carries bars, roles, params and variation names",
          set(routines) == {"build-rise", "fan-drop", "idle-orbit", "verse-sweep"}
          and all("bars" in r and "variations" in r for r in routines.values()))
    sets = st.list_template_sets(SD)["template_sets"]
    check("list_template_sets names each set's phrases",
          sets[0]["id"] == "club" and "Chorus" in sets[0]["phrases"], f"{sets}")
    track = st.list_tracks(SD)["tracks"][0]
    check("list_tracks summarises identity and whether it has a timeline",
          track["title"] == "synthetic 128" and track["bpm"] == 128.0
          and track["has_timeline"] is True)
    got = st.get_doc("timeline", "synth-128", SD)
    check("get_doc returns the document, valid, with an r: rev",
          got["ok"] and got["doc"]["track"] == "synth-128" and got["rev"].startswith("r:"))
    missing = st.get_doc("routine", "nope", SD)
    check("an absent document is an error with an empty rev",
          not missing["ok"] and missing["rev"] == "" and "no routine 'nope'" in missing["errors"][0])
    try:
        st.get_doc("routine", "../../etc/passwd", SD)
        escaped = "no error"
    except ValueError as exc:
        escaped = str(exc)
    check("an id that tries to leave the folder is refused before any file is opened",
          "not a usable id" in escaped, escaped)

    print("\n3. apply_ops: each op, and each way it can fail")
    base = timeline_doc()

    def apply(*ops):
        doc = copy.deepcopy(base)
        changes, problems = st.apply_ops(doc, list(ops))
        return doc, changes, problems

    doc, ch, pr = apply({"op": "add_row", "row": {"id": "new", "type": "hits", "items": []},
                         "index": 0})
    check("add_row at index 0 puts the lane on top", not pr and doc["rows"][0]["id"] == "new"
          and "at position 0" in ch[0])
    doc, ch, pr = apply({"op": "add_row", "row": {"id": "new", "type": "hits"}})
    check("with no index it goes at the bottom", not pr and doc["rows"][-1]["id"] == "new")
    doc, ch, pr = apply({"op": "add_row", "row": {"id": "new", "type": "hits"}, "index": -5},
                        {"op": "add_row", "row": {"id": "new2", "type": "hits"}, "index": 99})
    check("an index past either end is clamped, not an error",
          not pr and doc["rows"][0]["id"] == "new" and doc["rows"][-1]["id"] == "new2")
    for bad in ("top", None, True, [1], float("nan"), float("inf"), float("-inf")):
        doc, ch, pr = apply({"op": "add_row", "row": {"id": "new"}, "index": bad})
        check(f"an index of {bad!r} is a problem it reports, not an exception",
              pr and "index must be a number" in pr[0] and row_of(doc, "new") is None, f"{pr}")
    doc, ch, pr = apply({"op": "add_row", "row": {"type": "hits"}})
    check("a row without an id is refused", pr and "needs a row with an id" in pr[0])
    doc, ch, pr = apply({"op": "add_row", "row": "hits"})
    check("so is a row that is not an object", pr and "needs a row with an id" in pr[0])
    doc, ch, pr = apply({"op": "add_row", "row": {"id": "hits"}})
    check("and one whose id is taken", pr and "already a row 'hits'" in pr[0])

    doc, ch, pr = apply({"op": "remove_row", "row": "vj-opacity"})
    check("remove_row takes the lane away", not pr and row_of(doc, "vj-opacity") is None)
    doc, ch, pr = apply({"op": "remove_row", "row": "ghost"})
    check("removing a lane that is not there is a problem", pr and "no row 'ghost'" in pr[0])

    doc, ch, pr = apply({"op": "add_item", "row": "hits",
                         "item": {"id": "bo9", "hit": "blackout", "at": 300, "len": 1}})
    check("add_item appends to the lane and says what and where",
          not pr and item_ids(doc, "hits")[-1] == "bo9" and "blackout 'bo9' at beat 300" in ch[0],
          f"{ch}")
    doc, ch, pr = apply({"op": "add_item", "row": "ghost", "item": {"id": "x"}})
    check("add_item to a lane that is not there is a problem", pr and "existing row" in pr[0])
    doc, ch, pr = apply({"op": "add_item", "row": "hits", "item": "bo9"})
    check("as is an item that is not an object", pr and "existing row" in pr[0])
    doc, ch, pr = apply({"op": "add_item", "row": "hits", "item": {"id": "chorus1", "at": 0}})
    check("item ids are unique across the WHOLE timeline, not per lane",
          pr and "'chorus1' already exists" in pr[0])
    doc, ch, pr = apply({"op": "add_row", "row": {"id": "spare", "type": "hits"}},
                        {"op": "add_item", "row": "spare",
                         "item": {"id": "bo9", "hit": "blackout", "at": 8, "len": 1}})
    check("a lane added earlier in the same list can be filled by a later op",
          not pr and item_ids(doc, "spare") == ["bo9"], f"{pr}")

    doc, ch, pr = apply({"op": "update_item", "id": "chorus1", "set": {"fade": 3, "at": 164}})
    item = next(i for i in row_of(doc, "scene")["items"] if i["id"] == "chorus1")
    check("update_item changes only the fields it names, and reports before -> after",
          not pr and item["fade"] == 3 and item["at"] == 164 and "at 160 -> 164" in ch[0], f"{ch}")
    doc, ch, pr = apply({"op": "update_item", "id": "ghost", "set": {"at": 1}})
    check("updating an item that is not there is a problem", pr and "existing item" in pr[0])
    doc, ch, pr = apply({"op": "update_item", "id": "chorus1", "set": [("at", 1)]})
    check("as is a set that is not an object", pr and "existing item" in pr[0])

    doc, ch, pr = apply({"op": "remove_item", "id": "fl2"})
    check("remove_item takes it off its lane", not pr and "fl2" not in item_ids(doc, "hits"))
    doc, ch, pr = apply({"op": "remove_item", "id": "fl2"}, {"op": "remove_item", "id": "fl2"})
    check("removing the same item twice: the second is a problem",
          len(ch) == 1 and pr and "no item 'fl2'" in pr[0])

    doc, ch, pr = apply({"op": "set_points", "row": "size", "points": [[0, 0.2], [64, 0.9, "ease"]]})
    check("set_points replaces an automation lane's points",
          not pr and row_of(doc, "size")["points"] == [[0, 0.2], [64, 0.9, "ease"]]
          and "set 2 points" in ch[0])
    doc, ch, pr = apply({"op": "set_points", "row": "hits", "points": []})
    check("set_points on a lane that is not automation is a problem",
          pr and "automation row" in pr[0])
    doc, ch, pr = apply({"op": "set_wave", "row": "size",
                         "wave": {"shape": "triangle", "bars": 2, "depth": 0.3}})
    check("set_wave names the shape it put on", not pr and "triangle wave" in ch[0])
    doc, ch, pr = apply({"op": "set_wave", "row": "size", "wave": {"bars": 2}})
    check("a wave with no shape is described as the sine it defaults to",
          not pr and "sine wave" in ch[0])
    doc, ch, pr = apply({"op": "set_wave", "row": "size", "wave": {"shape": "sine"}},
                        {"op": "set_wave", "row": "size", "wave": None})
    check("a null wave takes it off again", not pr and "wave" not in row_of(doc, "size")
          and "removed the wave" in ch[1])
    doc, ch, pr = apply({"op": "set_wave", "row": "size", "wave": "wobbly"})
    check("a wave that is not an object is passed to validation, not crashed on",
          not pr and row_of(doc, "size")["wave"] == "wobbly")
    doc, ch, pr = apply({"op": "set_wave", "row": "scene", "wave": {"shape": "sine"}})
    check("set_wave on a lane that is not automation is a problem",
          pr and "automation row" in pr[0])
    doc, ch, pr = apply({"op": "set_audio", "row": "size",
                         "audio": {"band": "low", "depth": 0.3, "release": 0.5}})
    check("set_audio names the band the lane now follows",
          not pr and "follows the audio's low band" in ch[0]
          and row_of(doc, "size")["audio"]["release"] == 0.5)
    doc, ch, pr = apply({"op": "set_audio", "row": "size", "audio": {"band": "low"}},
                        {"op": "set_audio", "row": "size", "audio": None})
    check("a null audio takes it off again", not pr and "audio" not in row_of(doc, "size")
          and "no longer follows" in ch[1])
    doc, ch, pr = apply({"op": "set_audio", "row": "size", "audio": "loud"})
    check("audio that is not an object is passed to validation, not crashed on",
          not pr and row_of(doc, "size")["audio"] == "loud")
    doc, ch, pr = apply({"op": "set_audio", "row": "scene", "audio": {"band": "low"}})
    check("set_audio on a lane that is not automation is a problem",
          pr and "automation row" in pr[0])

    doc, ch, pr = apply({"op": "set", "key": "palette", "value": "cool"})
    check("set may change the palette", not pr and doc["palette"] == "cool")
    doc, ch, pr = apply({"op": "set", "key": "track", "value": "other"})
    check("but not which track the timeline belongs to",
          pr and "only palette, palettes or grid_rev" in pr[0] and doc["track"] == "synth-128")

    doc, ch, pr = apply({"op": "explode"}, "not even a dict", {})
    check("unknown ops, non-objects and an op with no name are each named by position",
          len(pr) == 3 and pr[0].startswith("op 0 (explode): unknown op")
          and pr[1].startswith("op 1 (None)") and pr[2].startswith("op 2 (None)"), f"{pr}")
    doc, ch, pr = apply({"op": "remove_item", "id": "fl1"}, {"op": "remove_item", "id": "ghost"},
                        {"op": "remove_item", "id": "fl2"})
    check("a failing op does not stop the ones after it from being tried "
          "(so every problem is reported at once)",
          len(ch) == 2 and len(pr) == 1 and pr[0].startswith("op 1"), f"{ch} {pr}")
    doc = {}
    st.apply_ops(doc, [{"op": "add_row", "row": {"id": "first"}}])
    check("a document with no rows gets a rows list", doc == {"rows": [{"id": "first"}]})
    ops = [{"op": "add_row", "row": {"id": "spare", "type": "hits", "items": []}},
           {"op": "add_item", "row": "spare",
            "item": {"id": "bo9", "hit": "blackout", "at": 8, "len": 1, "params": {"a": 1}}},
           {"op": "update_item", "id": "chorus1", "set": {"params": {"width": 2}}},
           {"op": "set_points", "row": "size", "points": [[0, 0.5]]}]
    pristine = copy.deepcopy(ops)
    doc, ch, pr = apply(*ops)
    row_of(doc, "spare")["items"][0]["params"]["a"] = 99
    row_of(doc, "size")["points"].append([4, 1.0])
    check("applying ops never changes the ops themselves, and the document shares "
          "nothing with them", ops == pristine and not pr, f"{ops}")
    doc2, ch2, pr2 = apply(*ops)
    check("so the same ops apply twice -- a dry run, then the write -- with the same result",
          not pr2 and ch2 == ch, f"{pr2}")

    print("\n4. edit_timeline: all of the ops, or none of them")
    before = TL.read_bytes()
    rev = st.get_doc("timeline", "synth-128", SD)["rev"]
    out = st.edit_timeline("synth-128", [{"op": "remove_item", "id": "fl1"},
                                         {"op": "remove_item", "id": "ghost"}],
                           base_rev=rev, write=True, show_dir=SD)
    check("one bad op in a list writes none of the list, even with write and a good rev",
          not out["ok"] and not out["written"] and TL.read_bytes() == before
          and out["changes"] == ["removed 'fl1' from 'hits'"], f"{out}")
    out = st.edit_timeline("synth-128", [{"op": "update_item", "id": "up1",
                                          "set": {"id": "chorus1"}}], show_dir=SD)
    check("renaming an item onto another's id is applied by the op but refused by validation",
          not out["ok"] and any("chorus1" in e for e in out["errors"]), f"{out.get('errors')}")
    out = st.edit_timeline("nope", [], show_dir=SD)
    check("a track that is not in the folder is an error naming it",
          not out["ok"] and "track 'nope' is not usable" in out["errors"][0])

    # A second track with no timeline yet: the first edit creates one.
    track_doc = json.loads((SHOW / "tracks" / "synth-128.json").read_text(encoding="utf-8"))
    track_doc["id"] = "second"
    showfiles.write_doc(SHOW / "tracks" / "second.json", track_doc, "track", base_rev="")
    ops = [{"op": "add_row", "row": {"id": "hits", "type": "hits", "items": []}},
           {"op": "add_item", "row": "hits",
            "item": {"id": "b1", "hit": "blackout", "at": 4, "len": 1}}]
    out = st.edit_timeline("second", ops, show_dir=SD)
    check("a track with no timeline gets a new one on its first edit (dry run)",
          out["ok"] and out["created"] and out["doc"]["track"] == "second"
          and out["doc"]["grid_rev"] and not (SHOW / "timelines" / "second.json").exists(),
          f"{out.get('errors')}")
    out = st.edit_timeline("second", ops, base_rev="", write=True, show_dir=SD)
    check("written as a new file with base_rev \"\"",
          out["written"] and (SHOW / "timelines" / "second.json").exists(), f"{out.get('errors')}")
    out = st.edit_timeline("second", [{"op": "remove_item", "id": "b1"}], base_rev="",
                           write=True, show_dir=SD)
    check("and base_rev \"\" is refused once the file exists",
          not out["ok"] and not out["written"], f"{out.get('errors')}")

    (SHOW / "timelines" / "second.json").write_text("{ broken", encoding="utf-8")
    out = st.edit_timeline("second", [], show_dir=SD)
    check("a timeline file that no longer loads is reported, pointing at put_timeline",
          not out["ok"] and "does not load" in out["errors"][0]
          and "put_timeline" in out["errors"][0], f"{out}")
    (SHOW / "timelines" / "second.json").unlink()

    print("\n5. put_doc")
    check("a document that is not an object is refused",
          not st.put_doc("routine", ["not", "a", "dict"], show_dir=SD)["ok"])
    out = st.put_doc("routine", {"id": "../escape"}, show_dir=SD)
    check("an id that would leave the folder is refused",
          not out["ok"] and "not a usable id" in out["errors"][0])
    routine = st.get_doc("routine", "fan-drop", SD)
    fresh = copy.deepcopy(routine["doc"])
    fresh["id"], fresh["name"] = "fan-drop-copy", "Fan drop (copy)"
    out = st.put_doc("routine", fresh, show_dir=SD)
    check("a dry run of a new document hints base_rev \"\"",
          out["ok"] and not out["written"] and not out["exists"]
          and 'base_rev=""' in out["hint"], f"{out}")
    out = st.put_doc("routine", routine["doc"], show_dir=SD)
    check("a dry run of an existing one hints its current rev",
          out["ok"] and out["exists"] and routine["rev"] in out["hint"])
    out = st.put_doc("routine", fresh, base_rev="", write=True, show_dir=SD)
    check("a new document is written with base_rev \"\", returning its rev",
          out["written"] and out["rev"].startswith("r:")
          and (SHOW / "routines" / "fan-drop-copy.json").exists(), f"{out.get('errors')}")
    check("and the folder still loads clean with it in", st.status(SD)["ok"])
    out = st.put_doc("routine", {**fresh, "bars": -1}, base_rev=out["rev"], write=True,
                     show_dir=SD)
    check("an invalid document is never written, even with the right rev",
          not out["ok"] and not out["written"])

    print("\n6. link_track, lint and explain")
    out = st.link_track("synth-128", "synthetic 128", "kLights", "test track", show_dir=SD)
    check("linking a description the track already answers to would add nothing",
          out["ok"] and out["dry_run"] and out["would_add"] is None, f"{out}")
    out = st.link_track("nope", "x", show_dir=SD)
    check("linking a track that is not there is an error", not out["ok"])
    out = st.link_track("synth-128", "Guest Copy", write=True, show_dir=SD)
    again = st.link_track("synth-128", "Guest Copy", write=True, show_dir=SD)
    check("writing a new alias adds it once; a second write reports nothing new",
          out["written"] and not again["written"] and again["added"] == "already",
          f"{out} {again}")
    lint = st.lint(SD)
    check("lint with no event checks the folder alone", lint["ok"] and "event" not in lint
          and lint["rig_problems"] == {}, f"{lint['errors']}")
    lint = st.lint(SD, event=str(REPO / "events" / "despacio"))
    check("lint takes an event as a path too", lint["ok"] and lint["event"] == "despacio",
          f"{lint.get('rig_problems')}")
    ex = st.explain("synth-128", 0, SD)
    check("explain at beat 0 is bar 1, with no rig unless an event is given",
          ex["ok"] and ex["bar"] == 1 and "rig" not in ex)
    ex = st.explain("synth-128", -3.5, SD)
    check("a beat in the pickup before the first downbeat is bar 0, not an error",
          ex["ok"] and ex["bar"] == 0, f"{ex.get('bar')}")
    ex = st.explain("nope", 10, SD)
    check("explaining a track with no timeline is an error", not ex["ok"]
          and "no valid timeline" in ex["errors"][0])
finally:
    shutil.rmtree(TMP, ignore_errors=True)


print()
if failures:
    print(f"showtools: {len(failures)} FAILED")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("showtools: all checks pass")
