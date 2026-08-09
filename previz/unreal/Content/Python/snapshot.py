"""
Render a still of the previz level to a PNG. Runs inside the editor.

    python previz/ue_remote.py previz/unreal/Content/Python/snapshot.py

Uses an offscreen SceneCapture rather than a viewport screenshot so it does not
depend on which panel happens to be focused, works with the editor minimised,
and gives the same framing every time -- which is what makes two snapshots
comparable when the point is "did this change the look".

`AUDIENCE` is deliberately a real place to stand: 1.65 m up, inside the crowd
footprint, looking at the mirror ball. A previz shot from an impossible angle
flatters a rig that nobody will ever see from there.
"""

import sys
from pathlib import Path

import unreal

REPO = Path(__file__).resolve().parents[4]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

OUT_DIR = REPO / "renders"
WIDTH, HEIGHT = 1600, 900
FOV = 85.0

# location, look-at, field of view, and any walls to cut away, in Unreal cm.
#
# Framing matters far more here than it looks. Four heads in the four corners
# all aim inward, so *any* camera standing in the room at head height sits in
# the crossing field: the nearest beam passes a metre from the lens, fills a
# third of the frame, and the picture becomes useless for judging where four
# beams are relative to each other. The honest fix is the one every previz
# uses -- stand outside and cut away the near wall.
#
# `hide` is handed to the capture component rather than applied to the level, so
# the walls stay present for lighting and for every other view.
# `fog_start` is the volumetric fog's start distance for this camera. The fog's
# depth slices span start..distance, so pushing the start out to just before the
# room concentrates every slice where the beams are and visibly smooths them.
# It is measured from the camera, which is why it belongs to the VIEW and not to
# the level: 500 cm is right for a camera standing 6 m outside the room and
# catastrophic for one standing inside it, where it erases the fog from every
# beam within 5 m. Measured both ways; see the README.
# One more framing trap, learned the same way: the room's MID-LINE is not empty
# either. The pinspots are centred on two opposing sides of the truss at beam
# height and aimed at the ball, so a camera on the mid-line looks straight down a
# pinspot's barrel and its body cube sits square in front of the ball. Every view
# below is therefore offset off both the diagonals and the mid-line -- far enough
# to clear the hardware, not so far that the shot stops being the one you wanted.
#
# All four views used to stand OUTSIDE the room with a wall cut away, because in
# a room that ended where the rig did there was nowhere inside it to stand that
# was not in a beam. The room is 18 m now and the rig is a 9 m truss frame in the
# middle of it, so there is 4.5 m of clear floor all the way round: every view
# stands in that margin instead. Nothing is cut away any more except the ceiling,
# which is the honest version -- a cutaway wall is a wall not bouncing light.
MIDLINE = 914.4                    # the room's centre line, UE cm
BALL = (914.4, 914.4, 274.3)
OFF_AXIS = 110.0

VIEWS = {
    # The working view: inside the front wall, outside the truss, whole rig in
    # frame. Lifted above beam height as well as offset, so the near pinspot --
    # which hangs at exactly the height of everything worth looking at -- drops
    # below the ball in frame instead of eclipsing it.
    # Above bar height (297 cm) as well, so the near bar sits below the line to
    # the ball instead of cutting the frame in half.
    "overview": dict(location=(100.0, MIDLINE + OFF_AXIS, 480.0),
                     target=BALL,
                     fov=70.0, hide=("PZ_Ceiling",), fog_start=80.0),
    # Looking down the room's diagonal from the empty corner outside the truss.
    # Deliberately NOT on the diagonal, near as it looks: the nearest head is in
    # that corner aiming at the ball, so a camera exactly on the line takes its
    # beam straight down the barrel and the shot comes back with a white spike
    # up the middle.
    "corner": dict(location=(150.0, 310.0, 480.0), target=(914.4, 914.4, 255.0),
                   fov=70.0, hide=("PZ_Ceiling",), fog_start=80.0),
    # Standing in the crowd at eye height. Deliberately kept, blinding beams and
    # all: this is what a person actually sees, and it is the only view that
    # answers "is this going to be unpleasant to stand in". Just inside the
    # crowd footprint's near edge (577 cm), which is where a real person is.
    "audience": dict(location=(600.0, MIDLINE + OFF_AXIS, 165.0),
                     target=(914.4, 914.4, 245.0),
                     fov=85.0, hide=(), fog_start=0.0),
    # Close on the ball, which is the only way to judge the tiling: from
    # anywhere a person stands it is 40 cm at 6 m, and any two tessellations
    # look the same at that size.
    # Off the diagonal, because the four heads sit in the four corners and
    # anything on a diagonal is standing in a beam -- the shot comes back as a
    # white rectangle. And off the mid-line, because that is where the pinspots
    # now are; on it, one of their bodies eclipses the ball.
    "ball": dict(location=(MIDLINE + OFF_AXIS, 570.4, 290.0),
                 target=BALL,
                 fov=26.0, hide=("PZ_Ceiling",), fog_start=0.0),
}


