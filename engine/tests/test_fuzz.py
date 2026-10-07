"""
Hostile input, generated: what the engine does with everything nobody wrote a
test case for.

Three doors let the outside world into a running show, and each has a promise
that a hand-picked example cannot really test:

  1. The DJ-sync UDP port is unauthenticated by design (engine/sync.py). Its
     promise: one datagram can cost one datagram, never the listener, and
     nothing it lets through is NaN, infinite, unbounded or off-whitelist.
  2. Config and show-folder files are JSON edited by hand at 4pm. The
     validators' promise is twofold: a bad file is a list of problems, never a
     traceback -- and anything they ACCEPT, the engine can actually load.
     The second half is the one that matters, because it is the reason the
     validators exist.
  3. Every console on the WebSocket sends commands. The promise: after ANY
     sequence of them, the show still renders every frame, the musical clock
     is still a number, and the snapshot every phone parses is still strict
     JSON. Python's json.loads accepts NaN and Infinity, so a client can send
     them even though a browser never would -- and a NaN that reaches the
     snapshot is written as a bare `NaN`, which JSON.parse rejects, freezing
     every console at once.

Everything is seeded, so a failure reproduces: the detail names the seed and
the input. The command fuzz reads the field names each `_cmd_*` handler uses
straight from engine/server.py, so it reaches past the first KeyError without
this file keeping a list of 82 commands in step by hand.

Run: python engine/tests/test_fuzz.py
"""

import atexit
import copy
import json
import math
import os
import random
import re
import shutil
import struct
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import auto as autom  # noqa: E402
from engine import config as configmod  # noqa: E402
from engine import cues as cuesmod  # noqa: E402
from engine import library as libmod  # noqa: E402
from engine import patch as patchmod  # noqa: E402
from engine import rig as rigmod  # noqa: E402
from engine import showfiles  # noqa: E402
from engine import state as statemod  # noqa: E402
from engine import sync  # noqa: E402
from engine import timeline as timelinemod  # noqa: E402
from engine import tracktime  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label + (f"  -- {detail}" if detail else ""))


# Fixed seeds, so CI and a failure report always agree on the input. To look
# for new trouble rather than re-check the old, shift every seed at once:
#     KLIGHTS_FUZZ_SEED=7 python engine/tests/test_fuzz.py
SEED = int(os.environ.get("KLIGHTS_FUZZ_SEED", "0"))
if SEED:
    print(f"(every seed shifted by KLIGHTS_FUZZ_SEED={SEED})")

TMP = Path(tempfile.mkdtemp(prefix="klights-fuzz-"))
atexit.register(shutil.rmtree, TMP, ignore_errors=True)

HOSTILE = [None, True, False, 0, -1, 1, 7, 2 ** 63, -(2 ** 63), 10 ** 30, 0.5, -0.5,
           1e308, -1e308, float("nan"), float("inf"), float("-inf"), "", "x", "0",
           "NaN", "x" * 5000, "\x00\x1f", "../../etc/passwd", "🎛️", [], [None], [1, 2],
           {}, {"": None}, {"type": "go"}]

# Exceptions a parser or validator raising would be a bug: they mean the code
# trusted the input's shape. ValueError/ConfigError are how "no" is said.
CRASHES = (TypeError, KeyError, AttributeError, IndexError, ZeroDivisionError,
           OverflowError, RecursionError, UnicodeError, AssertionError)


def short(value, n=160) -> str:
    text = repr(value)
    return text if len(text) <= n else text[:n] + "..."


# -- 1. the sync port ----------------------------------------------------------
print("\n1. the DJ-sync port: any datagram at all")


def osc(address: str, *args) -> bytes:
    def pad(b: bytes) -> bytes:
        return b + b"\0" * (4 - len(b) % 4)
    tags, body = ",", b""
    for a in args:
        if isinstance(a, bool):
            tags += "T" if a else "F"
        elif isinstance(a, int):
            tags, body = tags + "i", body + struct.pack(">i", a)
        elif isinstance(a, float):
            tags, body = tags + "f", body + struct.pack(">f", a)
        else:
            tags, body = tags + "s", body + pad(str(a).encode())
    return pad(address.encode()) + pad(tags.encode()) + body


