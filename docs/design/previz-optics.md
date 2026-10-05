# Previz optics and modelling notes

Why the previz looks the way it does: what was measured, what was tried
and rejected, and which numbers are eyeballed rather than derived. Moved
out of [`previz/README.md`](../../previz/README.md) so that stays a guide
to *running* it.

These are working notes, kept verbatim. Where a number here disagrees
with a venue's `previz.optics` block (or the defaults in
[`engine/scene.py`](../../engine/scene.py)), the config wins -- it is what
the code reads.

---

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
recalibration. Instead `klights_live.py` reads Art-Net itself and decodes with
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


## The heads take time to get there

DMX is a *command*, not a position. A real yoke needs the better part of a
second to cross the room, and until 2026-08-08 the previz drew the command:
change routine and the beams were simply somewhere else on the next frame.

That hid the two things a previz is best placed to show. One is what a routine
change actually costs — four lit beams dragged across every face in the room on
the way to the new pose, which is the thing you either accept or hide behind a
blackout. The other is every **dark move**, whose travel time *is* the effect;
with a teleporting previz, Teleport and an ordinary lit sweep render identically.

`engine.servo` rate-limits each head toward the commanded position and
`klights_live` decodes where the yoke has *got to* rather than where it was told
to be. The decode itself is untouched, so previz and show still share one
geometry.

- **The limit is applied in raw channel space**, not to bearing and elevation.
  That is what the motors turn, it is the only frame in which the two axes have
  independent speeds, and it means the model needs to know nothing about the
  mount — which matters here, where the heads are bolted sideways and Pan
  carries elevation. Both axes run at once, so a diagonal finishes when the
  slower one arrives.
- **The speeds are assumed**, and they are the number here worth being
  suspicious of: 216 °/s pan and 180 °/s tilt, the usual published figures for
  this class of 60 W beam. No `.qxf` can declare a slew rate and nobody has
  timed ours. `rig.json` overrides per unit with `pan_speed_deg_s` /
  `tilt_speed_deg_s`, the same lever `lumens` and `beam_deg` have.
- **Acceleration is deliberately not modelled.** A real yoke ramps up and
  brakes, so this arrives slightly early on a long move; correcting it would
  mean inventing a second unmeasured number to fix an error smaller than the one
  already in the first.
- **Stills settle first.** `snapshot.py` finishes every move before the shutter
  opens, because a still asks what a look *looks like*, not what it looks like
  partway through the travel — otherwise the answer would depend on how long ago
  the frame was written. A live previz is unaffected; it resumes following on
  the next tick. Anything else that writes a frame and photographs it should
  call `state.settle()`, which replaces the old advice to call `tick(0.0)`:
  with a mechanical model in the path, a zero-length tick no longer moves
  anything.
- `status()` prints each head as `pan=<told> (at <achieved>)` and names any head
  still in flight.

**What this immediately exposed.** The Dark Moves were authored against the real
rig's timing, and at the reference tempo two of them do not allow enough travel
for the widest hop: Teleport's swing out to the walls is 180° of bearing, which
needs about 1.0 s and is given 774 ms, and Freeze Frame's is given 871 ms. The
dimmer returns on a head still swinging, so the last fifth of the "teleport" is
a short lit sweep. `engine.servo.cue_margins` computes this for any chase at any
tempo and `engine/tests/test_library.py` prints the table. It gets worse as the
tempo goes up: everything is on a bar count now, so a faster show shortens every
travel while the heads stay exactly as fast as they were.


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

Everything is tagged `klights_previz` and rebuilt from scratch by
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


## Split ("duo") colour-wheel positions

A colour wheel is a disc of coloured segments, and the positions **between** two
of them put half of one and half of the next in front of the lens. The beam
comes out two-toned across its width — green above, blue below — not blended.
The MingJie wheel declares seven of these (values 80–139, "Cyan + Pink" through
"Yellow + Red"), which is half the colours it has.

Previz drew every one of them as a single muddy average until 2026-08-08. Three
things had to change, and the third is the one that actually mattered:

1. **`engine.rig` now resolves the pair.** The `.qxf` records a split slot as
   one approximate tint (`#80ff80` for "Green + Blue"), which is fine for the
   engine's nearest-slot colour matching and useless for drawing one.
   `_resolve_split_slots` reads the label and looks the two names up among *that
   same channel's* single-colour slots, so "Green + Blue" resolves to the exact
   `#00ff00` and `#0000ff` the profile already declares. No colour-name table,
   no guessing; an unresolvable name simply leaves `Capability.pair` None and
   the slot behaves as before. QLC+'s own `Res2` attribute is honoured first.
2. **The shaft mesh splits.** `M_PrevizBeam` lerps `Color` → `ColorB` across a
   world-space plane through the beam's own axis, handed over as `SplitNormal`.
   World-space and not the mesh's UVs, for the same reason the taper measures
   from `Origin`: the shaft is a stretched cone whose local frame twists with
   its aim, so a local split would roll as the head moved.
3. **The fixture gets a second spot light** — and this is the part without which
   the other two are invisible. An Unreal spot light has exactly one colour, and
   its volumetric fog is what makes a beam read as a beam at all. Splitting only
   the mesh left the fog a single average and the beam still looked one colour.
   So a split fixture carries two lights at half intensity each, tipped a
   quarter of the cone apart in elevation, one per half of the aperture. They
   overlap down the middle as the real halves do. Built only where the profile
   has split slots (`_has_split_slot`), because a shadow-casting spot light is
   not free.

**The ball's spray is drawn in the averaged colour**, deliberately. Each facet
really does reflect whichever half struck it, but every reflection of one
fixture shares a single material instance — that sharing is what makes a couple
of hundred of them affordable — so per-facet colour would need per-instance data
and a per-instance write. A mirror ball's spray genuinely is a mix of both
halves, so this reads acceptably; the *renderer's* own specular off the ball
tiles does show both, since that comes from the two real lights.


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
