"""
Tests for reading rekordbox's own database: the master.db reader, the `db` and
`catalogue` routes of the prep tool, the beat-link signature it computes, and
the engine's side -- the child-process wrapper, `GET /api/rekordbox` and the
`rekordbox_prep` command.

The database here is a plain SQLite file with the real schema's table and
column names (checked against a rekordbox 7.2.14 collection), so it opens with
the stdlib. The encrypted path is tested too, but only where `sqlcipher3` is
installed: CI installs nothing, on purpose, and the engine never needs it.

The analysis files are built byte by byte, independently of anlz.py, as in
test_prep.py. The signature is checked against a hash built here straight
from beat-link's `computeTrackSignature`, not by calling prep's.

Run: python engine/tests/test_rekordbox.py
"""

import base64
import contextlib
import hashlib
import io
import json
import shutil
import sqlite3
import struct
import subprocess
import sys
import tempfile
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bridges" / "rekordbox"))

import anlz  # noqa: E402
import masterdb  # noqa: E402
import prep  # noqa: E402
from engine import api as apimod  # noqa: E402
from engine import collection as collectionmod  # noqa: E402
from engine import server as servermod  # noqa: E402
from engine import showfiles as sf  # noqa: E402
from engine import showlibrary  # noqa: E402
from engine import tracks as tracksmod  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# -- an independent ANLZ writer (see test_prep.py) ----------------------------

def tag(fourcc: bytes, head: bytes, payload: bytes = b"") -> bytes:
    body = head + payload
    return fourcc + struct.pack(">II", 12 + len(head), 12 + len(body)) + body


def anlz_file(*tags: bytes) -> bytes:
    rest = b"".join(tags)
    return b"PMAI" + struct.pack(">II", 28, 28 + len(rest)) + b"\0" * 16 + rest


def pqtz(beats) -> bytes:
    payload = b"".join(struct.pack(">HHI", n, round(bpm * 100), t) for n, bpm, t in beats)
    return tag(b"PQTZ", struct.pack(">III", 0, 0x80000, len(beats)), payload)


def ppth(path: str) -> bytes:
    raw = path.encode("utf-16-be") + b"\0\0"
    return tag(b"PPTH", struct.pack(">I", len(raw)), raw)


def pwav(data: bytes) -> bytes:
    return tag(b"PWAV", struct.pack(">II", len(data), 0x10000), data)


def pwv3(data: bytes) -> bytes:
    return tag(b"PWV3", struct.pack(">III", 1, len(data), 0x960000), data)


