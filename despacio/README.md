# Despacio Light Show

Minimal slow-disco show: 4× MingJie MJ-OS-018 60W beam moving heads **mounted sideways
in the 4 corners of a 30 ft square room, 10 ft up** (the disco ball hangs center, also
10 ft up — same height as the heads), aimed at the ball. 2× UKing ZQ-B93 RGBW pinspots
are patched but **unused for now** (routines removed while the corner rig is being
tested — easy to bring back). Controlled from the APC40 mkII, same physical layout
conventions as `comsosLightsYear3.qxw`.

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
| `gen_mirrorball.py` / `mirrorball.obj` | Faceted sphere for the QLC+ 3D view (stand-in disco ball) |
| `aim_calc.py` | **On-site recalibration tool** — see below |
| `aim_calc_gui.py` | Desktop GUI (Tkinter) for editing `despacio_config.json` and re-running `aim_calc.py` — `python aim_calc_gui.py` |
| `despacio_config.json` | All site-measured values (room/head/ball geometry, per-mode calibration DMX readings, invert flags) — edit directly or via the GUI |
| `preflight.py` | **One-command readiness check** — `python preflight.py` (or the GUI's **Preflight check** button). Run before leaving home and again at the venue before going live. |
| `validate_despacio.py` | Structural validator (DMX patch, VC solo-mesh, MIDI, function refs). Run standalone or via `preflight.py`. |
| `backups/` | Timestamped `despacio.qxw` snapshots — `aim_calc.py` auto-writes one before every recalculation (newest ~20 kept). |

**Safety nets (so a venue-day mistake is recoverable):**
- `aim_calc.py` **auto-backs up** `despacio.qxw` to `backups/` before every write, and
  **fails hard without writing** if a pose function is missing (rather than silently
  shipping a stale pose) — so a bad run leaves the working file untouched.
- The whole project is under **git** — `git status` / `git checkout -- despacio/despacio.qxw`
  is the fastest undo if QLC+ or a script leaves the workspace in a bad state.
- `preflight.py` rolls the math self-test, the structural validator, and the mount-mode /
  calibration / fixture-install guardrails into one PASS/FAIL you can run before going live.

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

## Mounting convention assumed by `aim_calc.py`

Room = 30 ft (9144 mm) square, heads at 10 ft (3048 mm), ball also at 10 ft — heads and
ball are at the **same height**, so "look at the ball" comes out almost exactly level for
every head by symmetry, before any offset/inversion is applied. Room geometry (room size,
head/ball heights, corner positions) is **shared** between both mounting modes below — it
describes where the ball/floor/crowd targets are relative to each head, which has nothing
to do with which DMX channel encodes what.

### Two switchable mounting modes — `mount_mode: "table"` or `"venue"` in `despacio_config.json`

All the values discussed below (`mount_mode`, room/head/ball geometry, per-head
calibration DMX readings, mount facing overrides, invert flags) live in
`despacio_config.json`, not as literals inside `aim_calc.py` — edit that JSON directly,
or use `python aim_calc_gui.py` for a form instead of hand-editing JSON.

Running `python aim_calc.py` writes whichever mode is currently set into `despacio.qxw` —
it's a build-time selector, not a live in-show toggle. Switch `mount_mode` and rerun to
switch which target the workspace reflects. **The two modes are not just different
config values — Pan and Tilt trade which physical thing they control**, so read this
carefully before flipping the switch:

- **`"table"` — bench test, heads sitting BASE-DOWN on a desk.** This *simulates* hanging
  upside-down from a ceiling: flip the whole world upside-down, and a fixture hanging
  base-up-at-the-ceiling with its head dangling below becomes, in the flipped frame, a
  fixture sitting base-down on a table with its head rising up from it — same mechanical
  relationship, much easier to bench-test. Pan keeps its ordinary role (base spinning
  around a roughly-vertical shaft → **bearing**, center-anchored, confirmed on hardware
  2026-07-19 to be a bounded ~540° channel) and Tilt keeps its ordinary role (head
  pivoting on the yoke hinge → **elevation**, zero-anchored: DMX 0 = local "down").
  `pan_invert`/`tilt_invert` are **per-head empirical** values. Current confirmed table
  values: `tilt_invert = [false,false,false,false]` (all confirmed 2026-07-22),
  `pan_invert = [true,true,true,true]`. Note a single "aim at the ball" reading pins
  *where* each head points but not which way its pan rotates — though for the ball-
  relative poses this doesn't matter (see "Pan direction / inter-head geometry" below).
- **`"venue"` — real final installation, base tipped onto its side.** The base's shaft
  (normally vertical) is rotated to horizontal specifically so cables exit the top. That
  swaps which real-world quantity each channel controls:
  - **Pan (540° range) now controls ELEVATION** — a vertical arc instead of horizontal.
    Deliberate: 540° gives ±270° of throw around level, way more headroom than Tilt's
    270° ever gave, so beams can swing well above *and* well below the heads' mounting
    height — the whole reason for mounting this way.
  - **Tilt (270° range) now controls BEARING** — left/right, ±135° around center. Plenty
    for this room: every corner-to-ball bearing is within ±45–135°, well inside range.
  - Both channels center-anchored — confirmed for Pan; **assumed, unverified**, for
    Tilt's new bearing role (the "table" mode's zero-anchored-Tilt finding is specific to
    Tilt's elevation role there and doesn't carry over).
  - `pan_invert`/`tilt_invert` default **False** here — sideways mounting has no known
    inversion requirement, a different transform from upside-down. Confirm/flip per head
    via Corner Test once installed.

