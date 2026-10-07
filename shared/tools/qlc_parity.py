"""
Frame-level parity between QLC+ and the engine.

The plan's verification list asks for one thing this repo did not have: drive the
same look on both stacks and diff the DMX frames. `test_library.py` already
checks that a ported pose encodes to the workspace's pan/tilt and that a ported
color renders the workspace's color bytes -- but both of those look only at
channels somebody thought to name. Parity is the opposite question, asked over
all 512: **where do the two stacks differ, and does every difference have a
reason?**

That framing matters, because a naive whole-frame diff is all noise. The engine
deliberately differs from QLC+ in three ways, every frame, on purpose:

  * it asserts each fixture's `hold` channels unconditionally (the structural
    fix for the stuck-LTP-channel bug),
  * it always runs base layers, so the movers are aimed at the ball and lit even
    when the selected look says nothing about position,
  * it applies the safety taper last, which QLC+ has no equivalent of at all.

So this does not assert "the frames are equal". It **classifies every differing
channel** and requires each one to land in a category that is derived rather
than asserted -- `held` because the channel really is in `rig.json`'s hold map,
`base` because the engine's value really is what it emits with no look selected,
`taper` because rendering with the taper disabled really does change it. What is
left over is `dropped` (QLC+ asserted a channel the engine does not model) or
`unexplained`, and those are the findings.

**Two sources for the QLC+ side, and they are not equally strong.**

`check` (default) models QLC+ from the workspace's stored `<FixtureVal>` values:
one scene, running alone, on a zero universe. It is exact about what the show
data SAYS, repeatable, needs nothing installed, and is what runs in the test. It
is a model of QLC+, not QLC+.

`capture` records what QLC+ actually puts on the wire, one frame per look, via
the same Art-Net the rig sees; `check --captured` then runs the identical
classifier against those real frames. That is the plan's actual test, and it is
the one that can catch the model being wrong -- notably that a live QLC+ frame
carries the whole console's residual LTP state, not one scene in isolation.

Usage:
    python shared/tools/qlc_parity.py check
    python shared/tools/qlc_parity.py check --verbose --category dropped
    python shared/tools/qlc_parity.py capture --out captures.json --all
    python shared/tools/qlc_parity.py check --captured captures.json
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator, Optional

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import library as libmod
from engine import rig as rigmod
from engine import safety as safetymod
from engine import state as statemod
from shared.tools.artnet_listener import parse_artdmx
from shared.tools.port_library import NS, Porter, parse_values

# A color no look in the library uses. If a look silently falls through to the
# palette instead of setting its own color, its bytes will not match the
# workspace's and we want to hear about it rather than have a plausible default
# cover for it.
PALETTE = (0.13, 0.29, 0.71)

# Both stacks quantise through different paths -- the port stores 0..1 rounded to
# four places, the renderer scales back through 255 -- so one byte is the
# quantisation floor, not a tolerance anyone chose.
ROUNDING = 1


# ------------------------------------------------------------------- diffs --

@dataclass(frozen=True)
class Diff:
    """One channel where the two stacks disagree, and why."""
    look: str
    universe: int
    channel: int                 # 1-based DMX address
    fixture: str
    role: str                    # role name, or the raw channel name
    qlc: int
    engine: int
    category: str

    def __str__(self) -> str:
        return (f"{self.look}  {self.fixture}/{self.role} "
                f"(u{self.universe} ch{self.channel}): "
                f"qlc={self.qlc} engine={self.engine}  [{self.category}]")


#: In report order. Everything up to `dropped` is expected; the last two are
#: findings, and `unexplained` is what fails the test.
CATEGORIES = ("rounding", "held", "base", "taper", "dropped", "unexplained")

CATEGORY_HELP = {
    "rounding": "both stacks assert it and they differ by one byte -- "
                "quantisation, not disagreement",
    "held": "the engine pins this channel every frame via rig.json 'hold'; "
            "QLC+ had no equivalent and relied on a function having written it",
    "base": "no look wrote it, so the engine is at its own unconditional "
            "baseline (movers on the ball, lit). In a live capture QLC+ instead "
            "shows whatever its last function left there -- residual LTP state",
    "taper": "the safety layer moved it. QLC+ has no equivalent of this at all",
    "dropped": "QLC+ asserts a channel the engine leaves alone -- the port does "
               "not model it. Each one is a real gap, even if a deliberate one",
    "unexplained": "none of the above. A port defect or an engine defect",
}


def channel_labels(rig: rigmod.Rig) -> dict[tuple[int, int], tuple[str, str]]:
    """(universe, 1-based channel) -> (fixture name, role or channel name).

    Labelled by ROLE where one exists, because "Moving Head #1/pan" says what a
    diff means and "u0 ch1" does not. Falls back to the profile's own channel
    name, which is the only handle the fixture-specific channels have -- the
    pinspot's mode selector among them.
    """
    out: dict[tuple[int, int], tuple[str, str]] = {}
    for f in rig.fixtures:
        by_offset = {off: role for role, off in f.profile.offsets(f.mode).items()}
        for offset, name in enumerate(f.profile.modes[f.mode]):
            out[(f.universe, f.address + offset)] = (f.name, by_offset.get(offset, name))
    return out


def classify(look: str, rig: rigmod.Rig,
             qlc: dict[int, bytearray],
             engine: dict[int, bytearray],
             reference: dict[int, bytearray],
             untapered: dict[int, bytearray],
             asserted: set[tuple[int, int]],
             labels: dict[tuple[int, int], tuple[str, str]]) -> list[Diff]:
    """Every channel where the frames differ, each with a derived reason.

    `asserted` is the set of channels the QLC+ SOURCE actually wrote -- the
    scene's own `<FixtureVal>` channels. It is what separates "the engine
    invented a value" from "the engine dropped one", and a live capture has to
    supply it too, since a captured frame asserts all 512 bytes and so cannot
    say by itself which of them the look meant.

    The categories are ordered most-specific first. `held` outranks `base`
    because a held channel is also part of the baseline, and saying "held" is
    the more useful of the two true answers.
    """
    holds = {}
    for f in rig.fixtures:
        for key in f.hold:
            channel = f.address_of(key)
            if channel is not None:
                holds[(f.universe, channel)] = f.name

    diffs: list[Diff] = []
    for universe in sorted(set(qlc) & set(engine)):
        a, b = qlc[universe], engine[universe]
        for index in range(min(len(a), len(b))):
            if a[index] == b[index]:
                continue
            channel = index + 1
            key = (universe, channel)
            fixture, role = labels.get(key, ("(unpatched)", f"ch{channel}"))
            delta = abs(a[index] - b[index])

            if key in asserted and delta <= ROUNDING:
                category = "rounding"
            elif key in holds:
                category = "held"
            elif b[index] < untapered[universe][index]:
                # Rendering the same look with the taper off gives a HIGHER
                # value, so the taper is what pulled this one down. Derived
                # rather than inferred from "it is a dimmer" -- on a fixture
                # with no dimmer channel the level lands on the color channels
                # instead, and hardcoding a role would miss those.
                #
                # The comparison is one-sided deliberately. `!=` would have been
                # the obvious spelling and is far too generous: the taper can
                # only ever multiply intensity DOWN, so anything the engine
                # raises above its own untapered value has some other cause, and
                # this branch running before `dropped` would have quietly
                # absorbed it.
                category = "taper"
            elif key in asserted:
                # QLC+ wrote it, the engine is at its baseline: the port does
                # not model this channel.
                category = "dropped" if b[index] == reference[universe][index] \
                    else "unexplained"
            elif b[index] == reference[universe][index]:
                category = "base"
            else:
                category = "unexplained"

            diffs.append(Diff(look, universe, channel, fixture, role,
                              a[index], b[index], category))
    return diffs


# ------------------------------------------------------------------ frames --

def engine_frames(rig: rigmod.Rig, entry: Optional[libmod.LibraryEntry],
                  motion_bar: float = 0.0,
                  taper: bool = True) -> dict[int, bytearray]:
    """One rendered frame. A FRESH context each call, deliberately.

    The safety layer slew-limits against the previous frame, so reusing a
    context would make each look's result depend on which look was compared
    before it. With no previous value the limiter passes the raw taper through,
    which is the settled value a held look would reach anyway.
    """
    ctx = statemod.EvalContext(rig=rig, venue=rig.venue,
                               taper=safetymod.TaperConfig(enabled=taper))
    # Every slot to the same phase. The parity question is "what does this look
    # emit at phase p", and p is one number -- per-slot rate is a performance
    # control, not something a round-trip against a static workspace can model.
    ctx.set_phase(motion_bar)
    show = (libmod.build_look(entry).make(PALETTE) if entry is not None
            else libmod.compose(None, [], [], PALETTE))
    return statemod.frame(ctx, show)


def modelled_qlc_frames(rig: rigmod.Rig,
                        values_by_fixture: dict[int, dict[int, int]]
                        ) -> tuple[dict[int, bytearray], set[tuple[int, int]]]:
    """What QLC+ emits for one scene running alone: zeros, plus the scene.

    A QLC+ universe starts at zero and a Scene writes exactly its stored
    `<FixtureVal>` channels. With one function up and nothing else, that IS the
    frame -- which is the whole reason a single scene is the unit of comparison
    here rather than a whole console state.

    Returns the frame and the set of channels the scene asserted, because
    "wrote 0" and "did not write" are different claims and the classifier needs
    to tell them apart.
    """
    frames = {u: bytearray(512) for u in rig.universes}
    asserted: set[tuple[int, int]] = set()
    for fid, values in values_by_fixture.items():
        try:
            fixture = rig.by_id(fid)
        except KeyError:
            continue
        for offset, value in values.items():
            channel = fixture.address + offset
            if channel > 512:
                continue
            frames[fixture.universe][channel - 1] = value
            asserted.add((fixture.universe, channel))
    return frames, asserted


# ------------------------------------------------------------------ sample --

@dataclass
class Sample:
    """One comparable moment: a look, a phase, and what QLC+ stored for it."""
    label: str
    entry: libmod.LibraryEntry
    motion_bar: float
    values: dict[int, dict[int, int]]


def _scene_values(func) -> dict[int, dict[int, int]]:
    return {int(fv.get("ID")): parse_values(fv.text or "")
            for fv in func.findall(NS + "FixtureVal")}


def cue_phases(spans: list[list[float]]) -> list[float]:
    """Where in the cycle a CUED chase is actually showing each step.

    Not the step boundary. A cued step travels into its pose and then holds
    there, so at its boundary the head is still at the PREVIOUS pose and its
    dimmer is at the previous step's level -- sampling there compares the
    workspace's step against the one before it and reports every channel of
    every dark move as a mismatch.

    Mid-hold where there is a hold. Where there is none the pose is only
    reached at the very end of the travel, so this stops a hair short of the
    boundary rather than landing on it: position is continuous across the
    boundary and the dimmer is not, and this is the side where the step being
    compared is the one being shown.
    """
    total = sum(fade + hold for fade, hold in spans)
    out: list[float] = []
    elapsed = 0.0
    for fade, hold in spans:
        moment = elapsed + (fade + hold / 2 if hold > 0 else fade * 0.999)
        out.append(moment / total)
        elapsed += fade + hold
    return out


def samples(porter: Porter, entries: list[libmod.LibraryEntry],
            workspace: Path) -> tuple[list[Sample], list[str]]:
    """Every look, expanded to the moments that can actually be compared.

    A held look is one moment. A chase is one per step -- comparing a chase at a
    single phase would check one of its steps and silently ignore the rest,
    which is exactly the sort of partial coverage this whole exercise exists to
    replace.

    Step filtering goes back through `Porter`'s own predicates rather than
    re-deriving them. The port drops steps that write nothing of the relevant
    kind, so a chase's step list and its ported frame list do not line up by
    index, and any second opinion about which steps survived would eventually
    disagree with the porter and blame the engine for it.
    """
    root = ET.parse(workspace).getroot()
    functions = root.find(NS + "Engine").findall(NS + "Function")
    scenes = {f.get("Name"): f for f in functions if f.get("Type") == "Scene"}
    scenes_by_id = {int(f.get("ID")): f for f in functions if f.get("Type") == "Scene"}
    chasers = {f.get("Name"): f for f in functions if f.get("Type") == "Chaser"}

    out: list[Sample] = []
    unmatched: list[str] = []
    for entry in entries:
        if entry.kind in ("pose", "color", "mixed", "intensity"):
            func = scenes.get(entry.name)
            if func is None:
                unmatched.append(entry.name)
                continue
            out.append(Sample(entry.name, entry, 0.0, _scene_values(func)))
            continue

        func = chasers.get(entry.name)
        if func is None:
            unmatched.append(entry.name)
            continue
        steps = [scenes_by_id[int((s.text or "0").strip())]
                 for s in sorted(func.findall(NS + "Step"),
                                 key=lambda s: int(s.get("Number", 0)))
                 if int((s.text or "0").strip()) in scenes_by_id]

        if entry.kind == "path":
            kept = [s for s in steps
                    if porter.offsets_for(_scene_values(s)) is not None]
            if entry.is_cued:
                phases = cue_phases(entry.step_spans)
            else:
                # A closed path sits exactly on waypoint i at phase i/n -- the
                # easing contributes nothing there -- so a step compares against
                # the pose it came from with no interpolation in the way.
                phases = [i / len(kept) for i in range(len(kept))] if kept else []
        elif entry.kind == "color_path":
            kept = [s for s in steps if porter.colors_for(_scene_values(s))]
            # Stepped, not interpolated: land mid-slot, where the held frame is
            # unambiguous rather than on a boundary a rounding could cross.
            phases = [(i + 0.5) / len(kept) for i in range(len(kept))] if kept else []
        else:                                              # level_path
            kept = steps
            phases = [(i + 0.5) / len(kept) for i in range(len(kept))] if kept else []

        bars = entry.bars or libmod.DEFAULT_BARS
        for index, (scene, phase) in enumerate(zip(kept, phases)):
            out.append(Sample(f"{entry.name}[{index}]", entry, bars * phase,
                              _scene_values(scene)))
    return out, unmatched


# ------------------------------------------------------------------- check --

def run_check(event: Path, captured: Optional[Path] = None
              ) -> tuple[list[Diff], list[Sample], list[str]]:
    rig = rigmod.load_rig(event)
    entries = libmod.load_entries(event / "looks.json")
    porter = Porter(event)
    workspace = next(event.glob("*.qxw"))
    all_samples, unmatched = samples(porter, entries, workspace)
    labels = channel_labels(rig)

    live: dict[str, dict[int, bytearray]] = {}
    if captured is not None:
        payload = json.loads(Path(captured).read_text(encoding="utf-8"))
        for name, frames in payload["frames"].items():
            live[name] = {int(u): bytearray(v) for u, v in frames.items()}
        all_samples = [s for s in all_samples if s.label in live]

    reference = engine_frames(rig, None)
    diffs: list[Diff] = []
    for sample in all_samples:
        modelled, asserted = modelled_qlc_frames(rig, sample.values)
        qlc = live.get(sample.label, modelled)
        rendered = engine_frames(rig, sample.entry, sample.motion_bar)
        untapered = engine_frames(rig, sample.entry, sample.motion_bar, taper=False)
        diffs.extend(classify(sample.label, rig, qlc, rendered, reference,
                              untapered, asserted, labels))
    return diffs, all_samples, unmatched


def report(diffs: list[Diff], sampled: list[Sample], unmatched: list[str],
           verbose: bool = False, only: Optional[str] = None) -> int:
    counts = {c: 0 for c in CATEGORIES}
    for diff in diffs:
        counts[diff.category] = counts.get(diff.category, 0) + 1

    print(f"{len(sampled)} comparable moments from "
          f"{len({s.entry.name for s in sampled})} looks")
    if unmatched:
        print(f"{len(unmatched)} look(s) had no function of that name in the "
              f"workspace: {', '.join(unmatched[:4])}"
          + (" ..." if len(unmatched) > 4 else ""))
    print(f"{len(diffs)} differing channels\n")

    for category in CATEGORIES:
        count = counts.get(category, 0)
        marker = "!" if category in ("dropped", "unexplained") and count else " "
        print(f" {marker} {count:>6}  {category:<12} {CATEGORY_HELP[category]}")

    def show(category: str, limit: int) -> None:
        rows = [d for d in diffs if d.category == category]
        if not rows:
            return
        print(f"\n{category} ({len(rows)}):")
        seen: dict[str, int] = {}
        for row in rows:
            # One line per distinct fixture/role, with a count. A dropped
            # channel is dropped in every look that writes it, and 300 identical
            # lines hide how many DISTINCT things are wrong.
            key = f"{row.fixture}/{row.role}"
            seen[key] = seen.get(key, 0) + 1
        for key, count in sorted(seen.items(), key=lambda kv: -kv[1])[:limit]:
            example = next(r for r in rows if f"{r.fixture}/{r.role}" == key)
            print(f"  {key:<40} {count:>5} looks  "
                  f"e.g. {example.look}: qlc={example.qlc} engine={example.engine}")

    if only:
        show(only, 200)
    else:
        show("dropped", 20)
        show("unexplained", 20)
        if verbose:
            for category in ("rounding", "held", "base", "taper"):
                show(category, 10)

    print()
    if counts.get("unexplained"):
        print(f"PARITY FAILED: {counts['unexplained']} unexplained channel(s)")
        return 1
    print("parity holds: every difference is accounted for")
    return 0


# ----------------------------------------------------------------- capture --

def capture(event: Path, out: Path, names: list[str], universe: int,
            port: int = 6454, settle: float = 1.0, timeout: float = 30.0) -> int:
    """Record what QLC+ actually emits, one frame per look.

    Interactive on purpose. There is no way to ask QLC+ over its web API to
    "run function N and tell me when it is stable" that does not re-derive the
    reverse-engineered web protocol this whole rebuild exists to stop depending
    on -- so a person selects the look and this waits for the wire to go quiet.

    A frame counts as settled when it has not changed for `settle` seconds,
    which is what excludes fade-ins and chase steps. That also means a chase
    NEVER settles: capture the step scenes by name instead, which is what the
    modelled side compares against anyway.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    try:
        sock.bind(("0.0.0.0", port))
    except OSError as exc:
        # The single most expensive failure in this repo's history was another
        # process holding 6454 while the receiver reported itself healthy.
        print(f"cannot bind UDP {port}: {exc}\n"
              f"Something else holds the Art-Net port. On Windows:\n"
              f"  Get-NetUDPEndpoint -LocalPort {port} | "
              f"%{{Get-Process -Id $_.OwningProcess}}", file=sys.stderr)
        return 2
    sock.settimeout(0.5)

    payload: dict = {"source": "qlc+", "universe": universe, "frames": {}}
    if out.exists():
        payload = json.loads(out.read_text(encoding="utf-8"))
        payload.setdefault("frames", {})

    print(f"Listening on UDP {port} for universe {universe}.")
    print("Select each look in QLC+ when prompted; recording starts when the "
          "wire goes quiet.\n")
    try:
        for name in names:
            input(f"  select {name!r} in QLC+, then press Enter... ")
            frame = _settled_frame(sock, universe, settle, timeout)
            if frame is None:
                print(f"    no settled frame within {timeout:.0f}s -- skipped. "
                      f"Is QLC+ outputting Art-Net on universe {universe}?")
                continue
            payload["frames"][name] = {str(universe): list(frame)}
            lit = sum(1 for v in frame if v)
            print(f"    recorded {name!r}: {lit} non-zero channels")
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        sock.close()

    out.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"\nwrote {len(payload['frames'])} frame(s) to {out}")
    return 0


