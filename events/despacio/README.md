# Despacio Light Show

The rig, the patch and the venue-day procedure for this show. The QLC+ and
APC40 era — mounting conventions, the virtual console layout, the routine list
— moved to [`NOTES.md`](NOTES.md); the show runs on the engine now, and
[`docs/runbook.md`](../../docs/runbook.md) is the procedure for a show night.

Minimal slow-disco show: 4× MingJie MJ-OS-018 60W beam moving heads **mounted sideways
in the 4 corners of a 30 ft square room, 10 ft up** (the disco ball hangs center, also
10 ft up — same height as the heads), aimed at the ball. 2× UKing ZQ-B93 RGBW pinspots
are also aimed at the ball — they are the **only fixtures in this rig that can do a
genuine smooth color crossfade** (the heads have a mechanical 14-slot wheel and can only
jump between colors), so they carry the slow color drift the heads physically cannot.
Controlled from the APC40 mkII, same physical layout conventions as `comsosLightsYear3.qxw`.

**Haze is running for this show**, which makes mid-air beams visible and is why the
aerial looks (Apex / Cathedral / Zenith / Rise / Iris) are first-class rather than
decorative — see "Routines".

**Rigging note (confirmed 2026-07-22 — supersedes an earlier upside-down plan):** each
head's base is tipped onto its side (the shaft that normally sits vertical, floor-standing,
is now horizontal) specifically so its cable connectors exit the **top** of the unit. Unlike
the earlier upside-down plan's cable-mirroring (which was purely cosmetic), this rotation
is *load-bearing for the math* — see "Mounting convention" below for why.

## Files

