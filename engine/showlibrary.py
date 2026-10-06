"""
The show folder as the running engine sees it: loaded off the output thread,
swapped in whole, and never swapped under a playing track.

`showfiles.py` knows what a valid document is and how to read and write one.
This is the part that lives inside a running show:

- **`Library`** is one load of the folder -- every valid document, the match
  index over its tracks, each track's grid and its timeline compiled for
  querying (`timeline.py`), built ONCE. It is frozen: the
  output thread, the 10 Hz snapshot and a future HTTP read can all hold one at
  the same time, because nobody can change it. A reload builds a new one.

- **`Watcher`** notices the folder changed -- a save from the designer, a sync
  from the desktop, an MCP write -- by polling each file's size and mtime about
  once a second, on its own thread. Polling, not OS notifications, because the
  folder is usually a Dropbox/OneDrive/NAS mount and those are exactly where
  file notifications are least reliable; stat-ing a few hundred small files a
  second costs nothing. A change is acted on once it has held still for one
  poll, so a sync landing twenty files is one reload, not twenty.

  The watcher only notices. The engine hands the load to its worker
  (`worker.py`) and installs the result on the output thread by assigning one
  reference. Parsing JSON never happens where frames are drawn.

- **`Pinned`** is what the playing track was matched to, against WHICH library.
  This is the rule the folder's hot reload rests on: a reload never changes
  the track that is playing. A timeline saved mid-song, or a link made by hand,
  applies from that track's next play. Swapping the grid or the timeline under
  a playing track would move every cue relative to the music mid-phrase, which
  is the one thing a timecoded show must never do; a beat late is invisible,
  a jump is not.

- **`link`** records a manual match as an alias on a prepped track: the
  description the deck sent, which the matcher then accepts (`tracks.py`).

Nothing here touches the lights.
"""

from __future__ import annotations

import hashlib
import json
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Optional

from . import showfiles
from . import timeline as timelinemod
from . import tracks as tracksmod
from . import tracktime

POLL_S = 1.0

Signature = tuple[tuple[str, int, int], ...]


# -- one load -----------------------------------------------------------------

@dataclass(frozen=True)
class Library:
    """One load of a show folder. Immutable; replace, never edit."""
    root: Path
    folder: showfiles.Folder
    index: tracksmod.TrackIndex
    grids: Mapping[str, tracktime.Grid]
    rev: str                         # changes when any loaded document does
    timelines: Mapping[str, timelinemod.Timeline] = field(default_factory=dict)
    signature: Signature = ()        # the files as they were when loading began

    @property
    def counts(self) -> dict:
        f = self.folder
        return {"tracks": len(f.tracks), "timelines": len(f.timelines),
                "routines": len(f.routines), "templates": len(f.templates),
                "palettes": len(f.palettes)}

    def describe(self) -> str:
        c = self.counts
        return (f"{c['tracks']} tracks, {c['timelines']} timelines, "
                f"{c['routines']} routines, {c['templates']} template sets")


def scan(root: Path) -> Signature:
    """(relative path, mtime_ns, size) for every file a load reads. Cheap: one
    stat per file, no reads. A file that vanishes mid-scan is skipped -- it
    will be gone on the next scan too, or back."""
    root = Path(root)
    found: list[tuple[str, int, int]] = []
    candidates = [root / "show.json"]
    for sub in showfiles.SUBDIR.values():
        try:
            candidates.extend(sorted((root / sub).glob("*.json")))
        except OSError:
            continue
    for path in candidates:
        try:
            st = path.stat()
        except OSError:
            continue
        found.append((path.relative_to(root).as_posix(), st.st_mtime_ns,
                      st.st_size))
    return tuple(found)


