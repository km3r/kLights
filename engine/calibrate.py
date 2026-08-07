"""
Fast re-aim: teach the engine where the heads are actually pointing.

Replaces the Tkinter form in `events/despacio/aim_calc_gui.py`, and the workflow
around it -- eyeball each head at the ball, read the faders, type eight numbers
in, re-run the build script, restart QLC+. The lights got nudged overnight at
despacio and recalibrating was slow and manual. This is the fix.

Three things it does that the one-point-per-head model could not:

**It derives the invert flags instead of asking for them.** A single "aimed at
the ball" reading cannot tell you which way a head rotates as DMX increases: the
encode and decode share the sign, so the maths is self-consistent for either
value. That is a physical fact about the fixture, and it needs two aims at
*different bearings* to pin down. It shipped wrong once already -- an inter-head
pose pointed at the wrong fixture (2026-07-23). With three captures the solver
tries all four sign combinations and reports which one actually fits.

**It can catch a wrong channel range.** The `.qxf` claims 540 deg of Pan. If the
real fixture does 520, everything still calibrates perfectly at the ball and
drifts further off the further an aim gets from it -- which looks like random
inaccuracy rather than a systematic error. Given enough bearing spread the
solver fits the scale too.

**It says when your captures cannot answer the question.** Aiming at the ball
and then at the floor directly beneath it gives two captures at the *same*
bearing, which determines nothing about bearing handedness. The solver detects
that and says so, rather than returning a confident coin-flip.

The output is still an ordinary per-head `ball_dmx` reading plus invert flags --
the same shape `calibration.json` already holds and `geometry.Head` already
consumes. The multi-point solve is a better way to *produce* that, not a
different model to maintain alongside it.
"""

from __future__ import annotations

import json
import math
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Sequence

from . import geometry as geo
from .venue import Venue

SNAPSHOT_DIR = "calibration_history"

# Below this spread between captures, a channel's handedness (or scale) is not
# determined by the data and the solver refuses to guess. 25 deg is comfortably
# more than the few degrees of aiming slop involved in pointing a beam by eye.
MIN_BEARING_SPREAD_DEG = 25.0
MIN_ELEV_SPREAD_DEG = 10.0

# Scale is a much weaker signal than handedness -- it only shows up as a
# *proportional* error, so it needs a wide baseline before the fit means
# anything. Below this, report scale as 1.0 and say why.
MIN_SCALE_SPREAD_DEG = 60.0

# ...and even with the baseline, a fit this close to 1 is quantisation, not a
# discovery. An operator dials 8 bits, which is 0.39% of a 540 deg channel per
# step, so a few tenths of a percent falls out of perfect data. Reporting it
# would invite someone to "correct" a range that was already right.
SCALE_DEADBAND = 0.02


@dataclass(frozen=True)
class Capture:
    """One "I pointed this head at that, and these were the numbers" record.

    `pan`/`tilt` are the 0-255 readings as dialled, exactly what the operator
    sees on a fader or a phone slider. The target is a world point.
    """
    target: tuple[float, float, float]
    pan: int
    tilt: int
    label: str = ""

    def to_json(self) -> dict:
        return {"target": list(self.target), "pan": self.pan, "tilt": self.tilt,
                "label": self.label}

    @staticmethod
    def from_json(data: dict) -> "Capture":
        return Capture(target=tuple(float(c) for c in data["target"]),
                       pan=int(data["pan"]), tilt=int(data["tilt"]),
                       label=data.get("label", ""))


@dataclass
class Solution:
    """What the captures say about one head."""
    head_name: str
    mount_facing: float
    elevation_offset: float
    pan_invert: bool
    tilt_invert: bool
    ball_dmx: tuple[int, int]
    residual_deg: float
    bearing_spread_deg: float
    elev_spread_deg: float
    bearing_scale: float = 1.0
    warnings: list[str] = field(default_factory=list)
    determined: dict[str, bool] = field(default_factory=dict)

    @property
    def confident(self) -> bool:
        return not self.warnings

    def as_calibration_entry(self) -> dict:
        return {"fixture": self.head_name,
                "ball_dmx": list(self.ball_dmx),
                "pan_invert": self.pan_invert,
                "tilt_invert": self.tilt_invert}


