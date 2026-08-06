#!/usr/bin/env python3
"""
F2 timing spike — can Python hold a DMX output clock steady enough?

THIS IS A THROWAWAY. It exists to settle one decision: whether the show
engine's output stage can be Python, or whether it needs Go/Rust. Nothing
should import it.

WHAT IT MEASURES
    A dedicated output thread runs a fixed-rate loop (default 40 Hz), and on
    every frame does what the real engine will do: evaluate a synthetic show,
    encode pan/tilt to DMX, pack a 512-channel frame, and push it out over
    Art-Net. It records the wall-clock time of every frame and reports how far
    each one landed from where it should have.

    Two different errors matter and they are not the same thing:

      interval error — spacing between consecutive frames minus the target
        period. This is visible jitter: it makes smooth movement look uneven.

      lateness — how far past its intended deadline a frame actually fired.
        Absolute drift matters much less; a frame that is consistently 2 ms
        late but perfectly spaced looks fine.

    A dropped frame is one where the loop fell so far behind that it could not
    catch up without bursting, so the deadline is resynced and the frame is
    counted as lost.

PASS CRITERIA (from the plan)
    p99 |interval error| < 3 ms, max < 10 ms, zero dropped frames.

    For scale: a moving head's mechanical response is tens of milliseconds and
    the MJ-OS-018 has its own pan/tilt-speed smoothing on top, so single-digit
    milliseconds of jitter are not observable in the room. The threshold is
    deliberately far tighter than perceptibility.

WHY IT MIGHT FAIL
    Three suspects, all measured here:
      - GC pauses. Correlated against outliers via gc.callbacks.
      - Windows timer granularity. time.sleep() rounds up to the system timer
        period, which defaults to 15.6 ms — fatal for a 25 ms loop. Countered
        with timeBeginPeriod(1) plus a busy-wait tail. Use --no-timer-res to
        see the damage for yourself.
      - CPU contention. The show laptop will be running other things, so
        --contend N spawns N busy threads to make the test honest.

USAGE
    python spike/timing/jitter_harness.py --minutes 3
    python spike/timing/jitter_harness.py --minutes 60 --contend 2
    python spike/timing/jitter_harness.py --minutes 1 --no-timer-res   # A/B
    python spike/timing/jitter_harness.py --minutes 3 --json out.json
"""

import argparse
import ctypes
import gc
import json
import math
import platform
import statistics
import sys
import threading
import time
from pathlib import Path

# Reuse the real Art-Net packet builder rather than reimplementing it, so the
# per-frame syscall cost measured here is the cost the engine will actually pay.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent / "shared" / "tools"))
from artnet_sender import build_artdmx, make_socket, ARTNET_PORT  # noqa: E402


# ── Windows timer resolution ──────────────────────────────────────────────────

class TimerResolution:
    """Raise the system timer resolution for the duration of the block.

    Windows' default scheduling quantum is 15.6 ms, which rounds every
    time.sleep() up to a multiple of that — a 25 ms loop would land on 31.2 ms.
    timeBeginPeriod(1) drops it to 1 ms. No-op off Windows, where nanosleep is
    already fine-grained.
    """

    def __init__(self, ms: int = 1, enabled: bool = True):
        self.ms = ms
        self.enabled = enabled and platform.system() == "Windows"
        self.applied = False

    def __enter__(self):
        if self.enabled:
            try:
                ctypes.windll.winmm.timeBeginPeriod(self.ms)
                self.applied = True
            except Exception as e:      # pragma: no cover - diagnostic only
                print(f"WARNING: timeBeginPeriod({self.ms}) failed: {e}")
        return self

    def __exit__(self, *exc):
        if self.applied:
            ctypes.windll.winmm.timeEndPeriod(self.ms)
        return False


# ── Synthetic show — stands in for the real engine's per-frame work ───────────

PAN_MAX_DEG = 540.0
TILT_MAX_DEG = 270.0


