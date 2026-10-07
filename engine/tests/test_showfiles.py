"""
Tests for the show folder: its formats, its validation and its writes.

The folder is edited by hand, by the designer, by an assistant over MCP and by
a sync service that knows nothing about any of them. So most of what is checked
here is a refusal: a file that cannot mean anything is not loaded, a save over
a file that changed underneath it is refused, a conflict copy is never picked,
and one bad file never stops the rest loading.

Run: python engine/tests/test_showfiles.py
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

from engine import config as configmod  # noqa: E402
from engine import showfiles as sf  # noqa: E402

EXAMPLE = REPO / "shared" / "show-example"

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def load(rel):
    return json.loads((EXAMPLE / rel).read_text(encoding="utf-8"))


def errors_of(kind, doc):
    return sf.validate(kind, doc).errors


def refused(label, kind, doc, needle):
    errs = errors_of(kind, doc)
    check(label, any(needle in e for e in errs), f"{errs[:2]}")


def warned(label, kind, doc, needle):
    r = sf.validate(kind, doc)
    check(label, r.ok and any(needle in w for w in r.warnings),
          f"errors={r.errors[:1]} warnings={r.warnings[:2]}")


TRACK = load("tracks/synth-128.json")
TIMELINE = load("timelines/synth-128.json")
FAN = load("routines/fan-drop.json")
CLUB = load("templates/club.json")
SHOW = load("show.json")


def edit(doc, fn):
    d = copy.deepcopy(doc)
    fn(d)
    return d


def row(d, ident):
    """A timeline or routine row by id -- never by position, which moves
    whenever the example's lanes are reordered."""
    return next(r for r in d["rows"] if r["id"] == ident)


def scene_items(d):
    return row(d, "scene")["items"]


# -----------------------------------------------------------------------------
print("\n1. the example folder")
folder = sf.load_folder(EXAMPLE)
check("loads with no errors", folder.errors == [], f"{folder.errors[:3]}")
check("and no warnings -- it is the reference, so it should be clean",
      folder.warnings == [], f"{folder.warnings[:3]}")
check("everything in it is found",
      set(folder.tracks) == {"synth-128"} and set(folder.timelines) == {"synth-128"}
      and set(folder.routines) == {"idle-orbit", "verse-sweep", "build-rise", "fan-drop"}
      and set(folder.templates) == {"club"} and folder.show is not None,
      f"{sorted(folder.routines)}")
used_blocks = {i["block"] for r in folder.routines.values() for row in r["rows"]
               for i in row.get("items", []) if "block" in i}
check("every block the example uses is a known block",
      used_blocks <= set(sf.BLOCK_NAMES), f"{used_blocks - set(sf.BLOCK_NAMES)}")
kinds = {i.get("kind") for row in TIMELINE["rows"] for i in row.get("items", [])}
row_types = {row["type"] for row in TIMELINE["rows"]}
check("its timeline holds all four item types and every row type",
      {"snapshot", "look", "routine", "palette"} <= kinds
      and row_types == {"clips", "hits", "automation", "external"},
      f"{kinds} {row_types}")


# -----------------------------------------------------------------------------
print("\n2. every format refuses what cannot mean anything")
refused("a document that says it is something else",
        "timeline", TRACK, "kind")
refused("a version newer than the engine",
        "routine", edit(FAN, lambda d: d.update(version=99)), "newer")

refused("track: an id that cannot be a file name",
        "track", edit(TRACK, lambda d: d.update(id="Fan Drop")), "not usable")
refused("routine: an id with a newline on the end (it would be the file name)",
        "routine", edit(FAN, lambda d: d.update(id="fan-drop\n")), "not usable")
refused("track: a grid that runs backwards",
        "track", edit(TRACK, lambda d: d["grid"].update(
            segments=[[0, 1000, 128], [64, 900, 128]])), "backwards")
refused("track: phrases that overlap",
        "track", edit(TRACK, lambda d: d["phrases"].update(
            items=[[0, 64, "Intro"], [32, 96, "Verse 1"]])), "inside")
refused("track: a signature that is not one",
        "track", edit(TRACK, lambda d: d["ids"].update(blt_signatures=["abc"])),
        "40 lower-case")
refused("track: a string where a bpm belongs",
        "track", edit(TRACK, lambda d: d["identity"].update(bpm="128")), "number")
warned("track: a stated rev that no longer matches its segments warns",
       "track", edit(TRACK, lambda d: d["grid"].update(rev="g:000000")), "hand-edited")

refused("timeline: a clip a thousand times longer than any track (a typo)",
        "timeline", edit(TIMELINE, lambda d: scene_items(d)[0].update(len=3200000)),
        "at most")
refused("timeline: a clip placed past nine hours",
        "timeline", edit(TIMELINE, lambda d: scene_items(d)[0].update(at=1e9)),
        "at most")
refused("timeline: a palette change on the scene lane",
        "timeline", edit(TIMELINE, lambda d: scene_items(d).append(
            {"id": "p9", "kind": "palette", "at": 0, "len": 4, "palette": "Hot"})),
        "cannot go on the scene lane")
refused("timeline: a snapshot on a slot lane",
        "timeline", edit(TIMELINE, lambda d: row(d, "move")["items"].append(
            {"id": "s9", "kind": "snapshot", "at": 0, "len": 4,
             "movement": {"corner movers": "Ball Wave"}})),
        "belongs on the scene lane")
refused("timeline: a snapshot of nothing",
        "timeline", edit(TIMELINE, lambda d: scene_items(d).append(
            {"id": "s9", "kind": "snapshot", "at": 400, "len": 4})), "needs a preset")
