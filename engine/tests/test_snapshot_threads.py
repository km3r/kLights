"""
The snapshot is built on the broadcast thread while commands land on the
output thread and clients come and go on HTTP threads. Every container it
walks has to be REBOUND by a writer, never edited in place: a dict or a set
that changes size while a comprehension walks it raises, and that broadcast is
lost -- "snapshot failed" on every console.

That rule lived only in comments, and two writers broke it: the color picker
edited `color_overrides` in place (under a comment saying it was not
published -- it is), and connecting or dropping a client edited `clients`.
This suite makes the threads collide on purpose. The switch interval is cut
to a microsecond so the interpreter changes threads inside a comprehension
rather than between them, and each writer is given enough entries to walk
that the window is wide.

Run: python engine/tests/test_snapshot_threads.py
"""

import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from engine.tests.harness import check, finish, isolated_event  # noqa: E402

from engine import server as servermod  # noqa: E402

EVENT = isolated_event("despacio")
SECONDS = 1.5
WIDE = 300          # entries a writer adds, so a comprehension has far to walk


def collide(label: str, write) -> None:
    """Run `write(i)` in a loop on one thread while this one builds snapshots;
    check no snapshot raised."""
    controller = servermod.ShowController(EVENT)
    errors: list[str] = []
    stop = threading.Event()

    def writer():
        i = 0
        while not stop.is_set():
            try:
                write(controller, i)
            except Exception as exc:                        # noqa: BLE001
                errors.append(f"writer: {type(exc).__name__}: {exc}")
                return
            i += 1

    before = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    thread = threading.Thread(target=writer, daemon=True)
    built = 0
    try:
        thread.start()
        deadline = time.monotonic() + SECONDS
        while time.monotonic() < deadline and not errors:
            try:
                controller.snapshot()
                built += 1
            except RuntimeError as exc:
                errors.append(f"snapshot: {exc}")
    finally:
        stop.set()
        thread.join(5)
        sys.setswitchinterval(before)
    check(f"{label}: no snapshot fails while it is written to",
          not errors and built > 0,
          f"{errors[0]} after {built} snapshots" if errors else f"{built} snapshots")


print("\n1. hand color, set and cleared, on many targets")


def color(c, i):
    # Straight to apply, as _drain would, but from a thread of its own so it
    # can land mid-snapshot.
    if i == 0:
        for k in range(WIDE):
            c.apply({"type": "color", "target": f"t{k}", "color": [1, 0, 0],
                     "white": 0.5}, None)
    c.apply({"type": "color", "target": f"x{i % 7}", "color": [0, 1, 0],
             "clear": bool(i % 2)}, None)


collide("color_overrides", color)


print("\n2. hand levels and flashes")


def level(c, i):
    if i == 0:
        for k in range(WIDE):
            c.apply({"type": "level", "target": f"t{k}", "value": 0.5}, None)
            c.apply({"type": "flash", "target": f"t{k}", "on": True}, None)
    c.apply({"type": "level", "target": f"x{i % 7}", "value": 0.3,
             "clear": bool(i % 2)}, None)
    c.apply({"type": "flash", "target": f"x{i % 7}", "on": not i % 2}, None)


collide("level_overrides and flashing", level)


print("\n3. clients arriving and leaving")


def clients(c, i):
    if i == 0:
        for k in range(WIDE):
            c.add_client(servermod.Client(id=f"c{k}"))
    c.add_client(servermod.Client(id=f"x{i % 7}"))
    c.remove_client(f"x{(i + 3) % 7}")


collide("clients", clients)


print("\n4. the snapshot is every part's section, each key said once")
# Each part of the controller publishes its own section (`_public_*`). Merged
# into one dict, two parts naming the same key would have the later one win
# silently, and a part whose section is not merged would publish nothing.
controller = servermod.ShowController(EVENT)
parts = sorted(n for n in dir(controller) if n.startswith("_public_"))
sections = {n: getattr(controller, n)() for n in parts}
said: dict[str, list[str]] = {}
for name, section in sections.items():
    for key in section:
        said.setdefault(key, []).append(name)
twice = {k: v for k, v in said.items() if len(v) > 1}
check("no key is published by two parts", not twice, f"{twice}")
snap = controller.snapshot()
check("the snapshot is exactly every part's section, and its envelope",
      set(snap) == set(said) | {"type", "rev", "version"},
      f"missing {sorted(set(said) - set(snap))}, "
      f"extra {sorted(set(snap) - set(said) - {'type', 'rev', 'version'})}")
check("one section per part", len(parts) >= 11, parts)


finish("snapshot threads")
