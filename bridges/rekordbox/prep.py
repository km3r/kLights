"""Prep tracks into a show folder: identity, beat grid, phrases, cues, waveform.

    python bridges/rekordbox/prep.py db --playlist Friday
    python bridges/rekordbox/prep.py db --search "night drive"
    python bridges/rekordbox/prep.py db                       # lists the playlists
    python bridges/rekordbox/prep.py catalogue                # the collection, as JSON
    python bridges/rekordbox/prep.py xml rekordbox.xml --anlz-root "<USBANLZ>"
    python bridges/rekordbox/prep.py xml rekordbox.xml --anlz-root E:/PIONEER/USBANLZ --playlist Friday
    python bridges/rekordbox/prep.py synthetic
    python bridges/rekordbox/prep.py report

The show folder comes from --show-dir, $KLIGHTS_SHOW_DIR or klights.local.json,
exactly as for the engine.

**What it reads.** Two routes to the same tracks:

  - `db`: rekordbox's own database, master.db (masterdb.py), with no export
    step. Each track names its analysis file, and its cues are read from the
    database, which is the only place a collection keeps them. The database is
    encrypted: this route needs the `sqlcipher3` package and the key (see
    masterdb.py). It is the only part of this tool that needs a package, and
    it is only imported when the encrypted file is opened.
  - `xml`: rekordbox's XML export (File > Export Collection in xml format),
    joined to the analysis files on the audio path each records (PPTH), and
    failing that on the file name -- which is what makes a USB stick work: its
    analysis files say `/Contents/Artist/track.mp3`, not the path on the
    laptop. Stdlib only. `--anlz-root` is rekordbox's analysis folder,
    `%APPDATA%/Pioneer/rekordbox/share/PIONEER/USBANLZ`, or a stick's
    `E:/PIONEER/USBANLZ` with the XML from the rekordbox that made it.

Reading a stick's export.pdb directly, for a stick with no rekordbox machine
to hand, is not built.

**Who it is, to every deck.** A prepped track carries what each source can
match it by (engine/tracks.py): rkbx_link sends title, artist and album; CDJs
loading over the network from this rekordbox send this database's id; CDJs
playing a USB stick send an id that means nothing outside the stick, but also
beat-link-trigger's signature -- a hash of the analysis, which an export
copies unchanged. Prep computes that signature itself (`blt_signature`), so a
track is matched exactly on its first play from any stick exported from this
collection, rather than only after it has been linked once at a gig.

A track with no analysis file still gets the XML's beat grid and its cues,
without phrases -- the timeline still works, and templates fall back to
counting bars. The db route has no grid but the analysis's, so it skips one.

**What it writes.** `tracks/<id>.json` and `waveforms/<id>.json`, through
engine/showfiles.py, so a prepped track is validated by the same rules the
designer and MCP use. It is idempotent: a track already in the folder is
recognised (by its signature, then its rekordbox id, then by title, artist,
album and duration) and updated in place, and an unchanged track is not
rewritten at all. It never
touches a timeline. When a track's grid has moved since a timeline was drawn on
it, that is reported, because every cue on that timeline may now be off.
"""

from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import hashlib
import json
import socket
import struct
import sys
import urllib.parse
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE))

import anlz  # noqa: E402
import masterdb  # noqa: E402
from engine import config as configmod  # noqa: E402
from engine import showfiles as sf  # noqa: E402
from engine import tracks as tracksmod  # noqa: E402
from engine import tracktime  # noqa: E402

TOOL = "prep 0.1"

# How far a beat may sit from the straight line between two grid anchors before
# a new anchor is needed. PQTZ stores whole milliseconds, so a perfectly steady
# track is already up to half a millisecond off any line; one millisecond keeps
# a steady track to a couple of anchors and a drifting one honest.
GRID_TOLERANCE_MS = 1.0

DETAIL_RATE = 150           # rekordbox's scrolling waveforms: 150 columns/second


class PrepError(ValueError):
    """A track that cannot be prepped, with the reason."""


@dataclass
class Prepared:
    """One track, read and converted, before it meets the folder."""
    identity: dict
    segments: list
    phrases: Optional[dict] = None
    cues: list = field(default_factory=list)
    audio: list = field(default_factory=list)
    rekordbox_ids: list = field(default_factory=list)
    source: dict = field(default_factory=dict)
    waveform: Optional[dict] = None
    notes: list = field(default_factory=list)
    fixed_id: Optional[str] = None      # synthetic tracks have a known id
    # beat-link-trigger's, computed here: what a CDJ will report (blt_signatures)
    signatures: list = field(default_factory=list)


# -- grids --------------------------------------------------------------------

