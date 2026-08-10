"""
What is patched, what each of its channels means, and where in the DMX frame it
lives.

The engine never addresses a channel by number. It addresses a fixture by
**role** ("corner movers", "pinspots") and a channel by **function** (dimmer,
pan, red), and this module resolves that down to universe + address. That
indirection is what F9 needs to run a look built for four heads on a club rig
with eight, and it is why looks in this engine reference roles rather than
fixture IDs.

Channel meaning is read from the QLC+ `.qxf` profiles in `shared/fixtures/`,
which are already the authority for both events and for the GDTF build. Reading
them beats re-describing the hardware in a second format that can drift -- and
`legacy/blenderdmx/build_gdtf.py` is the cautionary example, since it
hand-transcribes its channel maps and can silently disagree with the `.qxf` it
claims to read. It is retired, and that is part of why.

**Merge semantics come from QLC+'s rule**, kept deliberately: a channel in the
Intensity group is HTP (highest takes precedence), everything else is LTP (latest
takes precedence). Every profile in the repo was authored against that rule and
the ported look library assumes it. Note the engine does NOT inherit QLC+'s
consequence of it -- that a Scene could only ever push a head brighter than the
dimmer fader, never darker, which forced a whole auto-park subsystem into the web
UI. Here the dimmer is a layer the engine owns outright.

**Held channels are asserted every frame.** QLC+ had no way to say "this channel
is always 0", so the show's workaround was to make every pose-managed scene
re-assert Reset=0 -- because a stuck LTP channel had already put every head into
a permanent reset loop once. The engine states it once, in the rig file, and the
baseline is written into the frame before any layer runs.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Iterable, Optional

from . import config as configmod
from . import geometry as geo
from .venue import Venue, load_venue

QXF_NS = "{http://www.qlcplus.org/FixtureDefinition}"

REPO = Path(__file__).resolve().parent.parent

# shared/fixtures/ first and always: it is the only root that survives a fresh
# clone. The vendored QLC+ checkout is gitignored, and a profile that lives only
# there is a profile the next machine cannot resolve -- which is exactly what
# happened to the pinspot until 2026-08-06.
QXF_SEARCH_ROOTS = [
    REPO / "shared" / "fixtures",
    Path.home() / "QLC+" / "Fixtures",
    REPO / "qlcplus" / "resources" / "fixtures",
]


# ------------------------------------------------------------------- roles --

# Semantic channel functions the engine knows how to drive. Anything a profile
# declares that is not in here stays addressable by channel name but no layer
# will write it automatically -- the engine would rather leave a channel alone
# than guess at it.
PAN, PAN_FINE, TILT, TILT_FINE = "pan", "pan_fine", "tilt", "tilt_fine"
DIMMER = "dimmer"
RED, GREEN, BLUE, WHITE = "red", "green", "blue", "white"
COLOR_WHEEL, GOBO, STROBE = "color_wheel", "gobo", "strobe"
PT_SPEED, RESET, EFFECT = "pt_speed", "reset", "effect"

# QLCChannel presets, mapped to (role, QLC+ group). Only presets our profiles
# actually use -- deliberately not a reimplementation of QLC+'s full switch, so
# an unknown preset falls through to the group/name heuristic below and is
# reported rather than silently mis-assigned.
PRESET_ROLES: dict[str, tuple[str, str]] = {
    "PositionPan": (PAN, "Position"),
    "PositionPanFine": (PAN_FINE, "Position"),
    "PositionTilt": (TILT, "Position"),
    "PositionTiltFine": (TILT_FINE, "Position"),
    "IntensityDimmer": (DIMMER, "Intensity"),
    "IntensityMasterDimmer": (DIMMER, "Intensity"),
    "IntensityRed": (RED, "Intensity"),
    "IntensityGreen": (GREEN, "Intensity"),
    "IntensityBlue": (BLUE, "Intensity"),
    "IntensityWhite": (WHITE, "Intensity"),
}

# Fallback for a channel with an explicit <Group> and no known preset.
#
# Effect and Maintenance are deliberately absent. Those groups hold channels
# whose meaning is entirely fixture-specific -- "Auto FX", "Total function
# control", "Pattern", "Motor Rotation" -- and a generic role would be a guess
# dressed up as knowledge. They stay addressable by channel NAME (see
# PatchedFixture.index_of), which is how the pinspot's mode selector is driven.
#
# Speed is here only for a genuine pan/tilt speed channel, matched by name: a
# Derby's "Laser Motor" is also in the Speed group and is not that.
GROUP_ROLES: dict[str, str] = {
    "Shutter": STROBE,
    "Colour": COLOR_WHEEL,
    "Gobo": GOBO,
    "Intensity": DIMMER,
}


def _hex_rgb(text: Optional[str]) -> Optional[tuple[int, int, int]]:
    """`#rrggbb` as a 0-255 triple, or None for anything else."""
    if text and text.startswith("#") and len(text) == 7:
        try:
            return (int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16))
        except ValueError:
            return None
    return None


def _resolve_split_slots(caps: list[Capability]) -> list[Capability]:
    """Fill in `Capability.pair` for wheel slots that name two colours.

    A colour wheel's in-between positions put half of one segment and half of
    the next in front of the lens, and the beam comes out split down the middle
    rather than blended. `.qxf` names these by convention -- "Green + Blue" --
    but records a single approximate tint for them, so the two real colours have
    to come from somewhere else.

    They come from the SAME CHANNEL's own single-colour slots, which is the
    point: "Green + Blue" resolves to exactly the `#00ff00` and `#0000ff` that
    channel already declares for Green and for Blue. No colour-name table, no
    guessing -- a profile is internally consistent or the pair is left None and
    the slot keeps behaving exactly as it did before.

    Deliberately tolerant: an unresolvable name is not an error. Plenty of
    capabilities have a "+" in them for other reasons, and a profile is allowed
    to describe hardware we cannot draw perfectly.
    """
    single = {c.label.strip().lower(): c.rgb
              for c in caps if c.rgb is not None and "+" not in c.label}
    out = []
    for cap in caps:
        if cap.pair is None and cap.rgb is not None and "+" in cap.label:
            parts = [p.strip().lower() for p in cap.label.split("+")]
            if len(parts) == 2:
                first, second = single.get(parts[0]), single.get(parts[1])
                if first is not None and second is not None:
                    cap = replace(cap, pair=(first, second))
        out.append(cap)
    return out


def merge_for_group(group: Optional[str]) -> str:
    """QLC+'s merge rule: Intensity is HTP, everything else LTP."""
    return "HTP" if group == "Intensity" else "LTP"


