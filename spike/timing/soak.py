"""The 60-minute soak the F2 spike asked for, on the REAL engine.

    python spike/timing/soak.py --minutes 60

F2 measured the output loop for three minutes and left one caveat that mattered:

  "GC collections were 0 in every run -- the loop allocates floats and bytes,
   which are refcounted and not GC-tracked. A real engine holding cyclic object
   graphs will collect, and that is the most likely source of a long-run
   outlier. Re-run at --minutes 60 on an otherwise-idle machine before trusting
   this for a show."

So running `jitter_harness.py --minutes 60` would NOT answer it. The harness
holds no cyclic graphs, so it would report zero collections again and prove
nothing about the thing that was actually in doubt. This runs the show engine
instead -- the real frame loop, the real layer stack, real recomposition on
every auto look change -- and watches both the clock and the collector.

Art-Net goes to LOOPBACK by default and never to a broadcast address: a soak is
not a reason to fill a venue's network, or to move a rig that might be plugged
in.
"""

from __future__ import annotations

import argparse
import gc
import json
import statistics
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine.server import ShowController              # noqa: E402


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--minutes", type=float, default=60.0)
    parser.add_argument("--event", default=str(REPO / "events" / "despacio"))
    parser.add_argument("--fps", type=float, default=40.0)
    parser.add_argument("--artnet", default="127.0.0.1",
                        help="loopback by default, and never make this a "
                             "broadcast address for a soak")
    parser.add_argument("--sample", type=float, default=30.0,
                        help="seconds between samples")
    parser.add_argument("--json", type=Path)
    args = parser.parse_args(argv)

    if args.artnet.endswith(".255"):
        print("refusing to soak onto a broadcast address -- a soak is not a "
              "reason to move a rig that might be plugged in", file=sys.stderr)
        return 2

    controller = ShowController(Path(args.event), artnet=args.artnet,
                                fps=args.fps)
    # Auto mode ON, so the engine does the thing that actually allocates: every
    # look change rebuilds the layer stack, and those closures-over-context ARE
    # the cyclic graphs F2 could not test.
    controller.director.config.timing = True
    controller.director.config.look_changes = True
    controller.director.config.palette = True

    gc.collect()
    base_gc = [s["collections"] for s in gc.get_stats()]
    started = time.time()
    deadline = started + args.minutes * 60.0
    samples = []

    controller.start()
    print(f"soaking {args.minutes:g} min at {args.fps:g} fps -> {args.artnet}, "
          f"auto mode on, sampling every {args.sample:g}s", flush=True)
    try:
        while time.time() < deadline:
            time.sleep(args.sample)
            stats = controller.runner.stats
            now_gc = [s["collections"] for s in gc.get_stats()]
            sample = {
                "t": round(time.time() - started, 1),
                "fps": round(stats.effective_fps, 4),
                "frames": stats.frames,
                "drops": stats.drops,
                "eval_errors": stats.eval_errors,
                "worst_error_ms": round(stats.worst_error * 1000, 3),
                "gc": [a - b for a, b in zip(now_gc, base_gc)],
                "rss_objects": len(gc.get_objects()),
                "look_changes": controller.director.changes,
            }
            samples.append(sample)
            print(f"  {sample['t']:7.0f}s  {sample['fps']:.3f} fps  "
                  f"frames {sample['frames']:>8}  drops {sample['drops']:>4}  "
                  f"worst {sample['worst_error_ms']:>7.3f} ms  "
                  f"gc {sample['gc']}  objects {sample['rss_objects']:>8}",
                  flush=True)
    except KeyboardInterrupt:
        print("\ninterrupted", flush=True)
    finally:
        controller.stop()

    if not samples:
        print("no samples taken", file=sys.stderr)
        return 1

    final = samples[-1]
    worst = max(s["worst_error_ms"] for s in samples)
    fps_values = [s["fps"] for s in samples]
    # Object count DRIFT is the leak signal. A flat count over an hour with
    # collections happening is the answer F2 wanted; a rising one is a leak the
    # three-minute run was far too short to see.
    growth = final["rss_objects"] - samples[0]["rss_objects"]

    report = {
        "minutes": round((time.time() - started) / 60.0, 2),
        "fps_target": args.fps,
        "fps_mean": round(statistics.fmean(fps_values), 4),
        "fps_min": round(min(fps_values), 4),
        "frames": final["frames"],
        "drops": final["drops"],
        "eval_errors": final["eval_errors"],
        "worst_error_ms": worst,
        "gc_collections": final["gc"],
        "objects_start": samples[0]["rss_objects"],
        "objects_end": final["rss_objects"],
        "object_growth": growth,
        "look_changes": final["look_changes"],
        "timing_applied": list(controller.runner.applied_timing),
        "python": sys.version.split()[0],
        "platform": sys.platform,
    }

    print("\n--- soak result ---")
    for key, value in report.items():
        print(f"  {key:16} {value}")

    # The thresholds F2 set for itself, applied to the real engine.
    verdict = []
    if report["drops"]:
        verdict.append(f"{report['drops']} dropped frame(s)")
    if report["eval_errors"]:
        verdict.append(f"{report['eval_errors']} evaluation error(s)")
    if worst > 10.0:
        verdict.append(f"worst interval error {worst:.1f} ms (threshold 10)")
    if abs(report["fps_mean"] - args.fps) > 0.05:
        verdict.append(f"mean {report['fps_mean']} fps vs {args.fps} target")
    print("\n" + ("SOAK FAILED: " + "; ".join(verdict) if verdict
                  else "SOAK PASSED: no drops, no errors, clock held."))

    if args.json:
        args.json.write_text(json.dumps({"report": report, "samples": samples},
                                        indent=2), encoding="utf-8")
        print(f"wrote {args.json}")
    return 1 if verdict else 0


if __name__ == "__main__":
    raise SystemExit(main())
