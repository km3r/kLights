# Timecoded shows (F19)

Light routines driven by **which track is playing and where in it**, authored in
a desktop designer and played back live from the DJ's own decks. This is the
design record: what was decided, with whom, and why. The staged build is at the
end; the commit log carries the detail of each stage.

**Status:** Milestone 1 (F19a–F19l) is built and tested end to end with the
fake bridge; it has not yet run against real decks (see "Things to verify on
hardware" below). Milestones 2 and 3 are designed, not built.

---

## The gap this closes

F16 gave the engine the DJ's *tempo, bar phase and phrase*. It still has no idea
*which track* is playing or *where in the track* it is:

- `clock.beat` counts from engine start and is scaled by `speed`, so it is not
  track time and cannot be made into it.
- The track title is a display label, deliberately kept off the clock
  (`server.py`, "a clock that carried track titles would be a clock with
  opinions").
- Phrase labels arrive and nothing consumes them.

So nothing can say "at bar 41 of *this* track, hit the drop". That is the
feature.

## Decisions

Made with the operator, one question at a time. Where a decision looks
arbitrary, the reason is next to it.

| topic | decision |
|---|---|
| Authoring model | **Phrase templates** run on any analysed track. Signature tracks get a hand-built **per-track timeline** that layers over the template *per lane*. |
| Timeline gaps | Per-lane option: **template fills gaps** (per time region), or **lane owns the track** (exclusive): in its gaps, before its first clip and after its last, **nothing drives that lane** -- not the lanes below, not the template (F19g, with the user). A blank palette lane shows the timeline's default palette. |
| Lane precedence | **The higher lane wins**, scene lanes included: a movement lane overrides a scene routine's movement only if it sits above the scene lane, and then only its movement (F19g, with the user). |
| Clip exit | A clip that ends into a gap **fades out over its own fade**, in its last beats; `fade: 0` cuts on the end beat. A clip followed directly by another crossfades on the next one's fade-in (F19g, with the user). |
| Partial clips | A clip drives only the fixtures it uses; **the rest fall through per fixture** to the lanes below, then the template. On a lane that owns the track they rest instead (F19h, with the user). |
| Rest state | Where nothing drives a slot: movers aim at the venue's **rest point** (`rest_point`, else the ball -- not every room has one), colour white, level **dark** (F19h, with the user). |
| Past the end | A routine that does not loop, on a clip longer than itself, **keeps running its end**: its last items carry on and its automation holds its final values (F19h, with the user). |
| Grabs | A look, preset or cue picked while the timeline drives **grabs** its lanes, and a grab lasts **until Release** -- across track changes too. Looks picked while it is not driving grab nothing (F19i, with the user). |
| Hand-overs | Every hand-over between the timeline and the operator's show is a **cut**: arming, disarming, matched/unmatched tracks, pause into idle and back (F19i, with the user). |
| Latency | The phone's latency slider applies at once and is **saved to the show folder's show.json** (F19i, with the user). |
| Fallback chain | timeline → phrase template → grid-based bar-count template → a setting: auto mode or operator-only. Guest DJs' tracks are unknown until they play, so the chain has to carry them. |
| Phrase vocabulary | rekordbox's labels **as-is**: Intro, Verse 1–6, Up 1–3, Chorus, Down, Bridge, Outro. Templates look up the exact label, then the label without its number, then `*`. rekordbox never says "Build" or "Drop". |
| Template sets | Several named sets, **switchable live** from the phone (a vibe, not a file). |
| Operator | Overrides win. The timeline keeps running underneath. Picking a look or preset **grabs** that lane until **Release**. |
| Blends | Follow the master deck; **hard switch** when master changes. |
| Pause / silence | A setting: idle routine after a grace period, freeze, or keep running. |
| Time base | **Beats on the track's rekordbox grid**, beat 0 = first downbeat. Survives pitch, matches how beat-link-trigger shows are keyed, and keeps the engine rule that nothing downstream is authored in milliseconds. |
| Track identity | A **prepped track library**. Runtime match, strongest key first: beat-link-trigger signature → a known rekordbox id, only with an agreeing title → a saved alias → title + artist + album → title + artist, the last three only where the durations could be one file. (Original bpm is not used: a variable-tempo track's changes along it.) No match or several → templates run and the console says *unmatched*; a manual link is saved as an alias and, like any change to the folder, **applies from the track's next play** (decided with the user in F19f). The prepped grid cross-checks the source's beat phase, so a re-gridded track warns instead of drifting. |
| Sources | Both from day one. CDJ-3000 / XDJ through **beat-link-trigger**, with Clojure expressions we ship. DDJ-1000 through **rkbx_link**. No fork of rkbx_link. |
| Routines | The one reusable unit, used on preset pads, in templates and as timeline clips. N bars of rows, open **parameters**, variations, written against **roles, not fixtures**. New parametric role-based blocks, *and* the existing looks, flagged **"this rig only"**. |
| Colour parameters | **Palette roles** (primary / secondary / accent) *and* direct colour, switchable wherever a routine is used. |
| Timeline items (M1) | All four: look/preset snapshots with a fade, automation lanes, one-shot hits, routine clips. |
| Designer | Waveform, phrase bands and grid; audio playback and scrub; a 2D plan preview; scrub and play drive the real rig and Unreal. **Undo/redo only** for history. |
| Show files | A **shared folder** (Dropbox, OneDrive, NAS) the engine points at, separate from the repo. Hot reload on change, but **never under a playing track**: the change applies at that track's next load. |
| Follow DJ | **Starts disarmed.** One tap arms it. Printed at startup. |
| Clock `speed` | **Left alone.** The timeline never uses `clock.speed`; it uses per-slot rate. The judder `speed` causes under a per-beat sync source is a known, documented issue, not fixed here. |
| MCP | Read and write routines and timelines in milestone 1, through the same validation as the designer. |
| Phone | Now playing and match state, which source drives each lane, Follow armed/safe, grab and release per lane, and (milestone 2) the template-set switcher. |
| Template scope | Templates run **only while a DJ track plays and Follow is armed**; otherwise auto mode or the operator's show, as before (milestone 2, with the user). |
| Set switch | A set switched mid-track takes over on the **next downbeat**, crossfading over the new set's `transition.fade_beats` (milestone 2, with the user). Not saved: show.json's `template_set` is the start-up default. |
| Pad routines | A preset pad holding a routine starts it on the **next downbeat** (milestone 2, with the user). |
| CDJ phrases | Guest tracks on CDJs get phrase templates: the beat-link-trigger expressions are **extended to send the USB's phrase analysis** (milestone 2, with the user; unverified on hardware until captures). |
| VJ | Later, and both: drive external apps (OSC, MIDI, Art-Net timecode) and built-in browser visuals. The timeline core is output-generic so this is an adapter, not a rewrite. |

## The designer: layout B

Three clickable mock-ups were built on one synthetic track and compared
([the canvas](https://claude.ai/artifact/Gy26gURBaH5bjZWSKbkreA)):

- **A**, waveform-first, in the style of SoundSwitch;
- **B**, arrangement lanes, in the style of a DAW;
- **C**, a timecode event list, in the style of a lighting console.

**B was chosen**, with three things borrowed from the others:

- **Draft from template** (from A): lay a template set's phrase → routine
  mapping onto the scene lane as an editable starting point.
- **Record pads** (from C): arm Record while the designer plays and tap
  Flash / Strobe / Blackout / Next scene; each tap lands on the lanes,
  quantised.
- **Event list view** (from C): the same timeline as a sortable list, for
  precise nudging.

What B is:

- lane headers carrying the per-lane **gap mode** toggle;
- snap to beat, bar or phrase;
- clips on scene, movement, palette and level lanes, and hit markers;
- a **Master** automation lane always shown, and a **+ automation** button that
  adds Size, Spread, Centre or per-slot Rate lanes only to tracks that use them
  (Size, Spread and Centre are the existing shape macros on the Move tab);
- a bottom panel with the selected clip's parameters and the **rows inside its
  routine**;
- a right-hand preview, evaluated by the engine, and a "who drives each lane
  right now" panel;
- a placeholder VJ lane, so the output-generic shape is visible from the start.

The preview has to come from the engine. The browser does not have the
calibration and cannot aim a beam.

## Architecture, in one screen

```
 DJ decks ─▶ bridge (rkbx_link | beat-link-trigger | designer | --fake)
               │ OSC/JSON, loopback by default
               ▼
 sync.clean ─▶ `sync` command ─▶ MasterClock        (tempo, phase: unchanged)
                              └▶ TrackTransport     (which track, where: new)
                                     │ sample(now), once per frame
 show folder ─▶ worker thread ─▶ match + compile ─▶ TrackPlayer
                                                     │ timeline > template > fallback,
                                                     │ operator grabs win
                                                     ▼
                     base → color → movement → fx → overrides → master → SAFETY
```

- **Nothing about tracks reaches the runner.** A `TrackPlayer` hands it one
  stable `Show`; lanes inside that show resolve precedence per fixture.
- **Safety still runs last, unconditionally.** A timeline beam aimed into the
  crowd is tapered like any other; a strobe hit is capped by the strobe policy.
- **Parse, match, compile and file writes run on a worker thread**, never on the
  output thread. The frame budget is 25 ms and in-process CPU work is what the
  timing spike showed breaks it.
- **The sync port can still only produce a `sync` command.** What changes is
  that its fields can now select pre-authored content, which is why Follow DJ
  starts disarmed and the port gains a source allowlist.

## Milestones

**Milestone 1: one track end-to-end.** Prep a track, design its timeline, play
it live from either source. Stages, each landable and testable with the fake
bridge and no hardware:

| stage | what |
|---|---|
| F19a | Seams and guards: WebSocket `sync` goes through `clean()`, a reply channel for commands, a worker thread |
| F19b | Track time (grid, phrases) and the show-file formats, schemas and an example show |
| F19c | Prep tool: rekordbox XML + analysis files (collection or USB stick) into the track library. Reading `export.pdb` or `master.db` directly is not built |
| F19d | Sync fields for position and identity, the transport, fake-bridge scripts, capture |
| F19e | beat-link-trigger expressions, rkbx_link config, golden OSC fixtures; hardware captures start |
| F19f | Track library, matcher, aliases, grid warnings, hot reload |
| F19g | The output-generic timeline core |
| F19h | Routines, parametric blocks, the lights compiler, palette roles, gap modes |
| F19i | Playback: the runner seam, pause policies, grab/release, Follow DJ, the phone card |
| F19j | The authoring API: reads, audio, draft/save/link/preview |
| F19k | MCP tools |
| F19l | The designer, layout B, and the routine editor |

**Milestone 2:** template runtime and live phrase mode for unmatched tracks, the
bar-count fallback, the template-set switcher, routines on preset pads.

**Milestone 3:** VJ outputs.

## Things to verify on hardware

Neither DJ path has been run against real decks yet. Before the transport is
called done, a ten-minute capture on each rig must show:

- whether rkbx_link's `/master/time` really goes silent while paused, which is
  the only pause signal on that path;
- whether rkbx_link re-sends track metadata or sends it only on change, and in
  what order during a master switch;
- rkbx_link's exact phrase strings in `string` format;
- that beat-link-trigger delivers the signature and rekordbox id;
- that it reads the song structure (PSSI) off a guest's USB, with the labels
  rekordbox shows, and that `/klights/v1/phrase`'s beats-into lands the
  template's routine on the phrase's first beat (milestone 2).
