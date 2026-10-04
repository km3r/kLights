"""The cue list: a named, ordered walk through the night.

The QLC+ show had one. Its six Collections -- Warm Up, Despacio Idle, Deep,
Spiral, Peak, Landing -- were the actual shape of the set, and the porter
skipped every one of them with the reason "Collection -- rebuild with motion
primitives". They have been missing ever since, which makes this the one live
regression from the old console rather than a new feature.

A cue is **the same three slots a preset is**, plus a fade and an optional
hold. That is deliberate: a cue list built on its own private notion of a look
would be a second way to say the same thing, and the two would drift. So a cue
is a preset with somewhere to go next.

    { "name": "Deep", "movement": {...}, "color": {...},
      "fade": 8, "hold": 64 }

`fade` and `hold` are in BEATS, not seconds. Everything an operator authors in
this engine is musical, and a cue list that ignored tempo would be the one
surface that drifts out of the music it is cueing.

What this is NOT is an automation system. `hold` is what lets a cue advance on
its own; leave it out and the cue waits for GO. The night this is modelled on
was driven by hand off a controller, and the reason it worked is that the
operator decided when the drop was.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Sequence

from . import config as configmod


@dataclass(frozen=True)
class Cue:
    """One step of the night."""
    name: str
    movement: dict[str, str] = field(default_factory=dict)
    color: dict[str, str] = field(default_factory=dict)
    level: dict[str, str] = field(default_factory=dict)
    # Beats to crossfade in. 0 is a hard cut, which is a legitimate choice for a
    # drop and a bad one for everything else.
    fade: float = 8.0
    # Beats to sit here before advancing on its own. 0 means wait for GO, which
    # is the default because the operator deciding when the drop happens is the
    # reason the original show worked.
    hold: float = 0.0
    # Live shape macros to restore with the cue, when it wants a particular
    # size or spread. Absent means "leave whatever is dialled in alone", so a
    # cue list does not fight an operator mid-set.
    macro: Optional[dict] = None
    # Per-slot chase rates, same rule as `macro`: absent leaves whatever is
    # dialled in alone. A cue that wants the colours crawling while the movers
    # run flat out says so here rather than needing a look stored at that rate.
    rates: Optional[dict] = None
    # Per-routine tuning, by look name: `{"Ball Orbit": {"radius_deg": 24}}`.
    #
    # Absent leaves every routine alone, like `macro` and `rates` -- which is
    # what a cue written before this existed means, and what one with no opinion
    # about tuning means. Present, it is EXHAUSTIVE over the looks this cue
    # names: a routine the cue names but does not list goes back to its authored
    # values. Without that, taking a cue could leave a radius from three cues
    # ago on stage, and a cue list whose result depends on the route you took
    # through it is not a cue list.
    params: Optional[dict] = None
    speed: Optional[float] = None
    master: Optional[float] = None
    notes: str = ""

    @property
    def auto_advance(self) -> bool:
        return self.hold > 0


@dataclass
class CueList:
    """The cues, and where in them we are.

    `index` is -1 before the first GO. That is not the same as being on cue 0:
    a show that has not started must not claim to be in the middle of one, and
    the UI shows "next: Warm Up" rather than "now: Warm Up".
    """
    name: str
    cues: list[Cue] = field(default_factory=list)
    index: int = -1
    # Musical time the current cue was taken, so a hold knows when it expires.
    entered_beat: float = 0.0

    @property
    def current(self) -> Optional[Cue]:
        if 0 <= self.index < len(self.cues):
            return self.cues[self.index]
        return None

    @property
    def next(self) -> Optional[Cue]:
        nxt = self.index + 1
        return self.cues[nxt] if nxt < len(self.cues) else None

    def go(self, beat: float) -> Optional[Cue]:
        """Advance one cue. Stops at the end rather than wrapping.

        Not a loop, on purpose. A set list that silently restarts at the first
        cue after the last one turns "the show ended" into "the show quietly
        went back to the warm-up", and the second is much harder to notice from
        behind a console.
        """
        if self.index + 1 >= len(self.cues):
            return None
        self.index += 1
        self.entered_beat = beat
        return self.current

    def back(self, beat: float) -> Optional[Cue]:
        if self.index <= 0:
            return None
        self.index -= 1
        self.entered_beat = beat
        return self.current

    def jump(self, index: int, beat: float) -> Optional[Cue]:
        if not 0 <= index < len(self.cues):
            raise ValueError(
                f"no cue {index} -- this list has {len(self.cues)}")
        self.index = index
        self.entered_beat = beat
        return self.current

    def reset(self) -> None:
        self.index = -1
        self.entered_beat = 0.0

    def due(self, beat: float) -> bool:
        """Has the current cue's hold expired?"""
        cue = self.current
        return bool(cue and cue.auto_advance
                    and beat - self.entered_beat >= cue.hold)

    def status(self) -> dict:
        cue, nxt = self.current, self.next
        return {
            "name": self.name,
            "index": self.index,
            "count": len(self.cues),
            "current": cue.name if cue else None,
            "next": nxt.name if nxt else None,
            "auto_advance": bool(cue and cue.auto_advance),
            "cues": [{"name": c.name, "fade": c.fade, "hold": c.hold,
                      "notes": c.notes} for c in self.cues],
        }


