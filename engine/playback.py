"""
Playback: whether the timeline drives the rig this frame, and running it.

`program.py` can say what every fixture does at a beat of a track. This decides
WHEN that is what goes on stage, and at which beat:

- **Follow DJ is armed** -- one tap on the phone, and it starts disarmed. While
  disarmed the track is still identified and matched and shown, but nothing it
  says reaches the rig. A port that takes unauthenticated datagrams must not be
  able to start a show on its own.
- **the playing track matched a prepped track that has a timeline**, and its
  program has finished compiling (on the worker; until then the operator's show
  carries on).
- **the deck is playing** -- or paused, per the show's pause policy:
  `freeze` holds the frame the deck stopped on, `continue` keeps moving at the
  tempo it had, `idle` hands over to the show's idle routine once the pause
  has lasted the grace period.

Otherwise the fallback runs: the operator's or auto mode's show, exactly as it
would without a show folder (the phrase templates join it in milestone 2).

Every hand-over is a CUT, on the frame it happens (decided with the user,
F19i): arming, disarming, a matched track starting, an unmatched one, pausing
into idle and back.

**Grabs.** While the timeline drives, an operator who picks a look, a preset or
a cue takes that lane -- movement, colour or level -- and the operator's
selection shows there instead of the timeline's. A grab lasts until it is
released (decided with the user): across track changes too, because an
operator who took the colour for the rest of the set meant it. Picking looks
while the timeline is NOT driving grabs nothing, so arming hands every lane to
the timeline.

The beat is the TRACK's: the transport's position (latency already applied)
through the matched track's grid. A loop or hot cue is a jump, and a jump fires
no hit it skipped.

Runs on the output thread: `choose()` is the runner's hook, and everything it
does is a reference read, a dictionary lookup or a call into the compiled
program. Compiling happens on the worker.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from . import blocks as blocksmod
from . import program as programmod
from . import state as statemod
from . import timeline as timelinemod
from . import transport as transportmod

POLICIES = ("idle", "freeze", "continue")
IDLE_LENGTH = 1e7            # beats: an idle clip that never runs out


@dataclass(frozen=True)
class Status:
    """What the phone's Track card shows. Replaced, never edited."""
    armed: bool
    engaged: bool
    mode: str                       # "timeline", "idle" or "fallback"
    reason: Optional[str]           # why the timeline is not driving
    beat: Optional[float]
    lanes: dict
    grabbed: tuple[str, ...]
    policy: str
    problems: int
    first_problem: Optional[str]

    def public(self) -> dict:
        return {"armed": self.armed, "engaged": self.engaged, "mode": self.mode,
                "reason": self.reason,
                "beat": None if self.beat is None else round(self.beat, 2),
                "bar": None if self.beat is None else int(self.beat // 4) + 1,
                "lanes": dict(self.lanes), "grabbed": list(self.grabbed),
                "policy": self.policy, "problems": self.problems,
                "first_problem": self.first_problem}


class TrackPlayer:
    """The runner seam. One per controller."""

    def __init__(self, transport: transportmod.TrackTransport,
                 pinned: Callable[[], object],
                 rigging: Callable[[], blocksmod.Rigging],
                 submit: Callable[..., None], post: Callable[[Callable], None],
                 note: Callable[[str], None], clock_beat: Callable[[], float],
                 base_palette: Callable[[], dict]):
        self.transport = transport
        self._pinned = pinned
        self._rigging = rigging
        self._submit = submit              # worker.submit(fn, done, label)
        self._post = post
        self._note = note
        self._clock_beat = clock_beat
        self._base_palette = base_palette
        self.armed = False
        self.grabbed: frozenset[str] = frozenset()
        self.policy = "idle"
        self.grace_s = 4.0
        self.idle_routine: Optional[str] = None
        self.program: Optional[programmod.Program] = None
        self._program_for: Optional[tuple] = None      # (track_seq, timeline id)
        self._compiling_for: Optional[tuple] = None
        self.idle: Optional[programmod.Program] = None
        self._beat: Optional[float] = None
        self._seq: Optional[int] = None
        self._jump: Optional[int] = None
        self._last_play: Optional[tuple[float, float, float]] = None
        self._paused_since: Optional[float] = None
        self.engaged = False
        self.status = Status(False, False, "fallback", "disarmed", None, {}, (),
                             self.policy, 0, None)
        self._sample: Optional[transportmod.TrackSample] = None

    # -- settings ----------------------------------------------------------

    def configure(self, show: Optional[dict]) -> None:
        """Take the pause policy and idle routine from show.json."""
        pause = (show or {}).get("pause") or {}
        self.policy = pause.get("policy") if pause.get("policy") in POLICIES \
            else "idle"
        self.grace_s = float(pause.get("grace_s", 4.0))
        self.idle_routine = pause.get("idle_routine")

    def arm(self, armed: bool) -> None:
        self.armed = bool(armed)

    def grab(self, slots) -> None:
        """The operator took these lanes. Only while the timeline drives:
        looks picked with Follow disarmed are the operator's show, not a claim
        against a timeline that is not running."""
        if self.engaged:
            self.grabbed = self.grabbed | frozenset(slots)

    def release(self, slot: Optional[str] = None) -> None:
        self.grabbed = frozenset() if slot is None else self.grabbed - {slot}

    # -- compiling, on the worker ------------------------------------------

    def compile_for(self, pinned) -> None:
        """Compile the playing track's timeline for this rig, off the output
        thread. A result for a track that has since changed is dropped."""
        if pinned is None or pinned.timeline is None:
            return
        key = (pinned.track_seq, id(pinned.timeline))
        if key in (self._program_for, self._compiling_for):
            return
        self._compiling_for = key
        timeline = pinned.timeline
        routines = pinned.library.folder.routines if pinned.library else {}
        rigging = self._rigging()
        track = (pinned.match.track_id if pinned.match else "?")

        def build():
            return programmod.compile(timeline, routines, rigging,
                                      f"timelines/{track}.json")

        def done(prog: programmod.Program) -> None:
            if self._compiling_for == key:
                self._compiling_for = None
            current = self._pinned()
            if current is None or (current.track_seq, id(current.timeline)) != key:
                return
            self.program, self._program_for = prog, key
            if prog.problems:
                more = (f" (+{len(prog.problems) - 1} more)"
                        if len(prog.problems) > 1 else "")
                self._note(f"{track}: {prog.problems[0]}{more}")

        self._submit(build, done, f"compiling {track}")

    def compile_idle(self, library) -> None:
        """The idle routine as a program of its own, for pauses."""
        name = self.idle_routine
        doc = library.folder.routines.get(name) if (library and name) else None
        if doc is None:
            self.idle = None
            return
        rows = [{"id": "idle", "type": "clips", "target": "scene", "gap": "fill",
                 "items": [{"id": "idle", "kind": "routine", "routine": name,
                            "at": 0, "len": IDLE_LENGTH}]}]
        from . import showfiles
        timeline = timelinemod.Timeline.from_rows(rows, showfiles.timeline_channels)
        routines = library.folder.routines
        rigging = self._rigging()

        def done(prog):
            self.idle = prog

        self._submit(lambda: programmod.compile(timeline, routines, rigging,
                                                f"idle routine {name!r}"),
                     done, f"compiling idle routine {name!r}")

    def recompile(self) -> None:
        """The rig changed under the programs: build them again."""
        self.program, self._program_for, self._compiling_for = None, None, None
        self.compile_for(self._pinned())

    # -- each frame, on the output thread ------------------------------------

    def choose(self, fallback: statemod.Show, sample: transportmod.TrackSample,
               now: float) -> statemod.Show:
        """The Show to put on stage this frame: the program's, or `fallback`."""
        show, mode, reason, beat = self._decide(fallback, sample, now)
        self.engaged = mode == "timeline"
        prog = self.program if mode == "timeline" else (
            self.idle if mode == "idle" else None)
        lanes = {}
        for slot in statemod.SLOTS:
            if slot in self.grabbed and self.engaged:
                lanes[slot] = "operator"
            elif prog is not None and beat is not None and mode == "timeline" \
                    and prog.timeline.entries(slot, beat):
                lanes[slot] = "timeline"
            elif mode == "idle":
                lanes[slot] = "idle"
            else:
                lanes[slot] = "fallback"
        problems = self.program.problems if self.program is not None else []
        self.status = Status(self.armed, self.engaged, mode, reason, beat, lanes,
                             tuple(sorted(self.grabbed)), self.policy,
                             len(problems), problems[0] if problems else None)
        return show

    def _decide(self, fallback, sample, now):
        pinned = self._pinned()
        self._sample = sample
        if not self.armed:
            return fallback, "fallback", "disarmed", None
        if sample is None or sample.state == transportmod.NO_TRACK:
            return fallback, "fallback", "no track", None
        if pinned is None or pinned.track_seq != sample.track_seq:
            return fallback, "fallback", "matching", None
        if pinned.match is None or pinned.match.track_id is None:
            return fallback, "fallback", "not in the show folder", None
        if pinned.timeline is None:
            return fallback, "fallback", "no timeline", None
        key = (pinned.track_seq, id(pinned.timeline))
        if self._program_for != key:
            self.compile_for(pinned)
            return fallback, "fallback", "compiling", None
        prog = self.program
        grid = pinned.grid
        if grid is None or sample.time_s is None:
            return fallback, "fallback", "no position", None

        jumped = (sample.jump_seq != self._jump or sample.track_seq != self._seq)
        self._jump, self._seq = sample.jump_seq, sample.track_seq

        if sample.state == transportmod.PAUSED:
            if self._paused_since is None:
                self._paused_since = now
            paused_for = now - self._paused_since
            silent = sample.age is not None and sample.age >= self.grace_s
            if self.policy == "idle" and (silent or paused_for >= self.grace_s):
                if self.idle is not None:
                    idle_beat = self._clock_beat()
                    self.idle.grabbed = frozenset()
                    self.idle.begin(idle_beat, fallback=fallback,
                                    base_palette=self._base_palette())
                    self._beat = None
                    return self.idle.show, "idle", "paused", idle_beat
                return fallback, "fallback", "paused (no idle routine)", None
            if self.policy == "continue" and self._last_play is not None:
                beat0, t0, bps = self._last_play
                beat = beat0 + (now - t0) * bps
                return self._run(prog, fallback, beat, jumped=False)
            # freeze, and idle during its grace: hold where the deck stopped
            beat = grid.beat_at(sample.time_s)
            return self._run(prog, fallback, beat, jumped=jumped)

        self._paused_since = None
        beat = grid.beat_at(sample.time_s)
        if sample.state == transportmod.PLAYING:
            bps = grid.bpm_at(sample.time_s) / 60.0 * sample.rate
            self._last_play = (beat, now, bps)
        return self._run(prog, fallback, beat, jumped=jumped)

    def _run(self, prog, fallback, beat, jumped):
        prev = None if jumped else self._beat
        prog.grabbed = self.grabbed
        prog.begin(beat, prev=prev, jumped=jumped, fallback=fallback,
                   base_palette=self._base_palette())
        self._beat = beat
        return prog.show, "timeline", None, beat
