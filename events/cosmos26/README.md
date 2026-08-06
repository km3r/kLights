# cosmos26 — ARCHIVED

The Year-3 Cosmos rig. This show is **retired**: it is preserved so it can be
opened and referenced, but it is not maintained against the current tooling.
Cosmos runs yearly, but each year's lighting plan changes — the durable assets
are the fixture inventory in `shared/fixtures/` and the tools in
`shared/tools/`, which the next year draws on à la carte.

For generic pipeline setup (Art-Net, BlenderDMX, the scripts), see
[`docs/pipeline.md`](../../docs/pipeline.md).

## Contents

| Path | What it is |
|---|---|
| `comsosLightsYear3.qxw` | The QLC+ workspace as it was last run |
| `patch_sheet.csv` | Intended patch — **see the drift note below** |
| `APCCheatSheet - NewLayout.csv` | APC40 mkII physical layout: 8 columns × (knob, buttons, fader) |
| `CAPTURE_SETUP.md` | Alternate previz path via Capture Student Edition |
| `scaffold_venue.py` | Blender venue blockout — hardcodes this room's geometry |
| `position_fixtures.py` | Re-places fixtures against the hand-built `scene/stage.blend` |
| `scene/` | Blender models and exports (mostly gitignored — large and regenerable) |

Filename typo `comsosLightsYear3.qxw` is preserved as-is rather than renamed,
since it is what the archived workspace has always been called.

## ⚠️ Patch drift: the sheet and the workspace disagree

`patch_sheet.csv` and the old README described **13 fixtures**. The workspace
as actually last saved has **11**, and they are not the same 11:

| | In `patch_sheet.csv` | In `comsosLightsYear3.qxw` |
|---|---|---|
| Par 36 Custom #1–4 | ✅ ch 1, 7, 13, 19 | ✅ same |
| ZQ-B93 Pinspot #1–2 | ✅ ch 25, 31 | ✅ same |
| DerbyLaserParty | ✅ ch 37 | ❌ **not patched** |
| Scorpion Dual RGB | ✅ ch 50 | ✅ same |
| Mini Kinta IRC | ✅ ch 61 | ✅ same |
| Dimmers #1 | ✅ ch 70 | ✅ same |
| Dimmers #2–4 | ✅ ch 71–73 | ❌ **not patched** |
| YeeSite 60W RGB Pixel Bar ×2 | ❌ absent | ✅ **ch 75 and ch 86** |

So the sheet lists a derby and three dimmers that the show does not use, and
omits the two light bars that it does. The workspace is the accurate record.

Model names drift too. The workspace patches the **3D** variants and a bare
generic dimmer, while the sheet names the plain ones — so its `gdtf_profile`
column points at profiles that do not correspond to what is patched:

| Sheet says | Workspace actually has |
|---|---|
| `Chauvet / Scorpion Dual RGB` | `Chauvet / Scorpion Dual RGB 3D` |
| `Chauvet / Mini Kinta IRC` | `Chauvet / Mini Kinta IRC 3D` |
| `Generic / Dimmer` | `Generic / Generic` |

This is recorded rather than fixed: reconciling a retired show's paperwork has
no payoff, and rewriting the sheet would destroy the evidence of what actually
ran. If a future year reuses this rig, patch from the workspace, not the sheet.

## Rig notes

The gaps in the patch (ch 6, 12, 18, 24, 60, 65–69) are intentional — they
match the original workspace and leave room for mode changes.

Workspace at retirement: 11 fixtures, 110 functions (73 Scene, 26 Chaser,
11 Collection), 136 virtual-console widgets across an 8-column APC40 layout.
Output was DMX USB only; the Art-Net output described in `docs/pipeline.md`
was never actually configured in this workspace.

## Venue scaffold

Blocks out the space in Blender (additive — won't delete the stage model):

1. Open `scene/stage.blend` in Blender.
2. Go to the **Scripting** workspace.
3. Click **Open** and select `scaffold_venue.py`.
4. Click **Run Script**.

This creates a **"Cosmos DMX Rig"** collection with a 30 × 60 ft dancefloor,
a 15 × 15 ft stage 2 ft high, truss bars (front stage, back stage, center
dancefloor), and one Empty per fixture carrying `dmx_start` / `dmx_universe`
as custom properties. Use the empties as placement guides when adding GDTF
fixtures in BlenderDMX.

Headless equivalent:

```bash
blender events/cosmos26/scene/stage.blend --python events/cosmos26/scaffold_venue.py --background
```