**Calibration data is per-mode and does not transfer between them** — a "table" reading
and a "venue" reading calibrate two physically different transforms.
`calibrated_ball_dmx["table"]` is pre-filled with the 2026-07-22 desk bench-test readings
(MH1/MH3 → `[22, 0]`, MH2/MH4 → `[127, 0]`); `calibrated_ball_dmx["venue"]` starts empty
— don't reuse the table readings there, redo the knob-reading step once each head is
really mounted sideways at the venue.

If a specific head turns out mounted differently than assumed (rare — most of the rig
should match), **calibrate that one head** instead of guessing:

1. Trigger **Corner Test N** (col 6) or **To Ball** (col 2) so head N is lit, then use
   its **Pan/Tilt knob** (cols 1–4 pan, cols 5–8 tilt) to visually aim it AT THE BALL by
   eye. The knobs display the exact 0–255 DMX value on screen.
2. Enter those two numbers into `despacio_config.json`'s
   `calibrated_ball_dmx[mount_mode]` for that head, **in the order read off the knobs**:
   `[pan_reading, tilt_reading]`, e.g. `[140, 60]` — or, easier, open
   `python aim_calc_gui.py`, pick the head's row, and type the two readings into the
   Cal. Pan DMX / Cal. Tilt DMX fields. Which one is bearing vs elevation depends on the
   active mode — you don't need to think about that, just enter exactly what the knobs
   show.
3. Run `python aim_calc.py` (or click **Save & Recalculate** in the GUI). It backs out
   that head's true mount angle (and any elevation zero-point error) from the verified
   point, then computes Floor/Walls/Crowd/Diagonal/Chase/Ball Spiral as geometric offsets
   **from it** — so once Ball is right, everything else lines up automatically. It
   re-splices all 14 pose-dependent functions in one shot (`Heads - Ball/Floor/Walls/Crowd`,
   `Diagonal A/B`, `Chase Pos 1–4`, `Ball Spiral Step 1–4`).
4. Reopen `despacio.qxw` in QLC+.

`head_height` / `ball_height` still matter (they set how far everything tilts toward the
floor vs. the ball) but are secondary once calibrated — a few cm of error there won't
throw off the Ball aim, only the less-visually-critical Floor/Walls/Crowd targets. A head left
uncalibrated (`null`) falls back to a pure geometry guess (assumes it's mounted pointed
straight at the ball) — the script prints `[UNCALIBRATED]` next to any head still on
that fallback, so you always know which ones are trustworthy.

If a head's actual mounting rotation doesn't match "aimed at the ball" (e.g. it got mounted
facing slightly off), correct just that head via `mount_facing_override` (degrees, shared
across modes) instead of re-measuring the whole room. If a head turns out to bear or
elevate the *opposite* direction from what's assumed, flip that head's entry in the active
mode's `pan_invert`/`tilt_invert` — **these are per-mode, per-head lists**
(`pan_invert["venue"][2]`, etc.), since there's no guarantee all 4 units behave identically
or that both modes need the same signs.

### Pan direction / inter-head geometry — why Circle & Neighbor Scan are venue-only

There are **two classes of routine**, and only one can be bench-tested on the desk rig:

