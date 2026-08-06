"""
Position the BlenderDMX fixtures in the real venue: key/wash light onto the DJ
from the stage beam, and effects out over the dancefloor.

This is the "scaffold" step adapted to the ACTUAL stage.blend layout (the venue
geometry was hand-placed, so the original scaffold_venue.py coordinates no longer
match). It:
  - moves each fixture's root to a sensible spot on the real geometry
  - aims each fixture's target (TRACK_TO): stage fixtures at the DJ (center stage),
    effects at the crowd
  - (re)creates a "Fixture Positions" collection of empties tagged with qlcplus_id
    so patch_blenderdmx.py can re-snap fixtures here on any future re-patch.

Run from Blender's Scripting workspace (Open -> Run Script) AFTER patch_blenderdmx.py.

Real-scene anchors (meters), from world-space BOUNDING BOXES (not object origins,
which are offset: the Dancefloor origin reads Y=+12.5 but its mesh sits in -Y):
  Mesh_0 stage model : X[-3.4,2.3] Y[-0.3,2.7] Z[0,3.8], faces -Y (the crowd)
  Stage_Floor deck   : X[-2.3,2.3] Y[0.5,5.1]
  Dancefloor         : X[-4.6,4.6] Y[0.7 .. -14.5]   ==> AUDIENCE IS IN -Y
  Truss_Audience bar : Y=-1.72, Z=3.05, 7.77 m wide  (over front of the crowd)
DJ focal point ~ (0, 1.4, 1.3). Adjust LAYOUT below to taste, then re-run.
"""

import bpy
from mathutils import Vector

# qlcplus_id -> (root position, aim/target point).  Audience/dancefloor is -Y.
LAYOUT = {
    # --- Stage beam (heavy truss, front-top of stage structure, Z~3.5) ---
    0:  ((-2.0, -0.3, 3.5), (-1.3,  1.8, 0.6)),   # Par #1 wash stage-left + downstage
    1:  ((-0.7, -0.3, 3.5), ( 0.0,  1.4, 1.2)),   # Par #2 color front-key on DJ
    2:  (( 0.7, -0.3, 3.5), ( 0.0,  1.4, 1.2)),   # Par #3 color front-key on DJ
    3:  (( 2.0, -0.3, 3.5), ( 1.3,  1.8, 0.6)),   # Par #4 wash stage-right + downstage
    4:  ((-1.3, -0.3, 3.6), ( 0.0,  1.5, 1.4)),   # Pinspot #1 tight cross-pin on DJ
    5:  (( 1.3, -0.3, 3.6), ( 0.0,  1.5, 1.4)),   # Pinspot #2 tight cross-pin on DJ
    7:  (( 0.0, -0.2, 3.6), ( 0.0,-10.0, 2.6)),   # Scorpion aerial laser over crowd
    # --- Audience truss (lightweight, over crowd, Z~3.0) ---
    6:  ((-1.0, -1.72, 3.0), (-1.5, -6.0, 0.0)),  # Derby patterns into crowd
    8:  (( 1.0, -1.72, 3.0), ( 1.5, -6.0, 0.0)),  # Mini Kinta flower into crowd
    # --- Stage floor edge ---
    9:  (( 0.0, 0.55, 0.22), ( 0.0, -3.0, 0.0)),  # LED strip on the stage lip
    # --- Spare dimmers (unused) parked behind the stage, out of frame ---
    10: ((-3.2, 4.6, 0.1), (-3.2, 5.6, 0.1)),
    11: ((-2.7, 4.6, 0.1), (-2.7, 5.6, 0.1)),
    12: ((-2.2, 4.6, 0.1), (-2.2, 5.6, 0.1)),
}


def main():
    dmx = getattr(bpy.context.scene, "dmx", None)
    if dmx is None:
        raise RuntimeError("BlenderDMX not enabled.")

    # (re)create the Fixture Positions collection
    if "Fixture Positions" in bpy.data.collections:
        pos_col = bpy.data.collections["Fixture Positions"]
        for o in list(pos_col.objects):
            bpy.data.objects.remove(o, do_unlink=True)
    else:
        pos_col = bpy.data.collections.new("Fixture Positions")
        bpy.context.scene.collection.children.link(pos_col)

    moved = 0
    for fx in dmx.fixtures:
        if not fx.fixture_id.isdigit():
            continue
        fid = int(fx.fixture_id)
        if fid not in LAYOUT:
            continue
        pos, aim = LAYOUT[fid]

        root = next((o for o in fx.collection.all_objects
                     if o.get("geometry_root", False)), None)
        target = next((o for o in fx.collection.all_objects
                       if "Target" in o.name), None)
        if root:
            root.location = Vector(pos)
        if target:
            target.location = Vector(aim)

        empty = bpy.data.objects.new(f"FP_{fid}_{fx.name[:14]}", None)
        empty.empty_display_type = "ARROWS"
        empty.empty_display_size = 0.4
        empty.location = Vector(pos)
        empty["qlcplus_id"] = fid
        pos_col.objects.link(empty)
        moved += 1

    bpy.context.view_layer.update()
    print(f"Positioned {moved} fixtures and created {len(pos_col.objects)} position empties.")


if __name__ == "__main__":
    main()