def grid_from_beats(beats: list) -> tuple[list, int]:
    """PQTZ's every-beat list -> (anchors, index of the first downbeat).

    Beat 0 is the first downbeat; a pickup before it gets negative beats. Runs
    of steady beats collapse to one segment, greedily: extend each segment for
    as long as every beat inside it stays within GRID_TOLERANCE_MS of the line
    between its ends.
    """
    if not beats:
        raise PrepError("the analysis has no beat grid")
    downbeat = next((i for i, b in enumerate(beats) if b.number == 1), None)
    if downbeat is None:
        raise PrepError("the beat grid has no downbeat")
    points = [(i - downbeat, float(b.time_ms)) for i, b in enumerate(beats)]
    for (b0, t0), (b1, t1) in zip(points, points[1:]):
        if t1 <= t0:
            raise PrepError(f"the beat grid runs backwards at beat {b1}")
    if len(points) == 1:
        return [[points[0][0], points[0][1], round(beats[0].bpm, 3)]], downbeat

    anchors = [0]
    start = 0
    end = 1
    while end < len(points) - 1:
        candidate = end + 1
        b0, t0 = points[start]
        b1, t1 = points[candidate]
        slope = (t1 - t0) / (b1 - b0)
        fits = all(abs(t0 + (points[k][0] - b0) * slope - points[k][1])
                   <= GRID_TOLERANCE_MS for k in range(start + 1, candidate))
        if fits:
            end = candidate
        else:
            anchors.append(end)
            start, end = end, end + 1
    anchors.append(len(points) - 1)

    segments = []
    for n, idx in enumerate(anchors):
        beat, time_ms = points[idx]
        if n + 1 < len(anchors):
            nb, nt = points[anchors[n + 1]]
            bpm = (nb - beat) / ((nt - time_ms) / 1000.0) * 60.0
        else:
            pb, pt = points[anchors[n - 1]]
            bpm = (beat - pb) / ((time_ms - pt) / 1000.0) * 60.0
        segments.append([beat, time_ms, round(bpm, 3)])
    return segments, downbeat


def grid_from_tempos(tempos: list[tuple[float, float, int]]) -> list:
    """rekordbox XML's TEMPO marks -> anchors. Each mark is (start seconds,
    bpm, beat-in-bar). Beat 0 is the first downbeat at or after the first
    mark; the beats between marks are counted at each mark's tempo."""
    if not tempos:
        raise PrepError("the XML has no TEMPO marks for this track")
    tempos = sorted(tempos)
    start, bpm, battito = tempos[0]
    index = -((5 - battito) % 4) if battito in (1, 2, 3, 4) else 0
    segments = [[index, round(start * 1000.0, 3), round(bpm, 3)]]
    for (s0, bpm0, _), (s1, bpm1, _) in zip(tempos, tempos[1:]):
        index += round((s1 - s0) * bpm0 / 60.0)
        if index <= segments[-1][0]:
            raise PrepError("the XML's TEMPO marks are closer than a beat apart")
        segments.append([index, round(s1 * 1000.0, 3), round(bpm1, 3)])
    return segments


# -- the rest of the analysis -------------------------------------------------

def phrases_from(analysis: anlz.Analysis, downbeat: int,
                 notes: list) -> Optional[dict]:
    """PSSI -> {"mood", "items": [[start, end, label], ...]} in track beats.
    PSSI counts beats from 1 at the first beat of the grid; ours count from 0
    at the first downbeat."""
    if not analysis.phrases:
        return None
    entries = sorted(analysis.phrases, key=lambda p: p.beat)
    items = []
    for n, entry in enumerate(entries):
        start = entry.beat - 1 - downbeat
        if n + 1 < len(entries):
            end = entries[n + 1].beat - 1 - downbeat
        elif analysis.phrase_end_beat:
            end = analysis.phrase_end_beat - 1 - downbeat
        else:
            end = start + 32
        if end <= start:
            notes.append(f"phrase {entry.index} ({entry.label}) has no length; "
                         f"left out")
            continue
        items.append([start, end, entry.label])
    problems = tracktime.phrase_problems(items)
    if problems:
        notes.append("phrases not used: " + "; ".join(problems))
        return None
    return {"mood": analysis.mood, "items": items}


def cue_entry(grid: tracktime.Grid, time_ms: float, hot: int, loop: bool,
              name: str) -> dict:
    entry = {"beat": round(grid.beat_at(time_ms / 1000.0), 3),
             "name": name,
             "kind": "loop" if loop else ("hot" if hot else "memory")}
    if hot:
        entry["slot"] = chr(ord("A") + hot - 1)
    return entry


def waveform_from(analysis: anlz.Analysis) -> Optional[dict]:
    if (analysis.preview is None and analysis.detail is None
            and analysis.bands is None):
        return None
    doc: dict = {}
    if analysis.preview is not None:
        doc["preview"] = base64.b64encode(analysis.preview).decode("ascii")
    if analysis.detail is not None:
        doc["detail"] = {"format": analysis.detail_format, "rate": DETAIL_RATE,
                         "data": base64.b64encode(analysis.detail).decode("ascii")}
    if analysis.bands is not None:
        # What a lane follows when its row carries `audio` (engine/bands.py).
        doc["bands"] = {"format": "pwv7", "rate": DETAIL_RATE,
                        "data": base64.b64encode(analysis.bands).decode("ascii")}
    return doc


def _waveform_note(root: Path, tid: str, waveform: dict) -> Optional[str]:
    """Why a track's stored waveform should be written again although the
    track itself is unchanged, or None if it is as rekordbox has it."""
    path = sf.path_for(root, "waveform", tid)
    if not path.exists():
        return "waveform was missing; rewritten"
    result, _ = sf.read_doc(path, "waveform")
    stored = result.doc if result.ok else {}
    if all(stored.get(k) == v for k, v in waveform.items()):
        return None
    if "bands" in waveform and "bands" not in stored:
        # Prepped before lanes could follow the audio: the same analysis, now
        # with the three bands rekordbox measured rather than its colours.
        return "waveform rewritten with rekordbox's three-band analysis"
    return "waveform changed in rekordbox; rewritten"