- **Ball-relative** (Ball, Floor, Walls, Crowd, Ball Wave, Ball Spiral): each head only
  needs its own aim at the *shared ball* (a single point). The per-head "aim at ball"
  calibration captures this fully, regardless of how the heads are laid out — which is why
  these all bench-test correctly on the desk. (Pan rotation *direction* is genuinely
  undetermined by one ball reading, but it doesn't matter here: Ball/Floor/Crowd are 0°
  offsets and Walls is 180°, and a 0°-or-180° turn lands on the same servo spot whichever
  way pan spins.)
- **Inter-head** (Circle, Neighbor Scan): each head must aim at *another head's position*,
  so these depend on where the heads sit **relative to each other** — the room's corner
  geometry. `aim_calc.py` derives that from the assumed 30 ft square with heads at corners
  in patched order (BR/FR/FL/BL). **The desk bench rig does not reproduce that geometry**
  (4 fixtures in a rough tabletop square at arbitrary spacing/orientation), so Circle and
  Neighbor Scan point at the wrong units *on the desk* — proven sign-independently:
  MH2's Circle DMX can only decode to MH3 or MH1 under the assumed square, never to MH4,
  yet on the desk it read as aimed at MH4. **These two routines can only be validated at
  the venue**, where the heads really are at the room corners. (Bearings are
  scale-invariant, so a *small* square would work too — but only if it matches the corner
  order and orientation, which the desk doesn't.)

**Circle needs a *uniform* pan direction across all 4 heads** — it's a cycle
(MH1→MH2→MH3→MH4→MH1). Either uniform `pan_invert` (all-true or all-false) gives a valid
circle, just clockwise vs counter-clockwise; a **mixed** setting is the one thing that
breaks it (some heads turn backward → adjacent heads point at each other). So never flip
`pan_invert` per-head to "fix" Circle — if the rotation looks reversed, flip *all four*
together.

If you want Circle/Neighbor Scan to run on a bench before the venue, either arrange the 4
desk heads as a proper square in patched order, or calibrate the targets directly (aim each
head at its intended neighbor and record the DMX) instead of deriving from ball-calibration
+ assumed geometry. `aim_calc.py`'s `_self_test()` verifies these routines aim at the
correct target *in the model's geometry*; it cannot know whether the physical layout
matches that geometry — that's what venue verification is for.

**Walls pose is capped at 135° off center in `"venue"` mode, not a full 180° turn** (it's
uncapped — a true 180° reversal — in `"table"` mode, since bearing lives on the 540°-range
Pan channel there). This falls naturally out of the shared encode/clamp logic — `"table"`'s
bearing channel has ±270° of headroom (comfortably fits 180°), `"venue"`'s only has ±135°
(saturates before reaching 180°) — not a special case, just what the numbers do.

`room_width`/`room_depth` (30 ft square) and `ball_height` (10 ft) are the stated real
dimensions, not placeholders. `head_height` is also a real 10 ft measurement, but it's a
**per-head list** (`[3048, 3048, 3048, 3048]`) rather than one number for all 4 — real
truss/ceiling attachment points are rarely perfectly even, so measure each corner's actual
mounting height on site and set that head's entry independently if it differs.
`head_inset` (0.5 m in from the literal wall corner) is still a guess — adjust to match
the actual bracket standoff once installed.

### Correctness note

`aim_calc.py` runs an automatic `_self_test()` (bearing/elevation round-trips, per-head
calibration exactness, in-range DMX under extreme inputs, Floor-matches-Ball's-bearing
invariant) before every write to `despacio.qxw`, covering **both modes** from the same
shared code path — a fix to the geometry or encode/decode math is checked against both
automatically, so bug-fixing one mode can't silently break the other.

Two real bugs found and fixed via bench testing 2026-07-22 (both pre-dated the table/venue
split, just never exposed by earlier small-offset testing). Note: "Crowd" in bug #2 below
refers to the pose since renamed to **Walls** (the outward/wall-facing one) — left as
originally written for the historical record, see the Virtual console section for the
current name and the genuinely new Crowd (straight down):
1. Naively wrapping a calibrated bearing delta into ±180° corrupted the Ball round-trip
   for any delta beyond that (up to ±270° is legitimately reachable on the 540°-range
   channel) — fixed by using the exact backed-out delta for Ball instead of re-deriving
   and re-wrapping it.
2. **Floor and Crowd were computed relative to `mount_facing`** (a derived value that can
   land far outside a normal compass bearing once calibrated, e.g. -358°) **instead of
   relative to Ball's own validated position** — confirmed via bench test (Ball correct,
   Floor/Crowd visibly wrong). Floor shares the exact same target bearing as Ball in this
   room (both aim at room center), so it now reuses Ball's bearing delta directly, no
   arithmetic. Crowd needs a genuinely different (180°-opposite) bearing, computed as a
   direct, non-wrapped half-turn from Ball's delta — picking whichever of `+180`/`-180`
   fits the channel's actual range, since (per bug #1's lesson) two deltas 360° apart are
   NOT interchangeable on this hardware even though they're the same angle in the abstract.

