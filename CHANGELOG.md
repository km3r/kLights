# Changelog

Notable changes, newest first. Versions follow [semver](https://semver.org);
until 1.0 the config file formats may change between minor versions, and any
break will say so here with a migration note.

The `F<n>` labels are the project's own feature milestones;
[`docs/ROADMAP.md`](docs/ROADMAP.md) lists them in order, with why each one
mattered.

---

## Unreleased

F19's milestones 2 and 3 are **F22** (templates, pads, pre-matching) and
**F23** (VJ outputs) below. Their commits were made as F20a-e and F21a-d,
before F20 (the standalone previz) and F21 (parametric looks) reached main;
the commit messages keep those labels.

### Fixed — every lane can be filled where it is, and roles say what they reach

- **Click an empty spot on a lane** to choose what goes there, in both editors.
  - In the routine editor, a clips lane offers its slot's blocks, then the
    rig's own (look, snapshot), and a hits lane offers flash, strobe and
    blackout. Before, the Blocks shelf always used the first lane of a slot, so
    a second colour lane for another role could not be filled, and a routine's
    hits lane could not be filled at all.
  - On a track, a scene, movement, colour or level lane offers routines and
    this rig's looks (a slot's lane, the looks for that slot), and a scene lane
    offers presets as snapshots. The palette lane offers this track's palettes
    and copies of the library's, and a hits lane offers hits. Before, the
    browser's click only reached the first lane of each kind, and any other
    lane needed a drag.
  - On an OSC, MIDI or visuals cue lane, the click adds a cue there.
  - What is added lands at the beat clicked and is selected, so its settings
    open below. A long menu has a search box, and Enter takes the first match.
    Enter on a focused lane opens the menu at the playhead. An empty lane says
    what a click adds.
- **Looks and snapshots can be placed on a track**: a Looks tab in the browser,
  and the lane menus. The inspector already edited them, but nothing in Studio
  could make one. A look clip can also be held to some of its fixtures
  (**Only on**).
- **Plays on**: a routine clip on a track, and a template set's pick (under its
  settings), show which fixtures each role reaches and can bind a role to
  another tag or to one fixture by name for that use (`bind`). The engine
  always honoured `bind`, but Studio could not show or set it. Changing a clip's
  routine now drops its old bindings, which named the old routine's roles.
- **Roles say what they reach**: the routine editor shows how many fixtures
  each role's tag reaches on this rig, or "no fixtures on this rig", with a
  link to where tags are set. Tags that the engine folds together as filters
  (despacio's "movers" and "corner movers") are all offered.
- Template sets: a pick's `bind` is now declared in the format and schema, and
  a draft from the set carries it onto the clips. A set made from a timeline
  keeps the clips' bindings. Two picks that differ only in their bindings are
  now two programs: before, the engine played one for both.
- Template sets: **a pick's visuals can be edited** under its settings: the
  scene the built-in visuals show while it plays, and that scene's settings,
  or none. Before, only a hand edit to the file could change them, and changing
  the pick's routine silently dropped them. A pick whose routine is missing
  from `routines/` keeps its settings in reach.
- **Drafting from a set brings its visuals**: each pick's visuals become a cue
  on the timeline's visuals lane, the way the palettes fill the palette lane.
  Live, a timeline with a visuals lane of its own silences the set's, so a
  draft that left them behind went dark on the projector.
- A blank template set is not offered when the show has no routines: the
  engine refuses a set with no pick, so it could never be saved. Adding an
  exact label or a bar cycle no longer writes a pick with no routine.
- Studio's look pickers (New from a look, a block's or parameter's look, a look
  clip) leave out retired looks and single chase steps, as the console does.

### Added — Studio's + New: one way in to making anything

