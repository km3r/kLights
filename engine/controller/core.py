"""
The engine's shared machinery: the rig and the context it is evaluated in,
the clock, the runner that owns the frame clock, the command queue, the
worker, and who is connected.

**Commands go through a queue drained at a frame boundary.** Nothing on a
server thread mutates the running show directly: a phone tapping a look while
the output thread is halfway through evaluating one would otherwise give a
frame built from two different shows. `submit` queues a command, `submit_call`
queues the engine's own work coming back from the worker, and `_drain` applies
both at the top of a frame. `apply` is the one place a command enters the show
(see engine/commands.py).
"""

from __future__ import annotations

import math
import queue
import time
from typing import Any, Callable, Optional

from .. import __version__
from .. import blocks as blocksmod
from .. import clock as clockmod
from .. import commands as commandsmod
from .. import config as configmod
from .. import patch as patchmod
from .. import rig as rigmod
from .. import safety as safetymod
from .. import state as statemod
from .. import worker as workermod
from ..commands import VIEW, command
from ..output import ArtNetOutput, NullOutput
from ..runner import Runner
from .common import Client, DEFERRED, S, describe, reply_id


# A head whose achieved aim misses what was asked by more than this has run into
# its rail. Well above quantisation (a 16-bit step is a hundredth of a degree)
# and well below anything the eye would call "close enough".
AT_LIMIT_DEG = 0.5


