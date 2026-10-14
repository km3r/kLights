"""
Following the DJ (F19): which track is playing, the show folder as loaded,
and the timelines and template sets that drive the rig while Follow is armed.
Also Studio's preview, which drives the same player from Studio's transport,
and the outputs that are not lights (OSC, MIDI, timecode, the visuals page),
which read the same frame.

`_choose_show` is the runner's hook: each frame, it decides whose Show is on
stage.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .. import collection as collectionmod
from .. import outputs as outputsmod
from .. import playback as playbackmod
from .. import program as programmod
from .. import showlibrary
from .. import state as statemod
from .. import tracks as tracksmod
from .. import transport as transportmod
from ..commands import CONFIGURE, OPERATE, command
from .common import N, S, _NAME, _SLOT

if TYPE_CHECKING:                   # the class these helpers are given
    from . import ShowController


# The same failure of the other outputs is said again after this long.
OUTPUT_NOTE_S = 10.0


class Follow:
    """The track, the show folder as loaded, and what drives the rig."""

    def _init_outputs(self, local_outputs: Optional[dict]) -> None:
        # The other outputs (milestone 3): OSC and friends, pointed where the
        # show folder says -- or this machine's klights.local.json, which wins.
        self.outputs = outputsmod.Outputs()
        self.local_outputs = dict(local_outputs or {})
        self._output_frame: Optional[outputsmod.ProgramFrame] = None
        # The last outputs failure said, and when: (message, engine time).
        self._output_said: Optional[tuple[str, float]] = None

    def _init_follow(self, show_dir: Optional[Path]) -> None:
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
        # The DJ's rekordbox collection, read by the prep bridge in a child
        # process -- never in this one (engine/collection.py).
        self.collection = collectionmod.Collection()
        # Whether the timeline drives the rig this frame (F19i). Only with a
        # show folder; without one the runner never asks.
        self.player: Optional[playbackmod.TrackPlayer] = None
        self._frame_sample: Optional[transportmod.TrackSample] = None
        # What the other decks have loaded (milestone 2), from beat-link-
        # trigger: matched, and their timelines built in advance.
        self.decks: dict[str, dict] = {}

    def _open_show_folder(self) -> None:
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

    def _grab(self, slots) -> None:
        if self.player is not None:
            self.player.grab(slots)

    @command("follow", tier=OPERATE, args={"armed": S(bool, required=True)})
    def _cmd_follow(self, m: dict, now: float) -> None:
        """Arm or disarm Follow DJ: whether the timeline may drive the rig."""
        if self.player is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        armed = m["armed"]
        self.player.arm(armed)
        self.note("Follow DJ ARMED -- a matched track's timeline drives the rig"
                  if armed else "Follow DJ SAFE -- the DJ feed drives nothing")

    @command("program_grab", tier=OPERATE, args={
        "slot": S(str, required=True, choices=statemod.SLOTS)})
    def _cmd_program_grab(self, m: dict, now: float) -> None:
        """Take a lane from the timeline without changing what is on it."""
        if self.player is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        slot = m["slot"]
        if not self.player.engaged:
            raise ValueError("the timeline is not driving; there is nothing "
                             "to take a lane from")
        self.player.grab({slot})

    @command("template_set", tier=OPERATE,
             reply={"pending": S(str, fix="the set taking over on the next downbeat; "
                                          "null for none")},
             args={
        "set": S(str, fix="a set's id, or null for none")})
    def _cmd_template_set(self, m: dict, now: float) -> dict:
        """Switch template set -- a vibe, live. It takes over on the next
        downbeat (decided with the user), crossfading over its own transition;
        `set: null` turns templates off. Not saved: show.json's `template_set`
        is what the next start begins with."""
        if self.player is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        # The set used to travel as `id` -- the field a reply is tagged with,
        # so a client asking for a reply had its set replaced by its own reply
        # id. A console on a bundle from before the rename still sends `id`
        # and no `set`, and is read as it meant.
        set_id = m["set"] if "set" in m else m.get("id")
        if set_id is not None and not isinstance(set_id, str):
            raise ValueError("template_set needs set: a set's id, or null for none")
        self.player.select_set(set_id)
        names = dict(self.player.sets)
        self.note(f"template set -> {names.get(set_id, set_id) if set_id else 'off'}"
                  f" on the next downbeat")
        return {"pending": set_id}

    @command("program_release", tier=OPERATE, args={"slot": _SLOT})
    def _cmd_program_release(self, m: dict, now: float) -> None:
        """Give a lane (or every lane) back to the timeline."""
        if self.player is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        self.player.release(m.get("slot"))

    @command("show_latency", tier=CONFIGURE,
             reply={"source": S(str, required=True), "ms": S(N, required=True)}, args={
        "source": S(str, required=True, non_empty=True, fix='e.g. "rkbx"'),
        "ms": S(N, required=True, min=-2000, max=2000)})
    def _cmd_show_latency(self, m: dict, now: float) -> dict:
        """How far ahead of a source's position the lights run, in ms. Applied
        at once and saved to the show folder's show.json (decided with the
        user, F19i), where it follows the show."""
        library = self.show_library
        if library is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        source, ms = m["source"], m["ms"]
        if len(source) > 32:
            raise ValueError("show_latency's source is a short name, e.g. \"rkbx\"")
        self.transport.latency_s = {**self.transport.latency_s,
                                    source: float(ms) / 1000.0}
        root = library.root

        def done(_):
            self.note(f"latency for {source}: {ms:g} ms, saved to show.json")
            self.reload_library()

        self.worker.submit(lambda: showlibrary.save_latency(root, source, ms),
                           done=done, label="saving the latency")
        return {"source": source, "ms": ms}

    @command("preview_arm", tier=CONFIGURE,
             reply={"track_id": S(str, required=True)},
             args={"track_id": _NAME, "force": S(bool)})
    def _cmd_preview_arm(self, m: dict, now: float) -> dict:
        """The designer takes the stage: its transport drives the rig through
        this track's timeline. Refused while a DJ is playing, unless forced."""
        library = self._need_library()
        if self.player is None:
            raise ValueError("no show folder -- start the engine with --show-dir")
        track_id = m["track_id"]
        if track_id not in library.folder.tracks:
            raise ValueError(f"there is no prepped track {track_id!r}")
        sample = self.transport.sample(now)
        if (not m.get("force") and sample.source not in (None, "designer")
                and sample.state in (transportmod.PLAYING, transportmod.STALLED)):
            raise ValueError(f"a DJ is playing ({sample.source}); preview with "
                             f"force to take the rig from them anyway")
        client = self._sender()
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
        self.note(f"STUDIO ({name}) is driving the rig on {track_id}")
        return {"track_id": track_id}

    def _owned_preview(self):
        preview = self.player.preview if self.player is not None else None
        if preview is None:
            raise ValueError("the designer is not driving the rig; arm a "
                             "preview first")
        client = self._sender()
        if client is not None and client.id != preview.client:
            raise ValueError(f"{preview.name} is driving the rig, not this "
                             f"console")
        return preview

    @command("preview_transport", tier=CONFIGURE, args={
        "time_s": S(N, required=True, min=-60, max=14400, fix="seconds into the track"),
        "playing": S(bool)})
    def _cmd_preview_transport(self, m: dict, now: float) -> None:
        """Where the designer's audio is, and whether it is playing."""
        if self.player is None or self.player.preview is None:
            # A stream outliving its preview -- released from a phone, or the
            # designer reconnected -- for the tenth of a second until the page
            # sees it. Ten failures a second in every console's notices would
            # bury the one that said why; the release already said it.
            return
        self._owned_preview()
        self.player.preview_position(float(m["time_s"]), bool(m.get("playing")), now)

    @command("preview_release", tier=CONFIGURE)
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

    def _public_follow(self) -> dict:
        """The DJ's track, the show folder, and what the timeline drives."""
        return {
            "track": _track_status(self),
            "show": _show_status(self),
            "program": _program_status(self),
            "outputs": self.outputs.public(),
            # What a #visuals page draws (milestone 3); None without a show folder.
            "visuals": outputsmod.visuals_public(self._output_frame),
            "preview": (self.player.preview.public()
                        if self.player is not None and self.player.preview
                        else None),
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
