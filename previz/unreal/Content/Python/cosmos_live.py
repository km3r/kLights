"""
Art-Net in, beams out. Runs inside the Unreal editor, on the editor tick.

    python previz/ue_remote.py previz/unreal/Content/Python/live_start.py
    python previz/ue_remote.py -c "import cosmos_live; cosmos_live.stop()"

A module rather than a script because the editor's Python keeps `sys.modules`
between remote-execution calls, and the receive socket plus the tick handle have
to survive from one call to the next.

**This decodes pan/tilt with `engine.geometry` itself.** That is the whole point
of the design: the heads are mounted sideways, so Pan carries elevation and Tilt
carries bearing, and each head's true mount facing is backed out of one
hand-aimed mirror-ball reading. Any previz that re-derived that from a GDTF
profile -- which is what Unreal's own DMX fixture pipeline would do -- would
articulate confidently and wrongly, and would keep on doing so after a
recalibration. Sharing the decoder makes drift impossible rather than unlikely.

The previz is a pure listener on the same UDP broadcast the rig hears. It sends
nothing, so it cannot affect a show even if it crashes mid-set.
"""

import math
import socket
import struct
import sys
import time
import traceback
from pathlib import Path

import unreal

import cosmos_live_state

REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine import rig as rigmod                    # noqa: E402
from engine import servo as servomod                # noqa: E402
from previz import mirrorball as mb                 # noqa: E402
from previz import scene as previz_scene            # noqa: E402

ARTNET_PORT = 6454
ARTNET_HEADER = b"Art-Net\x00"
ARTNET_OP_DMX = 0x5000
TAG = "cosmos_previz"

# How much of a beam survives each metre of haze, as an extinction coefficient:
# what is left after d metres is exp(-k*d). At 0.09 a beam has lost about a
# third of itself by 4 m and is at 40% by 10 m, which is roughly what the photo
# of the real night shows across a 9 m room.
#
# The same scattering that dims a beam is what makes it visible, so a beam you
# can see is one that is losing light -- a shaft of constant brightness is the
# one thing haze cannot produce. Applied to the mirror ball's reflected shafts
# too, which need it more: they leave the ball in every direction and the far
# ones cross the whole room.
BEAM_EXTINCTION_PER_M = 0.09

# Emissive multiplier on the beam shaft. Additive blending over a near-black
# room saturates fast, so this is well under 1: it is the difference between a
# beam whose colour you can judge and a white stripe.
BEAM_GAIN = 1.4

# Reflection dots read against a near-black wall, and each is only a few
# centimetres of it, so they need more gain than their share of the beam's
# energy would suggest. Tuned by eye: below about 1 they vanish, above about 3
# the ball out-shouts the beams that feed it.
#
# Raised from 1.6 to 2.3 on 2026-08-08 when the dots gained a soft edge
# (`build_level.DOT_SHOULDER`). The falloff's mean over the quad is roughly a
# third, so a dot that was a solid patch of `spot` millimetres is now one that
# FADES TO NOTHING at `spot` millimetres -- same physical extent, less light in
# it. This is the compensation, not a re-tune.
DOT_GAIN = 2.3

# The shafts leaving the ball. Dimmer than the dots they end on, because that
# is the honest ordering -- you see where a mirror-ball beam lands long before
# you see the beam itself -- and because a hundred-odd additive shafts crossing
# in the middle of the room stack up fast.
#
# Raised from 0.30 to 2.5 on 2026-08-08, and the factor is not a fudge: a shaft
# is drawn only as wide as the dot it ends on, a few centimetres, so in an 18 m
# room it is a sub-pixel hairline over most of its length and what you see is
# whatever survives being averaged with the black behind it. Doubling the room
# roughly doubled that length while the extinction fix cut the far end's
# brightness to what it should always have been, and 0.30 -- tuned by eye in a
# 9 m room against a taper that was wrong -- left the spray invisible. Swept at
# the new size: 0.3 and 1.1 are both a bare shimmer, 4.5 turns the far wall into
# a haze of rays that competes with the dots, 2.5 fills the room while the dots
# still clearly lead.
#
# THEN RE-SWEPT, because this dial does not stand alone. Widening the heads to 8
# degrees made every one of them overshoot the ball and light its whole near cap,
# so the ray COUNT per head went from about 55 to 150 and the room got three
# times brighter at an unchanged gain. Total spray goes as count x gain, so the
# gain had to come back down to keep the same picture: 1.8 at 150 rays is what
# 2.5 was at 55, plus the small lift that was actually asked for. Re-sweep this
# whenever the room's size or a fixture's beam angle changes -- both move it.
RAY_GAIN = 1.8

# Below this a reflected shaft is not built at all. Additive over a near-black
# room, so this is about one 8-bit level: a shaft dimmer than this cannot change
# the picture, and building it costs as much as one that can. See
# `_place_reflections` -- it is the difference between a 15.9 ms tick and a 7 ms
# one when a wide source lights the ball's whole near cap.
RAY_FLOOR = 1.0 / 255.0

# How hard to compress the brightness RATIO between fixtures on the stand-in
# meshes -- the beam shafts, the ball's reflected shafts and its dots. Applied
# as `ratio ** MESH_CONTRAST`, so 1.0 is the linear, literal answer and lower
# numbers pull the faint fixtures up toward the bright ones.
#
# TWO reasons it is not 1.0, and neither is a fudge.
#
# The ratio itself is not trustworthy. It is derived from each profile's Bulb
# Lumens, and that is what the EMITTER makes, not what leaves the lens. A
# 3-degree beam throws away most of its bulb at the aperture and a wide fresnel
# passes most of its own, so comparing the two straight says the pinspots are
# 360x fainter than the heads, which is not what they look like in the room. If
# a real measured output ever exists, put it in `rig.json` as `lumens` (see
# `PatchedFixture.output_lumens`) and this can move back toward 1.0.
#
# And this previz pins its exposure, deliberately -- one whose exposure drifts
# cannot be compared shot to shot. That also removes the thing that makes a
# quiet fixture visible in a real dark room: your eye adapting to it once the
# loud ones go out.
#
# So the meshes get a perceptual squash and the actual LIGHTS do not. Spot light
# intensity, the ball glow and everything the renderer tone-maps stay in real
# lumens; only the stand-ins, whose gains were eyeballed to begin with and which
# are in no physical unit at all, are compressed. Swept with every fixture on:
# 0.35 leaves the pinspots' spray a faint wash, 0.12 puts it level with the
# heads and the beams stop being the brightest thing in the room, 0.22 fills the
# room with pinspot dots while the heads still clearly lead.
MESH_CONTRAST = 0.22