NO_ARTIST = "[no artist]"


def blt_signature(title: str, artist: Optional[str], duration_s: Optional[float],
                  analysis: anlz.Analysis) -> Optional[str]:
    """beat-link-trigger's track signature, as a CDJ playing this track will
    report it -- or None when the analysis lacks what it hashes.

    beat-link 8's `SignatureFinder.computeTrackSignature`, byte for byte: SHA-1
    over the title in UTF-8, a zero byte, the artist (or "[no artist]" for a
    track with none), a zero byte, the duration in whole seconds, the colour
    detail waveform's entries (`PWV5`, which beat-link 8 always uses whatever
    style it displays), then each beat of the grid as its position in the bar
    and its time in milliseconds -- integers four bytes big-endian.

    Everything hashed is copied unchanged by a USB export, which is the point:
    a stick's track ids are its own, but its signature is this collection's.
    It changes when the track is re-gridded or retitled, and so does this.

    Checked against beat-link 8.0.0 itself on a whole collection, every one of
    6,331 tracks identical (bridges/rekordbox/blt_check/), and the golden
    signatures in engine/tests/data/ are beat-link's own output.
    """
    if (analysis.detail_format != "pwv5" or analysis.detail is None
            or not analysis.beats or duration_s is None or not title):
        return None
    digest = hashlib.sha1()
    digest.update(title.encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update((NO_ARTIST if artist is None else artist)
                  .encode("utf-8", errors="replace"))
    digest.update(b"\0")
    digest.update(struct.pack(">I", int(duration_s) & 0xFFFFFFFF))
    digest.update(analysis.detail)
    for beat in analysis.beats:
        digest.update(struct.pack(">II", beat.number & 0xFFFFFFFF,
                                  beat.time_ms & 0xFFFFFFFF))
    return digest.hexdigest()


def blt_signatures(title: str, artist: Optional[str],
                   duration_s: Optional[float],
                   analysis: anlz.Analysis) -> list[str]:
    """Every signature a CDJ could report for this track: one, or two for a
    track with no artist.

    beat-link reads a track's metadata one of three ways. From the stick's
    export.pdb, or rekordbox 7's exportLibrary.db, a track with no artist has
    no artist at all and hashes "[no artist]". From the player's metadata
    server it is whatever artist item the player sends, which may be an empty
    name -- hashing "". Which path a gig takes depends on the players and on
    beat-link-trigger's settings, so both are recorded. Neither can be another
    track's: a signature hashes the whole waveform and grid."""
    first = blt_signature(title, artist, duration_s, analysis)
    if first is None:
        return []
    if artist is None:
        return [first, blt_signature(title, "", duration_s, analysis)]
    return [first]


# -- rekordbox XML ------------------------------------------------------------

@dataclass
class XmlTrack:
    track_id: str
    identity: dict
    location: str
    size: Optional[int]
    tempos: list
    marks: list


def location_to_path(location: str) -> str:
    """rekordbox's `file://localhost/C:/Music/x.mp3` -> `C:/Music/x.mp3`."""
    path = urllib.parse.unquote(urllib.parse.urlparse(location).path)
    if len(path) > 2 and path[0] == "/" and path[2] == ":":
        path = path[1:]                         # a Windows drive letter
    return path


def _num(value: Optional[str]) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except ValueError:
        return None


def parse_xml(path: Path) -> tuple[list[XmlTrack], dict[str, list[str]]]:
    """The collection's tracks, and each playlist's track ids by name."""
    try:
        root = ET.parse(path).getroot()
    except ET.ParseError as exc:
        raise PrepError(f"{Path(path).name} is not valid XML: {exc}") from None
    if root.tag != "DJ_PLAYLISTS":
        raise PrepError(f"{Path(path).name} is not a rekordbox XML export "
                        f"(expected <DJ_PLAYLISTS>, found <{root.tag}>)")
    tracks = []
    for el in root.iterfind("./COLLECTION/TRACK"):
        identity = {"title": el.get("Name") or "", "artist": el.get("Artist") or "",
                    "album": el.get("Album") or ""}
        duration = _num(el.get("TotalTime"))
        bpm = _num(el.get("AverageBpm"))
        if duration:
            identity["duration_s"] = duration
        if bpm and 20 <= bpm <= 400:
            identity["bpm"] = bpm
        tempos = []
        for t in el.iterfind("TEMPO"):
            start, tbpm = _num(t.get("Inizio")), _num(t.get("Bpm"))
            if start is not None and tbpm and 20 <= tbpm <= 400:
                tempos.append((start, tbpm, int(_num(t.get("Battito")) or 1)))
        marks = []
        for m in el.iterfind("POSITION_MARK"):
            start = _num(m.get("Start"))
            if start is None:
                continue
            num = int(_num(m.get("Num")) or -1)
            marks.append({"time_ms": start * 1000.0, "hot": num + 1 if num >= 0 else 0,
                          "loop": m.get("Type") == "4", "name": m.get("Name") or ""})
        size = _num(el.get("Size"))
        tracks.append(XmlTrack(el.get("TrackID") or "", identity,
                               location_to_path(el.get("Location") or ""),
                               int(size) if size else None, tempos, marks))
    playlists: dict[str, list[str]] = {}
    for node in root.iter("NODE"):
        if node.get("Type") == "1":
            playlists[node.get("Name") or ""] = [t.get("Key") or ""
                                                 for t in node.iterfind("TRACK")]
    return tracks, playlists


# -- joining XML to analysis files --------------------------------------------

def _norm_path(path: str) -> str:
    return path.replace("\\", "/").casefold()


@dataclass
class AnlzIndex:
    by_path: dict = field(default_factory=dict)     # normalised audio path -> DAT
    by_name: dict = field(default_factory=dict)     # file name -> [DAT, ...]
    problems: list = field(default_factory=list)


def scan_anlz(roots: list[Path]) -> AnlzIndex:
    """Every ANLZ*.DAT under `roots`, keyed by the audio file it analyses."""
    index = AnlzIndex()
    for root in roots:
        for dat in sorted(Path(root).rglob("*")):
            if not (dat.is_file() and dat.name.upper().startswith("ANLZ")
                    and dat.suffix.upper() == ".DAT"):
                continue
            try:
                found = anlz.read_files(dat)
            except anlz.AnlzError as exc:
                index.problems.append(f"{dat}: {exc}")
                continue
            if not found.path:
                continue
            index.by_path[_norm_path(found.path)] = dat
            name = _norm_path(found.path).rsplit("/", 1)[-1]
            index.by_name.setdefault(name, []).append(dat)
    return index


def find_analysis(index: AnlzIndex, audio_path: str) -> Optional[Path]:
    """By full path; failing that, by file name if exactly one matches -- a
    stick's analysis records the path on the stick, not on the laptop."""
    hit = index.by_path.get(_norm_path(audio_path))
    if hit is not None:
        return hit
    name = _norm_path(audio_path).rsplit("/", 1)[-1]
    candidates = index.by_name.get(name, [])
    return candidates[0] if len(candidates) == 1 else None


def prepare_xml_track(xt: XmlTrack, index: AnlzIndex, db: str, host: str,
                      xml_name: str) -> Prepared:
    notes: list[str] = []
    dat = find_analysis(index, xt.location) if xt.location else None
    analysis = anlz.read_files(*anlz.siblings(dat)) if dat else None
    if analysis is not None and analysis.beats:
        segments, downbeat = grid_from_beats(analysis.beats)
        phrases = phrases_from(analysis, downbeat, notes)
        if phrases is None and not notes:
            notes.append("no phrase analysis in rekordbox for this track")
    else:
        if dat is None:
            notes.append("no analysis file found: grid from the XML, no phrases")
        segments = grid_from_tempos(xt.tempos)
        phrases = None
    grid = tracktime.Grid.from_segments(segments)
    if analysis is not None and analysis.cues:
        cues = [cue_entry(grid, c.time_ms, c.hot, c.loop, c.name)
                for c in analysis.cues]
    else:
        cues = [cue_entry(grid, m["time_ms"], m["hot"], m["loop"], m["name"])
                for m in xt.marks]
    cues.sort(key=lambda c: c["beat"])
    audio = [{"host": host, "path": xt.location}] if xt.location else []
    if audio and xt.size:
        audio[0]["size"] = xt.size
    ids = []
    if xt.track_id.isdigit():
        ids.append({"db": db, "id": int(xt.track_id)})
    # The XML writes Artist="" for a track with no artist; a CDJ hashes those
    # as "[no artist]".
    signatures = (blt_signatures(xt.identity["title"], xt.identity["artist"] or None,
                                 xt.identity.get("duration_s"), analysis)
                  if analysis is not None else [])
    return Prepared(identity=xt.identity, segments=segments, phrases=phrases,
                    cues=cues, audio=audio, rekordbox_ids=ids,
                    source={"from": "xml", "file": xml_name, "tool": TOOL},
                    waveform=waveform_from(analysis) if analysis else None,
                    notes=notes, signatures=signatures)


# -- rekordbox's database -----------------------------------------------------

def prepare_db_track(t: masterdb.Track, coll: masterdb.Collection, db: str,
                     host: str) -> Prepared:
    """One track of master.db. The grid is the analysis's or nothing: unlike
    the XML, the database holds no tempo marks of its own."""
    if not t.title:
        raise PrepError("it has no title, which is what a deck sends first")
    dat = coll.analysis_file(t)
    if dat is None:
        raise PrepError("rekordbox has not analysed it: analyse it there first")
    if not dat.is_file():
        raise PrepError(f"its analysis file is missing ({dat})")
    analysis = anlz.read_files(*anlz.siblings(dat))
    if not analysis.beats:
        raise PrepError("its analysis has no beat grid")
    notes: list[str] = []
    segments, downbeat = grid_from_beats(analysis.beats)
    phrases = phrases_from(analysis, downbeat, notes)
    if phrases is None and not notes:
        notes.append("no phrase analysis in rekordbox for this track")
    grid = tracktime.Grid.from_segments(segments)
    # One file imported twice is two rows and one track here, whichever row was
    # asked for: every row's id, and every row's cues -- often only one row has
    # any. A collection keeps its cues in the database; its analysis files' cue
    # lists are empty. A USB export is the other way round.
    copies = coll.copies(t)
    source_cues = (list(dict.fromkeys((c.time_ms, c.hot, c.loop, c.name)
                                      for row in copies
                                      for c in coll.cues.get(row.id, ())))
                   or [(c.time_ms, c.hot, c.loop, c.name) for c in analysis.cues])
    cues = sorted((cue_entry(grid, *c) for c in source_cues),
                  key=lambda c: c["beat"])
    identity = {"title": t.title, "artist": t.artist or "", "album": t.album}
    if t.duration_s:
        identity["duration_s"] = float(t.duration_s)
    if t.bpm and 20 <= t.bpm <= 400:
        identity["bpm"] = t.bpm
    audio = []
    if t.local:
        audio = [{"host": host, "path": t.path}]
        if t.size:
            audio[0]["size"] = t.size
    else:
        notes.append("a streaming track: there is no file for the designer to play")
    signatures = blt_signatures(t.title, t.artist, t.duration_s, analysis)
    if not signatures:
        notes.append("no colour waveform in its analysis, so no beat-link "
                     "signature: a CDJ playing it from a stick matches it by name")
    return Prepared(identity=identity, segments=segments, phrases=phrases,
                    cues=cues, audio=audio,
                    rekordbox_ids=[{"db": db, "id": row.id} for row in copies],
                    source={"from": "master.db", "tool": TOOL},
                    waveform=waveform_from(analysis), notes=notes,
                    signatures=signatures)


def select_db(coll: masterdb.Collection, playlists: list[str], ids: list[int],
              search: Optional[str], everything: bool) -> list[masterdb.Track]:
    """The tracks the command line names, in playlist order, each once.
    Playlists and ids add tracks; a search on its own searches the whole
    collection, and alongside them narrows what they chose."""
    chosen: dict[int, masterdb.Track] = {}
    for name in playlists:
        for t in coll.in_playlist(coll.find_playlist(name)):
            chosen.setdefault(t.id, t)
    missing = [i for i in ids if i not in coll.tracks]
    if missing:
        raise PrepError(f"no track with id {', '.join(map(str, missing))} in "
                        f"{coll.db_path.name}")
    for i in ids:
        chosen.setdefault(i, coll.tracks[i])
    if not playlists and not ids:
        chosen = dict(coll.tracks) if (everything or search) else {}
    if search:
        hits = {t.id for t in coll.search(search)}
        chosen = {i: t for i, t in chosen.items() if i in hits}
    return list(chosen.values())


def catalogue(coll: masterdb.Collection, db: str) -> dict:
    """The collection as the designer browses it: every playlist in tree
    order, and every track once. Small fields only -- no paths, no analysis --
    so 6,500 tracks are about a megabyte."""
    return {
        "kind": "klights.rekordbox_catalogue", "db": db,
        "path": str(coll.db_path), "rekordbox": coll.version,
        "read_at": dt.datetime.now().replace(microsecond=0).isoformat(),
        "playlists": [{"id": p.id, "name": p.name, "parent": p.parent,
                       "kind": p.kind, "tracks": list(p.tracks)}
                      for p in coll.playlists],
        "tracks": [{"id": t.id, "title": t.title, "artist": t.artist or "",
                    "album": t.album, "genre": t.genre, "key": t.key,
                    "bpm": t.bpm, "duration_s": t.duration_s,
                    "local": t.local, "analysed": bool(t.analysis),
                    "added": t.added}
                   for t in coll.tracks.values()],
    }


def playlist_tree(coll: masterdb.Collection) -> list[str]:
    by_id = {p.id: p for p in coll.playlists}
    lines = []
    for p in coll.playlists:
        depth, at = 0, p.parent
        while at is not None and at in by_id and depth < 32:
            depth, at = depth + 1, by_id[at].parent
        what = {"folder": "", "smart": "smart playlist, not read"}.get(
            p.kind, f"{len(p.tracks)} track{'' if len(p.tracks) == 1 else 's'}")
        name = p.name + ("/" if p.kind == "folder" else "")
        lines.append(f"  {'  ' * depth}{name:<{max(1, 40 - 2 * depth)}} {what}")
    return lines


# -- the synthetic track ------------------------------------------------------

def synthetic(bpm: float = 128.0) -> Prepared:
    """The track `bridge.py --fake` plays, as a prepped track. Built from the
    bridge's own phrase script so the two cannot drift apart."""
    sys.path.insert(0, str(REPO / "bridges" / "prolink"))
    import bridge  # noqa: E402
    items, beat = [], 0
    for label, bars in bridge.FAKE_PHRASES:
        items.append([beat, beat + bars * 4, label])
        beat += bars * 4
    grid = tracktime.Grid.steady(bpm)
    first_chorus = next(start for start, _, label in items if label == "Chorus")
    return Prepared(
        identity={"title": f"synthetic {bpm:g}", "artist": "kLights",
                  "album": "test track", "duration_s": beat * 60.0 / bpm,
                  "bpm": float(bpm)},
        segments=grid.segments(),
        phrases={"mood": "high", "items": items},
        cues=[{"beat": first_chorus, "name": "first chorus", "kind": "hot",
               "slot": "A"}],
        source={"from": "synthetic", "note": "matches bridge.py --fake"},
        fixed_id=f"synth-{bpm:g}".replace(".", "-"))


# -- into the folder ----------------------------------------------------------

def _track_doc(tid: str, p: Prepared, base: Optional[dict] = None) -> dict:
    """The track document. Built fresh, or merged onto `base` so everything
    the operator added -- aliases, signatures learned at a gig, notes -- is
    kept."""
    grid = tracktime.Grid.from_segments(p.segments)
    if base is None:
        doc = sf.new_doc("track", id=tid)
        doc.update({"identity": {}, "ids": {"rekordbox": [], "blt_signatures": []},
                    "aliases": []})
    else:
        doc = copy.deepcopy(base)
    doc["identity"] = dict(p.identity)
    ids = doc.setdefault("ids", {})
    known = ids.setdefault("rekordbox", [])
    for rid in p.rekordbox_ids:
        if rid not in known:
            known.append(rid)
    sigs = ids.setdefault("blt_signatures", [])
    # Prep's own signatures from last time are replaced: they hashed a grid or
    # a title that has since changed. Signatures learned at a gig are kept --
    # they are a stick someone really played.
    previous = ((base or {}).get("source") or {}).get("signatures") or []
    for old in previous:
        if old not in p.signatures and old in sigs:
            sigs.remove(old)
    for new in p.signatures:
        if new not in sigs:
            sigs.append(new)
    doc.setdefault("aliases", [])
    doc["grid"] = {"rev": grid.rev, "segments": p.segments}
    # What the analysis says now, including "no phrases". Old phrases are not
    # kept when rekordbox no longer has them: they were counted on whatever
    # grid the track had then, which may not be the grid it has now.
    if p.phrases is not None:
        doc["phrases"] = p.phrases
    else:
        doc.pop("phrases", None)
    doc["cues"] = p.cues
    if p.audio:
        others = [a for a in doc.get("audio", [])
                  if a.get("host") != p.audio[0].get("host")]
        doc["audio"] = others + p.audio
    doc["source"] = dict(p.source)
    if p.signatures:
        doc["source"]["signatures"] = list(p.signatures)
    return doc


def _without_stamp(doc: Optional[dict]) -> Optional[dict]:
    if doc is None:
        return None
    d = copy.deepcopy(doc)
    d.get("source", {}).pop("prepped", None)
    return d


def find_existing(tracks: dict, p: Prepared) -> Optional[str]:
    """The track already in the folder that `p` describes, if any: by its
    signature, then rekordbox id, then title, artist, album and duration.
    One signature is one analysis of one file, so a track rekordbox holds
    twice (the same file imported twice) is one track here, with both ids."""
    if p.fixed_id and p.fixed_id in tracks:
        return p.fixed_id
    for sig in p.signatures:
        for tid, doc in tracks.items():
            if sig in ((doc.get("ids") or {}).get("blt_signatures") or ()):
                return tid
    for tid, doc in tracks.items():
        known = (doc.get("ids") or {}).get("rekordbox") or []
        if any(rid in known for rid in p.rekordbox_ids):
            return tid
    key = tracksmod.identity_key(p.identity)
    for tid, doc in tracks.items():
        if (tracksmod.identity_key(doc["identity"]) == key
                and tracksmod.same_duration(doc["identity"].get("duration_s"),
                                            p.identity.get("duration_s"))):
            return tid
    return None


def apply(root: Path, prepared: list[Prepared], dry_run: bool = False,
          today: Optional[str] = None,
          outcomes: Optional[list] = None) -> list[str]:
    """Match, merge and write. Returns one line per track; `outcomes`, when
    given, gets the same as a dict per track, for a caller that is a program
    (the engine, preparing what the designer picked)."""
    folder = sf.load_folder(root)
    tracks = dict(folder.tracks)
    # Revs as this run leaves them: one run can write a track twice -- the
    # same file imported into rekordbox twice is two rows and one track.
    revs = dict(folder.revs)
    stamp = today or dt.datetime.now().replace(microsecond=0).isoformat()
    lines: list[str] = []

    def record(status: str, tid: str, p: Prepared, extra: str = "") -> None:
        if outcomes is not None:
            outcomes.append({
                "status": status, "track_id": tid,
                "rekordbox_ids": [r["id"] for r in p.rekordbox_ids],
                "title": p.identity.get("title", ""),
                "artist": p.identity.get("artist", ""),
                "signature": bool(p.signatures),
                "notes": list(p.notes) + [s.strip() for s in extra.split("\n")
                                          if s.strip()]})

    for p in prepared:
        tid = find_existing(tracks, p)
        base = tracks.get(tid) if tid else None
        if tid is None:
            tid = p.fixed_id or tracksmod.slug(p.identity, frozenset(tracks))
        doc = _track_doc(tid, p, base)
        if base is not None and tracksmod.identity_key(base["identity"]) \
                != tracksmod.identity_key(doc["identity"]):
            doc["aliases"].append({k: base["identity"].get(k, "")
                                   for k in ("title", "artist", "album")}
                                  | {"via": "renamed in rekordbox",
                                     "added": stamp[:10]})
        title = f"{p.identity.get('artist', '')} - {p.identity.get('title', '')}"
        note = "".join(f"\n      {n}" for n in p.notes)
        if _without_stamp(doc) == _without_stamp(base):
            again = (_waveform_note(root, tid, p.waveform)
                     if p.waveform is not None and not dry_run else None)
            if again:
                wave = sf.new_doc("waveform", track=tid, **p.waveform)
                sf.write_doc(sf.path_for(root, "waveform", tid), wave, "waveform")
                note += f"\n      {again}"
            lines.append(f"  unchanged  {tid}  ({title}){note}")
            record("unchanged", tid, p)
            continue
        if p.source.get("from") != "synthetic":
            doc["source"]["prepped"] = stamp
        verb = "created" if base is None else "updated"
        extra = ""
        if base is not None and base["grid"].get("rev") != doc["grid"]["rev"]:
            extra = (f"\n      re-gridded {base['grid'].get('rev')} -> "
                     f"{doc['grid']['rev']}")
            timeline = folder.timelines.get(tid)
            if timeline is not None and timeline.get("grid_rev") != doc["grid"]["rev"]:
                extra += (f"\n      timelines/{tid}.json was drawn on the old "
                          f"grid: re-anchor it in the designer")
        if not dry_run:
            path = sf.path_for(root, "track", tid)
            rel = f"tracks/{tid}.json"
            revs[rel] = sf.write_doc(path, doc, "track",
                                     base_rev=revs.get(rel, "") if base else "")
            if p.waveform is not None:
                wave = sf.new_doc("waveform", track=tid, **p.waveform)
                sf.write_doc(sf.path_for(root, "waveform", tid), wave, "waveform")
        tracks[tid] = doc
        lines.append(f"  {'would ' + verb.rstrip('d') if dry_run else verb}"
                     f"  {tid}  ({title}){extra}{note}")
        record(verb if not dry_run else "would " + verb.rstrip("d"), tid, p, extra)
    return lines


def report(root: Path) -> list[str]:
    folder = sf.load_folder(root)
    lines = []
    for tid, doc in sorted(folder.tracks.items()):
        ident = doc["identity"]
        phrases = len((doc.get("phrases") or {}).get("items") or [])
        anchors = len(doc["grid"]["segments"])
        timeline = folder.timelines.get(tid)
        tl = "no timeline"
        if timeline is not None:
            tl = "timeline" + ("" if timeline.get("grid_rev") in
                               (None, doc["grid"].get("rev"))
                               else " ON AN OLD GRID")
        sigs = len((doc.get("ids") or {}).get("blt_signatures") or ())
        lines.append(f"  {tid:<32} {ident.get('artist', '')} - {ident['title']}"
                     f"\n  {'':<32} {anchors} grid anchor(s), {phrases} phrases, "
                     f"{'waveform' if tid in folder.waveforms else 'no waveform'}, "
                     f"{sigs} CDJ signature(s), {tl}")
    lines += [f"  ERROR  {e}" for e in folder.errors]
    lines += [f"  warn   {w}" for w in folder.warnings]
    return lines


# -- command line -------------------------------------------------------------

def _db_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--master-db", help="the database; default rekordbox_db in "
                                       "klights.local.json, else rekordbox's own")
    p.add_argument("--key", help=f"its key; better set ${masterdb.KEY_ENV} or "
                                 f"rekordbox_key in klights.local.json, so it "
                                 f"is not in your shell history")
    p.add_argument("--anlz-root", type=Path,
                   help="the folder AnalysisDataPath is under; default where "
                        "the database says, else share/ beside it")
    p.add_argument("--db", help="name for the database these rekordbox ids "
                                "belong to (default collection:<host>)")


