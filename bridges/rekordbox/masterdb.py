"""
rekordbox's own collection database, `master.db`, read-only.

It answers what the XML export did -- who each track is, where its file is,
which playlists hold it -- without File > Export Collection first, and it adds
the one thing the XML cannot: `AnalysisDataPath`, which names each track's
analysis file outright, so nothing has to be joined by guessing from paths.

**Encrypted, and that is why this is in `bridges/`.** rekordbox 6 and 7
encrypt master.db with SQLCipher 4. Opening it needs the `sqlcipher3` package
and the key, and neither may enter the engine, which is stdlib-only so a show
laptop needs no pip. The key is not stored in this repository: it comes from
`--key`, `$RB_CIPHER_KEY` (the name the sorter project uses, so one setting
serves both) or `rekordbox_key` in klights.local.json. A decrypted copy, such
as the sorter's working copy, opens with the stdlib alone and needs no key.

**Never written.** The connection is opened `mode=ro`. rekordbox may be running
with the database open; a read-only reader takes only shared locks, the same
as rekordbox's own readers, and waits a few seconds rather than failing if
rekordbox is mid-write.

Schema notes, checked against a rekordbox 7.2.14 collection:

- `djmdContent` is the collection. `ID` is a decimal string; `BPM` is x100;
  `Length` is whole seconds; `FolderPath` is the FULL path of the file, or a
  streaming reference (`/v4/catalog/...`, `spotify:...`) with `FileSize` 0.
  Streaming tracks are analysed too, so they can be prepped; they have no file
  for the designer to play.
- `rb_local_deleted = 1` marks a row rekordbox has deleted but not yet purged.
- `djmdPlaylist.Attribute`: 0 playlist, 1 folder, 4 smart playlist. A smart
  playlist's tracks are a query rekordbox evaluates (`SmartList`), not rows,
  so it is listed with no tracks. `ParentID` is the literal `root` at the top.
- **Cues are only here.** A rekordbox 6/7 collection's analysis files carry an
  empty cue list (520 tracks with cues in the database, none in their ANLZ
  files); only a USB export writes them into the ANLZ. `djmdCue.Kind` is 0
  for a memory cue and 4 for a memory loop; hot cues skip 4, so 1-3 are A-C
  and 5-9 are D-H, on into the 16 pads of a CDJ-3000. (Kind 4 being skipped
  is what CueGen, which writes this table, does: `if (kind == 4) kind++`.)
"""

from __future__ import annotations

import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

REPO = Path(__file__).resolve().parent.parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine import tracks as tracksmod  # noqa: E402

KEY_ENV = "RB_CIPHER_KEY"

SQLITE_HEADER = b"SQLite format 3\x00"

# A file on this machine: a Windows drive path, or an absolute POSIX one that
# is not one of rekordbox's streaming references.
_DRIVE = re.compile(r"^[A-Za-z]:[/\\]")
_VERSION = re.compile(r"rekordbox (\d+(?:\.\d+)+)")

# djmdCue.Kind -> hot cue number (1 = A, 0 = a memory cue). Hot cues skip 4,
# which is a memory loop; see above.
_HOT_FROM_KIND = {0: 0, 1: 1, 2: 2, 3: 3, 4: 0}
_HOT_FROM_KIND.update({k: k - 1 for k in range(5, 18)})

ATTRIBUTE = {0: "playlist", 1: "folder", 4: "smart"}


class DbError(ValueError):
    """master.db could not be read, with what to do about it."""


@dataclass(frozen=True)
class Track:
    id: int
    title: str
    artist: Optional[str]           # None: the track has no artist at all
    album: str
    genre: str
    key: str
    bpm: Optional[float]
    duration_s: Optional[int]
    path: str
    size: Optional[int]
    analysis: str                   # AnalysisDataPath, or "" if never analysed
    added: str

    @property
    def local(self) -> bool:
        """A file on this machine, not a streaming service's track."""
        if not self.size:
            return False
        return bool(_DRIVE.match(self.path)) or (
            self.path.startswith("/") and not self.path.startswith("/v4/"))