refused("timeline: a fade longer than its item",
        "timeline", edit(TIMELINE, lambda d: scene_items(d)[0].update(fade=100)),
        "fades in over 100")
refused("timeline: an item of no length",
        "timeline", edit(TIMELINE, lambda d: scene_items(d)[0].update(len=0)),
        "longer than nothing")
refused("timeline: two items with one id",
        "timeline", edit(TIMELINE, lambda d: scene_items(d)[1].update(id="intro")),
        "share the id")
refused("timeline: two rows with one id",
        "timeline", edit(TIMELINE, lambda d: row(d, "move").update(id="scene")),
        "share the id")
refused("timeline: a routine clip with no routine",
        "timeline", edit(TIMELINE, lambda d: scene_items(d)[1].pop("routine")),
        "routine is required")
refused("timeline: an item kind that does not exist",
        "timeline", edit(TIMELINE, lambda d: scene_items(d)[1].update(kind="video")),
        "kind must be one of")
refused("timeline: a row type that does not exist",
        "timeline", edit(TIMELINE, lambda d: d["rows"].append(
            {"id": "x", "type": "lasers", "items": []})), "type must be one of")
warned("timeline: a second automation row for one target is never heard",
       "timeline", edit(TIMELINE, lambda d: d["rows"].append(
           {"id": "master2", "type": "automation", "target": "master",
            "points": [[0, 0.2]]})), "never heard")
refused("timeline: automation that goes back in time",
        "timeline", edit(TIMELINE, lambda d: row(d, "master")["points"].append([10, 0.5])),
        "not after the previous point")
refused("timeline: a master above 1",
        "timeline", edit(TIMELINE, lambda d: row(d, "master")["points"].append([400, 1.5])),
        "from 0 to 1")
refused("timeline: automating something that cannot be automated",
        "timeline", edit(TIMELINE, lambda d: row(d, "master").update(target="gobo")),
        "not something that can be automated")
refused("timeline: a curve that does not exist",
        "timeline", edit(TIMELINE, lambda d: row(d, "master")["points"].append(
            [400, 0.2, "bounce"])), "curve")
refused("timeline: a palette change to a palette it never defined",
        "timeline", edit(TIMELINE, lambda d: row(d, "palette")["items"][0].update(
            palette="Ultraviolet")), "not defined")
refused("timeline: a palette whose colour is itself a role",
        "timeline", edit(TIMELINE, lambda d: d["palettes"]["Hot"].update(
            primary="@accent")), "cannot name one")
refused("timeline: a palette missing a role",
        "timeline", edit(TIMELINE, lambda d: d["palettes"]["Hot"].pop("accent")),
        "has no accent")
refused("timeline: a palette role that does not exist",
        "timeline", edit(TIMELINE, lambda d: scene_items(d)[1]["params"].update(
            color="@tertiary")), "not a palette role")
refused("timeline: a hit that is not a hit",
        "timeline", edit(TIMELINE, lambda d: row(d, "hits")["items"][0].update(hit="laser")),
        "hit must be")
warned("timeline: two clips overlapping on one lane is a warning, not an error",
       "timeline", edit(TIMELINE, lambda d: scene_items(d)[1].update(at=60)), "overlap")
warned("timeline: a row for an output this engine does not play is kept, with "
       "a warning",
       "timeline", edit(TIMELINE, lambda d: row(d, "vj-clips").update(
           output="laser-show", layer=3,
           items=[{"id": "zap", "at": 0, "len": 4, "whatever": True}])),
       "does not play output 'laser-show'")
refused("timeline: but its items are still windows, which every output shares",
        "timeline", edit(TIMELINE, lambda d: row(d, "vj-clips").update(
            output="laser-show", items=[{"whatever": True}])), "id is required")
check("timeline: 'vj' is still read, as the visuals output",
      sf.output_name({"output": "vj"}) == "visuals")
refused("timeline: an OSC cue that says nothing",
        "timeline", edit(TIMELINE, lambda d: row(d, "vj-clips")["items"][0].pop("on")),
        "says nothing")
refused("timeline: an OSC address without its slash",
        "timeline", edit(TIMELINE, lambda d: row(d, "vj-clips")["items"][0]["on"].update(
            address="composition/layers/1")), "is not an OSC address")
refused("timeline: an OSC address with a space",
        "timeline", edit(TIMELINE, lambda d: row(d, "vj-clips")["items"][0]["on"].update(
            address="/layer 1")), "is not an OSC address")
refused("timeline: an argument token that does not exist",
        "timeline", edit(TIMELINE, lambda d: row(d, "vj-clips")["items"][1]["while"].update(
            args=["$progres"])), "'$progres' is not one of")
refused("timeline: an OSC argument that is a list",
        "timeline", edit(TIMELINE, lambda d: row(d, "vj-clips")["items"][0]["on"].update(
            args=[[1, 2]])), "numbers or text")
refused("timeline: a curve with nowhere to send it",
        "timeline", edit(TIMELINE, lambda d: row(d, "vj-opacity").pop("address")),
        "no address")
refused("timeline: a curve whose values are not numbers",
        "timeline", edit(TIMELINE, lambda d: row(d, "vj-opacity")["points"].append(
            [300, "loud"])), "with numbers")
refused("timeline: a curve going backwards",
        "timeline", edit(TIMELINE, lambda d: row(d, "vj-opacity")["points"].append(
            [10, 0.2])), "not after the point before it")
refused("timeline: an external cue with no length",
        "timeline", edit(TIMELINE, lambda d: row(d, "vj-clips")["items"][0].update(len=0)),
        "longer than nothing")