def _emit(doc: dict) -> None:
    """One JSON document on stdout, ASCII-only so no console code page can
    mangle a title on its way to the engine."""
    sys.stdout.write(json.dumps(doc, ensure_ascii=True, separators=(",", ":")))
    sys.stdout.write("\n")


def _open_collection(args) -> masterdb.Collection:
    path, key = masterdb.resolve(args.master_db, args.key, sf.read_local_config())
    return masterdb.read(path, key, args.anlz_root)


def main(argv: Optional[list[str]] = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        # A title in kana must not kill a run whose output is piped.
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="backslashreplace")
    parser = argparse.ArgumentParser(
        prog="prep.py", description=__doc__.split("\n")[0])
    parser.add_argument("--show-dir", help="defaults to $KLIGHTS_SHOW_DIR or "
                                           "klights.local.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would change and write nothing")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_db = sub.add_parser("db", help="tracks from rekordbox's own database "
                                     "(master.db); with nothing chosen, lists "
                                     "the playlists")
    _db_args(p_db)
    p_db.add_argument("--playlist", action="append", default=[],
                      help='a playlist or folder, by name or path ("Gigs/Friday"); '
                           'repeatable')
    p_db.add_argument("--id", type=int, action="append", default=[], dest="ids",
                      help="a track's rekordbox id; repeatable")
    p_db.add_argument("--search", help="tracks whose title, artist and album "
                                       "hold every word of this")
    p_db.add_argument("--all", action="store_true",
                      help="every track in the collection")
    p_db.add_argument("--list", action="store_true",
                      help="say which tracks were chosen, and prep nothing")
    p_db.add_argument("--json", action="store_true",
                      help="a JSON summary on stdout, for a program")
    p_cat = sub.add_parser("catalogue", help="the collection's playlists and "
                                             "tracks, as JSON, for the designer")
    _db_args(p_cat)
    p_xml = sub.add_parser("xml", help="tracks from a rekordbox XML export")
    p_xml.add_argument("xml", type=Path)
    p_xml.add_argument("--anlz-root", type=Path, action="append", default=[],
                       help="a folder of ANLZ files (repeatable)")
    p_xml.add_argument("--playlist", help="only this playlist's tracks")
    p_xml.add_argument("--title", help="only tracks whose title contains this")
    p_xml.add_argument("--db", help="name for the database these rekordbox ids "
                                    "belong to (default collection:<host>)")
    p_syn = sub.add_parser("synthetic", help="the track bridge.py --fake plays")
    p_syn.add_argument("--bpm", type=float, default=128.0)
    sub.add_parser("report", help="what is in the folder")
    args = parser.parse_args(argv)
    host = socket.gethostname()
    as_json = getattr(args, "json", False)

    def fail(text: str, code: int = 1) -> int:
        if as_json:
            _emit({"error": text})
        else:
            print(text, file=sys.stderr)
        return code

    coll = chosen = None
    if args.cmd in ("db", "catalogue"):
        try:
            coll = _open_collection(args)
            if args.cmd == "catalogue":
                _emit(catalogue(coll, args.db or f"collection:{host}"))
                return 0
            if args.playlist or args.ids or args.search or args.all:
                chosen = select_db(coll, args.playlist, args.ids, args.search,
                                   args.all)
        except (masterdb.DbError, PrepError) as exc:
            return fail(f"{args.cmd} failed: {exc}")
        if chosen is None:
            if as_json:
                return fail("choose tracks with --playlist, --search, --id or --all", 2)
            print(f"{coll.db_path}  (rekordbox {coll.version or 'version unknown'}, "
                  f"{len(coll.tracks)} tracks)")
            for line in playlist_tree(coll):
                print(line)
            print("choose tracks with --playlist, --search, --id or --all; "
                  "--list shows them without prepping", file=sys.stderr)
            return 2
        if args.list:
            for t in chosen:
                length = (f"{t.duration_s // 60}:{t.duration_s % 60:02d}"
                          if t.duration_s else "?")
                flags = "" if t.analysis else "  [not analysed]"
                print(f"  {t.id:>10}  {t.artist or ''} - {t.title}  "
                      f"({t.bpm or '?'} bpm, {length}){flags}")
            print(f"  {len(chosen)} track(s)")
            return 0

    root = sf.resolve_show_dir(args.show_dir)
    if root is None:
        return fail("no show folder: pass --show-dir, set KLIGHTS_SHOW_DIR, or "
                    "set show_dir in klights.local.json", 2)
    if args.cmd != "report" and not args.dry_run and not root.is_dir():
        sf.init(root)

    if args.cmd == "report":
        for line in report(root):
            print(line)
        return 0

    try:
        if args.cmd == "synthetic":
            prepared = [synthetic(args.bpm)]
        elif args.cmd == "db":
            db = args.db or f"collection:{host}"
            prepared, skipped, covered = [], [], set()
            for t in chosen:
                if t.id in covered:
                    continue                # a copy of a row already prepped
                covered.update(row.id for row in coll.copies(t))
                try:
                    prepared.append(prepare_db_track(t, coll, db, host))
                except (PrepError, anlz.AnlzError, tracktime.GridError) as exc:
                    skipped.append({"rekordbox_id": t.id, "title": t.title,
                                    "artist": t.artist or "", "reason": str(exc)})
                    if not as_json:
                        print(f"  skipped    {t.artist or ''} - {t.title}: {exc}")
            if as_json:
                outcomes: list = []
                apply(root, prepared, args.dry_run, outcomes=outcomes)
                _emit({"results": outcomes, "skipped": skipped,
                       "show_dir": str(root)})
                return 0
        else:
            tracks, playlists = parse_xml(args.xml)
            if args.playlist is not None:
                if args.playlist not in playlists:
                    print(f"no playlist {args.playlist!r}; the XML has: "
                          + ", ".join(sorted(playlists)), file=sys.stderr)
                    return 2
                wanted = set(playlists[args.playlist])
                tracks = [t for t in tracks if t.track_id in wanted]
            if args.title:
                needle = tracksmod.normalize(args.title)
                tracks = [t for t in tracks
                          if needle in tracksmod.normalize(t.identity["title"])]
            index = scan_anlz(args.anlz_root)
            for problem in index.problems:
                print(f"  warn   {problem}")
            db = args.db or f"collection:{host}"
            prepared = []
            for t in tracks:
                try:
                    prepared.append(prepare_xml_track(t, index, db, host,
                                                      args.xml.name))
                except (PrepError, anlz.AnlzError, tracktime.GridError) as exc:
                    print(f"  skipped    {t.identity.get('artist', '')} - "
                          f"{t.identity.get('title', '')}: {exc}")
        for line in apply(root, prepared, args.dry_run):
            print(line)
    except (PrepError, configmod.ConfigError, sf.StaleEdit) as exc:
        return fail(f"prep failed: {exc}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