| File | What |
|---|---|
| `despacio.qxw` | QLC+ 5 workspace (fixtures, functions, virtual console) |
| `fixtures/MingJie-MJ-OS-018-60W-Beam.qxf` | Custom fixture def (9 + 11 channel modes) |
| `patch_sheet.csv` | DMX patch source of truth |
| `webui/` | **Mobile virtual console** — phone-friendly web UI talking straight to QLC+'s own web API. `python webui/serve.py`, then load the printed LAN URL on a phone. See "Mobile web UI" below. |
| `gen_mirrorball.py` / `mirrorball.obj` | Faceted sphere for the QLC+ 3D view (stand-in disco ball) |
| `aim_calc.py` | **On-site recalibration tool** — see below |
| `aim_calc_gui.py` | Desktop GUI (Tkinter) for editing `despacio_config.json` and re-running `aim_calc.py` — `python aim_calc_gui.py` |
| `despacio_config.json` | All site-measured values (room/head/ball geometry, per-mode calibration DMX readings, invert flags) — edit directly or via the GUI |
| `preflight.py` | **One-command readiness check** — `python preflight.py` (or the GUI's **Preflight check** button). Run before leaving home and again at the venue before going live. |
| `validate_despacio.py` | Structural validator (DMX patch, VC solo-mesh, MIDI, function refs, **position-data provenance**, **VC geometry**). Run standalone or via `preflight.py`. |
| `backups/` | Timestamped `despacio.qxw` snapshots — `aim_calc.py` auto-writes one before every recalculation (newest ~20 kept). |

**Safety nets (so a venue-day mistake is recoverable):**
- `aim_calc.py` **auto-backs up** `despacio.qxw` to `backups/` before every write, and
  **fails hard without writing** if a pose function is missing (rather than silently
  shipping a stale pose) — so a bad run leaves the working file untouched.
- The whole project is under **git** — `git status` / `git checkout -- despacio/despacio.qxw`
  is the fastest undo if QLC+ or a script leaves the workspace in a bad state.
- `preflight.py` rolls the math self-test, the structural validator, and the mount-mode /
  calibration / fixture-install guardrails into one PASS/FAIL you can run before going live.
- `validate_despacio.py` enforces two invariants that exist because breaking them is
  invisible in QLC+ (both added 2026-07-30, after each had already shipped broken):
  - **Position provenance** — every Scene writing Pan/Tilt must come from `aim_calc.py`'s
    `build_targets()`, and every EFX must be `IsRelative=1`. Hand-picked absolute positions
    silently stop tracking recalibration; an absolute EFX *cannot* track it at all.
  - **VC geometry** — nothing may overflow its parent frame, overlap a sibling, or share an
    off-screen mirror slot. QLC+ clips out-of-bounds widgets with no warning at load or save,
    which is how buttons have gone missing three times in this project.

## DMX patch (Universe 1 → Art-Net universe 0)

| Fixture | Mode | DMX start |
|---|---|---|
| Moving Head #1–4 (corners: **BR/FR/FL/BL**) | 11 Channel | **1, 12, 23, 34** |
| Pinspot #1–2 (patched, unused) | 6-channel | **45, 51** |

**Address-to-corner mapping confirmed 2026-07-22** (not the FL/FR/BL/BR-in-order guess this
started with): DMX addr 1 = back-right, 12 = front-right, 23 = front-left, 34 = back-left.
`aim_calc.py`'s `HEADS` list and the Diagonal A/B pairing were both updated to match — this
matters for anything computed per-corner (Floor/Walls/Crowd/Diagonal/Corner Chase); it's a no-op
for Ball, which sends the same pan/tilt to every fixture regardless of assumed corner.

MJ-OS-018 11ch map: 1 Pan · 2 Pan fine · 3 Tilt · 4 Tilt fine · 5 Color · 6 Gobo ·
7 Strobe · 8 Dimmer · 9 P/T speed · 10 Auto/sound · 11 Reset (250–255 held 5 s).

**Color wheel (confirmed on hardware, 14 positions over DMX 0–139, ~10 wide each):**
White, Red, Yellow, Blue, Green, Orange, Pink, Cyan, Cyan+Pink, Pink+Orange,
Orange+Green, Green+Blue, Blue+Yellow, Yellow+Red — then 140–255 = auto color change.

**Strobe (confirmed):** 0–7 = open, 8–249 = strobe slow→fast, **250–255 = open again**
(solid on at both ends of the channel).

## Venue packing / logistics checklist

The software is only half the setup. Pack and check the physical side too:

- [ ] **Laptop** (this repo on it) **+ charger**. Confirm QLC+ opens `despacio.qxw` and
      the moving heads show named presets (not "Generic") *before* you leave.
- [ ] **DMX interface** (the USB-DMX widget) **+ its USB cable**.
- [ ] **APC40 mkII + its USB cable.**
- [ ] **DMX cable runs** long enough to reach all 4 corners, daisy-chained — **+ a DMX
      terminator** for the last fixture in the chain.
- [ ] **Power**: extension cords / power strips for 4 corner heads + the ball motor +
      laptop; know where the venue's outlets are.
- [ ] **The 4 heads + mounting brackets/clamps + safety cables** (one per head — never
      hang a fixture on the clamp alone).
- [ ] **Disco ball + its motor/hook**, and whatever rigs it at room center, 10 ft up.
- [ ] **Gaff / console tape** for cable runs and marking positions; a spare fixture if you
      have one; a flashlight for dark-venue patching.
- [ ] **Before leaving home: `python preflight.py` → expect PASS.** This is your "the
      software is internally consistent" gate; the venue-day checklist below is the "the
      physical rig matches the software" gate.

## Venue day checklist

Do these in order — calibration depends on the physical rig and patch being done first.

**Step 0: set `mount_mode` to match how you actually hung them** — `"venue"` if the bases
are tipped sideways, `"hung"` if they're hung upside-down (in `despacio_config.json`, or
the dropdown in `aim_calc_gui.py`). This checklist is for the real installation, not the
desk bench test. `preflight.py` **fails** on purpose if it's `"venue"`/`"hung"` with heads
uncalibrated, and **warns** if it's still `"table"` — that's the reminder, not a bug.

**Step 0b: set `apex_height`** — the canopy height if a parachute goes up above the truss,
otherwise the ceiling. It's the only geometry value the rigging choice changes, and it's
what the Apex / Rise / Build looks converge on.