- **+ New** sits in the top bar of Studio's library pages. It makes a timeline for a
  track, a routine, a template set or a palette, each with the starts that
  make sense for it. The keys T, R, S and P pick one while the menu is open,
  and **Tracks from rekordbox** is there too.
  - **A timeline** for a track that has none: a draft from a template set (the
    show's, to begin with), a copy of another track's timeline, or empty. A
    copy says how far the two tracks' phrases agree, since that is how far its
    clips are on the right phrases.
  - **A routine**: blank (bars, loops, folder), a copy of another, or **a look
    from the console**, which puts the look on its own lane as a routine. That
    routine is bound to this rig, because the look is.
  - **A template set**: blank, a copy of another, or **from a track's
    timeline**. That takes, for each phrase family, what its scene lane plays
    most (routine, variation and parameters together), plus the palette clip
    over it, the timeline's palettes and its most-used fade.
  - **A palette**: three colours, a copy of a library palette, or one that
    lives in a file, which brings its name with it.
- A timeline, routine or set **opens in its editor unsaved**, with its start
  applied. Nothing is written until Save, and a timeline's start can be undone.
  A palette has no editor page, so it goes into the library at once.
- The Routines, Template sets and Palettes pages' own New buttons open the same
  dialog. The inline id boxes are gone: a name is typed and the id (also the
  file name) follows it unless it is edited.
- A file that has never been saved now says **Save**, not "Saved", even before
  it is changed, so a copy or a draft can be saved as it stands.

### Added — the timeline editor: a browser, sections, and clip actions

- **A browser on the left of a track's timeline**, folding away like the other
  panels. It has routines (filed by folder, with each one's length and the
  slots it drives), palettes (this track's, then the library's) and hits.
  Click one to place it at the playhead on the lane it belongs on, or drag it
  onto a lane and beat. A library palette placed this way is copied into the
  track first. It replaces the wall of routine buttons in the right-hand panel.
- **A phrase is a section.** Click a phrase band to select it, then fill it
  with a routine, copy it (every clip and hit, trimmed to the phrase), paste
  it over another phrase, or clear it.
- **Clip actions**: copy, cut, paste at the playhead, duplicate straight after,
  and split at the playhead. They are on a right-click menu, on the
  inspector's buttons, and on Ctrl+C, Ctrl+X, Ctrl+V, Ctrl+D and S. A paste
  clears what it lands on rather than overlapping it. The routine editor
  takes the same keys.
- "At the playhead" says **template set / show** where nothing on the timeline
  drives a slot: the playing template set shows through there, or the
  operator's show where the set has nothing for the phrase.

### Changed — it is always clear whose palette you are editing

- In a timeline's and a template set's palette panels, each palette is tagged
  **copy of library**, **differs from library** (with *use library's*) or
  **only here**. The panels are called "This track's palettes" and "This
  set's palettes", and say that a change there changes that file only.
- The Palettes page says you are editing the library's palette. Its button is
  **Save to the library**, a copy that differs is called **different** (not
  older), and the update is **Give N copies the library's colours**.

### Added — a palette library for the show

- **`palettes/<id>.json`**, a new kind of show-folder file: one palette, with
  the name timelines and template sets know it by, and three `#rrggbb` colours.
  `python -m engine.showfiles init` makes the folder; `schemas/palette.schema.json`
  is generated with the rest.
- **A library palette is a source, not a link.** Timelines and template sets
  keep their own copies by name, as they always did, so each file still
  describes its whole show and the engine compiles exactly as before.
  `/api/palettes` lists each library palette with its copies, and whether each
  copy still has the library's colours. It also lists the palettes that live
  only inside timelines and sets.
- **Studio's Palettes page** edits the library: make, rename, recolour,
  duplicate, download, delete. After a change it offers **Update N copies to
  these colours** (`palette_sync`), which writes each file quoting the rev it
  was read at, so a file changed meanwhile is left alone and named. A palette
  found inside a file can be **added to the library** in one click. Deleting a
  library palette leaves its copies where they are.
- **A library palette's name is its own.** Copies are found by name, so the
  engine refuses a second library palette of the same name, and Duplicate
  picks a free one ("Hot copy 2").
- **From the library**: the timeline editor's and the template set editor's
  palette panels can copy a library palette in under its name.

### Added — template sets and show settings in Studio

- **Template sets have an editor** (`#studio/templates`). Before this, only MCP
  or a text editor could change one. It has a tab per set and a row per
  phrase family (Intro to Outro, then Anything else), each picking a routine,
  variation, parameters and palette. You can add exact labels (Up 2) that
  start from their family's pick, a bar cycle for tracks with no phrases, the
  set's own palettes, and the fade between phrases. Undo, the engine's check
  and Save with the rev work as in the timeline editor, and a recovery copy
  is kept in this browser.
- **Try it on a track**: the panel beside shows what the working copy would
  draft on any phrased track, phrase by phrase, and drafts that track's
  timeline from the saved set.
- **Make it the show's set**, duplicate, rename (show.json follows), download
  and delete. The show's set cannot be deleted. Studio says what a set does:
  live, the playing set (F22b) lights tracks with no timeline and shows through
  a timeline's gaps; the show's set is the one the engine starts on, and the
  one new timelines draft from.
- **Show settings** (`#studio/show`) edit show.json: the show's template set,
  what happens when the decks pause (policy, idle routine, silence, fade),
  and how Follow starts and how fast it believes a track change. Latency is
  shown, not edited, because it belongs to the phone's slider. A save quotes
  the file's rev, so a latency change made meanwhile is never overwritten.
- New engine commands: `template_draft` (the format, plus what the set asks of
  the routines), `template_save`, `template_rename`, `template_delete`,
  `show_save`. `/api/show` carries `show_rev`; `/api/templates` lines say which
  set is the show's, with their revs.
- A drafted clip now gets the set's fade between phrases (`transition.fade_beats`)
  as its fade in.
- Fixed: a save of show.json answered with nothing. The reply named the file
  by a subfolder that show.json does not have.

### Added — automation for every parameter, and waves on any lane

From PR #14. Both additions to the show-folder format are additive: old
files load unchanged.

- **A lane for each of a routine's parameters.** `param.<name>` lanes
  (`$radius`, `$color`, ...) can be added in the routine editor and on a
  track's timeline, beside master, size, spread, centre and rate. A
  timeline's **+ automation** lists one lane per name, with the routines it
  reaches. In a routine, the lane is read in the routine's own beats, so a
  loop lands on the same value every pass.
  - Precedence, highest first: the timeline's lane, the routine's own lane,
    the use's `params`, the variation, the default. A variation or a use that
    sets a parameter a lane already drives gets a warning, since the lane
    makes it dead.
  - Points are held to the parameter's own declaration: range, rate 0-8, or
    colour. That is an error in a routine. On a timeline it is a warning,
    because the declaration lives in another file, and a timeline lane is
    checked against every placed routine that declares the name.
  - A colour parameter's lane is drawn as a band of colour, not a curve.
  - A look parameter cannot be automated: the look is read once, when the
    routine is bound to the rig, so a lane would change nothing.
- **Block-argument lanes**: `arg.<item>.<argument>` (`arg.orbit.radius`)
  automates one block item's argument in a routine without declaring a
  parameter. Only number, integer and colour arguments can be automated, and
  the block's own declaration sets the range. An argument a `$param` already
  feeds is refused, since the param's lane is how that value moves. A timeline
  has no blocks, so there `arg.` is an error that points at `param.<name>`.
- **A wave on any automation row**: `"wave": {"shape", "bars", "depth",
  "phase"?, "seed"?, "toward"?}`, with shapes sine, triangle, ramp, saw,
  square and hold. It is added on top of the lane's points (value = points +
  depth × shape), so a lane gains a wave without its resting values moving.
  The swing is checked exactly against the lane's range and never clamped, so
  a rate lane's integral stays closed-form and a loop or hot cue still lands
  on the authored frame. On a colour lane, `toward` is the colour it swings
  to. In Studio, **∿** on a lane's head adds a wave sized to stay in range.
  MCP's `edit_timeline` gains `set_wave`.
- **`engine/waves.py`** holds the shapes. It is stdlib-only and shared by
  routine automation and the console's modulators, so a console sine and a
  routine sine are one sine. The UI's copy is held to it by a generated
  fixture.

### Added — Studio's routine library: folders, where used, rename, delete

- **Routines are cards**, each with a strip per row showing what it drives
  (movement, colour, level) and with which blocks, its roles, open parameters
  and variations, and where it is used. Filter by folder, "this rig only" or
  "unused", search by name, block, role or parameter, sort by name, use or
  length.
- **Where it's used**: the details panel lists every timeline that places the
  routine (with clip counts and variations), every template set that picks it
  (and for which phrases), and show.json's idle routine. `/api/routines` lines
  now carry `used_by`, `lanes`, `folder` and `rev`.
- **Duplicate, rename, move to a folder, download, delete**, from the card's
  menu or the panel. **Rename** is a new engine command, `routine_rename`: it
  writes the routine under its new id first, moves every reference (timelines,
  template sets, show.json) next, and removes the old file last -- so stopped
  anywhere, nothing names a routine that is gone. **Delete**
  (`routine_delete`) refuses while anything still uses the routine, and says
  where. Both quote the rev the routine was read at, and both judge what uses
  it from the folder as it is on disk at that moment, not as last loaded: a
  timeline saved a second ago (by MCP, another machine) is never missed.
- **Folders** are an optional `folder` on a routine: a name, set from the
  library or the routine editor's settings. It changes nothing about how the
  routine plays.

### Changed — the designer is now Studio, with a library to start from

- **Studio** is the new name for the designer, because "design" was already the
  console's own Design mode. It opens at `#studio`; old `#designer` links still
  work and are rewritten. The ways in are new: a **Studio** button in the
  console's header (Design mode only, so a phone never sees it), a Track-card
  link that says what it will do for the playing track (open its timeline, make
  one, or find it in rekordbox and add it), and **Open Studio** in the
  launcher. Every link opens Studio in a tab of its own, carrying the token.
- **A library, not a list.** Studio opens on every track in the show: what lights
  it on the night (its own timeline; else the template set that is playing,
  F22b; else, with no set on, the operator's show), rekordbox's
  phrases drawn in its row, BPM and length, and what needs attention -- a grid
  that changed since the timeline was drawn, no CDJ signature, unsaved work in
  this browser, the track playing now. Filter, search and sort; the selected
  track's details (waveform, checks, whether the engine can find its audio)
  sit beside it, with **Open timeline** / **Make a timeline** and **Draft
  from** a set.
- **rekordbox in the sidebar.** The playlist tree is the sidebar's second half; a
  playlist opens in the main column with which tracks are in the show already,
  and a panel saying how much of it the show covers. **Add to the show** preps
  the ticked tracks and starts each one in the same step: a timeline drafted
  from a template set, an empty timeline, or just the track. Only tracks the
  prep created are started; one ticked again is only re-prepped. The draft is the
  editor's own and is saved like any other timeline, so the engine checks it.
- **Make timelines for ticked** does the same for tracks already in the show.
- **Side panels fold away**: the library's sidebar and details, and the timeline
  and routine editors' right-hand panel, each with a button at the edge of the
  top bar. Remembered per browser.
- **Drafting knows the bar cycle**: a track with a grid but no phrases is
  drafted from the set's bar cycle, the way the engine plays it live, instead
  of refusing.
- `/api/tracks` lines carry `phrase_items`, a `timeline` summary (rows, items,
  the grid rev it was drawn on), `edited` and `audio_here`.

### Added — tracks straight from rekordbox

- **The designer browses the DJ's rekordbox collection.** The track list has a
  **From rekordbox** panel: the playlist tree, a playlist's or folder's tracks,
  a search over the whole collection. Tick tracks and **Add to the show** preps
  them into the show folder; a track already there says so and opens, and one
  rekordbox never analysed cannot be ticked. **Reload** reads rekordbox again.
- **`prep.py db`** reads rekordbox 6/7's own database, `master.db`, so there is
  no File → Export Collection step: `--playlist` (by path or name), `--search`,
  `--id`, `--all`, `--list` to see the choice first. `prep.py catalogue` is the
  collection as JSON. The database is SQLCipher-encrypted, so this route needs
  `pip install sqlcipher3` and the key in `$RB_CIPHER_KEY` or `rekordbox_key`
  in `klights.local.json`. Neither ever reaches the engine, which runs the
  bridge as a child process (`engine/collection.py`).
- **Every prepped track carries beat-link-trigger's signature**, computed from
  the analysis at prep, on the XML route too. CDJs playing a USB stick send the
  stick's own track ids, which mean nothing here; the signature survives an
  export, so a track now matches exactly on its first play from any stick
  exported from this collection, not only after being linked at a gig. With
  this database's id (CDJs loading from rekordbox over the network) and the
  title, artist and album (rkbx_link), one prepped track is the same song to
  every deck. A track with no artist carries two signatures, since beat-link
  hashes it as `[no artist]` from a stick's database but a player's metadata
  server may send an empty name.
- **`bridges/rekordbox/blt_check/check.py`** checks all of this with beat-link
  8's own code (a dev tool; Java 11+): `usb E:/` says which prepped track every
  track on a stick will match and, where only the name matches, which input
  differs; `collection` compares prep with beat-link on every analysed track
  (6,331 of 6,331 identical on a rekordbox 7.2.14 collection); `golden` records
  beat-link's output as the test suite's fixture.
- **Cues on the db route come from the database**, because a collection's
  analysis files carry none. The same file imported into rekordbox twice is
  one track with both ids and both rows' cues.
- **`GET /api/rekordbox`** (token) and **`rekordbox_prep {ids}`** (configure);
  `/api/tracks` lines now say which rekordbox rows each track is and how many
  CDJ signatures it has.

### Fixed — F22/F23 review

From a review of F22 and F23 before merging (PR #13); each one has a test
that fails without it.

- **The lights never pay for the rest.** An exception anywhere in the
  timeline, template, pad or output code used to end the output thread. Now
  that frame shows the operator's own show, the error is counted with the
  show errors, and the runner carries on. The other outputs (OSC, MIDI,
  timecode, visuals) are fenced off on their own: if they fail, they skip that
  frame and the lights still get the show. The failure is said once per
  message, and again at most every ten seconds, even if it fails only every
  other frame.
- **OSC numbers past a 32-bit float** (a curve times `1e40`, a huge integer)
  are sent as the largest float there is, rather than raising out of the frame.
- **Template set switches.**
  - A switch waiting for its downbeat is called off, and said, if its set
    leaves the folder. Before, the downbeat raised a KeyError.
  - A set whose build fails calls its switch off, rather than showing "next
    downbeat" for ever.
  - A rig change rebuilds a waiting set for the new rig, and a build for the
    old rig is dropped when it lands. The same goes for the active set.
- **The beat.**
  - Going from the clock's beat to the track's grid counts as a jump, so cues
    and hits never fire for the beats "crossed" between the two counts. One
    example: rkbx_link sending the title first and `master/time` later.
  - Re-arming Follow on a track that played on while disarmed is a jump too.
    Before, every hit, MIDI note and OSC cue from the disarmed stretch fired
    in one frame. The same goes for any return from frames that ran on no
    beat.
  - Under the `continue` pause policy, a new track no longer runs on from
    where the previous track last played.
- **Pre-matching:** a master switch to a deck whose show is still being built
  waits for that build, rather than queueing the same compile again. A reload
  that drops the build hands the wait back.
- **Routine pads.** A pad whose routine cannot be built (it left the folder,
  say) used to wait for its build for ever. Now it lands its looks on the
  downbeat and says why. Saving a routine pad again without `routine` keeps
  its routine, and `routine: null` clears it. The console sends null only when
  "none" is chosen, and typing a preset's name shows the routine its pad has.
- **`#visuals`.**
  - The page keeps the screen awake, as the console does.
  - A wash's `pulse` is a full-screen flash, so it now keeps to the same three
    flashes a second as the strobe, pulsing every two or four beats at fast
    tempos.
  - Only one strobe item flashes at a time: two at different rates could have
    added up to more than three flashes a second.
- **klights.local.json `outputs`** is an override, so it is checked for shape
  only. An OSC host there with show.json's port works. A bad field turns off
  only its own output; before, the whole override was thrown away.
- **beat-link-trigger expressions:** for a moment after a new track loads,
  BLT's latest phrase analysis can still be the previous track's. It was
  cached under the new track. Now the analysis held when the track changes is
  never used for the new track, nor is any analysis while the deck's metadata
  still names another track, and the phrases are worked out again when the
  analysis changes. These expressions have not yet been run against hardware.
- **MIDI sidecar:** if the driver refuses a message (a port unplugged, say),
  only that message is lost, and the sidecar says so once per reason. Before,
  the sidecar exited with every note still sounding. On the way out, every
  note-off and the port close are each tried, whatever happened to the one
  before.

### Added — F23d: built-in visuals

- **`#visuals`** (decided with the user: generative scenes and a video
  player): open it full screen on the projector laptop. Scenes are wash, bars,
  tunnel, particles and strobe, in the show's palette and on its beat, and
  video from the show folder's new `media/` folder (looped or not, at its own
  speed or stretched to the DJ's tempo). It runs on between snapshots at the
  show's tempo and eases into each one, so it stays smooth. Its own chunk: a
  phone never downloads it.
- **What plays:** a Visuals lane in the designer (`+ lane` → Visuals: a scene
  and its parameters per cue, a video picked from media/), the same lane in a
  routine, or a template set's `visuals {scene, params}` on each phrase pick
  -- so a guest's tracks get scenes too. The example folder has both.
- **Safety:** the strobe scene obeys the strobe policy -- off when strobe is
  off, no brighter than its ceiling, stopped after `max_seconds` -- and never
  flashes more than three times a second.
- `GET /api/media` lists the videos; `GET /api/media/<file>` serves one with
  Range, with the token only, video types only, never outside media/. A video
  a show names but the folder lacks is a warning when the folder loads.
- The snapshot gains `visuals`. Timeline, routine and template-set schemas
  regenerated.
- The MIDI stage's commit message (labelled F21c) counted 164 UI tests; it was 163.

### Added — F23c: MIDI, through a sidecar

- **MIDI lanes** (decided with the user: through an optional sidecar). A cue
  is a note held for its length, a CC value (and an optional value left at
  its end), or a program change, on channel 1-16; a **MIDI curve** drives one
  CC from a drawn 0-1 curve. Routines can carry them too. In the designer:
  `+ lane` → MIDI cues / MIDI curve.
- `bridges/midi/midi_out.py` owns the MIDI port, with its own pinned
  `mido` and `python-rtmidi`; the engine sends it one JSON datagram a frame
  over local UDP and stays stdlib-only. `--list`, `--midi NAME`, `--virtual
  NAME`, and `--fake` (prints; needs no MIDI library). It validates every
  datagram whole, listens on this machine only by default, and stops every
  note it started on the way out. The engine stops its own notes on close.
- `outputs.midi {}` in show.json or `klights.local.json`; the Track card
  shows it.

### Added — F23b: Art-Net timecode

- **ArtTimeCode carries the matched track's position** (decided with the
  user), so a VJ app with its own timeline per track follows the DJ: it jumps
  with loops and hot cues, and stops while the deck is paused, while Follow is
  disarmed, or when nothing matched is playing. The designer's preview sends
  its own position. Sent when its frame changes.
- `outputs.timecode {host, port, fps}` in show.json or `klights.local.json`:
  `{}` sends to everyone (255.255.255.255:6454) at 30 fps; 24, 25 and 29.97
  drop-frame too. The Track card shows the target and the time last sent.
- `shared/tools/artnet_listener.py --timecode` prints what arrives.

### Added — F23a: OSC out, for a VJ app

- **OSC lanes** (decided with the user: generic OSC, mapped by you). In the
  designer, `+ lane` → **OSC cues** adds a lane of cues; each cue sends an
  `on` message when it starts, an optional `off` when it ends, and an
  optional `while` as it plays (only when it changes, at most 30 a second).
  **OSC curve** sends a drawn curve's value to one address. Arguments are
  numbers and text, or `$beat`, `$bar`, `$phase`, `$progress` and `$value`.
- **Routines can carry OSC lanes too**, so a template set cues the VJ app on
  any track; each pass of a looping routine fires again. A track's timeline
  that has its own OSC lanes owns OSC for that track.
- Cues follow the deck like the lights: a loop or hot cue into a cue turns it
  on, out of one turns it off; Follow gates it, and disarming turns
  everything off.
- Where: `outputs.osc {host, port}` in show.json, overridden per machine by
  `klights.local.json`'s `outputs`. An IPv4 address, never a name. Printed at
  startup; the phone's Track card shows the target, how many cues are on, and
  any failed sends.
- The example show folder's VJ row is now two OSC lanes (Resolume-style clip
  triggers and an opacity curve). Timeline and routine schemas gain the
  external row's fields; show.json gains `outputs`.
- New suite `test_outputs` (29 suites).

### Added — F22e: per-deck pre-matching

- **A track's show is built before the DJ fades it in.** The beat-link-trigger
  expressions now say what every deck has loaded (`/klights/v1/deck`, the same
  shape as `track`). The engine matches it at once and builds its timeline on
  the worker, so when that deck becomes the tempo master its timeline drives
  from the very first frame instead of showing the layer below while it
  compiles. At most eight are kept; a rig reload or folder change rebuilds them.
- The Track card lists the other decks: what is loaded, which show it is, and
  whether that show is ready.
- These fields arrive as `loaded_*` and never move the transport, which follows
  the master alone; they are checked by the same rules as the master's.

### Fixed — F22e

- The beat-link-trigger expressions could send a new master track's identity
  with the previous track's metadata, if the deck reported the new track before
  its metadata arrived -- and then never send it again. Identity is now sent
  only once the metadata's rekordbox id is the one the deck reports.

### Added — F22d: routines on preset pads

- **A preset pad can play a routine** over its looks: choose one (and a
  variation) when saving the preset on the Show tab. Pressed, the pad waits for
  the next downbeat and then lands whole -- its looks and the routine from its
  first beat (decided with the user). The pad shows "next downbeat" until then
  and "playing" after. With the timeline or a template driving, it grabs every
  lane like any preset; picking a look, another preset or a cue puts it away.
- Needs a show folder (routines live there). presets.json gains `routine`
  (schema regenerated).

### Added — F22c: live phrases from CDJs

- **Guest tracks on CDJs get phrase templates** (decided with the user): the
  beat-link-trigger expressions read the song structure from the rekordbox
  analysis on the DJ's own USB and send `/klights/v1/phrase` (deck, label,
  beats into, beats left) when the phrase changes and every bar. The labels are
  the prep tool's own table, so a guest's "Up 1" reads exactly as a prepped
  one. Not yet run on hardware; golden bytes pin the encoding, and the fake
  bridge's `--blt` shape now sends the same message.
