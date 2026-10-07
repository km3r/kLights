"""
Tests for the patch editor's rules (`engine/patch.py`), called directly.

The MCP suite drives a few of these over stdio and the server suite drives a
few more over a socket, but both go through a surface. This suite asks the
rules themselves the questions a load-in actually raises -- the 512 boundary,
a fixture in a hole one channel too small, the same channel in two universes,
an autopatch that runs out of room -- because "what is a legal patch" is
answered here, once, for every surface that edits a rig.

It also holds the promise `check_addresses` only makes in a docstring: that it
and `shared/tools/validate_patch.py` agree. Two independent implementations of
one rule, compared on a few hundred random patches, so they agree by test
rather than by care.

Every write goes to a throwaway copy: the events, venues and fixture folders
are redirected before anything runs. Safe to patch module globals because each
suite runs in its own interpreter (see engine/tests/__main__.py).

Run: python engine/tests/test_patch.py
"""

import atexit
import contextlib
import copy
import csv
import io
import json
import random
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "shared" / "tools"))

from engine import config as configmod  # noqa: E402
from engine import patch as patchmod  # noqa: E402
from engine import rig as rigmod  # noqa: E402
import validate_patch  # noqa: E402

TMP = Path(tempfile.mkdtemp(prefix="klights-patch-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)
_SKIP = shutil.ignore_patterns("__pycache__", "*.bak", ".engine.lock", "backups")
shutil.copytree(REPO / "events" / "despacio", TMP / "events" / "despacio", ignore=_SKIP)
shutil.copytree(REPO / "shared" / "venues", TMP / "venues", ignore=_SKIP)
shutil.copytree(REPO / "shared" / "fixtures", TMP / "fixtures", ignore=_SKIP)
patchmod.EVENTS = TMP / "events"
patchmod.VENUES = rigmod.VENUE_LIBRARY = TMP / "venues"
patchmod.FIXTURES = TMP / "fixtures"
EVENT = TMP / "events" / "despacio"

# Only the folder this suite controls: the default roots include a machine-local
# QLC+ install, and a test that passes because of what is installed on one
# laptop is not a test.
LIB = rigmod.ProfileLibrary([TMP / "fixtures"])

MH = dict(manufacturer="MingJie", model="MJ-OS-018 60W Beam", mode="11 Channel")
PIN = dict(manufacturer="UKing", model="ZQ-B93 Pinspot RGBW", mode="6-channel")
BAR90 = dict(manufacturer="YeeSite", model="60W RGB Pixel Light Bar",
             mode="90 Channel (Pixel)")

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label + (f"  -- {detail}" if detail else ""))


ORIGINAL = configmod.load(EVENT / "rig.json", configmod.RIG)


def base() -> dict:
    """The reference rig as it was before this suite wrote anything."""
    return copy.deepcopy(ORIGINAL)


def empty() -> dict:
    cfg = base()
    cfg["fixtures"] = []
    return cfg


def entry(name, address, universe=0, kind=MH, fid=0, **extra):
    return {"id": fid, "name": name, "universe": universe, "address": address,
            "tags": [], **kind, **extra}


def addresses(cfg) -> dict:
    return {f["name"]: (f.get("universe", 0), f["address"]) for f in cfg["fixtures"]}


def any_has(items, *needles) -> bool:
    return any(all(n in s for n in needles) for s in items)


def check_cli(label, ok, output=""):
    """check(), with a command's output attached only when it failed -- a passing
    check that prints a screen of CLI output buries the next failure."""
    check(label, ok, "" if ok else output[-400:])


def run_cli(*argv) -> tuple[int, str]:
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        try:
            code = patchmod.main(list(argv))
        except SystemExit as exc:
            code = exc.code if isinstance(exc.code, int) else 2
    return code, out.getvalue()


print("\n1. a Result: errors refuse, warnings advise")
r = patchmod.Result({"a": 1})
check("no errors is ok", r.ok and r.as_dict() == {"ok": True, "errors": [], "warnings": []})
r = patchmod.Result({}, ["bad"], ["hm"])
check("any error is not ok, and as_dict says both",
      not r.ok and r.as_dict() == {"ok": False, "errors": ["bad"], "warnings": ["hm"]})


print("\n2. check_addresses: overlaps and range, per universe")
check("the reference rig is clean", patchmod.check_addresses(base(), LIB) == [])

cfg = empty()
cfg["fixtures"] = [entry("A", 1), entry("B", 11, fid=1)]
errs = patchmod.check_addresses(cfg, LIB)
check("one shared channel (11) is an overlap naming both",
      len(errs) == 1 and "B" in errs[0] and "overlaps A" in errs[0], f"{errs}")

cfg["fixtures"] = [entry("A", 1), entry("B", 12, fid=1)]
check("touching but not sharing is fine", patchmod.check_addresses(cfg, LIB) == [])

cfg["fixtures"] = [entry("A", 1, universe=0), entry("B", 1, universe=1, fid=1)]
check("the same channels in two universes are not a clash",
      patchmod.check_addresses(cfg, LIB) == [])

cfg["fixtures"] = [entry("A", 1), entry("B", 12, fid=1), entry("C", 5, kind=BAR90, fid=2)]
errs = patchmod.check_addresses(cfg, LIB)
check("one fixture across two others reports each of them once",
      len(errs) == 2 and any_has(errs, "C", "A") and any_has(errs, "C", "B"), f"{errs}")

cfg["fixtures"] = [entry("A", 502)]
check("11 channels at 502 end exactly on 512: legal",
      patchmod.check_addresses(cfg, LIB) == [])
cfg["fixtures"] = [entry("A", 503)]
errs = patchmod.check_addresses(cfg, LIB)
check("at 503 the last channel is 513: out of range, and says which channels",
      len(errs) == 1 and "503-513" in errs[0] and "1-512" in errs[0], f"{errs}")
cfg["fixtures"] = [entry("A", 0)]
errs = patchmod.check_addresses(cfg, LIB)
check("address 0 is out of range (DMX starts at 1)",
      len(errs) == 1 and "0-10" in errs[0], f"{errs}")

cfg["fixtures"] = [entry("A", 600), entry("B", 600, fid=1)]
errs = patchmod.check_addresses(cfg, LIB)
check("two fixtures wholly past 512 are each out of range, and not an 'overlap' "
      "of channels that do not exist",
      len(errs) == 2 and not any("overlaps" in e for e in errs), f"{errs}")

cfg["fixtures"] = [entry("A", 1, manufacturer_override=True)]
cfg["fixtures"][0]["model"] = "No Such Thing"
check("a fixture whose profile cannot be found is skipped, not a crash",
      patchmod.check_addresses(cfg, LIB) == [])


print("\n3. first_free: the lowest gap that fits")
check("an empty universe starts at 1", patchmod.first_free(empty(), 11, lib=LIB) == 1)
check("the reference rig's next 11 channels start after the last pinspot (57)",
      patchmod.first_free(base(), 11, lib=LIB) == 57)
holed = base()
holed["fixtures"] = [f for f in holed["fixtures"] if f["name"] != "Moving Head #2"]
check("a removed head leaves a hole that the same fixture refills",
      patchmod.first_free(holed, 11, lib=LIB) == 12)
check("but a fixture one channel too big skips the hole",
      patchmod.first_free(holed, 12, lib=LIB) == 57)
check("a smaller fixture takes the start of the hole",
      patchmod.first_free(holed, 6, lib=LIB) == 12)
check("another universe is a fresh 512",
      patchmod.first_free(base(), 11, universe=3, lib=LIB) == 1)
check("512 channels fit an empty universe exactly",
      patchmod.first_free(empty(), 512, lib=LIB) == 1)
check("513 never fit", patchmod.first_free(empty(), 513, lib=LIB) is None)
full = empty()
full["fixtures"] = [entry(f"bar{i}", 1 + 90 * i, kind=BAR90, fid=i) for i in range(5)]
check("five 90-channel bars leave 62: room for 62, not 63",
      patchmod.first_free(full, 62, lib=LIB) == 451
      and patchmod.first_free(full, 63, lib=LIB) is None)


print("\n4. add_fixture")
before = base()
snapshot = copy.deepcopy(before)
res = patchmod.add_fixture(before, name="Moving Head #5", **MH, tags=["movers"],
                           position={"x": 1.0, "y": 2.0, "z": 3.0}, beam_deg=8,
                           hold={"reset": 0}, notes="spare", lib=LIB)
added = res.config["fixtures"][-1] if res.config.get("fixtures") else {}
check("adds at the first gap with the next id", res.ok and added.get("address") == 57
      and added.get("id") == 6, f"{res.errors} {added}")
check("and keeps what it was given", added.get("tags") == ["movers"]
      and added.get("position") == {"x": 1.0, "y": 2.0, "z": 3.0}
      and added.get("beam_deg") == 8.0 and added.get("hold") == {"reset": 0}
      and added.get("notes") == "spare", f"{added}")
check("the caller's config is never mutated", before == snapshot)
check("a fifth MingJie when we own four is a warning, not a refusal",
      res.ok and any_has(res.warnings, "patching 5", "own 4"), f"{res.warnings}")

res = patchmod.add_fixture(base(), name="Pinspot #3", **PIN, lib=LIB)
check("a pinspot carries no optional keys it was not given",
      res.ok and not {"position", "beam_deg", "hold", "notes"} & set(res.config["fixtures"][-1]),
      f"{res.config['fixtures'][-1]}")
check("and a third pinspot when we own two warns too",
      any_has(res.warnings, "patching 3", "own 2"), f"{res.warnings}")

res = patchmod.add_fixture(base(), name="Moving Head #1", **MH, lib=LIB)
check("a duplicate name is refused, because looks and calibration key on it",
      not res.ok and "already patched" in res.errors[0]
      and len(res.config["fixtures"]) == 6)

res = patchmod.add_fixture(base(), name="X", manufacturer="Nobody", model="Ghost",
                           mode="1ch", lib=LIB)
check("an unknown profile is refused and points at list_profiles and import_profile",
      not res.ok and "list_profiles" in res.errors[0] and "import_profile" in res.errors[0])

res = patchmod.add_fixture(base(), name="X", **{**MH, "mode": "13 Channel"}, lib=LIB)
check("an unknown mode is refused and lists the modes the profile has",
      not res.ok and "9 Channel" in res.errors[0] and "11 Channel" in res.errors[0],
      f"{res.errors}")

res = patchmod.add_fixture(base(), name="X", **PIN, address=40, lib=LIB)
check("an explicit address on top of a head is refused",
      not res.ok and any_has(res.errors, "X", "overlaps Moving Head #4"), f"{res.errors}")

res = patchmod.add_fixture(base(), name="X", **PIN, address=1, universe=1, lib=LIB)
check("the same address in another universe is accepted",
      res.ok and res.config["fixtures"][-1]["universe"] == 1, f"{res.errors}")

res = patchmod.add_fixture(full, name="bar6", **BAR90, lib=LIB)
check("no gap big enough is a refusal that says how many channels were needed",
      not res.ok and "no free run of 90" in res.errors[0], f"{res.errors}")

res = patchmod.add_fixture(empty(), name="first", **MH, lib=LIB)
check("the first fixture in an empty rig is id 0 at address 1",
      res.ok and res.config["fixtures"][0]["id"] == 0
      and res.config["fixtures"][0]["address"] == 1)

res = patchmod.add_fixture(base(), name="Laser", manufacturer="Amazon",
                           model="DerbyLaserParty", mode="default", lib=LIB)
check("a profile we own is not an inventory warning",
      res.ok and not any("Amazon" in w for w in res.warnings), f"{res.warnings}")


print("\n5. remove_fixture")
res = patchmod.remove_fixture(base(), "Moving Head #2", lib=LIB)
check("removing a mover succeeds", res.ok and "Moving Head #2" not in addresses(res.config))
check("but warns that the head order changed, and names the drift check",
      any_has(res.warnings, "head order", "Drift check"), f"{res.warnings}")
res = patchmod.remove_fixture(base(), "Pinspot #1", lib=LIB)
check("removing a pinspot (no pan/tilt) carries no head-order warning",
      res.ok and res.warnings == [], f"{res.warnings}")
unplaced = base()
del unplaced["fixtures"][0]["position"]
res = patchmod.remove_fixture(unplaced, "Moving Head #1", lib=LIB)
check("nor does a mover that was never placed (it was not a geometry head)",
      res.ok and res.warnings == [], f"{res.warnings}")
res = patchmod.remove_fixture(base(), "Moving Head #9", lib=LIB)
check("removing something not patched is an error", not res.ok and "no fixture" in res.errors[0])


print("\n6. set_address, set_tags, set_position")
res = patchmod.set_address(base(), "Pinspot #2", 100, lib=LIB)
check("readdressing to free channels works",
      res.ok and addresses(res.config)["Pinspot #2"] == (0, 100))
res = patchmod.set_address(base(), "Pinspot #2", 44, lib=LIB)
check("readdressing onto a head's last channel is refused",
      not res.ok and any_has(res.errors, "Pinspot #2", "Moving Head #4"), f"{res.errors}")
res = patchmod.set_address(base(), "Pinspot #2", 1, universe=2, lib=LIB)
check("readdressing into another universe works",
      res.ok and addresses(res.config)["Pinspot #2"] == (2, 1))
res = patchmod.set_address(base(), "Pinspot #2", 508, lib=LIB)
check("and past the end of a universe is refused", not res.ok and "508-513" in res.errors[0],
      f"{res.errors}")
res = patchmod.set_address(base(), "Nobody", 1, lib=LIB)
check("readdressing something not patched is an error", not res.ok)

res = patchmod.set_tags(base(), "Pinspot #1", ["specials"], lib=LIB)
check("retagging one of two pinspots leaves the group alive: no warning",
      res.ok and res.warnings == [], f"{res.warnings}")
step = patchmod.set_tags(res.config, "Pinspot #2", ["specials"], lib=LIB)
check("retagging the last one warns that the group's looks will do nothing",
      step.ok and any_has(step.warnings, "'pinspots'", "do nothing"), f"{step.warnings}")
res = patchmod.set_tags(base(), "Moving Head #1", [], lib=LIB)
check("clearing one head's tags keeps both mover groups alive", res.ok and res.warnings == [])
res = patchmod.set_tags(base(), "Nobody", ["x"], lib=LIB)
check("retagging something not patched is an error", not res.ok)

res = patchmod.set_position(base(), "Moving Head #1", 1, 2, 3, lib=LIB)
pos = res.config["fixtures"][0]["position"]
check("a position is stored as floats in millimetres",
      res.ok and pos == {"x": 1.0, "y": 2.0, "z": 3.0}
      and all(isinstance(v, float) for v in pos.values()))
check("and always warns that calibration was measured somewhere else",
      any_has(res.warnings, "Drift check"))
res = patchmod.set_position(base(), "Nobody", 0, 0, 0, lib=LIB)
check("moving something not patched is an error", not res.ok)


print("\n7. autopatch: end to end, no gaps, and says what moved")
res = patchmod.autopatch(base(), lib=LIB)
check("an already-contiguous rig: nothing moved", res.ok and res.warnings == ["nothing moved"],
      f"{res.warnings}")
res = patchmod.autopatch(holed, lib=LIB)
check("a hole is closed: everything after it moves down by 11",
      res.ok and addresses(res.config)["Moving Head #3"] == (0, 12)
      and addresses(res.config)["Pinspot #2"] == (0, 40), f"{addresses(res.config)}")
check("and the warning is the whole before/after map, counted",
      res.warnings[0].startswith("4 fixture(s) re-addressed")
      and "Moving Head #3: u0/23 -> u0/12" in res.warnings, f"{res.warnings}")
res = patchmod.autopatch(base(), start=101, lib=LIB)
check("a start offset shifts the whole run",
      res.ok and addresses(res.config)["Moving Head #1"] == (0, 101)
      and addresses(res.config)["Pinspot #2"] == (0, 151))
res = patchmod.autopatch(base(), universe=1, lib=LIB)
check("a universe override moves everything there",
      res.ok and {u for u, _ in addresses(res.config).values()} == {1})
res = patchmod.autopatch(base(), start=500, lib=LIB)
check("a start too late to fit is refused, naming where it ran out (head 1 fits "
      "at 500-510; head 2 would end at 521)",
      not res.ok and "ran out of channels at Moving Head #2" in res.errors[0], f"{res.errors}")
split = empty()
split["fixtures"] = [entry("A", 300, 0), entry("B", 300, 1, fid=1), entry("C", 1, 0, fid=2)]
res = patchmod.autopatch(split, lib=LIB)
check("each universe is packed on its own, in file order",
      res.ok and addresses(res.config) == {"A": (0, 1), "B": (1, 1), "C": (0, 12)},
      f"{addresses(res.config)}")
broken = base()
broken["fixtures"][2]["mode"] = "No Mode"
res = patchmod.autopatch(broken, lib=LIB)
check("a fixture it cannot size stops it, rather than packing around a guess",
      not res.ok and "cannot size" in res.errors[0] and "Moving Head #3" in res.errors[0])


print("\n8. validation order: schema before addresses, then profiles")
bad = base()
bad["fixtures"][0]["address"] = "one"
res = patchmod.set_tags(bad, "Moving Head #2", ["movers"], lib=LIB)
check("a string where a number belongs is a schema error, not address nonsense",
      not res.ok and not any("overlaps" in e or "outside" in e for e in res.errors),
      f"{res.errors}")
bad = base()
bad["fixtures"][1]["mode"] = "13 Channel"
res = patchmod.set_tags(bad, "Pinspot #1", ["pinspots"], lib=LIB)
check("a mode the profile lacks is reported with the modes it has",
      not res.ok and any_has(res.errors, "Moving Head #2", "no mode", "9 Channel"),
      f"{res.errors}")
bad = base()
bad["fixtures"][4]["model"] = "Gone"
res = patchmod.set_tags(bad, "Pinspot #2", ["pinspots"], lib=LIB)
check("a profile that is not on disk is reported with where it looked",
      not res.ok and any_has(res.errors, "Pinspot #1", "no .qxf", "fixtures"), f"{res.errors}")


print("\n9. venues and new events")
res = patchmod.set_venue(base(), "despacio-room", lib=LIB)
check("pointing at a room that exists works, with a re-calibrate warning",
      res.ok and any_has(res.warnings, "Re-calibrate"))
res = patchmod.set_venue(base(), "the-moon", lib=LIB)
check("a room that does not exist is refused, listing the ones that do",
      not res.ok and "despacio-room" in res.errors[0])
check("list_venues reads the room's size", patchmod.list_venues() == [
    {"name": "despacio-room", "title": "despacio room", "width": 18288,
     "depth": 18288, "height": 6900}], f"{patchmod.list_venues()}")
(TMP / "venues" / "broken.json").write_text("{ not json", encoding="utf-8")
check("a room file that does not load is skipped, not fatal",
      [v["name"] for v in patchmod.list_venues()] == ["despacio-room"])
(TMP / "venues" / "broken.json").unlink()

res = patchmod.new_event("despacio", "despacio-room")
check("scaffolding over an event that has a rig is refused",
      not res.ok and "already has a rig.json" in res.errors[0])
res = patchmod.new_event("despacio", "despacio-room", force=True)
check("unless forced", res.ok)
res = patchmod.new_event("fresh", "the-moon")
check("scaffolding against a missing room is refused", not res.ok and "no room" in res.errors[0])
res = patchmod.new_event("fresh", "despacio-room")
check("a new event's rig passes the real schema, with no fixtures and a warning "
      "that says so", res.ok and res.config["fixtures"] == []
      and res.config["name"] == "fresh" and any_has(res.warnings, "no fixtures"))
try:
    configmod.validate(res.config, configmod.RIG, Path("rig.json"))
    runnable = True
except configmod.ConfigError as exc:
    runnable = exc.problems
check("it is not runnable yet -- the engine's own schema refuses an empty rig",
      runnable == ["fixtures must not be empty"], f"{runnable}")
try:
    configmod.validate(res.config, patchmod.RIG_DRAFT, Path("rig.json"))
    draft_ok = True
except configmod.ConfigError as exc:
    draft_ok = exc.problems
check("but it is a legal draft: every other rule holds", draft_ok is True, f"{draft_ok}")
grown = patchmod.add_fixture(res.config, name="first", **MH, lib=LIB)
check("and a fixture can be added to it straight away",
      grown.ok and grown.config["fixtures"][0]["address"] == 1, f"{grown.errors}")


print("\n10. profiles: listing and importing")
profiles = {(p["manufacturer"], p["model"]): p for p in patchmod.list_profiles(LIB)}
check("list_profiles offers every .qxf in the folder with its modes' sizes",
      profiles[("MingJie", "MJ-OS-018 60W Beam")]["modes"] == {"9 Channel": 9, "11 Channel": 11}
      and len(profiles) == 7, f"{sorted(profiles)}")

src = TMP / "incoming"
src.mkdir()
res = patchmod.import_profile(src / "missing.qxf")
check("importing a file that is not there is an error", not res.ok and "no such file" in res.errors[0])
(src / "notes.txt").write_text("hello", encoding="utf-8")
res = patchmod.import_profile(src / "notes.txt")
check("importing something that is not a .qxf is an error", not res.ok and "not a .qxf" in res.errors[0])
(src / "Junk.qxf").write_text("<FixtureDefinition><broken", encoding="utf-8")
res = patchmod.import_profile(src / "Junk.qxf")
check("a .qxf that does not parse is refused and not copied",
      not res.ok and "not a usable" in res.errors[0]
      and not (TMP / "fixtures" / "Junk.qxf").exists(), f"{res.errors}")
same = src / "UKing-Par-36-Custom.qxf"
shutil.copy2(REPO / "shared" / "fixtures" / same.name, same)
res = patchmod.import_profile(same, LIB)
check("an identical file already there is a no-op that says so",
      res.ok and any_has(res.warnings, "already there, identical")
      and not (TMP / "fixtures" / (same.name + ".bak")).exists())
same.write_bytes(same.read_bytes().replace(b"Par 36 Custom", b"Par 36 Custom"))  # same content
res = patchmod.import_profile(same, LIB)
check("(and still identical after an idempotent rewrite)", res.ok)
text = same.read_text(encoding="utf-8")
same.write_text(text.replace("</FixtureDefinition>", "<!-- v2 --></FixtureDefinition>"),
                encoding="utf-8")
res = patchmod.import_profile(same, LIB)
check("a changed file overwrites, keeping the previous one as .bak",
      res.ok and (TMP / "fixtures" / (same.name + ".bak")).exists()
      and any_has(res.warnings, "overwritten"), f"{res.warnings}")
renamed = src / "Brand-New-Par.qxf"
renamed.write_text(text.replace("Par 36 Custom", "Brand New Par"), encoding="utf-8")
res = patchmod.import_profile(renamed, LIB)
check("a new profile is copied in and reported patchable, with its modes",
      res.ok and res.config.get("model") == "Brand New Par"
      and res.config.get("modes") == ["5 Channel"]
      and (TMP / "fixtures" / renamed.name).exists(), f"{res.config} {res.errors}")
fresh_lib = rigmod.ProfileLibrary([TMP / "fixtures"])
grown = patchmod.add_fixture(base(), name="New Par", manufacturer="UKing",
                             model="Brand New Par", mode="5 Channel", lib=fresh_lib)
check("and is then patchable, with an inventory warning that we do not own it",
      grown.ok and any_has(grown.warnings, "Brand New Par", "not in shared/inventory.json"),
      f"{grown.errors} {grown.warnings}")


print("\n11. files: write, describe, lock")
res = patchmod.add_fixture(base(), name="Pinspot #3", **PIN, lib=LIB)
path = patchmod.write_rig(str(EVENT), res.config)
check("write_rig takes an event path and writes rig.json there", path == EVENT / "rig.json")
check("and what it wrote loads through the real loader",
      [f.name for f in rigmod.load_rig(EVENT).fixtures][-1] == "Pinspot #3")
patchmod.write_rig(str(EVENT), base())
check("a second write keeps a .bak of the first", (EVENT / "rig.json.bak").exists())
info = patchmod.describe(str(EVENT))
check("describe goes through the real loader: channel counts from the profile",
      info["fixtures"][-1]["channels"] == 6 and info["fixtures"][0]["is_mover"]
      and info["fixtures"][0]["head"] == 0 and info["fixtures"][-1]["head"] is None,
      f"{info['fixtures'][-1]}")
check("event_dir takes a name or a path",
      patchmod.event_dir(str(EVENT)) == EVENT and patchmod.event_dir("despacio") == EVENT)
check("an event with no lock is held by nobody", patchmod.held_by(str(EVENT)) is None)
lock = patchmod.lock_path(str(EVENT))
lock.write_text("engine pid 4242\n", encoding="utf-8")
check("a lock names who holds it", patchmod.held_by(str(EVENT)) == "engine pid 4242")
lock.write_text("", encoding="utf-8")
check("an empty lock is still held", patchmod.held_by(str(EVENT)) == "an engine")
lock.unlink()


print("\n12. the CLI: dry run unless --write, and never under a live show")
ev = str(EVENT)
before_text = (EVENT / "rig.json").read_text(encoding="utf-8")
code, out = run_cli("--event", ev, "describe")
check_cli("describe lists every fixture with its channel range",
      code == 0 and "Moving Head #1" in out and "ch 1-11" in out, out[-200:])
code, out = run_cli("profiles")
check("profiles lists modes with channel counts", code == 0 and "11 Channel (11ch)" in out)
code, out = run_cli("venues")
check_cli("venues lists rooms in metres", code == 0 and "18.3 x 18.3 m" in out, out)
code, out = run_cli("--event", ev, "add", "--name", "P3", "--manufacturer", "UKing",
                    "--model", "ZQ-B93 Pinspot RGBW", "--mode", "6-channel",
                    "--tags", "pinspots, specials", "--position", "1,2,3")
check_cli("add is a dry run by default and writes nothing",
      code == 0 and "dry run" in out
      and (EVENT / "rig.json").read_text(encoding="utf-8") == before_text, out)
lock.write_text("the show", encoding="utf-8")
code, out = run_cli("--event", ev, "address", "--name", "Pinspot #2", "--address", "200",
                    "--write")
check_cli("--write is refused while the event is locked, saying by whom",
      code == 1 and "refusing to write: the show" in out
      and (EVENT / "rig.json").read_text(encoding="utf-8") == before_text, out)
lock.unlink()
code, out = run_cli("--event", ev, "address", "--name", "Pinspot #2", "--address", "200",
                    "--write")
check("and allowed once the lock is gone",
      code == 0 and "wrote" in out and patchmod.describe(ev)["fixtures"][-1]["address"] == 200)
code, out = run_cli("--event", ev, "address", "--name", "Pinspot #2", "--address", "1",
                    "--write")
check_cli("a refused edit returns 1 and writes nothing",
      code == 1 and "nothing written" in out
      and patchmod.describe(ev)["fixtures"][-1]["address"] == 200, out)
code, out = run_cli("--event", ev, "tags", "--name", "Pinspot #1", "--tags", "a,b")
check("tags dry-runs", code == 0 and "dry run" in out)
code, out = run_cli("--event", ev, "position", "--name", "Pinspot #1", "--position", "1,2")
check_cli("a position that is not x,y,z is refused before anything happens",
      code != 0, out)
code, out = run_cli("--event", ev, "position", "--name", "Pinspot #1", "--position", "1,2,3")
check("a good position dry-runs with the drift note", code == 0 and "Drift check" in out)
code, out = run_cli("--event", ev, "remove", "--name", "Moving Head #4")
check("remove dry-runs with the head-order note", code == 0 and "head order" in out)
code, out = run_cli("--event", ev, "autopatch", "--write")
check_cli("autopatch --write closes the gap the move left",
      code == 0 and patchmod.describe(ev)["fixtures"][-1]["address"] == 51, out)
code, out = run_cli("--event", ev, "venue", "--name", "nowhere")
check("venue refuses a room that does not exist", code == 1 and "no room" in out)
code, out = run_cli("import", str(src / "notes.txt"))
check("import of a non-.qxf returns 1", code == 1 and "ERROR" in out)
code, out = run_cli("new", "--name", "pop-up", "--venue", "despacio-room", "--write")
check_cli("new --write scaffolds an event", code == 0
      and (TMP / "events" / "pop-up" / "rig.json").exists(), out)
code, out = run_cli("new", "--name", "pop-up", "--venue", "despacio-room")
check("and will not scaffold over it a second time", code == 1 and "already has" in out)
# The regression: every edit loaded through the engine's run-time schema, which
# refuses an empty rig, so the step new_event's own warning asks for next died
# with a ConfigError -- from the CLI and from the MCP tool alike.
try:
    code, out = run_cli("--event", "pop-up", "add", "--name", "MH", "--manufacturer",
                        "MingJie", "--model", "MJ-OS-018 60W Beam", "--mode",
                        "11 Channel", "--write")
except configmod.ConfigError as exc:
    code, out = -1, str(exc)
check_cli("and the next step, adding its first fixture, works on the scaffold",
      code == 0 and [f.name for f in rigmod.load_rig(TMP / "events" / "pop-up").fixtures]
      == ["MH"], out[-300:])
code, out = run_cli("--event", "pop-up", "remove", "--name", "MH")
check_cli("removing the last fixture is refused: an edit must leave a runnable rig",
      code == 1 and "must not be empty" in out, out)
scaffold = patchmod.new_event("draft", "despacio-room").config
res = patchmod.set_venue(scaffold, "despacio-room", lib=LIB)
check("a draft (no fixtures yet) can still be pointed at a room, and is told it is a draft",
      res.ok and any_has(res.warnings, "no fixtures yet"), f"{res.errors}")
res = patchmod.autopatch(scaffold, lib=LIB)
check("and autopatched (nothing to move), without an empty-rig refusal",
      res.ok and "nothing moved" in res.warnings, f"{res.errors}")


print("\n13. every event's rig.json agrees with its patch sheet")
# The patch sheet is what the venue's crew dials units from; the rig is what the
# engine drives. They are written separately, so this is the only thing that
# notices when one is edited without the other.
for event_path in sorted((REPO / "events").iterdir()):
    sheet, rig_json = event_path / "patch_sheet.csv", event_path / "rig.json"
    if not (sheet.exists() and rig_json.exists()):
        continue
    with open(sheet, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    from_sheet = {(r["name"], int(r["artnet_universe"]), int(r["dmx_start"]),
                   int(r["channel_count"])) for r in rows}
    from_rig = {(f.name, f.universe, f.address, f.channel_count)
                for f in rigmod.load_rig(event_path).fixtures}
    check(f"{event_path.name}: same names, universes, addresses and sizes",
          from_sheet == from_rig, f"only in one: {sorted(from_sheet ^ from_rig)}")


print("\n14. check_addresses and validate_patch.py agree, on random patches")
# Same rule, two implementations (see check_addresses' docstring). Compared on
# what each says is wrong with a patch: the set of (fixture, problem) pairs.
sizes = {"MH": (MH, 11), "PIN": (PIN, 6), "BAR90": (BAR90, 90)}


def verdicts_engine(cfg):
    out = set()
    for err in patchmod.check_addresses(cfg, LIB):
        name = err.split(" ")[0].rstrip(":")
        out.add((name, "range" if "outside" in err else "overlap"))
    return out


def verdicts_sheet(cfg):
    rows = [{"name": f["name"], "dmx_start": str(f["address"]),
             "channel_count": str(sizes[f["kind"]][1]),
             "artnet_universe": str(f["universe"]), "gdtf_profile": "x"}
            for f in cfg["_meta"]]
    errors, _, _ = validate_patch.validate(rows)
    out = set()
    for err in errors:
        if err.startswith("OVERLAP: "):
            out.add((err.split(" ")[1], "overlap"))
        else:
            out.add((err.split(":")[0], "range"))
    return out


rng = random.Random(20261006)
disagreements = []
for trial in range(300):
    cfg = empty()
    cfg["_meta"] = []
    for i in range(rng.randint(1, 8)):
        kind = rng.choice(list(sizes))
        f = {"name": f"F{i}", "address": rng.randint(0, 520), "universe": rng.randint(0, 1),
             "kind": kind}
        cfg["_meta"].append(f)
        cfg["fixtures"].append(entry(f["name"], f["address"], f["universe"],
                                     sizes[kind][0], fid=i))
    a, b = verdicts_engine(cfg), verdicts_sheet(cfg)
    if a != b:
        disagreements.append((trial, sorted(a ^ b)))
check("300 random patches (overlaps, both edges, two universes): same verdicts",
      not disagreements, f"{disagreements[:3]}")


print("\n15. validate_patch.py: the patch-sheet checker on its own")
rows = [{"name": "A", "dmx_start": "1", "channel_count": "11", "artnet_universe": "0",
         "gdtf_profile": ""},
        {"name": "B", "dmx_start": "20", "channel_count": "6", "artnet_universe": "0",
         "gdtf_profile": "x"},
        {"name": "", "dmx_start": "40", "channel_count": "0", "artnet_universe": "0"},
        {"name": "D", "dmx_start": "x", "channel_count": "6", "artnet_universe": "0"}]
errors, warnings, info = validate_patch.validate(rows)
check("an empty name, a zero-channel fixture and a non-number are each an error",
      any_has(errors, "row 4", "empty name") and any_has(errors, "must be > 0")
      and any_has(errors, "D", "bad numeric"), f"{errors}")
check("a missing GDTF profile is a warning", warnings == ["A: no GDTF profile set in patch sheet"],
      f"{warnings}")
check("and the gap between A and B is reported as information",
      info == ["Universe 0: gap of 8 unused channel(s) between A (ends 11) and B (starts 20)"],
      f"{info}")
check("a thirty-channel clash is one line, not thirty",
      len(validate_patch.validate([
          {"name": "X", "dmx_start": "1", "channel_count": "30", "artnet_universe": "0"},
          {"name": "Y", "dmx_start": "1", "channel_count": "30", "artnet_universe": "0"},
      ])[0]) == 1)


def tool(*argv):
    import subprocess
    import os
    proc = subprocess.run([sys.executable, str(REPO / "shared" / "tools" / "validate_patch.py"),
                           *argv], capture_output=True, text=True, encoding="utf-8",
                          env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return proc.returncode, proc.stdout + proc.stderr


code, out = tool("--event", "despacio")
check_cli("the despacio sheet passes from the command line",
      code == 0 and "Result: OK" in out and "Total assigned channels: 56" in out, out[-300:])
code, out = tool()
check_cli("with no arguments every event with a sheet is checked",
      code == 0 and "=== despacio" in out and "=== cosmos26" in out, out[-300:])
bad_sheet = TMP / "bad.csv"
bad_sheet.write_text("name,dmx_start,channel_count,artnet_universe,gdtf_profile\n"
                     "A,1,11,0,x\nB,5,6,0,x\n", encoding="utf-8")
code, out = tool("--csv", str(bad_sheet))
check("a sheet with an overlap fails with exit 1 and says so",
      code == 1 and "OVERLAP: B" in out and "Result: FAIL" in out)
code, out = tool("--csv", str(bad_sheet), "--event", "despacio")
check("--csv and --event together are a usage error", code == 2 and "not both" in out)
code, out = tool("--event", "nope")
check_cli("an unknown event is a usage error that lists the known ones",
      code == 2 and "despacio" in out, out)
code, out = tool("--csv", str(TMP / "missing.csv"))
check_cli("a sheet that is not there fails", code == 1 and "not found" in out, out)


print()
if failures:
    print(f"patch: {len(failures)} FAILED")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("patch: all checks pass")
