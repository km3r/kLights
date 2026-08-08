"""
The room and the rig, expressed in Unreal's coordinate frame.

This is the whole host-side half of previz: it reads an event's `rig.json` /
`venue.json` / `calibration.json` through `engine.rig.load_rig()` -- the exact
same loader the show runs on, so a previz scene cannot describe a rig the engine
would not drive -- and emits a flat scene description in Unreal units.

Deliberately importable with no Unreal in sight. Everything here is arithmetic
on the config, so it can be run, diffed and unit-tested from a normal shell;
`previz/unreal/Content/Python/` holds the half that needs an editor.

Frame conversion, which is the only subtle thing in this file
-------------------------------------------------------------
The show's world frame is **millimetres, y up**, origin at the room's front-left
floor corner, with bearing 0 deg = +z and 90 deg = +x (`geometry.bearing_between`
is `atan2(dx, dz)`).

Unreal is **centimetres, Z up**, X forward, Y right, and a rotator's yaw turns
the forward vector from +X toward +Y while positive pitch lifts it toward +Z.

Mapping ``UE_X = z``, ``UE_Y = x``, ``UE_Z = y`` (and dividing by 10) makes those
two descriptions line up exactly:

  * a world bearing turns +z toward +x, which is +X toward +Y -- so
    **UE yaw = world bearing**, no sign flip and no offset;
  * world elevation is positive-up about the horizontal, which is positive pitch
    -- so **UE pitch = world elevation**, likewise unflipped.

That is not a coincidence worth relying on silently, so `head_rotation()` is the
single place any aim becomes a rotator, and `ROTATION_SELF_TEST` below asserts
the two agree against `geometry.ray()`. If someone later "tidies" the axis
mapping into something more familiar (UE_X = x), every one of those identities
breaks and the previz will point four beams at the wrong walls -- convincingly.

Heights and distances are unsigned scalars, so they convert with `mm()` alone;
only *positions* go through `point()`.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:                      # importable from UE's editor
    sys.path.insert(0, str(REPO))

from engine import geometry as geo                 # noqa: E402
from engine import rig as rigmod                   # noqa: E402


# ------------------------------------------------------------ frame mapping --

# Output at full for a fixture whose .qxf declares no Bulb Lumens -- most of
# them in the wild. Bright enough to see in a dark room, dim enough that an
# undeclared fixture cannot quietly out-shout one whose output is really known.
# Lives here rather than in the editor half because the live driver sets
# intensity per frame and the builder sets it once, and they must agree.
UNDECLARED_LUMENS = 400.0


def mm(value: float) -> float:
    """A millimetre length as Unreal centimetres."""
    return float(value) / 10.0


def point(x: float, y: float, z: float) -> list[float]:
    """A world point (mm, y up) as an Unreal location [X, Y, Z] in cm."""
    return [mm(z), mm(x), mm(y)]


def head_rotation(bearing_deg: float, elev_deg: float) -> list[float]:
    """A world aim as an Unreal rotator [pitch, yaw, roll], degrees.

    See the module docstring: under this file's axis mapping both components
    pass through unchanged. Kept as a function anyway so there is exactly one
    place to fix if the mapping ever moves, and so the self-test has something
    to check.
    """
    return [float(elev_deg), float(bearing_deg), 0.0]


def aim_from(origin: tuple[float, float, float],
             target: tuple[float, float, float]) -> tuple[float, float]:
    """(bearing, elevation) in degrees from one world point to another.

    The show's own convention, as `geometry.bearing_between` defines it: bearing
    0 deg = +z and 90 deg = +x, elevation positive up. For fixtures that cannot
    be steered, so there is no calibration to go through -- and deliberately
    built on `geo.bearing_between` rather than a second atan2 written out here,
    because two spellings of the same convention is how they drift apart.
    """
    ox, oy, oz = origin
    tx, ty, tz = target
    bearing = geo.bearing_between(ox, oz, tx, tz)
    elevation = math.degrees(math.atan2(ty - oy, math.hypot(tx - ox, tz - oz)))
    return bearing, elevation


def forward_vector(pitch_deg: float, yaw_deg: float) -> tuple[float, float, float]:
    """Unreal's own rotator -> direction convention, for the self-test."""
    p, y = math.radians(pitch_deg), math.radians(yaw_deg)
    return (math.cos(p) * math.cos(y), math.cos(p) * math.sin(y), math.sin(p))


