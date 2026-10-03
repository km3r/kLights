# The engine

How the show engine works, for someone changing it. If you only want to *run* a
show, [`runbook.md`](runbook.md) is the shorter document.

The engine is **stdlib-only Python**. Nothing it needs is installed; a checkout
and a Python are the whole dependency list. That is a hard rule, not a
preference — a show laptop at a venue with no internet must not be one `pip`
away from working. Anything that genuinely needs a library lives behind a
process boundary in [`bridges/`](../bridges/prolink/README.md).

---

## Parameters, not stored values

The console this replaced stored DMX values. A "look" was a table of numbers, so
every variation of a move — faster, wider, centred lower — had to be a separate
stored scene, and 206 of them accumulated.

Here a look is a **function of musical position**. "One cycle per 8 bars" is
right at any tempo in any room, and the variations are knobs:

- **Size** scales a move about its own centre
- **Spread** lags each head along its own route
- **Centre** moves the whole look in bearing and elevation
- **Rate**, per slot, changes how fast one slot's chase runs

Nothing downstream of [`clock.py`](../engine/clock.py) is authored in
milliseconds.

## The three slots

Movement, colour and level are chosen **independently**, per fixture group.
Picking a colour does not disturb the move. The pinspots can be on their own
colour while the movers are on another.

Each slot has its own **motion phase**, integrated separately, so a colour chase
can crawl under a move running flat out.

## Per-frame evaluation

Every frame, at 40 fps, the engine walks a layer stack:

```
base  →  color  →  movement  →  fx  →  overrides  →  master  →  SAFETY
```

- **base** points everything at the mirror ball and opens it up, so a colour
  with no movement still produces a picture rather than leaving heads wherever
  the last look stopped.
- **overrides** are live operator input — a colour picked on a phone, a hand
  trim, a flash. They sit after the look so they outrank it, and before the
  master so they are still subject to it.
- **SAFETY is not a layer.** `apply_safety` and `apply_strobe_policy` run
  *after* the whole stack, unconditionally, in
  [`state.finish()`](../engine/state.py). A look cannot reorder itself in front
  of them, and no macro, rate or override can outrank them. That is the property
  that makes every other knob safe to expose.

## The safety taper

Per frame, from the current aim: the engine casts each beam, finds what it hits,
and dims beams whose core crosses the crowd's head band. It is a **comfort and
quality feature for 60 W LED beams, not a protective device** — read
[`SAFETY.md`](SAFETY.md) before relying on it, and note the two things on this
rig that genuinely are a different category.

The limiter is rate-limited (slewed) rather than instant, so a beam crossing the
crowd fades rather than flickering — and that same slew is what fades a rig
swap in when the patch is reloaded live.

## Musical time

`MasterClock` holds `(anchor_time, anchor_beat, bpm, speed)`. Every mutator
**re-anchors at the current position first**, which is why changing tempo,
nudging speed or swapping the clock source never moves the current beat. That is
guaranteed by construction rather than tested for afterwards: `_reanchor` is the
whole trick and there is only one of it.

- `beat` is cumulative and never wraps, so an 8-bar move has a monotonic phase.
- `phrase_measured` distinguishes phrase position that was **read** from a
  player from phrase **counted** off a tapped downbeat. Auto mode lands look
  changes on bars instead when it is false, because a counted phrase drifts.

Tempo can come from a tap, a typed BPM, or a DJ — see
[`bridges/prolink/`](../bridges/prolink/README.md).

## Output

`Runner` owns the frame clock; `output/` owns Art-Net. Two settings are
**mandatory** on the output path and the engine applies both at startup:

```python
sys.setswitchinterval(0.0005)            # cap GIL hold at 0.5 ms
ctypes.windll.winmm.timeBeginPeriod(1)   # Windows: 15.6 ms → 1 ms timer
```

Without the first, a CPU-bound Python thread holds the GIL for up to 5 ms and a
25 ms frame budget disappears. Without the second on Windows, `time.sleep()`
rounds up to 15.6 ms and a 40 fps loop lands on 31 fps. The measurements are in
[`spike/timing/FINDINGS.md`](../spike/timing/FINDINGS.md); the 60-minute soak on
the real engine is [`spike/timing/soak.py`](../spike/timing/soak.py).

**40 fps is a wire limit, not a tuning choice.** A full DMX512 universe takes
~22.7 ms to clock out, so ~44 Hz is the ceiling regardless of what the software
can do (measured: ~2000 Hz).

## The console protocol

HTTP + WebSocket, hand-rolled RFC 6455, no dependencies. The engine is
**server-authoritative**: the UI never predicts. A tap that has not come back
from the engine has not happened, so two phones can never disagree about what is
live.

