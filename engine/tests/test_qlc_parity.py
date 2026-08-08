"""
Frame-level parity between QLC+ and the engine.

`test_library.py` checks that a ported pose encodes to the workspace's pan/tilt
and that a ported colour renders its colour bytes. Both look only at channels
somebody thought to name. This asks the complementary question over all 512:
where do the two stacks differ, and does every difference have a reason?

The classifier lives in `shared/tools/qlc_parity.py`; this pins its result.
Three claims:

  1. **Nothing is unexplained.** Every differing channel lands in a category
     that was derived -- held because it really is in the hold map, base because
     the engine really does emit that with no look selected, taper because
     rendering with the taper off really does change it.
  2. **The classifier is not vacuous.** A guard that cannot fail is worse than
     no guard, so a deliberately corrupted frame must come back `unexplained`.
  3. **The known gaps stay known.** The `dropped` set is pinned by name. A new
     one is a port regression and fails here rather than in a room.

Run: python engine/tests/test_qlc_parity.py
"""

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import library as libmod
from engine import rig as rigmod
from shared.tools import qlc_parity as parity

EVENT = REPO / "events" / "despacio"
failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


print("\n1. the comparison is broad enough to mean something")
diffs, sampled, unmatched = parity.run_check(EVENT)
looks = {s.entry.name for s in sampled}
counts: dict[str, int] = {}
for d in diffs:
    counts[d.category] = counts.get(d.category, 0) + 1

print(f"  {len(sampled)} moments from {len(looks)} looks, "
      f"{len(diffs)} differing channels")
check("most of the library is compared", len(looks) > 180, f"{len(looks)} looks")
check("chases are compared per STEP, not at one phase",
      len(sampled) > len(looks), f"{len(sampled)} moments from {len(looks)} looks")
check("every look matched a function in the workspace", not unmatched,
      f"{len(unmatched)} unmatched" + (f": {unmatched[:3]}" if unmatched else ""))


print("\n2. every difference is accounted for")
for category in parity.CATEGORIES:
    print(f"  {counts.get(category, 0):>6}  {category}")
unexplained = [d for d in diffs if d.category == "unexplained"]
check("nothing is unexplained", not unexplained,
      f"{len(unexplained)}" + (f"; first: {unexplained[0]}" if unexplained else ""))

# Each expected category must actually FIRE. A category that never matches is
# not evidence of agreement -- it is evidence the classifier never looked, and
# it would quietly absorb the day one of them stops happening.
for category in ("held", "base", "taper"):
    check(f"the {category!r} category is exercised", counts.get(category, 0) > 0,
          f"{counts.get(category, 0)} channels")


print("\n3. the classifier can actually fail")
# Corrupt one channel of a rendered frame in a way no category explains: a
# value that is neither the baseline, nor the untapered render, nor held, on a
# channel the scene never asserted.
rig = rigmod.load_rig(EVENT)
entry = next(e for e in libmod.load_entries(EVENT / "looks.json") if e.kind == "pose")
labels = parity.channel_labels(rig)
reference = parity.engine_frames(rig, None)
rendered = parity.engine_frames(rig, entry)
untapered = parity.engine_frames(rig, entry, taper=False)

mover = rig.movers[0]
victim = mover.index_of(rigmod.GOBO)
clean = parity.classify("probe", rig, reference, rendered, reference, untapered,
                        set(), labels)
check("an unperturbed probe is clean", not [d for d in clean if d.category == "unexplained"])

# Corrupt the untapered render identically, since in real use both come from
# the same look and the taper branch is entitled to compare them. Perturbing
# only one would fake a taper and prove nothing.
corrupt = {u: bytearray(f) for u, f in rendered.items()}
corrupt_untapered = {u: bytearray(f) for u, f in untapered.items()}
for frames in (corrupt, corrupt_untapered):
    frames[mover.universe][victim] = (rendered[mover.universe][victim] + 77) % 256
caught = parity.classify("probe", rig, reference, corrupt, reference,
                         corrupt_untapered, set(), labels)
check("a corrupted channel is reported as unexplained",
      any(d.category == "unexplained" and d.channel == victim + 1 for d in caught),
      f"{[str(d) for d in caught if d.category == 'unexplained'][:1]}")

# And a channel QLC+ asserts that the engine ignores must read as `dropped`,
# not be swallowed as baseline noise.
probe_channel = (mover.universe, mover.address_of(rigmod.GOBO))
dropped_probe = parity.classify("probe", rig,
                                {u: bytearray(f) for u, f in reference.items()}
                                | {mover.universe: bytearray(
                                    b if i != victim else 200
                                    for i, b in enumerate(reference[mover.universe]))},
                                rendered, reference, untapered,
                                {probe_channel}, labels)
check("a channel QLC+ asserts and the engine ignores reads as 'dropped'",
      any(d.category == "dropped" and d.channel == victim + 1 for d in dropped_probe))


print("\n4. the known gaps stay known")
# Both families are the same underlying cause: a QLC+ function that states more
# than one dimension ports only one of them. Pinned by fixture/role and by the
# look that exposes it, so a NEW gap fails here instead of surfacing in a room.
# Each appears twice over: once as the standalone step scene, once as that step
# of the chase that steps through it. Both are real looks a person can select,
# so the step index is stripped rather than the duplicate being suppressed.
KNOWN = {
    ("Crowd Cascade Step 1", "pan"), ("Crowd Cascade Step 2", "pan"),
    ("Crowd Cascade Step 3", "pan"), ("Crowd Cascade Step 4", "pan"),
    ("Crowd Cascade", "pan"), ("Drop", "color_wheel"),
}
dropped = [d for d in diffs if d.category == "dropped"]
found = {(re.sub(r"\[\d+\]$", "", d.look), d.role) for d in dropped}
surprises = sorted(found - KNOWN)
for look, role in sorted(found):
    print(f"  {look:<24} {role}")
check("no gap beyond the two documented families", not surprises,
      f"new: {surprises}" if surprises else
      f"{len(dropped)} channels across {len(KNOWN)} known cases")

# What the two families ARE, so this reads as a recorded decision rather than
# an oversight:
#   * The Crowd Cascade steps write a position for THREE of four heads.
#     `port_library.offsets_for` refuses a partial pose on purpose -- inventing
#     the fourth head's aim would put a beam somewhere nobody asked for -- so
#     the chase keeps its levels and loses its cascade of positions.
#   * "Drop" is a two-step chase: a white bump, then a spotlight. Only one step
#     carries colour, and `port_color_chaser` needs two, so it ported as a level
#     chase and the white bump lost its white.
check("the engine never contradicts a channel it does model",
      all(d.engine == parity.engine_frames(rig, None)[d.universe][d.channel - 1]
          for d in dropped),
      "every dropped channel sits at the engine's baseline, so the port is "
      "silent about it rather than asserting something else")


print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("qlc parity: all checks pass")
