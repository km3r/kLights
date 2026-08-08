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
    if shutil.which("npm") is None:
        return True, "skipped -- npm not installed"
    build = subprocess.run(["npm", "run", "build"], cwd=REPO / "ui",
                           capture_output=True, text=True, shell=True)
    if build.returncode != 0:
        return False, (build.stderr or build.stdout or "").strip()[-400:]
    status = subprocess.run(["git", "status", "--porcelain", "ui/dist"],
                            cwd=REPO, capture_output=True, text=True)
    if status.stdout.strip():
        return False, ("ui/dist differs from a fresh build. Commit it as one set:\n"
                       "    cd ui && npm run build && cd .. && git add -A ui/dist\n"
                       + status.stdout.rstrip())
    return True, "committed bundle matches a fresh build"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Pre-venue checks")
    parser.add_argument("--event", default="despacio")
    parser.add_argument("--skip-tests", action="store_true",
                        help="skip the engine suites (they take ~15s)")
    args = parser.parse_args(argv)

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
        Step("rig loads and validates",
             [sys.executable, "-c",
              "import sys; sys.path.insert(0, '.');"
              "from pathlib import Path; from engine import rig;"
              f"r = rig.load_rig(Path('events/{args.event}'));"
              "e = r.validate();"
              "print('\\n'.join(e)) or sys.exit(1) if e else "
              "print(f'{len(r.fixtures)} fixtures, {len(r.movers)} movers, ok')"]),
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