- State is broadcast at 10 Hz, not per frame — a display cannot show 40 Hz, and
  serialising that much JSON on a thread sharing a GIL with the DMX clock is
  exactly the in-process work the timing spike identified as fatal.
- Each client has a **bounded queue and its own sender thread**, so one phone
  that locks its screen cannot stall the broadcast for everyone.
- Commands are queued and applied at a **frame boundary**, so nothing lands
  mid-evaluation.
- A command may carry an `id` (a short string or an integer). It then gets a
  `{"type": "reply", "id", "ok", "error"?, "data"?}` frame back, **to its sender
  only**, and a failure goes in that reply rather than in everyone's notices. A
  command without an id behaves as it always did. The reply is small by
  contract: anything large is read over HTTP, never pushed.
- Anything slow — parsing a file, matching a track, an fsync to a shared folder
  — runs on one **worker thread** ([`worker.py`](../engine/worker.py)) and hands
  its result back with `submit_call`, so the install still happens on the
  output thread at a frame boundary. Nothing that touches the show runs on the
  worker.

### Access tiers

Every command needs `view`, `operate` or `configure`. A token is generated per
run and embedded in the printed URL.

| tier | what it covers |
|---|---|
| **view** | watch only; every command is refused with a reason |
| **operate** | drive the show — looks, colour, cues, master, **panic**, Follow DJ arm/disarm, grab/release |
| **configure** | anything that persists past tonight or steps around a guard: `jog`, `solve --write`, venue edits, all `patch_*`, `track_link`, `show_reload` |

Panic is deliberately `operate`: the cost of it being unavailable to the wrong
person exceeds the cost of it being available, and pressing it again undoes it.

## Configuration

Five JSON formats, each validated on load against a declared shape in
[`config.py`](../engine/config.py), each naming a generated schema in `$schema`
so an editor gives completion and inline errors while you hand-edit at a venue.

| file | what it is |
|---|---|
| `shared/venues/<room>.json` | a room — size, ball, crowd zone, canopy, taper and strobe policy, and where movers rest when a timeline drives nothing (`rest_point`) |
| `events/<e>/rig.json` | what is plugged in, and which room it uses |
| `events/<e>/calibration.json` | what this rig measured in this room on this day |
| `events/<e>/looks.json` | the look library |
| `events/<e>/presets.json`, `cues.json` | saved pictures, and the night as an ordered list |

Writes go through `write_json_atomic` — temp file beside the target, `fsync`,
`os.replace`, one `.bak` kept. A crash mid-write leaves the old file or the new
one, never a truncated one.

**A venue outlives a show.** `rig.json` names one; a second night in the same
room is a one-line change rather than a forked copy of the geometry the safety
taper reads.

### The show folder (F19)

Timecoded shows — prepped tracks, per-track timelines, routines, template sets
— live in a **show folder outside the repo**, because they move between the
machine you design on and the show laptop through a shared folder. The engine
finds it from `--show-dir`, then `$KLIGHTS_SHOW_DIR`, then `show_dir` in a
gitignored `klights.local.json`; with none of those it runs exactly as before.

| file | what it is |
|---|---|
| `show.json` | template set, fallback, pause policy, per-source latency, Follow DJ default |
| `tracks/<id>.json` | one prepped track: identity, beat grid, rekordbox's phrases |
| `timelines/<track>.json` | the hand-built show for one track, in beats on its grid |
| `routines/<id>.json`, `templates/<id>.json` | reusable routines, and phrase → routine template sets |

[`showfiles.py`](../engine/showfiles.py) is the one authoring API: the designer,
MCP and the prep tool all validate and write through it. Writes carry the
revision the editor read and are refused if the file changed since — a sync
from another machine is the normal way that happens. Sync-service conflict
copies are never loaded. A track's time is [`tracktime.py`](../engine/tracktime.py):
beat 0 is the first downbeat, and the grid is the only thing that turns a
position in the audio into a beat. `python -m engine.showfiles check` validates
a folder; [`shared/show-example/`](../shared/show-example/) is a complete one.

**In a running engine** ([`showlibrary.py`](../engine/showlibrary.py)) the
folder is one immutable load: every valid document, the match index, each
track's grid. A watcher thread polls file sizes and mtimes about once a second
(no OS notifications: shared-folder mounts are where they fail), the worker
reloads, and the output thread swaps the new load in by one reference. A file
broken mid-sync keeps its last good version. **A reload never changes the
playing track**: its match, grid and (later) timeline are pinned until the
track changes, so an edit or a manual link applies from the track's next play.

**Which track is playing** ([`tracks.py`](../engine/tracks.py)) is matched in
layers: signature, rekordbox id (only with an agreeing title), manual alias,
title + artist + album, title + artist, the last three only where durations
agree. More than one track at the deciding layer is ambiguous and nothing
plays. `track_link {track_id}` records the playing description as an alias on
a prepped track. The deck's own beats are checked against the prepped grid --
rkbx_link's bar phase to a tenth of a beat, beat-link's count to a whole beat --
and two seconds of disagreement shows as `track.grid_warning`.