# ---------------------------------------------------------------- profiles --

@dataclass(frozen=True)
class Capability:
    """One band of a channel's range, with the colour it produces if it is a
    colour-wheel slot. `.qxf` records these as Res1="#rrggbb"."""
    lo: int
    hi: int
    label: str
    rgb: Optional[tuple[int, int, int]] = None
    # The TWO colours actually in the aperture, when this slot is a split.
    #
    # A colour wheel is a disc of coloured segments, and the positions between
    # two of them put half of each in front of the lens -- so the beam comes out
    # two-toned, split across its width, rather than blended. The MingJie wheel
    # declares seven of these (80-139: "Cyan + Pink" through "Yellow + Red") and
    # they are half the colours the show actually uses.
    #
    # `rgb` stays whatever the `.qxf` says, which for these slots is a single
    # approximate tint -- fine for "what colour is this roughly", which is what
    # the engine's nearest-slot colour matching wants, and useless for drawing
    # one. This is the pair, and it is None for an ordinary single-colour slot.
    pair: Optional[tuple[tuple[int, int, int], tuple[int, int, int]]] = None

    @property
    def is_split(self) -> bool:
        return self.pair is not None

    @property
    def mid(self) -> int:
        """A value safely inside the band. Slots are narrow (10 wide on the
        MingJie wheel) and the edges are where a fixture's own rounding puts you
        in the neighbouring colour, so aim for the middle."""
        return (self.lo + self.hi) // 2


@dataclass(frozen=True)
class ChannelDef:
    name: str
    role: Optional[str]
    group: Optional[str]
    preset: Optional[str]
    default: int = 0
    capabilities: tuple[Capability, ...] = ()

    @property
    def merge(self) -> str:
        return merge_for_group(self.group)

    @property
    def color_slots(self) -> tuple[Capability, ...]:
        """Capabilities that name an actual colour.

        This is what lets one colour picker drive both kinds of fixture: an
        RGBW pinspot takes the value directly, and a fixture with a mechanical
        wheel snaps to its nearest slot. The despacio movers have 14 slots and
        no colour mixing at all, so without this they can only be driven by slot
        number -- which is why the old UI had colour buttons rather than a
        picker.
        """
        return tuple(c for c in self.capabilities if c.rgb is not None)


@dataclass(frozen=True)
class FixtureProfile:
    manufacturer: str
    model: str
    type: str
    channels: dict[str, ChannelDef]
    modes: dict[str, tuple[str, ...]]      # mode name -> channel names, in order
    pan_max_deg: float
    tilt_max_deg: float
    beam_deg: float
    lumens: float                           # 0 when the .qxf does not declare it
    path: Path

    @property
    def key(self) -> tuple[str, str]:
        return (self.manufacturer, self.model)

    def offsets(self, mode: str) -> dict[str, int]:
        """{role: first 0-based offset} for one mode.

        First occurrence, not only occurrence: multi-cell fixtures (pixel bars,
        matrix washes -- we own a YeeSite bar) legitimately repeat a role once
        per cell. Taking the first drives cell 1 and leaves the rest dark, which
        is wrong but visible, and `duplicate_roles()` says so out loud rather
        than letting it pass as full support.
        """
        if mode not in self.modes:
            raise KeyError(
                f"{self.manufacturer} {self.model!r} has no mode {mode!r} "
                f"(has: {', '.join(self.modes)})")
        out: dict[str, int] = {}
        for i, chan_name in enumerate(self.modes[mode]):
            role = self.channels[chan_name].role
            if role is not None:
                out.setdefault(role, i)
        return out

    def duplicate_roles(self, mode: str) -> dict[str, list[str]]:
        """Roles claimed by more than one channel -- i.e. cells this engine does
        not yet drive individually. {role: [channel names]}."""
        seen: dict[str, list[str]] = {}
        for chan_name in self.modes[mode]:
            role = self.channels[chan_name].role
            if role is not None:
                seen.setdefault(role, []).append(chan_name)
        return {r: names for r, names in seen.items() if len(names) > 1}

    def unmapped(self, mode: str) -> list[str]:
        """Channel names in `mode` the engine has no role for. Surfaced rather
        than swallowed: on a club rig these are the channels a new fixture needs
        support for."""
        return [c for c in self.modes[mode] if self.channels[c].role is None]


