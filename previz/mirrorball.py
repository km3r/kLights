"""
Where a mirror ball throws a beam.

The ball is the show's signature look, and it is the one thing in the room that
takes a single beam and puts light everywhere. Everything here is real
reflection off real facet normals -- the dots land where they actually would --
because a decorative dot pattern would answer none of the questions previz is
asked ("does the ball cover the back wall from that head?", "do two heads on the
ball fight each other?").

Pure geometry, no Unreal, so it is testable from a shell. World frame is the
engine's: millimetres, y up, origin at the room's front-left floor corner.

The model, and its limits
-------------------------
A mirror ball facet is a flat mirror, so a beam striking it leaves along
`d - 2(d.n)n`. Three simplifications, all deliberate:

  * facets are treated as points on the sphere, not as tiles with area, so a
    beam either lights a facet or does not -- there is no partial coverage;
  * the reflected dot is sized analytically (see `APERTURE_MM`) rather than by
    ray-tracing a bundle;
  * a facet is lit if it faces the beam at all (`d.n < 0`), which ignores that
    the ball's own curvature shades facets near the silhouette.

None of the three changes where a dot lands, only how bright and how big it is,
and previz is asked about the former.
"""

from __future__ import annotations

import math
from typing import Iterable, NamedTuple, Optional

# The ball's tessellation, defined ONCE here because two things consume it and
# they must not disagree: the tiles `build_level.py` places on the ball mesh,
# and the facets this module reflects off. A ball that visibly has one tiling
# and demonstrably reflects off another is worse than either.
#
# This is THE dial for how a mirror ball reads -- more, smaller facets means
# more, smaller dots. A real 400 mm ball wears roughly 10 mm tiles and so has
# well over a thousand; this is coarser than that because every lit facet costs
# a dot and a reflected shaft to draw.
# Both lattices are given as a facet SIZE, not a facet count, and the ring and
# segment counts are worked out from the ball's radius. That is the whole point:
# a real ball wears mirrors of a fixed size and gets more of them as it grows,
# so size is what stays put when `ball_radius` changes. Fixing the counts
# instead means a bigger ball silently grows bigger mirrors -- going from a
# 400 mm ball to a 600 mm one took the facets from 36 mm to 54 mm, and the dots
# per head from 43 down to 17, because the beam covers less of a bigger ball.
#
# MIRROR is what the ball actually wears. REFLECT is a coarser sample of those
# mirrors that light is computed on, because the two are limited by different
# things: a mirror is drawn once and then costs nothing, while a facet that
# reflects costs a shaft and a dot every frame it is lit.
#
# The sample is worth naming as an approximation: the dots that ARE drawn land
# exactly where they belong, there are simply fewer of them than the real ball
# would throw. From anywhere a person stands, the ball is a few degrees wide, so
# nobody can tell which mirror a beam came off -- a ball visibly wearing 50 mm
# panels, on the other hand, everyone can tell.
MIRROR_SPACING_MM = 22.0
REFLECT_SPACING_MM = 45.0


def lattice(radius: float, spacing: float) -> tuple[int, int]:
    """(segments, rings) giving roughly square facets of `spacing` on a sphere.

    Segments is twice rings because a sphere is twice as far around as it is
    over the top, so that ratio is what makes a facet square at the equator.
    """
    rings = max(6, round(math.pi * radius / spacing))
    return 2 * rings, rings


def reflect_lattice(radius: float) -> tuple[int, int]:
    return lattice(radius, REFLECT_SPACING_MM)


def mirror_lattice(radius: float) -> tuple[int, int]:
    return lattice(radius, MIRROR_SPACING_MM)


# The despacio ball, for defaults and for the self-test.
SEGMENTS, RINGS = reflect_lattice(300.0)

