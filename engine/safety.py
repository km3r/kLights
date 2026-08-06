"""
Beam-aware intensity taper: the eye-safety fix.

Two things went wrong on the night. Beams swept through the crowd at head
height, and specific static poses were aimed too low. Those are the same bug:
QLC+ scenes store DMX values, so nothing in that pipeline knows where a beam
LANDS, and nothing that does not know where a beam lands can guard it.

This is evaluated **per frame from the current aim**, which is the property that
matters. A scene-based rig can at best vet its stored poses; it structurally
cannot cover the transit between them, and transit is where most of the damage
happened. Here a move that passes through the danger band dims on the way in and
comes back up on the way out, without anyone having authored that.

**The goal is "not blinding", not "never lands on anyone"** (decided 2026-08-06).
Beams are expected to cross the crowd; they are expected to be gentle about it.
So intensity is tapered *to* `TaperConfig.crowd_level` rather than to zero. Set
that to 0.0 and this becomes a hard guard again -- at the cost of every
floor-sweep pose, since a head aiming at the dancefloor necessarily crosses eye
height on the way down.

The geometry, in order:

  1. Cast the beam axis and clip it to the room -- past a wall the beam is gone.
  2. If the mirror ball blocks it first, stop. A mirror ball is a solid sphere,
     it genuinely occludes, and this is most of why the ball poses were safe on
     a rig with no safety system at all. Without this the taper would kill the
     show's signature look for no reason.
  3. Find where what is left of the axis crosses the head band. No crossing
     means no eye to hit, at full intensity.
  4. Measure how far that crossing passes from the crowd footprint, in the band
     plane, and compare against the beam's own radius at that range.

Step 4 is done in millimetres at the relevant range rather than in degrees.
Those are the same statement -- a 6 degree margin is `tan(6 deg) * range` of
lateral clearance -- but distance-at-range needs no trig per test, and it makes
the beam's own width fall out as just another distance.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Optional

from . import geometry as geo
from .venue import Box, Venue

INF = float("inf")


@dataclass(frozen=True)
class TaperConfig:
    """How far intensity falls approaching the crowd, and how fast.

    `crowd_level` is what a beam whose core is over people is dimmed **to**, not
    a floor to be nudged off zero. The goal chosen for this rig (2026-08-06) is
    "not blinding", not "never lands on anyone": beams are expected to cross the
    crowd, they are just expected to be gentle about it. So the taper
    interpolates between `crowd_level` and full rather than between zero and
    full.

    Be clear about what that buys and what it does not. At 0.5 a beam aimed
    directly into someone's eye still emits at half power, so this is a glare
    and comfort guard, not a hard optical-safety guarantee. Set it to 0.0 to get
    the guarantee back, at the cost of the floor-sweep pose family.

    `margin_deg` is the angular width of the soft edge -- about two beam widths
    on the MJ-OS-018, so a head crossing it at a typical sweep rate takes a
    noticeable fraction of a second rather than snapping.

    `slew_per_second` caps how fast the multiplier may move, in units per
    second. The spatial margin already smooths an ordinary sweep; this catches
    the fast ones, where a head can cross the whole margin inside two frames and
    the ramp would read as a step. 2.0 means a full 0-to-1 swing takes half a
    second at minimum. Set to 0 to disable.
    """
    margin_deg: float = 6.0
    crowd_level: float = 0.5
    slew_per_second: float = 2.0
    enabled: bool = True


@dataclass(frozen=True)
class Clearance:
    """Why a given aim got the intensity it did. Every field is here so the F4
    design-time report can explain a taper rather than just assert one."""
    taper: float
    enters_band: bool
    occluded: bool
    clearance_mm: float          # lateral distance from the crowd footprint
    beam_radius_mm: float        # the beam's own half-width at that range
    margin_mm: float
    range_mm: float              # head to the closest in-band point
    reason: str


# ------------------------------------------------------------- ray helpers --

def ray_box(origin, direction, box: Box) -> Optional[tuple[float, float]]:
    """(t_enter, t_exit) where a ray crosses an axis-aligned box, or None.

    The standard slab test. t is in the direction's own units, so with a unit
    direction it is millimetres along the beam. Entry is clamped at 0 -- a head
    already inside the box is treated as entering at itself, not behind itself.
    """
    t_lo, t_hi = 0.0, INF
    for o, d, lo, hi in (
            (origin[0], direction[0], box.min_x, box.max_x),
            (origin[1], direction[1], box.min_y, box.max_y),
            (origin[2], direction[2], box.min_z, box.max_z)):
        if abs(d) < 1e-12:
            # Parallel to this slab: either always inside it or never.
            if o < lo or o > hi:
                return None
            continue
        t_a, t_b = (lo - o) / d, (hi - o) / d
        if t_a > t_b:
            t_a, t_b = t_b, t_a
        t_lo = max(t_lo, t_a)
        t_hi = min(t_hi, t_b)
        if t_lo > t_hi:
            return None
    return (t_lo, t_hi)


def ray_sphere(origin, direction, center, radius: float) -> Optional[float]:
    """Distance to the first intersection with a sphere, or None."""
    ox, oy, oz = origin[0] - center[0], origin[1] - center[1], origin[2] - center[2]
    dx, dy, dz = direction
    b = 2.0 * (ox * dx + oy * dy + oz * dz)
    c = ox * ox + oy * oy + oz * oz - radius * radius
    disc = b * b - 4.0 * c          # a == 1 for a unit direction
    if disc < 0.0:
        return None
    root = math.sqrt(disc)
    for t in ((-b - root) / 2.0, (-b + root) / 2.0):
        if t > 1e-9:
            return t
    return None


def _point(origin, direction, t: float) -> tuple[float, float, float]:
    return (origin[0] + direction[0] * t,
            origin[1] + direction[1] * t,
            origin[2] + direction[2] * t)


# ------------------------------------------------------------------ taper --

def clearance(rig_geo: geo.RigGeometry, head: int, aim: geo.Aim, venue: Venue,
              config: TaperConfig = TaperConfig()) -> Clearance:
    """How close head `head`'s beam comes to somebody's eyes, and what that
    does to its intensity."""
    full = lambda reason: Clearance(1.0, False, False, INF, 0.0, 0.0, 0.0, reason)

    if not config.enabled or venue.crowd_zone is None:
        return full("taper disabled" if not config.enabled else "no crowd zone defined")

    origin, direction = rig_geo.ray(head, aim)

    # 1. Clip to the room. Past a wall the beam is absorbed, so anything the
    #    infinite ray would do out there is not a hazard in here.
    in_room = ray_box(origin, direction, venue.room)
    if in_room is None:
        return full("beam leaves the room without crossing it")
    _t_room_in, t_room_out = in_room

    # 2. The mirror ball is solid and blocks the beam.
    t_ball = ray_sphere(origin, direction, venue.ball, venue.ball_radius)

    # 3. Where does what is left cross the head band?
    #
    #    Tested against the band as an infinite HORIZONTAL SLAB, not against the
    #    crowd footprint. That distinction is the whole taper: a test against
    #    the footprint answers "is the axis over people", which is a yes/no, and
    #    a guard built on a yes/no is a mask however smoothly it is described.
    #    The slab answers "is the beam at eye height", and then the DISTANCE
    #    from the footprint supplies the soft edge.
    band = venue.crowd_zone.danger_box
    slab = Box(-INF, INF, band.min_y, band.max_y, -INF, INF)
    in_band = ray_box(origin, direction, slab)
    if in_band is None:
        return full("beam never crosses the head band")

    t_lo, t_hi = in_band
    t_lo = max(t_lo, 0.0)
    t_hi = min(t_hi, t_room_out)
    if t_ball is not None:
        if t_ball <= t_lo:
            return Clearance(1.0, True, True, INF, 0.0, 0.0, t_lo,
                             "mirror ball blocks the beam before the head band")
        t_hi = min(t_hi, t_ball)
    if t_lo > t_hi:
        return full("beam is blocked or leaves the room before eye height")

    # 4. How far does the in-band segment pass from the crowd footprint?

    # The segment is a straight line and the footprint is convex, so the
    # distance between them is unimodal along t -- sampling cannot miss the
    # minimum by more than the sample spacing, and 16 samples over a band a few
    # hundred millimetres deep is far finer than the beam is wide.
    steps = 16
    best_dist, best_t = INF, t_lo
    for k in range(steps + 1):
        t = t_lo + (t_hi - t_lo) * k / steps
        px, _py, pz = _point(origin, direction, t)
        d = venue.crowd_zone.footprint.distance_xz(px, pz)
        if d < best_dist:
            best_dist, best_t = d, t

    rng = max(best_t, 1.0)
    beam_radius = math.tan(math.radians(rig_geo.heads[head].beam_angle_deg / 2.0)) * rng
    margin = math.tan(math.radians(config.margin_deg)) * rng

    # `crowd_level` inside the core -- the beam's own width counts as "on
    # people", not as clearance -- rising linearly across the margin to full
    # outside it.
    fraction = (best_dist - beam_radius) / margin if margin > 0 else 1.0
    fraction = 0.0 if best_dist <= beam_radius else max(0.0, min(1.0, fraction))
    taper = config.crowd_level + (1.0 - config.crowd_level) * fraction

    if fraction <= 0.0:
        reason = (f"beam core is in the crowd head band "
                  f"(held at {config.crowd_level:.0%})")
    elif taper < 1.0:
        reason = (f"beam edge is {best_dist - beam_radius:.0f} mm from the crowd "
                  f"at {rng:.0f} mm range")
    else:
        reason = "beam clears the crowd head band"

    return Clearance(taper=taper, enters_band=True, occluded=False,
                     clearance_mm=best_dist, beam_radius_mm=beam_radius,
                     margin_mm=margin, range_mm=rng, reason=reason)


def taper(rig_geo: geo.RigGeometry, head: int, aim: geo.Aim, venue: Venue,
          config: TaperConfig = TaperConfig()) -> float:
    """The intensity multiplier for one head's current aim. 0 to 1."""
    return clearance(rig_geo, head, aim, venue, config).taper


