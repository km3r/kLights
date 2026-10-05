"""Check prep's CDJ signatures against beat-link's own code.

    python bridges/rekordbox/blt_check/check.py usb E:/          # a stick, against the show folder
    python bridges/rekordbox/blt_check/check.py collection       # every track rekordbox has analysed
    python bridges/rekordbox/blt_check/check.py golden           # rewrite the test suite's fixture

A prepped track matches a CDJ playing a USB stick because prep computes the
signature beat-link-trigger will report (prep.blt_signatures). Two things have
to hold for that, and this checks each with beat-link itself rather than with
a reading of it:

- **the hash**: `collection` runs beat-link 8's `computeTrackSignature` on
  every analysed track of the rekordbox collection and compares it with prep's.
- **the inputs**: `usb` reads a stick the way beat-link-trigger does -- its
  export.pdb through beat-link's own `TrackMetadata`, its analysis files --
  computes each track's signature, and asks the engine's matcher which prepped
  track it is. Where a track matches only by name, it says which input differs:
  the title, the artist, the length, the waveform or the grid. Usually it is a
  track re-gridded or re-analysed after the stick was exported, and the cure is
  to prep it again and re-export the stick.

`golden` writes engine/tests/data/blt_signatures.json: a few synthetic tracks'
analysis files and the signature beat-link gave each, so the test suite checks
prep against beat-link's output with no Java anywhere.

**A dev tool, not part of the show.** It needs a Java runtime, 11 or later
(`--java`, $JAVA_HOME or `java` on the PATH; a mise install is found too), and
fetches beat-link and its dependencies from Maven Central once, pinned by
SHA-1, into a `.jars/` folder beside this file. Nothing at the venue needs any
of it.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import urllib.request
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(HERE.parent))

import anlz  # noqa: E402
import masterdb  # noqa: E402
import prep  # noqa: E402
from engine import showfiles as sf  # noqa: E402
from engine import tracks as tracksmod  # noqa: E402

BEAT_LINK = "8.0.0"
MAVEN = "https://repo1.maven.org/maven2"
JARS = {        # path under MAVEN -> SHA-1, as Maven Central publishes it
    f"org/deepsymmetry/beat-link/{BEAT_LINK}/beat-link-{BEAT_LINK}.jar":
        "e30c12a08ef29281483caae26ea12bcb1530b78a",
    "org/deepsymmetry/crate-digger/0.2.1/crate-digger-0.2.1.jar":
        "052d3a34eaf96640161dc87b3528bb6ef3f8aa92",
    "org/deepsymmetry/electro/0.1.4/electro-0.1.4.jar":
        "5d65fcd83b21681f731df21fd51de7698d15ce86",
    "io/kaitai/kaitai-struct-runtime/0.10/kaitai-struct-runtime-0.10.jar":
        "8d17791c493ce69474c9bf297ee2ac46d1fe377d",
    "org/slf4j/slf4j-api/1.7.36/slf4j-api-1.7.36.jar":
        "6c62681a2f655b49963a5983b8b0950a6120ae14",
    "org/apiguardian/apiguardian-api/1.1.2/apiguardian-api-1.1.2.jar":
        "a231e0d844d2721b0fa1b238006d15c6ded6842a",
    "org/acplt/remotetea/remotetea-oncrpc/1.1.4/remotetea-oncrpc-1.1.4.jar":
        "cffb42370fe743dd29c22588e4ae7069bd25e852",
}
NONE = "~none~"
GOLDEN = REPO / "engine" / "tests" / "data" / "blt_signatures.json"


class CheckError(RuntimeError):
    pass


# -- java and beat-link ---------------------------------------------------------

def _runs(java: str) -> bool:
    try:
        return subprocess.run([java, "-version"], capture_output=True,
                              timeout=30).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def find_java(given: Optional[str]) -> str:
    candidates = [given, os.environ.get("JAVA")]
    if os.environ.get("JAVA_HOME"):
        candidates.append(str(Path(os.environ["JAVA_HOME"]) / "bin" / "java"))
    candidates.append(shutil.which("java"))
    mise = Path(os.environ.get("LOCALAPPDATA") or Path.home() / ".local" / "share") / "mise"
    candidates += [str(p) for p in sorted((mise / "installs" / "java").glob("*/bin/java*"),
                                          reverse=True)]
    for c in candidates:
        if c and _runs(c):
            return c
    raise CheckError("no Java runtime that runs (11 or later): pass --java, or "
                     "set JAVA_HOME")


def jars() -> list[Path]:
    """beat-link and its dependencies, fetched once and checked by SHA-1."""
    cache = HERE / ".jars"
    cache.mkdir(exist_ok=True)
    out = []
    for path, sha1 in JARS.items():
        local = cache / path.rsplit("/", 1)[-1]
        if not local.is_file() or hashlib.sha1(local.read_bytes()).hexdigest() != sha1:
            print(f"  fetching {local.name} from Maven Central", file=sys.stderr)
            with urllib.request.urlopen(f"{MAVEN}/{path}", timeout=60) as resp:
                data = resp.read()
            if hashlib.sha1(data).hexdigest() != sha1:
                raise CheckError(f"{local.name} from Maven Central does not have "
                                 f"the pinned SHA-1; refusing to run it")
            local.write_bytes(data)
        out.append(local)
    return out


def beat_link(java: str, *args: str) -> list[dict]:
    """Run SigCheck.java; one dict per track."""
    cp = os.pathsep.join(str(j) for j in jars())
    proc = subprocess.run([java, "-cp", cp, str(HERE / "SigCheck.java"), *args],
                          capture_output=True, timeout=3600)
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", "replace").strip().splitlines()[-5:]
        raise CheckError("beat-link failed:\n  " + "\n  ".join(tail))
    rows = []
    for line in proc.stdout.decode("utf-8").splitlines():
        f = line.split("\t")
        if len(f) < 9:
            continue
        unb = lambda s: base64.b64decode(s).decode("utf-8")
        rows.append({"id": int(f[0]), "title": unb(f[1]),
                     "artist": None if f[2] == NONE else unb(f[2]),
                     "album": unb(f[3]) if f[3] else "", "duration": int(f[4]),
                     "signature": f[5], "wave": f[6], "beats": f[7],
                     "beat_count": int(f[8] or 0)})
    return rows


def _b64(text: str) -> str:
    return base64.b64encode(text.encode("utf-8")).decode("ascii")


def tsv_line(i, title: str, artist: Optional[str], duration: int, dat: Path) -> str:
    return "\t".join([str(i), _b64(title), NONE if artist is None else _b64(artist),
                      str(duration), str(dat), str(dat.with_suffix(".EXT"))])


# -- what prep hashes, in pieces --------------------------------------------------

def pieces(analysis: anlz.Analysis) -> tuple[Optional[str], str, int]:
    """sha1 of the PWV5 entries and of the beats, as SigCheck reports them."""
    wave = (hashlib.sha1(analysis.detail).hexdigest()
            if analysis.detail_format == "pwv5" and analysis.detail else None)
    beats = hashlib.sha1(b"".join(struct.pack(">II", b.number, b.time_ms)
                                  for b in analysis.beats)).hexdigest()
    return wave, beats, len(analysis.beats)


# -- the collection ---------------------------------------------------------------

def open_collection(args) -> masterdb.Collection:
    path, key = masterdb.resolve(args.master_db, None, sf.read_local_config())
    return masterdb.read(path, key)


def cmd_collection(args, java: str) -> int:
    coll = open_collection(args)
    mine, lines = {}, []
    for t in coll.tracks.values():
        dat = coll.analysis_file(t)
        if (dat is None or t.duration_s is None or not dat.is_file()
                or not dat.with_suffix(".EXT").is_file()):
            continue
        sig = prep.blt_signature(t.title, t.artist, t.duration_s,
                                 anlz.read_files(*anlz.siblings(dat)))
        if sig is None:
            continue
        mine[t.id] = sig
        lines.append(tsv_line(t.id, t.title, t.artist, t.duration_s, dat))
        if args.limit and len(mine) >= args.limit:
            break
    with tempfile.TemporaryDirectory() as tmp:
        tsv = Path(tmp) / "collection.tsv"
        tsv.write_text("\n".join(lines) + "\n", encoding="utf-8")
        theirs = {r["id"]: r["signature"] for r in beat_link(java, "collection", str(tsv))}
    differ = [i for i in mine if theirs.get(i) != mine[i]]
    no_artist = sum(coll.tracks[i].artist is None for i in mine)
    print(f"{len(mine)} tracks: {len(mine) - len(differ)} signatures identical to "
          f"beat-link {BEAT_LINK}'s, {len(differ)} different "
          f"({no_artist} have no artist)")
    for i in differ[:20]:
        t = coll.tracks[i]
        print(f"  DIFFERENT  {i}  {t.artist or ''} - {t.title}: prep {mine[i]}, "
              f"beat-link {theirs.get(i)}")
    return 1 if differ else 0


# -- a stick ----------------------------------------------------------------------

def cmd_usb(args, java: str) -> int:
    stick = Path(args.stick)
    if not (stick / "PIONEER" / "rekordbox" / "export.pdb").is_file():
        raise CheckError(f"no PIONEER/rekordbox/export.pdb on {stick}: is it a "
                         f"stick rekordbox exported to?")
    root = sf.resolve_show_dir(args.show_dir)
    if root is None:
        raise CheckError("no show folder: pass --show-dir, set KLIGHTS_SHOW_DIR, "
                         "or set show_dir in klights.local.json")
    folder = sf.load_folder(root)
    index = tracksmod.TrackIndex.build(folder.tracks)
    rows = beat_link(java, "usb", str(stick))
    try:
        coll: Optional[masterdb.Collection] = open_collection(args)
    except masterdb.DbError as exc:
        coll = None
        print(f"  (no collection to compare inputs with: {exc})")
    counts = {"signature": 0, "name": 0, "absent": 0, "other": 0}
    for r in rows:
        who = f"{r['artist'] or ''} - {r['title']}"
        if not r["signature"] or r["signature"].startswith(("ERROR", "NO-PWV5")):
            print(f"  NO SIG   {who}: {r['signature'] or 'nothing'}")
            counts["other"] += 1
            continue
        m = index.match(title=r["title"], artist=r["artist"] or "", album=r["album"],
                        duration=float(r["duration"]), rekordbox_id=r["id"],
                        signature=r["signature"])
        if m.via == tracksmod.SIGNATURE:
            counts["signature"] += 1
            if args.verbose:
                print(f"  OK       {m.track_id}  ({who})")
            continue
        if not m.matched:
            if m.via == tracksmod.AMBIGUOUS:
                print(f"  AMBIGUOUS  {who}: {', '.join(m.candidates)}")
                counts["other"] += 1
            else:
                counts["absent"] += 1
                if args.verbose:
                    print(f"  absent   {who}  (not in the show folder)")
            continue
        counts["name"] += 1
        why = explain(r, folder.tracks[m.track_id], coll)
        print(f"  NAME ONLY  {m.track_id}  ({who}), by {m.via.replace('_', ' ')}: {why}")
    print(f"{len(rows)} tracks on {stick}: {counts['signature']} match by signature, "
          f"{counts['name']} only by name, {counts['absent']} not in the show "
          f"folder, {counts['other']} other")
    return 0 if counts["name"] == 0 and counts["other"] == 0 else 1


def explain(row: dict, doc: dict, coll: Optional[masterdb.Collection]) -> str:
    """Which input of the signature differs between the stick and the
    collection the track was prepped from."""
    if coll is None:
        return "the signature differs; re-prep it and re-export the stick"
    track = next((coll.tracks[r["id"]] for r in (doc.get("ids") or {}).get("rekordbox") or ()
                  if r.get("id") in coll.tracks), None)
    if track is None or coll.analysis_file(track) is None:
        return "the signature differs, and the collection no longer has its track"
    dat = coll.analysis_file(track)
    wave, beats, count = pieces(anlz.read_files(*anlz.siblings(dat)))
    diffs = []
    if row["title"] != track.title:
        diffs.append(f"title: stick {row['title']!r}, rekordbox {track.title!r}")
    if row["artist"] != track.artist:
        diffs.append(f"artist: stick {row['artist']!r}, rekordbox {track.artist!r}")
    if row["duration"] != track.duration_s:
        diffs.append(f"length: stick {row['duration']} s, rekordbox {track.duration_s} s")
    if row["wave"] != wave:
        diffs.append("the waveform (re-analysed since the export?)")
    if row["beats"] != beats:
        diffs.append(f"the beat grid ({row['beat_count']} beats on the stick, {count} "
                     f"in rekordbox: re-gridded since the export?)")
    if not diffs:
        return ("every input agrees with rekordbox, so the show folder is stale: "
                "prep it again")
    return "; ".join(diffs) + ". Re-export the stick, or prep again if rekordbox changed"


# -- the golden fixture -----------------------------------------------------------

def _tag(fourcc: bytes, head: bytes, payload: bytes = b"") -> bytes:
    body = head + payload
    return fourcc + struct.pack(">II", 12 + len(head), 12 + len(body)) + body


def _anlz(*tags: bytes) -> bytes:
    rest = b"".join(tags)
    return b"PMAI" + struct.pack(">II", 28, 28 + len(rest)) + b"\0" * 16 + rest


def golden_cases() -> list[dict]:
    """Synthetic tracks with every input the signature is sensitive to:
    non-Latin text, no artist, an empty artist, a tempo change, a pickup."""
    def steady(bpm, first_ms, count, first_number=1, start_index=0):
        return [((first_number - 1 + i) % 4 + 1, round(first_ms + i * 60000 / bpm))
                for i in range(start_index, start_index + count)]
    changing = steady(120.0, 40, 64) + [
        ((i % 4) + 1, round(32040 + (i + 1) * 60000 / 126.0)) for i in range(96)]
    wave_a = bytes((i * 37 + 11) % 256 for i in range(1200))
    wave_b = bytes((i * 91 + 3) % 256 for i in range(900))
    return [
        {"name": "artist with accents and an ampersand",
         "title": "Night Drive (Extended Mix)", "artist": "Kölsch & Friend",
         "duration": 400, "beats": steady(124.0, 100, 200, first_number=3), "wave": wave_a},
        {"name": "no artist at all (export.pdb, exportLibrary.db)",
         "title": "夜のドライブ", "artist": None, "duration": 233,
         "beats": steady(128.0, 51, 160), "wave": wave_b},
        {"name": "an empty artist (a player's metadata server, perhaps)",
         "title": "夜のドライブ", "artist": "", "duration": 233,
         "beats": steady(128.0, 51, 160), "wave": wave_b},
        {"name": "a tempo change", "title": "Tempo Change", "artist": "A & B",
         "duration": 112, "beats": changing, "wave": wave_a[:600]},
    ]


def _files(case: dict) -> tuple[bytes, bytes]:
    beats = b"".join(struct.pack(">HHI", n, 12000, t) for n, t in case["beats"])
    path = "/Contents/golden/track.mp3".encode("utf-16-be") + b"\0\0"
    ppth = _tag(b"PPTH", struct.pack(">I", len(path)), path)
    dat = _anlz(ppth, _tag(b"PQTZ", struct.pack(">III", 0, 0x80000, len(case["beats"])),
                           beats))
    ext = _anlz(ppth, _tag(b"PWV5", struct.pack(">III", 2, len(case["wave"]) // 2,
                                                0x960305), case["wave"]))
    return dat, ext


def cmd_golden(args, java: str) -> int:
    cases = golden_cases()
    with tempfile.TemporaryDirectory() as tmp:
        lines = []
        for n, case in enumerate(cases):
            dat, ext = _files(case)
            d = Path(tmp) / f"{n}" / "ANLZ0000.DAT"
            d.parent.mkdir()
            d.write_bytes(dat)
            d.with_suffix(".EXT").write_bytes(ext)
            lines.append(tsv_line(n, case["title"], case["artist"], case["duration"], d))
        tsv = Path(tmp) / "golden.tsv"
        tsv.write_text("\n".join(lines) + "\n", encoding="utf-8")
        got = {r["id"]: r["signature"] for r in beat_link(java, "collection", str(tsv))}
    out = {"note": ("Written by bridges/rekordbox/blt_check/check.py golden: each "
                    "case's analysis files and the signature beat-link computed "
                    "for them. Do not edit; regenerate."),
           "beat_link": BEAT_LINK,
           "cases": []}
    for n, case in enumerate(cases):
        dat, ext = _files(case)
        out["cases"].append({"name": case["name"], "title": case["title"],
                             "artist": case["artist"], "duration": case["duration"],
                             "dat": base64.b64encode(dat).decode("ascii"),
                             "ext": base64.b64encode(ext).decode("ascii"),
                             "signature": got[n]})
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {GOLDEN.relative_to(REPO)}: {len(cases)} cases from beat-link {BEAT_LINK}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="check.py", description=__doc__.split("\n")[0])
    parser.add_argument("--java", help="the java executable (11 or later)")
    parser.add_argument("--master-db", help="the rekordbox database, if not the default")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_usb = sub.add_parser("usb", help="a stick, against the show folder")
    p_usb.add_argument("stick", help="the stick's root, e.g. E:/")
    p_usb.add_argument("--show-dir")
    p_usb.add_argument("-v", "--verbose", action="store_true",
                       help="list every track, not only the ones that need attention")
    p_col = sub.add_parser("collection", help="every analysed track, prep against beat-link")
    p_col.add_argument("--limit", type=int, default=0)
    sub.add_parser("golden", help="rewrite the test suite's beat-link fixture")
    args = parser.parse_args(argv)
    try:
        java = find_java(args.java)
        return {"usb": cmd_usb, "collection": cmd_collection,
                "golden": cmd_golden}[args.cmd](args, java)
    except (CheckError, masterdb.DbError) as exc:
        print(f"check failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