# The diameter of the fixture's lens, in mm. It sets how big a mirror-ball dot
# is, and the reason is worth spelling out because the obvious answer is wrong.
#
# It is tempting to spread a dot at the fixture's own beam angle -- 3 degrees --
# but that is the *aggregate* cone of the whole beam, not the divergence of the
# rays arriving at any one point in it. A single facet is a flat mirror a few
# centimetres across; the bundle it intercepts comes from the lens, so it
# diverges at the lens's angular size from there, `aperture / distance`, and it
# keeps that divergence after reflecting. Using 3 degrees instead made every dot
# roughly seven times too wide -- half-metre blobs at the back wall.
#
# 40 mm is an assumption about a small 60 W fixture, and it is the one number
# here that is neither measured nor derived from config.
APERTURE_MM = 40.0


class Dot(NamedTuple):
    """One reflection: which facet threw it, and where it went.

    `facet` is on the ball's surface, and it is what lets previz draw the
    reflected shaft rather than just the dot at the end of it -- the beam
    striking a mirror ball does not vanish and reappear across the room, it
    bursts into dozens of thin beams, and that burst is the look.
    """
    facet: tuple[float, float, float]     # on the ball, where the beamlet leaves
    point: tuple[float, float, float]     # where it lands
    normal: tuple[float, float, float]    # inward normal of the surface it lands on
    throw: float                          # facet -> point, mm
    spot: float                           # diameter of the dot, mm


def facet_normals(segments: int = SEGMENTS,
                  rings: int = RINGS) -> list[tuple[float, float, float]]:
    """Unit normals for the facets of a UV-sphere mirror ball.

    Same tessellation as `shared/tools/gen_mirrorball.py`, so the dots agree
    with the ball mesh that generator produces rather than describing some other
    ball. Normals are taken at each quad's centre; the poles are skipped because
    their triangular caps are degenerate here and contribute two dots out of
    two hundred.
    """
    out = []
    for r in range(rings):
        theta = math.pi * (r + 0.5) / rings          # 0 = +y pole
        for s in range(segments):
            phi = 2 * math.pi * (s + 0.5) / segments
            out.append((math.sin(theta) * math.cos(phi),
                        math.cos(theta),
                        math.sin(theta) * math.sin(phi)))
    return out


def fibonacci_normals(count: int) -> list[tuple[float, float, float]]:
    """`count` unit normals spread as evenly as a sphere allows.

    Points march up the sphere in equal-area steps of height while turning by
    the golden angle, which is the standard construction for a quasi-uniform
    sphere sampling: every point owns about the same area and no two line up.

    This is what the REFLECTIONS come off, and a UV lattice is wrong for the job
    in two compounding ways. Its facets are laid out in rings of constant
    latitude and shrink toward the poles, so a wide source that lights the whole
    near cap throws a dense clump at each pole and visible concentric rings in
    between -- reported as "growing and shrinking rings of spotlights". And
    because the list is ring-major, taking every Nth facet (see `dots`' stride)
    aliases against the segment count and turns those rings into a spiral that
    crawls as the ball spins.

    A golden-angle spiral has neither problem, and subsampling one is still a
    golden-angle spiral -- N times an irrational angle is still irrational --
    so a strided fixture gets a sparser even spread rather than a pattern.

    The ball you SEE is still tiled on `facet_normals`, because a UV lattice is
    what tiles a sphere mesh without gaps. Those are two different jobs at two
    different densities (3698 mirrors drawn, 882 reflected off) and always were.
    """
    out = []
    golden = math.pi * (3.0 - math.sqrt(5.0))
    for i in range(max(1, count)):
        y = 1.0 - 2.0 * (i + 0.5) / count            # equal area in height
        ring = math.sqrt(max(0.0, 1.0 - y * y))
        phi = golden * i
        out.append((ring * math.cos(phi), y, ring * math.sin(phi)))
    return out


def reflect_normals(ball_radius: float) -> list[tuple[float, float, float]]:
    """The facets light actually bounces off, at the reflection density.

    Same count as the UV lattice `reflect_lattice` asks for -- so facet size,
    dot size and the previz's instance pools are all unchanged -- but evenly
    spread. See `fibonacci_normals`.
    """
    return fibonacci_normals(math.prod(reflect_lattice(ball_radius)))


