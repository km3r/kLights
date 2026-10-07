# Roadmap

What was built, in what order, and why — reconstructed from the commit history.

This exists because **six places in the code and tests cite "the plan" as an
authority**, and the plan was not in the repo. A comment that says "see the
plan" and cannot be followed is worse than no comment: it implies a decision was
reasoned about somewhere findable.

Milestones are labelled `F<n>` throughout the commit log and the comments.

---

## Where it came from

The show ran on **QLC+** for two years: a workspace of stored DMX scenes, driven
from an APC40. It worked, and its limits were specific and recurring:

- a scene is a table of DMX values, so every variation of a move — faster,
  wider, centred lower — had to be a *separate stored scene*, and 206
  accumulated;
- a stepped chase slowed down does not become smoother, it becomes *steppier*;
- a global speed dial cannot fix a per-routine rate;
- nothing in the console knew where the room was, so nothing could dim a beam
  because of where it was pointing.

The decision to retire it is recorded in the commit history around F1–F2, and
every one of those four points is a thing the engine now does differently.

## Built

| # | What | Why it mattered |
|---|---|---|
| **F1** | Reorganise into `events/` `shared/` `docs/` | A file belongs to an event if it encodes *this rig or this night*; to `shared/` if it describes hardware, a room, or a thing done to any show. Events come and go. |
| **F2** | Timing spike | Settled "can Python hold a DMX clock" — yes, with two mandatory settings. Row C of its matrix is the disaster case and it is self-inflicted GIL contention, not the OS. |
| **F3** | Geometry and rig model | Where a head is, where it points, what DMX aims it there. 16-bit positions; `bearing_delta` is unwrapped servo rotation and may exceed ±180°. |
| **F4** | Layered state, safety taper, Art-Net, frame clock | The layer stack, and the taper that runs *after* it unconditionally. |
| — | Taper to a crowd level rather than to zero | Going dark costs every floor-sweep look; dimming keeps them. |
| **F5** | Fast re-aim, drift detection, snapshots | The heads get nudged overnight. Recalibrating used to be eight typed numbers and a rebuild. |
| **F6** | Musical timing | Beats, bars, phrases, and continuous motion. Nothing downstream is authored in milliseconds. |
| **F7** | Auto mode | Four independently toggleable axes, so "self-running" is a set of choices rather than one switch. |
| **F8** | The show server and web UI | Hand-rolled WebSocket, zero dependencies; one responsive surface from phone to desktop. |
| **F9** | Port the QLC+ library | 206 looks, round-tripping exactly against the workspace they came from. |
| **F10** | Unreal previz | Shares the show's own decoder; listens to the same Art-Net the rig sees, so it can never break a show. |
| **F11** | Make it shippable | A licence, a safety page, and a committed bundle that had been lying about its own contents. |
| **F12** | The guards | CI, one test command, a byte-reproducible bundle. |
| **F13** | Config as a contract | Validated on load, atomic writes, and a room that outlives a show. |
| **F14** | Authoring | One patch API behind three surfaces — CLI, MCP, in-app editor — plus access tiers, and a broadcast that cannot be stalled by one slow phone. |
| **F15** | Show control | Shape macros, the Night cue list, crossfade, flash, per-slot rate, preset banks, Perform/Design. |
| **F16** | Tempo from the DJ | A `sync` seam and a UDP port speaking JSON and OSC. **No analysis is written here** — beat position and rekordbox's phrase labels are a *read*. |
| **F17** | Previz, generic | A plan view that needs no GPU; an Unreal path that does not know the word "despacio"; optics as config. |
| **F18** | Docs and the soak | This file, [`engine.md`](engine.md), [`runbook.md`](runbook.md), and the 60-minute soak F2 asked for — **passed**: 148 802 frames, zero drops, and the predicted GC outlier did not appear. |
| — | Guides and help in the app | The runbook lived in `docs/` and the console's few tooltips needed a hover a phone does not have. Now each tab has a guide behind the header's **?**, a first visit offers a tour, the cards whose labels do not explain them carry a tap-to-open **?**, and the designer (now Studio) has a Guide column. Built 2026-10-03. |
| **F19** | Timecoded shows, milestone 1 | The lights follow **which track is playing and where in it**, not just the tempo. A track is prepped from what rekordbox already knows, a timeline is drawn against it bar by bar in a desktop designer, and it plays live from either DJ source once Follow is armed. Routines are written against roles, not fixtures, and a lights compiler binds them to the rig. Design record: [`design/timecoded-shows.md`](design/timecoded-shows.md). |
| **F20** | The standalone previz | `KLightsPreviz.exe` needs no editor and no Python: it gets the room from the engine (`/api/previz/scene`), DMX from Art-Net, and models, set pieces and articulated fixture bodies from `.glb` files named in config ([`models.md`](models.md)). Its C++ decode is held to the Python one by golden vectors, exactly. The editor-Python path still works beside it. A launcher window (`kLights.pyw`) starts the engine and the app, and links into the console's Setup tab. |
| **F21** | Parametric looks on the console, and modulation | Every block argument is declared once (`blocks.PARAMS`) and both the routine editor and the console render from it, so defaults and ranges cannot drift between them. Six new blocks (`figure8`, `spiral`, `scatter`, `breathe`, `hue_cycle`, `duo`), available to routines and console alike. On the console: a look built from one block, tuned live, saved in presets and cues, swung by modulators, stacked, and varied from a seed. The centre is bounded by what the rig's heads can actually reach, and a head at its rail says so. `audit_library.py` measures which ported looks a block covers: the nine fixed positions are superseded, exactly, by `offset` blocks under the same names; `Lazy Circle` is retired in favour of a tunable orbit. |
| **F22** | Templates for every other track (F19 milestone 2) | A template set maps rekordbox's phrase labels to routines. With it, a prepped track nobody drew, the gaps in a timeline and a guest's track the folder has never seen all get a show. Also: live phrases from CDJs, the set switcher on the phone, routines on preset pads, and per-deck pre-matching, so a master switch is on its timeline from the first frame. |
| **F23** | VJ outputs (F19 milestone 3) | The timeline core was output-generic from the start, so each output is an adapter: generic OSC, Art-Net timecode at the DJ's position in the track, MIDI through a sidecar, and the built-in `#visuals` page for a projector. Nothing outside the lights can cost the lights a frame. |
| — | Studio | The designer, renamed because "design" was already the console's mode. It grew from a timeline editor into where a show is made: a track library with rekordbox in its sidebar, a routine library (folders, where each is used, a rename that moves every reference), a template set editor, a palette library that keeps every copy in step, show settings, and **+ New** as one way in to making any of them. Built 2026-10-05. |
| — | Automation for every parameter, and waves | Every routine parameter and block argument can have its own lane, and any lane can carry a musical wave on top of its points, so a swell no longer needs a keyframe per bar. The swing is checked exactly against the lane's range and never clamped, so a rate lane's phase stays closed-form and a loop still lands on the authored frame. Built 2026-10-05. |
| — | A taller waveform, and lanes that follow the audio | The waveform lane drags taller and can show its low, mid and high bands. A timeline's number lane can follow one of them: rekordbox's own analysis of the track, pooled onto its beats, so it is as repeatable as a keyframe and its integral is exact. Prep now keeps the three-band waveform; older tracks fall back to an estimate from the colours. Routines' own lanes and colour lanes do not follow yet. Built 2026-10-06. |

