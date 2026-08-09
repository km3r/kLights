"""
Build the previz level from an event's config. Runs inside the Unreal editor.

    python previz/ue_remote.py previz/unreal/Content/Python/build_level.py

Everything this places is tagged `cosmos_previz` and rebuilt from scratch each
run, so the level is a *generated artefact* -- edit `venue.json` / `rig.json`
and re-run rather than dragging things around in the viewport. Actors without
that tag (a camera you parked somewhere useful) are left alone.

The room is deliberately dark and hazy. A 3-degree beam is invisible in clear
air until it lands on something, so without volumetric fog there is nothing to
previsualize; and against light walls the bounce washes out exactly the
contrast the show is made of.
"""

import math
import sys
from pathlib import Path

import unreal

REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from previz import mirrorball as mb                 # noqa: E402
from previz import scene as previz_scene            # noqa: E402

TAG = "cosmos_previz"
LEVEL_PATH = "/Game/Maps/Previz"
MATERIAL_DIR = "/Game/Previz/Materials"

SHAPES = {
    "cube": "/Engine/BasicShapes/Cube.Cube",
    "sphere": "/Engine/BasicShapes/Sphere.Sphere",
    "cylinder": "/Engine/BasicShapes/Cylinder.Cylinder",
    "cone": "/Engine/BasicShapes/Cone.Cone",
    "plane": "/Engine/BasicShapes/Plane.Plane",
}
BEAM_MATERIAL = "M_PrevizBeam"
DOT_MATERIAL = "M_PrevizDot"

# The shoulder on a reflection dot's radial falloff, as the exponent in
# `(1 - r) ** DOT_SHOULDER` across its own quad. Below 1 the dot is nearly all
# soft edge and reads as a smudge; at 1 it is a linear cone, soft but muddy;
# above about 3 the core hardens back up and the softening stops being visible.
# 1.8 keeps a bright core with the fade in the outer third.
DOT_SHOULDER = 1.8

# How abruptly a split beam changes colour across its width, per Unreal cm.
# The two halves of the aperture meet at a hard mechanical edge, so this is
# deliberately steep -- large enough that the transition is a couple of
# centimetres wide and therefore antialiases rather than stair-steps, small
# enough that it is not a jagged line. It is NOT a soft blend: a wheel sitting
# between two segments throws two colours, it does not mix them.
SPLIT_SHARPNESS = 0.4

# The basic shapes are 100 cm across at scale 1, so a scale is a size in metres.
SHAPE_SIZE = 100.0
# Mirror tiles cover 92% of their share of the ball, so the dark core shows
# through as grout. A ball whose tiles meet edge to edge is a chrome sphere.
TILE_COVERAGE = 0.92
# How far a tile floats off the core, in cm. Enough to beat z-fighting on a
# curved surface and no more. Tiles are flat planes rather than thin boxes on
# purpose: a box's four side faces stand perpendicular to the ball, so they
# catch grazing light that the mirror faces do not, and the ball comes out
# looking like a bright wireframe globe with dark panels between the wires.
TILE_LIFT = 0.15
# The level's fixed exposure, as an auto-exposure brightness pinned top and
# bottom. 1.0 is the engine default and is what the room was lit and tuned
# against -- lower it and everything here reads brighter, not darker.
EXPOSURE = 1.0

# The room's surfaces, and the haze in it. These three are one decision, so they
# live together.
#
# The room does not fill with light because of the fog density -- raising that
# alone just makes the beams brighter against a black room, because Unreal's
# volumetric fog is SINGLE scatter: a point in the air only glows if a light
# shines on it directly, and four 3-degree cones light almost none of a 9 m
# room. What fills a real room is light bouncing off its surfaces, and at 0.05
# albedo this room was blacker than any real venue -- there was nothing for the
# beams to bounce off. Raising the albedo and letting Lumen carry it is the
# honest version, and it stays black when the fixtures are dark, which pure
# ambient does not.
ROOM_ALBEDO = 0.16
# Emissive as a multiple of the albedo. Only a floor now, so an unlit wall is
# distinguishable from no wall at all; the bounce does the rest.
ROOM_EMISSIVE = 0.35
# Enough haze to carry a beam without swallowing the room behind it. Raising
# this alone does NOT fill the room -- see the note above.
FOG_DENSITY = 0.35
# How hard the ball's re-radiated light works on the haze. See _ball_glow.
BALL_GLOW_SCATTER = 1.0

# Shadow map resolution multiplier on every fixture. THIS IS NOT A QUALITY DIAL.
# At the stock 1.0 the mirror ball does not shadow these beams at all: the beam
# stops on the ball, the fog stops on the ball, and a bright spot still lands on
# the wall behind it exactly where the beam would have gone. Unreal sizes a
# spot light's shadow map from the light's screen footprint, and a 3-degree cone
# is small enough that the ball's shadow falls below the sampling and vanishes.
#
# Measured on one head aimed at the ball, everything else dark, with the camera
# on the far wall:
#   1.0 (stock)  wall spot present
#   2.0          wall spot GONE, and the ball's shadow is crisp
#   cone widened to 20 deg at 1.0: crisp shadow -- so it is the cone, not the
#   ball, and not the caster's size (tripling the ball's radius changed nothing)
#   cone narrowed to 1.2 deg at 1.0: beam sails straight through, fog and all
# Raising `r.Shadow.TexelsPerPixel` / `r.Shadow.MinResolution` instead makes it
# WORSE, and turning virtual shadow maps off changes nothing -- both paths leak.
# 2.0 is the engine's own clamp on this property, so there is no more to give:
# if a future rig has a tighter beam than 3 degrees, expect this back.
SHADOW_RESOLUTION_SCALE = 2.0

actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
subobjects = unreal.get_engine_subsystem(unreal.SubobjectDataSubsystem)


# ------------------------------------------------------------------ helpers --

def log(message):
    print(f"[cosmos] {message}")


def vec(xyz):
    return unreal.Vector(float(xyz[0]), float(xyz[1]), float(xyz[2]))


def rot(pyr):
    """previz.scene emits [pitch, yaw, roll]; unreal.Rotator takes (roll, pitch, yaw)."""
    return unreal.Rotator(float(pyr[2]), float(pyr[0]), float(pyr[1]))


def _save_and_verify(path):
    """Save a freshly built asset and confirm the registry agrees it exists.

    Worth checking rather than trusting. `clear_previous()` deletes this
    directory and the build immediately recreates the same paths, and that
    sequence has been observed to leave the asset registry insisting the assets
    do not exist while the .uasset files sit happily on disk. When that happens
    the live driver's `load_asset` returns None -- which used to blank the dots'
    material and turn them into black spheres. Failing loudly here beats
    debugging it from the far end.
    """
    unreal.EditorAssetLibrary.save_asset(path)
    if not unreal.EditorAssetLibrary.does_asset_exist(path):
        raise RuntimeError(
            f"{path} was created and saved but the asset registry does not see "
            f"it. Re-run build_level.py; if it persists, restart the editor so "
            f"the registry rescans Content/.")