class Tile(NamedTuple):
    """One mirror on the ball, as something drawable.

    `normal` is the same normal `facet_normals` yields, in the same order.
    `east` is the tangent along the ball's rings, and it is there because a
    tile has to be ORIENTED, not just placed: a UV sphere's rings shrink toward
    the poles, so a ball tiled with one square size has even grout around the
    equator and a solid overlapping cap at each end -- it reads as a sphere
    wearing a lace collar rather than as a mirror ball.
    """
    normal: tuple[float, float, float]
    east: tuple[float, float, float]      # along the ring, local +X of the tile
    width: float                          # along east
    height: float                         # along the meridian


def facet_tiles(radius: float, segments: Optional[int] = None,
                rings: Optional[int] = None) -> list[Tile]:
    """The ball's mirrors, sized to the patch of sphere each one covers.

    Build-time only -- nothing here is needed to work out where light goes, only
    to draw the ball that sends it there. Defaults to the fine mirror lattice;
    pass the reflection counts to get tiles that line up with `facet_normals`
    one for one.
    """
    if segments is None or rings is None:
        segments, rings = mirror_lattice(radius)
    out = []
    ring_height = math.pi * radius / rings
    for r in range(rings):
        theta = math.pi * (r + 0.5) / rings
        width = 2.0 * math.pi * radius * math.sin(theta) / segments
        for s in range(segments):
            phi = 2 * math.pi * (s + 0.5) / segments
            out.append(Tile(
                (math.sin(theta) * math.cos(phi), math.cos(theta),
                 math.sin(theta) * math.sin(phi)),
                (-math.sin(phi), 0.0, math.cos(phi)),
                width, ring_height))
    return out


def spin(normals: Iterable[tuple[float, float, float]],
         angle_deg: float) -> list[tuple[float, float, float]]:
    """Rotate facet normals about the vertical axis.

    A mirror ball hangs from a motor and turns; the dots sweeping the room is
    most of the effect, and a still ball reads as a bug. `venue.json` says
    nothing about the motor, so the rate is a previz assumption -- see
    `DEFAULT_RPM`.
    """
    c, s = math.cos(math.radians(angle_deg)), math.sin(math.radians(angle_deg))
    return [(x * c - z * s, y, x * s + z * c) for x, y, z in normals]


# A real ball motor is a couple of turns a minute. Not measured, and not in
# venue.json -- if the despacio ball's motor is ever timed, this is where the
# number goes.
DEFAULT_RPM = 2.0


def facet_size(ball_radius: float, count: int) -> float:
    """Nominal edge length of one facet: the ball's surface area, shared out.

    Both the tiles on the ball mesh and the minimum size of a dot come from
    this, so it lives here rather than being worked out twice.
    """
    return math.sqrt(4.0 * math.pi * ball_radius ** 2 / max(1, count))


# The room's six faces, as (axis index, unit axis). Used by the slab test.
_AXES = ((0, (1.0, 0.0, 0.0)), (1, (0.0, 1.0, 0.0)), (2, (0.0, 0.0, 1.0)))


