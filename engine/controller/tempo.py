"""
Musical time from the console -- tap, BPM, speed, phase, the downbeat -- and
from a DJ, through `sync`. Every route that can move the clock ends here, so
there is one place that decides what each may do.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

from .. import sync as syncmod
from ..commands import OPERATE, command
from .common import N, S

if TYPE_CHECKING:                   # the class these helpers are given
    from . import ShowController


class Tempo:
    """Tempo by hand and from a DJ."""

    def _init_tempo(self) -> None:
        # Tempo ingest, opened only by `enable_sync`. See engine/sync.py for why
        # an open UDP port that can move the show clock is off by default.
        self.sync: Optional[syncmod.SyncListener] = None
        # What the bridge says is playing. Held here rather than on the clock
        # because it is not musical time -- it is a label for the operator, and
        # a clock that carried track titles would be a clock with opinions.
        self.sync_deck: Optional[str] = None
        self.sync_track: Optional[str] = None

    def enable_sync(self, port: int, bind: str = "127.0.0.1") -> None:
        """Open the tempo ingest port. Opt-in, and off unless asked for.

        The callback goes through `submit`, not `apply`, so a datagram lands at
        a frame boundary like every other command -- a bridge sending on its own
        thread must not be able to move the tempo halfway through an evaluation.
        """
        listener = syncmod.SyncListener(
            on_sync=lambda fields: self.submit({"type": "sync", **fields}, None),
            port=port, bind=bind)
        listener.start()            # raises on a taken port; then there is no sync
        self.sync = listener

    @command("tap", tier=OPERATE)
    def _cmd_tap(self, m: dict, now: float) -> None:
        self.clock.tap(now)

    @command("bpm", tier=OPERATE, args={"value": S(N, required=True)})
    def _cmd_bpm(self, m: dict, now: float) -> None:
        self.clock.set_bpm(float(m["value"]), now)

    @command("speed", tier=OPERATE, args={"value": S(N, required=True)})
    def _cmd_speed(self, m: dict, now: float) -> None:
        self.clock.set_speed(float(m["value"]), now)

    @command("nudge_phase", tier=OPERATE, args={"beats": S(N, required=True)})
    def _cmd_nudge_phase(self, m: dict, now: float) -> None:
        self.clock.nudge_phase(float(m["beats"]), now)

    @command("downbeat", tier=OPERATE)
    def _cmd_downbeat(self, m: dict, now: float) -> None:
        self.clock.set_downbeat(now)

    @command("sync", tier=OPERATE, args={k: S() for k in syncmod.FIELDS})
    def _cmd_sync(self, m: dict, now: float) -> None:
        """A position from something that already knows -- a CDJ, rekordbox.

        Arrives from the UDP port or over the WebSocket; both end here, so there
        is one place a external tempo can enter the show and one place that
        decides what it is allowed to do.

        A source that fires every beat should send `bpm` and `beat_in_bar` and
        leave `beat` alone. `beat` is a jump, correct for a re-sync and wrong for
        tracking -- `MasterClock.sync` says so, and this is the caller it is
        talking about.
        """
        # The same whitelist and range checks as the UDP port, applied here so
        # both routes in share one validator. The WebSocket route used to take
        # the message as it came: a bpm of 900 went straight to the clock, and a
        # track title was whatever length the sender liked. Cleaning twice is
        # harmless -- a datagram arrives already clean and comes out the same.
        fields = syncmod.clean(m)
        if fields is None:
            raise ValueError("sync carried no usable fields (bpm must be "
                             "40-250, beat_in_bar 0-64)")
        self.clock.sync(
            now,
            bpm=fields.get("bpm"),
            beat=fields.get("beat"),
            beat_in_bar=fields.get("beat_in_bar"),
            # The bridge naming itself is what makes the console able to say
            # WHICH thing is driving the clock, rather than just "not you".
            source=fields.get("source", "sync"),
            phrase_measured=fields.get("phrase_measured"),
            phrase_label=fields.get("phrase_label"),
            phrase_ends_in=fields.get("phrase_ends_in"),
            phrase_into=fields.get("phrase_into"),
            at=syncmod.now())
        if "deck" in fields:
            self.sync_deck = fields["deck"]
        if "track" in fields:
            self.sync_track = fields["track"]
        # `now` is when the datagram ARRIVED (see submit), which is what the
        # transport's line needs: applying it at the frame boundary instead
        # would quantise every position to the 25 ms frame grid.
        if "loaded_deck" in fields:
            self._prematch(fields)
        self.transport.ingest(fields, now)
        sample = self._track_frame(now)
        if sample is not None:
            self._check_grid(fields, sample, now)

    def sync_status(self) -> dict:
        return _sync_status(self)

    @command("sync_off", tier=OPERATE)
    def _cmd_sync_off(self, m: dict, now: float) -> None:
        """Take the clock back by hand.

        Always available, and it is the reason the sync source is displayed at
        all: a bridge must never silently own the tempo. Tempo and phase are
        left exactly where the bridge had them, because taking over is a
        decision about who decides next, not a reason to move the lights.
        """
        was = self.clock.source
        self.clock.unsync()
        self.sync_deck = self.sync_track = None
        self.transport.clear()
        self.note(f"took the clock back from {was!r} at "
                  f"{self.clock.bpm:.1f} bpm")

    def _public_tempo(self) -> dict:
        """Musical time, and who is setting it."""
        return {
            "clock": {"bpm": round(self.clock.bpm, 2),
                      "effective_bpm": round(self.clock.effective_bpm, 2),
                      "speed": round(self.clock.speed, 3),
                      "beat": round(self.ctx.beat, 3),
                      "bar": round(self.ctx.bar, 3),
                      "phrase": round(self.ctx.phrase, 3),
                      "beat_in_bar": round(
                          self.ctx.beat % self.clock.meter.beats_per_bar, 3),
                      "source": self.clock.source,
                      "phrase_measured": self.clock.phrase_measured,
                      "taps": self.clock.taps},
            "sync": self.sync_status(),
        }


def _sync_status(controller: "ShowController") -> dict:
    """What the DJ link is doing, including when it is doing nothing.

    `age` is the whole point. A bridge that dies leaves the timeline
    free-running at whatever tempo it last sent, with `clock.source` still
    naming it -- the console looks locked while it drifts away from a DJ nobody
    is listening to. Reporting how long ago the last packet arrived is what lets
    the UI say "LOCKED" and "no packets for 6s" as different things.
    """
    clock = controller.clock
    listening = controller.sync
    age = (None if clock.synced_at is None
           else round(syncmod.now() - clock.synced_at, 2))
    ends_in = None
    if clock.phrase_ends_at is not None:
        ends_in = round(clock.phrase_ends_at - controller.ctx.beat, 2)
    return {
        # Distinct from `clock.source`: a bridge can be connected and driving,
        # or connected and quiet, or not there at all.
        "listening": listening is not None,
        "driving": clock.synced_at is not None,
        "age": age,
        "phrase": clock.phrase_label,
        "phrase_ends_in": ends_in,
        "deck": controller.sync_deck,
        "track": controller.sync_track,
        **({"port": listening.status()} if listening is not None else {}),
    }
