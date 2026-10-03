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
import secrets
import socket
import threading
import time
import traceback
from urllib.parse import parse_qs, urlparse
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__
from . import auto as autom
from . import calibrate as calibmod
from . import config as configmod
from . import cues as cuesmod
from . import clock as clockmod
from . import geometry as geo
from . import library as libmod
from . import motion
from . import patch as patchmod
from . import rig as rigmod
from . import safety as safetymod
from . import scene as scenemod
from . import state as statemod
from . import sync as syncmod
from . import venue as venuemod
from .output import ArtNetOutput, NullOutput
from .output.artnet import parse_targets as parse_artnet_targets
from .runner import Runner
from .websocket import WebSocket, WebSocketClosed, WebSocketError

REPO = Path(__file__).resolve().parent.parent
UI_DIST = REPO / "ui" / "dist"

BROADCAST_HZ = 10.0

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
                 fps: float = 40.0, bpm: float = 124.0):
        self.event_dir = Path(event_dir)
        self.rig = rigmod.load_rig(self.event_dir)
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
        if looks_path.exists():
            self.setlist, self.library = libmod.load_setlist(looks_path)
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
            before_frame=self._drain, on_show=self._attach_overrides,
            on_frame=self._publish)

        self.commands: "queue.Queue[tuple[dict, Optional[Client], float]]" = queue.Queue()
        self.clients: dict[str, Client] = {}
        self.master = 0.9
        self.blackout = False

        # Live operator overrides, re-attached to whatever Show is running so
        # they survive an auto-mode look change.
        self.override_layers: list[statemod.Layer] = []
        self.color_overrides: dict[str, tuple[float, float, float]] = {}
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
        self.presets = load_presets(self.event_dir)
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
        self.runner.start()

    def stop(self) -> None:
        if self.sync is not None:
            self.sync.stop()
        self.runner.stop()
        self.output.close()
        try:
            patchmod.lock_path(str(self.event_dir)).unlink(missing_ok=True)
        except OSError:
            pass

    # -- the frame hooks ---------------------------------------------------

    def _drain(self) -> None:
        """Apply every queued command. Runs on the output thread, at the top of
        a frame, so a command can never land mid-evaluation."""
        while True:
            try:
                message, client, at = self.commands.get_nowait()
            except queue.Empty:
                return
            try:
                self.apply(message, client, at)
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

    def apply(self, message: dict, client: Optional[Client],
              at: Optional[float] = None) -> None:
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
        handler(message, now)
        self.rev += 1
        if client is not None and kind != "hello":
            client.last_action = describe(message)
            client.last_action_at = time.time()

    # slots ----------------------------------------------------------------

    def entry(self, name: Optional[str]) -> Optional[libmod.LibraryEntry]:
        return self.by_name.get(name) if name else None

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
                self.slot_entries("color"), self.slot_entries("level"), color)
        self.director.compose = compose
        show = self.director.rebuild()
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
        self._recompose()

    def _cmd_clear_slot(self, m: dict, now: float) -> None:
        slot = m["slot"]
        if slot not in ("movement", "color", "level"):
            raise ValueError(f"unknown slot {slot!r}")
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
        self._recompose()

    def _cmd_release(self, m: dict, now: float) -> None:
        self.director.release()

    def _cmd_next_look(self, m: dict, now: float) -> None:
        self.setlist.advance()
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
            "bank": where[0], "cell": where[1],
            "tags": [str(t) for t in tags] if tags is not None
                    else list(previous.get("tags", [])) if previous else [],
        }], key=_at)
        save_presets(self.event_dir, self.presets)
        self.note(f"saved preset {name!r} to {where[0]}.{where[1] + 1}")

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
        if preset.get("rates"):
            self.apply_rates(preset["rates"])
        if preset.get("speed"):
            self.clock.set_speed(float(preset["speed"]), now)
        if preset.get("master") is not None:
            self.master = max(0.0, min(1.0, float(preset["master"])))
        self.director.held = True
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
        if m.get("clear"):
            self.color_overrides.pop(target, None)
        else:
            self.color_overrides[target] = color
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
            layers.append(statemod.color_layer(color, tags=tags))
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
        """Compare fresh ball readings against the stored calibration."""
        if self.rig.geometry is None:
            raise ValueError("no geometry to check drift against")
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

    def take_cue(self, cue: "cuesmod.Cue") -> None:
        """Put a cue on stage, crossfading over its own fade time.

        Fills the same three slots a preset does, because a cue IS a preset
        with somewhere to go next -- a cue list with its own private notion of a
        look would be a second way to say the same thing, and the two would
        drift.

        A look a cue names but the library does not have is skipped with a
        notice rather than aborting the cue. A cue list outlives any one port of
        the library, and taking four of five slots is a recoverable night;
        refusing the cue is not.
        """
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
        """
        if "size" in m:
            self.ctx.move_size = max(0.0, min(3.0, float(m["size"])))
        if "spread" in m:
            self.ctx.move_spread = max(-1.0, min(1.0, float(m["spread"])))
        if "center" in m:
            c = m["center"]
            self.ctx.move_center = (max(-180.0, min(180.0, float(c[0]))),
                                    max(-90.0, min(90.0, float(c[1]))))
        if m.get("reset"):
            self.ctx.move_size = 1.0
            self.ctx.move_spread = 0.0
            self.ctx.move_center = (0.0, 0.0)

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
        self.clock.sync(
            now,
            bpm=m.get("bpm"),
            beat=m.get("beat"),
            beat_in_bar=m.get("beat_in_bar"),
            # The bridge naming itself is what makes the console able to say
            # WHICH thing is driving the clock, rather than just "not you".
            source=m.get("source", "sync"),
            phrase_measured=m.get("phrase_measured"),
            phrase_label=m.get("phrase_label"),
            phrase_ends_in=m.get("phrase_ends_in"),
            at=syncmod.now())
        if "deck" in m:
            self.sync_deck = m["deck"]
        if "track" in m:
            self.sync_track = m["track"]

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
        self.note(f"took the clock back from {was!r} at "
                  f"{self.clock.bpm:.1f} bpm")

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
        self.ctx.rig = new_rig
        self.ctx.venue = new_rig.venue
        self.latest_states = {}
        # Seeded dark, not cleared: an empty dict means "no previous value", and
        # apply_safety then skips the rate limit and adopts the computed taper
        # instantly. Starting at 0 makes the limiter fade it in.
        self.ctx._taper_prev = {f.fid: 0.0 for f in new_rig.fixtures}
        self._prune_targets()
        self._recompose()
        self.pending_patch = False

        if old_heads != new_heads:
            self.note(f"rig reloaded, and the moving heads CHANGED "
                      f"({len(old_heads)} -> {len(new_heads)}). Every pose is an "
                      f"offset from a head's calibrated ball aim, so check the "
                      f"calibration before trusting one: "
                      f"python -m engine.calibrate drift")
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
        targeted = set(self.color_overrides) | set(self.level_overrides) | self.flashing
        per_fixture = set(self.jog) | set(self.captures)
        dropped = sorted((targeted - valid) | (per_fixture - names))
        if not dropped:
            return
        self.color_overrides = {k: v for k, v in self.color_overrides.items()
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
            "auto": self.director.status(),
            # `kind` and `slot` let the UI put each look on the tab that owns it
            # and group within that -- a flat list of 200 is exactly why only a
            # handful got used. `step_of` files a chase's own steps under the
            # chase instead of beside it.
            # `cued` warns that this movement look drives the dimmer itself --
            # it travels dark. Worth saying on screen: an operator who picks one
            # and sees the rig start blinking should know that is the routine
            # and not a fault, and that it will fight a level chase for control.
            "looks": [{"name": l.name, "manual_only": l.manual_only,
                       "kind": by_name[l.name].kind if l.name in by_name else "look",
                       "slot": by_name[l.name].slot if l.name in by_name else "movement",
                       "cued": by_name[l.name].is_cued if l.name in by_name else False,
                       "groups": list(by_name[l.name].groups) if l.name in by_name else [],
                       "step_of": by_name[l.name].step_of if l.name in by_name else None}
                      for l in self.setlist.looks],
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
            "drift": getattr(self, "last_drift", None),
        }


BANK_SIZE = configmod.BANK_SIZE


def _at(preset: dict) -> tuple[int, int]:
    return (preset["bank"], preset["cell"])


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

    def drop(self, cid: str) -> None:
        conn = self.sockets.pop(cid, None)
        self.controller.clients.pop(cid, None)
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
                    tier = ("configure" if server.token is None
                            or supplied == server.token else "view")
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
                if urlparse(self.path).path.startswith("/api/previz/"):
                    self.serve_previz(urlparse(self.path).path)
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
                    body = scene.files[sha].read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "model/gltf-binary")
                    # Named by its own hash, so it can never change: cache hard.
                    self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404, "not part of the current previz scene")

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

    controller = ShowController(args.event, artnet=args.artnet, fps=args.fps,
                                bpm=args.bpm)
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