def ray_room_exit(origin: tuple[float, float, float],
                  direction: tuple[float, float, float],
                  width: float, height: float, depth: float
                  ) -> Optional[tuple[tuple[float, float, float],
                                      tuple[float, float, float]]]:
    """Where a ray leaving `origin` meets the room's inner surface, and the
    inward normal of the face it meets.

    The room is an axis-aligned box from (0,0,0) to (width, height, depth), so
    this is a slab test and costs nothing -- which is the point. Doing it
    analytically here rather than as an engine line trace is what makes a couple
    of hundred dots per frame affordable.

    The normal is returned because a dot has to lie flat ON the surface. Drawn
    as a sphere centred on the landing point instead, half of it is buried in
    the wall -- and near a room edge the neighbouring wall hides most of what is
    left, so dots wink out and back as they sweep into a corner.

    Returns None if the ray never leaves (a zero direction), which callers treat
    as "no dot" rather than as an error.
    """
    ox, oy, oz = origin
    bounds = (width, height, depth)
    best = math.inf
    normal = None
    for i, axis in _AXES:
        d = direction[i]
        if abs(d) < 1e-12:
            continue
        # The far plane in this axis' direction of travel.
        t = ((bounds[i] if d > 0 else 0.0) - origin[i]) / d
        if 0.0 < t < best:
            best = t
            # Inward means back the way the ray came, so it opposes travel.
            s = -1.0 if d > 0 else 1.0
            normal = (axis[0] * s, axis[1] * s, axis[2] * s)
    if not math.isfinite(best):
        return None
    return ((ox + direction[0] * best, oy + direction[1] * best,
             oz + direction[2] * best), normal)


def _normalise(v):
    length = math.sqrt(v[0] * v[0] + v[1] * v[1] + v[2] * v[2])
    if length < 1e-12:
        return None
    return (v[0] / length, v[1] / length, v[2] / length)


def beam_hits_ball(origin, direction, half_angle_deg,
                   ball, ball_radius) -> bool:
    """Cheap rejection: could this beam's cone touch the ball at all?

    Worth its own function because it skips the whole per-facet loop for every
    head that is not on the ball, which is most of them most of the time.
    """
    vx, vy, vz = ball[0] - origin[0], ball[1] - origin[1], ball[2] - origin[2]
    along = vx * direction[0] + vy * direction[1] + vz * direction[2]
    if along <= 0.0:
        return False                                  # ball is behind the head
    px = vx - along * direction[0]
    py = vy - along * direction[1]
    pz = vz - along * direction[2]
    radial = math.sqrt(px * px + py * py + pz * pz)
    return radial <= ball_radius + along * math.tan(math.radians(half_angle_deg))


def lit_facets(distance, half_angle_deg, ball_radius, count) -> int:
    """Roughly how many facets a beam of this width lights at this range.

    A cone of half-angle t at range d paints a disc of radius d*tan(t). Where
    that disc is smaller than the ball it lights a spherical cap, whose area is
    a closed form; where it is larger it overshoots and lights the whole near
    hemisphere, which is half the facets and the hard ceiling.

    Wanted so the caller can decide how coarsely to sample BEFORE tracing, from
    numbers that do not change frame to frame -- a head's distance to the ball
    is fixed by where it is bolted. See `dots`' `stride`.
    """
    r = distance * math.tan(math.radians(max(0.01, half_angle_deg)))
    if r >= ball_radius:
        return count // 2
    cap = ball_radius - math.sqrt(max(0.0, ball_radius * ball_radius - r * r))
    return max(1, int(count * cap / (2.0 * ball_radius)))