3. **Floor pointed toward the table/floor instead of the ceiling in `"table"` mode.**
   Two compounding causes, both fixed:
   - `_to_dmx_zero_elev`'s formula/docstring assumed **positive=down**, while
     `compute_poses()` (and its own docstring) used **positive=up** throughout — a direct
     contradiction between the geometry layer and the encode layer. Fixed to consistently
     use positive=up.
   - Confirmed on hardware (turning the Tilt knob up from the calibrated point moves the
     beam up): `TILT_INVERT["table"]` should be `False`, not the `True` it defaulted to.
   - Even with both of those fixed, Floor still clipped to the same DMX as Ball, because
     Floor's target elevation is computed in **real-venue** terms ("-27.9° = look down at
     the real floor"), but `"table"` mode is a **world-flip simulation** — flip the whole
     room upside-down and down-in-reality becomes up-in-simulation. Confirmed directly:
     "floor is the ceiling" for this bench rig. Fixed by adding `world_flip_elevation` to
     `MOUNT_PROFILES` (`True` for `"table"`, `False` for `"venue"`, the real installation
     with no simulation trick) and XORing it into the effective elevation-invert flag —
     applied consistently to both decoding the calibration reading and encoding every
     pose, rather than negating the geometry in one specific spot and risking missing
     another. This only applies to elevation: a purely vertical flip doesn't touch
     bearing (the room's horizontal X/Z plane, and therefore the ball's bearing, is
     unaffected by flipping up/down).
   Floor's tilt now reads a genuinely different, non-clipped DMX from Ball's for every
   head, correctly on the "up" side per the confirmed hardware direction.

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
**Set `mount_mode` to `"venue"` in `despacio_config.json` first** (or pick it from the
dropdown in `aim_calc_gui.py`) — this checklist is for the real installation, not the
desk bench test. (`preflight.py` will **fail** on purpose if it's still `"venue"` with
heads uncalibrated, and **warn** if it's still `"table"` — that's the reminder, not a bug.)

1. **Mount the 4 heads** in the room corners, base tipped onto its side (shaft horizontal,
   cables exiting the top — see Mounting convention above). Measure each head's actual
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
   loop runs backwards flip **all four** `pan_invert` together, never per-head.
8. **Final preflight before going live: `python preflight.py`** (or the GUI's **Preflight
   check** button) — expect **PASS** now that mode is `"venue"` and all 4 heads are
   calibrated. A red RESULT means don't go live until it's fixed.
9. **Start the show from column 5 (SHOW)** — see Virtual console layout below.

## Setup

