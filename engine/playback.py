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

**The designer** (F19j) can take the stage instead: armed from a configure-tier
console, it plays its own transport -- the browser's audio position -- through
the track's grid, running the timeline it is editing (its latest draft, else the
saved one). It is refused while a DJ is playing unless forced, every console
shows that it is driving, and it lets go when its browser does.

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
from . import templates as templatesmod
from . import timeline as timelinemod
from . import tracktime
from . import transport as transportmod

POLICIES = ("idle", "freeze", "continue")
NO_SET = ""                  # a pending switch to no template at all
IDLE_LENGTH = 1e7            # beats: an idle clip that never runs out
PREVIEW_JUMP_S = 0.2         # a designer position this far off is a seek


@dataclass
class Preview:
    """The designer driving the rig: whose it is, which track, where."""
    client: str
    name: str
    track_id: str
    grid: object
    program: Optional[programmod.Program] = None
    draft: bool = False
    time_s: float = 0.0
    at: float = 0.0
    playing: bool = False
    jumps: int = 0

    def position(self, now: float) -> float:
        return self.time_s + ((now - self.at) if self.playing else 0.0)

    def public(self) -> dict:
        return {"client": self.client, "name": self.name,
                "track_id": self.track_id, "draft": self.draft,
                "playing": self.playing, "ready": self.program is not None}