**What a timeline says at a beat** is [`timeline.py`](../engine/timeline.py),
which imports nothing but the standard library so the lights and, later, VJ
outputs share it. Rows are lanes, top first, and **the higher lane wins** for
each channel it drives (a scene lane drives movement, colour and level). A fill
lane lets the lanes below, then the template, show through its gaps; a lane that
**owns the track** is blank in its gaps. A clip fades in over its `fade` from
what was under it and, ending into a gap, fades out over the same. Automation
curves (`linear`, `step`, `ease` -- the curve named on a point shapes the
segment arriving at it) have an exact integral, so a rate curve gives a phase as
a function of the beat. Hits are windows: a jump into one shows it, a jump over
one never fires it. Everything is a pure function of the beat. `python -m
engine.showfiles explain TRACK BEAT` prints it.

**What the fixtures do** is [`program.py`](../engine/program.py), the only
place a timeline meets `state.py`. It compiles a track's timeline for one rig --
routines bound to it ([`routines.py`](../engine/routines.py)), their rows built
from parametric, role-based [`blocks.py`](../engine/blocks.py) -- into one
`Program` whose `Show` never changes object; `begin(beat)` each frame, then
evaluate it like any show, so safety and the strobe policy still run last. Each
fixture walks the lanes on its own: a clip drives only the fixtures it uses and
the rest fall through to the lanes below, then the fallback show; a lane that
owns the track rests them instead -- movers on the venue's `rest_point` (else
the ball), colour white, level dark. Each source runs once per slot on a scratch
copy and only its fixtures are taken from it, so two movement sources never add
their offsets together. A clip's phase is its own, a pure function of the beat
through any rate curves, so a loop lands on the authored frame. `python -m
engine.program --event DIR --show-dir DIR --track T --beat B` lists what will
not work on a rig and prints every fixture at a beat.

**When the timeline drives** is [`playback.py`](../engine/playback.py), the
runner's `choose_show` hook: Follow DJ armed (it starts disarmed, from
show.json, and says so at startup), the playing track matched a prepped track
with a timeline, its program compiled (on the worker; the operator's show runs
meanwhile). Then the Show is the program's, the same object every frame, at the
track's beat -- the transport's position, latency applied, through the matched
grid. Paused, the show's policy decides: `freeze` holds, `continue` keeps
moving at the last tempo, `idle` runs the show's idle routine once the pause
outlasts the grace period. Every hand-over is a cut. A look, preset or cue
picked while it drives grabs those lanes (`program_grab` / `program_release`,
operate tier) until released. `follow {armed}` is operate tier;
`show_latency {source, ms}` is configure and is saved to show.json.

With a template set active (show.json's `template_set`, or switched live with
`template_set {id | null}`, operate tier) the chain is timeline, then template,
then the operator's show -- still only while a DJ track plays with Follow
armed. A matched track's template follows its own phrases in track beats (bars
on its grid if it has none); a guest's follows the deck's live phrase in clock
beats (`clock.phrase_start`: the bar line the label changed on, or where the
source said the last phrase would end), else the bar cycle on the clock. The
template's Show is the timeline program's fallback, so a fill gap shows it. A
switch lands on the next bar line, crossfading over the new set's transition; a
folder edit of the active set, like any folder change, waits for the next
track. The Track card's lanes say `template`, and it shows the phrase, the
routine and the set switcher.

**A preset pad can carry a routine** (`routine: {id, variation?, params?}` in
presets.json, saved from the console with a show folder). Pressed, it waits for
the clock's next downbeat and then lands whole: its looks, and the routine over
them from the routine's beat 0, as the operator's show -- so with the timeline
or a template driving it grabs every lane like any preset. Picking a look, a
plain preset or a cue puts it away. Its program is built on the worker when the
folder loads, when it is saved, and after a rig reload.

**Templates** ([`templates.py`](../engine/templates.py), milestone 2) are the
middle of the chain -- timeline, then template, then the operator's or auto
mode's show. A template set maps rekordbox's phrase labels to routine picks
(exact label, then without its number, then `*`) and cycles `bars.cycle` every
`bars.every` bars where there are no phrases. `compile_set` builds one
`Program` per distinct pick on the worker; a `TemplateRunner` plays them as ONE
stable Show, each pick on its own beat counted from where its phrase began. The
same pick into the next phrase carries on; a different one crossfades over the
set's `transition.fade_beats` in parameter space, slot by slot, so a timeline
can take the runner's Show as its fallback and show it through its fill gaps.
A jump cuts.