# The most reflections one fixture may draw per frame. A 3-degree head lights
# about 35 facets, so this only ever bites on a source wide enough to overshoot
# the ball -- a pinspot lights its whole near cap, about 441. Building the
# Unreal transforms is the driver's dominant cost by a long way (11.7 us per
# reflection against 1.3 us for the optics), so this is what keeps a rig with
# wide fixtures in it from costing four times a rig without. Measured: 441 x2
# put the tick at 15.9 ms; 220 x2 with the same optics puts it near 8.
REFLECT_BUDGET = 220

GAIN_NOTE = ("Dot, ray and beam materials come from build_level.py, on the "
             "meshes themselves -- never looked up by asset path; see "
             "_shared_material for why that matters.")

# How far off a wall a dot is drawn, in cm. Nonzero for z-fighting, and the
# surface it lies on is what keeps it visible: drawn as a sphere centred on the
# landing point, half of it is inside the wall and near a room edge the
# neighbouring wall hides the rest, so dots wink out as they sweep into corners.
DOT_LIFT = 1.0

# How much of a beam the ball throws into the ROOM rather than into its dots,
# as a fraction of the fixture's own output. A mirror ball is not a perfect
# scatterer, so this is well under 1. Tuned by eye against a photo of the real
# night, on the 1300 lm heads: high enough that a room with four beams on the
# ball has a visible haze rather than being a black box, low enough that the
# beams stay the brightest thing in it.
#
# A fraction rather than a flat lumen count so it scales with what is actually
# hitting the ball: a 48 lm pinspot cannot wash a room the way a 1300 lm beam
# can, and a flat number would have said it could.
BALL_GLOW_FRACTION = 140.0 / 1300.0


# ---------------------------------------------------------------- art-net ----

def _decode_universe(sub_uni, net):
    return (net << 8) | (((sub_uni >> 4) & 0x0F) << 4) | (sub_uni & 0x0F)


# An instance that is not in use this frame. An instanced mesh has no
# per-instance visibility flag, so "not shown" is "scaled to nothing".
PARKED = unreal.Transform(unreal.Vector(0.0, 0.0, 0.0), unreal.Rotator(0, 0, 0),
                          unreal.Vector(0.0, 0.0, 0.0))


def split_plane(direction):
    """A unit normal perpendicular to `direction`, pointing UP.

    The plane through the beam's axis separating its top half from its bottom
    half, which is what a colour wheel sitting between two segments actually
    produces: half the aperture is one colour and half is the next.

    World up projected off the beam axis and renormalised, so it stays the TOP
    half however the head is aimed. A beam pointing straight up or down has no
    top half; +X is picked there, arbitrarily but stably, because any plane
    through a vertical axis is as good as any other and a wobbling choice would
    make the split spin as the head swung through vertical.
    """
    dx, dy, dz = direction.x, direction.y, direction.z
    ux, uy, uz = -dx * dz, -dy * dz, 1.0 - dz * dz     # up - d*(d.up), d.up = dz
    length = math.sqrt(ux * ux + uy * uy + uz * uz)
    if length < 1e-6:
        return (1.0, 0.0, 0.0)
    return (ux / length, uy / length, uz / length)


def surviving(distance_cm):
    """What fraction of a beam is left after `distance_cm` of haze."""
    return math.exp(-BEAM_EXTINCTION_PER_M * distance_cm / 100.0)


def full_lumens(fixture):
    """This fixture's output at full, from its .qxf."""
    return float(fixture.output_lumens or previz_scene.UNDECLARED_LUMENS)


def unit_key(fixture):
    """How this fixture's previz actors are tagged.

    A mover is keyed by head index and everything else by fixture id, and the
    h/f prefix is what keeps the two numbering schemes apart -- head 0 and
    fixture id 0 are different objects. `build_level.build_fixtures` writes the
    same key; changing it here alone silently unhooks every actor.
    """
    return f"h{fixture.head}" if fixture.head is not None else f"f{fixture.fid}"


def candela(fixture):
    """How bright this fixture's beam is per unit solid angle, at full.

    Lumens alone will not do. The shaft mesh stands in for the beam's own
    brightness, and brightness is lumens spread over the cone they go into: a
    1300 lm head in a 3-degree cone and a 48 lm pinspot in an 11-degree one
    differ by 27x in output and by about 360x in how bright the shaft looks.
    Scaling the mesh by lumens alone would draw the pinspot's shaft ten times
    too bright, which is exactly what it did before this existed.
    """
    half = math.radians(max(0.25, fixture.output_beam_deg) / 2.0)
    return full_lumens(fixture) / (2.0 * math.pi * (1.0 - math.cos(half)))


def rot_from_z_into(rot, dx, dy, dz):
    """`rot_from_z`, writing into an existing rotator instead of making one.

    Worth the ugliness only because of where it is called from. Constructing a
    `unreal.Rotator` costs about 1.1 us and mutating one costs 0.15 us per
    field, and with the pinspots throwing 441 reflections each this runs about a
    thousand times a frame. Measured, not assumed -- see `_place_reflections`.
    """
    if dz >= 1.0:
        rot.pitch = rot.yaw = 0.0
    elif dz <= -1.0:
        rot.pitch, rot.yaw = 0.0, 180.0
    else:
        rot.pitch = math.degrees(math.acos(dz))
        rot.yaw = math.degrees(math.atan2(-dy, -dx))
    return rot


def rot_from_z(dx, dy, dz):
    """A rotator whose local +Z is the unit vector (dx, dy, dz).

    `MathLibrary.make_rot_from_z` does this, but it is an engine call, and this
    runs a few hundred times a frame. Roll comes out arbitrary and that is fine:
    everything aimed with it -- a cone, a disc -- is rotationally symmetric
    about that axis.

    A zero-roll rotator's up vector is `(-sin p cos y, -sin p sin y, cos p)`,
    so `cos p = dz` and `y = atan2(-dy, -dx)`. Checked against the engine's own
    version in `_self_test`.
    """
    if dz >= 1.0:
        return unreal.Rotator(0.0, 0.0, 0.0)
    if dz <= -1.0:
        return unreal.Rotator(0.0, 180.0, 0.0)
    return unreal.Rotator(0.0, math.degrees(math.acos(dz)),
                          math.degrees(math.atan2(-dy, -dx)))


def parse_artdmx(data):
    """(universe, dmx bytes), or None. Same wire format as
    `shared/tools/artnet_listener.py`, which is the reference decoder."""
    if len(data) < 18 or data[:8] != ARTNET_HEADER:
        return None
    if struct.unpack_from("<H", data, 8)[0] != ARTNET_OP_DMX:
        return None
    length = struct.unpack_from(">H", data, 16)[0]
    if len(data) < 18 + length:
        return None
    return _decode_universe(data[14], data[15]), data[18:18 + length]


# ------------------------------------------------------------------ state ----