@dataclass(frozen=True)
class Status:
    """What the phone's Track card shows. Replaced, never edited."""
    armed: bool
    engaged: bool
    mode: str                       # "timeline", "template", "idle", "fallback"
    reason: Optional[str]           # why the timeline is not driving
    beat: Optional[float]
    lanes: dict
    grabbed: tuple[str, ...]
    policy: str
    problems: int
    first_problem: Optional[str]
    # Templates (milestone 2): the active set, the one waiting for the next
    # downbeat, every set in the folder, and what the template plays now.
    set: Optional[str] = None
    pending: Optional[str] = None
    sets: tuple = ()
    template: Optional[dict] = None

    def public(self) -> dict:
        return {"armed": self.armed, "engaged": self.engaged, "mode": self.mode,
                "reason": self.reason,
                "beat": None if self.beat is None else round(self.beat, 2),
                "bar": None if self.beat is None else int(self.beat // 4) + 1,
                "lanes": dict(self.lanes), "grabbed": list(self.grabbed),
                "policy": self.policy, "problems": self.problems,
                "first_problem": self.first_problem,
                "set": self.set,
                "pending": self.pending if self.pending != NO_SET else "off",
                "sets": [{"id": i, "name": n} for i, n in self.sets],
                "template": self.template}


class TrackPlayer:
    """The runner seam. One per controller."""

    def __init__(self, transport: transportmod.TrackTransport,
                 pinned: Callable[[], object],
                 rigging: Callable[[], blocksmod.Rigging],
                 submit: Callable[..., None], post: Callable[[Callable], None],
                 note: Callable[[str], None], clock_beat: Callable[[], float],
                 base_palette: Callable[[], dict],
                 clock_phrase: Optional[Callable[[], tuple]] = None):
        self.transport = transport
        self._pinned = pinned
        self._rigging = rigging
        self._submit = submit              # worker.submit(fn, done, label)
        self._post = post
        self._note = note
        self._clock_beat = clock_beat
        self._base_palette = base_palette
        # (label, start beat) of the phrase the deck says is playing, in clock
        # beats -- a guest's track, read live (milestone 2).
        self._clock_phrase = clock_phrase or (lambda: (None, None))
        self.armed = False
        self.grabbed: frozenset[str] = frozenset()
        self.policy = "idle"
        self.grace_s = 4.0
        self.idle_routine: Optional[str] = None
        self.program: Optional[programmod.Program] = None
        self._program_for: Optional[tuple] = None      # (track_seq, timeline id)
        self._compiling_for: Optional[tuple] = None
        # A compile that raised -- a bug, since compile reports problems
        # rather than raising. Remembered so it is said once and not retried
        # every frame; a new load of the folder or the rig tries again.
        self._failed_for: Optional[tuple] = None
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
        self.preview: Optional[Preview] = None
        self._preview_jumps = 0
        self._preview_fresh = False
        # -- templates (milestone 2) ----------------------------------------
        self.template = templatesmod.TemplateRunner()
        self.set_id: Optional[str] = None       # the active set
        self.cset: Optional[templatesmod.CompiledSet] = None
        self._cset_key: Optional[tuple] = None
        self._set_chosen = False                # the operator picked one live
        self.sets: tuple = ()                   # (id, name) of every set
        self._library = None
        # The operator's switch, landing on the next downbeat.
        self.pending: Optional[str] = None      # set id, or NO_SET for off
        self._pending_cset: Optional[templatesmod.CompiledSet] = None
        self._pending_ready = False
        self._pending_bar: Optional[int] = None
        # The active set, rebuilt after a folder edit: like any folder change
        # it waits for the next track rather than changing under this one.
        self._next_cset: Optional[templatesmod.CompiledSet] = None
        self._phrases: tuple = (None, None)     # (track_seq, PhraseMap)
        self._held: Optional[float] = None      # a paused clock beat

    # -- settings ----------------------------------------------------------

    def configure(self, show: Optional[dict]) -> None:
        """Take the pause policy and idle routine from show.json."""
        pause = (show or {}).get("pause") or {}
        self.policy = pause.get("policy") if pause.get("policy") in POLICIES \
            else "idle"
        self.grace_s = float(pause.get("grace_s", 4.0))
        self.idle_routine = pause.get("idle_routine")
        if not self._set_chosen:
            default = (show or {}).get("template_set")
            self.set_id = default if isinstance(default, str) and default else None

    def arm(self, armed: bool) -> None:
        self.armed = bool(armed)

    # -- the designer ------------------------------------------------------

    def start_preview(self, preview: Preview) -> None:
        self.preview = preview
        self._preview_fresh = True          # its first frame is a jump

    def stop_preview(self) -> None:
        self.preview = None
        self._seq = None

    def preview_position(self, time_s: float, playing: bool, now: float) -> None:
        pv = self.preview
        if pv is None:
            return
        if abs(time_s - pv.position(now)) > PREVIEW_JUMP_S:
            pv.jumps += 1
        pv.time_s, pv.at, pv.playing = float(time_s), now, bool(playing)

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
        if key in (self._program_for, self._compiling_for, self._failed_for):
            return
        self._compiling_for = key
        timeline = pinned.timeline
        routines = pinned.library.folder.routines if pinned.library else {}
        rigging = self._rigging()
        track = (pinned.match.track_id if pinned.match else "?")

        def build():
            try:
                return programmod.compile(timeline, routines, rigging,
                                          f"timelines/{track}.json")
            except Exception as exc:                        # noqa: BLE001
                return exc

        def done(prog) -> None:
            if self._compiling_for == key:
                self._compiling_for = None
            if isinstance(prog, Exception):
                self._failed_for = key
                self._note(f"{track}: its show could not be built ({prog}); "
                           f"the operator's show runs")
                return
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

    def compile_templates(self, library) -> None:
        """Build the active template set for this rig, on the worker. After a
        folder edit the rebuilt set waits for the next track; at start-up, or
        with nothing playing, it is used at once."""
        self._library = library
        docs = library.folder.templates if library is not None else {}
        self.sets = tuple((sid, str(doc.get("name") or sid))
                          for sid, doc in sorted(docs.items()))
        if self.set_id is not None and self.set_id not in docs:
            self._note(f"template set {self.set_id!r} is not in templates/; "
                       f"templates are off")
            self.set_id = None
        if self.set_id is None:
            self.cset, self._cset_key = None, None
            return
        doc = docs[self.set_id]
        key = (self.set_id, id(doc))
        if key == self._cset_key:
            return
        self._cset_key = key
        set_id, routines, rigging = self.set_id, library.folder.routines, self._rigging()

        def done(cset) -> None:
            if self._cset_key != key:
                return                       # switched or reloaded since
            self._report(cset)
            if self.cset is None or self.template.cue is None:
                self.cset = cset
            else:
                self._next_cset = cset

        self._submit(lambda: templatesmod.compile_set(set_id, doc, routines, rigging),
                     done, f"compiling template set {set_id!r}")

    def _report(self, cset) -> None:
        if cset.problems:
            more = (f" (+{len(cset.problems) - 1} more)"
                    if len(cset.problems) > 1 else "")
            self._note(f"{cset.problems[0]}{more}")

    def select_set(self, set_id: Optional[str]) -> None:
        """The operator chose a set (or None for no templates). It takes over
        on the next downbeat (decided with the user), crossfading over its
        own transition; with nothing playing, at once."""
        if set_id is not None and set_id not in dict(self.sets):
            raise ValueError(f"there is no template set {set_id!r} in templates/")
        self._set_chosen = True
        target = set_id if set_id is not None else NO_SET
        if target == (self.set_id or NO_SET) and self.pending is None:
            return
        self.pending = target
        self._pending_cset, self._pending_ready, self._pending_bar = None, False, None
        if set_id is None:
            self._pending_ready = True
            return
        doc = self._library.folder.templates[set_id]
        routines, rigging = self._library.folder.routines, self._rigging()

        def done(cset) -> None:
            if self.pending != set_id:
                return
            self._report(cset)
            self._pending_cset, self._pending_ready = cset, True

        self._submit(lambda: templatesmod.compile_set(set_id, doc, routines, rigging),
                     done, f"compiling template set {set_id!r}")

    def _switch_if_due(self, beat: Optional[float]) -> Optional[float]:
        """Make a waiting switch, if its downbeat has come. Returns the fade
        to use for a change made now, else None."""
        if not self._pending_ready:
            return None
        if beat is not None and self.template.cue is not None:
            bar = int(beat // templatesmod.BEATS_PER_BAR)
            if self._pending_bar is None:
                self._pending_bar = bar
                on_line = abs(beat - bar * templatesmod.BEATS_PER_BAR) < 1e-6
                if not on_line:
                    return None
            elif bar == self._pending_bar:
                return None
        target, cset = self.pending, self._pending_cset
        self.pending, self._pending_cset = None, None
        self._pending_ready, self._pending_bar = False, None
        self.set_id = None if target == NO_SET else target
        self.cset = cset
        self._next_cset = None
        self._cset_key = ((self.set_id, id(self._library.folder.templates[self.set_id]))
                          if self.set_id and self._library else None)
        return cset.fade if cset is not None else 0.0

    def recompile(self) -> None:
        """The rig changed under the programs: build them again."""
        self.program, self._program_for, self._compiling_for = None, None, None
        self._failed_for = None
        self.compile_for(self._pinned())
        self.cset, self._cset_key, self._next_cset = None, None, None
        self.template.reset()
        if self._library is not None:
            self.compile_templates(self._library)

    # -- each frame, on the output thread ------------------------------------

    def choose(self, fallback: statemod.Show, sample: transportmod.TrackSample,
               now: float) -> statemod.Show:
        """The Show to put on stage this frame: the program's, or `fallback`."""
        if self.preview is not None:
            show, mode, reason, beat = self._decide_preview(fallback, now)
        else:
            show, mode, reason, beat = self._decide(fallback, sample, now)
        self.engaged = mode in ("timeline", "preview", "template")
        prog = (self.preview.program if mode == "preview"
                else self.program if mode == "timeline"
                else self.idle if mode == "idle" else None)
        templating = (mode in ("timeline", "template")
                      and self.template.cue is not None)
        lanes = {}
        for slot in statemod.SLOTS:
            if slot in self.grabbed and self.engaged:
                lanes[slot] = "operator"
            elif prog is not None and beat is not None \
                    and mode in ("timeline", "preview") \
                    and prog.timeline.entries(slot, beat):
                lanes[slot] = "timeline"
            elif templating:
                lanes[slot] = "template"
            elif mode == "idle":
                lanes[slot] = "idle"
            else:
                lanes[slot] = "fallback"
        shown = prog if prog is not None else self.program
        problems = list(shown.problems) if shown is not None else []
        if self.cset is not None:
            problems += self.cset.problems
        self.status = Status(self.armed, self.engaged, mode, reason, beat, lanes,
                             tuple(sorted(self.grabbed)), self.policy,
                             len(problems), problems[0] if problems else None,
                             self.set_id, self.pending, self.sets,
                             self.template.status() if templating else None)
        return show

    def _decide_preview(self, fallback, now):
        pv = self.preview
        if pv.program is None:
            return fallback, "fallback", "preview: no timeline yet", None
        beat = pv.grid.beat_at(pv.position(now))
        jumped = pv.jumps != self._preview_jumps or self._preview_fresh
        self._preview_jumps, self._preview_fresh = pv.jumps, False
        self._seq = None                    # the DJ's next frame is a jump
        show, _, _, beat = self._run(pv.program, fallback, beat, jumped)
        return show, "preview", None, beat

    def _decide(self, fallback, sample, now):
        """The chain (F19): the matched track's timeline, over the template
        set's pick for this phrase, over the operator's or auto mode's show --
        while a DJ track plays and Follow is armed (milestone 2, with the
        user). Otherwise the fallback, and the reason why."""
        pinned = self._pinned()
        self._sample = sample
        if not self.armed or sample is None or sample.state == transportmod.NO_TRACK:
            self.template.reset()
            self._adopt_reloaded()
            self._switch_if_due(None)
            return fallback, "fallback", ("disarmed" if not self.armed
                                          else "no track"), None
        current = pinned is not None and pinned.track_seq == sample.track_seq
        matched = (current and pinned.match is not None
                   and pinned.match.track_id is not None)
        timeline = pinned.timeline if matched else None
        if timeline is None and self.cset is None and not self._pending_ready:
            self.template.reset()
            reason = ("matching" if not current
                      else "not in the show folder" if not matched
                      else "no timeline")
            return fallback, "fallback", reason, None

        new_track = sample.track_seq != self._seq
        jumped = sample.jump_seq != self._jump or new_track
        self._jump, self._seq = sample.jump_seq, sample.track_seq
        if new_track:
            self._adopt_reloaded()

        # -- the pause policy ------------------------------------------------
        paused = sample.state == transportmod.PAUSED
        if paused:
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
        else:
            self._paused_since = None

        # -- the beat: the track's on its grid, else the clock's ---------------
        grid = pinned.grid if matched else None
        on_track = grid is not None and sample.time_s is not None
        if on_track:
            if paused and self.policy == "continue" and self._last_play is not None:
                beat0, t0, bps = self._last_play
                beat, jumped = beat0 + (now - t0) * bps, False
            else:
                # freeze, and idle during its grace: hold where the deck stopped
                beat = grid.beat_at(sample.time_s)
            if sample.state == transportmod.PLAYING:
                bps = grid.bpm_at(sample.time_s) / 60.0 * sample.rate
                self._last_play = (beat, now, bps)
            self._held = None
        else:
            clock = self._clock_beat()
            if paused and self.policy != "continue":
                if self._held is None:
                    self._held = clock
                beat = self._held
            else:
                self._held = None
                beat = clock
        prev = None if jumped else self._beat

        # -- the template ------------------------------------------------------
        fade = self._switch_if_due(beat)
        cue = None
        if self.cset is not None:
            cue = self._template_cue(pinned if matched else None, on_track, beat)
        self.template.grabbed = self.grabbed
        self.template.begin(self.cset, cue, beat, prev=prev, jumped=jumped,
                            fallback=fallback, base_palette=self._base_palette(),
                            fade=fade)
        templated = self.template.cue is not None
        base = self.template.show if templated else fallback

        # -- the timeline, over it -----------------------------------------------
        if timeline is not None:
            key = (pinned.track_seq, id(timeline))
            reason = None
            if self._program_for != key:
                if self._failed_for == key:
                    reason = "compile failed"
                else:
                    self.compile_for(pinned)
                    reason = "compiling"
            elif not on_track:
                reason = "no position"
            if reason is None:
                return self._run(self.program, base, beat, jumped)
            self._beat = beat
            if templated:
                return base, "template", reason, beat
            return fallback, "fallback", reason, None
        self._beat = beat
        if templated:
            return base, "template", None, beat
        return fallback, "fallback", ("no template for this phrase"
                                      if self.cset is not None
                                      else "matching" if not current
                                      else "not in the show folder"
                                      if not matched else "no timeline"), None

    def _template_cue(self, pinned, on_track: bool, beat: float):
        """What the template plays at `beat`: a prepped track's own phrases
        (or bars on its grid); for anything else the deck's live phrase, or
        bars on the clock."""
        cset = self.cset
        if on_track and pinned is not None:
            return templatesmod.cue_in_track(cset, self._phrases_for(pinned), beat)
        label, start = self._clock_phrase()
        if label and start is not None:
            cue = templatesmod.cue_for_phrase(cset, label, start)
            if cue is not None:
                return cue
        return templatesmod.cue_for_bars(cset, beat)

    def _phrases_for(self, pinned) -> Optional[tracktime.PhraseMap]:
        seq, phrases = self._phrases
        if seq == pinned.track_seq:
            return phrases
        phrases = None
        folder = pinned.library.folder if pinned.library else None
        doc = folder.tracks.get(pinned.match.track_id) if folder else None
        items = ((doc or {}).get("phrases") or {}).get("items")
        if items:
            try:
                phrases = tracktime.PhraseMap.from_items(items)
            except tracktime.GridError:
                phrases = None
        self._phrases = (pinned.track_seq, phrases)
        return phrases

    def _adopt_reloaded(self) -> None:
        """A rebuilt active set (a folder edit) waits for a track change."""
        if self._next_cset is not None:
            self.cset, self._next_cset = self._next_cset, None

    def _run(self, prog, fallback, beat, jumped):
        prev = None if jumped else self._beat
        prog.grabbed = self.grabbed
        prog.begin(beat, prev=prev, jumped=jumped, fallback=fallback,
                   base_palette=self._base_palette())
        self._beat = beat
        return prog.show, "timeline", None, beat
