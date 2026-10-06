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
import math
import queue
import random
import secrets
import socket
import sys
import threading
import time
import traceback
from urllib.parse import parse_qs, urlparse
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

from . import __version__
from . import api as apimod
from . import auto as autom
from . import calibrate as calibmod
from . import config as configmod
from . import cues as cuesmod
from . import clock as clockmod
from . import geometry as geo
from . import library as libmod
from . import blocks as blocksmod
from . import modulate as modmod
from . import motion
from . import params as parammod
from . import patch as patchmod
from . import playback as playbackmod
from . import program as programmod
from . import rig as rigmod
from . import routines as routinesmod
from . import safety as safetymod
from . import scene as scenemod
from . import showfiles
from . import showlibrary
from . import state as statemod
from . import sync as syncmod
from . import outputs as outputsmod
from . import templates as templatesmod
from . import timeline as timelinemod
from . import tracks as tracksmod
from . import transport as transportmod
from . import venue as venuemod
from . import worker as workermod
from .output import ArtNetOutput, NullOutput
from .output.artnet import parse_targets as parse_artnet_targets
from .runner import Runner
from .websocket import WebSocket, WebSocketClosed, WebSocketError

REPO = Path(__file__).resolve().parent.parent
UI_DIST = REPO / "ui" / "dist"

BROADCAST_HZ = 10.0

# What a command handler returns when it will answer later, from the worker
# (`ShowController._deferred`). Never sent anywhere.
DEFERRED = object()

# How many snapshots a client may fall behind before it is dropped. Three is a
# third of a second at BROADCAST_HZ: long enough to ride out a garbage collection
# or a wifi hiccup, short enough that a client which has genuinely gone away is
# gone before anyone reaches for the master and wonders why nothing moved.
QUEUE_DEPTH = 3

# A solve fitting worse than this is not written over a working calibration.
# Comfortably above the 0.67 deg worst case the solver hits on clean captures,
# and well below the tens of degrees a mistaken capture produces.
MAX_WRITE_RESIDUAL_DEG = 5.0

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
        ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json"}


# --------------------------------------------------------------- access ----

# Three tiers, because "anyone on the venue wifi can do anything" stopped being
# acceptable once the patch became editable from the UI. The threat here is not
# an attacker -- it is the guest who opens the URL you showed someone, starts
# pressing, and re-addresses the rig between sets.
#
#   view       read the state. The default for a client that arrives with no
#              token, so showing someone the console stays a one-tap thing.
#   operate    run the show: looks, colour, tempo, master, blackout, panic.
#   configure  change what survives the night, or bypass a guard: jog (which
#              disables the safety taper for that head), writing a calibration,
#              editing the room, editing the patch.
#
# PANIC is deliberately `operate`, not `configure`. It is the one command whose
# cost of being unavailable to the wrong person exceeds the cost of being
# available to them -- everything it does is undone by pressing it again.
TIERS = ("view", "operate", "configure")

TIER: dict[str, str] = {
    "hello": "view",
    # configure: persists past tonight, or steps around a safety guard
    "jog": "configure", "jog_clear": "configure",
    "capture": "configure", "capture_clear": "configure", "solve": "configure",
    "drift": "configure", "venue": "configure", "venue_save": "configure",
    "taper": "configure",
    "patch_add": "configure", "patch_remove": "configure",
    "patch_address": "configure", "patch_tags": "configure",
    "patch_position": "configure", "patch_autopatch": "configure",
    "patch_apply": "configure",
    # the show folder: what is written there outlives the night
    "track_link": "configure", "show_reload": "configure",
    "show_latency": "configure",
    # the designer: writes the show folder, and can take the stage
    "timeline_draft": "configure", "timeline_save": "configure",
    "routine_draft": "configure", "routine_save": "configure",
    "preview_arm": "configure", "preview_transport": "configure", "preview_release": "configure",
    # GO is `operate`: driving the night is the job, not configuration.
    # everything not listed is `operate` -- see apply()
}


class Connection:
    """One socket, with the thread that owns writing to it.

    The write side is a queue and a thread rather than a direct call, so the
    broadcast loop can hand off a payload without ever waiting on a client --
    see `ShowServer.send_all` for why that matters.
    """

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.lock = threading.Lock()
        self.queue: "queue.Queue[Optional[str]]" = queue.Queue(maxsize=QUEUE_DEPTH)
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def _pump(self) -> None:
        while True:
            payload = self.queue.get()
            if payload is None:                     # close sentinel
                return
            try:
                # Same per-connection lock as before: a broadcast and a command
                # acknowledgement interleaving on one socket would splice two
                # frames together and corrupt both.
                with self.lock:
                    self.ws.send(payload)
            except (WebSocketClosed, WebSocketError, OSError):
                return

    def close(self) -> None:
        """Tear the connection down without ever blocking the caller.

        The subtle part: `WebSocket.close` sends a courtesy close frame with a
        blocking `sendall`, and the client most likely to be dropped is the one
        whose buffer is already full -- so the polite goodbye blocks on exactly
        the socket that caused the drop, and `drop()` re-freezes the broadcast
        thread it was called from to prevent that freeze. The queue fixed the
        send path and left this one, which the test then found.

        So the close frame gets a deadline, and failing that the socket is torn
        down underneath it. A browser that misses the frame just sees the TCP
        close and reconnects on the backoff it already implements.
        """
        try:
            self.queue.put_nowait(None)
        except queue.Full:
            pass                    # the pump is stuck; the shutdown below ends it
        try:
            self.ws.sock.settimeout(0.2)
            self.ws.close()
        except (OSError, WebSocketClosed, WebSocketError):
            pass
        # Unconditional, because `ws.close()` may have given up on a half-sent
        # close frame and left the socket open with the pump still parked in it.
        try:
            self.ws.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.ws.sock.close()
        except OSError:
            pass


# ------------------------------------------------------------------ clients --

@dataclass
class Client:
    """One connected browser. `name` is whatever it called itself."""
    id: str
    name: str = "someone"
    connected_at: float = 0.0
    last_action: str = ""
    last_action_at: float = 0.0
    tier: str = "configure"

    def may(self, need: str) -> bool:
        return TIERS.index(self.tier) >= TIERS.index(need)

    def public(self, now: float) -> dict:
        return {"id": self.id, "name": self.name, "tier": self.tier,
                "connected_for": round(now - self.connected_at, 1),
                "last_action": self.last_action,
                "last_action_ago": (round(now - self.last_action_at, 1)
                                    if self.last_action else None)}


# --------------------------------------------------------------- controller --