- The clock places a phrase's start exactly from "beats into"
  (`clock.phrase_start`), where a label change alone is only a bar line -- for
  rkbx_link too, which announces where a phrase will end.

### Added — F22b: templates on stage

- **The fallback chain is whole**: timeline, then template, then the operator's
  or auto mode's show, while a DJ track plays with Follow armed (decided with
  the user). A matched track with no timeline plays its own phrases' template;
  a timeline's fill gaps show the template underneath; a guest's track the
  folder does not know plays the deck's live phrase (rkbx_link's label, from
  the bar it changed on) or, with no phrase at all, the set's bar cycle on the
  clock.
- **Switching set** from the phone's Track card (`template_set`, operate tier):
  it takes over on the next downbeat, crossfading over the new set's transition
  (decided with the user). "Off" turns templates off. The card shows which
  phrase chose which routine, and every lane the template drives says so.
- A folder edit of the active set waits for the next track, like any folder
  change; the pause policies (freeze, continue, idle) apply to templates too.

### Added — F22a: the template runtime (milestone 2)

- **`engine/templates.py`**: what the lights do on a track nobody drew a
  timeline for. A template set's phrase map picks a routine for each of
  rekordbox's labels -- the exact label, then its family ("Verse 2" ->
  "Verse"), then `*` -- and its `bars.cycle` covers tracks with no phrases,
  one pick every `bars.every` bars. Each distinct pick compiles once into a
  program of its own, running on its own beat from where its phrase began, so
  a routine's phrasing lines up with the music.
