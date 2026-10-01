"""
The room: its walls, what hangs in it, and where people's heads are.

Separate from the rig because it changes for a different reason. A new room is a
new venue file and nothing else moves -- which is the whole portability claim,
and the reason the club gig is a config change rather than a rebuild.

Millimetres throughout, y up, origin at the front-left floor corner.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from . import config as configmod


@dataclass(frozen=True)
class Box:
    """An axis-aligned box. The engine's one spatial primitive -- rooms, crowd
    zones and clip volumes are all boxes, so one ray test serves all of them."""
    min_x: float
    max_x: float
    min_y: float
    max_y: float
    min_z: float
    max_z: float

    def contains_xz(self, x: float, z: float) -> bool:
        return self.min_x <= x <= self.max_x and self.min_z <= z <= self.max_z

    def distance_xz(self, x: float, z: float) -> float:
        """Distance from (x,z) to the box's footprint, 0 if inside."""
        dx = max(self.min_x - x, 0.0, x - self.max_x)
        dz = max(self.min_z - z, 0.0, z - self.max_z)
        return (dx * dx + dz * dz) ** 0.5


@dataclass(frozen=True)
class CrowdZone:
    """Where people stand, and how high their eyes are.

    The head band is a judgement call, not a measurement. 1.4-2.0 m is roughly
    seated-tall to standing-tall, deliberately generous at the top because
    someone up on someone's shoulders is exactly who gets hit. Raising the floor
    of the band buys back throw at the cost of catching shorter people.
    """
    footprint: Box
    head_band_min: float
    head_band_max: float

    @property
    def danger_box(self) -> Box:
        """The volume a beam must not put its core through: the crowd footprint
        extruded through the head band."""
        f = self.footprint
        return Box(f.min_x, f.max_x, self.head_band_min, self.head_band_max,
                   f.min_z, f.max_z)


@dataclass(frozen=True)
class Canopy:
    """A parachute or similar overhead surface.

    Both an obstruction and a target: beams terminate on it and it re-radiates
    diffusely, which is why it is worth aiming at deliberately -- and why it
    clipped the aerial poses' throw on the night, since those were computed as
    if the beams kept going.
    """
    enabled: bool
    height: float
    radius: float
    center_x: float
    center_z: float

    def covers(self, x: float, z: float) -> bool:
        if not self.enabled:
            return False
        return ((x - self.center_x) ** 2 + (z - self.center_z) ** 2) <= self.radius ** 2


@dataclass(frozen=True)
class Truss:
    """The frame the rig hangs on: a rectangle of bar standing in the room.

    Structure rather than architecture, and the reason it is worth naming is
    that it, not the walls, is what the rig is measured from. A room can be
    re-measured, repainted or replaced; the 500 mm from a head to the bar it is
    clamped to does not change. Anything reasoning about where the fixtures are
    should reason about this rectangle.

    Deliberately invisible to the safety taper. A bar is thin, it hangs above
    the head band, and the taper exists to protect eyes -- so modelling it there
    would add occlusion the taper could lean on without protecting anybody. The
    previz does give it collision, because a beam aimed into it really does stop.
    """
    enabled: bool
    footprint: Box                # min/max are the bar's CENTRELINE
    height: float                 # centre of the bar's section
    bar: float                    # section size, square

    @property
    def span_x(self) -> float:
        return self.footprint.max_x - self.footprint.min_x

    @property
    def span_z(self) -> float:
        return self.footprint.max_z - self.footprint.min_z


@dataclass(frozen=True)
class Venue:
    name: str
    width: float
    depth: float
    height: float
    ball: tuple[float, float, float]
    ball_radius: float
    apex_height: float
    crowd_zone: Optional[CrowdZone]
    canopy: Optional[Canopy]
    truss: Optional[Truss] = None
    elev_extreme_deg: float = 90.0
    # The `previz` block, RAW: models placed in the room, the mirror ball's look
    # and motor, how the room renders. Only engine.scene reads it, and it turns
    # anything malformed into a warning rather than an error, because nothing a
    # previz draws is allowed to stop the show loading. Kept out of equality so
    # two venues that differ only in how they are drawn compare equal.
    previz: Any = field(default_factory=dict, compare=False)

    @property
    def room(self) -> Box:
        return Box(0.0, self.width, 0.0, self.height, 0.0, self.depth)


def load_venue(path: Path) -> Venue:
    # Validated before a single field is read, so a typo is one readable
    # message naming the file and the key rather than a KeyError three modules
    # deep. Everything below can then assume its fields exist and are numbers.
    cfg = configmod.load(Path(path), configmod.VENUE)
    ball = cfg["ball"]

    crowd = None
    if "crowd_zone" in cfg:
        c = cfg["crowd_zone"]
        crowd = CrowdZone(
            footprint=Box(float(c["min_x"]), float(c["max_x"]),
                          float(c["head_band_min"]), float(c["head_band_max"]),
                          float(c["min_z"]), float(c["max_z"])),
            head_band_min=float(c["head_band_min"]),
            head_band_max=float(c["head_band_max"]))

    canopy = None
    if "canopy" in cfg:
        k = cfg["canopy"]
        center = k.get("center", {"x": ball["x"], "z": ball["z"]})
        canopy = Canopy(enabled=bool(k.get("enabled", True)),
                        height=float(k["height"]), radius=float(k["radius"]),
                        center_x=float(center["x"]), center_z=float(center["z"]))

    truss = None
    if "truss" in cfg:
        t = cfg["truss"]
        truss = Truss(
            enabled=bool(t.get("enabled", True)),
            footprint=Box(float(t["min_x"]), float(t["max_x"]),
                          float(t["height"]), float(t["height"]),
                          float(t["min_z"]), float(t["max_z"])),
            height=float(t["height"]), bar=float(t.get("bar", 300.0)))

    return Venue(
        name=cfg.get("name", Path(path).parent.name),
        width=float(cfg["width"]), depth=float(cfg["depth"]),
        height=float(cfg["height"]),
        ball=(float(ball["x"]), float(ball["y"]), float(ball["z"])),
        # A solid mirror ball genuinely OCCLUDES the beam -- it is a sphere, not
        # a scattering cloud -- which is a large part of why the ball poses were
        # safe on a rig with no safety system at all. Modelling it is what stops
        # the taper from killing the show's signature look.
        ball_radius=float(cfg.get("ball_radius", 200.0)),
        apex_height=float(cfg.get("apex_height", cfg["height"])),
        crowd_zone=crowd, canopy=canopy, truss=truss,
        elev_extreme_deg=float(cfg.get("elev_extreme_deg", 90.0)),
        previz=cfg.get("previz", {}))
