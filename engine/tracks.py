"""
Track identity: what makes two descriptions of a track the same track.

Nothing the decks send is stable everywhere. rkbx_link sends only title, artist
and album; a CDJ's rekordbox id differs between a collection and every USB
export made from it; beat-link-trigger's signature survives exports but not a
re-grid. So identity is matched in layers, strongest first, against the prepped
library (F19f). This module holds the part every layer and the prep tool share:
how a title is compared.

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
import unicodedata
from typing import Mapping, Optional

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
