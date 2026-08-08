# Changelog

Notable changes, newest first. Versions follow [semver](https://semver.org);
until 1.0 the config file formats may change between minor versions, and any
break will say so here with a migration note.

The `F<n>` labels are the project's own feature milestones; the summary below is
the only record of them until a roadmap doc lands.

---

## Unreleased

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
| **F4** | Layered state, the beam-aware safety taper, Art-Net output, the 40 fps frame clock. The taper dims *to* a crowd level rather than to zero — "not blinding", not "never lands on anyone". |
| **F5** | Fast re-aim: a multi-point solver recovering position, offsets and invert flags together, plus drift detection and calibration snapshots. |
| **F6** | Musical timing — beats, bars, phrases, tap tempo, and motion continuous across every tempo change. |
| **F7** | Auto mode: four independently toggleable axes (timing, look changes, palette, energy). |
| **F8** | The show server (WebSocket state sync, zero dependencies, a hand-rolled RFC 6455) and the web UI — one responsive surface from phone to desktop, with 53 tests against a captured real-engine snapshot. |
| **F9** | Ported the QLC+ library — 206 looks round-tripping exactly, with a frame-level parity checker that classifies every differing channel of all 512 and admits none it cannot explain. |
| **F10** | Unreal previz sharing the show's own decoder, driven by the same Art-Net the rig sees. |

Before F2: the repo reorganised into `events/`, `shared/` and `docs/`; line
endings pinned via `.gitattributes` because QLC+ rewrites the whole `.qxw` on
save; and the post-event despacio work salvaged from untracked state.