@dataclass(frozen=True)
class DbCue:
    hot: int                        # 0 a memory cue, 1 = A, ...
    loop: bool
    time_ms: int
    name: str


@dataclass(frozen=True)
class Playlist:
    id: str
    name: str
    parent: Optional[str]           # None at the top level
    kind: str                       # playlist | folder | smart
    tracks: tuple[int, ...] = ()


@dataclass
class Collection:
    db_path: Path
    anlz_root: Path
    tracks: dict[int, Track] = field(default_factory=dict)
    playlists: list[Playlist] = field(default_factory=list)   # tree order
    cues: dict[int, list[DbCue]] = field(default_factory=dict)
    version: Optional[str] = None   # the rekordbox that wrote it, if it says
    _by_path: Optional[dict] = field(default=None, repr=False)

    def copies(self, track: Track) -> list[Track]:
        """Every row that is this track: the same file under the same title,
        artist and album -- a file imported twice, which rekordbox keeps as two
        rows, often with the cues on only one. Lowest id first; includes
        `track`. Rows that differ in name stay apart: a deck reports the name,
        so they are different tracks to the matcher too."""
        if not track.path:
            return [track]
        if self._by_path is None:
            self._by_path = {}
            for t in self.tracks.values():
                self._by_path.setdefault(t.path, []).append(t)

        def key(t: Track):
            return (tracksmod.identity_key({"title": t.title, "artist": t.artist or "",
                                            "album": t.album}), t.duration_s)
        mine = key(track)
        return sorted((t for t in self._by_path.get(track.path, [track])
                       if key(t) == mine), key=lambda t: t.id)

    def analysis_file(self, track: Track) -> Optional[Path]:
        """The track's ANLZ0000.DAT, if rekordbox analysed it."""
        if not track.analysis:
            return None
        return self.anlz_root / track.analysis.lstrip("/\\")

    def paths(self) -> dict[str, Playlist]:
        """Every playlist and folder by its path, "Folder/Sub/Name"."""
        by_id = {p.id: p for p in self.playlists}
        out: dict[str, Playlist] = {}
        for p in self.playlists:
            names, at, seen = [p.name], p.parent, {p.id}
            while at is not None and at in by_id and at not in seen:
                seen.add(at)
                names.append(by_id[at].name)
                at = by_id[at].parent
            out["/".join(reversed(names))] = p
        return out

    def find_playlist(self, wanted: str) -> Playlist:
        """By path ("Gigs/Friday") or, when it is unique, by bare name --
        exactly first, then ignoring case and the stray spaces rekordbox
        keeps in names ("Despacio " is a real one)."""
        paths = self.paths()
        loose = lambda s: "/".join(part.strip().casefold() for part in s.split("/"))
        for same in (lambda a, b: a == b, lambda a, b: loose(a) == loose(b)):
            exact = [path for path in paths if same(path, wanted)]
            named = exact or [path for path, p in paths.items()
                              if same(p.name, wanted)]
            if len(named) == 1:
                return paths[named[0]]
            if named:
                raise DbError(f"{len(named)} playlists are called {wanted!r}; "
                              f"name one by its path: " + ", ".join(sorted(named)))
        raise DbError(f"no playlist {wanted!r} in {self.db_path.name}; "
                      f"`prep.py db` lists them")

    def in_playlist(self, playlist: Playlist) -> list[Track]:
        """A playlist's tracks in its order; a folder's, every playlist under it."""
        if playlist.kind != "folder":
            ids = playlist.tracks
        else:
            under = {playlist.id}
            for p in self.playlists:            # tree order: parents first
                if p.parent in under:
                    under.add(p.id)
            ids = tuple(dict.fromkeys(i for p in self.playlists
                                      if p.id in under for i in p.tracks))
        return [self.tracks[i] for i in ids if i in self.tracks]

    def search(self, text: str) -> list[Track]:
        """Tracks whose title, artist and album hold every word of `text`,
        compared the way the matcher compares names."""
        words = tracksmod.normalize(text).split()
        if not words:
            return []
        hits = []
        for t in self.tracks.values():
            hay = tracksmod.normalize(f"{t.title} {t.artist or ''} {t.album}")
            if all(w in hay for w in words):
                hits.append(t)
        return hits


