"""
Tune the LIVE EEVEE viewport so the DMX rig reads as a light show while you program
in QLC+ (realtime, 24 fps). This is the "optimize live EEVEE" pass — it does NOT make
EEVEE cinematic (BlenderDMX spots are weak in EEVEE and the stage Pars aim into the
DJ booth, so their beams are occluded), but it gets the most readable live view:
matte floors that catch pools, subtle volumetric haze so beams show as cones, brighter
emissive beam cones, a faint cool ambient so geometry reads, punchy exposure, and a
Bloom glow so fixture emitters glow.

Run AFTER go_live.py (Scripting workspace -> Open -> Run Script). Idempotent.
Tip: the view pops MUCH more once you program COLORS in QLC+ — flat white blooms to
a washed glow, but colored beams in haze look like a real rig. For a truly cinematic
"beams hitting everything" picture, render a frozen look in CYCLES instead (live +
Cycles can't coexist: the 24 fps DMX timer restarts Cycles' sampling every frame).
"""

import bpy


def _floor(matname, grey):
    m = bpy.data.materials.get(matname)
    if not m:
        return
    m.use_nodes = True
    bsdf = next((n for n in m.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None)
    if bsdf:
        bsdf.inputs["Base Color"].default_value = (grey, grey, grey, 1)
        if "Roughness" in bsdf.inputs:
            bsdf.inputs["Roughness"].default_value = 0.85
        if "Specular IOR Level" in bsdf.inputs:
            bsdf.inputs["Specular IOR Level"].default_value = 0.2


def main():
    sc = bpy.context.scene
    sc.render.engine = "BLENDER_EEVEE"            # realtime / live

    # Receptive matte floors so light pools read.
    _floor("Dancefloor_mat", 0.32)
    _floor("Stage_Floor_mat", 0.30)

    # World: subtle volumetric haze (beams become cones) + faint cool ambient.
    w = sc.world
    w.use_nodes = True
    nt = w.node_tree
    out = next(n for n in nt.nodes if n.type == "OUTPUT_WORLD")
    vs = next((n for n in nt.nodes if n.type == "VOLUME_SCATTER"), None) or nt.nodes.new("ShaderNodeVolumeScatter")
    vs.inputs["Density"].default_value = 0.025
    if "Anisotropy" in vs.inputs:
        vs.inputs["Anisotropy"].default_value = 0.3
    nt.links.new(vs.outputs["Volume"], out.inputs["Volume"])
    bg = next((n for n in nt.nodes if n.type == "BACKGROUND"), None) or nt.nodes.new("ShaderNodeBackground")
    bg.inputs["Color"].default_value = (0.01, 0.012, 0.02, 1)
    bg.inputs["Strength"].default_value = 1.0
    nt.links.new(bg.outputs["Background"], out.inputs["Surface"])

    # EEVEE volumetric range/quality covering the venue.
    ee = sc.eevee
    ee.volumetric_start = 0.1
    ee.volumetric_end = 55.0
    ee.volumetric_samples = 96
    ee.use_volumetric_shadows = True

    # Brighter emissive beam cones (BlenderDMX's own display beams). Keep moderate —
    # too high and colored light clips to white-hot and you lose the color.
    sc.dmx.beam_intensity_multiplier = 2.0

    # Soft fill lamp up & behind the stage (out of audience-POV frame), no volume orb.
    lamp = bpy.data.objects.get("Light")
    if lamp:
        lamp.location = (0.0, 7.0, 9.0)
        lamp.data.energy = 500
        if hasattr(lamp.data, "volume_factor"):
            lamp.data.volume_factor = 0.0
        lamp.visible_camera = False

    # Standard keeps beam colors saturated (AgX desaturates them). Keep exposure LOW
    # — high exposure clips colored light to white so fixtures look colorless.
    sc.view_settings.view_transform = "Standard"
    sc.view_settings.exposure = 0.3

    # Bloom glow via the Blender 5.1 scene compositor (params are INPUT SOCKETS now,
    # and the output is a Group Output 'Image' socket — no Composite node in 5.1).
    for g in list(bpy.data.node_groups):
        if g.name.startswith("Cosmos Compositing"):
            bpy.data.node_groups.remove(g)
    ng = bpy.data.node_groups.new("Cosmos Compositing", "CompositorNodeTree")
    ng.interface.new_socket("Image", in_out="OUTPUT", socket_type="NodeSocketColor")
    rl = ng.nodes.new("CompositorNodeRLayers")
    glare = ng.nodes.new("CompositorNodeGlare")
    gout = ng.nodes.new("NodeGroupOutput")
    glare.inputs["Type"].default_value = "Bloom"        # menu socket uses display names
    glare.inputs["Threshold"].default_value = 0.7        # only the brightest bloom...
    glare.inputs["Size"].default_value = 0.7
    glare.inputs["Strength"].default_value = 0.5         # ...softly, so color survives
    ng.links.new(rl.outputs["Image"], glare.inputs["Image"])
    ng.links.new(glare.outputs["Image"], gout.inputs[0])
    sc.compositing_node_group = ng

    # Viewport: Rendered + use SCENE lights/world (NOT the studio HDRI) + live compositor.
    vp = 0
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                sh = area.spaces.active.shading
                sh.type = "RENDERED"
                if hasattr(sh, "use_scene_lights"):
                    sh.use_scene_lights = True
                if hasattr(sh, "use_scene_world"):
                    sh.use_scene_world = True
                if hasattr(sh, "use_compositor"):
                    sh.use_compositor = "ALWAYS"
                vp += 1

    print("── live look tuned ──")
    print(f" engine EEVEE | floors matte | haze 0.025 | beams x3.5 | bloom on | {vp} viewport(s)")
    print(" Program COLORS in QLC+ to see beams pop. For a cinematic still, render in Cycles.")


if __name__ == "__main__":
    main()