class ShowController:
    """Owns the engine. Every mutation arrives as a queued command."""

    def __init__(self, event_dir: Path, artnet: Optional[str] = None,
                 fps: float = 40.0, bpm: float = 124.0,
                 show_dir: Optional[Path] = None,
                 local_outputs: Optional[dict] = None):
        self.event_dir = Path(event_dir)
        # The other outputs (milestone 3): OSC and friends, pointed where the
        # show folder says -- or this machine's klights.local.json, which wins.
        self.outputs = outputsmod.Outputs()
        self.local_outputs = dict(local_outputs or {})
        self._output_frame: Optional[outputsmod.ProgramFrame] = None
        # The last outputs failure said, and when: (message, engine time).
        self._output_said: Optional[tuple[str, float]] = None
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
        taper_cfg = json.loads(
            self.rig.venue_file.read_text(encoding="utf-8")
        ).get("taper", {})
        taper = safetymod.TaperConfig(
            crowd_level=float(taper_cfg.get("crowd_level", 0.5)),
            margin_deg=float(taper_cfg.get("margin_deg", 6.0)),
            slew_per_second=float(taper_cfg.get("slew_per_second", 2.0)),
            enabled=bool(taper_cfg.get("enabled", True)))

        # Strobe is the one genuine medical risk here (photosensitive
        # epilepsy) and nothing used to limit it. Policy lives beside the taper
        # because it is the same kind of thing: a property of the room and the
        # crowd in it, not of the rig.
        strobe_cfg = json.loads(
            self.rig.venue_file.read_text(encoding="utf-8")).get("strobe", {})
        strobe_policy = safetymod.StrobeConfig(
            enabled=bool(strobe_cfg.get("enabled", True)),
            ceiling=float(strobe_cfg.get("ceiling", 1.0)),
            max_seconds=float(strobe_cfg.get("max_seconds", 0.0)),
            recover_seconds=float(strobe_cfg.get("recover_seconds", 2.0)))

        self.ctx = statemod.EvalContext(rig=self.rig, venue=self.rig.venue,
                                        taper=taper,
                                        strobe_policy=strobe_policy)
        self.clock = clockmod.MasterClock(bpm=bpm, now=0.0)
        # The ported library if the event has one, otherwise a small scaffold.
        # Falling back rather than failing means a brand-new event runs before
        # anything has been ported into it.
        looks_path = self.event_dir / "looks.json"
        # `parametric_looks.json` is optional and separate: looks.json is
        # generated by the porter and guarded as a reproducible artifact, so
        # hand-authored looks cannot live in it. An event without one loads
        # exactly as before.
        parametric_path = self.event_dir / "parametric_looks.json"
        if looks_path.exists():
            self.setlist, self.library = libmod.load_setlist(
                looks_path, parametric_path)
        else:
            self.setlist, self.library = default_setlist(), []
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
            before_frame=self._before_frame, on_show=self._attach_overrides,
            on_frame=self._publish)

        # Each entry is (command dict, sender, arrival time) -- or, from inside
        # the engine only, (callable, None, time): see `submit_call`.
        self.commands: "queue.Queue[tuple[Any, Optional[Client], float]]" = queue.Queue()
        self.clients: dict[str, Client] = {}
        # How a reply reaches the one client that asked for it. Set by the
        # ShowServer that owns the sockets; None when there is no server (the
        # tests that drive a controller directly), and then nothing is sent.
        self.reply_to: Optional[Callable[[str, dict], None]] = None
        # Anything slow -- parsing a show file, matching a track, an fsync to a
        # shared folder -- runs here and posts its result back through
        # `submit_call`, so it lands on the output thread at a frame boundary.
        self.worker = workermod.Worker(post=self.submit_call, report=self.note)
        self.master = 0.9
        self.blackout = False

        # Live operator overrides, re-attached to whatever Show is running so
        # they survive an auto-mode look change.
        self.override_layers: list[statemod.Layer] = []
        # Per-routine tuning, by look name -- only the keys the operator has
        # actually moved. Stored as a sparse override rather than a full
        # resolved set so the three layers stay distinct and visible: the
        # generator's default, what the routine authored over it, and what is
        # dialled in now. A full snapshot here would silently freeze a routine's
        # authored value at the moment it was first touched.
        self.look_params: dict[str, dict] = {}
        # Parameters that move on their own. Empty by default, and an empty rack
        # costs one `if` per frame -- a show that never touches modulation runs
        # exactly as it did before this existed.
        self.modulators = modmod.Rack()
        # Movement routines stacked ON TOP of whatever the director has up.
        # Every movement layer adds a degree offset -- only the base pose layer
        # assigns -- so stacking is free and the sum is well defined. Held as
        # names rather than entries so `entry()` still applies tuning and
        # modulation to each on every recompose.
        self.movement_extra: list[str] = []
        # Bumped per `vary`, so pressing it twice gives two different results
        # without reaching for a clock or the module RNG. Published, so a
        # variation worth keeping can be written down.
        self.vary_seed = 0
        self.color_overrides: dict[str, tuple[float, float, float]] = {}
        # RGBW white, per target -- separate from color_overrides because not
        # every fixture has a white channel, and a target that mixes RGB-only
        # and RGBW fixtures should not force an opinion on the ones without one.
        self.white_overrides: dict[str, float] = {}
        # Hand dimming, per fixture or per group. A multiplier, applied after
        # the level slot and before the master -- so it trims a running pattern
        # rather than replacing it, and the master still governs the lot.
        self.level_overrides: dict[str, float] = {}
        # Momentary bumps, by target. Held while a finger is down and released
        # when it lifts -- the one control here that is not a state you leave
        # somewhere, which is why it is a set rather than a level.
        self.flashing: set[str] = set()
        self.jog: dict[str, tuple[int, int]] = {}
        self.captures: dict[str, list[calibmod.Capture]] = {}
        self.last_drift: Optional[list[dict]] = None

        self.latest_states: dict[int, statemod.FixtureState] = {}
        self.notices: list[str] = []
        self.rev = 0

        # The three independent slots. Selecting a colour must not disturb the
        # movement and vice versa -- while one selection replaced the entire
        # show, picking a colour threw away the move you had running, which is
        # the opposite of what splitting the scenes during the port was for.
        self.by_name = {e.name: e for e in self.library}
        # Colour and level are stored PER FIXTURE GROUP, so the pinspots can be
        # on their own colour while the movers are on another. One slot for the
        # whole rig meant picking a pinspot palette threw away the movers', and
        # the two are simply different decisions.
        #
        # MOVEMENT is read through to the set list rather than stored, because
        # auto mode advances that itself; two copies of "which movement look is
        # up" would need syncing on every auto change, and the one that got
        # missed would be the one the UI displays.
        self.slots: dict[str, dict[str, str]] = {"color": {}, "level": {}}
        # Tempo ingest, opened only by `enable_sync`. See engine/sync.py for why
        # an open UDP port that can move the show clock is off by default.
        self.sync: Optional[syncmod.SyncListener] = None
        # What the bridge says is playing. Held here rather than on the clock
        # because it is not musical time -- it is a label for the operator, and
        # a clock that carried track titles would be a clock with opinions.
        self.sync_deck: Optional[str] = None
        self.sync_track: Optional[str] = None
        # Which track the DJ is playing and where in it (F19). Fed by the same
        # `sync` command as the clock; read by the snapshot from another
        # thread, which is safe because it keeps its state in one immutable
        # value swapped by reference.
        self.transport = transportmod.TrackTransport()
        # The show folder (F19f): prepped tracks, timelines, routines. Optional
        # -- with no folder nothing below exists and the engine is exactly what
        # it was. Loaded here, synchronously, because nothing is running yet;
        # every later reload happens on the worker.
        self.show_dir = Path(show_dir) if show_dir is not None else None
        self.show_library: Optional[showlibrary.Library] = None
        # What the playing track was matched to, against which load. Replaced
        # only when the track changes -- see showlibrary.Pinned.
        self.pinned: Optional[showlibrary.Pinned] = None
        self.grid_check: Optional[tracksmod.GridCheck] = None
        self._check_jump: Optional[int] = None
        self.watcher: Optional[showlibrary.Watcher] = None
        # Whether the timeline drives the rig this frame (F19i). Only with a
        # show folder; without one the runner never asks.
        self.player: Optional[playbackmod.TrackPlayer] = None
        self._frame_sample: Optional[transportmod.TrackSample] = None
        # What the other decks have loaded (milestone 2), from beat-link-
        # trigger: matched, and their timelines built in advance.
        self.decks: dict[str, dict] = {}
        self.presets = load_presets(self.event_dir)
        # Routines on preset pads (milestone 2): the one playing, the one
        # waiting for its downbeat, and every routine pad built for this rig.
        self.pad: Optional[dict] = None
        self._pad_pending: Optional[dict] = None
        # A routine that could not be built is kept here as its exception:
        # the pad then lands its looks alone, rather than waiting for ever.
        self._pad_programs: dict[str, Any] = {}
        self._pad_gen = 0               # bumped when the rig or folder changes
        if self.show_dir is not None:
            self.player = playbackmod.TrackPlayer(
                self.transport, pinned=lambda: self.pinned,
                rigging=self._rigging, submit=self.worker.submit,
                post=self.submit_call, note=self.note,
                clock_beat=lambda: self.ctx.beat,
                base_palette=self._base_palette,
                clock_phrase=lambda: (self.clock.phrase_label,
                                      self.clock.phrase_start),
                clock_bpm=lambda: self.clock.effective_bpm)
            self.runner.choose_show = self._choose_show
            self._install_library(showlibrary.load(self.show_dir))
            show = self.show_library.folder.show or {}
            self.player.arm((show.get("follow") or {}).get("default") == "armed")
            self.watcher = showlibrary.Watcher(
                self.show_dir, on_change=self._library_changed,
                report=lambda text: self.submit_call(lambda: self.note(text)))
        # The cue list, if this event has one. Optional: a show driven entirely
        # by hand off the look picker is still a show, and the despacio night
        # was one for its whole first run.
        self.cues: Optional[cuesmod.CueList] = None
        cue_path = self.event_dir / "cues.json"
        if cue_path.exists():
            try:
                self.cues = cuesmod.load(cue_path)
            except configmod.ConfigError as exc:
                self.notices.append(f"cues.json not loaded: {exc}")
        # Profiles, resolved once. The patch editor needs to size a fixture's
        # channel footprint to answer "does this address clash", and rescanning
        # shared/fixtures/ per keystroke on a phone is not the way.
        self.profiles = rigmod.ProfileLibrary()
        # Set when an edit has been written that the running show is not using.
        # The UI turns this into a standing banner, because a patch that is
        # saved and not loaded is exactly the state where the file and the rig
        # disagree and nothing on screen says so.
        self.pending_patch = False
        self._recompose()

    @property
    def selection(self) -> dict[str, dict[str, str]]:
        current = self.setlist.current()
        entry = self.entry(current.name) if current else None
        movement = ({g: current.name for g in (entry.groups or ("movers",))}
                    if current and entry else {})
        return {"movement": movement,
                "color": dict(self.slots["color"]),
                "level": dict(self.slots["level"])}

    def palette_roles(self, current: tuple[float, float, float]) -> dict:
        """The console's palette, as the roles a block's colours name.

        `primary` is the colour auto mode (or the operator) has up right now;
        `secondary` and `accent` are the next two round the palette. So a duo
        built on "@primary"/"@secondary" turns when the palette does, the same
        way a routine's roles follow its show folder's palette -- the console
        has no named palette of its own, and the rotating list it does have is
        the closest honest equivalent.
        """
        colors = list(self.palette.colors) or [current]
        here = self.palette.index % len(colors)
        return {"primary": current,
                "secondary": colors[(here + 1) % len(colors)],
                "accent": colors[(here + 2) % len(colors)]}

    def slot_entries(self, slot: str) -> list[libmod.LibraryEntry]:
        """The distinct looks filling one slot across all groups.

        De-duplicated by name: a look covering two groups occupies both, and
        applying its layers twice would double its effect.
        """
        seen: dict[str, libmod.LibraryEntry] = {}
        for name in self.slots[slot].values():
            entry = self.entry(name)
            if entry is not None:
                seen[name] = entry
        return list(seen.values())

    # -- lifecycle ---------------------------------------------------------

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
        self.runner.start()

    def stop(self) -> None:
        if self.sync is not None:
            self.sync.stop()
        if self.watcher is not None:
            self.watcher.stop()
        self.runner.stop()
        self.worker.stop()
        self.output.close()
        self.outputs.close()
        try:
            patchmod.lock_path(str(self.event_dir)).unlink(missing_ok=True)
        except OSError:
            pass

    # -- the frame hooks ---------------------------------------------------

    def _before_frame(self) -> None:
        self._drain()
        self._frame_sample = self._track_frame()
        self._pad_frame(self.runner.now())

    def _choose_show(self, fallback: statemod.Show) -> statemod.Show:
        """The runner's hook: the timeline's show while it drives, else
        auto mode's, unchanged. A routine pad playing is the operator's show
        -- over the looks, which show wherever it says nothing."""
        now = self.runner.now()
        pad = self.pad
        if pad is not None:
            prog = pad["program"]
            prog.grabbed = frozenset()
            prog.begin(self.clock.beat(now) - pad["start"], fallback=fallback,
                       base_palette=self._base_palette())
            fallback = prog.show
        sample = self._frame_sample or self.transport.sample(now)
        show = self.player.choose(fallback, sample, now)
        extra = ()
        if pad is not None:
            extra = (("pad", f"pad:{pad['name']}@{pad['start']:g}", pad["program"]),)
        # Every frame, for the outputs and for the snapshot's visuals section
        # (a #visuals page may be open whether or not anything else is on).
        try:
            frame = self.player.output_frame(extra)
            self._output_frame = frame
            if self.outputs.active:
                self.outputs.send(frame, now)
        except Exception as exc:                            # noqa: BLE001
            # The other outputs never cost the lights a frame: the show chosen
            # above goes on stage whatever an OSC, MIDI or timecode send did.
            # Said once per message, or again after a while -- not 40 times a
            # second, nor once per flap of one that fails every other frame.
            self._output_frame = None
            said = f"the other outputs failed and are skipped: {exc}"
            last = self._output_said
            if last is None or last[0] != said or now - last[1] >= OUTPUT_NOTE_S:
                self._output_said = (said, now)
                self.note(said)
        return show

    # routines on pads (milestone 2) -----------------------------------------

    @staticmethod
    def _pad_key(routine: dict) -> str:
        return templatesmod.pick_key({"routine": routine.get("id"),
                                      "variation": routine.get("variation"),
                                      "params": routine.get("params") or None})

    def _compile_pad(self, preset: dict) -> None:
        """Build a routine pad's program for this rig, on the worker."""
        routine = preset.get("routine") or {}
        library = self.show_library
        if not routine or library is None:
            return
        key = self._pad_key(routine)
        if key in self._pad_programs:
            return
        pick = {"routine": routine.get("id"), "variation": routine.get("variation"),
                "params": routine.get("params")}
        routines, rigging, name = library.folder.routines, self._rigging(), preset["name"]
        gen = self._pad_gen

        def build():
            # Returned, not raised: the worker never calls `done` for a job
            # that raised, and a pad waiting on it would wait for ever.
            try:
                if pick["routine"] not in routines:
                    raise ValueError(f"routine {pick['routine']!r} is not in routines/")
                return programmod.compile(templatesmod.pick_timeline({}, pick),
                                          routines, rigging, f"preset {name!r}")
            except Exception as exc:                        # noqa: BLE001
                return exc

        def done(prog) -> None:
            if gen != self._pad_gen:
                return                  # built for a rig or load since replaced
            self._pad_programs[key] = prog
            if isinstance(prog, Exception):
                self.note(f"preset {name!r}: its routine could not be built "
                          f"({prog}); the pad applies its looks")
            elif prog.problems:
                self.note(f"preset {name!r}: {prog.problems[0]}")

        self.worker.submit(build, done, label=f"building preset {name!r}'s routine")

    def _compile_pads(self) -> None:
        self._pad_gen += 1
        self._pad_programs = {}
        for preset in self.presets:
            if preset.get("routine"):
                self._compile_pad(preset)

    def _clear_pad(self) -> None:
        self.pad = None
        self._pad_pending = None

    def _pad_frame(self, now: float) -> None:
        """A pressed routine pad lands on its downbeat (decided with the
        user): the whole picture at once, its looks and its routine, from
        beat 0 of the routine."""
        pending = self._pad_pending
        if pending is None:
            return
        beat = self.clock.beat(now)
        if beat < pending["start"] - 1e-9:
            return
        prog = self._pad_programs.get(pending["key"])
        if prog is None:
            # Not built yet (just saved, or the rig just changed): the next
            # downbeat instead, rather than starting it mid-bar.
            pending["start"] = templatesmod.next_downbeat(beat + 1e-6)
            return
        self._pad_pending = None
        self._apply_preset_looks(pending["preset"], now)
        if isinstance(prog, Exception):
            # Its routine could not be built: the looks alone, on the same
            # downbeat, and said -- each press, as each press is a choice.
            self.note(f"preset {pending['preset']['name']!r}: its routine could "
                      f"not be built ({prog}); applied its looks")
            return
        self.pad = {"name": pending["preset"]["name"],
                    "routine": pending["preset"]["routine"].get("id"),
                    "start": pending["start"], "program": prog}

    def _rigging(self):
        from . import blocks as blocksmod
        return blocksmod.Rigging(self.rig, dict(self.by_name),
                                 tuple(self.presets), self.rig.name)

    def _base_palette(self) -> dict:
        """The engine's own palette, as roles, for a timeline that has none."""
        colors = self.palette.colors
        i = self.palette.index
        primary = tuple(colors[i % len(colors)]) if colors else (1.0, 1.0, 1.0)
        secondary = (tuple(colors[(i + 1) % len(colors)]) if colors
                     else (1.0, 1.0, 1.0))
        return {"primary": primary, "secondary": secondary,
                "accent": (1.0, 1.0, 1.0)}

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
            # Who to answer, for a handler that answers later (`_deferred`).
            self._replying = (client, rid, message.get("type", "?"))
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
        client, rid, kind = getattr(self, "_replying", (None, None, "?"))

        def respond(ok: bool, data: Any = None, error: Optional[str] = None):
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

    def _track_frame(self, now: Optional[float] = None
                     ) -> Optional[transportmod.TrackSample]:
        """Match the playing track when it changes. Every frame, after the
        commands: a track can change with no command at all -- the settle
        window closing on a deck loaded while paused.

        The match is PINNED to the track: a reload of the folder mid-song does
        not touch it, and neither does a manual link. Both apply from the
        track's next play (showlibrary.Pinned)."""
        if self.show_dir is None:
            return None
        sample = self.transport.sample(self.runner.now() if now is None else now)
        pinned = self.pinned
        if pinned is None or pinned.track_seq != sample.track_seq:
            pinned = showlibrary.pin(self.show_library, sample)
            self.pinned = pinned
            self.grid_check = (tracksmod.GridCheck(pinned.grid)
                               if pinned.grid is not None else None)
            self._check_jump = sample.jump_seq
            if self.player is not None:
                # Compile now, armed or not, so arming is instant.
                self.player.compile_for(pinned)
        return sample

    def _check_grid(self, fields: dict, sample: transportmod.TrackSample,
                    now: float) -> None:
        """Feed the grid cross-check what the deck says about its own beats."""
        check = self.grid_check
        if check is None:
            return
        if sample.jump_seq != self._check_jump:
            # A loop or a hot cue: what was seen before it says nothing about
            # where the deck is now.
            check.reset()
            self._check_jump = sample.jump_seq
        if "beat_number" in fields and "track_time" in fields:
            # beat-link: count and position from ONE packet, so no estimate
            # comes into it and any transport state will do.
            check.number(fields["beat_number"], fields["track_time"], now)
        elif ("beat_in_bar" in fields and fields.get("source") == "rkbx"
              and sample.state == transportmod.PLAYING
              and sample.raw_time_s is not None):
            # rkbx_link: bar phase and position arrive as separate messages from
            # one read of rekordbox's memory, so the estimate at arrival is the
            # position the phase belongs to. Only rkbx_link's phase is a
            # continuous ramp; a bridge sending a beat number per beat is not.
            check.phase(fields["beat_in_bar"], sample.raw_time_s, now)

    def _install_library(self, library: showlibrary.Library) -> None:
        """Make a freshly loaded folder current. On the output thread, by one
        reference assignment. The playing track keeps the load it was matched
        against."""
        previous = self.show_library
        self.show_library = library
        showlibrary.apply_settings(self.transport, library)
        if self.player is not None:
            self.player.configure(library.folder.show)
            self.player.compile_idle(library)
            self.player.compile_templates(library)
            self._compile_pads()
            # The other decks' shows were built from the last load: match them
            # again against this one and build what changed.
            self.player.drop_precompiled()
            self._rematch_decks()
            said = self.outputs.configure((library.folder.show or {}).get("outputs"),
                                          self.local_outputs)
            if said is not None and previous is not None:
                self.note(said)
        if self.watcher is not None:
            # What this load read, so the watcher does not load it again.
            self.watcher.seen = library.signature
        if previous is not None:
            fresh = [e for e in library.folder.errors
                     if e not in previous.folder.errors]
            if fresh:
                more = f" (+{len(fresh) - 1} more)" if len(fresh) > 1 else ""
                self.note(f"show folder: {fresh[0]}{more}")

    def _library_changed(self) -> None:
        """The watcher saw the folder change. On the watcher's thread: hand the
        load to the worker and the install to the output thread."""
        self.reload_library()

    def reload_library(self) -> None:
        if self.show_dir is None:
            return
        root = self.show_dir
        self.worker.submit(
            lambda: showlibrary.load(root, previous=self.show_library),
            done=self._install_library, label="reloading the show folder")

    def _advance_due_cue(self) -> None:
        """Fire a cue's own hold, the same way an operator's GO would.

        `CueList.due()` has existed since the cue list was ported, but nothing
        ever called it -- a cue authored with a hold sat there until someone
        pressed GO by hand, silently contradicting the "auto after N" the UI
        shows next to it. One check at the top of the frame, same place a
        queued GO gets applied, is enough to make the hold real.
        """
        if self.cues is None or not self.cues.due(self.ctx.beat):
            return
        cue = self.cues.go(self.ctx.beat)
        if cue is not None:
            # Without grabbing. Taking a cue by hand is an operator overriding
            # the timeline, and main's rule is that the grab sticks until they
            # release it. A hold running out is nobody's decision: grabbing on
            # it would take all three lanes from a running timeline, and keep
            # them, with no one having touched anything. The slots still
            # change underneath, so the cue is there in any lane already held.
            self.take_cue(cue, grab=False)

    def _attach_overrides(self, show: statemod.Show) -> None:
        show.master = 0.0 if self.blackout else self.master
        show.overrides = self.override_layers
        self._run_modulators()

    def _run_modulators(self) -> None:
        """Resolve every modulator onto the context, once per frame.

        Called from `on_show`, which the runner invokes at the END of
        `sync_clock` -- so musical position and the per-slot phases are already
        current. Doing it in `before_frame` instead would read last frame's bar
        and put every LFO one frame behind the music for no reason.

        Writes a MACRO straight onto the field `move_layer` already reads, and a
        routine's parameter into `ctx.live_params` where the generator layers
        pick it up. Two destinations because they are genuinely two things: a
        macro is one number for the whole movement slot, a routine's radius
        belongs to that routine.

        Nothing here can reach past the safety taper. These are parameters, and
        `apply_safety` runs after the whole stack unconditionally -- see
        `test_safety.py` section 8, which sweeps modulated values for exactly
        this reason.
        """
        if not self.modulators:
            # The overwhelmingly common case. Clearing rather than skipping,
            # because a modulator removed mid-show must stop driving the value
            # rather than leaving its last frame frozen on stage.
            if self.ctx.live_params:
                self.ctx.live_params = {}
            return
        macros, looks = self.modulators.resolve(self.ctx.bar, self.ctx.energy)
        self.ctx.live_params = looks
        # Named from the declarations, so a renamed macro cannot leave a
        # modulator that resolves and lands nowhere.
        size, spread = parammod.SIZE.name, parammod.SPREAD.name
        bearing_key, elev_key = parammod.CENTER_BEARING.name, parammod.CENTER_ELEV.name
        if size in macros:
            self.ctx.move_size = macros[size]
        if spread in macros:
            self.ctx.move_spread = macros[spread]
        if bearing_key in macros or elev_key in macros:
            bearing, elev = self.ctx.move_center
            self.ctx.move_center = (macros.get(bearing_key, bearing),
                                    macros.get(elev_key, elev))

    def _publish(self, states: dict[int, statemod.FixtureState]) -> None:
        # One reference assignment. Atomic under the GIL, so the broadcast
        # thread never sees a half-built dict, and no lock touches the hot path.
        self.latest_states = states

    def note(self, text: str) -> None:
        self.notices.append(text)
        del self.notices[:-20]

    # -- commands ----------------------------------------------------------

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
        kind = message.get("type")
        now = self.ctx.time if at is None else at
        handler = getattr(self, f"_cmd_{kind}", None)
        if handler is None:
            raise ValueError(f"unknown command {kind!r}")
        # A client with no token may watch but not touch. Checked here rather
        # than at the socket, so there is exactly one place a command can enter
        # the show and exactly one place that decides whether it may.
        need = TIER.get(kind, "operate")
        if client is not None and not client.may(need):
            raise ValueError(
                f"{kind!r} needs {need} access and this client has "
                f"{client.tier}. Open the URL the engine printed, including "
                f"its ?token=, or restart with --no-token")
        result = handler(message, now)
        self.rev += 1
        if client is not None and kind != "hello":
            client.last_action = describe(message)
            client.last_action_at = time.time()
        # Whatever the handler returned rides back in the reply, for the few
        # commands whose answer is not visible in the broadcast state -- a
        # validation result, say. Most return None and the reply is just ok.
        return result

    # slots ----------------------------------------------------------------

    def entry(self, name: Optional[str]) -> Optional[libmod.LibraryEntry]:
        """A look by name, with whatever the operator has dialled into it.

        Tuning is keyed by look NAME rather than by (slot, group), because a
        look is addressed by name everywhere else -- cues, presets, the picker,
        auto mode's set list -- and because the movement slot has no per-group
        selection to key on. It also means a tweak survives switching away and
        back, which is what anyone would expect of a knob they just set.

        Applied HERE, at the one point every consumer resolves a name, so the
        composed show, the cue list and the set list all see the same tuned
        entry without any of them knowing tuning exists.
        """
        base = self.by_name.get(name) if name else None
        if base is None:
            return None
        overrides = self.look_params.get(base.name)
        if not overrides:
            return base
        return replace(base, args={**base.args, **overrides})

    def _set_look_params(self, name: str, values: Optional[dict]) -> None:
        """Replace one look's tuning; None or {} puts it back to authored.

        REBINDS the dict rather than editing it, the convention every
        collection the 10 Hz snapshot walks follows (`self.flashing` says why):
        that thread iterates `look_params` while a command lands on this one,
        and a dict changing size under it is a "snapshot failed" on every phone.
        """
        rest = {k: v for k, v in self.look_params.items() if k != name}
        self.look_params = {**rest, name: dict(values)} if values else rest

    def _recompose(self, fade_beats: Optional[float] = None) -> None:
        """Rebuild the Show from the three slots.

        The director keeps owning WHICH movement look is up (that is what auto
        look changes change), but it no longer owns the whole Show -- it
        delegates back here so the colour and level slots survive a look change.

        `fade_beats` crossfades into the result instead of cutting. Only cues
        pass it today: a look picked by hand is an operator watching the rig and
        wanting it now, while a cue is a rehearsed transition that should look
        like one.
        """
        def compose(look, color: tuple[float, float, float]):
            movement = self.entry(look.name) if look is not None else None
            return libmod.compose(
                movement if movement is not None and movement.is_movement else None,
                self.slot_entries("color"), self.slot_entries("level"), color,
                # Resolved here rather than held as entries, so a stacked look
                # picks up its own tuning and modulation exactly as the base
                # route does -- `entry()` is the one place that applies it.
                #
                # The base route is left out of the stack. `movement_add`
                # refuses to stack the current base, but the base can change
                # AFTER -- picked by hand, by a cue, by auto mode -- and a look
                # that is both would run twice at double excursion. Filtered
                # here, where every route to a new base passes, rather than
                # unstacked: when the base moves on, the stacked look returns.
                movement_extra=[e for e in
                                (self.entry(n) for n in self.movement_extra
                                 if look is None or n != look.name)
                                if e is not None and e.is_movement],
                palette_roles=self.palette_roles(color))
        self.director.compose = compose
        show = self.director.rebuild()
        if self.player is not None and self.player.engaged:
            # The timeline is on stage: the rebuilt show is what the grabbed
            # lanes read, picked up next frame. Handing it to the runner here
            # would swap the timeline out for a frame and cut straight back.
            return
        if fade_beats is not None:
            # `is not None`, not truthiness: a cue with fade 0 is a CUT, and it
            # has to cancel a fade already in flight rather than letting the
            # previous one carry on underneath it. Taking the Drop mid-fade and
            # watching it dissolve is exactly the failure.
            #
            # The runner picks the director's show up on the next frame anyway;
            # handing it over here is what gives it something to fade FROM,
            # since by then the old one is gone.
            self.runner.set_show(show, fade_beats=fade_beats)

    def _cmd_select_look(self, m: dict, now: float) -> None:
        """Select a look INTO ITS OWN SLOT, worked out from what it sets.

        The caller does not say which slot; the library already knows, because
        the port split every scene by which channels it touched. A `mixed` entry
        fills movement and colour together, since it genuinely states both --
        and either stays independently changeable afterwards.
        """
        name = m["name"]
        entry = self.by_name.get(name)
        if entry is None:
            raise KeyError(f"no look named {name!r}")
        self._clear_pad()
        slot = m.get("slot") or entry.slot
        if slot not in ("movement", "color", "level"):
            raise ValueError(f"unknown slot {slot!r}")
        if slot == "movement":
            self.director.select(name, hold=m.get("hold", True))
        else:
            # Fills its slot for every group it writes, and only those -- so a
            # pinspot colour replaces the pinspot colour and leaves the movers
            # alone. A look covering the whole rig naturally replaces both.
            for group in (entry.groups or ("movers",)):
                self.slots[slot][group] = name
        if entry.kind == "mixed":
            for group in (entry.groups or ("movers",)):
                self.slots["color"][group] = name
        self._grab({slot, "color"} if entry.kind == "mixed" else {slot})
        self._recompose()

    def _cmd_clear_slot(self, m: dict, now: float) -> None:
        slot = m["slot"]
        if slot not in ("movement", "color", "level"):
            raise ValueError(f"unknown slot {slot!r}")
        self._clear_pad()
        if slot == "movement":
            raise ValueError(
                "the movement slot cannot be empty -- with nothing aiming the "
                "heads they would hold wherever the last look left them. Pick "
                "another position instead.")
        group = m.get("group")
        if group is None:
            self.slots[slot].clear()
        else:
            self.slots[slot].pop(group, None)
        self._grab({slot})
        self._recompose()

    def _cmd_look_params(self, m: dict, now: float) -> None:
        """Turn one routine's own knobs, live.

        This is the thing the ported library cannot have. A stored look is a
        table of DMX -- there is nothing in "Ball Wave" to turn, which is why
        103 poses and 26 paths accumulated as separate entries. A routine names
        a generator, so its radius, its cycle length and its flattening are all
        numbers, and one entry covers what a shelf of stored chases used to.

        STRICT about unknown keys, unlike loading a file. A typo'd key arriving
        from the console means the UI and the engine disagree about what this
        routine has, and silently dropping it produces a slider that appears to
        work and changes nothing -- the most confusing failure a console has. A
        stale key in a hand-edited `parametric_looks.json` is the opposite case and is
        dropped, so the room still lights.

        Out-of-range values are CLAMPED rather than refused, because one bad
        number must not discard the three good ones sent alongside it. Nothing
        here can reach past the safety taper: these are parameters, and
        `apply_safety` runs after the whole stack regardless.
        """
        name = m["name"]
        base = self.by_name.get(name)
        if base is None:
            raise KeyError(f"no look named {name!r}")
        if not base.is_parametric:
            raise ValueError(
                f"{name!r} is a ported look -- a stored table of DMX with no "
                f"arguments to turn. Parametric looks come from "
                f"parametric_looks.json and are the ones built from a block")

        if m.get("reset"):
            self._set_look_params(name, None)
            self._recompose()
            self.note(f"{name!r} back to its authored values")
            return

        values = m.get("values") or {}
        if not values:
            raise ValueError("look_params needs values, or reset: true")
        declared = blocksmod.PARAMS[base.block or ""]
        resolved = parammod.resolve(declared, values, strict=True,
                                    reach=self.reach)
        # Only the keys actually sent are recorded. Copying `resolved` whole
        # would bake every other parameter's current default into the override
        # and detach the routine from its own authored numbers.
        current = dict(self.look_params.get(name, {}))
        for key in values:
            current[key] = resolved[key]
        # A colour or a list is passed through by `Param` -- whether "@accent"
        # or "#ff0080" means anything is a question for the rig -- so ask the
        # check the block itself would make, before storing rather than after.
        # Refused whole, unlike a clamped number: there is no nearest colour to
        # "nonsense", and storing it builds an empty block that lights nothing.
        problems = libmod.arg_problems(
            replace(base, args={**base.args, **current}), self.rig)
        if problems:
            raise ValueError(f"{name!r}: {'; '.join(problems)}")
        self._set_look_params(name, current)
        self._recompose()

    def looks_named(self, block: dict) -> list[str]:
        """Every look a preset or cue names, across its three slots."""
        out: list[str] = []
        for slot in ("movement", "color", "level"):
            for name in (block.get(slot) or {}).values():
                if name and name not in out:
                    out.append(name)
        return out

    def capture_look_params(self, named: Sequence[str]) -> Optional[dict]:
        """The tuning to store with a preset, for the looks it names.

        Returns None when the preset names no tunable routine at all, so a
        preset over ported looks carries no empty block.

        Note this records an EMPTY dict for a routine sitting at its authored
        values, and that is the deliberate difference from how `rates` is
        stored. A rate is a performance ride the operator keeps their hand on,
        so a preset that always restored one would undo a change made after it
        was saved -- hence "absent means leave alone" there. A routine's radius
        is not a ride, it is part of the picture the preset exists to get back
        to. Recording only the non-default ones would mean a preset saved at
        authored values silently kept whatever was dialled in later.
        """
        tuned: dict[str, dict] = {}
        for name in named:
            entry = self.by_name.get(name)
            if entry is not None and entry.is_parametric:
                tuned[name] = dict(self.look_params.get(name, {}))
        return tuned or None

    def apply_look_params(self, params: Optional[dict], named: Sequence[str],
                          where: str) -> None:
        """Restore per-routine tuning from a preset or a cue.

        `params` None leaves every routine alone -- what a cue hand-written
        before this existed means, and what one that simply has no opinion about
        tuning means. A dict is exhaustive over the looks the cue NAMES: a
        routine it names but does not list goes back to its authored values, so
        "take this cue" cannot leave a radius from three cues ago on stage.

        Values are clamped and unknown keys DROPPED rather than refused. This is
        file-sourced, like `parametric_looks.json` and unlike a command from the console:
        a preset saved against a slightly different engine should put back what
        it still can and say what it could not, rather than refusing to load
        mid-set.
        """
        if params is None:
            return
        dropped: list[str] = []
        for name in named:
            entry = self.by_name.get(name)
            if entry is None or not entry.is_parametric:
                continue
            wanted = params.get(name) or {}
            declared = blocksmod.PARAMS.get(entry.block or "")
            if not declared:
                continue
            kept: dict = {}
            for key, value in wanted.items():
                param = parammod.find(declared, key)
                if param is None:
                    dropped.append(f"{name}.{key}")
                    continue
                try:
                    value = parammod.clamp(param, value, self.reach)
                except parammod.ParamError:
                    dropped.append(f"{name}.{key}")
                    continue
                # One key at a time against the authored look, so a colour this
                # rig cannot resolve is dropped on its own and the rest of the
                # tuning still lands -- see `_cmd_look_params` for why it is
                # checked at all.
                if libmod.arg_problems(
                        replace(entry, args={**entry.args, key: value}), self.rig):
                    dropped.append(f"{name}.{key}")
                    continue
                kept[key] = value
            self._set_look_params(name, kept)
        if dropped:
            self.note(f"{where}: dropped {', '.join(dropped[:4])}"
                      f"{' and more' if len(dropped) > 4 else ''} -- "
                      f"not a parameter, or not a value this rig can use")

    def target_param(self, look: Optional[str], param: str) -> parammod.Param:
        """The `Param` a modulator is aimed at, whichever kind of target it is.

        One lookup for both, so a modulator is bounded by the same declaration
        the operator's own slider is bounded by. A modulator that could sweep
        outside its target's range would be a way to reach a value no finger
        could have dragged to -- which is precisely what the ranges are for.
        """
        if not look:
            found = parammod.find(parammod.MACROS, param)
            if found is None:
                raise ValueError(
                    f"no shape macro {param!r} -- one of "
                    f"{', '.join(p.name for p in parammod.MACROS)}")
            return found
        entry = self.by_name.get(look)
        if entry is None:
            raise KeyError(f"no look named {look!r}")
        if not entry.is_parametric:
            raise ValueError(
                f"{look!r} is a ported look -- a stored table of DMX with no "
                f"arguments to modulate")
        declared = blocksmod.PARAMS.get(entry.block or "", ())
        found = parammod.find(declared, param)
        if found is None:
            raise ValueError(
                f"{look!r} has no argument {param!r} -- its block takes "
                f"{', '.join(p.name for p in declared)}")
        return found

    def _cmd_modulate(self, m: dict, now: float) -> None:
        """Bind a parameter to a musical waveform.

        The single biggest thing the old console could not say. A stored scene
        has no parameters, so "the same look, slowly widening" was a chase of
        twenty scenes that stepped between them -- visibly. Here it is one
        routine and one modulator.

        Binding the same target twice REPLACES rather than stacking. Two LFOs
        fighting over one number is not something anyone means to ask for, and
        which one won would depend on dict order.
        """
        param = m["param"]
        look = m.get("look")
        target = self.target_param(look, param)
        self.modulators.add(modmod.build(m, target, self.reach))

    def _cmd_modulate_clear(self, m: dict, now: float) -> None:
        """Stop one modulator, or all of them.

        A cleared macro modulator leaves the macro wherever its last frame put
        it, deliberately: that is where the picture currently is, and snapping
        back to the authored value on release would be a jump nobody asked for.
        `macro reset` is the way back to identity, and it already exists.
        """
        if m.get("all"):
            self.modulators.clear()
            self.ctx.live_params = {}
            self.note("all modulation stopped")
            return
        param = m["param"]
        look = m.get("look")
        if not self.modulators.remove(look, param):
            raise KeyError(
                f"nothing is modulating {param!r}"
                + (f" on {look!r}" if look else ""))
        # A routine's parameter falls straight back to what the routine was
        # composed with, since `live_params` is rebuilt from the rack each
        # frame -- so releasing one does not need to restore anything by hand.

    # A cap, not a limit the maths needs. Offsets add, so ten would compose
    # fine and be unreadable -- and each one is another layer walked per frame.
    # Three is enough for "a slow route, a fast wobble, and a drift".
    MAX_STACK = 3

    def _cmd_movement_add(self, m: dict, now: float) -> None:
        """Stack another movement routine on top of the one that is up.

        Free, because every movement layer ADDS a degree offset -- only the base
        pose layer assigns. A slow orbit under a fast small jitter is two layers
        and nothing else, and it is a shape the old console could not express:
        it could store the sum of two moves as a third scene, but only at one
        relative phase, and that phase was baked in.

        The base route is NOT eligible. Stacking a look on itself doubles its
        excursion, which is what Size is for, and it would read as a routine
        that mysteriously got twice as big.
        """
        name = m["name"]
        entry = self.by_name.get(name)
        if entry is None:
            raise KeyError(f"no look named {name!r}")
        if not entry.is_movement:
            raise ValueError(
                f"{name!r} fills the {entry.slot} slot -- only movement looks "
                f"stack, because only movement offsets add")
        current = self.setlist.current()
        if current is not None and current.name == name:
            raise ValueError(
                f"{name!r} is already the base route -- stacking it on itself "
                f"would just double its excursion, which is what Size does")
        if name in self.movement_extra:
            raise ValueError(f"{name!r} is already stacked")
        if len(self.movement_extra) >= self.MAX_STACK:
            raise ValueError(
                f"{self.MAX_STACK} stacked routines is the limit -- past that "
                f"nobody can tell which one is doing what")
        # Rebound, never appended to -- the snapshot thread lists it.
        self.movement_extra = [*self.movement_extra, name]
        self._recompose()

    def _cmd_movement_remove(self, m: dict, now: float) -> None:
        if m.get("all"):
            self.movement_extra = []
            self._recompose()
            return
        name = m["name"]
        if name not in self.movement_extra:
            raise KeyError(f"{name!r} is not stacked")
        self.movement_extra = [n for n in self.movement_extra if n != name]
        self._recompose()

    def _cmd_vary(self, m: dict, now: float) -> None:
        """Nudge a routine's numbers around, reproducibly.

        Every parameter carries a declared range, which is what makes this
        possible at all: "somewhere else, but still sensible" needs to know what
        sensible is. `amount` is the fraction of each range to move within, so
        0.1 is a nudge and 1.0 can reach anywhere the slider goes.

        SEEDED, never the module RNG. `engine/` had no `random` import before
        this and the reason is reproducibility -- previz, the parity sweep and
        the port all depend on the same inputs giving the same frame. A seeded
        `random.Random` keeps that: the seed is published, so a variation worth
        keeping can be written down and reproduced exactly.

        A `musical` parameter (the cycle length, `bars`) is deliberately left
        alone -- it is how the routine sits against the track, while everything
        else is shape. Rolling a new cycle length is how you turn a 16-bar
        swell into a 3.75-bar one that fits nothing.

        MERGED over the operator's other settings, not instead of them. Vary
        only rolls shape numbers, so a direction or a cycle length dialled in by
        hand has nothing to do with it and must survive the press.
        """
        name = m["name"]
        entry = self.by_name.get(name)
        if entry is None:
            raise KeyError(f"no look named {name!r}")
        if not entry.is_parametric:
            raise ValueError(
                f"{name!r} is a ported look -- it has no parameters to vary")
        amount = float(m.get("amount", 0.3))
        if not math.isfinite(amount):
            raise ValueError(f"vary amount must be a finite number, got {amount}")
        amount = max(0.0, min(1.0, amount))
        if m.get("seed") is not None:
            seed = int(m["seed"])
        else:
            self.vary_seed += 1
            seed = self.vary_seed
        # A string seed, not a tuple: `random.Random` refuses tuples on 3.11+.
        # Keyed by NAME as well as seed so the same seed gives each look its
        # own variation rather than moving them all the same way.
        rng = random.Random(f"{name}:{seed}")

        # From the look's AUTHORED values, not from whatever is currently
        # dialled in. Varying the varied compounds: press it four times and you
        # have random-walked to an extreme, and no seed describes where you are.
        base = libmod.resolve_args(entry)
        varied: dict = {}
        for param in blocksmod.PARAMS.get(entry.block or "", ()):
            if param.musical or param.kind not in ("number", "integer"):
                continue
            # An argument with no fixed default (a fan's sweep is half its
            # width unless given) has nothing to perturb FROM.
            lo, hi = parammod.bounds(param, self.reach)
            if lo is None or hi is None or base.get(param.name) is None:
                continue
            span = (hi - lo) * amount
            varied[param.name] = parammod.clamp(
                param, base[param.name] + rng.uniform(-0.5, 0.5) * span,
                self.reach)
        if not varied:
            raise ValueError(f"{name!r} has nothing that can be varied")
        # Rebound, never mutated: the 10 Hz snapshot walks this dict from
        # another thread (see `_set_look_params`).
        kept = {k: v for k, v in self.look_params.get(name, {}).items()
                if k not in varied}
        self._set_look_params(name, {**kept, **varied})
        self._recompose()
        self.note(f"varied {name!r} at {amount:.2f} from seed {seed}")

    def _cmd_release(self, m: dict, now: float) -> None:
        self.director.release()

    def _cmd_next_look(self, m: dict, now: float) -> None:
        self._clear_pad()
        self.setlist.advance()
        self._grab({"movement"})
        self._recompose()

    # presets ---------------------------------------------------------------

    def _cmd_preset_save(self, m: dict, now: float) -> None:
        """Snapshot all three slots plus the tempo feel under one name.

        A preset is the thing a slot-based console loses: with colour, movement
        and level independent you can build a picture in three taps, and then
        have no way to get back to it. Saved to the event so it survives a
        restart -- a preset that lives in memory is a preset you rebuild.

        Saving over an existing name KEEPS ITS CELL. Re-recording a preset is
        the most common thing anyone does with one, and having it jump to the
        end of the grid every time would destroy the only thing a fixed cell is
        for.
        """
        name = str(m["name"]).strip()[:40]
        if not name:
            raise ValueError("a preset needs a name")
        previous = next((p for p in self.presets if p["name"] == name), None)
        others = [p for p in self.presets if p["name"] != name]
        where = _cell_request(m)
        if where is None and previous is not None:
            where = (previous["bank"], previous["cell"])
        if where is None:
            where = free_cell(others)
        elif any(p["bank"] == where[0] and p["cell"] == where[1]
                 for p in others):
            raise ValueError(f"bank {where[0]} cell {where[1]} is already taken")
        tags = m.get("tags")
        # Absent keeps the pad's routine (re-recording its looks is the
        # common case); null clears it, making it a pad of looks again.
        routine = (self._preset_routine(m["routine"]) if "routine" in m
                   else previous.get("routine") if previous is not None else None)
        captured = self.capture_look_params(self.looks_named(self.selection))
        # Built whole and rebound once. Appending to the live list and then
        # sorting it in place is what let the broadcast thread serialise an
        # EMPTY preset list -- CPython empties a list for the duration of
        # `sort()`, so a console mid-save could show "Presets - 0".
        self.presets = sorted(others + [{
            "name": name, **self.selection,
            "speed": round(self.clock.speed, 3),
            "master": round(self.master, 3),
            # Stored only when something has been dialled off 1x. A preset that
            # carried `{1, 1, 1}` would silently RESET rates the operator set
            # after saving it, which is the same trap as a cue that always
            # restores a macro -- absent has to mean "leave it alone".
            **({"rates": self.director.phases.status()}
               if self.director.phases.changed else {}),
            # Per-routine tuning for the looks this preset names -- see
            # `capture_look_params` for why this one is NOT stored only when it
            # differs from the authored values, unlike `rates` above.
            **({"params": captured} if captured is not None else {}),
            "bank": where[0], "cell": where[1],
            "tags": [str(t) for t in tags] if tags is not None
                    else list(previous.get("tags", [])) if previous else [],
            **({"routine": routine} if routine else {}),
        }], key=_at)
        save_presets(self.event_dir, self.presets)
        if routine:
            self._compile_pad(next(p for p in self.presets if p["name"] == name))
        self.note(f"saved preset {name!r} to {where[0]}.{where[1] + 1}"
                  + (f" with routine {routine['id']!r}" if routine else ""))

    def _preset_routine(self, raw) -> Optional[dict]:
        """A routine for a pad: {id, variation?, params?}, checked against the
        show folder. None for a pad of looks only."""
        if raw is None:
            return None
        library = self.show_library
        if library is None:
            raise ValueError("a routine pad needs a show folder -- start the "
                             "engine with --show-dir")
        if not isinstance(raw, dict) or not isinstance(raw.get("id"), str):
            raise ValueError('routine must be {"id": "<routine id>", ...}')
        doc = library.folder.routines.get(raw["id"])
        if doc is None:
            raise ValueError(f"there is no routine {raw['id']!r} in routines/")
        out: dict = {"id": raw["id"]}
        variation = raw.get("variation")
        if variation:
            if variation not in (doc.get("variations") or {}):
                raise ValueError(f"routine {raw['id']!r} has no variation "
                                 f"{variation!r}")
            out["variation"] = variation
        params = raw.get("params")
        if params:
            if not isinstance(params, dict):
                raise ValueError("routine params must be an object")
            out["params"] = dict(params)
        return out

    def _cmd_preset_move(self, m: dict, now: float) -> None:
        """Put a preset on a different pad, SWAPPING with whatever is there.

        Swap rather than refuse, because the thing anyone actually wants from
        this is to reorder a bank, and refusing on collision would mean shuffling
        via an empty cell to get two presets to trade places.
        """
        name = m["name"]
        preset = next((p for p in self.presets if p["name"] == name), None)
        if preset is None:
            raise KeyError(f"no preset named {name!r}")
        where = _cell_request(m)
        if where is None:
            raise ValueError("preset_move needs a bank and a cell")
        occupant = next((p for p in self.presets
                         if p["bank"] == where[0] and p["cell"] == where[1]), None)
        if occupant is preset:
            return
        # Replaced, not edited in place: the snapshot thread walks these dicts
        # while this runs, and editing two of them in sequence would let it
        # serialise a moment where both claim the same pad.
        moved = {**preset, "bank": where[0], "cell": where[1]}
        swapped = (None if occupant is None
                   else {**occupant, "bank": preset["bank"],
                         "cell": preset["cell"]})
        self.presets = sorted(
            [moved if p is preset else swapped if p is occupant else p
             for p in self.presets], key=_at)
        save_presets(self.event_dir, self.presets)
        self.note(f"moved preset {name!r} to {where[0]}.{where[1] + 1}")

    def _cmd_preset_tag(self, m: dict, now: float) -> None:
        name = m["name"]
        preset = next((p for p in self.presets if p["name"] == name), None)
        if preset is None:
            raise KeyError(f"no preset named {name!r}")
        tags = [str(t).strip()[:24] for t in m.get("tags", []) if str(t).strip()]
        self.presets = [{**p, "tags": tags} if p is preset else p
                        for p in self.presets]
        save_presets(self.event_dir, self.presets)

    def _cmd_preset_apply(self, m: dict, now: float) -> None:
        name = m["name"]
        preset = next((p for p in self.presets if p["name"] == name), None)
        if preset is None:
            raise KeyError(f"no preset named {name!r}")
        self._clear_pad()
        routine = preset.get("routine")
        if routine and self.show_library is not None:
            # On the next downbeat (decided with the user): the whole picture
            # lands then, so the routine's first bar is the music's.
            key = self._pad_key(routine)
            if key not in self._pad_programs:
                self._compile_pad(preset)
            self._pad_pending = {
                "preset": preset, "key": key,
                "start": templatesmod.next_downbeat(self.clock.beat(now))}
            return
        if routine:
            self.note(f"preset {name!r}: its routine needs a show folder; "
                      f"applying its looks")
        self._apply_preset_looks(preset, now)

    def _apply_preset_looks(self, preset: dict, now: float) -> None:
        name = preset["name"]
        # A preset naming a look that has since been re-ported away applies the
        # rest rather than failing whole -- a preset is a shortcut, and half a
        # shortcut beats an error message mid-set.
        missing = []
        for slot in ("color", "level"):
            self.slots[slot] = {}
            for group, value in (preset.get(slot) or {}).items():
                if value in self.by_name:
                    self.slots[slot][group] = value
                elif value:
                    missing.append(value)
        movement = next(iter((preset.get("movement") or {}).values()), None)
        if movement in self.by_name:
            self.director.select(movement, hold=True)
        elif movement:
            missing.append(movement)
        if missing:
            self.note(f"preset {name!r}: {', '.join(missing)} no longer exist(s)")
        self.apply_look_params(preset.get("params"), self.looks_named(preset),
                               f"preset {name!r}")
        if preset.get("rates"):
            self.apply_rates(preset["rates"])
        if preset.get("speed"):
            self.clock.set_speed(float(preset["speed"]), now)
        if preset.get("master") is not None:
            self.master = max(0.0, min(1.0, float(preset["master"])))
        self.director.held = True
        self._grab(statemod.SLOTS)
        self._recompose()

    def _cmd_preset_delete(self, m: dict, now: float) -> None:
        name = m["name"]
        before = len(self.presets)
        self.presets = [p for p in self.presets if p["name"] != name]
        if len(self.presets) == before:
            raise KeyError(f"no preset named {name!r}")
        save_presets(self.event_dir, self.presets)
        self.note(f"deleted preset {name!r}")

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
        # `white_overrides` is REBOUND, never edited: the snapshot thread
        # publishes it, and a dict changing size under that copy fails the
        # broadcast. (`color_overrides` is not published.)
        whites = {k: v for k, v in self.white_overrides.items() if k != target}
        if m.get("clear"):
            self.color_overrides.pop(target, None)
            self.white_overrides = whites
        else:
            self.color_overrides[target] = color
            # "white" absent (the quick palette, most colour picks) leaves any
            # white already overridden for this target alone -- a palette tap
            # is a statement about RGB only, not an instruction to forget the
            # white the operator dialed in a moment ago. `white: null` (the
            # picker's own clear) is what actually resets it.
            if "white" in m:
                white = m.get("white")
                if white is None:
                    self.white_overrides = whites
                else:
                    self.white_overrides = {
                        **whites, target: max(0.0, min(1.0, float(white)))}
        self._rebuild_overrides()

    def _cmd_palette_select(self, m: dict, now: float) -> None:
        self.palette.index = int(m["index"]) % len(self.palette.colors)
        self.director.rebuild()

    def _cmd_level(self, m: dict, now: float) -> None:
        """Dim one fixture, one group, or everything, by hand.

        The counterpart to the colour picker, and the same shape: an override
        layer keyed by target, so a level trimmed by hand survives an auto-mode
        look change. Distinct from the Bright slot, which holds a PATTERN from
        the library -- this is the operator saying "that head is too hot right
        now", which no stored look can anticipate.

        A multiplier, not an absolute. It sits after the level pattern and
        before the master, so pattern x hand-trim x master x safety all compose
        in the order an operator would expect: the pattern keeps running, the
        trim rides on top of it, and the master still takes everything down.
        """
        target = m.get("target", "all")
        if m.get("clear"):
            self.level_overrides.pop(target, None)
        else:
            self.level_overrides[target] = max(0.0, min(1.0, float(m["value"])))
        self._rebuild_overrides()

    def _cmd_flash(self, m: dict, now: float) -> None:
        """Momentary bump on a group, a fixture, or everything.

        Held rather than latched: `on: false` on release. A latching flash is a
        level, and there is already a level.

        It sets intensity rather than multiplying it, so it works from a group
        that is trimmed to zero -- which is the case it exists for. The safety
        taper still runs after it, so a bump cannot put a beam anywhere a look
        could not.
        """
        target = m.get("target", "all")
        # Rebound, not mutated. `snapshot()` runs on the broadcast thread and
        # reads this set while commands run on the output thread; a set that
        # changes size mid-iteration raises, and the broadcast that was building
        # the frame is lost. Rebinding is atomic, so a reader sees the set
        # before or the set after and never one being edited. Same reasoning
        # everywhere else this file hands a live container to the snapshot.
        if m.get("on", True):
            self.flashing = self.flashing | {target}
        else:
            self.flashing = self.flashing - {target}
        self._rebuild_overrides()

    def _cmd_flash_clear(self, m: dict, now: float) -> None:
        """Release every bump.

        A pointer that leaves the button while down, or a phone that locks
        mid-press, never sends the release -- and a flash stuck on is a group
        stuck at full. The UI sends this on reconnect for that reason.
        """
        self.flashing = set()
        self._rebuild_overrides()

    def _rebuild_overrides(self) -> None:
        layers: list[statemod.Layer] = []
        for target, color in self.color_overrides.items():
            tags = None if target == "all" else (target,)
            layers.append(statemod.color_layer(
                color, tags=tags, white=self.white_overrides.get(target)))
        for target, level in self.level_overrides.items():
            tags = None if target == "all" else (target,)
            layers.append(statemod.intensity_layer(
                lambda ctx, fixture, level=level: level, tags=tags))
        # Flash last, so a bump outranks a hand trim that is dimming the same
        # group -- pressing flash on something you just pulled down should make
        # it bright, not multiply two numbers together and produce nothing.
        # Still under the master and under safety, like every other override.
        for target in self.flashing:
            tags = None if target == "all" else (target,)
            layers.append(statemod.on_layer(1.0, tags=tags))
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
        """Record where a head is pointing right now, against a known target.

        Refuses if the head is not being jogged. A capture IS the current
        pan/tilt, and the jog dict is the only place that number exists -- so
        defaulting it silently recorded (0, 0), which the solver then fitted
        into a calibration with a large residual and no other complaint. Since
        the jog dict is keyed per fixture, the reachable case was simply
        selecting a second head and capturing before jogging it.
        """
        name = m["fixture"]
        if name not in self.jog:
            if "pan" not in m or "tilt" not in m:
                raise ValueError(
                    f"{name} is not jogging -- aim it first, then capture. "
                    f"A capture records where the head IS, so there is nothing "
                    f"to record until it has been pointed somewhere.")
            pan, tilt = int(m["pan"]), int(m["tilt"])
        else:
            pan, tilt = self.jog[name]
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
        if m.get("write"):
            # A bad fit must not be written just because someone pressed the
            # write button. The residual is the solver telling you the captures
            # disagree; overwriting a good calibration with one that does not
            # fit is worse than not solving at all, and it happens at load-in
            # when nobody is reading the notices.
            bad = [s for s in results if s.residual_deg > MAX_WRITE_RESIDUAL_DEG]
            if bad:
                raise ValueError(
                    "refusing to write: "
                    + "; ".join(f"{s.head_name} residual {s.residual_deg:.1f} deg"
                                for s in bad)
                    + f" (limit {MAX_WRITE_RESIDUAL_DEG:g}). Re-aim and re-capture; "
                      "a capture taken before the head was jogged is the usual cause.")
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
        # Same trap as save_presets: this rewrites the file, so every key that
        # should survive a solve has to be named here.
        payload = {"$schema": "../../schemas/calibration.schema.json",
                   "measured": time.strftime("%Y-%m-%d"),
                   "mount_mode": self.rig.geometry.mount_mode,
                   "source": "solved from the web UI", "heads": heads}
        configmod.write_json_atomic(path, payload)
        calibmod.save_snapshot(self.event_dir, payload, note="after web solve")
        self.note(f"wrote {path.name}; restart the engine to load it")

    def _cmd_drift(self, m: dict, now: float) -> None:
        """Compare fresh ball readings against the stored calibration.

        With no `readings`, the readings ARE the jog positions: the operator
        jogs every head onto the ball from the Setup tab and presses Check, and
        the jog dict is the only place those numbers exist. The console holds
        one pan/tilt pair for whichever head is selected, not one per head, so
        it cannot send them -- and a list it remembered would be wrong after a
        second phone jogged, a reload, or Stop all.

        Refuses, naming them, if any head is not jogging. Same guard as
        capture, for the same reason: a head nobody aimed has no reading.
        Checking it against (0, 0) reports it MOVED by tens of degrees, and
        skipping it reports a go for a head nobody looked at. `readings` (one
        [pan, tilt] per head in rig order, the CLI's shape) must have exactly
        one per head for the second reason; it used to drop the extras' heads.
        """
        if self.rig.geometry is None:
            raise ValueError("no geometry to check drift against")
        heads = self.rig.geometry.heads
        if "readings" in m:
            readings = [(int(r[0]), int(r[1])) for r in m["readings"]]
            if len(readings) != len(heads):
                raise ValueError(
                    f"expected {len(heads)} readings, one per head in rig "
                    f"order, got {len(readings)}")
        else:
            idle = [h.name for h in heads if h.name not in self.jog]
            if idle:
                raise ValueError(
                    f"not jogging: {', '.join(idle)} -- jog every head onto the "
                    f"ball, then check. The check compares where each head IS "
                    f"against the calibration, and a head that has not been "
                    f"aimed is not anywhere yet.")
            readings = [self.jog[h.name] for h in heads]
        drifts = [calibmod.drift_for_head(h, self.rig.venue.ball,
                                          self.rig.geometry.mount_mode, r)
                  for h, r in zip(heads, readings)]
        self.last_drift = [{"head": d.head_name,
                            "bearing": round(d.bearing_deg, 2),
                            "elevation": round(d.elevation_deg, 2),
                            "significant": d.significant} for d in drifts]
        moved = sum(1 for d in drifts if d.significant)
        self.note(f"drift check: {moved} head(s) moved 3 deg or more")

    # venue and taper policy ------------------------------------------------

    def _cmd_venue(self, m: dict, now: float) -> None:
        """Edit the crowd zone and canopy live, from the phone at load-in.

        Deliberately limited to what does NOT touch the geometry. The crowd
        zone, the head band and the canopy feed only the safety taper, so they
        can change between frames and take effect immediately -- which is what
        makes tuning them at load-in possible at all, since you can stand in the
        room and watch the beams respond.

        The ball position and the mount mode are not editable here. Both are
        baked into every head's calibration back-solve, so changing one means
        re-deriving the rig, and doing that mid-show would move every aim at
        once. Edit venue.json and restart for those.
        """
        venue = self.rig.venue
        if venue is None:
            raise ValueError("this event has no venue")
        crowd = venue.crowd_zone
        canopy = venue.canopy

        if "crowd" in m and crowd is not None:
            c = m["crowd"]
            box = crowd.footprint
            band_min = float(c.get("head_band_min", crowd.head_band_min))
            band_max = float(c.get("head_band_max", crowd.head_band_max))
            if band_min >= band_max:
                raise ValueError("head band min must be below max")
            crowd = venuemod.CrowdZone(
                footprint=venuemod.Box(
                    float(c.get("min_x", box.min_x)), float(c.get("max_x", box.max_x)),
                    band_min, band_max,
                    float(c.get("min_z", box.min_z)), float(c.get("max_z", box.max_z))),
                head_band_min=band_min, head_band_max=band_max)

        if "canopy" in m and canopy is not None:
            k = m["canopy"]
            canopy = replace(canopy,
                             enabled=bool(k.get("enabled", canopy.enabled)),
                             height=float(k.get("height", canopy.height)),
                             radius=float(k.get("radius", canopy.radius)))

        self.rig.venue = replace(venue, crowd_zone=crowd, canopy=canopy)
        self.ctx.venue = self.rig.venue
        self.note("venue updated (not yet saved)")

    def _cmd_taper(self, m: dict, now: float) -> None:
        """The taper policy: how far a beam over the crowd is dimmed, and how
        smoothly. `crowd_level` 0 restores a hard guard at the cost of every
        floor-sweep pose."""
        cfg = self.ctx.taper
        self.ctx.taper = replace(
            cfg,
            crowd_level=max(0.0, min(1.0, float(m.get("crowd_level", cfg.crowd_level)))),
            margin_deg=max(0.0, float(m.get("margin_deg", cfg.margin_deg))),
            slew_per_second=max(0.0, float(m.get("slew_per_second", cfg.slew_per_second))),
            enabled=bool(m.get("enabled", cfg.enabled)))
        if not self.ctx.taper.enabled:
            self.note("SAFETY TAPER DISABLED -- beams are no longer dimmed over the crowd")

    # -- the patch ---------------------------------------------------------

    def _patch(self, edit) -> None:
        """Apply one patch edit to rig.json and report it.

        Writes immediately rather than staging, because the alternative is a
        second notion of "the patch" living in the server that the file does not
        agree with, and a half-applied patch is worse than either state.

        It does NOT take effect on the running show. The engine resolves
        profiles, channel offsets and head indices once at startup, and
        re-deriving them mid-frame would change what every layer is writing to
        underneath a look that is up. So every edit says so: this is load-in
        work, and the restart is part of it.

        Note this is the one writer that does not check the lockfile. The lock
        exists to stop an OUTSIDE tool editing behind a running engine's back;
        this is that engine, and it is telling the operator what it did.
        """
        cfg = patchmod.load_rig_config(str(self.event_dir))
        result = edit(cfg, self.profiles)
        for problem in result.errors:
            self.note(f"patch refused: {problem}")
        if not result.ok:
            return
        for warning in result.warnings:
            self.note(f"patch: {warning}")
        patchmod.write_rig(str(self.event_dir), result.config)
        self.pending_patch = True
        self.note("patch saved to rig.json -- not live yet. Apply it to load it "
                  "into the running show")

    # -- the cue list ------------------------------------------------------

    def take_cue(self, cue: "cuesmod.Cue", *, grab: bool = True) -> None:
        """Put a cue on stage, crossfading over its own fade time.

        Fills the same three slots a preset does, because a cue IS a preset
        with somewhere to go next -- a cue list with its own private notion of a
        look would be a second way to say the same thing, and the two would
        drift.

        A look a cue names but the library does not have is skipped with a
        notice rather than aborting the cue. A cue list outlives any one port of
        the library, and taking four of five slots is a recoverable night;
        refusing the cue is not.

        `grab` False changes the slots without taking lanes from a running
        timeline -- for a cue nobody chose this instant (`_advance_due_cue`).
        """
        self._clear_pad()
        for slot in ("color", "level"):
            wanted = getattr(cue, slot)
            if wanted:
                self.slots[slot] = {}
                for group, look in wanted.items():
                    if self.entry(look) is None:
                        self.note(f"cue {cue.name!r}: no look {look!r}")
                        continue
                    self.slots[slot][group] = look
        for look in cue.movement.values():
            if self.entry(look) is None:
                self.note(f"cue {cue.name!r}: no look {look!r}")
                continue
            # hold=True: a cue is an explicit decision, and auto mode advancing
            # off it two bars later would make the cue list look broken.
            self.director.select(look, hold=True)
            break

        if cue.speed is not None:
            self.clock.set_speed(cue.speed, self.runner.now())
        if cue.master is not None:
            self.master = max(0.0, min(1.0, cue.master))
        if cue.macro is not None:
            self._cmd_macro(dict(cue.macro), 0.0)
        if cue.rates is not None:
            self.apply_rates(cue.rates)
        self.apply_look_params(
            cue.params,
            self.looks_named({"movement": cue.movement, "color": cue.color,
                              "level": cue.level}),
            f"cue {cue.name!r}")

        if grab:
            self._grab(statemod.SLOTS)
        self._recompose(fade_beats=cue.fade)
        self.note(f"cue {self.cues.index + 1}/{len(self.cues.cues)}: "
                  f"{cue.name}" + (f" (fade {cue.fade:g} beats)"
                                   if cue.fade else " (cut)"))

    def _cmd_go(self, m: dict, now: float) -> None:
        if self.cues is None:
            raise ValueError("this event has no cues.json")
        cue = self.cues.go(self.ctx.beat)
        if cue is None:
            self.note("end of the cue list -- it does not wrap")
            return
        self.take_cue(cue)

    def _cmd_cue_back(self, m: dict, now: float) -> None:
        if self.cues is None:
            raise ValueError("this event has no cues.json")
        cue = self.cues.back(self.ctx.beat)
        if cue is None:
            self.note("already at the first cue")
            return
        self.take_cue(cue)

    def _cmd_cue(self, m: dict, now: float) -> None:
        """Jump straight to a cue. Fades like any other take."""
        if self.cues is None:
            raise ValueError("this event has no cues.json")
        self.take_cue(self.cues.jump(int(m["index"]), self.ctx.beat))

    def _cmd_cue_reset(self, m: dict, now: float) -> None:
        if self.cues is None:
            raise ValueError("this event has no cues.json")
        self.cues.reset()
        self.note("cue list rewound -- nothing taken")

    def _cmd_macro(self, m: dict, now: float) -> None:
        """Live shape controls over whatever movement look is up.

        Set on the context rather than recomposing, so they survive an auto look
        change -- and so they cost nothing per frame beyond the multiply.

        Ranges are clamped, not because a bigger number breaks anything, but
        because a size of 40 aims every head at the floor in a straight line and
        an operator who typed it meant 4. The safety taper still runs after the
        whole stack regardless, so no macro value can put a beam anywhere the
        taper would not have allowed a stored look to put it.

        The ranges come from `params.MACROS` rather than from literals here.
        They used to be written out three times -- this clamp, a `Spec`, and the
        arguments to a slider in `Move.tsx` -- with nothing keeping them in
        step, so the UI could offer a value the engine would quietly clamp. Now
        the snapshot publishes the same descriptors the clamp uses.
        """
        if "size" in m:
            self.ctx.move_size = parammod.SIZE.coerce(m["size"])
        if "spread" in m:
            self.ctx.move_spread = parammod.SPREAD.coerce(m["spread"])
        if "center" in m:
            c = m["center"]
            # Bounded by what THIS rig's heads can reach, not a fixed +-180 /
            # +-90. On despacio that is -77..198 degrees of bearing: the old
            # limit offered a hundred degrees no head could go to and refused
            # the eighteen past 180 that every one of them can.
            self.ctx.move_center = (
                parammod.clamp(parammod.CENTER_BEARING, c[0], self.reach),
                parammod.clamp(parammod.CENTER_ELEV, c[1], self.reach))
        if m.get("reset"):
            self.ctx.move_size = parammod.SIZE.default
            self.ctx.move_spread = parammod.SPREAD.default
            self.ctx.move_center = (parammod.CENTER_BEARING.default,
                                    parammod.CENTER_ELEV.default)

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

    def _prematch(self, fields: dict) -> None:
        """Another deck loaded a track: match it now, and build its timeline
        on the worker, so that when it becomes the master its show is already
        there -- no frame of the operator's show while it compiles. Never
        touches the transport, which follows the master alone."""
        library = self.show_library
        deck = fields["loaded_deck"]
        if library is None or self.player is None:
            return
        match = library.index.match(
            title=fields.get("loaded_title", ""), artist=fields.get("loaded_artist", ""),
            album=fields.get("loaded_album", ""),
            duration=fields.get("loaded_duration"),
            rekordbox_id=fields.get("loaded_rekordbox_id"),
            signature=fields.get("loaded_signature"))
        tid = match.track_id
        timeline = library.timelines.get(tid) if tid else None
        # A new dict, assigned whole: the snapshot reads it from another thread.
        self.decks = {**self.decks,
                      deck: {"deck": deck, "title": fields.get("loaded_title") or None,
                             "track_id": tid, "timeline": timeline,
                             "fields": dict(fields)}}
        if timeline is not None:
            self.player.precompile(timeline, library.folder.routines,
                                   f"timelines/{tid}.json")

    def _rematch_decks(self) -> None:
        """A new folder load or rig: the decks' matches and shows again."""
        for d in list(self.decks.values()):
            self._prematch(d["fields"])

    def sync_status(self) -> dict:
        return _sync_status(self)

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

    def _grab(self, slots) -> None:
        if self.player is not None:
            self.player.grab(slots)

    def _cmd_follow(self, m: dict, now: float) -> None:
        """Arm or disarm Follow DJ: whether the timeline may drive the rig."""
        if self.player is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        armed = m.get("armed")
        if not isinstance(armed, bool):
            raise ValueError("follow needs armed: true or false")
        self.player.arm(armed)
        self.note("Follow DJ ARMED -- a matched track's timeline drives the rig"
                  if armed else "Follow DJ SAFE -- the DJ feed drives nothing")

    def _cmd_program_grab(self, m: dict, now: float) -> None:
        """Take a lane from the timeline without changing what is on it."""
        if self.player is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        slot = m.get("slot")
        if slot not in statemod.SLOTS:
            raise ValueError(f"slot must be one of {', '.join(statemod.SLOTS)}")
        if not self.player.engaged:
            raise ValueError("the timeline is not driving; there is nothing "
                             "to take a lane from")
        self.player.grab({slot})

    def _cmd_template_set(self, m: dict, now: float) -> dict:
        """Switch template set -- a vibe, live. It takes over on the next
        downbeat (decided with the user), crossfading over its own transition;
        `null` turns templates off. Not saved: show.json's `template_set` is
        what the next start begins with."""
        if self.player is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        set_id = m.get("id")
        if set_id is not None and not isinstance(set_id, str):
            raise ValueError("template_set needs id: a set's id, or null for none")
        self.player.select_set(set_id)
        names = dict(self.player.sets)
        self.note(f"template set -> {names.get(set_id, set_id) if set_id else 'off'}"
                  f" on the next downbeat")
        return {"pending": set_id}

    def _cmd_program_release(self, m: dict, now: float) -> None:
        """Give a lane (or every lane) back to the timeline."""
        if self.player is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        slot = m.get("slot")
        if slot is not None and slot not in statemod.SLOTS:
            raise ValueError(f"slot must be one of {', '.join(statemod.SLOTS)}")
        self.player.release(slot)

    def _cmd_show_latency(self, m: dict, now: float) -> dict:
        """How far ahead of a source's position the lights run, in ms. Applied
        at once and saved to the show folder's show.json (decided with the
        user, F19i), where it follows the show."""
        library = self.show_library
        if library is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        source = m.get("source")
        if not isinstance(source, str) or not source or len(source) > 32:
            raise ValueError("show_latency needs a source, e.g. \"rkbx\"")
        ms = m.get("ms")
        if isinstance(ms, bool) or not isinstance(ms, (int, float)) \
                or not -2000 <= ms <= 2000:
            raise ValueError("ms must be a number from -2000 to 2000")
        self.transport.latency_s = {**self.transport.latency_s,
                                    source: float(ms) / 1000.0}
        root = library.root

        def done(_):
            self.note(f"latency for {source}: {ms:g} ms, saved to show.json")
            self.reload_library()

        self.worker.submit(lambda: showlibrary.save_latency(root, source, ms),
                           done=done, label="saving the latency")
        return {"source": source, "ms": ms}

    # the designer (F19j) -----------------------------------------------------

    def _need_library(self):
        if self.show_library is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        return self.show_library

    def _cmd_timeline_draft(self, m: dict, now: float) -> object:
        """Check an unsaved timeline: the format's rules, then a compile
        against THIS rig. Answers with errors, warnings and problems. If the
        designer is driving the rig on that track, the draft is what plays."""
        library = self._need_library()
        doc = m.get("doc")
        if not isinstance(doc, dict):
            raise ValueError("timeline_draft needs the document as doc")
        routines = library.folder.routines
        rigging = self._rigging()

        def work():
            result = showfiles.validate("timeline", doc)
            if not result.ok:
                return result, None
            timeline = timelinemod.Timeline.from_doc(doc, showfiles.timeline_channels)
            return result, programmod.compile(
                timeline, routines, rigging, f"draft of {doc.get('track')}")

        def then(value, respond):
            result, program = value
            preview = self.player.preview if self.player else None
            if (program is not None and preview is not None
                    and preview.track_id == doc.get("track")):
                preview.program, preview.draft = program, True
            respond(True, {"errors": result.errors, "warnings": result.warnings,
                           "problems": program.problems if program else []})

        return self._on_worker("checking a timeline draft", work, then)

    def _save(self, kind: str, m: dict) -> object:
        library = self._need_library()
        doc = m.get("doc")
        if not isinstance(doc, dict):
            raise ValueError(f"{kind}_save needs the document as doc")
        if not isinstance(m.get("base_rev"), str):
            raise ValueError('base_rev is required: the rev you opened, or "" '
                             "for a new file -- so a save never overwrites a "
                             "change it has not seen")
        path = showfiles.path_for(library.root, kind,
                                  showfiles.doc_ident(kind, doc) or "")
        base = m["base_rev"]

        def then(rev, respond):
            respond(True, {"rev": rev, "path": f"{showfiles.SUBDIR[kind]}/{path.name}"})
            self.reload_library()

        return self._on_worker(f"saving {path.name}",
                               lambda: showfiles.write_doc(path, doc, kind, base),
                               then)

    def _cmd_timeline_save(self, m: dict, now: float) -> object:
        """Write a timeline, refused if the file changed since `base_rev`.
        It applies to the track from its next play, like any folder change."""
        return self._save("timeline", m)

    def _cmd_routine_draft(self, m: dict, now: float) -> object:
        """Check an unsaved routine: the format's rules, then the routine bound
        to THIS rig as it is -- and in each of its variations -- for what will
        not work here (a role no fixture carries, a block with nothing to aim)."""
        self._need_library()
        doc = m.get("doc")
        if not isinstance(doc, dict):
            raise ValueError("routine_draft needs the document as doc")
        rigging = self._rigging()

        def work():
            result = showfiles.validate("routine", doc)
            if not result.ok:
                return result, []
            where = f"routine {doc.get('id')!r}"
            problems = routinesmod.instantiate(doc, {}, rigging, where).problems
            for name in sorted(doc.get("variations") or {}):
                label = f"{where} variation {name!r}"
                for p in routinesmod.instantiate(
                        doc, {"variation": name}, rigging, label).problems:
                    # only what the variation adds: the rest is said above
                    if p.replace(label, where, 1) not in problems:
                        problems.append(p)
            return result, problems

        def then(value, respond):
            result, problems = value
            respond(True, {"errors": result.errors, "warnings": result.warnings,
                           "problems": problems})

        return self._on_worker("checking a routine draft", work, then)

    def _cmd_routine_save(self, m: dict, now: float) -> object:
        return self._save("routine", m)

    def _cmd_preview_arm(self, m: dict, now: float) -> dict:
        """The designer takes the stage: its transport drives the rig through
        this track's timeline. Refused while a DJ is playing, unless forced."""
        library = self._need_library()
        if self.player is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        track_id = m.get("track_id")
        if not isinstance(track_id, str) or track_id not in library.folder.tracks:
            raise ValueError(f"there is no prepped track {track_id!r}")
        sample = self.transport.sample(now)
        if (not m.get("force") and sample.source not in (None, "designer")
                and sample.state in (transportmod.PLAYING, transportmod.STALLED)):
            raise ValueError(f"a DJ is playing ({sample.source}); preview with "
                             f"force to take the rig from them anyway")
        client, _, _ = getattr(self, "_replying", (None, None, None))
        cid = client.id if client is not None else "local"
        name = client.name if client is not None else "designer"
        preview = playbackmod.Preview(cid, name, track_id,
                                      library.grids.get(track_id))
        self.player.start_preview(preview)
        timeline = library.timelines.get(track_id)
        if timeline is not None:
            routines = library.folder.routines
            rigging = self._rigging()

            def done(program):
                if self.player.preview is preview and not preview.draft:
                    preview.program = program

            self.worker.submit(
                lambda: programmod.compile(timeline, routines, rigging,
                                           f"timelines/{track_id}.json"),
                done, label=f"compiling {track_id} for the designer")
        self.note(f"DESIGNER ({name}) is driving the rig on {track_id}")
        return {"track_id": track_id}

    def _owned_preview(self):
        preview = self.player.preview if self.player is not None else None
        if preview is None:
            raise ValueError("the designer is not driving the rig; arm a "
                             "preview first")
        client, _, _ = getattr(self, "_replying", (None, None, None))
        if client is not None and client.id != preview.client:
            raise ValueError(f"{preview.name} is driving the rig, not this "
                             f"console")
        return preview

    def _cmd_preview_transport(self, m: dict, now: float) -> None:
        """Where the designer's audio is, and whether it is playing."""
        if self.player is None or self.player.preview is None:
            # A stream outliving its preview -- released from a phone, or the
            # designer reconnected -- for the tenth of a second until the page
            # sees it. Ten failures a second in every console's notices would
            # bury the one that said why; the release already said it.
            return
        self._owned_preview()
        time_s = m.get("time_s")
        if isinstance(time_s, bool) or not isinstance(time_s, (int, float)) \
                or not -60.0 <= time_s <= 14400.0:
            raise ValueError("time_s must be seconds into the track")
        self.player.preview_position(float(time_s), bool(m.get("playing")), now)

    def _cmd_preview_release(self, m: dict, now: float) -> None:
        preview = self.player.preview if self.player is not None else None
        if preview is None:
            return
        self.player.stop_preview()
        self.note(f"the designer ({preview.name}) let go of the rig")

    def preview_gone(self, cid: str) -> None:
        """A console went away. If it was driving the rig, it stops."""
        preview = self.player.preview if self.player is not None else None
        if preview is not None and preview.client == cid:
            self.player.stop_preview()
            self.note(f"the designer ({preview.name}) disconnected; the rig is "
                      f"back to the show")

    def _cmd_track_link(self, m: dict, now: float) -> dict:
        """This playing track IS that prepped track -- for a guest's copy the
        matcher could not place (a different tag, a different export).

        Recorded on the prepped track as an alias, plus the deck's signature
        when beat-link sent one, so every later play matches by itself. It does
        NOT re-match the track playing now: like any change to the show
        folder, it applies from the track's next play. The write is on the
        worker; the reply says it was queued and a notice says how it went."""
        library = self.show_library
        if library is None:
            raise ValueError("no show folder -- start the engine with "
                             "--show-dir to link tracks")
        track_id = m.get("track_id")
        if not isinstance(track_id, str) or track_id not in library.folder.tracks:
            raise ValueError(f"there is no prepped track {track_id!r} in "
                             f"{library.root}/tracks")
        ident = self.transport.sample(now).identity
        if not ident.title:
            raise ValueError("nothing identified is playing -- a link needs a "
                             "track title from the deck")
        sig = ident.signature
        if sig:
            owner = [t for t in library.index.by_signature.get(sig, ())
                     if t != track_id]
            if owner:
                raise ValueError(
                    f"this deck's signature already belongs to {owner[0]!r}; "
                    f"remove it from tracks/{owner[0]}.json ids.blt_signatures "
                    f"first, or both tracks would claim it")
        root, title = library.root, ident.title

        def done(what: str) -> None:
            if what == "already":
                self.note(f"{track_id} already answers to {title!r}")
            else:
                self.note(f"linked {title!r} to {track_id}; it applies from "
                          f"the track's next play")
            self.reload_library()

        self.worker.submit(
            lambda: showlibrary.link(root, track_id, ident.title, ident.artist,
                                     ident.album, sig,
                                     showlibrary.default_added()),
            done=done, label=f"linking {title!r} to {track_id}")
        return {"queued": True, "track_id": track_id, "applies": "next_play"}

    def _cmd_show_reload(self, m: dict, now: float) -> None:
        """Read the show folder again now, rather than at the next poll."""
        if self.show_dir is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        self.reload_library()

    def _cmd_rate(self, m: dict, now: float) -> None:
        """How fast one slot's chase runs, relative to everything else.

        Distinct from `speed`, which is the CLOCK: speed changes what the music
        is doing as far as the whole show is concerned, including cue holds and
        auto boundaries. A rate changes only how fast one slot's phase
        advances, so a colour chase at 0.5x under a move at 2x is a thing that
        can now be said. The old console needed a separately stored chase for
        every combination, which is a large part of why it accumulated 206
        looks.

        Safe by construction for the same reason the macros are: this moves a
        phase, and every layer downstream -- including the unconditional safety
        pass -- runs exactly as it did.
        """
        if m.get("reset"):
            self.director.phases.reset()
            self.note("slot rates back to 1x")
            return
        slot = str(m["slot"])
        self.director.phases.set_rate(slot, float(m["value"]))

    def apply_rates(self, rates: dict) -> None:
        """Restore stored slot rates, skipping any the engine does not know.

        Same rule as a preset naming a look that has been re-ported away: a
        stored rate for a slot this build has never heard of is a note, not a
        refusal. Half a recall beats an error message mid-set.
        """
        for slot, value in rates.items():
            try:
                self.director.phases.set_rate(str(slot), float(value))
            except (ValueError, TypeError) as exc:
                self.note(f"rate {slot!r}: {exc}")

    def _cmd_patch_apply(self, m: dict, now: float) -> None:
        self.reload_rig()

    def reload_rig(self) -> bool:
        """Adopt the rig currently on disk, without restarting the show.

        Runs on the output thread at a frame boundary, like every other command,
        which is the whole reason this is possible: nothing is halfway through
        evaluating a frame, so the swap cannot produce one built from two
        different rigs.

        **The old rig keeps running if the new one is bad.** Loading and
        validating happen before anything is adopted, so a typo in rig.json --
        or a fixture whose .qxf has gone missing -- costs a red notice rather
        than the show. That ordering is the only thing here that must not be
        rearranged.

        Three pieces of derived state have to go with it:

          * `latest_states` is keyed by fixture id, and an id can now mean a
            different fixture.
          * `_taper_prev` likewise -- and it is seeded to 0 rather than cleared,
            so intensity ramps up under the safety slew limiter instead of
            snapping to whatever the new geometry computes. The limiter that
            exists to stop a beam flashing as it crosses the crowd turns out to
            be exactly the right crossfade for a rig swap, which is why there is
            no separate fade here.
          * The composed Show, since layers resolve fixtures through the rig.

        What it deliberately does NOT do is re-point anything by itself. A pose
        is an offset from a head's calibrated ball aim, so if the reload changed
        which heads exist, every look moves -- correctly, but visibly. The
        caller is told when that is the case.
        """
        try:
            new_rig = rigmod.load_rig(self.event_dir, self.profiles)
        except (configmod.ConfigError, FileNotFoundError, ValueError) as exc:
            self.note(f"reload refused, still running the old rig: {exc}")
            return False
        errors = new_rig.validate()
        if errors:
            self.note("reload refused, still running the old rig: "
                      + "; ".join(errors[:3]))
            return False

        old_heads = tuple(h.name for h in self.rig.geometry.heads) \
            if self.rig.geometry else ()
        new_heads = tuple(h.name for h in new_rig.geometry.heads) \
            if new_rig.geometry else ()

        self.rig = new_rig
        # A re-patch can change which heads exist and how far each travels, so
        # what the centre macro may span is worked out again with the rig.
        self.reach = rig_reach(new_rig)
        self.ctx.rig = new_rig
        self.ctx.venue = new_rig.venue
        self.latest_states = {}
        # Seeded dark, not cleared: an empty dict means "no previous value", and
        # apply_safety then skips the rate limit and adopts the computed taper
        # instantly. Starting at 0 makes the limiter fade it in.
        self.ctx._taper_prev = {f.fid: 0.0 for f in new_rig.fixtures}
        self._prune_targets()
        # Measured against the rig that was just replaced. A moved head decodes
        # the same DMX to different degrees, so the old rows would show "ok"
        # for a head nobody has checked since.
        self.last_drift = None
        self._recompose()
        self.pending_patch = False
        if self.player is not None:
            # Programs are built against fixtures; the old ones are dropped at
            # once (the operator's show runs) and rebuilt for the new rig.
            self.player.idle = None
            self.player.recompile()
            if self.show_library is not None:
                self.player.compile_idle(self.show_library)
            self._clear_pad()
            self._compile_pads()
            self._rematch_decks()

        if old_heads != new_heads:
            self.note(f"rig reloaded, and the moving heads CHANGED "
                      f"({len(old_heads)} -> {len(new_heads)}). Every pose is an "
                      f"offset from a head's calibrated ball aim, so check the "
                      f"calibration before trusting one: Drift check, on Setup")
        else:
            self.note(f"rig reloaded live -- {len(new_rig.fixtures)} fixtures, "
                      f"no restart needed")
        for warning in new_rig.warnings():
            self.note(f"rig: {warning}")
        return True

    def _prune_targets(self) -> None:
        """Drop operator state naming something the rig no longer has.

        Everything cleared alongside the rig swap is keyed by fixture ID. This
        is the other half: trims, colours, flashes, jogs and captures are keyed
        by NAME, and a patch edit can rename or delete the thing they name.
        Left alone they are invisible -- a trim with no row to reset it, and a
        `jog` entry that would silently re-bypass the safety taper if a fixture
        with that name ever came back.

        A target is NOT just a fixture name: `all`, and every tag, are equally
        valid and are how a group gets dimmed. Pruning on fixture names alone
        would throw away the group overrides, which is the common case and
        would look exactly like the console forgetting what it was doing.
        """
        names = {f.name for f in self.rig.fixtures}
        valid = {"all", *self.rig.tags()} | names
        targeted = (set(self.color_overrides) | set(self.white_overrides)
                    | set(self.level_overrides) | self.flashing)
        per_fixture = set(self.jog) | set(self.captures)
        dropped = sorted((targeted - valid) | (per_fixture - names))
        if not dropped:
            return
        self.color_overrides = {k: v for k, v in self.color_overrides.items()
                                if k in valid}
        self.white_overrides = {k: v for k, v in self.white_overrides.items()
                                if k in valid}
        self.level_overrides = {k: v for k, v in self.level_overrides.items()
                                if k in valid}
        self.flashing = self.flashing & valid
        self.jog = {k: v for k, v in self.jog.items() if k in names}
        self.captures = {k: v for k, v in self.captures.items() if k in names}
        # "targets", not "fixtures": removing the last fixture carrying a tag
        # takes the tag with it, so a group trim can be dropped here too, and
        # saying "fixture" would send someone looking for a fixture by that name.
        self.note("dropped settings for targets the rig no longer has: "
                  + ", ".join(dropped[:6]))

    def _cmd_patch_add(self, m: dict, now: float) -> None:
        self._patch(lambda cfg, lib: patchmod.add_fixture(
            cfg, name=m["name"], manufacturer=m["manufacturer"],
            model=m["model"], mode=m["mode"], address=m.get("address"),
            universe=int(m.get("universe", 0)), tags=m.get("tags", ()),
            position=m.get("position"), beam_deg=m.get("beam_deg"),
            notes=m.get("notes", ""), lib=lib))

    def _cmd_patch_remove(self, m: dict, now: float) -> None:
        self._patch(lambda cfg, lib: patchmod.remove_fixture(
            cfg, m["name"], lib=lib))

    def _cmd_patch_address(self, m: dict, now: float) -> None:
        self._patch(lambda cfg, lib: patchmod.set_address(
            cfg, m["name"], int(m["address"]), m.get("universe"), lib=lib))

    def _cmd_patch_tags(self, m: dict, now: float) -> None:
        self._patch(lambda cfg, lib: patchmod.set_tags(
            cfg, m["name"], list(m.get("tags", [])), lib=lib))

    def _cmd_patch_position(self, m: dict, now: float) -> None:
        pos = m["position"]
        self._patch(lambda cfg, lib: patchmod.set_position(
            cfg, m["name"], float(pos["x"]), float(pos["y"]), float(pos["z"]),
            lib=lib))

    def _cmd_patch_autopatch(self, m: dict, now: float) -> None:
        self._patch(lambda cfg, lib: patchmod.autopatch(
            cfg, universe=m.get("universe"), start=int(m.get("start", 1)),
            lib=lib))

    def _cmd_venue_save(self, m: dict, now: float) -> None:
        """Write the live venue back to venue.json, preserving its comments.

        The file is full of `_comment` blocks explaining why each number is what
        it is -- the head band's reasoning, why the ball radius is an estimate.
        Rewriting it from the dataclass would throw all of that away, so this
        edits the parsed JSON in place and leaves every key it does not own.

        Note where it writes: if the room came from `shared/venues/`, this edits
        the SHARED file, and the next show in that room inherits the change.
        That is the intent -- a crowd zone measured tonight is a fact about the
        room, not about tonight -- but it is worth knowing before you drag the
        canopy slider.
        """
        path = self.rig.venue_file
        cfg = json.loads(path.read_text(encoding="utf-8"))
        venue = self.rig.venue
        if venue is None:
            raise ValueError("this event has no venue")

        if venue.crowd_zone is not None:
            box = venue.crowd_zone.footprint
            cfg.setdefault("crowd_zone", {}).update({
                "min_x": box.min_x, "max_x": box.max_x,
                "min_z": box.min_z, "max_z": box.max_z,
                "head_band_min": venue.crowd_zone.head_band_min,
                "head_band_max": venue.crowd_zone.head_band_max})
        if venue.canopy is not None:
            cfg.setdefault("canopy", {}).update({
                "enabled": venue.canopy.enabled,
                "height": venue.canopy.height,
                "radius": venue.canopy.radius})
        taper = self.ctx.taper
        cfg["taper"] = {"crowd_level": taper.crowd_level,
                        "margin_deg": taper.margin_deg,
                        "slew_per_second": taper.slew_per_second,
                        "enabled": taper.enabled}

        configmod.write_json_atomic(path, cfg)
        self.note(f"saved {path.name}")

    def _cmd_hello(self, m: dict, now: float) -> None:
        pass                          # name is set by the connection handler

    # -- the snapshot ------------------------------------------------------

    def snapshot(self) -> dict:
        """Everything the UI needs, built on the caller's thread."""
        now = time.time()
        states = self.latest_states
        g = self.rig.geometry
        stats = self.runner.stats
        by_name = self.by_name

        fixtures = []
        for f in self.rig.fixtures:
            st = states.get(f.fid)
            entry: dict[str, Any] = {
                "id": f.fid, "name": f.name, "tags": list(f.tags),
                "head": f.head, "universe": f.universe, "address": f.address,
                "is_mover": f.is_mover,
                # So the Colour tab can offer a white control only for fixtures
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
            "type": "state",
            "rev": self.rev,
            # So a phone that loaded a stale bundle from cache can be told which
            # engine it is actually driving, rather than the operator guessing.
            "version": __version__,
            # An edit is on disk that the running show is not using. A patch
            # saved and not loaded is precisely the state where the file and the
            # rig disagree, so it gets a standing banner rather than a notice
            # that scrolls away.
            "pending_patch": self.pending_patch,
            "strobe_policy": {
                "enabled": self.ctx.strobe_policy.enabled,
                "ceiling": self.ctx.strobe_policy.ceiling,
                "max_seconds": self.ctx.strobe_policy.max_seconds,
            },
            "cues": self.cues.status() if self.cues else None,
            "flashing": sorted(self.flashing),
            "fading": self.runner.fading,
            "macro": {"size": round(self.ctx.move_size, 3),
                      "spread": round(self.ctx.move_spread, 3),
                      "center": [round(self.ctx.move_center[0], 2),
                                 round(self.ctx.move_center[1], 2)]},
            "profiles": patchmod.list_profiles(self.profiles),
            "event": self.rig.name,
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
            "track": _track_status(self),
            "show": _show_status(self),
            "program": _program_status(self),
            "pad": _pad_status(self),
            "outputs": self.outputs.public(),
            # What a #visuals page draws (milestone 3); None without a show folder.
            "visuals": outputsmod.visuals_public(self._output_frame),
            "preview": (self.player.preview.public()
                        if self.player is not None and self.player.preview
                        else None),
            "auto": self.director.status(),
            # `kind` and `slot` let the UI put each look on the tab that owns it
            # and group within that -- a flat list of 200 is exactly why only a
            # handful got used. `step_of` files a chase's own steps under the
            # chase instead of beside it.
            # `cued` warns that this movement look drives the dimmer itself --
            # it travels dark. Worth saying on screen: an operator who picks one
            # and sees the rig start blinking should know that is the routine
            # and not a fault, and that it will fight a level chase for control.
            # `block` names the block behind a parametric look, which is what
            # lets the UI look its arguments up in the generated block table and
            # render a control panel for a look it has never heard of. Absent on
            # every ported look, which is the honest answer: a table of DMX has
            # nothing to tune.
            # `retired` hides an entry the operator has something better for.
            # Hidden, never removed -- looks.json is a generated artifact whose
            # round-trip proof depends on every entry staying in it, and going
            # back to the original should be one toggle away.
            "looks": [{"name": l.name, "manual_only": l.manual_only,
                       "kind": by_name[l.name].kind if l.name in by_name else "look",
                       "slot": by_name[l.name].slot if l.name in by_name else "movement",
                       "cued": by_name[l.name].is_cued if l.name in by_name else False,
                       "groups": list(by_name[l.name].groups) if l.name in by_name else [],
                       "block": (by_name[l.name].block
                                 if l.name in by_name else None),
                       "retired": (by_name[l.name].retired
                                   if l.name in by_name else False),
                       "replaced_by": (by_name[l.name].replaced_by
                                       if l.name in by_name else None),
                       "args": (dict(by_name[l.name].args)
                                if l.name in by_name else {}),
                       "step_of": by_name[l.name].step_of if l.name in by_name else None}
                      for l in self.setlist.looks],
            # NOT here: the block descriptors, the shape macros' descriptors and
            # the modulator shapes. They are constants of this engine build, so
            # they reach the UI as a file generated from it
            # (`dump_designer_fixtures.py` -> ui/src/blocks.generated.json),
            # which `test_api` keeps current. Putting them in this snapshot
            # would resend the same few kilobytes ten times a second to every
            # phone in the room to say something that cannot change while the
            # engine is up.
            # What the operator has turned, by look name, over what the routine
            # authored. Sparse, and sent separately from `looks[].params` for
            # the same reason `color_overrides` is sent separately from the
            # colour a look states: the UI needs to show both to be able to
            # offer a meaningful Reset.
            "look_params": {name: dict(values)
                            for name, values in self.look_params.items()},
            # Parameters that are moving on their own. A list rather than a map,
            # because the UI shows them as a rack of running modulators and a
            # (look, param) key is not a JSON object key.
            "modulators": self.modulators.status(),
            # How far this rig's heads can travel from the ball, per axis --
            # the real range of every reach-bounded control (the centre macro,
            # an `offset` look). Sent because it is a property of THIS rig and
            # its calibration, which the bundle cannot know in advance.
            "reach": {axis: list(span) for axis, span in self.reach.items()},
            # Movement routines stacked over the base route, in the order they
            # were added. Their offsets ADD, which is why stacking works at all.
            "movement_extra": list(self.movement_extra),
            "movement_stack_max": self.MAX_STACK,
            # The seed the last `vary` used, so a variation worth keeping can be
            # written down and reproduced exactly.
            "vary_seed": self.vary_seed,
            "selection": self.selection,
            "presets": self.presets,
            # How many pages the grid needs. Sent rather than derived, so the UI
            # and the engine cannot disagree about how many banks exist -- and
            # always at least one, because a bank selector that vanishes when
            # the last preset is deleted takes the "save here" pads with it.
            "preset_banks": {
                "size": BANK_SIZE,
                "count": max((p["bank"] for p in self.presets), default=1),
            },
            # The fixture types the UI filters by, biggest group first. Sent
            # rather than derived in the UI so both agree on what a group is.
            "groups": list(self.rig.tags()),
            "palette": [list(c) for c in self.palette.colors],
            "palette_index": self.palette.index,
            "master": round(self.master, 3),
            "blackout": self.blackout,
            "panicked": self.runner.panicked,
            "color_overrides": {k: list(v) for k, v in self.color_overrides.items()},
            "white_overrides": dict(self.white_overrides),
            "level_overrides": dict(self.level_overrides),
            "fixtures": fixtures,
            "venue": venue_summary(self.rig.venue),
            "taper": {"crowd_level": self.ctx.taper.crowd_level,
                      "margin_deg": self.ctx.taper.margin_deg,
                      "slew_per_second": self.ctx.taper.slew_per_second,
                      "enabled": self.ctx.taper.enabled},
            "presence": [c.public(now) for c in self.clients.values()],
            "stats": {"fps": round(stats.effective_fps, 2),
                      "frames": stats.frames,
                      "drops": stats.drops,
                      "eval_errors": stats.eval_errors,
                      "worst_error_ms": round(stats.worst_error * 1000, 3)},
            "notices": list(self.notices[-8:]),
            "warnings": self.rig.warnings(),
            "last_error": self.runner.last_error,
            "drift": self.last_drift,
        }


