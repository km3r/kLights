# Cosmos Lights

Lighting design, control and previsualization for a small moving-head and wash
rig, across multiple events.

The show runs on a **parametric Python engine**. It holds parameters rather
than stored DMX values, owns its own 40 fps frame clock, knows the room in three
dimensions, and dims beams that get near people — per frame, from the current
aim. A web UI drives it from a phone; Unreal renders it in 3D by listening to
the same Art-Net the rig sees.

> See [`docs/SAFETY.md`](docs/SAFETY.md) for what the beam taper manages — it is
> a comfort feature for LED beams, not a protective device — and for the two
> things on this rig that are a different category: **strobe** (nothing limits
> the rate, and photosensitive epilepsy is a real risk) and **lasers**.

```
lights/
├── docs/                     Generic pipeline documentation
│   ├── pipeline.md           Art-Net, the engine, previz, legacy QLC+ setup
│   └── SAFETY.md             What the beam taper manages, and the two real risks
├── engine/                   The show engine (stdlib only, no dependencies)
│   ├── geometry.py           Where a head is, where it points, what DMX aims it
│   ├── rig.py venue.py       What is patched; the room it is patched into
│   ├── state.py              Parameters and the layered per-frame evaluation
│   ├── safety.py             Beam-aware intensity taper
│   ├── clock.py motion.py    Musical time; movement as a path over bars
│   ├── auto.py library.py    Self-running axes; the ported look library
│   ├── calibrate.py          Fast re-aim, drift detection, snapshots
│   ├── runner.py output/     The frame clock and the Art-Net driver
│   ├── server.py websocket.py  HTTP + WebSocket, hand-rolled RFC 6455
│   └── tests/                Standalone test scripts, one per area
├── ui/                       React console — five tabs, phone through desktop
│   └── dist/                 Committed build, so a venue needs no Node
├── previz/                   Unreal previz: an Art-Net listener, never in the path
├── spike/                    Timing spike that settled the frame-clock question
├── schemas/                  JSON Schema, generated from engine/config.py
├── scripts/preflight.py      Everything that must be green before a venue
├── shared/                   Reusable across every event
│   ├── fixtures/             .qxf fixture definitions, one per hardware model
│   ├── venues/               Rooms. A venue outlives any one show
│   ├── gdtf/                 Generated GDTF profiles for BlenderDMX
│   ├── inventory.json        The units we actually own
│   └── tools/                Art-Net utilities, library porter, GDTF builder,
│                             patch validator, schema generator
└── events/
    ├── cosmos26/             ARCHIVED — the Year-3 Cosmos rig
    └── despacio/             4 moving heads + 2 pinspots on a mirror ball
```

**The organizing rule:** a file belongs to an event if it encodes *this rig or
this night* — patch, calibration, look library, workspace. It belongs in
`shared/` if it describes *hardware we own*, *a room*, or *a thing we do to any
show*, and in `engine/` if it is show logic that does not know which event it is
running. Events come and go; the inventory, the rooms, the tools and the engine
carry forward.

A **room** is shared because it outlives any one show: `rig.json` names one with
`"venue": "despacio-room"` and it resolves to `shared/venues/despacio-room.json`.
An event with no `venue` key keeps its own `venue.json`, so a second night in the
same room is a one-line change rather than a forked copy of the geometry the
safety taper reads.

Every config file is validated on load against a declared shape, and names a
generated JSON Schema in `$schema`, so an editor offers completion and inline
errors while you hand-edit at the venue.

## Events

| Event | Status | Rig |
|---|---|---|
| [despacio](events/despacio/README.md) | Ran 2026-08 | 4× MingJie MJ-OS-018 beams in the corners of a 30 ft room, sideways-mounted, aimed at a centre-hung mirror ball, + 2 pinspots. Now the engine's reference event: `rig.json`, `venue.json`, `calibration.json` and a 206-look library ported from its QLC+ workspace. |
| [cosmos26](events/cosmos26/README.md) | Archived | 4× Par 36 wash, 2× pinspot, 2× YeeSite pixel bar, Scorpion laser, Mini Kinta, dimmer. APC40-driven, QLC+ only. |

## Running a show

Start the engine and its UI. This is the whole show:

```bash
python -m engine.server --artnet 255.255.255.255
```

It prints the rig it loaded, which timing settings took effect, and the URLs to
open — including the LAN one to type into a phone. Defaults are the despacio
event, port 8765, 40 fps and 124 BPM; `--event`, `--port`, `--fps` and `--bpm`
override them. With no `--artnet` it runs against a null output, which is the
safe way to try things with the rig plugged in.

The UI is served from the committed `ui/dist/`, so a show laptop needs Python
and a checkout and nothing else. Five tabs, all driven by the same WebSocket
state: **Show** (presets, tempo, auto), **Color**, **Move**, **Bright**, and
**Setup** — which also holds the rig, venue and calibration panels, plus Panic.
Master and Blackout live in the header, on every tab.

Smoke-test the frame path without the UI:

```bash
python -m engine.demo --artnet 127.0.0.1 --seconds 30
```