def _text(el, tag: str) -> Optional[str]:
    found = el.find(QXF_NS + tag)
    return None if found is None else found.text


def parse_qxf(path: Path) -> FixtureProfile:
    root = ET.parse(path).getroot()   # root IS <FixtureDefinition>

    channels: dict[str, ChannelDef] = {}
    for c in root.findall(QXF_NS + "Channel"):
        name = c.get("Name")
        preset = c.get("Preset")
        group_el = c.find(QXF_NS + "Group")
        group = group_el.text if group_el is not None and group_el.text else None

        role: Optional[str] = None
        if preset in PRESET_ROLES:
            role, preset_group = PRESET_ROLES[preset]
            # An explicit <Group> wins if present, matching QLC+; in practice
            # preset-bearing channels omit it.
            group = group or preset_group
        elif group in GROUP_ROLES:
            role = GROUP_ROLES[group]
        else:
            lowered = (name or "").lower()
            # The two group-ambiguous channels worth naming, both matched on the
            # channel name rather than the group -- see GROUP_ROLES.
            if group == "Maintenance" and "reset" in lowered:
                role = RESET
            elif group == "Speed" and "pan" in lowered and "tilt" in lowered:
                role = PT_SPEED

        caps = []
        for cap in c.findall(QXF_NS + "Capability"):
            rgb = _hex_rgb(cap.get("Res1"))
            # Res2 is QLC+'s own way of saying "this slot is two colours at
            # once". None of our profiles use it, but honouring it first means a
            # profile that does needs no name to parse.
            second = _hex_rgb(cap.get("Res2"))
            caps.append(Capability(
                lo=int(cap.get("Min")), hi=int(cap.get("Max")),
                label=(cap.text or "").strip(), rgb=rgb,
                pair=None if (rgb is None or second is None) else (rgb, second)))
        caps = _resolve_split_slots(caps)

        channels[name] = ChannelDef(name=name, role=role, group=group,
                                    preset=preset, default=int(c.get("Default") or 0),
                                    capabilities=tuple(caps))

    modes: dict[str, tuple[str, ...]] = {}
    for m in root.findall(QXF_NS + "Mode"):
        entries = sorted(
            ((int(ch.get("Number")), ch.text) for ch in m.findall(QXF_NS + "Channel")),
            key=lambda e: e[0])
        numbers = [n for n, _ in entries]
        if numbers != list(range(len(numbers))):
            raise ValueError(f"{path}: mode {m.get('Name')!r} channel numbers "
                             f"are not 0..n-1: {numbers}")
        modes[m.get("Name")] = tuple(name for _, name in entries)

    physical = root.find(QXF_NS + "Physical")
    pan_max = tilt_max = 0.0
    beam = 0.0
    lumens = 0.0
    if physical is not None:
        focus = physical.find(QXF_NS + "Focus")
        if focus is not None:
            pan_max = float(focus.get("PanMax") or 0)
            tilt_max = float(focus.get("TiltMax") or 0)
        lens = physical.find(QXF_NS + "Lens")
        if lens is not None:
            # DegreesMin/Max are equal on a fixed lens; on a zoom they are the
            # ends of its range and the narrow end is the conservative choice
            # for safety work -- a narrow beam concentrates more energy.
            beam = float(lens.get("DegreesMin") or 0)
        bulb = physical.find(QXF_NS + "Bulb")
        if bulb is not None:
            # Output at full, for previz only -- nothing in the show path reads
            # it. Most .qxf files in the wild leave it at 0, so every consumer
            # has to have an answer for "not declared"; it is read here rather
            # than typed into the previz because the two fixtures we own DO
            # declare it (1300 and 48), and a 27:1 ratio is not something to
            # eyeball.
            # NOTE this is BULB lumens -- what the emitter makes, not what
            # leaves the lens. On a 3-degree beam most of it never gets out of
            # the fixture, and on a wide fresnel most of it does, so comparing
            # two profiles' Lumens directly overstates how much brighter the
            # narrow one really is. `rig.json` can override it per fixture,
            # which is where a measured number belongs.
            lumens = float(bulb.get("Lumens") or 0)

    return FixtureProfile(
        manufacturer=_text(root, "Manufacturer") or "?",
        model=_text(root, "Model") or "?",
        type=_text(root, "Type") or "?",
        channels=channels, modes=modes,
        pan_max_deg=pan_max, tilt_max_deg=tilt_max, beam_deg=beam,
        lumens=lumens, path=path)