## Decisions worth knowing

Things that look arbitrary and are not.

**The previz app is told the calibration, never derives it.** The engine ships
each moving head's *resolved* aim frame — mount facing, elevation offset, which
channel carries what — so the C++ decode is two linear maps and a sine, and the
traps that live in backing a calibration out of a hand-aimed reading stay in one
place, in Python. The parity file proves the two decodes agree to the bit, for
heads in every mount mode, not just despacio's.

**The engine is stdlib-only, permanently.** A show laptop at a venue with no
internet must not be one `pip` away from working. Anything needing a library
lives behind a process boundary in `bridges/`.

**Safety runs after the stack, not in it.** `apply_safety` is not a layer, so no
look, macro, rate or override can reorder itself in front of it. That is what
makes every other knob safe to expose.

**The UI never predicts.** State is server-authoritative. A tap that has not
come back from the engine has not happened — the cost is up to 100 ms of latency
on a button, and the benefit is that two phones can never disagree.

**Art-Net is the whole architecture.** The rig and the previz are both just
things listening. Nothing sits between the engine and the fixtures, which is why
previz can never break a show.

**40 fps is a wire limit.** A full DMX512 universe takes ~22.7 ms to clock out.
The software measured ~2000 Hz. Spend headroom on width, not rate.

**The taper is a comfort feature, not a protective device.** These are 60 W LED
beams; a beam in the eye is dazzling, not injurious. Saying otherwise buried the
two things that *are* a different category — see [`SAFETY.md`](SAFETY.md).

## Not built

Open, in rough priority order.

- **A strobe rate limit.** The strobe policy caps how far up the band and for
  how long, but not how fast: the fixture profiles give no Hz for their strobe
  band, so a limit in Hz would be invented. It needs each fixture's band
  measured. [`SAFETY.md`](SAFETY.md) calls it the highest-value safety item
  left.