VALID_DATAGRAMS = [
    osc("/master/bpm/current", 128.0), osc("/bpm/master/current", 126.5),
    osc("/master/beat/subdiv/4", 0.25), osc("/beat/subdiv/16", 0.5),
    osc("/master/phrase/current", "Chorus"), osc("/master/track/title", "Despacito"),
    osc("/master/time/master", 61.5),
    osc("/klights/v1/pos", 1, 1, 61.5, 1.0, 129, 1, 1),
    osc("/klights/v1/phrase", 1, "Chorus", 0.25, 16.0),
    osc("/klights/v1/track", 1, 42, "a" * 40, "Title", "Artist", "Album", 180.0),
    osc("/klights/v1/deck", 2, 43, "", "T2", "A2", "", 0.0),
    b'{"bpm": 128, "beat_in_bar": 1.5, "phrase_label": "Verse", "phrase_measured": true}',
    b'{"title": "Song", "artist": "Someone", "rekordbox_id": 7, "playing": "yes"}',
]


def sync_violation(fields) -> str:
    """'' if the parse result keeps sync's promises, else what it broke."""
    if fields is None or fields is sync.IGNORED:
        return ""
    if not isinstance(fields, dict):
        return f"returned {type(fields).__name__}"
    allowed = set(sync.FIELDS) | {"source", "track"}
    extra = set(fields) - allowed
    if extra:
        return f"off-whitelist keys {sorted(extra)}"
    for key, value in fields.items():
        if isinstance(value, float) and not math.isfinite(value):
            return f"{key} is {value}"
        if isinstance(value, str) and len(value) > 200:
            return f"{key} is {len(value)} chars"
        rule = key[len("loaded_"):] if key.startswith("loaded_") else key
        if rule in sync._FLAGS and not isinstance(value, bool):
            return f"flag {key} is {value!r}"
        if rule in sync._FLOATS:
            lo, hi = sync._FLOATS[rule]
            if (lo is not None and value < lo) or (hi is not None and value > hi):
                return f"{key}={value} outside {lo}..{hi}"
    return ""


def try_parse(data: bytes):
    try:
        return sync.parse(data), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


rng = random.Random(9000 + SEED)
raised, broke = [], []
for i in range(4000):
    kind = i % 4
    if kind == 0:
        data = bytes(rng.randrange(256) for _ in range(rng.randrange(0, 80)))
    else:
        data = bytearray(rng.choice(VALID_DATAGRAMS))
        for _ in range(rng.randint(1, 4)):
            op = rng.randrange(4)
            if op == 0 and data:
                data[rng.randrange(len(data))] = rng.randrange(256)
            elif op == 1:
                data = data[:rng.randrange(len(data) + 1)]
            elif op == 2:
                data += bytes(rng.randrange(256) for _ in range(rng.randrange(1, 12)))
            elif data:
                pos = rng.randrange(len(data))
                data[pos:pos] = rng.choice([b"\0", b",", b"s", b"d", b"h", b"T", b"/"])
        data = bytes(data)
    fields, error = try_parse(data)
    if error:
        raised.append((data, error))
    elif sync_violation(fields):
        broke.append((data, sync_violation(fields)))
check("4000 random and mutated datagrams: parse never raises",
      not raised, (f"{len(raised)}, e.g. {short(raised[0])}" if raised else ""))
check("and nothing it returns breaks the whitelist, the ranges, or finiteness",
      not broke, (f"{len(broke)}, e.g. {short(broke[0])}" if broke else ""))

# Every value the decoder can hand osc_fields, on every address it routes: a
# string where a number belongs is the case a mutation rarely hits exactly.
arg_cases = [0.5, 3, "abc", "nan", "inf", "", True, False, -1.0, float("nan"), 1e30]
raised = []
for address in ["/master/beat/subdiv/4", "/beat/subdiv/0", "/beat/subdiv/x",
                "/master/bpm/current", "/master/time/master", "/master/phrase/current",
                "/bpm/master/current"] + [f"/{k}" for k in sync.OSC_FIELDS]:
    for arg in arg_cases:
        try:
            out = sync.osc_fields(address, arg)
            out = sync.clean(out) if isinstance(out, dict) else out
        except Exception as exc:  # noqa: BLE001
            raised.append((address, arg, f"{type(exc).__name__}: {exc}"))
            continue
        why = sync_violation(out)
        if why:
            raised.append((address, arg, why))