check("routine: may carry an OSC row too, so a template can cue a VJ app",
      sf.validate("routine", edit(FAN, lambda d: d["rows"].append(
          {"id": "vj", "type": "external", "output": "osc",
           "items": [{"id": "go", "at": 0, "len": 4,
                      "on": {"address": "/go", "args": ["$bar"]}}]}))).ok)
refused("routine: and its OSC rows are checked the same way",
        "routine", edit(FAN, lambda d: d["rows"].append(
            {"id": "vj", "type": "external", "output": "osc",
             "items": [{"id": "go", "at": 0, "len": 4,
                        "on": {"address": "go"}}]})), "is not an OSC address")
def midi_row(d, **row):
    d["rows"].append({"id": "midi", "type": "external", "output": "midi", **row})


check("timeline: a MIDI lane of a note, a CC and a program change",
      sf.validate("timeline", edit(TIMELINE, lambda d: midi_row(d, channel=2, items=[
          {"id": "n", "at": 0, "len": 4, "note": 60, "velocity": 90},
          {"id": "c", "at": 4, "len": 4, "cc": 7, "value": 100, "off_value": 0},
          {"id": "p", "at": 8, "len": 1, "pc": 3, "channel": 10}]))).ok)
refused("timeline: a MIDI cue must be exactly one thing",
        "timeline", edit(TIMELINE, lambda d: midi_row(d, items=[
            {"id": "n", "at": 0, "len": 4, "note": 60, "cc": 7}])),
        "exactly one of a note, a cc or a pc, not note and cc")
refused("timeline: or something at all",
        "timeline", edit(TIMELINE, lambda d: midi_row(d, items=[
            {"id": "n", "at": 0, "len": 4, "channel": 3}])), "exactly one of")
refused("timeline: a note past 127",
        "timeline", edit(TIMELINE, lambda d: midi_row(d, items=[
            {"id": "n", "at": 0, "len": 4, "note": 128}])), "note must be at most 127")
refused("timeline: channel 0 -- channels are 1-16, as on the gear",
        "timeline", edit(TIMELINE, lambda d: midi_row(d, items=[
            {"id": "n", "at": 0, "len": 4, "note": 60, "channel": 0}])),
        "channel must be at least 1")
warned("timeline: a velocity on a CC means nothing, and says so",
       "timeline", edit(TIMELINE, lambda d: midi_row(d, items=[
           {"id": "c", "at": 0, "len": 4, "cc": 7, "velocity": 90}])),
       "velocity means nothing without a cc")
refused("timeline: a MIDI curve needs a cc to drive",
        "timeline", edit(TIMELINE, lambda d: midi_row(d, points=[[0, 0], [8, 1]])),
        "no cc")
refused("timeline: and runs 0-1",
        "timeline", edit(TIMELINE, lambda d: midi_row(d, cc=7, points=[[0, 0], [8, 64]])),
        "runs 0-1")
check("show.json: the MIDI sidecar, {} for the default",
      sf.validate("show", edit(SHOW, lambda d: d.update(outputs={"midi": {}}))).ok)
check("show.json: where OSC goes",
      sf.validate("show", edit(SHOW, lambda d: d.update(
          outputs={"osc": {"host": "10.0.0.5", "port": 7000}}))).ok)
refused("show.json: an OSC port out of range",
        "show", edit(SHOW, lambda d: d.update(outputs={"osc": {"port": 70000}})),
        "port must be at most")

refused("routine: a $param it never declared",
        "routine", edit(FAN, lambda d: d["rows"][0]["items"][0]["args"].update(
            width="$wdth")), "$wdth")
refused("routine: one hidden in a list",
        "routine", edit(FAN, lambda d: d["rows"][1]["items"][0]["args"].update(
            color=["$color", "$glow"])), "$glow")
refused("routine: a role it never declared",
        "routine", edit(FAN, lambda d: d["rows"][0].update(role="lasers")), "lasers")
refused("routine: a block that does not exist",
        "routine", edit(FAN, lambda d: d["rows"][0]["items"][0].update(block="spin")),
        "block must be")
refused("routine: a variation setting a param it does not have",
        "routine", edit(FAN, lambda d: d["variations"]["wide"].update(speed=2)),
        "not one of this routine's params")
refused("routine: a variation out of the param's range",
        "routine", edit(FAN, lambda d: d["variations"]["wide"].update(width=500)),
        "above the maximum")
refused("routine: a colour param whose default is not a colour",
        "routine", edit(FAN, lambda d: d["params"]["color"].update(default=[2, 0, 0])),
        "not a colour")
refused("routine: a param with no type",
        "routine", edit(FAN, lambda d: d["params"]["width"].pop("type")), "needs a type")
refused("routine: a role with no default tag",
        "routine", edit(FAN, lambda d: d["roles"].update(movers={})), "default tag")
uses_look = edit(FAN, lambda d: d["rows"][0]["items"].append(
    {"id": "lk", "at": 0, "len": 4, "block": "look", "args": {"look": "Ball Wave"}}))
refused("routine: a rig-bound look without saying which rig",
        "routine", uses_look, "which only exist on one rig")
check("routine: and with its rig named, it is accepted",
      sf.validate("routine", edit(uses_look, lambda d: d.update(rig="despacio"))).ok)
warned("routine: an item running past the routine's end warns",
       "routine", edit(FAN, lambda d: d["rows"][0]["items"][0].update(len=40)), "past")


def lane(target, points, rid="lane"):
    return {"id": rid, "type": "automation", "target": target, "points": points}


def with_lane(target, points, doc=FAN):
    return edit(doc, lambda d: d["rows"].append(lane(target, points)))


