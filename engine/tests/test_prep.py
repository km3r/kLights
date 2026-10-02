"""
Tests for the rekordbox prep tool: the ANLZ reader, the grid compression, and
the XML route into a show folder.

The ANLZ files here are built byte by byte with `struct`, straight from the
documented layouts (crate-digger's rekordbox_anlz.ksy), and NOT with anything
from the reader -- an encoder and decoder that share a misreading of the format
would agree with each other and both be wrong. The PSSI mask is applied here
independently too, so a masked tag is proven to decode to the same phrases as a
clear one.

No rekordbox and no real files: a real export is the one thing this cannot
check, and the hardware checklist in docs/design/timecoded-shows.md covers it.

Run: python engine/tests/test_prep.py
"""

import base64
import contextlib
import io
import json
import shutil
import struct
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "bridges" / "rekordbox"))

import anlz  # noqa: E402
import prep  # noqa: E402
from engine import showfiles as sf  # noqa: E402
from engine import tracktime  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


# -- an independent ANLZ writer -----------------------------------------------

def tag(fourcc: bytes, head: bytes, payload: bytes = b"") -> bytes:
    """fourcc, len_header, len_tag, then the tag's header fields and payload."""
    body = head + payload
    return fourcc + struct.pack(">II", 12 + len(head), 12 + len(body)) + body


def anlz_file(*tags: bytes) -> bytes:
    rest = b"".join(tags)
    return b"PMAI" + struct.pack(">II", 28, 28 + len(rest)) + b"\0" * 16 + rest


def pqtz(beats) -> bytes:
    """beats: [(number, bpm, time_ms)]."""
    payload = b"".join(struct.pack(">HHI", n, round(bpm * 100), t) for n, bpm, t in beats)
    return tag(b"PQTZ", struct.pack(">III", 0, 0x80000, len(beats)), payload)


def ppth(path: str) -> bytes:
    raw = path.encode("utf-16-be") + b"\0\0"
    return tag(b"PPTH", struct.pack(">I", len(raw)), raw)


def pwav(data: bytes) -> bytes:
    return tag(b"PWAV", struct.pack(">II", len(data), 0x10000), data)


def pwv3(data: bytes) -> bytes:
    return tag(b"PWV3", struct.pack(">III", 1, len(data), 0x960000), data)


MASK_BASE = [0xCB, 0xE1, 0xEE, 0xFA, 0xE5, 0xEE, 0xAD, 0xEE, 0xE9, 0xD2,
             0xE9, 0xEB, 0xE1, 0xE9, 0xF3, 0xE8, 0xE9, 0xF4, 0xE1]


def pssi(mood: int, end_beat: int, entries, masked: bool, bank: int = 3) -> bytes:
    """entries: [(index, beat, kind, k1, k2, k3)]."""
    body = struct.pack(">H6xH2xBx", mood, end_beat, bank)
    for index, beat, kind, k1, k2, k3 in entries:
        body += (struct.pack(">HHH", index, beat, kind) + bytes([0, k1, 0, k2, 0, 0])
                 + struct.pack(">HHH", 0, 0, 0) + bytes([0, k3, 0, 0])
                 + struct.pack(">H", 0))
    if masked:
        n = len(entries)
        body = bytes(b ^ ((MASK_BASE[i % 19] + n) % 256) for i, b in enumerate(body))
    return tag(b"PSSI", struct.pack(">IH", 24, len(entries)), body)


def pcob(cues) -> bytes:
    """cues: [(hot, kind, time_ms)] -- kind 1 cue, 2 loop."""
    payload = b""
    for hot, kind, time_ms in cues:
        payload += (b"PCPT" + struct.pack(">II", 28, 56)
                    + struct.pack(">IIIHH", hot, 1, 0x10000, 0xFFFF, 1)
                    + bytes([kind, 0, 0, 0]) + struct.pack(">II", time_ms, time_ms + 1000)
                    + bytes(16))
    return tag(b"PCOB", struct.pack(">I2xHI", 1, len(cues), 0), payload)


