# Changelog

Notable changes, newest first. Versions follow [semver](https://semver.org);
until 1.0 the config file formats may change between minor versions, and any
break will say so here with a migration note.

The `F<n>` labels are the project's own feature milestones; the summary below is
the only record of them until a roadmap doc lands.

---

## Unreleased

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
- A strobe rate limit is now the highest-value safety item outstanding.
- Same correction applied to `engine/safety.py`, the README, the Rig panel and
  the test commentary, so the codebase does not carry two framings.

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
- **`mcp/cosmos_mcp.py`** — the same operations over MCP, so the rig can be
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
