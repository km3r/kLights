"""
The show controller: owns the engine, and every change to it arrives as a
queued command.

One class, composed of one part per thing the console does. Each part is a
module holding that feature's state (its `_init_*`), its commands, the work
behind them, and its own section of the 10 Hz snapshot (its `_public_*`):

    core          the rig, its evaluation context and clock, the runner, the
                  command queue and its replies, the worker, who is connected
    picture       which look is up in each slot, tuning, modulation, stacked
                  moves, auto mode, the palette, shape macros and slot rates
    hand          what the operator sets by hand: master, blackout, panic,
                  color, level and flash per target
    presets       the preset pads, and routines on pads
    cues          the cue list
    calibration   jogging a head, captures, the solver, the drift check
    room          the crowd zone, the canopy, the taper and strobe policy
    patching      the patch editor, and taking a saved rig live
    tempo         tap, BPM, speed and phase, and tempo from a DJ
    follow        the DJ's track, the show folder as loaded, the timeline and
                  templates driving the rig, Studio's preview, other outputs
    showfolder    Studio's writes to the show folder
    looks         the event's look library, and Studio's edits to it

The parts share one object, so a part may read and call another's. What
each part's `_init_*` creates is that part's to write: a preset or a cue
fills the picture's slots through `fill_slot`, sets the master through
`set_master` and holds auto mode through `director.hold` -- the owner's rule,
said once. Taking a rig or a library live (`reload_rig`, `_install_looks`)
is the exception, reconciling every part with what changed under it.
Everything the snapshot reads is rebound by its writer, never edited in
place (`test_snapshot_threads`).

Two structural rules hold across all of them, set by `core`:

  * **Commands go through a queue drained at a frame boundary.** Nothing on a
    server thread mutates the show directly, so a frame is never built from
    two shows.
  * **Slow work runs on the worker** and comes back through `submit_call`,
    to be installed at a frame boundary like any command.

`engine/server.py` is the transport in front of this: HTTP, the WebSocket,
the broadcast thread that serialises the snapshot.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .. import commands as commandsmod
from .. import __version__
from .calibration import Calibration
from .common import Client, DEFERRED
from .core import Core
from .cues import Cues
from .follow import Follow
from .hand import Hand
from .looks import Looks
from .patching import Patching
from .picture import Picture
from .presets import Presets
from .room import Room
from .showfolder import ShowFolder
from .tempo import Tempo

__all__ = ["ShowController", "Client", "DEFERRED"]


class ShowController(Core, Picture, Hand, Presets, Cues, Calibration, Room,
                     Patching, Tempo, Follow, ShowFolder, Looks):
    """Owns the engine. Every mutation arrives as a queued command."""

    def __init__(self, event_dir: Path, artnet: Optional[str] = None,
                 fps: float = 40.0, bpm: float = 124.0,
                 show_dir: Optional[Path] = None,
                 local_outputs: Optional[dict] = None):
        # In this order. Most of these only declare state, but some build
        # what the next needs: the rig and the context before the library,
        # the library and auto mode before the runner that plays them, the
        # worker before anything that submits to it, and the presets before
        # the show folder, whose first load builds the routine pads.
        self.event_dir = Path(event_dir)
        self._init_outputs(local_outputs)
        self._init_engine(bpm)
        self._init_library()
        self._init_auto()
        self._init_runner(artnet, fps)
        self._init_hand()
        self._init_picture()
        self._init_calibration()
        self._init_tempo()
        self._init_follow(show_dir)
        self._init_presets()
        self._open_show_folder()
        self._init_cues()
        self._init_patching()
        self._recompose()

    def snapshot(self) -> dict:
        """Everything the UI needs, built on the caller's thread -- the
        broadcast thread, never the output thread. Each part says its own."""
        return {
            "type": "state",
            "rev": self.rev,
            # So a phone that loaded a stale bundle from cache can be told which
            # engine it is actually driving, rather than the operator guessing.
            "version": __version__,
            **self._public_engine(),
            **self._public_patching(),
            **self._public_room(),
            **self._public_picture(),
            **self._public_looks(),
            **self._public_hand(),
            **self._public_presets(),
            **self._public_cues(),
            **self._public_calibration(),
            **self._public_tempo(),
            **self._public_follow(),
        }


# Every command, by name: what `apply` dispatches on. Built from the
# `@command` declarations on the parts, so a handler and its tier and
# arguments are one thing.
ShowController.COMMANDS = commandsmod.collect(ShowController)