# ------------------------------------------------------------------ solver --

def _frame_for(head: geo.Head, ball, mount_mode: str, pan_invert: bool,
               tilt_invert: bool) -> geo.RigGeometry:
    """A one-head rig with the given signs, used to borrow geometry's encode
    and decode rather than reimplementing them here. Reimplementing them is
    exactly how the two halves of this transform drift apart."""
    probe = geo.Head(**{**head.__dict__, "calibrated_ball_dmx": None,
                        "pan_invert": pan_invert, "tilt_invert": tilt_invert})
    return geo.RigGeometry(heads=(probe,), ball=ball, mount_mode=mount_mode)


def _unwrap_near(value: float, reference: float) -> float:
    """`value` shifted by whole turns to sit closest to `reference`."""
    return value + 360.0 * round((reference - value) / 360.0)


def solve_head(head: geo.Head, ball: tuple[float, float, float], mount_mode: str,
               captures: Sequence[Capture], fit_scale: bool = True) -> Solution:
    """Fit one head's transform from its captures.

    Tries all four sign combinations and keeps the best-fitting one. That is the
    whole trick: with captures at two different bearings, the wrong bearing sign
    makes the implied mount facing disagree between captures by roughly twice
    their angular separation, which no amount of offset can absorb. With
    captures at one bearing it absorbs perfectly, and the solver says so instead
    of picking.
    """
    if len(captures) < 2:
        raise ValueError(
            f"{head.name}: need at least 2 captures at different bearings to "
            f"solve; got {len(captures)}. One capture can only be used as a "
            f"plain ball calibration, with the invert flags supplied by hand.")

    # How much the captures actually span. Everything below is only as
    # trustworthy as these two numbers.
    world = []
    for cap in captures:
        horiz = math.hypot(cap.target[0] - head.x, cap.target[2] - head.z)
        world.append((geo.bearing_between(head.x, head.z, cap.target[0], cap.target[2]),
                      math.degrees(math.atan2(cap.target[1] - head.height, horiz))))
    bearings = [w[0] for w in world]
    elevs = [w[1] for w in world]
    bearing_spread = max(abs(geo.norm180(a - b)) for a in bearings for b in bearings)
    elev_spread = max(elevs) - min(elevs)

    best: Optional[Solution] = None
    for pan_invert in (False, True):
        for tilt_invert in (False, True):
            candidate = _fit_signs(head, ball, mount_mode, captures, world,
                                   pan_invert, tilt_invert, bearing_spread,
                                   elev_spread, fit_scale)
            if best is None or candidate.residual_deg < best.residual_deg:
                best = candidate

    assert best is not None
    warnings: list[str] = []
    determined = {"bearing_sign": True, "elevation_sign": True, "scale": True}

    profile = geo.MOUNT_PROFILES[mount_mode]
    bearing_is_pan = profile["bearing_channel"] == "pan"

    if bearing_spread < MIN_BEARING_SPREAD_DEG:
        determined["bearing_sign"] = False
        warnings.append(
            f"captures span only {bearing_spread:.1f} deg of bearing, so which "
            f"way {'pan' if bearing_is_pan else 'tilt'} turns is NOT determined "
            f"-- aim at something off to one side and capture again")
    if elev_spread < MIN_ELEV_SPREAD_DEG:
        determined["elevation_sign"] = False
        warnings.append(
            f"captures span only {elev_spread:.1f} deg of elevation, so which "
            f"way {'tilt' if bearing_is_pan else 'pan'} tips is NOT determined "
            f"-- capture one aim clearly higher or lower")
    if best.bearing_scale == 1.0 and fit_scale:
        determined["scale"] = False

    # A large residual with good spread means the captures disagree with each
    # other, not that a sign is ambiguous -- usually a mistyped reading or a
    # target that was not actually where it was said to be.
    if best.residual_deg > 3.0 and bearing_spread >= MIN_BEARING_SPREAD_DEG:
        warnings.append(
            f"captures disagree by {best.residual_deg:.1f} deg RMS even at the "
            f"best-fitting signs -- check for a mistyped reading, or a capture "
            f"where the beam was not really on its target")

    best.warnings = warnings
    best.determined = determined
    return best