**Step 0c: set `canopy_sweep_radius`** if a canopy is going up — it's the radius of the
Canopy Ring pools measured on the canopy plane, defaulting to 2000 mm (smaller than the
floor sweeps' 2500 because the canopy is closer to the heads).

1. **Mount the 4 heads** in the room corners — either sideways (shaft horizontal, cables
   exiting the top — see Mounting convention above) or hung upside-down; just make sure
   `mount_mode` matches.
   **While you're on the ladder, try to clock each head so its pan channel's centre points
   roughly at the ball.** It isn't required — calibration back-solves whatever you give it —
   but it decides how much travel is left. Under the current placeholder `"venue"` numbers
   heads 1 and 3 sit ~184° from their elevation centre, leaving only ~86° before the rail,
   while heads 2 and 4 sit ~34° off with ~236° spare. That asymmetry is what forces Zenith
   and Crowd back from a true 90° (see "What the outward and downward poses actually hit").
   If the mirrored mounting needed to get cables out the top makes this impossible, fine —
   `_fit_elev_extreme()` handles it and `preflight.py` reports what was given up.
   Measure each head's actual
   mounting height; if any differ from the assumed 10 ft (3048 mm), update that head's
   entry in `head_height` in `despacio_config.json` (or the GUI's Height field). Also
   recheck `head_inset` (assumed 0.5 m in from the literal corner) against the real
   bracket standoff.
2. **Hang the disco ball** at room center; measure its real height and update
   `ball_height` if it's not exactly 10 ft (3048 mm). If the room isn't exactly a 30 ft
   square, update `room_width`/`room_depth` too.
3. **Patch DMX**: heads at addresses 1 / 12 / 23 / 34 per `patch_sheet.csv` — confirmed
   corners are back-right / front-right / front-left / back-left respectively. Walk up to
   each physical unit and check its own display shows the address you expect; this bit us
   once already (see "Address-to-corner mapping" above) and Floor/Walls/Crowd/Diagonal/Corner
   Chase will be wrong if any address ends up on a different corner than assumed.
4. **QLC+ software setup** — see the Setup section below (fixture install, I/O, previz).
   Then **cross-check addressing with Corner Test** (col 6): trigger Test Corner 1 → 2 →
   3 → 4 in turn and confirm the head that lights up is the physical corner you expect
   (Corner 1 = back-right, 2 = front-right, 3 = front-left, 4 = back-left). Corner Test
   works before calibration (it just points one head roughly ballward while the rest face
   out), so it's the fastest way to catch a swapped address — the exact failure that "bit
   us once already" in step 3.
5. **Whenever you change a config value** (heights, room size, inset), rerun
   `python aim_calc.py` (or click **Save & Recalculate** in the GUI) before reopening
   `despacio.qxw`.
6. **Calibrate each head** (the step that actually matters most — do this even if step 1–2
   measurements were exact, since mounting-angle precision matters more than room
   dimensions): for each of the 4 heads, trigger **Corner Test N** (col 6), use the
   **Pan/Tilt knobs** to visually aim it at the ball by eye, read the two 0–255 values
   shown, and enter them into `despacio_config.json`'s `calibrated_ball_dmx["venue"]` (or
   the GUI's Cal. Pan/Tilt DMX fields, with mode set to "venue") in the same order you
   read them off the knobs. Rerun `python aim_calc.py` after each head (or batch all 4,
   then rerun once — the GUI's Save & Recalculate does both in one click).
7. **Sanity-check the other poses** once all 4 heads are calibrated: run To Floor, To
   Walls, To Crowd, Diagonal Pulse, and Corner Chase and confirm they look right — these are all
   computed as offsets from the calibrated Ball point, so if Ball is right and these
   still look off, it's likely a height/room-dimension measurement (step 1–2), not the
   calibration itself. **Circle and Neighbor Scan can only be verified here at the venue**
   (they aim head-at-head, so they need the real corner geometry — see "Pan direction /
   inter-head geometry" above): watch Circle actually hit the neighbor fixtures, and if the
   loop runs backwards flip **all four** heads' bearing-channel invert together, never
   per-head (`pan_invert` in `"table"`/`"hung"`, `tilt_invert` in `"venue"` — see "Pan
   direction / inter-head geometry" above for why it's not always `pan_invert`).
8. **Check the two faders whose park position gates whole groups of routines.**
   - **MH Dim** should sit low (the workspace saves at 70). Every dimmer routine boosts
     *above* it, so at 255 they are all invisible. Pull it low, then run **Spotlight** and
     confirm you can see the three-tier falloff — featured head brightest, its two
     neighbours mid, the far corner dark.
   - **PT Speed** — nothing in the show writes channel 9, so this fader's park value is the
     movement speed for every pose change all night, and *which end is fast is still
     unverified on this fixture*. Sweep it end to end while a slow pose change runs, decide
     which way you want it, and park it deliberately. `preflight.py` prints the current
     value as a reminder.
9. **Sweep the Gobo fader once**, looking for a **frost or soft-breakup** slot. The gobo
   wheel is deliberately parked at Open (see "Gobo: why there are no gobo routines"), but a
   frost would soften these hard 60 W beams, which is very on-vibe for the wall grazes and
   the straight-down Crowd pools. If one exists, add a single **Soft** scene parked on it.
10. **Final preflight before going live: `python preflight.py`** (or the GUI's **Preflight
    check** button) — expect **PASS** now that mode is `"venue"` and all 4 heads are
    calibrated. A red RESULT means don't go live until it's fixed.
11. **Start the show from column 5 (SHOW)** — see Virtual console layout below.

## Setup

1. **Install the fixture**: `fixtures/MingJie-MJ-OS-018-60W-Beam.qxf` is already copied to
   `C:\Users\maxti\QLC+\Fixtures\`. QLC+ reads user fixtures **at startup only** →
   restart QLC+ before opening the workspace.
2. Open `despacio.qxw`. Fixture manager should show 6 fixtures with named presets
   (Pan/Tilt/Color groups) — if the moving heads show as "Generic", the .qxf didn't load.
3. **I/O**: Universe 1 output = DMX USB (re-pick your interface). Universe 3 input =
   APC40 mkII with profile `Akai APC40 mkII`; re-pick the device and tick **Feedback**
   so pad LEDs work.
4. **3D preview**: the workspace pre-places the 4 heads in the room corners and
   `mirrorball.obj` (1 m faceted sphere) center. Run `aim_calc.py` after measuring the
   real rig so the preview and the live show agree. There is no affordable previz that
   simulates real mirror-ball reflections (Capture explicitly doesn't) — the sphere is
   an aim target, not a sparkle sim.
   `aim_calc.py` also writes each head's `YRot` (mounting yaw) and `InvertedPan`/
   `InvertedTilt` flags on its `<FxItem>` — QLC+'s 3D view (confirmed against the
   `qlcplus` source, `engine/src/monitorproperties.cpp` /
   `qmlui/mainview3d.cpp`) reads these separately from the scene DMX values, and
   defaults every fixture to rotation 0 / not-inverted if they're absent. Without
   them the preview can't possibly match the corner-mount model even though the
   DMX values themselves are correct — this was fixed 2026-07-22. The sign of
   `YRot` is a best-effort mapping onto the mesh's own forward axis and isn't
   independently verified; if the preview beams face the wrong way, flip the sign
   in `apply_to_qxw()`'s Monitor loop and rerun.

   **Known previz limitation, and it got worse with the sideways-mount model —
   don't chase it.** QLC+'s 3D engine has no concept of our Pan/Tilt channel
   swap: it always treats its Pan channel as yaw (left-right) and Tilt channel as
   pitch (up-down), full stop. Since the real rig now sends elevation data over
   Pan and bearing data over Tilt (sideways mount), the preview's *motion* will
   look wrong in a more fundamental way than the previous tilt-anchor mismatch —
   it's animating the wrong axis entirely, not just the wrong zero-point. This is
   a genuine QLC+ limitation, not a bug in `aim_calc.py`. **Decided 2026-07-22:
   don't chase it** — trust the scene DMX values (verified to match
   `compute_poses()` exactly) and use Corner Test on the real hardware as the
   actual verification step. The 3D view remains useful for confirming each
   head's *position* in the room (via `XPos`/`YPos`/`ZPos`), not its motion.

## Still to verify on hardware

Color wheel and strobe are confirmed (see above) and the fixture def / scenes match.
Still unverified — generic guesses in the .qxf:

- **Whether the arming `fetch()` trick actually starts `SLIDER`/`CUE`/`BUTTON` pushes
  flowing against this project's real, installed QLC+ build** — see "Mobile web UI" above.
  (`SPEED_TIME` itself is now confirmed live, 2026-07-30 — see that section; this is the
  one piece of that cross-check still unconfirmed.) That conclusion came from reading the
  vendored `qlcplus/webaccess/src/webaccess.cpp` source, not from sniffing this show's
  actual server traffic — worth the same scrutiny the SPEED_TIME claim needed, since it
  turned out to be reading the wrong thing. Confirm with QLC+ running (`--web`,
  `despacio.qxw` loaded): compare raw socket frames before/after the one-shot
  `fetch("http://<host>:9999/")` and watch for `SLIDER`/`CUE` pushes appearing that weren't
  there before.
- **Gobo wheel** (Open + 7 gobos over 0–63, shake 64–127, auto 128–255). Deliberately left
  fader-only, and probably should stay that way — see "Gobo: why there are no gobo
  routines" below. Worth one sweep of the fader anyway just to document what's on the
  wheel, specifically to find out whether there's a **frost/diffusion** slot.
- **P/T speed direction** (ch 9) — fast→slow vs slow→fast.
- **Ch 10 auto/sound ranges**.
- **Pinspot colors weren't triggering at all (found 2026-08-01, reported as "colors
  never change").** Root cause: channel 0 isn't a plain dimmer, it's a **mode selector**
  (per the shipped `UKing-ZQB93-Pinspot-RGBW.qxf`): 0–8 off, **9–134 = "White Dimmer"**,
  135–239 = RGBW strobe, **240–255 = "RGBW On"**. **Pin Dim** was ranged to `LowLimit="9"
  HighLimit="134"` — the White Dimmer band — while every color scene (Pin Amber/Rose/
  Magenta/Indigo/Teal/Sea/Split) only ever writes channels 1–4 (R/G/B/W) and never touches
  channel 0. The moment Pin Dim was touched, ch0 sat in White Dimmer mode and the fixture
  stopped honouring the R/G/B/W values entirely — every color pick looked identical
  regardless of which button was pressed. Fixed by reranging Pin Dim to `LowLimit="240"
  HighLimit="255"` (the RGBW On band), matching **Pin Breathe**'s boost value (250), which
  was already correctly inside that band. **Not yet confirmed on real hardware** — same
  caveat as everything else in this section.
- **Pin Dim's real dimming range, post-fix.** RGBW On (240–255) is a single named
  capability across all 16 values with no documented sub-gradient, so unlike before, Pin
  Dim is now effectively a narrow on/near-off toggle at the very top of its fader throw
  rather than a smooth dimmer — this fixture appears to have no true continuous master
  dimmer over its RGBW output at all (only the earlier White Dimmer band was continuous,
  and that band ignores color). If finer brightness control over the pins is wanted later,
  it'll need to come from scaling each color scene's own R/G/B/W values down directly,
  not from channel 0.
- **Pin ch0 is LTP, not HTP — the "boost over a low fader" trick may not actually hold
  for Pin Breathe.** Found 2026-07-29 while building the mobile web UI, tracing
  `engine/src/fadechannel.cpp` (`addFlag(FadeChannel::HTP)` fires only when
  `channel->group() == QLCChannel::Intensity`) against
  `qlcplus/resources/fixtures/UKing/UKing-ZQB93-Pinspot-RGBW.qxf`: ch0 ("Total function
  control") is `Group="Effect"`, not `Intensity` — unlike the moving heads' Dimmer
  channel, which genuinely is `Preset="IntensityDimmer"` → Intensity → HTP. So Pin Dim
  and Pin Breathe are order-dependent last-write-wins on that channel, not
  highest-wins, which is a different mechanism than the one this section's Dim
  Chase/Spotlight/Prowl/Crowd Cascade writeups (correctly) describe for the moving
  heads. Worth a second look: **"Pin Breathe Off" (function 145) has zero `FixtureVal`
  entries — it writes nothing at all**, so whether the pulse actually fades back down
  each cycle, or just latches at 250 the first time "On" fires and stays there until
  something else (a color pick, or nudging Pin Dim) overwrites ch0, depends on
  Chaser step-transition internals not confirmed here. Watch a full Pin Breathe cycle
  on real hardware before trusting the "breathing" description above.
- **The aerial poses have never been run on hardware.** Apex / Zenith / Cathedral are the
  first looks in this show to ask for positive elevation at all — everything before them
  pointed at, below, or level with the mounting plane — so they exercise a half of the
  elevation channel nothing has touched yet. `_self_test()` asserts they encode *upward*
  relative to Ball in every mode, but that only guards the maths. Watch the beams.
- **`"venue"` mode's sideways-mount model is unverified on real hardware** (rewritten
  2026-07-22, split from `"table"` mode): that Pan (carrying elevation) and Tilt (carrying
  bearing) are both genuinely center-anchored, that neither needs inverting, and that
  "aimed at the ball" on Pan's center actually lands level — none of this has been
  confirmed with a head in its real final sideways orientation yet. Do the Corner Test +
  `calibrated_ball_dmx["venue"]` calibration in the *real* mounted orientation before
  trusting Floor/Walls/Crowd/Diagonal/Chase for the show; Ball alone is comparatively forgiving
  since it starts from mount_facing = bearing-to-ball regardless.
- **`"table"` mode's zero-anchored-Tilt-as-elevation is also unverified**, separately —
  the 2026-07-22 bench-test calibration handles a constant zero-point error just fine
  (that's what it's for), but the underlying `TILT_MAX=270°` slope assumption has never
  been checked against a second real data point, so accuracy could still drift at large
  elevation swings away from the calibrated ball position.
- **The rebuilt Lazy Circle / Grand Sweep / Slow Sweep / Cross Weave** (2026-07-30) are the
  first routines whose *shape* is derived from the calibration rather than hand-picked. The
  maths is guarded (`_self_test()` asserts each orbit straddles Ball in elevation and that its
  horizontal extremes sit at Ball's own elevation), but the look isn't verified. **Run
  `To Ball`, then `Lazy Circle`: all four heads should orbit the ball.** Before the fix two of
  them pointed ~180° away, so this one test is what proves the repair landed.
- **`Wall Graze` / the wall-graze extremes of Cross Weave, Slow Sweep and Grand Sweep** —
  these depend on the corner geometry being real (heads at the corners, in patched order),
  the same caveat Circle and Neighbor Scan carry, since they aim along the head-to-neighbour
  line. Venue-only; can't be checked on a desk. Four beams should run the full length of the
  four walls.
- **`PT Speed` (channel 9): which end is fast is still unknown**, and no scene in the show
  writes it, so the fader's park value (0) is the movement speed for every pose change all
  night. The `.qxf` capability is a placeholder ("verify direction"). Sweep it once against a
  slow pose change and park it deliberately — `preflight.py` prints the current value.
- **Pin Strobe's rate curve.** Channel 0's 135–239 band is documented as "RGBW strobe, slow to
  fast" in the stock fixture def; 180 was chosen as mid-band, not measured. Tune it against
  hardware the same way Medium Pulse's shutter value needs tuning.
- **Relative-EFX layering (Drift / Counter-Orbit) is still unverified**, and is now the *only*
  place the show relies on it — the three EFX that used to be absolute are chasers now. If the
  beams wander off unbounded when a layer runs on top of a pose, the fallback is to rebuild
  Drift/Counter-Orbit as calibrated chasers too, exactly as was done for the other three.

If you find corrections, edit both copies of the .qxf (this folder **and**
`C:\Users\maxti\QLC+\Fixtures\`), then restart QLC+.