# The same failure of the other outputs is said again after this long.
OUTPUT_NOTE_S = 10.0

BANK_SIZE = configmod.BANK_SIZE


def _at(preset: dict) -> tuple[int, int]:
    return (preset["bank"], preset["cell"])


def rig_reach(rig: rigmod.Rig) -> dict[str, tuple[float, float]]:
    """How far this rig's heads can travel from the ball, per axis, rounded
    OUTWARD to whole degrees so a slider's ends are reachable rather than a
    hair short. Empty for a rig with no moving heads -- every reach-bounded
    parameter then falls back to its declared range."""
    if rig.geometry is None:
        return {}
    return {axis: (float(math.floor(lo)), float(math.ceil(hi)))
            for axis, (lo, hi) in rig.geometry.rig_reach().items()}


# A head whose achieved aim misses what was asked by more than this has run into
# its rail. Well above quantisation (a 16-bit step is a hundredth of a degree)
# and well below anything the eye would call "close enough".
AT_LIMIT_DEG = 0.5


def _cell_request(m: dict) -> Optional[tuple[int, int]]:
    """The (bank, cell) a command asked for, or None if it did not ask."""
    if m.get("bank") is None and m.get("cell") is None:
        return None
    bank, cell = int(m.get("bank", 1)), int(m.get("cell", 0))
    if bank < 1:
        raise ValueError("banks count from 1")
    if not 0 <= cell < BANK_SIZE:
        raise ValueError(f"a bank holds {BANK_SIZE}; cell must be "
                         f"0-{BANK_SIZE - 1}")
    return (bank, cell)