def evaluate_show(frame_index: int, n_heads: int, frame: bytearray) -> None:
    """Do roughly what the engine will do each frame, with real float math.

    Per moving head: derive a target on an orbit, solve bearing/elevation,
    encode to 16-bit pan/tilt, apply a crude distance-based intensity taper,
    and write the channel block. This is deliberately not a spin loop — the
    point is to measure the loop under representative arithmetic, not to burn
    a fixed number of cycles.
    """
    t = frame_index / 40.0
    for head in range(n_heads):
        base = head * 11
        if base + 10 >= len(frame):
            break

        # Head position in a square room, target orbiting the centre.
        hx = 0.5 if head in (0, 1) else -0.5
        hz = 0.5 if head in (0, 3) else -0.5
        phase = t * 0.5 + head * (math.pi / 2)
        tx = 2.5 * math.cos(phase)
        tz = 2.5 * math.sin(phase)
        ty = 1.2 + 0.4 * math.sin(t * 0.7)

        dx, dy, dz = tx - hx, ty - 3.0, tz - hz
        ground = math.hypot(dx, dz)
        bearing = math.degrees(math.atan2(dx, dz))
        elevation = math.degrees(math.atan2(dy, ground))
        distance = math.sqrt(dx * dx + dy * dy + dz * dz)

        pan16 = int(((bearing % 360.0) / PAN_MAX_DEG) * 65535) & 0xFFFF
        tilt16 = int(((elevation + 135.0) / TILT_MAX_DEG) * 65535) & 0xFFFF

        # Safety taper stand-in: fade as the beam nears head height.
        height_at_target = ty
        margin = abs(height_at_target - 1.7)
        taper = 0.0 if margin < 0.15 else min(1.0, margin / 0.5)
        dim = int(255 * taper * (1.0 / max(1.0, distance * 0.25)))

        frame[base + 0] = (pan16 >> 8) & 0xFF
        frame[base + 1] = pan16 & 0xFF
        frame[base + 2] = (tilt16 >> 8) & 0xFF
        frame[base + 3] = tilt16 & 0xFF
        frame[base + 7] = dim & 0xFF
        frame[base + 10] = 0        # Reset channel pinned low, as in the show


# ── The output loop under test ───────────────────────────────────────────────

def output_loop(stop_evt, fps, n_heads, sock, target, universe, send, results):
    """Fixed-rate loop with monotonic deadline scheduling and a busy-wait tail.

    Deadlines advance by a fixed period rather than being computed from 'now',
    so scheduling error does not accumulate into drift.
    """
    period = 1.0 / fps
    spin_margin = 0.002          # busy-wait the last 2 ms for precision
    frame = bytearray(512)

    intervals, lateness = [], []
    drops = 0
    frame_index = 0

    clock = time.perf_counter
    start = clock()
    deadline = start + period
    prev_frame_time = start

    while not stop_evt.is_set():
        # Wait for the deadline: coarse sleep, then spin.
        while True:
            now = clock()
            remaining = deadline - now
            if remaining <= 0:
                break
            if remaining > spin_margin:
                time.sleep(remaining - spin_margin)
            # else: fall through and spin

        now = clock()
        lateness.append((now - deadline) * 1000.0)
        intervals.append((now - prev_frame_time) * 1000.0)
        prev_frame_time = now

        evaluate_show(frame_index, n_heads, frame)
        if send:
            sock.sendto(build_artdmx(universe, bytes(frame)), (target, ARTNET_PORT))

        frame_index += 1
        deadline += period

        # Fell more than a whole period behind: we cannot catch up without
        # bursting, so resync and record the loss.
        if clock() > deadline + period:
            drops += 1
            deadline = clock() + period

    results["intervals"] = intervals
    results["lateness"] = lateness
    results["drops"] = drops
    results["frames"] = frame_index
    results["elapsed"] = clock() - start


# ── Contention ───────────────────────────────────────────────────────────────

def burn_cpu(stop_evt):
    """CPU-bound work in a THREAD — contends for the GIL.

    This is the worst case, and it is not the same thing as "the laptop is
    busy". A CPU-bound Python thread holds the GIL for up to
    sys.getswitchinterval() (5 ms by default) before yielding, so the output
    thread can be blocked for multiples of that no matter what the OS
    scheduler wants. Use --contend-procs for genuine external load.
    """
    x = 0.0
    while not stop_evt.is_set():
        for i in range(10000):
            x += math.sqrt(i + 1)


BURNER_SRC = (
    "import math\n"
    "x=0.0\n"
    "while True:\n"
    "    for i in range(100000): x += math.sqrt(i+1)\n"
)


def spawn_burner_processes(n):
    """CPU load in SEPARATE PROCESSES — no GIL sharing, only OS scheduling.

    This is what "the show laptop is also running a browser" actually looks
    like to our output loop.
    """
    import subprocess
    procs = []
    for _ in range(n):
        procs.append(subprocess.Popen(
            [sys.executable, "-c", BURNER_SRC],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        ))
    return procs


