# The BlenderDMX previz path — superseded

These four scripts served the **QLC+ era**: QLC+ drove the rig, and BlenderDMX
previsualised it by receiving the same Art-Net. That pipeline is documented in
[`docs/pipeline.md`](../../docs/pipeline.md) and it worked, but nothing in the
current show uses it. The engine is a parametric Python show now, and the previz
is [`previz/`](../../previz/README.md) — an Unreal level built from the same
`rig.json` and `venue.json` the engine reads, plus a plan view in the console
that needs no 3D at all.

Kept rather than deleted because `docs/pipeline.md` still describes a complete
working system, and a reference that points at missing files is worse than one
that points at retired ones. Moved out of `shared/tools/` so that directory only
holds things the current show actually runs.

| script | what it did |
|---|---|
| `build_gdtf.py` | Generated GDTF fixture profiles for BlenderDMX from the `.qxf` definitions. |
| `patch_blenderdmx.py` | Patched a whole event's rig into Blender from its `patch_sheet.csv`. |
| `go_live.py` | Put BlenderDMX back into the live-receive state after a restart — the Art-Net receiver and the render timer, which did not reliably come back wired together. |
| `tune_live_look.py` | Tuned the live EEVEE viewport so the rig read as a light show while programming. |

## Do not trust `build_gdtf.py`'s channel maps

This is the reason it is not just unused but actively flagged, and
[`engine/rig.py`](../../engine/rig.py) says so at the top of the file: it
**hand-transcribes** channel layouts rather than reading them from the `.qxf`
it claims to correspond to. The two can therefore disagree, silently, and the
symptom is a fixture whose colors or position channels are subtly wrong in
previz while being right on the wire — or the reverse.

The engine parses `.qxf` for channel *roles* precisely so there is one
description of a fixture rather than two. Anything resurrected from here should
do the same before it is believed.