def make_material(name, color, roughness=0.9, metallic=0.0, emissive=0.0):
    """A flat constant material asset, created if it does not already exist.

    Built node by node rather than by instancing BasicShapeMaterial, because
    that material's parameter names are an engine detail we would be guessing
    at -- and a wrong guess yields a silently unchanged default colour.
    """
    path = f"{MATERIAL_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.EditorAssetLibrary.load_asset(path)

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = tools.create_asset(name, MATERIAL_DIR, unreal.Material,
                                  unreal.MaterialFactoryNew())
    lib = unreal.MaterialEditingLibrary

    base = lib.create_material_expression(
        material, unreal.MaterialExpressionConstant3Vector, -400, 0)
    base.set_editor_property("constant", unreal.LinearColor(*color))
    lib.connect_material_property(base, "", unreal.MaterialProperty.MP_BASE_COLOR)

    for value, prop, offset in ((roughness, unreal.MaterialProperty.MP_ROUGHNESS, 150),
                                (metallic, unreal.MaterialProperty.MP_METALLIC, 300)):
        node = lib.create_material_expression(
            material, unreal.MaterialExpressionConstant, -400, offset)
        node.set_editor_property("r", float(value))
        lib.connect_material_property(node, "", prop)

    if emissive:
        node = lib.create_material_expression(
            material, unreal.MaterialExpressionConstant3Vector, -400, 450)
        node.set_editor_property(
            "constant", unreal.LinearColor(color[0] * emissive, color[1] * emissive,
                                           color[2] * emissive, 1.0))
        lib.connect_material_property(node, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)

    lib.recompile_material(material)
    _save_and_verify(path)
    return material


def _soft_edge(material, lib, output, exponent):
    """Multiply `output` by a radial falloff across the mesh's own UVs.

    Full at the centre, zero at the edge of the quad. What it is for: a
    reflection dot drawn as a flat emissive tile has a knife-edge boundary, and
    a hard-edged constant rectangle is the one thing light landing on a wall
    never looks like. Nothing in the real path has a hard edge -- the mirror is
    small, the lens has an aperture, the air between is hazy -- so the dot reads
    as a decal stuck to the wall rather than as light on it.

    Radial rather than a soft-edged square, even though the facet throwing it is
    square. At the ranges these land -- 5 to 13 m off the ball -- the aperture
    blur has long since rounded them off, which the square version of this
    comment already conceded.

    The falloff costs brightness: its mean over the quad is well under 1, so the
    dot's PHYSICAL size (`mirrorball.Dot.spot`, from the lens aperture and the
    throw) is now the width at which it fades to nothing rather than the width
    of a solid patch. `DOT_GAIN` carries the difference.
    """
    uv = lib.create_material_expression(
        material, unreal.MaterialExpressionTextureCoordinate, -1100, 900)
    centre = lib.create_material_expression(
        material, unreal.MaterialExpressionConstant2Vector, -1100, 1020)
    centre.set_editor_property("r", 0.5)
    centre.set_editor_property("g", 0.5)

    radius = lib.create_material_expression(
        material, unreal.MaterialExpressionDistance, -850, 950)
    lib.connect_material_expressions(uv, "", radius, "A")
    lib.connect_material_expressions(centre, "", radius, "B")

    # UV distance runs 0..0.5 across the half-width, so double it to reach 1 at
    # the quad's edge midpoint.
    doubled = lib.create_material_expression(
        material, unreal.MaterialExpressionMultiply, -650, 950)
    lib.connect_material_expressions(radius, "", doubled, "A")
    two = lib.create_material_expression(
        material, unreal.MaterialExpressionConstant, -850, 1080)
    two.set_editor_property("r", 2.0)
    lib.connect_material_expressions(two, "", doubled, "B")

    inside = lib.create_material_expression(
        material, unreal.MaterialExpressionOneMinus, -480, 950)
    lib.connect_material_expressions(doubled, "", inside, "")
    clamped = lib.create_material_expression(
        material, unreal.MaterialExpressionSaturate, -340, 950)
    lib.connect_material_expressions(inside, "", clamped, "")

    # The shoulder. 1.0 is a linear cone and reads soft but muddy; raising it
    # keeps a bright core and puts the softness in the last part of the radius,
    # which is what a real spot on a wall looks like.
    shaped = lib.create_material_expression(
        material, unreal.MaterialExpressionPower, -200, 950)
    lib.connect_material_expressions(clamped, "", shaped, "Base")
    power = lib.create_material_expression(
        material, unreal.MaterialExpressionConstant, -340, 1080)
    power.set_editor_property("r", float(exponent))
    lib.connect_material_expressions(power, "", shaped, "Exponent")

    softened = lib.create_material_expression(
        material, unreal.MaterialExpressionMultiply, 0, 700)
    lib.connect_material_expressions(output, "", softened, "A")
    lib.connect_material_expressions(shaped, "", softened, "B")
    return softened


