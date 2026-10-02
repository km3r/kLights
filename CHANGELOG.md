# Changelog

Notable changes, newest first. Versions follow [semver](https://semver.org);
until 1.0 the config file formats may change between minor versions, and any
break will say so here with a migration note.

The `F<n>` labels are the project's own feature milestones; the summary below is
the only record of them until a roadmap doc lands.

---

## Unreleased

### Added — F19i: the timeline on stage

- **Follow DJ.** With a show folder, a matched track's timeline drives the rig
  once Follow is armed -- one tap on the new **Track** card (Show tab). It
  starts disarmed, as show.json's `follow.default` says, and the startup output
  says so. While disarmed the track is still matched and shown; nothing reaches
  the rig.
- **`engine/playback.py`** is the runner's new `choose_show` hook: the program's
  Show (the same object every frame) while the timeline drives, auto mode's
  show the moment it stops. Programs compile on the worker when a track is
  matched, armed or not, so arming is instant. A rig reload rebuilds them.
- **Pause policies** from show.json: `freeze`, `continue` (keeps moving at the
  last tempo), `idle` (the idle routine, once the pause outlasts the grace).
- **Decided with the user:** a look, preset or cue picked while the timeline
  drives grabs its lanes until Release, across tracks; every hand-over is a cut;
  the latency slider saves to show.json.
- Commands: `follow {armed}`, `program_grab {slot}`, `program_release {slot?}`
  (operate); `show_latency {source, ms}` (configure). Snapshot `program`:
  armed, mode, why not driving, bar, who has each lane, grabs, problems,
  latency. A cue GO while the timeline drives grabs instead of swapping it out.

### Added — F19h: the lights compiler

- **`engine/program.py`** compiles a track's timeline for one rig into a
  `Program` with one stable `Show`: `begin(beat)` each frame, then evaluate it
  like any show -- safety and the strobe policy still last. F19i puts it on the
  runner; until then it runs offline, and `python -m engine.program --event DIR
  --show-dir DIR --track T --beat B` prints every fixture at a beat.
- **`engine/routines.py`**: a routine bound to a rig -- params (default, then
  variation, then the use's own), roles bound to tags (an optional role absent
  on the rig is simply absent), "built for another rig" said out loud.
- **`engine/blocks.py`**: the parametric blocks -- `orbit`, `pendulum`,
  `fan_sweep`, `aim_points` (in room fractions, so portable), `solid`,
  `color_chase`, `chase` (ordered by where fixtures hang), `pulse`, `dim`,
  `strobe` -- plus the rig-bound `look` and `snapshot` adapters. Colours are a
  palette role, `#hex`, `[r, g, b]` or a colour look; parameters and colours
  resolve as they run, so the palette lane and `param.*` automation reach them.
- **Decided with the user:** a clip drives only the fixtures it uses and the
  rest fall through per fixture (an owning lane rests them); rest is the
  venue's new `rest_point` (else the ball), colour white, level dark; a
  routine that does not loop keeps running its end on a longer clip.
- Each source runs once per slot on a scratch copy and only its own fixtures
  are taken, so movement offsets never stack; crossfades blend whole states in
  parameter space. A clip's phase is a pure function of the beat through the
  timeline's and the routine's rate curves (exact, or tabulated when both
  vary), so loops land on the authored frame.
- Hits: flash raises, strobe opens the shutter, blackout goes last; a routine's
  own hits fire from inside it. Timeline `size`/`spread`/`center` scale the
  movement slot; `master` scales everything.
- `state.offset_aim` is now the one place size and centre apply, for ported
  looks and blocks alike; `EvalContext.scoped()` runs a source on its own time;
  `Timeline.entries()` gives the full per-lane stack.

### Added — F19g: what a timeline says at a beat

- **`engine/timeline.py`**, the output-generic core: at any beat, the stack on
  each channel (clips with weights, ending in blank or the template beneath),
  each automation value, and the hits firing. Standard library only, so VJ
  outputs can share it; a test parses its imports.
- **Decided with the user:** the higher lane wins, scene lanes included; a lane
  that owns the track is *blank* in its gaps (it no longer "holds its last
  item"); a clip ending into a gap fades out over its own `fade`.
- Curves: the curve named on a point shapes the segment arriving at it; `ease`
  is smoothstep; values hold outside the points; the integral is exact, so a
  rate curve's phase is a function of the beat and a loop lands on it every
  pass. Hits are position windows; one shorter than a frame fires once in
  forward play and never after a jump.
- The show library compiles each timeline at load; the playing track's is
  pinned with its match, and `track.match.has_timeline` shows on the Sync card.
- `python -m engine.showfiles explain TRACK BEAT` prints what a timeline says at
  a beat. Two automation rows for one target warn that the lower is never
  heard.
- The example timeline's movement lane moved above its scene lane, so its look
  overrides the outro's movement as intended.

### Added — F19f: which prepped track is playing

- **`--show-dir`** points the engine at a show folder (or `$KLIGHTS_SHOW_DIR`,
  or `show_dir` in `klights.local.json`). Without one nothing below exists.
- **Matching**, in layers, strongest first: beat-link's signature; a rekordbox
  id, only where the title agrees (every USB stick numbers from 1); a manual
  link; title + artist + album; title + artist -- the last three only where the
  durations could be one file. Two tracks at the deciding layer are
  *ambiguous* and neither plays. The snapshot's `track.match` says which track
  and how it knows; the Sync card shows it.
- **`track_link`** (configure): "this playing track is that prepped track".
  Saved on the prepped track as an alias, plus the deck's signature when
  beat-link sent one. It applies from the track's **next play**, never
  mid-song.
- **Hot reload that never moves a playing track.** The folder is polled on its
  own thread, reloaded on the worker, and swapped in by one reference; the
  playing track keeps the load it was matched against until it changes. A file
  broken by a half-finished sync keeps its last good version (`Folder.failed`).
  `show_reload` (configure) reloads at once. `show.json`'s per-source latency,
  pause grace and track-change limit now reach the transport.
- **Grid cross-check.** rkbx_link's bar phase, or beat-link's beat count, is
  compared with the prepped grid; two seconds of disagreement is
  `track.grid_warning`, with the offset in beats.

### Added — F19e: the wire format, shipped to both sources

- **`bridges/prolink/blt/klights.clj`**: the beat-link-trigger expressions that
  send the tempo master's position, playing state and identity (`/klights/v1`)
  and its tempo and bar phase on every beat. Not yet run against a CDJ. A golden
  fixture holds the exact bytes they must produce: the engine decodes them, and
  `bridge.py --blt` reproduces them.
- **`bridges/prolink/rkbx_link.config.example`**: a complete rkbx_link config
  for kLights, with the two settings its shipped config has off (`master/time`,
  `master/phrase`) turned on, and every other choice commented with why.
- The bridge README no longer says beat-link-trigger "emits OSC" with "no code
  of ours in the path". It sends OSC only from expressions, which are now ours.

### Added — F19d: which track, and where in it

- **The sync port reads position and identity.** rkbx_link's `/master/time`,
  artist, album and original bpm; our `/klights/v1/pos` and `/track` for
  beat-link-trigger, decoded strictly at a fixed arity; and the same fields as
  JSON. Every one is range-checked. Addresses the tools send that we choose not
  to use (`phrase/next`, `beat/trigger`) are counted as ignored rather than
  rejected, so healthy rkbx_link traffic no longer reads as unreadable packets.
- **`engine/transport.py`.** The track's position between packets. It is a
  smoothed line, so jitter never reads as motion. It detects jumps (loops, hot
  cues), pauses (including rkbx_link's silence), stalls and scratching. Identity
  arriving a field at a time waits for the rest, and a jump from such a source
  waits 30 ms, so a master switch reads as a track change and not as a jump in
  the old track. Lock-free: one immutable state, swapped by reference.
- The snapshot has a `track` section: state, title, artist, source, position,
  rate. Taking the clock back clears it.
- **The console's DJ sync card names the track and where in it**: position
  against length, its state, and the DJ's pitch. "Packets late" is shown as
  such rather than as a stopped deck.
- **`bridge.py --fake --track`** plays the show-example's synthetic track as a
  deck: position at 30 Hz, shaped as rkbx_link (`--osc`, silent while paused)
  or as our beat-link-trigger expressions (`--blt`). `--script` drives the
  transport: loops, hot cues, pauses, a master switch to a guest track, a
  scratch. Each shape is tested through the real decoder and transport, and
  must tell the same story.
- **`bridges/prolink/capture.py`** records what a source really sends, byte for
  byte, while forwarding it to the engine; `--replay` sends the bytes back
  exactly. Venue captures become regression tests.

### Added — F19c: the prep tool

- **[`bridges/rekordbox/prep.py`](bridges/rekordbox/README.md)** reads what
  rekordbox already knows about a track and writes it into a show folder.
  Identity and file location come from rekordbox's XML export. Beat grid,
  phrases, cues and waveform come from its analysis files. The two are joined on
  the audio path the analysis records, falling back to the file name, which is
  what makes a USB stick work. Stdlib only; no encrypted database is read.
- **`bridges/rekordbox/anlz.py`**, a reader for `ANLZ0000.DAT/.EXT`: the grid,
  phrases (unmasking rekordbox 6+'s XOR mask), named cues, waveforms and the
  audio path. Phrase labels are rekordbox's own, including high-mood numbering
  ("Up 3", "Chorus 2").
- Re-running prep changes nothing. A track renamed in rekordbox keeps its old
  name as an alias. A re-gridded track is reported along with every timeline
  drawn on the old grid, and prep never touches a timeline.
- `engine/tracks.py`: how track names are compared. It forgives accents, case,
  "&" and "ft.", but never "Original Mix" against "Extended Mix".

### Added — F19b: a track's time, and the show folder

- **`engine/tracktime.py`.** A track's own musical time, from rekordbox's beat
  grid: beat 0 is the first downbeat, a position in the audio maps to a beat
  through anchors, and the map is continuous and monotonic by construction
  across tempo changes. A grid fingerprint (`rev`) lets a timeline notice that
  its track was re-gridded after it was drawn.
- **`engine/showfiles.py`: the show folder and its formats.** Show settings,
  tracks, timelines, routines, template sets and waveforms, each validated for
  shape and meaning, with errors for what cannot mean anything and warnings
  for what is merely suspicious. One authoring API for the designer, MCP and
  the prep tool. Writes are refused if the file changed since it was read;
  sync-service conflict copies are never loaded; unknown keys survive a round
  trip. `python -m engine.showfiles init|check`.
- **Six generated schemas** in `schemas/`, so an editor completes a timeline
  as it does a rig. `config.Spec` gained discriminated variants, emitted as
  `oneOf`, and each format has its own version.
- **[`shared/show-example/`](shared/show-example/)**: a complete show folder
  around the fake bridge's synthetic track, with one of everything a timeline
  can hold.
- `validate` is tested against 4,570 mutated documents and never raises. That
  sweep found four ways it could.

### Added — F19a: replies, a worker thread, and one validator for sync

- **A command can ask for a reply.** Give it an `id` and the engine answers the
  sender, and only the sender, with `ok` or the reason it failed. The designer
  needs to know whether *its* save worked, not scan a notices list shared with
  every phone. Commands without an id behave exactly as before.
- **A worker thread for slow work.** Parsing, matching and writes to a shared
  folder cannot run on the output thread, which already drains every command
  inside the 25 ms frame. They run on the worker and hand their result back to
  be installed at a frame boundary. Tested by thread name, because a worker
  that quietly ran jobs inline would pass every other test.
- **The WebSocket `sync` command now goes through the UDP port's checks.** It
  used to take the message as it came: a bpm of 900 reached the clock, and a
  track title could be any length. Both routes into the clock now share one
  validator.

### Fixed — a quoted "false" read as true

- `sync.clean` turned `phrase_measured: "false"` into True, because
  `bool("false")` is. A sender that quoted its booleans claimed a measured
  phrase by saying it had none. Flags are parsed now, and one that is neither
  true nor false is refused rather than guessed.

### Changed — the fake bridge speaks rekordbox's phrase vocabulary

- **`bridge.py --fake` used phrase names rekordbox never sends.** Its script said
  Build and Drop; rekordbox's phrase analysis says Up, Chorus and Down, and
  numbers repeats ("Verse 1", "Up 2"). Anything keyed on the fake's names would
  have passed every test and done nothing at a venue. The script is now a
  three-minute track in rekordbox's own labels, and `test_sync` refuses the two
  names that can never arrive. The README and roadmap said the same wrong thing
  and are corrected.

### Added — F19 design record

- [`docs/design/timecoded-shows.md`](docs/design/timecoded-shows.md): shows
  driven by which track is playing and where in it. The decisions, the designer
  layout chosen from three clickable mock-ups, and the staged build. Nothing in
  the engine changes yet.

### Fixed — the rate buttons, and a test that meant two different things

- **The six Rate buttons broke mid-token on a phone.** `overflow-wrap: anywhere`
  is right for a look called "Split Pink/Green" and wrong for a five-character
  label: six equal flex children at 420 px are narrower than `0.25×`, so it
  rendered "hol / d" and "0.25 / ×". They are a six-column auto-fit grid now,
  with the labels atomic and the *row* giving way instead — six across where
  they fit, four plus two on a 320 px phone.
- **`test_server` failed on Linux and passed on Windows**, for the whole
  repository's history. The stuck-client check sampled presence at a fixed
  moment ~2.6 s after the client went quiet. But the drop is not on the same
  clock as the stall: the app queue only fills once the kernel stops absorbing
  writes, and the buffer that has to fill first is the *server's* send buffer,
  which Linux auto-tunes into the megabytes — so the same client is dropped in
  under a second on Windows and after ~5 s on Linux. It now waits for the drop.
  The behaviour was always correct; the test was measuring the platform.
  Its first clause also looked for a notice reading `stalls the broadcast`,
  which no code has ever emitted — dead since it was written.

### Changed — F18, documentation

- **`README.md` is a user document now.** What it is, screenshots, quick start,
  how to use the console, how to build the UI, where everything lives. The
  rationale that filled it moved to `docs/`.
- **New: [`docs/runbook.md`](docs/runbook.md)** — show night start to finish,
  written to be followed by someone who did not build this. The QLC+ era had one
  and the engine era did not.
- **New: [`docs/engine.md`](docs/engine.md)** — layer order, the three slots, the
  taper, the protocol, the tiers, the config contract.
- **New: [`docs/ROADMAP.md`](docs/ROADMAP.md)** — F1–F18 reconstructed, the
  decisions worth knowing, and what is *not* built. Six places in the code cite
  "the plan" as an authority that was not in the repo.
- **History and working notes moved out of the READMEs**, verbatim:
  `previz/README.md` 692 → 221 lines, with the optics and modelling essays now
  in [`docs/design/previz-optics.md`](docs/design/previz-optics.md);
  `events/despacio/README.md` 1418 → 325 lines, with the QLC+/APC40 era in
  `events/despacio/NOTES.md`.
- Screenshots: a real previz render and the console's own plan view, both
  produced by this project rather than mocked up.
- **The web console is in the README with screenshots** — all four performance
  tabs on a phone and the Setup tab on a laptop, captured from a live engine
  running the despacio show rather than staged. Plus what the README never said
  out loud: the console is served by the engine itself, and several people can
  be on it at once with no locking.

### Added — F18, the 60-minute soak

[`spike/timing/soak.py`](spike/timing/soak.py) runs the **real engine** for an
hour with auto mode on, and watches the clock and the garbage collector
together.

Running `jitter_harness.py --minutes 60` would not have answered the question F2
actually left open. Its caveat was specific: *"GC collections were 0 in every
run… a real engine holding cyclic object graphs will collect, and that is the
most likely source of a long-run outlier."* The harness holds no cyclic graphs,
so it would have reported zero collections again and proved nothing. The soak
drives the engine's real frame loop and real layer stack, recomposing on every
auto look change, and reports collections per generation and object-count drift
alongside fps, drops and worst interval error.

Art-Net goes to loopback and the script refuses a broadcast address: a soak is
not a reason to move a rig that might be plugged in.

**Result — 62 minutes, 148 802 frames: zero drops, zero evaluation errors, mean
40.0008 fps, worst interval error 2.659 ms against a 10 ms threshold.** The GC
worry was real and small: collections happened (9, where the harness saw zero),
so the engine does hold cyclic graphs — but gen-1 and gen-2 never ran, and the
predicted long-run outlier did not appear. Tracked objects moved 0.6% and
oscillated rather than climbed, so nothing leaks.

Two honest limits on that: the machine was **not idle** — the run shared it with
the test suite, several UI builds and an Unreal editor — and `soak.py` records a
running max rather than a distribution, so there is no p99 to compare with the
harness. Both are written into `FINDINGS.md` beside the result.

### Added — F16, tempo and phrase from the DJ

**No analysis is written here.** No beat tracking, no DSP, no audio in the
chain. Pro DJ Link is reverse-engineered thoroughly enough that beat position
and rekordbox's own phrase labels are a *read*; anything inferred from a room
mic would be worse data, obtained harder.

- **`engine/sync.py`** — a `sync` command and an opt-in UDP port
  (`--sync-port`), speaking **JSON or OSC**. OSC because both tools worth using
  emit it, so pointing either at the port is the whole integration.
- **Both rigs, by different mechanisms.** CDJs via **beat-link-trigger** over
  Pro DJ Link; a **DDJ-1000 via rkbx_link**, which reads rekordbox's memory —
  a DDJ is USB and never speaks Pro DJ Link, so the network route does not
  exist for it. The decoder handles both address shapes.
- OSC addresses are matched on a **two-component suffix**, not the last
  component. rkbx_link sends `/master/bpm/current` *and*
  `/master/phrase/current`; last-component matching read a phrase label as a
  tempo. Found by reading rkbx_link's actual spec rather than assuming a shape.
- `beat/subdiv/<n>` — rkbx_link's 0–1 ramp looping every *n* beats — is scaled
  back up to beats, which is what `align_bar` needs. Taking the raw 0–1 would
  squeeze every downbeat correction into the first beat of the bar.
- **Numeric decks are ignored; only `master` drives.** `/1/bpm` and `/2/bpm`
  during a blend are two decks fighting over one clock, and the tempo that
  comes out belongs to neither. Dropped packets show in the rejected count,
  where following the last deck that spoke would be invisible.
- A phrase label over OSC implies `phrase_measured`. OSC cannot send the flag
  separately, and without this the rekordbox path would report phrases while
  auto look changes quietly kept landing on bars.
- `bridge.py --fake --osc` sends the feed shaped as rkbx_link, so the rekordbox
  decoder is exercised without a DDJ, rekordbox and a licensed rkbx_link in one
  room.
- **`MasterClock.align_bar`** — the safe way for a per-beat source to keep the
  grid honest. Corrects to the nearest equivalent beat, so phase never moves
  more than half a bar however wrong the grid was; passing absolute `beat` every
  packet re-anchors the timeline and makes every move judder, which is the trap
  `sync` has always documented and now has an alternative to.
- `phrase_label` and a countdown stored as an **absolute** end beat, so a bridge
  can speak once a bar rather than once a beat and the UI still counts down live.
- **Staleness is reported.** A bridge that dies leaves the show free-running at
  the tempo it was left holding with the clock still naming it — locked-looking
  and wrong. The Show tab's Sync row shows the age of the last packet, says NO
  SIGNAL past four seconds, and take-over is always one tap.
- **`bridges/prolink/`** — a sidecar, stdlib-only, that never shares a process
  with the engine. `--fake` is a synthetic feed with a scripted Intro → Build →
  Drop → Outro timeline, so the whole downstream path is provable at a desk with
  no players in the room; `--replay` plays back a captured session. `--live`
  refuses honestly and names the two routes rather than half-working.

**The security shape**, since this is a write path into the show clock that no
token guards — a datagram cannot be challenged:

- off unless `--sync-port` is given;
- loopback by default (`--sync-bind` to change it);
- the listener parses into a **fixed set of clock fields** and the engine builds
  the command. This port cannot patch a fixture, write a calibration or panic
  the rig whatever is sent to it. That is structural, and both `test_sync` and
  `test_server` assert it by firing exactly those commands at it.

### Added — F17a, the plan view

The previz that will actually get used. Unreal renders a beautiful room and
needs a GPU, a 90 GB engine install and a second machine; every number this
needs is already in the snapshot the phone in your hand receives ten times a
second.

- **The room from above, on the Move tab, in both modes.** Room, crowd zone,
  canopy, mirror ball, every fixture, and every lit beam drawn to where it
  actually lands at the width it actually spreads to (`throw × tan(half-angle)`).
- SVG rather than canvas: six fixtures at ten frames a second do not need an
  imperative draw loop, and an SVG plan is made of elements a test can assert
  on, where a canvas is one opaque bitmap.
- The engine now publishes `lands_at`, the landing POINT. The UI cannot derive
  it — `aim.bearing` is the servo's delta from its mount facing, and the mount
  facing lives in the calibration.
- Beams the safety taper is holding are ringed in amber. A beam at 50% because
  someone pulled it down and a beam at 50% because it is over a head are
  different facts, and opacity alone cannot tell them apart.
- The legend states how many fixtures have **no** position and are therefore not
  drawn. Silent omission is the dangerous failure: a plan missing two fixtures
  still looks like a complete plan.

### Fixed

- **Static fixtures reported no position at all.** The snapshot filled
  `position` from the geometry head list, which only movers are in — so both
  despacio pinspots were invisible to anything downstream. Now taken from the
  patch when there is no geometry.
- **The UI bundle was served with no cache headers.** Browsers apply their own
  heuristic to `index.html`, so a phone that had the console open before an
  engine update kept asking for a hashed asset the rebuild had deleted — and got
  `index.html` back as JavaScript, which is a blank console. `index.html` is now
  `no-cache` and hashed assets are `immutable`; a missing asset is a 404 with a
  reason rather than an SPA fallback. Found by watching the browser serve a
  stale bundle while checking the plan view.

### Added — F15, per-slot rate

The last thing the three slots did not have independently, and the reason it
waited for a pass of its own rather than being bolted onto the macros.

- **Three motion phases instead of one**, in `state.SlotPhases`. Each slot's
  layers read that slot's phase, so a colour chase at 0.5× under a move at 2×
  is now something the engine can express at all. The old console needed a
  separately stored chase per combination, which is a large part of how it
  accumulated 206 looks.
- Each phase is integrated as `rate × d(bar)`, **never** computed as
  `rate × bar`. The naive form jumps by `(new − old) × bars_so_far` the instant
  a rate changes — at bar 40 a move from 1.0 to 1.5 snaps every running move
  forward twenty bars. That hazard is what made this a milestone rather than a
  knob, and it is guarded per slot in `test_auto`.
- Rate 0 is a **hold**, not a speed: it parks a slot on its current frame while
  the others keep running. Negative rates are refused, and the reason is the
  cued chases — those travel dark and light on arrival, so running one backwards
  means holding first and travelling second, which reads as broken rather than
  reversed. Reverse needs its own thinking about `cue_path`, not a sign flip.
- **A cued chase keeps its own dimmer on the movement phase**, deliberately.
  Its darkness is part of the routine, not a level look, and letting the level
  rate move it would light the head before it had finished travelling.
- Slot rates multiply auto mode's energy rate rather than replacing it, and the
  `timing` axis still freezes every slot at once.
- `rate` command, `auto.slot_rates` in the snapshot, and stored in **presets
  and cues** — stored only when something is off 1×, because a preset that
  always wrote 1× would silently undo a rate set after it was saved.
- **The Move tab's Speed card is now its Rate card.** It had been a second copy
  of the Show tab's global Speed — two controls doing one thing in two places,
  and actively confusing next to a rate that also makes the move faster. Speed
  stays on Show, beside the tempo it belongs to.

### Fixed

- `engine/tests/dump_snapshot.py`'s hand-written preset fixture had drifted (see
  below); the same regeneration now covers `slot_rates`.

### Added — F15, preset banks and Perform mode

The answer to "how do presets grow without the console getting worse", which is
review finding #17 and the last substantial piece of F15.

- **Presets sit on pages of eight**, with `bank` and `cell` in `presets.json`.
  A pad is a *place*, not a sort order: saving over a preset keeps its pad, and
  adding or deleting neighbours does not shuffle it. Eight because that is the
  APC40 grid the despacio show ran on for two years — "the drop is bottom-right
  of bank 2" is muscle memory that already exists.
- Empty pads are drawn and are tappable: tapping one and typing a name saves
  *there*, rather than wherever the engine had room.
- `preset_move` swaps rather than refusing, so a bank reorders without needing
  a spare pad to shuffle through. `preset_tag` sets cross-cutting labels
  (`intro` / `build` / `drop` / …); the filter row is absent until something is
  tagged, so it costs nothing to anyone not using it.
- **Migration is automatic and non-destructive.** A `presets.json` written
  before banks existed, or hand-edited into a collision, opens as a working
  grid — first claim on a pad wins, everything else is rehomed. Saving is never
  refused for want of space: the last bank grows.
- No "recents" or "favourites" section, deliberately. Both are a *second* place
  the same preset lives, which is the clutter this removes wearing a helpful
  hat. A fixed pad is already the answer to "where is it".
- **Perform / Design in the header.** Perform hides Setup and the read-only
  diagnostics; Design is the full console. Defaults to Perform on a phone and
  Design on a laptop, persists per device, and is always one tap from the other.
  It is a preference about screen space, **not** a permission — `--token` and
  the view/operate/configure tiers are what the engine enforces.
- **Panic moved from Setup to the bottom of Show.** Perform hides Setup, and a
  rig you cannot force to zero from the surface in your hand is not a rig anyone
  should be running. Still nowhere near the master.
- The strobe policy card is the one thing filtered by *content* rather than
  kind: "you are capped" is reassurance and Perform drops it, "nothing is
  capping this" is the reason the card exists and shows everywhere.
- **The two unbounded lists are collapsed** (finding #17's other half): the
  Color tab's "Applies to" grid and the Bright tab's dimmers are groups first,
  with individual fixtures behind a disclosure that opens itself when one of
  them is actually overridden.

### Fixed

- `engine/tests/dump_snapshot.py` hand-wrote its preset fixture and it had
  drifted: it still carried `"movement": "Lazy Circle"` from before slots went
  per fixture group, so every UI test rendered a preset shape the engine had not
  produced in months. It is now built from the snapshot's own selection — the
  exact failure that file's docstring exists to prevent, in the one part of it
  that was not captured.

### Changed — safety framing corrected

- **`docs/SAFETY.md` was written as though these were lasers.** They are 60 W
  LED beam heads: a beam in the eye is dazzling and unpleasant, not injurious,
  and the aversion response is what actually protects anyone. The taper is a
  **comfort and quality feature**, not a protective device, and the document now
  says so throughout.
- That overstatement was not harmless. A safety page that cries wolf gets
  discounted wholesale, taking the two items that *are* a different category
  with it. Those are now the headline rather than a footnote:
  **photosensitive epilepsy from strobe** — a genuine medical risk that nothing
  in the software limits, since strobe is reachable from ported looks and from
  auto mode's energy axis with no rate cap — and **lasers**, which are
  unmodelled and regulated.
- **A strobe policy now exists**, enforced in the same unconditional post-stack
  position as the taper so no look or auto axis can outrank it. Deliberately
  *not* a frequency limit: the fixture profiles declare "strobe slow to fast"
  with no Hz at either end, so a number in Hz would be invented to look
  rigorous. What is enforced instead is a **ceiling** on how far up each
  fixture's own band anything may drive the shutter (faster is further up), and
  a **maximum continuous duration** — which is what the guidance is actually
  about and which holds whatever the rate turns out to be. After a cutoff the
  shutter must stay open for `recover_seconds`, so it is a stop rather than a
  duty cycle. Defaults change nothing; despacio is set to 75% / 8s as a
  conservative, explicitly unmeasured starting point.
- The policy is printed at startup and shown on the Bright tab, including a
  loud UNLIMITED when nothing is limiting it.
- Same correction applied to `engine/safety.py`, the README, the Rig panel and
  the test commentary, so the codebase does not carry two framings.

### Added — F15, the missing live controls

- **Flash** — a momentary bump per group, held rather than latched, on the
  Bright tab. It *sets* intensity rather than multiplying it, so it bumps a
  group you have trimmed all the way down, which is the case it exists for. The
  safety taper still applies after it. Released on pointer-up, pointer-leave and
  pointer-cancel, and cleared on reconnect: a thumb sliding off the button or a
  phone locking mid-press never sends a normal release, and a flash stuck on is
  a group stuck at full.
- **`auto_interval` and `palette_select` finally have senders.** Both have had
  working handlers since F7 and nothing in the UI that sent one, so how often
  the show rearranges itself was the only auto setting editable exclusively in
  code. Interval buttons now sit under the Auto toggles, and a long-press on a
  palette swatch selects it.

### Added — F15, the cue list

- **The Night cue list is back.** The QLC+ show's six Collections — Warm Up,
  Idle, Deep, Spiral, Peak, Landing — were the actual shape of the set, and the
  porter skipped every one with *"Collection — rebuild with motion primitives"*.
  They have been missing since F9, which made this the one live regression from
  the old console rather than a new feature. `events/despacio/cues.json` rebuilds
  the whole night as nine cues, using looks that already exist plus the F15 shape
  macros to make each section its own size and spread.
- **A cue is the same three slots a preset is**, plus a fade and an optional
  hold. A cue list with its own private notion of a look would be a second way
  to say the same thing, and the two would drift.
- `fade` and `hold` are in **beats**, not seconds — everything authored in this
  engine is musical, and a cue list that ignored tempo would be the one surface
  drifting out of the music it is cueing. Every despacio cue holds at 0, meaning
  every one waits for GO: the operator deciding when the drop is, which is why
  the original worked.
- **Crossfade between shows** (`state.evaluate_crossfade`), which also closes the
  long-standing "a look change is a hard cut" gap independently. Blends in
  *parameter* space — blending rendered DMX would interpolate a colour-wheel slot
  index and quantise the aim to 8 bits before smoothing it. Aim blends in
  degrees, and because `bearing_delta` is unwrapped servo rotation, a head
  crossing ±180° travels the way a yoke physically can rather than teleporting.
- Both stacks are evaluated, blended, and *then* finished, so safety and the
  strobe policy see the aim actually going to the wire, once. Running them per
  side and blending the results would let a fade pass through a state neither
  show was allowed to produce.

### Added — F15, shape macros

- **Four live controls over whatever movement look is up**: size, spread, and a
  two-axis centre. The ported library holds 103 poses and 26 paths because QLC+
  stored DMX values and had no parameters, so every variation of a move had to
  be its own scene. These are the variations that actually recurred — "Ball
  Wave" with the centre dropped 40° *is* the look that used to need a separate
  "Floor Wave" entry.
- They live on `EvalContext`, not in the composed look, so they survive an auto
  look change the same way `energy` does, and cost nothing per frame beyond a
  multiply.
- Size scales about zero, and zero is each head's own calibrated ball aim — so
  it scales about the look's own centre, per head, with no extra geometry.
  Applied in `move_layer`, the single point every movement offset passes
  through, rather than in each of the three offset builders.
- Spread reuses `motion.phase`'s existing cycle-relative offset, so spreading n
  heads evenly is `spread * i/n` regardless of the cycle length.
- **No macro can outrank the safety taper**, and there is now a test that says
  so across the extremes of every macro: `evaluate` runs `apply_safety` after
  the entire stack, so a macro can only change *which* aim the taper is asked
  about, never whether it is asked. QLC+ parity is unchanged at identity.

### Added — F14, editing the rig

- **`engine/patch.py`** — one set of rules for what a legal patch is, shared by
  every surface that edits one. Pure functions over config dicts; nothing in it
  writes a file, so a caller can preview a change, refuse one, or diff it first.
  Errors reject (channel clash, duplicate name, unknown mode); warnings apply
  and inform (over-inventory, a removed mover shifting head order, an emptied
  tag group silently disabling looks).
- **`python -m engine.patch`** — describe, profiles, venues, add, remove,
  address, tags, position, autopatch, venue, import, new. Dry run until
  `--write`.
- **`mcp/klights_mcp.py`** — the same operations over MCP, so the rig can be
  described in conversation. JSON-RPC over stdio in pure standard library, no
  SDK. Registered in `.mcp.json`. Covered by `engine/tests/test_mcp.py`, which
  drives it through a real pipe rather than importing it, because the transport
  is where a stdio server actually breaks.
- **Writes refuse while a show is running.** The engine writes
  `events/<name>/.engine.lock` at startup; the CLI and MCP server check it. The
  engine reads its config once, so an edit mid-show leaves the file and the rig
  disagreeing with nothing on screen to explain it.
- **A Patch section on the Setup tab** — add, remove, re-address, retag and
  autopatch from the phone with the rig in front of you. A *section* rather than
  a sixth tab, following Rig and Venue, which were tabs once and became sections
  here because they are all one job. Locked by default: every other control on
  that surface is recoverable by pressing it again, and a re-addressed rig is a
  walk around the room with a torch.
- **A patch edit applies without restarting.** `patch_apply` swaps the whole rig
  at a frame boundary — the same place every command already lands, so no frame
  is ever built from two rigs. The new rig is loaded and validated *before*
  anything is adopted, so a typo or a missing `.qxf` costs a red notice and the
  old rig keeps running; a bad edit must never take down a live show. Taper
  memory is seeded dark rather than cleared, which makes the safety slew limiter
  double as the reload crossfade instead of needing one of its own.
- **Access tiers** — `view` / `operate` / `configure`, checked at the single
  point a command enters the show. A token is generated per run and printed
  inside the URL so it survives being a QR code; `--no-token` and `--bind`
  restore or narrow the old behaviour. Cross-origin WebSocket handshakes are
  refused. The UI carries the token through and shows a VIEW ONLY banner.

### Fixed — F14

- **One stuck client could freeze the console for everyone.** `send_all` called
  a blocking `sendall` from the single broadcast thread, so a phone that locked
  its screen filled its TCP window and stalled every other client — at a venue,
  indistinguishable from the engine hanging. Now a bounded queue per client
  drained by that client's own thread, so the broadcast thread cannot block; a
  client three snapshots behind is dropped and reconnects.
- **The drop path had the same bug.** Dropping a stuck client called
  `WebSocket.close`, which sends a courtesy close frame with a blocking
  `sendall` — to the socket whose buffer was already full. The close frame now
  has a deadline and the socket is torn down underneath it regardless.
- **Preflight's bundle check asked the wrong question**, failing on a correct
  bundle that was staged but not committed. It now compares the directory on
  disk against a rebuild, which is what actually gets served. CI still asks
  whether the committed bundle matches the committed source.

### Added — F13, config as a contract

- **`engine/config.py`** — every config file is now validated on load against a
  declared shape, reporting *every* problem at once with the file, the path
  within it, what was found and what to do about it. A hand-edited file usually
  has the same mistake in several places, and fixing them one restart at a time
  is how a load-in runs late. Previously a typo was a `KeyError` three modules
  deep and the spec lived only in the files' own `_comment` blocks.
- **Atomic config writes.** `venue.json`, `calibration.json` and `presets.json`
  are written from the running show; they used a plain `write_text`, which
  truncates then writes, so a crash at the wrong instant left a zero-length
  calibration and a show that would not start. Now written beside the target and
  renamed, with one `.bak` kept. Verified by simulating a failure between write
  and rename.
- **`shared/venues/`** — a room outlives a show. `rig.json` names one with
  `"venue": "despacio-room"`; an event with no `venue` key keeps its own
  `venue.json`. despacio's room moved to `shared/venues/despacio-room.json`.
  Note that saving the venue from the UI now edits the *shared* file, which is
  the intent — a crowd zone measured tonight is a fact about the room.
- **`schemas/`** — JSON Schema for all six formats, **generated** from
  `engine/config.py` by `shared/tools/gen_schemas.py` rather than hand-written,
  because two descriptions of one file agree only on the day they are written.
  Each config names its schema in `$schema`, so VS Code gives completion and
  inline validation with no extension. CI and preflight fail on drift.
- **The inventory is load-bearing.** `Rig.warnings()` now checks the patch
  against `shared/inventory.json`: a model that is not there, more units patched
  than owned, or one the inventory marks `unverified`. Warnings, not errors —
  when the two disagree the inventory is at least as likely to be the stale one.
- **Looks can bind offsets by fixture name.** A look's per-head arrays were
  positional, so re-ordering the patch or adding a head silently re-pointed
  every look — silently, because each head still moved to a position that was
  authored, just not its own. Entries may now carry `"fixtures": [names]`; the
  porter emits it, and despacio's 206 looks were regenerated to include it
  (a purely additive diff — 774 insertions, no deletions, with QLC+ parity and
  the pose round-trip unchanged). Looks without it stay positional, which is
  what they were authored against.

### Added — F12, the guards

- **CI** (`.github/workflows/ci.yml`): the engine suites on Linux and Windows
  across Python 3.10 and 3.12, the UI suite and typecheck, and a bundle job
  that rebuilds `ui/dist` and fails if it differs from what is committed. A
  second step asserts every asset `index.html` references is actually tracked —
  the specific shape of the near-miss below, which survives a matching rebuild
  if the new assets were simply never added.
- **`python -m engine.tests`** — discovers every suite, runs each in its own
  subprocess (they set process-wide timing and assert on wall-clock behaviour,
  so sharing an interpreter would make one suite's leftovers decide another's
  result), and reports once. Also runs the four module self-tests. Replaces a
  bash `for` loop that stopped at the first failure, on a Windows-primary
  project. `-k` narrows, `-v` streams.
- **`python scripts/preflight.py`** — the one command before leaving for a
  venue: suites, patch sheet, rig validation, the event's own venue checks,
  QLC+ parity, and the bundle guard. Exit 0 means go.

### Fixed — F12

- **The UI build was not byte-reproducible.** `.gitattributes` had `* text=auto`,
  so a Windows clone checked out `ui/index.html` with CRLF, vite treated the
  stray CR as page content, and the rebuilt `index.html` came out with `\r\r\n`.
  A fresh clone therefore could not reproduce the committed bundle, which would
  have made the new CI guard fire on every Windows run and be disabled as noise
  within a week. The web sources are now pinned to `eol=lf` and `ui/dist` is
  marked `-text`: normalising a build artifact is meaningless, and it made every
  byte comparison platform-specific. Verified by rebuilding in a fresh clone
  with `core.autocrlf=true`.

### Added
- `LICENSE` — Apache-2.0.
- `docs/SAFETY.md` — what the beam taper guards and what it does not, the three
  unmeasured inputs (`beam_deg`, `ball_radius`, the crowd head band) with the
  direction each errs in and the procedure that retires it, and the
  `crowd_level` trade-off. Linked from the README.
- The server prints its live taper policy at startup, so "why is nothing
  dimming" is answered before it is asked.
- `engine.__version__`, reported in the WebSocket snapshot and shown on the
  Setup tab — a phone can serve a cached bundle, so this is the only reliable
  answer to which engine it is driving.
- `engine/tests/data/ball_frame.json` — a captured universe-0 frame, preserved
  from scratch space.

### Fixed
- **`ui/dist` could ship broken.** The tracked bundle assets and the built ones
  had diverged, so a `git commit -a` would have published an `index.html`
  pointing at two files that were not in the tree — a blank console at the
  venue. The bundle is now staged as one consistent set. A CI guard against the
  recurrence lands in F12.
- `engine/servo.py`, `shared/tools/qlc_parity.py`,
  `engine/tests/test_qlc_parity.py` and `events/despacio/presets.json` were
  untracked despite the README documenting `qlc_parity.py` as a verification
  command. A fresh clone could not run it.
- README: five tabs, not six — Rig and Venue became panels inside Setup, and
  **Bright** was undocumented. The despacio library is 206 looks, not 198.
- `engine/__init__.py`'s module map listed one module out of sixteen.

### Removed
- `temp/` — 6.8 MB of stale duplicates that was untracked *and* unignored, one
  `git add .` away from entering history. The 16 unique workspace autosaves and
  7 dated pre-change workspaces were moved into `events/despacio/backups/`
  first; everything else was an older copy of a tracked file. `temp/` and
  `scratch/` are now ignored.

---

## F1–F10 — 2026-07-23 → 2026-08-08

The engine, built to replace the QLC+ workspace that ran the despacio show.

| | |
|---|---|
| **F2** | Timing spike: Python can hold the DMX clock, given `sys.setswitchinterval(0.0005)` and Windows `timeBeginPeriod(1)`. External load is harmless; in-process GIL contention is the killer. |
| **F3** | Engine geometry and rig model — 16-bit positions, three mount profiles, `.qxf` profiles parsed for channel roles rather than addresses. |
| **F4** | Layered state, the beam-aware safety taper, Art-Net output, the 40 fps frame clock. The taper dims *to* a crowd level rather than to zero — "not dazzling", not "never lands on anyone". |
| **F5** | Fast re-aim: a multi-point solver recovering position, offsets and invert flags together, plus drift detection and calibration snapshots. |
| **F6** | Musical timing — beats, bars, phrases, tap tempo, and motion continuous across every tempo change. |
| **F7** | Auto mode: four independently toggleable axes (timing, look changes, palette, energy). |
| **F8** | The show server (WebSocket state sync, zero dependencies, a hand-rolled RFC 6455) and the web UI — one responsive surface from phone to desktop, with 53 tests against a captured real-engine snapshot. |
| **F9** | Ported the QLC+ library — 206 looks round-tripping exactly, with a frame-level parity checker that classifies every differing channel of all 512 and admits none it cannot explain. |
| **F10** | Unreal previz sharing the show's own decoder, driven by the same Art-Net the rig sees. |

Before F2: the repo reorganised into `events/`, `shared/` and `docs/`; line
endings pinned via `.gitattributes` because QLC+ rewrites the whole `.qxw` on
save; and the post-event despacio work salvaged from untracked state.
