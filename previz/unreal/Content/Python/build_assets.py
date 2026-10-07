"""
Create the content the packaged previz loads by path. Runs inside the editor,
headless, as a build step -- the app itself never runs Python:

    python previz/build.py assets
    (= UnrealEditor-Cmd.exe previz/unreal/KLightsPreviz.uproject
         -ExecutePythonScript=<abs path>/build_assets.py -unattended -nosplash)

Three materials and one empty map. Everything else in the room is spawned at
runtime from the engine's scene, so this is the WHOLE of the app's content.
The parameter names below are a contract with `Source/KLightsPreviz` (which sets
them by name); change one side, change both.

Idempotent: an asset that already exists is left alone unless --force is given
(``-ExecutePythonScript="build_assets.py --force"``). The usage flags are
re-asserted every run regardless -- see `instanced_usage`.
"""

import sys

import unreal

MATERIAL_DIR = "/Game/Previz/Materials"
MAP_PATH = "/Game/Maps/KLights"
WHITE = "/Engine/EngineResources/WhiteSquareTexture"

# The shoulder on a reflection dot's radial falloff: (1 - r) ** DOT_SHOULDER
# across its own quad. Below 1 the dot is nearly all soft edge and reads as a
# smudge; above about 3 the core hardens back up. 1.8 keeps a bright core with
# the fade in the outer third.
DOT_SHOULDER = 1.8

# How abruptly a split beam changes color across its width, per Unreal cm. A
# wheel between two segments throws two colors with a hard mechanical edge, so
# this is steep -- just wide enough (a couple of cm) to antialias.
SPLIT_SHARPNESS = 0.4

FORCE = "--force" in sys.argv

lib = unreal.MaterialEditingLibrary
assets = unreal.EditorAssetLibrary


def log(message):
    print(f"[kLights] {message}")


def _fresh(name):
    """A new, empty material at `name`, or None if it exists and --force is off."""
    path = f"{MATERIAL_DIR}/{name}"
    if assets.does_asset_exist(path):
        if not FORCE:
            log(f"{path} exists; leaving it (use --force to rebuild)")
            return None
        assets.delete_asset(path)
    tools = unreal.AssetToolsHelpers.get_asset_tools()
    return tools.create_asset(name, MATERIAL_DIR, unreal.Material, unreal.MaterialFactoryNew())


def _node(material, cls, x, y, **properties):
    node = lib.create_material_expression(material, cls, x, y)
    for key, value in properties.items():
        node.set_editor_property(key, value)
    return node


def _param(material, cls, name, default, x, y):
    node = _node(material, cls, x, y, parameter_name=name)
    if cls is unreal.MaterialExpressionScalarParameter:
        node.set_editor_property("default_value", float(default))
    elif cls is unreal.MaterialExpressionVectorParameter:
        node.set_editor_property("default_value", unreal.LinearColor(*default))
    else:
        node.set_editor_property("texture", unreal.load_asset(default))
    return node


def _connect(a, a_pin, b, b_pin):
    lib.connect_material_expressions(a, a_pin, b, b_pin)


def _binary(material, cls, a, b, x, y, a_pin="", b_pin=""):
    node = _node(material, cls, x, y)
    _connect(a, a_pin, node, "A")
    _connect(b, b_pin, node, "B")
    return node


def _times(material, a, a_pin, b, b_pin, x, y):
    return _binary(material, unreal.MaterialExpressionMultiply, a, b, x, y, a_pin, b_pin)


def _constant(material, value, x, y):
    return _node(material, unreal.MaterialExpressionConstant, x, y, r=float(value))


def _xyz(material, source, x, y):
    """A float4 parameter's RGB: everything downstream wants a float3."""
    mask = _node(material, unreal.MaterialExpressionComponentMask, x, y, r=True, g=True, b=True, a=False)
    _connect(source, "", mask, "")
    return mask


def _save(material, name):
    lib.recompile_material(material)
    path = f"{MATERIAL_DIR}/{name}"
    assets.save_asset(path)
    if not assets.does_asset_exist(path):
        raise RuntimeError(f"{path} was saved but the asset registry does not see it")
    log(f"built {path}")


# ----------------------------------------------------------------- surface --