def make_emissive_material(name, round_off, taper=False, soft_edge=0.0,
                           split=False):
    """An additive, unlit, `Color * Brightness` material.

    `round_off` multiplies by `1 - Fresnel`, which peaks where the surface faces
    the camera. That is what stops a beam's cone looking like a flat triangle --
    it makes the shaft read as round. It is exactly wrong for a reflection dot,
    though: a dot is a half-buried sphere on a wall, so the ones on the ceiling
    and floor are always seen at a grazing angle and Fresnel dims precisely the
    dots you most want to see. Dots are flat.

    `taper` fades the shaft along its length: brightness falls from full at
    `Origin` to `Falloff` at `Reach` centimetres away. Beams really do lose
    intensity crossing a hazy room -- every centimetre of haze scatters a little
    of the beam sideways, which is the same scattering that makes the beam
    visible at all, so a beam you can SEE is by definition one that is losing
    light. A shaft of constant brightness reads as a solid rod.

    Measured from the beam's own start rather than from the mesh's local Z on
    purpose. It means one material serves both jobs: for a head's shaft
    `Origin` is the lens, and for the mirror ball's spray `Origin` is the ball,
    where all several-hundred reflected shafts genuinely do begin -- so a single
    shared parameter fades all of them correctly at once, which a per-instance
    local coordinate could not do.

    Volumetric fog alone cannot draw these fixtures. Its froxel grid is screen
    space by depth slice, and a 3-degree beam is thinner than a froxel over most
    of a 9 m room, so the shaft renders as a string of beads no matter how the
    grid is tuned -- pushing the grid fine enough to resolve it blows the
    renderer's voxel budget and it silently falls back to something coarser.
    The mesh is the beam's crisp core, and it is resolution-independent, which
    volumetric fog is not: the froxel grid is screen space by depth slice, and
    at stock settings a froxel is wider than a 3-degree beam is at room
    distance, so fog alone renders the shaft as a row of blocks. Tuned fog is
    still worth having for the glow around the mesh -- see `apply_render_cvars`
    and the fog component in `build_atmosphere` -- but it cannot carry the beam
    on its own. Drawing the shaft as geometry is also exactly what Unreal's own
    DMXFixtures moving head does.
    """
    path = f"{MATERIAL_DIR}/{name}"
    if unreal.EditorAssetLibrary.does_asset_exist(path):
        return unreal.EditorAssetLibrary.load_asset(path)

    tools = unreal.AssetToolsHelpers.get_asset_tools()
    material = tools.create_asset(name, MATERIAL_DIR, unreal.Material,
                                  unreal.MaterialFactoryNew())
    lib = unreal.MaterialEditingLibrary

    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_ADDITIVE)
    material.set_editor_property("shading_model",
                                 unreal.MaterialShadingModel.MSM_UNLIT)
    # Two-sided so the beam still draws when the camera is inside it -- which is
    # most of the time in a room where four heads aim at the middle.
    material.set_editor_property("two_sided", True)

    color = lib.create_material_expression(
        material, unreal.MaterialExpressionVectorParameter, -700, -100)
    color.set_editor_property("parameter_name", "Color")
    color.set_editor_property("default_value", unreal.LinearColor(1, 1, 1, 1))

    brightness = lib.create_material_expression(
        material, unreal.MaterialExpressionScalarParameter, -700, 100)
    brightness.set_editor_property("parameter_name", "Brightness")
    brightness.set_editor_property("default_value", 1.0)

    # Where this shaft starts, and how far the pixel being shaded is from it.
    # Both the taper and the split want that, so it is built once: `Origin` is
    # the lens for a head's shaft and the ball for the mirror-ball spray.
    origin = xyz = here = offset_xyz = None
    if taper or split:
        origin = lib.create_material_expression(
            material, unreal.MaterialExpressionVectorParameter, -1900, 500)
        origin.set_editor_property("parameter_name", "Origin")
        origin.set_editor_property("default_value", unreal.LinearColor(0, 0, 0, 0))
        # A VectorParameter is a float4 and everything downstream wants a
        # float3, so the alpha has to be masked off rather than left to
        # broadcast.
        xyz = lib.create_material_expression(
            material, unreal.MaterialExpressionComponentMask, -1700, 500)
        for channel, on in (("r", True), ("g", True), ("b", True), ("a", False)):
            xyz.set_editor_property(channel, on)
        lib.connect_material_expressions(origin, "", xyz, "")

        here = lib.create_material_expression(
            material, unreal.MaterialExpressionWorldPosition, -1900, 650)
        offset_xyz = lib.create_material_expression(
            material, unreal.MaterialExpressionSubtract, -1500, 600)
        lib.connect_material_expressions(here, "", offset_xyz, "A")
        lib.connect_material_expressions(xyz, "", offset_xyz, "B")

    shade = color
    if split:
        # A colour wheel's in-between positions put half of one segment and half
        # of the next in front of the lens, so the beam leaves TWO-TONED, split
        # down its middle -- not blended. Seven of the MingJie wheel's fourteen
        # slots are these, and previz drew every one of them as a single muddy
        # average, which is the whole reason this exists.
        #
        # Split by a world-space PLANE through the beam's own axis rather than by
        # the mesh's local UVs, for the same reason the taper is measured from
        # `Origin`: the shaft is a stretched cone whose local frame twists with
        # its aim, so a local split would roll as the head moved. The driver
        # hands over the plane's normal -- perpendicular to the beam and lying in
        # the vertical plane -- so "top half" stays the top half wherever the
        # head points.
        color_b = lib.create_material_expression(
            material, unreal.MaterialExpressionVectorParameter, -700, -250)
        color_b.set_editor_property("parameter_name", "ColorB")
        color_b.set_editor_property("default_value", unreal.LinearColor(1, 1, 1, 1))

        normal = lib.create_material_expression(
            material, unreal.MaterialExpressionVectorParameter, -1500, -450)
        normal.set_editor_property("parameter_name", "SplitNormal")
        # Zero by default: the dot product is then 0 everywhere, the alpha sits
        # at 0.5, and a fixture that is not split reads ColorB == Color anyway.
        normal.set_editor_property("default_value", unreal.LinearColor(0, 0, 0, 0))
        normal_xyz = lib.create_material_expression(
            material, unreal.MaterialExpressionComponentMask, -1300, -450)
        for channel, on in (("r", True), ("g", True), ("b", True), ("a", False)):
            normal_xyz.set_editor_property(channel, on)
        lib.connect_material_expressions(normal, "", normal_xyz, "")

        side = lib.create_material_expression(
            material, unreal.MaterialExpressionDotProduct, -1100, -350)
        lib.connect_material_expressions(offset_xyz, "", side, "A")
        lib.connect_material_expressions(normal_xyz, "", side, "B")

        # Scale before biasing to 0.5, so the transition spans a couple of
        # centimetres and antialiases instead of stair-stepping. Sharp on
        # purpose: this is a hard mechanical edge in the aperture, not a fade.
        sharp = lib.create_material_expression(
            material, unreal.MaterialExpressionMultiply, -900, -300)
        lib.connect_material_expressions(side, "", sharp, "A")
        gain = lib.create_material_expression(
            material, unreal.MaterialExpressionConstant, -1100, -200)
        gain.set_editor_property("r", SPLIT_SHARPNESS)
        lib.connect_material_expressions(gain, "", sharp, "B")

        biased = lib.create_material_expression(
            material, unreal.MaterialExpressionAdd, -750, -300)
        lib.connect_material_expressions(sharp, "", biased, "A")
        half = lib.create_material_expression(
            material, unreal.MaterialExpressionConstant, -900, -180)
        half.set_editor_property("r", 0.5)
        lib.connect_material_expressions(half, "", biased, "B")

        alpha = lib.create_material_expression(
            material, unreal.MaterialExpressionSaturate, -600, -300)
        lib.connect_material_expressions(biased, "", alpha, "")

        mixed = lib.create_material_expression(
            material, unreal.MaterialExpressionLinearInterpolate, -450, -150)
        lib.connect_material_expressions(color, "", mixed, "A")
        lib.connect_material_expressions(color_b, "", mixed, "B")
        lib.connect_material_expressions(alpha, "", mixed, "Alpha")
        shade = mixed

    tinted = lib.create_material_expression(
        material, unreal.MaterialExpressionMultiply, -300, 0)
    lib.connect_material_expressions(shade, "", tinted, "A")
    lib.connect_material_expressions(brightness, "", tinted, "B")
    output = tinted

    if round_off:
        fresnel = lib.create_material_expression(
            material, unreal.MaterialExpressionFresnel, -700, 250)
        fresnel.set_editor_property("exponent", 1.6)
        inverted = lib.create_material_expression(
            material, unreal.MaterialExpressionOneMinus, -450, 250)
        lib.connect_material_expressions(fresnel, "", inverted, "")

        shaped = lib.create_material_expression(
            material, unreal.MaterialExpressionMultiply, -150, 100)
        lib.connect_material_expressions(tinted, "", shaped, "A")
        lib.connect_material_expressions(inverted, "", shaped, "B")
        output = shaped

    if taper:
        travelled = lib.create_material_expression(
            material, unreal.MaterialExpressionDistance, -700, 550)
        lib.connect_material_expressions(here, "", travelled, "A")
        lib.connect_material_expressions(xyz, "", travelled, "B")

        reach = lib.create_material_expression(
            material, unreal.MaterialExpressionScalarParameter, -700, 750)
        reach.set_editor_property("parameter_name", "Reach")
        # Never zero: this is a divisor, and a beam that has not been aimed yet
        # would otherwise render as NaN rather than as nothing.
        reach.set_editor_property("default_value", 1000.0)

        fraction = lib.create_material_expression(
            material, unreal.MaterialExpressionDivide, -500, 600)
        lib.connect_material_expressions(travelled, "", fraction, "A")
        lib.connect_material_expressions(reach, "", fraction, "B")
        clamped = lib.create_material_expression(
            material, unreal.MaterialExpressionSaturate, -350, 600)
        lib.connect_material_expressions(fraction, "", clamped, "")

        # Falloff ^ (travelled / Reach), not a lerp between 1 and Falloff.
        #
        # The driver passes `Falloff = exp(-k * Reach)`, so this expands to
        # exp(-k * travelled) exactly -- real extinction, at any Reach, with no
        # per-length tuning. A lerp is the same thing only near the origin, and
        # the error grows with Reach: at the 16 m diagonal this started life on
        # it was close enough to miss, and at the 27 m diagonal of the enlarged
        # room a shaft 5 m out came back at 0.83 instead of 0.64. Multiply that
        # by the several hundred additive shafts a mirror ball throws and the
        # room washes out to an even blue -- which is exactly what happened the
        # first time it was rendered at the new size.
        far = lib.create_material_expression(
            material, unreal.MaterialExpressionScalarParameter, -500, 480)
        far.set_editor_property("parameter_name", "Falloff")
        far.set_editor_property("default_value", 1.0)

        remaining = lib.create_material_expression(
            material, unreal.MaterialExpressionPower, -200, 480)
        lib.connect_material_expressions(far, "", remaining, "Base")
        lib.connect_material_expressions(clamped, "", remaining, "Exponent")

        faded = lib.create_material_expression(
            material, unreal.MaterialExpressionMultiply, 0, 200)
        lib.connect_material_expressions(output, "", faded, "A")
        lib.connect_material_expressions(remaining, "", faded, "B")
        output = faded

    if soft_edge:
        output = _soft_edge(material, lib, output, soft_edge)

    lib.connect_material_property(output, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    lib.recompile_material(material)
    _save_and_verify(path)
    return material


def make_beam_material():
    return make_emissive_material(BEAM_MATERIAL, round_off=True, taper=True,
                                 split=True)


def make_dot_material():
    # No taper: a dot has no length to lose light over. It is already dimmed by
    # its distance from the ball, because `Live._place_reflections` sizes it
    # from the real throw.
    #
    # `soft_edge` instead of `round_off`. Fresnel is the wrong tool here -- it
    # keys off the angle to the camera, so it dims the floor and ceiling dots
    # you most want to see. This keys off the mesh's own UVs, so a dot fades
    # from its centre outward wherever it happens to be lying. See DOT_SHOULDER.
    return make_emissive_material(DOT_MATERIAL, round_off=False,
                                  soft_edge=DOT_SHOULDER)


def spawn_shape(shape, label, location, scale, material=None, rotation=None):
    actor = actors.spawn_actor_from_class(
        unreal.StaticMeshActor, vec(location), rotation or unreal.Rotator(0, 0, 0))
    actor.set_actor_label(label)
    actor.tags = [TAG]
    component = actor.static_mesh_component
    component.set_mobility(unreal.ComponentMobility.MOVABLE)
    component.set_static_mesh(unreal.EditorAssetLibrary.load_asset(SHAPES[shape]))
    actor.set_actor_scale3d(vec(scale))
    if material is not None:
        component.set_material(0, material)
    return actor


def add_component(actor, component_class, where):
    """Add a component to a level actor instance.

    `unreal.Actor` has no `add_component_by_class` in 5.8, so this goes through
    the subobject editor -- the same path the Details panel's "Add Component"
    button uses.
    """
    handles = subobjects.k2_gather_subobject_data_for_instance(actor)
    params = unreal.AddNewSubobjectParams(
        parent_handle=handles[0], new_class=component_class,
        blueprint_context=None)
    handle, failure = subobjects.add_new_subobject(params)
    # `failure` is an FText, and an empty one is still a live object -- so
    # `if failure` is true on success. Only its text says anything.
    if str(failure):
        raise RuntimeError(f"{where}: {failure}")
    return unreal.SubobjectDataBlueprintFunctionLibrary.get_object(
        subobjects.k2_find_subobject_data_from_handle(handle))


def spawn_instances(label, tags, parts):
    """One actor carrying instanced-static-mesh components.

    `parts` is [(component tag, shape, material, [transforms])]. Everything the
    ball throws is drawn this way, and the reason is per-frame cost: a head
    lights a couple of hundred facets, each wanting a dot AND the shaft that
    reaches it, and as individual actors that is a thousand-odd Python->engine
    transform writes every tick. As instances it is one batched call per
    component, which is what makes the facet count a dial you can turn rather
    than a budget you are already over.
    """
    actor = actors.spawn_actor_from_class(
        unreal.Actor, unreal.Vector(0.0, 0.0, 0.0), unreal.Rotator(0, 0, 0))
    actor.set_actor_label(label)
    actor.tags = tags

    for component_tag, shape, material, transforms in parts:
        component = add_component(actor, unreal.InstancedStaticMeshComponent,
                                  f"{label}/{component_tag}")

        # Found by tag, not by name or by position in the component list --
        # same reasoning as the actor tags: a rename in the outliner must not
        # be able to silently unhook the driver.
        component.set_editor_property("component_tags", [component_tag])
        component.set_mobility(unreal.ComponentMobility.MOVABLE)
        component.set_static_mesh(unreal.EditorAssetLibrary.load_asset(SHAPES[shape]))
        component.set_material(0, material)
        component.set_editor_property("cast_shadow", False)
        # Beams are line-traced to where they land, and a dot or a reflected
        # shaft is light rather than an object: leave collision on and every
        # beam terminates on the first sliver of its own reflection.
        component.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        for transform in transforms:
            component.add_instance(transform, False)

    return actor


def apply_render_cvars():
    """Push the volumetric settings live, as well as into DefaultEngine.ini.

    The ini is what a fresh editor starts with; these are what the *running*
    editor uses, so a tuning change can be judged without a restart. Kept in
    both places deliberately -- console-only would be lost on restart, ini-only
    would make every experiment a five-minute round trip.
    """
    world = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem).get_editor_world()
    for command in (
            # The froxel grid is screen-space tiles by DEPTH SLICE, and the
            # beading that makes a beam look like a string of beads is the
            # depth axis, not the screen axis. Measured on a single-beam shot:
            # (pixel 2, Z 128) beads badly; (2, 512) is clean; (4, 512) is just
            # as clean and costs the same as (2, 128) did. So at any given
            # budget, spend it on Z. Drop the pixel size to 2 as well if the
            # GPU has room -- it is a small extra gain for 4x the fill rate.
            # There is no upper clamp and no budget fallback in the renderer
            # (GetVolumetricFogGridPixelSize is just Max(1, cvar)), so both are
            # honoured as written.
            "r.VolumetricFog.GridPixelSize 4",
            "r.VolumetricFog.GridSizeZ 512",
            "r.VolumetricFog.HistoryMissSupersampleCount 16",
            # LEAVE THIS AT THE DEFAULT. It is tempting to lower it to spread
            # the depth slices evenly over a small room, but the slice
            # distribution degenerates well before that helps: at 1 the far
            # half of the room renders as a solid black rectangle, and at 8 the
            # beam is measurably beadier than at 32. Measured, not assumed --
            # the sweep is in the README.
            "r.VolumetricFog.DepthDistributionScale 32"):
        unreal.SystemLibrary.execute_console_command(world, command)


