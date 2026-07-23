"""
Blender venue scaffolding script.
Paste into Blender's Text Editor (Scripting workspace) and click Run Script,
or run headless:
    blender stage.blend --python scripts/scaffold_venue.py

NOTE: The stage model (Mesh_0) was already scaled and positioned via the Blender MCP
during initial setup. If re-running this script on a fresh file, you'll need to first
rescale Mesh_0:
    scale = 4.572 / bpy.data.objects["Mesh_0"].dimensions.x
    bpy.data.objects["Mesh_0"].scale = (scale, scale, scale)
    # then apply scale and reposition so front face = Y=0, floor = Z=0

What this creates (all inside collection "Cosmos DMX Rig"):
  - Dancefloor plane     30 x 60 ft  (9.144 x 18.288 m)
  - Stage floor deck     15 x 15 ft  centered at Y=0
  - Front stage truss    at Y=0, Z=10ft (the FOH wash bar)
  - Back stage truss     at Y=+STAGE_D/2
  - Center dancefloor truss (for Mini Kinta)
  - One Empty (ARROWS display) per fixture from the patch sheet,
    with custom properties: dmx_universe, dmx_start, channel_count

Coordinate system (all meters):
  +X = stage right (from audience POV)
  +Y = upstage (toward back of stage); audience is at Y ≈ -20 m
  +Z = up; stage floor = Z=0
  Stage front face (structure) = Y=0
  Stage performance area extends: Y = -STAGE_D/2 to Y = +STAGE_D/2

Adjust CLEAR_EXISTING = True to rebuild from scratch.
Adjust fixture positions in FIXTURES list below as needed.
"""

import bpy
import math

# ── Config ────────────────────────────────────────────────────────────────────
CLEAR_EXISTING = False  # Set True to delete "Cosmos DMX Rig" collection first

FT = 0.3048  # feet → metres

STAGE_W = 15 * FT    # 4.572 m
STAGE_D = 15 * FT    # 4.572 m
FLOOR_W = 30 * FT    # 9.144 m
FLOOR_D = 60 * FT    # 18.288 m
TRUSS_H = 10 * FT    # 3.048 m
TRUSS_R = 0.06       # 6 cm tube radius (visual only)

# Stage front face of structure = Y=0; performance area straddles Y=0
STAGE_FRONT_EDGE = -(STAGE_D / 2)    # -2.286 m  (where audience meets stage)
FLOOR_CENTER_Y   = STAGE_FRONT_EDGE - FLOOR_D / 2  # -11.43 m

# ── Fixture layout ────────────────────────────────────────────────────────────
# Each entry: (qlcplus_id, name, (X, Y, Z), {custom_props})
# Positions match what was set up via the Blender MCP — adjust as needed.
FIXTURES = [
    # Par 36 stage wash — front truss (Y=0, the main wash bar)
    (0, "Par36_1_FrontLeft",  (-STAGE_W/2 + 0.3, 0, TRUSS_H),
        {"dmx_universe": 0, "dmx_start": 1,  "channel_count": 5}),
    (1, "Par36_2_FrontRight", ( STAGE_W/2 - 0.3, 0, TRUSS_H),
        {"dmx_universe": 0, "dmx_start": 7,  "channel_count": 5}),
    # Par 36 stage wash — back truss
    (2, "Par36_3_BackLeft",   (-STAGE_W/2 + 0.3, STAGE_D/2, TRUSS_H),
        {"dmx_universe": 0, "dmx_start": 13, "channel_count": 5}),
    (3, "Par36_4_BackRight",  ( STAGE_W/2 - 0.3, STAGE_D/2, TRUSS_H),
        {"dmx_universe": 0, "dmx_start": 19, "channel_count": 5}),
    # Pinstpots — front truss, aimed at stage
    (4, "Pinspot_1_Left",  (-0.8, 0, TRUSS_H),
        {"dmx_universe": 0, "dmx_start": 25, "channel_count": 6}),
    (5, "Pinspot_2_Right", ( 0.8, 0, TRUSS_H),
        {"dmx_universe": 0, "dmx_start": 31, "channel_count": 6}),
    # Derby / laser — center stage overhead
    (6, "DerbyLaser",     (0.0, 1.0, TRUSS_H + 0.5),
        {"dmx_universe": 0, "dmx_start": 37, "channel_count": 13}),
    # Scorpion laser — upstage center on stand
    (7, "Scorpion_Laser", (0.0, 2.0, 2.5),
        {"dmx_universe": 0, "dmx_start": 50, "channel_count": 10}),
    # Mini Kinta effect — center dancefloor truss
    (8, "MiniKinta",      (0.0, FLOOR_CENTER_Y + 2, TRUSS_H),
        {"dmx_universe": 0, "dmx_start": 61, "channel_count": 4}),
    # Dimmers — stage wings and audience near-side
    (9,  "Dimmer_1_StageLeft",  (-STAGE_W/2 - 0.3, 0, 1.4),
        {"dmx_universe": 0, "dmx_start": 70, "channel_count": 1}),
    (10, "Dimmer_2_StageRight", ( STAGE_W/2 + 0.3, 0, 1.4),
        {"dmx_universe": 0, "dmx_start": 71, "channel_count": 1}),
    (11, "Dimmer_3_AudLeft",    (-FLOOR_W/2 + 0.5, STAGE_FRONT_EDGE - 4*FT, 1.5),
        {"dmx_universe": 0, "dmx_start": 72, "channel_count": 1}),
    (12, "Dimmer_4_AudRight",   ( FLOOR_W/2 - 0.5, STAGE_FRONT_EDGE - 4*FT, 1.5),
        {"dmx_universe": 0, "dmx_start": 73, "channel_count": 1}),
]

