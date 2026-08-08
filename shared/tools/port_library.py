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
from typing import Iterable, Optional

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
    # The rig groups this look actually writes, from the fixtures the scene
    # touched. Without it a scene that coloured only the pinspots ported as an
    # untagged uniform colour and repainted the movers too -- "Pin Ball Glow"
    # writes two pinspots in the workspace and was recolouring the whole rig.
    groups: list[str] = field(default_factory=list)
    offsets: Optional[list[list[float]]] = None      # per head, (bearing, elev)
    steps: Optional[list[list[list[float]]]] = None  # per step, per head
    color: Optional[list[float]] = None
    colors: Optional[dict[str, list[float]]] = None
    # Per fixture, 0..1. The pinspots are RGBW and several looks blend a real
    # white component -- "Pin Ball Glow" is RGB(120,60,20) plus W=80, and reading
    # only RGB ported it as a cooler, dimmer light with no complaint.
    whites: Optional[dict[str, float]] = None
    frames: Optional[list[dict[str, list[float]]]] = None
    bars: Optional[float] = None
    intensity: Optional[float] = None
    # Per fixture, where the scene dims fixtures differently. Collapsing this to
    # one number threw away exactly the information the per-fixture COLOUR
    # handling was added to preserve.
    intensities: Optional[dict[str, float]] = None
    # Per fixture, 0..1 across the fixture's own strobe band.
    strobes: Optional[dict[str, float]] = None
    # A level CHASE: per step, {fixture: level}. Without this the porter had no
    # concept of an intensity chaser at all, so "Spotlight", "Dim Chase" and
    # "Crowd Cascade" were skipped and their step scenes were left loose in the
    # library as separate looks -- the chase itself was unreachable.
    levels: Optional[list[dict[str, float]]] = None
    # Per step, alongside `levels` -- the Breathe pair chases the shutter rather
    # than the dimmer, so a level chase has to be able to carry both.
    strobe_steps: Optional[list[dict[str, float]]] = None
    # Name of the chaser this scene is a step OF, when one ported. "Spotlight
    # Step 1..4" are meaningless on their own and were cluttering the level list
    # with four entries for one routine; they stay reachable, but behind the
    # chase rather than beside it.
    step_of: Optional[str] = None
    source: str = ""
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        out: dict = {"name": self.name, "kind": self.kind, "tags": self.tags,
                     "groups": self.groups}
        if self.step_of:
            out["step_of"] = self.step_of
        for key in ("offsets", "steps", "frames", "levels", "strobe_steps",
                    "color", "colors", "whites", "bars", "intensity",
                    "intensities", "strobes"):
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

    def groups_for(self, names: Iterable[str]) -> list[str]:
        """The rig groups covering a set of fixture names.

        A group is a rig tag ("movers", "pinspots"). Reported per look so the
        engine can scope the layer, and so the UI can offer a filter that means
        something: the whole reason "colour" needed splitting by fixture type is
        that a pinspot palette and a mover palette are different decisions.
        """
        wanted = set(names)
        if not wanted:
            return []
        members = {tag: {f.name for f in self.rig.fixtures if tag in f.tags}
                   for tag in self.rig.tags()}

        # Tags entirely inside what the look wrote, biggest first, dropping any
        # that a bigger one already covers. "corner movers" and "movers" hold
        # the same four heads here, and listing both says nothing twice.
        covered: list[str] = []
        for tag in sorted(members, key=lambda t: (-len(members[t]), t)):
            if members[tag] and members[tag] <= wanted \
                    and not any(members[tag] <= members[c] for c in covered):
                covered.append(tag)
        if covered:
            return covered

        # Nothing fully covered: the look writes SOME of a group -- the
        # "Spotlight Step" scenes name three of four heads. Report the smallest
        # group that contains them all, because "movers" is both the honest
        # answer and the filter a person would look under. The per-fixture data
        # still scopes what actually changes; a fixture the look does not name
        # is left alone rather than blanked.
        containing = [t for t in members if wanted <= members[t]]
        if containing:
            return [min(containing, key=lambda t: (len(members[t]), t))]
        return sorted(wanted)

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

    def colors_for(self, values_by_fixture: dict[int, dict[int, int]],
                   whites: Optional[dict[str, float]] = None
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
                # A component the scene does not write reads as 0. Unlike
                # `offsets_for`, which refuses a partial position, there is no
                # way to express "set red and leave green alone" -- the engine's
                # colour is one RGB triple, not three channels. Verified against
                # the workspace: no scene writes a partial R/G/B set, so this
                # branch does not fire on the material being ported.
                out[fixture.name] = [round(values.get(o, 0) / 255.0, 4)
                                     for o in rgb_offsets]
                white = offsets.get(rigmod.WHITE)
                if whites is not None and white is not None and white in values:
                    whites[fixture.name] = round(values[white] / 255.0, 4)
        return out

    def strobe_for(self, values_by_fixture: dict[int, dict[int, int]]
                   ) -> Optional[dict[str, float]]:
        """{fixture name: 0..1 within its own strobe band}, for what strobes.

        Read against the profile's declared band rather than as a raw byte: the
        MJ-OS-018 is OPEN at 0-7 AND at 250-255, with the strobe between, so a
        raw value carries no meaning without the band. Fixtures sitting in an
        open band are left out entirely -- "shutter open" is the default and
        does not need a look to assert it.
        """
        out: dict[str, float] = {}
        for fixture in self.rig.fixtures:
            values = values_by_fixture.get(fixture.fid)
            if not values:
                continue
            offset = fixture.profile.offsets(fixture.mode).get(rigmod.STROBE)
            band = fixture.strobe_band()
            if offset is None or band is None or offset not in values:
                continue
            lo, hi = band
            value = values[offset]
            if lo <= value <= hi and hi > lo:
                out[fixture.name] = round((value - lo) / (hi - lo), 4)
        return out or None

    def intensity_for(self, values_by_fixture: dict[int, dict[int, int]]
                      ) -> tuple[Optional[float], Optional[dict[str, float]]]:
        """(overall level, per-fixture levels where they differ).

        The per-fixture half exists for the same reason the colour half does: a
        scene that dims two heads differently is expressing something, and
        flattening it to the maximum turns a two-level look into a flat one.
        Where every fixture agrees, only the single number is emitted -- that
        case stays portable to a rig with different fixture names.
        """
        levels: dict[str, float] = {}
        for fixture in self.rig.fixtures:
            values = values_by_fixture.get(fixture.fid)
            if not values:
                continue
            dim = fixture.profile.offsets(fixture.mode).get(rigmod.DIMMER)
            if dim is not None and dim in values:
                levels[fixture.name] = round(values[dim] / 255.0, 3)
        if not levels:
            return None, None
        overall = round(max(levels.values()), 3)
        if len(set(levels.values())) == 1:
            return overall, None
        return overall, levels

    # -- the port ---------------------------------------------------------

    def scene_values(self, func) -> dict[int, dict[int, int]]:
        return {int(fv.get("ID")): parse_values(fv.text or "")
                for fv in func.findall(NS + "FixtureVal")}

    def port_scene(self, func) -> Optional[PortedLook]:
        name = func.get("Name", "?")
        values = self.scene_values(func)
        offsets = self.offsets_for(values)
        whites: dict[str, float] = {}
        colors = self.colors_for(values, whites)
        intensity, intensities = self.intensity_for(values)
        strobes = self.strobe_for(values)

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
                and intensity is None and strobes is None:
            self.skipped.append((name, "writes nothing the engine models"))
            return None

        has_color = color is not None or per_fixture is not None
        kind = ("mixed" if offsets and has_color else
                "pose" if offsets else
                "color" if has_color else "intensity")
        written = {f.name for f in self.rig.fixtures if values.get(f.fid)}
        look = PortedLook(name=name, kind=kind,
                          tags=["movers"] if offsets else [],
                          groups=self.groups_for(written),
                          offsets=offsets, color=color, intensity=intensity,
                          intensities=intensities, strobes=strobes,
                          whites={k: v for k, v in whites.items() if v > 0} or None,
                          source=f"Scene {func.get('ID')}")
        if strobes:
            look.notes.append(
                f"strobe on {', '.join(sorted(strobes))} -- 0..1 across the "
                f"fixture's own band, since the raw byte means nothing without it")
        if look.whites:
            look.notes.append(
                f"white channel on {', '.join(sorted(look.whites))} -- these are "
                f"RGBW, and the W is part of the colour")
        if look.intensities:
            look.notes.append("per-fixture levels differ")
        if per_fixture is not None:
            look.colors = per_fixture
            look.notes.append(
                f"per-fixture colour ({len(set(map(tuple, per_fixture.values())))} "
                f"distinct) -- a split, duo or quad")
        return look

    def port_chaser(self, func, scenes_by_id: dict[int, ET.Element]) -> Optional[PortedLook]:
        name = func.get("Name", "?")
        steps = []
        dropped = 0
        for step in sorted(func.findall(NS + "Step"),
                           key=lambda s: int(s.get("Number", 0))):
            scene = scenes_by_id.get(int((step.text or "0").strip()))
            if scene is None:
                continue
            offsets = self.offsets_for(self.scene_values(scene))
            if offsets is None:
                # A step that writes no position. Skip the STEP, not the chase:
                # abandoning the whole chaser here threw away "Build", six good
                # positional steps, because its seventh wrote nothing.
                dropped += 1
                continue
            steps.append(offsets)
        if not steps:
            # No positional step at all. It may still be a COLOUR chase -- the
            # Rainbow Wheel and Wheel Walk families step through wheel slots
            # rather than positions -- or a LEVEL chase, which is what Spotlight,
            # Dim Chase and Crowd Cascade are. All three are named sections of
            # the old console, so dropping any of them loses real material.
            #
            # Only the LAST branch records a skip. Letting each failing branch
            # append one put the same chaser in the list three times under three
            # different reasons, which reads as three lost looks.
            colour = self.port_color_chaser(func, scenes_by_id, record=False)
            if colour is not None:
                return colour
            return self.port_level_chaser(func, scenes_by_id)
        if len(steps) < 2:
            self.skipped.append((name, "fewer than two positional steps"))
            return None

        speed = func.find(NS + "Speed")
        duration = float(speed.get("Duration", 0)) if speed is not None else 0.0
        if duration <= 0:
            duration = 1000.0
        raw_bars = duration * len(steps) / self.ms_per_bar
        notes = [f"{len(steps)} steps, {duration:.0f} ms each "
                 f"= {raw_bars:.2f} bars at {self.bpm:.0f} bpm, "
                 f"snapped to {snap_bars(raw_bars):g}"]
        if dropped:
            notes.append(f"{dropped} step(s) wrote no position and were skipped")
        return PortedLook(
            name=name, kind="path", tags=["movers"],
            groups=self.groups_for(f.name for f in self.rig.movers), steps=steps,
            bars=snap_bars(raw_bars), source=f"Chaser {func.get('ID')}",
            notes=notes)

    def port_color_chaser(self, func, scenes_by_id: dict[int, ET.Element],
                          record: bool = True) -> Optional[PortedLook]:
        """A chaser whose steps are colours rather than positions.

        Emitted as a list of colour frames rather than a path: colour on a
        mechanical wheel is a set of discrete slots, so interpolating between
        them would ask for values the wheel cannot produce and land on whichever
        slot happened to be nearest. Stepping is the honest representation of
        the hardware.
        """
        name = func.get("Name", "?")
        frames: list[dict[str, list[float]]] = []
        dropped = 0
        for step in sorted(func.findall(NS + "Step"),
                           key=lambda s: int(s.get("Number", 0))):
            scene = scenes_by_id.get(int((step.text or "0").strip()))
            if scene is None:
                continue
            step_whites: dict[str, float] = {}
            colors = self.colors_for(self.scene_values(scene), step_whites)
            if not colors:
                # Skip the step, not the chase -- same reasoning as
                # `port_chaser`, and it makes the skip reason honest when what
                # is really wrong is that too few steps survived.
                dropped += 1
                continue
            # A frame is [r, g, b] or [r, g, b, w]. "Pin Drift" steps through
            # the same warm pastels the Pin scenes hold, and those are RGBW --
            # so a chase frame needs the W as much as a scene does.
            frames.append({name: (rgb + [step_whites[name]]
                                  if step_whites.get(name) else rgb)
                           for name, rgb in colors.items()})
        if len(frames) < 2:
            if record:
                self.skipped.append((name, "fewer than two colour steps"))
            return None

        speed = func.find(NS + "Speed")
        duration = float(speed.get("Duration", 0)) if speed is not None else 0.0
        if duration <= 0:
            duration = 1000.0
        raw_bars = duration * len(frames) / self.ms_per_bar
        return PortedLook(
            name=name, kind="color_path", tags=[],
            groups=self.groups_for({n for fr in frames for n in fr}), frames=frames,
            bars=snap_bars(raw_bars), source=f"Chaser {func.get('ID')}",
            notes=[f"{len(frames)} colour steps; stepped, not interpolated -- a "
                   f"mechanical wheel has no in-between slots"]
                  + ([f"{dropped} step(s) wrote no colour and were skipped"]
                     if dropped else []))

    def port_level_chaser(self, func, scenes_by_id: dict[int, ET.Element]
                          ) -> Optional[PortedLook]:
        """A chaser whose steps differ only in LEVEL.

        The whole "Spotlight" / "Dim Chase" / "Crowd Cascade" family, plus the
        Breathe pair. The porter previously had no third branch here, so each of
        those chasers was skipped and its step scenes were left loose in the
        library as separate one-step looks -- the chase itself unreachable, and
        the steps individually meaningless.

        Stepped rather than interpolated, matching the colour chases: the source
        is a step list, and inventing a fade between two dimmer levels would be
        asserting a shape the original never had. A level chase composes as a
        MULTIPLIER over whatever colour and position are running -- see
        `library.level_layers`.
        """
        name = func.get("Name", "?")
        levels: list[dict[str, float]] = []
        strobes: list[dict[str, float]] = []
        for step in sorted(func.findall(NS + "Step"),
                           key=lambda s: int(s.get("Number", 0))):
            scene = scenes_by_id.get(int((step.text or "0").strip()))
            if scene is None:
                continue
            values = self.scene_values(scene)
            overall, per_fixture = self.intensity_for(values)
            step_strobe = self.strobe_for(values)
            # An EMPTY step is kept, unlike in the position and colour chases.
            # "MH Breathe" is literally Breathe On / Breathe Off, and the Off
            # half writes nothing the engine models -- dropping it would leave a
            # one-step chase that never breathes. In a level chase, "back to
            # normal" is a step.
            if per_fixture is None and overall is not None:
                per_fixture = {f.name: overall for f in self.rig.fixtures
                               if f.profile.offsets(f.mode).get(rigmod.DIMMER)
                               in (values.get(f.fid) or {})}
            levels.append(per_fixture or {})
            strobes.append(step_strobe or {})
        if len(levels) < 2 or not any(levels) and not any(strobes):
            self.skipped.append((
                name, "fewer than two level steps" if len(levels) < 2
                else "every step of this chase is empty"))
            return None

        speed = func.find(NS + "Speed")
        duration = float(speed.get("Duration", 0)) if speed is not None else 0.0
        if duration <= 0:
            duration = 1000.0
        raw_bars = duration * len(levels) / self.ms_per_bar
        notes = [f"{len(levels)} level steps, {duration:.0f} ms each "
                 f"= {raw_bars:.2f} bars, snapped to {snap_bars(raw_bars):g}",
                 "applied as a MULTIPLIER, so it composes with whatever colour "
                 "and position are running rather than replacing them"]
        return PortedLook(
            name=name, kind="level_path", tags=[],
            groups=self.groups_for({n for fr in levels for n in fr}
                                   | {n for fr in strobes for n in fr}), levels=levels,
            strobe_steps=strobes if any(strobes) else None,
            bars=snap_bars(raw_bars), source=f"Chaser {func.get('ID')}",
            notes=notes)

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

        # Mark scenes that are a step of a chase that ported. "Spotlight Step 1"
        # is not a look anyone wants to pick; four of them beside the Spotlight
        # chase itself is the flat-list problem the whole port exists to fix.
        # Kept rather than dropped -- picking a single step is a legitimate, if
        # rare, thing to do -- but the UI files them under the parent.
        by_name = {look.name: look for look in looks}
        for func in functions:
            if func.get("Type") != "Chaser" or func.get("Name", "?") not in by_name:
                continue
            for step in func.findall(NS + "Step"):
                scene = scenes_by_id.get(int((step.text or "0").strip()))
                if scene is None:
                    continue
                child = by_name.get(scene.get("Name", ""))
                # Only scenes NAMED after the parent. Being used by a chase does
                # not make a scene an artefact of it -- "MH Red" and
                # "Heads - Ball" are first-class looks that several chases
                # happen to step through, and filing them under a parent would
                # hide most of the library. "Spotlight Step 1" and
                # "MH Breathe On" are the real artefacts, and they say so in
                # their own names.
                if (child is not None and child.source.startswith("Scene")
                        and child.name.startswith(func.get("Name", "\0"))):
                    child.step_of = func.get("Name")
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