def clear_previous():
    """Delete only what a previous run of this script placed.

    The materials go too. They are generated from the constants in this file, so
    keeping them would mean a tuning change here silently did nothing -- the
    `does_asset_exist` early-out in `make_material` would hand back the old one
    and the next render would look identical for no visible reason.
    """
    removed = 0
    for actor in actors.get_all_level_actors():
        if TAG in [str(t) for t in actor.tags]:
            actors.destroy_actor(actor)
            removed += 1
    if unreal.EditorAssetLibrary.does_directory_exist(MATERIAL_DIR):
        unreal.EditorAssetLibrary.delete_directory(MATERIAL_DIR)
    return removed


# ------------------------------------------------------------------- build ---

def build_room(spec):
    """Floor, ceiling and four walls as thin boxes, forming a closed dark box.

    Closed on purpose: an open box leaks the skylight in and lifts the black
    floor, and the beams stop reading against the walls.
    """
    # Dark, but not black, and faintly self-lit. The emissive is doing real work:
    # a SkyLight inside a closed box captures its own black walls and therefore
    # contributes nothing no matter how high its intensity goes, so with only
    # ambient the room rendered as pure void and there was no way to tell an
    # unlit wall from no wall at all. A little emissive is a floor that cannot
    # be captured away.
    # 2.5x the base colour, so the walls sit around 0.12 emissive. Auto-exposure
    # is off (a previz whose exposure drifts cannot be compared shot to shot),
    # so this is an absolute brightness, not something the eye adapts to -- at
    # 0.02 it was indistinguishable from black.
    dark = make_material("M_PrevizRoom",
                         (ROOM_ALBEDO, ROOM_ALBEDO, ROOM_ALBEDO * 1.15, 1.0),
                         roughness=0.85, emissive=ROOM_EMISSIVE)
    room = spec["room"]
    w, d, h = room["width"], room["depth"], room["height"]
    cx, cy = w / 2.0, d / 2.0                       # UE X spans depth, Y spans width
    thickness = 10.0
    t = thickness / SHAPE_SIZE

    spawn_shape("cube", "PZ_Floor", (cx, cy, -thickness / 2),
                (d / SHAPE_SIZE, w / SHAPE_SIZE, t), dark)
    spawn_shape("cube", "PZ_Ceiling", (cx, cy, h + thickness / 2),
                (d / SHAPE_SIZE, w / SHAPE_SIZE, t), dark)

    walls = [
        ("PZ_Wall_Front", (-thickness / 2, cy, h / 2), (t, w / SHAPE_SIZE, h / SHAPE_SIZE)),
        ("PZ_Wall_Back", (d + thickness / 2, cy, h / 2), (t, w / SHAPE_SIZE, h / SHAPE_SIZE)),
        ("PZ_Wall_Left", (cx, -thickness / 2, h / 2), (d / SHAPE_SIZE, t, h / SHAPE_SIZE)),
        ("PZ_Wall_Right", (cx, w + thickness / 2, h / 2), (d / SHAPE_SIZE, t, h / SHAPE_SIZE)),
    ]
    for label, location, scale in walls:
        spawn_shape("cube", label, location, scale, dark)


