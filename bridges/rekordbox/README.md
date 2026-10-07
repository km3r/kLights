# rekordbox → show folder

The prep tool. It reads what rekordbox already knows about a track — who it
is, where its beats are, what its phrases are, what its waveform looks like —
and writes it into a show folder, so the designer can draw a timeline on it and
the engine can recognise it when it plays.

**No analysis is written here,** as with the DJ bridge. Beat grids and phrases
are rekordbox's own, read out of its analysis files. A grid we derived would be
a different grid from the one the decks are playing to.

The easy way is the designer: its track list has a **From rekordbox** panel —
the playlist tree, a playlist's tracks, a search over the whole collection —
and **Add to the show** preps what is ticked. It is this tool underneath.

## Straight from rekordbox's database

```bash
python bridges/rekordbox/prep.py db                          # the playlists
python bridges/rekordbox/prep.py db --playlist "Gigs/Friday" --list
python bridges/rekordbox/prep.py db --playlist Friday
python bridges/rekordbox/prep.py db --search "night drive"
python bridges/rekordbox/prep.py db --id 79832981 --id 159604375
python bridges/rekordbox/prep.py catalogue > collection.json # what the designer browses
```

This reads `master.db`, rekordbox 6 and 7's collection, so there is no File →
Export Collection step. A playlist is named by its path or, where it is unique,
its name; case and stray spaces don't matter (rekordbox keeps them: "Despacio "
is a real playlist name). `--search` wants every word in the title, artist or
album, compared the way the matcher compares names. With `--playlist` or `--id`
it narrows what they chose.

**It needs `sqlcipher3` and a key.** The database is SQLCipher-encrypted:

```bash
pip install sqlcipher3      # for this bridge only; the engine never imports it
```

The key is the same in every rekordbox 6 and 7 release so far, and it is not in
this repository. Set `RB_CIPHER_KEY` (the rekordbox-sorter project uses the
same variable, so one setting serves both), or put it in `klights.local.json`:

```json
{ "rekordbox_key": "…", "rekordbox_db": "D:/elsewhere/master.db" }
```

`rekordbox_db` is only needed when the database is not in rekordbox's default
place (`%APPDATA%/Pioneer/rekordbox/master.db`, or
`~/Library/Pioneer/rekordbox/master.db` on a Mac). A decrypted copy, such as
the sorter's working copy, opens with no package and no key. The database is
opened read-only, so it is safe with rekordbox running.

What comes from where on this route:

| what | from | |
|---|---|---|
| title, artist, album, length, tempo, key, file | `djmdContent` and its tables | |
| which analysis file | `AnalysisDataPath` | no joining by path |
| beat grid, phrases, waveform | that `ANLZ0000.DAT`/`.EXT` | as for the XML route |
| cues | `djmdCue` | a collection's analysis files have **no** cues |
| playlists | `djmdPlaylist`, `djmdSongPlaylist` | a smart playlist is a query, so it is listed with no tracks |

A track rekordbox never analysed is skipped, saying so: there is no grid to put
a show on. A streaming track (Beatport, TIDAL…) is prepped if it is analysed; it
has no file, so the designer plays nothing for it. The same file imported twice
is two rows and one track, carrying both ids and both rows' cues.

## One song, every deck

Each source identifies a track differently, so each prepped track carries what
every one of them sends:

| source | sends | matched by |
|---|---|---|
| rekordbox on this laptop, through rkbx_link | title, artist, album | the track's identity |
| CDJs loading over the network from this rekordbox | this database's id | `ids.rekordbox` |
| CDJs playing a USB stick, through beat-link-trigger | the stick's own id, and a signature | `ids.blt_signatures` |

A stick numbers its tracks from 1, so its ids mean nothing here. The signature
is what survives: beat-link's SHA-1 over the title, artist, length, color
waveform (`PWV5`) and every beat of the grid, all of which an export copies
unchanged. **Prep computes it**, so a track matches exactly the first time it
is played from any stick exported from this collection, instead of only after
someone links it at a gig. Re-grid or retitle the track in rekordbox and the
signature changes, so prep it again (and re-export the stick); prep replaces its
own old signature and keeps any learned at a gig. `report` shows how many each
track has.

A track with **no artist** gets two signatures. beat-link hashes such a track
as `[no artist]` when it reads the stick's database, but a player's metadata
server may send an empty artist name instead, and which of the two a gig uses
depends on the players and BLT's settings.

### Checking it with beat-link itself

[`blt_check/check.py`](blt_check/check.py) runs beat-link's own code (a dev
tool: it needs Java 11+ and fetches beat-link 8 from Maven Central once, pinned
by SHA-1):

```bash
python bridges/rekordbox/blt_check/check.py usb E:/        # this stick, against the show folder
python bridges/rekordbox/blt_check/check.py collection     # prep vs beat-link, every analysed track
```