def free_cell(presets: list[dict]) -> tuple[int, int]:
    """The first empty pad, scanning banks upward.

    Never returns a cell that is taken, and never runs out: the last bank grows
    a new one rather than refusing the save. Being told "your preset banks are
    full" while building a show is not a thing a console should ever do.
    """
    taken = {_at(p) for p in presets}
    bank = 1
    while True:
        for cell in range(BANK_SIZE):
            if (bank, cell) not in taken:
                return (bank, cell)
        bank += 1


def arrange_presets(presets: list[dict]) -> list[dict]:
    """Give every preset a real pad, keeping the ones that already have one.

    Runs on load, so a presets.json written before banks existed -- or one
    hand-edited into a collision -- opens as a working grid instead of an error.
    A preset with no home is placed rather than dropped: it is somebody's saved
    picture, and the worst honest outcome is that it is not where they expected.
    """
    out: list[dict] = []
    placed: list[dict] = []
    homeless: list[dict] = []
    seen: set[tuple[int, int]] = set()
    for p in presets:
        if not isinstance(p, dict) or not p.get("name"):
            continue
        p.setdefault("tags", [])
        bank, cell = p.get("bank"), p.get("cell")
        ok = (isinstance(bank, int) and not isinstance(bank, bool) and bank >= 1
              and isinstance(cell, int) and not isinstance(cell, bool)
              and 0 <= cell < BANK_SIZE and (bank, cell) not in seen)
        if ok:
            seen.add((bank, cell))
            placed.append(p)
        else:
            # Includes the collision case: first claim of a pad wins, the
            # second is rehomed. Order in the file decides, which is at least
            # something a person can look at and predict.
            homeless.append(p)
    out.extend(placed)
    for p in homeless:
        p["bank"], p["cell"] = free_cell(out)
        out.append(p)
    out.sort(key=_at)
    return out


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