def load(root: Path, previous: Optional[Library] = None) -> Library:
    """Read the folder and build everything a running show needs from it.
    Slow (it parses every document): call it on the worker. A file that was
    good in `previous` and is broken now keeps its last good version."""
    root = Path(root)
    signature = scan(root)
    folder = showfiles.load_folder(
        root, previous.folder if previous is not None else None)
    grids: dict[str, tracktime.Grid] = {}
    for track_id, doc in folder.tracks.items():
        try:
            grids[track_id] = tracktime.Grid.from_segments(doc["grid"]["segments"])
        except (tracktime.GridError, KeyError, TypeError):
            pass                        # validated already; belt and braces
    timelines: dict[str, timelinemod.Timeline] = {}
    for track_id, doc in folder.timelines.items():
        try:
            timelines[track_id] = timelinemod.Timeline.from_doc(
                doc, showfiles.timeline_channels)
        except timelinemod.TimelineError as exc:
            # Validated already, so this is a bug between the two modules
            # rather than a bad file -- reported where a bad file would be.
            folder.errors.append(f"timelines/{track_id}.json: {exc}")
    canon = json.dumps(sorted(folder.revs.items())).encode("utf-8")
    return Library(root=root, folder=folder,
                   index=tracksmod.TrackIndex.build(folder.tracks),
                   grids=grids, rev="l:" + hashlib.sha1(canon).hexdigest()[:8],
                   signature=signature, timelines=timelines)


def transport_settings(library: Optional[Library]) -> dict:
    """What show.json says about the DJ sources, in the transport's units."""
    show = (library.folder.show if library is not None else None) or {}
    latency = {}
    for name, src in (show.get("sources") or {}).items():
        if isinstance(src, dict) and src.get("latency_ms") is not None:
            latency[name] = float(src["latency_ms"]) / 1000.0
    out: dict = {"latency_s": latency}
    grace = (show.get("pause") or {}).get("grace_s")
    if grace is not None:
        out["grace_s"] = float(grace)
    change = (show.get("follow") or {}).get("min_track_change_s")
    if change is not None:
        out["min_track_change_s"] = float(change)
    return out


def apply_settings(transport, library: Optional[Library]) -> None:
    """Set a TrackTransport's tunables from show.json. On the output thread:
    the transport reads them there."""
    for key, value in transport_settings(library).items():
        setattr(transport, key, value)


# -- the playing track --------------------------------------------------------

@dataclass(frozen=True)
class Pinned:
    """What one track_seq was matched to. Held until the track changes, however
    many times the folder reloads meanwhile."""
    track_seq: int
    match: Optional[tracksmod.Match]         # None: nothing identified to match
    library: Optional[Library]
    track: Optional[Mapping] = None          # the prepped track document
    grid: Optional[tracktime.Grid] = None
    timeline: Optional[timelinemod.Timeline] = None   # None: templates only

    def public(self, current: Optional[Library]) -> Optional[dict]:
        if self.match is None:
            return None
        out = self.match.public()
        out["has_timeline"] = self.timeline is not None
        # The folder has changed since this track was matched. Nothing about
        # the playing track changes until its next play -- said, so a save that
        # "did nothing" is not a mystery.
        out["stale"] = current is not None and self.library is not None \
            and current.rev != self.library.rev
        return out


def pin(library: Optional[Library], sample) -> Pinned:
    """Match a transport sample's identity against `library`, once, for its
    track_seq. Dictionary lookups: fine on the output thread."""
    ident = sample.identity
    if library is None or not ident.known:
        return Pinned(sample.track_seq, None, library)
    match = library.index.match(
        title=ident.title, artist=ident.artist, album=ident.album,
        duration=ident.duration, rekordbox_id=ident.rekordbox_id,
        signature=ident.signature)
    if match.track_id is None:
        return Pinned(sample.track_seq, match, library)
    return Pinned(sample.track_seq, match, library,
                  library.folder.tracks.get(match.track_id),
                  library.grids.get(match.track_id),
                  library.timelines.get(match.track_id))


# -- linking by hand ----------------------------------------------------------