# A routine's own param lanes, held to the routine's declaration of the param
# exactly as a variation is (fan-drop: width 0-120, rate a rate, color a colour).
check("routine: a lane for each kind of param it can automate is accepted",
      sf.validate("routine", edit(FAN, lambda d: d["rows"].extend([
          lane("param.width", [[0, 20], [16, 120, "ease"]], "lane-w"),
          lane("param.rate", [[0, 0.5], [32, 8]], "lane-r"),
          lane("param.color", [[0, "@primary"], [8, "#00ff88"], [16, [0, 0, 1]]],
               "lane-c")]))).errors == [])
refused("routine: a lane for a param it never declared",
        "routine", with_lane("param.speed", [[0, 1]]), "does not declare in params")
refused("routine: a lane point above the param's max",
        "routine", with_lane("param.width", [[0, 20], [8, 121]]), "above the maximum 120")
refused("routine: a lane point below the param's min",
        "routine", with_lane("param.width", [[0, -5]]), "below the minimum 0")
refused("routine: a rate lane past the 0-8 every rate is held to, even unstated",
        "routine", with_lane("param.rate", [[0, 9]]), "above the maximum 8")
refused("routine: a number on a colour param's lane",
        "routine", with_lane("param.color", [[0, "@primary"], [4, 0.5]]), "not a colour")
refused("routine: a colour on a number param's lane",
        "routine", with_lane("param.width", [[0, 10], [4, "#ff0000"]]), "must be a number")
refused("routine: a palette role that does not exist, on a colour lane",
        "routine", with_lane("param.color", [[0, "@tertiary"]]), "not a palette role")
refused("routine: a look param's lane -- the look is chosen when the routine is "
        "built, so a lane could never change it",
        "routine", edit(with_lane("param.which", [[0, "Ball Wave"]]),
                        lambda d: d["params"].update(which={"type": "look"})),
        "is a look")
warned("routine: a variation setting a param the routine's own lane drives warns",
       "routine", with_lane("param.width", [[0, 20]]), "never heard")
refused("timeline: a param lane mixing numbers and colours",
        "timeline", edit(TIMELINE, lambda d: d["rows"].append(
            lane("param.color", [[0, "#ff0000"], [8, 0.5]]))), "mixes numbers and colours")
refused("timeline: a param lane value that is neither",
        "timeline", edit(TIMELINE, lambda d: d["rows"].append(
            lane("param.color", [[0, True]]))), "a number or a colour")

# Argument lanes: arg.<item>.<argument>, a routine's only, ranged by the
# block's own declaration of the argument (blocks.PARAMS).
check("routine: lanes on a number argument and a colour argument are accepted",
      sf.validate("routine", edit(FAN, lambda d: (
          row(d, "c")["items"][0]["args"].update(color="#ff0000"),
          d["rows"].extend([
              lane("arg.fan.spread", [[0, 0.5], [16, 1.0]], "lane-s"),
              lane("arg.chase.width", [[0, 0.25], [8, 0.5, "step"]], "lane-cw"),
              lane("arg.solid.color", [[0, "#ff0000"], [8, "@accent"]], "lane-sc")])
      ))).errors == [])
refused("routine: an argument lane on an item it does not have",
        "routine", with_lane("arg.nope.width", [[0, 1]]), "no item 'nope'")
refused("routine: an argument the block does not take",
        "routine", with_lane("arg.fan.radius", [[0, 1]]), "fan_sweep has no argument 'radius'")
refused("routine: a choice argument -- no halfway between x and -x",
        "routine", with_lane("arg.chase.order", [[0, 1]]), "is a choice")
refused("routine: an argument already fed by a $param -- automate the param",
        "routine", with_lane("arg.fan.width", [[0, 30]]), "automate param.width")
refused("routine: an argument lane past the block's declared range",
        "routine", with_lane("arg.fan.spread", [[0, 0.5], [8, 2]]), "above the maximum 1")
refused("routine: a number on a colour argument's lane",
        "routine", edit(with_lane("arg.solid.color", [[0, 0.5]]),
                        lambda d: row(d, "c")["items"][0]["args"].update(color="#ff0000")),
        "not a colour")
with_offset = edit(FAN, lambda d: row(d, "m")["items"].append(
    {"id": "off", "at": 0, "len": 4, "block": "offset", "args": {"bearing": 0}}))
warned("routine: an absolute angle past the fallback range only warns -- its "
       "real bound is the playing rig's reach",
       "routine", with_lane("arg.off.bearing", [[0, 300]], with_offset), "above the maximum 270")
warned("routine: a lane on a cycle length warns that the block will jump",
       "routine", with_lane("arg.fan.bars", [[0, 4], [16, 2]]), "makes the block jump")
refused("timeline: an argument lane -- a timeline has no blocks",
        "timeline", edit(TIMELINE, lambda d: d["rows"].append(
            lane("arg.fan.spread", [[0, 0.5]]))), "a timeline has no blocks")
refused("routine: a param named like an argument lane would be shadowed by one",
        "routine", edit(FAN, lambda d: d["params"].update(
            {"arg.fan.spread": {"type": "number"}})), "kept for argument lanes")

# Waves, on top of any lane's points.
def with_wave(doc, rid, wave):
    return edit(doc, lambda d: row(d, rid).update(wave=wave))


check("timeline: a wave on a macro lane that stays in range is accepted",
      sf.validate("timeline", with_wave(TIMELINE, "size",
                                        {"shape": "sine", "bars": 4, "depth": 0.5})).ok)
