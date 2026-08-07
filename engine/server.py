"""
The show server: one HTTP + WebSocket endpoint that the phone, the tablet and
the laptop all talk to.

One responsive surface, not a phone app and a desktop app -- that was the
explicit requirement, and it is also the only way the six tabs stay in sync,
since there is exactly one implementation of each.

Two structural decisions shape everything here.

**Commands go through a queue drained at frame boundaries.** Nothing on a
server thread ever mutates the running show directly. A phone tapping a look
while the output thread is halfway through evaluating one would otherwise give a
frame built from two different shows, and the resulting one-frame flicker is
invisible in testing and infuriating in a room. Queueing costs at most one
frame -- 25 ms -- and removes the whole class.

**JSON is built on the broadcast thread, never the output thread.** The output
thread publishes its raw state by assigning one reference, which is atomic under
the GIL; the broadcast thread serialises at 10 Hz. The F2 spike was explicit
that in-process CPU-bound work is what breaks the DMX clock, and serialising a
few kilobytes 40 times a second to feed a display that cannot show 40 Hz would
be exactly that mistake.

**Presence is awareness, not ownership.** Everyone controls everything; the UI
shows who is connected and who last touched what. No locking, no claiming. A
lighting desk with two people on it works because they can see each other, not
because the desk arbitrates.
"""

from __future__ import annotations

import json
import queue
import socket
import threading
import time
import traceback
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from . import auto as autom
from . import calibrate as calibmod
from . import clock as clockmod
from . import geometry as geo
from . import motion
from . import rig as rigmod
from . import safety as safetymod
from . import state as statemod
from .output import ArtNetOutput, NullOutput
from .runner import Runner
from .websocket import WebSocket, WebSocketClosed, WebSocketError

REPO = Path(__file__).resolve().parent.parent
UI_DIST = REPO / "ui" / "dist"

BROADCAST_HZ = 10.0

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
        ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json"}


# ------------------------------------------------------------------ clients --

@dataclass
class Client:
    """One connected browser. `name` is whatever it called itself."""
    id: str
    name: str = "someone"
    connected_at: float = 0.0
    last_action: str = ""
    last_action_at: float = 0.0

    def public(self, now: float) -> dict:
        return {"id": self.id, "name": self.name,
                "connected_for": round(now - self.connected_at, 1),
                "last_action": self.last_action,
                "last_action_ago": (round(now - self.last_action_at, 1)
                                    if self.last_action else None)}


# --------------------------------------------------------------- controller --

