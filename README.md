# Cosmos Lights — Live Previsualization Pipeline

QLC+ programs the show. BlenderDMX renders it in 3D in real time.
Art-Net (DMX over UDP) is the bridge between them.

```
  ┌──────────────┐   Art-Net (UDP)   ┌─────────────────────────────┐
  │    QLC+      │ ─────────────────▶│  BlenderDMX (Blender addon) │
  │  Universe 1  │    port 6454      │  Universe 0  (EEVEE/Cycles) │
  └──────┬───────┘                   └─────────────────────────────┘
         │ DMX USB                              ▲
         ▼                          test sender │
  Physical fixtures               scripts/artnet_sender.py
                                  scripts/artnet_listener.py (verify QLC+)
```

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

**Your rig:** All 13 fixtures live in **Art-Net universe 0** (QLC+ Universe 1).


## Directory layout

```
lights/
├── comsosLightsYear3.qxw   QLC+ workspace (existing)
├── stage.blend              Blender stage model (existing)
├── patch_sheet.csv          SOURCE OF TRUTH — fixture patch (see below)
├── gdtf/                    GDTF fixture profiles (.gdtf files)
│   └── README.md            Where to download + custom GDTF guide
├── scripts/
│   ├── validate_patch.py    Check patch_sheet.csv for overlaps / gaps
│   ├── artnet_sender.py     Send test DMX values to BlenderDMX
│   ├── artnet_listener.py   Print Art-Net traffic from QLC+ (or any source)
│   └── scaffold_venue.py    Blender bpy script — builds venue + fixture empties
├── renders/                 Render output (.png, .exr)
└── README.md                This file
```


## Patch sheet

`patch_sheet.csv` is the single source of truth. Keep QLC+ and BlenderDMX in
sync by always patching from this file.

Key columns:

| Column | Meaning |
|--------|---------|
| `qlcplus_id` | Fixture ID in QLC+ |
| `dmx_start` | First DMX channel, **1-indexed** (DMX standard) |
| `channel_count` | Number of channels this fixture uses |
| `artnet_universe` | Art-Net universe (0 = QLC+ Universe 1) |
| `gdtf_profile` | Filename in `gdtf/` — fill this in once you download profiles |

Run the validator any time you edit the patch:

```bash
python scripts/validate_patch.py
```

Output shows channel ranges, gaps, overlaps, and missing GDTF profiles.


## Current patch (Universe 0)

| DMX ch | Fixture | Mode | Channels |
|--------|---------|------|----------|
| 1–5 | Par 36 Custom #1 | 5 Channel | 5 |
| 7–11 | Par 36 Custom #2 | 5 Channel | 5 |
| 13–17 | Par 36 Custom #3 | 5 Channel | 5 |
| 19–23 | Par 36 Custom #4 | 5 Channel | 5 |
| 25–30 | ZQ-B93 Pinspot RGBW #1 | 6-channel | 6 |
| 31–36 | ZQ-B93 Pinspot RGBW #2 | 6-channel | 6 |
| 37–49 | DerbyLaserParty | default | 13 |
| 50–59 | Scorpion Dual RGB | 10-Channel | 10 |
| 61–64 | Mini Kinta IRC | 4-Channel | 4 |
| 70–73 | Dimmers #1–4 | 1 Channel | 1 each |

> Gaps (ch 6, 12, 18, 24, 60, 65–69) are intentional — they match the original
> QLC+ workspace and give room for mode changes.


## Setup: QLC+ Art-Net output

QLC+ currently outputs Universe 1 via DMX USB. You need to **also** enable
Art-Net output so BlenderDMX receives live values.

1. Open QLC+ and load `comsosLightsYear3.qxw`.
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
   - Select the GDTF profile from `gdtf/`
   - Set the universe and start address to match `patch_sheet.csv`
   - Match the mode exactly (e.g. "10-Channel" for Scorpion Dual RGB)


## Setup: Blender venue scaffold

Run this once to block out the space (additive — won't delete your existing
stage model):

1. Open `stage.blend` in Blender.
2. Go to the **Scripting** workspace.
3. Click **Open** and select `scripts/scaffold_venue.py`.
4. Click **Run Script**.

This creates a **"Cosmos DMX Rig"** collection with:
- Dancefloor plane (30 × 60 ft)
- Stage platform (15 × 15 ft, 2 ft high)
- Truss bars (front stage, back stage, center dancefloor)
- One Empty per fixture (with `dmx_start`, `dmx_universe` etc. as custom
  properties)

Use the empties as placement guides when adding GDTF fixtures in BlenderDMX.
Adjust positions in Blender as needed — fixture placement is just a starting
guess.

Alternatively, run headless and save:
```bash
blender stage.blend --python scripts/scaffold_venue.py --background
```


## Scripts reference

### `scripts/artnet_sender.py`

Tests BlenderDMX reception **without QLC+ running**.

```bash
# Blackout universe 0
python scripts/artnet_sender.py --blackout

# Turn on Par 36 #1 (ch 1-5: dimmer, R, G, B, strobe)
python scripts/artnet_sender.py -c 1=255 -c 2=255 -c 3=0 -c 4=0 -c 5=0

# All channels at full (stress-test)
python scripts/artnet_sender.py --full --loop

# Slowly sweep 0→255→0 on all channels
python scripts/artnet_sender.py --sweep

# Send to specific IP (useful on networks that block broadcast)
python scripts/artnet_sender.py -c 1=255 --ip 192.168.1.50
```

### `scripts/artnet_listener.py`

Confirms QLC+ (or any source) is emitting Art-Net.

```bash
# Show all incoming traffic
python scripts/artnet_listener.py

# Watch only universe 0, print only when values change
python scripts/artnet_listener.py -u 0 --changed

# Monitor specific channels (Par 36 #1)
python scripts/artnet_listener.py --watch 1,2,3,4,5

# Full 512-channel grid on every change
python scripts/artnet_listener.py -u 0 --changed --grid
```

### `scripts/validate_patch.py`

```bash
python scripts/validate_patch.py
# Prints summary, errors (overlaps, out-of-range), warnings (missing GDTF)
```


## Troubleshooting

| Symptom | Check |
|---------|-------|
| BlenderDMX shows no movement | Run `artnet_listener.py` — are packets arriving? |
| Listener shows packets but Blender doesn't respond | Blender Art-Net universe # matches QLC+ output universe? |
| QLC+ emits but listener shows nothing | Firewall blocking UDP 6454? Try: Windows Defender → Allow app → python.exe |
| Fixture colors wrong in Blender | GDTF channel order matches QLC+ fixture definition? Compare `gdtf/` profile against your `.qxw` fixture file. |
| Sender works, QLC+ doesn't | Check QLC+ output plugin: ArtNet selected? Universe set to 0? Output enabled (green indicator)? |
| Same machine, broadcast not working | Try `--ip 127.0.0.1` in sender; use `127.0.0.1` in QLC+ ArtNet output config |


## Requirements

- Python 3.10+ (stdlib only — no pip installs needed for the scripts)
- Blender 3.3+ with BlenderDMX addon
- QLC+ 4.14+ with ArtNet plugin (included in standard install)
