# Despacio Light Show

Minimal slow-disco show: 4× MingJie MJ-OS-018 60W beam moving heads **mounted sideways
in the 4 corners of a 30 ft square room, 10 ft up** (the disco ball hangs center, also
10 ft up — same height as the heads), aimed at the ball. 2× UKing ZQ-B93 RGBW pinspots
are also aimed at the ball — they are the **only fixtures in this rig that can do a
genuine smooth colour crossfade** (the heads have a mechanical 14-slot wheel and can only
jump between colours), so they carry the slow colour drift the heads physically cannot.
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

## Mounting convention assumed by `aim_calc.py`

Room = 30 ft (9144 mm) square, heads at 10 ft (3048 mm), ball also at 10 ft — heads and
ball are at the **same height**, so "look at the ball" comes out almost exactly level for
every head by symmetry, before any offset/inversion is applied. Room geometry (room size,
head/ball heights, corner positions) is **shared** between both mounting modes below — it
describes where the ball/floor/crowd targets are relative to each head, which has nothing
to do with which DMX channel encodes what.

### Three switchable mounting modes — `mount_mode` in `despacio_config.json`

`"table"` (bench simulation) · `"venue"` (sideways) · `"hung"` (real upside-down hang)

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
  - `pan_invert`/`tilt_invert` default **False** here, but confirmed 2026-07-29: a
    *mirrored* sideways mount (a head tipped onto its side the opposite way from its
    neighbors — e.g. cables still exiting the top, but the base rotated the other
    direction to get there) DOES invert that head's elevation per-head, on Pan. This is
    NOT the same class of thing as Circle's uniform-`pan_invert` requirement below — Pan
    carries **elevation** in `"venue"` mode, not bearing, so a per-head `pan_invert` here
    only flips that one head's up/down handedness and is expected to legitimately differ
    head-to-head on a mirrored rig. It's `tilt_invert` (venue's bearing channel) that must
    stay uniform for Circle. Confirm/flip per head via Corner Test once installed.
- **`"hung"` — real final installation, actually hung upside-down** (added 2026-07-25,
  because the mount is still an open decision). Identical channel roles and anchor to
  `"table"` — Pan = bearing (center-anchored), Tilt = elevation (zero-anchored) — because
  the fixture is physically the same way up in both. The *only* difference is
  `world_flip_elevation`, which is **False** here: `"table"` needs the flip because it is
  a simulation staged in an upside-down reference frame, whereas at a real hung install
  "down" is simply down, so elevation targets are used as computed.

**Which mount reaches higher? Both, near enough.** The sideways mount's headline appeal
is elevation throw, but the difference is smaller than it sounds:

| | `"hung"` (upside-down) | `"venue"` (sideways) |
|---|---|---|
| Elevation channel | Tilt, 270°, **zero-anchored** | Pan, 540°, **center-anchored** |
| Elevation range | −90° … +180° | ±270° |
| Straight up (+90°) | DMX ~170 | DMX ~170 |
| Level | DMX 85 — near a channel edge | DMX 128 — dead centre, symmetric headroom |
| Bearing range | ±270° (Pan) | **±135°** (Tilt) — why `walls` is range-capped |
| Verified on hardware | all bench testing to date | nothing yet |

The elevation clipping seen in `"table"` mode is a **calibration artifact, not a range
limit** — the bench reading was taken at `tilt = 0`, the mechanical stop, which is the
worst possible anchor point and leaves no headroom on that side. Sideways is the safer
model for extreme elevations; upside-down is the better-verified one and has twice the
bearing range. Either works for every routine in this show.

**Calibration data is per-mode and does not transfer between them** — a "table", "venue"
and "hung" reading calibrate three physically different transforms. `preflight.py` fails
if `mount_mode` is `"venue"` or `"hung"` while any head is still uncalibrated.
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

### Reset Heads (col 6, below Corner Test) — re-home the pan/tilt steppers

`fixtures/MingJie-MJ-OS-018-60W-Beam.qxf` exposes a **Reset** channel (channel index 10 in
the "11 Channel" mode this show uses — DMX 250–255, **held 5 s**, per the fixture's own
capability table). These are open-loop stepper moving heads with no absolute position
encoder, so "DMX value X = physical angle Y" is only true relative to wherever the head's
internal step-count reference currently is — if that reference gets knocked out of sync
(a skipped step, a bump, an interrupted power-down), the head will hold a real, calibrated
DMX value and still point somewhere physically wrong until it re-homes.