# -- where it is, and the key -------------------------------------------------

def default_db_path() -> Path:
    if sys.platform == "win32":
        base = Path(os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming")
        return base / "Pioneer" / "rekordbox" / "master.db"
    return Path.home() / "Library" / "Pioneer" / "rekordbox" / "master.db"


def resolve(db: Optional[str], key: Optional[str],
            local: Mapping, env: Optional[Mapping[str, str]] = None
            ) -> tuple[Path, Optional[str]]:
    """(master.db, key): the command line, then the environment (key only),
    then klights.local.json, then rekordbox's default location."""
    env = os.environ if env is None else env
    path = db or local.get("rekordbox_db") or default_db_path()
    found = key or env.get(KEY_ENV) or local.get("rekordbox_key")
    return Path(path).expanduser(), (found if isinstance(found, str) and found
                                     else None)


# -- opening ------------------------------------------------------------------

def connect(path: Path, key: Optional[str]):
    """A read-only connection: the stdlib's for a decrypted copy, sqlcipher3's
    for the real, encrypted one."""
    path = Path(path)
    try:
        with open(path, "rb") as fh:
            header = fh.read(16)
    except FileNotFoundError:
        raise DbError(f"no rekordbox database at {path}: is rekordbox installed "
                      f"here? Point at it with --master-db or rekordbox_db in "
                      f"klights.local.json") from None
    except OSError as exc:
        raise DbError(f"cannot read {path}: {exc}") from None
    uri = path.resolve().as_uri() + "?mode=ro"
    if header == SQLITE_HEADER:
        return sqlite3.connect(uri, uri=True, timeout=5)
    if not key:
        raise DbError(f"{path.name} is encrypted and no key was given: set "
                      f"{KEY_ENV}, or rekordbox_key in klights.local.json, or "
                      f"pass --key")
    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError:
        raise DbError("reading the encrypted master.db needs the sqlcipher3 "
                      "package: pip install sqlcipher3 (only for this bridge; "
                      "the engine never imports it)") from None
    conn = sqlcipher.connect(uri, uri=True, timeout=5)
    conn.execute("PRAGMA key = '" + key.replace("'", "''") + "'")
    conn.execute("PRAGMA cipher_compatibility = 4")
    try:
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
    except sqlcipher.DatabaseError:
        conn.close()
        raise DbError(f"the key does not open {path.name}. Every rekordbox 6 and "
                      f"7 so far has used one key; a rekordbox that changed it "
                      f"needs the new one") from None
    return conn


# -- reading ------------------------------------------------------------------

_TRACKS = """
SELECT c.ID, c.Title, c.ArtistID, a.Name, al.Name, g.Name, k.ScaleName, c.BPM,
       c.Length, c.FolderPath, c.FileSize, c.AnalysisDataPath, c.DateCreated
FROM djmdContent c
LEFT JOIN djmdArtist a ON a.ID = c.ArtistID
LEFT JOIN djmdAlbum al ON al.ID = c.AlbumID
LEFT JOIN djmdGenre g ON g.ID = c.GenreID
LEFT JOIN djmdKey k ON k.ID = c.KeyID
WHERE c.rb_local_deleted = 0
"""


def _int(value) -> Optional[int]:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def read(path: Path, key: Optional[str] = None,
         anlz_root: Optional[Path] = None) -> Collection:
    """The whole collection: tracks, playlists in tree order, cues."""
    conn = connect(path, key)
    try:
        return _read(conn, Path(path), anlz_root)
    except sqlite3.Error as exc:
        raise DbError(f"{Path(path).name} is not a rekordbox collection this "
                      f"reader knows: {exc}") from None
    except Exception as exc:                            # noqa: BLE001
        # sqlcipher3's errors are its own classes, not sqlite3's.
        if type(exc).__module__.startswith("sqlcipher3"):
            raise DbError(f"{Path(path).name}: {exc}") from None
        raise
    finally:
        conn.close()


def _read(conn, path: Path, anlz_root: Optional[Path]) -> Collection:
    registry = {}
    try:
        registry = {r[0]: r[1] for r in conn.execute(
            "SELECT registry_id, str_1 FROM agentRegistry")}
    except Exception:                                   # noqa: BLE001
        pass                    # an old or stripped copy: defaults below
    version = None
    m = _VERSION.search(registry.get("LangPath") or "")
    if m:
        version = m.group(1)
    if anlz_root is None:
        # Where rekordbox keeps its analysis files: it records the folder, and
        # it is otherwise `share` beside the database.
        recorded = registry.get("SyncAnalysisDataRootPath")
        anlz_root = (Path(recorded) if recorded and Path(recorded).is_dir()
                     else path.parent / "share")
    coll = Collection(db_path=path, anlz_root=Path(anlz_root), version=version)

    for (rid, title, artist_id, artist, album, genre, key, bpm, length,
         folder, size, analysis, added) in conn.execute(_TRACKS):
        tid = _int(rid)
        if tid is None or tid < 0 or tid >= 2 ** 32:
            continue            # not representable as a deck's rekordbox id
        coll.tracks[tid] = Track(
            id=tid, title=title or "",
            artist=artist if (artist_id and artist is not None) else None,
            album=album or "", genre=genre or "", key=key or "",
            bpm=(bpm / 100.0) if bpm else None, duration_s=_int(length),
            path=(folder or "").replace("\\", "/"), size=_int(size),
            analysis=analysis or "", added=(added or "")[:10])

    rows = conn.execute("SELECT ID, Name, ParentID, Attribute, Seq FROM djmdPlaylist "
                        "WHERE rb_local_deleted = 0").fetchall()
    members: dict[str, list[int]] = {}
    for pid, cid in conn.execute(
            "SELECT PlaylistID, ContentID FROM djmdSongPlaylist "
            "WHERE rb_local_deleted = 0 ORDER BY PlaylistID, TrackNo"):
        tid = _int(cid)
        if tid in coll.tracks:
            members.setdefault(str(pid), []).append(tid)
    children: dict[Optional[str], list] = {}
    known = {str(r[0]) for r in rows}
    for pid, name, parent, attribute, seq in rows:
        parent = str(parent) if parent not in (None, "", "root") else None
        if parent is not None and parent not in known:
            parent = None       # an orphan: shown at the top rather than lost
        children.setdefault(parent, []).append((seq or 0, name or "", str(pid),
                                                attribute))

    def walk(parent: Optional[str], seen: set) -> None:
        for _, name, pid, attribute in sorted(children.get(parent, ())):
            if pid in seen:
                continue
            seen.add(pid)
            kind = ATTRIBUTE.get(attribute, "playlist")
            coll.playlists.append(Playlist(
                id=pid, name=name, parent=parent, kind=kind,
                tracks=tuple(members.get(pid, ())) if kind == "playlist" else ()))
            walk(pid, seen)
    walk(None, set())

    for cid, kind, in_ms, out_ms, comment in conn.execute(
            "SELECT ContentID, Kind, InMsec, OutMsec, Comment FROM djmdCue "
            "WHERE rb_local_deleted = 0 ORDER BY ContentID, InMsec"):
        tid, kind = _int(cid), _int(kind)
        if tid not in coll.tracks or kind is None or in_ms is None or in_ms < 0:
            continue
        hot = _HOT_FROM_KIND.get(kind)
        if hot is None:
            continue
        coll.cues.setdefault(tid, []).append(DbCue(
            hot, bool(out_ms and out_ms > in_ms), int(in_ms), comment or ""))
    return coll