def _settled_frame(sock: socket.socket, universe: int, settle: float,
                   timeout: float) -> Optional[bytes]:
    deadline = time.monotonic() + timeout
    current: Optional[bytes] = None
    since = time.monotonic()
    while time.monotonic() < deadline:
        try:
            raw, _ = sock.recvfrom(1024)
        except socket.timeout:
            # Silence still counts as settled -- QLC+ only sends on change by
            # default, so a held look can stop transmitting entirely.
            if current is not None and time.monotonic() - since >= settle:
                return current
            continue
        parsed = parse_artdmx(raw)
        if parsed is None or parsed[0] != universe:
            continue
        dmx = parsed[1].ljust(512, b"\x00")[:512]
        if dmx != current:
            current, since = dmx, time.monotonic()
        elif time.monotonic() - since >= settle:
            return current
    return None


# -------------------------------------------------------------------- main --

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command")

    check = sub.add_parser("check", help="diff the engine against QLC+")
    check.add_argument("--event", type=Path, default=REPO / "events" / "despacio")
    check.add_argument("--captured", type=Path,
                       help="frames recorded by `capture`; without this, QLC+ "
                            "is modelled from the workspace's stored values")
    check.add_argument("--verbose", action="store_true",
                       help="also break down the expected categories")
    check.add_argument("--category", choices=CATEGORIES,
                       help="list every diff in one category")

    grab = sub.add_parser("capture", help="record live QLC+ frames")
    grab.add_argument("--event", type=Path, default=REPO / "events" / "despacio")
    grab.add_argument("--out", type=Path, required=True)
    grab.add_argument("--universe", type=int, default=0)
    grab.add_argument("--looks", help="comma-separated look names")
    grab.add_argument("--all", action="store_true",
                      help="every scene-backed look in looks.json -- long")
    grab.add_argument("--settle", type=float, default=1.0)

    args = parser.parse_args()
    if args.command == "capture":
        if args.all:
            names = [e.name for e in libmod.load_entries(args.event / "looks.json")
                     if e.kind in ("pose", "color", "mixed", "intensity")]
        elif args.looks:
            names = [n.strip() for n in args.looks.split(",") if n.strip()]
        else:
            print("pass --looks or --all", file=sys.stderr)
            return 2
        return capture(args.event, args.out, names, args.universe,
                       settle=args.settle)

    if args.command is None:
        parser.print_help()
        return 2
    diffs, sampled, unmatched = run_check(args.event, args.captured)
    if args.captured:
        print(f"comparing against LIVE frames from {args.captured}\n")
    else:
        print("comparing against the workspace's stored scene values "
              "(a model of QLC+, not QLC+ -- see `capture`)\n")
    return report(diffs, sampled, unmatched, args.verbose, args.category)


if __name__ == "__main__":
    raise SystemExit(main())