class Core:
    """The rig, the clock, the runner, and the queue every other part's
    commands arrive through."""

    def _init_engine(self, bpm: float) -> None:
        self.rig = rigmod.load_rig(self.event_dir)
        self.reach = rig_reach(self.rig)
        errors = self.rig.validate()
        if errors:
            raise ValueError("rig does not validate:\n  " + "\n  ".join(errors))

        # A taper policy saved from the UI has to survive a restart, or saving
        # it is theatre. venue.json is the right home: the policy is a property
        # of the room and the crowd in it, not of the rig.
        # rig.venue_file, not event_dir/venue.json: the room may live in
        # shared/venues/ and be shared with another show, and reading the taper
        # from a path this class derives itself would silently pick up a stale
        # per-event file that nothing else is using.
        # Through configmod.load, not json.loads: the same file load_rig has
        # already validated, read the same way, so `"taper": null` (or a null
        # inside it) is absent here too rather than None.get() at startup.
        venue_cfg = configmod.load(self.rig.venue_file, configmod.VENUE)
        taper_cfg = venue_cfg.get("taper", {})
        taper = safetymod.TaperConfig(
            crowd_level=float(taper_cfg.get("crowd_level", 0.5)),
            margin_deg=float(taper_cfg.get("margin_deg", 6.0)),
            slew_per_second=float(taper_cfg.get("slew_per_second", 2.0)),
            enabled=bool(taper_cfg.get("enabled", True)))

        # Strobe is the one genuine medical risk here (photosensitive
        # epilepsy) and nothing used to limit it. Policy lives beside the taper
        # because it is the same kind of thing: a property of the room and the
        # crowd in it, not of the rig.
        strobe_cfg = venue_cfg.get("strobe", {})
        strobe_policy = safetymod.StrobeConfig(
            enabled=bool(strobe_cfg.get("enabled", True)),
            ceiling=float(strobe_cfg.get("ceiling", 1.0)),
            max_seconds=float(strobe_cfg.get("max_seconds", 0.0)),
            recover_seconds=float(strobe_cfg.get("recover_seconds", 2.0)))

        self.ctx = statemod.EvalContext(rig=self.rig, venue=self.rig.venue,
                                        taper=taper,
                                        strobe_policy=strobe_policy)
        self.clock = clockmod.MasterClock(bpm=bpm, now=0.0)

    def _init_runner(self, artnet: Optional[str], fps: float) -> None:
        self.output = ArtNetOutput(artnet) if artnet else NullOutput()
        self.runner = Runner(
            ctx=self.ctx, show=self.setlist.current().make(self.palette.current()),
            output=self.output, fps=fps, clock=self.clock, director=self.director,
            before_frame=self._before_frame, on_show=self._attach_overrides,
            on_frame=self._publish)

        # Each entry is (command dict, sender, arrival time) -- or, from inside
        # the engine only, (callable, None, time): see `submit_call`.
        self.commands: "queue.Queue[tuple[Any, Optional[Client], float]]" = queue.Queue()
        # Who is connected. Written by the HTTP threads as clients arrive and
        # leave, through `add_client` / `remove_client` -- never edited in
        # place, since the snapshot walks it on the broadcast thread.
        self.clients: dict[str, Client] = {}
        # How a reply reaches the one client that asked for it. Set by the
        # ShowServer that owns the sockets; None when there is no server (the
        # tests that drive a controller directly), and then nothing is sent.
        self.reply_to: Optional[Callable[[str, dict], None]] = None
        # The command `apply` is in the middle of, for a handler that answers
        # later or needs its sender; None between commands.
        self._call: Optional[commandsmod.Call] = None
        # Anything slow -- parsing a show file, matching a track, an fsync to a
        # shared folder -- runs here and posts its result back through
        # `submit_call`, so it lands on the output thread at a frame boundary.
        self.worker = workermod.Worker(post=self.submit_call, report=self.note)
        self.latest_states: dict[int, statemod.FixtureState] = {}
        self.notices: list[str] = []
        self.rev = 0

    def start(self) -> None:
        # A marker so the editing tools know not to rewrite this event's config
        # underneath a running show. Nothing is corrupted if they do -- the
        # engine read its config at startup and will not read it again -- but
        # the file then says one thing while the rig does another, and nothing
        # on screen explains why. Best-effort: a read-only checkout is a
        # perfectly good reason to run a show, and not a reason to refuse.
        try:
            patchmod.lock_path(str(self.event_dir)).write_text(
                f"engine {__version__} since "
                f"{time.strftime('%Y-%m-%d %H:%M:%S')}\n", encoding="utf-8")
        except OSError:
            pass
        self.worker.start()
        if self.watcher is not None:
            self.watcher.start(self.show_library.signature
                               if self.show_library is not None else None)
        self.look_watcher.start()
        self.cue_watcher.start()
        self.runner.start()

    def stop(self) -> None:
        if self.sync is not None:
            self.sync.stop()
        if self.watcher is not None:
            self.watcher.stop()
        self.look_watcher.stop()
        self.cue_watcher.stop()
        self.runner.stop()
        self.worker.stop()
        self.output.close()
        self.outputs.close()
        try:
            patchmod.lock_path(str(self.event_dir)).unlink(missing_ok=True)
        except OSError:
            pass

    def _before_frame(self) -> None:
        self._drain()
        self._frame_sample = self._track_frame()
        self._pad_frame(self.runner.now())

    def _rigging(self):
        return blocksmod.Rigging(self.rig, dict(self.by_name),
                                 tuple(self.presets), self.rig.name)

    def _drain(self) -> None:
        """Apply every queued command. Runs on the output thread, at the top of
        a frame, so a command can never land mid-evaluation."""
        self._advance_due_cue()
        while True:
            try:
                message, client, at = self.commands.get_nowait()
            except queue.Empty:
                return
            if callable(message):
                # Posted from inside the engine by `submit_call` -- a worker
                # handing a result back. Never reachable from a socket: those
                # deliver parsed JSON, and JSON has no callables.
                try:
                    message()
                except Exception as exc:                    # noqa: BLE001
                    self.note(f"engine task failed: {exc}")
                continue
            rid = reply_id(message)
            try:
                result = self.apply(message, client, at)
            except Exception as exc:                        # noqa: BLE001
                # One bad command must not stop the others, and must not stop
                # the show. A command that asked for a reply gets its failure
                # there, where the screen that sent it can show it beside the
                # thing that failed; one that did not is reported where every
                # console can see it, as it always was.
                if rid is not None and client is not None:
                    self._reply(client, rid, ok=False, error=str(exc))
                else:
                    self.note(f"{message.get('type', '?')} failed: {exc}")
                continue
            if result is DEFERRED:
                continue                   # answered when its work is done
            if rid is not None and client is not None:
                self._reply(client, rid, ok=True, data=result)

    def _deferred(self) -> Callable[..., None]:
        """A way to answer the command being applied, later -- for one whose
        work runs on the worker. Without an id (or a sender) a failure lands
        in the notices instead, as any other failure would."""
        call = self._call
        if call is None:
            raise RuntimeError("_deferred() outside a command: there is no "
                               "one to answer")
        client, rid, cmd = call.client, call.reply_id, call.command
        kind = cmd.name

        def respond(ok: bool, data: Any = None, error: Optional[str] = None):
            if ok:
                problem = self._reply_problem(cmd, data)
                if problem is not None and commandsmod.STRICT:
                    ok, data, error = False, None, problem
            if rid is not None and client is not None:
                self._reply(client, rid, ok=ok, error=error, data=data)
            elif not ok:
                self.note(f"{kind} failed: {error}")
        return respond

    def _on_worker(self, label: str, fn: Callable[[], Any],
                   then: Callable[[Any, Callable], None]) -> object:
        """Run `fn` on the worker and `then(result, respond)` back here; a
        failure is answered as one. Returns DEFERRED for the handler to return."""
        respond = self._deferred()

        def job():
            try:
                return True, fn()
            except Exception as exc:                        # noqa: BLE001
                return False, str(exc)

        def done(outcome) -> None:
            ok, value = outcome
            if ok:
                then(value, respond)
            else:
                respond(False, error=value)

        self.worker.submit(job, done, label=label)
        return DEFERRED

    def _reply(self, client: Client, rid: Any, ok: bool,
               error: Optional[str] = None, data: Any = None) -> None:
        if self.reply_to is None:
            return
        payload: dict = {"type": "reply", "id": rid, "ok": ok}
        if error is not None:
            payload["error"] = error
        if data is not None:
            payload["data"] = data
        try:
            self.reply_to(client.id, payload)
        except Exception as exc:                            # noqa: BLE001
            # Outside _drain's per-command guard, and on the output thread: a
            # reply that cannot be sent is a notice, never a stopped frame.
            self.note(f"reply to {client.name} failed: {exc}")

    def _publish(self, states: dict[int, statemod.FixtureState]) -> None:
        # One reference assignment. Atomic under the GIL, so the broadcast
        # thread never sees a half-built dict, and no lock touches the hot path.
        self.latest_states = states

    def note(self, text: str) -> None:
        self.notices.append(text)
        del self.notices[:-20]

    def add_client(self, client: Client) -> None:
        """A browser connected. Called on its HTTP thread: rebinds, so a
        snapshot walking the presence list never sees it change size."""
        self.clients = {**self.clients, client.id: client}

    def remove_client(self, cid: str) -> None:
        """A browser went away. Rebinds, as `add_client` does."""
        if cid in self.clients:
            self.clients = {k: c for k, c in self.clients.items() if k != cid}

    def _sender(self) -> Optional[Client]:
        """Who sent the command being applied; None for one from inside the
        engine or from a test driving the controller directly."""
        return self._call.client if self._call is not None else None

    def submit(self, message: dict, client: Optional[Client]) -> None:
        """Queue a command, stamped with the time it ARRIVED.

        The stamp matters for exactly one command and matters a lot for it. A
        tap applied at the next frame boundary is a tap quantised to the frame
        grid: at 40 fps near 128 bpm the reachable beat intervals are 18, 19 or
        20 frames, so the tempo can only land on 133.3, 126.3 or 120.0 and a
        metronomic 128.00 reads as 126.32. The median cannot rescue that,
        because it is quantisation rather than noise. Stamping here keeps the
        queue's frame-boundary guarantee for the MUTATION while giving the clock
        the real arrival time.
        """
        self.commands.put((message, client, self.runner.now()))

    def submit_call(self, fn: Callable[[], None]) -> None:
        """Run `fn()` on the output thread at the next frame boundary.

        The way back in for work done elsewhere. A worker thread that has parsed
        a file or compiled a timeline must not install the result itself -- it
        would be mutating the show from a second thread, mid-evaluation, which
        is the exact bug the command queue exists to rule out. It posts here
        instead, and the install happens where every other mutation does.

        Internal only. Nothing a client sends can become a callable.
        """
        self.commands.put((fn, None, self.runner.now()))

    def apply(self, message: dict, client: Optional[Client],
              at: Optional[float] = None) -> Any:
        """Apply one command, on the output thread. The one place a command
        enters the show: the one lookup that says what it is, the one check
        of who may send it, the one check of its arguments."""
        kind = message.get("type")
        now = self.ctx.time if at is None else at
        cmd = self.COMMANDS.get(kind) if isinstance(kind, str) else None
        if cmd is None:
            raise ValueError(f"unknown command {kind!r}")
        # A client with no token may watch but not touch. Checked here rather
        # than at the socket, so there is exactly one place a command can enter
        # the show and exactly one place that decides whether it may.
        if client is not None and not client.may(cmd.tier):
            raise ValueError(
                f"{kind!r} needs {cmd.tier} access and this client has "
                f"{client.tier}. Open the URL the engine printed, including "
                f"its ?token=, or restart with --no-token")
        cmd.check(message)
        previous = self._call
        self._call = commandsmod.Call(cmd, client, reply_id(message))
        try:
            result = getattr(self, cmd.method)(message, now)
        finally:
            self._call = previous
        self.rev += 1
        if client is not None and kind != "hello":
            client.last_action = describe(message)
            client.last_action_at = time.time()
        # Whatever the handler returned rides back in the reply, for the few
        # commands whose answer is not visible in the broadcast state -- a
        # validation result, say. Most return None and the reply is just ok.
        if result is not DEFERRED:
            problem = self._reply_problem(cmd, result)
            if problem is not None and commandsmod.STRICT:
                raise commandsmod.CommandError(problem)
        return result

    def _reply_problem(self, cmd: commandsmod.Command, data: Any) -> Optional[str]:
        """What is wrong with an answer, against what its command declares
        (`reply`); said in the notices and sent all the same, unless the
        suites are running (`commands.STRICT`), which refuse it instead."""
        problem = cmd.reply_problems(data)
        if problem is not None and not commandsmod.STRICT:
            self.note(f"{problem} (sent all the same)")
        return problem

    @command("hello", tier=VIEW, args={"name": S(str)})
    def _cmd_hello(self, m: dict, now: float) -> None:
        pass                          # name is set by the connection handler

    def _public_engine(self) -> dict:
        """The rig as it was last evaluated, who is connected, and the engine's
        own health. Built on the caller's thread, from references the output
        thread only ever rebinds."""
        now = time.time()
        states = self.latest_states
        g = self.rig.geometry
        stats = self.runner.stats

        fixtures = []
        for f in self.rig.fixtures:
            st = states.get(f.fid)
            entry: dict[str, Any] = {
                "id": f.fid, "name": f.name, "tags": list(f.tags),
                "head": f.head, "universe": f.universe, "address": f.address,
                "is_mover": f.is_mover,
                # So the Color tab can offer a white control only for fixtures
                # that can actually do anything with it, instead of showing a
                # slider that silently does nothing on an RGB-only fixture.
                "has_white": f.has(rigmod.WHITE),
            }
            # Position, so the UI can work out capture targets itself -- the
            # adjacent corners are 90 degrees either side of the ball from a
            # head in a corner, which is the spread the solver needs and which
            # the obvious targets do not give.
            #
            # Taken from the PATCH for anything without geometry, which is what
            # puts the pinspots on the plan view. Filling this from the head
            # list alone meant every static fixture reported no position at all
            # -- and a plan of the room that silently omits a third of the rig
            # is worse than no plan, because it looks complete.
            if f.head is not None and g is not None:
                h = g.heads[f.head]
                entry["position"] = [h.x, h.height, h.z]
                entry["beam_deg"] = h.beam_angle_deg
            elif f.position is not None:
                entry["position"] = list(f.position)
                if f.beam_deg is not None:
                    entry["beam_deg"] = f.beam_deg

            if st is not None:
                entry["intensity"] = round(st.intensity, 3)
                entry["color"] = [round(c, 3) for c in st.color]
                # RGBW fixtures blend a real white component, so a swatch drawn
                # from RGB alone shows a colder light than the room has.
                if st.white > 0:
                    entry["white"] = round(st.white, 3)
                if st.strobe > 0:
                    entry["strobe"] = round(st.strobe, 3)
                if st.safety is not None:
                    entry["safety"] = {"taper": round(st.safety.taper, 3),
                                       "reason": st.safety.reason}
                if st.aim is not None and g is not None and f.head is not None:
                    entry["aim"] = {"bearing": round(st.aim.bearing_delta, 2),
                                    "elevation": round(st.aim.elev_deg, 2)}
                    # Asked to go further than its own travel allows, so it has
                    # stopped at the rail. Rigs mix fixtures with different
                    # travel, and the centre macro is bounded by the MOST
                    # capable head -- so a lesser one can be asked for a place
                    # it cannot reach. It still lands somewhere sensible (the
                    # rail); this is the part that says so, on that head, the
                    # same way a taper dim is explained rather than left to be
                    # noticed. Computed here at 10 Hz, never on the frame path.
                    b_err, e_err = g.reach_error(f.head, st.aim)
                    limits = [axis for axis, err in (("bearing", b_err),
                                                     ("elevation", e_err))
                              if err > AT_LIMIT_DEG]
                    if limits:
                        entry["at_limit"] = limits
                    land = safetymod.landing(g, f.head, st.aim, self.rig.venue)
                    if land is not None:
                        entry["lands_on"] = land.surface
                        entry["throw_mm"] = round(land.distance)
                        # The point itself, not just how far away it is. The
                        # plan view needs somewhere to draw the beam TO, and it
                        # cannot work that out from the aim: `bearing` here is
                        # the servo's own delta from its mount facing, and the
                        # mount facing lives in the calibration. Publishing the
                        # answer the engine already computed beats shipping the
                        # calibration to every phone so each can redo the maths.
                        entry["lands_at"] = [round(v) for v in land.point]
                entry["jogging"] = f.name in self.jog
                entry["captures"] = len(self.captures.get(f.name, []))
            fixtures.append(entry)

        return {
            "event": self.rig.name,
            # How far this rig's heads can travel from the ball, per axis --
            # the real range of every reach-bounded control (the centre macro,
            # an `offset` look). Sent because it is a property of THIS rig and
            # its calibration, which the bundle cannot know in advance.
            "reach": {axis: list(span) for axis, span in self.reach.items()},
            # The fixture types the UI filters by, biggest group first. Sent
            # rather than derived in the UI so both agree on what a group is.
            "groups": list(self.rig.tags()),
            "fixtures": fixtures,
            "presence": [c.public(now) for c in self.clients.values()],
            "stats": {"fps": round(stats.effective_fps, 2),
                      "frames": stats.frames,
                      "drops": stats.drops,
                      "eval_errors": stats.eval_errors,
                      "worst_error_ms": round(stats.worst_error * 1000, 3)},
            "notices": list(self.notices[-8:]),
            "last_error": self.runner.last_error,
        }


def rig_reach(rig: rigmod.Rig) -> dict[str, tuple[float, float]]:
    """How far this rig's heads can travel from the ball, per axis, rounded
    OUTWARD to whole degrees so a slider's ends are reachable rather than a
    hair short. Empty for a rig with no moving heads -- every reach-bounded
    parameter then falls back to its declared range."""
    if rig.geometry is None:
        return {}
    return {axis: (float(math.floor(lo)), float(math.ceil(hi)))
            for axis, (lo, hi) in rig.geometry.rig_reach().items()}