def build_ball(spec):
    """The mirror ball: a solid dark core, tiled with individual mirrors.

    The core is the occluding sphere the safety taper models, and it is a real
    shadow caster so the previz agrees with the taper about which beams the ball
    blocks -- that occlusion is most of why the ball poses were safe on a rig
    with no safety system, so a previz that let beams pass straight through
    would disagree with the engine about the show's signature look.

    The tiles are what make it a mirror ball rather than a chrome marble. A
    smooth sphere reads as a ball bearing and, worse, carries exactly one
    specular highlight per beam instead of the spray of them the look is made of.

    Drawn on `mirrorball.facet_normals()`, a UV lattice, because that is what
    tiles a sphere mesh without gaps. What light REFLECTS off is a different set
    -- `mirrorball.reflect_normals()`, evenly spread on a golden spiral, at a
    quarter the density. Two jobs, and a UV lattice is only right for one of
    them: its facets bunch at the poles, which a wide source lighting the whole
    near cap turns into visible rings. See `fibonacci_normals`.
    """
    ball = spec["ball"]
    radius = ball["radius"]                          # UE cm
    centre = ball["location"]

    # Near-black, because it is grout: the gaps between mirrors on a real ball
    # are dark, and they are most of what makes the tiling visible at all.
    core = make_material("M_PrevizBallCore", (0.02, 0.02, 0.025, 1.0),
                         roughness=0.9)
    actor = spawn_shape("sphere", "PZ_MirrorBall", centre,
                        (2 * radius / SHAPE_SIZE,) * 3, core)
    actor.static_mesh_component.set_editor_property("cast_shadow", True)
    # Tagged so the driver can take the ball OUT of a beam's trace when the
    # ball only clips the edge of that beam -- see Live._ball_blocks_all.
    actor.tags = [TAG, "mirror_ball"]

    # Fully metallic, so there is no diffuse term. That is not pedantry: give
    # these tiles any diffuse response and two 1300-lumen beams at five metres
    # render the whole lit hemisphere as a solid white blob, which is the
    # opposite of a mirror ball. A mirror is dark except where it happens to be
    # aimed at you, and the few tiles that are, are blinding.
    #
    # The emissive is doing the same job as on the walls: a mirror in a closed
    # black box reflects black, so without a floor that cannot be captured away
    # the ball would vanish entirely whenever no beam was on it.
    mirror = make_material("M_PrevizBallTile", (0.9, 0.92, 0.97, 1.0),
                           roughness=0.18, metallic=1.0, emissive=0.14)

    # The lattice is worked out from the radius in mm, since it is specified as
    # a facet size in mm; the tiles themselves are then built in Unreal cm.
    tiles = mb.facet_tiles(radius, *mb.mirror_lattice(ball["radius_mm"]))
    seat = radius + TILE_LIFT                        # sit ON the core, not in it

    transforms = []
    for tile in tiles:
        # A normal and a tangent are directions in the show's frame, so they
        # take the axis permutation but not the mm->cm scale (previz/scene.py).
        n, e = tile.normal, tile.east
        out = unreal.Vector(n[2], n[0], n[1])
        east = unreal.Vector(e[2], e[0], e[1])
        transforms.append(unreal.Transform(
            unreal.Vector(centre[0] + out.x * seat,
                          centre[1] + out.y * seat,
                          centre[2] + out.z * seat),
            # Local X along the ring and local Z out of the ball, so the
            # tile's width and height land on the axes they were measured on.
            unreal.MathLibrary.make_rot_from_xz(east, out),
            unreal.Vector(tile.width * TILE_COVERAGE / SHAPE_SIZE,
                          tile.height * TILE_COVERAGE / SHAPE_SIZE, 1.0)))

    spawn_instances("PZ_MirrorBall_Tiles", [TAG],
                    [("tiles", "plane", mirror, transforms)])
    return len(transforms), mb.facet_size(radius, len(tiles))