# ------------------------------------------------------------------- spec ----

@dataclass
class FixtureSpec:
    """One patched unit, ready to place.

    `head` is its index into `RigGeometry.heads` (None for anything that does
    not move), and it is the handle the live driver uses to decode this
    fixture's pan/tilt with the show's own calibration rather than a guess.
    """
    fid: int
    name: str
    model: str
    mode: str
    universe: int
    address: int
    tags: list[str]
    head: Optional[int]
    location: Optional[list[float]]          # UE cm, None if the rig gives none
    beam_deg: float
    # Output at full, from the .qxf. A mover and a pinspot differ by 27x here,
    # so it travels per fixture rather than as a constant in the previz.
    lumens: float
    # Where this head sits when aimed at the mirror ball -- the one aim that was
    # verified by hand. Placing the actor here means a freshly built level shows
    # the calibrated pose immediately, before any DMX arrives, which is the
    # cheapest possible check that the calibration is not nonsense.
    rest_rotation: Optional[list[float]]
    mount_facing: Optional[float]
    # role -> 0-based index into this fixture's universe frame buffer.
    channels: dict[str, int] = field(default_factory=dict)
    # Mechanical colour wheel slots, as [lo, hi, r, g, b]. Empty on a mixing
    # fixture, which takes red/green/blue directly instead.
    color_slots: list[list[int]] = field(default_factory=list)
    notes: str = ""


@dataclass
class SceneSpec:
    """Everything the editor needs to build a level, in Unreal units."""
    event: str
    venue: str
    mount_mode: str
    room: dict
    ball: dict
    canopy: Optional[dict]
    truss: Optional[dict]
    crowd_zone: Optional[dict]
    fixtures: list[FixtureSpec]
    max_throw: float
    unplaced: list[str]
    warnings: list[str]

    def to_dict(self) -> dict:
        out = self.__dict__.copy()
        out["fixtures"] = [f.__dict__.copy() for f in self.fixtures]
        return out


# ------------------------------------------------------------------ build ----

# Roles the previz wants an address for. Anything absent from a given fixture's
# mode is simply omitted -- a pinspot has no pan, a mover has no red.
PREVIZ_ROLES = (
    rigmod.PAN, rigmod.PAN_FINE, rigmod.TILT, rigmod.TILT_FINE,
    rigmod.DIMMER, rigmod.RED, rigmod.GREEN, rigmod.BLUE, rigmod.WHITE,
    rigmod.COLOR_WHEEL, rigmod.STROBE,
)


