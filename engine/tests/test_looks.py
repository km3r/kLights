"""
Tests for the look library as Studio edits it: `engine/lookstore.py`, the
`look_*` commands, the live reload, and `GET /api/looks`.

Every controller here runs against a COPY of the event and of the example show
folder: these commands write parametric_looks.json, cues.json and presets.json,
and a suite that crashed between a save and its cleanup must not leave junk in
tonight's show.

The command half drives the controller's queue directly with a client whose
replies are captured (as test_api.py does), so every answer -- including the
ones that arrive after the worker finishes -- is checked exactly.

Run: python engine/tests/test_looks.py
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
from engine import library as libmod  # noqa: E402
from engine import lookstore  # noqa: E402
from engine import patch as patchmod  # noqa: E402
from engine import rig as rigmod  # noqa: E402
from engine import server as servermod  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label + (f"  -- {detail}" if detail else ""))


TMP = Path(tempfile.mkdtemp(prefix="klights-looks-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
_SKIP = shutil.ignore_patterns("__pycache__", "*.bak", ".engine.lock", "backups")
EVENT = TMP / "despacio"
shutil.copytree(REPO / "events" / "despacio", EVENT, ignore=_SKIP)
shutil.copytree(rigmod.VENUE_LIBRARY, TMP / "venues", ignore=_SKIP)
rigmod.VENUE_LIBRARY = patchmod.VENUES = TMP / "venues"
SHOWS = TMP / "shows"
shutil.copytree(REPO / "shared" / "show-example", SHOWS)
FILE = EVENT / "parametric_looks.json"


def raises(fn, *needles):
    """The ValueError `fn` raises, if its message holds every needle."""
    try:
        fn()
    except ValueError as exc:
        return all(n in str(exc) for n in needles), str(exc)
    return False, "nothing raised"


# -- 1. the file ----------------------------------------------------------------
print("\n1. parametric_looks.json, written as it was written by hand")
original = (REPO / "events" / "despacio" / "parametric_looks.json").read_text(encoding="utf-8")
cfg = json.loads(original)
text = lookstore.render(cfg)
check("a document renders to JSON that reads back as itself", json.loads(text) == cfg)
before, after = original.replace("\r\n", "\n").splitlines(), text.splitlines()
check("and in the layout the file has by hand, byte for byte -- its one-line "
      "arguments, its blank lines between one picker heading and the next -- so "
      "a save that changes nothing changes nothing, and a diff shows only what "
      "was edited",
      before == after and text.endswith("}\n"),
      f"{next(((a, b) for a, b in zip(before, after) if a != b), len(before) - len(after))}")
grouped = lookstore.render({"looks": [
    {"name": "a", "block": "orbit"}, {"name": "b", "block": "pendulum"},
    {"name": "c", "block": "solid", "args": {"color": "@primary"}},
    {"name": "d", "block": "wobble"}], "retired": [{"name": "x"}]})
check("a new look is set apart from the heading before it, and a block this "
      "engine does not know is no reason to fail",
      grouped.count("\n\n") == 3 and json.loads(grouped)["looks"][3]["block"] == "wobble",
      grouped)
long = {"looks": [{"name": "x", "block": "aim_points",
                   "args": {"points": [[0.1, 0.2, 0.3]] * 12}}]}
check("an argument too long for a line is spread over several, still JSON",
      json.loads(lookstore.render(long)) == long
      and max(len(line) for line in lookstore.render(long).splitlines()) <= 110)

# -- 2. a look from a client -----------------------------------------------------
print("\n2. a look as a client sends it")
rig = rigmod.load_rig(EVENT)
good = lookstore.clean_look({"name": "  My   Orbit ", "block": "orbit",
                             "groups": ["corner movers", "corner movers"],
                             "args": {"radius": 30, "spread": None},
                             "notes": "  wide  "})
check("is stored tidied: the name's spaces, a group named twice, an argument "
      "left to its default, and in the file's key order",
      good == {"name": "My Orbit", "block": "orbit", "groups": ["corner movers"],
               "args": {"radius": 30}, "notes": "wide"}
      and list(good) == ["name", "block", "groups", "args", "notes"], f"{good}")
check("a number is NOT clamped to the block's declared range: the range "
      "describes the controls, and what an author wrote is what plays",
      lookstore.clean_look({"name": "Big", "block": "orbit",
                            "args": {"radius": 120}})["args"] == {"radius": 120})
check("an integer argument is stored as one",
      lookstore.clean_look({"name": "S", "block": "scatter",
                            "args": {"stations": 6.0}})["args"] == {"stations": 6})
for label, look, needles in (
    ("no name", {"block": "orbit"}, ("needs a name",)),
    ("a name of spaces", {"name": "   ", "block": "orbit"}, ("needs a name",)),
    ("a name too long", {"name": "x" * 81, "block": "orbit"}, ("at most 80",)),
    ("no block", {"name": "A"}, ("needs a block", "orbit")),
    ("a block that does not exist", {"name": "A", "block": "wobble"}, ("needs a block",)),
    ("a block that plays a look rather than being one",
     {"name": "A", "block": "look", "args": {"look": "MH Red"}}, ("belongs in a routine",)),
    ("an argument the block does not have",
     {"name": "A", "block": "orbit", "args": {"radius_deg": 3}}, ("no argument 'radius_deg'", "radius")),
    ("text where a number belongs",
     {"name": "A", "block": "orbit", "args": {"radius": "20"}}, ("radius must be a number",)),
    ("true where a number belongs",
     {"name": "A", "block": "orbit", "args": {"radius": True}}, ("radius must be a number",)),
    ("NaN", {"name": "A", "block": "orbit", "args": {"radius": float("nan")}},
     ("radius must be a number",)),
    ("NaN inside a color",
     {"name": "A", "block": "solid", "args": {"color": [float("nan"), 0, 0]}}, ("finite",)),
    ("a cycle of no bars", {"name": "A", "block": "orbit", "args": {"bars": 0}},
     ("bars must be more than 0",)),
    ("a choice that is not one",
     {"name": "A", "block": "spiral", "args": {"direction": "sideways"}}, ("direction must be one of",)),
    ("a color that is not one",
     {"name": "A", "block": "solid", "args": {"color": "nonsense"}}, ("color:",)),
    ("another look's name as a color: a block look is portable, and a look "
     "name means something on one rig only",
     {"name": "A", "block": "solid", "args": {"color": "MH Red"}}, ("color:",)),
    ("a solid with no color", {"name": "A", "block": "solid"}, ("solid needs a color",)),
    ("groups that are not a list of tags",
     {"name": "A", "block": "orbit", "groups": "movers"}, ("groups is a list",)),
    ("something that is not an object", ["orbit"], ("a look is an object",)),
):
    ok, said = raises(lambda look=look: lookstore.clean_look(look), *needles)
    check(f"refused: {label}", ok, said)
check("a group nothing on the rig carries is allowed, and named",
      lookstore.unknown_groups({"groups": ["corner movers", "lasers"]}, rig) == ["lasers"])

# -- 3. what a stored look is ----------------------------------------------------
print("\n3. describing the stored looks")
ported = {e.name: e for e in libmod.load_entries(EVENT / "looks.json")}
check("one color is offered as a solid block of that color",
      lookstore.block_version(ported["MH Red"])
      == {"block": "solid", "args": {"color": [1.0, 0.0, 0.0]}})
uniform = next(e for e in ported.values() if e.kind == "pose" and e.intensity is None
               and lookstore._one_offset(e) is not None)
version = lookstore.block_version(uniform)
check("a pose with every head at one offset is offered as an offset block",
      version is not None and version["block"] == "offset"
      and abs(version["args"]["bearing"] - uniform.offsets[0][0]) < 0.01
      and abs(version["args"]["elevation"] - uniform.offsets[0][1]) < 0.01, f"{version}")
level = next(e for e in ported.values() if e.kind == "intensity"
             and not e.intensities and not e.strobes and e.intensity is not None)
check("one level is offered as a dim block",
      lookstore.block_version(level) == {"block": "dim",
                                         "args": {"level": round(level.intensity, 4)}})
tables = [e for e in ported.values()
          if e.steps is not None or e.frames is not None or e.levels is not None
          or e.colors is not None]
check("a route, a chase or a color per fixture is a table: no block says the "
      "same thing, so none is offered",
      tables and all(lookstore.block_version(e) is None for e in tables))
check("and a pose that also dims is not an offset: the block would lose the level",
      all(lookstore.block_version(e) is None for e in ported.values()
          if e.kind == "pose" and e.intensity is not None))
lazy = lookstore.stored_summary(ported["Lazy Circle"])
check("a stored route says what it is in a line",
      lazy["what"] == "a route through stored positions" and lazy["steps"] == 8
      and lazy["bars"] == 16, f"{lazy}")
check("a stored color carries its swatch",
      lookstore.stored_summary(ported["MH Red"]).get("swatches") == [[1.0, 0.0, 0.0]])
check("the porter's notes on a stored look are read, as one string",
      isinstance(ported["Lazy Circle"].notes, str) and "8 steps" in ported["Lazy Circle"].notes)

# -- 4. the commands --------------------------------------------------------------
print("\n4. reading the library, and saving a new look")
sc = servermod.ShowController(EVENT, show_dir=SHOWS)
sc.worker.start()
replies: list[dict] = []
sc.reply_to = lambda cid, payload: replies.append(payload)
designer = servermod.Client(id="d1", name="designer", tier="configure")
operator = servermod.Client(id="o1", name="phone", tier="operate")
_ids = iter(range(1, 10_000))


def ask(msg, client=designer):
    replies.clear()
    sc.submit({**msg, "id": next(_ids)}, client)
    sc._drain()
    for _ in range(4):
        assert sc.worker.wait_idle(5.0)
        sc._drain()
    return replies[-1] if replies else None


def listed(name):
    return next((l for l in sc.looks_public()["looks"] if l["name"] == name), None)


def in_snapshot(name):
    return next((l for l in sc.snapshot()["looks"] if l["name"] == name), None)


def on_disk():
    return json.loads(FILE.read_text(encoding="utf-8"))


try:
    public = sc.looks_public()
    check("the library lists every look, stored and block",
          len(public["looks"]) == len(sc.library) == 226
          and {l["source"] for l in public["looks"]} == {"stored", "block"},
          f"{len(public['looks'])}")
    check("with the rev a save must quote, which is the file's",
          public["rev"] == lookstore.file_rev(EVENT) and public["rev"].startswith("r:")
          and public["stale"] is False and public["file"] == "parametric_looks.json")
    check("and the same rev rides in the snapshot, so a page knows when to read again",
          sc.snapshot()["looks_rev"] == public["rev"])
    ball = listed("Heads - Ball")
    check("a block look says its block, its arguments and that it took over a stored look",
          ball["source"] == "block" and ball["block"] == "offset"
          and ball["args"] == {"bearing": 0.0, "elevation": 0.0}
          and ball["supersedes"] is True and ball["slot"] == "movement", f"{ball}")
    check("and where it is used: the cues that name it",
          ball["used_by"]["cues"] == ["Warm Up", "Landing"], f"{ball['used_by']}")
    wave = listed("Ball Wave")
    check("a look names every cue and preset that puts it in a slot",
          wave["used_by"]["cues"] == ["Idle"] and wave["used_by"]["presets"] == ["Phase a"],
          f"{wave['used_by']}")
    lazy = listed("Lazy Circle")
    check("a stored look: what it is, that it is hidden, what replaced it and why",
          lazy["source"] == "stored" and lazy["retired"] is True
          and lazy["replaced_by"] == "Lazy Orbit" and "1.6 degrees" in lazy["retired_note"]
          and "8 steps" in lazy["notes"] and "block" not in lazy, f"{lazy}")
    check("and the show folder's timeline that places it",
          lazy["used_by"]["timelines"] == [{"track": "synth-128", "title": "synthetic 128"}],
          f"{lazy['used_by']}")
    check("the look it was hidden for says so",
          listed("Lazy Orbit")["used_by"]["hides"] == ["Lazy Circle"])
    resp = apimod.handle(None, "/api/looks", looks=sc.looks_public)
    body = json.loads(resp.body)
    check("GET /api/looks answers with no show folder: looks are the event's",
          resp.status == 200 and len(body["looks"]) == 226 and body["event"] == "despacio")
    check("and takes no id", apimod.handle(None, "/api/looks/orbit",
                                           looks=sc.looks_public).status == 404)
    json.dumps(sc.looks_public(), allow_nan=False)

    rev0 = public["rev"]
    mine = {"name": "Slow Ring", "block": "orbit", "groups": ["corner movers"],
            "args": {"radius": 30, "bars": 16}, "notes": "made in Studio"}
    r = ask({"type": "look_save", "look": mine, "base_rev": rev0}, client=operator)
    check("writing the library is configure-tier",
          r and r["ok"] is False and "needs configure" in r["error"], f"{r}")
    r = ask({"type": "look_save", "look": mine, "base_rev": "r:000000000000"})
    check("a save quoting a rev that is not the file's is refused, and nothing is written",
          r["ok"] is False and "changed since you opened it" in r["error"]
          and lookstore.file_rev(EVENT) == rev0 and listed("Slow Ring") is None, f"{r}")
    r = ask({"type": "look_save", "look": mine})
    check("and so is one that quotes none", r["ok"] is False and "base_rev" in r["error"])

    r = ask({"type": "look_save", "look": mine, "base_rev": rev0})
    rev1 = r["data"]["rev"] if r and r["ok"] else None
    check("a new look is written, and answered with the file's new rev",
          r["ok"] and r["data"]["name"] == "Slow Ring" and rev1 == lookstore.file_rev(EVENT)
          and rev1 != rev0 and r["data"]["warnings"] == [], f"{r}")
    doc = on_disk()
    check("the file keeps everything that was in it: its comments, its schema "
          "line, every other look and the hidden list",
          doc["_comment"] == cfg["_comment"] and doc["$schema"] == cfg["$schema"]
          and doc["looks"][:-1] == cfg["looks"] and doc["retired"] == cfg["retired"]
          and doc["looks"][-1] == mine)
    check("and its one .bak is the file as it was", (EVENT / "parametric_looks.json.bak").exists()
          and json.loads((EVENT / "parametric_looks.json.bak").read_text(encoding="utf-8")) == cfg)
    snap = in_snapshot("Slow Ring")
    check("it is in the running library at once, with no restart: the console lists it",
          snap is not None and snap["block"] == "orbit" and snap["slot"] == "movement"
          and snap["args"] == {"radius": 30, "bars": 16}
          and sc.snapshot()["looks_rev"] == rev1, f"{snap}")
    sc.apply({"type": "select_look", "name": "Slow Ring"}, None)
    check("and it plays", sc.selection["movement"] == {"corner movers": "Slow Ring"})
    for stored_name in ("MH Red", level.name, uniform.name):
        stored = ported[stored_name]        # as looks.json has it
        made = {"name": f"{stored_name} as a block", "groups": list(stored.groups),
                **lookstore.block_version(stored)}
        r = ask({"type": "look_save", "look": made, "base_rev": sc.looks_rev})
        check(f"a stored look remade as its block ({made['block']}) files under the "
              f"same heading and in the same slot as the original",
              r["ok"] and sc.by_name[made["name"]].kind == stored.kind
              and sc.by_name[made["name"]].slot == stored.slot,
              f"{r} {sc.by_name.get(made['name'])}")
        r = ask({"type": "look_delete", "look": made["name"], "base_rev": sc.looks_rev})
        check("and is deleted again", r["ok"], f"{r}")
    rev1 = sc.looks_rev
    r = ask({"type": "look_save", "look": mine, "base_rev": rev1})
    check("a second new look of the same name is refused",
          r["ok"] is False and "already a look called 'Slow Ring'" in r["error"], f"{r}")
    r = ask({"type": "look_save", "look": {**mine, "name": "MH Red"}, "base_rev": rev1})
    check("and so is one with a stored look's name: a look is addressed by name",
          r["ok"] is False and "already a stored look called 'MH Red'" in r["error"], f"{r}")
    r = ask({"type": "look_save", "look": {**mine, "name": "Lasers", "groups": ["lasers"]},
             "base_rev": rev1})
    check("a group nothing carries is saved, with a warning that says so",
          r["ok"] and any("'lasers'" in w for w in r["data"]["warnings"]), f"{r}")

    # -- 5. changing one ----------------------------------------------------------
    print("\n5. changing a look")
    rev = sc.looks_rev
    sc.apply({"type": "look_params", "name": "Slow Ring", "values": {"radius": 12}}, None)
    sc.apply({"type": "modulate", "look": "Slow Ring", "param": "radius", "shape": "sine",
              "low": 5, "high": 20, "bars": 8}, None)
    sc.apply({"type": "modulate", "look": "Ball Orbit", "param": "radius", "shape": "sine",
              "low": 5, "high": 20, "bars": 8}, None)
    check("(a radius dialled in on the console, over the authored 30)",
          sc.entry("Slow Ring").args["radius"] == 12 and len(sc.modulators) == 2)
    r = ask({"type": "look_save", "was": "Slow Ring", "base_rev": rev,
             "look": {**mine, "args": {"radius": 44, "bars": 8}}})
    check("saving new arguments changes the running look",
          r["ok"] and sc.by_name["Slow Ring"].args == {"radius": 44, "bars": 8}, f"{r}")
    check("and drops what was dialled in over the old ones: the saved values play",
          "Slow Ring" not in sc.look_params and sc.entry("Slow Ring").args["radius"] == 44)
    check("its modulator runs on -- the parameter is still there -- and so does another look's",
          {m["look"] for m in sc.snapshot()["modulators"]} == {"Slow Ring", "Ball Orbit"})
    check("it is still the look on stage", sc.setlist.current().name == "Slow Ring")
    r = ask({"type": "look_save", "was": "Slow Ring", "base_rev": sc.looks_rev,
             "look": {**mine, "block": "pendulum", "args": {"width": 20}}})
    check("changing its block is a save like any other",
          r["ok"] and sc.by_name["Slow Ring"].block == "pendulum")
    check("and a modulator left aimed at an argument the new block lacks is dropped",
          {m["look"] for m in sc.snapshot()["modulators"]} == {"Ball Orbit"},
          f"{sc.snapshot()['modulators']}")
    r = ask({"type": "look_save", "was": "MH Red", "base_rev": sc.looks_rev,
             "look": {"name": "MH Red", "block": "solid", "args": {"color": "#ff0000"}}})
    check("a stored look cannot be changed: it says it can only be hidden",
          r["ok"] is False and "stored look" in r["error"], f"{r}")
    r = ask({"type": "look_save", "was": "Heads - Ball", "base_rev": sc.looks_rev,
             "look": {"name": "Heads - Ball", "block": "offset", "groups": ["corner movers"],
                      "args": {"bearing": 0.0, "elevation": 3.0}}})
    ball_now = next(l for l in on_disk()["looks"] if l["name"] == "Heads - Ball")
    check("a look that took over a stored one stays that when edited -- the editor "
          "does not send the flag, and does not clear it",
          r["ok"] and ball_now.get("supersedes") is True
          and sc.by_name["Heads - Ball"].args["elevation"] == 3.0)
    check("but it no longer says what the stored look says, and the file records "
          "that it was changed on purpose: exact is false, so test_library stops "
          "holding it to the original",
          ball_now.get("exact") is False and sc.by_name["Heads - Ball"].exact is False
          and listed("Heads - Ball")["exact"] is False
          and list(ball_now)[-2:] == ["supersedes", "exact"], f"{ball_now}")
    check("the ones nobody changed are still exact, and say nothing about it",
          sc.by_name["Heads - Apex"].exact is True and listed("Heads - Apex")["exact"] is True
          and "exact" not in next(l for l in on_disk()["looks"] if l["name"] == "Heads - Apex"))
    r = ask({"type": "look_save", "was": "Heads - Ball", "base_rev": sc.looks_rev,
             "look": {**ball_now, "notes": "three degrees up, on purpose", "exact": True}})
    check("a save that changes only its notes leaves the record alone -- and a "
          "client cannot simply claim it is exact again",
          r["ok"] and next(l for l in on_disk()["looks"] if l["name"] == "Heads - Ball")
          .get("exact") is False, f"{r}")
    r = ask({"type": "look_save", "was": "Heads - Ball", "base_rev": sc.looks_rev,
             "look": {"name": "Heads - Ball", "block": "offset", "groups": ["corner movers"],
                      "args": {"bearing": 0.0, "elevation": 0.0}}})
    check("changed back to what the stored look says, it is exact again",
          r["ok"] and "exact" not in next(l for l in on_disk()["looks"]
                                          if l["name"] == "Heads - Ball")
          and sc.by_name["Heads - Ball"].exact is True, f"{r}")
    check("every look that took over a stored one in the event as committed "
          "reproduces it, by the measure a save uses",
          all(lookstore.reproduces(l, ported[l["name"]])
              for l in cfg["looks"] if l.get("supersedes")))
    check("and a look that says something else does not",
          not lookstore.reproduces({"block": "offset", "groups": ["corner movers"],
                                    "args": {"bearing": 0.0, "elevation": 0.02}},
                                   ported["Heads - Ball"])
          and not lookstore.reproduces({"block": "offset", "groups": ["pinspots"],
                                        "args": {}}, ported["Heads - Ball"])
          and lookstore.reproduces({"block": "solid", "groups": ["corner movers"],
                                    "args": {"color": "#ff0000"}}, ported["MH Red"])
          and not lookstore.reproduces({"block": "solid", "groups": ["corner movers"],
                                        "args": {"color": "@primary"}}, ported["MH Red"])
          and not lookstore.reproduces({"block": "orbit", "groups": ["corner movers"],
                                        "args": {}}, ported["Lazy Circle"]))

    # -- 6. renaming ---------------------------------------------------------------
    print("\n6. renaming")
    sc.apply({"type": "select_look", "name": "Runner"}, None)
    sc.apply({"type": "movement_add", "name": "Nod"}, None)
    sc.apply({"type": "look_params", "name": "Runner", "values": {"width": 0.4}}, None)
    sc.apply({"type": "preset_save", "name": "Ring pad"}, None)
    cue_doc = json.loads((EVENT / "cues.json").read_text(encoding="utf-8"))
    cue_doc["cues"][0]["level"] = {"corner movers": "Runner"}
    cue_doc["cues"][0]["params"] = {"Runner": {"width": 0.6}}
    configmod.write_json_atomic(EVENT / "cues.json", cue_doc, backup=False)
    from engine import cues as cuesmod  # noqa: E402
    sc.cues = cuesmod.load(EVENT / "cues.json")
    runner = next(l for l in on_disk()["looks"] if l["name"] == "Runner")
    r = ask({"type": "look_save", "was": "Runner", "base_rev": sc.looks_rev,
             "look": {**runner, "name": "Chaser"}})
    check("a rename is answered with what followed it: the cues and presets that named it",
          r["ok"] and r["data"]["name"] == "Chaser" and r["data"]["cues"] == 1
          and r["data"]["presets"] == 1, f"{r}")
    check("the library has it under the new name only",
          "Chaser" in sc.by_name and "Runner" not in sc.by_name
          and in_snapshot("Runner") is None)
    check("the slot it filled still holds it, and its tuning came along",
          sc.selection["level"] == {"corner movers": "Chaser"}
          and sc.look_params.get("Chaser") == {"width": 0.4}
          and "Runner" not in sc.look_params, f"{sc.selection} {sc.look_params}")
    preset = next(p for p in json.loads((EVENT / "presets.json").read_text(encoding="utf-8"))
                  ["presets"] if p["name"] == "Ring pad")
    check("presets.json names it by the new name, in its slot and in its tuning",
          preset["level"] == {"corner movers": "Chaser"} and "Chaser" in preset["params"]
          and "Runner" not in preset["params"], f"{preset}")
    cue0 = json.loads((EVENT / "cues.json").read_text(encoding="utf-8"))["cues"][0]
    check("cues.json too -- and keeps its comments",
          cue0["level"] == {"corner movers": "Chaser"} and cue0["params"] == {"Chaser": {"width": 0.6}}
          and json.loads((EVENT / "cues.json").read_text(encoding="utf-8")).get("_comment")
          == cue_doc.get("_comment"), f"{cue0}")
    check("and the cue list in memory, without losing its place",
          sc.cues.cues[0].level == {"corner movers": "Chaser"}
          and sc.cues.cues[0].params == {"Chaser": {"width": 0.6}})
    check("a stacked move and the look on stage are untouched by another look's rename",
          sc.movement_extra == ["Nod"] and sc.setlist.current().name == "Slow Ring")
    r = ask({"type": "look_save", "was": "Slow Ring", "base_rev": sc.looks_rev,
             "look": {**mine, "name": "Wide Ring"}})
    check("renaming the look that is on stage keeps it on stage",
          r["ok"] and sc.setlist.current().name == "Wide Ring"
          and sc.selection["movement"] == {"corner movers": "Wide Ring"}, f"{r}")
    r = ask({"type": "look_save", "was": "Lazy Orbit", "base_rev": sc.looks_rev,
             "look": {**next(l for l in on_disk()["looks"] if l["name"] == "Lazy Orbit"),
                      "name": "Lazy Ring"}})
    check("what was hidden in a look's favour points at its new name",
          r["ok"] and sc.by_name["Lazy Circle"].replaced_by == "Lazy Ring", f"{r}")
    r = ask({"type": "look_save", "was": "Wide Ring", "base_rev": sc.looks_rev,
             "look": {**mine, "name": "Nod"}})
    check("not to a name that is taken", r["ok"] is False and "already a look called 'Nod'" in r["error"])
    r = ask({"type": "look_save", "was": "MH Red", "base_rev": sc.looks_rev,
             "look": {"name": "Red", "block": "solid", "args": {"color": "#ff0000"}}})
    check("and a stored look is not renamed: it is remade under the new name, and hidden",
          r["ok"] is False and "make a block look from it" in r["error"], f"{r}")

    cathedral = next(l for l in on_disk()["looks"] if l["name"] == "Heads - Cathedral")
    r = ask({"type": "look_save", "was": "Heads - Cathedral", "base_rev": sc.looks_rev,
             "look": {**cathedral, "name": "Cathedral"}})
    renamed_now = next((l for l in on_disk()["looks"] if l["name"] == "Cathedral"), {})
    check("a look that took over a stored look CAN be renamed: it leaves the name, "
          "and is a look of its own under the new one",
          r["ok"] and sc.by_name["Cathedral"].is_parametric
          and not sc.by_name["Cathedral"].supersedes
          and "supersedes" not in renamed_now and "exact" not in renamed_now, f"{r}")
    back = sc.by_name.get("Heads - Cathedral")
    check("the stored look comes back under the old name, hidden in favour of the "
          "new one -- not back on the picker beside it",
          back is not None and not back.is_parametric and back.retired
          and back.replaced_by == "Cathedral"
          and listed("Cathedral")["used_by"]["hides"] == ["Heads - Cathedral"], f"{back}")
    check("and the cue that named it plays the renamed look, in the file and in memory",
          r["data"]["cues"] == 1
          and next(c for c in sc.cues.cues if c.name == "Cathedral").movement
          == {"corner movers": "Cathedral"}
          and next(c for c in json.loads((EVENT / "cues.json").read_text(encoding="utf-8"))["cues"]
                   if c["name"] == "Cathedral")["movement"] == {"corner movers": "Cathedral"})

    # -- 6b. a rename takes the show folder with it ---------------------------------
    print("\n6b. a rename, and everything in the show folder that names the look")
    from engine import showfiles  # noqa: E402
    ask({"type": "look_save", "base_rev": sc.looks_rev,
         "look": {"name": "index", "block": "dim", "groups": ["pinspots"], "args": {"level": 0.5}}})
    routine_path = SHOWS / "routines" / "idle-orbit.json"
    routine = json.loads(routine_path.read_text(encoding="utf-8"))
    routine["rig"] = "despacio"
    routine["params"]["which"] = {"type": "look", "default": "Chaser"}
    routine["variations"] = {"alt": {"which": "Chaser", "color": "@accent"}}
    routine["rows"].append({"id": "glow", "type": "clips", "target": "level", "role": "movers",
                            "items": [{"id": "g", "at": 0, "len": 4, "block": "look",
                                       "args": {"look": "Chaser"}},
                                      {"id": "h", "at": 4, "len": 4, "block": "look",
                                       "args": {"look": "index"}},
                                      # a CHOICE that happens to be spelled like a look
                                      {"id": "k", "at": 8, "len": 4, "block": "chase",
                                       "args": {"order": "index"}}]})
    timeline_path = SHOWS / "timelines" / "synth-128.json"
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    scene = next(row for row in timeline["rows"] if row["id"] == "scene")
    next(i for i in scene["items"] if i["id"] == "down")["params"]["which"] = "Chaser"
    timeline["rows"].append({"id": "lv", "type": "clips", "target": "level",
                             "items": [{"id": "lk", "kind": "look", "look": "Chaser",
                                        "at": 0, "len": 8}]})
    timeline["rows"].append({"id": "scene2", "type": "clips", "target": "scene",
                             "items": [{"id": "sn", "kind": "snapshot", "at": 0, "len": 8,
                                        "level": {"corner movers": "Chaser"}}]})
    set_path = SHOWS / "templates" / "club.json"
    club = json.loads(set_path.read_text(encoding="utf-8"))
    club["phrases"]["Down"]["params"]["which"] = "Chaser"
    for path, kind, doc in ((routine_path, "routine", routine),
                            (timeline_path, "timeline", timeline),
                            (set_path, "template_set", club)):
        result = showfiles.validate(kind, doc)
        check(f"(the test's {path.name} is a valid {kind})", result.ok, f"{result.errors}")
        path.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    use = lookstore.usage((), (), showfiles.load_folder(SHOWS), sc.library)["Chaser"]
    check("a look names every show file that plays it: a routine's look block, its "
          "parameter's default and a variation; a timeline's look clip, a snapshot's "
          "slot and a routine clip's parameter; a template set's pick",
          use["routines"] == [{"id": "idle-orbit", "name": "Idle orbit"}]
          and use["timelines"] == [{"track": "synth-128", "title": "synthetic 128"}]
          and use["templates"] == [{"id": "club", "name": "Club"}], f"{use}")

    r = ask({"type": "look_save", "was": "Chaser", "base_rev": sc.looks_rev,
             "look": {**runner, "name": "Chaser 2"}})
    check("renaming it rewrites them all -- from the folder as it is on disk, which "
          "the engine had not even reloaded yet -- and says which",
          r["ok"] and r["data"]["written"] == ["routines/idle-orbit.json",
                                              "timelines/synth-128.json",
                                              "templates/club.json"]
          and r["data"]["cues"] == 1 and r["data"]["presets"] == 1, f"{r}")
    routine = json.loads(routine_path.read_text(encoding="utf-8"))
    glow = {i["id"]: i["args"] for i in routine["rows"][-1]["items"]}
    check("in the routine: the block, the parameter's default, the variation",
          glow["g"] == {"look": "Chaser 2"}
          and routine["params"]["which"]["default"] == "Chaser 2"
          and routine["variations"]["alt"] == {"which": "Chaser 2", "color": "@accent"},
          f"{glow} {routine['params']['which']} {routine['variations']}")
    timeline = json.loads(timeline_path.read_text(encoding="utf-8"))
    tl_items = {i["id"]: i for row in timeline["rows"] for i in row.get("items", [])}
    check("in the timeline: the look clip, the snapshot's slot, the routine clip's parameter",
          tl_items["lk"]["look"] == "Chaser 2"
          and tl_items["sn"]["level"] == {"corner movers": "Chaser 2"}
          and tl_items["down"]["params"] == {"color": "@secondary", "which": "Chaser 2"}
          and tl_items["lazy"]["look"] == "Lazy Circle")
    check("in the template set: the pick's parameter",
          json.loads(set_path.read_text(encoding="utf-8"))["phrases"]["Down"]["params"]
          == {"color": "@secondary", "which": "Chaser 2"})
    check("the engine reads the rewritten folder at once, and the look's uses follow it",
          sc.show_library.folder.routines["idle-orbit"]["params"]["which"]["default"] == "Chaser 2"
          and listed("Chaser 2")["used_by"]["templates"] == [{"id": "club", "name": "Club"}]
          and "Chaser" not in sc.by_name)
    check("nothing is left behind under both names when it worked",
          [l["name"] for l in on_disk()["looks"]].count("Chaser 2") == 1
          and not any(l["name"] == "Chaser" for l in on_disk()["looks"]))

    r = ask({"type": "look_save", "was": "index", "base_rev": sc.looks_rev,
             "look": {"name": "Half", "block": "dim", "groups": ["pinspots"], "args": {"level": 0.5}}})
    glow = {i["id"]: i["args"] for i in json.loads(routine_path.read_text(encoding="utf-8"))
            ["rows"][-1]["items"]}
    check("only an argument that can BE a look is rewritten: a chase's order "
          "'index' is a choice, not the look that was called index",
          r["ok"] and glow["h"] == {"look": "Half"} and glow["k"] == {"order": "index"}, f"{glow}")

    # Stopped part-way: a timeline that cannot be written.
    real_write = showfiles.write_doc

    def refusing(path, doc, kind=None, base_rev=None):
        if kind == "timeline":
            raise showfiles.StaleEdit("synth-128.json changed since you opened it")
        return real_write(path, doc, kind, base_rev)

    showfiles.write_doc = refusing
    try:
        r = ask({"type": "look_save", "was": "Chaser 2", "base_rev": sc.looks_rev,
                 "look": {**runner, "name": "Chaser 3"}})
    finally:
        showfiles.write_doc = real_write
    check("a rename stopped part-way says how far it got",
          r["ok"] is False and "renamed as far as routines/idle-orbit.json, then stopped" in r["error"]
          and "'Chaser 2' is still in the library beside 'Chaser 3'" in r["error"], f"{r}")
    names_now = [l["name"] for l in on_disk()["looks"]]
    check("and leaves the look under BOTH names, so nothing names a look that is gone: "
          "the routine plays the new one, the timeline and the cue still the old",
          "Chaser 2" in names_now and "Chaser 3" in names_now
          and json.loads(routine_path.read_text(encoding="utf-8"))["rows"][-1]["items"][0]["args"]
          == {"look": "Chaser 3"}
          and json.loads(timeline_path.read_text(encoding="utf-8"))["rows"][-2]["items"][0]["look"]
          == "Chaser 2"
          and json.loads((EVENT / "cues.json").read_text(encoding="utf-8"))["cues"][0]["level"]
          == {"corner movers": "Chaser 2"}, f"{names_now}")
    r = ask({"type": "looks_reload"})
    check("the engine picks the half-renamed library up as it is: both play",
          r["ok"] and "Chaser 2" in sc.by_name and "Chaser 3" in sc.by_name)

    # -- 7. deleting, hiding --------------------------------------------------------
    print("\n7. deleting and hiding")
    r = ask({"type": "look_delete", "look": "Chaser 2", "base_rev": sc.looks_rev})
    check("a look that is used is not deleted: the refusal says where",
          r["ok"] is False and "cues.json (Warm Up)" in r["error"]
          and "presets.json (Ring pad)" in r["error"]
          and "timelines/synth-128.json" in r["error"]
          and "templates/club.json" in r["error"], f"{r}")
    r = ask({"type": "look_delete", "look": "Chaser 3", "base_rev": sc.looks_rev})
    check("wherever it is used", r["ok"] is False and "routines/idle-orbit.json" in r["error"])
    r = ask({"type": "look_delete", "look": "Lazy Ring", "base_rev": sc.looks_rev})
    check("nor one that something is hidden in favour of",
          r["ok"] is False and "Lazy Circle is hidden in favour of" in r["error"], f"{r}")
    r = ask({"type": "look_delete", "look": "MH Red", "base_rev": sc.looks_rev})
    check("a stored look cannot be deleted, only hidden",
          r["ok"] is False and "hide it instead" in r["error"], f"{r}")
    r = ask({"type": "look_delete", "look": "Lasers", "base_rev": "r:000000000000"})
    check("a delete quotes the rev too", r["ok"] is False and "changed since" in r["error"])
    r = ask({"type": "look_delete", "look": "Lasers", "base_rev": sc.looks_rev})
    check("an unused look is deleted",
          r["ok"] and r["data"]["deleted"] == "Lasers" and "Lasers" not in sc.by_name
          and not any(l["name"] == "Lasers" for l in on_disk()["looks"]), f"{r}")
    sc.apply({"type": "select_look", "name": "Rainbow Roll"}, None)
    check("(a color look selected, nothing else naming it)",
          sc.slots["color"].get("corner movers") == "Rainbow Roll")
    r = ask({"type": "look_delete", "look": "Rainbow Roll", "base_rev": sc.looks_rev})
    check("deleting a look that fills a slot empties that slot",
          r["ok"] and "corner movers" not in sc.slots["color"], f"{r} {sc.slots}")
    ask({"type": "look_save", "base_rev": sc.looks_rev,
         "look": {"name": "Stage Only", "block": "figure8", "groups": ["corner movers"]}})
    sc.apply({"type": "select_look", "name": "Stage Only"}, None)
    r = ask({"type": "look_delete", "look": "Stage Only", "base_rev": sc.looks_rev})
    check("deleting the movement look on stage puts another one up, and says so",
          r["ok"] and sc.setlist.current() is not None
          and sc.setlist.current().name in sc.by_name
          and sc.by_name[sc.setlist.current().name].is_movement
          and any("Stage Only" in n and "up instead" in n for n in sc.notices),
          f"{r} {sc.notices[-3:]}")
    r = ask({"type": "look_delete", "look": "Heads - Apex", "base_rev": sc.looks_rev})
    check("deleting a look that took over a stored one gives the stored one back, "
          "under the same name",
          r["ok"] and "Heads - Apex" in sc.by_name
          and not sc.by_name["Heads - Apex"].is_parametric, f"{r}")

    r = ask({"type": "look_hide", "look": "MH Blue", "base_rev": sc.looks_rev,
             "replaced_by": "Duo Swap", "note": "the duo covers it"})
    check("a stored look is hidden from the picker, with what covers it",
          r["ok"] and in_snapshot("MH Blue")["retired"] is True
          and in_snapshot("MH Blue")["replaced_by"] == "Duo Swap"
          and listed("MH Blue")["retired_note"] == "the duo covers it"
          and {"name": "MH Blue", "replaced_by": "Duo Swap", "note": "the duo covers it"}
          in on_disk()["retired"], f"{r}")
    check("hidden is not removed: it still plays for whatever names it",
          "MH Blue" in sc.by_name
          and next(l for l in sc.setlist.looks if l.name == "MH Blue").manual_only is True)
    r = ask({"type": "look_hide", "look": "Nod", "base_rev": sc.looks_rev})
    check("a block look can be hidden too, and keeps its own notes",
          r["ok"] and listed("Nod")["retired"] is True and "Vertical" in listed("Nod")["notes"])
    r = ask({"type": "look_save", "was": "Nod", "base_rev": sc.looks_rev,
             "look": {**next(l for l in on_disk()["looks"] if l["name"] == "Nod"),
                      "name": "Nod Small"}})
    check("renaming a hidden look keeps it hidden",
          r["ok"] and listed("Nod Small")["retired"] is True, f"{r}")
    r = ask({"type": "look_hide", "look": "MH Blue", "hidden": False, "base_rev": sc.looks_rev})
    check("and a hidden look is shown again",
          r["ok"] and in_snapshot("MH Blue")["retired"] is False
          and not any(row["name"] == "MH Blue" for row in on_disk()["retired"]), f"{r}")
    for label, extra, needle in (
        ("a look that does not exist", {"look": "Nope"}, "no look named 'Nope'"),
        ("pointing at a look that does not exist",
         {"look": "MH Blue", "replaced_by": "Nope"}, "no look named 'Nope'"),
        ("pointing a look at itself", {"look": "MH Blue", "replaced_by": "MH Blue"}, "by itself"),
        ("hidden: maybe", {"look": "MH Blue", "hidden": "maybe"}, "true or false"),
    ):
        r = ask({"type": "look_hide", "base_rev": sc.looks_rev, **extra})
        check(f"refused: hiding {label}", r["ok"] is False and needle in r["error"], f"{r}")

    # -- 8. the file edited by hand -------------------------------------------------
    print("\n8. the file edited by hand, and read again")
    rev = sc.looks_rev
    doc = on_disk()
    doc["looks"].append({"name": "By Hand", "block": "breathe", "groups": ["pinspots"],
                         "args": {"depth": 0.5}})
    FILE.write_text(json.dumps(doc, indent=2), encoding="utf-8")
    check("an edit by hand is noticed: the library says it is out of date",
          sc.looks_public()["stale"] is True and sc.looks_public()["rev"] == rev
          and "By Hand" not in sc.by_name)
    r = ask({"type": "look_hide", "look": "MH Red", "base_rev": rev})
    check("and a save against the library that was read is refused, not merged blind",
          r["ok"] is False and "changed since you opened it" in r["error"], f"{r}")
    r = ask({"type": "looks_reload"})
    check("looks_reload reads it again, live",
          r["ok"] and "By Hand" in sc.by_name and sc.looks_public()["stale"] is False
          and r["data"]["rev"] == lookstore.file_rev(EVENT), f"{r}")
    good_text = FILE.read_text(encoding="utf-8")
    FILE.write_text(good_text.replace('"breathe"', '"wobble"'), encoding="utf-8")
    r = ask({"type": "looks_reload"})
    check("a file that does not load is said, by name, and the running library runs on",
          r["ok"] is False and "'wobble'" in r["error"] and "By Hand" in sc.by_name
          and sc.by_name["By Hand"].block == "breathe", f"{r}")
    FILE.write_text(good_text, encoding="utf-8")
    ask({"type": "looks_reload"})

    print("\n8b. the engine watches the look files itself")

    def settle():
        for _ in range(3):
            assert sc.worker.wait_idle(5.0)
            sc._drain()

    check("a library just read is not read again", sc.look_watcher.poll() is False
          and sc.look_watcher.poll() is False)
    r = ask({"type": "look_hide", "look": "MH Green", "base_rev": sc.looks_rev})
    check("nor is Studio's own save: the library it installed is the file's",
          r["ok"] and sc.look_watcher.poll() is False and sc.look_watcher.poll() is False)
    doc = on_disk()
    doc["looks"].append({"name": "Watched", "block": "pulse", "groups": ["pinspots"]})
    FILE.write_text(lookstore.render(doc), encoding="utf-8")
    check("an edit made outside the engine is seen, and waited on for one poll in "
          "case it is still being written", sc.look_watcher.poll() is False
          and "Watched" not in sc.by_name)
    fired = sc.look_watcher.poll()
    settle()
    check("then read, on the worker, and installed with no command and no restart",
          fired and "Watched" in sc.by_name and in_snapshot("Watched") is not None
          and sc.looks_public()["stale"] is False
          and any("looks read again" in n for n in sc.notices), f"{sc.notices[-2:]}")
    watched_text = FILE.read_text(encoding="utf-8")
    FILE.write_text(watched_text.replace('"pulse"', '"wobble"'), encoding="utf-8")
    sc.look_watcher.poll()
    sc.look_watcher.poll()
    settle()
    public = sc.looks_public()
    check("a file that does not load is said once, with why, and the library that "
          "was running runs on",
          "Watched" in sc.by_name and sc.by_name["Watched"].block == "pulse"
          and public["stale"] is True and "'wobble'" in (public["problem"] or "")
          and any("do not load" in n and "wobble" in n for n in sc.notices),
          f"{sc.notices[-1:]}")
    before_notes = len([n for n in sc.notices if "do not load" in n])
    sc.look_watcher.poll()
    sc.look_watcher.poll()
    settle()
    check("and it is not read over and over while it stays broken",
          len([n for n in sc.notices if "do not load" in n]) == before_notes)
    FILE.write_text(watched_text, encoding="utf-8")
    sc.look_watcher.poll()
    sc.look_watcher.poll()
    settle()
    check("mended, it is read, and the problem goes",
          sc.looks_public()["stale"] is False and sc.looks_public()["problem"] is None)
    sc.apply({"type": "look_params", "name": "Watched", "values": {"depth": 0.5}}, None)
    doc = on_disk()
    next(l for l in doc["looks"] if l["name"] == "Watched")["args"] = {"depth": 0.9}
    FILE.write_text(lookstore.render(doc), encoding="utf-8")
    sc.look_watcher.poll()
    sc.look_watcher.poll()
    settle()
    check("tuning dialled in over a look whose arguments then changed on disk is let go",
          sc.by_name["Watched"].args == {"depth": 0.9} and "Watched" not in sc.look_params)

    # -- 9. the show folder's programs ----------------------------------------------
    print("\n9. what was built from the old library")
    sentinel = object()
    sc.player.program, sc.player._program_for = sentinel, ("seq", 1)
    ask({"type": "look_hide", "look": "MH Red", "base_rev": sc.looks_rev})
    check("a look edit does not drop the program on stage: it plays on until its "
          "rebuild lands (a rig change drops it at once; this is not one)",
          sc.player.program is sentinel and sc.player._program_for is None)
    sc.player.program = None

    # -- 10. an event that was never ported ----------------------------------------
    print("\n10. an event with no looks at all")
    bare = TMP / "bare"
    bare.mkdir()
    for name in ("rig.json", "calibration.json"):
        shutil.copy(EVENT / name, bare / name)
    bc = servermod.ShowController(bare)
    bc.worker.start()
    bc.reply_to = lambda cid, payload: replies.append(payload)

    def ask_bare(msg):
        replies.clear()
        bc.submit({**msg, "id": next(_ids)}, designer)
        bc._drain()
        for _ in range(4):
            assert bc.worker.wait_idle(5.0)
            bc._drain()
        return replies[-1] if replies else None

    public = bc.looks_public()
    check("with neither file the library is empty, the scaffold runs, and there is no rev",
          public["looks"] == [] and public["rev"] is None and public["stale"] is False
          and bc.setlist.current() is not None and bc.snapshot()["looks_rev"] == "")
    r = ask_bare({"type": "look_save", "base_rev": "",
                  "look": {"name": "First", "block": "orbit", "args": {"radius": 15}}})
    made = json.loads((bare / "parametric_looks.json").read_text(encoding="utf-8"))
    check("its first look makes the file -- schema line, a comment, version -- and "
          "is the library",
          r["ok"] and made["looks"] == [{"name": "First", "block": "orbit",
                                         "groups": [], "args": {"radius": 15}}]
          and made["version"] == 1 and "$schema" in made and made["_comment"]
          and [e.name for e in bc.library] == ["First"]
          and bc.setlist.current().name == "First", f"{r}")
    r = ask_bare({"type": "look_save", "base_rev": "",
                  "look": {"name": "Second", "block": "solid", "args": {"color": "@primary"}}})
    check('"" stops meaning "no file yet" once there is one',
          r["ok"] is False and "changed since" in r["error"], f"{r}")
    r = ask_bare({"type": "look_delete", "look": "First", "base_rev": bc.looks_rev})
    check("deleting the last look goes back to the scaffold rather than an empty set list",
          r["ok"] and bc.library == [] and bc.setlist.current() is not None, f"{r}")
    bc.worker.stop()
finally:
    sc.worker.stop()

print()
if failures:
    print(f"{len(failures)} FAILED:")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("all look-library tests passed")
