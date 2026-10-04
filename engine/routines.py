"""
A routine, instantiated: one use of a reusable routine, bound to a rig.

A routine document (`showfiles.ROUTINE`) is written once and used everywhere --
as a timeline clip, from a template, on a pad. Each USE says three things the
routine leaves open:

- **params**: values for its open parameters. Lowest to highest: the
  parameter's default, then the use's `variation`, then the use's own
  `params`. A colour parameter takes a palette role (`"@primary"`), a hex
  colour, `[r, g, b]`, or a colour look's name. Above all three, per frame:
  the routine's own `param.<name>` lane, read in its own beats, and above
  that the timeline's (`blocks.Env.param`).
- **bind**: which rig tag plays each ROLE. Unbound, a role plays its default
  tag. A role marked `optional` that finds no fixtures on this rig is simply
  absent; a required one is a warning, and its rows do nothing.
- where it sits, which the compiler handles (`program.py`).

What comes out is its rows as a `timeline.Timeline` in ROUTINE-LOCAL beats --
the same core a track's timeline uses, so overlaps, fades and hits inside a
routine behave exactly as they do on a track -- plus one built block
(`blocks.py`) per item.

**Length and looping.** A looping routine repeats every `bars`. One that does
not loop keeps running its END once it gets there (decided with the user,
F19h): its rows are read at their last moment, its automation holds its final
values, and its motion carries on -- a build that ends tight and fast stays
tight and fast until the clip ends.

A routine that names a `rig` other than this event's is "this rig only" and
says so: its looks were ported from somewhere else.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Mapping, Optional

from . import blocks as blocksmod
from . import rig as rigmod
from . import showfiles
from . import timeline as timelinemod
from . import tracktime

EPS = 1e-6


@dataclass
class Instance:
    """One use of a routine on one rig."""
    id: str
    length: float                           # beats
    loop: bool
    timeline: timelinemod.Timeline          # its rows, in routine-local beats
    blocks: dict[tuple[str, str], blocksmod.Block]   # (row id, item id) -> block
    hit_fixtures: dict[tuple[str, str], frozenset[int]]
    roles: dict[str, tuple[rigmod.PatchedFixture, ...]]
    env: blocksmod.Env
    problems: list[str] = field(default_factory=list)
    # Routine-local beat the compiler last placed it at (`sched`), which its
    # own param lanes are read at.
    now: float = 0.0

    def sched(self, local: float) -> float:
        """Where in its own rows a routine is, `local` beats after it
        started: wrapped if it loops, held at its last moment if not."""
        if self.length <= 0:
            return 0.0
        if self.loop:
            return local % self.length
        return min(max(local, 0.0), self.length - EPS)

    def item_start(self, local: float, item_at: float) -> float:
        """When, in beats since the routine started, the current pass of an
        item began -- what its motion phase counts from."""
        if self.loop and self.length > 0:
            return math.floor(local / self.length) * self.length + item_at
        return item_at

    def curve(self, target: str) -> Optional[timelinemod.Curve]:
        entry = self.timeline.curves.get(target)
        return entry[1] if entry else None

    def param_lane(self, name: str) -> Any:
        """Its own `param.<name>` lane at `now`, or None if it has none."""
        curve = self.curve(f"param.{name}")
        return blocksmod.automation_value(curve, self.now) if curve else None

    @property
    def fixtures(self) -> frozenset[int]:
        """Every fixture any of its roles is bound to."""
        return frozenset(f.fid for fs in self.roles.values() for f in fs)


def resolve_params(doc: Mapping, use: Mapping, rigging: blocksmod.Rigging,
                   where: str) -> tuple[dict[str, Any], list[str]]:
    """Default, then variation, then the use's own values."""
    problems: list[str] = []
    defs = {k: v for k, v in (doc.get("params") or {}).items()
            if isinstance(v, dict)}
    values = {name: p.get("default") for name, p in defs.items()}
    variation = use.get("variation")
    if variation:
        chosen = (doc.get("variations") or {}).get(variation)
        if not isinstance(chosen, dict):
            problems.append(f"{where}: routine {doc.get('id')!r} has no variation "
                            f"{variation!r}; using its defaults")
        else:
            values.update(chosen)
    for name, value in (use.get("params") or {}).items():
        if name not in defs:
            problems.append(f"{where}: routine {doc.get('id')!r} has no param "
                            f"{name!r}")
            continue
        values[name] = value
    looks = rigging.look_colors()
    for name, value in list(values.items()):
        if value is None:
            continue
        problem = showfiles._param_value_problem(defs.get(name), value)
        if problem is None and defs.get(name, {}).get("type") == "color":
            problem = blocksmod.color_problem(value, looks, values)
        if problem:
            problems.append(f"{where} param {name}: {problem}")
            values[name] = defs.get(name, {}).get("default")
    return values, problems


