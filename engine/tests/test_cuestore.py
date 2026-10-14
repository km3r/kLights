"""
Tests for the cue list as Studio edits it: `engine/cuestore.py`, the `cue_*`
commands, the live install that keeps the list's place, the watcher,
`GET /api/cues` and the tools behind the MCP server (`engine/cuetools.py`).

Every controller here runs against a COPY of the event: these commands write
cues.json (and, where a look is renamed, parametric_looks.json), and a suite
that crashed between a save and its cleanup must not leave junk in tonight's
show.

The command half drives the controller's queue directly with a client whose
replies are captured (as test_looks.py does), so every answer -- including the
ones that arrive after the worker finishes -- is checked exactly.

Run: python engine/tests/test_cuestore.py
"""

import atexit
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import api as apimod  # noqa: E402
from engine import config as configmod  # noqa: E402
from engine import cues as cuesmod  # noqa: E402
from engine import cuestore  # noqa: E402
from engine import cuetools  # noqa: E402
from engine import library as libmod  # noqa: E402
from engine import lookstore  # noqa: E402
from engine import patch as patchmod  # noqa: E402
from engine import rig as rigmod  # noqa: E402
from engine import server as servermod  # noqa: E402
from engine import showfiles  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label + (f"  -- {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="klights-cues-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
_SKIP = shutil.ignore_patterns("__pycache__", "*.bak", ".engine.lock", "backups")
shutil.copytree(rigmod.VENUE_LIBRARY, TMP / "venues", ignore=_SKIP)
rigmod.VENUE_LIBRARY = patchmod.VENUES = TMP / "venues"


def event_copy(name: str) -> Path:
    path = TMP / name
    shutil.copytree(REPO / "events" / "despacio", path, ignore=_SKIP)
    return path


def raises(fn, *needles):
    """The ValueError `fn` raises, if its message holds every needle."""
    try:
        fn()
    except ValueError as exc:
        return all(n in str(exc) for n in needles), str(exc)
    return False, "nothing raised"


def read(event: Path) -> dict:
    return json.loads((event / "cues.json").read_text(encoding="utf-8"))


def names(event: Path) -> list[str]:
    return [c["name"] for c in read(event)["cues"]]


NIGHT = ["Warm Up", "Idle", "Deep", "Spiral", "Cathedral", "Peak", "Build", "Drop",
         "Landing"]

# -- 1. the file ----------------------------------------------------------------
print("\n1. cues.json, written as it was written by hand")
original = (REPO / "events" / "despacio" / "cues.json").read_text(
    encoding="utf-8").replace("\r\n", "\n")
doc = json.loads(original)
gaps = cuestore.gaps_of(original)
check("the file sets its name and its cues apart with a blank line, and that is read",
      gaps == {"name", "cues"}, f"{sorted(gaps)}")
text = cuestore.render(doc, gaps)
before, after = original.splitlines(), text.splitlines()
check("a document renders in the layout the file has by hand, byte for byte -- a "
      "cue to a block of lines, each slot's looks on one, the blank lines where "
      "they were -- so a save that changes nothing changes nothing",
      text == original,
      f"{next(((a, b) for a, b in zip(before, after) if a != b), len(before) - len(after))}")
plain = json.dumps(doc, indent=2) + "\n"
check("a file with no blank lines gets none, and still reads back as itself",
      cuestore.gaps_of(plain) == frozenset()
      and "\n\n" not in cuestore.render(doc, cuestore.gaps_of(plain))
      and json.loads(cuestore.render(doc, cuestore.gaps_of(plain))) == doc)
check("a file this module starts is laid out the same way",
      cuestore.gaps_of(None) == {"name", "cues"})
accented = cuestore.render({"name": "Soirée", "cues": [{"name": "Café", "notes": "45° up"}]})
check("a name or a note is written as it was typed, not escaped to ASCII",
      '"Café"' in accented and '"45° up"' in accented and "\\u00" not in accented)

# A look renamed to a name long enough to push a cue's slot onto lines of its
# own: `lookstore.relaid` cannot follow a change of line count, and used to
# fall back to the plain dump -- which threw the whole layout away.
long_name = "Split Warm/Cool, the long way round the room and back to the ball, twice over"
longer = {**doc, "cues": [lookstore.renamed(c, "Split Warm/Cool", long_name) for c in doc["cues"]]}
kept = cuestore.render_like(original, doc, longer)
check("a rename that changes how many lines a cue takes still leaves the file in "
      "its layout: the two editors of this file agree about it",
      lookstore.relaid(original, doc, longer) is None and kept is not None
      and json.loads(kept) == longer and cuestore.gaps_of(kept) == gaps
      and kept.count("\n") > original.count("\n"))
layout_copy = TMP / "cues-layout.json"
layout_copy.write_text(original, encoding="utf-8", newline="\n")
moved_doc, moved_count, moved_text = lookstore._renamed_file(
    layout_copy, "cues", "Split Warm/Cool", long_name)
check("and that is what a look rename writes", moved_count == 4 and moved_text == kept,
      f"{moved_count}")
check("a file laid out some other way is not guessed at: the plain dump is "
      "written instead", cuestore.render_like(plain, doc, longer) is None)

# -- 2. a cue from a client -------------------------------------------------------
print("\n2. a cue as a client sends it")
good = cuestore.clean_cue({
    "notes": "  doors  ", "name": "  Warm   Up ", "fade": 32.0, "hold": 0,
    "movement": {"corner movers": "Heads - Ball"}, "color": {}, "level": None,
    "master": 0.55, "speed": None, "macro": {"size": 0.6, "spread": 0.0},
    "rates": {"color": 0.5, "movement": None}, "params": None})
check("is stored tidied, in the file's key order: the name's spaces, a whole "
      "number as one, an empty slot and a hold of 0 left out",
      good == {"name": "Warm Up", "movement": {"corner movers": "Heads - Ball"},
               "fade": 32, "master": 0.55, "macro": {"size": 0.6, "spread": 0},
               "rates": {"color": 0.5}, "notes": "doors"}
      and list(good) == ["name", "movement", "fade", "master", "macro", "rates", "notes"]
      and isinstance(good["fade"], int), f"{good}")
check("a cue that says only its name fades over the loader's own default",
      cuestore.clean_cue({"name": "Bare"}) == {"name": "Bare", "fade": 8})
check("a hold is kept when it is one", cuestore.clean_cue({"name": "A", "hold": 16})["hold"] == 16)
check("an EMPTY params is not the same as none -- it puts every routine the cue "
      "names back to its authored values -- so it is kept",
      cuestore.clean_cue({"name": "A", "params": {}})["params"] == {}
      and "params" not in cuestore.clean_cue({"name": "A", "params": None}))
check("per-look tuning comes through as it was sent",
      cuestore.clean_cue({"name": "A", "params": {"Ball Orbit": {"radius": 24, "color": [1, 0, 0]}}})
      ["params"] == {"Ball Orbit": {"radius": 24, "color": [1, 0, 0]}})
check("a cut is a fade of 0, and it is written", cuestore.clean_cue({"name": "Drop", "fade": 0})
      == {"name": "Drop", "fade": 0})
for label, cue, needles in (
    ("no name", {"fade": 8}, ("needs a name",)),
    ("a name of spaces", {"name": "   "}, ("needs a name",)),
    ("a name too long", {"name": "x" * 81}, ("at most 80",)),
    ("a field a cue does not have", {"name": "A", "fade_beats": 4}, ("no field 'fade_beats'", "fade")),
    ("a slot that is not group to look", {"name": "A", "movement": "Ball Orbit"},
     ("movement is an object of fixture group to look name",)),
    ("a look that is not a name", {"name": "A", "color": {"pinspots": 3}}, ("color is an object",)),
    ("a group with no name", {"name": "A", "color": {"": "MH Red"}}, ("color is an object",)),
    ("a negative fade", {"name": "A", "fade": -1}, ("fade must be at least 0", "cut")),
    ("a fade that is text", {"name": "A", "fade": "slow"}, ("fade must be a number",)),
    ("true where a number belongs", {"name": "A", "hold": True}, ("hold must be a number",)),
    ("NaN", {"name": "A", "fade": float("nan")}, ("fade must be a number",)),
    ("an integer too big to be a number of beats", {"name": "A", "hold": 10 ** 400},
     ("hold must be a number",)),
    ("a speed the clock would refuse at GO", {"name": "A", "speed": 100}, ("speed must be between",)),
    ("a master over full", {"name": "A", "master": 1.5}, ("master must be between 0 and 1",)),
    ("a macro that is not an object", {"name": "A", "macro": 1.2}, ("macro is an object",)),
    ("a macro key there is not", {"name": "A", "macro": {"zoom": 2}}, ("macro has no 'zoom'",)),
    ("a size the console's own slider stops short of", {"name": "A", "macro": {"size": 40}},
     ("macro size must be between 0 and 3",)),
    ("a center that is not two angles", {"name": "A", "macro": {"center": [10]}},
     ("[bearing, elevation]",)),
    ("a rate for a slot there is not", {"name": "A", "rates": {"strobe": 2}}, ("no slot 'strobe'",)),
    ("a rate past what a slot runs at", {"name": "A", "rates": {"color": 9}},
     ("color rate must be between 0 and 8",)),
    ("params that are not tuning by look", {"name": "A", "params": {"Ball Orbit": 24}},
     ("params is tuning by look name",)),
    ("NaN inside a look's tuning", {"name": "A", "params": {"X": {"color": [float("nan"), 0, 0]}}},
     ("finite",)),
    ("an object where an argument's value belongs", {"name": "A", "params": {"X": {"r": {"a": 1}}}},
     ("not a value",)),
    ("notes that are not text", {"name": "A", "notes": 5}, ("notes must be text",)),
    ("something that is not an object", ["Warm Up"], ("a cue is an object",)),
):
    ok, said = raises(lambda cue=cue: cuestore.clean_cue(cue), *needles)
    check(f"refused: {label}", ok, said)

# -- 3. what is wrong with a cue, and where a list is -----------------------------
print("\n3. flags, and keeping the place")
library = {e.name: e for e in libmod.load_library(REPO / "events" / "despacio")}
rig = rigmod.load_rig(REPO / "events" / "despacio")
groups = cuestore.known_groups(rig)
check("a slot's group can be any tag a fixture carries -- including one the rig "
      "folds into another -- a fixture by name, or the lane a look with no "
      "groups fills", {"corner movers", "movers", "pinspots", "Pinspot #1"} <= groups)
fine = {"name": "Idle", "movement": {"corner movers": "Ball Wave"},
        "color": {"corner movers": "Split Warm/Cool", "pinspots": "Pin Ball Glow"}}
check("a cue whose looks are all there, each in its own slot, has nothing said of it",
      cuestore.flags(fine, library, groups) == [] and cuestore.missing(fine, library) == [])
odd = {"name": "Odd", "movement": {"corner movers": "No Such Look", "movers": "Ball Wave"},
       "color": {"lasers": "MH Red", "pinspots": "Ball Wave"},
       "params": {"Ball Wave": {}, "Ball Orbit": {"radius": 3}}}
said = cuestore.flags(odd, library, groups)
check("a look the library lacks is FLAGGED, by slot and group, and named",
      cuestore.missing(odd, library) == ["No Such Look"]
      and any("'No Such Look'" in s and "not in the library" in s and "movement" in s for s in said),
      f"{said}")
check("so is a look in a slot that is not its own",
      any("color holds 'Ball Wave', which is a movement look" in s for s in said), f"{said}")
check("a group nothing on this rig carries",
      any("nothing on this rig is tagged 'lasers'" in s for s in said), f"{said}")
check("more than one movement look: the rig runs one",
      any("only the first that exists is taken" in s for s in said), f"{said}")
check("and tuning for a look the cue does not put in a slot",
      [s for s in said if "tuning" in s] == [
          "it carries tuning for 'Ball Orbit', which it does not put in a slot: that "
          "tuning is ignored"],
      f"{said}")
check("with no rig to ask, the groups are not judged",
      not any("tagged" in s for s in cuestore.flags(odd, library, None)))
night = cuesmod.load(REPO / "events" / "despacio" / "cues.json")
described = [cuestore.describe(c, library, groups) for c in night.cues]
check("the event's own list: every look is there (cues.missing_looks agrees)",
      all(d["missing"] == [] for d in described)
      and cuesmod.missing_looks(night, list(library)) == [])
check("a described cue carries every field an editor needs, unset ones null",
      described[0]["name"] == "Warm Up" and described[0]["fade"] == 32
      and described[0]["hold"] == 0 and described[0]["speed"] is None
      and described[0]["master"] == 0.55 and described[0]["macro"] == {"size": 0.6, "spread": 0}
      and described[0]["level"] == {} and described[0]["params"] is None, f"{described[0]}")


def cue_list(*cue_names, at=-1):
    return cuesmod.CueList("t", [cuesmod.Cue(n) for n in cue_names], index=at)


def cues_of(*cue_names):
    return [cuesmod.Cue(n) for n in cue_names]


check("before the first GO a list stays before it, whatever the cues become",
      cuestore.carry(cue_list("a", "b"), cues_of("x", "a")) == (-1, None)
      and cuestore.carry(None, cues_of("a")) == (-1, None))
check("the current cue stays current where it is",
      cuestore.carry(cue_list("a", "b", "c", at=1), cues_of("a", "b", "c")) == (1, None))
check("wherever it has moved to",
      cuestore.carry(cue_list("a", "b", "c", at=1), cues_of("b", "a", "c")) == (0, None)
      and cuestore.carry(cue_list("a", "b", "c", at=1), cues_of("new", "a", "c", "b")) == (3, None))
check("and under its new name after a rename",
      cuestore.carry(cue_list("a", "b", "c", at=1), cues_of("a", "bee", "c"), ("b", "bee")) == (1, None))
check("another cue's rename does not move it",
      cuestore.carry(cue_list("a", "b", "c", at=1), cues_of("ay", "b", "c"), ("a", "ay")) == (1, None))
check("deleted, the list falls back to the nearest cue before it that is left, "
      "so GO takes what would have come next -- and says which cue went",
      cuestore.carry(cue_list("a", "b", "c", at=1), cues_of("a", "c")) == (0, "b")
      and cuestore.carry(cue_list("a", "b", "c", "d", at=2), cues_of("d", "a")) == (1, "c"))
check("with none before it left, it is back before the first cue",
      cuestore.carry(cue_list("a", "b", "c", at=0), cues_of("b", "c")) == (-1, "a")
      and cuestore.carry(cue_list("a", "b", at=1), cues_of("x", "y")) == (-1, "b"))
check("two cues of one name, in a file edited by hand: the one at the same place "
      "is the one that stays current",
      cuestore.carry(cue_list("a", "x", "b", "x", at=3), cues_of("a", "x", "b", "x")) == (3, None))

# -- 4. the edits, on a file ---------------------------------------------------------
print("\n4. the edits, straight on the file")
EV = event_copy("store")
rev0 = cuestore.file_rev(EV)
loaded, loaded_rev = cuestore.read_list(EV)
check("the list and the rev a save must quote come from one read of the file",
      loaded_rev == rev0 == showfiles.doc_rev(EV / "cues.json")
      and [c.name for c in loaded.cues] == NIGHT and loaded.name == "Night")
smoke = cuestore.clean_cue({"name": "Smoke", "movement": {"corner movers": "Ball Wave"},
                            "fade": 4, "hold": 16})
done = cuestore.save(EV, smoke, None, rev0, dry_run=True)
check("a dry run checks everything and writes nothing",
      done == {"rev": rev0, "index": 9, "count": 10} and cuestore.file_rev(EV) == rev0
      and not (EV / "cues.json.bak").exists(), f"{done}")
ok, said = raises(lambda: cuestore.save(EV, smoke, None, "r:000000000000"),
                  "changed since you opened it")
check("a save quoting a rev that is not the file's is refused, and nothing is written",
      ok and cuestore.file_rev(EV) == rev0, said)
ok, said = raises(lambda: cuestore.save(EV, smoke, None, None), "base_rev is required")
check("and so is one that quotes none", ok, said)
done = cuestore.save(EV, smoke, None, rev0, at=2)
rev1 = done["rev"]
saved = read(EV)
check("a new cue goes in at the place asked for, and the file's new rev comes back",
      done["index"] == 2 and done["count"] == 10 and rev1 == cuestore.file_rev(EV) != rev0
      and names(EV) == NIGHT[:2] + ["Smoke"] + NIGHT[2:], f"{names(EV)}")
check("the file keeps everything that was in it: its schema line, its comments, "
      "its name, and every other cue exactly",
      saved["$schema"] == doc["$schema"] and saved["_comment"] == doc["_comment"]
      and saved["name"] == "Night" and [c for c in saved["cues"] if c["name"] != "Smoke"] == doc["cues"]
      and saved["cues"][2] == smoke)
text_now = (EV / "cues.json").read_text(encoding="utf-8")
check("and its layout: only the new cue's lines are new",
      set(original.splitlines()) <= set(text_now.splitlines())
      and len(text_now.splitlines()) - len(original.splitlines()) == 6
      and '      "movement": { "corner movers": "Ball Wave" },' in text_now.splitlines(),
      f"{len(text_now.splitlines()) - len(original.splitlines())}")
check("its one .bak is the file as it was",
      json.loads((EV / "cues.json.bak").read_text(encoding="utf-8")) == doc)
check("what was written loads, as the engine loads it",
      [c.name for c in cuesmod.load(EV / "cues.json").cues][2] == "Smoke"
      and cuesmod.load(EV / "cues.json").cues[2].hold == 16.0)
ok, said = raises(lambda: cuestore.save(EV, smoke, None, rev1), "already a cue called 'Smoke'")
check("a second cue of the same name is refused: the list keeps its place by name", ok, said)
ok, said = raises(lambda: cuestore.save(EV, {**smoke, "name": "X"}, None, rev1, at=99),
                  "place in the running order")
check("and so is a place that is not in the list", ok, said)

# A key added to a cue by hand -- a comment, a field this engine does not know.
by_hand = read(EV)
by_hand["cues"][2]["_comment"] = "the haze machine needs a minute"
by_hand["cues"][2]["haze"] = {"level": 0.4}
(EV / "cues.json").write_text(cuestore.render(by_hand, {"name", "cues"}), encoding="utf-8", newline="\n")
rev1 = cuestore.file_rev(EV)
done = cuestore.save(EV, {**smoke, "fade": 12}, "Smoke", rev1)
changed = read(EV)["cues"][2]
check("changing a cue keeps what its entry holds beyond what this engine writes",
      changed["fade"] == 12 and changed["_comment"] == "the haze machine needs a minute"
      and changed["haze"] == {"level": 0.4} and done["index"] == 2, f"{changed}")
done = cuestore.save(EV, {"name": "Smoke", "fade": 12}, "Smoke", done["rev"])
check("and a field the editor took off the cue is taken off the file",
      "movement" not in read(EV)["cues"][2] and "hold" not in read(EV)["cues"][2]
      and read(EV)["cues"][2]["haze"] == {"level": 0.4})
done = cuestore.save(EV, {"name": "Haze", "fade": 12}, "Smoke", done["rev"])
check("a different name renames it, where it is", names(EV)[2] == "Haze" and done["index"] == 2)
ok, said = raises(lambda: cuestore.save(EV, {"name": "Idle", "fade": 1}, "Haze", done["rev"]),
                  "already a cue called 'Idle'")
check("not to a name another cue has", ok, said)
ok, said = raises(lambda: cuestore.save(EV, {"name": "Y", "fade": 1}, "Nope", done["rev"]),
                  "no cue called 'Nope'")
check("and a cue that is not there cannot be changed", ok, said)
done = cuestore.save(EV, {"name": "Haze", "fade": 12}, "Haze", done["rev"], at=0)
check("a change can move the cue as well", names(EV)[0] == "Haze" and done["index"] == 0)

copied = cuestore.duplicate(EV, "Haze", "  Haze   again ", done["rev"])
check("a duplicate is the entry as the file has it, every key, under the new name "
      "and straight after the original",
      names(EV)[:2] == ["Haze", "Haze again"] and copied["index"] == 1
      and copied["name"] == "Haze again" and read(EV)["cues"][1]["haze"] == {"level": 0.4}
      and read(EV)["cues"][1]["_comment"] == "the haze machine needs a minute", f"{copied}")
ok, said = raises(lambda: cuestore.duplicate(EV, "Haze", "Idle", copied["rev"]), "already a cue called")
check("not under a name that is taken", ok, said)
moved = cuestore.move(EV, "Haze", 3, copied["rev"])
check("a move puts a cue at a place in the running order, the others closing up",
      names(EV)[:4] == ["Haze again", "Warm Up", "Idle", "Haze"] and moved["index"] == 3,
      f"{names(EV)[:5]}")
still = cuestore.move(EV, "Haze", 3, moved["rev"])
check("a move to where it already is writes nothing", still["rev"] == moved["rev"])
ok, said = raises(lambda: cuestore.move(EV, "Haze", 11, moved["rev"]), "place in the running order")
check("and one past the end is refused", ok, said)
gone = cuestore.delete(EV, "Haze again", moved["rev"])
gone = cuestore.delete(EV, "Haze", gone["rev"])
check("a delete removes the cue and nothing else", names(EV) == NIGHT and gone["count"] == 9)
named = cuestore.rename_list(EV, "  Saturday ", gone["rev"])
check("the list itself can be named, in place: it is the title of the GO card",
      read(EV)["name"] == "Saturday" and list(read(EV))[:3] == ["$schema", "_comment", "name"]
      and named["name"] == "Saturday")

# The loader has the last word on what is written.
rev = named["rev"]
ok, said = raises(lambda: cuestore.save(EV, {"name": "Bad", "fade": "slow"}, None, rev),
                  "cues.json is not usable", "fade must be a number")
check("whatever reaches a save, the document is held to what a LOAD would hold it "
      "to before anything is written -- here a cue that never went through the "
      "client check -- so no save writes a file the next start refuses",
      ok and cuestore.file_rev(EV) == rev and names(EV) == NIGHT, said)
ok, said = raises(lambda: cuestore.save(EV, {"name": "Bad", "fade": 1, "macro": float("nan")},
                                        None, rev), "not finite")
check("nor a number no JSON reader takes back", ok and cuestore.file_rev(EV) == rev, said)

one = TMP / "one"
one.mkdir()
(one / "cues.json").write_text(json.dumps({"cues": [{"name": "Only"}]}), encoding="utf-8")
ok, said = raises(lambda: cuestore.delete(one, "Only", cuestore.file_rev(one)),
                  "the only cue", "cannot be empty")
check("the last cue cannot be deleted: a list of none is one the loader refuses",
      ok and names(one) == ["Only"], said)
named = cuestore.rename_list(one, "Solo", cuestore.file_rev(one))
check("a list with no name gets one before its cues, where a person would look",
      list(read(one)) == ["name", "cues"] and read(one)["name"] == "Solo")
(one / "cues.json").write_text('{"cues": [{"name": "Only"},]}', encoding="utf-8")
ok, said = raises(lambda: cuestore.save(one, smoke, None, cuestore.file_rev(one)),
                  "is not valid JSON", "line 1")
check("a file that does not parse is not edited: the mistake would be buried",
      ok and (one / "cues.json").read_text(encoding="utf-8") == '{"cues": [{"name": "Only"},]}', said)
(one / "cues.json").write_text(json.dumps({"version": 99, "cues": [{"name": "Only"}]}),
                               encoding="utf-8")
ok, said = raises(lambda: cuestore.read_list(one), "newer than this engine understands")
check("nor is one this engine would not load", ok, said)

fresh = TMP / "fresh"
fresh.mkdir()
check("an event with no cue list has no rev, and no list",
      cuestore.file_rev(fresh) is None and cuestore.read_list(fresh) == (None, None)
      and cuestore.signature(fresh) == ())
ok, said = raises(lambda: cuestore.rename_list(fresh, "Night", ""), "make a cue first")
check("there is nothing to name before the first cue", ok, said)
made = cuestore.save(fresh, smoke, None, "")
first = read(fresh)
check('its first cue makes the file -- schema line, a comment, a name -- quoting "" '
      "for the rev",
      first["cues"] == [smoke] and "cues.schema.json" in first["$schema"] and first["_comment"]
      and first["name"] == "Cues" and made["index"] == 0
      and cuesmod.load(fresh / "cues.json").cues[0].name == "Smoke")
ok, said = raises(lambda: cuestore.save(fresh, {**smoke, "name": "Two"}, None, ""), "changed since")
check('"" stops meaning "no file yet" once there is one', ok, said)

# -- 5. the commands ---------------------------------------------------------------
print("\n5. the commands: reading the list, and saving a cue")
EVENT = event_copy("despacio")
FILE = EVENT / "cues.json"
sc = servermod.ShowController(EVENT)
sc.worker.start()
replies: list[dict] = []
sc.reply_to = lambda cid, payload: replies.append(payload)
designer = servermod.Client(id="d1", name="designer", tier="configure")
operator = servermod.Client(id="o1", name="phone", tier="operate")
_ids = iter(range(1, 10_000))


def settle(controller=None):
    controller = controller or sc
    for _ in range(4):
        assert controller.worker.wait_idle(5.0)
        controller._drain()


def ask(msg, client=designer, controller=None):
    controller = controller or sc
    replies.clear()
    controller.submit({**msg, "id": next(_ids)}, client)
    controller._drain()
    settle(controller)
    return replies[-1] if replies else None


def listed(name):
    return next((c for c in sc.cues_public()["cues"] if c["name"] == name), None)


def order():
    return [c.name for c in sc.cues.cues]


try:
    public = sc.cues_public()
    check("the list is every cue in running order, in full",
          [c["name"] for c in public["cues"]] == NIGHT and public["name"] == "Night"
          and public["event"] == "despacio" and public["file"] == "cues.json"
          and public["cues"][7]["fade"] == 0 and public["cues"][6]["speed"] == 2
          and public["groups"] == ["corner movers", "pinspots"], f"{public['cues'][7]}")
    check("with the rev a save must quote, which is the file's",
          public["rev"] == cuestore.file_rev(EVENT) and public["rev"].startswith("r:")
          and public["stale"] is False and public["problem"] is None)
    check("and the same rev rides in the snapshot, so a page knows when to read again",
          sc.snapshot()["cues_rev"] == public["rev"])
    resp = apimod.handle(None, "/api/cues", cues=sc.cues_public)
    body = json.loads(resp.body)
    check("GET /api/cues answers with no show folder: the cue list is the event's",
          resp.status == 200 and [c["name"] for c in body["cues"]] == NIGHT)
    check("it takes no id", apimod.handle(None, "/api/cues/deep", cues=sc.cues_public).status == 404)
    check("an engine with nothing to read says so",
          apimod.handle(None, "/api/cues").status == 503)
    resp = apimod.handle(None, "/api/cues", cues=lambda: 1 / 0)
    check("and whatever goes wrong describing the list is answered as JSON",
          resp.status == 500 and "ZeroDivisionError" in json.loads(resp.body)["error"])
    json.dumps(public, allow_nan=False)
    drop = listed("Drop")
    check("what is wrong with a cue is said beside it: the event's own Drop cue "
          "puts a level look in its movement and color slots",
          drop["missing"] == [] and len(drop["flags"]) == 2
          and all("'Drop', which is a level look" in f for f in drop["flags"]), f"{drop['flags']}")

    rev0 = public["rev"]
    haze = {"name": "Haze", "movement": {"corner movers": "Ball Wave"},
            "color": {"pinspots": "Pin Amber"}, "fade": 4, "hold": 0, "master": 0.4,
            "notes": "made in Studio"}
    r = ask({"type": "cue_save", "cue": haze, "base_rev": rev0}, client=operator)
    check("writing the cue list is configure-tier",
          r and r["ok"] is False and "needs configure" in r["error"], f"{r}")
    for kind in ("cue_save", "cue_copy", "cue_move", "cue_delete", "cues_name", "cues_reload"):
        check(f"{kind} is in the tier table as configure",
              sc.COMMANDS[kind].tier == "configure" and hasattr(sc, f"_cmd_{kind}"))
    check("taking, stepping back and rewinding the list stay operate-tier: running "
          "the night is not configuration",
          all(sc.COMMANDS[k].tier == "operate" for k in ("go", "cue_back", "cue", "cue_reset")))
    r = ask({"type": "cue_save", "cue": haze, "base_rev": "r:000000000000"})
    check("a save quoting a rev that is not the file's is refused, and nothing is written",
          r["ok"] is False and "changed since you opened it" in r["error"]
          and cuestore.file_rev(EVENT) == rev0 and listed("Haze") is None, f"{r}")
    r = ask({"type": "cue_save", "cue": haze})
    check("and so is one that quotes none", r["ok"] is False and "base_rev" in r["error"])
    r = ask({"type": "cue_save", "cue": {**haze, "fade": -4}, "base_rev": rev0})
    check("a cue that is not valid is refused with why, and nothing is written",
          r["ok"] is False and "fade must be at least 0" in r["error"]
          and cuestore.file_rev(EVENT) == rev0, f"{r}")
    r = ask({"type": "cue_save", "cue": {**haze, "colour": {}}, "base_rev": rev0})
    check("and so is a field a cue does not have", r["ok"] is False and "no field 'colour'" in r["error"])

    r = ask({"type": "cue_save", "cue": haze, "base_rev": rev0, "at": 1})
    rev1 = r["data"]["rev"] if r and r["ok"] else None
    check("a new cue is written at its place, and answered with the file's new rev",
          r["ok"] and r["data"]["name"] == "Haze" and r["data"]["index"] == 1
          and r["data"]["count"] == 10 and rev1 == cuestore.file_rev(EVENT) != rev0
          and r["data"]["warnings"] == [], f"{r}")
    check("the file has it, with its comments and its schema line kept",
          names(EVENT) == ["Warm Up", "Haze"] + NIGHT[1:] and read(EVENT)["cues"][1] == {
              k: v for k, v in haze.items() if k != "hold"}
          and read(EVENT)["_comment"] == doc["_comment"] and read(EVENT)["$schema"] == doc["$schema"])
    snap = sc.snapshot()
    check("it is in the running list at once, with no restart: the console's GO "
          "card lists it, and the snapshot's rev has moved",
          order() == ["Warm Up", "Haze"] + NIGHT[1:] and snap["cues"]["count"] == 10
          and snap["cues"]["cues"][1] == {"name": "Haze", "fade": 4.0, "hold": 0.0,
                                         "notes": "made in Studio"}
          and snap["cues_rev"] == rev1 and sc.cues_public()["stale"] is False, f"{snap['cues']['cues'][1]}")
    check("a list that had not started has still not started",
          sc.cues.index == -1 and snap["cues"]["current"] is None and snap["cues"]["next"] == "Warm Up")
    r = ask({"type": "cue_save", "cue": {**haze, "name": "Ghost",
                                         "movement": {"corner movers": "No Such Look"}},
             "base_rev": sc.cues_rev})
    check("a cue naming a look the library lacks is SAVED, and flagged: in the "
          "reply and on the list",
          r["ok"] and any("'No Such Look'" in w and "not in the library" in w for w in r["data"]["warnings"])
          and listed("Ghost")["missing"] == ["No Such Look"] and order()[-1] == "Ghost", f"{r}")
    r = ask({"type": "cue_delete", "cue": "Ghost", "base_rev": sc.cues_rev})
    check("and deleted again", r["ok"] and r["data"]["deleted"] == "Ghost" and "Ghost" not in order())

    # -- 6. keeping the place --------------------------------------------------------
    print("\n6. the list keeps its place through every edit, and the stage is not touched")
    for _ in range(4):
        sc.apply({"type": "go"}, None)
    sc._drain()
    check("(four GOs in: Warm Up, Haze, Idle, Deep)",
          sc.cues.current.name == "Deep" and sc.cues.index == 3)
    sc.cues.entered_beat = 123.0
    stage = json.dumps(sc.selection, sort_keys=True)
    master = sc.master
    running = sc.cues

    def unmoved():
        return json.dumps(sc.selection, sort_keys=True) == stage and sc.master == master

    r = ask({"type": "cue_save", "was": "Peak", "base_rev": sc.cues_rev,
             "cue": {**listed("Peak"), "fade": 2, "missing": None, "flags": None}})
    check("(a cue as /api/cues lists it is not a cue to save: what the page adds "
          "to it is refused, so the page must send the cue alone)",
          r["ok"] is False and "no field" in r["error"], f"{r}")

    def as_sent(which, **changes):
        cue = {k: v for k, v in listed(which).items() if k not in ("missing", "flags")}
        return {**cue, **changes}

    r = ask({"type": "cue_save", "was": "Peak", "base_rev": sc.cues_rev,
             "cue": as_sent("Peak", fade=2)})
    check("changing another cue leaves the list on the cue it was on, counting its "
          "hold from when it was taken",
          r["ok"] and sc.cues.current.name == "Deep" and sc.cues.index == 3
          and sc.cues.entered_beat == 123.0 and listed("Peak")["fade"] == 2, f"{r}")
    check("the running list is REPLACED, never edited in place: the snapshot and "
          "/api/cues read it from other threads",
          sc.cues is not running and [c.name for c in running.cues] == ["Warm Up", "Haze"] + NIGHT[1:]
          and next(c for c in running.cues if c.name == "Peak").fade == 8.0)
    check("and nothing on stage has changed", unmoved())
    r = ask({"type": "cue_save", "was": "Deep", "base_rev": sc.cues_rev,
             "cue": as_sent("Deep", color={"corner movers": "MH Red"}, master=0.2)})
    check("changing the CURRENT cue does not take it again: the rig keeps what it "
          "has until the cue is taken",
          r["ok"] and sc.cues.current.name == "Deep" and sc.cues.current.master == 0.2
          and unmoved() and sc.cues.entered_beat == 123.0, f"{sc.selection}")
    r = ask({"type": "cue_save", "base_rev": sc.cues_rev, "at": 0,
             "cue": {"name": "Doors", "fade": 16}})
    check("a cue added before it moves it down the order, still current",
          r["ok"] and sc.cues.index == 4 and sc.cues.current.name == "Deep"
          and sc.snapshot()["cues"]["index"] == 4 and sc.snapshot()["cues"]["current"] == "Deep")
    r = ask({"type": "cue_move", "cue": "Deep", "to": 1, "base_rev": sc.cues_rev})
    check("moved, it is current where it lands -- and GO takes what now follows it",
          r["ok"] and r["data"]["index"] == 1 and sc.cues.index == 1
          and sc.cues.current.name == "Deep" and sc.cues.next.name == "Warm Up"
          and order()[:3] == ["Doors", "Deep", "Warm Up"] and names(EVENT) == order(), f"{order()}")
    r = ask({"type": "cue_move", "cue": "Landing", "to": 0, "base_rev": sc.cues_rev})
    check("another cue moved across it shifts its place, not its hold on it",
          r["ok"] and sc.cues.index == 2 and sc.cues.current.name == "Deep" and order()[0] == "Landing")
    r = ask({"type": "cue_move", "cue": "Landing", "to": len(order()) - 1, "base_rev": sc.cues_rev})
    check("(and back to the end)", r["ok"] and order()[-1] == "Landing" and sc.cues.index == 1)
    r = ask({"type": "cue_move", "cue": "Deep", "to": 99, "base_rev": sc.cues_rev})
    check("a move to a place that is not there is refused",
          r["ok"] is False and "place in the running order" in r["error"])
    r = ask({"type": "cue_move", "cue": "Deep", "to": "first", "base_rev": sc.cues_rev})
    check("and so is one that names no place",
          r["ok"] is False and "cue_move: to must be a whole number" in r["error"], f"{r}")
    r = ask({"type": "cue_save", "was": "Deep", "base_rev": sc.cues_rev,
             "cue": as_sent("Deep", name="Deeper")})
    check("renamed, it is current under its new name",
          r["ok"] and r["data"]["name"] == "Deeper" and sc.cues.current.name == "Deeper"
          and sc.cues.index == 1 and sc.cues.entered_beat == 123.0
          and sc.snapshot()["cues"]["current"] == "Deeper" and "Deep" not in names(EVENT)
          and any("renamed cue 'Deep' to 'Deeper'" in n for n in sc.notices), f"{r}")
    r = ask({"type": "cue_save", "was": "Deeper", "base_rev": sc.cues_rev,
             "cue": as_sent("Deeper", name="Idle")})
    check("not to a name another cue has",
          r["ok"] is False and "already a cue called 'Idle'" in r["error"])
    r = ask({"type": "cue_copy", "cue": "Deeper", "to": "Deeper still", "base_rev": sc.cues_rev})
    check("duplicated, the copy is the next cue and the original is still current",
          r["ok"] and r["data"]["name"] == "Deeper still" and r["data"]["index"] == 2
          and sc.cues.current.name == "Deeper" and sc.cues.next.name == "Deeper still"
          and listed("Deeper still")["master"] == 0.2, f"{r}")
    r = ask({"type": "cue_copy", "cue": "Deeper", "to": "Idle", "base_rev": sc.cues_rev})
    check("a copy cannot take a name in use", r["ok"] is False and "already a cue called" in r["error"])
    check("through all of that nothing on stage changed", unmoved())

    sc.ctx.beat = 500.0
    r = ask({"type": "cue_delete", "cue": "Deeper", "base_rev": sc.cues_rev})
    check("deleting the CURRENT cue falls back to the cue before it, so GO takes "
          "what would have come next",
          r["ok"] and sc.cues.current.name == "Doors" and sc.cues.index == 0
          and sc.cues.next.name == "Deeper still", f"{order()[:3]} {sc.cues.index}")
    check("a notice says which cue went and where the list is now",
          any("'Deeper' was the current one" in n and "at 'Doors'" in n
              and "GO takes 'Deeper still'" in n for n in sc.notices), f"{sc.notices[-2:]}")
    check("a hold on the cue it fell back to counts from now, not from a GO that "
          "was for another cue",
          sc.cues.entered_beat == 500.0)
    check("and the stage is still as it was", unmoved())
    r = ask({"type": "cue_delete", "cue": "Doors", "base_rev": sc.cues_rev})
    check("with no cue before it left, the list is back before its first cue: "
          "nothing is claimed to be current",
          r["ok"] and sc.cues.index == -1 and sc.snapshot()["cues"]["current"] is None
          and sc.snapshot()["cues"]["next"] == "Deeper still" and unmoved(), f"{sc.cues.index}")
    r = ask({"type": "cue_delete", "cue": "Deeper still", "base_rev": sc.cues_rev})
    r = ask({"type": "cue_delete", "cue": "Haze", "base_rev": sc.cues_rev})
    r = ask({"type": "cue_save", "base_rev": sc.cues_rev, "at": 2,
             "cue": as_sent("Idle", name="Deep", movement={"corner movers": "Slow Sweep"})})
    check("(the list is the night again)", order() == NIGHT, f"{order()}")

    sc.apply({"type": "go"}, None)
    sc.apply({"type": "go"}, None)
    check("GO takes the cue as it was last saved",
          sc.cues.current.name == "Idle" and sc.selection["movement"] == {"corner movers": "Ball Wave"})
    sc.cues.entered_beat = 100.0
    sc.ctx.beat = 110.0
    r = ask({"type": "cue_save", "was": "Idle", "base_rev": sc.cues_rev,
             "cue": as_sent("Idle", hold=32)})
    check("a hold given to the current cue counts from when it was taken",
          r["ok"] and sc.cues.current.hold == 32.0 and not sc.cues.due(120.0) and sc.cues.due(132.0)
          and sc.snapshot()["cues"]["auto_advance"] is True)
    r = ask({"type": "cue_reset"}, client=operator)
    check("the list is rewound by the command the console never had a button for",
          sc.cues.index == -1 and sc.snapshot()["cues"]["current"] is None
          and any("rewound" in n for n in sc.notices))
    r = ask({"type": "cues_name", "name": "Saturday", "base_rev": sc.cues_rev})
    check("the list is renamed: the GO card's title, in the file and on the console",
          r["ok"] and read(EVENT)["name"] == "Saturday" and sc.snapshot()["cues"]["name"] == "Saturday"
          and sc.cues_public()["name"] == "Saturday", f"{r}")
    r = ask({"type": "cues_name", "name": "", "base_rev": sc.cues_rev})
    check("not to nothing", r["ok"] is False and "needs a name" in r["error"])

    # -- 7. a look renamed under the cues ---------------------------------------------
    print("\n7. a look renamed on the Looks page, and the cue list that names it")
    sc.apply({"type": "go"}, None)
    cathedral = next(l for l in json.loads((EVENT / "parametric_looks.json").read_text(encoding="utf-8"))
                     ["looks"] if l["name"] == "Heads - Cathedral")
    r = ask({"type": "look_save", "was": "Heads - Cathedral", "base_rev": sc.looks_rev,
             "look": {**cathedral, "name": "Cathedral Columns"}})
    check("(the rename moved the cue that named the look)", r["ok"] and r["data"]["cues"] == 1, f"{r}")
    check("the cue plays the renamed look, in the file and in the running list",
          listed("Cathedral")["movement"] == {"corner movers": "Cathedral Columns"}
          and read(EVENT)["cues"][4]["movement"] == {"corner movers": "Cathedral Columns"}
          and listed("Cathedral")["missing"] == [])
    check("the two editors agree about the file: the rev the Cues page must quote "
          "is the file's at once, not after the watcher catches up",
          sc.cues_rev == cuestore.file_rev(EVENT) and sc.cues_public()["stale"] is False
          and sc.snapshot()["cues_rev"] == sc.cues_rev)
    check("and the list kept its place through it", sc.cues.index == 0 and sc.cues.current.name == "Warm Up")
    check("the watcher has nothing to read again",
          sc.cue_watcher.poll() is False and sc.cue_watcher.poll() is False)
    renamed_text = FILE.read_text(encoding="utf-8")
    check("the file is still in its layout", cuestore.gaps_of(renamed_text) == {"name", "cues"}
          and cuestore.render(read(EVENT), {"name", "cues"}) == renamed_text.replace("\r\n", "\n"))
    r = ask({"type": "cue_save", "was": "Cathedral", "base_rev": sc.cues_rev,
             "cue": as_sent("Cathedral", fade=20)})
    check("so a cue saved straight after the rename is accepted",
          r["ok"] and read(EVENT)["cues"][4]["fade"] == 20
          and read(EVENT)["cues"][4]["movement"] == {"corner movers": "Cathedral Columns"}, f"{r}")

    # -- 8. the watcher ------------------------------------------------------------
    print("\n8. the engine watches cues.json itself")
    check("a list just read is not read again", sc.cue_watcher.poll() is False
          and sc.cue_watcher.poll() is False)
    by_hand = read(EVENT)
    by_hand["cues"].insert(0, {"name": "By Hand", "fade": 2,
                               "color": {"pinspots": "Pin Amber"}})
    FILE.write_text(cuestore.render(by_hand, {"name", "cues"}), encoding="utf-8", newline="\n")
    check("an edit made outside the engine is seen, and waited on for one poll in "
          "case it is still being written",
          sc.cue_watcher.poll() is False and "By Hand" not in order())
    check("(until it is read, the page is told the list is out of date)",
          sc.cues_public()["stale"] is True)
    fired = sc.cue_watcher.poll()
    settle()
    check("then read, on the worker, and installed with no command and no restart "
          "-- the list still on the cue it was on",
          fired and order()[0] == "By Hand" and sc.cues.current.name == "Warm Up"
          and sc.cues.index == 1 and sc.cues_public()["stale"] is False
          and sc.snapshot()["cues_rev"] == cuestore.file_rev(EVENT)
          and any("cues read again" in n for n in sc.notices), f"{sc.notices[-2:]}")
    good_text = FILE.read_text(encoding="utf-8")
    FILE.write_text(good_text.replace('"fade": 2,', '"fade": "two",'), encoding="utf-8", newline="\n")
    sc.cue_watcher.poll()
    sc.cue_watcher.poll()
    settle()
    public = sc.cues_public()
    check("a file that does not load is said once, with why, and the list that was "
          "running runs on",
          order()[0] == "By Hand" and sc.cues.cues[0].fade == 2.0 and public["stale"] is True
          and "fade must be a number" in (public["problem"] or "")
          and any("does not load" in n and "fade" in n for n in sc.notices), f"{sc.notices[-1:]}")
    r = ask({"type": "cue_save", "base_rev": sc.cues_rev, "cue": {"name": "Over It", "fade": 1}})
    check("and nothing is saved over it: the mistake would be buried",
          r["ok"] is False and "By Hand" in FILE.read_text(encoding="utf-8")
          and '"two"' in FILE.read_text(encoding="utf-8"), f"{r}")
    before_notes = len([n for n in sc.notices if "does not load" in n])
    sc.cue_watcher.poll()
    sc.cue_watcher.poll()
    settle()
    check("it is not read over and over while it stays broken",
          len([n for n in sc.notices if "does not load" in n]) == before_notes)
    FILE.write_text(good_text, encoding="utf-8", newline="\n")
    sc.cue_watcher.poll()
    sc.cue_watcher.poll()
    settle()
    check("mended, it is read, and the problem goes",
          sc.cues_public()["stale"] is False and sc.cues_public()["problem"] is None)
    r = ask({"type": "cue_delete", "cue": "By Hand", "base_rev": sc.cues_rev})
    check("Studio's own save is not read a second time by the watcher",
          r["ok"] and sc.cue_watcher.poll() is False and sc.cue_watcher.poll() is False)
    FILE.write_text(good_text, encoding="utf-8", newline="\n")
    r = ask({"type": "cues_reload"})
    check("a reload can be asked for, rather than waited for",
          r["ok"] and order()[0] == "By Hand" and r["data"]["rev"] == cuestore.file_rev(EVENT))

    real_install = sc._install_cues

    def cannot(*args, **kwargs):
        raise RuntimeError("no room")

    sc._install_cues = cannot
    try:
        r = ask({"type": "cue_delete", "cue": "By Hand", "base_rev": sc.cues_rev})
    finally:
        sc._install_cues = real_install
    check("if the install itself fails, the client is told so rather than left to "
          "time out",
          r is not None and r["ok"] is False
          and "was written, but the engine could not take it up" in r["error"], f"{r}")
    ask({"type": "cues_reload"})
    check("(the file it wrote is read on the next look)", "By Hand" not in order())

    kept = FILE.read_text(encoding="utf-8")
    FILE.unlink()
    sc.cue_watcher.poll()
    sc.cue_watcher.poll()
    settle()
    snap = sc.snapshot()
    check("the file taken away is an event with no cue list: the console's card "
          "goes, and nothing claims a rev",
          sc.cues is None and snap["cues"] is None and snap["cues_rev"] == ""
          and sc.cues_public()["cues"] == [] and sc.cues_public()["rev"] is None
          and sc.cues_public()["stale"] is False, f"{sc.notices[-1:]}")
    FILE.write_text(kept, encoding="utf-8", newline="\n")
    sc.cue_watcher.poll()
    sc.cue_watcher.poll()
    settle()
    check("and put back, it is the list again, not started",
          sc.cues is not None and order() == NIGHT and sc.cues.index == -1)

    # -- 9. an event with no cue list -----------------------------------------------
    print("\n9. an event with no cue list at all")
    bare = TMP / "bare"
    bare.mkdir()
    for name in ("rig.json", "calibration.json", "looks.json", "parametric_looks.json"):
        shutil.copy(EVENT / name, bare / name)
    bc = servermod.ShowController(bare)
    bc.worker.start()
    bc.reply_to = lambda cid, payload: replies.append(payload)
    try:
        public = bc.cues_public()
        check("with no file the list is empty, there is no rev, and nothing is out of date",
              public["cues"] == [] and public["rev"] is None and public["stale"] is False
              and public["name"] is None and bc.snapshot()["cues"] is None
              and bc.snapshot()["cues_rev"] == "")
        r = ask({"type": "cue_save", "base_rev": "",
                 "cue": {"name": "First", "movement": {"corner movers": "Ball Wave"}, "fade": 4}},
                controller=bc)
        made = json.loads((bare / "cues.json").read_text(encoding="utf-8"))
        check("its first cue makes the file -- schema line, a comment, a name -- and "
              "the console has a GO card",
              r["ok"] and made["cues"] == [{"name": "First", "movement": {"corner movers": "Ball Wave"},
                                            "fade": 4}]
              and "$schema" in made and made["_comment"] and made["name"] == "Cues"
              and bc.snapshot()["cues"]["next"] == "First"
              and bc.snapshot()["cues_rev"] == cuestore.file_rev(bare), f"{r}")
        bc.apply({"type": "go"}, None)
        check("and GO takes it", bc.cues.current.name == "First"
              and bc.selection["movement"] == {"corner movers": "Ball Wave"})
        r = ask({"type": "cue_save", "base_rev": "", "cue": {"name": "Second", "fade": 4}},
                controller=bc)
        check('"" stops meaning "no file yet" once there is one',
              r["ok"] is False and "changed since" in r["error"], f"{r}")
        r = ask({"type": "cue_delete", "cue": "First", "base_rev": bc.cues_rev}, controller=bc)
        check("the only cue cannot be deleted",
              r["ok"] is False and "the only cue" in r["error"] and bc.cues.current.name == "First")
    finally:
        bc.worker.stop()

    broken = TMP / "broken"
    broken.mkdir()
    for name in ("rig.json", "calibration.json"):
        shutil.copy(EVENT / name, broken / name)
    (broken / "cues.json").write_text('{"cues": []}', encoding="utf-8")
    kc = servermod.ShowController(broken)
    check("an event whose cues.json does not load still starts, says why, and "
          "tells the Cues page",
          kc.cues is None and any("cues.json not loaded" in n for n in kc.notices)
          and kc.cues_public()["stale"] is True and "must not be empty" in kc.cues_public()["problem"])

    # -- 10. the tools behind the MCP server --------------------------------------------
    print("\n10. the cue tools: dry runs unless asked, and the rev they read")
    ev = str(EVENT)
    listing = cuetools.list_cues(ev)
    check("list_cues is the night in order, a line each, with the rev to quote",
          listing["ok"] and [c["name"] for c in listing["cues"]] == NIGHT
          and [c["index"] for c in listing["cues"]] == list(range(9))
          and listing["rev"] == cuestore.file_rev(EVENT) and listing["name"] == "Saturday"
          and listing["cues"][0]["movement"] == {"corner movers": "Heads - Ball"}
          and "level" not in listing["cues"][0] and "speed" not in listing["cues"][0], f"{listing['cues'][0]}")
    check("and what is wrong with a cue, on its line",
          any("level look" in f for f in listing["cues"][7].get("flags", ()))
          and "flags" not in listing["cues"][0])
    got = cuetools.get_cue("Build", ev)
    check("get_cue is one cue in full, and where it is",
          got["ok"] and got["index"] == 6 and got["count"] == 9 and got["cue"]["speed"] == 2
          and got["cue"]["macro"] == {"size": 1.6, "spread": 0.75} and got["cue"]["missing"] == [])
    check("a cue that is not there is an error that says so",
          cuetools.get_cue("Nope", ev)["ok"] is False
          and "no cue called 'Nope'" in cuetools.get_cue("Nope", ev)["errors"][0])
    rev = listing["rev"]
    cue = {"name": "Encore", "movement": {"corner movers": "No Such Look"}, "fade": 8}
    tried = cuetools.put_cue(cue, event=ev)
    check("put_cue is a dry run unless asked: it says what would happen, with the "
          "missing look as a warning, and writes nothing",
          tried["ok"] and tried["dry_run"] and tried["written"] is False
          and tried["index"] == 9 and "write=true" in tried["hint"]
          and any("'No Such Look'" in w for w in tried["warnings"])
          and cuestore.file_rev(EVENT) == rev, f"{tried}")
    check("writing needs the rev that was read",
          cuetools.put_cue(cue, write=True, event=ev)["ok"] is False
          and "base_rev is required" in cuetools.put_cue(cue, write=True, event=ev)["errors"][0])
    stale = cuetools.put_cue(cue, write=True, base_rev="r:000000000000", event=ev)
    check("and a stale one is refused", stale["ok"] is False and "changed since" in stale["errors"][0])
    bad = cuetools.put_cue({"name": "Encore", "fade": -1}, event=ev)
    check("a cue that is not valid is refused before the file is looked at",
          bad["ok"] is False and "fade must be at least 0" in bad["errors"][0])
    wrote = cuetools.put_cue(cue, write=True, base_rev=rev, at=0, event=ev)
    check("with write and the rev, it is saved where asked",
          wrote["ok"] and wrote["written"] and wrote["index"] == 0 and names(EVENT)[0] == "Encore"
          and wrote["rev"] == cuestore.file_rev(EVENT), f"{wrote}")
    changed = cuetools.put_cue({**cue, "name": "Encore 2"}, was="Encore", at=9, write=True,
                               base_rev=wrote["rev"], event=ev)
    check("`was` changes a cue -- renaming and moving it in the one write",
          changed["ok"] and changed["renamed"] == {"from": "Encore", "to": "Encore 2"}
          and names(EVENT)[-1] == "Encore 2" and changed["index"] == 9, f"{changed}")
    sc.cue_watcher.poll()
    sc.cue_watcher.poll()
    settle()
    check("a running engine reads what the tools wrote by itself", order()[-1] == "Encore 2")
    tried = cuetools.delete_cue("Encore 2", event=ev)
    check("delete_cue is a dry run unless asked",
          tried["ok"] and tried["written"] is False and names(EVENT)[-1] == "Encore 2")
    gone = cuetools.delete_cue("Encore 2", write=True, base_rev=changed["rev"], event=ev)
    check("and deletes with write and the rev", gone["ok"] and gone["written"] and names(EVENT) == NIGHT)
    check("an event that is not there is an error, not a traceback",
          cuetools.list_cues(str(TMP / "nowhere"))["ok"] is False)
    (TMP / "empty-event").mkdir()
    none = cuetools.list_cues(str(TMP / "empty-event"))
    check('an event with no cue list lists none, and says "" is the rev that makes one',
          none["ok"] and none["cues"] == [] and none["rev"] == "" and 'base_rev=""' in none["hint"])
finally:
    sc.worker.stop()

print()
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("all cue list checks passed")
