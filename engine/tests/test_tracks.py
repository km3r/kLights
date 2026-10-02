"""
Tests for track identity: the layered matcher, aliases, and the grid
cross-check.

The matcher's job is as much refusing as matching. A guest's track that shares
a rekordbox id with one of ours, two tracks with one title, an extended mix
against its radio edit -- each must come back as NOT a match, because playing
one track's timeline over another's beats is worse than playing none. So most
checks here are failure paths.

Run: python engine/tests/test_tracks.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import tracks as tr  # noqa: E402
from engine import tracktime  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


SIG_A = "a" * 40
SIG_B = "b" * 40


def track(title, artist="Artist", album="", duration=None, rekordbox=(),
          signatures=(), aliases=()):
    ident = {"title": title, "artist": artist, "album": album}
    if duration is not None:
        ident["duration_s"] = duration
    return {"identity": ident,
            "ids": {"rekordbox": [{"db": "usb:X", "id": i} for i in rekordbox],
                    "blt_signatures": list(signatures)},
            "aliases": list(aliases)}


LIB = {
    "night-drive": track("Night Drive", "Someone", "Night EP", 300.0,
                         rekordbox=(7,), signatures=(SIG_A,)),
    "night-drive-ext": track("Night Drive (Extended Mix)", "Someone",
                             "Night EP", 420.0),
    "intro-a": track("Intro", "Band A", "First", 60.0),
    "intro-b": track("Intro", "Band B", "Second", 61.0),
    "twin-1": track("Twin", "Same", "One", 200.0),
    "twin-2": track("Twin", "Same", "Two", 260.0),
    "dup-1": track("Dup", "Same", "", 100.0),
    "dup-2": track("Dup", "Same", "", 100.5),
    "renamed": track("Proper Title", "Real Artist", "", 250.0,
                     aliases=[{"title": "propr title (promo)", "artist": "real artist",
                               "album": "", "via": "manual"}]),
}
IDX = tr.TrackIndex.build(LIB)


# -- 1. each layer ------------------------------------------------------------
print("\n1. each layer, strongest first")
m = IDX.match(title="anything at all", signature=SIG_A)
check("a signature decides alone, whatever the title says",
      m.track_id == "night-drive" and m.via == tr.SIGNATURE, f"{m}")

m = IDX.match(title="night drive", rekordbox_id=7)
check("a rekordbox id matches when the title agrees",
      m.track_id == "night-drive" and m.via == tr.REKORDBOX_ID, f"{m}")

m = IDX.match(title="Some Guest Track", artist="Guest", rekordbox_id=7)
check("but NOT when the title disagrees -- every USB numbers from 1",
      m.track_id is None and m.via == tr.NONE, f"{m}")

m = IDX.match(title="Night Drive", artist="Someone", album="Night EP")
check("title + artist + album", m.track_id == "night-drive"
      and m.via == tr.TITLE_ARTIST_ALBUM, f"{m}")

m = IDX.match(title="NIGHT  DRIVE", artist="someone", album="")
check("title + artist when the album differs, normalised",
      m.track_id == "night-drive" and m.via == tr.TITLE_ARTIST, f"{m}")

m = IDX.match(title="Propr Title (Promo)", artist="Real Artist")
check("an alias matches the description it records",
      m.track_id == "renamed" and m.via == tr.ALIAS, f"{m}")

m = IDX.match(title="Night Drive", artist="Someone", album="Night EP",
              signature=SIG_B)
check("an unknown signature falls through to the next layers",
      m.track_id == "night-drive" and m.via == tr.TITLE_ARTIST_ALBUM, f"{m}")


# -- 2. refusing --------------------------------------------------------------
print("\n2. refusing to guess")
m = IDX.match(title="Night Drive", artist="Someone", album="Night EP",
              duration=420.0)
check("a duration that cannot be the same file refuses the match",
      m.track_id is None and m.via == tr.NONE, f"{m}")
m = IDX.match(title="Night Drive", artist="Someone", duration=301.0)
check("one within 1.5 s accepts it", m.track_id == "night-drive", f"{m}")
m = IDX.match(title="Night Drive (Extended Mix)", artist="Someone")
check("an extended mix is its own track, not the radio edit",
      m.track_id == "night-drive-ext", f"{m}")

m = IDX.match(title="Intro", artist="Band C")
check("the same title by another artist is not a match",
      m.track_id is None and m.via == tr.NONE, f"{m}")

m = IDX.match(title="Dup", artist="Same")
check("two tracks with one description are ambiguous, with both named",
      m.track_id is None and m.via == tr.AMBIGUOUS
      and set(m.candidates) == {"dup-1", "dup-2"}, f"{m}")

m = IDX.match(title="Twin", artist="Same")
check("ambiguous without a duration", m.via == tr.AMBIGUOUS, f"{m}")
m = IDX.match(title="Twin", artist="Same", duration=260.2)
check("and a duration settles it", m.track_id == "twin-2", f"{m}")
m = IDX.match(title="Twin", artist="Same", album="One")
check("as does the album, at its stronger layer",
      m.track_id == "twin-1" and m.via == tr.TITLE_ARTIST_ALBUM, f"{m}")

m = IDX.match(title="", artist="Someone", rekordbox_id=7)
check("no title and no signature is no match", m.via == tr.NONE, f"{m}")

two_sigs = tr.TrackIndex.build({"x": track("X", signatures=(SIG_A,)),
                                "y": track("Y", signatures=(SIG_A,))})
m = two_sigs.match(title="X", signature=SIG_A)
check("a signature claimed by two tracks is ambiguous, not the first one",
      m.via == tr.AMBIGUOUS and m.track_id is None, f"{m}")

check("an empty library matches nothing",
      tr.TrackIndex.build({}).match(title="Night Drive").via == tr.NONE)
check("public() caps the candidate list",
      len(tr.Match(None, tr.AMBIGUOUS, tuple("abcdefgh")).public()["candidates"]) == 5)


# -- 3. aliases ---------------------------------------------------------------
print("\n3. aliases")
alias = tr.alias_for("Guest Name", "Guest Artist", "", "2026-10-01")
check("an alias records the description and that it was by hand",
      alias == {"title": "Guest Name", "artist": "Guest Artist", "album": "",
                "via": "manual", "added": "2026-10-01"}, f"{alias}")
doc = LIB["renamed"]
check("a track answers to its own identity",
      tr.has_description(doc, {"title": "proper title", "artist": "REAL ARTIST"}))
check("and to its aliases",
      tr.has_description(doc, {"title": "Propr Title (Promo)",
                               "artist": "Real Artist"}))
check("but not to something else",
      not tr.has_description(doc, {"title": "Other", "artist": "Real Artist"}))


# -- 4. the grid cross-check --------------------------------------------------
print("\n4. does the deck agree with the grid?")
GRID = tracktime.Grid.steady(128.0, first_downbeat_s=0.25)
BEAT_S = 60.0 / 128.0


def feed_phase(check_, offset_beats, seconds=3.0, hz=60.0, start=10.0, at0=0.0,
               jitter=0.0):
    """rkbx_link's bar phase, off by `offset_beats` from the grid."""
    n = int(seconds * hz)
    for i in range(n):
        t = start + i / hz
        beat = GRID.beat_at(t) + offset_beats + (jitter if i % 7 == 0 else 0.0)
        check_.phase(beat % 4, t, at0 + i / hz)


