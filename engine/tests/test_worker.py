"""
Tests for the background worker.

The worker exists for one reason: slow work must not run on the output thread,
and its results must not be installed from anywhere else. So these check which
THREAD things run on, not just that they run -- a worker that quietly ran jobs
inline would pass every "did it happen" test and break the DMX clock.

Run: python engine/tests/test_worker.py
"""

import queue
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine.worker import Worker  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


class Owner:
    """Stands in for the output thread: a queue of posted callables that only
    run when `drain` is called, on whichever thread calls it."""

    def __init__(self):
        self.posted: "queue.Queue" = queue.Queue()
        self.notices: list[str] = []

    def post(self, fn):
        self.posted.put(fn)

    def drain(self, timeout=2.0, want=1):
        ran = 0
        deadline = time.time() + timeout
        while ran < want and time.time() < deadline:
            try:
                fn = self.posted.get(timeout=0.05)
            except queue.Empty:
                continue
            fn()
            ran += 1
        return ran


print("\n1. jobs run on the worker, results come back through post")
owner = Owner()
w = Worker(post=owner.post, report=owner.notices.append)
w.start()

seen: dict = {}


def job():
    seen["job_thread"] = threading.current_thread().name
    return 42


def done(result):
    seen["done_thread"] = threading.current_thread().name
    seen["result"] = result


w.submit(job, done)
check("the job ran on the worker's own thread",
      w.wait_idle() and seen.get("job_thread") == "klights-worker",
      f"{seen.get('job_thread')}")
check("and `done` did NOT run there -- it waits to be posted",
      "done_thread" not in seen, f"{seen}")
owner.drain()
check("draining the post runs `done` on the owner's thread, with the result",
      seen.get("done_thread") == threading.current_thread().name
      and seen.get("result") == 42, f"{seen}")


print("\n2. submit never blocks the caller")
gate = threading.Event()
started = time.perf_counter()
w.submit(lambda: gate.wait(2.0))
w.submit(lambda: None)
elapsed = time.perf_counter() - started
check("submitting behind a slow job returns at once",
      elapsed < 0.05, f"{elapsed * 1000:.1f} ms")
check("and the queue knows both are pending", w.pending() >= 2, f"{w.pending()}")
gate.set()
check("they finish once the slow one does", w.wait_idle(), f"{w.pending()}")


print("\n3. order is kept")
order: list[int] = []
for i in range(20):
    w.submit(lambda i=i: order.append(i))
w.wait_idle()
check("jobs run first-in, first-out", order == list(range(20)), f"{order}")

# Late binding would hand every `done` the last job's result. Twenty jobs, each
# with its own result, each result delivered to its own callback.
got: list[tuple[int, int]] = []
for i in range(20):
    w.submit(lambda i=i: i * 10, lambda r, i=i: got.append((i, r)))
w.wait_idle()
owner.drain(want=20)
check("each `done` receives its own job's result",
      got == [(i, i * 10) for i in range(20)], f"{got[:4]}...")


print("\n4. failures become notices, and the worker survives them")


def broken():
    raise RuntimeError("the NAS went away")


w.submit(broken, label="save timeline")
w.wait_idle()
owner.drain()
check("a failed job is reported by its label",
      any("save timeline failed" in n and "NAS" in n for n in owner.notices),
      f"{owner.notices}")
w.submit(lambda: 7, lambda r: seen.__setitem__("after", r))
w.wait_idle()
owner.drain()
check("and the next job still runs", seen.get("after") == 7, f"{seen}")

# A post that itself fails must not kill the thread or lose the failure.
bad = Worker(post=lambda fn: (_ for _ in ()).throw(RuntimeError("queue gone")),
             report=owner.notices.append, name="bad-post")
bad.start()
bad.submit(lambda: 1, lambda r: None, label="compile")
bad.wait_idle()
check("a post that raises is reported directly rather than lost",
      any("could not hand back" in n and "compile" in n for n in owner.notices),
      f"{owner.notices[-1:]}")
bad.submit(lambda: seen.__setitem__("bad_alive", True))
bad.wait_idle()
check("and that worker is still alive", seen.get("bad_alive") is True)
bad.stop()


print("\n5. stop")
w.submit(lambda: seen.__setitem__("last", True))
w.stop()
check("stop finishes what was queued before it", seen.get("last") is True)
check("and the thread is gone", not w.running)

stuck = Worker(post=owner.post, report=owner.notices.append, name="stuck")
stuck.start()
hang = threading.Event()
stuck.submit(lambda: hang.wait(10))
started = time.perf_counter()
stuck.stop(timeout=0.2)
elapsed = time.perf_counter() - started
check("a job that never returns cannot hang shutdown",
      elapsed < 1.0, f"{elapsed:.2f} s")
hang.set()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("worker: all checks pass")