**The designer's side of the wire** ([`api.py`](../engine/api.py)): large reads
are `GET /api/*` -- `show`, `tracks[/<id>]`, `timelines/<id>`,
`routines[/<id>]`, `templates[/<id>]`, `waveforms/<id>` -- each document with
the rev a save must quote, never in the 10 Hz snapshot. `GET /api/audio/<id>`
streams the track's file with Range (206), needs the token, and only ever serves
a file the track names (or the same name under this machine's `audio_roots` in
`klights.local.json`) with an audio extension. Writes are configure-tier
commands answered from the worker: `timeline_draft {doc}` (the format's rules,
then a compile against this rig), `timeline_save` and `routine_save {doc,
base_rev}` (refused if the file changed since), and `routine_draft {doc}` (the
format's rules, then the routine bound to this rig as it is and in each
variation: roles no fixture carries, blocks with nothing to aim).
`preview_arm {track_id, force?}`
puts the designer's transport on the rig -- refused while a DJ plays unless
forced, shown on every console, released by `preview_release` or by the
designer's browser going away; `preview_transport {time_s, playing}` moves it,
and a draft replaces what it plays until saved. A `preview_transport` that
arrives after its preview has ended is ignored rather than reported: the page
learns from the snapshot within a tenth of a second and stops sending. Clip
positions and lengths are capped at `showfiles.MAX_BEATS` (65536), so no
document can ask the compiler for unbounded work.

**The designer** itself is `ui/src/designer/`, a chunk of its own loaded only
from `#designer` (`test_api` checks the console's entry script never contains
it). `#designer` lists the tracks and routines; `#designer/<track>` is layout B
-- bar ruler, rekordbox's phrases, the waveform, then the timeline's rows (the
higher lane wins), hits, automation and the VJ lane, with the rig's plan and
"who drives each lane" at the playhead on the right and the selected clip
below; `#designer/routine/<id>` edits a routine with the same lanes in loop
mode, plus its roles, open parameters, variations and blocks. Its beat grid,
block list and automation targets are copies of the engine's, held to them by
fixtures the engine writes (`engine/tests/dump_designer_fixtures.py`; a stale
fixture fails `test_api`). Everything else -- whether a document is valid,
what a routine does on this rig -- it asks the engine.

**In conversation** the same folder is [`showtools.py`](../engine/showtools.py)
behind the MCP server: status, tracks (with their phrase beats), routines,
template sets and timelines to read; `put_*` for whole documents and
`edit_timeline` for small ops (add/update/remove items and lanes, set points or
palettes); `link_track`; `lint_show` (the folder, and against an event's rig);
`explain_position` (a track at a beat, and what every fixture does there).
Writes are dry runs unless asked and quote the rev they read; unlike the rig,
the folder may be written while a show runs.

## Where to look

| question | file |
|---|---|
| where a head is, where it points, what DMX aims it | [`geometry.py`](../engine/geometry.py) |
| what is patched, and the room | [`rig.py`](../engine/rig.py), [`venue.py`](../engine/venue.py) |
| the layer stack and per-frame evaluation | [`state.py`](../engine/state.py) |
| the beam taper | [`safety.py`](../engine/safety.py) |
| musical time | [`clock.py`](../engine/clock.py) |
| movement as a path over bars | [`motion.py`](../engine/motion.py) |
| self-running axes | [`auto.py`](../engine/auto.py) |
| the ported look library | [`library.py`](../engine/library.py) |
| the frame clock and Art-Net | [`runner.py`](../engine/runner.py), [`output/`](../engine/output/) |
| console HTTP + WebSocket | [`server.py`](../engine/server.py), [`websocket.py`](../engine/websocket.py) |
| editing the patch | [`patch.py`](../engine/patch.py) |
| DJ tempo ingest | [`sync.py`](../engine/sync.py) |
| which track, and where in it | [`transport.py`](../engine/transport.py), [`tracks.py`](../engine/tracks.py) |
| the show folder, live | [`showfiles.py`](../engine/showfiles.py), [`showlibrary.py`](../engine/showlibrary.py) |
| what a timeline says at a beat | [`timeline.py`](../engine/timeline.py) |
| when the timeline drives, and grabs | [`playback.py`](../engine/playback.py) |
| the designer's reads and audio | [`api.py`](../engine/api.py) |
| what the fixtures do on a timeline | [`program.py`](../engine/program.py), [`routines.py`](../engine/routines.py), [`blocks.py`](../engine/blocks.py) |

## Tests

Standalone scripts, one per area, no test framework:

```bash
python -m engine.tests
```

Each runs in its own subprocess so one crash cannot take the rest with it, and
exit codes aggregate. The suites deliberately assert **failure paths** — a
refused command, a corrupt config, a crash mid-write, a rig that cannot load —
because a test that only proves the happy path is a test that passes when the
feature is deleted.
