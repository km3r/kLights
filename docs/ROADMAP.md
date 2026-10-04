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
| **F20** | The standalone previz | `KLightsPreviz.exe` needs no editor and no Python: it gets the room from the engine (`/api/previz/scene`), DMX from Art-Net, and models, set pieces and articulated fixture bodies from `.glb` files named in config ([`models.md`](models.md)). Its C++ decode is held to the Python one by golden vectors, exactly. The editor-Python path still works beside it. A launcher window (`kLights.pyw`) starts the engine and the app, and links into the console's Setup tab. |
| **F21** | Parametric looks on the console, and modulation | Every block argument is declared once (`blocks.PARAMS`) and both the routine editor and the console render from it, so defaults and ranges cannot drift between them. Six new blocks (`figure8`, `spiral`, `scatter`, `breathe`, `hue_cycle`, `duo`), available to routines and console alike. On the console: a look built from one block, tuned live, saved in presets and cues, swung by modulators, stacked, and varied from a seed. The centre is bounded by what the rig's heads can actually reach, and a head at its rail says so. `audit_library.py` measures which ported looks a block covers: the nine fixed positions are superseded, exactly, by `offset` blocks under the same names; `Lazy Circle` is retired in favour of a tunable orbit. |

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

- **`--live` for the DJ bridge.** The seam, `--fake` and `--replay` are done and
  tested; nothing talks to real hardware yet. Two routes, both documented in
  [`bridges/prolink/`](../bridges/prolink/README.md): beat-link-trigger for
  CDJs, rkbx_link for rekordbox and a DDJ.
- **F19, timecoded shows — in progress.** Routines driven by which track is
  playing and where in it: phrase templates for any track, hand-built timelines
  for signature tracks, a desktop designer. Milestone 1 (one track end to end:
  prep, design, play live from either source) is built; it waits on hardware
  captures from real decks. Milestone 2 (the template runtime, live phrase mode)
  and 3 (VJ) are next. Design record:
  [`design/timecoded-shows.md`](design/timecoded-shows.md).
- **Phrase-driven cues.** An `Up` arms the next cue, the `Chorus` downbeat fires
  it, `Outro` releases to ambient. The data arrives; nothing consumes it yet.
  This is the thing the APC40 show was doing by hand, and F19's phrase templates
  are how it gets built. The labels are rekordbox's own: it says Up, Chorus and
  Down, never Build or Drop.
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
- **Waveforms in routine automation.** Modulation shapes drive the console
  live; a show folder's automation rows are still keyframes only. A `wave`
  row would need a closed-form integral for rate targets, and the designer
  to draw it.

## Conventions

- **Comments say why, not what.** Where a guard exists because something broke,
  the comment names what broke.
- **Tests assert failure paths.** A refused command, a corrupt config, a crash
  mid-write. A test that only proves the happy path passes when the feature is
  deleted.
- **Commit messages are the design record.** They are long on purpose, and they
  are the first place to look for why something is the way it is.