def _fit_signs(head, ball, mount_mode, captures, world, pan_invert, tilt_invert,
               bearing_spread, elev_spread, fit_scale) -> Solution:
    """Fit mount facing, elevation offset and optionally bearing scale, holding
    the sign hypothesis fixed."""
    rig = _frame_for(head, ball, mount_mode, pan_invert, tilt_invert)
    frame = rig.frame(0)
    profile = geo.MOUNT_PROFILES[mount_mode]
    res = rig.resolution

    raw_bearings, raw_elevs = [], []
    for cap in captures:
        vals = {"pan": cap.pan << 8, "tilt": cap.tilt << 8}
        raw_bearings.append(geo.from_dmx_centered(
            vals[profile["bearing_channel"]], frame.bearing_max,
            frame.bearing_invert, res))
        raw_elevs.append(geo.decode_elev(
            vals[profile["elevation_channel"]], frame.elevation_max,
            frame.elevation_invert, profile["elevation_anchor"], res))

    # Bearing: world_bearing = mount_facing + scale * raw_delta.
    #
    # Unwrap each world bearing onto the branch nearest what a unit scale would
    # predict. Scale is expected within a few percent of 1, so this is safe, and
    # it is the only way to fit at all -- the raw delta is deliberately NOT
    # reduced mod 360 (it can legitimately reach +-270 on a 540 deg channel), so
    # the two sides live on different branches until this lines them up.
    anchor = world[0][0] - raw_bearings[0]
    targets = [_unwrap_near(world[i][0], anchor + raw_bearings[i])
               for i in range(len(captures))]

    scale = 1.0
    if fit_scale and len(captures) >= 3 and bearing_spread >= MIN_SCALE_SPREAD_DEG:
        n = len(captures)
        mean_x = sum(raw_bearings) / n
        mean_y = sum(targets) / n
        sxx = sum((x - mean_x) ** 2 for x in raw_bearings)
        sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(raw_bearings, targets))
        if sxx > 1e-9:
            fitted = sxy / sxx
            # Reject an implausible fit rather than adopting it. A scale far
            # from 1 means the captures are wrong, not that the fixture has a
            # secret gear ratio. And ignore a fit inside the deadband, which is
            # quantisation dressed up as a measurement.
            if 0.8 <= fitted <= 1.25 and abs(fitted - 1.0) >= SCALE_DEADBAND:
                scale = fitted

    facings = [t - scale * r for t, r in zip(targets, raw_bearings)]
    mount_facing = sum(facings) / len(facings)

    offsets = [raw - geom for raw, (_b, geom) in zip(raw_elevs, world)]
    elevation_offset = sum(offsets) / len(offsets)

    residual = math.sqrt(
        (sum((f - mount_facing) ** 2 for f in facings)
         + sum((o - elevation_offset) ** 2 for o in offsets)) / (2 * len(captures)))

    # Express the fit as the ball reading that would produce it, so the result
    # drops straight into calibration.json with no new model to maintain.
    bearing_delta_ball = (frame.bearing_to_ball - mount_facing) / scale
    bearing_val = geo.to_dmx_centered(bearing_delta_ball, frame.bearing_max,
                                      frame.bearing_invert, res)
    elev_val = geo.encode_elev(frame.elev_to_ball + elevation_offset,
                               frame.elevation_max, frame.elevation_invert,
                               profile["elevation_anchor"], res)
    by_channel = {profile["bearing_channel"]: bearing_val,
                  profile["elevation_channel"]: elev_val}

    return Solution(
        head_name=head.name, mount_facing=mount_facing,
        elevation_offset=elevation_offset,
        pan_invert=pan_invert, tilt_invert=tilt_invert,
        ball_dmx=(geo.split16(by_channel["pan"])[0],
                  geo.split16(by_channel["tilt"])[0]),
        residual_deg=residual, bearing_spread_deg=bearing_spread,
        elev_spread_deg=elev_spread, bearing_scale=scale)


# ------------------------------------------------------------------- drift --

