# kLights

A lighting console for a small moving-head rig, with 3D previsualization.

It runs a **parametric show engine**: it holds parameters rather than stored DMX
values, owns its own 40 fps frame clock, knows the room in three dimensions, and
dims beams that get near people. You drive it from a phone. Unreal renders it by
listening to the same Art-Net the rig hears.

**Nothing needs installing.** The engine is stdlib-only Python and the web
console ships pre-built, so a show laptop needs a checkout and a Python.

> ⚠️ Read [`docs/SAFETY.md`](docs/SAFETY.md) before pointing this at people. The
> beam taper is a **comfort feature for LED beams, not a protective device**,
> and two things on this rig are a different category: **strobe** (nothing
> limits the rate, and photosensitive epilepsy is a real risk) and **lasers**.

---

<img src="docs/images/previz-ball.png" alt="Unreal previz: two beams on a mirror ball scattering across a hazy room" width="100%">

*The Unreal previz — the same Art-Net the rig receives, rendered in 3D.*

<img src="docs/images/plan-view.svg" alt="Plan view of the room from above, beams drawn to where they land" width="520">

*The console's plan view: the room from above, every lit beam drawn to where it
actually lands, at the width it actually spreads to. No GPU, no install — this
runs on the show laptop.*

## Quick start

```bash
git clone <this repo>
cd lights
python -m engine.server
```

That runs the despacio show against a **null output** — no DMX on the wire —
which is the safe way to try it with a rig plugged in. Open the URL it prints
(including its `?token=`) on a phone or a laptop.

To actually drive a rig:

```bash
python -m engine.server --artnet 255.255.255.255
```

