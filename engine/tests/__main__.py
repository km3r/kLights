"""Run every engine test suite and report once.

    python -m engine.tests            # everything
    python -m engine.tests -k server  # only suites matching "server"
    python -m engine.tests --no-self-tests
    python -m engine.tests -v         # stream each suite's own output

Replaces the shell loop the README used to document:

    for t in engine/tests/test_*.py; do python "$t" || break; done

which stops at the first failure (so you fix one, rerun, and find the next),
and is bash on a project whose primary machine is Windows.

Each suite runs in its own subprocess. That is not fastidiousness: the suites
install a real frame clock, bind real sockets and set process-wide timing via
`runner.install_timing_contract()`, and several assert on wall-clock behaviour.
Sharing an interpreter would let one suite's leftovers decide another's result,
and the first time that happened it would look like a flaky engine rather than
a flaky harness.

Exit code is 0 only if every suite passed, so this is what CI and the preflight
script call.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# Modules that self-test under `if __name__ == "__main__"` rather than having a
# suite in this directory. They are cheap and they cover the two things most
# likely to be wrong after a refactor -- the aim maths and the yoke model -- so
# a "run everything" command that skipped them would be lying about its name.
SELF_TESTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("engine/geometry.py", ()),
    ("engine/servo.py", ()),
    ("previz/mirrorball.py", ()),
    ("previz/scene.py", ("--self-test",)),
    ("engine/scene.py", ("--self-test",)),
)


def suites() -> list[Path]:
    return sorted(HERE.glob("test_*.py"))


# UTF-8 on every suite's pipes, as launcher/core.py does for the engine. On
# Windows a child writing to a pipe otherwise encodes as cp1252, and the first
# check whose label or detail holds a character outside it -- a replacement
# character, an emoji in a hostile input -- crashes the suite on Windows only.
CHILD_ENV = {**os.environ, "PYTHONIOENCODING": "utf-8"}


def run(label: str, cmd: list[str], verbose: bool) -> tuple[bool, float, str]:
    started = time.monotonic()
    if verbose:
        print(f"\n----- {label} " + "-" * max(0, 60 - len(label)))
        result = subprocess.run(cmd, cwd=REPO, env=CHILD_ENV)
        output = ""
    else:
        result = subprocess.run(cmd, cwd=REPO, capture_output=True, text=True,
                                encoding="utf-8", errors="replace", env=CHILD_ENV)
        output = (result.stdout or "") + (result.stderr or "")
    return result.returncode == 0, time.monotonic() - started, output


def main(argv: list[str] | None = None) -> int:
    # This process's own stdout is a cp1252 pipe under CI on Windows too, and
    # it reprints the tail of a failing suite: replace, rather than crash on,
    # what it cannot encode.
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="replace")
    parser = argparse.ArgumentParser(description="Run every engine test suite")
    parser.add_argument("-k", metavar="SUBSTRING",
                        help="only run suites whose name contains this")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="stream each suite's output instead of capturing it")
    parser.add_argument("--no-self-tests", action="store_true",
                        help="skip the module self-tests")
    args = parser.parse_args(argv)

    jobs: list[tuple[str, list[str]]] = [
        (path.stem, [sys.executable, str(path)]) for path in suites()
    ]
    if not args.no_self_tests:
        jobs += [
            (Path(rel).stem + " (self-test)",
             [sys.executable, str(REPO / rel), *extra])
            for rel, extra in SELF_TESTS
            if (REPO / rel).exists()
        ]
    if args.k:
        jobs = [j for j in jobs if args.k in j[0]]
    if not jobs:
        print("no suites matched", file=sys.stderr)
        return 1

    width = max(len(name) for name, _ in jobs)
    failures: list[tuple[str, str]] = []
    started = time.monotonic()

    for name, cmd in jobs:
        if not args.verbose:
            # Flushed without a newline so a hung suite is identifiable by the
            # line left dangling, rather than being invisible until it returns.
            print(f"{name:<{width}}  ", end="", flush=True)
        ok, seconds, output = run(name, cmd, args.verbose)
        if not args.verbose:
            print(f"{'PASS' if ok else 'FAIL'}  {seconds:5.1f}s")
        if not ok:
            failures.append((name, output))

    elapsed = time.monotonic() - started
    print()
    for name, output in failures:
        print(f"===== {name} " + "=" * max(0, 60 - len(name)))
        # The tail, because these suites print a PASS line per check and the
        # failure is at the bottom. The whole log is available with -v.
        tail = [ln for ln in output.splitlines() if ln.strip()][-25:]
        print("\n".join(tail) if tail else "(no output)")
        print()

    passed = len(jobs) - len(failures)
    print(f"{passed}/{len(jobs)} suites passed in {elapsed:.1f}s")
    if failures:
        print("failed: " + ", ".join(name for name, _ in failures))
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
