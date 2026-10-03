# Models in the previz

How to put a room, a set piece, a mirror ball or a fixture's body into the
standalone previz, and what the files have to be. Everything here is
**previz-only**: nothing the show does reads a model, and a broken model is a
warning on the previz's overlay, never a reason the engine will not start.

## The short version

- **Binary glTF (`.glb`), one self-contained file.** No `.gltf` with a separate
  `.bin` or loose textures: the app is sent one file and nothing else.
- **Metres, +Y up.** glTF's own units. A model that comes out 100× too big was
  exported in centimetres, and the engine will say so.
- **Put it in `shared/models/`** (or beside the file that names it) and name it
  from config. The engine serves it to the app by its content hash, so an
  unchanged model is never downloaded twice.

```bash
python -m engine.scene events/<name>      # the scene the app will get, with any model warnings
python previz/doctor.py                   # the same warnings, plus everything else that can go wrong
```

## Where a model is named

| What | Where | Key |
|---|---|---|
| The room, set pieces, props | the venue file, `previz.models[]` | `file`, `name`, `position`, `rotation`, `scale`, `collide` |
| The mirror ball's look | the venue file, `previz.ball` | `model` |
| A fixture profile's body | [`shared/fixtures/bodies.json`](../shared/fixtures/bodies.json) | `"Manufacturer/Model": {"file": ...}` |
| One unit's body, overriding its profile | `rig.json`, the fixture's `body` | `model`, `nodes`, `rotation` |

A `file` (or `model`) path is resolved **relative to the JSON file that names
it, then `shared/models/`**, and must end up inside `events/` or `shared/` —
the engine will not serve a file from anywhere else.

```json
"previz": {
  "room_walls": false,
  "models": [
    {"name": "hall",  "file": "venues/the-hall.glb"},
    {"name": "riser", "file": "props/riser.glb",
     "position": {"x": 6000, "y": 0, "z": 600}, "rotation": {"yaw": 180}},
    {"name": "banner", "file": "props/banner.glb", "collide": false}
  ],
  "ball": {"model": "props/mirror_ball_24in.glb", "rpm": 2.0}
}
```

`position` is millimetres in the venue's own frame (origin at the room's
front-left floor corner, y up) — the same frame as every other number in the
venue file. `rotation` is degrees; `yaw` is a bearing (0 = into the room, +z;
90 = across, +x). `scale` is uniform. `collide` (default true) is whether a beam
stops on it — a wall should, a hanging banner a beam is meant to pass through
should not.

`room_walls: false` hides the drawn box, for a venue whose walls come from a
model. The box's planes still exist for one purpose: the mirror ball's dots
land on them analytically (that is what makes several hundred dots a frame
affordable), so a model room should be roughly the venue's declared size.

## Venue models: which way round

**Author the room as if standing at its front, looking in**: origin at the
front-left floor corner, **+X across the room, +Y up, the room receding along
-Z**. In Blender that is simply: origin at the front-left floor corner, X
across, **Y away from you**, Z up — the glTF exporter's default `+Y Up`
conversion does the rest.

Why it matters: Unreal's glTF reader and the show's frame disagree by a mirror,
and the app corrects for exactly this convention (a fixed quarter-turn — see
`KLightsStage.cpp`). A room authored any other way arrives rotated or flipped.
[`shared/models/test/axes.glb`](../shared/models/test/axes.glb) is the
orientation check: red arm +X, green +Y, blue +Z. Place it and look.

## The mirror ball

Centred on its own origin, at real size. It **replaces how the ball looks**,
and spins with `rpm`. It does not change where its reflections land: those come
from the ball's position and `ball_radius` in the venue file, analytically, so
keep the model's radius close to `ball_radius`. The occluding core — what stops
beams, and what the safety taper models — stays either way.

## Fixture bodies

A body is drawn for every placed fixture: its model if it has one, otherwise a
box sized from the `.qxf`'s `<Dimensions>`.

A **fixed fixture** (par, pinspot) is posed whole: its front, **+Z**, is turned
along its beam, and its origin goes where the beam starts — so put the origin
at the lens.

A **moving head** is articulated, and needs this shape:

```
base                 the part bolted to the truss
└── yoke             pans about +Y (glTF)
    └── head         tilts about +X; its origin is the tilt axle
        └── lens     optional; where the beam leaves, along +Z
```

- **At rest**: standing upright on its base, **head facing +Z** (the asset's
  front), **no rotation on `yoke` or `head`**.