def pco2(cues) -> bytes:
    """cues: [(hot, kind, time_ms, name)]."""
    payload = b""
    for hot, kind, time_ms, name in cues:
        comment = name.encode("utf-16-be") + b"\0\0"
        length = 44 + len(comment) + 4
        payload += (b"PCP2" + struct.pack(">II", 16, length) + struct.pack(">I", hot)
                    + bytes([kind, 0, 0, 0]) + struct.pack(">II", time_ms, 0)
                    + bytes(8) + struct.pack(">HH", 0, 0)
                    + struct.pack(">I", len(comment)) + comment + bytes(4))
    return tag(b"PCO2", struct.pack(">IH2x", 1, len(cues)), payload)


def steady_beats(bpm, first_ms, count, first_number=1):
    """A rekordbox-style grid: whole milliseconds, beat numbers cycling 1-4."""
    return [((first_number - 1 + i) % 4 + 1, bpm, round(first_ms + i * 60000 / bpm))
            for i in range(count)]


# -----------------------------------------------------------------------------
print("\n1. reading ANLZ")
BEATS = steady_beats(124.0, 100, 200, first_number=3)     # a two-beat pickup
PHRASES = [(1, 3, 1, 1, 0, 0), (2, 67, 2, 0, 0, 1), (3, 99, 5, 0, 0, 0)]
WAVE = bytes(range(200)) * 2
DAT = anlz_file(ppth("C:/Music/Night Drive.mp3"), pqtz(BEATS), pwav(WAVE),
                pcob([(1, 1, 46500), (0, 1, 1000)]),
                tag(b"PVBR", struct.pack(">I", 0), bytes(1600)))
EXT = anlz_file(ppth("C:/Music/Night Drive.mp3"),
                pssi(1, 163, PHRASES, masked=True), pwv3(bytes(range(256)) * 4),
                pco2([(1, 1, 46500, "drop!"), (0, 2, 1000, "")]))

a = anlz.parse(DAT)
check("the audio path comes back from PPTH", a.path == "C:/Music/Night Drive.mp3", a.path)
check("every beat of the grid, with its bar position and tempo",
      len(a.beats) == 200 and a.beats[0].number == 3 and a.beats[2].number == 1
      and abs(a.beats[0].bpm - 124.0) < 1e-9 and a.beats[1].time_ms == 584,
      f"{a.beats[:3]}")
check("the 400-column preview", a.preview == WAVE)
check("cues from PCOB", [(c.hot, c.time_ms) for c in a.cues] == [(1, 46500), (0, 1000)])
check("an unknown tag is listed and skipped, not an error", a.unknown == ["PVBR"])

anlz.parse(EXT, a)
check("the .EXT merges into the same analysis", a.mood == "high"
      and [p.label for p in a.phrases] == ["Intro 1", "Up 2", "Chorus 2"],
      f"{a.mood} {[p.label for p in a.phrases]}")
check("a masked PSSI is detected and unmasked", a.masked is True)
check("the phrase end and the lighting bank are read",
      a.phrase_end_beat == 163 and a.bank == 3)
check("named cues from PCO2 replace PCOB's",
      [(c.hot, c.name, c.loop) for c in a.cues] == [(1, "drop!", False), (0, "", True)],
      f"{a.cues}")
check("the colourless detail waveform is kept", a.detail_format == "pwv3"
      and len(a.detail) == 1024)

clear = anlz.parse(anlz_file(pssi(1, 163, PHRASES, masked=False)))
check("a clear PSSI decodes to the same phrases as a masked one",
      not clear.masked and [p.label for p in clear.phrases] == ["Intro 1", "Up 2", "Chorus 2"]
      and [p.beat for p in clear.phrases] == [p.beat for p in a.phrases])

