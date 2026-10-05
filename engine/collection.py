"""
The DJ's rekordbox collection, for the designer to browse and prep tracks from.

The engine never opens rekordbox's database itself, for two reasons:

- it is SQLCipher-encrypted, and opening it needs a package (`sqlcipher3`) the
  engine must not depend on: the engine is stdlib-only, so a show laptop needs
  Python and a checkout and nothing from pip;
- reading it, and parsing a playlist's analysis files, is CPU work, and the F2
  spike showed in-process CPU work is the one thing that breaks the DMX clock.
  A child process has its own GIL.

So this runs the prep bridge (bridges/rekordbox/prep.py) as a child process and
hands on what it says:

- `catalogue()` is the collection's playlists and tracks, as the bridge's JSON
  BYTES. They are served to the designer without being parsed here: a megabyte
  of `json.loads` is one C call that holds the GIL for its whole run.
- `prep(ids, show_dir)` preps those tracks into the show folder and returns
  the bridge's summary (small, so it is parsed). The folder reloads after, like
  after any other write to it.

Where the database is and its key are the bridge's business (`--master-db`,
`$RB_CIPHER_KEY`, klights.local.json); the child inherits this environment, so
it finds the same settings a prep run from a shell would. When it cannot read
the collection -- no rekordbox, no key, no sqlcipher3 -- its message is passed
on unchanged, because it already says what to do.

Called from HTTP threads and from a thread the controller starts, never from
the output thread.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional, Sequence

REPO = Path(__file__).resolve().parent.parent
PREP = REPO / "bridges" / "rekordbox" / "prep.py"

# A catalogue this fresh is served again rather than re-read: a page asking
# twice as it loads. Anything older is re-read, so a playlist edited in
# rekordbox is there the next time the designer opens it. A read is ~0.3 s.
CACHE_S = 5.0
CATALOGUE_TIMEOUT_S = 60.0
PREP_TIMEOUT_S = 600.0
MAX_IDS = 500                   # one request; a whole collection is not a click


class CollectionError(RuntimeError):
    """The bridge could not do it; the message says why, in its words."""


class Busy(CollectionError):
    """A prep is already running."""


def _python() -> str:
    """The interpreter running the engine -- but python.exe, not the
    console-less pythonw.exe the launcher may have started us with, whose
    child would have nowhere to write."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe":
        console = exe.with_name("python.exe")
        if console.is_file():
            return str(console)
    return str(exe)


class Collection:
    def __init__(self, extra_args: Sequence[str] = (),
                 python: Optional[str] = None, script: Path = PREP):
        # extra_args go after the subcommand: --master-db, --anlz-root, --db.
        self.extra_args = list(extra_args)
        self.python = python or _python()
        self.script = Path(script)
        self._cat_lock = threading.Lock()
        self._cached: Optional[tuple[float, bytes]] = None
        self._prep_lock = threading.Lock()

    def _run(self, args: list[str], timeout: float) -> subprocess.CompletedProcess:
        env = dict(os.environ)
        env["PYTHONIOENCODING"] = "utf-8"
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        try:
            return subprocess.run([self.python, str(self.script), *args],
                                  capture_output=True, timeout=timeout, env=env,
                                  cwd=str(REPO), creationflags=flags)
        except subprocess.TimeoutExpired:
            raise CollectionError(f"the rekordbox bridge took longer than "
                                  f"{timeout:.0f} s and was stopped") from None
        except OSError as exc:
            raise CollectionError(f"could not start the rekordbox bridge: "
                                  f"{exc}") from None

    @staticmethod
    def _complaint(proc: subprocess.CompletedProcess) -> str:
        text = (proc.stderr or proc.stdout or b"").decode("utf-8", "replace").strip()
        last = text.splitlines()[-1] if text else ""
        return last or f"the rekordbox bridge exited with {proc.returncode}"

    def catalogue(self, refresh: bool = False) -> bytes:
        """The catalogue JSON, as bytes. Raises CollectionError."""
        with self._cat_lock:
            cached = self._cached
            if (cached is not None and not refresh
                    and time.monotonic() - cached[0] < CACHE_S):
                return cached[1]
            proc = self._run(["catalogue", *self.extra_args], CATALOGUE_TIMEOUT_S)
            body = proc.stdout or b""
            if proc.returncode != 0 or not body.lstrip().startswith(b"{"):
                raise CollectionError(self._complaint(proc))
            self._cached = (time.monotonic(), body)
            return body

    def prep(self, ids: Sequence[int], show_dir: Path) -> dict:
        """Prep these rekordbox ids into the show folder. Returns the bridge's
        summary: {"results": [...], "skipped": [...]}. One at a time."""
        if not self._prep_lock.acquire(blocking=False):
            raise Busy("a prep from rekordbox is already running; wait for it")
        try:
            args = ["--show-dir", str(show_dir), "db", *self.extra_args, "--json"]
            for i in ids:
                args += ["--id", str(int(i))]
            proc = self._run(args, PREP_TIMEOUT_S)
            try:
                summary = json.loads((proc.stdout or b"").decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise CollectionError(self._complaint(proc)) from None
            if not isinstance(summary, dict):
                raise CollectionError(self._complaint(proc))
            if "error" in summary:
                raise CollectionError(str(summary["error"]))
            return summary
        finally:
            self._prep_lock.release()


def check_ids(raw) -> list[int]:
    """The ids a request names: a non-empty list of rekordbox ids, each once."""
    if not isinstance(raw, list) or not raw:
        raise ValueError("ids must be a list of rekordbox track ids")
    if len(raw) > MAX_IDS:
        raise ValueError(f"at most {MAX_IDS} tracks at a time")
    ids = []
    for i in raw:
        if isinstance(i, bool) or not isinstance(i, int) or not 0 <= i < 2 ** 32:
            raise ValueError(f"{i!r} is not a rekordbox track id")
        if i not in ids:
            ids.append(i)
    return ids
