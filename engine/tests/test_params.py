"""The parameter descriptor: what it clamps, and what it refuses.

A `Param` is the single declaration a range now has. Before it, a range was
three unrelated literals -- a clamp in `_cmd_macro`, a `Spec` in `config.py`,
and a slider's arguments in `Move.tsx` -- and nothing kept them in step.

The interesting assertions here are the failure paths, because the whole value
of the class is in where it draws the line between "clamp it and carry on" and
"refuse this". Getting that line wrong in either direction is a real show
failure: a refused command drops the three good parameters alongside the bad
one, and a silently-dropped key produces a slider that appears to work and
changes nothing.

Run: python engine/tests/test_params.py
"""

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import params as parammod
from engine.params import MACROS, Param, ParamError, resolve

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


print("\n1. a number clamps rather than refusing")
radius = Param("radius_deg", "Radius", 12.0, min=0.0, max=45.0)
check("above the max clamps to the max", radius.coerce(90) == 45.0)
check("below the min clamps to the min", radius.coerce(-5) == 0.0)
check("inside the range is untouched", radius.coerce(12.5) == 12.5)

print("\n2. the things clamping cannot fix are refused")
try:
    radius.coerce("wide")
    check("text is refused", False, "accepted")
except ParamError as exc:
    check("text is refused", "must be a number" in str(exc))

# bool is a subclass of int in Python, so a bare isinstance check lets `true`
# satisfy a numeric field -- the same trap `config._check` documents, and the
# reason this assertion exists rather than being assumed.
try:
    radius.coerce(True)
    check("true/false is refused for a number", False, "accepted")
except ParamError as exc:
    check("true/false is refused for a number", "number" in str(exc))

shape = Param("shape", "Shape", "sine", kind="choice", choices=("sine", "ramp"))
try:
    shape.coerce("square")
    check("an unlisted choice is refused", False, "accepted")
except ParamError as exc:
    check("an unlisted choice is refused", "sine" in str(exc))

colour = Param("tint", "Tint", (1.0, 0.0, 0.0), kind="color")
try:
    colour.coerce([1.0, 0.0])
    check("a two-component colour is refused", False, "accepted")
except ParamError as exc:
    check("a two-component colour is refused", "r, g, b" in str(exc))
check("a colour clamps each component",
      colour.coerce([2.0, -1.0, 0.5]) == (1.0, 0.0, 0.5))

print("\n3. an integer parameter really is whole")
count = Param("count", "Count", 4, kind="integer", min=1, max=16)
check("rounds rather than truncating", count.coerce(4.6) == 5)
check("and is an int, not a float", isinstance(count.coerce(4.6), int))

print("\n4. resolve layers over the declared defaults")
pair = (radius, count)
check("an empty override gives the defaults",
      resolve(pair) == {"radius_deg": 12.0, "count": 4})
check("a partial override leaves the other default alone",
      resolve(pair, {"count": 9})["radius_deg"] == 12.0)
check("and clamps what it is given",
      resolve(pair, {"radius_deg": 999})["radius_deg"] == 45.0)
check("defaults() agrees with resolve() on an empty override",
      parammod.defaults(pair) == resolve(pair))

print("\n5. an unknown key is refused from a command, dropped from a file")
# The two callers genuinely differ. A typo from the console means the UI and the
# engine disagree about what a routine has; a stale key in a hand-edited
# parametric_looks.json at a venue must not stop the room lighting up.
try:
    resolve(pair, {"radius": 3})
    check("strict refuses an unknown key", False, "accepted")
except ParamError as exc:
    check("strict refuses an unknown key", "no parameter named" in str(exc))
    check("and names what it does have", "radius_deg" in str(exc))
check("lenient drops it and keeps the defaults",
      resolve(pair, {"radius": 3}, strict=False)["radius_deg"] == 12.0)
check("lenient still applies the keys it recognises",
      resolve(pair, {"radius": 3, "count": 7}, strict=False)["count"] == 7)

print("\n6. a malformed Param fails on import, not at a venue")
for label, kwargs in (("an unknown kind", {"kind": "slider"}),
                      ("a choice with no choices", {"kind": "choice"}),
                      ("min above max", {"min": 10.0, "max": 1.0})):
    try:
        Param("x", "X", 0.0, **kwargs)
        check(f"{label} is refused", False, "accepted")
    except ParamError:
        check(f"{label} is refused", True)

print("\n7. the descriptor the console receives")
pub = parammod.SIZE.public()
check("carries a label, not just a wire name",
      pub["label"] == "Size" and pub["name"] == "size")
check("carries the range the engine will clamp to",
      pub["min"] == 0.0 and pub["max"] == 3.0)
# The UI branches on presence, so a null and a missing key would be two
# spellings of "unbounded" -- and only one of them is what the TypeScript says.
check("omits absent keys rather than sending null",
      "choices" not in pub and all(v is not None for v in pub.values()))
check("a choice publishes its choices", "choices" in shape.public())

print("\n8. the shape macros are declared once, here")
check("all four are present", len(MACROS) == 4)
check("every one has a sentence under it", all(m.help for m in MACROS))
check("every one has a step, since they are all sliders",
      all(m.step is not None for m in MACROS))
# These are the ranges `_cmd_macro` used to hardcode and `Move.tsx` used to
# repeat. Pinned so that moving one without the other fails here.
check("size is 0..3", (parammod.SIZE.min, parammod.SIZE.max) == (0.0, 3.0))
check("spread is -1..1", (parammod.SPREAD.min, parammod.SPREAD.max) == (-1.0, 1.0))
check("centre bearing is +/-180",
      (parammod.CENTER_BEARING.min, parammod.CENTER_BEARING.max) == (-180.0, 180.0))
check("centre elevation is +/-90",
      (parammod.CENTER_ELEV.min, parammod.CENTER_ELEV.max) == (-90.0, 90.0))
check("their defaults are the identity",
      resolve(MACROS) == {"size": 1.0, "spread": 0.0, "bearing": 0.0,
                          "elev": 0.0})

print("\n9. a number that is not a number is refused, never clamped")
# `json.loads` accepts NaN and Infinity, and `max`/`min` pass NaN straight
# through -- so a clamp alone let NaN reach a frame, which then raised on its way
# to a DMX integer, every frame, until someone found the reset.
for bad in (float("nan"), float("inf"), float("-inf")):
    for label, call in (
        ("coerce", lambda: parammod.SIZE.coerce(bad)),
        ("clamp against a rig's reach",
         lambda: parammod.clamp(parammod.CENTER_BEARING, bad,
                                {"bearing": (-77.0, 198.0)})),
        ("resolve", lambda: resolve(MACROS, {"spread": bad})),
    ):
        try:
            call()
            check(f"{label} refuses {bad}", False, "accepted")
        except ParamError as exc:
            check(f"{label} refuses {bad}", "finite" in str(exc), str(exc))

print("\n10. a musical argument is marked on its declaration")
from engine import blocks as blocksmod                          # noqa: E402
cycles = [p for params in blocksmod.PARAMS.values() for p in params
          if p.name == "bars"]
check("every block's cycle length is musical, so vary leaves it alone",
      cycles and all(p.musical for p in cycles), f"{len(cycles)} cycles")
check("and nothing else is -- a shape number marked musical is never varied",
      not any(p.musical for params in blocksmod.PARAMS.values()
              for p in params if p.name != "bars"))
check("musical is engine-side only, not published to the console",
      "musical" not in cycles[0].public())

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("params: all checks pass")
