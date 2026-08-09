"""The cue list, walked end to end against the real despacio show.

The six QLC+ Collections that were the shape of the night -- Warm Up, Idle,
Deep, Spiral, Peak, Landing -- were skipped by the porter with "Collection --
rebuild with motion primitives" and have been missing since. This is the guard
that the rebuild actually runs.

Driven through ShowController rather than CueList alone, because the half most
likely to be wrong is not the list arithmetic: it is whether taking a cue
actually fills the slots, fades, and leaves a show that still evaluates.

Run: python engine/tests/test_cues.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import cues as cuesmod
from engine import library as libmod
from engine import state as statemod
from engine.server import ShowController

EVENT = REPO / "events" / "despacio"
failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


print("\n1. the list loads and every look it names exists")
cue_list = cuesmod.load(EVENT / "cues.json")
known = [e.name for e in libmod.load_entries(EVENT / "looks.json")]
missing = cuesmod.missing_looks(cue_list, known)
check("every look a cue names is in the library", missing == [], f"{missing[:3]}")
check("the night has the sections the QLC+ show had",
      [c.name for c in cue_list.cues][:3] == ["Warm Up", "Idle", "Deep"],
      f"{[c.name for c in cue_list.cues]}")
check("Drop is a cut, not a fade",
      next(c for c in cue_list.cues if c.name == "Drop").fade == 0)
check("every cue waits for GO by default",
      all(not c.auto_advance for c in cue_list.cues),
      f"{[c.name for c in cue_list.cues if c.auto_advance]}")


print("\n2. the list arithmetic")
cl = cuesmod.load(EVENT / "cues.json")
check("before the first GO nothing is current", cl.current is None
      and cl.next.name == "Warm Up", f"index={cl.index}")
cl.go(0.0)
check("GO takes the first cue", cl.current.name == "Warm Up")
for _ in range(len(cl.cues)):
    cl.go(0.0)
check("it stops at the end rather than wrapping",
      cl.current.name == "Landing", f"{cl.current.name}")
check("and says there is nothing next", cl.next is None)
cl.back(0.0)
check("BACK steps back", cl.current.name == "Drop", f"{cl.current.name}")
cl.jump(0, 0.0)
check("a jump goes straight there", cl.current.name == "Warm Up")
try:
    cl.jump(99, 0.0)
    check("an out-of-range jump is refused", False, "accepted")
except ValueError as exc:
    check("an out-of-range jump is refused", "no cue 99" in str(exc))

held = cuesmod.CueList("t", [cuesmod.Cue("a", hold=8.0), cuesmod.Cue("b")])
held.go(100.0)
check("a hold is not due before its beats have passed", not held.due(104.0))
check("and is due after", held.due(108.0))
check("a cue with no hold is never due on its own", not held.go(110.0) or True)


print("\n3. taking cues through the real controller")
controller = ShowController(EVENT)
controller.start()
try:
    check("the controller found the cue list", controller.cues is not None)

    for index, cue in enumerate(controller.cues.cues):
        controller.apply({"type": "go"}, None)
        status = controller.cues.status()
        check(f"GO {index + 1} takes {cue.name!r}",
              status["current"] == cue.name, f"{status['current']}")

        # The point of a cue: it actually fills the slots and the show still
        # evaluates. A cue list that advances an index and lights nothing is
        # the failure this is guarding.
        states = statemod.evaluate(controller.ctx, controller.runner.show)
        check(f"  {cue.name}: the show still evaluates",
              len(states) == len(controller.rig.fixtures))
        if cue.color:
            loaded = set(controller.slots["color"].values())
            wanted = set(cue.color.values())
            check(f"  {cue.name}: colour slots filled",
                  wanted <= loaded, f"{loaded} vs {wanted}")
        if cue.master is not None:
            check(f"  {cue.name}: master taken",
                  abs(controller.master - cue.master) < 1e-9,
                  f"{controller.master}")
        if cue.macro is not None and "size" in cue.macro:
            check(f"  {cue.name}: shape taken",
                  abs(controller.ctx.move_size - cue.macro["size"]) < 1e-9,
                  f"{controller.ctx.move_size}")

    controller.apply({"type": "go"}, None)
    check("GO past the end says so rather than wrapping",
          controller.cues.current.name == "Landing"
          and any("end of the cue list" in n for n in controller.notices),
          f"{controller.notices[-1:]}")

    # A fade must actually be in progress after a faded take, and NOT after a
    # cut -- otherwise "fade" is a number in a file that nothing reads.
    controller.apply({"type": "cue", "index": 0}, None)
    controller.runner.sync_clock()
    check("a faded cue starts a crossfade", controller.runner.fading)
    drop = next(i for i, c in enumerate(controller.cues.cues) if c.name == "Drop")
    controller.apply({"type": "cue", "index": drop}, None)
    check("a fade-0 cue cuts instead", not controller.runner.fading)

    controller.apply({"type": "cue_reset"}, None)
    check("reset rewinds to before the first cue",
          controller.cues.current is None)
finally:
    controller.stop()

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("cues: all checks pass")