def dots(origin, direction, half_angle_deg, ball, ball_radius,
         width, height, depth, normals,
         aperture_mm: float = APERTURE_MM, stride: int = 1) -> list[Dot]:
    """Where one beam's reflections land, as `Dot`s.

    `normals` is the already-spun facet set, passed in rather than computed here
    so that all four heads in a frame share one rotation -- there is only one
    ball, and computing it per head would let the heads disagree about where its
    facets are.

    `stride` draws every Nth facet instead of all of them, for a source wide
    enough to light more of the ball than there is budget to render. It skips by
    LATTICE INDEX, which is the whole point: the chosen facets are then the same
    facets every frame, so as the ball turns they simply enter and leave the
    beam the way real mirrors do. Decimating the *result* list instead -- which
    is what this replaced -- re-picks a different subset whenever the hit count
    changes by one, and several hundred dots each jumping to a neighbouring
    facet reads as a crawling moire pattern rolling across the room. That is
    what it looked like, and it is an artefact of the sampling, not of the ball.
    """
    out: list[Dot] = []
    if not beam_hits_ball(origin, direction, half_angle_deg, ball, ball_radius):
        return out

    tan_half = math.tan(math.radians(half_angle_deg))
    tile = facet_size(ball_radius, len(normals))
    # Each drawn facet now stands in for `stride` of them, so it must be that
    # much bigger or a sampled ball throws visibly smaller dots than a fully
    # drawn one -- the same total light, spread over fewer mirrors.
    tile *= math.sqrt(max(1, stride))
    for n in normals[::max(1, stride)]:
        facing = (direction[0] * n[0] + direction[1] * n[1] + direction[2] * n[2])
        if facing >= 0.0:
            continue                                  # facet points away

        point = (ball[0] + ball_radius * n[0],
                 ball[1] + ball_radius * n[1],
                 ball[2] + ball_radius * n[2])
        wx, wy, wz = point[0] - origin[0], point[1] - origin[1], point[2] - origin[2]
        along = wx * direction[0] + wy * direction[1] + wz * direction[2]
        if along <= 0.0:
            continue
        px, py, pz = (wx - along * direction[0], wy - along * direction[1],
                      wz - along * direction[2])
        if math.sqrt(px * px + py * py + pz * pz) > along * tan_half:
            continue                                  # outside the beam cone

        reflected = _normalise((direction[0] - 2.0 * facing * n[0],
                                direction[1] - 2.0 * facing * n[1],
                                direction[2] - 2.0 * facing * n[2]))
        if reflected is None:
            continue
        exit = ray_room_exit(point, reflected, width, height, depth)
        if exit is None:
            continue
        landing, surface = exit

        # How big the dot is. `along` is how far the facet is from the lens, so
        # `aperture / along` is the angular size of the lens seen from the
        # facet -- which is the divergence of the bundle it intercepts, and so
        # of the bundle it throws. See APERTURE_MM for why this is not the
        # fixture's beam angle.
        throw = math.dist(point, landing)
        out.append(Dot(point, landing, surface, throw,
                       tile + throw * (aperture_mm / max(along, 1.0))))
    return out


# -------------------------------------------------------------- self-test ----