class ProfileLibrary:
    """Fixture profiles, resolved lazily by (manufacturer, model).

    Roots are searched in order and the first hit wins, so `shared/fixtures/`
    shadows a vendored copy rather than the other way round -- the repo's own
    definition is what the show was built against, and it is the only one that
    survives a fresh clone.

    Lazy on purpose. The vendored QLC+ checkout holds ~1700 profiles; parsing it
    on every engine start would cost seconds to answer a question about six
    fixtures. `shared/fixtures/` is small enough to index eagerly, and a deeper
    root is only walked when something is genuinely missing from it -- at which
    point that profile should be copied into `shared/fixtures/` anyway.
    """

    def __init__(self, roots: Optional[Iterable[Path]] = None):
        self.roots = list(roots if roots is not None else QXF_SEARCH_ROOTS)
        self._cache: dict[tuple[str, str], Optional[FixtureProfile]] = {}
        self._scanned: set[Path] = set()
        if self.roots:
            self._scan(self.roots[0])

    def _scan(self, root: Path) -> None:
        if root in self._scanned:
            return
        self._scanned.add(root)
        if not root.exists():
            return
        for path in sorted(root.rglob("*.qxf")):
            try:
                profile = parse_qxf(path)
            except (ET.ParseError, ValueError, TypeError):
                continue   # a malformed profile is not this engine's problem
            self._cache.setdefault(profile.key, profile)

    def get(self, manufacturer: str, model: str) -> Optional[FixtureProfile]:
        key = (manufacturer, model)
        if key in self._cache:
            return self._cache[key]
        for root in self.roots:
            if root in self._scanned:
                continue
            self._scan(root)
            if key in self._cache:
                return self._cache[key]
        self._cache[key] = None
        return None

    def indexed(self) -> dict[tuple[str, str], FixtureProfile]:
        """Everything scanned so far -- the primary root unless a miss forced a
        deeper walk. Not "every profile that exists"."""
        return {k: v for k, v in self._cache.items() if v is not None}


# ------------------------------------------------------------------- patch --

@dataclass(frozen=True)
class PatchedFixture:
    """One physical unit at one address, with a resolved role -> address map."""
    fid: int
    name: str
    profile: FixtureProfile
    mode: str
    universe: int
    address: int                            # 1-based DMX start, as on the fixture
    tags: tuple[str, ...] = ()              # "corner movers", "pinspots", ...
    head: Optional[int] = None              # index into Rig.geometry.heads
    # Where the unit hangs, in mm, y up, origin front-left floor corner -- the
    # venue.json frame. Present for anything rig.json places, moving or not:
    # previz needs a static fixture's position to draw it, and the taper needs a
    # mover's to aim it, and those are the same fact.
    position: Optional[tuple[float, float, float]] = None
    # What this unit actually puts out, overriding the .qxf's Bulb Lumens. The
    # profile figure is the EMITTER's output, and how much of it clears the
    # optics differs enormously between a 3-degree beam and a wide fresnel -- so
    # two profiles' Lumens are not comparable, and previz brightness ratios were
    # visibly wrong when taken from them. Set this when the real output is known.
    lumens: Optional[float] = None
    # The full cone angle this unit really throws, overriding the .qxf's Lens
    # DegreesMin. Same reasoning as `lumens`: the profile is what the maker
    # claims, and on cheap fixtures it is optimistic. This one is NOT cosmetic --
    # `safety.clearance` sizes the beam's half-width at range from it, so a wider
    # number makes the taper dim earlier. That is the conservative direction, but
    # it does change the show, so it belongs in the rig file where it is visible
    # rather than buried in a shared profile.
    beam_deg: Optional[float] = None
    # How fast the yoke actually slews, deg/s per axis. Nothing in a .qxf can
    # carry this -- QLC+ has no field for it -- so `engine.servo` falls back to
    # a published figure for the fixture class, and this is where a MEASURED one
    # goes. Previz-only today: the show sends a command and the fixture's own
    # servo obeys it, but a previz that ignores the servo teleports.
    pan_speed_deg_s: Optional[float] = None
    tilt_speed_deg_s: Optional[float] = None
    hold: dict[str, int] = field(default_factory=dict)   # role -> value, every frame
    notes: str = ""

    @property
    def output_beam_deg(self) -> float:
        """This unit's full cone angle: the rig's number if it has one, else the
        profile's, else 3 degrees."""
        return float(self.beam_deg if self.beam_deg is not None
                     else (self.profile.beam_deg or 3.0))

    @property
    def output_lumens(self) -> float:
        """This unit's output at full: the rig's number if it has one, else the
        profile's, else 0 for "not declared" -- which every caller must handle,
        since most `.qxf` files in the wild leave it at 0."""
        return float(self.lumens if self.lumens is not None
                     else self.profile.lumens)

    @property
    def channel_count(self) -> int:
        return len(self.profile.modes[self.mode])

    @property
    def last_address(self) -> int:
        return self.address + self.channel_count - 1

    def offset_of(self, key: str) -> Optional[int]:
        """0-based offset of a role, or failing that of a literal channel name.

        Role first so looks stay portable across fixtures; channel name as the
        escape hatch, because the channels that matter most on cheap fixtures
        are exactly the ones no generic role fits -- the pinspot's "Total
        function control" being the live example.
        """
        offsets = self.profile.offsets(self.mode)
        if key in offsets:
            return offsets[key]
        try:
            return self.profile.modes[self.mode].index(key)
        except ValueError:
            return None

    def address_of(self, key: str) -> Optional[int]:
        """1-based DMX address, or None if this mode has no such channel."""
        off = self.offset_of(key)
        return None if off is None else self.address + off

    def index_of(self, key: str) -> Optional[int]:
        """0-based index into a 512-byte frame buffer."""
        addr = self.address_of(key)
        return None if addr is None else addr - 1

    def has(self, key: str) -> bool:
        return self.offset_of(key) is not None

    def strobe_band(self) -> Optional[tuple[int, int]]:
        """The shutter channel's slow-to-fast range, read from the profile.

        Found by label rather than hardcoded, because the value that means
        "strobing" is entirely fixture-specific -- on the MJ-OS-018 it is 8-249
        with OPEN on both sides of it, so a naive 0-255 ramp would spend each end
        of its travel not strobing at all. Returns None when the profile
        declares a shutter with no recognisable strobe band, which is the honest
        answer: better to leave the channel alone than to guess a value.
        """
        offset = self.offset_of(STROBE)
        if offset is None:
            return None
        channel = self.profile.channels[self.profile.modes[self.mode][offset]]
        for cap in channel.capabilities:
            if "strobe" in cap.label.lower() and "no" not in cap.label.lower():
                return (cap.lo, cap.hi)
        return None

    @property
    def is_mover(self) -> bool:
        return self.has(PAN) and self.has(TILT)

    @property
    def is_16bit(self) -> bool:
        return self.has(PAN_FINE) and self.has(TILT_FINE)