check("every routed OSC address with every argument type: no exception, nothing "
      "unclean", not raised, (f"{len(raised)}, e.g. {raised[:3]}" if raised else ""))

raised, broke = [], []
for i in range(1500):
    raw = {rng.choice(sync.FIELDS + ("junk", "source")): rng.choice(HOSTILE + ["1e400", "-0"])
           for _ in range(rng.randint(1, 6))}
    try:
        text = json.dumps(raw).encode()
    except (TypeError, ValueError):
        continue
    fields, error = try_parse(text)
    if error:
        raised.append((text, error))
    elif sync_violation(fields):
        broke.append((text, sync_violation(fields)))
check("1500 JSON datagrams of hostile values on real field names: never raises",
      not raised, (f"{len(raised)}, e.g. {short(raised[0])}" if raised else ""))
check("and NaN, Infinity, 1e400, quoted flags and huge strings never get through",
      not broke, (f"{len(broke)}, e.g. {short(broke[0])}" if broke else ""))


# -- 2. files ------------------------------------------------------------------
print("\n2. config and show-folder files: mutated, then validated, then loaded")


def nodes(doc, path=()):
    yield path, doc
    if isinstance(doc, dict):
        for k, v in doc.items():
            yield from nodes(v, path + (k,))
    elif isinstance(doc, list):
        for i, v in enumerate(doc):
            yield from nodes(v, path + (i,))


def mutate(doc, rng: random.Random):
    """A copy of `doc` with one to three things wrong with it -- a hostile value
    where a real one was, a key gone or added, or a real value from elsewhere in
    the file in the wrong place (the type confusion a hand edit actually makes)."""
    doc = copy.deepcopy(doc)
    for _ in range(rng.randint(1, 3)):
        all_nodes = list(nodes(doc))
        path, node = rng.choice(all_nodes)
        op = rng.randrange(5)
        if not path:
            if isinstance(node, dict) and node:
                node.pop(rng.choice(list(node)))
            continue
        parent = doc
        for step in path[:-1]:
            parent = parent[step]
        last = path[-1]
        if op == 0:
            parent[last] = rng.choice(HOSTILE)
        elif op == 1 and isinstance(parent, dict):
            parent.pop(last)
        elif op == 1 and isinstance(parent, list):
            parent.pop(last)
        elif op == 2 and isinstance(node, dict):
            node[rng.choice(["junk", "", "id", "name", "type", "at"])] = rng.choice(HOSTILE)
        elif op == 3:
            parent[last] = copy.deepcopy(rng.choice(all_nodes)[1])
        elif isinstance(node, list) and node:
            node.append(copy.deepcopy(rng.choice(node)))
    return doc


def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


EVENT = REPO / "events" / "despacio"
LIB = rigmod.ProfileLibrary([REPO / "shared" / "fixtures"])
FUZZ_EVENT = TMP / "event"
shutil.copytree(EVENT, FUZZ_EVENT, ignore=shutil.ignore_patterns(
    "__pycache__", "calibration_history", "*.bak", ".engine.lock"))


def loads_rig(doc) -> None:
    configmod.write_json_atomic(FUZZ_EVENT / "rig.json", doc, backup=False)
    try:
        rig = rigmod.load_rig(FUZZ_EVENT, LIB)
    except KeyError as exc:
        # FixtureProfile.offsets() refuses an unknown mode with a KeyError on
        # purpose -- it is a lookup -- and says which modes there are. That
        # one is a refusal; any other KeyError is a crash.
        if "has no mode" in str(exc):
            raise ValueError(str(exc)) from exc
        raise
    rig.validate()
    rig.warnings()


def loads_calibration(doc) -> None:
    configmod.write_json_atomic(FUZZ_EVENT / "calibration.json", doc, backup=False)
    rigmod.load_rig(FUZZ_EVENT, LIB)