def _self_test() -> None:
    normals = facet_normals()
    assert len(normals) == SEGMENTS * RINGS

    for n in normals:
        assert abs(math.sqrt(sum(c * c for c in n)) - 1.0) < 1e-9, "normal not unit"

    # The drawable tiles line up with the normals they are drawn on, and their
    # frames are orthonormal -- a tile whose east tangent is not perpendicular
    # to its normal renders skewed into the ball.
    tiles = facet_tiles(200.0, SEGMENTS, RINGS)
    assert [t.normal for t in tiles] == normals, "tiles do not match the facets"
    # The ball's real mirrors are finer than the sample light reflects off.
    assert mirror_lattice(300.0) > reflect_lattice(300.0), \
        "the mirror lattice is not finer than the reflection sample"

    # The reflecting set is the same COUNT as the lattice it replaces -- facet
    # size, dot size and the previz's instance pools all come off that number.
    reflectors = reflect_normals(300.0)
    assert len(reflectors) == math.prod(reflect_lattice(300.0))
    for n in reflectors:
        assert abs(math.sqrt(sum(c * c for c in n)) - 1.0) < 1e-9

    # And it is genuinely even, at full density AND strided -- which is the
    # property the whole thing exists for. Counting how many land in each of
    # eight equal-area bands of height: a UV lattice puts the same number in
    # each band too, so the test that separates them is CLUMPING WITHIN a band.
    # Facets near a pole share a ring of tiny circumference, so their pairwise
    # spacing collapses while an even spread's does not.
    for stride in (1, 3, 7):
        sample = reflectors[::stride]
        # Nearest-neighbour distance, worst and typical. On an even sphere
        # sampling these are within a small factor of each other; on a UV
        # lattice the polar rings drag the minimum toward zero.
        gaps = []
        for i, a in enumerate(sample):
            gaps.append(min(math.dist(a, b)
                            for j, b in enumerate(sample) if j != i))
        assert min(gaps) > 0.45 * (sum(gaps) / len(gaps)), (
            f"stride {stride}: closest facets are {min(gaps):.4f} apart against "
            f"a mean of {sum(gaps) / len(gaps):.4f} -- the sample is clumping, "
            f"which is what draws rings and spirals on the walls")

    # Facet SIZE is what holds still as the ball grows -- that is the whole
    # reason both lattices are given as a spacing. Counts must scale with area.
    for spacing in (MIRROR_SPACING_MM, REFLECT_SPACING_MM):
        small = facet_size(200.0, math.prod(lattice(200.0, spacing)))
        large = facet_size(600.0, math.prod(lattice(600.0, spacing)))
        assert abs(small - large) < 0.05 * small, (
            f"facets are {small:.0f} mm on a 400 mm ball and {large:.0f} mm on "
            f"a 1200 mm one -- the lattice is not tracking the radius")
    for t in tiles:
        assert abs(sum(t.normal[i] * t.east[i] for i in range(3))) < 1e-9
        assert abs(math.sqrt(sum(c * c for c in t.east)) - 1.0) < 1e-9
        assert 0.0 < t.width and 0.0 < t.height
    # Tiles shrink toward the poles. If they did not, the caps would be a solid
    # overlapping mat and only the equator would look tiled.
    assert tiles[0].width < tiles[len(tiles) // 2].width, \
        "polar tiles are no narrower than equatorial ones"

    # Spinning preserves length and height, and a full turn is the identity.
    spun = spin(normals, 37.0)
    for a, b in zip(normals, spun):
        assert abs(math.sqrt(sum(c * c for c in b)) - 1.0) < 1e-9
        assert abs(a[1] - b[1]) < 1e-9, "spin about y must not change height"
    for a, b in zip(normals, spin(normals, 360.0)):
        assert all(abs(p - q) < 1e-9 for p, q in zip(a, b)), "360 deg is identity"

    room = (9144.0, 4600.0, 9144.0)
    ball = (4572.0, 2743.0, 4572.0)
    radius = 200.0

    # A ray from the ball leaves through the room's surface, whichever way it
    # points -- the guard on the slab test's sign handling.
    for direction in [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1),
                      (0, 0, -1), (0.4, 0.8, -0.44)]:
        d = _normalise(direction)
        exit = ray_room_exit(ball, d, *room)
        assert exit is not None
        hit, normal = exit
        on_surface = (abs(hit[0]) < 1e-6 or abs(hit[0] - room[0]) < 1e-6
                      or abs(hit[1]) < 1e-6 or abs(hit[1] - room[1]) < 1e-6
                      or abs(hit[2]) < 1e-6 or abs(hit[2] - room[2]) < 1e-6)
        assert on_surface, f"{direction} exits at {hit}, not on the room surface"
        for c, hi in zip(hit, room):
            assert -1e-6 <= c <= hi + 1e-6, f"{hit} is outside the room"

        # The normal is a unit axis, and it points back INTO the room -- if it
        # pointed outward, every dot would be drawn facing the wall it lands on
        # and the room would look empty.
        assert sorted(abs(c) for c in normal) == [0.0, 0.0, 1.0], \
            f"{normal} is not an axis normal"
        assert sum(normal[i] * d[i] for i in range(3)) < 0.0, \
            f"normal {normal} does not oppose travel {d}"
        # Stepping off the surface along it must land inside the room.
        for c, hi in zip((hit[i] + normal[i] * 1.0 for i in range(3)), room):
            assert -1e-6 <= c <= hi + 1e-6, "the normal points out of the room"

    # A head in a corner aimed at the ball throws dots; aimed away it throws none.
    head = (8644.0, 2971.0, 8644.0)
    to_ball = _normalise((ball[0] - head[0], ball[1] - head[1], ball[2] - head[2]))
    away = (-to_ball[0], -to_ball[1], -to_ball[2])

    lit = dots(head, to_ball, 1.5, ball, radius, *room, normals)
    assert lit, "a beam on the ball must produce dots"
    assert not dots(head, away, 1.5, ball, radius, *room, normals), \
        "a beam pointed away from the ball must produce none"

    # Every dot is on the room surface, and none is left sitting on the ball.
    for d in lit:
        assert d.throw > radius, f"dot only {d.throw:.0f} mm away -- on the ball"
        for c, hi in zip(d.point, room):
            assert -1e-6 <= c <= hi + 1e-6, f"dot at {d.point} is outside the room"
        # The shaft it came from starts ON the ball, not inside or beside it.
        assert abs(math.dist(d.facet, ball) - radius) < 1e-6, \
            f"facet at {d.facet} is not on the ball's surface"
        # A dot is a few centimetres, not half a metre. This is the guard on
        # APERTURE_MM: spreading at the fixture's 3-degree beam angle instead
        # gives 470 mm here, which is what "the dots are too big" looked like.
        assert facet_size(radius, len(normals)) <= d.spot < 200.0, \
            f"dot is {d.spot:.0f} mm across, which is not a mirror-ball dot"

    # The reflection law itself: angle of incidence equals angle of reflection
    # about the facet normal. Checked directly rather than trusted, because a
    # sign slip here would still produce a plausible spray of dots -- just the
    # wrong ones, which is exactly the failure previz would not survive.
    for n in normals[:40]:
        facing = sum(to_ball[i] * n[i] for i in range(3))
        if facing >= 0.0:
            continue
        reflected = _normalise(tuple(to_ball[i] - 2.0 * facing * n[i] for i in range(3)))
        out_angle = sum(reflected[i] * n[i] for i in range(3))
        assert abs(out_angle + facing) < 1e-9, "incidence != reflection"

    # A wider beam lights at least as many facets as a narrow one.
    narrow = len(dots(head, to_ball, 0.5, ball, radius, *room, normals))
    wide = len(dots(head, to_ball, 6.0, ball, radius, *room, normals))
    assert wide >= narrow, f"wider beam lit fewer facets ({wide} < {narrow})"