@dataclass
class Rig:
    """A patch plus the geometry of whatever in it moves."""
    name: str
    fixtures: tuple[PatchedFixture, ...]
    geometry: Optional[geo.RigGeometry] = None
    venue: Optional[Venue] = None
    # Where the venue was actually read from, which is no longer derivable from
    # the event directory now that a room can live in shared/venues/. Anything
    # that reads or writes the room -- the taper policy, the UI's venue form --
    # has to use this rather than re-deriving a path and quietly editing the
    # wrong file.
    venue_file: Optional[Path] = None

    def by_tag(self, tag: str) -> tuple[PatchedFixture, ...]:
        return tuple(f for f in self.fixtures if tag in f.tags)

    def tags(self) -> tuple[str, ...]:
        """Every distinct group in the rig, biggest first.

        These are the fixture TYPES the UI filters by and the engine scopes
        looks to. Biggest first so a look covering the whole rig is described by
        the broadest tag that fits rather than by an arbitrary one.

        Tags holding exactly the same fixtures are collapsed to one. The
        despacio rig tags its heads both "movers" and "corner movers", which are
        the same four units -- offering both as filters would put "Movers" in
        the row twice and make the choice between them meaningless.
        """
        members: dict[str, frozenset[str]] = {}
        for fixture in self.fixtures:
            for tag in fixture.tags:
                members.setdefault(tag, frozenset())
        for tag in members:
            members[tag] = frozenset(f.name for f in self.fixtures if tag in f.tags)

        out: list[str] = []
        seen: set[frozenset[str]] = set()
        for tag in sorted(members, key=lambda t: (-len(members[t]), t)):
            if members[tag] and members[tag] not in seen:
                seen.add(members[tag])
                out.append(tag)
        return tuple(out)

    def by_id(self, fid: int) -> PatchedFixture:
        for f in self.fixtures:
            if f.fid == fid:
                return f
        raise KeyError(f"no fixture with id {fid}")

    @property
    def universes(self) -> tuple[int, ...]:
        return tuple(sorted({f.universe for f in self.fixtures}))

    @property
    def movers(self) -> tuple[PatchedFixture, ...]:
        """Movers in head order, so index i lines up with geometry.heads[i]."""
        return tuple(sorted((f for f in self.fixtures if f.head is not None),
                            key=lambda f: f.head))

    def baseline(self, universe: int) -> bytearray:
        """A 512-byte frame with every held channel asserted.

        Written before any layer runs, every frame. That is the structural fix
        for the stuck-LTP-channel class of bug: a value that must always be
        present cannot depend on some function happening to be running.
        """
        frame = bytearray(512)
        for f in self.fixtures:
            if f.universe != universe:
                continue
            for key, value in f.hold.items():
                idx = f.index_of(key)
                if idx is None:
                    raise KeyError(
                        f"{f.name}: hold names {key!r}, which is neither a role "
                        f"nor a channel of mode {f.mode!r} "
                        f"(channels: {', '.join(f.profile.modes[f.mode])})")
                frame[idx] = max(0, min(255, int(value)))
        return frame

    def validate(self) -> list[str]:
        """Overlaps, out-of-range addresses, and geometry/patch disagreement.

        Overlap is checked per universe: the same channel in two different
        universes is not a conflict, and treating it as one reports a mixed
        universe rig -- a club house rig alongside ours -- as one giant clash.
        """
        errors: list[str] = []
        occupied: dict[tuple[int, int], str] = {}
        for f in self.fixtures:
            if f.address < 1 or f.last_address > 512:
                errors.append(
                    f"{f.name}: channels {f.address}-{f.last_address} out of "
                    f"range 1-512 (universe {f.universe})")
            clashes = sorted({occupied[(f.universe, ch)]
                              for ch in range(f.address, f.last_address + 1)
                              if (f.universe, ch) in occupied})
            for other in clashes:
                errors.append(
                    f"OVERLAP: {f.name} (universe {f.universe}, ch {f.address}-"
                    f"{f.last_address}) conflicts with {other}")
            for ch in range(f.address, f.last_address + 1):
                occupied.setdefault((f.universe, ch), f.name)

        movers = self.movers
        n_heads = 0 if self.geometry is None else len(self.geometry.heads)
        if len(movers) != n_heads:
            errors.append(
                f"{len(movers)} fixture(s) claim a geometry head but the "
                f"geometry has {n_heads} -- every mover needs a position, and "
                f"every position needs a mover")
        for i, f in enumerate(movers):
            if f.head != i:
                errors.append(
                    f"{f.name}: head index {f.head} is not contiguous from 0 "
                    f"(got {[m.head for m in movers]})")
            if not f.is_mover:
                errors.append(
                    f"{f.name}: claims geometry head {f.head} but its mode "
                    f"{f.mode!r} has no pan/tilt")
        return errors

    def inventory_warnings(self, inventory: Optional[dict] = None) -> list[str]:
        """Does the patch agree with the hardware we actually own?

        `shared/inventory.json` called itself "the durable asset -- events come
        and go, the inventory carries forward", and nothing read it. So a rig
        could patch six of a fixture we own two of, or a model not in the
        inventory at all, and the first symptom was a dark fixture at the venue.

        Warnings rather than errors, deliberately. The inventory is a record of
        what is in the road cases, maintained by a person; the patch is a
        statement about what is plugged in right now. When they disagree the
        inventory is at least as likely to be the stale one, and refusing to
        start the show over a bookkeeping mismatch would be the wrong trade at
        4pm. `status: unverified` is called out for the same reason -- the
        inventory itself flags those as unconfirmed.
        """
        if inventory is None:
            try:
                inventory = configmod.load(REPO / "shared" / "inventory.json",
                                           configmod.INVENTORY)
            except configmod.ConfigError:
                # No inventory, or an unreadable one, is not a reason to stop.
                return []

        owned = {(e["manufacturer"], e["model"]): e
                 for e in inventory.get("fixtures", [])}
        used: dict[tuple[str, str], list[str]] = {}
        for f in self.fixtures:
            key = (f.profile.manufacturer, f.profile.model)
            used.setdefault(key, []).append(f.name)

        out: list[str] = []
        for (manufacturer, model), names in sorted(used.items()):
            entry = owned.get((manufacturer, model))
            if entry is None:
                out.append(
                    f"{manufacturer} {model}: patched {len(names)}x but not in "
                    f"shared/inventory.json -- add it there, or fix the "
                    f"manufacturer/model spelling in rig.json")
                continue
            if len(names) > entry.get("count", 0):
                out.append(
                    f"{manufacturer} {model}: rig patches {len(names)} but the "
                    f"inventory says we own {entry.get('count', 0)} "
                    f"({', '.join(names)})")
            if entry.get("status") == "unverified":
                out.append(
                    f"{manufacturer} {model}: inventory marks this 'unverified' "
                    f"-- confirm we still own it before relying on it")
        return out

    def warnings(self) -> list[str]:
        """Things worth knowing that are not errors: channels the engine has no
        role for, and cells it will leave dark. Separate from validate() because
        a rig with these is still perfectly runnable -- it just is not doing
        everything the hardware can."""
        out: list[str] = list(self.inventory_warnings())
        for f in self.fixtures:
            dupes = f.profile.duplicate_roles(f.mode)
            for role, names in dupes.items():
                out.append(
                    f"{f.name}: {len(names)} channels claim role {role!r} "
                    f"({', '.join(names)}) -- only the first is driven, so the "
                    f"remaining cells stay dark")
            # A channel the rig file explicitly holds is handled, not ignored --
            # holding by name is the sanctioned way to drive a channel no
            # generic role fits, so warning about it would train the operator to
            # ignore this list.
            unmapped = [c for c in f.profile.unmapped(f.mode) if c not in f.hold]
            if unmapped:
                out.append(
                    f"{f.name}: nothing drives {', '.join(unmapped)} -- it will "
                    f"sit at 0 every frame. Add it to 'hold' in rig.json if that "
                    f"is not what the fixture wants")
            if f.is_mover and not f.is_16bit:
                out.append(
                    f"{f.name}: mode {f.mode!r} has no fine pan/tilt, so motion "
                    f"quantises to 8 bits ({f.profile.pan_max_deg / 256:.1f} deg "
                    f"per pan step)")
        return out