`usb` reads the stick the way beat-link-trigger does (its `export.pdb` through
beat-link's `TrackMetadata`, its analysis files) and asks the engine's matcher
which prepped track each one is. A track that matches only by name is listed
with the input that differs: title, artist, length, waveform or grid, almost
always a track re-gridded after the stick was exported. Run it on a stick
before a gig.

`collection` was run on a 6,331-track rekordbox 7.2.14 collection and beat-link
agreed with prep on every track. `golden` records beat-link's output for a few
synthetic tracks in `engine/tests/data/blt_signatures.json`, which the test
suite checks prep against, with no Java.

## From an XML export

```bash
# the local collection (Windows path shown)
python bridges/rekordbox/prep.py xml rekordbox.xml \
    --anlz-root "%APPDATA%/Pioneer/rekordbox/share/PIONEER/USBANLZ"

# a USB stick, with the XML from the rekordbox that made it
python bridges/rekordbox/prep.py xml rekordbox.xml --anlz-root E:/PIONEER/USBANLZ

# narrow it down
python bridges/rekordbox/prep.py xml rekordbox.xml --anlz-root ... --playlist Friday
python bridges/rekordbox/prep.py xml rekordbox.xml --anlz-root ... --title "night drive"

# the synthetic track bridge.py --fake plays
python bridges/rekordbox/prep.py synthetic

# what is in the folder, and which timelines sit on an old grid
python bridges/rekordbox/prep.py report
```

The show folder comes from `--show-dir`, `$KLIGHTS_SHOW_DIR` or `show_dir` in
`klights.local.json`, the same as for the engine. `--dry-run` says what would
change and writes nothing.

## Where the pieces come from

| what | from | |
|---|---|---|
| title, artist, album, duration, rekordbox id, file location | rekordbox XML | File → Export Collection in xml format |
| beat grid | `ANLZ0000.DAT` (`PQTZ`) | falls back to the XML's `TEMPO` marks |
| phrases | `ANLZ0000.EXT` (`PSSI`) | rekordbox's labels as it shows them: `Intro 1`, `Up 2`, `Verse 3`, `Chorus` |
| cues | `.EXT` (`PCO2`, with names), else `.DAT` (`PCOB`), else the XML | |
| waveform | `.DAT` (`PWAV`) and `.EXT` (`PWV5`/`PWV3`) | for the designer |
| three-band waveform | `.2EX` (`PWV7`) | what a lane follows when it follows the audio |
| which analysis is which track | `PPTH` in each analysis file | joined on the full path, else the file name |

The file-name fallback is what makes a stick work: its analysis files record
`/Contents/Artist/track.mp3`, the path on the stick, not the path on the
laptop. Two files with the same name and no full-path match are left
unjoined rather than guessed between.

Turn on **phrase analysis** in rekordbox's preferences before analysing, or the
`.EXT` files have no `PSSI` and every track falls back to counting bars.

`PSSI` is XOR-masked in rekordbox 6 and later exports. `anlz.py` detects and
removes the mask the same way crate-digger does.

## What it does to the folder

- **Recognises tracks it has seen,** by signature, then rekordbox id, then by
  title, artist, album and duration, and updates them in place. A track prepped
  from the XML and later from the database is the same track. Running it twice changes
  nothing; an unchanged track is not rewritten.
- **Keeps what you added.** Aliases, beat-link-trigger signatures learned at a
  gig, anything else in the file survives a re-prep. A track renamed in
  rekordbox keeps its old name as an alias, so decks still sending the old
  name still match it.
- **Never touches a timeline.** When a track's grid has moved since a timeline
  was drawn on it — re-gridded in rekordbox — prep says so, and `report` keeps
  saying so until the timeline is re-anchored in the designer.
- **Refuses rather than guesses.** A damaged analysis file is a warning and the
  track uses the XML's grid; a track with no grid anywhere is skipped and says
  why; a file that is not a rekordbox export is refused.

## Not built

- Reading a stick's `export.pdb` directly, for a stick with no rekordbox
  machine to hand. The XML route needs the rekordbox that made the stick.
- Smart playlists' contents. rekordbox stores the rule (`SmartList`), not the
  tracks, so prep would have to re-implement rekordbox's query language.

## Format references

[crate-digger](https://github.com/Deep-Symmetry/crate-digger)'s
`rekordbox_anlz.ksy` and `anlz.adoc`, by Deep Symmetry — the reference every
Pro DJ Link tool reads these files with. `engine/tests/test_prep.py` builds its
fixtures byte by byte from those layouts, independently of `anlz.py`.

For `master.db`: [pyrekordbox](https://github.com/dylanljones/pyrekordbox)'s
database documentation, and the schema of a real rekordbox 7.2.14 collection.
The hot cue numbering (Kind skips 4) is what
[CueGen](https://github.com/mganss/CueGen), which writes that table, does. The
signature is
[beat-link](https://github.com/Deep-Symmetry/beat-link)'s `SignatureFinder`.
`engine/tests/test_rekordbox.py` builds a plain-SQLite `master.db` with the
real column names, and checks the signature against a hash built in the test
from beat-link's steps, not by calling prep's.