def raise_priority():
    """Ask Windows to treat this process as time-critical-ish.

    A real show engine would do this; a lighting desk missing frames because
    a browser wanted CPU is not acceptable.
    """
    if platform.system() != "Windows":
        try:
            import os
            os.nice(-10)
            return "nice(-10)"
        except Exception as e:
            return f"failed: {e}"
    try:
        HIGH_PRIORITY_CLASS = 0x00000080
        handle = ctypes.windll.kernel32.GetCurrentProcess()
        ok = ctypes.windll.kernel32.SetPriorityClass(handle, HIGH_PRIORITY_CLASS)
        return "HIGH_PRIORITY_CLASS" if ok else "SetPriorityClass failed"
    except Exception as e:
        return f"failed: {e}"


# ── Reporting ────────────────────────────────────────────────────────────────

def pct(sorted_vals, p):
    if not sorted_vals:
        return float("nan")
    k = (len(sorted_vals) - 1) * (p / 100.0)
    lo, hi = math.floor(k), math.ceil(k)
    if lo == hi:
        return sorted_vals[int(k)]
    return sorted_vals[lo] * (hi - k) + sorted_vals[hi] * (k - lo)


def describe(name, vals, unit="ms"):
    s = sorted(vals)
    print(f"  {name}")
    print(f"    n       {len(s)}")
    print(f"    mean    {statistics.fmean(s):+8.3f} {unit}")
    print(f"    p50     {pct(s, 50):+8.3f} {unit}")
    print(f"    p95     {pct(s, 95):+8.3f} {unit}")
    print(f"    p99     {pct(s, 99):+8.3f} {unit}")
    print(f"    p99.9   {pct(s, 99.9):+8.3f} {unit}")
    print(f"    min/max {min(s):+8.3f} / {max(s):+8.3f} {unit}")