# ------------------------------------------------------------------ loading --

VENUE_LIBRARY = REPO / "shared" / "venues"


def venue_path(event_dir: Path, rig_cfg: dict) -> Path:
    """Where this event's room description lives.

    A venue outlives a show. The same room hosts a second night with a
    different rig, a different library and a different set list, and the walls
    do not move -- so keeping venue.json inside the event meant a second show in
    the same room forked the file, and the two copies then drifted on exactly
    the numbers the safety taper reads.

    So `rig.json` may name a room in `shared/venues/` instead:

        "venue": "despacio-room"

    An event with no `venue` key keeps its own `venue.json`, which is what every
    event written before this did. Falling back rather than migrating means this
    change cannot break a checkout that has not been touched yet.
    """
    named = rig_cfg.get("venue")
    if not named:
        return event_dir / "venue.json"

    # A path wins over a library name, so an event can point at a room that is
    # not in the shared library yet without having to put it there first.
    as_path = Path(named)
    if as_path.suffix == ".json" and (event_dir / as_path).exists():
        return event_dir / as_path
    if as_path.is_absolute() and as_path.exists():
        return as_path

    candidate = VENUE_LIBRARY / f"{named}.json"
    if not candidate.exists():
        available = sorted(p.stem for p in VENUE_LIBRARY.glob("*.json")) \
            if VENUE_LIBRARY.is_dir() else []
        raise configmod.ConfigError(event_dir / "rig.json", [
            f"venue {named!r} is not in {VENUE_LIBRARY}\n"
            f"      fix: " + (f"did you mean one of {available}?" if available
                              else f"create {candidate}, or drop the 'venue' key "
                                   f"to use this event's own venue.json")])
    return candidate


