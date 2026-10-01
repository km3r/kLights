"""
Create the content the packaged previz loads by path. Runs inside the editor,
headless, as a build step -- the app itself never runs Python:

    UnrealEditor-Cmd.exe previz/unreal/KLightsPreviz.uproject ^
        -ExecutePythonScript=<abs path>/build_assets.py -unattended -nosplash

Everything here is an asset the C++ looks up by name, so the parameter names
below are a contract with `Source/KLightsPreviz`. Change one side, change both.

Idempotent: an asset that already exists is left alone unless --force is given
(``-ExecutePythonScript="build_assets.py --force"``).
"""

import sys

import unreal

MATERIAL_DIR = "/Game/Previz/Materials"
WHITE = "/Engine/EngineResources/WhiteSquareTexture"

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


def _param(material, cls, name, default, x, y):
    node = lib.create_material_expression(material, cls, x, y)
    node.set_editor_property("parameter_name", name)
    if cls is unreal.MaterialExpressionScalarParameter:
        node.set_editor_property("default_value", float(default))
    elif cls is unreal.MaterialExpressionVectorParameter:
        node.set_editor_property("default_value", unreal.LinearColor(*default))
    else:
        node.set_editor_property("texture", unreal.load_asset(default))
    return node


def _times(material, a, a_pin, b, b_pin, x, y):
    node = lib.create_material_expression(material, unreal.MaterialExpressionMultiply, x, y)
    lib.connect_material_expressions(a, a_pin, node, "A")
    lib.connect_material_expressions(b, b_pin, node, "B")
    return node


def _save(material, name):
    lib.recompile_material(material)
    path = f"{MATERIAL_DIR}/{name}"
    assets.save_asset(path)
    if not assets.does_asset_exist(path):
        raise RuntimeError(f"{path} was saved but the asset registry does not see it")
    log(f"built {path}")


def model_material():
    """M_PrevizModel: what every glTF material is instanced from.

    Parameters (FKLightsModelLoader sets them): BaseColor, BaseColorMap,
    Metallic, Roughness, Emissive, EmissiveMap. Maps default to white, so a
    material with no texture is just its factor.

    Two-sided, because venue models are routinely authored as single planes and
    the previz camera stands inside them.
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


def instanced_usage():
    """Mark every material the app draws on instanced meshes as allowed to be.

    The editor sets a missing usage flag by itself the first time a material
    meets an instanced mesh, so this never shows up in the editor or in PIE. A
    COOKED game cannot recompile, and falls back to the default material with
    only a log line to say so (Material.cpp:1882) -- measured in the F20a
    spike: M_PrevizDot came out of the cook with the flag unset.
    """
    for name in ("M_PrevizDot", "M_PrevizBeam", "M_PrevizBallTile"):
        path = f"{MATERIAL_DIR}/{name}"
        material = assets.load_asset(path) if assets.does_asset_exist(path) else None
        if material is None:
            log(f"{path} missing; nothing to flag")
            continue
        # Set and saved unconditionally. Reading the flag first is a trap: the
        # editor opens the startup map before this runs, its instanced meshes
        # set the flag IN MEMORY, and a "skip if already set" check then skips
        # the save that was the whole point -- the package on disk, which is
        # what the cooker reads, still says false.
        material.set_editor_property("used_with_instanced_static_meshes", True)
        lib.recompile_material(material)
        assets.save_asset(path, only_if_is_dirty=False)
        log(f"flagged {path} for instanced static meshes")


def main():
    model_material()
    instanced_usage()
    log("build_assets done")


main()