class Live:
    def __init__(self, event_dir):
        self.rig = rigmod.load_rig(event_dir)
        if self.rig.geometry is None:
            raise ValueError(f"{event_dir}: no moving heads, so nothing to aim")

        # Placed but not steerable -- the pinspots. They take no head index and
        # carry no calibration, so they are driven straight from their DMX with
        # no geometry in the path at all.
        self.statics = tuple(f for f in self.rig.fixtures
                             if f.head is None and f.position is not None)
        # The brightest beam in the rig, which is what BEAM_GAIN was tuned
        # against. Everything else's shaft is drawn relative to it.
        placed = [f for f in self.rig.fixtures if f.position is not None]
        self.brightest = max([candela(f) for f in placed] or [1.0])
        # And how brightly each fixture lights the ball, relative to the best of
        # them: candela over distance squared. DOT_GAIN and RAY_GAIN were tuned
        # on the head that comes out at 1.0. Fixed for the driver's life, since
        # neither the fixtures nor the ball move.
        lit = {}
        for f in placed:
            d = max(1.0, math.dist(f.position, self.rig.venue.ball))
            lit[unit_key(f)] = candela(f) / (d * d)
        best = max(lit.values() or [1.0])
        self.illuminance = {k: (v / best) ** MESH_CONTRAST for k, v in lit.items()}
        # The same compression for the shafts, keyed the same way.
        self.beam_share = {unit_key(f): (candela(f) / self.brightest) ** MESH_CONTRAST
                           for f in placed}
        # And where a fixed fixture's beam goes, as (origin, unit direction) in
        # the show's mm frame -- the same shape `geometry.ray()` hands back for a
        # mover. Bolted to a wall and pointed at the ball, so it is worked out
        # once here instead of every frame. `events/despacio/README.md` is what
        # says the pinspots are aimed at the ball; it is not an invention.
        self.aims = {}
        for f in self.statics:
            ox, oy, oz = f.position
            bx, by, bz = self.rig.venue.ball
            d = max(1.0, math.dist(f.position, self.rig.venue.ball))
            self.aims[unit_key(f)] = (
                (ox, oy, oz), ((bx - ox) / d, (by - oy) / d, (bz - oz) / d))

        self.frames = {u: bytearray(512) for u in self.rig.universes}
        self.packets = 0
        self.errors = 0
        self.rebinds = 0
        self.handle = None
        self.ticks = 0
        self.tick_ms = 0.0
        self.tick_ms_worst = 0.0

        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(("0.0.0.0", ARTNET_PORT))
        self.sock.setblocking(False)

        self.throw = self._max_throw()

        # The yokes. DMX is a command, not a position: a real head needs the
        # better part of a second to cross the room, and a previz that drew the
        # command showed every routine change as a teleport -- which hid both
        # the lit sweep a change actually drags across the room, and the whole
        # point of a dark move, whose travel time IS the effect.
        self.servos = servomod.Rack()

        # The mirror ball, as facet normals plus a rotation that advances with
        # wall-clock time. One shared set for the whole frame: there is one ball,
        # and computing it per head would let four heads disagree about where
        # its facets are this instant. The tessellation lives in `mirrorball`
        # because `build_level` tiles the ball mesh off the same call -- the
        # ball you can see and the ball these reflect off are one object.
        venue = self.rig.venue
        self.facets = mb.reflect_normals(venue.ball_radius)
        self.ball_angle = 0.0
        # How coarsely each fixture samples those facets. Fixed per fixture and
        # worked out here rather than per frame, from two things that cannot
        # change while the show runs: how wide the beam is and how far the
        # fixture is bolted from the ball.
        #
        # A 3-degree head lights about 35 facets and gets stride 1 -- every one.
        # An 11-degree pinspot overshoots a 60 cm ball at 4 m and lights its
        # whole near cap, 441 of them, which is both more than the budget and
        # more than the frame can afford; it gets every other one, or every
        # third. Deciding it here is what makes the drawn subset STABLE, which
        # is the whole fix: see `mirrorball.dots`' `stride`.
        self.stride = {}
        for f in (f for f in self.rig.fixtures if f.position is not None):
            want = mb.lit_facets(math.dist(f.position, venue.ball),
                                 f.output_beam_deg / 2.0,
                                 venue.ball_radius, len(self.facets))
            self.stride[unit_key(f)] = max(1, -(-want // REFLECT_BUDGET))
        self.facet_mm = mb.facet_size(venue.ball_radius, len(self.facets))
        # The ball in Unreal cm, for the cone-vs-ball occlusion test.
        self.ball_centre = previz_scene.point(*venue.ball)
        self.ball_radius = previz_scene.mm(venue.ball_radius)
        self.dots_truncated = 0
        self.dots_sampled = {}
        self.spot_mm = (0.0, 0.0)

        # Reused by `_place_reflections`; see the note there.
        self._scratch = (unreal.Vector(0.0, 0.0, 0.0),
                         unreal.Rotator(0.0, 0.0, 0.0),
                         unreal.Vector(1.0, 1.0, 1.0))

        self.world = None
        self.bind(self.active_world())

    # -- which world are we driving? ---------------------------------------

    @staticmethod
    def active_world():
        """The world the viewport is actually showing.

        Pressing Play duplicates the whole level into a separate PIE world. The
        actors on screen from that moment are the duplicates, so a driver still
        holding the editor world's actors goes on faithfully updating things
        nobody is looking at -- the beams freeze exactly where they were when
        Play was pressed, which looks like a crash and is not one. Re-resolving
        against the active world is the fix, and it has to be re-checked rather
        than done once, because Play and Stop can happen at any time.
        """
        editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
        game = editor.get_game_world()
        return game if game is not None else editor.get_editor_world()

    def bind(self, world):
        """Point this driver at `world`'s copies of the previz actors."""
        self.world = world
        # All four keyed by `unit_key` -- "h<head>" for a mover, "f<fid>" for
        # anything else. One namespace for movers and pinspots alike, because
        # everything below this line does the same job for both and three
        # parallel sets of dicts is three chances for them to disagree.
        self.lights = self._find_tagged(world, "unit:")
        # The other half of the aperture, on fixtures with a split colour
        # wheel. Absent for everything else -- see build_level._has_split_slot.
        self.lights2 = self._find_tagged(world, "unit2:")
        self.beams = self._find_tagged(world, "beam:")
        self.glows = self._find_tagged(world, "glow:")
        # One dynamic material instance per beam, made once per binding.
        # Creating one per frame would leak a material every tick.
        self.beam_materials = {
            key: actor.static_mesh_component.create_dynamic_material_instance(0)
            for key, actor in self.beams.items()}
        self.trace_ignore = self._ignored_by_trace(world)
        # The same list plus the ball, for beams the ball only partly covers.
        ball = [a for a in self._tagged_actors(world)
                if "mirror_ball" in [str(t) for t in a.tags]]
        self.trace_ignore_ball = self.trace_ignore + ball

        # The ball's reflections off each fixture: two instanced meshes, the
        # dots and the shafts that reach them, plus ONE shared material instance
        # per component. Every reflection of one fixture is the same colour by
        # definition, so sharing turns a couple of hundred material writes a
        # frame into two.
        self.reflections = {}
        self.reflect_materials = {}
        self.capacity = {}
        for key, actor in self._find_tagged(world, "reflect:").items():
            pools = {}
            for name in ("dots", "rays"):
                found = actor.get_components_by_tag(
                    unreal.InstancedStaticMeshComponent, name)
                if found:
                    pools[name] = found[0]
            if len(pools) != 2:
                print(f"[cosmos] WARNING: {key}'s reflection actor is "
                      f"missing an instance pool ({sorted(pools)}). Re-run go.py.")
                continue
            materials = {n: self._shared_material(c) for n, c in pools.items()}
            if any(m is None for m in materials.values()):
                print(f"[cosmos] WARNING: {key}'s reflections have no "
                      f"material to instance; they will not take colour.")
                continue
            # The reflected shafts all leave the same place, and the ball does
            # not move, so their taper is fixed for the life of this binding --
            # one write here rather than several hundred a frame. `Reach` is the
            # room's diagonal, the longest a reflection could possibly be; the
            # ones that stop sooner simply never get to the far end of the fade.
            materials["rays"].set_vector_parameter_value(
                "Origin", unreal.LinearColor(*self.ball_centre, 0.0))
            materials["rays"].set_scalar_parameter_value("Reach", self.throw)
            materials["rays"].set_scalar_parameter_value(
                "Falloff", surviving(self.throw))

            self.reflections[key] = pools
            self.reflect_materials[key] = materials
            self.capacity[key] = min(c.get_instance_count() for c in pools.values())
        self.dots_shown = {key: 0 for key in self.reflections}
        self.rays_shown = {key: 0 for key in self.reflections}
        self.dot_look = {}

    @staticmethod
    def _shared_material(component):
        """One dynamic material for a whole instance pool, taken from the mesh
        itself rather than looked up by asset path.

        This deliberately never touches `EditorAssetLibrary`. Its lookups do not
        resolve while Play-in-Editor is running -- `does_asset_exist` returns
        False for an asset that plainly exists in the editor -- so the previous
        version got None back the moment Play was pressed, assigned it, and
        Unreal substituted DefaultMaterial: opaque, unlit, and in a black room
        the reflection dots turned into black golf balls.

        Walking up past any existing dynamic instance first stops a rebind from
        chaining a new instance onto the last one every time Play is pressed.
        """
        base = component.get_material(0)
        while isinstance(base, unreal.MaterialInstanceDynamic):
            base = base.get_editor_property("parent")
        if base is None:
            return None
        component.set_material(0, base)
        return component.create_dynamic_material_instance(0)

    @staticmethod
    def _tagged_actors(world):
        """Every previz actor in `world`.

        `GameplayStatics.get_all_actors_with_tag` takes a world, unlike
        `EditorActorSubsystem.get_all_level_actors`, which only ever sees the
        editor's. That difference is the whole reason PIE works here.
        """
        return unreal.GameplayStatics.get_all_actors_with_tag(world, TAG)

    def _find_tagged(self, world, prefix):
        """{key: actor} for previz actors carrying `prefix<key>`.

        Matched by tag rather than by label so renaming an actor in the
        viewport cannot quietly unhook it from its calibration. The key is left
        as the string the builder wrote -- see `unit_key`.
        """
        found = {}
        for actor in self._tagged_actors(world):
            for tag in [str(t) for t in actor.tags]:
                if tag.startswith(prefix):
                    found[tag.split(":", 1)[1]] = actor
        return found

    def _max_throw(self):
        """The longest a beam could possibly be, in Unreal cm: the room's
        diagonal. Actual length is traced per frame."""
        venue = self.rig.venue
        return math.dist((0.0, 0.0, 0.0),
                         (venue.width, venue.height, venue.depth)) / 10.0

    def _ignored_by_trace(self, world):
        """Actors a beam trace must pass straight through.

        Three things. The beams themselves, obviously. The fixture bodies, since
        the trace starts at the lens and that is inside its own body -- miss
        this and every beam reports a throw of a few centimetres and disappears.
        And the crowd-zone marker, which is a TriggerBox and so has collision:
        without it every beam stops dead on an invisible annotation box, which
        is maddening to debug precisely because the box does not render.
        """
        ignore = list(self.beams.values())
        for actor in self._tagged_actors(world):
            tags = [str(t) for t in actor.tags]
            if isinstance(actor, unreal.TriggerBase) or "fixture_body" in tags:
                ignore.append(actor)
        return ignore

    def _ball_clearance(self, origin, direction, half_angle_deg):
        """What fraction of this beam's cone gets PAST the ball: 0 to 1.

        The trace below is a single line down the beam's axis, so on its own it
        can only answer yes or no -- and that disagrees with the light, which is
        a real 3-degree cone. Aim a head so its axis clips the edge of the ball
        and the trace says "blocked": the shaft vanishes at the ball while most
        of the cone sails past and lights the far wall. The beam appears to stop
        dead in front of a wall it is visibly still lighting. The light is right
        and the mesh is wrong.

        Seen from the lens, both are discs: the ball spans `asin(R/d)`, the cone
        spans `half_angle`, and their centres are `theta` apart. The blocked
        fraction is the overlap of two circles over the cone's own area. At
        these angles treating them as flat discs is exact to well under a pixel.
        """
        cx, cy, cz = self.ball_centre
        vx, vy, vz = cx - origin.x, cy - origin.y, cz - origin.z
        distance = math.sqrt(vx * vx + vy * vy + vz * vz)
        if distance <= self.ball_radius:
            return 0.0                                # the lens is inside it
        along = (vx * direction.x + vy * direction.y + vz * direction.z) / distance
        theta = math.acos(max(-1.0, min(1.0, along)))
        rho = math.asin(self.ball_radius / distance)
        alpha = math.radians(half_angle_deg)

        if theta >= rho + alpha:
            return 1.0                                # misses the ball entirely
        if theta + alpha <= rho:
            return 0.0                                # wholly behind it
        if theta + rho <= alpha:                      # ball sits inside the cone
            return 1.0 - (rho * rho) / (alpha * alpha)

        # Lens-shaped intersection of two circles.
        a = (theta * theta + alpha * alpha - rho * rho) / (2.0 * theta * alpha)
        b = (theta * theta + rho * rho - alpha * alpha) / (2.0 * theta * rho)
        a = math.acos(max(-1.0, min(1.0, a)))
        b = math.acos(max(-1.0, min(1.0, b)))
        overlap = (alpha * alpha * (a - math.sin(2.0 * a) / 2.0)
                   + rho * rho * (b - math.sin(2.0 * b) / 2.0))
        return max(0.0, 1.0 - overlap / (math.pi * alpha * alpha))

    def _throw_distance(self, origin, direction, ignore):
        """How far this beam travels before it hits something.

        Terminating the shaft where it lands is what makes the mirror ball read
        as solid -- the engine's safety taper already models it as an occluding
        sphere, and a previz that drew beams straight through it would disagree
        with the taper about the show's signature look. It also removes the flat
        disc a full-length cone leaves hanging wherever it happens to stop.
        """
        end = unreal.Vector(origin.x + direction.x * self.throw,
                            origin.y + direction.y * self.throw,
                            origin.z + direction.z * self.throw)
        hit = unreal.SystemLibrary.line_trace_single(
            self.world, origin, end, unreal.TraceTypeQuery.TRACE_TYPE_QUERY1,
            False, ignore, unreal.DrawDebugTrace.NONE, True)
        if hit is None:
            return self.throw
        location = hit.to_tuple()[4]
        return max(10.0, math.dist((origin.x, origin.y, origin.z),
                                   (location.x, location.y, location.z)))

    def close(self):
        if self.handle is not None:
            unreal.unregister_slate_post_tick_callback(self.handle)
            self.handle = None
        try:
            self.sock.close()
        except OSError:
            pass

    # -- per frame ---------------------------------------------------------

    def _drain(self):
        """Take every pending packet and keep the newest per universe.

        Draining rather than reading one per tick matters: the engine sends at
        40 fps and the editor may tick slower, so reading a single packet would
        put the previz further behind the show every second it ran.
        """
        while True:
            try:
                data = self.sock.recv(2048)
            except (BlockingIOError, OSError):
                return
            parsed = parse_artdmx(data)
            if parsed is None:
                continue
            universe, dmx = parsed
            if universe in self.frames:
                self.frames[universe][:len(dmx)] = dmx
                self.packets += 1

    @staticmethod
    def _color_for(fixture, frame):
        """(r, g, b) 0-1 for a fixture, from whichever colour system it has.

        A mixing fixture takes its channels directly. A mover has a mechanical
        wheel with 14 discrete slots and no mixing at all, so its value is
        looked up against the slot table the `.qxf` declares -- which is what
        lets one colour concept drive both kinds of hardware.
        """
        idx = fixture.index_of(rigmod.RED)
        if idx is not None:
            def value(role):
                i = fixture.index_of(role)
                return 0.0 if i is None else frame[i] / 255.0
            r, g, b = value(rigmod.RED), value(rigmod.GREEN), value(rigmod.BLUE)
            w = value(rigmod.WHITE)
            return (min(1.0, r + w), min(1.0, g + w), min(1.0, b + w))

        idx = fixture.index_of(rigmod.COLOR_WHEEL)
        if idx is None:
            return (1.0, 1.0, 1.0)
        raw = frame[idx]
        for name in fixture.profile.modes[fixture.mode]:
            channel = fixture.profile.channels[name]
            if channel.role != rigmod.COLOR_WHEEL:
                continue
            for slot in channel.color_slots:
                if slot.lo <= raw <= slot.hi:
                    return tuple(c / 255.0 for c in slot.rgb)
            break
        # Above the last colour slot the wheel is spinning ("auto colour
        # change"). White is an honest stand-in for "some colour, changing".
        return (1.0, 1.0, 1.0)

    @staticmethod
    def _split_for(fixture, frame):
        """The two colours in the aperture, or None if this slot is one colour.

        A colour wheel is a disc of segments and its in-between positions put
        half of one and half of the next in front of the lens, so the beam
        leaves two-toned across its width rather than blended. The MingJie wheel
        declares seven of these and the show uses them, and previz drew every
        one as a single muddy average until 2026-08-08.

        The pair comes from `engine.rig`, resolved out of the profile's own
        single-colour slots -- see `_resolve_split_slots`. A mixing fixture has
        no wheel and therefore never splits.
        """
        idx = fixture.index_of(rigmod.COLOR_WHEEL)
        if idx is None:
            return None
        raw = frame[idx]
        for name in fixture.profile.modes[fixture.mode]:
            channel = fixture.profile.channels[name]
            if channel.role != rigmod.COLOR_WHEEL:
                continue
            for slot in channel.color_slots:
                if slot.lo <= raw <= slot.hi:
                    if slot.pair is None:
                        return None
                    return tuple(tuple(c / 255.0 for c in half)
                                 for half in slot.pair)
            break
        return None

    def _aim_beam(self, fixture, light, beam, material, glow, level, color,
                  split=None):
        """Stretch and aim one fixture's shaft along the beam it is emitting.

        The cone mesh runs from local Z -50 to +50 with its apex at +Z, so
        pointing its local +Z back down the beam puts the apex at the lens and
        the wide end out in the room -- a beam that narrows toward the fixture
        would be immediately, obviously wrong.

        Takes its actors as arguments rather than looking them up, because the
        movers are indexed by head and the pinspots by fixture id, and this is
        the same job for both.
        """
        if beam is None:
            return
        mesh = beam.static_mesh_component
        mesh.set_visibility(level > 0.0, False)
        if level <= 0.0:
            # Put the ball's glow out too, or a fixture that blacks out while
            # aimed at the ball leaves its share of the wash hanging there.
            self._ball_glow(glow, 0.0, color, 0.0)
            return

        origin = light.get_actor_location()
        direction = light.get_actor_forward_vector()
        half_angle = fixture.output_beam_deg / 2.0
        # Let the shaft through the ball if any of the cone gets past it. A beam
        # clipping the ball's edge really does keep going -- the light does, so
        # the mesh has to as well, or the previz shows a beam stopping dead in
        # front of a wall it is visibly still lighting.
        clear = self._ball_clearance(origin, direction, half_angle)
        stopped_by_ball = clear <= 0.02
        ignore = self.trace_ignore if stopped_by_ball else self.trace_ignore_ball
        # Full brightness when the ball stops the beam dead, because then every
        # bit of shaft actually drawn is between the lens and the ball and
        # nothing is obstructing it. Only a beam drawn PAST the ball is partial.
        shaft = 1.0 if stopped_by_ball else clear
        length = self._throw_distance(origin, direction, ignore)
        radius = length * math.tan(math.radians(half_angle))

        beam.set_actor_location(
            unreal.Vector(origin.x + direction.x * length / 2.0,
                          origin.y + direction.y * length / 2.0,
                          origin.z + direction.z * length / 2.0), False, False)
        beam.set_actor_rotation(
            unreal.MathLibrary.make_rot_from_z(
                unreal.Vector(-direction.x, -direction.y, -direction.z)), False)
        # The cone is 100 cm across and 100 cm long at scale 1.
        beam.set_actor_scale3d(
            unreal.Vector(2 * radius / 100.0, 2 * radius / 100.0, length / 100.0))

        # What the ball catches, it sprays over the room.
        self._ball_glow(glow, level * (1.0 - clear), color, full_lumens(fixture))

        if material is not None:
            # A split slot puts half of one wheel segment and half of the next
            # in the aperture, so the beam leaves two-toned across its width.
            # `ColorB` equals `Color` when it is not split, which makes the
            # material's blend a no-op and costs one parameter write.
            # `SplitNormal` points UP and the material lerps Color -> ColorB as
            # the dot product with it rises, so ColorB is the TOP half. The
            # first-named colour of a slot ("Green + Blue") goes on top, which
            # is the convention the show describes them by.
            top, bottom = split if split else (color, color)
            material.set_vector_parameter_value(
                "Color", unreal.LinearColor(*bottom, 1.0))
            material.set_vector_parameter_value(
                "ColorB", unreal.LinearColor(*top, 1.0))
            material.set_vector_parameter_value(
                "SplitNormal", unreal.LinearColor(*split_plane(direction), 0.0))
            # Where this shaft starts and how far it gets, so the material can
            # fade it along its length. Written every frame because both move:
            # the lens follows the head, and the throw is re-traced.
            material.set_vector_parameter_value(
                "Origin", unreal.LinearColor(origin.x, origin.y, origin.z, 0.0))
            material.set_scalar_parameter_value("Reach", length)
            material.set_scalar_parameter_value("Falloff", surviving(length))
            # Dimmed by how much of the cone clears the ball, so a grazing beam
            # reads as the partial thing it is rather than as a full shaft
            # punched through a solid object. One brightness has to serve the
            # whole shaft, so the stub between lens and ball comes out dimmer
            # than it really is -- the alternative is two meshes per beam, and
            # the far half is the half anyone is looking at.
            material.set_scalar_parameter_value(
                "Brightness", level * BEAM_GAIN * shaft
                * self.beam_share.get(unit_key(fixture), 1.0))

    def _light_up(self, key, light, level, color, lumens, split=None,
                  beam_deg=3.0):
        """Set one fixture's actual light to its level and colour.

        A split wheel position needs TWO lights and this is why: an Unreal spot
        light has one colour, and its volumetric fog is what makes a beam read
        as a beam at all. Splitting only the shaft mesh left the fog a single
        average and the beam still looked one colour, which is exactly what was
        reported.

        So the two halves of the aperture become two lights at half intensity,
        tipped a quarter of the cone apart in ELEVATION -- which is up and down
        in the room, since the previz's rotator carries elevation as pitch. They
        overlap down the middle, as the real halves do. The primary keeps the
        bottom colour so a fixture with no second light still looks sane.
        """
        spot = light.spot_light_component
        second = self.lights2.get(key)
        lit = level > 0.0

        if split is None or second is None:
            spot.set_visibility(lit, False)
            if lit:
                spot.set_intensity(lumens * level)
                spot.set_light_color(unreal.LinearColor(*color, 1.0))
            if second is not None:
                second.spot_light_component.set_visibility(False, False)
            return

        top, bottom = split
        other = second.spot_light_component
        spot.set_visibility(lit, False)
        other.set_visibility(lit, False)
        if not lit:
            return

        # Half the output each, because each half-aperture passes half the beam.
        half = lumens * level * 0.5
        spot.set_intensity(half)
        other.set_intensity(half)
        spot.set_light_color(unreal.LinearColor(*bottom, 1.0))
        other.set_light_color(unreal.LinearColor(*top, 1.0))

        # Tip them apart. `light` has already been aimed this frame, so its
        # rotation is the true aim to work from; next frame re-aims it from the
        # DMX again, so nothing accumulates.
        aim = light.get_actor_rotation()
        tilt = beam_deg / 4.0
        second.set_actor_location(light.get_actor_location(), False, False)
        second.set_actor_rotation(
            unreal.Rotator(aim.roll, aim.pitch + tilt, aim.yaw), False)
        light.set_actor_rotation(
            unreal.Rotator(aim.roll, aim.pitch - tilt, aim.yaw), False)

    @staticmethod
    def _ball_glow(glow, caught, color, lumens):
        """Light the room with whatever the ball intercepted from this beam.

        `caught` is the fixture's level times the fraction of its cone the ball
        blocks, so this rises as a head sweeps onto the ball and goes out when
        it leaves -- it is never ambient, and a dark rig leaves a dark room.
        """
        if glow is None:
            return
        point = glow.point_light_component
        lit = caught > 0.0
        point.set_visibility(lit, False)
        if not lit:
            return
        point.set_intensity(lumens * BALL_GLOW_FRACTION * caught)
        point.set_light_color(unreal.LinearColor(*color, 1.0))

    def _drive(self, key, fixture, light, level, color, spun, ray, split=None):
        """Everything one fixture puts into the room this frame.

        The same four steps for a mover and for a pinspot. The only difference
        between them is upstream: a mover's aim is decoded from DMX through the
        show's calibration, and a pinspot's is where its bracket points.
        """
        self._light_up(key, light, level, color, full_lumens(fixture),
                       split, fixture.output_beam_deg)
        self._aim_beam(fixture, light, self.beams.get(key),
                       self.beam_materials.get(key), self.glows.get(key),
                       level, color, split)
        self._place_reflections(key, fixture, ray, level, color, spun)

    def _place_reflections(self, key, fixture, ray, level, color, spun):
        """Draw what the mirror ball does with this fixture's beam.

        Not just the dots. A beam striking a mirror ball does not vanish and
        reappear across the room -- it bursts into as many thin beams as there
        are facets turned toward it, and in a hazy room those shafts are the
        look. Each one is drawn from the facet it leaves to the dot it makes.

        `ray` is (origin, unit direction) in the show's own mm frame. For a
        mover it comes from `geometry.ray()` -- the same call the F4 safety
        taper casts -- so these are reflections of the beam the engine believes
        it is emitting, not of a separately derived one. For a fixture that
        cannot be aimed it is just where the thing is bolted and what it looks
        at, which has no calibration in it and nothing to drift.
        """
        pools = self.reflections.get(key)
        if pools is None:
            return
        venue = self.rig.venue

        hits = ()
        if level > 0.0 and venue.ball_radius > 0.0:
            origin, direction = ray
            stride = self.stride.get(key, 1)
            hits = mb.dots(origin, direction, fixture.output_beam_deg / 2.0,
                           venue.ball, venue.ball_radius,
                           venue.width, venue.height, venue.depth, spun,
                           stride=stride)
            if stride > 1:
                self.dots_sampled[key] = stride

        # The budget is enforced at the source now -- `self.stride` picks a fixed
        # subset of the ball's facets per fixture, so a wide source lights fewer
        # mirrors rather than lighting all of them and having the list thinned
        # afterwards. This is only the backstop for a geometry the estimate did
        # not see coming, and it drops the tail rather than resampling, because
        # resampling per frame is precisely the thing that crawled.
        capacity = min(REFLECT_BUDGET, self.capacity.get(key, 0))
        if len(hits) > capacity:
            hits = hits[:capacity]
            self.dots_truncated += 1
        shown = len(hits)

        # Whether this fixture's shafts are worth building at all. They are
        # additive over a near-black room, so below about one 8-bit level they
        # add literally nothing to the picture -- and a wide source is exactly
        # the case where there are hundreds of them AND each is faintest, since
        # the ball divides one beam among every facet it lights. Skipping them
        # is half the per-reflection cost of a pinspot. Note this is decided by
        # brightness, not by fixture type: raise the pinspot's declared lumens
        # and its shafts come back on their own.
        share = self.illuminance.get(key, 1.0)
        draw_rays = level * RAY_GAIN * share > RAY_FLOOR
        # Scratch structs, mutated and handed to the Transform constructor,
        # which copies. This loop runs about a thousand times a frame with the
        # pinspots throwing the whole near cap, and at that scale allocating
        # four Vectors and two Rotators per reflection is the single most
        # expensive thing the driver does: measured at 11.7 us per reflection
        # against 1.3 us for the optics behind it. Ugly, and worth it -- naive
        # allocation put the tick at 15.9 ms.
        loc, rot, scale = self._scratch
        dots, rays = [], []
        for hit in hits:
            fx, fy, fz = previz_scene.point(*hit.facet)
            lx, ly, lz = previz_scene.point(*hit.point)
            # A surface normal is a direction, so it takes the axis permutation
            # but not the mm->cm scale (see previz/scene.py).
            nx, ny, nz = hit.normal[2], hit.normal[0], hit.normal[1]
            spot = previz_scene.mm(hit.spot) / 100.0

            # The dot: a flat tile lying ON the wall, lifted clear of it.
            # Square, because the facet that threw it is -- a real mirror ball
            # sprays squares, and it is only the blur of distance that ever
            # makes them look round.
            loc.x = lx + nx * DOT_LIFT
            loc.y = ly + ny * DOT_LIFT
            loc.z = lz + nz * DOT_LIFT
            scale.x = scale.y = spot
            scale.z = 1.0
            dots.append(unreal.Transform(
                loc, rot_from_z_into(rot, nx, ny, nz), scale))

            if not draw_rays:
                continue
            # The shaft: a cone from the facet out to the dot. Apex at +Z local,
            # so pointing local +Z back up the beam puts the point on the ball.
            dx, dy, dz = lx - fx, ly - fy, lz - fz
            length = max(1.0, math.sqrt(dx * dx + dy * dy + dz * dz))
            ux, uy, uz = dx / length, dy / length, dz / length
            loc.x = fx + ux * length / 2.0
            loc.y = fy + uy * length / 2.0
            loc.z = fz + uz * length / 2.0
            scale.z = length / 100.0
            rays.append(unreal.Transform(
                loc, rot_from_z_into(rot, -ux, -uy, -uz), scale))

        # Park whatever was in use last frame and is not now. Writing only the
        # range that changed rather than the whole pool is what keeps this
        # affordable when a head sweeps off the ball and back.
        for _ in range(shown, self.dots_shown.get(key, 0)):
            dots.append(PARKED)
        # Rays park independently of dots, because `draw_rays` can drop to zero
        # while the dots stay -- and an instance that is simply never written
        # again keeps whatever transform it last had, so it would hang in the
        # room forever.
        for _ in range(len(rays), self.rays_shown.get(key, 0)):
            rays.append(PARKED)
        if dots:
            pools["dots"].batch_update_instances_transforms(0, dots, True, True, True)
        if rays:
            pools["rays"].batch_update_instances_transforms(0, rays, True, True, True)
        self.dots_shown[key] = shown
        self.rays_shown[key] = shown if draw_rays else 0
        if shown:
            self.spot_mm = (min(h.spot for h in hits[:shown]),
                            max(h.spot for h in hits[:shown]))

        materials = self.reflect_materials.get(key)
        # How bright one reflection is, relative to the brightest fixture's.
        #
        # It is the ILLUMINANCE this fixture puts on the ball -- candela over
        # distance squared -- and deliberately not "its share of the beam
        # divided among the facets it lights". Each mirror re-radiates what
        # lands on it, and what lands on it does not depend on how many of its
        # neighbours are also lit; the facet's own size versus the dot it throws
        # is already carried by `hit.spot`. Dividing by the lit count instead
        # would make a head that half-misses the ball throw brighter dots than
        # one square on it, which is backwards.
        share = self.illuminance.get(key, 1.0)
        look = (color, round(level * share, 4))
        if materials is not None and self.dot_look.get(key) != look:
            tint = unreal.LinearColor(*color, 1.0)
            for name, gain in (("dots", DOT_GAIN), ("rays", RAY_GAIN)):
                materials[name].set_vector_parameter_value("Color", tint)
                # The rays share the beam material, which blends toward `ColorB`
                # for a split beam. Both ends the same here, so the blend is a
                # no-op -- and the ball's spray of a split beam is drawn in the
                # slot's AVERAGED colour, deliberately. Each facet really does
                # reflect whichever half struck it, but every reflection of one
                # fixture shares a single material instance (that sharing is
                # what makes a couple of hundred of them affordable), so drawing
                # them individually would need per-instance colour and a
                # per-instance write. Left as a known simplification rather than
                # a silent one -- the shaft carries the split, the spray averages
                # it, and a mirror ball's spray really is a mix of both halves.
                if name == "rays":
                    materials[name].set_vector_parameter_value("ColorB", tint)
                materials[name].set_scalar_parameter_value(
                    "Brightness", level * gain * share)
            self.dot_look[key] = look

    def settle(self):
        """Finish every move now, and redraw. For stills.

        A SceneCapture runs no slate ticks, so a caller that writes a frame and
        photographs it immediately would catch four heads that had not started
        moving yet -- and with a mechanical model in the path, "not yet" is now
        a whole second wide rather than a rounding error. Two ticks: the first
        so the yokes hear the command, the second so everything hanging off
        their position (shafts, ball reflections, the glow) is redrawn from
        where they ended up.
        """
        self.tick(0.0)
        self.servos.settle()
        self.tick(0.0)

    def tick(self, delta):
        started = time.perf_counter()
        try:
            self._drain()

            # Follow Play and Stop. Cheap to check, and getting it wrong is the
            # difference between a live previz and one frozen mid-look.
            world = self.active_world()
            if world != self.world:
                self.bind(world)
                self.rebinds += 1

            geometry = self.rig.geometry

            # Advance the ball. rpm * 6 = degrees per second.
            self.ball_angle = (self.ball_angle
                               + delta * mb.DEFAULT_RPM * 6.0) % 360.0
            spun = mb.spin(self.facets, self.ball_angle)

            for fixture in self.rig.movers:
                key = unit_key(fixture)
                light = self.lights.get(key)
                if light is None:
                    continue
                frame = self.frames.get(fixture.universe)
                if frame is None:
                    continue

                def word(coarse_role, fine_role):
                    hi = fixture.index_of(coarse_role)
                    lo = fixture.index_of(fine_role)
                    if hi is None:
                        return 0
                    return (frame[hi] << 8) | (0 if lo is None else frame[lo])

                # Where the console says to be, then where the yoke has got to.
                # Decoding the SERVO's position rather than the command is what
                # makes a move take time; the decode itself is unchanged, so
                # previz and show still share one geometry.
                pan, tilt = self.servos.of(
                    key, fixture, geometry.heads[fixture.head]).follow(
                    word(rigmod.PAN, rigmod.PAN_FINE),
                    word(rigmod.TILT, rigmod.TILT_FINE), delta)
                aim = geometry.decode(fixture.head, pan, tilt)
                light.set_actor_rotation(
                    unreal.Rotator(0.0, aim.elev_deg,
                                   geometry.world_bearing(fixture.head, aim)),
                    False)

                dim_idx = fixture.index_of(rigmod.DIMMER)
                level = 1.0 if dim_idx is None else frame[dim_idx] / 255.0
                color = self._color_for(fixture, frame)
                self._drive(key, fixture, light, level, color, spun,
                            geometry.ray(fixture.head, aim),
                            self._split_for(fixture, frame))

            for fixture in self.statics:
                key = unit_key(fixture)
                light = self.lights.get(key)
                frame = self.frames.get(fixture.universe)
                if light is None or frame is None:
                    continue
                # No pan, no tilt, and no dimmer either: this fixture's mode
                # selector sits where a dimmer would be and brightness comes
                # entirely from the colour channels (see its note in rig.json).
                # So the level IS the colour's own magnitude.
                color = self._color_for(fixture, frame)
                level = max(color)
                color = (1.0, 1.0, 1.0) if level <= 0.0 else tuple(
                    c / level for c in color)
                self._drive(key, fixture, light, level, color, spun,
                            self.aims.get(key))
        except Exception:                            # noqa: BLE001
            # A raise inside a slate tick callback fires every frame and floods
            # the log until the editor is unusable. Count it, print the first,
            # and keep the previz alive -- it is a viewer, not the show.
            self.errors += 1
            if self.errors == 1:
                traceback.print_exc()
        finally:
            # This runs on the game thread, so its cost comes straight off the
            # editor's frame rate. Tracked because the dot pool is the one part
            # that scales -- more facets means more actor writes per frame.
            spent = time.perf_counter() - started
            self.tick_ms = spent * 1000.0
            self.tick_ms_worst = max(self.tick_ms_worst, self.tick_ms)
            self.ticks += 1


# ------------------------------------------------------------------- api -----

def start(event="despacio"):
    stop()

    event_dir = Path(event)
    if not event_dir.exists():
        event_dir = REPO / "events" / event

    state = Live(event_dir)
    state.handle = unreal.register_slate_post_tick_callback(state.tick)
    cosmos_live_state.current = state

    placed = [f for f in state.rig.fixtures if f.position is not None]
    missing = [f.name for f in placed if unit_key(f) not in state.lights]
    print(f"[cosmos] live: listening on 0.0.0.0:{ARTNET_PORT}, "
          f"universe(s) {list(state.frames)}, {len(state.rig.movers)} head(s) "
          f"and {len(state.statics)} fixed fixture(s) driven")
    if missing:
        print(f"[cosmos] no previz actor for: {', '.join(missing)} "
              f"-- run build_level.py first")
    return state


def stop():
    state = cosmos_live_state.current
    if state is not None:
        state.close()
        print(f"[cosmos] live: stopped after {state.packets} packet(s), "
              f"{state.errors} error(s)")
        cosmos_live_state.current = None


def status():
    state = cosmos_live_state.current
    if state is None:
        print("[cosmos] live: not running")
        return
    print(f"[cosmos] live: {state.packets} packet(s), {state.errors} error(s), "
          f"{len(state.lights)} fixture(s), driving world "
          f"{state.world.get_name()!r} ({state.rebinds} rebind(s))")
    print(f"[cosmos] tick: {state.tick_ms:.2f} ms now, {state.tick_ms_worst:.2f} ms "
          f"worst over {state.ticks} tick(s)")
    print(f"[cosmos] ball: spun {state.ball_angle:.0f} deg, {len(state.facets)} "
          f"facets of {state.facet_mm:.0f} mm, reflections showing "
          f"{ {h: n for h, n in sorted(state.dots_shown.items())} }")
    print(f"[cosmos] dots: {state.spot_mm[0]:.0f}-{state.spot_mm[1]:.0f} mm "
          f"across (lens aperture {mb.APERTURE_MM:.0f} mm, an assumption)")
    if state.dots_sampled:
        print(f"[cosmos] facet stride (REFLECT_BUDGET={REFLECT_BUDGET}): "
              f"{ {k: f'every {n}' for k, n in sorted(state.dots_sampled.items())} }"
              f" -- a source wide enough to overshoot the ball lights more "
              f"facets than are worth drawing; density drops, coverage does not")
    if state.dots_truncated:
        print(f"[cosmos] WARNING: the reflection pool ran out on "
              f"{state.dots_truncated} frame(s) -- cosmos_live and build_level "
              f"disagree about the facet count, so re-run go.py to rebuild")
    moving = [k for k, s in state.servos.servos.items()
              if s.target is not None and not s.arrived(*s.target)]
    print(f"[cosmos] yokes: {servomod.DEFAULT_PAN_DEG_PER_S:.0f} deg/s pan, "
          f"{servomod.DEFAULT_TILT_DEG_PER_S:.0f} deg/s tilt (ASSUMED -- see "
          f"engine/servo.py), "
          + (f"in flight: {', '.join(sorted(moving))}" if moving
             else "every head is where it was told to be"))
    for fixture in state.rig.movers:
        frame = state.frames[fixture.universe]
        pan = fixture.index_of(rigmod.PAN)
        tilt = fixture.index_of(rigmod.TILT)
        dim = fixture.index_of(rigmod.DIMMER)
        # Commanded, then where the yoke has actually got to -- printed as the
        # same coarse byte so the two are comparable at a glance.
        servo = state.servos.servos.get(unit_key(fixture))
        at = ("" if servo is None or servo.pan is None else
              f"  (at {int(servo.pan) >> 8:>3},{int(servo.tilt) >> 8:>3})")
        print(f"    {fixture.name:<16} pan={frame[pan]:>3} tilt={frame[tilt]:>3} "
              f"dim={0 if dim is None else frame[dim]:>3}{at}")
    for fixture in state.statics:
        frame = state.frames[fixture.universe]
        rgb = Live._color_for(fixture, frame)
        print(f"    {fixture.name:<16} rgb=({rgb[0]:.2f},{rgb[1]:.2f},{rgb[2]:.2f}) "
              f"-> level {max(rgb):.2f}")