def build_scene(event_dir: Path) -> SceneSpec:
    rig = rigmod.load_rig(Path(event_dir))
    venue = rig.venue
    if venue is None:
        raise ValueError(f"{event_dir}: no venue.json, so there is no room to build")

    warnings: list[str] = list(rig.validate())
    unplaced: list[str] = []
    fixtures: list[FixtureSpec] = []

    for f in rig.fixtures:
        location = rest = facing = None
        if f.head is not None and rig.geometry is not None:
            head = rig.geometry.heads[f.head]
            frame = rig.geometry.frame(f.head)
            location = point(head.x, head.height, head.z)
            aim = rig.geometry.aim_at_ball(f.head)
            rest = head_rotation(rig.geometry.world_bearing(f.head, aim),
                                 aim.elev_deg)
            facing = frame.mount_facing
        elif f.position is not None:
            # Placed but not steerable -- a pinspot bolted to a wall. Its aim is
            # a fact about the bracket, not something DMX can change, so it is
            # computed straight from the geometry with no calibration in it: the
            # calibration exists to back out a MOVING head's mount facing from a
            # hand-aimed reading, and there is no such reading, no servo, and
            # nothing to drift. `events/despacio/README.md` records that these
            # two are aimed at the ball, which is what makes it derivable at all.
            location = point(*f.position)
            rest = head_rotation(*aim_from(f.position, venue.ball))
        else:
            # No position in rig.json. Not an error -- but it cannot be placed,
            # and silently dropping it would leave the previz quietly missing
            # hardware that is really in the room.
            unplaced.append(f.name)

        channels = {}
        for role in PREVIZ_ROLES:
            idx = f.index_of(role)
            if idx is not None:
                channels[role] = idx

        slots: list[list[int]] = []
        wheel = f.profile.modes[f.mode]
        for name in wheel:
            cd = f.profile.channels[name]
            if cd.role == rigmod.COLOR_WHEEL:
                slots = [[c.lo, c.hi, *c.rgb] for c in cd.color_slots]
                break

        fixtures.append(FixtureSpec(
            fid=f.fid, name=f.name, model=f.profile.model, mode=f.mode,
            universe=f.universe, address=f.address, tags=list(f.tags),
            head=f.head, location=location,
            beam_deg=f.output_beam_deg,
            lumens=float(f.output_lumens or UNDECLARED_LUMENS),
            rest_rotation=rest, mount_facing=facing,
            channels=channels, color_slots=slots, notes=f.notes))

    bx, by, bz = venue.ball
    room = {"width": mm(venue.width), "depth": mm(venue.depth),
            "height": mm(venue.height),
            # The room's centre on the floor, which is where a box brush wants
            # its origin; the world origin stays the front-left floor corner so
            # every number here is still comparable with the config files.
            "center": point(venue.width / 2, 0.0, venue.depth / 2)}

    canopy = None
    if venue.canopy is not None and venue.canopy.enabled:
        canopy = {"location": point(venue.canopy.center_x, venue.canopy.height,
                                    venue.canopy.center_z),
                  "radius": mm(venue.canopy.radius)}

    truss = None
    if venue.truss is not None and venue.truss.enabled:
        f = venue.truss.footprint
        # The four bars, each as a centre point and a half-extent, already in
        # Unreal's axis order -- the same treatment the crowd zone gets, and for
        # the same reason: the editor half should be placing boxes, not
        # re-deriving which of X and Y is the room's depth.
        h, bar = venue.truss.height, venue.truss.bar
        half = bar / 2.0
        bars = []
        for label, cx, cz, ex, ez in (
                ("front", (f.min_x + f.max_x) / 2, f.min_z, venue.truss.span_x / 2 + half, half),
                ("back", (f.min_x + f.max_x) / 2, f.max_z, venue.truss.span_x / 2 + half, half),
                ("left", f.min_x, (f.min_z + f.max_z) / 2, half, venue.truss.span_z / 2 + half),
                ("right", f.max_x, (f.min_z + f.max_z) / 2, half, venue.truss.span_z / 2 + half)):
            bars.append({"label": label,
                         "center": point(cx, h, cz),
                         # [X=depth, Y=width, Z=height] half-extents, in cm.
                         "extent": [mm(ez), mm(ex), mm(half)]})
        truss = {"bars": bars, "bar": mm(bar), "height": mm(h)}

    crowd = None
    if venue.crowd_zone is not None:
        b = venue.crowd_zone.footprint
        crowd = {
            "center": point((b.min_x + b.max_x) / 2,
                            (venue.crowd_zone.head_band_min
                             + venue.crowd_zone.head_band_max) / 2,
                            (b.min_z + b.max_z) / 2),
            "extent": [mm((b.max_z - b.min_z) / 2), mm((b.max_x - b.min_x) / 2),
                       mm((venue.crowd_zone.head_band_max
                           - venue.crowd_zone.head_band_min) / 2)],
            "head_band": [mm(venue.crowd_zone.head_band_min),
                          mm(venue.crowd_zone.head_band_max)],
        }

    return SceneSpec(
        event=rig.name, venue=venue.name,
        mount_mode="" if rig.geometry is None else rig.geometry.mount_mode,
        room=room,
        # `radius_mm` as well as the Unreal one, because the mirror-ball facet
        # lattices are specified as a facet size in mm and so have to be worked
        # out in the show's own units before anything is converted.
        ball={"location": point(bx, by, bz), "radius": mm(venue.ball_radius),
              "radius_mm": float(venue.ball_radius)},
        canopy=canopy, truss=truss, crowd_zone=crowd, fixtures=fixtures,
        # Long enough that a beam always reaches a wall from anywhere in the
        # room, so the live driver never has to decide whether a beam "ends".
        max_throw=mm(math.dist((0, 0, 0), (venue.width, venue.height, venue.depth))),
        unplaced=unplaced, warnings=warnings)


# -------------------------------------------------------------- self-test ----