**Reset Heads** (Setup tab on the phone, or col 6 below the Test Corner buttons in QLC+) is
a hold-to-Flash button — added 2026-07-30 — that writes the Reset channel to 255 on all 4
heads for as long as it's held; hold it 5+ seconds, then release. It's phone/mouse-only,
deliberately **not** bound to an APC40 pad (adding one risks colliding with an existing
physical-console mapping without direct visibility into which pads are truly free — bind
one yourself via QLC+'s Input "MIDI learn" if you want it on the console).

If a head still drifts off its calibrated aim after a genuine power-cycle where the
fixture's own auto-homing already ran, that points *away* from a missed-reset issue and
toward something else worth checking instead: a loose/slipping mount bracket, DMX
patch/address mismatch (§ Setup below), or mechanical wear (backlash/skipped steps) on
that specific unit — Reset Heads is a quick first thing to try, not a guaranteed fix.

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

**Circle needs a *uniform* bearing-channel invert across all 4 heads** — it's a cycle
(MH1→MH2→MH3→MH4→MH1). Either uniform invert (all-true or all-false) gives a valid circle,
just clockwise vs counter-clockwise; a **mixed** setting is the one thing that breaks it
(some heads turn backward → adjacent heads point at each other). So never flip the
bearing-channel invert per-head to "fix" Circle — if the rotation looks reversed, flip
*all four* together. **Which flag this means depends on the mode**: `pan_invert` in
`"table"`/`"hung"` (Pan carries bearing there), but **`tilt_invert`** in `"venue"` (Tilt
carries bearing there — Pan carries elevation instead, and per-head `pan_invert` in
`"venue"` is fine, even expected, on a mirrored mount — see the `"venue"` mounting bullet
above). `preflight.py` warns if the active mode's bearing-channel invert list is mixed.

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
4. **`_self_test()` rejected a real, needed per-head `pan_invert` in `"venue"` mode**
   (found 2026-07-29, setting up the real venue install: MH1 and MH4 turned out mounted
   mirrored, needing `pan_invert["venue"] = [true, false, false, true]`). Bug #3 above
   fixed the *encoding* to correctly XOR each head's own hardware invert flag with
   `world_flip_elevation` — but the three direction-checking assertions this bug's own fix
   added to `_self_test()` (Floor-vs-Ball, Aerial-vs-Ball, Floor-sweeps-vs-Ball) still
   derived the expected DMX direction from `world_flip_elevation` **alone**, a leftover
   assumption from when every head in a mode shared one elevation handedness. The moment a
   real head needed a per-head override, `aim_calc.py` crashed with `AssertionError:
   Floor elevation-channel DMX ... is not < Ball's` — correct encoding, self-test asserting
   the old, too-narrow invariant. Fixed by extracting the effective-invert computation into
   one helper, `_elevation_invert(mode, head_i)` (the per-head hardware flag XORed with the
   mode's world-flip), and having **both** `compute_poses()` and `_self_test()` call it, so
   the two can never derive different answers again.

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
| 2 | MH Move | To Ball / To Floor / To Walls / **To Crowd** / Lazy Circle, small = Cross Weave; track-row Activator = **Wall Graze**; wide = Beam Open (toggle, non-solo) | PT Speed | Pan MH2 |
| 3 | MH Color Loops | Color Drift / Color Spin / **Rainbow Wheel**; small = Slow Pulse, track-row R = **Medium Pulse** (the two pulses exclude each other) | **Color Scroll** (raw wheel) | Pan MH3 |
| 4 | **MH Programs** | Diagonal Pulse / Corner Chase / Grand Sweep / **Slow Sweep** / **Ball Spiral**; then on col 4's own **track-row** buttons (R / S / Activator): **Floor Ring / Floor Wipe / Floor Cross**, plus **Floor Bounce / Floor Breathe** on col 7's borrowed track-row R/S | — | Pan MH4 |
| 5 | **SHOW** | **Warm Up / Idle / Deep / Peak / Landing** | — | Tilt MH1 |
| 6 | **Corner Test** | **Test Corner 1 / 2 / 3 / 4** (setup tool, see below); below the grid, **Reset Heads** — hold-to-Flash, phone/mouse only (deliberately not bound to an APC40 pad, see below) | — | Tilt MH2 |
| 7 | Accents | White Bump / Strobe — **hold-to-Flash**, no solo | — | Tilt MH3 |
| 8 | Ball + Dim | **Ball Wave / Circle / Neighbor Scan** (movement family, grid rows); small=**Prowl**, wide=**Crowd Cascade** (movement + dimmer accent); **Dim Chase / Spotlight / MH Breathe** (local dimmer group, track-row R = MH Breathe) | MH Gobo | Tilt MH4 |
| M | Master | Blackout (ch209) | Master Dimmer (submaster) | — |

The two global MH faders are the pair **MH Dim** (col 1) and **MH Strobe** (col 7) — same
size, same position in their column, one sets the floor and the other the shutter.

**Row 2 — extended looks (no APC column identity).** These live on the A/B row, clip
stops and scene-launch buttons rather than the 8×5 grid, so they are deliberately *not*
numbered as columns; grouping them into a second row is what keeps row 1 an honest
picture of the controller.

| Frame | Pads | Fader |
|---|---|---|
| **Aerial** | Apex / Cathedral / Zenith / Rise / Iris / **Canopy Ring** (movement family) — grid pads c3r5, c6r5, c7r5, c3r4, c7r4 + track-row col7 Select | — |
| **Split Color** | Split Warm/Cool / Split Red/Cyan / Split Pink/Green / Split Yellow/Blue / Split White/Orange / Split Red/Green / Duo Cyan/Pink / Duo Green/Blue (colour family) — pad c7r3 + wide 3–6 + track-row col6 R/S/Activator | — |
| **Color Extras** | **Yellow / Green / Cyan** (the three wheel slots that had no static) / **Quad Spectrum** / **Wheel Walk** (colour family) — track-row col3 S/Activator/Select + col4 Select + col6 Select | — |
| **Layer + FX** | Drift / Counter-Orbit / Shiver / Figure Eight / Diamond Weave (relative EFX layers — see below); Build / Drop (one-shots, non-solo) | Drift Width |
| **Pinspots** | Pin Glow / Pin Drift / Pin Amber / Pin Rose / Pin Magenta / Pin Indigo / Pin Teal / Pin Sea / Pin Rainbow / **Pin Split** (local group, one at a time); below them **Pin Breathe / Pin Strobe** (a second local group — both drive the pins' banded channel 0, so they exclude each other) | Pin Dim |
| **Dark Moves (MH Dim low)** | Teleport / Apparition / Freeze Frame / Stutter (movement + dimmer family — see below) | — |

**APC slot budget note.** Every grid pad, clip stop, A/B and scene-launch button on the
controller is now consumed; the additions above sit on the 4×8 **track-row** buttons
(R / S / Activator / Select per column), which is where the floor sweeps and the extra
splits already went. Formula: track *N* (1-indexed) → `R = 4096*(N-1) + 176`, `S = +177`,
`Activator = +178`, `Select = +179`. After this pass one track-row slot remains free
(col 8 Select, 28851).

**Right-hand panel:** the **Tempo** dial and the **Night** cue list sit at the top level of
the console, not inside a column — they are global transport, they apply to everything in
both rows, and they need the room.

### Global tempo — the Tempo dial

Every chaser used to have its step time frozen in XML. The **Tempo** dial now scales 18
chasers and 5 EFX at once.

**The dial holds ONE BAR. Tap it once per bar, on the downbeat.**

| APC control | Ch | Does |
|---|---|---|
| **Tempo encoder** | 13 | sets the bar length (1200–4000 ms = 200–60 BPM) |
| **Tap Tempo** | 227 | tap the downbeat; the dial takes the tap *interval* |
| **Nudge −** / **Nudge +** | 228 / 229 | ÷2 / ×2 — instant half-time / double-time |

Every routine is attached as a power-of-two **bar count**, so tapping the track puts all
of them on bar lines together:

| Tier | Routines |
|---|---|
| **2 bars** | Ball Spiral · Ball Wave · Canopy Ring · Corner Chase · Crowd Cascade · Dim Chase · Floor Bounce · Floor Breathe · Floor Cross · Floor Ring · Floor Wipe · Freeze Frame · Iris · Neighbor Scan · Pin Breathe · Prowl · Spotlight · **Grand Sweep · Lazy Circle · Slow Sweep · Shiver** |
| **4 bars** | Pin Rainbow · Rainbow Wheel · Rise · **MH Breathe · Wheel Walk** |
| **8 bars** | Color Drift · Cross Weave · Diagonal Pulse · **Figure Eight** |
| **16 bars** | Counter-Orbit · Drift · Pin Drift · **Diamond Weave** |

Fades sit one tier below their routine, so a fade is always about half its step — with a
deliberate set of exceptions where **fade = duration** (zero hold), because the routine is
meant to be continuous motion rather than a sequence of held states: **Pin Rainbow** (flows
through the hue wheel), **Lazy Circle / Grand Sweep** (trace a circle — a hold would make it
a polygon), **Slow Sweep** (a rake), **Wheel Walk** (the wheel is a stepper; the "fade" *is*
the wheel travelling) and **MH Breathe** (a swell, never a plateau).

**Lazy Circle, Grand Sweep and Slow Sweep moved tiers 2026-07-30** when they stopped being
EFX and became chasers. An EFX's `Duration` is one *whole revolution*; a `Common` chaser's is
one *step*. Leaving them at 16/8 bars would have made each of the 8 (or 4) steps that long
— a single revolution taking 128 bars. At 2 bars/step an 8-step orbit is a 16-bar
revolution, which is the musical length they had before.

**Teleport / Apparition / Stutter are deliberately off the Tempo dial**, unlike every other
chaser here — the same reason `Build` is off it. All three split each formation into a
*dark travel* step and a *lit hold* step; a shared `Common` duration would size both steps
identically, which meant the head finished travelling almost instantly but then sat dark
for most of the step before the light was even allowed to snap on — a dead, unlit pause
that was the opposite of "teleport." Fixed by giving each step **its own** duration
(`SpeedModes FadeIn="PerStep" Duration="PerStep"`, same pattern `Build` uses for its
accelerating steps): the dark step's length is exactly its travel fade (no lingering once
the head has arrived), and the "hold and look at it" time moves entirely into the lit
step, where it belongs. `validate_despacio.py` forbids attaching a `Duration="PerStep"`
chaser to the dial (`Chaser::tap()` is a no-op for anything but `Common` duration mode), so
these three run at their own fixed internal timing instead of tap tempo. **Freeze Frame**
never had this problem — its two diagonal pairs swap lit/dark roles every step, so the
single shared fade already reads correctly as "the arriving pair fades up while the
departing pair silently swings away" — and it stays a normal `Common` chaser on the dial.

**Why bars and not "keep each routine's original milliseconds":** `VCSpeedDial`
multipliers are only powers of two (1/16 … ×16), so an arbitrary millisecond target can
never be hit exactly anyway — but powers of two *are* musical divisions. Quantising to
2/4/8/16 bars costs nothing real (the old values were hand-picked, not sacred; the biggest
shift is 33%) and buys the whole show landing on the beat. At 120 BPM the tiers are
4 s / 8 s / 16 s / 32 s.

**Why tap per bar rather than per beat:** the ×16 ceiling. From a beat-length dial the
slowest reachable step is 16 beats = 4 bars, and the ambient textures here (Lazy Circle,
Drift, Pin Drift) need 16 bars. Tapping bars is the only unit that spans 2 → 16 bars in
one dial. The dial is captioned **"Tempo (tap 1 per bar)"** on screen so this isn't
something you have to remember.

Two behaviours that are engine limits, not bugs:

- **Tap does nothing on `Build`**, which is deliberately not attached. `Chaser::tap()`
  only advances a chaser whose duration mode is `Common`, and Build is `PerStep` — that
  is exactly what makes it accelerate. `validate_despacio.py` now errors if a PerStep
  chaser is ever attached to the dial.
- **The EFX follow the dial but ignore taps.** `Function::tap()` is a no-op for anything
  that isn't a Chaser or RGBMatrix.

### Night cue list

One GO button walks the whole night, so a guest operator never has to choose:
Warm Up → Idle → Deep → Spiral → Cathedral → Peak → Build → Drop → Landing (holds).

| APC control | Ch | Does |
|---|---|---|
| **Bank ►** / **Bank ◄** | 224 / 225 | next / previous cue |
| **Play** | 219 | play / pause |
| **Session** | 230 | stop |
| **Cross fader** | 15 | manual crossfade between cues |

### Drift / Counter-Orbit / Shiver / Figure Eight / Diamond Weave are LAYERS, not looks (row 2, Layer + FX)

These five are the only functions in the show meant to run *on top of* another look. They
are relative EFX: they write an **offset** onto whatever pan/tilt is already in the
universe buffer (zero point 127) rather than absolute positions, so they add motion to
any of the pose scenes instead of replacing it. Same construction for all five (only
`Algorithm`/`Width`/`Height`/`Duration`/per-fixture `Direction` differ — see
`despacio.qxw`'s `<Function Type="EFX">` blocks):

- **Drift** — barely-perceptible breathing, `Circle`, width 6, 30 s.
- **Counter-Orbit** — wider, `Circle`, width 28, 45 s, adjacent heads orbiting opposite
  directions so beams cross.
- **Shiver** (new 2026-07-30) — small and fast, `Circle`, width 3, 8 s: a nervous, tight
  shimmer, the quickest layer in the show.
- **Figure Eight** (new 2026-07-30) — `Eight`, width 14, 20 s: a genuinely different
  motion shape from the other four's circles, a lazy figure-eight weave.
- **Diamond Weave** (new 2026-07-30) — `Diamond`, width 20, 35 s, adjacent heads
  alternating `Forward`/`Backward` like Counter-Orbit: a boxier, slower cross-weave.

**One rule: never leave a layer running without a pose under it.**

Traced through the engine (`universe.cpp` `processFaders`/`writeRelative`,
`genericfader.cpp` `write`, `Universe::requestFader`), there are exactly three cases:

| What's running | What happens |
|---|---|
| Pose started **first**, then layer | **Correct.** The Scene's fader re-asserts its absolute pan/tilt every 20 ms tick, then the EFX adds its offset on top. Bounded, oscillating around the pose. |
| Layer started **first**, then pose | Harmless. The EFX writes, then the Scene overwrites absolutely — you just see no drift. Toggle the layer off and on to fix. |
| Layer running with **no pose** | **Bad.** With no absolute re-assertion, the EFX integrates on its own previous output. `processFaders` only zeroes *intensity* channels each tick, not LTP pan/tilt, so the offsets accumulate — a width-6 circle integrates to roughly ±1400 DMX of swing, i.e. the beams slam to a rail and sit there. |

The last case is only reachable by toggling the *active* pose off while a layer is up
(picking a different pose is safe — the MOVEMENT solo mesh swaps them without a gap).
Order is deterministic, not a race: `requestFader` inserts equal-priority faders after
the existing ones, so whichever function started first runs first, every tick.

They are deliberately **excluded from the MOVEMENT solo mesh** — mirroring them there
would make starting one cancel the very pose it is decorating. They only exclude each
other. The **Drift Width** fader (fader 5) is a QLC+ 5 `Adjust` slider on the EFX's Width
attribute, so it only bites while Drift is actually running; it will not start it.

**Renamed 2026-07-22: "Crowd" → "Walls".** The pose that turns outward, level, toward the
walls was always called "Crowd" but never actually pointed at people — it washes the
walls. It's now labeled **Walls**. The genuinely new **Crowd** (col 2) points straight
down at the people on the dancefloor, directly below each head. `Slow Sweep` moved from
col 2 to col 4 to make room; its function is unchanged.

### What the outward and downward poses actually hit (measured 2026-07-30)

Worth knowing before designing anything new against them, because two of these names
promise more than the geometry delivers. The heads sit in the room's four corners, inset
`head_inset` (500 mm) from each of their two walls, at the ball's own height.

- **`Walls` points into the head's own corner and terminates about 0.7 m away**
  (`head_inset × √2`). It turns 180° from the ball, i.e. directly *away* from room centre,
  and there is a wall right there. Same for **`orbit_p90`/`orbit_m90`** (Ball Spiral's
  quarter-turn steps) and, tilted up 45°, **`sky_out`** behind Cathedral. These are all
  perfectly good "park the beam off the ball" positions and they do uplight the
  corner/ceiling junction — but none of them is a wall wash, and in haze you see a stub of
  beam rather than a throw. **If a fabric canopy or drape ends up near a corner, keep this
  in mind:** a 60 W beam parked 0.7 m off it is a hot spot on fabric.
- **The directions that *do* rake a wall are `scan_prev`/`scan_next`** — the bearings
  toward each head's two wall-sharing neighbours. A corner head lies on the line between
  those two neighbours, so aiming at one aims *along* the shared wall, offset by
  `head_inset`, at head height: the beam grazes the wall's whole length. In this square
  room that works out to exactly ±45° off the ball bearing, but the poses are derived from
  the neighbour positions rather than a hardcoded 45 so they stay right if the room isn't
  square. **Wall Graze** (col 2) holds this statically; Cross Weave, Slow Sweep and Grand
  Sweep all now use it as their outer extreme.
- **`Crowd` (straight down) puts a pool in each of the four corners**, 500 mm off two
  walls — not on the dancefloor, which is in the middle. The name describes the intent, not
  the result. The **floor sweeps** (Ring / Wipe / Cross / Bounce / Breathe) are what put
  light where people actually stand; they aim at arbitrary floor points instead of straight
  down. Kept as-is rather than renamed: it is a genuinely useful "four corner pools" look
  and renaming it would churn three mirror sets, the phone UI captions and the validator
  table for a cosmetic gain.
- **Vertical reach is limited by whichever head has least travel left.** `Zenith` and
  `Crowd` no longer ask for a literal ±90°: `_fit_elev_extreme()` probes down from
  `elev_extreme_deg` (default 90) and returns the largest angle **every** head can reach,
  so all four stay identical. Under the current `"venue"` calibration that lands at 86°.
  Before this, heads 1 and 3 — whose ball point sits ~184° from their elevation channel's
  centre, leaving only ~86° before the rail — clamped 5.6° short on one extreme each while
  heads 2 and 4 hit 90° exactly: four beams visibly not doing the same thing. A
  `_self_test()` guard now asserts the spread across heads stays within a DMX step.
- **Bearing budget is nearly exhausted in `"venue"` mode.** The ball sits at −50.6° of a
  ±135° channel, so `Walls` (+180°) lands at +129.4°, 5.6° from the rail, and `orbit_m90`
  wants −140.6° and clamps on all four heads (the one entry in `_KNOWN_RAIL_POSES`). **No
  new pose can reach further out than Walls** — design within that.

Solo behavior (Year-3 hidden-mirror mesh): each column's pads are mutually exclusive
within the column, **and** cross-column conflicts auto-resolve along two independent
families — COLOR (col 1 statics + col 3 loops + the SHOW macros) and MOVEMENT (col 2
positions/loops + col 4 programs + col 8's **Ball Wave / Circle / Neighbor Scan / Prowl /
Crowd Cascade** + the Aerial row's poses + the Dark Moves frame's **Teleport / Apparition /
Freeze Frame / Stutter** + the SHOW macros). Pressing a SHOW look stops any running color or
movement loop; pressing a manual color stops a competing color loop, same for movement.
**Ball Wave, Circle, Neighbor Scan, Prowl, Crowd Cascade, Teleport, Apparition, Freeze
Frame, and Stutter** all live outside cols 2/4 but are full MOVEMENT-family members
(mirrored into cols 2, 4, 5, Aerial, and each other exactly like Slow Sweep/Ball Spiral),
since each writes Pan/Tilt like any other position routine and would otherwise visibly
fight whatever position pose is already running. ~240 hidden mirror buttons (no MIDI
input, parked off-screen inside each SoloFrame — `python validate_despacio.py` prints the
exact per-frame counts as `Mirror mesh: {...}`) implement this; don't delete them.

**Dim Chase and Spotlight (col 8) share a local dimmer-exclusivity group with Prowl,
Crowd Cascade, and the Dark Moves routines.** Dim Chase/Spotlight only ever write the
Dimmer channel (never Pan/Tilt or Color); **Prowl**, **Crowd Cascade**, and all four
**Dark Moves** routines write BOTH Pan/Tilt (as full MOVEMENT-family members) *and* a
continuous Dimmer boost as part of their normal loop. White Bump also touches Dimmer but
is a momentary Flash, already excluded from the mesh (same as Strobe). Since all eight
continuously drive Dimmer, they share one local `mh-extra-dim-solo` SoloFrame so starting
any one stops the others' brightness boost — every entry there besides Dim Chase/Spotlight
themselves is a hidden mirror only (their real, MIDI-bound buttons live in
`mh-extra-move-solo` and `mh-snap-solo` respectively, per the "one real button per
function" rule everywhere else in the mesh).

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
every 5 s, Ball Spiral every 3.5 s, Prowl every 3 s, Crowd Cascade every 4.5 s, Slow Sweep
cycles every 20 s, Lazy Circle every 30 s, Grand Sweep every 40 s. Switching between
routines with wildly different durations is
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

## Mobile web UI (`webui/`)

A phone-friendly virtual console (`webui/index.html` + `app.js`) that talks **straight to
QLC+'s own built-in web API** over `ws://<host>:9999/qlcplusWS` — `webui/serve.py` only
hosts the static page (`python webui/serve.py`, then load the printed LAN URL on a phone);
it never touches DMX or QLC+ itself. QLC+ must already be running with `--web`/`-w` and
`despacio.qxw` loaded. The page drives VC **widget** IDs, never function IDs, so pressing a
button still fires the whole hidden SoloFrame exclusivity mesh QLC+ already built.
`webui/ui_config.js` is generated from the live workspace by `webui/gen_webui_config.py`
(regenerated automatically by `serve.py` if `despacio.qxw` is newer); `webui/ui_layout.js`
is the hand-curated phone layout (tabs, dim-park classification, swatch colours) — see
that generator's module docstring for the add-a-routine workflow and what it lints.

**Protocol facts below came from reading the vendored QLC+ source
(`qlcplus/webaccess/src/webaccess.cpp`), not from watching this show's actual installed
QLC+ build talk on the wire — the vendored tree is the classic (non-qmlui) web-access
server, which every symptom recorded in `app.js` matches, but it hasn't been sniffed
against this project's real binary. Confirm before fully trusting either one; see "Still
to verify on hardware".**

- **Most of QLC+'s per-widget live-update pushes only start flowing after something has
  fetched `http://<host>:9999/` at least once.** `webaccess.cpp` wires each widget's
  change signal (`stateChanged`, `valueChanged`, `stepChanged`, …) **inside** the HTML
  generator for that widget type (`getButtonHTML()`/`getSliderHTML()`/`getCueListHTML()`),
  which only runs when the classic HTML page itself is built. A client that only opens the
  WebSocket (this one) never triggers that, so without doing anything about it, the *only*
  thing QLC+ ever broadcasts is `FUNCTION|<fid>|Running`/`Stopped` (wired once, globally, at
  server construction — this is why `runningFunctions` is the one signal `app.js` can always
  rely on). `app.js` works around this with a single, one-shot, fire-and-forget
  `fetch("http://<host>:9999/", {mode:"no-cors"})` right after connecting — **once per page
  load, not per reconnect**, since re-fetching re-arms (stacks) duplicate Qt signal
  connections and duplicates every future broadcast. Controlled by
  `localStorage["despacio.armPush"]` (`"0"` disables it) as a kill switch in case a
  different QLC+ build ever reacts badly to it; everything still works without it, just
  back to FUNCTION-broadcast-only (seed-on-load, no live external-move sync).
- **`SPEED_TIME` does reach this project's actual QLC+ install and does move the
  dial — confirmed live 2026-07-30.** This contradicts a read of the vendored
  `webaccess.cpp` source (whose widget-command switch has no
  `case VCWidget::SpeedWidget` at all, only `webaccess-qml.cpp` does), so either the
  installed build differs from that vendored tree or the reasoning missed something —
  either way, trust the live result over the source read. The manual ms entry and TAP
  both worked correctly; the two things that looked broken were real `app.js`/`despacio.qxw`
  bugs, both fixed 2026-07-30:
  - **TAP's median was biased slow.** With an even number of tap intervals (3 taps → 2
    intervals, the common case), `intervals[Math.floor(len/2)]` picks the sorted array's
    upper-middle element — always the *larger* of the two — instead of an average.
    Replaced with a plain mean, and a tap sequence now resets if the gap since the last
    tap exceeds the dial's own `maxMs` (previously an old tap from a while ago could
    splice a multi-second gap into the "interval" average).
  - **÷2/×2 capped out after one press.** The SpeedDial's `AbsoluteValue` range in
    `despacio.qxw` was `Minimum="1200" Maximum="4000"` — only a ~3.3× span — so a single
    literal doubling/halving from the ~2000ms default already overshot one rail, and a
    second press in the same direction did nothing (already clamped). Rather than change
    the ÷2/×2 *math* (half-time/double-time is the documented semantic — see "Global
    tempo" above), the range itself was widened to `Minimum="500" Maximum="8000"` (16×
    span, and 2000 is exactly its geometric mean), giving four full octave-presses of
    room in each direction before hitting a rail.
- **A button's `127` value means "Monitoring"** — running because a *different* widget
  bound to the same function fired it (the SoloFrame mirror mesh again), not because this
  widget was pressed. Treated as active (`v !== 0`) everywhere in `app.js`, same as `255`.
- **Pose/colour detection is structural, not a hand-kept caption list.**
  `gen_webui_config.py`'s `resolve_movement_and_color()` walks the same Chaser/Collection →
  Scene flattening the HTP dim-conflict scan already does, and flags a widget as a "pose"
  if any step it can reach writes a `Position`-group channel (Pan/Tilt), or a "colour look"
  if any step writes a `Colour`-group channel (this show's moving heads expose colour as a
  single Colour-Wheel-preset channel — see `MingJie-MJ-OS-018-60W-Beam.qxf` — not RGB
  intensities). Emitted as `VC.poseCaptions`/`VC.colorCaptions` and consumed by `app.js`'s
  "layer running with no pose under it" / "MH Dim up but no colour look active" banners.
  This replaced a hand-kept caption list that had already gone stale in practice: it
  excluded **Build** on the claim that it "writes no pan/tilt at all", which
  `despacio.qxw`'s actual Build Chaser steps (Walls → Iris Out → Ball → Apex → Zenith)
  disprove. `LAYER_CAPTIONS` (`Drift`/`Counter-Orbit`) stays hand-kept in
  `ui_layout.js`'s `layerCaptions` — EFX functions write no Scene `FixtureVal` at all, so
  "layer, not a pose" genuinely isn't derivable the same way — but it's now linted there
  against real widget captions, same as `dimPark`/`dimParkExempt`.
- **QLC+ host defaults to the page's own hostname** (`serve.py` and QLC+ assumed to be the
  same machine). Override with `?qlc=<host>` in the URL once — it's remembered in
  `localStorage` for future loads.

## Routines

**SHOW looks (col 5, the set-and-forget buttons).** **Warm Up, Idle and Landing also start
Pin Glow** (added 2026-07-30) — the pinspots' warm always-on base exists precisely so the ball
keeps some sparkle while the heads are pointed elsewhere, but no SHOW look actually included
it, so the ball went fully dark on every head transition. Deep / Peak / Spiral deliberately
don't: they're busy enough that a constant glow would just wash them.
- **Warm Up** — heads sunset (yellow+red) on a slow rake between the wall grazes.
- **Idle** — heads white orbiting the ball on a slow 20° circle. The signature look.
- **Deep** — heads blue weaving criss-cross (Cross Weave).
- **Peak** — 20 s color drift + Corner Chase movement.
- **Spiral** — Ball Spiral + Rainbow Wheel together (the collection that existed with no
  button on it until 2026-07-25).
- **Landing** — end of night: heads white, parked looking at the ball.

**Aerial (row 2) — the haze payoff.** Nothing in this show pointed above the mounting
plane before these; the maximum elevation anywhere was Ball Wave's +20°. With haze in the
room the beams themselves are the effect, and if a parachute canopy goes up above the
truss it becomes a large diffuse reflector that these looks light directly.
- **Apex** — all 4 converge on a point directly above the ball: a teepee of light over the
  dancefloor. Its height is `apex_height` in `despacio_config.json` — **set at the venue**
  to the canopy height if there's a canopy, otherwise the ceiling. It is the one geometry
  value the rigging decision actually changes.
- **Cathedral** — up *and* outward over the crowd, beams crossing high overhead. The
  "everyone looks up" moment; it's the aerial look wired into the Night cue list.
- **Zenith** — straight up (as near as every head can reach — see "What the outward and
  downward poses actually hit"). Four vertical columns standing in the corners.
- **Canopy Ring** (new 2026-07-30) — Floor Ring's mirror, drawn on the canopy/ceiling plane
  at `apex_height`: four pools on a circle of `canopy_sweep_radius`, each head's assignment
  rotating a quarter per step so they carousel. With a parachute canopy rigged this is the
  payoff — the canopy is a big diffuse reflector, so moving pools across it light the room
  indirectly instead of throwing hard beams. Exists because `_floor_point` was generalised
  to `_point_at(fx, fz, y)`; the floor sweeps go through the same helper at y=0 and their
  output was diffed byte-identical across that refactor.
- **Rise** — a slow vertical curtain: floor → ball → apex → zenith → back, all 4 in
  unison, passing through the ball twice a cycle so the specks pulse on each pass.
- **Iris** — converge/diverge: walls → ±45° → ball → ∓45° → walls. In haze this reads as
  four cones swinging out and tightening onto a single point on the ball.

Apex, Zenith and Cathedral sit at a **0° or 180° bearing offset** from the calibrated ball
point, which means they land on the same servo position whichever way a head's pan rotates
— so unlike Circle / Neighbor Scan they do **not** depend on the unresolved rotation-sign
question and are trustworthy the moment each head is ball-calibrated. Iris's ±45° pair is
sign-*tolerant*: the bearing-channel invert is uniform across heads within a mode (see
"Pan direction / inter-head geometry" above), so either sign gives a valid symmetric iris,
just mirrored.

**Split colour (row 2) — what a mirror ball is actually for.** Every colour scene before
these wrote the *same* wheel slot to all 4 heads. Two heads on the ball in different
colours throw two interleaved speck fields sweeping in opposite directions.
- **Split Warm/Cool** (orange ↔ blue), **Split Red/Cyan**, **Split Pink/Green**,
  **Split Yellow/Blue**, **Split White/Orange**, **Split Red/Green** — the two diagonal
  pairs (ID 0+2 and ID 1+3) get complementary slots.
- **Duo Cyan/Pink**, **Duo Green/Blue** — the wheel's two-colour slots, which are
  physically half-and-half in the beam, so a *single* head throws bicolour specks.
- **Quad Spectrum** (new 2026-07-30) — one slot per head: Red / Green / Yellow / Blue in
  fixture-ID order, which is already rotational around the room, so adjacent corners get
  contrasting hues and each diagonal pair gets a temperature. Four interleaved speck
  fields off the ball where a Split gives two. Everything before this was either
  all-four-the-same or two-by-diagonal; nothing used four.

**Colour statics that were missing** (new 2026-07-30, "Color Extras" frame in row 2):
**Yellow** (slot 25), **Green** (45) and **Cyan** (75). The wheel has had confirmed slots
for all three since the 2026-07-18 hardware check, but they only ever appeared *inside*
Split scenes — so there was no way to put all four heads on green short of dragging the
Color Scroll fader. Slot 105 (Orange+Green) is now the only one of the 14 confirmed
positions nothing uses.

**Wheel Walk** (new 2026-07-30, col 3's Loops) — an 8-step chase that advances every head
by exactly **one physical wheel slot** per step, with the heads two slots apart:
White → Red → Yellow → Blue → Green → Orange → Pink → Cyan and around. A one-slot hop is
the shortest move this stepper wheel can make, so it is the smoothest colour motion the
heads are capable of, and the beam passes through the boundary blend on the way. Fade =
duration, so the wheel never sits still. This is the logical end of the same
even-wheel-travel reasoning behind Rainbow Wheel's step order (below) — Rainbow Wheel hops
3–4 slots by design and reads as distinct colour changes; Wheel Walk reads as a drift.

**Dynamics (row 2, Layer + FX).**
- **Build** — an accelerating one-shot: walls → ±45 → ball → apex → ball → zenith →
  strobe, with each step shorter than the last (8 s down to 0.4 s). This is the one chaser
  in the show using `SpeedModes Duration="PerStep"`, which is also why tap tempo can't
  drive it.
- **Drop** — snap to white, then one head alone on the ball, held indefinitely. Pull MH
  Dim under it.

**Pinspots (row 2).** The heads' colour wheel can only *jump*; any "fade" is the wheel
spinning through the slots in between. The RGBW pins are the only fixtures here that can
crossfade for real. Colour/pattern picks are one at a time (`pin-solo`); **Pin Breathe** is
a separate standalone toggle that layers on top of whichever one is running.
- **Pin Glow** — a warm always-on base so the ball keeps some sparkle whenever the heads
  are pointed elsewhere.
- **Pin Amber / Pin Rose / Pin Magenta / Pin Indigo / Pin Teal / Pin Sea** — the six fixed
  colours that used to only exist as `Pin Drift`'s hidden steps, now each individually
  selectable. Each still carries its original 30 s fade-in when picked directly (matching
  this show's unhurried pace, not a snappy colour-picker feel).
- **Pin Drift** — the same six colours as a slow chase, 30 s fades, 45 s steps: four and a
  half minutes a cycle, never visibly moving. The most despacio thing in the file.
- **Pin Rainbow** — a smooth 12-step crossfade around the full hue wheel (every 30°),
  continuously flowing with no hold on any colour (its fade IS the full step — see the
  Tempo dial section for why that's the one routine that breaks the "fade one tier below"
  rule). ~72 s per revolution at the dial's default tempo. Pure colour math — no direction
  sign or hardware calibration involved, unlike the moving-head routines.
- **Pin Breathe** — a slow on/off pulse, independent of colour choice: boosts the pins'
  dimmer (channel 0) to full and lets it fall back over roughly a 24 s cycle at the dial's
  default tempo. Uses the same HTP boost-over-a-low-fader-baseline trick as Dim
  Chase/Spotlight/Prowl/Crowd Cascade — **set Pin Dim low first**, then Pin Breathe pulses
  on top of it. This is also why the fixed-colour scenes above no longer write channel 0
  themselves (they used to bake in `0,250`): stripped out so Pin Dim owns dimmer
  exclusively, the same "scenes never touch the dimmer, the fader does" convention already
  used for the moving heads' MH Dim fader. Combine it with any colour pick, or with Pin
  Rainbow, for a breathing rainbow ball.
- **Pin Split** (new 2026-07-30) — the first pin look where the two fixtures get *different*
  values. They aim at the ball from opposite sides, so a warm one and a cool one throw two
  counter-coloured speck fields sweeping in opposite directions. Every pin scene before this
  wrote both fixtures byte-identically: two fixtures, one voice.
- **Pin Strobe** (new 2026-07-30) — and this needs the channel map spelled out, because it
  is not where you'd guess. **Channel 5 is Auto FX, not strobe** (built-in programs +
  sound-active modes); it is deliberately parked at 0 in every pin scene. The strobe is a
  *value band* on channel 0, which is a mode selector rather than a plain dimmer:

  | ch 0 | meaning |
  |---|---|
  | 0–8 | off |
  | 9–134 | white dimmer |
  | 135–239 | RGBW strobe, slow → fast |
  | 240–255 | RGBW full on |

  That is why the **Pin Dim** fader is range-limited to `LowLimit=9 HighLimit=134` — so the
  operator can never drag it into the strobe band by accident. Pin Strobe writes 180
  (mid-band) as the deliberate way in. Because channel 0 is HTP, Pin Strobe beats the fader
  and Pin Breathe's 250 would beat Pin Strobe, so **Pin Breathe and Pin Strobe exclude each
  other** (`pin-ch0-solo`) — the same local-exclusivity reasoning as Slow/Medium Pulse on the
  heads' shutter. Strobing the pins freezes the speck field without touching the heads at
  all, which is the one mirror-ball move the rig couldn't do before.

### Gobo: why there are no gobo routines

The gobo wheel stays on the fader, at Open, on purpose.

A gobo doesn't do what you'd hope on a mirror ball. The speck field in the room is set by
the ball's **facet geometry**, not by the beam's cross-section — so a gobo doesn't texture
the specks, it just masks which facets get lit, making the field patchier and dimmer.
**Gobo shake** (an earlier note in this repo called it the best untapped effect here —
that was wrong for this show) makes the lit-facet set jitter at a few Hz. It's a nervous,
high-frequency effect, and nothing in despacio is nervous.

With haze it's worse, not better: a gobo'd beam in haze splits into a bundle of thin
mid-air beams, which is the single most recognisable "EDM club rig" signature there is —
the opposite of the warm, patient, one-big-ball look this show is built around.

**The one exception worth checking**: if the wheel has a **frost or soft-breakup** slot,
that would *soften* these 60 W beams, which is very on-vibe — the hard narrow beam is the
least flattering thing about this fixture for wall washes and the straight-down Crowd
pose. If a sweep of the Gobo fader turns up a frost, add exactly one thing: a **Soft**
scene parked on that slot. If not, leave it at Open and spend the effort elsewhere.

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
  both are now bundled as the **Spiral** button in col 5.

**Floor sweeps (col 4, on the track-row buttons).** Everything else that faced downward
aimed at exactly one spot — `floor` (the point under the ball) or `crowd` (straight down).
These aim at *arbitrary* points on the floor, which is what makes a sweep possible: a pool
of light that travels rather than sits. Radius is `floor_sweep_radius` in
`despacio_config.json` (default 2500 mm, comfortably inside a 30 ft room — raise it to
throw the pools nearer the walls). All three run 2 bars/step.
- **Floor Ring** — four pools on a circle around room center, carouselling around the
  room together. Each head walks the full circle over the 4 steps, one quarter per step.
- **Floor Wipe** — a straight *row* of four pools marching front ↔ back. Each head keeps
  its own x lane and only the depth changes, so the row holds formation; lanes are handed
  out by the head's own position so no head has to swing a pool across the whole room.
- **Floor Cross** — each head rakes its pool between the floor points under its two
  neighbouring heads, with the diagonal pairs in antiphase so the beams sweep past each
  other through the middle. Reuses the neighbour geometry from Circle / Neighbor Scan.
- **Floor Bounce** (new 2026-07-30) — reuses Wipe's own front/mid/back lane points (no new
  geometry), but swaps which diagonal pair is at which end each step (same antiphase `i%2`
  split as Floor Cross) instead of Wipe's lockstep march. Two pools converge toward the
  middle while the other two retreat toward the walls, then swap — a "breathing diamond"
  along the depth axis rather than a row holding formation.
- **Floor Breathe** (new 2026-07-30) — all 4 heads pulse together between a small radius and
  a large one, at each head's own fixed compass angle (0/90/180/270°, same angle set as
  Ring, just not rotating). The four pools stay in place and grow/shrink together instead
  of travelling — the one sweep character Ring/Wipe/Cross/Bounce didn't cover. Radius
  fractions (`FLOOR_BREATHE_NEAR_FRAC` = 0.4, `FLOOR_BREATHE_FAR_FRAC` = 1.6, both
  multiples of the same `floor_sweep_radius`) live in `aim_calc.py`, not the config file —
  they're a stylistic choice, not a site measurement.

Floor Bounce and Floor Breathe live in the same **col 4** frame as Ring/Wipe/Cross (column
4's own track-row R/S/Activator triplet was already spoken for by those three), so their
physical buttons borrow column 7's leftover track-row R/S instead — the same
"borrow another column's free track-row slot" pattern the Split Color statics and Medium
Pulse already used elsewhere in this show.

Like Iris, the sweeps use non-trivial bearing offsets, so they are sign-*tolerant* rather
than sign-insensitive: the bearing-channel invert is uniform across heads within a mode,
so a flipped sign mirrors the pattern (Ring spins the other way, Cross crosses the other
way, Bounce/Breathe mirror left-right) but never breaks it. `_self_test()` asserts every
floor-sweep pose (Ring/Wipe/Cross/Bounce's underlying points, and Breathe's near/far
points) encodes *downward* relative to Ball in all three modes — the exact mirror of the
aerial guard.

**Color loops (col 3):**
- **Color Drift** — MH wheel steps Orange → Pink → Blue → Red, 20 s per step.
- **Color Spin** — the wheel's own slowest continuous color rotation (DMX 141).
- **Rainbow Wheel** (new 2026-07-22, re-spaced 2026-07-23) — MH wheel cycles
  Red → Orange → Pink+Orange → Blue+Yellow, 8 s/step, phase-offset by 1 step per head so
  all 4 show different-but-adjacent hues at any moment, continuously rotating between
  heads. Deliberately a *different* period than Ball Spiral (8 s vs 3.5 s) so the combined
  color/position pattern keeps evolving rather than repeating in lockstep.
  **Step order is chosen for even wheel travel, not strict hue grouping:** the wheel's 14
  positions aren't evenly spaced by color family (the 4 "warm" slots — Red, Yellow,
  Orange, Yellow+Red — sit bunched in one arc of the wheel with nothing warm opposite
  them), so the original Red → Yellow+Red → Yellow → Orange order made two of the four
  hops nearly a full wheel rotation (bands 1→13 and 5→1, ~10-12 of 14 positions) while the
  other two were short (~3 positions). On this fixture's stepper wheel that reads as a
  stall/catch-up on the long hops and a snap on the short ones, i.e. uneven pacing that
  looks like a pause at the end of the cycle. The current order (bands 1, 5, 9, 12) moves
  monotonically forward around the wheel with gaps of 4, 4, 3, 3 positions — even whether
  the wheel seeks the short way round or (as budget wheel motors often do) only spins
  forward.

**Building blocks / loops** — all four rebuilt 2026-07-30, see the box below:
- **Lazy Circle** — the ambient signature: each head walks an 8-point circle of radius
  `LAZY_ORBIT_RADIUS_DEG` (20°) around *its own* calibrated ball point, with the four
  heads a quarter-revolution apart. 2 bars/step, fade = duration, so it never holds.
- **Grand Sweep** — the same machinery at `GRAND_ORBIT_RADIUS_DEG` (45°), so its
  horizontal extremes land exactly on the wall-graze bearings. A big slow carousel.
- **Slow Sweep** — a horizontal rake between each head's two wall-graze bearings,
  passing through the ball on the way, with the diagonal pairs half a cycle apart
  (the same `i % 2` antiphase Floor Cross uses).
- **Cross Weave** — the two diagonal pairs alternating between their two wall-graze
  bearings, so four beams rake the four walls and pinwheel past each other.

> **These four were the show's one real defect, found 2026-07-30.** Lazy Circle, Slow
> Sweep and Grand Sweep were absolute-DMX EFX centred on channel 127, and Cross Weave's
> two step scenes (`Heads Cross A`/`B`) were hand-picked absolute DMX. A QLC+ EFX has
> **one global centre and no per-fixture offset**, so none of them could follow a rig
> whose heads have different calibrated ball points. In `"venue"` mode the two mirrored
> pairs sit ~103 DMX apart on the elevation channel, which meant two heads orbited the
> ball while the other two pointed ~180° away into their own corners; Grand Sweep and
> Cross Weave also pushed past the ±270° pan rail and sat pinned there for part of the
> cycle. Because these fed **Warm Up, Idle and Deep**, three of the six SHOW looks were
> affected.
>
> The old note here said the hand-picked values were "fine since they're stylistic, not
> aimed at a real target". That was true when all four heads were assumed identical and
> false the moment they weren't — and it is exactly why nobody looked. Neither
> `aim_calc.py`'s self-test nor the reachability report could see it either: both only
> examine poses `aim_calc` *generates*, so anything hand-authored was invisible to the
> whole toolchain.
>
> All four are now derived from the calibrated poses. `validate_despacio.py` enforces
> this going forward: **every Scene that writes Pan/Tilt must be reachable from
> `build_targets()`**, and **every EFX must be `IsRelative=1`** (an absolute one cannot be
> per-head correct on this rig). Both allow-lists — `HAND_PICKED_POSITION_FUNCTIONS` and
> `ABSOLUTE_EFX_ALLOWED` — are deliberately empty; adding an entry is an explicit
> statement that a function will not track recalibration.
- **Slow Pulse** — slowest shutter blink (~lazy heartbeat) on the heads, shutter ch = 12.
- **Medium Pulse** — the step up from it, shutter ch = 45. Deliberately biased toward the
  slow end of the 8–249 strobe band rather than sitting mid-scale, because a true midpoint
  reads as a strobe rather than a pulse in a show pitched this slow. The rate curve is
  still a generic guess in the .qxf, so tune the number against hardware. The two pulses
  **exclude each other** (`mh-pulse-solo`) — both write the shutter continuously, so
  without that, toggling one off while the other was on would strand the shutter at the
  loser's value. The **MH Strobe** fader (col 7) covers the whole continuum; these two are
  just the presets worth having on a pad.

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

**Prowl (col 8, new 2026-07-23)** — reuses Ball Spiral's exact position choreography
(`aim_calc.py`'s `SPIRAL_SEQ`, phase-offset per head: ball → orbit → walls → orbit) but
adds a Dimmer boost: whichever head is in its Ball phase that step is driven to full
brightness and holds still there; the other 3 heads (mid-sweep through the orbit/Walls
positions) are left with Dimmer unwritten, so they read as dark against the low MH Dim
baseline — same HTP boost-over-a-fader trick Dim Chase/Spotlight use. Net effect: heads
move while dark, freeze lit only when they land on the ball. Since exactly one head is
in the Ball phase every step, at least one head is always lit. 2 s fade / 3 s per step.
Ball-relative like Ball Wave, so — unlike Circle/Neighbor Scan — it bench-tests correctly
on the desk rig, no venue-only restriction.

**Crowd Cascade (col 8, new 2026-07-23)** — a slower, more dramatic cousin of Prowl built
around the Ball → Crowd (straight-down) tilt instead of Ball Spiral's orbit. Each head
cycles through 4 phase-offset states: lit at Ball → lit while tilting down to Crowd (the
visible staggered sweep) → Dimmer left unwritten so it goes dark once it arrives at Crowd
→ position reset back to Ball while still dark (Dimmer stays unwritten, so the reset
itself is invisible) → repeats, snapping back on at Ball. Phase-offset by 1 step per head
means 2 heads are always lit (one arriving at Ball, one sweeping down to Crowd) and 2 are
always dark (one holding at Crowd, one resetting) at any instant — never all-on or
all-off. 3 s fade / 4.5 s per step. Also Ball/Crowd-relative, so no venue-only
restriction.

Both routines only ever push a head's Dimmer **up** past the MH Dim fader (HTP) — set the
fader to a low/dim baseline before triggering either, same as Dim Chase/Spotlight, or the
"dark" heads will just look constantly lit.

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
  - **Spotlight** — the inverse: only 1 head boosted to full each step, rotating every 4 s.
    **Three-tier since 2026-07-30**: the featured head at 255, its two *adjacent* corners at
    110, and the far corner left unwritten. Fixture-ID order is rotational around the room,
    so `(k±1)%4` really are the neighbouring corners and `(k+2)%4` really is the opposite
    one — the boost now falls off spatially instead of being a hard on/off. Until then
    **every Dimmer write in the entire show was 255**: the dimmer vocabulary had exactly two
    values in it, "fader baseline" and "full", with nothing in between anywhere.

**MH Breathe (col 8, new 2026-07-30)** — the heads' answer to Pin Breathe, and the one
obviously-missing dimmer idea: all four swell to full together and fall back, over a 4-bar
fade with no hold (fade = duration). Step 2 writes nothing at all, so the fall *is* the MH
Dim baseline showing through — same HTP mechanism as everything above. Joins the local
dimmer-exclusivity group with Dim Chase/Spotlight. The phone UI auto-parks MH Dim to 48
rather than 0 for this one: at 0 the trough is a full blackout every cycle, which reads as
a fault rather than a breath at this show's pace.

> **The MH Dim fader now saves at 70, not 255** (changed 2026-07-30). Everything in this
> section only works as a boost *above* the fader — HTP can raise a head past the fader but
> never pull it below. The workspace used to load with MH Dim at 255, which is precisely the
> one position where all nine of these routines, and the entire "Dark Moves" frame, are
> silent no-ops. `preflight.py` now warns if it drifts back above 128.

**Dark Moves (new 2026-07-29, extended 2026-07-30) — the inverse of every routine above.**
Everything so far moves *lit*: you watch the beam sweep from one position to the next.
These seven routines instead go dark for the travel and only turn on once a head has
arrived and is holding still — in a hazed room this reads as the beams *teleporting*
between formations rather than sweeping through them. Same underlying mechanism as
Prowl/Crowd Cascade above (the fixture has no shutter blackout, so "dark" is Dimmer left
unwritten against a low **MH Dim** baseline — hence the frame's name) but here it's the
entire point of the look rather than an accent. **Set MH Dim low before triggering any of
these**, exactly as for Dim Chase/Spotlight/Prowl/Crowd Cascade, or a head that's supposed
to be "dark and travelling" will just look like an ordinary lit pan/tilt move.
- **Teleport** — all 4 heads in unison: 0.8 s dark travel → snap on together, hold 3.2 s at
  the ball → 0.8 s dark travel → snap on at zenith (straight up), hold 3.2 s → …walls
  (outward)… →crowd (straight down)→ repeat. Maximum contrast between stops,
  on-then-off-then-on like the room itself is teleporting between four totally different
  formations. Runs at its own fixed internal timing, not the Tempo dial (see "Global
  tempo" above for why).
- **Apparition** — the exact same 8 scenes as Teleport, chased with a slow 3 s fade-up
  instead of a snap: beams *materialize* into each formation rather than appearing
  instantly (the same "reuse the scenes, only the chaser differs" idiom `Rise` uses on the
  aerial poses). No new scenes of its own; also off the Tempo dial.
- **Freeze Frame** — the two diagonal pairs (heads 1+3, heads 2+4) alternate: one pair
  holds lit and still on the ball while the other travels dark to the walls, then they
  swap, every 4 s. Exactly 2 beams lit at all times, and — unlike Teleport/Apparition —
  never a beam caught mid-sweep: whichever pair moved last step is now the one standing
  still. The only Dark Move still on the Tempo dial (2-bar tier) — tap tempo re-times it
  like everything else.
- **Stutter** — a small, fast stop-motion bob between the same ±20° wave points Ball Wave
  uses, but hopped dark instead of crossfaded lit: a 150 ms dark hop → snap on, hold
  700 ms → repeat. Deliberately a small move — a wide swing can't complete inside a hop
  this short the way this ±20° one can. Also off the Tempo dial.
- **Glitch** (new 2026-07-30) — reuses Teleport's exact same 8 scenes, with **dark-travel**
  times of 800/900/800/1000 ms (at or barely above Teleport's own proven 800 ms floor for
  these same room-scale moves — never below it) and short, irregular **hold** times
  (700/1000/600/1300 ms): a broken, stuttering-through-space feel that stays snappy overall
  because the pacing knob is the (freely adjustable) hold, not the travel time. Cheapest of
  the three new routines — no new scenes or poses at all, just a different chaser. Off the
  Tempo dial (`PerStep`, like Teleport/Apparition/Stutter).
- **Ascension** (new 2026-07-30) — a unison dark-travel *climb*, not a loop: straight down
  at the crowd → the ball → the aerial apex → straight up at the zenith, 1 s dark travel /
  2.5 s hold per stop. Reaches poses Teleport never does (apex, zenith) for a "rising to the
  ceiling" arc rather than an arbitrary 4-stop cycle, without dragging the whole loop out.
  Off the Tempo dial.
- **Blink** (new 2026-07-30) — reuses the same 4 floor points Floor Ring sweeps through
  (`floor_ring_0..3`), but as a unison dark-travel/lit-arrive cycle instead of a continuous
  crossfade: "the carousel that teleports" rather than sweeps, 1.5 s dark travel / 2.5 s hold
  per stop. No new geometry — same points, different motion character. Off the Tempo dial.

**Two lessons from tuning these three, worth keeping for any future Dark Move:**
1. **Dark-travel time is a safety floor, not a style knob.** It only needs to be long enough
   for the head to physically finish arriving before the lit step's Dimmer flips on —
   cutting it below the *shortest* value an established routine already proves safe for a
   similarly-sized move risks the lit step catching the head still mid-travel (the very
   first bug here: it turned on too early, not that it moved too slowly). Match or exceed
   the closest precedent (Teleport/Apparition's 800–1500 ms for room-scale ball/zenith/
   walls/crowd jumps; Stutter's 150 ms only because its own move is a tiny ±20°) — but don't
   pad it further than that "just in case." The reveal snap comes from the lit step's own
   `FadeIn="0"`, not from a long dark step.
2. **Hold time is the actual pacing knob, and it's free to tune** — no motion happens during
   a hold, so shortening it can't reintroduce the "still arriving" bug. When a routine feels
   sluggish overall, look at hold times first, not dark-travel times: the first pass here
   over-corrected by also inflating dark-travel well past the safety floor it needed to hit
   (Blink to 3 s, Ascension to 1.5 s dark / 4 s hold), which made every cycle noticeably
   slower without actually fixing anything the shorter dark-travel value hadn't already
   fixed.

All seven are Ball/Zenith/Walls/Crowd/Wave/Apex/floor_ring-relative like Ball Wave and
Prowl (not inter-head like Circle), so they bench-test correctly on the desk rig — no
venue-only restriction.

**Corner Test (col 6, setup tool):** Test Corner 1–4 — one head at a time points at the
ball, the rest face outward. Use while aiming/adjusting the physical rig.

**Accents (col 7, hold to fire):**
- **White Bump** — heads snap white + full dimmer surge. Release = back to normal.
- **Strobe** — fast strobe on the heads.

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
- **Pinspot colours weren't triggering at all (found 2026-08-01, reported as "colours
  never change").** Root cause: channel 0 isn't a plain dimmer, it's a **mode selector**
  (per the shipped `UKing-ZQB93-Pinspot-RGBW.qxf`): 0–8 off, **9–134 = "White Dimmer"**,
  135–239 = RGBW strobe, **240–255 = "RGBW On"**. **Pin Dim** was ranged to `LowLimit="9"
  HighLimit="134"` — the White Dimmer band — while every colour scene (Pin Amber/Rose/
  Magenta/Indigo/Teal/Sea/Split) only ever writes channels 1–4 (R/G/B/W) and never touches
  channel 0. The moment Pin Dim was touched, ch0 sat in White Dimmer mode and the fixture
  stopped honouring the R/G/B/W values entirely — every colour pick looked identical
  regardless of which button was pressed. Fixed by reranging Pin Dim to `LowLimit="240"
  HighLimit="255"` (the RGBW On band), matching **Pin Breathe**'s boost value (250), which
  was already correctly inside that band. **Not yet confirmed on real hardware** — same
  caveat as everything else in this section.
- **Pin Dim's real dimming range, post-fix.** RGBW On (240–255) is a single named
  capability across all 16 values with no documented sub-gradient, so unlike before, Pin
  Dim is now effectively a narrow on/near-off toggle at the very top of its fader throw
  rather than a smooth dimmer — this fixture appears to have no true continuous master
  dimmer over its RGBW output at all (only the earlier White Dimmer band was continuous,
  and that band ignores colour). If finer brightness control over the pins is wanted later,
  it'll need to come from scaling each colour scene's own R/G/B/W values down directly,
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
  something else (a colour pick, or nudging Pin Dim) overwrites ch0, depends on
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