@dataclass(frozen=True)
class Drift:
    """How far one head has moved since it was last calibrated."""
    head_name: str
    bearing_deg: float
    elevation_deg: float
    pan_dmx: int
    tilt_dmx: int

    @property
    def magnitude(self) -> float:
        return math.hypot(self.bearing_deg, self.elevation_deg)

    @property
    def significant(self) -> bool:
        """Above roughly a beam width, where it starts to be visible on a
        distant target. Below it, this is aiming slop rather than movement."""
        return self.magnitude >= 3.0


def drift_for_head(head: geo.Head, ball, mount_mode: str,
                   observed_ball_dmx: tuple[int, int]) -> Drift:
    """Compare a fresh "aimed at the ball" reading against the stored one.

    The morning go/no-go. Reported in DEGREES, not DMX, for three reasons --
    none of which is "identical heads convert differently", because they do not:
    degrees per DMX step is a property of the fixture and the mount mode, so
    four of the same head in the same mode share it exactly.

      * It is comparable across fixture TYPES. A club rig mixing a 540 deg head
        with a 360 deg one has two different degrees-per-step, and a DMX delta
        cannot be compared between them at all.
      * It carries the right SIGN. Heads 2 and 4 here are mounted mirrored, so
        the same DMX nudge tips them opposite real-world ways. Degrees say that;
        DMX hides it.
      * It is directly comparable to the BEAM WIDTH, which is what decides
        whether a drift is visible on target. `significant` uses that.
    """
    stored = head.calibrated_ball_dmx
    if stored is None:
        raise ValueError(f"{head.name} has no stored calibration to compare against")

    rig = geo.RigGeometry(heads=(head,), ball=ball, mount_mode=mount_mode)
    frame = rig.frame(0)
    profile = geo.MOUNT_PROFILES[mount_mode]

    def decode(reading):
        vals = {"pan": reading[0] << 8, "tilt": reading[1] << 8}
        return (geo.from_dmx_centered(vals[profile["bearing_channel"]],
                                      frame.bearing_max, frame.bearing_invert,
                                      rig.resolution),
                geo.decode_elev(vals[profile["elevation_channel"]],
                                frame.elevation_max, frame.elevation_invert,
                                profile["elevation_anchor"], rig.resolution))

    old_b, old_e = decode(stored)
    new_b, new_e = decode(observed_ball_dmx)
    return Drift(head_name=head.name,
                 bearing_deg=geo.norm180(new_b - old_b),
                 elevation_deg=new_e - old_e,
                 pan_dmx=observed_ball_dmx[0] - stored[0],
                 tilt_dmx=observed_ball_dmx[1] - stored[1])


# --------------------------------------------------------------- snapshots --

def snapshot_dir(event_dir: Path) -> Path:
    return Path(event_dir) / SNAPSHOT_DIR


def save_snapshot(event_dir: Path, calibration: dict,
                  note: str = "", when: Optional[datetime] = None) -> Path:
    """Archive a calibration, timestamped.

    This is what makes an overnight nudge a DELTA rather than a from-scratch
    re-aim: last night's numbers are still on disk, so the question becomes
    "what moved, and by how much" instead of "what were they again".
    """
    when = when or datetime.now()
    out = snapshot_dir(event_dir)
    out.mkdir(exist_ok=True)
    payload = dict(calibration)
    payload["snapshot_taken"] = when.isoformat(timespec="seconds")
    if note:
        payload["note"] = note

    # Second resolution collides more easily than it looks: `solve --write`
    # archives the old calibration and the new one back to back, and both landed
    # in the same second, so the second silently overwrote the first -- losing
    # the "before" snapshot, which is the only reason to take one. Suffix rather
    # than overwrite.
    stem = when.strftime("%Y-%m-%dT%H-%M-%S")
    path = out / f"{stem}.json"
    serial = 2
    while path.exists():
        path = out / f"{stem}-{serial}.json"
        serial += 1

    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


STAMP_LEN = len("YYYY-MM-DDTHH-MM-SS")


