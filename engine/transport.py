"""
Which track the DJ is playing, and where in it -- estimated between packets.

The clock (`clock.py`) knows the tempo and the bar. This knows the TRACK: who it
is, and the position in its audio, in seconds, at any instant. A timeline is
drawn against that position, so this is what makes "at bar 41 of this track"
land on bar 41.

The sources are imperfect in different ways, and the estimator is shaped by
them:

- **rkbx_link** sends `/master/time` at roughly 60 Hz, from a Windows timer, so
  packets jitter by a few milliseconds. It sends nothing at all while the deck
  is paused -- silence is its only pause signal, and it cannot be told apart
  from a dead bridge. Title, artist and album arrive as SEPARATE messages, so a
  track change is spread over several packets.
- **beat-link-trigger** sends position, a playing flag and the whole identity
  in single messages (our `/klights/v1` namespace), 25 times a second.
- **the designer** (F19j) sends the browser's audio position.

**The estimate.** Between packets, position is a line: `p0 + rate * (t - t0)`.
Each packet re-anchors the line a fifth of the way towards what it says -- the
same trick as the clock's re-anchoring, so jitter is smoothed and the estimate
never jumps on an ordinary packet. `rate` is the pitch: `bpm / bpm_original`
from rkbx_link, `pitch` from beat-link-trigger, or measured from successive
packets when neither is sent. Extrapolation stops `stall_after` past the last
packet, so a dropped bridge freezes the estimate rather than running it on
forever.

**Jumps** -- a loop, a hot cue, a needle drop -- are packets that disagree with
the line by more than a little: forward by over 0.2 s, or backward by over
50 ms. Audio played forward never goes backwards, so a backward disagreement
is a jump unless it is small enough to be packet jitter; 50 ms is several times
the jitter seen from a 60 Hz Windows sender and well under the shortest loop.
A jump re-anchors outright and bumps `jump_seq`, so whatever is following the
position can tell a jump from motion and chase to the new place rather than
firing everything it skipped. After a gap in the packets nothing is a jump that
lies between "the deck stopped" and "the deck played on": the estimator cannot
know which happened, and a resume after a pause is not a hot cue.

From a source that sends its identity a field at a time, a jump is held for
`jump_settle_s` (30 ms) before it counts. A master switch can deliver the new
deck's position a moment BEFORE its title; held, that position becomes the new
track's starting point when the title lands, instead of a jump in the old track
that something downstream has already chased. beat-link-trigger sends identity
in one message, so its jumps count at once.

**Scratching** is many small backward jumps in a row. Four inside a second holds
the position where the scratch began until the deck has run forward again for a
quarter of a second, so the lights do not twitch with the platter.

**Track changes** are a new identity. beat-link-trigger sends one atomically.
rkbx_link sends the pieces one at a time, so a change waits a short settle
window for the rest; a position packet that arrives inside the window is held,
and on commit it is either the new track's starting point or, if the identity
did not actually change, an ordinary packet. Without that, the first packet of
the new deck's position would be read as a jump in the OLD track.

**Thread safety.** All state lives in one frozen value, replaced by `ingest` on
the output thread and read by `sample` from any thread with one reference load.
No locks, and no torn reads by the 10 Hz snapshot.

Pure: no clock, no I/O. Every call takes the time from its caller.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Mapping, Optional

# States, as the console shows them.
NO_TRACK = "no_track"
PLAYING = "playing"
STALLED = "stalled"      # packets late: holding the estimate, still "playing"
PAUSED = "paused"
REVERSE = "reverse"      # scratching: holding where it began

JUMP_FORWARD_S = 0.2
JUMP_BACKWARD_S = 0.05
CORRECTION = 0.2          # how far each packet pulls the line towards itself
STALL_MIN_S = 0.15
SCRATCH_JUMPS = 4         # backward jumps inside SCRATCH_WINDOW_S to call it a scratch
SCRATCH_WINDOW_S = 1.0
SCRATCH_RELEASE_S = 0.25

IDENTITY_FIELDS = ("title", "artist", "album", "duration", "rekordbox_id",
                   "signature")


@dataclass(frozen=True)
class Identity:
    """Who the track is, as the source described it."""
    title: str = ""
    artist: str = ""
    album: str = ""
    duration: Optional[float] = None
    rekordbox_id: Optional[int] = None
    signature: Optional[str] = None

    @property
    def known(self) -> bool:
        return bool(self.title) or self.rekordbox_id is not None \
            or self.signature is not None

    def same_track(self, other: "Identity") -> bool:
        """Duration is not part of sameness: rkbx_link never sends it, and a
        later packet that adds it is not a new track."""
        return (self.title, self.artist, self.album, self.rekordbox_id,
                self.signature) == (other.title, other.artist, other.album,
                                    other.rekordbox_id, other.signature)


@dataclass(frozen=True)
class TrackSample:
    """The transport at one instant. What everything downstream reads."""
    state: str
    identity: Identity
    time_s: Optional[float]         # position in the audio, latency applied
    rate: float                     # audio seconds per wall second
    source: Optional[str]
    deck: Optional[str]
    track_seq: int                  # bumps on every track change
    jump_seq: int                   # bumps on every loop, hot cue or seek
    age: Optional[float]            # seconds since the last position packet
    beat_number: Optional[int] = None
    beat_in_bar: Optional[float] = None
    on_air: Optional[bool] = None
    # Position at the instant asked about, with NO latency applied -- where the
    # deck itself is, for comparing against what the deck says about its own
    # beats (the grid cross-check), which carries the same lack of latency.
    raw_time_s: Optional[float] = None


@dataclass(frozen=True)
class _State:
    identity: Identity = Identity()
    pending: Optional[Identity] = None      # identity awaiting its settle time
    pending_until: float = 0.0
    held: Optional[tuple[float, float]] = None   # (position, at) during settle
    atomic: bool = False                    # this source sends whole identities
    track_seq: int = 0
    jump_seq: int = 0
    last_change: Optional[float] = None
    # the line
    p0: Optional[float] = None
    t0: float = 0.0
    last_p: Optional[float] = None
    last_t: Optional[float] = None
    interval: float = 1 / 30
    # a jump not yet counted: (detected at, the old line's position then,
    # latest position, latest at)
    jump_hold: Optional[tuple[float, float, float, float]] = None
    # rate
    bpm: Optional[float] = None
    bpm_original: Optional[float] = None
    pitch: Optional[float] = None
    measured_rate: float = 1.0
    playing: Optional[bool] = None
    # scratching
    back_jumps: tuple[float, ...] = ()
    scratch_hold: Optional[float] = None
    burst_start: Optional[float] = None
    # labels
    source: Optional[str] = None
    deck: Optional[str] = None
    beat_number: Optional[int] = None
    beat_in_bar: Optional[float] = None
    on_air: Optional[bool] = None

    @property
    def rate(self) -> float:
        if self.playing is False:
            return 0.0
        if self.pitch is not None:
            return self.pitch
        if self.bpm and self.bpm_original:
            return self.bpm / self.bpm_original
        return self.measured_rate

    @property
    def stall_after(self) -> float:
        return max(STALL_MIN_S, 3 * self.interval)


class TrackTransport:
    """One DJ source's transport. `ingest` on the output thread; `sample`
    from anywhere."""

    def __init__(self, settle_s: float = 0.1, grace_s: float = 4.0,
                 min_track_change_s: float = 0.0, jump_settle_s: float = 0.03,
                 latency_s: Optional[Mapping[str, float]] = None):
        self.settle_s = settle_s
        self.grace_s = grace_s
        self.min_track_change_s = min_track_change_s
        self.jump_settle_s = jump_settle_s
        self.latency_s = dict(latency_s or {})
        self._st = _State()

    # -- writing -------------------------------------------------------------

    def clear(self) -> None:
        """Forget everything -- the operator took the clock back."""
        self._st = _State(track_seq=self._st.track_seq + 1,
                          jump_seq=self._st.jump_seq)

    def ingest(self, fields: Mapping, at: float) -> None:
        """One cleaned sync message (see `sync.clean`), arriving at `at`."""
        st = self._due(self._st, at)
        labels = {k: fields[k] for k in
                  ("source", "deck", "beat_number", "beat_in_bar", "on_air",
                   "bpm", "bpm_original", "pitch") if k in fields}
        if labels:
            st = replace(st, **labels)
        if "playing" in fields:
            st = self._set_playing(st, bool(fields["playing"]), at)
        if any(k in fields for k in IDENTITY_FIELDS):
            st = self._identity(st, fields, at)
        if "track_time" in fields:
            p = float(fields["track_time"])
            if st.pending is not None:
                st = replace(st, held=(p, at))      # wait for the identity
            else:
                st = self._position(st, p, at)
        self._st = self._due(st, at)

    # -- reading -------------------------------------------------------------

    def sample(self, now: float) -> TrackSample:
        # One load of the state, then anything already due applied to a COPY:
        # a track loaded while the deck is paused sends no further packets, and
        # must still show up. Pure, so the snapshot thread cannot disturb it.
        st = self._due(self._st, now)
        at = now + self.latency_s.get(st.source or "", 0.0)
        age = None if st.last_t is None else max(0.0, now - st.last_t)
        common = dict(identity=st.identity, source=st.source, deck=st.deck,
                      track_seq=st.track_seq, jump_seq=st.jump_seq, age=age,
                      beat_number=st.beat_number, beat_in_bar=st.beat_in_bar,
                      on_air=st.on_air)
        if not st.identity.known:
            return TrackSample(state=NO_TRACK, time_s=None, rate=0.0, **common)
        if st.p0 is None:
            return TrackSample(state=PAUSED, time_s=None, rate=0.0, **common)
        if st.playing is False:
            return TrackSample(state=PAUSED, time_s=st.p0, rate=0.0,
                               raw_time_s=st.p0, **common)
        if st.scratch_hold is not None and \
                now - st.back_jumps[-1] < SCRATCH_RELEASE_S:
            return TrackSample(state=REVERSE, time_s=st.scratch_hold, rate=0.0,
                               raw_time_s=st.scratch_hold, **common)
        rate = st.rate
        elapsed = min(max(0.0, at - st.t0), st.stall_after)
        time_s = st.p0 + rate * elapsed
        raw = st.p0 + rate * min(max(0.0, now - st.t0), st.stall_after)
        if age is not None and age > self.grace_s:
            return TrackSample(state=PAUSED, time_s=time_s, rate=0.0,
                               raw_time_s=raw, **common)
        if age is not None and age > st.stall_after:
            return TrackSample(state=STALLED, time_s=time_s, rate=rate,
                               raw_time_s=raw, **common)
        return TrackSample(state=PLAYING, time_s=time_s, rate=rate,
                           raw_time_s=raw, **common)

    # -- the rules -----------------------------------------------------------

    def _identity(self, st: _State, fields: Mapping, at: float) -> _State:
        base = st.pending or st.identity
        values = {k: getattr(base, k) for k in IDENTITY_FIELDS}
        for key in IDENTITY_FIELDS:
            if key in fields:
                values[key] = fields[key]
        candidate = Identity(**values)
        # beat-link-trigger sends who the track is in one message; rkbx_link
        # sends it a field at a time. Only the second needs to wait.
        atomic = "rekordbox_id" in fields or "signature" in fields
        if atomic:
            st = replace(st, atomic=True)
        if candidate == st.identity and st.pending is None:
            return st
        if st.jump_hold is not None:
            # The "jump" was the new deck's position arriving ahead of its
            # name. It belongs to the incoming track, not the outgoing one.
            st = replace(st, held=st.jump_hold[2:], jump_hold=None)
        return replace(st, pending=candidate,
                       pending_until=at if atomic else at + self.settle_s)

    def _due(self, st: _State, at: float) -> _State:
        """Apply whatever has waited long enough. Pure."""
        if st.pending is not None and at >= st.pending_until:
            st = self._commit_identity(st, at)
        if (st.jump_hold is not None and st.pending is None
                and at >= st.jump_hold[0] + self.jump_settle_s):
            detected, old_p, p, p_at = st.jump_hold
            st = self._jump(replace(st, jump_hold=None), p, p_at, old_p, detected)
        return st

    def _commit_identity(self, st: _State, at: float) -> _State:
        if (self.min_track_change_s and st.last_change is not None
                and not st.pending.same_track(st.identity)
                and at - st.last_change < self.min_track_change_s):
            return st                   # a storm of changes waits its turn
        pending, held = st.pending, st.held
        st = replace(st, pending=None, held=None)
        if pending.same_track(st.identity):
            st = replace(st, identity=pending)      # e.g. duration filled in
            return self._position(st, *held) if held else st
        # A new track. Its position starts from scratch -- the held packet, if
        # one came in the settle window, is its first.
        st = replace(st, identity=pending, track_seq=st.track_seq + 1,
                     last_change=at, p0=None, last_p=None, last_t=None,
                     measured_rate=1.0, back_jumps=(), scratch_hold=None,
                     burst_start=None, beat_number=None, beat_in_bar=None,
                     jump_hold=None)
        return self._position(st, *held) if held else st

    def _set_playing(self, st: _State, playing: bool, at: float) -> _State:
        if st.playing == playing:
            return st
        if st.p0 is not None:
            # Freeze (or restart) the line exactly where it is now.
            here = st.p0 + st.rate * min(max(0.0, at - st.t0), st.stall_after)
            st = replace(st, p0=here, t0=at)
        return replace(st, playing=playing)

    def _position(self, st: _State, p: float, at: float) -> _State:
        if st.jump_hold is not None:
            # Follow the deck to wherever it went until the jump counts.
            detected, old_p, _, _ = st.jump_hold
            return replace(st, jump_hold=(detected, old_p, p, at))
        if st.p0 is None:
            return replace(st, p0=p, t0=at, last_p=p, last_t=at)
        interval = st.interval
        gap = 0.0 if st.last_t is None else at - st.last_t
        if gap > 0:
            interval = 0.9 * st.interval + 0.1 * min(gap, 1.0)
        if gap > st.stall_after and st.last_p is not None:
            # After a gap, anything between "it stopped" and "it played on" is
            # plausible. Only something outside that is a jump.
            lo = st.last_p - JUMP_BACKWARD_S
            hi = st.last_p + max(st.rate, 1.0) * gap + JUMP_FORWARD_S
            if lo <= p <= hi:
                return replace(st, p0=p, t0=at, last_p=p, last_t=at,
                               interval=interval)
            predicted = st.last_p
        else:
            predicted = st.p0 + st.rate * max(0.0, at - st.t0)
        error = p - predicted
        if error > JUMP_FORWARD_S or error < -JUMP_BACKWARD_S:
            if not st.atomic and self.jump_settle_s > 0:
                return replace(st, jump_hold=(at, predicted, p, at),
                               interval=interval)
            return self._jump(replace(st, interval=interval), p, at,
                              predicted, at)
        # Ordinary motion. Learn the rate if nobody is telling us it, then pull
        # the line a fraction of the way to this packet.
        measured = st.measured_rate
        if st.last_p is not None and st.last_t is not None and at > st.last_t:
            instant = (p - st.last_p) / (at - st.last_t)
            if 0.0 <= instant <= 4.0:
                measured = 0.9 * measured + 0.1 * instant
        hold, burst = st.scratch_hold, st.burst_start
        if st.back_jumps and at - st.back_jumps[-1] >= SCRATCH_RELEASE_S:
            hold = None                             # running forward again
        if st.back_jumps and at - st.back_jumps[-1] > SCRATCH_WINDOW_S:
            burst = None                            # that burst is over
        return replace(st, p0=predicted + CORRECTION * error, t0=at,
                       last_p=p, last_t=at, interval=interval,
                       measured_rate=measured, scratch_hold=hold,
                       burst_start=burst)

    def _jump(self, st: _State, p: float, at: float, old_p: float,
              detected: float) -> _State:
        """Count a jump: re-anchor at `p`, and if it went backwards, see
        whether it is part of a scratch."""
        back, hold, burst = st.back_jumps, st.scratch_hold, st.burst_start
        if p < old_p:
            back = tuple(t for t in back if detected - t <= SCRATCH_WINDOW_S)
            if burst is None or not back:
                burst = old_p                       # where the scratch began
            back = back + (detected,)
            if len(back) >= SCRATCH_JUMPS:
                hold = burst
        return replace(st, p0=p, t0=at, last_p=p, last_t=at,
                       jump_seq=st.jump_seq + 1, back_jumps=back,
                       scratch_hold=hold, burst_start=burst)