def _track_status(controller: "ShowController") -> dict:
    """Which track the DJ is playing and where in it, for the console. Small on
    purpose: it rides the 10 Hz snapshot."""
    s = controller.transport.sample(controller.runner.now())
    pinned, check = controller.pinned, controller.grid_check
    # Only this track's match: between a track change and the next frame the
    # pin still describes the last one, and showing it would name the wrong
    # track for 25 ms.
    current = pinned is not None and pinned.track_seq == s.track_seq
    return {
        "state": s.state,
        "title": s.identity.title or None,
        "artist": s.identity.artist or None,
        "album": s.identity.album or None,
        "duration": s.identity.duration,
        "source": s.source,
        "deck": s.deck,
        "time": None if s.time_s is None else round(s.time_s, 3),
        "rate": round(s.rate, 4),
        "age": None if s.age is None else round(s.age, 2),
        "track_seq": s.track_seq,
        "jump_seq": s.jump_seq,
        "on_air": s.on_air,
        # Which prepped track, and whether its beats agree (F19f).
        "match": (pinned.public(controller.show_library) if current else None),
        "grid_warning": (check.warning if current and check is not None
                         else None),
        # The other decks (milestone 2): what they have loaded, matched, and
        # whether its show is built yet. An empty deck is left out.
        "decks": [{"deck": d["deck"], "title": d["title"], "track_id": d["track_id"],
                   "has_timeline": d["timeline"] is not None,
                   "ready": (d["timeline"] is not None and controller.player is not None
                             and controller.player.precompiled(d["timeline"]))}
                  for d in sorted(list(controller.decks.values()),
                                  key=lambda d: d["deck"])
                  if d["deck"] != s.deck and (d["title"] or d["track_id"])],
    }