def loads_cues(doc) -> None:
    path = TMP / "cues.json"
    configmod.write_json_atomic(path, doc, backup=False)
    cuesmod.load(path)


def loads_parametric(doc) -> None:
    path = TMP / "parametric_looks.json"
    configmod.write_json_atomic(path, doc, backup=False)
    libmod.load_parametric(path)


CONFIGS = [
    ("rig.json", EVENT / "rig.json", configmod.RIG, loads_rig),
    ("calibration.json", EVENT / "calibration.json", configmod.CALIBRATION,
     loads_calibration),
    ("venue", REPO / "shared" / "venues" / "despacio-room.json", configmod.VENUE, None),
    ("presets.json", EVENT / "presets.json", configmod.PRESETS, None),
    ("looks.json", EVENT / "looks.json", configmod.LOOKS, None),
    ("parametric_looks.json", EVENT / "parametric_looks.json",
     configmod.PARAMETRIC_LOOKS, loads_parametric),
    ("inventory.json", REPO / "shared" / "inventory.json", configmod.INVENTORY, None),
    ("cues.json", EVENT / "cues.json", cuesmod.CUES_SCHEMA, loads_cues),
]
rng = random.Random(1300 + SEED)
for name, path, schema, loader in CONFIGS:
    original = load_json(path)
    crashed, load_crashed, accepted = [], [], 0
    for _ in range(250):
        doc = mutate(original, rng)
        try:
            configmod.validate(doc, schema, Path(name))
        except configmod.ConfigError:
            continue
        except CRASHES as exc:
            crashed.append(f"{type(exc).__name__}: {exc}")
            continue
        accepted += 1
        if loader is None:
            continue
        try:
            loader(doc)
        except (configmod.ConfigError, ValueError, FileNotFoundError):
            pass                      # a semantic "no" with a message is fine
        except CRASHES as exc:
            load_crashed.append(f"{type(exc).__name__}: {exc}")
    check(f"{name}: 250 mutations -- the validator says no or yes, never raises",
          not crashed, (f"{len(crashed)}, e.g. {crashed[:2]}" if crashed else ""))
    if loader is not None:
        check(f"{name}: and every one it accepted ({accepted}) the engine loads without "
              f"a traceback", not load_crashed, (f"{len(load_crashed)}, e.g. {load_crashed[:2]}" if load_crashed else ""))

SHOW = REPO / "shared" / "show-example"
DOCS = {
    "track": load_json(SHOW / "tracks" / "synth-128.json"),
    "timeline": load_json(SHOW / "timelines" / "synth-128.json"),
    "routine": load_json(SHOW / "routines" / "fan-drop.json"),
    "template_set": load_json(SHOW / "templates" / "club.json"),
    "show": load_json(SHOW / "show.json"),
}
rng = random.Random(1900 + SEED)
for kind, original in DOCS.items():
    crashed, use_crashed, accepted = [], [], 0
    for _ in range(300):
        doc = mutate(original, rng)
        try:
            result = showfiles.validate(kind, doc)
        except Exception as exc:  # noqa: BLE001
            crashed.append(f"{type(exc).__name__}: {exc}")
            continue
        if not result.ok:
            continue
        accepted += 1
        try:
            if kind == "timeline":
                tl = timelinemod.Timeline.from_doc(result.doc, showfiles.timeline_channels)
                for beat in (-4.0, 0.0, 61.5, 160.0, 1e6):
                    tl.explain(beat)
            elif kind == "track":
                tracktime.Grid.from_segments(result.doc["grid"]["segments"])
        except (ValueError, configmod.ConfigError):
            pass
        except CRASHES as exc:
            use_crashed.append(f"{type(exc).__name__}: {exc}")
    check(f"{kind}: 300 mutations -- validate returns problems, never raises",
          not crashed, (f"{len(crashed)}, e.g. {crashed[:2]}" if crashed else ""))
    if kind in ("timeline", "track"):
        check(f"{kind}: and every one it accepted ({accepted}) the engine can use",
              not use_crashed, (f"{len(use_crashed)}, e.g. {use_crashed[:2]}" if use_crashed else ""))

