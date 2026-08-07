"""
Port a QLC+ workspace's function library into the engine's look format.

The despacio workspace holds 179 Scenes, 39 Chasers, 6 Collections and 5 EFX.
The plan asks for a BROAD port -- translate the lot mechanically rather than
hand-picking survivors -- because the reason only a handful got used on the
night was not that the rest were bad, it was that finding them on a phone was
hard. Judging which to keep is a decision for a room, not for a converter.

Three things change in translation, and all three are the point:

**Scenes split by what they touch.** QLC+ has one flat namespace, so a colour
and a position are the same kind of object and every combination of the two has
to exist as its own stored scene. That is how 179 accumulated. Here a scene that
writes only the colour wheel becomes a COLOUR look, one that writes pan/tilt
becomes a POSE look, and they compose -- so the same 179 scenes yield far more
than 179 combinations, and adding a colour costs one entry rather than N.

**Positions become offsets from each head's own ball point.** The stored DMX is
decoded through the same geometry the engine aims with, then expressed relative
to that head's calibrated ball aim. A ported look therefore TRACKS
RECALIBRATION: re-aim a head and every look built on it re-centres, which the
stored absolute values could never do.

**Chasers become paths in bars.** A chaser is a step list with a millisecond
duration, which is why slowing one down made it steppier rather than smoother.
The step positions become waypoints for `motion.path()`, and the duration is
converted to bars at a reference tempo and snapped to a musical value -- so the
route survives and the timing becomes musical.

Usage:
    python shared/tools/port_library.py --event events/despacio
    python shared/tools/port_library.py --event events/despacio --write
"""

from __future__ import annotations

import argparse
import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import sys

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import geometry as geo
from engine import rig as rigmod

NS = "{http://www.qlcplus.org/Workspace}"

# Musical lengths a chaser's cycle is snapped to. A chase authored at 4000 ms a
# step was aiming at a musical length and missing it by whatever the tempo drift
# was; snapping recovers the intent rather than preserving the error.
MUSICAL_BARS = [0.25, 0.5, 1, 2, 4, 8, 16, 32]