def feed_number(check_, offset_beats, seconds=3.0, hz=25.0, start=10.0, at0=0.0,
                lag_s=0.0):
    """beat-link's beat count (1 at the grid's first beat), with the count
    `lag_s` stale, as an old player's status packets are."""
    n = int(seconds * hz)
    for i in range(n):
        t = start + i / hz
        counted = GRID.beat_at(max(0.0, t - lag_s)) + offset_beats
        number = int(counted // 1) - int(GRID.beats[0]) + 1
        check_.number(number, t, at0 + i / hz)


c = tr.GridCheck(GRID)
feed_phase(c, 0.0, jitter=0.3)
check("a deck in phase raises nothing, even with the odd late packet",
      c.warning is None, f"{c.warning}")

c = tr.GridCheck(GRID)
feed_phase(c, 0.5, seconds=1.0)
check("an offset raises nothing before the window has filled",
      c.warning is None, f"{c.warning}")
feed_phase(c, 0.5, seconds=2.0, start=11.0, at0=1.0)
check("half a beat out, for two seconds, is a warning saying by how much",
      c.warning is not None and c.warning["kind"] == "phase"
      and abs(c.warning["offset_beats"] - 0.5) < 0.02, f"{c.warning}")
feed_phase(c, 0.0, seconds=3.0, start=20.0, at0=10.0)
check("and back in phase, it clears", c.warning is None, f"{c.warning}")

c = tr.GridCheck(GRID)
feed_phase(c, -1.0)
check("a whole beat early is caught by phase too",
      c.warning is not None and abs(c.warning["offset_beats"] + 1.0) < 0.02,
      f"{c.warning}")
c = tr.GridCheck(GRID)
feed_phase(c, 2.0)
check("two beats out wraps to the edge, and still warns",
      c.warning is not None and abs(abs(c.warning["offset_beats"]) - 2.0) < 0.02,
      f"{c.warning}")
c = tr.GridCheck(GRID)
feed_phase(c, 4.0)
check("a whole bar out is invisible to phase -- documented, not a bug",
      c.warning is None, f"{c.warning}")
c = tr.GridCheck(GRID)
feed_phase(c, 0.05)
check("a twentieth of a beat is within tolerance", c.warning is None,
      f"{c.warning}")

c = tr.GridCheck(GRID)
feed_number(c, 0.0, lag_s=0.2)
check("a beat count in step raises nothing, even 200 ms stale",
      c.warning is None, f"{c.warning}")
c = tr.GridCheck(GRID)
feed_number(c, 1.0)
check("one beat ahead on the count is a warning of +1 beat -- the same sign "
      "as phase",
      c.warning is not None and c.warning["kind"] == "number"
      and c.warning["offset_beats"] == 1.0, f"{c.warning}")
c = tr.GridCheck(GRID)
feed_number(c, 4.0)
check("and a whole bar out, which phase cannot see, is caught by the count",
      c.warning is not None and c.warning["offset_beats"] == 4.0,
      f"{c.warning}")

anacrusis = tracktime.Grid.from_segments([[-2, 0.0, 120.0]])
c = tr.GridCheck(anacrusis)
for i in range(75):
    t = 5.0 + i / 25
    beat = anacrusis.beat_at(t)
    c.number(int(beat // 1) + 2 + 1, t, i / 25)
check("a grid starting before its first downbeat counts from its first beat",
      c.warning is None, f"{c.warning}")

c = tr.GridCheck(GRID)
feed_phase(c, 0.5, seconds=1.5)
c.reset()
feed_phase(c, 0.5, seconds=1.5, start=12.0, at0=1.5)
check("reset (a jump) restarts the window", c.warning is None, f"{c.warning}")


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("tracks: all checks pass")