def model_material():
    """M_PrevizModel: every opaque surface in the app.

    The room, the truss, the canopy, the mirror ball's core and tiles, fixture
    bodies, and every material of every glTF model are all instances of this,
    with their colors set as parameters at runtime -- so a venue's optics
    (`room_albedo`, `room_emissive`) are config, not baked into an asset.

    Parameters: BaseColor, BaseColorMap, Metallic, Roughness, Emissive,
    EmissiveMap. Maps default to white, so a material with no texture is just
    its factor. Two-sided, because venue models are routinely authored as single
    planes and the previz camera stands inside them.
    """
    name = "M_PrevizModel"
    material = _fresh(name)
    if material is None:
        return
    material.set_editor_property("two_sided", True)
    sample = unreal.MaterialExpressionTextureSampleParameter2D
    vector = unreal.MaterialExpressionVectorParameter
    scalar = unreal.MaterialExpressionScalarParameter

    base = _param(material, vector, "BaseColor", (0.8, 0.8, 0.8, 1.0), -900, -100)
    base_map = _param(material, sample, "BaseColorMap", WHITE, -900, 100)
    lib.connect_material_property(_times(material, base, "", base_map, "RGB", -450, 0),
                                  "", unreal.MaterialProperty.MP_BASE_COLOR)

    metallic = _param(material, scalar, "Metallic", 0.0, -450, 250)
    lib.connect_material_property(metallic, "", unreal.MaterialProperty.MP_METALLIC)
    roughness = _param(material, scalar, "Roughness", 0.8, -450, 350)
    lib.connect_material_property(roughness, "", unreal.MaterialProperty.MP_ROUGHNESS)

    emissive = _param(material, vector, "Emissive", (0.0, 0.0, 0.0, 1.0), -900, 450)
    emissive_map = _param(material, sample, "EmissiveMap", WHITE, -900, 650)
    lib.connect_material_property(_times(material, emissive, "", emissive_map, "RGB", -450, 550),
                                  "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    _save(material, name)


# ------------------------------------------------------------------- light --

def _soft_edge(material, output, exponent):
    """Multiply `output` by a radial falloff across the mesh's own UVs.

    Full at the centre, zero at the quad's edge. A reflection dot drawn as a flat
    emissive tile has a knife edge, and nothing in the real path has one -- the
    mirror is small, the lens has an aperture, the air is hazy -- so the dot
    reads as a decal rather than as light on a wall. The falloff costs
    brightness (its mean over the quad is well under 1), which `dot_gain` carries.
    """
    uv = _node(material, unreal.MaterialExpressionTextureCoordinate, -1100, 900)
    centre = _node(material, unreal.MaterialExpressionConstant2Vector, -1100, 1020, r=0.5, g=0.5)
    radius = _binary(material, unreal.MaterialExpressionDistance, uv, centre, -850, 950)
    # UV distance runs 0..0.5 across the half-width; double it to reach 1 at the edge.
    doubled = _times(material, radius, "", _constant(material, 2.0, -850, 1080), "", -650, 950)
    inside = _node(material, unreal.MaterialExpressionOneMinus, -480, 950)
    _connect(doubled, "", inside, "")
    clamped = _node(material, unreal.MaterialExpressionSaturate, -340, 950)
    _connect(inside, "", clamped, "")
    shaped = _node(material, unreal.MaterialExpressionPower, -200, 950)
    _connect(clamped, "", shaped, "Base")
    _connect(_constant(material, exponent, -340, 1080), "", shaped, "Exponent")
    return _times(material, output, "", shaped, "", 0, 700)


def emissive_material(name, round_off, taper=False, soft_edge=0.0, split=False):
    """An additive, unlit, two-sided `Color * Brightness` material.

    `round_off` multiplies by 1 - Fresnel, which is what stops a beam's cone
    looking like a flat triangle -- and is exactly wrong for a dot on a wall,
    where it dims the floor and ceiling dots you most want to see.

    `taper` fades a shaft along its length: Falloff ** (travelled / Reach) from
    `Origin`. The driver passes Falloff = exp(-k * Reach), so this is exactly
    exp(-k * travelled) at any length. (A lerp from 1 to Falloff is the same
    thing only near the origin, and at the scale of a mirror ball's several
    hundred shafts the difference washed the room out to an even blue.) Measured
    from `Origin` rather than the mesh's own Z, so one material serves a head's
    shaft (Origin = the lens) and the ball's spray (Origin = the ball).

    `split` puts `ColorB` on the TOP half of the beam and `Color` on the bottom,
    across the world-space plane `SplitNormal` through `Origin` -- a color wheel
    parked between segments throws two colors, not a blend.
    """
    material = _fresh(name)
    if material is None:
        return
    material.set_editor_property("blend_mode", unreal.BlendMode.BLEND_ADDITIVE)
    material.set_editor_property("shading_model", unreal.MaterialShadingModel.MSM_UNLIT)
    # The camera is inside a beam most of the time in a room where heads aim at the middle.
    material.set_editor_property("two_sided", True)

    color = _param(material, unreal.MaterialExpressionVectorParameter, "Color", (1, 1, 1, 1), -700, -100)
    brightness = _param(material, unreal.MaterialExpressionScalarParameter, "Brightness", 1.0, -700, 100)

    xyz = here = offset = None
    if taper or split:
        origin = _param(material, unreal.MaterialExpressionVectorParameter, "Origin", (0, 0, 0, 0), -1900, 500)
        xyz = _xyz(material, origin, -1700, 500)
        here = _node(material, unreal.MaterialExpressionWorldPosition, -1900, 650)
        offset = _binary(material, unreal.MaterialExpressionSubtract, here, xyz, -1500, 600)

    shade = color
    if split:
        color_b = _param(material, unreal.MaterialExpressionVectorParameter, "ColorB", (1, 1, 1, 1), -700, -250)
        # Zero by default: the dot product is 0 everywhere, alpha sits at 0.5,
        # and an unsplit fixture has ColorB == Color anyway.
        normal = _param(material, unreal.MaterialExpressionVectorParameter, "SplitNormal", (0, 0, 0, 0), -1500, -450)
        side = _binary(material, unreal.MaterialExpressionDotProduct, offset,
                       _xyz(material, normal, -1300, -450), -1100, -350)
        sharp = _times(material, side, "", _constant(material, SPLIT_SHARPNESS, -1100, -200), "", -900, -300)
        biased = _binary(material, unreal.MaterialExpressionAdd, sharp,
                         _constant(material, 0.5, -900, -180), -750, -300)
        alpha = _node(material, unreal.MaterialExpressionSaturate, -600, -300)
        _connect(biased, "", alpha, "")
        mixed = _node(material, unreal.MaterialExpressionLinearInterpolate, -450, -150)
        _connect(color, "", mixed, "A")
        _connect(color_b, "", mixed, "B")
        _connect(alpha, "", mixed, "Alpha")
        shade = mixed

    output = _times(material, shade, "", brightness, "", -300, 0)
    if round_off:
        fresnel = _node(material, unreal.MaterialExpressionFresnel, -700, 250, exponent=1.6)
        inverted = _node(material, unreal.MaterialExpressionOneMinus, -450, 250)
        _connect(fresnel, "", inverted, "")
        output = _times(material, output, "", inverted, "", -150, 100)

    if taper:
        travelled = _binary(material, unreal.MaterialExpressionDistance, here, xyz, -700, 550)
        # Never zero: it is a divisor, and an un-aimed beam would render NaN.
        reach = _param(material, unreal.MaterialExpressionScalarParameter, "Reach", 1000.0, -700, 750)
        fraction = _binary(material, unreal.MaterialExpressionDivide, travelled, reach, -500, 600)
        clamped = _node(material, unreal.MaterialExpressionSaturate, -350, 600)
        _connect(fraction, "", clamped, "")
        far = _param(material, unreal.MaterialExpressionScalarParameter, "Falloff", 1.0, -500, 480)
        remaining = _node(material, unreal.MaterialExpressionPower, -200, 480)
        _connect(far, "", remaining, "Base")
        _connect(clamped, "", remaining, "Exponent")
        output = _times(material, output, "", remaining, "", 0, 200)

    if soft_edge:
        output = _soft_edge(material, output, soft_edge)

    lib.connect_material_property(output, "", unreal.MaterialProperty.MP_EMISSIVE_COLOR)
    _save(material, name)


def light_materials():
    # The shaft of every beam, and the ball's reflected shafts.
    emissive_material("M_PrevizBeam", round_off=True, taper=True, split=True)
    # A dot has no length to lose light over; it is sized from its real throw.
    emissive_material("M_PrevizDot", round_off=False, soft_edge=DOT_SHOULDER)


def instanced_usage():
    """Mark every material the app draws on instanced meshes as allowed to be.

    The editor sets a missing usage flag by itself the first time a material
    meets an instanced mesh, so a missing one never shows up in the editor or in
    PIE. A COOKED game cannot recompile, and falls back to the default material
    with only a log line to say so (Material.cpp:1882) -- measured in the F20a
    spike: M_PrevizDot came out of the cook with the flag unset.
    """
    for name in ("M_PrevizModel", "M_PrevizBeam", "M_PrevizDot"):
        path = f"{MATERIAL_DIR}/{name}"
        material = assets.load_asset(path) if assets.does_asset_exist(path) else None
        if material is None:
            raise RuntimeError(f"{path} is missing after it was built")
        # Set and saved unconditionally. Reading the flag first is a trap: the
        # editor opens a map before this runs, instanced meshes in it set the
        # flag IN MEMORY, and a "skip if already set" check then skips the save
        # that was the whole point -- the package on disk, which is what the
        # cooker reads, still says false.
        material.set_editor_property("used_with_instanced_static_meshes", True)
        lib.recompile_material(material)
        assets.save_asset(path, only_if_is_dirty=False)
        log(f"flagged {path} for instanced static meshes")


# --------------------------------------------------------------------- map --

def empty_map():
    """The app's one map, and it is EMPTY.

    Everything in the room is spawned at runtime from the engine's scene, so a
    map with anything in it would show that thing in every venue. Kept separate
    from /Game/Maps/Previz, which the editor-Python driver still builds into.
    """
    if assets.does_asset_exist(MAP_PATH) and not FORCE:
        log(f"{MAP_PATH} exists; leaving it")
        return
    levels = unreal.get_editor_subsystem(unreal.LevelEditorSubsystem)
    if not levels.new_level(MAP_PATH):
        raise RuntimeError(f"could not create {MAP_PATH}")
    levels.save_current_level()
    log(f"built {MAP_PATH}")


def main():
    model_material()
    light_materials()
    instanced_usage()
    empty_map()
    log("build_assets done")


main()
