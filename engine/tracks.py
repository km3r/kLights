"""
Track identity: what makes two descriptions of a track the same track.

Nothing the decks send is stable everywhere. rkbx_link sends only title, artist
and album; a CDJ's rekordbox id differs between a collection and every USB
export made from it; beat-link-trigger's signature survives exports but not a
re-grid. So identity is matched in layers, strongest first, against the prepped
library. This module holds how a title is compared (which the prep tool shares),
the layered matcher itself, and the cross-check that catches a match the beats
disagree with.

**Normalisation is deliberately conservative.** It forgives what differs between
two honest descriptions of one track -- case, accents, "feat." against "ft.",
"&" against "and", punctuation and spacing. It does NOT forgive what makes two
tracks different: "Original Mix" and "Extended Mix" are different audio with
different grids, and a matcher that merged them would play one track's timeline
over the other's beats.

Pure.
"""

from __future__ import annotations

import hashlib
import re
import statistics
import unicodedata
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from . import tracktime

_FEAT_RE = re.compile(r"\b(?:featuring|feat|ft)\b\.?")
_NON_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)


def normalize(text: Optional[str]) -> str:
    """A form of `text` for comparing, never for display."""
    if not text:
        return ""
    t = unicodedata.normalize("NFKD", text)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = t.casefold()
    t = t.replace("&", " and ")
    t = _FEAT_RE.sub(" feat ", t)
    t = _NON_WORD_RE.sub(" ", t)
    return " ".join(t.split())


def identity_key(identity: Mapping) -> tuple[str, str, str]:
    """(title, artist, album), each normalised. Album is part of the key
    because rkbx_link sends it and a single and its album version can differ."""
    return (normalize(identity.get("title")), normalize(identity.get("artist")),
            normalize(identity.get("album")))


def same_duration(a: Optional[float], b: Optional[float],
                  tolerance_s: float = 1.5) -> bool:
    """Two durations that could be one file. Unknown on either side is not
    evidence against a match -- rkbx_link does not send duration at all."""
    if a is None or b is None:
        return True
    return abs(float(a) - float(b)) <= tolerance_s


def slug(identity: Mapping, taken: frozenset[str] = frozenset()) -> str:
    """A file-name id for a newly prepped track: readable, lower-case, unique
    among `taken`. Falls back to a short hash for titles with no Latin letters
    to keep, so a track called entirely in kana still gets an id."""
    base = normalize(f"{identity.get('artist', '')} {identity.get('title', '')}")
    base = re.sub(r"[^a-z0-9]+", "-", base.encode("ascii", "ignore").decode()).strip("-")
    base = base[:50].rstrip("-")
    if not base:
        digest = hashlib.sha1(repr(sorted(identity.items())).encode()).hexdigest()
        base = "track-" + digest[:8]
    candidate, n = base, 2
    while candidate in taken:
        candidate = f"{base}-{n}"
        n += 1
    return candidate


# -- matching -----------------------------------------------------------------

# How a match was made, strongest first. The console shows it, because "matched
# by title and artist" and "matched by signature" deserve different amounts of
# trust from an operator about to arm a show.
SIGNATURE = "signature"
REKORDBOX_ID = "rekordbox_id"
ALIAS = "alias"
TITLE_ARTIST_ALBUM = "title_artist_album"
TITLE_ARTIST = "title_artist"
AMBIGUOUS = "ambiguous"
NONE = "none"


@dataclass(frozen=True)
class Match:
    """Which prepped track a deck's description is, and how sure."""
    track_id: Optional[str]
    via: str
    candidates: tuple[str, ...] = ()

    @property
    def matched(self) -> bool:
        return self.track_id is not None

    def public(self) -> dict:
        return {"track_id": self.track_id, "via": self.via,
                "candidates": list(self.candidates[:5])}


def _add(table: dict, key: Any, track_id: str) -> None:
    ids = table.setdefault(key, [])
    if track_id not in ids:
        ids.append(track_id)