labels = {
    ("high", 1, 1, 0, 0): "Intro 1", ("high", 1, 0, 0, 0): "Intro 2",
    ("high", 2, 0, 0, 0): "Up 1", ("high", 2, 0, 0, 1): "Up 2",
    ("high", 2, 0, 1, 0): "Up 3", ("high", 3, 0, 0, 0): "Down",
    ("high", 5, 1, 0, 0): "Chorus 1", ("high", 6, 0, 0, 0): "Outro 2",
    ("mid", 4, 0, 0, 0): "Verse 3", ("mid", 8, 0, 0, 0): "Bridge",
    ("low", 3, 0, 0, 0): "Verse 1", ("low", 6, 0, 0, 0): "Verse 2",
    ("low", 9, 0, 0, 0): "Chorus",
}
wrong = {k: anlz.phrase_label(*k) for k, v in labels.items() if anlz.phrase_label(*k) != v}
check("labels are rekordbox's own, including high-mood numbering",
      not wrong, f"{wrong}")
check("which never says Build or Drop",
      not any(w in anlz.phrase_label(m, k, a1, a2, a3)
              for m in ("high", "mid", "low") for k in range(1, 11)
              for a1 in (0, 1) for a2 in (0, 1) for a3 in (0, 1)
              for w in ("Build", "Drop")))

