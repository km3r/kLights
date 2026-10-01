# Pro DJ Link → kLights

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

Synthetic 128 BPM with a scripted phrase timeline in rekordbox's own
vocabulary — Intro, Verse 1, **Up 1**, **Chorus**, Down, Up 2, Chorus, Outro —
that cycles every three minutes. Every downstream branch happens: phrase becomes
measured, the console's sync row lights up, and anything driven by phrase gets
exercised.

Start the engine with the port open first:

```bash
python -m engine.server --sync-port 9000
```

`--replay capture.jsonl` plays back a captured session: one JSON object per
line, each with a `dt` in seconds and whatever sync fields it carried. The
format is the dullest thing that works on purpose — a capture nobody can take
at a venue is a capture that never exists.

`--fake --osc` sends the same feed shaped as **rkbx_link's** OSC instead of
JSON, which is how the rekordbox path's decoder — the deck filter, the nested
addresses, the 0–1 subdiv conversion — gets exercised without a DDJ, rekordbox
and a licensed copy of rkbx_link in one room.

### A deck, not just a beat (F19)

```bash
python bridges/prolink/bridge.py --fake --track --osc
python bridges/prolink/bridge.py --fake --track --blt \
    --script "play:64,loop:4x3,play:8,hotcue:160,pause:3s,play:32,switch,scratch"
```

`--track` plays the show-example's synthetic track as a deck would: its
position 30 times a second (`--hz`), its identity, and the beat and phrase as
before. `--osc` shapes it as rkbx_link does — identity a field at a time, and
**silence while paused**. `--blt` shapes it as the beat-link-trigger
expressions we ship do: `/klights/v1` messages with an explicit playing flag.

`--script` is what the deck does, step by step, then it plays on forever:

| step | |
|---|---|
| `play[:BEATS]` | play forwards |
| `loop:BxK` | play B beats and jump back to their start, K times |
| `hotcue:BEAT` | jump to a beat of the track |
| `pause:SECONDS` | stop |
| `switch` | the other deck becomes master, playing a track no show folder has — a guest DJ's |
| `scratch` | a second of back-and-forth on the platter |

### Capturing what a source really sends

```bash
python bridges/prolink/capture.py --listen 9001 --forward 127.0.0.1:9000 --out venue.jsonl
python bridges/prolink/bridge.py --replay venue.jsonl
```

Point rkbx_link or beat-link-trigger at `--listen` instead of the engine;
`--forward` passes every datagram on, so the show keeps running. It records the
**bytes**, base64, one per line, and `--replay` sends them back exactly — a
capture taken at a venue becomes a regression test at a desk. The F19 design
record lists what the first captures on each rig have to show.

## Two rigs, two tools, one wire format

The CDJs and the DDJ-1000 need completely different mechanisms — one is a
network protocol, the other is reading a running application's memory. Both
land on `--sync-port` as OSC, so the engine has one seam and no idea which is
which.

Neither tool is ours and neither is bundled. What is built and tested here is
the receiving end of both.

### CDJs — beat-link-trigger