class ShowController:
    """Owns the engine. Every mutation arrives as a queued command."""

    def __init__(self, event_dir: Path, artnet: Optional[str] = None,
                 fps: float = 40.0, bpm: float = 124.0):
        self.event_dir = Path(event_dir)
        self.rig = rigmod.load_rig(self.event_dir)
        errors = self.rig.validate()
        if errors:
            raise ValueError("rig does not validate:\n  " + "\n  ".join(errors))

        self.ctx = statemod.EvalContext(rig=self.rig, venue=self.rig.venue)
        self.clock = clockmod.MasterClock(bpm=bpm, now=0.0)
        self.setlist = default_setlist()
        self.palette = default_palette()
        self.director = autom.AutoDirector(
            self.setlist,
            autom.AutoConfig(look_changes=False, palette=False, energy=False,
                             change_every_phrases=2.0, palette_every_phrases=4.0),
            self.palette, autom.PhraseEnergy())

        self.output = ArtNetOutput(artnet) if artnet else NullOutput()
        self.runner = Runner(
            ctx=self.ctx, show=self.setlist.current().make(self.palette.current()),
            output=self.output, fps=fps, clock=self.clock, director=self.director,
            before_frame=self._drain, on_show=self._attach_overrides,
            on_frame=self._publish)

        self.commands: "queue.Queue[tuple[dict, Optional[Client]]]" = queue.Queue()
        self.clients: dict[str, Client] = {}
        self.master = 0.9
        self.blackout = False

        # Live operator overrides, re-attached to whatever Show is running so
        # they survive an auto-mode look change.
        self.override_layers: list[statemod.Layer] = []
        self.color_overrides: dict[str, tuple[float, float, float]] = {}
        self.jog: dict[str, tuple[int, int]] = {}
        self.captures: dict[str, list[calibmod.Capture]] = {}

        self.latest_states: dict[int, statemod.FixtureState] = {}
        self.notices: list[str] = []
        self.rev = 0

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        self.runner.start()

    def stop(self) -> None:
        self.runner.stop()
        self.output.close()

    # -- the frame hooks ---------------------------------------------------

    def _drain(self) -> None:
        """Apply every queued command. Runs on the output thread, at the top of
        a frame, so a command can never land mid-evaluation."""
        while True:
            try:
                message, client = self.commands.get_nowait()
            except queue.Empty:
                return
            try:
                self.apply(message, client)
            except Exception as exc:                        # noqa: BLE001
                # One bad command must not stop the others, and must not stop
                # the show. Record it where the UI can see it.
                self.note(f"{message.get('type', '?')} failed: {exc}")

    def _attach_overrides(self, show: statemod.Show) -> None:
        show.master = 0.0 if self.blackout else self.master
        show.overrides = self.override_layers

    def _publish(self, states: dict[int, statemod.FixtureState]) -> None:
        # One reference assignment. Atomic under the GIL, so the broadcast
        # thread never sees a half-built dict, and no lock touches the hot path.
        self.latest_states = states

    def note(self, text: str) -> None:
        self.notices.append(text)
        del self.notices[:-20]

    # -- commands ----------------------------------------------------------

    def submit(self, message: dict, client: Optional[Client]) -> None:
        self.commands.put((message, client))

    def apply(self, message: dict, client: Optional[Client]) -> None:
        kind = message.get("type")
        now = self.ctx.time
        handler = getattr(self, f"_cmd_{kind}", None)
        if handler is None:
            raise ValueError(f"unknown command {kind!r}")
        handler(message, now)
        self.rev += 1
        if client is not None and kind != "hello":
            client.last_action = describe(message)
            client.last_action_at = time.time()

    # look selection -------------------------------------------------------

    def _cmd_select_look(self, m: dict, now: float) -> None:
        self.director.select(m["name"], hold=m.get("hold", True))

    def _cmd_release(self, m: dict, now: float) -> None:
        self.director.release()

    def _cmd_next_look(self, m: dict, now: float) -> None:
        self.setlist.advance()
        self.director.rebuild()

    # levels ---------------------------------------------------------------

    def _cmd_master(self, m: dict, now: float) -> None:
        self.master = max(0.0, min(1.0, float(m["value"])))

    def _cmd_blackout(self, m: dict, now: float) -> None:
        self.blackout = bool(m.get("on", not self.blackout))

    def _cmd_panic(self, m: dict, now: float) -> None:
        self.runner.panic()
        self.note("PANIC -- output forced to zero")

    def _cmd_clear_panic(self, m: dict, now: float) -> None:
        self.runner.clear_panic()
        self.note("panic cleared")

    # clock ----------------------------------------------------------------

    def _cmd_tap(self, m: dict, now: float) -> None:
        self.clock.tap(now)

    def _cmd_bpm(self, m: dict, now: float) -> None:
        self.clock.set_bpm(float(m["value"]), now)

    def _cmd_speed(self, m: dict, now: float) -> None:
        self.clock.set_speed(float(m["value"]), now)

    def _cmd_nudge_phase(self, m: dict, now: float) -> None:
        self.clock.nudge_phase(float(m["beats"]), now)

    def _cmd_downbeat(self, m: dict, now: float) -> None:
        self.clock.set_downbeat(now)

    # auto -----------------------------------------------------------------

    def _cmd_auto(self, m: dict, now: float) -> None:
        axis = m["axis"]
        if axis not in ("timing", "look_changes", "palette", "energy"):
            raise ValueError(f"unknown auto axis {axis!r}")
        setattr(self.director.config, axis, bool(m["on"]))

    def _cmd_auto_interval(self, m: dict, now: float) -> None:
        field_name = {"looks": "change_every_phrases",
                      "palette": "palette_every_phrases"}[m["axis"]]
        setattr(self.director.config, field_name, max(0.0625, float(m["value"])))

    def _cmd_energy(self, m: dict, now: float) -> None:
        source = m.get("source", "manual")
        if source == "manual":
            level = float(m.get("value", 0.5))
            if not isinstance(self.director.energy_source, autom.ManualEnergy):
                self.director.energy_source = autom.ManualEnergy(level)
            else:
                self.director.energy_source.value = level
        elif source == "phrase":
            self.director.energy_source = autom.PhraseEnergy()
        else:
            raise ValueError(f"unknown energy source {source!r}")

    # colour ---------------------------------------------------------------

    def _cmd_color(self, m: dict, now: float) -> None:
        """Per-fixture picker and global quick palette, in one command.

        `target` is a tag, a fixture name, or "all". Applied as an override
        layer rather than by rebuilding the look, so a colour picked by hand
        survives an auto-mode look change -- which is what "everyone controls
        everything" needs to mean in practice.
        """
        color = tuple(max(0.0, min(1.0, float(c))) for c in m["color"])
        target = m.get("target", "all")
        if m.get("clear"):
            self.color_overrides.pop(target, None)
        else:
            self.color_overrides[target] = color
        self._rebuild_overrides()

    def _cmd_palette_select(self, m: dict, now: float) -> None:
        self.palette.index = int(m["index"]) % len(self.palette.colors)
        self.director.rebuild()

    def _rebuild_overrides(self) -> None:
        layers: list[statemod.Layer] = []
        for target, color in self.color_overrides.items():
            tags = None if target == "all" else (target,)
            layers.append(statemod.color_layer(color, tags=tags))
        for name, (pan, tilt) in self.jog.items():
            layers.append(statemod.raw_pose_layer({name: (pan, tilt)}, intensity=1.0))
        self.override_layers = layers

    # calibration ----------------------------------------------------------

    def _cmd_jog(self, m: dict, now: float) -> None:
        """Drive one head at literal DMX so it can be aimed by eye.

        Bypasses the aim maths, and therefore the safety taper, because the aim
        maths is precisely what is being calibrated. The UI is responsible for
        saying so -- see the notice below, which the phone shows in red.
        """
        name = m["fixture"]
        self.jog[name] = (int(m["pan"]), int(m["tilt"]))
        self._rebuild_overrides()
        self.note(f"JOG {name}: safety taper bypassed -- empty room only")

    def _cmd_jog_clear(self, m: dict, now: float) -> None:
        self.jog.pop(m["fixture"], None) if "fixture" in m else self.jog.clear()
        self._rebuild_overrides()

    def _cmd_capture(self, m: dict, now: float) -> None:
        name = m["fixture"]
        pan, tilt = self.jog.get(name, (int(m.get("pan", 0)), int(m.get("tilt", 0))))
        self.captures.setdefault(name, []).append(calibmod.Capture(
            target=tuple(float(c) for c in m["target"]),
            pan=pan, tilt=tilt, label=m.get("label", "")))
        self.note(f"captured {name} at {m.get('label', 'a target')} "
                  f"({len(self.captures[name])} so far)")

    def _cmd_capture_clear(self, m: dict, now: float) -> None:
        self.captures.pop(m["fixture"], None) if "fixture" in m else self.captures.clear()

    def _cmd_solve(self, m: dict, now: float) -> None:
        """Solve from the captures taken so far, optionally writing them."""
        if self.rig.geometry is None:
            raise ValueError("no geometry to solve")
        results = []
        for i, head in enumerate(self.rig.geometry.heads):
            caps = self.captures.get(head.name, [])
            if len(caps) < 2:
                continue
            solution = calibmod.solve_head(head, self.rig.venue.ball,
                                           self.rig.geometry.mount_mode, caps)
            results.append(solution)
            self.note(f"{head.name}: ball {solution.ball_dmx} "
                      f"pan_invert={solution.pan_invert} "
                      f"residual {solution.residual_deg:.2f} deg"
                      + ("  " + "; ".join(solution.warnings) if solution.warnings else ""))
        if not results:
            raise ValueError("no head has 2 or more captures yet")
        self.last_solution = results
        if m.get("write"):
            self._write_calibration(results)

    def _write_calibration(self, results) -> None:
        path = self.event_dir / "calibration.json"
        existing = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        calibmod.save_snapshot(self.event_dir, existing, note="before web solve")
        by_name = {s.head_name: s.as_calibration_entry() for s in results}
        heads = []
        for head in self.rig.geometry.heads:
            heads.append(by_name.get(head.name, {
                "fixture": head.name,
                "ball_dmx": list(head.calibrated_ball_dmx) if head.calibrated_ball_dmx else None,
                "pan_invert": head.pan_invert, "tilt_invert": head.tilt_invert}))
        payload = {"measured": time.strftime("%Y-%m-%d"),
                   "mount_mode": self.rig.geometry.mount_mode,
                   "source": "solved from the web UI", "heads": heads}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
        calibmod.save_snapshot(self.event_dir, payload, note="after web solve")
        self.note(f"wrote {path.name}; restart the engine to load it")

    def _cmd_drift(self, m: dict, now: float) -> None:
        """Compare fresh ball readings against the stored calibration."""
        readings = m["readings"]
        heads = self.rig.geometry.heads
        drifts = [calibmod.drift_for_head(h, self.rig.venue.ball,
                                          self.rig.geometry.mount_mode,
                                          tuple(readings[i]))
                  for i, h in enumerate(heads) if i < len(readings)]
        self.last_drift = [{"head": d.head_name,
                            "bearing": round(d.bearing_deg, 2),
                            "elevation": round(d.elevation_deg, 2),
                            "significant": d.significant} for d in drifts]
        moved = sum(1 for d in drifts if d.significant)
        self.note(f"drift check: {moved} head(s) moved 3 deg or more")

    def _cmd_hello(self, m: dict, now: float) -> None:
        pass                          # name is set by the connection handler

    # -- the snapshot ------------------------------------------------------

    def snapshot(self) -> dict:
        """Everything the UI needs, built on the caller's thread."""
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
            }
            if st is not None:
                entry["intensity"] = round(st.intensity, 3)
                entry["color"] = [round(c, 3) for c in st.color]
                if st.safety is not None:
                    entry["safety"] = {"taper": round(st.safety.taper, 3),
                                       "reason": st.safety.reason}
                if st.aim is not None and g is not None and f.head is not None:
                    entry["aim"] = {"bearing": round(st.aim.bearing_delta, 2),
                                    "elevation": round(st.aim.elev_deg, 2)}
                    land = safetymod.landing(g, f.head, st.aim, self.rig.venue)
                    if land is not None:
                        entry["lands_on"] = land.surface
                        entry["throw_mm"] = round(land.distance)
                entry["jogging"] = f.name in self.jog
                entry["captures"] = len(self.captures.get(f.name, []))
            fixtures.append(entry)

        return {
            "type": "state",
            "rev": self.rev,
            "event": self.rig.name,
            "clock": {"bpm": round(self.clock.bpm, 2),
                      "effective_bpm": round(self.clock.effective_bpm, 2),
                      "speed": round(self.clock.speed, 3),
                      "beat": round(self.ctx.beat, 3),
                      "bar": round(self.ctx.bar, 3),
                      "phrase": round(self.ctx.phrase, 3),
                      "beat_in_bar": round(self.ctx.beat % 4, 3),
                      "source": self.clock.source,
                      "phrase_measured": self.clock.phrase_measured,
                      "taps": self.clock.taps},
            "auto": self.director.status(),
            "looks": [{"name": l.name, "manual_only": l.manual_only}
                      for l in self.setlist.looks],
            "palette": [list(c) for c in self.palette.colors],
            "palette_index": self.palette.index,
            "master": round(self.master, 3),
            "blackout": self.blackout,
            "panicked": self.runner.panicked,
            "color_overrides": {k: list(v) for k, v in self.color_overrides.items()},
            "fixtures": fixtures,
            "venue": venue_summary(self.rig.venue),
            "presence": [c.public(now) for c in self.clients.values()],
            "stats": {"fps": round(stats.effective_fps, 2),
                      "frames": stats.frames,
                      "drops": stats.drops,
                      "eval_errors": stats.eval_errors,
                      "worst_error_ms": round(stats.worst_error * 1000, 3)},
            "notices": list(self.notices[-8:]),
            "warnings": self.rig.warnings(),
            "last_error": self.runner.last_error,
            "drift": getattr(self, "last_drift", None),
        }


