"""
One background thread for work that must not run on the output thread.

The F2 spike measured the one thing that breaks the DMX clock: CPU-bound Python
running *in the same process*, holding the GIL while a 25 ms frame waits. The
output thread already does more than draw frames -- it drains every queued
command at the top of each frame -- so anything slow a command wants done
(parsing a show file, matching a track, compiling a timeline, an fsync to a
Dropbox folder) cannot happen there. It happens here.

The shape is deliberately narrow:

- `submit(fn, done)` runs `fn()` on this thread and hands its result back by
  **posting** `done(result)` to be run somewhere else. The engine posts onto its
  command queue (`ShowController.submit_call`), so `done` runs on the output
  thread at a frame boundary like every other mutation. Nothing that touches the
  running show ever executes on this thread.
- Failures are reported through the same post, so a crashed job becomes a line
  in the console's notices rather than a dead thread nobody notices.
- One thread, FIFO. A second worker would buy parallelism the GIL does not give
  and ordering bugs it would. A save followed by a reload of the same file must
  happen in that order.

It knows nothing about shows, tracks or the controller: it takes a `post`
callable and a `report` callable, which is what keeps it testable on its own.
"""

from __future__ import annotations

import queue
import threading
from typing import Any, Callable, Optional

Post = Callable[[Callable[[], None]], None]

_STOP = object()


class Worker:
    """A single background thread with a FIFO of jobs.

    `post(fn)` must arrange for `fn()` to run later on the thread that owns the
    show; `report(text)` records a failure where an operator will see it. Both
    are called from this worker's thread, so `post` must be thread-safe -- a
    `queue.Queue.put`, as the engine's command queue is, qualifies.
    """

    def __init__(self, post: Post, report: Callable[[str], None],
                 name: str = "klights-worker"):
        self.post = post
        self.report = report
        self.name = name
        self._jobs: "queue.Queue[Any]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self._run, name=self.name,
                                        daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        """Finish what is queued, then stop. A job still running after
        `timeout` is abandoned with the thread -- it is a daemon, so it cannot
        hold the process open -- rather than hanging shutdown on, say, a NAS
        that stopped answering mid-write."""
        if self._thread is None:
            return
        self._jobs.put(_STOP)
        self._thread.join(timeout)
        self._thread = None

    @property
    def running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- work --------------------------------------------------------------

    def submit(self, fn: Callable[[], Any],
               done: Optional[Callable[[Any], None]] = None,
               label: str = "") -> None:
        """Run `fn()` on the worker; post `done(result)` back when it returns.

        Never blocks the caller, which is the point: this is called from the
        output thread. `label` names the job in a failure notice -- "save
        timeline failed: ..." reads better than a function's repr.
        """
        self._jobs.put((fn, done, label or getattr(fn, "__name__", "job")))

    def pending(self) -> int:
        """Jobs queued and not yet finished, the running one included."""
        return self._jobs.unfinished_tasks

    def wait_idle(self, timeout: float = 5.0) -> bool:
        """Block until every submitted job has finished. For tests and for an
        orderly shutdown; never call it from the output thread."""
        done = threading.Event()

        def watch() -> None:
            self._jobs.join()
            done.set()

        threading.Thread(target=watch, daemon=True).start()
        return done.wait(timeout)

    def _run(self) -> None:
        while True:
            item = self._jobs.get()
            try:
                if item is _STOP:
                    return
                fn, done, label = item
                try:
                    result = fn()
                except Exception as exc:                    # noqa: BLE001
                    text = f"{label} failed: {exc}"
                    self._post_safely(lambda t=text: self.report(t), label)
                    continue
                if done is not None:
                    # Bind now: the loop variable is gone by the time the post
                    # runs, and a late-binding lambda would hand every `done`
                    # the LAST job's result.
                    self._post_safely(lambda d=done, r=result: d(r), label)
            finally:
                self._jobs.task_done()

    def _post_safely(self, fn: Callable[[], None], context: str) -> None:
        try:
            self.post(fn)
        except Exception as exc:                            # noqa: BLE001
            # The one place a failure has nowhere to go but here. Report
            # directly rather than lose it; `report` is a list append.
            try:
                self.report(f"worker could not hand back {context!r}: {exc}")
            except Exception:                               # noqa: BLE001
                pass