CUES_SCHEMA = {
    "name": configmod.Spec(str),
    "cues": configmod.Spec(list, required=True, non_empty=True,
                           each=configmod.Spec(dict, of={
        "name": configmod.Spec(str, required=True, non_empty=True),
        "movement": configmod.Spec(dict), "color": configmod.Spec(dict),
        "level": configmod.Spec(dict),
        "fade": configmod.Spec(configmod.Number, min=0,
                               fix="beats to crossfade in, not seconds"),
        "hold": configmod.Spec(configmod.Number, min=0,
                               fix="beats before advancing on its own. "
                                   "0 waits for GO"),
        "speed": configmod.Spec(configmod.Number, min=0),
        "master": configmod.Spec(configmod.Number, min=0, max=1),
        "macro": configmod.Spec(dict),
        "rates": configmod.Spec(dict,
            fix="per-slot chase rates, e.g. {\"color\": 0.5, \"movement\": 2}"),
        "params": configmod.Spec(dict,
            fix="per-routine parameter values, by look name, e.g. "
                "{\"Ball Orbit\": {\"radius_deg\": 24}}"),
        "notes": configmod.Spec(str),
    })),
}


def load(path: Path) -> CueList:
    cfg = configmod.load(Path(path), CUES_SCHEMA)
    cues = [Cue(name=c["name"],
                movement=dict(c.get("movement", {})),
                color=dict(c.get("color", {})),
                level=dict(c.get("level", {})),
                fade=float(c.get("fade", 8.0)),
                hold=float(c.get("hold", 0.0)),
                macro=c.get("macro"),
                rates=c.get("rates"),
                params=c.get("params"),
                speed=(None if c.get("speed") is None else float(c["speed"])),
                master=(None if c.get("master") is None else float(c["master"])),
                notes=c.get("notes", ""))
            for c in cfg["cues"]]
    return CueList(name=cfg.get("name", Path(path).stem), cues=cues)


def missing_looks(cue_list: CueList, known: Sequence[str]) -> list[str]:
    """Looks a cue names that the library does not have.

    Reported rather than raised. A cue list outlives any one port of the
    library, and a set list that refuses to load because one of forty looks was
    renamed is worse at 4pm than one that loads and says which cue is short.
    """
    have = set(known)
    out = []
    for cue in cue_list.cues:
        for slot in (cue.movement, cue.color, cue.level):
            for group, look in slot.items():
                if look not in have:
                    out.append(f"{cue.name!r} wants {look!r} for {group!r}, "
                               f"which is not in the library")
    return out