def build_truss(spec):
    """The frame the rig hangs on: four bars, standing free in the room.

    Collision left ON, unlike everything else the previz draws for reference.
    The crowd-zone marker is an annotation and a beam must pass through it; a
    truss bar is steel, and a beam aimed along the rig line really does stop on
    it. Since the heads sit only 500 mm inboard of the bar at exactly its
    height, that is not a hypothetical -- it is most of what stops a beam aimed
    outward from crossing the whole enlarged room.
    """
    truss = spec.get("truss")
    if not truss:
        return 0
    # Aluminium, and lighter than the walls so the frame reads against them.
    #
    # NOT metallic, though it is metal. A metal surface has no diffuse term --
    # it shows you its surroundings -- and its surroundings here are a black
    # room, so a fully metallic truss rendered as four solid black slabs across
    # the middle of every shot. Same emissive floor the walls get, and for the
    # same reason: something has to be visible that a black box cannot capture
    # away.
    metal = make_material("M_PrevizTruss", (0.30, 0.31, 0.34, 1.0),
                          roughness=0.5, metallic=0.2, emissive=ROOM_EMISSIVE)
    for bar in truss["bars"]:
        e = bar["extent"]
        actor = spawn_shape("cube", f"PZ_Truss_{bar['label']}", bar["center"],
                            (2 * e[0] / SHAPE_SIZE, 2 * e[1] / SHAPE_SIZE,
                             2 * e[2] / SHAPE_SIZE), metal)
        # A thin bright bar crossing a beam throws a hard shadow stripe across
        # the far wall for no benefit -- it is scenery, not an occluder worth
        # four more shadow maps a frame. It still BLOCKS beams, via collision.
        actor.static_mesh_component.set_editor_property("cast_shadow", False)
    return len(truss["bars"])


def build_canopy(spec):
    """The parachute: both a throw limit and a target that glows when hit."""
    canopy = spec.get("canopy")
    if not canopy:
        return
    fabric = make_material("M_PrevizCanopy", (0.75, 0.72, 0.7, 1.0), roughness=0.95)
    radius = canopy["radius"]
    spawn_shape("cylinder", "PZ_Canopy", canopy["location"],
                (2 * radius / SHAPE_SIZE, 2 * radius / SHAPE_SIZE, 0.05), fabric)


def build_crowd_zone(spec):
    """A wireframe marker for the head band the safety taper defends.

    A TriggerBox draws its bounds in the editor and renders nothing at all in
    game, which is exactly right: it is an annotation for whoever is judging
    the look, not part of the picture.
    """
    crowd = spec.get("crowd_zone")
    if not crowd:
        return
    actor = actors.spawn_actor_from_class(
        unreal.TriggerBox, vec(crowd["center"]), unreal.Rotator(0, 0, 0))
    actor.set_actor_label("PZ_CrowdHeadBand")
    actor.tags = [TAG]
    actor.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    actor.collision_component.set_editor_property("box_extent", vec(crowd["extent"]))


def build_exposure(spec):
    """Pin exposure and bloom for the whole level.

    A dark room is exactly what auto-exposure exists to destroy: point the
    camera at black walls and it winds the gain up until they are grey, and the
    show's contrast -- which is the whole point -- disappears. `DefaultEngine.ini`
    already asks for fixed exposure, but that is a project default; a volume in
    the level is what the viewport, Play-in-Editor and `snapshot.py` all
    actually agree on, which is what makes a still comparable with what you are
    looking at.

    Pinning min and max brightness to the same number is the idiom for "do not
    adapt": the histogram still runs, it simply has nowhere to go.

    Note this only wins if the level viewport's own exposure override is off --
    that is a per-viewport editor setting, not a level one, so the README says
    where the toggle is rather than pretending this can reach it.

    A PostProcessComponent rather than a PostProcessVolume: a volume is a brush
    actor and `spawn_actor_from_class` hands back None for it, because a brush
    with no builder is not a thing. The component does the same job.
    """
    room = spec["room"]
    holder = actors.spawn_actor_from_class(
        unreal.Actor,
        vec((room["depth"] / 2, room["width"] / 2, room["height"] / 2)),
        unreal.Rotator(0, 0, 0))
    holder.set_actor_label("PZ_Look")
    holder.tags = [TAG]
    volume = add_component(holder, unreal.PostProcessComponent, "PZ_Look")
    # Unbound, so it applies wherever the camera is -- including the snapshot
    # cameras, which stand outside the room and would otherwise get a different
    # picture from the one you are judging in the viewport.
    volume.set_editor_property("unbound", True)
    volume.set_editor_property("priority", 1.0)

    settings = volume.settings
    for name, value in (
            ("auto_exposure_method", unreal.AutoExposureMethod.AEM_HISTOGRAM),
            ("auto_exposure_min_brightness", EXPOSURE),
            ("auto_exposure_max_brightness", EXPOSURE),
            ("auto_exposure_bias", 0.0),
            # Bloom is not decoration here: it is what makes a beam whose core
            # is blown out still read as a beam rather than a white stripe.
            ("bloom_intensity", 0.6),
            ("bloom_threshold", 0.4)):
        settings.set_editor_property(name, value)
        settings.set_editor_property(f"override_{name}", True)
    volume.set_editor_property("settings", settings)


def build_atmosphere(spec):
    """Haze and a floor of ambient light.

    Volumetric fog is the entire reason a beam is visible in flight. The
    density is tuned for "you can see the shaft" rather than for realism -- a
    real room this hazy would be a fire alarm.
    """
    room = spec["room"]
    fog = actors.spawn_actor_from_class(
        unreal.ExponentialHeightFog,
        vec((room["depth"] / 2, room["width"] / 2, 0.0)), unreal.Rotator(0, 0, 0))
    fog.set_actor_label("PZ_Haze")
    fog.tags = [TAG]
    component = fog.component
    component.set_editor_property("fog_density", FOG_DENSITY)
    # Near-zero falloff so the haze is even from floor to ceiling. The default
    # is tuned for outdoor scenes and leaves the top of the room clear, which
    # would make every aerial pose look dimmer than it is.
    component.set_editor_property("fog_height_falloff", 0.005)
    component.set_editor_property("enable_volumetric_fog", True)
    component.set_editor_property("volumetric_fog_extinction_scale", 1.0)
    # Just past the room's far corner, NOT the 6000 cm default. Depth slices are
    # distributed across this distance, so quoting 60 m for a 9 m room spends
    # nine tenths of them outside the venue. Derived from the room rather than
    # typed, because it was typed once (1600 cm, for a 9 m room) and the room
    # then doubled -- at which point the far half of it had no fog in it at all
    # and beams simply stopped being visible partway across.
    component.set_editor_property("volumetric_fog_distance",
                                  spec["max_throw"] * 1.1)
    # Slices run from max(camera near clip, this) to the distance above --
    # VolumetricFog.cpp:1369. Raising it packs every slice into the part of the
    # room you are looking at and visibly smooths the beams, but it is measured
    # FROM THE CAMERA, so a value that flatters a camera outside the room
    # deletes the fog from everything within that range of one standing inside
    # it. Zero is right for the in-room views that matter; `snapshot.py` raises
    # it per view for the cutaway cameras that stand well back.
    component.set_editor_property("volumetric_fog_start_distance", 0.0)
    component.set_editor_property("volumetric_fog_scattering_distribution", 0.4)
    component.set_editor_property("volumetric_fog_albedo", unreal.Color(255, 255, 255, 255))

    build_exposure(spec)

    sky = actors.spawn_actor_from_class(
        unreal.SkyLight, vec((room["depth"] / 2, room["width"] / 2, room["height"] / 2)),
        unreal.Rotator(0, 0, 0))
    sky.set_actor_label("PZ_Ambient")
    sky.tags = [TAG]
    sky.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
    # Just enough to read the room's shape with every fixture dark. Any more and
    # the black the show is built against stops being black.
    sky.light_component.set_editor_property("intensity", 0.15)
    # The skylight must not add haze of its own, or the room fills with an even
    # blue glow and the beams stop being the brightest thing in the picture.
    sky.light_component.set_editor_property("volumetric_scattering_intensity", 0.0)