[beat-link-trigger](https://github.com/Deep-Symmetry/beat-link-trigger) sits on
Deep Symmetry's `beat-link`, *the* reference implementation of Pro DJ Link. It
already has **phrase-triggered cues** as a first-class feature.

It sends OSC only from **expressions** — Clojure that someone writes inside it.
We ship them: [`blt/klights.clj`](blt/klights.clj). Paste each section into the
expression it names, in the Triggers window's File menu:

1. **Shared Functions** — the three `klights-` functions.
2. **Global Setup Expression** — set `:klights-port` to the engine's
   `--sync-port` (and `:klights-host` if the engine is on another machine).
3. **Came Online Expression** and **Going Offline Expression** and **Global
   Shutdown Expression** — one line or block each.

Then go online. 25 times a second they send the tempo master's position and
playing state (`/klights/v1/pos`), its identity whenever the deck or track
changes (`/klights/v1/track`, with rekordbox id and signature), and on every
beat its tempo and bar phase (`/bpm`, `/beat`), which the clock reads as before.
`engine/tests/data/blt_klights_v1_golden.json` holds the exact bytes they must
produce; the engine decodes them and `bridge.py --blt` reproduces them, so a
desk test with `--blt` is a test of this encoding.

**Not yet run against a CDJ.** They follow BLT's documented API and its own
ArtNet-timecode example; the first real run is judged by the hardware
checklist in [the F19 design record](../../docs/design/timecoded-shows.md).
Position is exact only on CDJ-3000s, which report it every 30 ms; older players'
is estimated from beats and goes wrong on loops — BLT's documentation says so.

`engine/sync.py` also reads flat addresses — `/…/bpm`, `/…/beat`, `/…/phrase`,
`/…/deck`, `/…/track` — for anything hand-rolled.

Cost: a JVM in the show chain.

### DDJ-1000 — rkbx_link

**The DDJ-1000 is USB and never speaks Pro DJ Link at all.** rekordbox's PRO DJ
LINK Bridge does not cover it, so the network route simply does not exist here;
the state has to come out of rekordbox itself.

[rkbx_link](https://github.com/grufkork/rkbx_link) reads transport position and
beatgrid **directly out of rekordbox's memory**, and the phrase structure out of
its analysis files. It emits OSC, Ableton Link and sACN. Windows, rekordbox
7.2.x. Its OSC is what this engine's decoder is written against:

| rkbx_link address | becomes |
|---|---|
| `/master/bpm/current` | `bpm` |
| `/master/beat/subdiv/4` | `beat_in_bar` — it sends a 0–1 ramp looping every *n* beats, scaled back up here |
| `/master/phrase/current` | `phrase_label`, and `phrase_measured` with it |
| `/master/phrase/countin` | `phrase_ends_in` |
| `/master/track/title` | `title`, and the console's `track` label |
| `/master/track/artist`, `/master/track/album` | `artist`, `album` |
| `/master/bpm/original` | `bpm_original` — with `bpm/current`, the pitch |
| `/master/time` | `track_time`: where in the track, in seconds (F19) |
| `/master/phrase/next`, `/master/beat/trigger/*` | understood and **ignored** — counted separately from rejects |

Everything under `/master/` is tagged `source: "rkbx"`, which is how the
transport applies rkbx_link's latency offset and the console names it.

[`rkbx_link.config.example`](rkbx_link.config.example) is a complete config
with every setting kLights needs, each commented with why. Four things to get
right, each of which fails quietly otherwise:

- **`osc.msg.master/time true` and `osc.msg.master/phrase true`.** Both are
  `false` in rkbx_link's shipped config: without the first there is no position
  at all, and without the second, no phrases.
- **`osc.destination` must point at the engine's `--sync-port`.** rkbx_link
  defaults to `127.0.0.1:4460`.
- **`osc.phrase_output_format string`.** The other formats send a number, which
  arrives here as a phrase called `"3"`.
- **Send the `master` deck, not numbered decks.** The engine *ignores* any
  address whose first component is a digit, deliberately: `/1/bpm` and `/2/bpm`
  during a blend are two decks fighting over one clock, and the tempo that comes
  out belongs to neither. Dropped packets show in the Sync row's rejected count;
  following the last deck that spoke would be invisible.

#### What the licence actually gates — and why to pin rekordbox

The **software is GPL-3.0** and entirely on GitHub. Nothing is held back in the
code, and it can be forked and built from source freely.

What is sold is `data/offsets`: a text file of base addresses and pointer chains
into rekordbox's memory, one block per rekordbox build. The copy committed to
the repo carries exactly one — **7.2.2**. A licence buys access to the update
server, which is where blocks for 7.2.3 through 7.2.17 (and 6.8.5) live. It is a
data subscription, not a feature gate.

So the free path is real: **pin rekordbox to 7.2.2** and the committed offsets
are all you need.

Pin it anyway. A show laptop should not be auto-updating rekordbox the week of a
gig, and here an update does not degrade the sync — it *ends* it, because the
addresses stop meaning anything. Treat the rekordbox version like the rest of
the show config: chosen deliberately, changed on purpose, verified with
`bridge.py --fake --osc` before it matters.

Buy the licence when a newer rekordbox is worth having. Forking to hunt offsets
yourself is permitted by the GPL and is the wrong trade: it is reverse
engineering pointer chains into a stripped release binary, and it has to be
redone for **every** rekordbox update, forever.

The other caveat: memory reading is inherently version-brittle, so whatever
route is taken, the failure mode is "sync stops" rather than "sync drifts". The
console's Sync row goes to NO SIGNAL and take-over is one tap — which is the
same handling as any other bridge dying, and the reason that was built first.

### Rejected: the now-playing tools

[supbox](https://github.com/gabek/supbox) and
[Rekordbox-NowPlaying](https://github.com/rogeraabbccdd/Rekordbox-NowPlaying)
both look relevant and neither is. supbox polls `djmdSongHistory` — the play
*history* table — for the most recent row; there is no position, elapsed time or
beat in it, and rekordbox only writes that row about **60 seconds** into a track
(10s minimum, 6.6.8+). Rekordbox-NowPlaying is OCR of a 1920×1080 fullscreen
window. Both answer "what is playing" for a stream overlay. Neither can answer
"where are we in the bar", which is the only question this needs.

[pyrekordbox](https://github.com/dylanljones/pyrekordbox) unlocks the SQLCipher
`master.db` (v6 and v7) and parses ANLZ files. That is the right library for
*static* data — beat grids, phrase maps, cue points — and it is worth
remembering for anything offline. It has no realtime surface.

**Ableton Link** is the fallback if rkbx_link ever stops working: rekordbox
supports it natively and it gives tempo and quantum phase, but **no phrase and
no track identity**. Enough to keep movement on the beat; not enough for the
thing phrase was wanted for.

### If we ever build our own

[python-prodj-link](https://github.com/flesniak/python-prodj-link) for the
virtual-CDJ and beat-packet layer, plus a Kaitai-generated parser for the ANLZ
tags — `PQTZ` is the beat grid, `PSSI` the phrase structure, and the `.ksy`
grammars compile to Python so the parser is generated rather than transcribed.
One language, no JVM. The cost is owning track identification over dbserver/NFS
and the fact that **PSSI is masked in newer rekordbox exports** — beat-link
handles that deobfuscation and the Python side may not.

This is the CDJ path only. There is no realistic own-built version of the
rekordbox one: it is memory offsets into someone else's binary.

## What the engine expects

JSON over UDP, any subset of these, unknown keys ignored:

| field | meaning |
|---|---|
| `bpm` | source tempo, 40–250. Safe to send every packet. |
| `beat_in_bar` | beat within the bar. **This is what a per-beat source should send.** Corrects the grid by at most half a bar and never moves cumulative position. |
| `beat` | absolute musical position. A **jump** — correct for a re-sync, wrong for tracking. Sending it every beat makes every move judder. |
| `phrase_measured` | true when phrase is read, not counted from a tapped downbeat. |
| `phrase_label` | rekordbox's label as it shows it: `Intro`, `Verse 1`, `Up 2`, `Chorus`, `Down`, `Bridge`, `Outro`. It never says Build or Drop. Empty string clears it. |
| `phrase_ends_in` | beats until the phrase ends. Stored as an absolute beat, so sending it once per bar is enough. |
| `source` | what to display as the clock owner. |
| `deck`, `track` | labels for the console. |

And which track is playing, and where in it (F19). These can select
pre-authored shows, so each is range-checked and anything out of range is
dropped:

| field | meaning |
|---|---|
| `track_time` | position in the audio, seconds, -60 to 14400. Send it often: ~60 Hz from rkbx_link, 25 Hz from beat-link-trigger. |
| `title`, `artist`, `album` | who the track is. Unicode-normalised, control characters stripped, 200 characters at most. A `title` also sets `track`. |
| `duration` | track length, seconds. |
| `bpm_original` | the track's own tempo; with `bpm`, the pitch. |
| `pitch` | playback rate, 0–4, when a source knows it directly. |
| `playing`, `on_air`, `master` | flags. `playing: false` pauses at once; without it, a source going silent for the grace period counts as paused. |
| `rekordbox_id` | 0 to 2³²−1. Only meaningful with the database it came from. |
| `signature` | beat-link-trigger's track signature, 40 hex characters. |
| `beat_number` | the beat in the track, for the grid phase check. |

### `/klights/v1` — our own namespace

The beat-link-trigger expressions we ship send two messages, each with
**several** arguments. Unlike the flat addresses they are decoded strictly:
the exact argument count, each of the right kind, or the whole message is
rejected.

| address | arguments |
|---|---|
| `/klights/v1/pos` | deck, playing, time_s, pitch, beat_number, master, on_air |
| `/klights/v1/track` | deck, rekordbox_id, signature, title, artist, album, duration_s |

Numbers may be OSC `i`, `f`, `d` or `h`. Both are tagged `source: "blt"`.
Identity arrives whole in one message, so a track change and a jump from this
source count at once; from rkbx_link, whose identity arrives a field at a time,
both wait a moment for the rest (see `engine/transport.py`).

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