def load_rig(event_dir: Path, library: Optional[ProfileLibrary] = None) -> Rig:
    """Load `rig.json` + `venue.json` + `calibration.json` from an event folder.

    Three files, not one, because they change on different schedules and for
    different reasons -- the split F9 needs, arrived at now so the loader is not
    written twice. The rig is what is plugged in; the venue is the room; the
    calibration is what this rig measured in this room on this day, and it is
    the one that gets re-measured after an overnight nudge (F5).
    """
    library = ProfileLibrary() if library is None else library
    rig_cfg = configmod.load(event_dir / "rig.json", configmod.RIG)
    venue_file = venue_path(event_dir, rig_cfg)
    venue = load_venue(venue_file)

    cal_path = event_dir / "calibration.json"
    cal_cfg = (configmod.load(cal_path, configmod.CALIBRATION)
               if cal_path.exists() else {"heads": []})

    mount_mode = rig_cfg.get("mount_mode", "venue")

    # A calibration reading is mount-mode specific: a bench "table" reading and
    # a real "venue" reading calibrate two physically different transforms and
    # are not interchangeable. calibration.json records which mode it was
    # measured in, so check it -- the failure is otherwise invisible, because
    # the back-solve is self-consistent in ANY mode and the ball keeps aiming
    # perfectly while every other pose is wrong.
    cal_mode = cal_cfg.get("mount_mode")
    if cal_mode is not None and cal_mode != mount_mode:
        raise ValueError(
            f"{event_dir.name}: rig.json is in mount_mode {mount_mode!r} but "
            f"calibration.json was measured in {cal_mode!r}. These are not "
            f"interchangeable -- they calibrate physically different transforms. "
            f"Re-measure in {mount_mode!r}, or fix whichever file is wrong.")
    fixtures: list[PatchedFixture] = []
    heads: list[geo.Head] = []

    def _can_aim(profile: FixtureProfile, mode: str) -> bool:
        """Does this fixture, in this mode, have both Pan and Tilt?"""
        offsets = profile.offsets(mode)
        return PAN in offsets and TILT in offsets

    for entry in rig_cfg["fixtures"]:
        profile = library.get(entry["manufacturer"], entry["model"])
        if profile is None:
            raise FileNotFoundError(
                f"{entry['name']}: no .qxf for {entry['manufacturer']} "
                f"{entry['model']!r} in any of "
                f"{[str(r) for r in library.roots]}")
        head_index = None
        position = None
        if "position" in entry:
            pos = entry["position"]
            position = (float(pos["x"]), float(pos["y"]), float(pos["z"]))

        # A position alone does NOT make a geometry head. A head is a thing that
        # can be *aimed*, and the head list is an ordered, calibration-bearing
        # index: every entry needs a Pan/Tilt reading measured on the night, and
        # `validate()` insists the movers and the heads line up one for one. Let
        # a positioned pinspot in and it takes an index, shifts every head after
        # it, and quietly re-points the show. So the test is the fixture's own
        # channel list, which is the only honest source for "does it move".
        if position is not None and _can_aim(profile, entry["mode"]):
            head_index = len(heads)
            cal = next((c for c in cal_cfg.get("heads", [])
                        if c.get("fixture") == entry["name"]), {})
            reading = cal.get("ball_dmx")
            heads.append(geo.Head(
                name=entry["name"],
                x=float(pos["x"]), z=float(pos["z"]), height=float(pos["y"]),
                # The .qxf's declared range, overridable per fixture. A profile
                # can be optimistic -- a head claiming 540 deg of Pan that
                # really does 500 calibrates perfectly at the ball and drifts
                # further off the further an aim gets from it. The calibration
                # solver measures this when the captures span enough bearing;
                # this is where its answer goes.
                pan_range_deg=float(entry.get("pan_range_deg",
                                              profile.pan_max_deg or 540.0)),
                tilt_range_deg=float(entry.get("tilt_range_deg",
                                               profile.tilt_max_deg or 270.0)),
                beam_angle_deg=entry.get("beam_deg", profile.beam_deg or 3.0),
                calibrated_ball_dmx=None if reading is None else tuple(reading),
                pan_invert=bool(cal.get("pan_invert", False)),
                tilt_invert=bool(cal.get("tilt_invert", False)),
                mount_facing_override=cal.get("mount_facing_override"),
            ))

        fixtures.append(PatchedFixture(
            fid=entry["id"], name=entry["name"], profile=profile,
            mode=entry["mode"], universe=int(entry.get("universe", 0)),
            address=int(entry["address"]), tags=tuple(entry.get("tags", [])),
            head=head_index, position=position,
            lumens=(None if entry.get("lumens") is None
                    else float(entry["lumens"])),
            beam_deg=(None if entry.get("beam_deg") is None
                      else float(entry["beam_deg"])),
            pan_speed_deg_s=(None if entry.get("pan_speed_deg_s") is None
                             else float(entry["pan_speed_deg_s"])),
            tilt_speed_deg_s=(None if entry.get("tilt_speed_deg_s") is None
                              else float(entry["tilt_speed_deg_s"])),
            hold=dict(entry.get("hold", {})), notes=entry.get("notes", "")))

    geometry = geo.RigGeometry(
        heads=tuple(heads), ball=venue.ball, mount_mode=mount_mode,
        elev_extreme_deg=venue.elev_extreme_deg) if heads else None

    return Rig(name=rig_cfg.get("name", event_dir.name),
               fixtures=tuple(fixtures), geometry=geometry, venue=venue,
               venue_file=venue_file)