- A pick that comes round again carries on (Verse 1 into Verse 2 does not
  restart the movement); a new pick crossfades over the set's
  `transition.fade_beats`; a jump cuts. The runner is one stable Show, so a
  timeline can sit on top of it and show it through its fill gaps.
- Nothing plays it yet: F22b puts it on stage.

### Added — run a drift check, and move a fixture, from the console

- **Drift check has a button.** Jog every head onto the mirror ball and press
  **Check all heads**. The engine compares each head's live jog position with
  its stored calibration. The card always showed results, but nothing in the
  console could start a check: the only way was the CLI, and its answer never
  reached the console. The button stays off, and names the heads still to aim,
  until every head is jogging. The engine refuses the same way, as capture does:
  a head nobody aimed has no reading.
- **`drift` with no `readings`** uses the jog positions. With `readings`, it now
  refuses a list that is not one per head, instead of quietly checking only the
  heads the list reached.
- **A drift result is dropped when a patch is applied**, since it was measured
  against the rig that was replaced.
- **Fixture positions are editable on the Patch card** (X, height, Z in mm).
  The card already said so, and the engine had `patch_position`, but there was
  no field. Sent once, when focus leaves all three.
- Notices that told operators to run `python -m engine.calibrate drift` now
  point at the Drift check.