Useful flags: `--event` (which show), `--port`, `--bpm`, `--bind`, `--token` /
`--no-token`, `--sync-port` (tempo from a DJ), `--show-dir` (prepped tracks
and their timelines -- see [Timecoded shows](#timecoded-shows)). `--help`
lists them all.
`--artnet` takes a list, so one engine can feed the rig's node and a previz on
the same laptop: `--artnet 10.0.0.50,127.0.0.1`.

Or double-click **`kLights.pyw`** (`python -m launcher` from a terminal): one
window to pick the event, start and stop the engine, open the console or its
Setup tab, copy the phone link, and launch or build the 3D previz. The engine
runs as its own process, so closing the launcher does not stop a show, and
reopening it finds the engine again.

Before a show, run everything that must be green:

```bash
python scripts/preflight.py
```

## Using the console

The console is a **web app the engine serves itself** — no install, no pairing,
no app store, and nothing to keep in sync. Open the URL the engine prints and
you are on the desk. It is built for a phone in one hand; on a laptop the tab
bar becomes a side rail and the panels widen.

<table>
<tr>
<td width="50%"><img src="docs/images/console-show.png" alt="Show tab: the Night cue list on cue 1 of 9, the four independent slots that are up now, and the preset pads"></td>
<td width="50%"><img src="docs/images/console-color.png" alt="Color tab: the colour look list, filtered by fixture group, with Split Warm/Cool selected"></td>
</tr>
<tr align="center"><td><b>Show</b></td><td><b>Color</b></td></tr>
<tr>
<td width="50%"><img src="docs/images/console-move.png" alt="Move tab: the plan view with four beams converging on the mirror ball, and the route list below"></td>
<td width="50%"><img src="docs/images/console-bright.png" alt="Bright tab: level chases with MH Breathe running, and the per-slot rate control"></td>
</tr>
<tr align="center"><td><b>Move</b></td><td><b>Bright</b></td></tr>
</table>

Five tabs, all driven by the same live state.

| tab | what it is for |
|---|---|
| **Show** | the cue list, preset banks, tempo and tap, auto mode, DJ sync, panic |
| **Color** | colour looks, a quick palette, a per-fixture picker, colour rate |
| **Move** | the plan view, movement routes, shape macros, movement rate |
| **Bright** | level patterns, hand dimming, momentary flash, strobe policy |
| **Setup** | the rig, the room, calibration and the patch editor |

Master and Blackout are in the header on every tab.

**Several people can be on it at once.** Every client sees the same state over a
WebSocket, and Setup shows who is connected and who last touched what. There is
no locking and no claiming — you can see each other instead, which is how two
people on a desk actually works.

**Perform / Design** is in the header too. Perform hides Setup and the read-only
diagnostics, leaving only what drives the show; Design is everything. It
defaults to Perform on a phone and Design on a laptop and is always one tap from
the other. It is a preference about screen space, **not** a permission — access
is what `--token` decides.

Three ideas make the rest make sense:

- **Movement, colour and level are independent slots.** Picking a colour does
  not disturb the move. Each has its own rate, so a colour chase can crawl under
  a move running flat out.
- **Presets are pages of eight pads**, and a pad is a *place*. Saving over a
  preset keeps its pad; adding or deleting neighbours does not shuffle it.
- **The cue list is the night**, and GO walks it. A guest who knows nothing
  about the rig can run the whole show off one button.

## Running a show

Full procedure for the day, including what to do when something breaks:
**[`docs/runbook.md`](docs/runbook.md)**.

## Previz

The previz is a **listener**: it watches the same Art-Net the rig does, so it
can never break a show. The standalone app needs no editor and no Python to
run, and draws whatever event the running engine is driving:

```bash
python previz/build.py                    # once; needs Unreal 5.8 to BUILD
previz/dist/Windows/KLightsPreviz.exe     # beside python -m engine.server
```

The launcher does both from buttons. The original editor-driven path still works
beside it (`python previz/doctor.py` checks either). See
[`previz/README.md`](previz/README.md).

The console's own plan view needs none of that and runs anywhere.

## Tempo from the DJ

The engine can take tempo, bar phase and **phrase** from the players rather than
from a tapped downbeat:

```bash
python -m engine.server --sync-port 9000
python bridges/prolink/bridge.py --fake      # no hardware needed
```

**No audio analysis happens here.** Beat position and rekordbox's phrase labels
are a *read*, not a derivation, so everything that knows what a CDJ is lives in
a sidecar. CDJs via beat-link-trigger; a DDJ-1000 via rkbx_link, since a DDJ is
USB and never speaks Pro DJ Link at all. Both emit OSC and the engine reads
both. See [`bridges/prolink/README.md`](bridges/prolink/README.md).

## Timecoded shows

For a signature track, the lights can follow **which track is playing and where
in it**, not just the tempo: a show drawn bar by bar against the track, played
back on the night from the DJ's own position.

<img src="docs/images/designer.png" alt="The designer: a bar ruler, rekordbox's phrase bands and the timeline's lanes -- scene clips, a palette lane, hit diamonds and master and size automation curves -- with the rig from above and who drives each lane at the playhead on the right" width="100%">

*The designer on the example show: rekordbox's phrases across the top, the
timeline's lanes below (the higher lane wins), the rig from above and "who
drives each lane" at the playhead on the right.*

```bash
python -m engine.showfiles init shows/              # a show folder (or use shared/show-example)
python bridges/rekordbox/prep.py --show-dir shows/ xml rekordbox.xml --anlz-root E:/PIONEER/USBANLZ
python -m engine.server --show-dir shows/ --sync-port 9000
```

1. **Prep** reads what rekordbox already knows -- identity, beat grid, phrases,
   cues, waveform -- from an XML export and its analysis files, into the show
   folder ([`bridges/rekordbox/`](bridges/rekordbox/README.md)). No audio
   analysis of our own.
2. **Design** at the engine's URL with `#designer` on the end, on a computer (a
   phone never downloads it). Each track is a set of lanes against its waveform
   and phrases: scene, movement, colour, level and palette lanes holding
   routines, looks and presets; hits (flash, strobe, blackout); automation of
   master, size, spread, centre and rate. Draft a first pass from a template
   set, tap hits in with the Record pads, or edit the list. **Drive the rig**
   plays the draft you are editing on the real rig, from the page's own audio.
   Routines -- reusable rows on roles (movers, pinspots), with open parameters
   and variations -- have their own editor. Undo, Ctrl+S, Space and Delete work
   as you would expect; every save quotes the version it opened, so a change made
   elsewhere is never overwritten.
3. **Play.** The engine matches the DJ's track against the folder (CDJs via
   beat-link-trigger, a DDJ via rkbx_link -- see
   [`bridges/prolink/`](bridges/prolink/README.md)). On the phone's **Track**
   card, arm **Follow** and the matched track's timeline drives the rig;
   picking a look grabs that lane back until you release it.

What is safe by default: Follow starts **disarmed** every time, because the DJ
feed arrives on an unauthenticated port; writing to the show folder and taking
the rig from the designer need the token; the audio endpoint serves only files
a track names, and only with the token; safety and the strobe policy run after
the timeline exactly as they run after everything else. The folder can live on
Dropbox or a NAS and is reloaded when it changes -- but never under a track
that is playing.

An assistant can read, lint, explain and edit the same folder through the MCP
server. The design record, with every decision and why, is
[`docs/design/timecoded-shows.md`](docs/design/timecoded-shows.md); the
example folder is [`shared/show-example/`](shared/show-example/README.md).
**Not yet run against real decks** -- the hardware checklist is in the design
record.

