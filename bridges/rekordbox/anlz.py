"""
A reader for rekordbox's analysis files (ANLZ0000.DAT / .EXT / .2EX).

rekordbox writes one set of these per analysed track. They hold what F19 needs
and nothing else in the chain can supply: the beat grid (`PQTZ`), the phrase
analysis (`PSSI`, in .EXT), the waveforms the designer draws (`PWAV`, `PWV3`,
`PWV5`), the cue points (`PCOB`, `PCO2`) and the path of the audio file the
analysis belongs to (`PPTH`) -- which is how a folder of these is joined to a
track list.

Stdlib only, and written against Deep Symmetry's documentation of the format
(crate-digger's `rekordbox_anlz.ksy` and `anlz.adoc`), which is the reference
every Pro DJ Link tool uses. Layouts quoted below are byte offsets from the
start of each tag. Everything is big-endian.

**PSSI is masked in rekordbox 6+ exports.** Every byte after the entry count is
XORed with a 19-byte pattern plus that count. Whether a tag is masked is
detected the way crate-digger does it: the mood field is 1-3 in a clear tag and
much larger in a masked one.

**Refuse, do not guess.** A truncated tag, a file that is not an ANLZ file, a
grid entry that runs past its tag: an AnlzError naming the problem, never a
partial result that looks whole. A tag this reader does not know is skipped and
listed, because rekordbox adds tags and an unknown one is not damage.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


class AnlzError(ValueError):
    """A file that is not a usable rekordbox analysis file."""


@dataclass(frozen=True)
class Beat:
    number: int         # position in the bar, 1 = downbeat
    bpm: float          # the tempo at this beat
    time_ms: int        # when it falls, at 100% pitch


@dataclass(frozen=True)
class PhraseEntry:
    index: int
    beat: int           # 1-based beat number in the grid where it starts
    kind: int
    label: str          # as rekordbox displays it: "Up 2", "Verse 3", "Chorus 1"
    fill_beat: Optional[int] = None


@dataclass(frozen=True)
class Cue:
    hot: int            # 0 for a memory cue, 1 = A, 2 = B, ...
    loop: bool
    time_ms: int
    loop_ms: Optional[int]
    name: str = ""


@dataclass
class Analysis:
    """Everything read from one track's analysis files, merged."""
    path: Optional[str] = None
    beats: list[Beat] = field(default_factory=list)
    mood: Optional[str] = None
    phrases: list[PhraseEntry] = field(default_factory=list)
    phrase_end_beat: Optional[int] = None
    bank: Optional[int] = None
    masked: bool = False
    cues: list[Cue] = field(default_factory=list)
    cues_named: bool = False           # came from PCO2, which carries names
    preview: Optional[bytes] = None    # PWAV: 400 columns
    detail: Optional[bytes] = None     # PWV5 if present, else PWV3
    detail_format: Optional[str] = None
    unknown: list[str] = field(default_factory=list)


MOODS = {1: "high", 2: "mid", 3: "low"}

# PSSI mask base pattern, from crate-digger. Each byte is (base + len_entries).
_MASK = bytes([0xCB, 0xE1, 0xEE, 0xFA, 0xE5, 0xEE, 0xAD, 0xEE, 0xE9, 0xD2,
               0xE9, 0xEB, 0xE1, 0xE9, 0xF3, 0xE8, 0xE9, 0xF4, 0xE1])

_MID = {1: "Intro", 2: "Verse 1", 3: "Verse 2", 4: "Verse 3", 5: "Verse 4",
        6: "Verse 5", 7: "Verse 6", 8: "Bridge", 9: "Chorus", 10: "Outro"}
_LOW = {1: "Intro", 2: "Verse 1", 3: "Verse 1", 4: "Verse 1", 5: "Verse 2",
        6: "Verse 2", 7: "Verse 2", 8: "Bridge", 9: "Chorus", 10: "Outro"}


def phrase_label(mood: Optional[str], kind: int, k1: int, k2: int, k3: int) -> str:
    """The label rekordbox shows. High-mood numbering is not in `kind`; it is
    in three flag bytes, per anlz.adoc's "High mood phrase variants" table."""
    if mood == "high":
        if kind == 1:
            return "Intro 1" if k1 == 1 else "Intro 2"
        if kind == 2:
            if k2 == 1:
                return "Up 3"
            return "Up 2" if k3 == 1 else "Up 1"
        if kind == 3:
            return "Down"
        if kind == 5:
            return "Chorus 1" if k1 == 1 else "Chorus 2"
        if kind == 6:
            return "Outro 1" if k1 == 1 else "Outro 2"
        return f"Phrase {kind}"
    table = _LOW if mood == "low" else _MID
    return table.get(kind, f"Phrase {kind}")


