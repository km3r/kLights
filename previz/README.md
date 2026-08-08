# Previz — the show engine, rendered in Unreal

Previz is a **listener**. The engine broadcasts Art-Net; this watches it. Nothing
here sits between the engine and the rig, so previz cannot break a show, and
F10 was always safe to build alongside everything else.

```
  engine.runner ──Art-Net UDP 6454──┬──▶ the rig
                                    └──▶ Unreal (cosmos_live.py)
```

## Running it

Needs Unreal Engine **5.8** (5.8.1 is what this was built against).

**1.** Open `previz/unreal/CosmosPrevis.uproject` in the editor and leave it
sitting on the Previz level. **Do not press Play.**

**2.** Build the level and start the driver — one command, safe to re-run any
time:

```bash
python previz/ue_remote.py previz/unreal/Content/Python/go.py
```

**3.** Send it some DMX from a second terminal:

```bash
python previz/ball_check.py --artnet 127.0.0.1 --seconds 300
```

The beams move in the ordinary editor viewport. That is the whole loop.

> **Play is not part of it.** The driver runs on the editor's tick, so the
> previz is already live without Play — and Play puts the editor into a mode
> where you cannot move the camera freely or edit anything. It does work (the
> driver follows the world into Play-in-Editor and back), but there is no
> reason to use it.

Re-run `go.py` after editing `venue.json` / `rig.json` / `calibration.json`, or
after editing anything under `Content/Python`. It reloads the modules, so your
edit really does take effect.

### Making the viewport look like the stills

Two viewport settings, both in the **level viewport's own toolbar** — the level
cannot reach either, which is why they are here and not in `build_level.py`:

1. **Exposure → tick "Game Settings"** (the ☀ dropdown). Unticked, the viewport
   applies its own exposure and *auto-adapts*, which is exactly what a dark room
   defeats: point a camera at black walls and it winds the gain up until they
   are grey, taking the show's contrast with it. Ticked, it obeys the level's
   `PZ_Look` volume, which pins exposure so a still is comparable with what you
   are looking at — and with the still you took last week.
2. **Press `G` for Game View.** Hides the editor's own furniture: light
   billboards, the grid, selection outlines, and the crowd-zone wireframe.

The stills are captured through the same pinned exposure, so with those two set
the viewport and `renders/` agree.

If nothing happens, in order: is the editor open (`python previz/ue_remote.py
--ping`), is a sender running, and what does the driver say?

```bash
python previz/ue_remote.py -c "import cosmos_live; cosmos_live.status()"
```

Only **one sender at a time** — two both emitting to 6454 means the previz
samples whichever packet landed last, which looks like stuttering.

The host-side half runs with no Unreal at all, and is worth reading when
something looks wrong in 3D:

```bash
python previz/scene.py despacio -o scene.json
```

Anything that emits Art-Net will drive it:

```bash
python -m engine.demo --artnet 127.0.0.1 --seconds 300
```

To look at the mirror ball specifically, hold every head on it — `engine.demo`
orbits *around* the ball at 15–40°, so its reflections are correctly absent
almost the whole time, which looks exactly like a broken ball:

```bash
python previz/ball_check.py --artnet 127.0.0.1 --seconds 300
```

Stills go to `renders/` (gitignored) — an overview, a corner, what the audience
sees, and a close-up of the ball, which is the only view that can settle a
question about the tiling:

```bash
python previz/ue_remote.py previz/unreal/Content/Python/snapshot.py
```

Other useful one-liners:

```bash
python previz/ue_remote.py -c "import cosmos_live; cosmos_live.status()"
```

```bash
python previz/ue_remote.py -c "import cosmos_live; cosmos_live.stop()"
```

## The mirror ball

`previz/mirrorball.py` computes **real reflections**: facet normals for a
UV-sphere ball, a test for which facets the beam cone actually lights, the
mirror law `d - 2(d·n)n`, and an analytic ray/room-box intersection for where
each reflection lands. It has its own self-test, including the reflection law
itself — a sign slip there would still spray a plausible-looking pattern of
dots, just the wrong ones, which is the one failure previz would not survive.
Doing the landing analytically rather than as engine line traces is what makes
a couple of hundred reflections a frame affordable.

**The beam does not teleport.** It strikes the ball, terminates there, and
bursts into one thin shaft per lit facet, each drawn through the haze to the
dot it makes. Drawing only the dots leaves them hanging on the walls with
nothing to explain them, and makes it look as though the beam went *through*
the ball.

