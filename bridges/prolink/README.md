# Pro DJ Link → cosmos

Tempo, bar phase and phrase, off the CDJs, into the show clock.

**We write no analysis.** No beat tracking, no DSP, no audio in the chain. Beat
position and rekordbox's own phrase labels are both a *read*, not a derivation —
the players broadcast the beat grid, and the phrase structure is sitting in an
analysis file on the USB stick. Anything we inferred from a room mic would be
worse data, obtained harder, and wrong in ways nobody could debug at 1am.

## The shape

```
CDJs ──Pro DJ Link──▶  a bridge  ──UDP──▶  engine --sync-port  ──▶  MasterClock
                       (this dir)          (engine/sync.py)
```

The process boundary is the point. Reading the CDJ protocol properly needs
libraries the show laptop must never depend on — the engine is stdlib-only and
stays that way. The engine's entire side of this is a `sync` command and a UDP
port; it never learns what a CDJ is, and the bridge can be replaced with
something in another language without it noticing.

## Right now, with no hardware

```bash
python bridges/prolink/bridge.py --fake
```

Synthetic 128 BPM with a scripted phrase timeline — Intro, Verse, **Build**,
Chorus, Verse, Build, **Drop**, Outro — that cycles in about two minutes. Every
downstream branch happens: phrase becomes measured, the console's sync row
lights up, and anything driven by phrase gets exercised.

Start the engine with the port open first:

```bash
python -m engine.server --sync-port 9000
```

`--replay capture.jsonl` plays back a captured session: one JSON object per
line, each with a `dt` in seconds and whatever sync fields it carried. The
format is the dullest thing that works on purpose — a capture nobody can take
at a venue is a capture that never exists.

## The two routes to real players

Neither is built. The seam is, and it is the same seam either way, so choosing
later costs nothing.

### 1. beat-link-trigger as the bridge — recommended first

[beat-link-trigger](https://github.com/Deep-Symmetry/beat-link-trigger) is the
app on top of Deep Symmetry's `beat-link`, which is *the* reference
implementation of this protocol. It already has **phrase-triggered cues** as a
first-class feature, and it emits OSC.

**The engine speaks its OSC directly**, which is what makes this an evening's
work rather than a project: point a trigger at `--sync-port` and there is no
code of ours in the path at all. `engine/sync.py` reads `/…/bpm`,
`/…/beat`, `/…/phrase`, `/…/deck` and `/…/track`, matched on the last path
component so the namespace can be renamed freely.

Cost: a JVM in the show chain.

### 2. A Python sidecar

[python-prodj-link](https://github.com/flesniak/python-prodj-link) for the
virtual-CDJ and beat-packet layer, plus a Kaitai-generated parser for the ANLZ
tags — `PQTZ` is the beat grid, `PSSI` is the phrase structure. The `.ksy`
grammars (`rekordbox_pdb`, `rekordbox_anlz`) compile to Python, so the parser is
generated rather than hand-written.

One language, one process, no JVM. The cost is that we own the hard edges:
track identification over dbserver/NFS, and **PSSI is masked in newer rekordbox
exports** — beat-link handles that deobfuscation, and whether the Python side
does needs checking before committing to this route.

### The DDJ-1000

It is USB, not Pro DJ Link. rekordbox's **PRO DJ LINK Bridge** puts rekordbox's
playback state onto the same network protocol, and the phrase data is already
local to rekordbox — so the same bridge serves it. Check it is on your rekordbox
plan.

If it is not: **Ableton Link** (rekordbox 6/7) gives tempo and quantum phase but
**no phrase**. That is the degraded fallback, not the plan.

## What the engine expects

JSON over UDP, any subset of these, unknown keys ignored:

| field | meaning |
|---|---|
| `bpm` | source tempo, 40–250. Safe to send every packet. |
| `beat_in_bar` | beat within the bar. **This is what a per-beat source should send.** Corrects the grid by at most half a bar and never moves cumulative position. |
| `beat` | absolute musical position. A **jump** — correct for a re-sync, wrong for tracking. Sending it every beat makes every move judder. |
| `phrase_measured` | true when phrase is read, not counted from a tapped downbeat. |
| `phrase_label` | `Intro` / `Verse` / `Build` / `Chorus` / `Drop` / `Outro`. Empty string clears it. |
| `phrase_ends_in` | beats until the phrase ends. Stored as an absolute beat, so sending it once per bar is enough. |
| `source` | what to display as the clock owner. |
| `deck`, `track` | labels for the console. |

## Security

The port has **no authentication** — a datagram cannot be challenged, and there
is no handshake to carry a token. So:

- it is **off unless `--sync-port` is given**;
- it binds **loopback by default** (`--sync-bind` to change it), because the
  bridge normally runs on the show machine;
- it parses into a fixed set of clock fields and the engine builds the command
  itself. **This port cannot patch a fixture, write a calibration or blackout
  the rig**, whatever is sent to it. That is structural, not a promise, and
  `engine/tests/test_sync.py` asserts it.

## Prior art

| project | what it gives |
|---|---|
| [dysentery](https://github.com/Deep-Symmetry/dysentery) | the protocol documentation — announce, beat and status packet layouts |
| [beat-link](https://github.com/Deep-Symmetry/beat-link) | reference implementation: `VirtualCdj`, `BeatFinder`, `TimeFinder`, `AnalysisTagFinder` |
| [Crate Digger](https://github.com/Deep-Symmetry/crate-digger) | reads rekordbox `.PDB` and ANLZ `.DAT`/`.EXT` off the player |
| Kaitai `.ksy` specs | the same formats as grammars that compile to Python |
| [beat-link-trigger](https://github.com/Deep-Symmetry/beat-link-trigger) | the app on top; phrase-triggered cues already built in |
| [python-prodj-link](https://github.com/flesniak/python-prodj-link) | virtual CDJ, beat packets and a dbserver client, in Python |