# -- the file -----------------------------------------------------------------

def parse(data: bytes, into: Optional[Analysis] = None) -> Analysis:
    """Parse one ANLZ file. Pass `into` to merge a .EXT into its .DAT."""
    out = into if into is not None else Analysis()
    if len(data) < 12 or data[:4] != b"PMAI":
        raise AnlzError("not a rekordbox analysis file (no PMAI header)")
    len_header, len_file = struct.unpack_from(">II", data, 4)
    if len_header < 12 or len_header > len(data):
        raise AnlzError(f"header length {len_header} does not fit the file")
    if len_file > len(data):
        raise AnlzError(f"truncated: the header says {len_file} bytes, "
                        f"the file has {len(data)}")
    pos = len_header
    end = len_file or len(data)
    while pos + 12 <= end:
        tag = data[pos:pos + 4]
        _, len_tag = struct.unpack_from(">II", data, pos + 4)
        if len_tag < 12 or pos + len_tag > end:
            raise AnlzError(f"tag {_name(tag)} at byte {pos} claims {len_tag} "
                            f"bytes and runs past the end of the file")
        body = data[pos + 12:pos + len_tag]
        handler = _TAGS.get(tag)
        if handler is None:
            out.unknown.append(_name(tag))
        else:
            handler(body, out)
        pos += len_tag
    if pos != end:
        raise AnlzError(f"{end - pos} stray bytes after the last tag")
    return out


def read_files(*paths: Path) -> Analysis:
    """A track's .DAT, .EXT and .2EX, merged into one Analysis. Order does not
    matter; a missing sibling is simply absent."""
    out = Analysis()
    for path in paths:
        try:
            data = Path(path).read_bytes()
        except FileNotFoundError:
            continue
        try:
            parse(data, out)
        except AnlzError as exc:
            raise AnlzError(f"{Path(path).name}: {exc}") from None
    return out


def siblings(dat: Path) -> list[Path]:
    """ANLZ0000.DAT and whichever of its .EXT and .2EX exist beside it."""
    dat = Path(dat)
    found = [dat]
    for ext in (".EXT", ".2EX", ".ext", ".2ex"):
        p = dat.with_suffix(ext)
        if p.exists() and p not in found:
            found.append(p)
    return found


def _name(tag: bytes) -> str:
    try:
        return tag.decode("ascii")
    except UnicodeDecodeError:
        return tag.hex()


def _need(body: bytes, n: int, what: str) -> None:
    if len(body) < n:
        raise AnlzError(f"{what} is truncated: needs {n} bytes, has {len(body)}")


# -- tags ---------------------------------------------------------------------
# Offsets are within the tag's BODY, which starts after the 12-byte common
# header (fourcc, len_header, len_tag).

def _pqtz(body: bytes, out: Analysis) -> None:
    """Beat grid. u4 ?, u4 ?, u4 num_beats, then num_beats x
    (u2 beat_number, u2 tempo*100, u4 time_ms)."""
    _need(body, 12, "PQTZ")
    (count,) = struct.unpack_from(">I", body, 8)
    _need(body, 12 + 8 * count, f"PQTZ with {count} beats")
    beats = []
    for i in range(count):
        number, tempo, time_ms = struct.unpack_from(">HHI", body, 12 + 8 * i)
        beats.append(Beat(number, tempo / 100.0, time_ms))
    out.beats = beats


def _ppth(body: bytes, out: Analysis) -> None:
    """Path of the audio file. u4 len_path, then UTF-16BE with a trailing NUL."""
    _need(body, 4, "PPTH")
    (length,) = struct.unpack_from(">I", body, 0)
    if length <= 1:
        return
    _need(body, 4 + length, "PPTH path")
    raw = body[4:4 + length - 2]
    try:
        out.path = raw.decode("utf-16-be")
    except UnicodeDecodeError:
        raise AnlzError("PPTH path is not valid UTF-16") from None


def _pwav(body: bytes, out: Analysis) -> None:
    """Waveform preview. u4 len_data, u4 ?, then len_data bytes."""
    _need(body, 8, "PWAV")
    (length,) = struct.unpack_from(">I", body, 0)
    _need(body, 8 + length, "PWAV data")
    out.preview = bytes(body[8:8 + length])


def _detail(fmt: str):
    def handle(body: bytes, out: Analysis) -> None:
        """Scrolling waveform. u4 entry_bytes, u4 entries, u4 ?, then data."""
        _need(body, 12, fmt.upper())
        size, count = struct.unpack_from(">II", body, 0)
        _need(body, 12 + size * count, f"{fmt.upper()} data")
        # The color detail wins over the blue one when both are present.
        if out.detail_format == "pwv5" and fmt == "pwv3":
            return
        out.detail = bytes(body[12:12 + size * count])
        out.detail_format = fmt
    return handle