def _snapshot_order(stem: str) -> tuple[str, int]:
    """Sort key for a snapshot filename: (timestamp, serial).

    Not the raw name. A same-second snapshot gets a `-2` suffix, and by string
    comparison "...-05-2" sorts BEFORE "...-05" because '-' is below '.' -- so a
    plain reverse sort silently hands back the pair in the wrong order and the
    diff runs backwards.
    """
    stamp, _, serial = stem[:STAMP_LEN], "", stem[STAMP_LEN:].lstrip("-")
    return (stamp, int(serial) if serial.isdigit() else 1)


def load_snapshots(event_dir: Path) -> list[tuple[str, dict]]:
    """(timestamp, calibration) newest first."""
    out = snapshot_dir(event_dir)
    if not out.is_dir():
        return []
    rows = []
    for path in sorted(out.glob("*.json"), key=lambda p: _snapshot_order(p.stem),
                       reverse=True):
        try:
            rows.append((path.stem, json.loads(path.read_text(encoding="utf-8"))))
        except json.JSONDecodeError:
            continue
    return rows


def readings_of(calibration: dict) -> dict[str, tuple[int, int]]:
    return {h["fixture"]: tuple(h["ball_dmx"])
            for h in calibration.get("heads", [])
            if h.get("ball_dmx") is not None}


def diff_calibrations(heads: Sequence[geo.Head], ball, mount_mode: str,
                      older: dict, newer: dict) -> list[Drift]:
    """What moved between two calibrations, per head, in degrees."""
    old_readings, new_readings = readings_of(older), readings_of(newer)
    drifts = []
    for head in heads:
        if head.name not in old_readings or head.name not in new_readings:
            continue
        reference = geo.Head(**{**head.__dict__,
                                "calibrated_ball_dmx": old_readings[head.name]})
        drifts.append(drift_for_head(reference, ball, mount_mode,
                                     new_readings[head.name]))
    return drifts


# --------------------------------------------------------------------- CLI --
#
# The web form (F8) will call the functions above; this exists so the workflow
# is usable at a load-in before that form does. Everything here is a thin shell
# over the library -- no logic lives down here.

def _parse_readings(args: Sequence[str]) -> list[tuple[int, int]]:
    out = []
    for token in args:
        pan, _, tilt = token.partition(",")
        out.append((int(pan), int(tilt)))
    return out


def _print_drifts(drifts: Sequence[Drift]) -> int:
    moved = 0
    for d in drifts:
        flag = "MOVED" if d.significant else "  ok "
        moved += d.significant
        print(f"  [{flag}] {d.head_name:<16} "
              f"{d.bearing_deg:+7.2f} deg bearing  "
              f"{d.elevation_deg:+7.2f} deg elevation  "
              f"(DMX {d.pan_dmx:+d}/{d.tilt_dmx:+d})")
    return moved