if __name__ == "__main__":
    import sys

    library = ProfileLibrary()
    indexed = library.indexed()
    print(f"{len(indexed)} profile(s) indexed from {QXF_SEARCH_ROOTS[0]}:\n")
    for (man, model), p in sorted(indexed.items()):
        print(f"  {man} {model}  [{p.type}]  pan {p.pan_max_deg:.0f} "
              f"tilt {p.tilt_max_deg:.0f} beam {p.beam_deg:.0f} deg")
        for mode in p.modes:
            offsets = p.offsets(mode)
            print(f"    {mode}: " + ", ".join(
                f"{r}@{o}" for r, o in sorted(offsets.items(), key=lambda kv: kv[1])))
            for role, names in p.duplicate_roles(mode).items():
                print(f"      {len(names)} cells claim {role!r}: {', '.join(names)}")
            unmapped = p.unmapped(mode)
            if unmapped:
                print(f"      no role for: {', '.join(unmapped)}")

    event = Path(sys.argv[1]) if len(sys.argv) > 1 else REPO / "events" / "despacio"
    if not (event / "rig.json").exists():
        raise SystemExit(f"\nno rig.json in {event}")

    rig = load_rig(event, library)
    print(f"\nrig {rig.name!r}: {len(rig.fixtures)} fixtures, "
          f"universes {rig.universes}")
    for f in rig.fixtures:
        bits = "16-bit" if f.is_16bit else "8-bit " if f.is_mover else "      "
        tags = f"[{', '.join(f.tags)}]" if f.tags else ""
        print(f"  {f.name:<16} {f.mode:<12} u{f.universe} "
              f"ch {f.address:>3}-{f.last_address:<3} {bits} {tags}")

    if rig.geometry is not None:
        g = rig.geometry
        print(f"\ngeometry: {len(g.heads)} heads, mode={g.mount_mode}, "
              f"ball at {g.ball}")
        for i, head in enumerate(g.heads):
            pan, tilt = g.encode(i, g.aim_at_ball(i))
            print(f"  {head.name:<16} beam {head.beam_angle_deg:.0f} deg, "
                  f"ball -> pan {geo.split16(pan)} tilt {geo.split16(tilt)}")

    print("\nbaseline holds (asserted every frame, before any layer):")
    for f in rig.fixtures:
        for key, value in f.hold.items():
            print(f"  u{f.universe} ch {f.address_of(key):>3}  "
                  f"{f.name} / {key} = {value}")

    errors, warns = rig.validate(), rig.warnings()
    print(f"\nvalidate: {'OK' if not errors else f'{len(errors)} error(s)'}")
    for e in errors:
        print(f"  [ERR]  {e}")
    for w in warns:
        print(f"  [WARN] {w}")
