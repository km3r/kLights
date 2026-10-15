"""
The patch editor, and taking a saved rig live. An edit is written to rig.json
at once and does not touch the running show: `patch_apply` (`reload_rig`)
swaps the saved rig in at a frame boundary, and keeps the old rig if the new
one does not load.
"""

from __future__ import annotations

from .. import config as configmod
from .. import patch as patchmod
from .. import rig as rigmod
from ..commands import CONFIGURE, command
from .common import N, S, _NAME, _TAGS, _XYZ
from .core import rig_reach


class Patching:
    """Patch edits, and reloading the rig without a restart."""

    def _init_patching(self) -> None:
        # Profiles, resolved once. The patch editor needs to size a fixture's
        # channel footprint to answer "does this address clash", and rescanning
        # shared/fixtures/ per keystroke on a phone is not the way.
        self.profiles = rigmod.ProfileLibrary()
        # The files written that the running show is not using yet: rig.json
        # from a patch edit, calibration.json from a solve. The UI turns this
        # into a standing banner with Apply now, because a rig that is saved
        # and not loaded is exactly the state where the file and the rig
        # disagree and nothing on screen says so. Apply (`reload_rig`) loads
        # both, since the rig is read from both.
        self.pending_files: list[str] = []

    @property
    def pending_patch(self) -> bool:
        """Something is saved that the running show is not using."""
        return bool(self.pending_files)

    def _saved_not_live(self, name: str) -> None:
        if name not in self.pending_files:
            self.pending_files.append(name)

    def _patch(self, edit) -> None:
        """Apply one patch edit to rig.json and report it.

        Writes immediately rather than staging, because the alternative is a
        second notion of "the patch" living in the server that the file does not
        agree with, and a half-applied patch is worse than either state.

        It does NOT take effect on the running show by itself. Re-deriving
        profiles, channel offsets and head indices in the middle of an edit
        would change what every layer is writing to underneath a look that is
        up, so the edit is only saved and `pending_files` says so. Applying it
        (`patch_apply` -> `reload_rig`) swaps the saved rig in at a frame
        boundary, with no restart.

        Note this is the one writer that does not check the lockfile. The lock
        exists to stop an OUTSIDE tool editing behind a running engine's back;
        this is that engine, and it is telling the operator what it did.
        """
        cfg = patchmod.load_rig_config(str(self.event_dir))
        result = edit(cfg, self.profiles)
        for problem in result.errors:
            self.note(f"patch refused: {problem}")
        if not result.ok:
            return
        for warning in result.warnings:
            self.note(f"patch: {warning}")
        patchmod.write_rig(str(self.event_dir), result.config)
        self._saved_not_live("rig.json")
        self.note("patch saved to rig.json -- not live yet. Apply it to load it "
                  "into the running show")

    @command("patch_apply", tier=CONFIGURE)
    def _cmd_patch_apply(self, m: dict, now: float) -> None:
        self.reload_rig()

    def reload_rig(self) -> bool:
        """Adopt the rig currently on disk, without restarting the show.

        Runs on the output thread at a frame boundary, like every other command,
        which is the whole reason this is possible: nothing is halfway through
        evaluating a frame, so the swap cannot produce one built from two
        different rigs.

        **The old rig keeps running if the new one is bad.** Loading and
        validating happen before anything is adopted, so a typo in rig.json --
        or a fixture whose .qxf has gone missing -- costs a red notice rather
        than the show. That ordering is the only thing here that must not be
        rearranged.

        Three pieces of derived state have to go with it:

          * `latest_states` is keyed by fixture id, and an id can now mean a
            different fixture.
          * `_taper_prev` likewise -- and it is seeded to 0 rather than cleared,
            so intensity ramps up under the safety slew limiter instead of
            snapping to whatever the new geometry computes. The limiter that
            exists to stop a beam flashing as it crosses the crowd turns out to
            be exactly the right crossfade for a rig swap, which is why there is
            no separate fade here.
          * The composed Show, since layers resolve fixtures through the rig.

        What it deliberately does NOT do is re-point anything by itself. A pose
        is an offset from a head's calibrated ball aim, so if the reload changed
        which heads exist, every look moves -- correctly, but visibly. The
        caller is told when that is the case.
        """
        try:
            new_rig = rigmod.load_rig(self.event_dir, self.profiles)
        except (configmod.ConfigError, FileNotFoundError, ValueError) as exc:
            self.note(f"reload refused, still running the old rig: {exc}")
            return False
        errors = new_rig.validate()
        if errors:
            self.note("reload refused, still running the old rig: "
                      + "; ".join(errors[:3]))
            return False

        old_heads = tuple(h.name for h in self.rig.geometry.heads) \
            if self.rig.geometry else ()
        new_heads = tuple(h.name for h in new_rig.geometry.heads) \
            if new_rig.geometry else ()

        self.rig = new_rig
        # A re-patch can change which heads exist and how far each travels, so
        # what the centre macro may span is worked out again with the rig.
        self.reach = rig_reach(new_rig)
        self.ctx.rig = new_rig
        self.ctx.venue = new_rig.venue
        self.latest_states = {}
        # Seeded dark, not cleared: an empty dict means "no previous value", and
        # apply_safety then skips the rate limit and adopts the computed taper
        # instantly. Starting at 0 makes the limiter fade it in.
        self.ctx._taper_prev = {f.fid: 0.0 for f in new_rig.fixtures}
        self._prune_targets()
        # Measured against the rig that was just replaced. A moved head decodes
        # the same DMX to different degrees, so the old rows would show "ok"
        # for a head nobody has checked since.
        self.last_drift = None
        self._recompose()
        calibrated = "calibration.json" in self.pending_files
        self.pending_files = []
        if self.player is not None:
            # Programs are built against fixtures; the old ones are dropped at
            # once (the operator's show runs) and rebuilt for the new rig.
            self.player.idle = None
            self.player.recompile()
            if self.show_library is not None:
                self.player.compile_idle(self.show_library)
            self._clear_pad()
            self._compile_pads()
            self._rematch_decks()

        if old_heads != new_heads:
            self.note(f"rig reloaded, and the moving heads CHANGED "
                      f"({len(old_heads)} -> {len(new_heads)}). Every pose is an "
                      f"offset from a head's calibrated ball aim, so check the "
                      f"calibration before trusting one: Drift check, on Setup")
        else:
            self.note(f"rig reloaded live -- {len(new_rig.fixtures)} fixtures, "
                      f"no restart needed")
        if calibrated:
            self.note("the new calibration is live: every look aims from it")
        for warning in new_rig.warnings():
            self.note(f"rig: {warning}")
        return True

    def _prune_targets(self) -> None:
        """Drop operator state naming something the rig no longer has.

        Everything cleared alongside the rig swap is keyed by fixture ID. This
        is the other half: trims, colors, flashes, jogs and captures are keyed
        by NAME, and a patch edit can rename or delete the thing they name.
        Left alone they are invisible -- a trim with no row to reset it, and a
        `jog` entry that would silently re-bypass the safety taper if a fixture
        with that name ever came back.

        A target is NOT just a fixture name: `all`, and every tag, are equally
        valid and are how a group gets dimmed. Pruning on fixture names alone
        would throw away the group overrides, which is the common case and
        would look exactly like the console forgetting what it was doing.
        """
        names = {f.name for f in self.rig.fixtures}
        valid = {"all", *self.rig.tags()} | names
        targeted = (set(self.color_overrides) | set(self.white_overrides)
                    | set(self.level_overrides) | self.flashing)
        per_fixture = set(self.jog) | set(self.captures)
        dropped = sorted((targeted - valid) | (per_fixture - names))
        if not dropped:
            return
        self.color_overrides = {k: v for k, v in self.color_overrides.items()
                                if k in valid}
        self.white_overrides = {k: v for k, v in self.white_overrides.items()
                                if k in valid}
        self.level_overrides = {k: v for k, v in self.level_overrides.items()
                                if k in valid}
        self.flashing = self.flashing & valid
        self.jog = {k: v for k, v in self.jog.items() if k in names}
        self.captures = {k: v for k, v in self.captures.items() if k in names}
        # "targets", not "fixtures": removing the last fixture carrying a tag
        # takes the tag with it, so a group trim can be dropped here too, and
        # saying "fixture" would send someone looking for a fixture by that name.
        self.note("dropped settings for targets the rig no longer has: "
                  + ", ".join(dropped[:6]))

    @command("patch_add", tier=CONFIGURE, args={
        "name": _NAME,
        "manufacturer": _NAME,
        "model": _NAME,
        "mode": _NAME,
        "address": S(int),
        "universe": S(int),
        "tags": _TAGS,
        "position": _XYZ,
        "beam_deg": S(N),
        "notes": S(str)})
    def _cmd_patch_add(self, m: dict, now: float) -> None:
        self._patch(lambda cfg, lib: patchmod.add_fixture(
            cfg, name=m["name"], manufacturer=m["manufacturer"],
            model=m["model"], mode=m["mode"], address=m.get("address"),
            universe=int(m.get("universe", 0)), tags=m.get("tags", ()),
            position=m.get("position"), beam_deg=m.get("beam_deg"),
            notes=m.get("notes", ""), lib=lib))

    @command("patch_remove", tier=CONFIGURE, args={"name": _NAME})
    def _cmd_patch_remove(self, m: dict, now: float) -> None:
        self._patch(lambda cfg, lib: patchmod.remove_fixture(
            cfg, m["name"], lib=lib))

    @command("patch_address", tier=CONFIGURE, args={
        "name": _NAME,
        "address": S(int, required=True),
        "universe": S(int)})
    def _cmd_patch_address(self, m: dict, now: float) -> None:
        self._patch(lambda cfg, lib: patchmod.set_address(
            cfg, m["name"], int(m["address"]), m.get("universe"), lib=lib))

    @command("patch_tags", tier=CONFIGURE, args={"name": _NAME, "tags": _TAGS})
    def _cmd_patch_tags(self, m: dict, now: float) -> None:
        self._patch(lambda cfg, lib: patchmod.set_tags(
            cfg, m["name"], list(m.get("tags", [])), lib=lib))

    @command("patch_position", tier=CONFIGURE, args={
        "name": _NAME,
        "position": S(dict, required=True, of=_XYZ.of)})
    def _cmd_patch_position(self, m: dict, now: float) -> None:
        pos = m["position"]
        self._patch(lambda cfg, lib: patchmod.set_position(
            cfg, m["name"], float(pos["x"]), float(pos["y"]), float(pos["z"]),
            lib=lib))

    @command("patch_autopatch", tier=CONFIGURE, args={
        "start": S(int),
        "universe": S(int)})
    def _cmd_patch_autopatch(self, m: dict, now: float) -> None:
        self._patch(lambda cfg, lib: patchmod.autopatch(
            cfg, universe=m.get("universe"), start=int(m.get("start", 1)),
            lib=lib))

    def _public_patching(self) -> dict:
        """What is saved and not live yet, and what can be patched."""
        return {
            # An edit is on disk that the running show is not using. A patch
            # or a calibration saved and not loaded is precisely the state
            # where the file and the rig disagree, so it gets a standing banner
            # rather than a notice that scrolls away. `pending_files` says
            # which, so the banner shows where that edit was made.
            "pending_patch": self.pending_patch,
            "pending_files": list(self.pending_files),
            "profiles": patchmod.list_profiles(self.profiles),
            "warnings": self.rig.warnings(),
        }