def _pssi(body: bytes, out: Analysis) -> None:
    """Song structure. u4 entry_bytes (24), u2 len_entries, then -- masked in
    rekordbox 6+ -- u2 mood, 6 ?, u2 end_beat, 2 ?, u1 bank, 1 ?, entries."""
    _need(body, 6, "PSSI")
    entry_bytes, count = struct.unpack_from(">IH", body, 0)
    if entry_bytes < 24:
        raise AnlzError(f"PSSI entries are {entry_bytes} bytes; at least 24 "
                        f"were expected")
    rest = bytearray(body[6:])
    _need(rest, 14 + entry_bytes * count, f"PSSI with {count} phrases")
    (raw_mood,) = struct.unpack_from(">H", rest, 0)
    out.masked = raw_mood > 20
    if out.masked:
        for i in range(len(rest)):
            rest[i] ^= (_MASK[i % len(_MASK)] + count) & 0xFF
    mood_id, end_beat = struct.unpack_from(">H6xH", rest, 0)
    bank = rest[12]
    if mood_id not in MOODS:
        raise AnlzError(f"PSSI mood {mood_id} is not 1-3, even after "
                        f"unmasking; this is not a phrase map this reader knows")
    mood = MOODS[mood_id]
    phrases = []
    for i in range(count):
        o = 14 + i * entry_bytes
        index, beat, kind = struct.unpack_from(">HHH", rest, o)
        k1, k2 = rest[o + 7], rest[o + 9]
        k3, fill = rest[o + 19], rest[o + 21]
        (beat_fill,) = struct.unpack_from(">H", rest, o + 22)
        phrases.append(PhraseEntry(index, beat, kind,
                                   phrase_label(mood, kind, k1, k2, k3),
                                   beat_fill if fill else None))
    out.mood = mood
    out.phrase_end_beat = end_beat
    out.bank = bank
    out.phrases = phrases


def _pcob(body: bytes, out: Analysis) -> None:
    """Cue list. u4 type, 2 ?, u2 num_cues, u4 ?, then PCPT entries."""
    if out.cues_named:
        return                      # PCO2 already supplied these, with names
    _need(body, 12, "PCOB")
    (count,) = struct.unpack_from(">H", body, 6)
    pos = 12
    for _ in range(count):
        _need(body, pos + 12, "PCPT entry")
        if body[pos:pos + 4] != b"PCPT":
            raise AnlzError(f"PCOB entry at {pos} is not a PCPT")
        (len_entry,) = struct.unpack_from(">I", body, pos + 8)
        _need(body, pos + max(len_entry, 40), "PCPT entry")
        hot, status = struct.unpack_from(">II", body, pos + 12)
        kind = body[pos + 28]
        time_ms, loop_ms = struct.unpack_from(">II", body, pos + 32)
        if status != 0:             # 0 is a disabled entry
            out.cues.append(Cue(hot, kind == 2, time_ms,
                                loop_ms if kind == 2 else None))
        pos += len_entry


def _pco2(body: bytes, out: Analysis) -> None:
    """Extended cue list, with names. u4 type, u2 num_cues, 2 ?, then PCP2."""
    _need(body, 8, "PCO2")
    (count,) = struct.unpack_from(">H", body, 4)
    if not out.cues_named:
        out.cues = []               # replaces anything PCOB supplied
        out.cues_named = True
    pos = 8
    for _ in range(count):
        _need(body, pos + 12, "PCP2 entry")
        if body[pos:pos + 4] != b"PCP2":
            raise AnlzError(f"PCO2 entry at {pos} is not a PCP2")
        (len_entry,) = struct.unpack_from(">I", body, pos + 8)
        _need(body, pos + max(len_entry, 28), "PCP2 entry")
        (hot,) = struct.unpack_from(">I", body, pos + 12)
        kind = body[pos + 16]
        time_ms, loop_ms = struct.unpack_from(">II", body, pos + 20)
        name = ""
        if len_entry > 43:
            (len_comment,) = struct.unpack_from(">I", body, pos + 40)
            raw = body[pos + 44:pos + 44 + len_comment]
            name = raw.decode("utf-16-be", errors="replace").rstrip("\x00")
        out.cues.append(Cue(hot, kind == 2, time_ms,
                            loop_ms if kind == 2 else None, name))
        pos += len_entry


_TAGS = {
    b"PQTZ": _pqtz, b"PPTH": _ppth, b"PWAV": _pwav,
    b"PWV3": _detail("pwv3"), b"PWV5": _detail("pwv5"),
    b"PSSI": _pssi, b"PCOB": _pcob, b"PCO2": _pco2,
}