refused("timeline: a wave that lifts master past 1 at some point (0.6 + 0.5)",
        "timeline", with_wave(TIMELINE, "master", {"shape": "sine", "bars": 4, "depth": 0.5}),
        "at point 0 it reaches 1.1")
refused("timeline: a number lane's wave with no depth",
        "timeline", with_wave(TIMELINE, "size", {"shape": "sine", "bars": 4}), "needs a depth")
refused("timeline: a number lane's wave swinging toward a colour",
        "timeline", with_wave(TIMELINE, "size", {"shape": "sine", "bars": 4, "depth": 0.2,
                                                 "toward": "#ff0000"}), "has a toward colour")
refused("timeline: a wave shape that does not exist",
        "timeline", with_wave(TIMELINE, "size", {"shape": "wobble", "bars": 4, "depth": 0.2}),
        "wobble")
refused("timeline: a wave with no cycle length",
        "timeline", with_wave(TIMELINE, "size", {"shape": "sine", "bars": 0, "depth": 0.2}),
        "bars must be at least")
colour_lane = lambda w: edit(FAN, lambda d: d["rows"].append(  # noqa: E731
    lane("param.color", [[0, "@primary"]]) | {"wave": w}))
check("routine: a colour lane's wave toward another colour is accepted",
      sf.validate("routine", colour_lane({"shape": "square", "bars": 1,
                                          "toward": "@accent", "depth": 0.5})).ok)
refused("routine: a colour lane's wave with nowhere to swing to",
        "routine", colour_lane({"shape": "sine", "bars": 1}), "needs toward")
refused("routine: a colour lane's wave toward a role that does not exist",
        "routine", colour_lane({"shape": "sine", "bars": 1, "toward": "@tertiary"}),
        "not a palette role")
refused("routine: a colour lane's wave more than all the way there",
        "routine", colour_lane({"shape": "sine", "bars": 1, "toward": "#ffffff",
                                "depth": 1.5}), "0 to 1")
refused("routine: a wave on a param lane that swings past the param's max",
        "routine", edit(with_lane("param.width", [[0, 100]]),
                        lambda d: row(d, "lane").update(
                            wave={"shape": "triangle", "bars": 2, "depth": 30})),
        "it reaches 130")
warned("routine: a wave whose cycle does not fit the loop jumps every pass",
       "routine", edit(with_lane("param.width", [[0, 20]]),
                       lambda d: row(d, "lane").update(
                           wave={"shape": "sine", "bars": 3, "depth": 10})),
       "does not fit")

# A band of the track's own audio, on top of a timeline lane's points.
def with_audio(doc, rid, audio):
    return edit(doc, lambda d: row(d, rid).update(audio=audio))


check("timeline: a lane following a band that stays in range is accepted",
      sf.validate("timeline", with_audio(TIMELINE, "size", {
          "band": "low", "depth": 0.5, "floor": 0.2, "ceiling": 0.9, "release": 0.5})).ok)
check("timeline: and one that goes below its points",
      sf.validate("timeline", with_audio(TIMELINE, "master",
                                         {"band": "high", "depth": -0.4})).ok)
refused("timeline: a band that lifts master past 1 at some point (0.6 + 0.5)",
        "timeline", with_audio(TIMELINE, "master", {"band": "low", "depth": 0.5}),
        "audio: at point 0 it reaches 1.1")
refused("timeline: a wave and a band that fit apart, and not on the same beat",
        "timeline", with_wave(with_audio(TIMELINE, "size", {"band": "low", "depth": 0.7}),
                              "size", {"shape": "sine", "bars": 4, "depth": 0.7}),
        "it reaches 3.2 with the wave")
refused("timeline: a band with no depth",
        "timeline", with_audio(TIMELINE, "size", {"band": "low"}), "needs a depth")
refused("timeline: a band that does not exist",
        "timeline", with_audio(TIMELINE, "size", {"band": "sub", "depth": 0.2}), "band")
refused("timeline: a floor at or above its ceiling",
        "timeline", with_audio(TIMELINE, "size", {"band": "low", "depth": 0.2,
                                                  "floor": 0.6, "ceiling": 0.6}),
        "must be under its ceiling")
refused("timeline: a release of a thousand beats",
        "timeline", with_audio(TIMELINE, "size", {"band": "low", "depth": 0.2,
                                                  "release": 1000}), "release")
refused("timeline: a band on a colour lane -- it moves a number",
        "timeline", edit(TIMELINE, lambda d: d["rows"].append(
            lane("param.color", [[0, "@primary"]])
            | {"audio": {"band": "low", "depth": 0.5}})), "this lane is a colour")
refused("routine: a band on a routine's own lane -- it plays on any track",
        "routine", edit(with_lane("param.width", [[0, 20]]),
                        lambda d: row(d, "lane").update(
                            audio={"band": "low", "depth": 10})),
        "a routine plays on any track")
check("swing: a wave and a band reach the sum of their depths on each side",
      sf.swing({"wave": {"depth": 0.3}, "audio": {"depth": -0.2}}) == (-0.2, 0.3)
      and sf.swing({"wave": {"depth": 0.3}, "audio": {"depth": 0.2}}) == (0.0, 0.5)
      and sf.swing({"points": []}) == (0.0, 0.0))

# A timeline's param lanes, against the routines it places (fan-drop, idle-orbit,
# verse-sweep, build-rise). Warnings: the routine is another file.
ROUTINES = {r: load(f"routines/{r}.json")
            for r in ("fan-drop", "idle-orbit", "verse-sweep", "build-rise")}


def lane_warnings(target, points):
    return sf.param_lane_problems(
        edit(TIMELINE, lambda d: d["rows"].append(lane(target, points))), ROUTINES)


