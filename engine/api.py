"""
`GET /api/*`: the show folder, read over plain HTTP.

The designer needs whole documents -- a track's grid and phrases, a timeline,
every routine, a waveform of thousands of points -- and none of that belongs in
the 10 Hz snapshot, which every phone receives whether it wants it or not. So
large reads are pulled, here, when a screen asks; the snapshot carries only
revs and counts.

Read-only. Every write goes through a WebSocket command, so it passes the same
tier check, frame boundary and reply channel as everything else that changes
the show.

    /api/show                 show.json, counts, every problem in the folder
    /api/tracks               every prepped track, one line each
    /api/tracks/<id>          one track document and its rev
    /api/timelines/<id>       a track's timeline and its rev (404: none yet)
    /api/routines[/<id>]      routines
    /api/templates[/<id>]     template sets
    /api/waveforms/<id>       a track's waveform, read from disk on request
    /api/audio/<id>           the track's audio file, with Range (206)
    /api/rekordbox            the DJ's rekordbox collection: playlists and
                              tracks to prep from (?refresh=1 re-reads now)

JSON reads need nothing more than watching the show does. **Audio needs the
token**: it reads a file off this machine's disk and streams megabytes, so it is
for the operator's own designer, not for anyone who opened the view URL. It is
only ever a file the track document (or an `audio_roots` search) names, with an
audio extension -- never a path a request supplies. **So does the rekordbox
collection**: it is the whole of someone's music library, not the show.

Runs on the HTTP server's threads. Reads the library by one reference load,
and the library is immutable, so nothing here can disturb the output thread.
"""

from __future__ import annotations

import glob
import json
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

from . import collection as collectionmod
from . import showfiles

AUDIO_TYPES = {".mp3": "audio/mpeg", ".wav": "audio/wav", ".aif": "audio/aiff",
               ".aiff": "audio/aiff", ".flac": "audio/flac", ".m4a": "audio/mp4",
               ".mp4": "audio/mp4", ".ogg": "audio/ogg", ".aac": "audio/aac"}

_ID = r"[a-z0-9][a-z0-9_-]{0,63}"
_ROUTE = re.compile(rf"^/api/(show|tracks|timelines|routines|templates|waveforms"
                    rf"|audio|rekordbox)(?:/({_ID}))?/?$")
_RANGE = re.compile(r"^bytes=(\d*)-(\d*)$")


@dataclass
class Response:
    status: int
    body: bytes = b""
    content_type: str = "application/json"
    file: Optional[Path] = None             # stream this instead of `body`
    start: int = 0
    length: int = 0
    headers: dict = field(default_factory=dict)


def _json(obj: Any, status: int = 200) -> Response:
    return Response(status, json.dumps(obj).encode("utf-8"),
                    headers={"Cache-Control": "no-store"})


def _error(status: int, text: str) -> Response:
    return _json({"error": text}, status)


def handle(library, path: str, range_header: Optional[str] = None,
           token_ok: bool = True,
           audio_roots: Sequence[str] = (),
           collection: Optional["collectionmod.Collection"] = None,
           query: str = "") -> Response:
    """Answer one GET. `library` is the controller's current
    `showlibrary.Library`, or None without a show folder."""
    match = _ROUTE.match(path)
    if match is None:
        return _error(404, f"no such endpoint {path!r}; see engine/api.py")
    if library is None:
        return _error(503, "no show folder -- start the engine with --show-dir")
    what, ident = match.group(1), match.group(2)
    folder = library.folder

    if what == "rekordbox":
        if ident is not None:
            return _error(404, "/api/rekordbox takes no id")
        if not token_ok:
            return _error(401, "the rekordbox collection needs the engine's "
                               "token: open the designer from the URL the "
                               "engine printed")
        if collection is None:
            return _error(503, "this engine has no rekordbox bridge")
        try:
            body = collection.catalogue(refresh="refresh=1" in query.split("&"))
        except collectionmod.CollectionError as exc:
            return _error(503, str(exc))
        # The bridge's bytes as they came: parsing a megabyte here would hold
        # the GIL the DMX clock needs (engine/collection.py).
        return Response(200, body, headers={"Cache-Control": "no-store"})

    if what == "show":
        return _json({"dir": str(library.root), "rev": library.rev,
                      "show": folder.show, **library.counts,
                      "errors": folder.errors, "warnings": folder.warnings,
                      "failed": folder.failed})
    if what == "tracks" and ident is None:
        return _json({"tracks": [_track_line(library, tid, doc)
                                 for tid, doc in sorted(folder.tracks.items())]})
    if what == "routines" and ident is None:
        return _json({"routines": [
            {"id": rid, "name": doc.get("name"), "bars": doc.get("bars"),
             "loop": doc.get("loop", True), "rig": doc.get("rig"),
             "params": doc.get("params") or {},
             "variations": sorted((doc.get("variations") or {}).keys()),
             "roles": doc.get("roles") or {}}
            for rid, doc in sorted(folder.routines.items())]})
    if what == "templates" and ident is None:
        return _json({"templates": [{"id": tid, "name": doc.get("name")}
                                    for tid, doc in sorted(folder.templates.items())]})
    if ident is None:
        return _error(404, f"/api/{what} needs an id")

    if what in ("tracks", "timelines", "routines", "templates"):
        docs = {"tracks": folder.tracks, "timelines": folder.timelines,
                "routines": folder.routines, "templates": folder.templates}[what]
        doc = docs.get(ident)
        if doc is None:
            return _error(404, f"no {what[:-1]} {ident!r}")
        rel = f"{showfiles.SUBDIR[_KIND[what]]}/{ident}.json"
        return _json({"doc": doc, "rev": folder.revs.get(rel),
                       "failed": folder.failed.get(rel)})
    if what == "waveforms":
        result, rev = showfiles.read_doc(
            showfiles.path_for(library.root, "waveform", ident), "waveform")
        if result.doc is None or not result.ok:
            return _error(404, result.errors[0] if result.errors
                          else f"no waveform for {ident!r}")
        return _json({"doc": result.doc, "rev": rev})
    # audio
    if not token_ok:
        return _error(401, "audio needs the engine's token: open the designer "
                           "from the URL the engine printed")
    doc = folder.tracks.get(ident)
    if doc is None:
        return _error(404, f"no track {ident!r}")
    found = find_audio(doc, audio_roots)
    if found is None:
        return _error(404, f"no audio file for {ident!r} on this machine: prep "
                           f"it here, or add its folder to audio_roots in "
                           f"klights.local.json -- or open the file in the "
                           f"designer")
    return _file(found, range_header)


