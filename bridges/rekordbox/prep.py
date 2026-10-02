"""Prep tracks into a show folder: identity, beat grid, phrases, cues, waveform.

    python bridges/rekordbox/prep.py xml rekordbox.xml --anlz-root "<USBANLZ>"
    python bridges/rekordbox/prep.py xml rekordbox.xml --anlz-root E:/PIONEER/USBANLZ --playlist Friday
    python bridges/rekordbox/prep.py synthetic
    python bridges/rekordbox/prep.py report

The show folder comes from --show-dir, $KLIGHTS_SHOW_DIR or klights.local.json,
exactly as for the engine.

**What it reads.** rekordbox's XML export (File > Export Collection in xml
format) for who each track is and where its file lives, joined to rekordbox's
analysis files (ANLZ0000.DAT/.EXT) for the beat grid, the phrases and the
waveform. The join is on the audio path the analysis file records (PPTH), and
falls back to the file name, which is what makes a USB stick work: its
analysis files say `/Contents/Artist/track.mp3`, not the path on the laptop.

  - the local collection: `--anlz-root` is rekordbox's own analysis folder,
    `%APPDATA%/Pioneer/rekordbox/share/PIONEER/USBANLZ` on Windows;
  - a USB export: `--anlz-root E:/PIONEER/USBANLZ`, with the XML exported from
    the rekordbox that made the stick.

Neither needs the encrypted master.db or any package: this is stdlib, like the
engine. Reading a stick's export.pdb directly, for a stick with no rekordbox
machine to hand, is not built.

A track with no analysis file still gets the XML's beat grid and its cues,
without phrases -- the timeline still works, and templates fall back to
counting bars.

**What it writes.** `tracks/<id>.json` and `waveforms/<id>.json`, through
engine/showfiles.py, so a prepped track is validated by the same rules the
designer and MCP use. It is idempotent: a track already in the folder is
recognised (by its rekordbox id, then by title, artist, album and duration) and
updated in place, and an unchanged track is not rewritten at all. It never
touches a timeline. When a track's grid has moved since a timeline was drawn on
it, that is reported, because every cue on that timeline may now be off.
"""

from __future__ import annotations

import argparse
import base64
import copy
import datetime as dt
import socket
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
    if analysis.preview is None and analysis.detail is None:
        return None
    doc: dict = {}
    if analysis.preview is not None:
        doc["preview"] = base64.b64encode(analysis.preview).decode("ascii")
    if analysis.detail is not None:
        doc["detail"] = {"format": analysis.detail_format, "rate": DETAIL_RATE,
                         "data": base64.b64encode(analysis.detail).decode("ascii")}
    return doc


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
    return Prepared(identity=xt.identity, segments=segments, phrases=phrases,
                    cues=cues, audio=audio, rekordbox_ids=ids,
                    source={"from": "xml", "file": xml_name, "tool": TOOL},
                    waveform=waveform_from(analysis) if analysis else None,
                    notes=notes)


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
    ids.setdefault("blt_signatures", [])
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
    return doc


def _without_stamp(doc: Optional[dict]) -> Optional[dict]:
    if doc is None:
        return None
    d = copy.deepcopy(doc)
    d.get("source", {}).pop("prepped", None)
    return d


def find_existing(tracks: dict, p: Prepared) -> Optional[str]:
    """The track already in the folder that `p` describes, if any: by
    rekordbox id first, then by title, artist, album and duration."""
    if p.fixed_id and p.fixed_id in tracks:
        return p.fixed_id
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
          today: Optional[str] = None) -> list[str]:
    """Match, merge and write. Returns one line per track."""
    folder = sf.load_folder(root)
    tracks = dict(folder.tracks)
    stamp = today or dt.datetime.now().replace(microsecond=0).isoformat()
    lines: list[str] = []
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
            if (p.waveform is not None and tid not in folder.waveforms
                    and not dry_run):
                wave = sf.new_doc("waveform", track=tid, **p.waveform)
                sf.write_doc(sf.path_for(root, "waveform", tid), wave, "waveform")
                note += "\n      waveform was missing; rewritten"
            lines.append(f"  unchanged  {tid}  ({title}){note}")
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
            sf.write_doc(path, doc, "track",
                         base_rev=folder.revs.get(rel, "") if base else "")
            if p.waveform is not None:
                wave = sf.new_doc("waveform", track=tid, **p.waveform)
                sf.write_doc(sf.path_for(root, "waveform", tid), wave, "waveform")
        tracks[tid] = doc
        lines.append(f"  {'would ' + verb.rstrip('d') if dry_run else verb}"
                     f"  {tid}  ({title}){extra}{note}")
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
        lines.append(f"  {tid:<32} {ident.get('artist', '')} - {ident['title']}"
                     f"\n  {'':<32} {anchors} grid anchor(s), {phrases} phrases, "
                     f"{'waveform' if tid in folder.waveforms else 'no waveform'}, "
                     f"{tl}")
    lines += [f"  ERROR  {e}" for e in folder.errors]
    lines += [f"  warn   {w}" for w in folder.warnings]
    return lines


# -- command line -------------------------------------------------------------

def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="prep.py", description=__doc__.split("\n")[0])
    parser.add_argument("--show-dir", help="defaults to $KLIGHTS_SHOW_DIR or "
                                           "klights.local.json")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would change and write nothing")
    sub = parser.add_subparsers(dest="cmd", required=True)
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

    root = sf.resolve_show_dir(args.show_dir)
    if root is None:
        print("no show folder: pass --show-dir, set KLIGHTS_SHOW_DIR, or set "
              "show_dir in klights.local.json", file=sys.stderr)
        return 2
    if args.cmd != "report" and not args.dry_run and not root.is_dir():
        sf.init(root)

    if args.cmd == "report":
        for line in report(root):
            print(line)
        return 0

    host = socket.gethostname()
    try:
        if args.cmd == "synthetic":
            prepared = [synthetic(args.bpm)]
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
        print(f"prep failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