def _pad_status(controller: "ShowController") -> Optional[dict]:
    """A routine pad: playing, or waiting for its downbeat."""
    pad, pending = controller.pad, controller._pad_pending
    if pending is not None:
        return {"name": pending["preset"]["name"],
                "routine": pending["preset"]["routine"].get("id"),
                "waiting": True}
    if pad is not None:
        return {"name": pad["name"], "routine": pad["routine"], "waiting": False}
    return None


def _program_status(controller: "ShowController") -> Optional[dict]:
    """Whether the timeline drives, and who has each lane -- the phone's Track
    card. None without a show folder."""
    player = controller.player
    if player is None:
        return None
    out = player.status.public()
    out["latency_ms"] = {k: round(v * 1000.0)
                         for k, v in controller.transport.latency_s.items()}
    return out


def _show_status(controller: "ShowController") -> Optional[dict]:
    """The show folder, in a few hundred bytes: where, which load, how many of
    each, and the first few problems. The documents themselves never ride the
    snapshot."""
    library = controller.show_library
    if library is None:
        return None
    f = library.folder
    return {"dir": str(library.root), "rev": library.rev, **library.counts,
            "errors": len(f.errors), "warnings": len(f.warnings),
            "failed": len(f.failed),
            "problems": showlibrary.describe_problems(library)}