@dataclass
class PortedLook:
    name: str
    kind: str                       # "pose" | "color" | "path" | "mixed"
    tags: list[str]
    offsets: Optional[list[list[float]]] = None      # per head, (bearing, elev)
    steps: Optional[list[list[list[float]]]] = None  # per step, per head
    color: Optional[list[float]] = None
    colors: Optional[dict[str, list[float]]] = None
    frames: Optional[list[dict[str, list[float]]]] = None
    bars: Optional[float] = None
    intensity: Optional[float] = None
    source: str = ""
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        out: dict = {"name": self.name, "kind": self.kind, "tags": self.tags}
        for key in ("offsets", "steps", "frames", "color", "colors",
                    "bars", "intensity"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        if self.source:
            out["source"] = self.source
        if self.notes:
            out["notes"] = self.notes
        return out


def parse_values(text: str) -> dict[int, int]:
    """'0,47,2,69' -> {0: 47, 2: 69}. Channel offsets are fixture-relative."""
    if not text:
        return {}
    nums = [int(x) for x in text.strip().split(",") if x.strip()]
    if len(nums) % 2:
        raise ValueError(f"odd channel,value list: {text!r}")
    return dict(zip(nums[::2], nums[1::2]))


def wheel_color(profile, mode: str, offset: int, value: int) -> Optional[list[float]]:
    """The RGB a colour-wheel value actually produces, from the .qxf."""
    name = profile.modes[mode][offset]
    for cap in profile.channels[name].capabilities:
        if cap.lo <= value <= cap.hi and cap.rgb is not None:
            return [round(c / 255.0, 4) for c in cap.rgb]
    return None


def snap_bars(raw_bars: float) -> float:
    return min(MUSICAL_BARS, key=lambda b: abs(b - raw_bars))


class Porter:
    def __init__(self, event_dir: Path, reference_bpm: float = 124.0):
        self.rig = rigmod.load_rig(event_dir)
        if self.rig.geometry is None:
            raise ValueError(f"{event_dir} has no positioned heads to decode against")
        self.geo = self.rig.geometry
        self.bpm = reference_bpm
        self.ms_per_bar = 4 * 60_000.0 / reference_bpm
        self.skipped: list[tuple[str, str]] = []

        # QLC+ fixture ID -> our patched fixture. The workspace and rig.json are
        # both derived from the same patch sheet, so the ids line up; anything
        # that does not is reported rather than guessed at.
        self.by_qlc_id = {f.fid: f for f in self.rig.fixtures}

    # -- decoding ---------------------------------------------------------

    def offsets_for(self, values_by_fixture: dict[int, dict[int, int]]
                    ) -> Optional[list[list[float]]]:
        """Per-head (bearing, elevation) offsets from that head's ball aim.

        Returns None if this scene does not write position for every mover --
        a partial position is not a pose, and inventing the missing heads would
        put beams somewhere nobody asked for.
        """
        movers = self.rig.movers
        out: list[list[float]] = []
        for fixture in movers:
            values = values_by_fixture.get(fixture.fid)
            if not values:
                return None
            offsets = fixture.profile.offsets(fixture.mode)
            pan_o, tilt_o = offsets.get(rigmod.PAN), offsets.get(rigmod.TILT)
            if pan_o is None or tilt_o is None or pan_o not in values or tilt_o not in values:
                return None
            pan_fine = values.get(offsets.get(rigmod.PAN_FINE, -1), 0)
            tilt_fine = values.get(offsets.get(rigmod.TILT_FINE, -1), 0)

            aim = self.geo.decode(fixture.head,
                                  (values[pan_o] << 8) | pan_fine,
                                  (values[tilt_o] << 8) | tilt_fine)
            frame = self.geo.frame(fixture.head)
            out.append([round(aim.bearing_delta - frame.bearing_delta_ball, 3),
                        round(aim.elev_deg - frame.elev_to_ball, 3)])
        return out

    def colors_for(self, values_by_fixture: dict[int, dict[int, int]]
                   ) -> dict[str, list[float]]:
        """{fixture name: RGB} for every fixture this scene colours.

        Covers both mechanisms: a mechanical wheel, where the value is looked up
        in the .qxf's own capability colours, and RGBW channels, where it is
        read directly. Doing both matters because the pinspots are RGBW and the
        movers are a wheel, and a converter that only understood one would
        silently drop half the colour library.

        Per fixture, not one colour per scene. The Split, Duo and Quad families
        give different heads different colours, and that is precisely what made
        them worth having -- flattening them to a single colour, or skipping
        them, loses a whole section of the old console.
        """
        out: dict[str, list[float]] = {}
        for fixture in self.rig.fixtures:
            values = values_by_fixture.get(fixture.fid)
            if not values:
                continue
            offsets = fixture.profile.offsets(fixture.mode)

            wheel = offsets.get(rigmod.COLOR_WHEEL)
            if wheel is not None and wheel in values:
                rgb = wheel_color(fixture.profile, fixture.mode, wheel, values[wheel])
                if rgb is not None:
                    out[fixture.name] = rgb
                # A value in an auto-change band is not a colour; leave the
                # fixture out rather than inventing one for it.
                continue

            rgb_offsets = [offsets.get(r) for r in
                           (rigmod.RED, rigmod.GREEN, rigmod.BLUE)]
            if all(o is not None for o in rgb_offsets) and \
                    any(o in values for o in rgb_offsets):
                out[fixture.name] = [round(values.get(o, 0) / 255.0, 4)
                                     for o in rgb_offsets]
        return out

    def intensity_for(self, values_by_fixture: dict[int, dict[int, int]]
                      ) -> Optional[float]:
        levels = []
        for fixture in self.rig.fixtures:
            values = values_by_fixture.get(fixture.fid)
            if not values:
                continue
            dim = fixture.profile.offsets(fixture.mode).get(rigmod.DIMMER)
            if dim is not None and dim in values:
                levels.append(values[dim] / 255.0)
        return round(max(levels), 3) if levels else None

    # -- the port ---------------------------------------------------------

    def scene_values(self, func) -> dict[int, dict[int, int]]:
        return {int(fv.get("ID")): parse_values(fv.text or "")
                for fv in func.findall(NS + "FixtureVal")}

    def port_scene(self, func) -> Optional[PortedLook]:
        name = func.get("Name", "?")
        values = self.scene_values(func)
        offsets = self.offsets_for(values)
        colors = self.colors_for(values)
        intensity = self.intensity_for(values)

        # One colour if every coloured fixture agrees, per-fixture if not.
        # Collapsing the uniform case keeps the common look portable to a rig
        # with a different number of heads, which per-fixture colours cannot be.
        color = None
        per_fixture = None
        if colors:
            distinct = {tuple(c) for c in colors.values()}
            if len(distinct) == 1:
                color = list(next(iter(distinct)))
            else:
                per_fixture = colors

        if offsets is None and color is None and per_fixture is None \
                and intensity is None:
            self.skipped.append((name, "writes nothing the engine models"))
            return None

        has_color = color is not None or per_fixture is not None
        kind = ("mixed" if offsets and has_color else
                "pose" if offsets else
                "color" if has_color else "intensity")
        look = PortedLook(name=name, kind=kind,
                          tags=["movers"] if offsets else [],
                          offsets=offsets, color=color, intensity=intensity,
                          source=f"Scene {func.get('ID')}")
        if per_fixture is not None:
            look.colors = per_fixture
            look.notes.append(
                f"per-fixture colour ({len(set(map(tuple, per_fixture.values())))} "
                f"distinct) -- a split, duo or quad")
        return look

    def port_chaser(self, func, scenes_by_id: dict[int, ET.Element]) -> Optional[PortedLook]:
        name = func.get("Name", "?")
        steps = []
        for step in sorted(func.findall(NS + "Step"),
                           key=lambda s: int(s.get("Number", 0))):
            scene = scenes_by_id.get(int((step.text or "0").strip()))
            if scene is None:
                continue
            offsets = self.offsets_for(self.scene_values(scene))
            if offsets is None:
                # Not a movement chase. It may still be a COLOUR chase -- the
                # Rainbow Wheel and Wheel Walk families step through wheel slots
                # rather than positions, and they are a named section of the old
                # console, so dropping them would lose real material.
                return self.port_color_chaser(func, scenes_by_id)
            steps.append(offsets)
        if len(steps) < 2:
            self.skipped.append((name, "fewer than two positional steps"))
            return None

        speed = func.find(NS + "Speed")
        duration = float(speed.get("Duration", 0)) if speed is not None else 0.0
        if duration <= 0:
            duration = 1000.0
        raw_bars = duration * len(steps) / self.ms_per_bar
        return PortedLook(
            name=name, kind="path", tags=["movers"], steps=steps,
            bars=snap_bars(raw_bars), source=f"Chaser {func.get('ID')}",
            notes=[f"{len(steps)} steps, {duration:.0f} ms each "
                   f"= {raw_bars:.2f} bars at {self.bpm:.0f} bpm, "
                   f"snapped to {snap_bars(raw_bars):g}"])

    def port_color_chaser(self, func, scenes_by_id: dict[int, ET.Element]
                          ) -> Optional[PortedLook]:
        """A chaser whose steps are colours rather than positions.

        Emitted as a list of colour frames rather than a path: colour on a
        mechanical wheel is a set of discrete slots, so interpolating between
        them would ask for values the wheel cannot produce and land on whichever
        slot happened to be nearest. Stepping is the honest representation of
        the hardware.
        """
        name = func.get("Name", "?")
        frames: list[dict[str, list[float]]] = []
        for step in sorted(func.findall(NS + "Step"),
                           key=lambda s: int(s.get("Number", 0))):
            scene = scenes_by_id.get(int((step.text or "0").strip()))
            if scene is None:
                continue
            colors = self.colors_for(self.scene_values(scene))
            if not colors:
                self.skipped.append((name, "a step is neither position nor colour"))
                return None
            frames.append(colors)
        if len(frames) < 2:
            self.skipped.append((name, "fewer than two colour steps"))
            return None

        speed = func.find(NS + "Speed")
        duration = float(speed.get("Duration", 0)) if speed is not None else 0.0
        if duration <= 0:
            duration = 1000.0
        raw_bars = duration * len(frames) / self.ms_per_bar
        return PortedLook(
            name=name, kind="color_path", tags=[], frames=frames,
            bars=snap_bars(raw_bars), source=f"Chaser {func.get('ID')}",
            notes=[f"{len(frames)} colour steps; stepped, not interpolated -- a "
                   f"mechanical wheel has no in-between slots"])

    def run(self, workspace: Path) -> list[PortedLook]:
        root = ET.parse(workspace).getroot()
        engine = root.find(NS + "Engine")
        functions = engine.findall(NS + "Function")
        scenes_by_id = {int(f.get("ID")): f for f in functions
                        if f.get("Type") == "Scene"}

        looks: list[PortedLook] = []
        for func in functions:
            kind = func.get("Type")
            name = func.get("Name", "?")
            # The `~`-prefixed functions are the hidden mirror mesh that
            # emulated radio buttons in XML. The engine has real state, so they
            # have no successor and porting them would recreate the problem.
            if name.startswith("~"):
                continue
            if kind == "Scene":
                ported = self.port_scene(func)
            elif kind == "Chaser":
                ported = self.port_chaser(func, scenes_by_id)
            elif kind in ("EFX", "Collection"):
                self.skipped.append((name, f"{kind} -- rebuild with motion primitives"))
                continue
            else:
                continue
            if ported is not None:
                looks.append(ported)
        return looks


def main() -> int:
    parser = argparse.ArgumentParser(description="Port a QLC+ library to the engine")
    parser.add_argument("--event", type=Path, default=REPO / "events" / "despacio")
    parser.add_argument("--workspace", type=Path)
    parser.add_argument("--bpm", type=float, default=124.0,
                        help="reference tempo for converting chaser ms to bars")
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    workspace = args.workspace or next(args.event.glob("*.qxw"))
    porter = Porter(args.event, reference_bpm=args.bpm)
    looks = porter.run(workspace)

    by_kind: dict[str, int] = {}
    for look in looks:
        by_kind[look.kind] = by_kind.get(look.kind, 0) + 1

    print(f"{workspace.name} -> {len(looks)} looks")
    for kind, count in sorted(by_kind.items()):
        print(f"  {count:>3} {kind}")
    print(f"  {len(porter.skipped)} skipped")
    reasons: dict[str, int] = {}
    for _name, reason in porter.skipped:
        reasons[reason] = reasons.get(reason, 0) + 1
    for reason, count in sorted(reasons.items(), key=lambda kv: -kv[1]):
        print(f"      {count:>3} {reason}")

    payload = {
        "_comment": [
            "Ported from the QLC+ workspace by shared/tools/port_library.py.",
            "",
            "Positions are OFFSETS from each head's own calibrated ball aim, not",
            "stored DMX -- so these looks track recalibration, which the originals",
            "could not. Chaser step lists became paths measured in bars, so slowing",
            "one down now makes it smoother rather than steppier.",
            "",
            "Regenerate rather than hand-editing: the workspace is still the",
            "source until QLC+ is retired.",
        ],
        "source": workspace.name,
        "reference_bpm": args.bpm,
        "looks": [l.to_json() for l in looks],
        "skipped": [{"name": n, "reason": r} for n, r in porter.skipped],
    }

    out = args.event / "looks.json"
    if args.write:
        out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        print(f"\nwrote {out}")
    else:
        print(f"\nnothing written -- pass --write to create {out.name}")
        for look in looks[:6]:
            print(f"  {look.kind:<6} {look.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