w = lane_warnings("param.width", [[0, 40], [16, 130]])
check("timeline: a param lane point past a placed routine's max is named, "
      "with the routine", any("'fan-drop'" in x and "above the maximum 120" in x
                              and "point 1" in x for x in w), f"{w}")
w = lane_warnings("param.radius", [[0, 30]])
check("timeline: and one within range says nothing", w == [], f"{w}")
w = sf.param_lane_problems(edit(TIMELINE, lambda d: d["rows"].append(
    lane("param.width", [[0, 100]]) | {"wave": {"shape": "sine", "bars": 4, "depth": 30}})),
    ROUTINES)
check("timeline: a param lane's wave that swings past a placed routine's max",
      any("it reaches 130" in x and "'fan-drop'" in x for x in w), f"{w}")
w = sf.param_lane_problems(edit(TIMELINE, lambda d: d["rows"].append(
    lane("param.width", [[0, 100]]) | {"audio": {"band": "low", "depth": 30}})),
    ROUTINES)
check("timeline: a param lane's band that swings past a placed routine's max",
      any("audio: at point 0 it reaches 130" in x and "'fan-drop'" in x for x in w),
      f"{w}")
w = sf.param_lane_problems(edit(TIMELINE, lambda d: d["rows"].append(
    lane("param.width", [[0, 100]]) | {"audio": {"band": "low", "depth": 15},
                                       "wave": {"shape": "sine", "bars": 4, "depth": 15}})),
    ROUTINES)
check("timeline: or with its wave, when the two together do",
      any("wave and audio: at point 0 it reaches 130" in x for x in w), f"{w}")
w = lane_warnings("param.color", [[0, 0.5]])
check("timeline: a number lane for a param every routine has as a colour",
      sum("not a colour" in x for x in w) >= 2, f"{w}")
w = lane_warnings("param.wdith", [[0, 40]])
check("timeline: a lane no placed routine has does nothing, and says so",
      any("does nothing" in x for x in w), f"{w}")
w = sf.param_lane_problems(
    edit(TIMELINE, lambda d: d["rows"].append(lane("param.which", [[0, "x"]]))),
    {**ROUTINES, "fan-drop": edit(ROUTINES["fan-drop"], lambda d: d["params"].update(
        which={"type": "look"}))})
check("timeline: a lane for a look param says it cannot change it",
      any("is a look" in x for x in w), f"{w}")

refused("template set: a phrase with no routine",
        "template_set", edit(CLUB, lambda d: d["phrases"].update(Up={})),
        "routine is required")
refused("template set: a palette it never defined",
        "template_set", edit(CLUB, lambda d: d["phrases"]["Up"].update(palette="Neon")),
        "not defined")
refused("show: a pause policy that does not exist",
        "show", edit(SHOW, lambda d: d["pause"].update(policy="panic")), "policy")
refused("show: a latency of a minute",
        "show", edit(SHOW, lambda d: d["sources"]["rkbx"].update(latency_ms=60000)),
        "latency_ms")
check("show: Follow DJ in the example starts disarmed",
      SHOW["follow"]["default"] == "disarmed")


