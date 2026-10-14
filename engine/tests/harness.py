"""
What every suite here shares: `check`, the summary at the end, and the
throwaway copies of an event and a show folder that a suite writes to.

    from engine.tests.harness import check, finish

    check("a look selects", ok, detail)
    ...
    finish("server")

Still no test framework, on purpose: a suite is a script that runs top to
bottom and exits non-zero if anything failed, so it runs on a show laptop with
nothing installed, and `python -m engine.tests` runs each in its own
interpreter (see `__main__.py` for why). This module is only the part forty
suites had each typed out for themselves.

A failure is recorded WITH its detail. `python -m engine.tests`, and so CI,
shows only the tail of a failing suite, which is the list `finish` prints -- a
label alone says which check failed but not by how much.
"""

from __future__ import annotations

import atexit
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any, NoReturn

REPO = Path(__file__).resolve().parent.parent.parent

# What a copy of an event or a room leaves behind: caches, the backups a write
# keeps, and a running engine's lock -- which would tell the tools this copy is
# live when it is not.
SKIP = shutil.ignore_patterns("__pycache__", "*.bak", ".engine.lock", "backups")

failures: list[str] = []

# Every answer a command sends is held to what the command declares it sends
# (`@command(..., reply=...)`): under the suites a mismatch is refused, so the
# test that sent the command fails, rather than a notice nobody reads.
sys.path.insert(0, str(REPO))
from engine import commands as _commands  # noqa: E402

_commands.STRICT = True


def check(label: str, ok: Any, detail: Any = "") -> bool:
    """One assertion. Prints PASS or FAIL, remembers a failure, never raises,
    so one wrong answer does not hide the ones after it."""
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label + (f"  -- {detail}" if detail else ""))
    return bool(ok)


def finish(name: str) -> NoReturn:
    """The suite's last line: every failure, and the exit code."""
    print()
    if failures:
        print(f"{name}: {len(failures)} FAILED")
        for f in failures:
            print(f"  - {f}")
        sys.exit(1)
    print(f"{name}: all checks pass")
    sys.exit(0)


def tmpdir(prefix: str) -> Path:
    """A fresh directory, removed when the suite exits however it exits."""
    path = Path(tempfile.mkdtemp(prefix=prefix))
    atexit.register(shutil.rmtree, path, ignore_errors=True)
    return path


def isolated_event(name: str = "despacio", *, under: Path | None = None) -> Path:
    """A copy of `events/<name>` to write to, and the venue library with it.

    The copy keeps the event's name, because the engine reports the folder
    name as the event. The rooms are copied too, and both library lookups
    pointed at the copy: an event names a room in shared/venues/, and
    `venue_save` writes it there. Patching module globals is safe because every
    suite runs in its own interpreter.

    Never the real event: a save that crashed before its cleanup would leave
    junk in tonight's show, and even a clean restore rewrites line endings.
    """
    from engine import patch as patchmod
    from engine import rig as rigmod

    root = under if under is not None else tmpdir(f"klights-{name}-")
    event = root / name
    shutil.copytree(REPO / "events" / name, event, ignore=SKIP)
    venues = root / "venues"
    if not venues.exists():
        shutil.copytree(rigmod.VENUE_LIBRARY, venues, ignore=SKIP)
    rigmod.VENUE_LIBRARY = patchmod.VENUES = venues
    return event


def show_copy(dest: Path) -> Path:
    """A copy of the example show folder at `dest`, to write to."""
    shutil.copytree(REPO / "shared" / "show-example", dest)
    return dest