rng = random.Random(4 + SEED)
bad = []
for _ in range(2000):
    segs = [[rng.choice([0, 1, 4, 64, -1, 1e9]) * rng.random(),
             rng.uniform(-1000, 400000), rng.choice([128, 0, -5, 1e9, 60, 174])]
            for _ in range(rng.randint(0, 5))]
    try:
        grid = tracktime.Grid.from_segments(segs)
    except ValueError:
        continue
    except Exception as exc:  # noqa: BLE001
        bad.append((segs, f"{type(exc).__name__}: {exc}"))
        continue
    samples = [grid.beat_at(t) for t in (-10.0, 0.0, 1.0, 30.0, 600.0, 3600.0)]
    if not all(math.isfinite(b) for b in samples) or any(
            b2 <= b1 for b1, b2 in zip(samples, samples[1:])):
        bad.append((segs, f"beats {samples}"))
check("2000 random beat grids: refused with ValueError, or finite and running forwards",
      not bad, f"{len(bad)}, e.g. {short(bad[0])}" if bad else "")


# -- 3. the command queue --------------------------------------------------------
print("\n3. any sequence of console commands")

# The controller runs against a copy of the event, with the venue library
# redirected too: several of these commands save presets, venues and patches.
CTRL_TMP = TMP / "ctrl"
shutil.copytree(EVENT, CTRL_TMP / "despacio", ignore=shutil.ignore_patterns(
    "__pycache__", "*.bak", ".engine.lock", "backups"))
shutil.copytree(rigmod.VENUE_LIBRARY, CTRL_TMP / "venues")
rigmod.VENUE_LIBRARY = patchmod.VENUES = CTRL_TMP / "venues"

from engine import server as servermod  # noqa: E402

source = (REPO / "engine" / "server.py").read_text(encoding="utf-8")
FIELDS_OF: dict[str, list[str]] = {}
for match in re.finditer(r"\n    def _cmd_(\w+)\(self, m: dict, now: float\)[^\n]*\n(.*?)(?=\n    def |\nclass |\Z)",
                         source, re.S):
    name, body = match.group(1), match.group(2)
    keys = set(re.findall(r'\bm\["(\w+)"\]', body)) | set(re.findall(r'\bm\.get\("(\w+)"', body))
    FIELDS_OF[name] = sorted(keys)
check("the command list read from engine/server.py is the whole list",
      len(FIELDS_OF) >= 80 and "go" in FIELDS_OF and "master" in FIELDS_OF,
      f"{len(FIELDS_OF)} commands")

sc = servermod.ShowController(CTRL_TMP / "despacio")
looks = sorted(sc.by_name)
REAL = {
    "name": looks[:6] + ["Nope"], "look": looks[:6], "slot": ["color", "movement", "level",
                                                               "base", "beam", "x"],
    "group": ["movers", "pinspots", "corner movers", "all", "nobody"],
    "tags": [["movers"], [], ["x"]], "fixture": ["Moving Head #1", "Pinspot #2", "ghost"],
    "axis": ["looks", "palette", "energy", "all", "x"], "on": [True, False, "maybe"],
    "value": [0.0, 0.5, 1.0, 128.0, 2.0], "index": [0, 1, 3, 8, 99, -1],
    "color": ["#ff0000", [1.0, 0.0, 0.0], "red"], "source": ["manual", "phrase", "x"],
}


def message_for(rng: random.Random, kind: str) -> dict:
    msg = {"type": kind}
    for key in FIELDS_OF.get(kind, []):
        roll = rng.random()
        if roll < 0.15:
            continue                                  # leave it out
        if roll < 0.55 and key in REAL:
            msg[key] = copy.deepcopy(rng.choice(REAL[key]))
        else:
            msg[key] = copy.deepcopy(rng.choice(HOSTILE))
    if rng.random() < 0.1:
        msg[rng.choice(["junk", "id", "rev"])] = rng.choice(HOSTILE)
    return msg


def strict_json_problem(snapshot) -> str:
    try:
        json.dumps(snapshot, allow_nan=False)
        return ""
    except ValueError as exc:
        return str(exc)