# -----------------------------------------------------------------------------
print("\n3. round trips and writes")
tmp = Path(tempfile.mkdtemp(prefix="klights-showfiles-"))
try:
    root = tmp / "show"
    shutil.copytree(EXAMPLE, root)

    marked = edit(TIMELINE, lambda d: (
        d.update(_comment=["kept"], future_top={"x": 1}),
        d["rows"][0].update(future_row=[1, 2]),
        scene_items(d)[0].update(future_item="yes")))
    path = sf.path_for(root, "timeline", "synth-128")
    base = sf.doc_rev(path)
    new_rev = sf.write_doc(path, marked, base_rev=base)
    back = json.loads(path.read_text(encoding="utf-8"))
    check("unknown keys survive validation and a write, at every level",
          back == marked, "something was dropped")
    check("a write returns the new rev, and it differs", new_rev != base
          and new_rev == sf.doc_rev(path))

    try:
        sf.write_doc(path, TIMELINE, base_rev=base)
        check("a save over a file that changed since it was read is refused",
              False, "it was written")
    except sf.StaleEdit as exc:
        check("a save over a file that changed since it was read is refused",
              json.loads(path.read_text(encoding="utf-8")) == marked, str(exc)[:60])
    try:
        sf.write_doc(path, TIMELINE, base_rev="")
        check("'create' over an existing file is refused", False, "it was written")
    except sf.StaleEdit:
        check("'create' over an existing file is refused", True)

    before = path.read_bytes()
    bad = edit(TIMELINE, lambda d: scene_items(d)[0].update(len=0))
    try:
        sf.write_doc(path, bad, base_rev=new_rev)
        check("an invalid document is never written", False, "it was written")
    except configmod.ConfigError:
        check("an invalid document is never written", path.read_bytes() == before)

    try:
        sf.write_doc(sf.path_for(root, "timeline", "other-track"), TIMELINE, base_rev="")
        check("a document cannot be written under another id's name", False,
              "it was written")
    except configmod.ConfigError as exc:
        check("a document cannot be written under another id's name",
              not (root / "timelines" / "other-track.json").exists(), str(exc)[:60])

    # A crash between writing and renaming leaves the old file whole.
    real_replace = os.replace

    def boom(*a, **k):
        raise OSError("power cut")

    os.replace = boom
    try:
        sf.write_doc(path, TIMELINE, base_rev=new_rev)
        check("a crash mid-write leaves the old file", False, "no error")
    except OSError:
        check("a crash mid-write leaves the old file",
              path.read_bytes() == before
              and not any(p.suffix == ".tmp" for p in path.parent.iterdir()))
    finally:
        os.replace = real_replace

    for bad_id in ("../escape", "Fan Drop", "", "a/b"):
        try:
            sf.path_for(root, "routine", bad_id)
            check(f"path_for refuses {bad_id!r}", False, "accepted")
        except ValueError:
            check(f"path_for refuses {bad_id!r}", True)

    # -------------------------------------------------------------------------
    print("\n4. a folder a sync service has been at")
    (root / "timelines" / "synth-128 (conflicted copy 2026-09-30).json").write_text(
        json.dumps(TIMELINE), encoding="utf-8")
    (root / "routines" / "fan-drop (1).json").write_text(json.dumps(FAN), encoding="utf-8")
    (root / "routines" / "Fan Drop.json").write_text(json.dumps(FAN), encoding="utf-8")
    (root / "routines" / "broken.json").write_text('{"kind": "klights.routine",',
                                                  encoding="utf-8")
    misnamed = edit(FAN, lambda d: d.update(id="something-else"))
    (root / "routines" / "misnamed.json").write_text(json.dumps(misnamed), encoding="utf-8")
    (root / "routines" / "notes.json.bak").write_text("old", encoding="utf-8")
    f = sf.load_folder(root)
    check("a conflict copy is never loaded, and the notice says what it is",
          any("conflicted copy" in w and "not loaded" in w for w in f.warnings)
          and any("fan-drop (1)" in w for w in f.warnings), f"{f.warnings[:3]}")
    check("a file name that is not an id is not loaded",
          any("Fan Drop.json" in w for w in f.warnings))
    check("a file that is not JSON is an error naming the line",
          any("broken.json" in e and "line" in e for e in f.errors), f"{f.errors[:2]}")
    check("a file whose id disagrees with its name is an error",
          any("misnamed.json" in e and "something-else" in e for e in f.errors))
    check("and none of it stops the good files loading",
          set(f.routines) == {"idle-orbit", "verse-sweep", "build-rise", "fan-drop"}
          and "synth-128" in f.timelines, f"{sorted(f.routines)}")

    # -------------------------------------------------------------------------
    print("\n5. warnings that need two files to see")
    for p in list((root / "routines").iterdir()):
        if p.name not in {"idle-orbit.json", "verse-sweep.json", "build-rise.json",
                          "fan-drop.json"}:
            p.unlink()
    (root / "timelines" / "synth-128 (conflicted copy 2026-09-30).json").unlink()
    tl = json.loads(path.read_text(encoding="utf-8"))
    tl["grid_rev"] = "g:0ld0ld"
    scene_items(tl)[1].update(routine="no-such-routine")
    scene_items(tl)[2].update(variation="enormous")
    scene_items(tl)[3]["params"].update(glow=1)
    scene_items(tl).append({"id": "late", "kind": "routine", "at": 500, "len": 8,
                            "routine": "idle-orbit"})
    tl["rows"].append(lane("param.width", [[0, 40], [64, 500]], "wide"))
    sf.write_doc(path, tl)
    orbit_path = sf.path_for(root, "routine", "idle-orbit")
    sf.write_doc(orbit_path, edit(json.loads(orbit_path.read_text(encoding="utf-8")),
                                  lambda d: d["rows"].append(
                                      lane("param.color", [[0, "@accent"]], "own"))))
    orphan = edit(TIMELINE, lambda d: d.update(track="not-prepped"))
    sf.write_doc(sf.path_for(root, "timeline", "not-prepped"), orphan, base_rev="")
    f = sf.load_folder(root)
    w = "\n".join(f.warnings)
    check("a timeline drawn on a grid that has since changed",
          "re-gridded" in w and "g:0ld0ld" in w)
    check("a clip naming a routine that is not there", "no-such-routine" in w)
    check("a variation the routine does not have", "'enormous'" in w)
    check("a param the routine does not have", "'glow'" in w)
    check("an item starting after the track ends", "'late'" in w and "after the track ends" in w)
    check("a timeline for a track that was never prepped", "not-prepped" in w)
    check("a timeline param lane past a placed routine's declared max",
          "row 'wide' point 1: routine 'fan-drop' $width" in w)
    check("a use setting a param the routine's own lane drives",
          "automates on its own lane 'own'" in w and "never heard" in w)
    check("all of them warnings, none errors -- the other half may not have synced yet",
          f.errors == [], f"{f.errors[:2]}")

    # -------------------------------------------------------------------------
    print("\n6. init")
    fresh = tmp / "fresh"
    done = sf.init(fresh)
    check("init makes every subfolder",
          all((fresh / sub).is_dir() for sub in sf.SUBDIR.values()), f"{done}")
    check("copies every show schema for editor completion",
          all((fresh / "schemas" / f"{k}.schema.json").is_file() for k in sf.KINDS))
    made = sf.load_folder(fresh)
    check("and writes a show.json that loads, with Follow DJ disarmed",
          made.errors == [] and made.show["follow"]["default"] == "disarmed",
          f"{made.errors}")
    shown = json.loads((fresh / "show.json").read_text(encoding="utf-8"))
    check("whose $schema resolves from where it is",
          (fresh / shown["$schema"]).is_file(), shown["$schema"])
    shown["fallback"] = "operator"
    (fresh / "show.json").write_text(json.dumps(shown), encoding="utf-8")
    sf.init(fresh)
    check("init again never overwrites a document",
          json.loads((fresh / "show.json").read_text())["fallback"] == "operator")
    check("a new document's $schema resolves from its subfolder",
          (fresh / "routines" / sf.schema_ref("routine")).resolve().is_file())

    # -------------------------------------------------------------------------
    print("\n6b. the audio a lane follows")
    import base64

    def waveform(**parts):
        wave = sf.new_doc("waveform", track="synth-128", **{
            k: {"format": fmt, "rate": 150,
                "data": base64.b64encode(data).decode("ascii")}
            for k, (fmt, data) in parts.items()})
        sf.write_doc(sf.path_for(root, "waveform", "synth-128"), wave, "waveform")

    wave_path = sf.path_for(root, "waveform", "synth-128")
    wave_path.unlink(missing_ok=True)
    check("a timeline with no lane following the audio asks for nothing -- no "
          "waveform is read, and none is missed",
          sf.timeline_audio(root, TIMELINE, TRACK) == (None, []))
    bass = with_audio(TIMELINE, "size", {"band": "low", "depth": 0.5})
    audio, said = sf.timeline_audio(root, bass, TRACK)
    check("a lane following a track with no waveform is a warning, with the "
          "way out: the lane plays its points alone",
          audio is None and len(said) == 1 and "row 'size' follows" in said[0]
          and "prep the track" in said[0] and "points alone" in said[0], f"{said}")
    waveform(detail=("pwv3", bytes([20]) * 600))
    audio, said = sf.timeline_audio(root, bass, TRACK)
    check("an analysis with only the overall level cannot give the bass, and "
          "says to follow \"all\"",
          audio is not None and audio.bands == ("all",) and len(said) == 1
          and "only its overall level" in said[0], f"{said}")
    waveform(bands=("pwv7", bytes([90, 40, 10]) * 900))
    audio, said = sf.timeline_audio(root, bass, TRACK)
    check("the three-band analysis gives every band, with nothing to say",
          audio is not None and audio.bands == ("low", "mid", "high", "all")
          and said == [] and audio.exact)
    check("and is decoded once: the same audio for as long as the file stands",
          sf.timeline_audio(root, bass, TRACK)[0] is audio)
    waveform(bands=("pwv7", bytes([90, 40, 10]) * 1200))
    check("a waveform prepped again is read again",
          sf.timeline_audio(root, bass, TRACK)[0] is not audio)
    wave_path.write_text("{not json", encoding="utf-8")
    audio, said = sf.timeline_audio(root, bass, TRACK)
    check("a waveform that does not load is a warning too, never an error",
          audio is None and len(said) == 1 and "does not load" in said[0], f"{said}")
    wave_path.unlink()

    # -------------------------------------------------------------------------
    print("\n7. finding the folder")
    local = tmp / "klights.local.json"
    local.write_text(json.dumps({"show_dir": str(tmp / "from-local")}), encoding="utf-8")
    env = {sf.ENV_VAR: str(tmp / "from-env")}
    check("--show-dir wins over everything",
          sf.resolve_show_dir("/x/cli", env, local) == Path("/x/cli"))
    check("then the environment",
          sf.resolve_show_dir(None, env, local) == tmp / "from-env")
    check("then klights.local.json",
          sf.resolve_show_dir(None, {}, local) == tmp / "from-local")
    check("and nothing means no show folder -- the engine runs as it did",
          sf.resolve_show_dir(None, {}, tmp / "missing.json") is None)
    local.write_text("{not json", encoding="utf-8")
    check("a broken local file is no settings, not a crash",
          sf.resolve_show_dir(None, {}, local) is None)
    check("a folder that does not exist is an error, not an exception",
          sf.load_folder(tmp / "nowhere").errors != [])