1. **Install the fixture**: `fixtures/MingJie-MJ-OS-018-60W-Beam.qxf` is already copied to
   `C:\Users\maxti\QLC+\Fixtures\`. QLC+ reads user fixtures **at startup only** →
   restart QLC+ before opening the workspace.
2. Open `despacio.qxw`. Fixture manager should show 6 fixtures with named presets
   (Pan/Tilt/Colour groups) — if the moving heads show as "Generic", the .qxf didn't load.
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

## Virtual console / APC40 layout

**Guests: start at column 5 (SHOW).** One press = a complete room look; anything running
elsewhere stops automatically. Columns 1–3 are manual color/movement control, 4 is
corner-aware movement programs, 6 is a setup/calibration tool, 7 is momentary punch, 8 extra.

Columns (grid pads top→bottom = Button 1–5, then clip-stop = small, A/B = wide):

| Col | Frame | Pads (solo group unless noted) | Fader | Knob |
|---|---|---|---|---|
| 1 | MH Color | White / Red / Blue / Pink / Orange, small = Color Auto, wide = Sunset | MH Dim (ch8 ×4) | Pan MH1 |
| 2 | MH Move | To Ball / To Floor / To Walls / **To Crowd** / Lazy Circle, small = Cross Weave; wide = Beam Open (toggle, non-solo) | PT Speed | Pan MH2 |
| 3 | MH Color Loops | Color Drift / Color Spin / **Rainbow Wheel**; small = Slow Pulse (toggle, non-solo) | **Color Scroll** (raw wheel) | Pan MH3 |
| 4 | **MH Programs** | Diagonal Pulse / Corner Chase / Grand Sweep / **Slow Sweep** / **Ball Spiral** | — | Pan MH4 |
| 5 | **SHOW** | **Warm Up / Idle / Deep / Peak / Landing** | — | Tilt MH1 |
| 6 | **Corner Test** | **Test Corner 1 / 2 / 3 / 4** (setup tool, see below) | — | Tilt MH2 |
| 7 | Accents | White Bump / Strobe — **hold-to-Flash**, no solo | — | Tilt MH3 |
| 8 | Extra | **Ball Wave / Circle / Neighbor Scan** (movement family); **Dim Chase / Spotlight** (local pair) | MH Gobo | Tilt MH4 |
| M | Master | Blackout (ch209) | Master Dimmer (submaster) | — |

**Renamed 2026-07-22: "Crowd" → "Walls".** The pose that turns outward, level, toward the
walls was always called "Crowd" but never actually pointed at people — it washes the
walls. It's now labeled **Walls**. The genuinely new **Crowd** (col 2) points straight
down at the people on the dancefloor, directly below each head. `Slow Sweep` moved from
col 2 to col 4 to make room; its function is unchanged.

Solo behavior (Year-3 hidden-mirror mesh): each column's pads are mutually exclusive
within the column, **and** cross-column conflicts auto-resolve along two independent
families — COLOR (col 1 statics + col 3 loops + the SHOW macros) and MOVEMENT (col 2
positions/loops + col 4 programs + col 8's **Ball Wave / Circle / Neighbor Scan** + the
SHOW macros). Pressing a SHOW look stops any running color or movement loop; pressing a
manual color stops a competing color loop, same for movement. **Ball Wave, Circle, and
Neighbor Scan** all live in col 8 but are full MOVEMENT-family members (mirrored into
cols 2, 4, and 5 exactly like Slow Sweep/Ball Spiral), since each writes Pan/Tilt like
any other position routine and would otherwise visibly fight whatever position pose is
already running. ~87 hidden mirror buttons (no MIDI input, parked off-screen inside each
SoloFrame) implement this; don't delete them.

**Dim Chase and Spotlight (col 8) are a separate, small solo pair, not meshed into
either family.** They only ever write the Dimmer channel (never Pan/Tilt or Color), and
nothing else in the show writes Dimmer continuously — White Bump also touches Dimmer but
is a momentary Flash, already excluded from the mesh (same as Strobe) — so the two of
them just need to be mutually exclusive with **each other**, local to col 8, the same
"local exclusivity only" pattern as Corner Test.

**Column 6 (Corner Test) is a setup tool, not a performance look** — it is deliberately
*not* meshed into the auto-stop system. Use it while installing/aiming the rig: each
button points exactly one head at the ball while the other three park facing outward,
so you can verify one corner at a time. Stop any other MH movement (col 2/4/5) manually
before testing, or the two will visibly fight for the same pan/tilt channels.

Knobs 1–4 are labeled Pan and 5–8 Tilt (matching the physical DMX channel names), but per
the sideways mounting, knobs 1–4 actually move the heads **up/down** and knobs 5–8 move
them **left/right** — manual aim only works while no position preset / EFX is running
(EFX rewrites pan/tilt every tick). **Color Scroll** (fader 3) sweeps the raw color wheel
through all 14 positions including the split-color combos.

**Important:** scenes never touch the MH dimmer channel — brightness is owned by the
**MH Dim fader (col 1)**. Press **Beam Open** (col 2 wide) once to open shutter + gobo,
then raise MH Dim, or nothing will light up. The one exception is **White Bump**, whose
Flash deliberately surges the dimmer above the fader.

**PT Speed (col 2 fader) is not a master tempo control — expect it to feel like it
"resets" every time you switch routines.** It's wired directly to the fixture's own
Pan/Tilt Speed channel (ch 9 of 11), which only governs the servo's ramp characteristic
for a given move. It has no effect on *how often* QLC+ sends new Pan/Tilt targets — that
pacing is baked separately into each routine's own `Speed`/`Duration`/`FadeIn`, e.g.
Chase Pos/Diagonal A/B fade over 4–6 s, Diagonal Pulse steps every 12 s, Corner Chase
every 5 s, Ball Spiral every 3.5 s, Slow Sweep cycles every 20 s, Lazy Circle every 30 s,
Grand Sweep every 40 s. Switching between routines with wildly different durations is
what makes the apparent motor speed jump — the DMX byte on ch 9 never actually changes.
If a true global tempo control is ever wanted, it'd need per-routine Speed Dial widgets
(or normalizing the durations closer together) rather than relying on this fader.

**Strobe channel baseline (fixed 2026-07-22):** the Strobe/Shutter channel (ch 7 of 11)
is an LTP channel — QLC+ just holds its last DMX byte once nothing is actively writing
it, it doesn't auto-reset. The **Strobe** accent button and **Slow Pulse** toggle both
write that channel, and used to get stuck on release/off because none of the base color
scenes wrote it at all. Fixed by baking `Strobe = 0` (shutter open) into all 8 base color
functions (**White/Red/Blue/Pink/Orange/Color Auto/Sunset/Color Spin** — `Color Drift`
inherits it since it chases through the same static scenes), so there's always a live
writer holding the channel open underneath whichever color look is running. **Beam Open**
is still the manual reset if you ever end up with no color scene active at all (e.g. right
after a blackout, before picking a look).

## Routines

**SHOW looks (col 5, the set-and-forget buttons):**
- **Warm Up** — heads sunset (yellow+red) on a slow sweep.
- **Idle** — heads white on a 30 s lazy circle. The signature look.
- **Deep** — heads blue weaving criss-cross (Cross Weave).
- **Peak** — 20 s color drift + Corner Chase movement.
- **Landing** — end of night: heads white, parked looking at the ball.

**Corner-aware movement (col 4):**
- **Diagonal Pulse** — the two diagonal pairs alternate: one pair aims at the ball while
  the other faces the walls, then swap, every 12 s.
- **Corner Chase** — lighthouse effect: one head aims at the ball while the other three
  face the walls, rotating 1→2→3→4 every 5 s.
- **Grand Sweep** — all 4 heads in a large synchronized circle, phase-offset 90° apart
  (a slow "carousel" sweeping across the room), 40 s per revolution.
- **Ball Spiral** (new 2026-07-22) — every head cycles through 4 positions: aimed at the
  ball, a 90° swing one way, facing the walls (opposite the ball), a 90° swing the other
  way — then repeats. Phase-offset by 1 step per head (`aim_calc.py`'s `SPIRAL_SEQ`), so
  while one head is exactly on the ball, its neighbors are mid-sweep at the other 3
  positions — a rotating "lighthouse that never stops moving," 3.5 s/step. Pairs well
  with **Rainbow Wheel** (col 3) for a full spiral-of-color-around-the-ball effect —
  trigger both together (no dedicated SHOW button yet, columns 5 is full; the underlying
  **Spiral** collection function exists for a future binding).

**Color loops (col 3):**
- **Color Drift** — MH wheel steps Orange → Pink → Blue → Red, 20 s per step.
- **Color Spin** — the wheel's own slowest continuous color rotation (DMX 141).
- **Rainbow Wheel** (new 2026-07-22) — MH wheel cycles Red → Yellow+Red → Yellow →
  Orange, 8 s/step, phase-offset by 1 step per head so all 4 show different-but-adjacent
  warm hues at any moment, continuously rotating between heads. Deliberately a *different*
  period than Ball Spiral (8 s vs 3.5 s) so the combined color/position pattern keeps
  evolving rather than repeating in lockstep.

**Building blocks / loops:**
- **Lazy Circle / Slow Sweep / Cross Weave** — smaller movement loops (16-bit smooth);
  Cross Weave slowly weaves mirrored beam pairs past each other every 16 s. These use
  hand-picked values, not `aim_calc` geometry — fine since they're stylistic, not aimed
  at a real target.
- **Slow Pulse** — slowest shutter blink (~lazy heartbeat) on the heads.

**Ball Wave (col 8, new 2026-07-23)** — every head bobs its elevation up and down
around the ball's own elevation (±20°, `WAVE_ELEV_SWING` in `aim_calc.py`), bearing held
steady on the ball throughout. Phase-offset by 1 step per head (`aim_calc.py`'s
`WAVE_SEQ = ["ball", "wave_up", "ball", "wave_down"]`, same offset trick as Ball
Spiral's `SPIRAL_SEQ`), so the bob ripples across the 4 heads like a Mexican wave
instead of all 4 bobbing in lockstep, 3 s/step. Computed geometrically like Ball/Floor/
Walls/Crowd — recalibrating a head's Ball position automatically keeps Wave centered on
it. **On the `"table"` bench rig, Wave may clip flat on one side** (a swing pushes past
DMX 0 or 255 and just clamps there) if that head's calibrated Ball point happens to sit
near the zero-anchored Tilt channel's boundary — expected on the bench, not a bug; the
`"venue"` mode's center-anchored Pan-as-elevation has far more headroom (±270°) and
should swing cleanly both ways once mounted for real.

**Circle (col 8, new 2026-07-23)** — a single static pose, not a chase: each head aims
at the *next* head around the room instead of the ball (`aim_calc.py`'s `HEADS` list is
already in rotational order, so head `i` targets head `(i+1)%4`'s own position and its
own `HEAD_HEIGHT`, not the ball's). The 4 beams form a closed loop pointing corner to
corner around the room rather than converging on the center — a deliberately different
shape from every other routine, all of which either hit the ball or a shared outward
target.

**Neighbor Scan (col 8, new 2026-07-23)** — each head sweeps between its two neighbors
(`(i-1)%4` and `(i+1)%4`), holding the ball's own elevation throughout (a level,
horizontal sweep). Two-step Chaser (`aim_calc.py`'s `scan_prev`/`scan_next` poses feeding
Functions 79/80, chased by 81), 2.2 s fade / 3.2 s/step — comparable cadence to Ball
Wave. QLC+ scenes crossfade linearly in DMX, so each fade sweeps the beam through
everything between the two neighbor bearings, including the ball's own bearing at the
midpoint — **verified, not assumed**: for every head, the bearing-channel DMX value
exactly midway between `scan_prev` and `scan_next` equals `ball`'s value bit-for-bit
(e.g. head 1: scan_prev=1, scan_next=43, ball=22 — the exact average), because this
room's corner-square symmetry puts each head's two neighbors exactly 90° apart with the
ball precisely on the angular bisector. A plain 2-step oscillation (not a 3-step
left→ball→right chase) was sufficient because of this — no separate "hit the ball"
step needed, the linear crossfade already passes through it every cycle.

**Dim Chase and Spotlight (col 8, new 2026-07-23)** — pure brightness chases, no
position or color changes; both leave 3 of the 4 heads' Dimmer channel **completely
unwritten** each step rather than writing a low value, so those heads simply show
whatever the **MH Dim fader** (col 1) currently has them at — this is deliberate, not an
oversight: Dimmer is an HTP (highest-takes-precedence) channel, so a chase step can only
ever push a head's brightness *up* past the fader, never down past it (same mechanism
White Bump already relies on). **Set MH Dim to a low/dim baseline before triggering
either of these** — the chase then rotates a brightness *boost* to 255 on top of that
baseline, which is what makes the "dim" heads actually look dim:
  - **Dim Chase** — 3 of 4 heads boosted to full each step, one head left at the dim
    baseline, rotating 1→2→3→4 every 4.5 s — the *odd one out* is the dim one.
  - **Spotlight** — the inverse: only 1 head boosted to full each step, the other 3 at
    the dim baseline, rotating every 4 s — a single roaming bright spot.

**Corner Test (col 6, setup tool):** Test Corner 1–4 — one head at a time points at the
ball, the rest face outward. Use while aiming/adjusting the physical rig.

**Accents (col 7, hold to fire):**
- **White Bump** — heads snap white + full dimmer surge. Release = back to normal.
- **Strobe** — fast strobe on the heads.

## Still to verify on hardware

Color wheel and strobe are confirmed (see above) and the fixture def / scenes match.
Still unverified — generic guesses in the .qxf:

- **Gobo wheel** (Open + 7 gobos over 0–63, shake 64–127, auto 128–255).
- **P/T speed direction** (ch 9) — fast→slow vs slow→fast.
- **Ch 10 auto/sound ranges**.
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

If you find corrections, edit both copies of the .qxf (this folder **and**
`C:\Users\maxti\QLC+\Fixtures\`), then restart QLC+.