- **The tilt axle sits on the pan axis**, so panning never moves it. The app
  puts that axle exactly where the fixture is in `rig.json`.
- Other node names are fine — map them in the entry's `nodes`:
  `{"file": "...", "nodes": {"yoke": "Arm", "head": "Lamp"}}`.

The app does not drive the joints from the pan and tilt channels. It **solves**
them: whatever pan and tilt point the head exactly along the beam the show
decoded. On an upright or hung base that is the channel angles anyway; on the
sideways despacio mount, where the engine models bearing and elevation as two
independent channels, it is not, and a body driven from the channels would
swing off its own beam. The beam is always drawn from the decode — the body
follows it, never the other way round.

[`shared/models/fixtures/generic_moving_head.glb`](../shared/models/fixtures/generic_moving_head.glb)
and `generic_can.glb` are built to this convention by
[`shared/tools/gen_models.py`](../shared/tools/gen_models.py), and are what
despacio's fixtures use.

## What the engine checks

Every model is opened while the scene is built — before the app sees it — and
anything wrong becomes a line in the scene's warnings, on the app's overlay and
in `previz doctor`:

| Check | Why |
|---|---|
| Self-contained `.glb` | the app is sent one file |
| No required extension Unreal's runtime reader lacks | it would not load as drawn |
| Under 500 000 triangles | a colliding model's collision is cooked as the room builds — a hitch |
| Textures at most 4096 px a side | memory the previz does not need |
| Between 1 cm and 250 m across (bodies: under 3 m) | anything else was exported in the wrong unit |
| Not skinned or animated | drawn in its rest pose |
| A moving head's body has `yoke` > `head`, unrotated at rest | or the head will not follow its beam |

They are warnings, not refusals: a heavy model still loads, slowly.

## Lighting: why the previz uses hardware ray tracing

A model loaded at runtime is lit properly only with **hardware ray-traced
Lumen**, which is why the project turns it on (`DefaultEngine.ini`). Unreal
builds the two things *software* Lumen traces — a mesh distance field and Lumen
cards — only when it **cooks** a mesh, never for one built at runtime from a
file. Under software Lumen a model room is therefore invisible to global
illumination: it bounces nothing and the room renders black except where a
beam lands directly. Hardware ray tracing needs only ray-tracing geometry, which
runtime meshes do get; hit lighting (`r.Lumen.HardwareRayTracing.LightingMode=1`)
then lights each ray hit directly instead of from cards the model lacks.

Measured on an RTX 5070 Ti, despacio with every fixture lit at 1080p: 10.9 ms a
frame software, 12.5 ms hardware. On a GPU without ray tracing Lumen falls back
to software by itself — the drawn box room still bounces (it is made of cooked
shapes), but model walls go dark.

If machines without ray tracing ever matter, the fix is to have the models
**cooked**: a step that imports an event's models in the editor and packs them
for the app to mount at launch. It is not built; it would need Unreal on the
machine preparing the event, and minutes of cooking per model change.

## Offline: a scene with no engine

The app normally asks the running engine for its scene. To take a room
somewhere the engine is not:

```bash
python -m engine.scene events/<name> -o scene.json --models models/
previz/dist/Windows/KLightsPreviz.exe -Scene=scene.json -ModelCache=models/
```

DMX still arrives by Art-Net, from anything that sends it.

## Re-sweeping a room's optics

Fog, beam gains and wall reflectance live in the venue file's `previz.optics`,
merged key by key over the defaults in [`engine/scene.py`](../engine/scene.py).
Every default was eyeballed against one photograph of despacio, which is honest
for despacio and nothing at all for a second room. To tune a new one:

1. Run the engine with a look that holds one head on the ball, and the app.
2. Take stills of the `overview` and `audience` views:
   `KLightsPreviz.exe -Snapshot=overview.png -View=overview`.
3. `beam_gain` until a shaft reads as bright as the spot it lands on; then
   `dot_gain` until the landing spots are not blown white discs.
4. `beam_extinction_per_m` until a beam crossing the whole room fades about as
   much as it does in the real room.
5. `ray_gain` last — it changes most with room size, because the ball's shafts
   are drawn over a distance the room decides.
6. Compare against a photo of the real room at the same look. If there is no
   photo, say so in a `_comment` beside the numbers rather than implying they
   were measured.

The engine reads the venue file when it loads the rig, so after editing it
either restart the engine or apply a patch from the console (which reloads the
rig); the app picks up the new scene within a second of that. A model file
re-exported on disk is picked up without either — its content hash changes.