if __name__ == "__main__":
    _self_test()
    print("mirrorball self-test: PASS")

    normals = facet_normals()
    room = (9144.0, 4600.0, 9144.0)
    ball = (4572.0, 2743.0, 4572.0)
    head = (8644.0, 2971.0, 8644.0)
    to_ball = _normalise((ball[0] - head[0], ball[1] - head[1], ball[2] - head[2]))
    print(f"\n{len(normals)} facets of {facet_size(200.0, len(normals)):.0f} mm; "
          f"a 3 deg beam from {head} on the ball lights:")
    # Reporting WHERE the dots are, not how far they threw. In this room the
    # throw distribution is misleadingly stable: the facet lattice is 20 deg in
    # phi and the head sits on the square room's diagonal, so a 30 deg spin maps
    # the facet set onto its own mirror image about that diagonal and the dots
    # land the same distances away in different places.
    for angle in (0.0, 5.0, 10.0, 15.0, 30.0):
        lit = dots(head, to_ball, 1.5, ball, 200.0, *room, spin(normals, angle))
        if not lit:
            print(f"  ball spun {angle:5.0f} deg: no dots")
            continue
        xs = [d.point[0] for d in lit]
        zs = [d.point[2] for d in lit]
        spots = [d.spot for d in lit]
        print(f"  ball spun {angle:5.0f} deg: {len(lit):3d} dot(s), "
              f"centroid x={sum(xs) / len(xs):6.0f} z={sum(zs) / len(zs):6.0f} mm, "
              f"dots {min(spots):3.0f}-{max(spots):3.0f} mm across")
