"""Everything that should be green before you leave for a venue.

    python scripts/preflight.py
    python scripts/preflight.py --event despacio

One command, because a checklist you have to remember the items of is a
checklist you will half-run at 4pm with the van loaded. Exit 0 means go.

Written in Python rather than as two shell scripts for the same reason the
engine is: it runs identically on the Windows machine this is developed on and
on whatever laptop ends up at the desk, and it needs nothing installed.

What it does NOT check is the rig itself -- that it is plugged in, addressed,
and pointing where the calibration says. `python -m engine.calibrate drift`
does that, and it has to happen at the venue with the lamps on.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent


class Step:
    """One check. `optional` means a missing tool skips it rather than failing
    the run -- Node is not installed at a venue and must not be needed there."""

    def __init__(self, name: str, cmd: list[str], cwd: Path | None = None,
                 optional: bool = False, needs: str | None = None):
        self.name, self.cmd, self.cwd = name, cmd, cwd or REPO
        self.optional, self.needs = optional, needs


def bundle_matches_source() -> tuple[bool, str]:
    """The check CI exists for, run locally: does the committed ui/dist match a
    build from the committed source? Rebuilds first, so it also catches a bundle
    that was simply never rebuilt after a UI edit."""
    # Skip, don't fail, when the toolchain to check with is absent. A show
    # laptop has neither Node nor node_modules and does not need them -- the
    # bundle is committed precisely so it does not. Reporting NOT READY there
    # would be crying wolf, and a preflight that cries wolf gets ignored, which
    # costs more than the check is worth.
    npm = shutil.which("npm")
    if npm is None:
        return True, "skipped -- npm not installed, and a venue does not need it"
    if not (REPO / "ui" / "node_modules").is_dir():
        return True, ("skipped -- ui/node_modules absent. Run `cd ui && npm ci` "
                      "to enable this check on a machine that builds the UI")
    # Compare the bundle on disk against a rebuild, NOT against HEAD. What gets
    # served at the venue is the directory, so "is the directory current" is the
    # question; git status also reports staged-but-uncommitted, which made this
    # fail for a bundle that was perfectly correct. CI asks the other question --
    # does the COMMITTED bundle match the committed source -- and that is the
    # right one there, where a fresh clone is what runs.
    def fingerprint() -> dict:
        dist = REPO / "ui" / "dist"
        return {p.relative_to(dist).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in sorted(dist.rglob("*")) if p.is_file()}

    before = fingerprint()
    # The resolved path, not the bare name and not shell=True. On Windows npm is
    # npm.cmd, which CreateProcess will not find from "npm" -- `which` resolves
    # it. shell=True was the earlier fix for that, and on POSIX it turns a list
    # into `sh -c npm run build`, where "run" and "build" become $0 and $1: bare
    # npm prints its usage, exits 1, and the check failed on every Linux and Mac.
    build = subprocess.run([npm, "run", "build"], cwd=REPO / "ui",
                           capture_output=True, text=True)
    if build.returncode != 0:
        return False, (build.stderr or build.stdout or "").strip()[-400:]
    after = fingerprint()
    if before != after:
        changed = sorted(set(before) ^ set(after)) or \
            [k for k in after if before.get(k) != after[k]]
        return False, ("ui/dist was stale -- a rebuild changed it. It is correct "
                       "now; commit it as one set:\n"
                       "    git add -A ui/dist\n  " + "\n  ".join(changed))
    return True, "the bundle on disk matches a fresh build"


def check_rig(event_dir: Path) -> int:
    """Load the event's rig the way a fresh clone will, then validate it.

    Profiles come from shared/fixtures/ only. The engine also searches
    ~/QLC+/Fixtures and the gitignored qlcplus/ tree, so a .qxf that lives in
    one of those loads on the machine that has it and fails on every other one,
    the show laptop included -- which is how the pinspot went missing until
    2026-08-06. Loading with the engine's own search order would pass on
    exactly the machine this gets run on before leaving.

    This replaced despacio's "fixture def installed in QLC+" check. That one
    guarded a second copy going stale, and failed outright wherever QLC+ was not
    installed. The engine has no second copy -- it reads the repo's -- so the
    only way left to be wrong is for the repo not to have it.
    """
    sys.path.insert(0, str(REPO))
    from engine import config as configmod, rig

    # Root 0 is shared/fixtures/, "first and always" -- see rig.py.
    in_repo = rig.ProfileLibrary(rig.QXF_SEARCH_ROOTS[:1])
    cfg =configmod.load(event_dir / "rig.json", configmod.RIG)
    missing: dict[tuple[str, str], list[str]] = {}
    for entry in cfg["fixtures"]:
        key = (entry["manufacturer"], entry["model"])
        if in_repo.get(*key) is None:
            missing.setdefault(key, []).append(entry["name"])
    if missing:
        # Say where each one IS coming from on this machine, so the fix is one
        # command rather than a hunt.
        elsewhere = rig.ProfileLibrary(rig.QXF_SEARCH_ROOTS[1:])
        for (manufacturer, model), names in sorted(missing.items()):
            found = elsewhere.get(manufacturer, model)
            print(f"{', '.join(names)}: {manufacturer} {model!r} is not in "
                  f"shared/fixtures/" + (
                      f" -- it loads here only from {found.path}, which a "
                      f"fresh clone does not have. Copy it in:\n"
                      f"    python -m engine.patch import \"{found.path}\""
                      if found else " or anywhere else on this machine"))
        return 1

    r = rig.load_rig(event_dir, in_repo)
    errors = r.validate()
    if errors:
        print("\n".join(errors))
        return 1
    print(f"{len(r.fixtures)} fixtures, {len(r.movers)} movers, ok")
    return 0


def rig_step(event_dir: Path) -> Step:
    # Its own process, like every other step, so a broken rig cannot take the
    # runner down with it.
    return Step("rig loads and validates",
                [sys.executable, str(Path(__file__).resolve()),
                 "--check-rig", str(event_dir)])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-venue checks")
    parser.add_argument("--event", default="despacio")
    parser.add_argument("--skip-tests", action="store_true",
                        help="skip the engine suites (they take ~15s)")
    parser.add_argument("--check-rig", metavar="EVENT_DIR",
                        help=argparse.SUPPRESS)   # what rig_step runs
    args = parser.parse_args(argv)

    if args.check_rig:
        return check_rig(Path(args.check_rig))

    event_dir = REPO / "events" / args.event
    if not event_dir.is_dir():
        print(f"no such event: {event_dir}", file=sys.stderr)
        return 2

    steps: list[Step] = []
    if not args.skip_tests:
        steps.append(Step("engine test suites",
                          [sys.executable, "-m", "engine.tests"]))
    steps += [
        Step("schemas match config.py",
             [sys.executable, "shared/tools/gen_schemas.py"]),
        Step("patch sheet validates",
             [sys.executable, "shared/tools/validate_patch.py",
              "--event", args.event]),
        rig_step(event_dir),
    ]

    # Event-specific gate, where the event has one. despacio's checks venue
    # readiness -- calibration coverage, mount mode, pose reachability.
    event_preflight = event_dir / "preflight.py"
    if event_preflight.exists():
        steps.append(Step(f"{args.event} venue checks",
                          [sys.executable, str(event_preflight)]))

    # Parity is only meaningful while a QLC+ workspace still exists to compare
    # against; it is the guard that the port did not quietly drop a channel.
    if list(event_dir.glob("*.qxw")):
        steps.append(Step("QLC+ parity holds",
                          [sys.executable, "shared/tools/qlc_parity.py", "check"]))

    width = max([len(s.name) for s in steps] + [len("bundle matches source")])
    failures: list[tuple[str, str]] = []
    started = time.monotonic()

    for step in steps:
        print(f"{step.name:<{width}}  ", end="", flush=True)
        result = subprocess.run(step.cmd, cwd=step.cwd, capture_output=True,
                                text=True, errors="replace")
        if result.returncode == 0:
            print("OK")
        else:
            print("FAIL")
            failures.append((step.name,
                             ((result.stdout or "") + (result.stderr or "")).strip()))

    print(f"{'bundle matches source':<{width}}  ", end="", flush=True)
    ok, detail = bundle_matches_source()
    print("OK" if ok else "FAIL")
    if ok and "skipped" in detail:
        print(f"{'':<{width}}  ({detail})")
    if not ok:
        failures.append(("bundle matches source", detail))

    print()
    for name, output in failures:
        print(f"===== {name} " + "=" * max(0, 58 - len(name)))
        tail = [ln for ln in output.splitlines() if ln.strip()][-25:]
        print("\n".join(tail) if tail else "(no output)")
        print()

    elapsed = time.monotonic() - started
    if failures:
        print(f"NOT READY -- {len(failures)} check(s) failed in {elapsed:.1f}s")
        return 1
    print(f"ready to load in ({elapsed:.1f}s)")
    print("At the venue, still to do: power up, then "
          "`python -m engine.calibrate drift` before doors.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