for label, bad in [
    ("a file that is not an analysis file", b"RIFF" + bytes(40)),
    ("an empty file", b""),
    ("a file shorter than its header claims", DAT[:len(DAT) // 2]),
    ("a tag that runs past the end",
     b"PMAI" + struct.pack(">II", 12, 40) + b"PQTZ" + struct.pack(">II", 24, 4000) + bytes(16)),
    ("a grid claiming more beats than it holds",
     anlz_file(tag(b"PQTZ", struct.pack(">III", 0, 0, 50), bytes(16)))),
    ("a phrase map whose mood is nonsense even unmasked",
     anlz_file(pssi(9, 10, [(1, 1, 1, 0, 0, 0)], masked=False))),
]:
    try:
        anlz.parse(bad)
        check(f"refused: {label}", False, "it parsed")
    except anlz.AnlzError as exc:
        check(f"refused: {label}", True, str(exc)[:60])


from engine import tracks as tracksmod  # noqa: E402

n = tracksmod.normalize
check("names that differ only in accents, case, '&' and 'ft.' compare equal",
      n("Kölsch & Friend ft. Y") == n("KOLSCH and Friend feat Y")
      == n("Kolsch  &  Friend (feat. Y)"), n("Kölsch & Friend ft. Y"))
check("but an Original Mix is not an Extended Mix -- different audio, different grid",
      n("Night Drive (Original Mix)") != n("Night Drive (Extended Mix)"))
check("and 'left' does not lose its 'ft'", n("Left Behind") == "left behind")
check("an id for a title with no Latin letters still exists",
      tracksmod.slug({"title": "夜", "artist": "誰"}).startswith("track-"))
check("ids do not collide", tracksmod.slug({"title": "a", "artist": "b"},
                                           frozenset({"b-a"})) == "b-a-2")


# -----------------------------------------------------------------------------
print("\n2. compressing a grid")
segs, down = prep.grid_from_beats(anlz.parse(DAT).beats)
grid = tracktime.Grid.from_segments(segs)
check("the first downbeat is beat 0, and the pickup before it is negative",
      down == 2 and segs[0][0] == -2, f"{segs}")
check("a steady track collapses to a couple of anchors",
      len(segs) <= 3, f"{len(segs)} anchors")
worst = max(abs(grid.time_at(i - 2) * 1000 - b[2]) for i, b in enumerate(BEATS))
check("and every beat is still where rekordbox put it, within 1 ms",
      worst <= 1.0, f"{worst:.3f} ms")
changing = steady_beats(120.0, 0, 64) + [
    (n, 126.0, round(32000 + (i + 1) * 60000 / 126)) for i, (n, _, _) in
    enumerate(steady_beats(126.0, 0, 64))]
segs2, _ = prep.grid_from_beats([anlz.Beat(n, b, t) for n, b, t in changing])
g2 = tracktime.Grid.from_segments(segs2)
worst2 = max(abs(g2.time_at(i) * 1000 - t) for i, (_, _, t) in enumerate(changing))
check("a tempo change gets an anchor, and the beats on both sides fit",
      2 <= len(segs2) <= 5 and worst2 <= 1.0, f"{len(segs2)} anchors, {worst2:.3f} ms")
for label, bad in [("no beats", []),
                   ("no downbeat", [anlz.Beat(2, 120, 0), anlz.Beat(3, 120, 500)]),
                   ("time running backwards",
                    [anlz.Beat(1, 120, 500), anlz.Beat(2, 120, 400)])]:
    try:
        prep.grid_from_beats(bad)
        check(f"refused: a grid with {label}", False, "accepted")
    except prep.PrepError:
        check(f"refused: a grid with {label}", True)
xml_grid = prep.grid_from_tempos([(0.100, 124.0, 3), (100.0, 124.0, 1)])
check("an XML grid that starts on beat 3 puts the downbeat two beats later",
      xml_grid[0][0] == -2, f"{xml_grid}")


# -----------------------------------------------------------------------------
print("\n3. the XML route, into a show folder")
XML = """<?xml version="1.0" encoding="UTF-8"?>
<DJ_PLAYLISTS Version="1.0.0">
  <PRODUCT Name="rekordbox" Version="7.2.2" Company="AlphaTheta"/>
  <COLLECTION Entries="3">
    <TRACK TrackID="101" Name="{title}" Artist="Kölsch &amp; Friend feat. Y"
           Album="Night EP" TotalTime="400" AverageBpm="124.00" Size="9000000"
           Location="file://localhost/C:/Music/Night%20Drive.mp3">
      <TEMPO Inizio="0.100" Bpm="124.00" Metro="4/4" Battito="3"/>
      <POSITION_MARK Name="xml cue" Type="0" Start="46.500" Num="0"/>
    </TRACK>
    <TRACK TrackID="102" Name="No Analysis" Artist="Someone" Album=""
           TotalTime="300" AverageBpm="128.00"
           Location="file://localhost/C:/Music/no%20analysis.mp3">
      <TEMPO Inizio="0.050" Bpm="128.00" Metro="4/4" Battito="1"/>
      <POSITION_MARK Name="" Type="0" Start="15.050" Num="-1"/>
    </TRACK>
    <TRACK TrackID="103" Name="Gridless" Artist="Nobody" TotalTime="60"
           Location="file://localhost/C:/Music/gridless.mp3"/>
  </COLLECTION>
  <PLAYLISTS>
    <NODE Type="0" Name="ROOT" Count="1">
      <NODE Name="Friday" Type="1" KeyType="0" Entries="1">
        <TRACK Key="101"/>
      </NODE>
    </NODE>
  </PLAYLISTS>
</DJ_PLAYLISTS>
"""


def run(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        code = prep.main(list(args))
    return code, out.getvalue()


tmp = Path(tempfile.mkdtemp(prefix="klights-prep-"))
try:
    xml = tmp / "rekordbox.xml"
    xml.write_text(XML.replace("{title}", "Night Drive (Extended Mix)"), encoding="utf-8")
    anlz_root = tmp / "USBANLZ" / "P016" / "0000ABCD"
    anlz_root.mkdir(parents=True)
    (anlz_root / "ANLZ0000.DAT").write_bytes(DAT)
    (anlz_root / "ANLZ0000.EXT").write_bytes(EXT)
    show = tmp / "show"

    code, out = run("--show-dir", str(show), "xml", str(xml),
                    "--anlz-root", str(tmp / "USBANLZ"), "--db", "collection:TEST")
    check("prep runs", code == 0, out)
    check("a track with no grid at all is skipped, saying why",
          "skipped" in out and "Gridless" in out and "TEMPO" in out, out)
    folder = sf.load_folder(show)
    check("the folder it made loads with no errors or warnings",
          folder.errors == [] and folder.warnings == [],
          f"{folder.errors[:2]} {folder.warnings[:2]}")
    ids = sorted(folder.tracks)
    check("two tracks, with readable ids", len(ids) == 2
          and any(i.startswith("kolsch-and-friend-feat-y-night-drive") for i in ids), f"{ids}")
    night = next(d for d in folder.tracks.values() if d["identity"]["title"].startswith("Night"))
    plain = next(d for d in folder.tracks.values() if d["identity"]["title"] == "No Analysis")
    check("the analysed track's phrases, in track beats from its first downbeat",
          night["phrases"] == {"mood": "high", "items": [
              [0, 64, "Intro 1"], [64, 96, "Up 2"], [96, 160, "Chorus 2"]]},
          f"{night.get('phrases')}")
    check("its named cue, on the beat it falls on",
          night["cues"][-1]["name"] == "drop!" and night["cues"][-1]["slot"] == "A",
          f"{night['cues']}")
    check("its rekordbox id, its audio file, and its source",
          night["ids"]["rekordbox"] == [{"db": "collection:TEST", "id": 101}]
          and night["audio"][0]["path"] == "C:/Music/Night Drive.mp3"
          and night["audio"][0]["size"] == 9000000
          and night["source"]["from"] == "xml", f"{night['ids']} {night.get('audio')}")
    wave = json.loads((show / "waveforms" / f"{night['id']}.json").read_text())
    check("and a waveform the designer can draw",
          base64.b64decode(wave["preview"]) == WAVE and wave["detail"]["format"] == "pwv3")
    check("the unanalysed track has the XML's grid and cue, and no phrases",
          "phrases" not in plain and plain["grid"]["segments"][0][:2] == [0, 50.0]
          and plain["cues"][0]["kind"] == "memory"
          and abs(plain["cues"][0]["beat"] - 32.0) < 1e-6,
          f"{plain['grid']} {plain['cues']}")
    check("and the output says why", "no analysis file found" in out)

    before = {p: p.read_bytes() for p in show.rglob("*.json")}
    code, out = run("--show-dir", str(show), "xml", str(xml),
                    "--anlz-root", str(tmp / "USBANLZ"), "--db", "collection:TEST")
    check("running it again changes nothing", code == 0 and out.count("unchanged") == 2
          and {p: p.read_bytes() for p in show.rglob("*.json")} == before, out)
    wave_path = show / "waveforms" / f"{night['id']}.json"
    wave_path.unlink()
    code, out = run("--show-dir", str(show), "xml", str(xml),
                    "--anlz-root", str(tmp / "USBANLZ"), "--db", "collection:TEST")
    check("a waveform deleted by hand comes back, without touching the track",
          wave_path.exists() and "waveform was missing" in out
          and (show / "tracks" / f"{night['id']}.json").read_bytes()
          == before[show / "tracks" / f"{night['id']}.json"], out)

    # Renamed in rekordbox: same id, so the same track, with the old name kept.
    xml.write_text(XML.replace("{title}", "Night Drive (Club Mix)"), encoding="utf-8")
    code, out = run("--show-dir", str(show), "xml", str(xml),
                    "--anlz-root", str(tmp / "USBANLZ"), "--db", "collection:TEST")
    renamed = sf.load_folder(show).tracks[night["id"]]
    check("a track renamed in rekordbox is updated, not duplicated",
          len(sf.load_folder(show).tracks) == 2
          and renamed["identity"]["title"] == "Night Drive (Club Mix)", out)
    check("and its old name is kept as an alias, so the decks still match it",
          renamed["aliases"][-1]["title"] == "Night Drive (Extended Mix)"
          and renamed["aliases"][-1]["via"] == "renamed in rekordbox", f"{renamed['aliases']}")

    # Re-gridded in rekordbox after a timeline was drawn.
    tl = sf.new_doc("timeline", track=night["id"], grid_rev=renamed["grid"]["rev"],
                    rows=[{"id": "h", "type": "hits", "items": [
                        {"id": "a", "hit": "flash", "at": 96, "len": 1}]}])
    tl_path = sf.path_for(show, "timeline", night["id"])
    sf.write_doc(tl_path, tl, base_rev="")
    tl_bytes = tl_path.read_bytes()
    shifted = anlz_file(ppth("C:/Music/Night Drive.mp3"),
                        pqtz([(n, b, t + 20) for n, b, t in BEATS]), pwav(WAVE))
    (anlz_root / "ANLZ0000.DAT").write_bytes(shifted)
    code, out = run("--show-dir", str(show), "xml", str(xml),
                    "--anlz-root", str(tmp / "USBANLZ"), "--db", "collection:TEST")
    check("a re-gridded track is reported",
          "re-gridded" in out and "drawn on the old grid" in out, out)
    check("and its timeline is never touched", tl_path.read_bytes() == tl_bytes)
    code, out = run("--show-dir", str(show), "report")
    check("report flags the timeline that is now on an old grid",
          code == 0 and "ON AN OLD GRID" in out, out)
    (anlz_root / "ANLZ0000.DAT").write_bytes(DAT)

    # Only one playlist.
    xml.write_text(XML.replace("{title}", "Night Drive (Extended Mix)"), encoding="utf-8")
    fresh = tmp / "friday"
    code, out = run("--show-dir", str(fresh), "xml", str(xml),
                    "--anlz-root", str(tmp / "USBANLZ"), "--playlist", "Friday")
    check("--playlist preps only that playlist's tracks",
          code == 0 and len(sf.load_folder(fresh).tracks) == 1, out)
    code, out = run("--show-dir", str(fresh), "xml", str(xml), "--playlist", "Sunday")
    check("a playlist that is not there says which ones are", code == 2 and "Friday" in out)

    # A dry run writes nothing at all.
    dry = tmp / "dry"
    code, out = run("--show-dir", str(dry), "--dry-run", "xml", str(xml),
                    "--anlz-root", str(tmp / "USBANLZ"))
    check("--dry-run says what it would do and writes nothing",
          code == 0 and "would create" in out and not dry.exists(), out)

    # A USB stick's analysis records the path on the stick.
    usb = tmp / "stick" / "PIONEER" / "USBANLZ" / "P001" / "00000001"
    usb.mkdir(parents=True)
    on_stick = "/Contents/Kolsch/Night Drive.mp3"
    (usb / "ANLZ0000.DAT").write_bytes(anlz_file(ppth(on_stick), pqtz(BEATS), pwav(WAVE)))
    (usb / "ANLZ0000.EXT").write_bytes(anlz_file(
        ppth(on_stick), pssi(1, 163, PHRASES, masked=True)))
    stick = tmp / "from-stick"
    code, out = run("--show-dir", str(stick), "xml", str(xml), "--anlz-root",
                    str(tmp / "stick" / "PIONEER" / "USBANLZ"), "--title", "night")
    got = list(sf.load_folder(stick).tracks.values())
    check("a stick's analysis is joined by file name, phrases and all",
          code == 0 and len(got) == 1 and got[0].get("phrases", {}).get("mood") == "high", out)

    # A damaged analysis file is a warning, and the track still gets a grid.
    broken = tmp / "broken" / "P1"
    broken.mkdir(parents=True)
    (broken / "ANLZ0000.DAT").write_bytes(DAT[:60])
    code, out = run("--show-dir", str(tmp / "b-show"), "xml", str(xml),
                    "--anlz-root", str(tmp / "broken"))
    check("a damaged analysis file is reported, and the XML's grid is used",
          code == 0 and "warn" in out and "ANLZ0000.DAT" in out
          and len(sf.load_folder(tmp / "b-show").tracks) == 2, out)

    not_xml = tmp / "other.xml"
    not_xml.write_text("<playlist/>", encoding="utf-8")
    code, out = run("--show-dir", str(tmp / "x"), "xml", str(not_xml))
    check("an XML file that is not a rekordbox export is refused",
          code == 1 and "not a rekordbox XML export" in out, out)

    # The synthetic track is the example's, exactly.
    syn = tmp / "syn"
    code, out = run("--show-dir", str(syn), "synthetic")
    made = json.loads((syn / "tracks" / "synth-128.json").read_text())
    example = json.loads((REPO / "shared" / "show-example" / "tracks" /
                          "synth-128.json").read_text())
    made.pop("$schema"), example.pop("$schema")
    check("prep synthetic writes exactly the example folder's track",
          code == 0 and made == example, f"{out}")
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("prep: all checks pass")
