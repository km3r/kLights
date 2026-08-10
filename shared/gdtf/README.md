# GDTF Profiles

> **Retired with the BlenderDMX path.** These served the QLC+ era previz; the
> current previz reads `rig.json` directly and needs no GDTF at all. See
> [`legacy/blenderdmx/`](../../legacy/blenderdmx/README.md), and note the
> warning there about hand-transcribed channel maps.

All six profiles in this folder are **auto-generated** from the QLC+ `.qxf`
fixture definitions by `legacy/blenderdmx/build_gdtf.py`. Channel order,
counts and mode names match the patch sheet exactly. To rebuild after editing the channel maps:

```bash
python legacy/blenderdmx/build_gdtf.py
```

A `.gdtf` file is just a ZIP containing a `description.xml` (GDTF 1.2 schema).
The generator maps each DMX channel to a GDTF attribute: rendered channels use
standard names (`Dimmer`, `ColorAdd_R/G/B/W`, `Shutter1Strobe`, `Pan`, `Tilt`,
`Zoom`); control/effect channels (patterns, motors, macros, laser) get
addressable-but-not-rendered control attributes. All profiles parse cleanly in
`pygdtf`, the same library BlenderDMX uses.

| File | Mode | Ch |
|------|------|----|
| `UKing@Par 36 Custom@5 Channel.gdtf` | 5 Channel | 5 |
| `UKing@ZQ-B93 Pinspot RGBW@6-channel.gdtf` | 6-channel | 6 |
| `Amazon@DerbyLaserParty@default.gdtf` | default | 13 |
| `Chauvet@Scorpion Dual RGB@10-Channel.gdtf` | 10-Channel | 10 |
| `Chauvet@Mini Kinta IRC@4-Channel.gdtf` | 4-Channel | 4 |
| `Generic@Dimmer@1 Channel.gdtf` | 1 Channel | 1 |

## Where to download (alternative)

If you'd rather use vendor-supplied profiles, the official source is
https://gdtf-share.com — search by manufacturer and model name.

## Your fixtures

| Fixture | Search term | Notes |
|---------|------------|-------|
| UKing Par 36 Custom | `UKing Par` | Custom/generic — may not have official GDTF. Use a generic Par GDTF or create one with the GDTF Builder (gdtf-share.com/builder). Channels: Dimmer, R, G, B, Strobe |
| UKing ZQ-B93 Pinspot RGBW | `UKing ZQ-B93` | Custom/generic. Channels: Dimmer, R, G, B, W, Strobe |
| Amazon DerbyLaserParty | N/A | Custom. No official GDTF likely. Use a Generic Effect profile or DMX Emitter type in BlenderDMX. |
| Chauvet Scorpion Dual RGB | `Chauvet Scorpion Dual` | Chauvet is well-supported on gdtf-share.com |
| Chauvet Mini Kinta IRC | `Chauvet Mini Kinta IRC` | Chauvet is well-supported on gdtf-share.com |
| Generic Dimmer | Built-in | Use BlenderDMX's built-in "Generic Dimmer" or a simple 1-channel GDTF |

## Creating a custom GDTF profile

1. Go to https://gdtf-share.com/builder
2. Fill in manufacturer, model, firmware version
3. Add DMX modes matching your `mode` column in `patch_sheet.csv`
4. For each channel, add a DMX channel with the correct function
   (Dimmer, ColorAdd_R, ColorAdd_G, ColorAdd_B, etc.)
5. Export as `.gdtf` and save here

## Naming convention

BlenderDMX will look for files matching:
  `<Manufacturer>@<Model>@<Mode>.gdtf`

Example:
  `Chauvet@Scorpion Dual RGB@10-Channel.gdtf`