def venue_summary(venue) -> dict:
    if venue is None:
        return {}
    crowd = venue.crowd_zone
    return {"name": venue.name, "width": venue.width, "depth": venue.depth,
            "height": venue.height, "ball": list(venue.ball),
            "crowd": None if crowd is None else {
                "min_x": crowd.footprint.min_x, "max_x": crowd.footprint.max_x,
                "min_z": crowd.footprint.min_z, "max_z": crowd.footprint.max_z,
                "head_band_min": crowd.head_band_min,
                "head_band_max": crowd.head_band_max},
            "canopy": None if venue.canopy is None else {
                "enabled": venue.canopy.enabled, "height": venue.canopy.height,
                "radius": venue.canopy.radius}}


def describe(message: dict) -> str:
    kind = message.get("type", "?")
    if kind == "select_look":
        return f"selected {message.get('name')!r}"
    if kind == "color":
        return f"coloured {message.get('target', 'all')}"
    if kind == "auto":
        return f"turned {message.get('axis')} {'on' if message.get('on') else 'off'}"
    if kind in ("tap", "downbeat", "bpm", "speed"):
        return f"set the tempo ({kind})"
    if kind == "jog":
        return f"jogged {message.get('fixture')}"
    if kind == "capture":
        return f"captured {message.get('fixture')}"
    return kind