_KIND = {"tracks": "track", "timelines": "timeline", "routines": "routine",
         "templates": "template_set"}


def _track_line(library, tid: str, doc: dict) -> dict:
    ident = doc.get("identity") or {}
    folder = library.folder
    return {"id": tid, "title": ident.get("title"), "artist": ident.get("artist"),
            "album": ident.get("album"), "duration_s": ident.get("duration_s"),
            "bpm": ident.get("bpm"),
            "grid_rev": library.grids[tid].rev if tid in library.grids else None,
            "has_timeline": tid in folder.timelines,
            "has_waveform": tid in folder.waveforms,
            "has_audio": bool(doc.get("audio")),
            "phrases": len((doc.get("phrases") or {}).get("items") or ()),
            # Which rekordbox rows this is, so the collection browser can say
            # "in the show" -- and how many CDJ signatures it answers to.
            "rekordbox": [{"db": r.get("db"), "id": r.get("id")}
                          for r in (doc.get("ids") or {}).get("rekordbox") or ()],
            "signatures": len((doc.get("ids") or {}).get("blt_signatures") or ()),
            "rev": folder.revs.get(f"tracks/{tid}.json")}


# Where each track's audio turned out to be. A browser playing a file asks for
# it in Range requests, several a second while it buffers and seeks, and each
# one would otherwise search every audio root -- a whole music library -- again.
# Keyed by everything the answer depends on. A hit is re-checked on disk before
# it is trusted; a miss is remembered for MISS_TTL_S, so a file copied in
# afterwards is found without a restart. Shared by the HTTP threads: a dict
# get or set is one step, and the worst a race costs is one extra search.
_FOUND: dict[tuple, tuple[Optional[Path], float]] = {}
MISS_TTL_S = 30.0


def find_audio(doc: dict, audio_roots: Sequence[str] = ()) -> Optional[Path]:
    """The track's audio file on this machine, remembered (see `_FOUND`)."""
    key = (json.dumps(doc.get("audio"), sort_keys=True, default=str),
           tuple(audio_roots))
    hit = _FOUND.get(key)
    if hit is not None:
        path, at = hit
        if path is not None and path.is_file():
            return path
        if path is None and time.monotonic() - at < MISS_TTL_S:
            return None
    found = _search_audio(doc, audio_roots)
    if len(_FOUND) > 256:
        _FOUND.clear()
    _FOUND[key] = (found, time.monotonic())
    return found


def _search_audio(doc: dict, audio_roots: Sequence[str]) -> Optional[Path]:
    """A path the track document names, if it is there; else the same file
    name under one of this machine's `audio_roots` -- a library moved to
    another drive, or the design machine's copy of the show laptop's music.
    Only files with an audio extension."""
    names = []
    for entry in doc.get("audio") or ():
        raw = entry.get("path") if isinstance(entry, dict) else None
        if not isinstance(raw, str) or not raw:
            continue
        path = Path(raw).expanduser()
        if path.suffix.lower() not in AUDIO_TYPES:
            continue
        if path.is_file():
            return path
        names.append(Path(raw.replace("\\", "/")).name)
    for root in audio_roots:
        base = Path(root).expanduser()
        if not base.is_dir():
            continue
        for name in names:
            direct = base / name
            if direct.is_file():
                return direct
            # A NAME, not a pattern: "Night Drive [Extended Mix].mp3" has a
            # glob character class in it, which would match anything but
            # itself.
            for hit in base.rglob(glob.escape(name)):
                if hit.is_file() and hit.suffix.lower() in AUDIO_TYPES:
                    return hit
    return None


def _file(path: Path, range_header: Optional[str]) -> Response:
    size = path.stat().st_size
    ctype = AUDIO_TYPES.get(path.suffix.lower(), "application/octet-stream")
    headers = {"Accept-Ranges": "bytes", "Cache-Control": "no-store"}
    if range_header:
        m = _RANGE.match(range_header.strip())
        if m is None or (not m.group(1) and not m.group(2)):
            return Response(416, b"", ctype,
                            headers={"Content-Range": f"bytes */{size}"})
        if m.group(1):
            start = int(m.group(1))
            end = int(m.group(2)) if m.group(2) else size - 1
        else:                                   # the last N bytes
            start = max(0, size - int(m.group(2)))
            end = size - 1
        end = min(end, size - 1)
        if start > end or start >= size:
            return Response(416, b"", ctype,
                            headers={"Content-Range": f"bytes */{size}"})
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        return Response(206, content_type=ctype, file=path, start=start,
                        length=end - start + 1, headers=headers)
    return Response(200, content_type=ctype, file=path, start=0, length=size,
                    headers=headers)