**Dots are a few centimetres, not half a metre**, and getting that right needed
the physics rather than a fudge factor. It is tempting to spread a reflection
at the fixture's own 3° beam angle, but that is the *aggregate* cone of the
whole beam, not the divergence of the rays arriving anywhere in it. One facet
is a flat mirror a couple of centimetres across; the bundle it intercepts comes
from the lens, so it diverges at the lens's angular size from there —
`aperture / distance` — and keeps that divergence after bouncing. Using 3°
instead made every dot about seven times too wide. `mirrorball.APERTURE_MM` is
40 mm, and it is the one number here that is neither measured nor derived from
config.

Dots are drawn as flat tiles lying **on** the surface they land on, which is
why `ray_room_exit` returns a normal. As spheres centred on the landing point
they are half buried, and near a room edge the neighbouring wall hides most of
what is left — so they wink out and back as they sweep into a corner.

Three dials:

- `mirrorball.SEGMENTS, RINGS` — the facets light actually reflects off. 24×16
  gives 384, about 43 lit per head. Every one costs a shaft and a dot drawn
  every frame, so this is the one with a price.
- `mirrorball.TILE_SEGMENTS, TILE_RINGS` — the mirrors the ball *wears*, 48×32.
  Finer, because a tile is drawn once and then costs nothing. So the ball has
  its real mirrors while the light bouncing off it is computed on one in four
  of them. That is a sampling approximation and worth naming: the dots that are
  drawn land exactly where they belong, there are simply fewer than the real
  ball throws. A 400 mm ball is 4° wide from anywhere a person stands, so
  nobody can tell which mirror a beam came off — a ball visibly wearing 36 mm
  panels, everyone can tell.
- `mirrorball.DEFAULT_RPM` — the ball spins, because a still one reads as a
  bug. `venue.json` says nothing about the motor, so 2 rpm is an assumption.

The tiles are **flat planes, and fully metallic**. Both were arrived at the
hard way. Thin boxes have four side faces standing perpendicular to the ball
which catch grazing light the mirror faces do not, so the ball renders as a
bright wireframe globe with dark panels between the wires. And any diffuse
response at all — even `metallic 0.35` — has two 1300-lumen beams at five
metres render the whole lit hemisphere as one white blob, which is precisely
the opposite of a mirror ball: a mirror is dark except where it happens to be
aimed at you, and the few tiles that are, are blinding.

Costs about 3.8 ms of editor frame time for four heads, and 0.5 ms with every
head off the ball. `status()` reports it, and the dot sizes.

## Three decisions worth not relearning

**Unreal's DMX plugin is deliberately not used.** It would patch each fixture
from a GDTF profile and articulate pan/tilt by that profile's convention. Our
heads are mounted *sideways*: Pan carries elevation, Tilt carries bearing, and
each head's true mount facing is backed out of one hand-aimed mirror-ball
reading. Unreal's fixture model cannot express that, so a GDTF-driven previz
would articulate confidently and wrongly — and would keep doing so after every
recalibration. Instead `cosmos_live.py` reads Art-Net itself and decodes with
**`engine.geometry`, the show's own decoder**. Previz and show cannot drift
because they are the same code. (`previz/scene.py`'s self-test additionally
checks the Unreal frame conversion against `geometry.ray()`, which is what the
F4 safety taper casts.)

**There is no Unreal MCP server.** The F10 plan expected UE 5.8 to ship one at
`127.0.0.1:8000/mcp`. What 5.8.1 actually ships is `MCPClientToolset` — an
adapter letting the editor's *own* assistant dial out to MCP servers. There is
nothing to attach to. `ue_remote.py` uses `PythonScriptPlugin`'s remote
execution instead, which is first-party, stable, and does the same job.

**Beams are drawn twice: an additive cone mesh for the core, volumetric fog for
the glow.** The mesh is traced to where the beam actually lands, so it is the
crisp, geometrically honest part; the fog is the atmosphere it hangs in. Either
alone is worse — the mesh by itself looks like a decal floating in a vacuum, and
the fog by itself is far too soft to judge where a 3-degree beam is pointing.

Three things had to be true for the mirror ball to *stop* a beam, and all three
were wrong at first in ways that looked like the beam passing through it:

- **`cast_volumetric_shadow` is False by default on a spawned SpotLight, and
  `cast_shadows = True` does not imply it.** So the mesh terminated on the ball
  exactly right while the light's fog sailed straight on.
- **The mesh is traced down a single axis line; the light is a real cone.** Aim
  a head so its axis clips the ball's edge and the trace says "blocked" — the
  shaft vanishes at the ball while most of the cone gets past and lights the far
  wall, so the beam appears to stop dead in front of a wall it is visibly still
  lighting. `Live._ball_clearance` now works out what fraction of the cone
  clears the ball, as the overlap of two discs seen from the lens, and the shaft
  is drawn through and dimmed by that fraction instead. One brightness has to
  serve the whole shaft, so the stub between lens and ball comes out dimmer than
  it really is; the alternative is two meshes per beam.
- **A 3-degree spot light's shadow map is too coarse to hold the ball's shadow
  at all.** With both of the above fixed, the mesh stopped on the ball and the
  fog stopped on the ball — and a bright spot still landed on the wall behind
  it, exactly where the beam would have gone. Unreal sizes a local light's
  shadow map from the light's screen footprint, and at three degrees the ball's
  shadow falls below the sampling and disappears. Setting the light's
  `ShadowResolutionScale` to 2.0 (the engine's own ceiling) fixes it.

  Measured, on one head aimed at the ball with everything else dark:

  | | wall behind the ball |
  |---|---|
  | stock (`ShadowResolutionScale` 1.0) | lit |
  | `ShadowResolutionScale` 2.0 | dark, and the ball's shadow is crisp |
  | cone widened to 20° at 1.0 | dark — so it is the *cone*, not the ball |
  | cone narrowed to 1.2° at 1.0 | beam sails straight through, fog and all |
  | ball radius tripled at 1.0 | no change — not the caster's size either |
  | `r.Shadow.TexelsPerPixel 8`, `MinResolution 512` | **worse** |
  | `r.Shadow.Virtual.Enable 0` | no change — both shadow paths leak |

  If a future rig has a beam tighter than 3°, expect this back, and note there
  is no headroom left in the property.

Check any of these with **one head lit and the rest switched off**. Four
heads aimed inward from four corners means a beam arriving from the far side is
indistinguishable from one passing through, and it is very easy to convince
yourself of the wrong answer.

## Why the room glows

Compared against a photo of the real night, the first version was a black box
with bright tubes in it. Three things were wrong, and the obvious fix was not
one of them.

**Raising the fog density does not fill the room.** Unreal's volumetric fog is
*single* scatter: a point in the air glows only if a light shines on it
directly, and four 3-degree cones light almost none of a 9 m room. Swept from
0.06 to 3.0 — the beams get brighter and brighter against a room that stays
exactly as black. Density is set to 0.35, which is enough to carry a beam
without swallowing what is behind it, and that is all it does.

**The room was blacker than any real venue.** At 0.05 albedo there was nothing
for a beam to bounce off. It is 0.16 now with only a token emissive, so Lumen
carries beam light into the room — and unlike ambient, it is still black when
the fixtures are dark.

**The ball emitted nothing.** This is the big one. Its reflections are additive
meshes, so in the one pose the whole show is built around — every head on the
ball — four beams terminated on a 60 cm sphere and lit precisely nothing. A real
mirror ball takes a beam and sprays it over the entire room, and that spray is
most of why a room with a ball in it glows. Each head now has a `_BallGlow`
point light at the ball, driven by `level × (1 − clearance)` — the share of that
beam the ball actually intercepts — so it rises as a head sweeps on, and goes
out when it leaves. `BALL_GLOW_FRACTION` (140/1300 of the fixture's own output)
and `BALL_GLOW_SCATTER` (1.0) were swept against the photo; much above 1.0 on
the scatter and the room flattens into an even field with no corners left,
which is the failure that looks like cheating. It is a *fraction* rather than a
flat lumen count so that a 48 lm pinspot cannot wash the room the way a 1300 lm
beam can.

## Beams lose light as they travel

A shaft of constant brightness is the one thing haze cannot produce: the same
scattering that dims a beam is what makes it visible at all, so a beam you can
*see* is by definition one that is losing light. `M_PrevizBeam` computes
`Falloff ^ (travelled / Reach)`, with `Falloff = exp(−BEAM_EXTINCTION_PER_M ×
metres)` — which expands to `exp(−k × travelled)`, real extinction at any
`Reach`, with nothing left to tune per length.

It was a **lerp** from 1 to `Falloff` until 2026-08-07, which is the same thing
only near the origin. The error grows with `Reach`, and the room doubling to an
18 m box was what exposed it: a shaft 5 m out came back at 0.83 instead of 0.64,
and multiplied by the several hundred additive shafts a mirror ball throws, the
whole room washed out to an even blue. If a previz suddenly reads flat and
bright, suspect an approximation that was fine at the old scale.

`RAY_GAIN` went 0.30 → **2.5** at the same time, and that is not a fudge to undo
the fix. A reflected shaft is drawn only as wide as the dot it ends on, a few
centimetres, so across an 18 m room it is a sub-pixel hairline over most of its
length and what you see is whatever survives being averaged with the black
behind it. Doubling the room roughly doubled that length. Swept at the new size:
0.3 and 1.1 are a bare shimmer, 4.5 turns the far wall into a haze of rays that
competes with the dots, 2.5 fills the room while the dots still lead.

Measured from the beam's own start rather than from the mesh's local Z, which
is what lets one material do both jobs: for a head's shaft `Origin` is the lens,
and for the mirror ball's spray `Origin` is the ball — where all several hundred
reflected shafts genuinely do begin, so one shared parameter fades all of them
correctly at once. A per-instance local coordinate could not have.

`BEAM_EXTINCTION_PER_M` is 0.09 (about 40% left at 10 m). Swept against the
photo: at 0.04 the ball's reflected shafts stay bright right across the room and
the depth flattens out; at 0.20 they die two-thirds of the way and leave dots
hanging on the far walls with nothing reaching them.

## How bright one fixture is against another

The stand-in meshes — beam shafts, the ball's reflected shafts, its dots — are
scaled by the fixture's **candela**, lumens over the solid angle of its cone,
not by lumens. Without that, adding the pinspots drew two great grey cones
across the room as bright as the 1300 lm beams.

But the ratio that comes out of it is **not trustworthy, and the previz does not
trust it.** Both figures are the `.qxf`'s Bulb Lumens, which is what the emitter
makes, not what leaves the lens: a 3° beam throws away most of its bulb at the
aperture and a wide fresnel passes most of its own. Taken straight, that says
the pinspots are 360× fainter than the heads, which is not what they look like
in the room.

Two levers, in order of preference:

1. **`lumens` in `rig.json`**, per fixture, overriding the profile — see
   `PatchedFixture.output_lumens`. This is where a *measured* output belongs,
   and it is the only one of the two that makes the ratio actually right.
2. **`MESH_CONTRAST`** (0.22), which compresses whatever ratio it is given:
   `ratio ** MESH_CONTRAST`. It exists because the previz also pins its
   exposure, deliberately — and that removes the thing that makes a quiet
   fixture visible in a real dark room, which is your eye adapting to it once
   the loud ones go out. Swept with everything lit: 0.35 leaves the pinspots'
   spray a faint wash, 0.12 puts it level with the heads and the beams stop
   being the brightest thing in the room, 0.22 fills the room with pinspot dots
   while the heads still clearly lead.

The compression is applied to the **meshes only**. Spot light intensity, the
ball glow and everything the renderer actually tone-maps stay in real lumens;
the stand-ins are in no physical unit to begin with — `BEAM_GAIN`, `DOT_GAIN`
and `RAY_GAIN` were all eyeballed.

Getting the fog usable took a measured sweep (`r.VolumetricFog.*`, below),
because at stock settings it renders a beam as a row of blocks. Three findings,
all the opposite of the obvious guess:

- **Spend the froxel budget on depth, not on screen.** The grid is screen tiles
  by depth slice, and the beading that makes a beam look like a string of beads
  comes from the *depth* axis — which is the opposite of the obvious reading,
  and of what this file used to say. Measured on a single-beam shot with three
  heads switched off, so there was nothing to misattribute:
  `(GridPixelSize 2, GridSizeZ 128)` beads badly, `(2, 512)` is clean, and
  `(4, 512)` is just as clean at the same total froxel count as `(2, 128)`.
  So `(4, 512)` is what both `apply_render_cvars` and `DefaultEngine.ini` use.
  Dropping the pixel size back to 2 is a small further gain for 4× the fill
  rate. There is *no* upper clamp and no budget fallback in the renderer —
  `GetVolumetricFogGridPixelSize()` is literally `FMath::Max(1, cvar)` — so
  both are honoured as written.
- **`VolumetricFogStartDistance` is the sleeper setting.** Slices span
  `max(camera near clip, start) .. distance` (`VolumetricFog.cpp:1369`), so with
  the default start of 0 almost every slice is spent in the first metre of empty
  air in front of the lens. Pushing the start out to just before the room packs
  them all where the beams are. But it is measured **from the camera**, so it
  belongs to the view, not the level: 500 cm smooths a camera standing 6 m
  outside the room and erases the fog from every beam within 5 m of one standing
  inside it. `snapshot.py` sets it per view; the level default is 0.
- **Do not touch `DepthDistributionScale`.** Lowering it to spread slices evenly
  over a small room is the obvious move and it is wrong: at 8 the beam is
  measurably beadier than at the stock 32, and at 1 the far half of the room
  renders as a solid black rectangle.

## What the level is made of

Everything is tagged `cosmos_previz` and rebuilt from scratch by
`build_level.py`, so **the level is a generated artefact** — change
`venue.json` / `rig.json` / `calibration.json` and re-run, rather than dragging
things around in the viewport. Untagged actors (a camera you parked somewhere
useful) survive a rebuild.

| Actor | What it is for |
|---|---|
| `PZ_Fixture_<id>` | A spot light at the head's calibrated position, aimed at its **rest** pose — the mirror ball. A freshly built level shows every head on the ball before any DMX arrives, which is the cheapest check that `calibration.json` is sane. |
| `PZ_Fixture_<id>_Beam` | The visible shaft. Stretched, aimed and coloured every frame. |
| `PZ_Fixture_<id>_Reflections` | Two instanced meshes — the shafts leaving the ball and the dots they land on. One batched transform write each per frame, rather than one actor write per reflection; that is what lets the facet count be a dial rather than a budget. A wide fixture draws every Nth facet by lattice index, sized so it stays under `REFLECT_BUDGET` (220) — density drops, coverage does not, and the drawn subset is the same every frame. |
| `PZ_Fixture_<id>_BallGlow` | A point light at the ball carrying what that head's beam puts into it. Off unless the beam is actually on the ball. |

Every placed fixture — mover or pinspot — gets all four of the above. They are
tagged `unit:<key>` / `beam:<key>` / `glow:<key>` / `reflect:<key>` with one key
per fixture: `h<head index>` for something steerable and `f<fixture id>` for
anything else. The `h`/`f` prefix is not decoration: head 0 and fixture id 0 are
different objects, and a pinspot holds **no head index** at all (see below).
| `PZ_MirrorBall` | A solid shadow-casting sphere — the same occluder the safety taper models. Beams terminate on it. Near-black, because it is the grout between the mirrors. |
| `PZ_MirrorBall_Tiles` | The mirrors, one instance per tile, each sized to the patch of sphere it covers. Sizing matters: a UV sphere's rings narrow toward the poles, so uniformly square tiles give even grout at the equator and a solid overlapping cap at each end. |
| `PZ_CrowdHeadBand` | A wireframe box over the crowd footprint at 1.4–2.0 m. Editor-only annotation; renders nothing. |
| `PZ_Look` | An unbound post-process component pinning exposure and bloom, so the viewport, Play and `snapshot.py` agree. A `PostProcessVolume` would be the obvious choice and cannot be used: it is a brush actor, and `spawn_actor_from_class` returns None for one. |
| `PZ_Canopy` | The parachute, when `venue.json` has one enabled. |

### A position does not make a geometry head

Placing the pinspots meant giving them a `position` in `rig.json`, and until now
`load_rig` treated any positioned fixture as a **geometry head**. That list is
the calibration's index: every entry needs a Pan/Tilt reading measured on the
night, `validate()` insists the movers and the heads line up one for one, and
head indices are positional. A positioned pinspot would have taken index 4,
shifted nothing (it sorts last) — but a pinspot patched *before* a mover would
silently re-point the show.

So `load_rig` now builds a head only for a fixture that has both Pan and Tilt in
its mode, which is the only honest source for "does it move". Positions are
recorded on every fixture either way (`PatchedFixture.position`), because previz
needs a static fixture's position to draw it and the taper needs a mover's to
aim it, and those are the same fact. A fixture placed but not steerable takes
its aim straight from the geometry — no calibration in the path, since there is
no servo and nothing to drift.

## Play in the editor

You do not need Play — see the runbook. It is supported anyway, and two things
had to be fixed to make it so; both are worth knowing because they bite the same
way.

**Nothing can be *built* during Play.** Not the actor subsystem, not the level
subsystem, and not the asset registry: `list_assets('/Game')` comes back **empty**
and `does_asset_exist` returns False for a map sitting on disk. Every call logs
`The Editor is currently in a play mode` and hands Python None. `go.py` now
refuses up front, because without that the build cleared the level and then died
on `'NoneType' has no attribute 'set_actor_label'` — and the honest reading of
that wreckage is "the rebuild deleted my map", which is wrong and sends you
looking in exactly the wrong place. Press Stop and re-run.

**Play duplicates the level into a separate PIE world.** The actors on screen
from that moment are the duplicates, so a driver holding editor-world actors
goes on updating things nobody is looking at and the beams freeze exactly where
they were. The driver re-resolves against whichever world is active on every
tick; `status()` reports the world and the rebind count.

**`EditorAssetLibrary` lookups do not resolve during Play.** `does_asset_exist`
returns False for an asset that plainly exists, and `load_asset` returns None.
Assigning that None to a mesh substitutes Unreal's DefaultMaterial — opaque and
unlit — so the reflection dots turned into black spheres the instant Play was
pressed. Nothing in the driver looks a material up by path any more; it
instances from the material already on the mesh (`_shared_material`).

## Known gaps

- **The ball's size is unmeasured, and it is a safety number.** `venue.json`
  says `ball_radius: 300`, set by hand on 2026-08-07 over an estimated 200. The
  taper treats the ball as an occluder, so a *bigger* ball means it believes
  more beams are blocked and dims less — the direction that errs toward not
  dimming. Measure the real one; previz follows, since it reads the same file.
- **`APERTURE_MM` is a guess** — 40 mm for the lens of a small 60 W fixture. It
  sets how big every mirror-ball dot is and nothing else.
- **The pinspots' placement is stated, not measured.** Same plane as the movers
  (y 2971), centred on two opposing sides of the truss, inset by the same 500 mm
  they are — recorded 2026-08-07. If "north/south" meant the *other* pair, swap
  x and z in both pinspots' `position` in `rig.json`; nothing else changes.
- **The room's new size is stated, not measured.** As of 2026-08-07 it is twice
  as long and twice as wide with a 6.9 m ceiling, and the rig hangs on a
  free-standing 9.144 m truss frame in the middle of it rather than on the
  walls — `venue.json`'s `truss` block. The room is assumed to have grown
  *symmetrically* about the rig; if the truss is really off-centre, move the rig
  rather than the room, because every coordinate in `rig.json` is measured from
  that frame. The bar is drawn as a 60 mm pole, which is a drawing choice and
  nothing else depends on it.
- **No fixture's real output is measured.** Everything comes from the `.qxf`'s
  Bulb Lumens — 1300 for a head, 48 for a pinspot — and that is emitter output,
  not what clears the lens, so the ratio between a narrow and a wide fixture is
  overstated by an unknown amount. `MESH_CONTRAST` papers over it; a measured
  number in `rig.json` would fix it. Most `.qxf` files in the wild declare 0,
  which falls back to `scene.UNDECLARED_LUMENS` (400).
- **A wide fixture's reflections are sampled, not drawn in full.** An 11°
  pinspot overshoots a 60 cm ball at 4 m and lights its whole near cap — about
  441 facets, against a 3° head's 35 — and drawing every one for both pinspots
  put the tick at 15.9 ms against a baseline of 4. Each fixture gets a **facet
  stride**, worked out once from its beam angle and its fixed distance to the
  ball (`mirrorball.lit_facets`) so `lit / REFLECT_BUDGET` facets are drawn:
  1 for the heads, 3 for the pinspots. Same kind of sampling `REFLECT_SPACING_MM`
  already is (the ball wears 3698 mirrors and reflects off 882), one step
  further and only where it is needed. `status()` says when it bites.

  It skips by **lattice index**, not by position in the hit list, and that
  distinction is the whole thing. Thinning the *results* — which is what this
  replaced — re-picks a different subset whenever the hit count changes by one,
  so several hundred dots each jump to a neighbouring facet every few frames and
  the spray reads as a crawling moire rolling across the room. Skipping by index
  picks the same mirrors every frame; they enter and leave the beam as the ball
  turns, the way real ones do.

## Dots have a soft edge, and beam angle comes from the rig

A reflection dot is a flat emissive tile, and a hard-edged constant rectangle is
the one thing light landing on a wall never looks like — nothing in the real
path has a hard edge, since the mirror is small, the lens has an aperture and
the air between is hazy. `M_PrevizDot` now multiplies by `(1 − r) ** DOT_SHOULDER`
across its own UVs, `r` measured from the tile's centre.

Keyed off the mesh's UVs, **not** Fresnel. Fresnel keys off the angle to the
camera, so it dims exactly the floor and ceiling dots you most want to see —
that is why `round_off` is still off for dots and on for beam shafts.

The falloff costs brightness: its mean over the quad is roughly a third, so
`Dot.spot` is now the width at which a dot fades to nothing rather than the
width of a solid patch. `DOT_GAIN` went 1.6 → 2.3 to carry that; it is
compensation, not a re-tune.

`rig.json` now takes **`beam_deg`** per fixture, overriding the `.qxf`'s cone
angle, the same way `lumens` overrides Bulb Lumens. The despacio movers are set
to 8° against a profile claiming 3° — stated 2026-08-08, not measured. This one
is **not** cosmetic: `safety.clearance` sizes the beam's half-width at range
from it, so a wider number makes the crowd taper dim earlier. That is the
conservative direction, and it is in the rig file where it is visible rather
than buried in a shared profile.

Two knock-ons worth knowing, because they are what makes this dial expensive.
An 8° cone at 5.5 m paints a 77 cm disc on a 60 cm ball, so every head now
overshoots and lights the whole near cap: reflections per head went 55 → 150,
the tick went 6.2 → 8.6 ms, and `RAY_GAIN` had to come *down* to hold the same
picture (total spray goes as count × gain). And the overshoot lands on the far
wall as a bright annulus with the ball's shadow punched through the middle —
that is not an artefact, it is the shadow work in `SHADOW_RESOLUTION_SCALE`
finally having something to show.

## The ball you see and the ball light bounces off are two sets

The drawn mirrors are a **UV lattice** (`facet_normals`, 3698 of them), because
that is what tiles a sphere mesh without gaps. Reflections come off
`reflect_normals` — 882 points on a **golden spiral**, evenly spread.

A UV lattice is wrong for reflecting in two compounding ways. Its facets are
rings of constant latitude and shrink toward the poles, so a wide source that
lights the whole near cap throws a dense clump at each pole and visible
concentric rings in between. And because the list is ring-major, taking every
Nth facet aliases against the segment count and bends those rings into a spiral
that crawls as the ball turns. Both were visible as "growing and shrinking rings
of spotlights" off the pinspots.

A golden-angle spiral has neither problem, and *subsampling one is still one* —
N times an irrational angle is still irrational — so a strided fixture gets a
sparser even spread rather than a pattern. `mirrorball`'s self-test measures it:
nearest-neighbour spacing against the mean, at strides 1, 3 and 7. The spiral
scores 0.92/0.81/0.87; the UV lattice it replaced scores 0.117 at stride 1.

  The cost is almost entirely **building Unreal's structs from Python** — 11.7 µs
  per reflection against 1.3 µs for all the optics behind it. Halved again by
  mutating three scratch structs instead of allocating six per reflection
  (`Transform` copies its inputs, so this is safe; verified). Tick is 7 ms with
  every fixture on the ball.
- **Reflected shafts are mesh only**, with no volumetric fog of their own — a
  hundred extra lights is not affordable and Unreal would not thank you for it.
  They read as thin bright tubes rather than glowing in the haze the way the
  four main beams do.
- **The main beams bead** at some camera distances: the volumetric fog resolves
  them as a row of blobs rather than a continuous shaft. `snapshot.py` fights it
  with 24 accumulation passes and a per-view `fog_start`, and the froxel notes
  above are the state of the art here, but it is not solved.
- **Colour on the movers is a wheel lookup, not mixing.** That is correct — the
  MJ-OS-018 has 14 mechanical slots and no colour mixing — but it means a
  requested colour snaps to its nearest slot, exactly as the real fixture does.
- **Strobe is ignored.** The channel is read but nothing acts on it.
- The editor tick is not locked to the engine's 40 fps, so the previz samples
  whatever the latest frame is. Fine for judging looks; not a timing reference.
