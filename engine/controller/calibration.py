"""
Calibrating the heads from the Setup tab: jog a head at raw DMX until it is
on a target, capture it, solve for a calibration from the captures, and check
for drift against the one stored. Jogging steps around the safety taper,
which is why every command here is `configure`.
"""

from __future__ import annotations

import json
import time
from typing import Optional

from .. import calibrate as calibmod
from .. import config as configmod
from ..commands import CONFIGURE, command
from .common import N, S, _NAME


# A solve fitting worse than this is not written over a working calibration.
# Comfortably above the 0.67 deg worst case the solver hits on clean captures,
# and well below the tens of degrees a mistaken capture produces.
MAX_WRITE_RESIDUAL_DEG = 5.0


class Calibration:
    """Jog, capture, solve, and the drift check."""

    def _init_calibration(self) -> None:
        self.jog: dict[str, tuple[int, int]] = {}
        self.captures: dict[str, list[calibmod.Capture]] = {}
        self.last_drift: Optional[list[dict]] = None

    @command("jog", tier=CONFIGURE, args={
        "fixture": _NAME,
        "pan": S(N, required=True),
        "tilt": S(N, required=True)})
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

    @command("jog_clear", tier=CONFIGURE, args={"fixture": S(str)})
    def _cmd_jog_clear(self, m: dict, now: float) -> None:
        self.jog.pop(m["fixture"], None) if "fixture" in m else self.jog.clear()
        self._rebuild_overrides()

    @command("capture", tier=CONFIGURE, args={
        "fixture": _NAME,
        "target": S(list, required=True, each=S(N), fix="[x, y, z] in millimetres"),
        "label": S(str),
        "pan": S(N),
        "tilt": S(N)})
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

    @command("capture_clear", tier=CONFIGURE, args={"fixture": S(str)})
    def _cmd_capture_clear(self, m: dict, now: float) -> None:
        self.captures.pop(m["fixture"], None) if "fixture" in m else self.captures.clear()

    @command("solve", tier=CONFIGURE, args={"write": S(bool)})
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
        # Not loaded here, for the same reason a patch edit is not: a new
        # calibration moves every look that aims at something, and when that
        # happens is the operator's call. Apply now loads it, live.
        self._saved_not_live(path.name)
        self.note(f"wrote {path.name} -- not live yet. Apply it to load it into "
                  f"the running show")

    @command("drift", tier=CONFIGURE, args={
        "readings": S(list, each=S(list, each=S(N)), fix="one [pan, tilt] per head, in rig order")})
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

    def _public_calibration(self) -> dict:
        """The last drift check."""
        return {
            "drift": self.last_drift,
        }