def main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    from .rig import load_rig

    repo = Path(__file__).resolve().parent.parent
    parser = argparse.ArgumentParser(
        description="Calibrate, check drift, and snapshot a rig's aim")
    parser.add_argument("--event", type=Path, default=repo / "events" / "despacio")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="stored calibration and what it implies")

    p_drift = sub.add_parser(
        "drift", help="compare fresh ball readings against the stored ones")
    p_drift.add_argument("readings", nargs="+", metavar="PAN,TILT",
                         help="one per head, in rig order")

    p_solve = sub.add_parser(
        "solve", help="solve from a captures file (see --example)")
    p_solve.add_argument("captures", type=Path, nargs="?")
    p_solve.add_argument("--example", action="store_true",
                         help="print a captures file template and exit")
    p_solve.add_argument("--write", action="store_true",
                         help="write the result to calibration.json, snapshotting first")

    p_snap = sub.add_parser("snapshot", help="archive the current calibration")
    p_snap.add_argument("--note", default="")

    sub.add_parser("history", help="list snapshots")

    p_diff = sub.add_parser("diff", help="compare two snapshots (default: latest two)")
    p_diff.add_argument("older", nargs="?")
    p_diff.add_argument("newer", nargs="?")

    p_jog = sub.add_parser(
        "jog", help="hold one head at literal DMX so you can aim it by eye")
    p_jog.add_argument("--head", type=int, default=0, help="index in rig order")
    p_jog.add_argument("--pan", type=int, required=True)
    p_jog.add_argument("--tilt", type=int, required=True)
    p_jog.add_argument("--artnet", metavar="IP")
    p_jog.add_argument("--seconds", type=float, default=20.0)

    args = parser.parse_args(argv)

    rig = load_rig(args.event)
    if rig.geometry is None or rig.venue is None:
        print(f"{args.event} has no positioned heads", file=sys.stderr)
        return 2
    heads = rig.geometry.heads
    ball = rig.venue.ball
    mode = rig.geometry.mount_mode
    cal_path = args.event / "calibration.json"
    calibration = (json.loads(cal_path.read_text(encoding="utf-8"))
                   if cal_path.exists() else {"heads": []})

    if args.command == "status":
        print(f"{rig.name}: {len(heads)} heads, mount_mode={mode}, "
              f"measured {calibration.get('measured', 'unknown')}")
        for i, head in enumerate(heads):
            frame = rig.geometry.frame(i)
            print(f"  [{i}] {head.name:<16} ball {head.calibrated_ball_dmx}  "
                  f"facing {frame.mount_facing:7.1f} deg  "
                  f"elev offset {frame.elevation_offset:+7.1f} deg  "
                  f"invert pan={head.pan_invert} tilt={head.tilt_invert}")
        snaps = load_snapshots(args.event)
        print(f"  {len(snaps)} snapshot(s)"
              + (f", latest {snaps[0][0]}" if snaps else ""))
        return 0

    if args.command == "drift":
        readings = _parse_readings(args.readings)
        if len(readings) != len(heads):
            print(f"expected {len(heads)} readings, got {len(readings)}",
                  file=sys.stderr)
            return 2
        print(f"drift against {calibration.get('measured', 'the stored calibration')}:")
        drifts = [drift_for_head(h, ball, mode, r) for h, r in zip(heads, readings)]
        moved = _print_drifts(drifts)
        print(f"\n{moved} head(s) moved significantly (>= 3 deg).")
        return 1 if moved else 0

    if args.command == "solve" and args.example:
        # Targets are filled in from the venue, per head, so the operator only
        # types readings.
        #
        # The choice matters more than it looks. Three targets clustered in one
        # direction leave the handedness undetermined however carefully they are
        # aimed, and the obvious picks -- ball, far corner, far wall -- are all
        # roughly the same direction from a corner head, spanning under 20 deg.
        # The floor beneath each ADJACENT corner is about 90 deg either side of
        # the ball from a head in a corner, which spans the room's whole usable
        # bearing range, and being on the floor it also spans elevation against
        # the ball. If the solver still complains, believe it.
        venue = rig.venue
        template = {}
        for head in heads:
            template[head.name] = [
                {"label": "mirror ball", "target": list(ball), "pan": 0, "tilt": 0},
                {"label": "floor under the adjacent corner (across the room)",
                 "target": [head.x, 0.0, venue.depth - head.z], "pan": 0, "tilt": 0},
                {"label": "floor under the adjacent corner (along the wall)",
                 "target": [venue.width - head.x, 0.0, head.z], "pan": 0, "tilt": 0},
            ]
        print(json.dumps({
            "_comment": [
                "Aim each head at each target by eye and record the two 0-255",
                "readings into 'pan' and 'tilt'. Targets are pre-filled from",
                "venue.json -- change them if you aim at something else, and",
                "keep the coordinates honest, because the solver believes them.",
                "",
                "Three targets per head, spanning bearing AND elevation. Two at",
                "the same bearing cannot determine which way the head turns;",
                "the solver will say so rather than guess.",
                "",
                "Then: python -m engine.calibrate solve captures.json --write",
            ],
            "mount_mode": mode,
            "heads": template,
        }, indent=2))
        return 0

    if args.command == "solve":
        if args.captures is None:
            print("give a captures file, or --example for a template",
                  file=sys.stderr)
            return 2
        data = json.loads(args.captures.read_text(encoding="utf-8"))
        solve_mode = data.get("mount_mode", mode)
        entries = []
        for head in heads:
            raw = data.get("heads", {}).get(head.name)
            if not raw:
                print(f"  {head.name}: no captures, keeping stored calibration")
                entries.append({"fixture": head.name,
                                "ball_dmx": list(head.calibrated_ball_dmx)
                                if head.calibrated_ball_dmx else None,
                                "pan_invert": head.pan_invert,
                                "tilt_invert": head.tilt_invert})
                continue
            captures = [Capture.from_json(c) for c in raw]
            solution = solve_head(head, ball, solve_mode, captures)
            print(f"  {solution.head_name}: ball {solution.ball_dmx}  "
                  f"pan_invert={solution.pan_invert} tilt_invert={solution.tilt_invert}  "
                  f"residual {solution.residual_deg:.2f} deg  "
                  f"spread {solution.bearing_spread_deg:.0f}/{solution.elev_spread_deg:.0f} deg")
            if solution.bearing_scale != 1.0:
                channel = geo.MOUNT_PROFILES[solve_mode]["bearing_channel"]
                nominal = (head.pan_range_deg if channel == "pan"
                           else head.tilt_range_deg)
                measured = solution.bearing_scale * nominal
                print(f"    bearing range measures {measured:.0f} deg, not the "
                      f"{nominal:.0f} deg the .qxf claims. Aims far from the ball "
                      f"stay off until this is recorded -- add to this fixture in "
                      f"rig.json:")
                print(f'        "{channel}_range_deg": {measured:.0f}')
            for warning in solution.warnings:
                print(f"    WARNING: {warning}")
            entries.append(solution.as_calibration_entry())

        if args.write:
            save_snapshot(args.event, calibration, note="before solve")
            payload = {"measured": datetime.now().strftime("%Y-%m-%d"),
                       "mount_mode": solve_mode,
                       "source": f"solved from {args.captures.name}",
                       "heads": entries}
            cal_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
            save_snapshot(args.event, payload, note="after solve")
            print(f"\nwrote {cal_path} (previous archived in {SNAPSHOT_DIR}/)")
        else:
            print("\nnothing written -- pass --write to apply")
        return 0

    if args.command == "snapshot":
        path = save_snapshot(args.event, calibration, note=args.note)
        print(f"wrote {path}")
        return 0

    if args.command == "history":
        snaps = load_snapshots(args.event)
        if not snaps:
            print(f"no snapshots in {snapshot_dir(args.event)}")
            return 0
        for stamp, data in snaps:
            note = data.get("note", "")
            print(f"  {stamp}  measured={data.get('measured', '?'):<12} {note}")
        return 0

    if args.command == "diff":
        snaps = load_snapshots(args.event)
        if len(snaps) < 2 and not (args.older and args.newer):
            print("need at least two snapshots", file=sys.stderr)
            return 2
        by_stamp = dict(snaps)
        older = by_stamp[args.older] if args.older else snaps[1][1]
        newer = by_stamp[args.newer] if args.newer else snaps[0][1]
        print(f"{args.older or snaps[1][0]}  ->  {args.newer or snaps[0][0]}")
        moved = _print_drifts(diff_calibrations(heads, ball, mode, older, newer))
        print(f"\n{moved} head(s) moved significantly (>= 3 deg).")
        return 1 if moved else 0

    if args.command == "jog":
        from . import state as statemod
        from .output import ArtNetOutput, NullOutput
        from .runner import Runner

        if not 0 <= args.head < len(heads):
            print(f"head must be 0..{len(heads) - 1}", file=sys.stderr)
            return 2
        target = rig.movers[args.head]

        ctx = statemod.EvalContext(rig=rig, venue=rig.venue)
        show = statemod.Show()
        show.base.append(statemod.raw_pose_layer(
            {target.name: (args.pan, args.tilt)}, intensity=1.0))
        show.color.append(statemod.color_layer((1.0, 1.0, 1.0)))

        output = ArtNetOutput(args.artnet) if args.artnet else NullOutput()
        runner = Runner(ctx=ctx, show=show, output=output)
        print(f"holding {target.name} at pan={args.pan} tilt={args.tilt} "
              f"for {args.seconds:.0f}s")
        print("SAFETY TAPER IS BYPASSED in raw jog -- do this in an empty room.")
        print("Aim it by eye, then record the readings as a capture.")
        try:
            runner.run(seconds=args.seconds)
        except KeyboardInterrupt:
            pass
        finally:
            runner.stop()
            output.close()
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
