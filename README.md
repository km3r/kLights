# Cosmos Lights

Lighting design, control and previsualization for a small moving-head and wash
rig, across multiple events.

```
lights/
├── docs/                     Generic pipeline documentation
│   └── pipeline.md           Art-Net, QLC+ output, BlenderDMX, script reference
├── shared/                   Reusable across every event
│   ├── fixtures/             .qxf fixture definitions, one per hardware model
│   ├── gdtf/                 Generated GDTF profiles for BlenderDMX
│   ├── inventory.json        The units we actually own
│   └── tools/                Art-Net utilities, GDTF builder, patch validator,
│                             BlenderDMX patcher, Blender scene helpers
└── events/
    ├── cosmos26/             ARCHIVED — the Year-3 Cosmos rig
    └── despacio/             4 moving heads + 2 pinspots on a mirror ball
```

**The organizing rule:** a file belongs to an event if it encodes *this room,
this rig, or this night* — workspaces, patch sheets, venue geometry,
calibration, 3D scenes, controller layouts. It belongs in `shared/` if it
describes *hardware we own* or *a thing we do to any show*. Events come and go;
the inventory and the tools carry forward, and a new show draws on them à la
carte.

## Events

| Event | Status | Rig |
|---|---|---|
| [despacio](events/despacio/README.md) | Ran 2026-08 | 4× MingJie MJ-OS-018 beams in the corners of a 30 ft room, sideways-mounted, aimed at a centre-hung mirror ball, + 2 pinspots. Has its own geometry/calibration engine (`aim_calc.py`) and a mobile web console. |
| [cosmos26](events/cosmos26/README.md) | Archived | 4× Par 36 wash, 2× pinspot, 2× YeeSite pixel bar, Scorpion laser, Mini Kinta, dimmer. APC40-driven. |

## Getting started

Validate every event's patch:

```bash
python shared/tools/validate_patch.py
```

Rebuild the GDTF profiles BlenderDMX consumes:

```bash
python shared/tools/build_gdtf.py
```

Check the despacio show is venue-ready (geometry self-test, structural
validation, fixture install, web-UI sync):

```bash
python events/despacio/preflight.py
```

See [`docs/pipeline.md`](docs/pipeline.md) for the full Art-Net → BlenderDMX
setup, and each event's README for its rig, patch and rigging notes.

## Where this is going

The current stack programs shows in QLC+ and drives DMX over USB. That model
stores *values*, which makes per-fixture colour control, phrase-aware
automation, smooth interpolated motion and venue portability all expensive or
impossible. The project is moving to a parametric Python show engine with a
web UI, outputting Art-Net/sACN directly — which also decouples previz, since
any previz tool can simply listen on the wire.

QLC+ and the existing workspaces stay runnable as the fallback until the
engine reaches parity.

## Requirements

Python 3.10+ (stdlib only — the tools have no pip dependencies).
Blender 3.3+ with the BlenderDMX addon for previz.
QLC+ 4.14+ for the current show stack.