## Editing the rig

Three surfaces, one set of rules — every write goes through the same API and the
same validation:

```bash
python -m engine.newevent                       # a new show, interactively
python -m engine.patch add --name "Par 5" ...   # one-shot edits
```

- the **Setup tab** in the console, phone in hand at load-in
- the **MCP server** (`mcp/klights_mcp.py`), so an assistant can patch and
  describe the rig -- and read, lint, explain and edit the show folder's
  tracks, timelines, routines and template sets (dry runs unless asked, every
  write quoting the rev it read)
- the **CLI** above, which needs no UI

Edits are validated, written atomically, and applied to the running show without
a restart. A rig that will not load is refused and the old one keeps running.

<img src="docs/images/console-setup.png" alt="Setup tab on a laptop: the patch editor listing six fixtures with their universe, address and tags, above the room's dimensions and crowd head band" width="100%">

*The Setup tab on a laptop — the same console, with the tab bar as a side rail.
The patch is read-only until you unlock it, because it is load-in work rather
than something to reach for mid-set.*

## Building the UI

Only needed if you change it — `ui/dist/` is committed so a venue needs no Node.

```bash
cd ui
npm ci
npm run dev      # live-reloading dev server
npm test         # 151 tests: the console against a fixture captured from a
                 # real engine, the designer against the example show folder
npm run build    # writes ui/dist/
```

CI rebuilds the bundle and fails if it differs from what is committed. That
check exists because a stale bundle ships a blank console to a venue, and it
nearly did.

## Tests

```bash
python -m engine.tests    # every suite, no test framework
cd ui && npm test         # the console and the designer
```

Engine suites are standalone scripts — run one directly with
`python engine/tests/test_clock.py`. Each runs in its own subprocess, since
several set process-wide timing and assert on wall-clock behaviour.

Two are load-bearing. `test_geometry_parity.py` compares every aim against the
code that drove the real show and requires agreement within one 8-bit step.
`test_qlc_parity.py` diffs whole DMX frames against QLC+ across the ported
library, and requires every differing channel to fall into a category that was
*derived* rather than assumed.

## Layout

```
engine/        the show engine — stdlib only, no dependencies
launcher/      the desktop window that starts the engine and the previz (Tk)
ui/            React console and designer; ui/dist is committed so a venue needs no Node
previz/        Unreal previz — an Art-Net listener, never in the show's path
bridges/       sidecars: DJ tempo and position, and the rekordbox prep tool
mcp/           MCP server over stdio: the rig and the show folder, from an assistant
events/        one directory per show: patch, calibration, looks, cues, presets
shared/        things that outlive a show: fixtures, venues, inventory, tools
schemas/       JSON Schema, generated from engine/config.py
docs/          how it works, how to run it, and why it is like this
legacy/        the retired BlenderDMX path, kept for reference
spike/         the timing spike that settled the frame-clock question
```

**The organizing rule:** a file belongs to an event if it encodes *this rig or
this night*. It belongs in `shared/` if it describes *hardware we own*, *a room*,
or *a thing we do to any show*, and in `engine/` if it is show logic that does
not know which event it is running. Events come and go; the inventory, the
rooms, the tools and the engine carry forward.

A **room** is shared because it outlives any one show, so a second night in the
same room is a one-line change rather than a forked copy of the geometry the
safety taper reads.

## Documentation

| | |
|---|---|
| [`docs/runbook.md`](docs/runbook.md) | show night, start to finish |
| [`docs/SAFETY.md`](docs/SAFETY.md) | what the taper does and does not do |
| [`docs/engine.md`](docs/engine.md) | how the engine works, for changing it |
| [`docs/ROADMAP.md`](docs/ROADMAP.md) | what was built, why, and what is not |
| [`docs/pipeline.md`](docs/pipeline.md) | Art-Net architecture; the QLC+ era |
| [`docs/design/timecoded-shows.md`](docs/design/timecoded-shows.md) | timecoded shows: decisions, formats, stages |
| [`docs/design/`](docs/design/) | working notes and measurements |
| [`CHANGELOG.md`](CHANGELOG.md) | notable changes, newest first |

## Requirements

- **Python 3.10+**, stdlib only — the engine, the tools and the previz host half
  have no pip dependencies at all.
- **Node 18+**, only to rebuild the UI. Never needed at a venue.
- **Unreal Engine 5.8**, only for the 3D previz.

## Licence

[Apache-2.0](LICENSE). Note the warranty disclaimer: this software aims light at
people, and its comfort model is documented, deliberately limited and
unvalidated. See [`docs/SAFETY.md`](docs/SAFETY.md).