@dataclass(frozen=True)
class TrackIndex:
    """The library's tracks, indexed for each matching layer. Built once per
    library load (on the worker); matching is then dictionary lookups, cheap
    enough for the output thread."""
    by_signature: Mapping[str, tuple[str, ...]] = field(default_factory=dict)
    by_rekordbox: Mapping[int, tuple[str, ...]] = field(default_factory=dict)
    by_alias: Mapping[tuple, tuple[str, ...]] = field(default_factory=dict)
    by_title_artist_album: Mapping[tuple, tuple[str, ...]] = field(default_factory=dict)
    by_title_artist: Mapping[tuple, tuple[str, ...]] = field(default_factory=dict)
    titles: Mapping[str, frozenset[str]] = field(default_factory=dict)
    durations: Mapping[str, Optional[float]] = field(default_factory=dict)

    @classmethod
    def build(cls, tracks: Mapping[str, Mapping]) -> "TrackIndex":
        """From `{track_id: track doc}` -- documents that already validated."""
        sig: dict = {}
        rb: dict = {}
        alias: dict = {}
        taa: dict = {}
        ta: dict = {}
        titles: dict = {}
        durations: dict = {}
        for track_id in sorted(tracks):
            doc = tracks[track_id]
            ident = doc.get("identity") or {}
            ids = doc.get("ids") or {}
            for s in ids.get("blt_signatures") or ():
                _add(sig, s, track_id)
            for entry in ids.get("rekordbox") or ():
                _add(rb, int(entry["id"]), track_id)
            key = identity_key(ident)
            names = {key[0]}
            if key[0]:
                _add(taa, key, track_id)
                _add(ta, key[:2], track_id)
            for a in doc.get("aliases") or ():
                akey = identity_key(a)
                if akey[0]:
                    _add(alias, akey, track_id)
                    names.add(akey[0])
            titles[track_id] = frozenset(n for n in names if n)
            durations[track_id] = ident.get("duration_s")

        def freeze(table: dict) -> dict:
            return {k: tuple(v) for k, v in table.items()}

        return cls(by_signature=freeze(sig), by_rekordbox=freeze(rb),
                   by_alias=freeze(alias), by_title_artist_album=freeze(taa),
                   by_title_artist=freeze(ta), titles=titles,
                   durations=durations)

    def __len__(self) -> int:
        return len(self.titles)

    def match(self, title: str = "", artist: str = "", album: str = "",
              duration: Optional[float] = None,
              rekordbox_id: Optional[int] = None,
              signature: Optional[str] = None) -> Match:
        """The layered match, strongest layer first; the first layer with any
        hit decides.

        1. **signature** -- beat-link-trigger's hash of the audio's analysis.
           Exact, and survives a USB export, so nothing else is asked.
        2. **rekordbox id**, but only if the title agrees: an id is a row number
           in ONE rekordbox database, and every USB stick numbers from 1. Track
           1 on the guest's stick is not track 1 in this collection.
        3. **alias** -- a description someone linked by hand (`track_link`).
        4. **title + artist + album**, then 5. **title + artist**, each kept
           only where the durations could be one file. Duration is not
           evidence when either side lacks it: rkbx_link never sends one.

        More than one track at the deciding layer is AMBIGUOUS, never a guess:
        playing the wrong timeline is worse than playing none.
        """
        if signature:
            hit = self.by_signature.get(signature)
            if hit:
                return self._decide(hit, SIGNATURE)
        t = normalize(title)
        if rekordbox_id is not None and t:
            hit = tuple(i for i in self.by_rekordbox.get(int(rekordbox_id), ())
                        if t in self.titles.get(i, ()))
            if hit:
                return self._decide(hit, REKORDBOX_ID)
        if not t:
            return Match(None, NONE)
        key = (t, normalize(artist), normalize(album))
        for table, k, via in ((self.by_alias, key, ALIAS),
                              (self.by_title_artist_album, key, TITLE_ARTIST_ALBUM),
                              (self.by_title_artist, key[:2], TITLE_ARTIST)):
            hit = tuple(i for i in table.get(k, ())
                        if same_duration(duration, self.durations.get(i)))
            if hit:
                return self._decide(hit, via)
        return Match(None, NONE)

    @staticmethod
    def _decide(hit: tuple[str, ...], via: str) -> Match:
        if len(hit) == 1:
            return Match(hit[0], via, hit)
        return Match(None, AMBIGUOUS, hit)