def main():
    ap = argparse.ArgumentParser(description="DMX output-loop jitter harness (F2 spike)")
    ap.add_argument("--minutes", type=float, default=60.0, help="run duration (default 60)")
    ap.add_argument("--fps", type=float, default=40.0, help="target frame rate (default 40)")
    ap.add_argument("--heads", type=int, default=6, help="fixtures to evaluate per frame (default 6)")
    ap.add_argument("--contend", type=int, default=0,
                    help="CPU-burner THREADS (GIL contention — worst case)")
    ap.add_argument("--contend-procs", type=int, default=0,
                    help="CPU-burner PROCESSES (OS contention — realistic external load)")
    ap.add_argument("--switch-interval", type=float, default=None,
                    help="sys.setswitchinterval() in ms; lowering it caps how long "
                         "another Python thread can hold the GIL (default 5)")
    ap.add_argument("--priority", action="store_true",
                    help="raise process scheduling priority")
    ap.add_argument("--no-send", action="store_true", help="skip the Art-Net socket send")
    ap.add_argument("--target", default="127.0.0.1", help="Art-Net destination (default loopback)")
    ap.add_argument("--universe", type=int, default=0)
    ap.add_argument("--no-timer-res", action="store_true",
                    help="do NOT raise the Windows timer resolution (A/B comparison)")
    ap.add_argument("--json", type=Path, help="write raw samples + summary here")
    args = ap.parse_args()

    period_ms = 1000.0 / args.fps
    duration = args.minutes * 60.0
    send = not args.no_send

    if args.switch_interval is not None:
        sys.setswitchinterval(args.switch_interval / 1000.0)

    # Correlate outliers against garbage collection.
    gc_events = []
    def gc_cb(phase, info):
        if phase == "stop":
            gc_events.append((time.perf_counter(), info.get("collected", 0)))
    gc.callbacks.append(gc_cb)

    sock = target = None
    if send:
        sock, target = make_socket(args.target)

    print("=" * 68)
    print("  DMX OUTPUT-LOOP JITTER HARNESS  (F2 spike — throwaway)")
    print("=" * 68)
    print(f"  platform      {platform.system()} {platform.release()}, Python {platform.python_version()}")
    print(f"  target rate   {args.fps:g} fps  ({period_ms:.3f} ms period)")
    print(f"  duration      {args.minutes:g} min")
    print(f"  per-frame     evaluate {args.heads} fixtures, pack 512 ch"
          f"{', send Art-Net -> ' + str(target) if send else ', no send'}")
    print(f"  timer res     {'DEFAULT (not raised)' if args.no_timer_res else '1 ms via timeBeginPeriod'}")
    print(f"  contention    {args.contend} thread(s) [GIL], {args.contend_procs} process(es) [OS]")
    print(f"  switch intvl  {sys.getswitchinterval() * 1000:.3f} ms")
    if args.priority:
        print(f"  priority      {raise_priority()}")
    print("=" * 68)
    print("  running...", flush=True)

    stop_evt = threading.Event()
    results = {}

    burners = [threading.Thread(target=burn_cpu, args=(stop_evt,), daemon=True)
               for _ in range(args.contend)]
    for b in burners:
        b.start()
    burner_procs = spawn_burner_processes(args.contend_procs)

    with TimerResolution(1, enabled=not args.no_timer_res):
        loop = threading.Thread(
            target=output_loop,
            args=(stop_evt, args.fps, args.heads, sock, target, args.universe, send, results),
            daemon=True,
        )
        loop.start()
        try:
            time.sleep(duration)
        except KeyboardInterrupt:
            print("\n  interrupted — reporting on what we have")
        stop_evt.set()
        loop.join(timeout=10)

    for p in burner_procs:
        p.terminate()
    gc.callbacks.remove(gc_cb)

    intervals = results.get("intervals", [])
    lateness = results.get("lateness", [])
    if len(intervals) < 2:
        print("ERROR: no samples collected")
        return 2

    # Frame 1's interval is measured from loop start, not a previous frame.
    intervals = intervals[1:]
    errors = [v - period_ms for v in intervals]
    abs_errors = [abs(e) for e in errors]

    print(f"  done — {results['frames']} frames in {results['elapsed']:.1f} s "
          f"(effective {results['frames'] / results['elapsed']:.2f} fps)\n")

    print("-" * 68)
    print("INTERVAL ERROR  (spacing between frames minus target — visible jitter)")
    describe("signed", errors)
    print()
    describe("absolute", abs_errors)
    print()
    print("-" * 68)
    print("LATENESS  (how far past its deadline each frame fired — absolute drift)")
    describe("signed", lateness)
    print()

    print("-" * 68)
    print(f"DROPPED FRAMES   {results['drops']}")
    print(f"GC COLLECTIONS   {len(gc_events)}")

    s_abs = sorted(abs_errors)
    p99 = pct(s_abs, 99)
    worst = max(s_abs)
    drops = results["drops"]

    print()
    print("=" * 68)
    print("VERDICT")
    print("=" * 68)
    checks = [
        ("p99 |interval error| < 3 ms", p99 < 3.0, f"{p99:.3f} ms"),
        ("max |interval error| < 10 ms", worst < 10.0, f"{worst:.3f} ms"),
        ("zero dropped frames", drops == 0, str(drops)),
    ]
    for label, ok, actual in checks:
        print(f"  [{'PASS' if ok else 'FAIL'}] {label:<32} actual: {actual}")

    passed = all(ok for _, ok, _ in checks)
    print()
    if passed:
        print("  => Python holds the clock. Keep the engine in Python; the output")
        print("     stage stays a small isolated component behind one interface.")
    else:
        print("  => Thresholds missed. Before reaching for Go, check in order:")
        print("     timer resolution (rerun with and without --no-timer-res),")
        print("     GC pause correlation, and whether contention was realistic.")
    print("=" * 68)

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps({
            "config": {
                "fps": args.fps, "minutes": args.minutes, "heads": args.heads,
                "contend": args.contend, "send": send,
                "timer_res_raised": not args.no_timer_res,
                "platform": f"{platform.system()} {platform.release()}",
                "python": platform.python_version(),
            },
            "summary": {
                "frames": results["frames"], "elapsed_s": results["elapsed"],
                "drops": drops, "gc_collections": len(gc_events),
                "interval_err_p50": pct(s_abs, 50), "interval_err_p95": pct(s_abs, 95),
                "interval_err_p99": p99, "interval_err_max": worst,
                "lateness_p99": pct(sorted(lateness), 99),
                "passed": passed,
            },
            "intervals_ms": intervals,
            "lateness_ms": lateness,
        }, indent=2), encoding="utf-8")
        print(f"  raw samples written to {args.json}")

    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