def _configure_spot(spot, fixture, spec):
    """Every property one previz spot light needs.

    Factored out because a fixture with a split colour wheel gets TWO of
    them -- see the note in `build_fixtures` -- and two lights configured
    from two copies of this list is two lights that quietly diverge.
    """
    half_angle = max(0.5, fixture["beam_deg"] / 2.0)
    spot.set_editor_property("outer_cone_angle", half_angle)
    # A hard-edged beam: these are cheap fixed-lens beams with no frost, and
    # a soft edge would flatter them into looking like proper profiles.
    spot.set_editor_property("inner_cone_angle", half_angle * 0.85)
    spot.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
    spot.set_editor_property("intensity", fixture["lumens"])   # .qxf Bulb
    spot.set_editor_property("attenuation_radius", spec["max_throw"] * 1.2)
    spot.set_editor_property("source_radius", 2.0)
    # The atmosphere the beam hangs in. With the froxel grid at 2 px and the
    # slice range confined to the room, the fog resolves the beam well
    # enough to be worth having again -- it was only useless at the stock
    # grid. Kept below the beam mesh's own brightness so the mesh stays the
    # crisp core and this reads as the glow around it: the mesh alone looks
    # like a decal floating in a vacuum, and the fog alone is too soft to
    # judge where a 3-degree beam is actually pointing.
    spot.set_editor_property("volumetric_scattering_intensity", 2.5)
    spot.set_editor_property("cast_shadows", True)
    # Without this the beam MESH stops dead on the mirror ball and the fog
    # around it sails straight through, so the beam reads as passing
    # through the ball -- which is exactly the occlusion the safety taper
    # is counting on. It is off by default on a spawned SpotLight, and it
    # is not implied by cast_shadows.
    spot.set_editor_property("cast_volumetric_shadow", True)
    # And without THIS the ball does not shadow the beam onto surfaces at
    # all, however the two flags above are set. See SHADOW_RESOLUTION_SCALE.
    spot.set_editor_property("shadow_resolution_scale", SHADOW_RESOLUTION_SCALE)
    # Dark until the engine says otherwise. A previz that starts bright
    # cannot be told apart from one that is not receiving DMX at all.
    spot.set_editor_property("visible", False)


def _has_split_slot(fixture):
    """Does this fixture's colour wheel have any two-colour position?

    `previz.scene` emits each slot as [lo, hi, r,g,b, r1,g1,b1, r2,g2,b2] -- the
    averaged colour, then the two halves. They differ only on a split.
    """
    for slot in fixture.get("color_slots") or ():
        if len(slot) >= 11 and slot[5:8] != slot[8:11]:
            return True
    return False


def build_fixtures(spec):
    """One spot light per placed fixture, at its calibrated rest aim.

    A freshly built level therefore shows every head pointing at the mirror
    ball before a single DMX packet arrives -- the cheapest possible check that
    the calibration in `calibration.json` is not nonsense. If a beam does not
    land on the ball here, the show will not either.
    """
    body = make_material("M_PrevizFixture", (0.05, 0.05, 0.05, 1.0), roughness=0.4)
    split_lights = 0
    beam_material = make_beam_material()
    dot_material = make_dot_material()
    placed = static = 0

    # How many reflections one head can show at once. Only facets turned toward
    # the beam can throw one (`d.n < 0`), and on a UV sphere that is half of
    # them, so half the facet count is a real upper bound rather than a guess
    # needing a "pool ran out" warning behind it. Derived from the same lattice
    # the driver reflects off, so the two cannot disagree.
    facets = math.prod(mb.reflect_lattice(spec["ball"]["radius_mm"]))
    dots_per_head = facets // 2 + 2

    for fixture in spec["fixtures"]:
        if not fixture["location"]:
            continue
        # How the live driver finds this fixture's actors. A mover is keyed by
        # its head index and anything else by its fixture id, and the h/f prefix
        # is what keeps those two numbering schemes apart -- head 0 and fid 0 are
        # different objects. One key for all four roles, so there is exactly one
        # place a fixture's actors can fail to line up.
        steerable = fixture["head"] is not None
        key = f"h{fixture['head']}" if steerable else f"f{fixture['fid']}"

        label = f"PZ_Fixture_{fixture['fid']}"
        rotation = rot(fixture["rest_rotation"] or [0.0, 0.0, 0.0])

        light = actors.spawn_actor_from_class(
            unreal.SpotLight, vec(fixture["location"]), rotation)
        light.set_actor_label(label)
        # The head index is how the live driver finds this actor and decodes its
        # pan/tilt with the right calibration, so it travels as a tag.
        light.tags = [TAG, f"unit:{key}", f"fid:{fixture['fid']}"]
        light.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)

        _configure_spot(light.spot_light_component, fixture, spec)

        # What the ball does with this head's beam, as actual light. A mirror
        # ball takes a beam and sprays it over the whole room, and that spray is
        # most of why a room with a ball in it glows rather than being a black
        # box with bright tubes in it. The reflection dots and shafts are
        # additive meshes and emit nothing, so without this the one pose the
        # show is built around -- every head on the ball -- lights nothing at
        # all: four beams terminate on a 60 cm sphere and the room stays black.
        #
        # Driven per frame from how much of the beam the ball actually
        # intercepts, so it is dark when the head is pointed elsewhere.
        glow = actors.spawn_actor_from_class(
            unreal.PointLight, vec(spec["ball"]["location"]),
            unreal.Rotator(0, 0, 0))
        glow.set_actor_label(f"{label}_BallGlow")
        glow.tags = [TAG, f"glow:{key}"]
        glow.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
        point = glow.point_light_component
        point.set_editor_property("intensity_units", unreal.LightUnits.LUMENS)
        point.set_editor_property("intensity", 0.0)
        point.set_editor_property("attenuation_radius", spec["max_throw"])
        # Roughly the ball's own size, so the light does not read as a point.
        point.set_editor_property("source_radius", spec["ball"]["radius"])
        # No shadows: this stands in for light already scattered off a few
        # hundred mirrors in every direction, and shadowing that would be both
        # wrong and four more shadow maps a frame.
        point.set_editor_property("cast_shadows", False)
        # The haze is the point. This is what puts a glow in the ROOM rather
        # than just a wash on the walls. Swept against a photo of the real
        # night: much above 1 and the room flattens into an even blue field
        # with no corners left, which is the failure mode that looks like
        # cheating; much below and it is only a wash on the walls again.
        point.set_editor_property("volumetric_scattering_intensity",
                                  BALL_GLOW_SCATTER)
        point.set_editor_property("visible", False)

        # A SECOND spot light, for fixtures whose colour wheel has split slots.
        #
        # A wheel position between two segments throws half the aperture in one
        # colour and half in the next, and an Unreal spot light has exactly one
        # colour -- so the shaft mesh could be split all it liked while the
        # volumetric fog around it, which is what actually makes a beam read as
        # a beam, stayed a single average. That is what "duo colours are not
        # working" looked like.
        #
        # Two lights at half intensity, tipped a quarter of the cone apart in
        # elevation, is the model: each carries one half of the aperture, they
        # overlap down the middle the way the real halves do, and the fog picks
        # up both colours. Only built where the profile actually has split
        # slots, because a shadow-casting spot light is not free.
        if _has_split_slot(fixture):
            second = actors.spawn_actor_from_class(
                unreal.SpotLight, vec(fixture["location"]), rotation)
            second.set_actor_label(f"{label}_Half2")
            second.tags = [TAG, f"unit2:{key}"]
            second.root_component.set_mobility(unreal.ComponentMobility.MOVABLE)
            _configure_spot(second.spot_light_component, fixture, spec)
            split_lights += 1

        body_actor = spawn_shape("cube", f"{label}_Body", fixture["location"],
                                 (0.18, 0.2, 0.28), body)
        # Tagged so the live driver's beam trace can ignore it. The trace starts
        # at the lens, which is inside this cube, so without the tag every beam
        # would report a throw of a few centimetres and vanish.
        body_actor.tags = [TAG, "fixture_body"]

        # The shaft. Placed at the head for now; the live driver stretches and
        # aims it every frame, so its transform here only has to be valid.
        beam = spawn_shape("cone", f"{label}_Beam", fixture["location"],
                           (0.01, 0.01, 0.01), beam_material)
        beam.tags = [TAG, f"beam:{key}"]
        mesh = beam.static_mesh_component
        # A beam is light, not an object: it must not cast or receive shadows,
        # or four crossing shafts start shadowing each other into a mess.
        mesh.set_editor_property("cast_shadow", False)
        mesh.set_editor_property("receives_decals", False)
        mesh.set_collision_enabled(unreal.CollisionEnabled.NO_COLLISION)
        mesh.set_visibility(False, False)

        # The mirror ball's reflections off this head, as two instance pools:
        # the shafts leaving the ball and the dots they land on. The beam does
        # not teleport across the room -- it strikes the ball and bursts into
        # dozens of thin beams, and drawing only the far end of each one leaves
        # dots hanging on the walls with nothing to explain them.
        #
        # Every placed fixture gets these, movers and pinspots alike -- a pinspot
        # aimed at a mirror ball is the most literal example of the effect there
        # is. The pool is sized to half the facet count, which is a real upper
        # bound rather than a guess: only facets turned toward the beam can throw
        # a reflection, and on a UV sphere that is half of them. It has to be
        # that generous for the pinspots specifically, because an 11-degree cone
        # at 4 m OVERSHOOTS a 60 cm ball and lights the whole near cap -- roughly
        # 440 facets, against a 3-degree head's 35.
        #
        # Instances start at zero scale rather than hidden: an instanced mesh
        # has no per-instance visibility, and scaling to nothing is both the
        # idiom and one number cheaper per frame than a separate flag.
        parked = unreal.Transform(unreal.Vector(0.0, 0.0, 0.0),
                                  unreal.Rotator(0, 0, 0),
                                  unreal.Vector(0.0, 0.0, 0.0))
        pool = [parked] * dots_per_head
        spawn_instances(
            f"{label}_Reflections", [TAG, f"reflect:{key}"],
            [("rays", "cone", beam_material, pool),
             ("dots", "plane", dot_material, pool)])

        placed += 1
        static += 0 if steerable else 1

    return placed, static, split_lights


