"""
The room as the safety pass sees it: the crowd zone and the canopy, edited
live at load-in, and the taper policy -- saved back to the venue file, which
may be shared by every show in that room.
"""

from __future__ import annotations

import json
from dataclasses import replace

from .. import config as configmod
from .. import venue as venuemod
from ..commands import CONFIGURE, command
from .common import N, S, _finite


class Room:
    """The crowd zone, the canopy and the taper, live and saved."""

    @command("venue", tier=CONFIGURE, args={
        "crowd": S(dict, of={k: S(N) for k in ("min_x", "max_x", "min_z", "max_z",
                                               "head_band_min", "head_band_max")}),
        "canopy": S(dict, of={"enabled": S(bool), "height": S(N), "radius": S(N)})})
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

        # Finite or refused. Every comparison with NaN is false, so a NaN edge
        # on the crowd zone would put no beam "over the crowd" ever again --
        # the taper switched off, silently, by a slider. And within the venue
        # schema's bounds, so the live venue is always one venue_save can write.
        if "crowd" in m and crowd is not None:
            c = m["crowd"]
            box = crowd.footprint
            band_min = _finite(c.get("head_band_min", crowd.head_band_min), "head_band_min", 0)
            band_max = _finite(c.get("head_band_max", crowd.head_band_max), "head_band_max", 0)
            if band_min >= band_max:
                raise ValueError("head band min must be below max")
            crowd = venuemod.CrowdZone(
                footprint=venuemod.Box(
                    _finite(c.get("min_x", box.min_x), "min_x"),
                    _finite(c.get("max_x", box.max_x), "max_x"),
                    band_min, band_max,
                    _finite(c.get("min_z", box.min_z), "min_z"),
                    _finite(c.get("max_z", box.max_z), "max_z")),
                head_band_min=band_min, head_band_max=band_max)

        if "canopy" in m and canopy is not None:
            k = m["canopy"]
            canopy = replace(canopy,
                             enabled=bool(k.get("enabled", canopy.enabled)),
                             height=_finite(k.get("height", canopy.height), "height", 0),
                             radius=_finite(k.get("radius", canopy.radius), "radius", 0))

        self.rig.venue = replace(venue, crowd_zone=crowd, canopy=canopy)
        self.ctx.venue = self.rig.venue
        self.note("venue updated (not yet saved)")

    @command("taper", tier=CONFIGURE, args={
        "crowd_level": S(N),
        "margin_deg": S(N),
        "slew_per_second": S(N),
        "enabled": S(bool)})
    def _cmd_taper(self, m: dict, now: float) -> None:
        """The taper policy: how far a beam over the crowd is dimmed, and how
        smoothly. `crowd_level` 0 restores a hard guard at the cost of every
        floor-sweep pose."""
        cfg = self.ctx.taper
        # Finite, and inside the venue schema's own bounds, so what is live is
        # always something venue_save can write and the next start can load.
        self.ctx.taper = replace(
            cfg,
            crowd_level=max(0.0, min(1.0, float(m.get("crowd_level", cfg.crowd_level)))),
            margin_deg=max(0.0, min(90.0, float(m.get("margin_deg", cfg.margin_deg)))),
            slew_per_second=max(0.0, float(m.get("slew_per_second", cfg.slew_per_second))),
            enabled=bool(m.get("enabled", cfg.enabled)))
        if not self.ctx.taper.enabled:
            self.note("SAFETY TAPER DISABLED -- beams are no longer dimmed over the crowd")

    @command("venue_save", tier=CONFIGURE)
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

        # Held to the schema the next start will load it with. Without this a
        # live value the schema refuses (a margin of 2**63 from a confused
        # client) was saved, and the NEXT engine in this room would not start.
        configmod.validate(cfg, configmod.VENUE, path)
        configmod.write_json_atomic(path, cfg)
        self.note(f"saved {path.name}")

    def _public_room(self) -> dict:
        """The room's safety policy, and the room."""
        return {
            "strobe_policy": {
                "enabled": self.ctx.strobe_policy.enabled,
                "ceiling": self.ctx.strobe_policy.ceiling,
                "max_seconds": self.ctx.strobe_policy.max_seconds,
            },
            "venue": venue_summary(self.rig.venue),
            "taper": {"crowd_level": self.ctx.taper.crowd_level,
                      "margin_deg": self.ctx.taper.margin_deg,
                      "slew_per_second": self.ctx.taper.slew_per_second,
                      "enabled": self.ctx.taper.enabled},
        }


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