def bind_roles(doc: Mapping, use: Mapping, rigging: blocksmod.Rigging,
               where: str) -> tuple[dict[str, tuple], list[str]]:
    problems: list[str] = []
    bind = use.get("bind") or {}
    roles: dict[str, tuple] = {}
    for name, spec in (doc.get("roles") or {}).items():
        tag = bind.get(name) or (spec or {}).get("default")
        fixtures = rigging.tagged(tag) if isinstance(tag, str) else ()
        if not fixtures and not (spec or {}).get("optional"):
            problems.append(f"{where}: role {name!r} ({tag!r}) has no fixtures "
                            f"on this rig, so its rows do nothing")
        roles[name] = fixtures
    for name in bind:
        if name not in roles:
            problems.append(f"{where}: binds role {name!r}, which routine "
                            f"{doc.get('id')!r} does not have")
    return roles, problems


def instantiate(doc: Mapping, use: Mapping, rigging: blocksmod.Rigging,
                where: str = "") -> Instance:
    """Bind a routine document to a rig for one use. Never raises for a
    problem in the routine: problems are listed, and whatever cannot be built
    claims nothing, so the lane below shows through."""
    rid = str(doc.get("id", "?"))
    where = where or f"routine {rid!r}"
    params, problems = resolve_params(doc, use, rigging, where)
    roles, more = bind_roles(doc, use, rigging, where)
    problems += more
    rig_name = doc.get("rig")
    if rig_name and rigging.event and rig_name != rigging.event:
        problems.append(f"{where}: routine {rid!r} was built for rig {rig_name!r} "
                        f"(this rig only) -- its looks may not exist here")
    env = blocksmod.Env(params, rigging.look_colors())
    length = float(doc.get("bars") or 0) * tracktime.BEATS_PER_BAR
    try:
        timeline = timelinemod.Timeline.from_rows(
            doc.get("rows") or (), channels=lambda row: (row["target"],))
    except timelinemod.TimelineError as exc:
        problems.append(f"{where}: {exc}")
        timeline = timelinemod.Timeline.from_rows(())
    built: dict[tuple[str, str], blocksmod.Block] = {}
    hit_fixtures: dict[tuple[str, str], frozenset[int]] = {}
    everyone = frozenset(f.fid for fs in roles.values() for f in fs)
    for row in timeline.clip_rows:
        slot = row.channels[0]
        source = next(r for r in doc["rows"] if r.get("id") == row.id)
        for item in row.items:
            role = item.data.get("role") or source.get("role")
            fixtures = roles.get(role, ())
            block = blocksmod.make(
                str(item.data.get("block")), item.data.get("args") or {},
                fixtures, slot, env, rigging,
                where=f"{where} row {row.id!r} item {item.id!r}")
            problems += block.problems
            built[(row.id, item.id)] = block
    for row in timeline.hit_rows:
        for item in row.items:
            role = item.data.get("role")
            hit_fixtures[(row.id, item.id)] = (
                frozenset(f.fid for f in roles.get(role, ())) if role else everyone)
    inst = Instance(rid, length, bool(doc.get("loop", True)), timeline, built,
                    hit_fixtures, roles, env, problems)
    env.lanes = inst.param_lane
    return inst