# -------------------------------------------------------------------- main ---

def stop_live():
    """Pause the live driver, if one is running, before the actors go away.

    Otherwise the driver keeps ticking through the rebuild and spends a few
    frames writing rotations to actors that have just been destroyed. It
    survives that -- the tick catches and counts -- but the errors are noise
    that looks exactly like a real fault the next time someone reads the log.
    """
    if "cosmos_live" not in sys.modules:
        return False
    sys.modules["cosmos_live"].stop()
    return True


def refuse_if_playing():
    """Stop before touching anything if Play-in-Editor is running.

    Nothing here works during Play. Not the actor subsystem, not the level
    subsystem, not even the asset registry -- `list_assets('/Game')` comes back
    EMPTY and `does_asset_exist` returns False for a map sitting on disk. The
    editor logs "The Editor is currently in a play mode" and hands Python None,
    so without this the build dies on `'NoneType' has no attribute
    'set_actor_label'` after having already cleared the level, and the honest
    reading of that wreckage is "the rebuild deleted my map" -- which is wrong,
    and sends you looking in exactly the wrong place.
    """
    if unreal.get_editor_subsystem(unreal.LevelEditorSubsystem).is_in_play_in_editor():
        raise RuntimeError(
            "Play-in-Editor is running, and the editor refuses every build "
            "operation while it is. Press Stop in the editor, then re-run. "
            "(You do not need Play at all -- the driver runs on the editor "
            "tick, so the previz is already live in the ordinary viewport.)")


def main(event="despacio", restart_hint=True):
    refuse_if_playing()
    was_live = stop_live()
    event_dir = Path(event)
    if not event_dir.exists():
        event_dir = REPO / "events" / event
    spec = previz_scene.build_scene(event_dir).to_dict()

    if unreal.EditorAssetLibrary.does_asset_exist(LEVEL_PATH):
        levels.load_level(LEVEL_PATH)
        log(f"rebuilding {LEVEL_PATH}: removed {clear_previous()} previz actor(s)")
    else:
        levels.new_level(LEVEL_PATH)
        clear_previous()
        log(f"created {LEVEL_PATH}")

    apply_render_cvars()
    build_atmosphere(spec)
    build_room(spec)
    tiles, tile_cm = build_ball(spec)
    bars = build_truss(spec)
    build_canopy(spec)
    build_crowd_zone(spec)
    placed, static, split_lights = build_fixtures(spec)

    levels.save_current_level()

    room = spec["room"]
    log(f"{spec['event']} in {spec['venue']}: {room['width']:.0f}x{room['depth']:.0f}"
        f"x{room['height']:.0f} cm, {placed} fixture(s) placed "
        f"({placed - static} steerable, {static} fixed), "
        f"mount mode {spec['mount_mode']!r}")
    if split_lights:
        log(f"  {split_lights} fixture(s) have a split colour wheel and carry a "
            f"second spot light for the other half of the aperture")
    if bars:
        truss = spec["truss"]
        log(f"  truss: {bars} bar(s) of {truss['bar']:.0f} cm at "
            f"{truss['height']:.0f} cm -- the rig hangs on this, not on the walls")
    log(f"  mirror ball: {2 * spec['ball']['radius']:.0f} cm across "
        f"(venue.json ball_radius, flagged there as an ESTIMATE), "
        f"{tiles} tiles of {tile_cm:.1f} cm")
    for name in spec["unplaced"]:
        log(f"  NOT PLACED: {name} -- no position in rig.json")
    for warning in spec["warnings"]:
        log(f"  WARN: {warning}")
    if was_live and restart_hint:
        log("  the live driver was stopped for the rebuild -- run live_start.py "
            "again to re-attach it to the new actors")
    return placed


if __name__ == "__main__":
    main()
