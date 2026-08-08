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
from dataclasses import dataclass, field, replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional

from . import __version__
from . import auto as autom
from . import calibrate as calibmod
from . import clock as clockmod
from . import geometry as geo
from . import library as libmod
from . import motion
from . import rig as rigmod
from . import safety as safetymod
from . import state as statemod
from . import venue as venuemod
from .output import ArtNetOutput, NullOutput
from .runner import Runner
from .websocket import WebSocket, WebSocketClosed, WebSocketError

REPO = Path(__file__).resolve().parent.parent
UI_DIST = REPO / "ui" / "dist"

BROADCAST_HZ = 10.0

# A solve fitting worse than this is not written over a working calibration.
# Comfortably above the 0.67 deg worst case the solver hits on clean captures,
# and well below the tens of degrees a mistaken capture produces.
MAX_WRITE_RESIDUAL_DEG = 5.0

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

        # A taper policy saved from the UI has to survive a restart, or saving
        # it is theatre. venue.json is the right home: the policy is a property
        # of the room and the crowd in it, not of the rig.
        taper_cfg = json.loads(
            (self.event_dir / "venue.json").read_text(encoding="utf-8")
        ).get("taper", {})
        taper = safetymod.TaperConfig(
            crowd_level=float(taper_cfg.get("crowd_level", 0.5)),
            margin_deg=float(taper_cfg.get("margin_deg", 6.0)),
            slew_per_second=float(taper_cfg.get("slew_per_second", 2.0)),
            enabled=bool(taper_cfg.get("enabled", True)))

        self.ctx = statemod.EvalContext(rig=self.rig, venue=self.rig.venue,
                                        taper=taper)
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
        self.presets = load_presets(self.event_dir)
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
        handler(message, now)
        self.rev += 1
        if client is not None and kind != "hello":
            client.last_action = describe(message)
            client.last_action_at = time.time()

    # slots ----------------------------------------------------------------

    def entry(self, name: Optional[str]) -> Optional[libmod.LibraryEntry]:
        return self.by_name.get(name) if name else None

    def _recompose(self) -> None:
        """Rebuild the Show from the three slots.

        The director keeps owning WHICH movement look is up (that is what auto
        look changes change), but it no longer owns the whole Show -- it
        delegates back here so the colour and level slots survive a look change.
        """
        def compose(look, color: tuple[float, float, float]):
            movement = self.entry(look.name) if look is not None else None
            return libmod.compose(
                movement if movement is not None and movement.is_movement else None,
                self.slot_entries("color"), self.slot_entries("level"), color)
        self.director.compose = compose
        self.director.rebuild()

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
        """
        name = str(m["name"]).strip()[:40]
        if not name:
            raise ValueError("a preset needs a name")
        self.presets = [p for p in self.presets if p["name"] != name]
        self.presets.append({
            "name": name, **self.selection,
            "speed": round(self.clock.speed, 3),
            "master": round(self.master, 3),
        })
        save_presets(self.event_dir, self.presets)
        self.note(f"saved preset {name!r}")

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

    def _rebuild_overrides(self) -> None:
        layers: list[statemod.Layer] = []
        for target, color in self.color_overrides.items():
            tags = None if target == "all" else (target,)
            layers.append(statemod.color_layer(color, tags=tags))
        for target, level in self.level_overrides.items():
            tags = None if target == "all" else (target,)
            layers.append(statemod.intensity_layer(
                lambda ctx, fixture, level=level: level, tags=tags))
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
        payload = {"measured": time.strftime("%Y-%m-%d"),
                   "mount_mode": self.rig.geometry.mount_mode,
                   "source": "solved from the web UI", "heads": heads}
        path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
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

    def _cmd_venue_save(self, m: dict, now: float) -> None:
        """Write the live venue back to venue.json, preserving its comments.

        The file is full of `_comment` blocks explaining why each number is what
        it is -- the head band's reasoning, why the ball radius is an estimate.
        Rewriting it from the dataclass would throw all of that away, so this
        edits the parsed JSON in place and leaves every key it does not own.
        """
        path = self.event_dir / "venue.json"
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

        path.write_text(json.dumps(cfg, indent=2) + "\n", encoding="utf-8")
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
            # Head position, so the UI can work out capture targets itself --
            # the adjacent corners are 90 degrees either side of the ball from a
            # head in a corner, which is the spread the solver needs and which
            # the obvious targets do not give.
            if f.head is not None and g is not None:
                h = g.heads[f.head]
                entry["position"] = [h.x, h.height, h.z]
                entry["beam_deg"] = h.beam_angle_deg

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
                entry["jogging"] = f.name in self.jog
                entry["captures"] = len(self.captures.get(f.name, []))
            fixtures.append(entry)

        return {
            "type": "state",
            "rev": self.rev,
            # So a phone that loaded a stale bundle from cache can be told which
            # engine it is actually driving, rather than the operator guessing.
            "version": __version__,
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


def load_presets(event_dir: Path) -> list[dict]:
    path = Path(event_dir) / "presets.json"
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text(encoding="utf-8")).get("presets", [])
    except (json.JSONDecodeError, OSError):
        # A corrupt presets file must not stop the show starting. Presets are a
        # convenience; the rig is not.
        return []


def save_presets(event_dir: Path, presets: list[dict]) -> None:
    (Path(event_dir) / "presets.json").write_text(
        json.dumps({
            "_comment": [
                "Named combinations of the three slots -- movement, colour and",
                "level -- plus the speed and master they were built at.",
                "",
                "Written by the UI. Safe to hand-edit; a preset naming a look",
                "that no longer exists applies the rest and skips that slot.",
            ],
            "presets": presets,
        }, indent=2) + "\n", encoding="utf-8")


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
