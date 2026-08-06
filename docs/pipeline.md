# Live Previsualization Pipeline

Generic pipeline documentation — applies to any event in this repo. For a
specific show's patch, venue and rigging notes, see `events/<name>/README.md`.

QLC+ programs the show. BlenderDMX renders it in 3D in real time.
Art-Net (DMX over UDP) is the bridge between them.

```
  ┌──────────────┐   Art-Net (UDP)   ┌─────────────────────────────┐
  │    QLC+      │ ─────────────────▶│  BlenderDMX (Blender addon) │
  │  Universe 1  │    port 6454      │  Universe 0  (EEVEE/Cycles) │
  └──────┬───────┘                   └─────────────────────────────┘
         │ DMX USB                              ▲
         ▼                          test sender │
  Physical fixtures            shared/tools/artnet_sender.py
                               shared/tools/artnet_listener.py (verify QLC+)
```

> **Note:** this describes the *current* QLC+-based pipeline. The project is
> moving to a parametric Python show engine that outputs Art-Net/sACN directly;
> QLC+ stays runnable as the fallback until the engine reaches parity.


## Art-Net primer

Art-Net is the standard protocol for sending DMX over a regular WiFi/Ethernet network.

- **Transport:** UDP broadcast or unicast on **port 6454**
- **Universes:** Each universe carries 512 DMX channels. Universe numbering is
  **0-indexed** in Art-Net, BlenderDMX, and the scripts here.
- **QLC+ mapping:** QLC+ labels universes starting at 1 internally, but its
  Art-Net output plugin sends QLC+ Universe 1 as Art-Net universe 0, Universe 2
  as universe 1, etc.
- **Packet structure (ArtDmx):**
  - 8-byte ASCII header: `Art-Net\0`
  - 2-byte OpCode: `0x5000` (little-endian)
  - 2-byte protocol version: `14` (big-endian)
  - SubUni byte (lower 8 bits of universe address), Net byte (upper 7 bits)
  - 2-byte length (big-endian), then up to 512 bytes of DMX channel data


## Directory layout

```
lights/
├── docs/                     Generic pipeline documentation (this file)
├── shared/                   Everything reusable across events
│   ├── fixtures/             .qxf fixture definitions, one per hardware model
│   ├── gdtf/                 Generated GDTF profiles (build_gdtf.py)
│   ├── inventory.json        The units we actually own
│   └── tools/                Generic scripts (Art-Net, GDTF, patching, Blender)
└── events/
    ├── cosmos26/             One show: workspace, patch sheet, venue, 3D scene
    └── despacio/             Another show, with its own aim/calibration tooling
```

Anything that encodes *this room, this rig, or this night* belongs in an event
folder. Anything describing *hardware we own* or *a thing we do to any show*
belongs in `shared/`.


## Patch sheet

Each event has a `patch_sheet.csv` — the single source of truth for its patch.
Keep QLC+ and BlenderDMX in sync by always patching from this file.

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
in either, or a clean checkout will not resolve it.

Regenerate the GDTF profiles for BlenderDMX with:

```bash
python shared/tools/build_gdtf.py
```


## Setup: QLC+ Art-Net output

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


## Setup: BlenderDMX addon

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


## Scripts reference

### `shared/tools/artnet_sender.py`

Tests BlenderDMX reception **without QLC+ running**.

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

### `shared/tools/artnet_listener.py`

Confirms QLC+ (or any source) is emitting Art-Net.

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

### `shared/tools/validate_patch.py`

```bash
python shared/tools/validate_patch.py
# Prints summary, errors (overlaps, out-of-range), warnings (missing GDTF)
```


## Troubleshooting

| Symptom | Check |
|---------|-------|
| BlenderDMX shows no movement | Run `artnet_listener.py` — are packets arriving? |
| Listener shows packets but Blender doesn't respond | Blender Art-Net universe # matches QLC+ output universe? |
| QLC+ emits but listener shows nothing | Firewall blocking UDP 6454? Try: Windows Defender → Allow app → python.exe |
| Fixture colors wrong in Blender | GDTF channel order matches QLC+ fixture definition? Compare the `shared/gdtf/` profile against the `.qxf` in `shared/fixtures/`. Note `build_gdtf.py` hand-transcribes channel maps, so the two *can* drift. |
| Sender works, QLC+ doesn't | Check QLC+ output plugin: ArtNet selected? Universe set to 0? Output enabled (green indicator)? |
| Same machine, broadcast not working | Try `--ip 127.0.0.1` in sender; use `127.0.0.1` in QLC+ ArtNet output config |
| QLC+ can't find a fixture definition | Is its `.qxf` in `shared/fixtures/`, and copied to `~/QLC+/Fixtures/`? |


## Requirements

- Python 3.10+ (stdlib only — no pip installs needed for the scripts)
- Blender 3.3+ with BlenderDMX addon
- QLC+ 4.14+ with ArtNet plugin (included in standard install)