def load_presets(event_dir: Path) -> list[dict]:
    path = Path(event_dir) / "presets.json"
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8")).get("presets", [])
    except (json.JSONDecodeError, OSError):
        # A corrupt presets file must not stop the show starting. Presets are a
        # convenience; the rig is not.
        return []
    return arrange_presets(raw if isinstance(raw, list) else [])


def save_presets(event_dir: Path, presets: list[dict]) -> None:
    configmod.write_json_atomic(Path(event_dir) / "presets.json", {
        # Rewritten in full on every save, so anything not named here is lost --
        # which is how the first version of this quietly stripped the $schema
        # line the moment the UI saved a preset, taking the editor's completion
        # with it.
        "$schema": "../../schemas/presets.schema.json",
        "_comment": [
            "Named combinations of the three slots -- movement, colour and",
            "level -- plus the speed and master they were built at.",
            "",
            "Written by the UI. Safe to hand-edit; a preset naming a look",
            "that no longer exists applies the rest and skips that slot.",
            "",
            "'bank' and 'cell' are a fixed position on a page of eight, not a",
            "sort order -- a preset does not move when its neighbours change.",
            "Two presets claiming one pad, or a preset with neither key, is",
            "not an error: the engine rehomes them at load.",
        ],
        "presets": presets,
    })


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


def reply_id(message: dict) -> Optional[Any]:
    """The id a command wants its reply tagged with, or None for no reply.

    Only a short string or a plain integer. Anything else is treated as no id at
    all rather than an error: the id is the sender's bookkeeping, and a command
    that did its job should not fail because its label was odd. Bounded, because
    it is echoed back and a client should not be able to make the engine send it
    a megabyte.
    """
    if not isinstance(message, dict):
        return None
    rid = message.get("id")
    if isinstance(rid, bool):
        return None
    if isinstance(rid, int) and abs(rid) < 2 ** 53:
        return rid
    if isinstance(rid, str) and 0 < len(rid) <= 64:
        return rid
    return None


def describe(message: dict) -> str:
    kind = message.get("type", "?")
    if kind == "select_look":
        return f"selected {message.get('name')!r}"
    if kind == "color":
        return f"coloured {message.get('target', 'all')}"
    if kind == "level":
        target = message.get("target", "all")
        return (f"cleared the level on {target}" if message.get("clear")
                else f"dimmed {target} to {round(float(message.get('value', 1)) * 100)}%")
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
            show.fx.append(autom.energy_strobe_layer())
            return show
        return autom.Look(name=name, make=make)

    # Cycle lengths live on the patterns themselves now, so they are stated once.
    return autom.SetList([
        look("ball", None, 0.0),
        look("drift", motion.orbit(15.0, bars=16.0, elongation=1.5), None),
        look("sweep", motion.pendulum(45.0, bars=8.0), None),
        look("wide orbit", motion.orbit(40.0, bars=8.0), None),
        look("bob", motion.pendulum(18.0, bars=4.0, vertical=True), None),
    ])


def default_palette() -> autom.Palette:
    return autom.Palette([
        (1.0, 1.0, 1.0), (1.0, 0.1, 0.05), (1.0, 0.55, 0.05), (1.0, 0.95, 0.15),
        (0.15, 1.0, 0.25), (0.1, 0.85, 1.0), (0.2, 0.35, 1.0), (1.0, 0.2, 0.75),
    ])


# ------------------------------------------------------------------- server --

class ExclusiveHTTPServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that refuses a port something else is listening on.

    http.server turns on SO_REUSEADDR. On POSIX that only lets a restart bind
    past connections still in TIME_WAIT. On Windows it means something else
    entirely: a second socket may bind a port another process is already
    LISTENING on, the bind succeeds silently, and two engines answer one port
    -- which one a phone reaches is luck. So on Windows the flag is off and
    SO_EXCLUSIVEADDRUSE is on, which also stops anything that does set
    SO_REUSEADDR from taking the port out from under a running show. Windows
    rebinds a listener over TIME_WAIT without help, so a quick restart still
    works: checked on Windows 11 with connections the server closed first,
    which is what `stop` does to every phone.

    SO_REUSEPORT is pinned off whatever the base class says: on Linux it is
    the deliberate form of the same thing, two listeners on one port.
    """

    # Only Windows has the option, so its presence is the platform test.
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")
    allow_reuse_port = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class ShowServer:
    """HTTP for the UI bundle, WebSocket for everything live."""

    def __init__(self, controller: ShowController, port: int = 8765,
                 ui_dir: Path = UI_DIST, token: Optional[str] = None,
                 bind: str = "0.0.0.0"):
        self.controller = controller
        self.port = port
        self.ui_dir = Path(ui_dir)
        # None means no access control at all: every client arrives as
        # `configure`, which is what this was before tokens existed and is still
        # the right answer on a laptop with no network.
        self.token = token
        self.bind = bind
        self.sockets: dict[str, Connection] = {}
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._stop = threading.Event()
        self.httpd: Optional[ThreadingHTTPServer] = None
        controller.reply_to = self.send_to
        # Where this machine keeps music, for /api/audio when the track file
        # moved (klights.local.json's audio_roots).
        roots = showfiles.read_local_config().get("audio_roots")
        self.audio_roots: list[str] = ([r for r in roots if isinstance(r, str)]
                                       if isinstance(roots, list) else [])

    def token_matches(self, supplied: str) -> bool:
        """Whether a request carries the token -- in constant time, so how
        long a wrong guess takes says nothing about how close it was."""
        if self.token is None:
            return True
        return secrets.compare_digest(supplied.encode("utf-8"),
                                      self.token.encode("utf-8"))

    def new_client_id(self) -> str:
        with self._id_lock:
            self._next_id += 1
            return f"c{self._next_id}"

    def previz_scene(self) -> scenemod.Scene:
        """The previz app's scene, for the rig this engine is driving NOW.

        Rebuilt on every request rather than cached. It costs about 0.3 ms, the
        app polls once a second, and a cache would need invalidating from every
        path that can change what is drawn -- a patch reload, a live venue edit,
        a model re-exported on disk -- each one a way for the previz to show a
        room that no longer exists. One read of `self.controller.rig`: a reload
        swaps the reference rather than mutating the old rig, so the build sees
        one rig, never half of two.
        """
        controller = self.controller
        return scenemod.build(controller.rig, controller.event_dir)

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
        """Hand the payload to each client's own sender. Never blocks.

        This used to call `ws.send` inline, which is a blocking `sendall` on a
        socket with no timeout, from the single broadcast thread. One client
        that stopped reading -- a phone that locked its screen, or walked out of
        wifi range -- filled its TCP window, the send blocked, and the console
        froze for everyone else. At a venue that reads as the engine hanging,
        and the cause is nowhere near the symptom. Found by leaving three idle
        clients connected in a test.

        A timeout on the socket cannot fix it: a receive timeout can land
        halfway through a frame, and the header is already consumed by then, so
        the read stream is unrecoverable. `select` for writability cannot fix it
        either -- it reports ready when a single byte of buffer is free, and a
        30 kB snapshot then blocks partway anyway.

        So the broadcast thread is made structurally incapable of blocking: a
        bounded queue per client, drained by that client's own thread. Full
        queue means the client is behind by `QUEUE_DEPTH` snapshots and is not
        coming back, so it is dropped and its browser reconnects on the backoff
        it already implements.
        """
        for cid, conn in list(self.sockets.items()):
            try:
                conn.queue.put_nowait(payload)
            except queue.Full:
                who = self.controller.clients.get(cid)
                self.controller.note(
                    f"dropped {who.name if who else cid}: {QUEUE_DEPTH} "
                    f"snapshots behind and not reading")
                self.drop(cid)

    def send_to(self, cid: str, payload: dict) -> None:
        """One message to one client. Never blocks, because it is called from
        the output thread with a frame waiting.

        A full queue means this client is already `QUEUE_DEPTH` snapshots behind
        and the broadcast loop is about to drop it; the reply is skipped rather
        than dropping it from here, because closing a socket can wait on the
        network and the output thread cannot.
        """
        conn = self.sockets.get(cid)
        if conn is None:
            return
        try:
            text = json.dumps(payload)
        except (TypeError, ValueError) as exc:
            # A handler returned something JSON cannot carry. The sender still
            # learns the command ran; the data is what is lost, and says so.
            text = json.dumps({"type": "reply", "id": payload.get("id"),
                               "ok": payload.get("ok"),
                               "error": f"reply data not serialisable: {exc}"})
        try:
            conn.queue.put_nowait(text)
        except queue.Full:
            pass

    def drop(self, cid: str) -> None:
        conn = self.sockets.pop(cid, None)
        self.controller.clients.pop(cid, None)
        # A designer that went away must not leave the rig on its transport.
        self.controller.submit_call(lambda: self.controller.preview_gone(cid))
        if conn is not None:
            conn.close()

    # -- one connection ----------------------------------------------------

    def serve_websocket(self, sock: socket.socket, key: str,
                        tier: str = "configure") -> None:
        ws = WebSocket(sock)
        cid = self.new_client_id()
        conn = Connection(ws)
        lock = conn.lock
        client = Client(id=cid, connected_at=time.time(), tier=tier)
        self.sockets[cid] = conn
        self.controller.clients[cid] = client
        try:
            with lock:
                ws.send(json.dumps({"type": "welcome", "id": cid,
                                    "tier": tier}))
                ws.send(json.dumps(self.controller.snapshot()))
            while not self._stop.is_set():
                text = ws.receive()
                if text is None:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                # Valid JSON that is not an object -- `[1, 2]`, `"go"`, `7` --
                # used to reach `.get` below and end this client's connection.
                # It is not a command; ignore it like any other junk.
                if not isinstance(message, dict):
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

            def cross_origin(self) -> bool:
                """A browser page on another site opening a socket to us.

                Browsers send Origin on a WebSocket handshake and do NOT apply
                the same-origin policy to it, so without this any page the
                operator happens to have open could drive the rig. A missing
                Origin is not a browser at all -- the tests' own client, a
                script -- and is left alone, since the token is what guards
                those.
                """
                origin = self.headers.get("Origin")
                if not origin:
                    return False
                host = self.headers.get("Host", "")
                return origin.split("//", 1)[-1].rstrip("/") != host

            def do_GET(self):
                if self.headers.get("Upgrade", "").lower() == "websocket":
                    key = self.headers.get("Sec-WebSocket-Key")
                    if not key:
                        self.send_error(400, "missing Sec-WebSocket-Key")
                        return
                    if self.cross_origin():
                        self.send_error(403, "cross-origin websocket refused")
                        return
                    # The token rides in the query string because that is what
                    # survives being turned into a QR code and scanned by a
                    # phone -- there is no login form to put it in, and there
                    # should not be one at a load-in.
                    query = parse_qs(urlparse(self.path).query)
                    supplied = (query.get("token") or [""])[0]
                    tier = ("configure" if server.token_matches(supplied)
                            else "view")
                    from .websocket import handshake_response
                    self.wfile.write(handshake_response(key))
                    self.wfile.flush()
                    # Hand the raw socket over. close_connection stops the
                    # handler looping for another request line off a socket that
                    # is now speaking a different protocol; `finish` above deals
                    # with the teardown finding it already closed.
                    self.close_connection = True
                    server.serve_websocket(self.connection, key, tier=tier)
                    return
                # The previz app's two reads first: /api/previz/ is under /api/
                # but is not engine/api.py's -- see serve_previz.
                if urlparse(self.path).path.startswith("/api/previz/"):
                    self.serve_previz(urlparse(self.path).path)
                    return
                if urlparse(self.path).path.startswith("/api/"):
                    self.serve_api()
                    return
                self.serve_static()

            def serve_previz(self, path):
                """The standalone previz app's two reads: its scene, and the
                model files that scene names.

                View tier, read-only, no token: the same rig the console
                already shows every phone. Models are served by CONTENT HASH
                and only when the current scene names that hash, so this
                cannot be used to fetch an arbitrary file, and a model that has
                not changed is never downloaded twice.
                """
                # Refused cross-origin like the rest of /api/. The app sends no
                # Origin, so this only ever stops a browser page elsewhere.
                if self.cross_origin():
                    self.send_error(403, "cross-origin request refused")
                    return
                try:
                    scene = server.previz_scene()
                except Exception as exc:                # noqa: BLE001
                    # A previz must never be able to take the console down.
                    body = json.dumps({"error": f"no previz scene: {exc}"}).encode("utf-8")
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/api/previz/scene":
                    etag = f'"{scene.rev}"'
                    if self.headers.get("If-None-Match") == etag:
                        self.send_response(304)
                        self.send_header("ETag", etag)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    body = scene.to_json()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                prefix, suffix = "/api/previz/model/", ".glb"
                sha = path[len(prefix):-len(suffix)] if (
                    path.startswith(prefix) and path.endswith(suffix)) else ""
                if sha in scene.files:
                    body = scene.read_model(sha)
                    if body is None:
                        # Changed on disk since it was hashed. The next scene
                        # names its new hash; the app fetches that instead.
                        self.send_error(503, "model changed on disk; poll the scene again")
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "model/gltf-binary")
                    # Named by its own hash, so it can never change: cache hard.
                    self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404, "not part of the current previz scene")

            def serve_api(self):
                """GET /api/* -- see engine/api.py."""
                if self.cross_origin():
                    self.send_error(403, "cross-origin request refused")
                    return
                url = urlparse(self.path)
                supplied = ((parse_qs(url.query).get("token") or [""])[0]
                            or self.headers.get("X-Klights-Token", ""))
                token_ok = server.token_matches(supplied)
                try:
                    resp = apimod.handle(server.controller.show_library, url.path,
                                         self.headers.get("Range"), token_ok,
                                         server.audio_roots)
                except OSError as exc:
                    resp = apimod.Response(500, json.dumps(
                        {"error": str(exc)}).encode("utf-8"))
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.content_type)
                for name, value in resp.headers.items():
                    self.send_header(name, value)
                length = resp.length if resp.file is not None else len(resp.body)
                self.send_header("Content-Length", str(length))
                self.end_headers()
                if resp.file is None:
                    self.wfile.write(resp.body)
                    return
                with open(resp.file, "rb") as fh:
                    fh.seek(resp.start)
                    left = resp.length
                    while left > 0:
                        chunk = fh.read(min(65536, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)

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
                    # A missing ASSET is a real 404, not a client route. The
                    # bundle's asset names are content-hashed, so a request for
                    # one that is not there means a client is holding a stale
                    # index.html -- and answering it with index.html hands the
                    # browser HTML where it asked for JavaScript, which it
                    # refuses to execute. That is a white screen with an
                    # obscure console error; a 404 is a white screen with an
                    # obvious one, and the reload below stops both.
                    if path.startswith("/assets/"):
                        self.send_error(404, "stale bundle -- reload the page")
                        return
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
                # Caching, and the two halves are opposites on purpose.
                #
                # Assets are content-hashed by the build, so a given name can
                # never change contents: cache them for a year and a phone
                # coming back to the console loads instantly off local storage.
                #
                # index.html is the one file whose contents change under a
                # fixed name, and it is the file that names which asset to
                # fetch. With no header at all a browser applies its own
                # heuristic and can hold it for hours -- so a phone that had the
                # console open before an engine update keeps asking for an asset
                # the rebuild deleted. `version` in the snapshot exists to
                # DETECT that; this is what stops it happening.
                if path.startswith("/assets/"):
                    self.send_header("Cache-Control",
                                     "public, max-age=31536000, immutable")
                else:
                    self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)

            def send_placeholder(self):
                body = PLACEHOLDER.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        # Binds here, so a taken port raises before any thread starts.
        self.httpd = ExclusiveHTTPServer((self.bind, self.port), Handler)
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
<title>kLights engine</title>
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
    import errno

    parser = argparse.ArgumentParser(description="Run the show engine and its UI")
    parser.add_argument("--event", type=Path, default=REPO / "events" / "despacio")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--artnet", metavar="IP",
                        help="send Art-Net here (e.g. 127.0.0.1 or a broadcast "
                             "address). Several: comma separated, each host or "
                             "host:port, e.g. 10.0.0.50,127.0.0.1")
    parser.add_argument("--fps", type=float, default=40.0)
    parser.add_argument("--bpm", type=float, default=124.0)
    parser.add_argument("--ui", type=Path, default=UI_DIST)
    parser.add_argument("--bind", default="0.0.0.0",
                        help="interface to listen on (127.0.0.1 for this "
                             "machine only)")
    parser.add_argument("--token",
                        help="require this token for control. Default: a fresh "
                             "one each run, printed in the URL below")
    parser.add_argument("--no-token", action="store_true",
                        help="no access control -- every client may do anything")
    parser.add_argument("--sync-port", type=int, metavar="PORT",
                        help="listen for tempo from a DJ bridge (JSON or OSC "
                             "over UDP). Off unless given -- see engine/sync.py")
    parser.add_argument("--sync-bind", default="127.0.0.1", metavar="IP",
                        help="interface for --sync-port. Loopback by default, "
                             "because the bridge normally runs on this machine "
                             "and the port has no authentication")
    parser.add_argument("--stop-file", type=Path, metavar="PATH",
                        help="stop cleanly when this file appears. For a "
                             "launcher, which cannot send Ctrl-C to a process "
                             "with no console -- see launcher/")
    parser.add_argument("--show-dir", metavar="DIR",
                        help="the show folder: prepped tracks and their "
                             "timelines (F19). Default: $KLIGHTS_SHOW_DIR, then "
                             "show_dir in klights.local.json, else none")
    args = parser.parse_args(argv)
    if args.artnet:
        try:
            parse_artnet_targets(args.artnet)
        except ValueError as exc:
            parser.error(str(exc))
    # A stop request older than this engine is not addressed to it: it is what
    # an engine that died before it could tidy up left behind.
    if args.stop_file is not None:
        args.stop_file.unlink(missing_ok=True)

    # A token by default, because the alternative default is that anyone who can
    # reach the port can re-address the rig. Regenerated every run: there is
    # nothing to remember, and a link shared last week stops working, which is
    # the correct behaviour for a link that grants control of a lighting rig.
    token = None if args.no_token else (args.token or secrets.token_urlsafe(6))

    show_dir = showfiles.resolve_show_dir(args.show_dir)
    # This machine's own output addresses (milestone 3), over the show's.
    local_outputs, problem = outputsmod.local_override(
        showfiles.read_local_config().get("outputs"))
    if problem is not None:
        print(f"outputs: {problem}", file=sys.stderr)
    controller = ShowController(args.event, artnet=args.artnet, fps=args.fps,
                                bpm=args.bpm, show_dir=show_dir,
                                local_outputs=local_outputs)
    server = ShowServer(controller, port=args.port, ui_dir=args.ui,
                        token=token, bind=args.bind)

    # The port before anything touches the rig. controller.start() writes the
    # event's lock and starts sending Art-Net, so a second engine that got that
    # far before finding its port taken would flash the rig, and on its way out
    # delete the lock that belongs to the engine already running.
    try:
        server.start()
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise SystemExit(f"port {args.port} is already in use -- "
                             f"is another engine running?")
        raise SystemExit(f"cannot listen on {args.bind}:{args.port}: "
                         f"{exc.strerror or exc}")

    if args.sync_port:
        # The tempo port too, for the same reason: an engine sharing it with
        # another hears none of the DJ, and nothing on screen says so.
        try:
            controller.enable_sync(args.sync_port, args.sync_bind)
        except OSError as exc:
            server.stop()
            if exc.errno == errno.EADDRINUSE:
                raise SystemExit(f"sync port {args.sync_port} is already in use -- "
                                 f"is another engine, or an OSC app, listening there?")
            raise SystemExit(f"cannot listen for sync on {args.sync_bind}:"
                             f"{args.sync_port}: {exc.strerror or exc}")

    controller.start()

    print(f"event   {controller.rig.name}  "
          f"({len(controller.rig.fixtures)} fixtures, universes {controller.rig.universes})")
    print(f"output  {'Art-Net -> ' + args.artnet if args.artnet else 'null (no wire)'}")
    # The taper is the only thing standing between a stored pose and someone's
    # eyes, and its policy is editable from a phone. Say what it is set to every
    # time, so "why is nothing dimming" is answered before it is asked.
    taper = controller.ctx.taper
    if not taper.enabled:
        print("safety  TAPER DISABLED -- beams are not guarded. See docs/SAFETY.md")
    else:
        print(f"safety  taper on, crowd level {taper.crowd_level:.0%}"
              f"{' -- beams over the crowd go dark' if taper.crowd_level == 0 else ''}"
              f"  (docs/SAFETY.md)")
    # Strobe is the one genuine medical risk here and it is easy to forget the
    # policy exists, so it is printed whether or not it is doing anything.
    strobe = controller.ctx.strobe_policy
    if not strobe.enabled:
        print("strobe  blocked entirely")
    elif strobe.max_seconds > 0 or strobe.ceiling < 1.0:
        parts = []
        if strobe.ceiling < 1.0:
            parts.append(f"capped at {strobe.ceiling:.0%} of the band")
        if strobe.max_seconds > 0:
            parts.append(f"cut off after {strobe.max_seconds:g}s continuous")
        print(f"strobe  {', '.join(parts)}")
    else:
        print("strobe  UNLIMITED -- no ceiling, no duration cap "
              "(docs/SAFETY.md)")
    print(f"timing  {', '.join(controller.runner.applied_timing)}")
    # Said out loud because it is a write path into the show clock that no token
    # guards -- a datagram cannot be challenged. Anyone reading this line should
    # be able to tell whether it is bound where they meant.
    if controller.sync is not None:
        print(f"sync    tempo ingest on {args.sync_bind}:{args.sync_port} "
              f"(UDP, JSON or OSC, unauthenticated)")
    library = controller.show_library
    if library is not None:
        f = library.folder
        problems = (f" -- {len(f.errors)} errors, see `python -m "
                    f"engine.showfiles check {library.root}`" if f.errors else "")
        print(f"shows   {library.root}: {library.describe()}{problems}")
        print(f"outputs {controller.outputs.describe()}")
        if controller.player is not None:
            print("follow  " + ("ARMED -- a matched track's timeline drives the "
                                "rig" if controller.player.armed else
                                "DISARMED -- the DJ feed shows but drives "
                                "nothing until someone arms it on the phone"))
    print(f"ui      {'bundle at ' + str(args.ui) if Path(args.ui).is_dir() else 'not built -- see the page for how'}")
    if token is None:
        print("access  OPEN -- anyone who can reach this port has full control")
    else:
        print(f"access  token {token} required to control; without it, view only")
    # The token goes in the URL because the single most common load-in failure
    # is typing something wrong into a phone, and a URL can be a QR code.
    suffix = "" if token is None else f"?token={token}"
    for url in local_addresses(args.port):
        print(f"open    {url}{suffix}")
    print("\nCtrl-C to stop." if args.stop_file is None
          else f"\nCtrl-C, or create {args.stop_file}, to stop.", flush=True)

    try:
        while True:
            time.sleep(0.5)
            if args.stop_file is not None and args.stop_file.exists():
                print("\nstop requested...", flush=True)
                break
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        server.stop()
        controller.stop()
        if args.stop_file is not None:
            args.stop_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
