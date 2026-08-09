# What the beam taper is for, and what it is not

This rig is six LED fixtures: four 60 W beam moving heads and two RGBW pinspots.
One part of the software exists to manage how they behave over an audience — the
**beam-aware intensity taper** in [`engine/safety.py`](../engine/safety.py).

This page is deliberately specific about what that buys, because the honest
answer changes what you should worry about.

---

## Start here: what these fixtures actually do to a person

A 60 W LED beam in the eye at room distance is **dazzling and unpleasant**. You
get glare, an afterimage, a moment of not being able to see, and — if it keeps
happening — a genuinely worse night. What you do not get is injury. The aversion
response (blink, flinch, look away) is doing the real work, and it is adequate
for this class of fixture.

So the taper is a **comfort and quality feature**, not a protective device. It
exists because a beam parked in the front row's faces for two bars is bad
lighting, and because a slow sweep through head height is worse than a fast one.
Treat it as part of the design, not as a guard you are relying on.

**Two things on this rig are a different category, and neither is handled by the
taper. They are the reason this page exists at all — see
[The two real ones](#the-two-real-ones).**

---

## What the taper does

Evaluated every frame from the aim the fixture is *actually* being sent, after
the entire layer stack, in `state.evaluate()`. Nothing a look does can outrank
it — `apply_safety` is deliberately not a layer, so it cannot be reordered, and
the F15 shape macros were checked against exactly this.

The geometry, in order (`safety.clearance`):

1. Cast the beam axis, clip it to the room. Past a wall the beam is gone.
2. If the mirror ball blocks it first, stop — a mirror ball is a solid sphere
   and genuinely occludes.
3. Find where what remains crosses the head band. No crossing → full intensity.
4. Measure how far that crossing passes from the crowd footprint, in the band
   plane, against the beam's own radius at that range. Inside → taper.

The property that matters is **per frame from the current aim**. A scene-based
console can at best vet its stored poses; it cannot cover the transit between
them, and transit is most of where the discomfort came from. Here a move that
passes through the crowd dims on the way in and comes back up on the way out,
with nobody having authored it.

Movement is rate-limited in both directions (`slew_per_second`, default 2.0) so
the change reads as a dip rather than a flicker. That limiter also doubles as
the crossfade when the rig is reloaded live.

---

## What it does not cover

Not alarming, just true — and worth knowing when you are wondering why something
did or did not dim.

| Gap | Detail |
|---|---|
| It dims rather than cuts | `crowd_level` defaults to **0.5**. A beam over the crowd is halved, not extinguished. Deliberate: the goal is "not dazzling", not "never lands on anyone", because every floor-sweep pose crosses head height on the way down. |
| Only the crowd box | One axis-aligned footprint × a head band. Someone at the bar, in a doorway or on a riser is invisible to it. |
| Not the pinspots | `state.py:282` skips any fixture with no geometry head. The pinspots are hand-aimed and entirely yours. |
| Not mirror-ball reflections | The ball *occludes* (which reduces dimming), but the hundreds of small beams it throws back are not modelled. They are also far dimmer than the source, which is why this has never mattered. |
| No other reflective surface | No mirrors, glass, glossy walls, wet floors. |
| Purely geometric | No irradiance, no exposure time, no photobiological assessment. The test is "does the cone enter a box". For this class of fixture that is the right level of model. |
| `jog` bypasses it | By design — you cannot calibrate a head through a guard that dims it. The UI raises a red banner while any head is jogging, and jog now requires `configure` access. |
| It trusts the config | A wrong fixture position or room size produces a confident, wrong answer. |

---

## The two real ones

### 1. Strobe and photosensitive epilepsy

**This is the genuine medical risk on this rig, and nothing in the software
limits it.** Strobe is reachable through ported looks and through auto mode's
energy axis, and there is no cap on rate.

Photosensitive epilepsy is typically provoked in the 3–30 Hz range, worst around
15–20 Hz. Unlike dazzle, this is not something an aversion response protects
anyone from, and the person affected has no warning.

Practical position until a rate limit exists (it is on the roadmap, and it is
the highest-value safety item left):

- Keep sustained strobe short and infrequent.
- Avoid the 15–20 Hz region for anything more than a hit.
- If you run strobe at all, say so at the door. That is the actual mitigation.

### 2. Lasers

`shared/inventory.json` lists a Chauvet Scorpion Dual RGB and a derby laser.
**Nothing in this repo models, guards or restricts laser output**, and lasers
are a genuinely different hazard class from every LED fixture here — audience
scanning is prohibited or licensed in most jurisdictions. Check your local
rules; this software will not help you.

---

## The three estimated inputs

These are the taper's most load-bearing numbers and none has been measured. They
matter for whether the taper *behaves as designed*, not because someone gets
hurt if they are wrong.

### `beam_deg: 8` — `events/despacio/rig.json`

> "the .qxf profile claims 3 degrees and these read visibly wider than that in
> the room, so they are set to 8 — STATED 2026-08-08, not measured."

Sets the beam's half-width at range, i.e. how wide a swathe counts as being over
the crowd. Too small and the taper under-dims; too large and it over-dims and
you lose looks. The current 8 widens the profile's 3, so it errs toward dimming.

**To measure:** project one head onto a wall at a known distance, measure the
bright core's diameter, `2 * atan(radius / distance)`. Ten minutes.

### `ball_radius: 300` — `shared/venues/despacio-room.json`

> "errs toward NOT dimming — a bigger ball here means the taper believes more
> beams are blocked than really are."

Of the three this is the one that errs toward doing nothing: overstate the ball
and the taper thinks beams are occluded that are not. **To measure:** measure
the ball. One minute.

### `head_band_min/max: 1400–2000` — the venue file

A policy choice rather than a measurable — roughly seated-tall to standing-tall,
deliberately generous at the top because someone on shoulders is who gets hit.
Revisit if the room gains a stage, a riser, or a bar people stand on.

---

## The `crowd_level` dial

In the venue file under `taper`, live-editable from the Setup tab.

| Value | Behaviour | Cost |
|---|---|---|
| `0.5` (default) | Beams over the crowd are halved. | Still bright enough to dazzle at close range. |
| `0.0` | Beams over the crowd go dark. | Kills every floor-sweep pose — a head aiming at the dancefloor blinks out on the way down. |
| `1.0` | No taper at all. | None of this applies. |

`enabled: false` disables it entirely; the UI raises a notice and the server
prints the live value at startup.

---

## Things the software genuinely cannot do for you

- **Aim the pinspots above head height by hand.** Nothing checks them.
- **Re-calibrate after any re-hang.** Every look's position is an offset from a
  head's calibrated ball aim, so a moved head with a stale calibration points
  somewhere nobody authored. `python -m engine.calibrate drift` checks.
- **Keep the crowd box honest.** If the crowd spreads past the footprint, the
  taper stops covering the people who moved.
- **Run `python scripts/preflight.py` before you leave**, and
  `events/despacio/preflight.py` at the venue.
- **Know where Panic is.** Setup tab. It forces zeros onto the wire and does not
  need the show to be healthy — unlike Blackout, which takes the master to zero
  with the show still running underneath.

---

## Status

The taper has unit coverage in
[`engine/tests/test_safety.py`](../engine/tests/test_safety.py) — beams held to
`crowd_level` in the crowd, ball occlusion, outside the footprint, above the
band, a sweep tapering rather than flashing, and every shape macro still passing
through it. That proves the code implements the model. It does not prove the
model is right, and nobody has validated it against an instrument — which for a
comfort feature is a reasonable place to be, and for the strobe gap above is
not.
