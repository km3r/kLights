"""
Load a ported look library into runnable looks.

`looks.json` is produced by `shared/tools/port_library.py` from the QLC+
workspace. This turns each entry into an `auto.Look` -- a factory that takes the
current palette colour and returns a layer stack.

The five kinds map onto the engine's layers rather than onto QLC+'s flat
namespace, which is the whole gain from the port:

  pose        a held position, as offsets from each head's own ball aim
  path        a route through several positions, interpolated over N bars
  color       a colour, uniform or per fixture
  color_path  a stepped colour sequence (a wheel has no in-between slots)
  intensity   a level

Because they are separate, a pose and a colour COMPOSE. In the workspace every
combination had to be its own stored scene, which is how 179 accumulated and why
only a handful got used; here the same material recombines freely.

Offsets are relative to each head's calibrated ball aim, so every ported look
tracks recalibration automatically. That is a property the stored DMX could not
have had, and it is why re-aiming a nudged head no longer invalidates the
library.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import auto as autom
from . import geometry as geo
from . import motion
from . import state as statemod

EASINGS = {"linear": motion.linear, "ease_in_out": motion.ease_in_out,
           "ease_out": motion.ease_out}


@dataclass(frozen=True)
class LibraryEntry:
    """One ported look, as data. Kept separate from the runnable `auto.Look` so
    the UI can list and group the library without building every layer stack."""
    name: str
    kind: str
    tags: tuple[str, ...]
    offsets: Optional[list[list[float]]] = None
    steps: Optional[list[list[list[float]]]] = None
    frames: Optional[list[dict[str, list[float]]]] = None
    color: Optional[list[float]] = None
    colors: Optional[dict[str, list[float]]] = None
    bars: Optional[float] = None
    intensity: Optional[float] = None
    source: str = ""

    @property
    def is_movement(self) -> bool:
        return self.kind in ("pose", "path", "mixed")

    @property
    def is_color(self) -> bool:
        return self.kind in ("color", "color_path", "mixed")


def load_entries(path: Path) -> list[LibraryEntry]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for raw in data.get("looks", []):
        out.append(LibraryEntry(
            name=raw["name"], kind=raw["kind"], tags=tuple(raw.get("tags", [])),
            offsets=raw.get("offsets"), steps=raw.get("steps"),
            frames=raw.get("frames"), color=raw.get("color"),
            colors=raw.get("colors"), bars=raw.get("bars"),
            intensity=raw.get("intensity"), source=raw.get("source", "")))
    return out


# ------------------------------------------------------------------ layers --

def pose_offsets(offsets: list[list[float]]):
    """A held position: each head sits at its own stored offset.

    Indexed by head, so a rig with more heads than the library was authored for
    wraps rather than failing. Wrapping is the least surprising thing an 8-head
    club rig can do with a 4-head look -- the alternative is refusing to run it.
    """
    def offset_for(ctx, head: int) -> tuple[float, float]:
        pair = offsets[head % len(offsets)]
        return (pair[0], pair[1])
    return offset_for


def path_offsets(steps: list[list[list[float]]], bars: float,
                 easing=motion.ease_in_out):
    """A route: per head, interpolate through that head's column of the steps.

    The transpose matters. The port stores steps as [step][head] because that is
    how a chaser reads; `motion.path` wants one head's whole route, so each head
    gets its own path built from its own column.
    """
    per_head = [motion.path([tuple(step[h % len(step)]) for step in steps],
                            easing=easing)
                for h in range(len(steps[0]))]

    def offset_for(ctx, head: int) -> tuple[float, float]:
        return per_head[head % len(per_head)](motion.phase(ctx.motion_bar, bars))
    return offset_for


def color_frames_layer(frames: list[dict[str, list[float]]], bars: float):
    """A stepped colour sequence, held per step rather than interpolated."""
    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        index = int(motion.phase(ctx.motion_bar, bars) * len(frames)) % len(frames)
        for fixture in ctx.rig.fixtures:
            rgb = frames[index].get(fixture.name)
            if rgb is not None:
                out[fixture.fid].color = (rgb[0], rgb[1], rgb[2])
    return layer


def per_fixture_color_layer(colors: dict[str, list[float]]):
    def layer(ctx: statemod.EvalContext, out: dict) -> None:
        for fixture in ctx.rig.fixtures:
            rgb = colors.get(fixture.name)
            if rgb is not None:
                out[fixture.fid].color = (rgb[0], rgb[1], rgb[2])
    return layer


# ------------------------------------------------------------------- looks --

def build_look(entry: LibraryEntry, base_bars: float = 8.0) -> autom.Look:
    """One library entry as a runnable look.

    Every look aims at the ball first and then applies its own offsets, so a
    colour-only entry still produces a usable picture rather than leaving the
    heads wherever the last look left them. That is a change from the workspace,
    where selecting a colour deliberately left position alone -- here the layer
    order gives that composability back without the look having to be partial.
    """
    def make(palette_color: tuple[float, float, float]) -> statemod.Show:
        show = statemod.Show()
        show.base.append(statemod.pose_layer(
            lambda ctx, head: ctx.geometry.aim_at_ball(head), tags=("movers",),
            intensity=entry.intensity if entry.intensity is not None else 1.0))
        show.base.append(statemod.on_layer(
            entry.intensity if entry.intensity is not None else 0.7,
            tags=("pinspots",)))

        if entry.color is not None:
            show.color.append(statemod.color_layer(tuple(entry.color)))
        elif entry.colors is not None:
            show.color.append(per_fixture_color_layer(entry.colors))
        elif entry.frames is not None:
            show.color.append(color_frames_layer(entry.frames,
                                                 entry.bars or base_bars))
        else:
            show.color.append(statemod.color_layer(palette_color))

        if entry.offsets is not None:
            show.movement.append(statemod.move_layer(
                pose_offsets(entry.offsets), tags=("movers",)))
        elif entry.steps is not None:
            show.movement.append(statemod.move_layer(
                path_offsets(entry.steps, entry.bars or base_bars),
                tags=("movers",)))

        show.fx.append(autom.energy_intensity_layer())
        return show

    # Maintenance and one-shot entries stay reachable by hand but must never be
    # selected by a timer -- the same reasoning as a blackout in the set list.
    manual_only = entry.kind == "intensity" or "reset" in entry.name.lower()
    return autom.Look(name=entry.name, make=make, manual_only=manual_only)


def load_setlist(path: Path) -> tuple[autom.SetList, list[LibraryEntry]]:
    entries = load_entries(path)
    if not entries:
        raise ValueError(f"{path} contains no looks")
    return autom.SetList([build_look(e) for e in entries]), entries


def encode_entry(rig_geo: geo.RigGeometry, entry: LibraryEntry,
                 head: int) -> Optional[tuple[int, int]]:
    """The DMX a pose entry produces for one head.

    Used by the round-trip test to prove a ported look still aims where the
    workspace aimed. Only meaningful for `pose`; a path has no single position.
    """
    if entry.offsets is None:
        return None
    d_bearing, d_elev = entry.offsets[head % len(entry.offsets)]
    return rig_geo.encode(head, rig_geo.aim_offset(head, d_bearing, d_elev))
