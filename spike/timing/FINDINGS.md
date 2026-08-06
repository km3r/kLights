# F2 timing spike — findings

**Question:** can a Python output loop hold a DMX clock steady enough, or does
the output core need Go/Rust?

**Answer: Python, comfortably — but two settings are load-bearing, and without
either one it fails catastrophically.**

Measured 2026-08-06 on Windows 10, Python 3.11.9, at 40 fps (25 ms period),
6 fixtures evaluated per frame with real trig, packed into a 512-channel frame
and sent over Art-Net. Harness: [`jitter_harness.py`](jitter_harness.py).

Threshold from the plan: p99 |interval error| < 3 ms, max < 10 ms, zero drops.

## Headline result

3 minutes, 7200 frames, under *both* kinds of contention simultaneously
(2 GIL-competing Python threads + 2 external CPU-burning processes):

| metric | measured | threshold |
|---|---|---|
| effective rate | 40.000 fps | 40 |
| p50 \|interval error\| | 0.003 ms | — |
| p95 | 0.011 ms | — |
| **p99** | **0.046 ms** | < 3 ms |
| **max** | **2.122 ms** | < 10 ms |
| **dropped frames** | **0** | 0 |

That is roughly a 65× margin on p99. For scale, a moving head's mechanical
response is tens of milliseconds and the MJ-OS-018 adds its own pan/tilt-speed
smoothing, so this is far below anything observable in the room.

## The diagnostic matrix

1 minute each, identical except for the contention and the mitigation:

| config | p99 err | max err | drops |
|---|---|---|---|
| A — no contention | 0.012 ms | 0.086 ms | 0 |
| B — 2 external **processes** | 0.011 ms | 0.100 ms | 0 |
| C — 2 Python **threads** (GIL) | **26.9 ms** | **73.0 ms** | **16** |
| D — C + `setswitchinterval(0.5 ms)` | 0.018 ms | 1.931 ms | 0 |
| E — D + raised process priority | 0.048 ms | 3.082 ms | 0 |

And separately, without the Windows timer fix (plus GIL contention):
**p99 130 ms, max 178 ms, 421 drops in 60 seconds.**

## What this means

**1. External load is a non-issue.** Row B is indistinguishable from baseline.
The show laptop running a browser, Spotify, whatever — the OS scheduler handles
it. This was the thing we were worried about, and it turns out not to be the
problem.

**2. The real risk is *in-process* CPU-bound Python.** Row C is the disaster
case, and it has nothing to do with the OS. A CPU-bound Python thread holds the
GIL for up to `sys.getswitchinterval()` — **5 ms by default** — before yielding.
On a 25 ms period, two such threads can eat most of the budget. This is entirely
self-inflicted and entirely fixable.

**3. Both fixes are one-liners, and both are mandatory:**

```python
sys.setswitchinterval(0.0005)          # cap GIL hold at 0.5 ms
ctypes.windll.winmm.timeBeginPeriod(1) # Windows: 15.6 ms -> 1 ms timer
```

Windows' default scheduling quantum is 15.6 ms, so `time.sleep()` rounds up to
a multiple of that — a 25 ms loop lands on 31.2 ms. `timeBeginPeriod(1)` is not
optional on Windows; skipping it produced 421 dropped frames in one minute.

**4. Raising process priority does not help GIL contention** — row E is
marginally *worse* than D. That is the correct result, not noise:
`SetPriorityClass` lifts every thread in the process equally, including the ones
competing for the GIL. Don't reach for it.

## Frame rate: 40 fps is a wire limit, not a software one

Worth recording because the 40 in this document looks like a tuning choice and
is not. It also was never a QLC+ limit — QLC+ ticks at **50 Hz**
(`MasterTimer::s_frequency = 50` in `qlcplus/engine/src/mastertimer.cpp:44`,
overridable via the `mastertimer/frequency` setting).

Three separate ceilings, only one of which binds:

| layer | ceiling |
|---|---|
| Python output loop | ~2000 Hz measured |
| **DMX512 wire, full universe** | **~44 Hz** ← binding |
| The fixtures themselves | lower still |

Rate sweep, ~24 s each, `--switch-interval 0.5`, no contention. Frame counts
were exact at every rate (30001 frames in 30 s at 1000 fps):

| rate | p99 \|err\| | max \|err\| | drops |
|---|---|---|---|
| 100 fps | 0.006 ms | 0.541 ms | 0 |
| 500 fps | 0.001 ms | 0.085 ms | 0 |
| 1000 fps | 0.000 ms | 0.070 ms | 0 |
| 2000 fps | 0.000 ms | 0.445 ms | 0 |

The DMX512 ceiling is arithmetic: 250 kbaud, 11 bits per slot, 513 slots
(start code + 512 channels) = 5643 bits = 22.57 ms, plus Break (≥92 µs) and
Mark After Break (≥12 µs) ≈ 22.7 ms → **~44 Hz for a full universe**. Short
frames are faster in principle (despacio's 56 channels ≈ 2.6 ms), but cheap
fixtures often assume conventional timing, and the Art-Net spec recommends
≤44 Hz per universe precisely because nodes convert to physical DMX.

**Spend the headroom on width, not rate.** Ten universes at 44 Hz is 440
packets/second against the ~2000/s already demonstrated. A club-scale check —
44 fps, 46 fixtures evaluated per frame, full 512-channel frame, 2 GIL threads
*and* 2 external processes — gave p99 0.020 ms, max 0.034 ms, 0 drops. Per-frame
evaluation cost at 8× the despacio rig was invisible, which is the budget F4's
safety taper draws on.

**Frame rate is not what made motion look steppy.** That came from stepped
*scenes* jumping between stored DMX positions; F6's continuous interpolation
fixes it at 40 Hz. 25 ms between updates is already well inside the MJ-OS-018's
own pan/tilt-speed smoothing and its tens-of-milliseconds mechanical response.

## Design consequences for the engine

- Set both knobs at engine startup, before any thread starts. Treat them as part
  of the output stage's contract, not as tuning.
- The WebSocket server is I/O-bound and releases the GIL on socket waits, so it
  is low-risk in-process. Heavy *per-frame show evaluation* is the thing to
  watch — if it grows CPU-hungry, move it out of the output process rather than
  trying to schedule around it.
- The busy-wait tail costs ~8% of one core continuously (2 ms spin per 25 ms
  frame). That is the price of microsecond deadline precision and it is worth
  paying, but it is a real battery and thermal cost on a laptop. Tunable via
  `spin_margin`.
- Deadlines advance by a fixed period rather than from `now`, so scheduling
  error never accumulates into drift. Effective rate was exactly 40.000 fps over
  7200 frames.

## What this does NOT establish

Stated plainly, because the run was short:

- **3 minutes, not 60.** Thermal throttling and long-run GC behaviour are
  untested. Notably **GC collections were 0 in every run** — the loop allocates
  floats and bytes, which are refcounted and not GC-tracked. A real engine
  holding cyclic object graphs will collect, and that is the most likely source
  of a long-run outlier. Re-run at `--minutes 60` on an otherwise-idle machine
  before trusting this for a show.
- **This machine, not the show laptop.** Windows 10, Python 3.11.9.
- **6 fixtures.** A club rig of 48 is ~8× the per-frame math — still trivial in
  absolute terms, but unmeasured.
- **No DMX hardware in the loop.** Art-Net over loopback is a cheap syscall; a
  USB-DMX driver may block differently. If a USB interface is used, re-measure.

## Verdict

Build the engine in Python. Keep the output stage a small isolated component
behind one interface, as planned — not because a rewrite is expected, but
because that is what makes the two settings above easy to enforce in one place
and easy to replace if the hardware findings change.

Reproduce with:

```bash
python spike/timing/jitter_harness.py --minutes 3 --contend 2 --contend-procs 2 --switch-interval 0.5
```
