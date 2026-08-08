# The Show Pipeline

Generic pipeline documentation — applies to any event in this repo. For a
specific show's patch, venue and rigging notes, see `events/<name>/README.md`.

**Art-Net is the whole architecture.** The engine broadcasts DMX over UDP; the
rig and the previz are both just things listening to it. Nothing sits between
the engine and the fixtures, which is why previz can never break a show and why
swapping either end is a config change rather than a rebuild.

```
  ┌──────────────────────┐                        ┌──────────────────────┐
  │  engine.server       │   Art-Net (UDP 6454)   │  Unreal previz       │
  │  40 fps frame clock  │ ──────────────┬───────▶│  previz/unreal       │
  │  Universe 0          │               │        │  (cosmos_live.py)    │
  └──────────┬───────────┘               │        └──────────────────────┘
             │ HTTP + WebSocket          │
             ▼                           ▼
      ui/dist (phone,             DMX interface ──▶ Physical fixtures
      tablet, laptop)

                          verify either end with
                     shared/tools/artnet_listener.py
                     shared/tools/artnet_sender.py
```

QLC+ programmed every show before the engine and stays runnable as the
fallback. Its path — QLC+ → Art-Net → BlenderDMX — is documented at the bottom,
under [The legacy QLC+ path](#the-legacy-qlc-path). Both sources emit the same
Art-Net, so everything from the primer down to the troubleshooting table
applies to either.


## Art-Net primer

Art-Net is the standard protocol for sending DMX over a regular WiFi/Ethernet network.

- **Transport:** UDP broadcast or unicast on **port 6454**
- **Universes:** Each universe carries 512 DMX channels. Universe numbering is
  **0-indexed** in Art-Net, in BlenderDMX, in the engine, and in the scripts here.
- **QLC+ mapping:** QLC+ labels universes starting at 1 internally, but its
  Art-Net output plugin sends QLC+ Universe 1 as Art-Net universe 0, Universe 2
  as universe 1, etc. The patch sheets carry both columns for this reason.
- **Packet structure (ArtDmx):**
  - 8-byte ASCII header: `Art-Net\0`
  - 2-byte OpCode: `0x5000` (little-endian)
  - 2-byte protocol version: `14` (big-endian)
  - SubUni byte (lower 8 bits of universe address), Net byte (upper 7 bits)
  - 2-byte length (big-endian), then up to 512 bytes of DMX channel data

`engine/output/artnet.py` emits this layout byte for byte identically to
`shared/tools/artnet_sender.py`, so `artnet_listener.py` decodes engine frames
with no changes — which is what makes frame-level diffing against QLC+ possible
without writing any new tooling.

**Only one sender at a time.** Two processes both emitting to 6454 means a
receiver samples whichever packet landed last, which looks like stuttering in
previz and like a fault on the rig. In particular, never leave
`artnet_listener.py` bound while going live in Blender — it squats on the port
the receiver needs.


## Directory layout

```
lights/
├── docs/                     Generic pipeline documentation (this file)
├── engine/                   The show engine — parameters, layers, frame clock
├── ui/                       The React console the engine serves
├── previz/                   Unreal previz (an Art-Net listener)
├── spike/                    The timing spike behind the frame clock
├── shared/                   Everything reusable across events
│   ├── fixtures/             .qxf fixture definitions, one per hardware model
│   ├── gdtf/                 Generated GDTF profiles (build_gdtf.py)
│   ├── inventory.json        The units we actually own
│   └── tools/                Generic scripts (Art-Net, porting, GDTF, Blender)
└── events/
    ├── cosmos26/             One show: workspace, patch sheet, venue, 3D scene
    └── despacio/             Another show, plus the engine's config trio
```

Anything that encodes *this room, this rig, or this night* belongs in an event
folder. Anything describing *hardware we own* or *a thing we do to any show*
belongs in `shared/`. Show logic that does not know which event it is running
belongs in `engine/`.


## An event's configuration

The engine loads three files from an event folder, split by *what makes them
change*. That split is the portability claim: a new room is a new `venue.json`
and nothing else moves.

| File | Holds | Changes when |
|---|---|---|
| `rig.json` | What is plugged in — each unit's address, universe, position and tags | The patch changes |
| `venue.json` | The room: walls, what hangs in it, where people's heads are | You are in a different room |
| `calibration.json` | What was measured on the night, per head | Someone nudges a light |

Two rules the loader enforces, both bought the hard way:

- **Looks reference tags, never fixture ids.** A look built for four corner
  movers runs on a club rig with eight because it asks for the `movers` tag and
  the rig resolves that to addresses.
- **A position does not make a fixture a geometry head.** Only a fixture with
  both Pan and Tilt in its mode gets one, because the head list is the
  calibration's index and head indices are positional. The pinspots are
  positioned so previz can draw them, and take no head index.

Channel meanings come from the QLC+ `.qxf` profiles in `shared/fixtures/` —
already the authority for both events and for the GDTF build — rather than from
a second description of the hardware that could drift.


## Patch sheet

Each event also has a `patch_sheet.csv` — the source of truth for the patch as
QLC+ and BlenderDMX see it, and what `rig.json`'s addresses are kept in step
with.

Key columns:

| Column | Meaning |
|--------|---------|
| `qlcplus_id` | Fixture ID in QLC+ |
| `dmx_start` | First DMX channel, **1-indexed** (DMX standard) |
| `channel_count` | Number of channels this fixture uses |
| `artnet_universe` | Art-Net universe (0 = QLC+ Universe 1) |
| `gdtf_profile` | Filename in `shared/gdtf/` |

Run the validator any time you edit a patch:

```bash
python shared/tools/validate_patch.py
```

With no arguments it validates every event. Use `--event <name>` for one show,
or `--csv <path>` for an arbitrary file. Output shows channel ranges, gaps,
overlaps, and missing GDTF profiles.


## Fixture library

`shared/fixtures/` holds one `.qxf` per hardware model, and is the source that
survives a fresh clone. `~/QLC+/Fixtures/` and the gitignored `qlcplus/` vendor
tree are fallbacks only — never let a profile the show depends on live solely
in either, or a clean checkout will not resolve it. The engine reads these
files directly, so a missing one is a hard failure at load rather than a
mis-rendered fixture.

Regenerate the GDTF profiles for BlenderDMX with:

```bash
python shared/tools/build_gdtf.py
```


## Porting a QLC+ library

`shared/tools/port_library.py` translates a workspace's stored functions into
the engine's look format, writing `events/<name>/looks.json`:

```bash
python shared/tools/port_library.py --write
```

The port is deliberately broad — everything the converter can translate, rather
than a hand-picked shortlist — because the reason most of the old library went
unused was that finding a scene on a phone was hard, not that the scenes were
bad. Three things change in translation, and all three are the point:

- **Scenes split by what they touch.** A stored scene that writes only the
  colour wheel becomes a *colour* look and one that writes pan/tilt becomes a
  *pose* look, so the two compose instead of every combination needing its own
  stored scene.
- **Positions become offsets** from each head's own calibrated ball aim,
  decoded through the same geometry the engine aims with — so a ported look
  survives recalibration and a different room.
- **Chases become paths in bars**, evaluated at the current musical phase, so
  slowing one down samples the path more finely instead of making it steppier.

Rerun the port after editing the workspace; `engine/tests/test_library.py`
checks the result round-trips.


## Previz

The Unreal previz has its own runbook in [`previz/README.md`](../previz/README.md)
— level build, the mirror ball, viewport settings, and the volumetric-fog
findings. Two things belong here because they are pipeline facts rather than
Unreal ones:

- **Previz is a listener and nothing else.** It is downstream of the wire, so
  it is independent of which source is driving and cannot delay a frame.
- **It does not use Unreal's DMX plugin, and does not read GDTF.** It decodes
  Art-Net with `engine.geometry`, the show's own decoder. The heads are mounted
  sideways — Pan carries elevation, Tilt carries bearing — which no fixture
  profile can express, so a GDTF-driven previz would articulate confidently and
  wrongly, and would keep doing so after every recalibration.

The host-side half runs with no Unreal installed at all, which is the first
thing to check when something looks wrong in 3D:

```bash
python previz/scene.py despacio -o scene.json
```

BlenderDMX remains the previz for the legacy path only.


## Scripts reference

### `shared/tools/artnet_listener.py`

Confirms the engine (or QLC+, or any source) is emitting Art-Net.

```bash
# Show all incoming traffic
python shared/tools/artnet_listener.py

# Watch only universe 0, print only when values change
python shared/tools/artnet_listener.py -u 0 --changed

# Monitor specific channels
python shared/tools/artnet_listener.py --watch 1,2,3,4,5

# Full 512-channel grid on every change
python shared/tools/artnet_listener.py -u 0 --changed --grid
```

### `shared/tools/artnet_sender.py`

Tests a receiver **with nothing else running** — no engine, no QLC+.

```bash
# Blackout universe 0
python shared/tools/artnet_sender.py --blackout

# Turn on a 5-channel fixture at ch 1 (dimmer, R, G, B, strobe)
python shared/tools/artnet_sender.py -c 1=255 -c 2=255 -c 3=0 -c 4=0 -c 5=0

# All channels at full (stress-test)
python shared/tools/artnet_sender.py --full --loop

# Slowly sweep 0→255→0 on all channels
python shared/tools/artnet_sender.py --sweep

# Send to specific IP (useful on networks that block broadcast)
python shared/tools/artnet_sender.py -c 1=255 --ip 192.168.1.50
```

### `python -m engine.demo`

The engine's own end-to-end smoke run — loads an event, evaluates a moving
look, and puts real frames on the wire. Useful as a signal source for previz or
for the listener.

```bash
python -m engine.demo                          # 5 s to a null output
python -m engine.demo --artnet 127.0.0.1       # to a listener on this machine
python -m engine.demo --seconds 30 --no-taper  # what the taper is holding back
```

### `python -m engine.calibrate`

`solve` derives position, offsets and invert flags from three captures per
head; `drift` flags which heads moved; `snap` / `diff` record and compare
snapshots; `jog` parks one head so you can look at it.

```bash
python -m engine.calibrate solve captures.json --write
python -m engine.calibrate jog --head 0 --pan 128 --tilt 200 --artnet 127.0.0.1
```

### `shared/tools/validate_patch.py`

```bash
python shared/tools/validate_patch.py
# Prints summary, errors (overlaps, out-of-range), warnings (missing GDTF)
```

### `shared/tools/port_library.py`

See [Porting a QLC+ library](#porting-a-qlc-library) above.


## Timing

The frame clock is Python's, and the F2 spike (`spike/timing/FINDINGS.md`)
settled that it holds comfortably: p99 interval error 0.046 ms and zero drops
over 7200 frames under contention, about 65× margin on the 3 ms threshold. Two
findings shape how you treat a show laptop:

- **External load is a non-issue.** Two busy background processes measured the
  same as an idle machine. A browser open during the show is fine.
- **In-process CPU-bound Python is the killer**, because a CPU-bound thread
  holds the GIL for a full switch interval. Don't do heavy work in the engine
  process.

Two settings are mandatory, not tuning — `sys.setswitchinterval(0.0005)` and
Windows' `timeBeginPeriod(1)`. Without either, the clock fails badly rather
than gracefully. `Runner.__init__` applies them itself rather than trusting a
caller to, and `engine.server` prints which ones took effect at startup.


## Troubleshooting

| Symptom | Check |
|---------|-------|
| Nothing moves anywhere | Run `artnet_listener.py` — are packets arriving at all? |
| Listener shows packets, receiver doesn't respond | Receiver's universe # matches the sender's output universe? Both are 0-indexed. |
| Engine runs but emits nothing | Was `--artnet` passed? Without it the engine runs against a null output on purpose. |
| Source emits but listener shows nothing | Firewall blocking UDP 6454? Try: Windows Defender → Allow app → python.exe |
| Beams stutter or jump | Two senders on 6454. A stray `artnet_listener.py` or a second engine counts. |
| Heads aim wrong after a nudge | Recalibrate: `python -m engine.calibrate drift …`, then `solve --write`. |
| Levels lower than authored | The safety taper is doing its job. Confirm with `engine.demo --no-taper`, and check `venue.json`'s crowd zone. |
| Engine won't load an event | A `.qxf` in `shared/fixtures/` is missing, or `rig.json`'s `mount_mode` disagrees with `calibration.json`'s — the loader names which. |
| UI loads but shows nothing | `ui/dist/` missing from the checkout. Rebuild with `cd ui && npm run build`. |
| Fixture colors wrong in Blender | GDTF channel order matches the QLC+ fixture definition? Compare the `shared/gdtf/` profile against the `.qxf` in `shared/fixtures/`. Note `build_gdtf.py` hand-transcribes channel maps, so the two *can* drift. |
| Same machine, broadcast not working | Use `127.0.0.1` instead — in the sender's `--ip`, the engine's `--artnet`, or QLC+'s ArtNet output config. |
| QLC+ can't find a fixture definition | Is its `.qxf` in `shared/fixtures/`, and copied to `~/QLC+/Fixtures/`? |


---

## The legacy QLC+ path

Everything below describes the pipeline the shows ran on before the engine. It
still works, and the workspaces stay runnable as the fallback.

```
  ┌──────────────┐   Art-Net (UDP)   ┌─────────────────────────────┐
  │    QLC+      │ ─────────────────▶│  BlenderDMX (Blender addon) │
  │  Universe 1  │    port 6454      │  Universe 0  (EEVEE/Cycles) │
  └──────┬───────┘                   └─────────────────────────────┘
         │ DMX USB
         ▼
  Physical fixtures
```

The reason it is being retired: QLC+ stores *values*. Nothing in that pipeline
knows where a beam lands, so nothing in it can guard one — which is what the
engine's safety taper exists to fix. Per-fixture colour, phrase-aware
automation, smooth interpolated motion and running one show in two rooms are
each combinatorially explosive as stored scenes, and all four were wanted.

### Setup: QLC+ Art-Net output

By default QLC+ outputs Universe 1 via DMX USB. You need to **also** enable
Art-Net output so BlenderDMX receives live values.

1. Open QLC+ and load the event's `.qxw` workspace.
2. Click the **Inputs/Outputs** tab (icon looks like plugs).
3. Find **Universe 1** in the list.
4. In the **Output** column, click the plugin selector dropdown.
5. Choose **ArtNet**.
6. Click the small **Configure** button (gear icon) next to the ArtNet output.
7. Set:
   - **Output address:** `255.255.255.255` (broadcast — works if QLC+ and Blender
     are on the same machine or the same LAN)
   - **Universe:** `0`
8. Click **OK / Apply**.
9. You can leave the DMX USB output enabled alongside Art-Net — QLC+ will send
   to both simultaneously.

> **Same machine?** If QLC+ and Blender run on the same Windows PC, use
> `127.0.0.1` as the output address instead of broadcast. Broadcast works either
> way on most setups.

### Setup: BlenderDMX addon

1. Download the latest release `.zip` from:
   https://github.com/open-stage/blender-dmx/releases
2. In Blender: **Edit → Preferences → Add-ons → Install…**
3. Select the downloaded `.zip`.
4. Enable **"BlenderDMX"** in the add-ons list (search for "dmx").
5. In the 3D viewport, press **N** to open the side panel. You should see a
   **DMX** tab.
6. In **DMX → Setup**:
   - Enable **Art-Net** receiver.
   - Set universe to **0**.
   - Port is fixed at 6454.
   - Click **Start** (or the toggle to activate it).
7. Add fixtures using **DMX → Fixtures → Add Fixture**:
   - Select the GDTF profile from `shared/gdtf/`
   - Set the universe and start address to match the event's `patch_sheet.csv`
   - Match the mode exactly (e.g. "10-Channel" for Scorpion Dual RGB)

Or patch the whole rig at once — set `EVENT` at the top of
`shared/tools/patch_blenderdmx.py`, then run it from Blender's Scripting
workspace.

### Blender helper scripts

Run these from Blender's Scripting workspace (**Open → Run Script**), not from
a shell:

| Script | What it does |
|---|---|
| `shared/tools/go_live.py` | Puts BlenderDMX into the live-receive state after a restart — the Art-Net receiver and the render timer, which do not always come back wired up together. Idempotent. |
| `shared/tools/tune_live_look.py` | Tunes the live EEVEE viewport so the rig reads as a light show while you program: matte floors, haze, brighter beam cones, punchy exposure. Run after `go_live.py`. |
| `shared/tools/patch_blenderdmx.py` | Patches a whole event's rig from its `patch_sheet.csv`. |

`shared/tools/gen_mirrorball.py` and `gen_checkerboard.py` generate scene
geometry and run from a normal shell.

### Requirements for this path

- Blender 3.3+ with BlenderDMX addon
- QLC+ 4.14+ with ArtNet plugin (included in standard install)