def find_non_finite(node, path="snapshot"):
    if isinstance(node, float) and not math.isfinite(node):
        return f"{path} = {node}"
    if isinstance(node, dict):
        for k, v in node.items():
            if not isinstance(k, str):
                # json.dumps turns a non-string key into a string, or -- for a
                # NaN or infinite float -- refuses. Either way a client's value
                # became a key, which is the bug.
                return f"{path} has a {type(k).__name__} key {k!r}"
            hit = find_non_finite(v, f"{path}.{k}")
            if hit:
                return hit
    if isinstance(node, (list, tuple)):
        for i, v in enumerate(node):
            hit = find_non_finite(v, f"{path}[{i}]")
            if hit:
                return hit
    return ""


def take_snapshot():
    """The snapshot, or the exception building it raised -- which is itself the
    failure being looked for (the broadcast thread swallows it, and every phone
    simply stops updating)."""
    try:
        return sc.snapshot(), ""
    except Exception as exc:  # noqa: BLE001
        return None, f"snapshot() raised {type(exc).__name__}: {exc}"


def snapshot_problem() -> str:
    snap, raised = take_snapshot()
    return raised or find_non_finite(snap) or strict_json_problem(snap)


def wait_frames(n: int = 3) -> None:
    target = sc.runner.stats.frames + n
    deadline = time.monotonic() + 5
    while sc.runner.stats.frames < target and time.monotonic() < deadline:
        time.sleep(0.005)


rng = random.Random(4242 + SEED)
kinds = sorted(FIELDS_OF)
first_problem = {}
sent = 0
sc.start()
try:
    wait_frames()
    for batch in range(24):
        batch_msgs = [message_for(rng, rng.choice(kinds)) for _ in range(60)]
        for msg in batch_msgs:
            sc.submit(msg, None)
        sent += len(batch_msgs)
        wait_frames()
        checks = {
            "evaluation error": (f"{sc.runner.stats.eval_errors} -- "
                                 f"{(sc.runner.last_error or '').strip().splitlines()[-5:]}"
                                 if sc.runner.stats.eval_errors else ""),
            "non-finite clock": ("" if math.isfinite(sc.ctx.beat) and math.isfinite(sc.ctx.bpm)
                                 else f"beat {sc.ctx.beat}, bpm {sc.ctx.bpm}"),
            "non-strict snapshot": snapshot_problem(),
            "output thread died": "" if sc.runner._thread and sc.runner._thread.is_alive() else "dead",
        }
        for what, problem in checks.items():
            if problem and what not in first_problem:
                first_problem[what] = (batch, problem, [m for m in batch_msgs
                                                        if m["type"] in ("speed", "nudge_phase",
                                                                         "bpm", "energy",
                                                                         "master", "rate")][:4])
        if len(first_problem) == len(checks):
            break

    for what in ("evaluation error", "non-finite clock", "non-strict snapshot",
                 "output thread died"):
        hit = first_problem.get(what)
        check(f"{sent} random commands: no {what}", hit is None,
              "" if hit is None else f"batch {hit[0]}: {hit[1]}; suspects {short(hit[2], 300)}")

    # And the desk still answers afterwards: the commands an operator reaches
    # for when something looks wrong, in the order they would reach for them.
    for msg in [{"type": "clear_panic"}, {"type": "blackout", "on": False},
                {"type": "master", "value": 1.0}, {"type": "bpm", "value": 128.0},
                {"type": "speed", "value": 1.0}, {"type": "cue_reset"}, {"type": "go"}]:
        sc.submit(msg, None)
    wait_frames(10)
    b1 = sc.ctx.beat
    wait_frames(10)
    check("after the barrage, a reset takes: the clock runs forward at 128 bpm",
          math.isfinite(b1) and sc.ctx.beat > b1 and abs(sc.ctx.bpm - 128.0) < 1e-6,
          f"beat {b1} -> {sc.ctx.beat}, bpm {sc.ctx.bpm}")
    # Panic, not blackout: blackout zeroes intensity and leaves the heads where
    # they are (so nothing swings in the dark); panic is every channel to zero.
    sc.submit({"type": "panic"}, None)
    wait_frames(5)
    # The rig's universes as they are NOW: a live patch in the barrage may have
    # moved them, and NullOutput keeps a universe's last frame forever.
    last = sc.runner.output.last if hasattr(sc.runner.output, "last") else {}
    live = {u: last.get(u, b"") for u in sc.ctx.rig.universes}
    check("and panic still takes every channel of the rig to zero",
          live and all(frame and not any(frame) for frame in live.values()),
          f"{ {u: sum(1 for v in f if v) for u, f in live.items()} }")