finally:
    shutil.rmtree(tmp, ignore_errors=True)


# -----------------------------------------------------------------------------
print("\n8. validate never raises")
# The designer validates on every drag and MCP on every edit; an exception from
# a malformed document is a crashed request instead of a list of problems. So
# every value at every path in every example document is replaced with every
# wrong type, and validate must return each time.


def paths(node, prefix=()):
    yield prefix
    if isinstance(node, dict):
        for k, v in node.items():
            yield from paths(v, prefix + (k,))
    elif isinstance(node, list):
        for i, v in enumerate(node):
            yield from paths(v, prefix + (i,))


def replaced(doc, path, value):
    d = copy.deepcopy(doc)
    if not path:
        return value
    node = d
    for key in path[:-1]:
        node = node[key]
    node[path[-1]] = value
    return d


raised = []
tried = 0
for kind, doc in (("track", TRACK), ("timeline", TIMELINE), ("routine", FAN),
                  ("template_set", CLUB), ("show", SHOW)):
    for path in paths(doc):
        for junk in (None, 1, -5, "x", "$nope", [], {}, True, [1, 2], {"a": 1}):
            tried += 1
            try:
                sf.validate(kind, replaced(doc, path, junk))
            except Exception as exc:                # noqa: BLE001
                raised.append(f"{kind} {'/'.join(map(str, path))} = {junk!r}: "
                              f"{type(exc).__name__}: {exc}")
check(f"validate returned for all {tried} mutated documents",
      raised == [], "\n      ".join(raised[:5]))


# -----------------------------------------------------------------------------
print("\n9. colours")
for good in ("@primary", "@accent", "#ff2d6f", [1, 0, 0.5], "MH Pink"):
    check(f"a colour: {good!r}", sf.color_problem(good) is None)
for bad in ("@tertiary", "#ff2d6", [1, 0], [2, 0, 0], [True, 0, 0], "", 7):
    check(f"not a colour: {bad!r}", sf.color_problem(bad) is not None)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("showfiles: all checks pass")
