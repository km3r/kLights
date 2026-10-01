# Example show folder

A complete, valid show folder built around the synthetic track that
`bridges/prolink/bridge.py --fake` plays: 128 BPM, 96 bars, three minutes, in
rekordbox's phrase vocabulary. It exists so every part of F19 can be run and
tested without a rekordbox library or a set of decks.

```bash
python -m engine.showfiles check shared/show-example
```

A real show folder does **not** live in the repo. It lives in a folder that
syncs between the machine you design on and the show laptop (Dropbox, OneDrive,
a NAS), and the engine is pointed at it:

```bash
python -m engine.showfiles init "D:/Dropbox/kLights show"   # creates or completes one
python -m engine.server --show-dir "D:/Dropbox/kLights show"
```

or, per machine, in a gitignored `klights.local.json` at the repo root:

```json
{"show_dir": "D:/Dropbox/kLights show"}
```

## What is in here

| file | what it shows |
|---|---|
| `show.json` | the folder's settings: template set, fallback, pause policy, Follow DJ starting disarmed |
| `tracks/synth-128.json` | a prepped track: identity, a one-anchor grid, rekordbox's phrases |
| `timelines/synth-128.json` | one of everything a timeline holds: a preset snapshot, routine clips with palette-role and direct colours, a rig-bound look, palette changes, hits, master and size automation, and a VJ row carried for milestone 3 |
| `routines/*.json` | four routines written against roles (`movers`, `pins`), not fixtures, with open parameters and variations |
| `templates/club.json` | a template set: rekordbox phrase label → routine, and the bar-count fallback |

The `$schema` keys point at the repo's own `schemas/`, so an editor gives
completion here without a copy. A folder made by `init` gets its own copy of
the schemas instead, because it lives outside the repo.

The snapshot and the `Lazy Circle` clip use looks from the despacio event, so
this timeline is despacio-bound in the same way a real one built on a real rig
would be. Routines that use only the parametric blocks run on any rig.