finally:
    sc.stop()

# The specific inputs the barrage is most likely to stumble on, each alone, so
# a failure names its command rather than a batch.
NON_FINITE = [float("nan"), float("inf"), float("-inf")]
singles = [({"type": t, k: v}) for t, k in [("speed", "value"), ("nudge_phase", "beats"),
                                            ("bpm", "value"), ("master", "value"),
                                            ("energy", "value")]
           for v in NON_FINITE + [1e308]]
singles += [{"type": "auto_interval", "axis": a, "value": v}
            for a in ("looks", "palette") for v in NON_FINITE]
singles += [{"type": "flash", "target": t, "on": True} for t in (1.5, None, 0)]
sc = servermod.ShowController(CTRL_TMP / "despacio")
sc.start()
try:
    wait_frames()
    for msg in singles:
        master_before = sc.master
        sc.submit(msg, None)
        wait_frames(2)
        problem = ("" if math.isfinite(sc.ctx.beat) else f"clock beat {sc.ctx.beat}") \
            or snapshot_problem()
        if msg["type"] == "master" and not problem and sc.master != master_before \
                and not math.isfinite(msg["value"]):
            problem = f"master moved {master_before} -> {sc.master} on a non-number"
        if msg["type"] == "energy" and not problem and not math.isfinite(msg["value"]) \
                and isinstance(sc.director.energy_source, autom.ManualEnergy) \
                and not math.isfinite(sc.director.energy_source.value):
            problem = f"energy stored as {sc.director.energy_source.value}"
        if msg["type"] == "flash" and not problem:
            problem = ("" if all(isinstance(t, str) for t in sc.flashing)
                       else f"flashing holds {sc.flashing}")
        shown = {k: v for k, v in msg.items() if k != "type"}
        check(f"{msg['type']} {shown}: nothing non-finite reaches the clock, the "
              f"snapshot or the show", not problem, problem)
        if problem:
            # Put it right so the next case starts clean.
            sc.stop()
            sc = servermod.ShowController(CTRL_TMP / "despacio")
            sc.start()
            wait_frames()
finally:
    sc.stop()


# -- 4. what the fuzz found, pinned --------------------------------------------
# Each input below once broke something, found by sections 1-3 on some seed.
# Pinned here so it fails by name, on every run, if it ever comes back.
print("\n4. every input the fuzz has found, pinned")

check("sync: a string on /beat/subdiv/<n> is None, not a ValueError out of parse()",
      try_parse(osc("/master/beat/subdiv/4", "abc")) == (None, ""))
check("sync: and a boolean there is not a phase either",
      sync.osc_fields("/beat/subdiv/4", True) is None)

try:
    configmod.validate({"width": float("nan"), "depth": 1, "height": 1},
                       configmod.VENUE, Path("v.json"))
    nan_refused = ""
except configmod.ConfigError as exc:
    nan_refused = "\n".join(exc.problems)
ok = "width must be a finite number" in nan_refused
check("config: a NaN passes no range test, so it is refused by name", ok,
      "" if ok else nan_refused)

rig_doc = load_json(EVENT / "rig.json")
rig_doc["fixtures"][0]["hold"] = None
rig_doc["fixtures"][1]["tags"] = None
rig_doc["fixtures"][2]["universe"] = None
try:
    loads_rig(rig_doc)
    rig_ok = ""
except Exception as exc:  # noqa: BLE001
    rig_ok = f"{type(exc).__name__}: {exc}"
check("config: optional keys set to null (hold, tags, universe) load as absent",
      not rig_ok, rig_ok)
