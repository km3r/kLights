# Safety: what the beam taper does, and what it does not

This rig points 60 W beams at a room with people in it. One part of the software
exists specifically to make that less dangerous — the **beam-aware intensity
taper** in [`engine/safety.py`](../engine/safety.py). This page states plainly
what it covers, what it does not, and which of its inputs are guesses.

Read it before running a show, and again before letting anyone else run one.

---

## The one-sentence version

The taper knows where every moving head's beam **lands**, per frame, and dims a
beam whose cone enters the crowd's head band — **to half power, not to off**.

It is a **glare and comfort guard**. It is **not** an optical-safety guarantee,
and it has never been validated against any photobiological standard.

---

## What it does

Evaluated every frame from the aim the fixture is *actually* being sent, after
the entire layer stack, in `state.evaluate()`. Nothing a look does can outrank
it — `apply_safety` is deliberately not a layer, so it cannot be reordered.

The geometry, in order (`safety.clearance`):

1. Cast the beam axis, clip it to the room. Past a wall the beam is gone.
2. If the mirror ball blocks it first, stop — a mirror ball is a solid sphere
   and genuinely occludes.
3. Find where what remains crosses the head band. No crossing → full intensity.
4. Measure how far that crossing passes from the crowd footprint, in the band
   plane, against the beam's own radius at that range. Inside → taper.

The property that matters is **per frame from the current aim**. A scene-based
console can at best vet its stored poses; it structurally cannot cover the
transit between them, and transit is where most of the damage happened on the
night this was written for. Here a move that passes through the danger band dims
on the way in and comes back up on the way out, with nobody having authored it.

Movement is rate-limited in both directions (`slew_per_second`, default 2.0) so
the change reads as a dip rather than a flicker.

---

## What it does NOT do

Every item here is a real gap, not a hypothetical one.

| Gap | Detail |
|---|---|
| **It does not turn beams off** | `crowd_level` defaults to **0.5**. A beam aimed directly into someone's eye still emits at **half power**. This was a deliberate choice (2026-08-06): the goal is "not blinding", not "never lands on anyone", because every floor-sweep pose necessarily crosses eye height on the way down. |
| **It does not protect anyone outside the crowd box** | The crowd is modelled as one axis-aligned footprint × a head band. Someone at the bar, in a doorway, on the stage, or standing outside `crowd_zone` is invisible to it. |
| **It does not taper fixtures that cannot aim** | `state.py:282` skips any fixture with no geometry head. The pinspots are hand-aimed and **entirely the operator's responsibility**. |
| **It does not model mirror-ball reflections as hazards** | The ball *occludes* (which reduces dimming), but the hundreds of small beams it throws back into the room are not modelled at all. |
| **It does not model any other reflective surface** | No mirrors, glass, glossy walls, or wet floors. |
| **It has no radiometry** | No irradiance, no exposure time, no IEC 62471 / EN 62471 assessment. The test is purely geometric: does the cone enter a box. |
| **It has no photosensitivity guard** | There is no frequency limit on strobe. Strobe is reachable through ported looks and through auto mode's energy axis, and nothing caps its rate. |
| **`jog` bypasses it completely** | By design — you cannot calibrate a head through a guard that dims it. The UI raises a red banner while any head is jogging. Until F14 lands, **anyone on the venue network can send `jog`.** |
| **It assumes the config is true** | Every number below is taken on faith. A wrong fixture position or a wrong room size produces a confident, wrong answer. |

---

## The three inputs that are guesses

These are the taper's most load-bearing numbers and **none of them has been
measured**. Each is flagged in its own file; they are collected here so the list
exists in one place.

### 1. `beam_deg: 8` — `events/despacio/rig.json`

> "the .qxf profile claims 3 degrees and these read visibly wider than that in
> the room, so they are set to 8 — STATED 2026-08-08, not measured."

Sets the beam's half-width at range, i.e. how wide a swathe counts as dangerous.

- **Direction of error:** too small → the taper under-protects (thinks the beam
  is narrower than it is). Too large → over-dims. The current 8 is a widening of
  the profile's 3, so it errs *toward* protecting.
- **To retire it:** project one head onto a wall at a measured distance, measure
  the bright core's diameter, `2 * atan(radius / distance)`. Ten minutes with a
  tape measure.

### 2. `ball_radius: 300` — `events/despacio/venue.json`

> "errs toward NOT dimming — a bigger ball here means the taper believes more
> beams are blocked than really are."

- **Direction of error: this one errs the unsafe way.** Overstating the ball
  makes the taper believe beams are occluded that are not, and it will not dim
  them. This is the most important of the three.
- **To retire it:** measure the ball. One minute.

### 3. `head_band_min/max: 1400–2000` — `events/despacio/venue.json`

> "roughly seated-tall to standing-tall. It is a judgement call, not a
> measurement, and it is deliberately generous at the top — someone on someone's
> shoulders is exactly who gets hit."

- **Direction of error:** a band too narrow misses people. Deliberately generous
  at the top for exactly that reason.
- **To retire it:** it is a policy choice, not a measurable. Revisit it if the
  room gets a stage, a riser, or a bar people stand on.

---

## The `crowd_level` dial

In `venue.json` under `taper`, live-editable from the Setup tab.

| Value | Behaviour | Cost |
|---|---|---|
| `0.5` (default) | Beams over the crowd are held to half power. | A beam in an eye is still half power. |
| `0.0` | Hard guard — beams over the crowd go dark. | Kills every floor-sweep pose; a head aiming at the dancefloor crosses eye height on the way down and will blink out. |
| `1.0` | No taper. | None of this applies. Don't. |

**If you are running for a crowd you do not know, or anyone has been drinking
near the front, set it to 0.0 and lose the floor sweeps.**

`enabled: false` disables the taper entirely. The UI raises a notice when it is
off; the server prints the live value at startup.

---

## Operator responsibilities the software cannot take

- **Aim the pinspots by hand, above head height.** Nothing checks them.
- **Re-run calibration after any re-hang.** Every look's position is an offset
  from each head's calibrated ball aim. A moved head with a stale calibration
  points somewhere nobody authored. `python -m engine.calibrate drift` checks.
- **Keep the crowd box honest.** If the crowd spreads past the footprint in
  `venue.json`, the taper stops covering the people who moved.
- **Run `events/despacio/preflight.py` before doors.** Exit 0 means the venue
  checks pass.
- **Know where Panic is.** Setup tab. It forces zeros onto the wire from the
  output thread and does not need the show to be healthy — unlike Blackout,
  which takes the master to zero with the show still running underneath.

---

## Lasers

`shared/inventory.json` lists a Chauvet Scorpion Dual RGB and a derby laser.
**Nothing in this repo models, guards, or restricts laser output.** Lasers are
regulated differently from lamps in most jurisdictions and audience-scanning is
prohibited or licensed in many of them. Check your local rules; this software
will not help you.

---

## Status

The taper has unit coverage in [`engine/tests/test_safety.py`](../engine/tests/test_safety.py)
— beams held to `crowd_level` in the crowd, ball occlusion, outside the
footprint, above the band, and a sweep tapering rather than flashing. That
proves the code implements the model. **It does not prove the model is right,
and nobody has validated it against a standard or an instrument.**