### Fixed — banners with a bold name in them

- The Capture card's "Jog **head** onto the target first" warning laid out as
  three columns on a phone. Banners are flex rows, so text either side of the
  bold name became separate items. Result chips no longer wrap mid-word.

### Added — guides and help in the app

- **A guide to every tab**, behind a **?** in the console's header: a short
  walkthrough of what the tab is for, then the things worth knowing. Start,
  Show, Color, Move, Bright, Setup, and a pointer to the designer. A guide
  opens in place of the tab, under the same header, so Master and Blackout stay
  one tap away while someone reads; it is in the main bundle, so it works while
  the engine is down. `#guide/<tab>` links straight to one.
- **A first-run tour**, offered once per device by a card at the top of the
  console. Never a dialog: the device it most often appears on is a phone
  handed to someone mid-set.
- **Tap-to-open help on the cards whose labels do not explain them**: the cue
  list, On now (Release hold), Presets, Track (Follow, Grab, Latency), Tempo
  (Downbeat, nudge), Auto, the quick palette's long-press, the safety taper's
  soft edge and smoothing, the crowd zone's axes, Capture, and Drift check.
  A tap rather than a tooltip, because a phone has no hover.
- **The designer has a Guide** (or press **?**): building a track's show and
  building a routine, in a column beside the lanes so nothing it tells you to
  press is covered. A first visit offers it once. At the playhead, Draft from
  template, Record and Palettes have their own **?**.