cue_doc = load_json(EVENT / "cues.json")
cue_doc["cues"][0].update(color=None, fade=None, notes=None)
try:
    loads_cues(cue_doc)
    cue_ok = ""
except Exception as exc:  # noqa: BLE001
    cue_ok = f"{type(exc).__name__}: {exc}"
check("cues: \"color\": null, \"fade\": null load as no colour and the default fade",
      not cue_ok and cuesmod.load(TMP / "cues.json").cues[0].fade == 8.0, cue_ok)

sc = servermod.ShowController(CTRL_TMP / "despacio")
sc.start()
try:
    wait_frames()

    def outcome(msg) -> str:
        """'' if the command left the show sound, else what it broke."""
        errors = sc.runner.stats.eval_errors
        sc.submit(msg, None)
        wait_frames(4)
        if sc.runner.stats.eval_errors != errors:
            return (sc.runner.last_error or "").strip().splitlines()[-1]
        return snapshot_problem()

    for msg, what in [
        ({"type": "color", "color": [], "target": "all"}, "an empty colour (froze the rig)"),
        ({"type": "color", "color": [1.0], "target": "movers"}, "a one-channel colour"),
        ({"type": "color", "color": [1, 0, 0], "target": float("nan")}, "a NaN colour target"),
        ({"type": "level", "value": 0.5, "target": float("inf")}, "an infinite level target"),
        ({"type": "flash", "target": 2.5}, "a numeric flash target (froze the snapshot)"),
        ({"type": "select_look", "slot": "movement", "name": looks[0],
          "hold": float("-inf")}, "a -Infinity hold"),
        ({"type": "taper", "margin_deg": float("inf")}, "an infinite taper margin"),
        ({"type": "venue", "crowd": {"min_x": float("nan")}}, "a NaN crowd edge"),
    ]:
        problem = outcome(msg)
        check(f"command: {what} leaves every frame rendering and the snapshot strict JSON",
              not problem, problem)
    sc.submit({"type": "flash_clear"}, None)
    sc.submit({"type": "level", "value": float("nan"), "target": "movers"}, None)
    sc.submit({"type": "color", "color": [float("nan"), 0, 0], "target": "pinspots"}, None)
    wait_frames(3)
    check("command: a NaN level or colour channel is refused, not clamped to full",
          "movers" not in sc.level_overrides and "pinspots" not in sc.color_overrides,
          f"{sc.level_overrides} {sc.color_overrides}")

    sc.submit({"type": "taper", "margin_deg": 2 ** 63}, None)
    wait_frames(2)
    check("command: a taper margin past the schema's 90 is held at 90 live",
          sc.ctx.taper.margin_deg == 90.0, f"{sc.ctx.taper.margin_deg}")
    sc.submit({"type": "venue_save"}, None)
    wait_frames(3)
    try:
        venue_now = configmod.load(sc.rig.venue_file, configmod.VENUE)
        venue_ok = ""
    except configmod.ConfigError as exc:
        venue_ok = str(exc)
    check("command: and venue_save writes only what the next start can load",
          not venue_ok and venue_now["taper"]["margin_deg"] == 90.0, venue_ok)

    # The runner's own guard: whatever goes wrong in syncing the clock costs
    # frames' worth of error count, never the thread that sends them.
    real_update = sc.runner.director.update if sc.runner.director else None
    if real_update is not None:
        def broken(*args, **kwargs):
            raise OverflowError("cannot convert float infinity to integer")
        sc.runner.director.update = broken
        frames_before, errors_before = sc.runner.stats.frames, sc.runner.stats.eval_errors
        wait_frames(5)
        sc.runner.director.update = real_update
        check("runner: an exception syncing the clock is counted, and frames keep going",
              sc.runner._thread.is_alive() and sc.runner.stats.frames >= frames_before + 5
              and sc.runner.stats.eval_errors > errors_before,
              f"alive {sc.runner._thread.is_alive()}, frames +"
              f"{sc.runner.stats.frames - frames_before}")
    else:
        check("runner: the director the guard protects is wired up", False, "no director")
finally:
    sc.stop()


print()
if failures:
    print(f"fuzz: {len(failures)} FAILED")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("fuzz: all checks pass")