Point [`shared/tools/artnet_listener.py`](shared/tools/artnet_listener.py) at
it to see the frames, or `--no-taper` to see what the safety taper is holding
back.

## Editing the rig

Three surfaces, one set of rules — `engine/patch.py` decides what a legal patch
is, so the answer cannot differ between them.

```bash
python -m engine.patch describe
python -m engine.patch add --name "Par 1" --manufacturer UKing \
    --model "Par 36 Custom" --mode "5 Channel" --tags pars --write
```

Everything is a dry run until `--write`, and a write is refused while an engine
is running against that event — it reads its config once at startup, so an edit
mid-show leaves the file and the rig disagreeing with nothing on screen to say
so. `profiles`, `venues`, `remove`, `address`, `tags`, `position`, `autopatch`,
`venue`, `import` and `new` round it out; `--help` on any of them.

The same operations are available to Claude over MCP, registered in `.mcp.json`:

```bash
python mcp/cosmos_mcp.py
```

It speaks JSON-RPC over stdio in pure standard library — no SDK, so the
zero-dependency rule survives. Editing tools take `write`, defaulting to false.

## At the venue

Re-aim after the heads get nudged overnight — three captures per head solve
position, offsets and invert flags together:

```bash
python -m engine.calibrate solve captures.json --write
```

`drift` flags which heads moved from a set of readings, `snap` and `diff`
record and compare calibration snapshots, and `jog` parks one head at a
Pan/Tilt so you can eyeball it. Every subcommand takes `--event`.

Validate the patch after editing one:

```bash
python shared/tools/validate_patch.py
```

Re-port the look library from a QLC+ workspace (writes `looks.json`):

```bash
python shared/tools/port_library.py --write
```

The despacio show also keeps its own one-command readiness check, covering the
geometry self-test, patch and mount-mode guardrails:

```bash
python events/despacio/preflight.py
```

## Previz

Unreal 5.8 renders the show live by listening to Art-Net — it sits beside the
rig, never between the engine and it, so previz cannot break a show. It decodes
with `engine.geometry`, the show's own decoder, rather than a GDTF profile,
because the heads are mounted sideways in a way a fixture profile cannot
express. The heads model a real yoke (`engine.servo`), so a move takes the time
it takes — without that the previz teleported between routines and could not
show either what a routine change costs or what a dark move looks like. See
[`previz/README.md`](previz/README.md) for the runbook, the mirror ball, and the
several things about Unreal's volumetric fog that are the opposite of the
obvious guess.

```bash
python previz/ue_remote.py previz/unreal/Content/Python/go.py
```

## Tests

The engine's tests are standalone scripts with no test-runner dependency — run
one directly (`python engine/tests/test_clock.py`), or all of them plus the
module self-tests:

```bash
python -m engine.tests
```

Each suite runs in its own subprocess, because several set process-wide timing
and assert on wall-clock behaviour. `-k <substring>` narrows it, `-v` streams a
suite's own output instead of capturing it. Before a venue, run the lot along
with the patch, venue and bundle checks:

```bash
python scripts/preflight.py
```

Two of them are load-bearing. `test_geometry_parity.py` compares every aim
against `events/despacio/aim_calc.py`, the code that drove the real show, and
insists they agree to within one 8-bit step. `test_qlc_parity.py` diffs whole
DMX frames against QLC+ across the ported library and requires every differing
channel to fall in a category that was *derived* — held, base, taper — leaving
`dropped` and `unexplained` as the findings.

```bash
python shared/tools/qlc_parity.py check --verbose
```

By default that models QLC+ from the workspace's stored scene values. To diff
against what QLC+ really emits, record it off the wire first and compare
against that:

```bash
python shared/tools/qlc_parity.py capture --out captures.json --all
```

The UI has its own suite, run against a fixture captured from a real engine:

```bash
cd ui && npm test
```

CI runs all of the above on Linux and Windows, and adds the one check that
cannot be made by being careful: it rebuilds `ui/dist` and fails if the result
differs from what is committed. That bundle is committed on purpose so a show
laptop needs no Node — which means a stale or half-staged one ships a blank
console to the venue, and it nearly did.

## The QLC+ path

QLC+ programmed every show before the engine, and the workspaces stay runnable
as the fallback. That model stores *values*, which is why per-fixture colour,
phrase-aware automation, smooth interpolated motion and venue portability were
each expensive or impossible — see
[`docs/pipeline.md`](docs/pipeline.md#the-legacy-qlc-path) for the setup, and
`events/<name>/README.md` for a show's own patch and rigging notes.

## Requirements

- **Python 3.10+** — stdlib only. The engine, the tools and the previz host
  half have no pip dependencies at all.
- **Node 18+** — only to rebuild the UI (`cd ui && npm run build`). Never
  needed at a venue; `ui/dist/` is committed.
- **Unreal Engine 5.8** — for previz.
- **Blender 3.3+ with BlenderDMX**, **QLC+ 4.14+** — for the legacy path.

## Licence

[Apache-2.0](LICENSE). Note the warranty disclaimer: this software aims light at
people, and its comfort model is documented, deliberately limited and
unvalidated. See [`docs/SAFETY.md`](docs/SAFETY.md).