### Fixed — links between console tabs

- **The On now rows did nothing when tapped.** They are links to the tab that
  owns each slot, and the console only read the address when it loaded, so
  following one changed the address and nothing else. It now follows the
  address as it changes.

### Fixed — preflight on a machine without QLC+

- **`scripts/preflight.py` said NOT READY on every machine without QLC+**: any
  container, CI runner or fresh laptop. despacio's venue checks hard-failed
  when the fixture def was not installed in QLC+'s user dir, and nothing at the
  venue loads that copy since the show moved to the engine. That check is gone.
- **The rig step now loads the rig the way a fresh clone will**, resolving
  profiles from `shared/fixtures/` only. The engine also searches
  `~/QLC+/Fixtures` and the gitignored `qlcplus/` tree, so a `.qxf` that lived
  only there passed preflight on the machine that had it and failed on every
  other one. That is how the pinspot went missing until 2026-08-06. The failure
  names the file it was really loading from and the
  `python -m engine.patch import` command that fixes it.

### Added — the launcher

- **`kLights.pyw`, one window for show setup** (`python -m launcher`). Pick the
  event, start and stop the engine, open the console or straight to its Setup
  tab, copy the phone link, launch, close or build the previz, and read the
  engine's output. Stdlib Tk, like everything else. The engine runs as its own
  process, so closing the launcher never stops a show, and reopening it finds
  the engine again. It refuses to start an engine on a port that is already
  answering, says when an event is locked by another engine, and warns when the
  engine's Art-Net would not reach the previz on this machine.