# ---------------------------------------------------------------- landing --

@dataclass(frozen=True)
class Landing:
    """Where a beam actually terminates, and how far it got.

    This is the parachute question made answerable. The canopy limited the
    aerials' throw on the night because the poses were computed as though the
    beams kept going to the ceiling; with this, a look can be checked against
    the canopy that is actually rigged.
    """
    surface: str                 # "ball" | "canopy" | "ceiling" | "floor" | "wall"
    point: tuple[float, float, float]
    distance: float


def landing(rig_geo: geo.RigGeometry, head: int, aim: geo.Aim,
            venue: Venue) -> Optional[Landing]:
    origin, direction = rig_geo.ray(head, aim)

    hits: list[tuple[float, str]] = []

    t_ball = ray_sphere(origin, direction, venue.ball, venue.ball_radius)
    if t_ball is not None:
        hits.append((t_ball, "ball"))

    # The canopy is a disc, not a plane: a beam going up outside its radius
    # misses it and carries on to the ceiling. That distinction is the whole
    # point -- it is what decides whether an aerial pose glows or overshoots.
    if venue.canopy is not None and venue.canopy.enabled and direction[1] > 1e-12:
        t = (venue.canopy.height - origin[1]) / direction[1]
        if t > 0:
            px, _py, pz = _point(origin, direction, t)
            if venue.canopy.covers(px, pz):
                hits.append((t, "canopy"))

    in_room = ray_box(origin, direction, venue.room)
    if in_room is not None:
        t_exit = in_room[1]
        px, py, pz = _point(origin, direction, t_exit)
        if py <= 1e-6:
            surface = "floor"
        elif py >= venue.height - 1e-6:
            surface = "ceiling"
        else:
            surface = "wall"
        hits.append((t_exit, surface))

    if not hits:
        return None
    t, surface = min(hits, key=lambda h: h[0])
    return Landing(surface=surface, point=_point(origin, direction, t), distance=t)


# ----------------------------------------------------------------- report --

def audit(rig_geo: geo.RigGeometry, venue: Venue,
          aims: dict[str, list[geo.Aim]],
          config: TaperConfig = TaperConfig()) -> list[dict]:
    """Which looks RELY on the taper, and what each beam actually hits.

    The plan asks for this explicitly, and the reason is worth stating: a safety
    system you cannot audit is one you end up trusting blindly. This lists every
    aim the taper is currently dimming, so it is visible what the show would do
    if the taper were ever off -- and it doubles as the canopy check, since a
    look whose beams all land on "ceiling" is a look the parachute will clip.

    `aims` maps a look name to one aim per head, in head order.
    """
    rows: list[dict] = []
    for name, per_head in aims.items():
        for i, aim in enumerate(per_head):
            c = clearance(rig_geo, i, aim, venue, config)
            land = landing(rig_geo, i, aim, venue)
            rows.append({
                "look": name,
                "head": i,
                "head_name": rig_geo.heads[i].name,
                "taper": round(c.taper, 3),
                "reason": c.reason,
                "lands_on": None if land is None else land.surface,
                "throw_mm": None if land is None else round(land.distance),
                "clearance_mm": None if c.clearance_mm == INF else round(c.clearance_mm),
            })
    return rows