# -------------------------------------------------------------- default show --

def default_setlist() -> autom.SetList:
    """A starter set list, built from the motion primitives.

    Placeholder for the ported library (F9) -- deliberately small, so it is
    obvious this is scaffolding rather than the real show.
    """
    def look(name, offset_fn, bars):
        def make(color):
            show = statemod.Show()
            show.base.append(statemod.pose_layer(
                lambda ctx, head: ctx.geometry.aim_at_ball(head), tags=("movers",)))
            show.base.append(statemod.on_layer(0.7, tags=("pinspots",)))
            show.color.append(statemod.color_layer(color))
            if offset_fn is not None:
                show.movement.append(statemod.move_layer(
                    motion.as_move(offset_fn, bars=bars), tags=("movers",)))
            show.fx.append(autom.energy_intensity_layer())
            return show
        return autom.Look(name=name, make=make)

    return autom.SetList([
        look("ball", None, 0.0),
        look("drift", motion.orbit(15.0, elongation=1.5), 16.0),
        look("sweep", motion.pendulum(45.0), 8.0),
        look("wide orbit", motion.orbit(40.0), 8.0),
        look("bob", motion.pendulum(18.0, vertical=True), 4.0),
    ])


def default_palette() -> autom.Palette:
    return autom.Palette([
        (1.0, 1.0, 1.0), (1.0, 0.1, 0.05), (1.0, 0.55, 0.05), (1.0, 0.95, 0.15),
        (0.15, 1.0, 0.25), (0.1, 0.85, 1.0), (0.2, 0.35, 1.0), (1.0, 0.2, 0.75),
    ])