- **`--artnet` takes a list**: `--artnet 10.0.0.50,127.0.0.1` sends every frame
  to the rig's node and to a previz on the same laptop, and `host:port` gives a
  second local listener its own port (`127.0.0.1,127.0.0.1:6455`). Broadcast
  was the only way to reach two listeners before, and it reaches everything else
  on the network too.
- **`engine.server --stop-file PATH`** stops the engine cleanly when the file
  appears: how the launcher stops it, since a program with no console cannot be
  sent Ctrl-C.

### Fixed — Art-Net universes 16 and up

- **Art-Net universes 16 and up went out as the wrong universe.** The packet's
  SubUni byte dropped the SubNet, so universe 16 was sent as universe 0. Every
  receiver here already decoded it correctly; only the engine's sender and
  `shared/tools/artnet_sender.py` were wrong. Universes 0-15, which is every
  rig so far, are byte for byte unchanged.

### Added — F20, the standalone previz

- **`KLightsPreviz.exe`, a packaged previz that needs no editor and no
  Python.** It asks the running engine for the room and the rig, listens for
  Art-Net like the rig does, and draws the show: beams, the mirror ball's spray,
  haze, and a body for every fixture. Build it with `python previz/build.py`;
  run it beside `python -m engine.server`. See [`previz/README.md`](previz/README.md).