def pwv5(data: bytes) -> bytes:
    """Color detail: two bytes an entry."""
    return tag(b"PWV5", struct.pack(">III", 2, len(data) // 2, 0x960305), data)


MASK_BASE = [0xCB, 0xE1, 0xEE, 0xFA, 0xE5, 0xEE, 0xAD, 0xEE, 0xE9, 0xD2,
             0xE9, 0xEB, 0xE1, 0xE9, 0xF3, 0xE8, 0xE9, 0xF4, 0xE1]


def pssi(mood, end_beat, entries) -> bytes:
    body = struct.pack(">H6xH2xBx", mood, end_beat, 3)
    for index, beat, kind, k1, k2, k3 in entries:
        body += (struct.pack(">HHH", index, beat, kind) + bytes([0, k1, 0, k2, 0, 0])
                 + struct.pack(">HHH", 0, 0, 0) + bytes([0, k3, 0, 0])
                 + struct.pack(">H", 0))
    n = len(entries)
    body = bytes(b ^ ((MASK_BASE[i % 19] + n) % 256) for i, b in enumerate(body))
    return tag(b"PSSI", struct.pack(">IH", 24, n), body)


EMPTY_PCO2 = tag(b"PCO2", struct.pack(">IH2x", 1, 0))   # what a collection has


def steady_beats(bpm, first_ms, count, first_number=1):
    return [((first_number - 1 + i) % 4 + 1, bpm, round(first_ms + i * 60000 / bpm))
            for i in range(count)]


BEATS = steady_beats(124.0, 100, 200, first_number=3)
PHRASES = [(1, 3, 1, 1, 0, 0), (2, 67, 2, 0, 0, 1), (3, 99, 5, 0, 0, 0)]
WAVE = bytes(range(200)) * 2
DETAIL = bytes(range(256)) * 4


def expected_signature(title: bytes, artist: bytes, duration: int, detail: bytes,
                       beats) -> str:
    """beat-link's SignatureFinder.computeTrackSignature, transcribed: SHA-1 of
    title, 0, artist, 0, duration, the PWV5 entries, then per beat its bar
    position and time -- each integer four bytes, big-endian."""
    h = hashlib.sha1(title + b"\x00" + artist + b"\x00" + duration.to_bytes(4, "big")
                     + detail)
    for number, _, time_ms in beats:
        h.update(number.to_bytes(4, "big") + time_ms.to_bytes(4, "big"))
    return h.hexdigest()


# -- a master.db ---------------------------------------------------------------

SCHEMA = """
CREATE TABLE djmdContent (ID VARCHAR(255) PRIMARY KEY, FolderPath VARCHAR(255),
  Title VARCHAR(255), ArtistID VARCHAR(255), AlbumID VARCHAR(255),
  GenreID VARCHAR(255), BPM INTEGER, Length INTEGER, KeyID VARCHAR(255),
  AnalysisDataPath VARCHAR(255), FileSize INTEGER, DateCreated VARCHAR(255),
  rb_local_deleted TINYINT(1) DEFAULT 0);
CREATE TABLE djmdArtist (ID VARCHAR(255) PRIMARY KEY, Name VARCHAR(255));
CREATE TABLE djmdAlbum (ID VARCHAR(255) PRIMARY KEY, Name VARCHAR(255));
CREATE TABLE djmdGenre (ID VARCHAR(255) PRIMARY KEY, Name VARCHAR(255));
CREATE TABLE djmdKey (ID VARCHAR(255) PRIMARY KEY, ScaleName VARCHAR(255));
CREATE TABLE djmdPlaylist (ID VARCHAR(255) PRIMARY KEY, Seq INTEGER,
  Name VARCHAR(255), Attribute INTEGER, ParentID VARCHAR(255),
  SmartList TEXT, rb_local_deleted TINYINT(1) DEFAULT 0);
CREATE TABLE djmdSongPlaylist (ID VARCHAR(255) PRIMARY KEY,
  PlaylistID VARCHAR(255), ContentID VARCHAR(255), TrackNo INTEGER,
  rb_local_deleted TINYINT(1) DEFAULT 0);
CREATE TABLE djmdCue (ID VARCHAR(255) PRIMARY KEY, ContentID VARCHAR(255),
  InMsec INTEGER, OutMsec INTEGER, Kind INTEGER, Comment VARCHAR(255),
  rb_local_deleted TINYINT(1) DEFAULT 0);
CREATE TABLE agentRegistry (registry_id VARCHAR(255) PRIMARY KEY,
  str_1 VARCHAR(255));
"""

ANLZ_NIGHT = "/PIONEER/USBANLZ/0a1/0001-night/ANLZ0000.DAT"
ANLZ_STREAM = "/PIONEER/USBANLZ/0b2/0002-stream/ANLZ0000.DAT"
ANLZ_LONE = "/PIONEER/USBANLZ/0c3/0003-lone/ANLZ0000.DAT"
ANLZ_BLUE = "/PIONEER/USBANLZ/0d4/0004-blue/ANLZ0000.DAT"


def write_anlz(share: Path, rel: str, audio: str, beats=BEATS, detail=pwv5(DETAIL)):
    dat = share / rel.lstrip("/")
    dat.parent.mkdir(parents=True, exist_ok=True)
    dat.write_bytes(anlz_file(ppth(audio), pqtz(beats), pwav(WAVE)))
    dat.with_suffix(".EXT").write_bytes(anlz_file(
        ppth(audio), pssi(1, 163, PHRASES), detail, EMPTY_PCO2))


def make_db(path: Path, share: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    conn.executemany("INSERT INTO djmdArtist VALUES (?, ?)",
                     [("1", "Kölsch & Friend"), ("2", "Streamer")])
    conn.executemany("INSERT INTO djmdAlbum VALUES (?, ?)", [("1", "Night EP")])
    conn.executemany("INSERT INTO djmdKey VALUES (?, ?)", [("1", "8A")])
    night = "C:/Music/Night Drive.mp3"
    conn.executemany(
        "INSERT INTO djmdContent (ID, FolderPath, Title, ArtistID, AlbumID, BPM, "
        "Length, KeyID, AnalysisDataPath, FileSize, DateCreated, rb_local_deleted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", [
            ("101", night, "Night Drive (Extended Mix)", "1", "1", 12400, 400, "1",
             ANLZ_NIGHT, 9000000, "2026-01-02", 0),
            # the same file imported twice: two rows, one analysis
            ("102", night, "Night Drive (Extended Mix)", "1", "1", 12400, 400, "1",
             ANLZ_NIGHT, 9000000, "2026-01-03", 0),
            ("103", "/v4/catalog/track/9", "Streamed", "2", None, 12800, 300, None,
             ANLZ_STREAM, 0, "2026-02-01", 0),
            ("104", "C:/Music/raw.mp3", "Never Analysed", "2", None, 0, 200, None,
             "", 1000, "2026-02-02", 0),
            ("105", "C:/Music/gone.mp3", "Deleted", "2", None, 12000, 200, None,
             "", 1000, "2026-02-03", 1),
            ("106", "C:/Music/lone.mp3", "Nobody's Tune", None, None, 12400, 400,
             None, ANLZ_LONE, 5000, "2026-02-04", 0),
            ("107", "C:/Music/blue.mp3", "Blue Only", "2", None, 12400, 400, None,
             ANLZ_BLUE, 5000, "2026-02-05", 0),
        ])
    conn.executemany(
        "INSERT INTO djmdPlaylist (ID, Seq, Name, Attribute, ParentID, "
        "rb_local_deleted) VALUES (?, ?, ?, ?, ?, ?)", [
            ("10", 1, "Gigs", 1, "root", 0),
            ("11", 1, "Friday ", 0, "10", 0),       # rekordbox keeps the space
            ("12", 2, "Friday", 0, "root", 0),
            ("13", 3, "Smart", 4, "root", 0),
            ("14", 4, "Binned", 0, "root", 1),
            ("15", 2, "Warmup", 0, "10", 0),
            ("16", 5, "Other", 1, "root", 0),
            ("17", 1, "Warmup", 0, "16", 0),        # a name used twice
        ])
    conn.executemany(
        "INSERT INTO djmdSongPlaylist (ID, PlaylistID, ContentID, TrackNo, "
        "rb_local_deleted) VALUES (?, ?, ?, ?, ?)", [
            ("1", "11", "101", 1, 0), ("2", "11", "106", 2, 0),
            ("3", "12", "103", 1, 0), ("4", "15", "107", 2, 0),
            ("5", "15", "101", 1, 0), ("6", "15", "105", 3, 0),
            ("7", "14", "104", 1, 0), ("8", "11", "104", 3, 1),
        ])
    conn.executemany(
        "INSERT INTO djmdCue (ID, ContentID, InMsec, OutMsec, Kind, Comment, "
        "rb_local_deleted) VALUES (?, ?, ?, ?, ?, ?, ?)", [
            ("1", "101", 46500, -1, 1, "drop!", 0),     # A
            ("2", "101", 1000, -1, 5, "", 0),           # D: hot cues skip 4
            ("3", "101", 10000, -1, 0, "mem", 0),       # memory cue
            ("4", "101", 20000, 22000, 4, "", 0),       # memory loop
            ("5", "101", 30000, -1, 2, "gone", 1),      # deleted
        ])
    conn.execute("INSERT INTO agentRegistry VALUES (?, ?)",
                 ("SyncAnalysisDataRootPath", str(share)))
    conn.execute("INSERT INTO agentRegistry VALUES (?, ?)",
                 ("LangPath", r"C:\Program Files\rekordbox\rekordbox 7.2.14\locale\english.lang"))
    conn.commit()
    conn.close()
    write_anlz(share, ANLZ_NIGHT, night)
    write_anlz(share, ANLZ_STREAM, "/v4/catalog/track/9")
    write_anlz(share, ANLZ_LONE, "C:/Music/lone.mp3")
    write_anlz(share, ANLZ_BLUE, "C:/Music/blue.mp3", detail=pwv3(DETAIL))


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = prep.main(list(args))
    return code, out.getvalue()


tmp = Path(tempfile.mkdtemp(prefix="klights-rekordbox-"))
try:
    share = tmp / "elsewhere" / "share"
    db = tmp / "rekordbox" / "master.db"
    db.parent.mkdir()
    make_db(db, share)

    # -------------------------------------------------------------------------
    print("\n1. reading master.db")
    coll = masterdb.read(db)
    check("a decrypted copy opens with the stdlib and no key",
          len(coll.tracks) == 6, f"{sorted(coll.tracks)}")
    check("a row rekordbox deleted is not in the collection", 105 not in coll.tracks)
    check("the analysis folder is where rekordbox says, not beside the db",
          coll.anlz_root == share, f"{coll.anlz_root}")
    check("and the rekordbox that wrote it", coll.version == "7.2.14", f"{coll.version}")
    night = coll.tracks[101]
    check("a track's identity, tempo, length and key",
          (night.title, night.artist, night.album, night.bpm, night.duration_s, night.key)
          == ("Night Drive (Extended Mix)", "Kölsch & Friend", "Night EP", 124.0, 400, "8A"),
          f"{night}")
    check("a file on this machine is local; a streaming reference is not",
          night.local and not coll.tracks[103].local)
    check("a track with no artist says so, distinct from an empty name",
          coll.tracks[106].artist is None)
    check("each track names its own analysis file",
          coll.analysis_file(night) == share / ANLZ_NIGHT.lstrip("/")
          and coll.analysis_file(coll.tracks[104]) is None)
    cues = [(c.hot, c.loop, c.time_ms, c.name) for c in coll.cues[101]]
    check("cues from the database: A, D (Kind 5 -- hot cues skip 4), a memory "
          "cue and a memory loop; not the deleted one",
          cues == [(4, False, 1000, ""), (0, False, 10000, "mem"),
                   (0, True, 20000, ""), (1, False, 46500, "drop!")], f"{cues}")

    paths = coll.paths()
    check("playlists in tree order, by path; smart ones listed, deleted ones not",
          list(paths) == ["Gigs", "Gigs/Friday ", "Gigs/Warmup", "Friday", "Smart",
                          "Other", "Other/Warmup"], f"{list(paths)}")
    check("a smart playlist is a query rekordbox runs, so it has no tracks",
          paths["Smart"].kind == "smart" and paths["Smart"].tracks == ())
    check("a playlist's tracks in its order, skipping removed and deleted rows",
          [t.id for t in coll.in_playlist(paths["Gigs/Warmup"])] == [101, 107])
    check("a folder is every playlist under it, each track once",
          [t.id for t in coll.in_playlist(paths["Gigs"])] == [101, 106, 107])
    check("a name found exactly", coll.find_playlist("Friday").id == "12")
    check("a path, forgiving case and rekordbox's stray spaces",
          coll.find_playlist("gigs/friday").id == "11")
    check("a whole path beats a name: FRIDAY is the top-level one",
          coll.find_playlist("FRIDAY").id == "12")
    try:
        coll.find_playlist("warmup")
        check("a name two folders share is refused", False, "found one")
    except masterdb.DbError as exc:
        check("a name two folders share is refused, naming both paths",
              "Gigs/Warmup" in str(exc) and "Other/Warmup" in str(exc), str(exc))
    check("a file imported twice under one name is two rows that are copies",
          [t.id for t in coll.copies(coll.tracks[102])] == [101, 102]
          and [t.id for t in coll.copies(coll.tracks[106])] == [106])
    check("search matches every word, the way the matcher compares names",
          [t.id for t in coll.search("kolsch and friend night")] == [101, 102]
          and coll.search("nothing like this") == [] and coll.search("  ") == [])

    env = {masterdb.KEY_ENV: "from-env"}
    local = {"rekordbox_db": str(db), "rekordbox_key": "from-local"}
    check("the key: command line, then the environment, then klights.local.json",
          masterdb.resolve(None, "cli", local, env)[1] == "cli"
          and masterdb.resolve(None, None, local, env)[1] == "from-env"
          and masterdb.resolve(None, None, local, {})[1] == "from-local"
          and masterdb.resolve(None, None, {}, {})[1] is None)
    check("the database: command line, then klights.local.json, then rekordbox's own",
          masterdb.resolve("x.db", None, local, {})[0] == Path("x.db")
          and masterdb.resolve(None, None, local, {})[0] == db
          and masterdb.resolve(None, None, {}, {})[0] == masterdb.default_db_path())

    for label, path, key, expect in [
        ("a database that is not there says where it looked",
         tmp / "nope.db", None, "no rekordbox database"),
        ("an encrypted database with no key says how to give one",
         tmp / "scrambled.db", None, masterdb.KEY_ENV),
    ]:
        if path.name == "scrambled.db":
            path.write_bytes(bytes(range(256)) * 16)
        try:
            masterdb.read(path, key)
            check(label, False, "it opened")
        except masterdb.DbError as exc:
            check(label, expect in str(exc), str(exc))
    saved = sys.modules.get("sqlcipher3")
    sys.modules["sqlcipher3"] = None            # as if it were not installed
    try:
        masterdb.read(tmp / "scrambled.db", "a key")
        check("without sqlcipher3, an encrypted database says what to install", False)
    except masterdb.DbError as exc:
        check("without sqlcipher3, an encrypted database says what to install",
              "pip install sqlcipher3" in str(exc), str(exc))
    finally:
        if saved is None:
            del sys.modules["sqlcipher3"]
        else:
            sys.modules["sqlcipher3"] = saved

    try:
        from sqlcipher3 import dbapi2 as sqlcipher
    except ImportError:
        sqlcipher = None
        print("  skip  the encrypted round trip: sqlcipher3 is not installed")
    if sqlcipher is not None:
        enc = tmp / "encrypted.db"
        conn = sqlcipher.connect(str(db))
        conn.execute(f"ATTACH DATABASE '{enc.as_posix()}' AS enc KEY 'it''s a key'")
        conn.execute("SELECT sqlcipher_export('enc')")
        conn.execute("DETACH DATABASE enc")
        conn.close()
        check("an encrypted copy is not a plain SQLite file",
              not enc.read_bytes().startswith(masterdb.SQLITE_HEADER))
        got = masterdb.read(enc, "it's a key", share)
        check("with the key (quotes and all) it reads the same collection",
              sorted(got.tracks) == sorted(coll.tracks)
              and [p.name for p in got.playlists] == [p.name for p in coll.playlists])
        try:
            masterdb.read(enc, "the wrong key", share)
            check("the wrong key is refused, saying the key is the problem", False)
        except masterdb.DbError as exc:
            check("the wrong key is refused, saying the key is the problem",
                  "key does not open" in str(exc), str(exc))

    # -------------------------------------------------------------------------
    print("\n2. the beat-link signature")
    sig = expected_signature("Night Drive (Extended Mix)".encode(),
                             "Kölsch & Friend".encode(), 400, DETAIL, BEATS)
    analysis =anlz.read_files(*anlz.siblings(share / ANLZ_NIGHT.lstrip("/")))
    check("prep's signature is beat-link's, byte for byte",
          prep.blt_signature("Night Drive (Extended Mix)", "Kölsch & Friend", 400,
                             analysis) == sig)
    check("a track with no artist hashes beat-link's \"[no artist]\"",
          prep.blt_signature("T", None, 400, analysis)
          == expected_signature(b"T", b"[no artist]", 400, DETAIL, BEATS))
    blue = anlz.read_files(*anlz.siblings(share / ANLZ_BLUE.lstrip("/")))
    check("no signature without the color waveform it hashes, rather than a wrong one",
          prep.blt_signature("Blue Only", "Streamer", 400, blue) is None)
    check("and none without a length", prep.blt_signature("T", "A", None, analysis) is None)

    # beat-link's own output, recorded by bridges/rekordbox/blt_check/check.py
    # golden from these exact analysis files: no Java needed to check against it.
    golden = json.loads((REPO / "engine" / "tests" / "data" / "blt_signatures.json")
                        .read_text(encoding="utf-8"))
    by_name = {}
    for n, case in enumerate(golden["cases"]):
        dat = tmp / "golden" / str(n) / "ANLZ0000.DAT"
        dat.parent.mkdir(parents=True)
        dat.write_bytes(base64.b64decode(case["dat"]))
        dat.with_suffix(".EXT").write_bytes(base64.b64decode(case["ext"]))
        got = prep.blt_signature(case["title"], case["artist"], case["duration"],
                                 anlz.read_files(*anlz.siblings(dat)))
        by_name[case["name"]] = (case, dat)
        check(f"identical to beat-link {golden['beat_link']}'s own: {case['name']}",
              got == case["signature"], f"prep {got}, beat-link {case['signature']}")
    none_case, none_dat = next(v for k, v in by_name.items() if k.startswith("no artist"))
    empty_case = next(v[0] for k, v in by_name.items() if k.startswith("an empty artist"))
    check("a track with no artist carries both signatures a CDJ could report: the "
          "stick database's \"[no artist]\" and a metadata server's empty name",
          prep.blt_signatures(none_case["title"], None, none_case["duration"],
                              anlz.read_files(*anlz.siblings(none_dat)))
          == [none_case["signature"], empty_case["signature"]])

    # -------------------------------------------------------------------------
    print("\n3. the db route, into a show folder")
    show = tmp / "show"
    code, out = run("--show-dir", str(show), "db", "--master-db", str(db),
                    "--db", "collection:TEST")
    check("with nothing chosen it lists the playlists and preps nothing",
          code == 2 and "Gigs/" in out and "Friday" in out and not show.exists(), out)
    code, out = run("db", "--master-db", str(db), "--playlist", "gigs", "--list")
    check("--list says what was chosen, needing no show folder",
          code == 0 and "3 track(s)" in out and "Night Drive" in out, out)
    code, out = run("db", "--master-db", str(db), "--playlist", "Sunday", "--list")
    check("a playlist that is not there is refused, saying how to see them",
          code == 1 and "prep.py db" in out, out)
    code, out = run("db", "--master-db", str(db), "--id", "999", "--list")
    check("so is an id that is not there", code == 1 and "999" in out, out)

    code, out = run("--show-dir", str(show), "db", "--master-db", str(db),
                    "--db", "collection:TEST", "--all", "--json")
    summary = json.loads(out)
    by_id = {i: r for r in summary.get("results", []) for i in r["rekordbox_ids"]}
    skipped = {s["rekordbox_id"]: s["reason"] for s in summary.get("skipped", [])}
    check("--all --json preps the collection and answers in one JSON document",
          code == 0 and set(by_id) == {101, 102, 103, 106, 107}, out[:300])
    check("a track rekordbox never analysed is skipped, saying so",
          "not analysed" in skipped.get(104, ""), f"{skipped}")
    folder = sf.load_folder(show)
    check("the folder it made loads with no errors or warnings",
          folder.errors == [] and folder.warnings == [],
          f"{folder.errors[:2]} {folder.warnings[:2]}")
    tid = by_id[101]["track_id"]
    doc = folder.tracks[tid]
    check("the same file imported twice is one track, answering to both ids",
          by_id[102]["track_id"] == tid and len(folder.tracks) == 4
          and doc["ids"]["rekordbox"] == [{"db": "collection:TEST", "id": 101},
                                          {"db": "collection:TEST", "id": 102}],
          f"{doc['ids']}")
    check("it carries its beat-link signature, and says it computed it",
          doc["ids"]["blt_signatures"] == [sig] and doc["source"]["signatures"] == [sig]
          and doc["source"]["from"] == "master.db", f"{doc['ids']} {doc['source']}")
    lone = folder.tracks[by_id[106]["track_id"]]
    check("a track with no artist carries both of its possible signatures",
          lone["ids"]["blt_signatures"] == [
              expected_signature("Nobody's Tune".encode(), b"[no artist]", 400, DETAIL, BEATS),
              expected_signature("Nobody's Tune".encode(), b"", 400, DETAIL, BEATS)],
          f"{lone['ids']}")
    check("its phrases, from the analysis the database names",
          doc["phrases"]["items"] == [[0, 64, "Intro 1"], [64, 96, "Up 2"],
                                      [96, 160, "Chorus 2"]], f"{doc.get('phrases')}")
    check("its cues, from the database, on the beats they fall on",
          [(c["kind"], c.get("slot"), c["name"]) for c in doc["cues"]]
          == [("hot", "D", ""), ("memory", None, "mem"), ("loop", None, ""),
              ("hot", "A", "drop!")]
          # 46.5 s, from a downbeat at 1068 ms, at 124 bpm
          and abs(doc["cues"][-1]["beat"] - (46500 - 1068) / (60000 / 124)) < 0.01,
          f"{doc['cues']}")
    check("its file and size, for the designer to play",
          doc["audio"] == [{"host": doc["audio"][0]["host"],
                            "path": "C:/Music/Night Drive.mp3", "size": 9000000}])
    streamed = folder.tracks[by_id[103]["track_id"]]
    check("a streaming track is prepped, with no file, and the reason given",
          "audio" not in streamed and any("streaming" in n for n in by_id[103]["notes"]),
          f"{by_id[103]}")
    blue_doc = folder.tracks[by_id[107]["track_id"]]
    check("a track with no color waveform gets no signature, and says so",
          blue_doc["ids"]["blt_signatures"] == []
          and any("signature" in n for n in by_id[107]["notes"]), f"{by_id[107]}")

    print("\n4. the same song, whichever deck plays it")
    index = tracksmod.TrackIndex.build(folder.tracks)
    cdj_stick = index.match(title="Night Drive (Extended Mix)", artist="Kölsch & Friend",
                            rekordbox_id=7, signature=sig, duration=400.0)
    check("a CDJ playing a USB stick: the stick's own id means nothing, the "
          "signature matches it exactly",
          cdj_stick.track_id == tid and cdj_stick.via == tracksmod.SIGNATURE, f"{cdj_stick}")
    cdj_link = index.match(title="Night Drive (Extended Mix)", artist="Kölsch & Friend",
                           rekordbox_id=102)
    check("a CDJ loading from this rekordbox over the network: by this database's id",
          cdj_link.track_id == tid and cdj_link.via == tracksmod.REKORDBOX_ID, f"{cdj_link}")
    rkbx = index.match(title="Night Drive (Extended Mix)", artist="Kolsch & Friend",
                       album="Night EP")
    check("rekordbox itself through rkbx_link: by title, artist and album",
          rkbx.track_id == tid and rkbx.via == tracksmod.TITLE_ARTIST_ALBUM, f"{rkbx}")

    print("\n5. prepping again")
    before = {p: p.read_bytes() for p in show.rglob("*.json")}
    code, out = run("--show-dir", str(show), "db", "--master-db", str(db),
                    "--db", "collection:TEST", "--all")
    check("running it again changes nothing",
          code == 0 and "created" not in out and "updated" not in out
          and {p: p.read_bytes() for p in show.rglob("*.json")} == before, out)
    code, out = run("--show-dir", str(show), "db", "--master-db", str(db),
                    "--db", "collection:TEST", "--id", "102")
    check("and prepping the other copy alone is the same track, unchanged -- its "
          "cues are the copies' cues, whichever row holds them",
          code == 0 and f"unchanged  {tid}" in out
          and {p: p.read_bytes() for p in show.rglob("*.json")} == before, out)

    # A signature learned at a gig, then the track is re-gridded in rekordbox.
    learned = "f" * 40
    path = sf.path_for(show, "track", tid)
    doc["ids"]["blt_signatures"].append(learned)
    sf.write_doc(path, doc, "track")
    write_anlz(share, ANLZ_NIGHT, "C:/Music/Night Drive.mp3",
               beats=[(n, b, t + 20) for n, b, t in BEATS])
    code, out = run("--show-dir", str(show), "db", "--master-db", str(db),
                    "--db", "collection:TEST", "--id", "101")
    regridded = sf.load_folder(show).tracks[tid]
    new_sig = expected_signature("Night Drive (Extended Mix)".encode(),
                                 "Kölsch & Friend".encode(), 400, DETAIL,
                                 [(n, b, t + 20) for n, b, t in BEATS])
    check("a re-gridded track is reported", "re-gridded" in out, out)
    check("its old computed signature is replaced; the one learned at a gig is kept",
          regridded["ids"]["blt_signatures"] == [learned, new_sig], f"{regridded['ids']}")
    write_anlz(share, ANLZ_NIGHT, "C:/Music/Night Drive.mp3")

    # The XML route computes the same signature from the same analysis.
    xml = tmp / "rekordbox.xml"
    xml.write_text("""<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0"><COLLECTION Entries="1">
  <TRACK TrackID="101" Name="Night Drive (Extended Mix)" Artist="K&#246;lsch &amp; Friend"
         Album="Night EP" TotalTime="400" AverageBpm="124.00"
         Location="file://localhost/C:/Music/Night%20Drive.mp3"/>
</COLLECTION></DJ_PLAYLISTS>""", encoding="utf-8")
    xml_show = tmp / "xml-show"
    code, out = run("--show-dir", str(xml_show), "xml", str(xml),
                    "--anlz-root", str(share))
    xml_doc = next(iter(sf.load_folder(xml_show).tracks.values()), {})
    check("the XML route computes the same signature",
          code == 0 and xml_doc.get("ids", {}).get("blt_signatures") == [sig], out)

    # -------------------------------------------------------------------------
    print("\n6. the catalogue the designer browses")
    code, out = run("catalogue", "--master-db", str(db), "--db", "collection:TEST")
    cat = json.loads(out)
    check("one JSON document, ASCII only, so no code page can mangle a title",
          code == 0 and out.isascii() and cat["kind"] == "klights.rekordbox_catalogue")
    check("every track once, with what the browser shows",
          [t["id"] for t in cat["tracks"]] == [101, 102, 103, 104, 106, 107]
          and cat["tracks"][0]["artist"] == "Kölsch & Friend"
          and cat["tracks"][3]["analysed"] is False and cat["tracks"][2]["local"] is False)
    check("and every playlist in tree order with its tracks; no paths, no analysis",
          [(p["name"], p["parent"], p["kind"], p["tracks"]) for p in cat["playlists"]]
          == [("Gigs", None, "folder", []), ("Friday ", "10", "playlist", [101, 106]),
              ("Warmup", "10", "playlist", [101, 107]), ("Friday", None, "playlist", [103]),
              ("Smart", None, "smart", []), ("Other", None, "folder", []),
              ("Warmup", "16", "playlist", [])]
          and "path" not in cat["tracks"][0], f"{cat['playlists']}")
    code, out = run("catalogue", "--master-db", str(tmp / "nope.db"))
    check("a catalogue that cannot be read fails, saying why", code == 1
          and "no rekordbox database" in out, out)

    # -------------------------------------------------------------------------
    print("\n7. the engine's side: a child process, never an import")
    coll_args = ["--master-db", str(db), "--db", "collection:TEST"]
    wrapper = collectionmod.Collection(extra_args=coll_args)
    body = wrapper.catalogue()
    check("the catalogue comes back as the bridge's bytes",
          json.loads(body)["tracks"][0]["id"] == 101)
    check("asked again at once, it is served from the last read",
          wrapper.catalogue() is body)
    check("refresh reads again", wrapper.catalogue(refresh=True) is not body)
    try:
        collectionmod.Collection(extra_args=["--master-db", str(tmp / "nope.db")]).catalogue()
        check("the bridge's complaint is passed on unchanged", False)
    except collectionmod.CollectionError as exc:
        check("the bridge's complaint is passed on unchanged",
              "no rekordbox database" in str(exc), str(exc))
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys, engine.server, engine.api, engine.collection; "
         "print(sorted(m for m in sys.modules if 'sqlcipher' in m or "
         "m in ('masterdb', 'prep', 'anlz')))"],
        cwd=REPO, capture_output=True, text=True)
    check("the engine imports neither sqlcipher3 nor the bridge: a child process does",
          probe.returncode == 0 and probe.stdout.strip() == "[]",
          probe.stdout + probe.stderr[-300:])

    small = tmp / "small-show"
    summary = wrapper.prep([106], small)
    check("prep runs the bridge and returns its summary",
          [r["status"] for r in summary["results"]] == ["created"], f"{summary}")
    try:
        wrapper.prep([999], small)
        check("an id rekordbox does not have is the bridge's error", False)
    except collectionmod.CollectionError as exc:
        check("an id rekordbox does not have is the bridge's error", "999" in str(exc))
    wrapper._prep_lock.acquire()
    try:
        wrapper.prep([106], small)
        check("one prep at a time", False)
    except collectionmod.Busy:
        check("one prep at a time", True)
    finally:
        wrapper._prep_lock.release()
    for bad in ([], "101", [True], [-1], [2 ** 32], [1.5], list(range(501))):
        try:
            collectionmod.check_ids(bad)
            check(f"refused ids: {str(bad)[:20]}", False)
        except ValueError:
            check(f"refused ids: {str(bad)[:20]}", True)
    check("ids are kept in order, each once", collectionmod.check_ids([3, 1, 3]) == [3, 1])

    library = showlibrary.load(show)
    r = apimod.handle(library, "/api/rekordbox", token_ok=False, collection=wrapper)
    check("GET /api/rekordbox needs the token: it is someone's whole music library",
          r.status == 401)
    r = apimod.handle(library, "/api/rekordbox", token_ok=True, collection=wrapper)
    check("with it, the catalogue", r.status == 200 and json.loads(r.body)["db"]
          == "collection:TEST")
    r = apimod.handle(library, "/api/rekordbox", token_ok=True,
                      collection=collectionmod.Collection(
                          extra_args=["--master-db", str(tmp / "nope.db")]))
    check("a collection that cannot be read is a 503 saying why",
          r.status == 503 and "no rekordbox database" in json.loads(r.body)["error"])
    r = apimod.handle(library, "/api/tracks")
    line = next(t for t in json.loads(r.body)["tracks"] if t["id"] == tid)
    check("each track line says which rekordbox rows it is, and its signatures",
          {"db": "collection:TEST", "id": 101} in line["rekordbox"]
          and line["signatures"] == 2, f"{line}")

    print("\n8. the rekordbox_prep command")
    cmd_show = tmp / "cmd-show"
    shutil.copytree(REPO / "shared" / "show-example", cmd_show)
    sc = servermod.ShowController(REPO / "events" / "despacio", show_dir=cmd_show)
    sc.collection = collectionmod.Collection(extra_args=coll_args)
    sc.worker.start()
    replies: list[dict] = []
    sc.reply_to = lambda cid, payload: replies.append(payload)
    designer = servermod.Client(id="d1", name="designer", tier="configure")
    phone = servermod.Client(id="p1", name="phone", tier="operate")

    def ask(msg, client=designer, wait=20.0):
        replies.clear()
        sc.submit(msg, client)
        deadline = time.monotonic() + wait
        while not replies and time.monotonic() < deadline:
            sc._drain()
            time.sleep(0.02)
        assert sc.worker.wait_idle(5.0)
        sc._drain()
        return replies[-1] if replies else None

    r = ask({"type": "rekordbox_prep", "ids": [101], "id": 1}, client=phone)
    check("prepping into the show folder is configure-tier",
          r and r["ok"] is False and "needs configure" in r["error"], f"{r}")
    r = ask({"type": "rekordbox_prep", "ids": "all", "id": 2})
    check("ids are checked before anything runs",
          r and r["ok"] is False and "list of rekordbox" in r["error"], f"{r}")
    r = ask({"type": "rekordbox_prep", "ids": [101, 104], "id": 3})
    check("prepped off the output thread, answered with the bridge's summary",
          r and r["ok"] and [x["status"] for x in r["data"]["results"]] == ["created"]
          and r["data"]["skipped"][0]["rekordbox_id"] == 104, f"{r}")
    new_tid = r["data"]["results"][0]["track_id"] if r and r["ok"] else None
    check("and the show folder reloads with the new track in it",
          new_tid in sc.show_library.folder.tracks, f"{new_tid}")
    check("with a notice every console sees",
          any("prepped 1 track(s) from rekordbox, 1 new; 1 skipped" in n
              for n in sc.notices), f"{sc.notices[-2:]}")

    # A CDJ playing a stick exported from this collection, as beat-link-trigger
    # reports it: the stick's own track id (3: it means nothing here), the
    # names, and the signature beat-link computed from the stick's analysis.
    t0 = 100.0
    sc.ctx.time = t0
    sc.apply({"type": "sync", "source": "blt", "deck": "1",
              "title": "Night Drive (Extended Mix)", "artist": "Kölsch & Friend",
              "album": "Night EP", "duration": 400.0, "rekordbox_id": 3,
              "signature": sig}, None, t0)
    for i in range(10):
        sc.apply({"type": "sync", "source": "blt", "deck": "1", "track_time": 30 + i / 25,
                  "playing": True, "pitch": 1.0}, None, t0 + i / 25)
    sc.ctx.time = t0 + 0.5
    sc._track_frame(t0 + 0.5)
    live = (sc.snapshot()["track"] or {}).get("match") or {}
    check("a CDJ playing the track from a USB stick matches it, by signature, the "
          "first time it is played",
          live.get("track_id") == new_tid and live.get("via") == "signature", f"{live}")
    sc.worker.stop()
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("rekordbox: all checks pass")