- **Real decks, and a real VJ app.** F19, F22 and F23 are built and tested
  against the fake bridge and golden fixtures, never against hardware. Both DJ routes ship, documented in
  [`bridges/prolink/`](../bridges/prolink/README.md): beat-link-trigger
  expressions for CDJs and an rkbx_link config for rekordbox and a DDJ. Each
  talks to the engine's `--sync-port` directly, so `bridge.py --live` is not
  built and may never need to be. What a ten-minute capture on each rig must
  show is listed in
  [`design/timecoded-shows.md`](design/timecoded-shows.md#things-to-verify-on-hardware).
- **Phrase-driven cues.** An `Up` arms the next cue, the `Chorus` downbeat fires
  it, `Outro` releases to ambient. This is the thing the APC40 show was doing by
  hand. Phrase labels are now consumed: a template set (F22) picks a routine
  per phrase. The Night cue list still does not follow them. The labels are
  rekordbox's own: it says Up, Chorus and Down, never Build or Drop.
- **The three unmeasured taper inputs.** `beam_deg`, `ball_radius` and the crowd
  head band are estimates. `SAFETY.md` lists the measurement that retires each.
- **A second venue.** Everything is in place — shared rooms, derived previz
  cameras, per-venue optics — and none of it has met a room that is not
  despacio.
- **`auto.AudioEnergy`.** Probably never: phrase from the DJ is a better energy
  signal than anything a room mic would give.
- **What the audit left alone, on purpose.** 1 of 18 ported paths is a block
  (`Grand Sweep` is close at 3.4° and was kept: visible on a 60 W beam at the
  far wall). `Diagonal A/B` and `Heads Cross A/B` are each other doubled and
  turned, and all four are kept — on stage a cross and a diagonal read as
  different decisions.
- **A wave on a timeline with rate lanes in both places.** When a timeline
  and the routine under it both automate a rate, the warp is tabulated over
  the clip's length. A curve with a wave never settles, so a clip over 8192
  beats (about half an hour) would drift at its far end
  (`program.TABLE_MAX_BEATS`).
- **Prep from a stick alone.** Prep reads rekordbox's XML export or its own
  `master.db`, so it needs the rekordbox that made the stick. Reading a stick's
  `export.pdb` directly is not built
  ([`bridges/rekordbox/`](../bridges/rekordbox/README.md#not-built)). A guest's
  stick still gets templates from the deck's live phrase.

### Noted next — not yet ranked

Added 2026-10-03. Each one says where it starts from.

- **Designer UI/UX.** The F19l designer works end to end (lanes, the routine
  editor, a preview the engine evaluates). This is the polish pass, done once
  it has been used to build real tracks' shows. What it is today:
  [`design/timecoded-shows.md`](design/timecoded-shows.md), "The designer:
  layout B".
- **A DMX sidecar.** The engine sends only Art-Net. despacio's rig ran through
  a USB-DMX widget under QLC+. `engine/output` leaves room for USB-DMX as
  another driver, but serial/FTDI needs a library and the engine is
  stdlib-only. So it goes in `bridges/`: a process that listens to the engine's
  Art-Net and writes to the widget. The rig is then one more thing listening,
  like the previz, and nothing sits between the engine and the wire. The F2
  spike notes that a USB interface may block differently, so re-measure timing
  when this lands ([`FINDINGS.md`](../spike/timing/FINDINGS.md)).
- **rekordbox database integration.** Prep reads the XML export plus the ANLZ
  analysis files, so every new track needs File → Export Collection first.
  Reading the collection's `master.db` directly removes that step. It was left
  out because the database is SQLCipher-encrypted and its key handling changes
  between rekordbox versions
  ([`bridges/rekordbox/`](../bridges/rekordbox/README.md), "Not built"). A
  library that keeps up with those changes is fine in `bridges/`, since it never
  enters the engine. The related item is reading a stick's `export.pdb`, for a
  stick with no rekordbox machine to hand.
- **Calibration without a mirror ball.** The solver doesn't need the ball. It
  takes two or more captures at any world points, as long as they sit at
  different bearings. The ball-shaped parts are around it: the Setup tab's first
  capture is **Ball**, `calibration.json` stores each head as a `ball_dmx`
  reading, `drift` parks every head on the ball, and the venue file requires
  `ball` (the taper also treats it as an occluder). A room with no ball needs a
  measured reference point in its place, such as a taped floor mark or a truss
  leg, and the occluder made optional. `Venue.rest` already falls back from
  `rest_point` because "not every room has a ball". This is part of what **A
  second venue** above will hit.
- **Previz UI.** `KLightsPreviz.exe` is driven by keys and command-line
  arguments: WASD / Q E and the mouse, **1–4** for views, **H** for the overlay,
  and `-Engine=`, `-ArtNetPort=` and `-Snapshot=` at launch
  ([`previz/README.md`](../previz/README.md)). This item is an on-screen UI for
  those, so the app is usable without knowing them.

## Conventions

- **Comments say why, not what.** Where a guard exists because something broke,
  the comment names what broke.
- **Tests assert failure paths.** A refused command, a corrupt config, a crash
  mid-write. A test that only proves the happy path passes when the feature is
  deleted.
- **Commit messages are the design record.** They are long on purpose, and they
  are the first place to look for why something is the way it is.