# ── Helpers ───────────────────────────────────────────────────────────────────

def get_or_create_collection(name: str, parent=None):
    if name in bpy.data.collections:
        return bpy.data.collections[name]
    col = bpy.data.collections.new(name)
    parent_col = parent or bpy.context.scene.collection
    parent_col.children.link(col)
    return col


def move_to_collection(obj, target_col):
    for col in list(obj.users_collection):
        col.objects.unlink(obj)
    target_col.objects.link(obj)


def deselect_all():
    bpy.ops.object.select_all(action="DESELECT")


def add_box(name: str, location, dimensions, collection) -> bpy.types.Object:
    deselect_all()
    bpy.ops.mesh.primitive_cube_add(size=1, location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.dimensions = dimensions
    bpy.ops.object.transform_apply(scale=True)
    move_to_collection(obj, collection)
    return obj


def add_cylinder(name: str, location, radius: float, depth: float,
                 rotation=(0, 0, 0), collection=None) -> bpy.types.Object:
    deselect_all()
    bpy.ops.mesh.primitive_cylinder_add(
        radius=radius, depth=depth,
        location=location,
        rotation=rotation,
    )
    obj = bpy.context.active_object
    obj.name = name
    if collection:
        move_to_collection(obj, collection)
    return obj


def add_empty(name: str, location, props: dict, collection) -> bpy.types.Object:
    deselect_all()
    bpy.ops.object.empty_add(type="ARROWS", location=location)
    obj = bpy.context.active_object
    obj.name = name
    obj.empty_display_size = 0.4
    for key, val in props.items():
        obj[key] = val
    move_to_collection(obj, collection)
    return obj


# ── Main ──────────────────────────────────────────────────────────────────────

def build_venue():
    root_col = get_or_create_collection("Cosmos DMX Rig")

    if CLEAR_EXISTING:
        for obj in list(root_col.objects):
            bpy.data.objects.remove(obj, do_unlink=True)

    geo_col = get_or_create_collection("Venue Geometry", parent=root_col)
    fix_col = get_or_create_collection("Fixture Positions", parent=root_col)

    # Stage floor deck (flat plane under/in-front-of the stage structure model)
    add_box(
        "Stage_Floor",
        location=(0, 0, -0.025),
        dimensions=(STAGE_W, STAGE_D, 0.05),
        collection=geo_col,
    )

    # Dancefloor (extends in -Y direction from stage front edge)
    add_box(
        "Dancefloor",
        location=(0, FLOOR_CENTER_Y, -0.025),
        dimensions=(FLOOR_W, FLOOR_D, 0.05),
        collection=geo_col,
    )

    # Front truss (Y=0, front of stage structure — the main FOH wash bar)
    add_cylinder(
        "Truss_Stage_Front",
        location=(0, 0, TRUSS_H),
        radius=TRUSS_R,
        depth=STAGE_W * 1.1,
        rotation=(0, math.radians(90), 0),
        collection=geo_col,
    )

    # Back/upstage truss
    add_cylinder(
        "Truss_Stage_Back",
        location=(0, STAGE_D / 2, TRUSS_H),
        radius=TRUSS_R,
        depth=STAGE_W * 0.8,
        rotation=(0, math.radians(90), 0),
        collection=geo_col,
    )

    # Center dancefloor truss (for Mini Kinta / aerial effects)
    add_cylinder(
        "Truss_Dancefloor",
        location=(0, FLOOR_CENTER_Y + 2, TRUSS_H),
        radius=TRUSS_R,
        depth=FLOOR_W * 0.7,
        rotation=(0, math.radians(90), 0),
        collection=geo_col,
    )

    # Fixture empties
    for qlcplus_id, name, loc, props in FIXTURES:
        full_props = {"qlcplus_id": qlcplus_id, **props}
        add_empty(name, loc, full_props, fix_col)

    print(f"Cosmos DMX Rig built: {len(FIXTURES)} fixture empties created.")
    print(f"  Stage floor:  X ±{STAGE_W/2:.2f}, Y {-STAGE_D/2:.2f} to {STAGE_D/2:.2f}")
    print(f"  Dancefloor:   X ±{FLOOR_W/2:.2f}, Y {STAGE_FRONT_EDGE:.2f} to {STAGE_FRONT_EDGE - FLOOR_D:.2f}")
    print(f"  Front truss:  Y=0.00, Z={TRUSS_H:.2f}")
    print("  Adjust fixture empty positions in Blender, then add GDTF fixtures in BlenderDMX.")


build_venue()