def _actors_labelled(labels):
    if not labels:
        return []
    wanted = set(labels)
    subsystem = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    return [a for a in subsystem.get_all_level_actors()
            if a.get_actor_label() in wanted]


def _settle_driver():
    """Let any running driver finish its moves before the shutter opens.

    The heads model a real yoke now, so they lag the DMX by up to a second. A
    still is asking what a look LOOKS like, not what it looks like partway
    into the travel -- and without this the answer would depend on how long
    ago the frame was written, which is exactly the kind of irreproducibility
    a scripted snapshot exists to remove. A live previz is unaffected: it goes
    straight back to following on the next editor tick.
    """
    try:
        import cosmos_live_state
    except ImportError:
        return
    state = getattr(cosmos_live_state, "current", None)
    if state is not None:
        state.settle()


def snapshot(name="overview", out_dir=OUT_DIR, width=WIDTH, height=HEIGHT,
             passes=24):
    view = VIEWS[name]
    _settle_driver()
    location, target = view["location"], view["target"]
    editor = unreal.get_editor_subsystem(unreal.UnrealEditorSubsystem)
    world = editor.get_editor_world()

    # RGBA8, not the RGBA16f default: export_render_target picks its file format
    # from the target's pixel format, and a float target silently writes an
    # OpenEXR with whatever extension you asked for.
    target_rt = unreal.RenderingLibrary.create_render_target2d(
        world, width, height, unreal.TextureRenderTargetFormat.RTF_RGBA8)

    # Point the fog's slice range at this camera, and put it back afterwards so
    # a snapshot never leaves the viewport looking different from before.
    fogs = [a for a in unreal.GameplayStatics.get_all_actors_with_tag(world, "cosmos_previz")
            if isinstance(a, unreal.ExponentialHeightFog)]
    restore = [(f, f.component.get_editor_property("volumetric_fog_start_distance"))
               for f in fogs]
    for fog in fogs:
        fog.component.set_editor_property("volumetric_fog_start_distance",
                                          float(view.get("fog_start", 0.0)))
    actors = unreal.get_editor_subsystem(unreal.EditorActorSubsystem)
    capture = actors.spawn_actor_from_class(
        unreal.SceneCapture2D, unreal.Vector(*location),
        unreal.MathLibrary.find_look_at_rotation(unreal.Vector(*location),
                                                 unreal.Vector(*target)))
    try:
        component = capture.capture_component2d
        component.set_editor_property("texture_target", target_rt)
        # Final colour, so what lands in the PNG is what the viewport shows --
        # volumetric fog, bloom and tone mapping included. Scene colour alone
        # would omit exactly the haze the beams are visible in.
        component.set_editor_property(
            "capture_source", unreal.SceneCaptureSource.SCS_FINAL_COLOR_LDR)
        component.set_editor_property("capture_every_frame", False)
        component.set_editor_property("capture_on_movement", False)
        component.set_editor_property("fov_angle", view.get("fov", FOV))
        # hide_actor_components(), not the `hidden_actors` array: that property
        # refuses to be set from Python ("cannot be edited on templates"), while
        # this function does the same job and is callable on a spawned instance.
        for hidden in _actors_labelled(view["hide"]):
            component.hide_actor_components(hidden, True)

        # Capture repeatedly before exporting. Volumetric fog is jittered per
        # frame and resolved by reprojecting the previous frame's result, so a
        # single capture has no history to reproject and renders the raw froxel
        # grid -- a beam comes out as a string of beads rather than a shaft.
        # Successive captures accumulate that history.
        for _ in range(passes):
            component.capture_scene()

        out_dir = Path(out_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        filename = f"previz_{name}.png"
        unreal.RenderingLibrary.export_render_target(
            world, target_rt, str(out_dir), filename)
        path = out_dir / filename
        print(f"[cosmos] {path}")
        return str(path)
    finally:
        actors.destroy_actor(capture)
        for fog, previous in restore:
            fog.component.set_editor_property("volumetric_fog_start_distance",
                                              previous)


if __name__ == "__main__":
    for view in VIEWS:
        snapshot(view)