def alias_for(title: str, artist: str = "", album: str = "",
              added: str = "") -> dict:
    """The alias `track_link` records: the deck's description, as it sent it."""
    alias = {"title": title, "artist": artist, "album": album, "via": "manual"}
    if added:
        alias["added"] = added
    return alias


def has_description(doc: Mapping, alias: Mapping) -> bool:
    """Whether a track already answers to this description, by its own identity
    or an alias -- so linking twice does not grow the file."""
    key = identity_key(alias)
    if identity_key(doc.get("identity") or {}) == key:
        return True
    return any(identity_key(a) == key for a in doc.get("aliases") or ())


# -- does the deck agree with the grid? ---------------------------------------

PHASE_TOLERANCE = 0.1       # beats: rkbx_link's bar phase vs the prepped grid
NUMBER_TOLERANCE = 0.75     # beats: beat-link's beat count vs the prepped grid
CHECK_WINDOW_S = 2.0


def _wrap(beats: float, period: float) -> float:
    """Into [-period/2, period/2)."""
    return (beats + period / 2) % period - period / 2


@dataclass
class GridCheck:
    """Does what the deck says about its beats agree with the grid we prepped?

    A track can match by name and still be the wrong grid: re-gridded in
    rekordbox after it was prepped, or a different version under the same
    title. Every cue in its timeline would then land beside the beat. The decks
    say where they think the beat is, so this compares:

    - **bar phase** (rkbx_link's `beat/subdiv/4` ramp, 0-4 over a bar) against
      the grid's beat at the same position, modulo a bar. Sensitive to a
      fraction of a beat; blind to a whole bar, which phase cannot see.
    - **beat count** (beat-link's `beat_number`, 1 at the grid's first beat)
      against the grid's beat at the position in the same packet. Coarse --
      the count is a whole number and old players report it a few times a
      second -- so it catches a beat or more, the size a moved downbeat is.

    `warning["offset_beats"]` is how far AHEAD of the grid the deck puts the
    beat. The median over a 2 s window decides, so one late packet is never a
    warning and a real offset always is. Clears the same way. A warning only:
    the designer offers the re-anchor, because "the grid moved" and "the track
    is a different edit" need a person to tell apart.

    Mutated on the output thread; `warning` is replaced, never edited, so the
    snapshot thread can read it.
    """
    grid: tracktime.Grid
    window_s: float = CHECK_WINDOW_S
    _seen: list = field(default_factory=list)       # (at, kind, offset)
    warning: Optional[dict] = None

    def reset(self) -> None:
        self._seen = []

    def phase(self, beat_in_bar: float, time_s: float, at: float) -> None:
        bars = tracktime.BEATS_PER_BAR
        grid_phase = self.grid.beat_at(time_s) % bars
        self._observe(at, "phase", _wrap(beat_in_bar - grid_phase, bars))

    def number(self, beat_number: int, time_s: float, at: float) -> None:
        # beat_number counts from 1 at the grid's FIRST BEAT, which is the
        # grid's first anchor; beat 0 here is the first downbeat. In phase, the
        # position is somewhere inside that beat: 0..1 past its start, plus
        # however stale the count is. Centred on half a beat.
        start = beat_number - 1 + self.grid.beats[0]
        self._observe(at, "number", start + 0.5 - self.grid.beat_at(time_s))

    def _observe(self, at: float, kind: str, offset: float) -> None:
        seen = [s for s in self._seen
                if s[1] == kind and at - s[0] <= self.window_s]
        seen.append((at, kind, offset))
        self._seen = seen
        if at - seen[0][0] < self.window_s * 0.9:
            return                       # not enough of a window to judge yet
        median = statistics.median(s[2] for s in seen)
        tolerance = PHASE_TOLERANCE if kind == "phase" else NUMBER_TOLERANCE
        if abs(median) > tolerance:
            if kind == "number":
                median = float(round(median))
            if (self.warning is None or self.warning["kind"] != kind
                    or abs(self.warning["offset_beats"] - median) > 0.05):
                self.warning = {"kind": kind,
                                "offset_beats": round(median, 2)}
        elif self.warning is not None and self.warning["kind"] == kind:
            self.warning = None