def link(root: Path, track_id: str, title: str, artist: str = "",
         album: str = "", signature: Optional[str] = None,
         added: str = "") -> str:
    """Teach a prepped track to answer to a description: an alias, and the
    deck's signature if it sent one. Reads and writes the one file, refusing if
    it changed in between. Returns what was added ("alias", "signature",
    "alias+signature", or "already" when the track already answered to it).
    File I/O: call it on the worker."""
    path = showfiles.path_for(Path(root), "track", track_id)
    result, rev = showfiles.read_doc(path, "track")
    if not result.ok:
        raise ValueError(f"tracks/{track_id}.json cannot be linked to: "
                         f"{result.errors[0] if result.errors else 'unreadable'}")
    doc = result.doc
    alias = tracksmod.alias_for(title, artist, album, added)
    added_parts = []
    if not tracksmod.has_description(doc, alias):
        doc.setdefault("aliases", []).append(alias)
        added_parts.append("alias")
    if signature:
        ids = doc.setdefault("ids", {})
        sigs = ids.setdefault("blt_signatures", [])
        if signature not in sigs:
            sigs.append(signature)
            added_parts.append("signature")
    if not added_parts:
        return "already"
    showfiles.write_doc(path, doc, "track", base_rev=rev)
    return "+".join(added_parts)


def save_latency(root: Path, source: str, ms: float) -> str:
    """Set one source's latency in show.json, keeping everything else in it.
    Writes a default show.json if the folder has none. File I/O: call it on
    the worker."""
    path = Path(root) / "show.json"
    if path.exists():
        result, rev = showfiles.read_doc(path, "show")
        if not result.ok:
            raise ValueError(f"show.json cannot be updated: {result.errors[0]}")
        doc = result.doc
    else:
        doc = showfiles.new_doc("show", **json.loads(json.dumps(
            showfiles.DEFAULT_SHOW)))
        rev = ""
    sources = doc.setdefault("sources", {})
    entry = sources.setdefault(source, {})
    entry["latency_ms"] = ms
    return showfiles.write_doc(path, doc, "show", base_rev=rev)


# -- noticing changes ---------------------------------------------------------

@dataclass
class Watcher:
    """Polls a show folder and calls `on_change()` when it has changed and then
    held still for one poll. Runs on its own daemon thread; `on_change` runs on
    that thread too, so it must only hand work on (the engine submits a load to
    its worker)."""
    root: Path
    on_change: Callable[[], None]
    report: Callable[[str], None] = lambda text: None
    interval: float = POLL_S
    seen: Optional[Signature] = None        # what the installed load read
    _pending: Optional[Signature] = None
    _stop: threading.Event = field(default_factory=threading.Event)
    _thread: Optional[threading.Thread] = None

    def start(self, seen: Optional[Signature] = None) -> None:
        if seen is not None:
            self.seen = seen
        if self.seen is None:
            self.seen = scan(self.root)
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, daemon=True,
                                        name="klights-showwatch")
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def poll(self) -> bool:
        """One look. True if it called `on_change`."""
        now = scan(self.root)
        if now == self.seen:
            self._pending = None
            return False
        if now != self._pending:
            self._pending = now          # still moving: look again next time
            return False
        self.seen, self._pending = now, None
        self.on_change()
        return True

    def _run(self) -> None:
        failing = False
        while not self._stop.wait(self.interval):
            try:
                self.poll()
                failing = False
            except Exception as exc:                        # noqa: BLE001
                # A NAS that went away. Say so once, keep looking: the share
                # coming back is a change like any other.
                if not failing:
                    self.report(f"show folder watch failed: {exc}")
                failing = True


def describe_problems(library: Library, limit: int = 5) -> list[str]:
    """The first few things wrong, errors before warnings, for the console."""
    f = library.folder
    return (list(f.errors) + list(f.warnings))[:limit]


def default_added() -> str:
    """Today, for an alias's `added`. Local date: it is for a person reading
    the file."""
    return time.strftime("%Y-%m-%d")