- **The engine serves the previz scene**: `GET /api/previz/scene` (with ETag,
  so the app's once-a-second poll is a 304 until something changes) and
  `GET /api/previz/model/<sha256>.glb`, which serves only files the current
  scene names. `python -m engine.scene <event>` prints the same thing.
- **Models.** Venue walls, set pieces, a mirror-ball model and fixture bodies
  are `.glb` files named in config: the venue's new `previz` block,
  `shared/fixtures/bodies.json`, and a fixture's `body` in `rig.json`. Moving
  heads are articulated so the head always points along the beam the show
  decodes. Conventions and budgets in [`docs/models.md`](docs/models.md).
- **The engine inspects every model** while it builds the scene: externally
  referenced files, unsupported extensions, triangle and texture budgets,
  centimetre exports, and a body the app could not articulate. Each problem is a
  warning on the app's overlay and in `previz doctor`, never an error that stops
  the show.
- **A fixed fixture can be aimed somewhere other than the mirror ball**:
  `aim: {x, y, z}` on its `rig.json` entry. The default is still the ball.
- **Hardware ray-traced Lumen.** A model loaded at runtime has no distance
  field and no Lumen cards, so under software Lumen it is invisible to global
  illumination. The project now targets SM6 with ray tracing and hit lighting.
  A GPU without ray tracing falls back to software by itself.
- **Golden parity vectors**, `engine/tests/data/previz_parity.json`, hold the
  app's C++ decode, servo and colour to the Python. They are checked by
  `python previz/build.py test`, and their staleness is checked in CI.

### Changed — F20

- **Previz optics moved into the venue file** (`previz.optics`), and
  `previz/optics.json` is gone. Despacio's numbers moved with them, unchanged.
- **The Unreal project is `KLightsPreviz.uproject`** (was `CosmosPrevis`), and
  has its first C++ module.
- **`previz.scene.camera_views` and the editor builder confused width and
  depth** for a room that is not square. The new scene gives the room as
  `size = [X, Y, Z]` in Unreal's own axes, and its views are tested in non-square
  rooms both ways round. (The editor path, left as it was, still has it.)
- The editor-Python previz (`go.py`, `klights_live.py`) is **unchanged and still
  works**. It now deletes only its own materials on a rebuild, so it can no
  longer take the app's away.

### Fixed — F19 review, before merging

A review of the whole F19 branch before it merged. Each fix has a test.

- **One UDP datagram could end DJ sync for the night.** A `/klights/v1/pos`
  with a NaN or infinite deck number raised inside the decoder, on the
  listener's thread, and the thread died. Non-finite numbers are refused, and
  the listener now survives any decoder failure, counting it as a reject.
- **A typo could hang the worker.** Clip `at`, `len` and `fade` had no upper
  bound; a long clip under two rate curves asked the compiler for a table of
  millions of entries. They are capped at 65536 beats (nine hours at 120 bpm),
  and the table at 8192 beats, past which the clip runs on at its settled rate.
  A compile that fails anyway is reported once ("could not be built") and the
  operator's show runs, instead of the track sitting on "compiling".
- **Audio in the designer**: a file named like `Night Drive [Extended Mix].mp3`
  was never found under `audio_roots` (the brackets were read as a pattern),
  and every Range request searched the whole music folder again. Names are
  matched literally, and where a track's audio is is remembered.
- **A designer that lost the rig kept talking to it.** When the engine ended a
  preview -- the socket dropped, a phone pressed Release, another designer
  forced its way on -- the page went on sending its transport ten times a
  second, and each one failed into every console's notices. The page now
  notices and says why; the engine ignores a transport that outlived its
  preview. A preview that is someone else's is labelled as theirs.
- The waveform drew nothing on a long track at the closest zoom (a browser's
  canvas width limit); ids with a trailing newline were accepted (and would have
  been file names); token checks are constant-time; a view-only phone is no
  longer offered a Release it cannot do.

### Changed — the designer, after review

- **Automation points** are selected by a click (it used to delete them),
  dragged to a new beat or value, and edited in the inspector: beat, value, and
  the curve that arrives at the point (linear, step, ease) -- which the format
  always had and the designer could not set.
- **Keys**: Ctrl/Cmd+Z, Shift+Ctrl/Cmd+Z or Ctrl+Y, Ctrl/Cmd+S, Space to play,
  Delete or Backspace to remove what is selected, Escape to let go of it.
- The playhead pages the lanes along while playing; the routine editor's back
  link returns to the track it was opened from.
- README: a "Timecoded shows" section, with the designer pictured.

### Added — F19l: the designer

- **`#designer`**, a desktop page the engine serves with the console, loaded
  only from that link -- a phone never downloads it (checked against the
  built bundle). It lists the show folder's tracks and routines.
- **A track's timeline as lanes** (layout B, picked from the mock-ups): bar
  ruler, rekordbox's phrases, the waveform, the timeline's rows in order, hits,
  automation, the VJ lane. Play and scrub with the track's audio from the
  engine, or a file opened in the browser (never uploaded). **Drive the rig**
  puts this page's transport on the real rig through the draft being edited.
  The right panel shows the rig from above and who drives each lane at the
  playhead.
- **Editing**: drag clips and hits (snapped to beat, bar or phrase), resize,
  fades, routine/variation/params with palette role or direct colour, looks,
  presets, palettes and palette clips, hit type/level/who/envelope, lanes
  added, reordered, removed, fill-gaps/owns-track per lane, automation points,
  routines placed from the shelf, **Draft from template**, **Record** pads
  (flash, strobe, blackout, next scene at the playhead) and a sortable **event
  list** with nudges. Undo/redo over the whole document. Each change goes to
  the engine as a draft; its answer (errors, notes, what will not work on this
  rig) gates Save, which quotes the rev it read. The working copy is kept in
  the browser until saved.
- **The routine editor** (`#designer/routine/<id>`, or New routine): its rows
  on the same lanes in loop mode at a tempo of your choosing, a role per lane,
  roles, open parameters and their defaults, variations, blocks by slot, and
  each block's arguments as a value or a `$param`. A rig-bound block (look,
  snapshot) marks the routine this-rig-only. New command `routine_draft`
  checks it against this rig, and each variation.
- The phone's Track card links the matched track to the designer.
- `engine/tests/dump_designer_fixtures.py` writes the grid vectors and block
  lists the designer is tested against; `test_api` fails when they are stale.

### Added — F19k: the show folder in conversation

- **MCP tools** for the show folder, through the new `engine/showtools.py`:
  `show_status`, `list_tracks` (with rekordbox's phrases and their beats),
  `get_track`, `list_routines`, `get_routine`/`put_routine`,
  `get_timeline`/`put_timeline`, `edit_timeline` (small ops: add, update or
  remove items and lanes, set points, set palettes -- creating the timeline if
  the track has none), `link_track`, `lint_show` (optionally against an event's
  rig: looks it lacks, roles with no fixtures), `explain_position` (a track at
  a beat, and what every fixture does), and the template-set trio.
- Dry runs unless asked; a write needs the rev it read and is refused if the
  file changed since. Unlike rig edits they are allowed while a show runs: the
  engine reloads, and a playing track keeps its version until its next play.

### Added — F19j: what the designer talks to

- **`GET /api/*`** (`engine/api.py`): the show, tracks, timelines, routines,
  template sets and waveforms as whole documents with their revs, over HTTP --
  never in the snapshot. `GET /api/audio/<id>` streams the track's audio with
  Range requests; it needs the token, and serves only files the track names (or
  the same file name under `audio_roots` in `klights.local.json`) with an audio
  extension.
- **Commands answered from the worker**: `timeline_draft` (validation plus a
  compile against this rig: errors, warnings, rig problems), `timeline_save`
  and `routine_save` (with `base_rev`; refused if the file changed since; the
  folder reloads, the playing track keeps its version until its next play).
  The reply channel gained deferred answers for these.
- **Preview**: `preview_arm` puts the designer's transport on the rig through
  the track's timeline -- its latest draft, else the saved one. Refused while a
  DJ plays unless forced; every console shows a "DESIGNER is driving the rig"
  banner with Release; it lets go when the designer's browser does.
  `preview_transport` moves it; a jump in it is a seek.

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
