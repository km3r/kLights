"""
The frame clock: the thing that must not miss.

Everything here is a direct consequence of the F2 timing spike
(`spike/timing/FINDINGS.md`). Python holds a DMX clock comfortably -- p99
interval error 0.046 ms, zero drops over 7200 frames under contention, about 65x
margin on the 3 ms threshold -- but only with two settings applied, and without
either one it fails badly rather than gracefully.

Those two settings are `install_timing_contract()`, and it is called from
`Runner.__init__` rather than left to the caller. They are part of the output
stage's contract, not tuning.

Three things the spike established that shape this file:

  * **External load is a non-issue.** Two busy background processes measured the
    same as an idle machine. The show laptop running a browser is fine.
  * **In-process CPU-bound Python is the killer.** A CPU-bound thread holds the
    GIL for up to `sys.getswitchinterval()`, 5 ms by default, which on a 25 ms
    period is most of the budget: p99 26.9 ms and 16 drops/minute. Hence the
    0.5 ms switch interval, and hence heavy per-frame work belongs out of this
    process. The WebSocket server is I/O-bound and safe in-process.
  * **Raising process priority does not help** and measured slightly worse.
    `SetPriorityClass` lifts every thread equally, including the competitors.

Frame rate is 40 Hz because DMX512 cannot carry more: 513 slots at 250 kbaud is
22.6 ms, so a full universe tops out near 44 Hz. It was never a QLC+ limit
(QLC+ ticks at 50) and it is nowhere near a Python limit (2000 fps measured with
zero drops). The headroom belongs to more universes and heavier per-frame work,
not to a higher rate.
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Callable, Optional

from . import state as statemod

if TYPE_CHECKING:                       # auto imports state; avoid the cycle
    from . import auto as autoged
from .clock import MasterClock
from .output.base import NullOutput, Output

# 44 Hz is the DMX512 ceiling for a full universe; 40 sits just under it.
DEFAULT_FPS = 40.0

# Coarse-sleep to this far short of the deadline, then busy-wait the rest. Buys
# microsecond precision at about 8% of one core continuously -- a real battery
# and thermal cost on a laptop, and worth paying.
SPIN_MARGIN = 0.002

GIL_SWITCH_INTERVAL = 0.0005


def install_timing_contract() -> list[str]:
    """Apply the two mandatory settings. Returns what it actually did.

    Call before starting any thread. Idempotent.
    """
    applied = []

    sys.setswitchinterval(GIL_SWITCH_INTERVAL)
    applied.append(f"sys.setswitchinterval({GIL_SWITCH_INTERVAL})")

    if sys.platform == "win32":
        # Windows' default scheduling quantum is 15.6 ms, so time.sleep() rounds
        # up to a multiple of it -- a 25 ms loop lands on 31.2 ms. Skipping this
        # produced 421 dropped frames in one minute.
        try:
            import ctypes
            ctypes.windll.winmm.timeBeginPeriod(1)
            applied.append("timeBeginPeriod(1)")
        except (OSError, AttributeError) as exc:
            applied.append(f"timeBeginPeriod FAILED ({exc}) -- expect dropped frames")

    return applied


@dataclass
class FrameStats:
    frames: int = 0
    # Deadlines missed badly enough to resynchronise -- a machine problem.
    drops: int = 0
    # Frames where show evaluation raised and the last good frame was re-sent --
    # a show problem. Kept separate because one number covering both cannot tell
    # you which of the two is happening, and they need opposite responses.
    eval_errors: int = 0
    started: float = 0.0
    worst_error: float = 0.0     # seconds
    last_error: float = 0.0

    @property
    def elapsed(self) -> float:
        return time.perf_counter() - self.started if self.started else 0.0

    @property
    def effective_fps(self) -> float:
        return self.frames / self.elapsed if self.elapsed > 0 else 0.0


@dataclass
class Runner:
    """Evaluates the show and puts frames on the wire at a fixed rate."""
    ctx: statemod.EvalContext
    show: statemod.Show
    # Beats a crossfade takes when the show is replaced. 0 is the old behaviour,
    # a hard cut on the frame the new look lands. Musical rather than seconds
    # because everything an operator authors here is musical, and a fade that
    # ignores tempo is the one thing on this surface that would.
    fade_beats: float = 0.0
    output: Output = field(default_factory=NullOutput)
    fps: float = DEFAULT_FPS
    # The musical timeline. Looks read ctx.bar and ctx.beat, which this keeps
    # up to date each frame; without a clock they stay at zero and every look
    # holds still, which is the honest behaviour for "no tempo yet".
    clock: Optional[MasterClock] = None
    # Auto mode. When present it owns which Show is running, so `show` becomes
    # an output of the frame rather than an input to it.
    director: Optional["autoged.AutoDirector"] = None
    # Called at the top of each frame, before anything is evaluated. Where
    # queued operator commands are applied, so they land on a frame boundary
    # rather than half way through an evaluation.
    before_frame: Optional[Callable[[], None]] = None
    # Called with whichever Show is about to be evaluated, every frame. Lets the
    # controller re-attach live overrides to a Show that auto mode just rebuilt.
    on_show: Optional[Callable[[statemod.Show], None]] = None
    on_frame: Optional[Callable[[dict[int, statemod.FixtureState]], None]] = None

    def __post_init__(self) -> None:
        self.applied_timing = install_timing_contract()
        self.stats = FrameStats()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self._panic = False
        # Last good frame per universe. Held on fault rather than blanking: a
        # rig that freezes on the last look is recoverable mid-set; a rig that
        # goes black is a stopped show.
        self._last: dict[int, bytes] = {}
        self._blackout = bytes(512)
        # A crossfade in progress: the show being faded OUT, when it started in
        # musical time, and how far through it is. The outgoing show is kept
        # whole rather than snapshotted as a frame, so it carries on running
        # underneath -- a move that was mid-sweep keeps sweeping as it fades,
        # which is the difference between a crossfade and a dissolve to a still.
        self._fading_from: Optional[statemod.Show] = None
        self._fade_start_beat = 0.0
        self._fade_beats = 0.0
        self._fade_t = 0.0
        # The mapping from wall time to engine time, so any thread can ask what
        # time it is on the show's clock. Needed because a command's timestamp
        # has to be taken when it ARRIVES, not when the frame loop gets round to
        # it -- a tap stamped at the next frame boundary is quantised to the
        # frame grid, which at 40 fps makes the tempo estimate step in jumps of
        # several bpm.
        self._begin: Optional[float] = None
        self._start_time = 0.0
        # The most recent evaluation traceback, for the UI to surface. Silently
        # holding the last frame is right for the rig and wrong for the
        # operator, who otherwise sees a show that has quietly stopped moving.
        self.last_error: Optional[str] = None

    # -- panic ------------------------------------------------------------

    def panic(self) -> None:
        """Kill all output immediately and keep sending zeros.

        Hardware-independent by design: it does not need the interface to
        cooperate, it does not need the UI to be responsive, and it does not
        stop the loop -- a blackout that stops sending is not a blackout,
        because most fixtures hold their last value when DMX goes away.
        """
        self._panic = True

    def clear_panic(self) -> None:
        self._panic = False

    @property
    def panicked(self) -> bool:
        return self._panic

    # -- the clock, readable from any thread -------------------------------

    def now(self) -> float:
        """Engine time, right now. Thread-safe.

        `ctx.time` only advances once per frame, so reading it off-thread gives
        the time of the last frame boundary rather than the present. Anything
        timestamping an external event -- a tap arriving on the socket thread --
        wants this instead.
        """
        if self._begin is None:
            return self.ctx.time
        return self._start_time + (time.perf_counter() - self._begin)

    # -- one frame --------------------------------------------------------

    def sync_clock(self) -> None:
        """Copy musical position onto the context, once per frame.

        Sampled once and shared by every layer, rather than each layer asking
        the clock itself. Two layers reading the clock a few microseconds apart
        would get slightly different beats, and a movement layer disagreeing
        with the intensity layer it is supposed to be in step with is exactly
        the kind of drift that is impossible to see and impossible to debug.
        """
        if self.clock is None:
            return
        position = self.clock.position(self.ctx.time)
        self.ctx.beat = position.beat
        self.ctx.bar = position.bar
        self.ctx.phrase = position.phrase
        self.ctx.bpm = position.bpm

        # Advance any crossfade in progress. Measured in BEATS off the same
        # position everything else reads, so slowing the tempo lengthens the
        # fade rather than desynchronising it from the music it is under.
        if self._fading_from is not None:
            elapsed = position.beat - self._fade_start_beat
            if self._fade_beats <= 0 or elapsed >= self._fade_beats:
                self._fading_from = None
            else:
                self._fade_t = max(0.0, min(1.0, elapsed / self._fade_beats))

        if self.director is None:
            # No auto mode: movement phase IS musical position, so a look reads
            # the same whether or not a director is attached.
            self.ctx.motion_bar = position.bar
        else:
            self.set_show(self.director.update(position,
                                               self.clock.phrase_measured))
            self.director.apply(self.ctx)
        if self.on_show is not None:
            self.on_show(self.show)

    def set_show(self, show: statemod.Show, fade_beats: Optional[float] = None
                 ) -> None:
        """Swap the running show, fading if asked.

        A second change mid-fade does NOT stack: it starts a new fade from
        whatever is currently on stage. Chaining fades would mean three shows
        evaluated per frame at the second change and four at the third, and the
        operator pressing GO twice quickly means "go to that one", not "blend
        the last three".
        """
        if show is self.show:
            return
        beats = self.fade_beats if fade_beats is None else fade_beats
        if beats > 0 and self.clock is not None:
            self._fading_from = self.show
            self._fade_start_beat = self.ctx.beat
            self._fade_beats = beats
            self._fade_t = 0.0
        else:
            self._fading_from = None
        self.show = show

    @property
    def fading(self) -> bool:
        return self._fading_from is not None

    def render_once(self) -> dict[int, bytes]:
        """Evaluate and emit a single frame. Never raises.

        A show-evaluation bug must not take the rig down mid-set, so a failure
        re-sends the last good frame and is counted. The alternative -- letting
        it propagate and stop the loop -- turns a wrong colour into a dead room.
        """
        if self._panic:
            return {u: self._blackout for u in self.ctx.rig.universes}
        try:
            if self._fading_from is not None:
                states = statemod.evaluate_crossfade(
                    self.ctx, self._fading_from, self.show, self._fade_t)
            else:
                states = statemod.evaluate(self.ctx, self.show)
            frames = {u: bytes(f) for u, f in statemod.render(self.ctx, states).items()}
            self._last = frames
            if self.on_frame is not None:
                self.on_frame(states)
            return frames
        except Exception:                                  # noqa: BLE001
            self.stats.eval_errors += 1
            self.last_error = traceback.format_exc()
            return self._last or {u: self._blackout for u in self.ctx.rig.universes}

    # -- the loop ---------------------------------------------------------

    def run(self, seconds: Optional[float] = None, start_time: float = 0.0) -> FrameStats:
        """Hold the clock until stopped, or for `seconds`."""
        period = 1.0 / self.fps
        clock = time.perf_counter
        begin = clock()
        self.stats = FrameStats(started=begin)
        self._begin, self._start_time = begin, start_time
        deadline = begin + period

        while not self._stop.is_set():
            if seconds is not None and clock() - begin >= seconds:
                break

            self.ctx.time = start_time + (clock() - begin)
            if self.before_frame is not None:
                try:
                    self.before_frame()
                except Exception:                          # noqa: BLE001
                    # An operator command must never take the rig down. Count it
                    # with the show errors and keep the clock running.
                    self.stats.eval_errors += 1
                    self.last_error = traceback.format_exc()
            self.sync_clock()
            for universe, frame in self.render_once().items():
                self.output.send(universe, frame)
            self.stats.frames += 1

            # Coarse sleep, then spin. Sleeping the whole way inherits the OS
            # timer's granularity; spinning the whole way burns a core.
            slack = deadline - clock() - SPIN_MARGIN
            if slack > 0:
                time.sleep(slack)
            while clock() < deadline:
                pass

            error = clock() - deadline
            self.stats.last_error = error
            self.stats.worst_error = max(self.stats.worst_error, abs(error))

            # Advance by a FIXED period, never from now, so scheduling error
            # cannot accumulate into drift. If a frame ran so long that the next
            # deadline is already past, resynchronise and count the drop rather
            # than sprinting to catch up -- catching up would send a burst of
            # frames a fixture cannot act on anyway.
            deadline += period
            if clock() > deadline + period:
                self.stats.drops += 1
                deadline = clock() + period

        return self.stats

    def start(self, start_time: float = 0.0) -> None:
        """Run the clock on its own thread."""
        if self._thread is not None:
            raise RuntimeError("runner already started")
        self._stop.clear()
        self._thread = threading.Thread(target=self.run, kwargs={"start_time": start_time},
                                        name="dmx-output", daemon=True)
        self._thread.start()

    def stop(self, blackout: bool = True) -> FrameStats:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None
        if blackout:
            # Leave the rig dark rather than frozen on whatever was last lit.
            for universe in self.ctx.rig.universes:
                try:
                    self.output.send(universe, self._blackout)
                except OSError:
                    pass
        return self.stats
