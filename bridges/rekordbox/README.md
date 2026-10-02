# rekordbox → show folder

The prep tool. It reads what rekordbox already knows about a track — who it
is, where its beats are, what its phrases are, what its waveform looks like —
and writes it into a show folder, so the designer can draw a timeline on it and
the engine can recognise it when it plays.

**No analysis is written here,** as with the DJ bridge. Beat grids and phrases
are rekordbox's own, read out of its analysis files. A grid we derived would be
a different grid from the one the decks are playing to.

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

- **Recognises tracks it has seen,** by rekordbox id, then by title, artist,
  album and duration, and updates them in place. Running it twice changes
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
- Reading the collection's `master.db` directly. It is SQLCipher-encrypted and
  its key handling changes between rekordbox versions; the XML export carries
  the same identity without either problem.

## Format references

[crate-digger](https://github.com/Deep-Symmetry/crate-digger)'s
`rekordbox_anlz.ksy` and `anlz.adoc`, by Deep Symmetry — the reference every
Pro DJ Link tool reads these files with. `engine/tests/test_prep.py` builds its
fixtures byte by byte from those layouts, independently of `anlz.py`.