def _self_test() -> None:
    """The axis mapping agrees with `geometry.ray()`.

    `ray()` is what the F4 safety taper casts, so it is the definition of where
    a beam actually goes. If the previz draws a beam somewhere else, the previz
    is wrong -- and it would be wrong *plausibly*, with four beams lighting four
    believable but incorrect walls. This is the guard on that.
    """
    rig = geo.despacio_reference_rig("venue")
    for i in range(len(rig.heads)):
        for target in [(0.0, 0.0, 0.0), (9144.0, 4600.0, 0.0),
                       (4572.0, 2743.0, 4572.0), (1000.0, 1500.0, 8000.0)]:
            aim = rig.aim_at_point(i, *target)
            origin, direction = rig.ray(i, aim)

            pitch, yaw, roll = head_rotation(rig.world_bearing(i, aim), aim.elev_deg)
            assert roll == 0.0
            fx, fy, fz = forward_vector(pitch, yaw)

            # geometry.ray()'s direction is a unit vector in (x, y, z) world.
            # It takes the same axis PERMUTATION as a point but not the mm->cm
            # scale: a direction is a difference of two points already divided
            # by its own length, so scaling it again would just un-normalise it.
            wx, wy, wz = direction
            ux, uy, uz = wz, wx, wy
            assert math.isclose(fx, ux, abs_tol=1e-9), (
                f"head {i} -> {target}: rotator forward X {fx:.9f} != ray {ux:.9f}")
            assert math.isclose(fy, uy, abs_tol=1e-9), (
                f"head {i} -> {target}: rotator forward Y {fy:.9f} != ray {uy:.9f}")
            assert math.isclose(fz, uz, abs_tol=1e-9), (
                f"head {i} -> {target}: rotator forward Z {fz:.9f} != ray {uz:.9f}")

            # And the actor's own location must be the head's, converted.
            head = rig.heads[i]
            assert point(*origin) == point(head.x, head.height, head.z)

    # Units: the despacio room is 9.144 m, which is 914.4 Unreal cm.
    assert math.isclose(mm(9144.0), 914.4)

    # A static fixture's rotator really points at the thing it was aimed at.
    # Same check as above but for the branch that has no geometry head behind
    # it: build the rotator, take its forward vector, and walk it from the
    # fixture to the target.
    for origin in [(4572.0, 2971.0, 500.0), (4572.0, 2971.0, 8644.0),
                   (0.0, 0.0, 0.0), (9144.0, 4600.0, 9144.0)]:
        target = (4572.0, 2743.0, 4572.0)
        pitch, yaw, roll = head_rotation(*aim_from(origin, target))
        assert roll == 0.0
        fx, fy, fz = forward_vector(pitch, yaw)
        ox, oy, oz = point(*origin)
        tx, ty, tz = point(*target)
        reach = math.dist((ox, oy, oz), (tx, ty, tz))
        for got, want in ((ox + fx * reach, tx), (oy + fy * reach, ty),
                          (oz + fz * reach, tz)):
            assert math.isclose(got, want, abs_tol=1e-6), (
                f"aim_from{origin} misses {target}: {got:.6f} != {want:.6f}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("event", nargs="?", default="despacio",
                        help="event folder name under events/, or a path")
    parser.add_argument("-o", "--out", help="write JSON here (default: stdout)")
    parser.add_argument("--self-test", action="store_true")
    args = parser.parse_args()

    if args.self_test:
        _self_test()
        print("previz scene self-test: PASS")
        raise SystemExit(0)

    event_dir = Path(args.event)
    if not event_dir.exists():
        event_dir = REPO / "events" / args.event

    _self_test()
    scene = build_scene(event_dir)
    text = json.dumps(scene.to_dict(), indent=2)

    if args.out:
        Path(args.out).write_text(text, encoding="utf-8")
        placed = sum(1 for f in scene.fixtures if f.location)
        print(f"{scene.event}: {placed}/{len(scene.fixtures)} fixtures placed, "
              f"room {scene.room['width']:.0f}x{scene.room['depth']:.0f}"
              f"x{scene.room['height']:.0f} cm -> {args.out}")
        for name in scene.unplaced:
            print(f"  [unplaced] {name}: no position in rig.json")
        for w in scene.warnings:
            print(f"  [WARN] {w}")
    else:
        print(text)