# ------------------------------------------------------------------- server --

class ShowServer:
    """HTTP for the UI bundle, WebSocket for everything live."""

    def __init__(self, controller: ShowController, port: int = 8765,
                 ui_dir: Path = UI_DIST):
        self.controller = controller
        self.port = port
        self.ui_dir = Path(ui_dir)
        self.sockets: dict[str, tuple[WebSocket, threading.Lock]] = {}
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._stop = threading.Event()
        self.httpd: Optional[ThreadingHTTPServer] = None

    def new_client_id(self) -> str:
        with self._id_lock:
            self._next_id += 1
            return f"c{self._next_id}"

    # -- broadcasting ------------------------------------------------------

    def broadcast_loop(self) -> None:
        """Serialise and push at BROADCAST_HZ.

        Deliberately not per frame. The display cannot show 40 Hz, and
        serialising this much JSON 40 times a second on a thread sharing a GIL
        with the DMX clock is precisely the in-process CPU-bound work the F2
        spike identified as the thing that breaks it.
        """
        period = 1.0 / BROADCAST_HZ
        while not self._stop.is_set():
            start = time.perf_counter()
            try:
                payload = json.dumps(self.controller.snapshot())
            except Exception:                               # noqa: BLE001
                self.controller.note("snapshot failed: " + traceback.format_exc(limit=1))
                payload = None
            if payload is not None:
                self.send_all(payload)
            elapsed = time.perf_counter() - start
            self._stop.wait(max(0.0, period - elapsed))

    def send_all(self, payload: str) -> None:
        for cid, (ws, lock) in list(self.sockets.items()):
            try:
                # Per-connection lock: a broadcast and a command acknowledgement
                # interleaving on one socket would splice two frames together
                # and corrupt both.
                with lock:
                    ws.send(payload)
            except (WebSocketClosed, WebSocketError, OSError):
                self.drop(cid)

    def drop(self, cid: str) -> None:
        entry = self.sockets.pop(cid, None)
        self.controller.clients.pop(cid, None)
        if entry is not None:
            try:
                entry[0].close()
            except OSError:
                pass

    # -- one connection ----------------------------------------------------

    def serve_websocket(self, sock: socket.socket, key: str) -> None:
        ws = WebSocket(sock)
        cid = self.new_client_id()
        lock = threading.Lock()
        client = Client(id=cid, connected_at=time.time())
        self.sockets[cid] = (ws, lock)
        self.controller.clients[cid] = client
        try:
            with lock:
                ws.send(json.dumps({"type": "welcome", "id": cid}))
                ws.send(json.dumps(self.controller.snapshot()))
            while not self._stop.is_set():
                text = ws.receive()
                if text is None:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                if message.get("type") == "hello":
                    client.name = str(message.get("name", "someone"))[:40]
                self.controller.submit(message, client)
        except (WebSocketClosed, WebSocketError, OSError):
            pass
        finally:
            self.drop(cid)

    # -- running -----------------------------------------------------------

    def start(self) -> None:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            # Unbuffered reads. Without this, `rfile` can pull bytes past the
            # end of the handshake into a buffer the WebSocket layer never sees
            # -- so a client that pipelines its first frame immediately after
            # the upgrade has that frame silently eaten. With rbufsize 0 the
            # socket stays the single source of truth once we hand it over.
            rbufsize = 0

            def log_message(self, *args):        # quiet; the UI shows status
                pass

            def finish(self):
                # The WebSocket session closes the socket itself, so the normal
                # teardown is operating on an already-dead connection. Let it
                # try and swallow what it finds, rather than leaving
                # ThreadingHTTPServer to print a traceback per disconnect.
                try:
                    super().finish()
                except (OSError, ValueError, AttributeError):
                    pass

            def do_GET(self):
                if self.headers.get("Upgrade", "").lower() == "websocket":
                    key = self.headers.get("Sec-WebSocket-Key")
                    if not key:
                        self.send_error(400, "missing Sec-WebSocket-Key")
                        return
                    from .websocket import handshake_response
                    self.wfile.write(handshake_response(key))
                    self.wfile.flush()
                    # Hand the raw socket over. close_connection stops the
                    # handler looping for another request line off a socket that
                    # is now speaking a different protocol; `finish` above deals
                    # with the teardown finding it already closed.
                    self.close_connection = True
                    server.serve_websocket(self.connection, key)
                    return
                self.serve_static()

            def serve_static(self):
                path = self.path.split("?", 1)[0]
                if path == "/":
                    path = "/index.html"
                target = (server.ui_dir / path.lstrip("/")).resolve()
                try:
                    # Containment check: a request for /../../secrets must not
                    # escape the bundle directory.
                    target.relative_to(server.ui_dir.resolve())
                except (ValueError, OSError):
                    self.send_error(403)
                    return
                if not target.is_file():
                    # A single-page app owns its own routing, so an unknown path
                    # is a client route, not a 404 -- unless the bundle is
                    # missing entirely, which deserves a real explanation.
                    index = server.ui_dir / "index.html"
                    if not index.is_file():
                        self.send_placeholder()
                        return
                    target = index
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type",
                                 MIME.get(target.suffix, "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def send_placeholder(self):
                body = PLACEHOLDER.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.httpd = ThreadingHTTPServer(("0.0.0.0", self.port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, name="http",
                         daemon=True).start()
        threading.Thread(target=self.broadcast_loop, name="broadcast",
                         daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for cid in list(self.sockets):
            self.drop(cid)
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()


PLACEHOLDER = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>cosmos engine</title>
<style>
 body{font:16px/1.6 system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:2rem;}
 code{background:#222;padding:.15em .4em;border-radius:3px;}
 .ok{color:#7c7}
</style>
<h1>Engine is running</h1>
<p class="ok">The WebSocket is live on this same port. The UI bundle has not been built yet.</p>
<pre><code>cd ui
npm install
npm run build</code></pre>
<p>Then reload. For development with hot reload, <code>npm run dev</code> serves on
its own port and proxies here.</p>
"""


def local_addresses(port: int) -> list[str]:
    """URLs a phone on the same network can actually reach.

    Printed at startup because the single most common load-in failure is typing
    localhost into a phone. Uses a UDP connect to a public address, which sets
    the socket's local address without sending anything -- the reliable way to
    learn which interface would carry outbound traffic.
    """
    urls = [f"http://localhost:{port}"]
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        urls.append(f"http://{probe.getsockname()[0]}:{port}")
        probe.close()
    except OSError:
        pass
    return urls


def main(argv: Optional[list[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="Run the show engine and its UI")
    parser.add_argument("--event", type=Path, default=REPO / "events" / "despacio")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--artnet", metavar="IP",
                        help="send Art-Net here (e.g. 127.0.0.1 or a broadcast address)")
    parser.add_argument("--fps", type=float, default=40.0)
    parser.add_argument("--bpm", type=float, default=124.0)
    parser.add_argument("--ui", type=Path, default=UI_DIST)
    args = parser.parse_args(argv)

    controller = ShowController(args.event, artnet=args.artnet, fps=args.fps,
                                bpm=args.bpm)
    server = ShowServer(controller, port=args.port, ui_dir=args.ui)

    controller.start()
    server.start()

    print(f"event   {controller.rig.name}  "
          f"({len(controller.rig.fixtures)} fixtures, universes {controller.rig.universes})")
    print(f"output  {'Art-Net -> ' + args.artnet if args.artnet else 'null (no wire)'}")
    print(f"timing  {', '.join(controller.runner.applied_timing)}")
    print(f"ui      {'bundle at ' + str(args.ui) if Path(args.ui).is_dir() else 'not built -- see the page for how'}")
    for url in local_addresses(args.port):
        print(f"open    {url}")
    print("\nCtrl-C to stop.")

    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        server.stop()
        controller.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
