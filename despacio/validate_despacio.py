"""Structural validator for the despacio workspace + fixture def.

Checks (fails with exit 1 on any error):
  * QXF: mode channel refs defined, channel numbers contiguous, capability
    ranges non-overlapping / non-inverted.
  * QXW patch: qxw Channels matches the qxf mode width, no DMX span overlaps.
  * Deleted pin-only functions really gone.
  * Scene FixtureVal refs point at real fixtures / in-range channels;
    chaser/collection/EFX step refs resolve.
  * Virtual console: button function refs resolve, slider channels in range,
    no duplicate MIDI input channels, no duplicate widget IDs.
  * Mirror mesh: at most one input-bearing button per function (the Year-3
    asymmetric-solo bug), and each SoloFrame contains exactly the expected
    visible-member + hidden-mirror function IDs.

Paths are resolved relative to this file, so it runs from anywhere.
"""
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

BASE = Path(__file__).parent
QXW = str(BASE / "despacio.qxw")
QXF = str(BASE / "fixtures" / "MingJie-MJ-OS-018-60W-Beam.qxf")
NSW = "{http://www.qlcplus.org/Workspace}"
NSF = "{http://www.qlcplus.org/FixtureDefinition}"

errors = []

# --- QXF ---
fx = ET.parse(QXF).getroot()
chan_names = {c.get("Name") for c in fx.findall(f"{NSF}Channel")}
modes = {}
for m in fx.findall(f"{NSF}Mode"):
    refs = [c.text for c in m.findall(f"{NSF}Channel")]
    modes[m.get("Name")] = refs
    for r in refs:
        if r not in chan_names:
            errors.append(f"QXF mode {m.get('Name')}: channel '{r}' not defined")
    nums = sorted(int(c.get("Number")) for c in m.findall(f"{NSF}Channel"))
    if nums != list(range(len(nums))):
        errors.append(f"QXF mode {m.get('Name')}: channel numbers not contiguous: {nums}")
print("QXF OK: modes", {k: len(v) for k, v in modes.items()})

# capability range checks
for c in fx.findall(f"{NSF}Channel"):
    caps = [(int(cap.get("Min")), int(cap.get("Max"))) for cap in c.findall(f"{NSF}Capability")]
    last = -1
    for mn, mx in caps:
        if mn <= last:
            errors.append(f"QXF channel {c.get('Name')}: overlapping capability at {mn}")
        if mx < mn:
            errors.append(f"QXF channel {c.get('Name')}: inverted range {mn}-{mx}")
        last = mx

# --- QXW ---
ws = ET.parse(QXW).getroot()
eng = ws.find(f"{NSW}Engine")

fixtures = {}
spans = []
for f in eng.findall(f"{NSW}Fixture"):
    fid = int(f.find(f"{NSW}ID").text)
    addr = int(f.find(f"{NSW}Address").text)
    nch = int(f.find(f"{NSW}Channels").text)
    mode = f.find(f"{NSW}Mode").text
    model = f.find(f"{NSW}Model").text
    fixtures[fid] = (addr, nch, model, mode)
    spans.append((fid, addr, addr + nch - 1))
    if model == "MJ-OS-018 60W Beam" and len(modes.get(mode, [])) != nch:
        errors.append(f"Fixture {fid}: qxw Channels={nch} but qxf mode '{mode}' has {len(modes.get(mode, []))}")

spans.sort(key=lambda s: s[1])
for a, b in zip(spans, spans[1:]):
    if b[1] <= a[2]:
        errors.append(f"DMX overlap: fixture {a[0]} ({a[1]}-{a[2]}) vs {b[0]} ({b[1]}-{b[2]})")
print("Patch spans (0-idx):", spans)

DELETED_PIN_FUNCS = [11, 12, 13, 14, 15, 16, 17, 20, 29, 30, 31, 32, 34, 36, 37, 38]
present = [fid for fid in DELETED_PIN_FUNCS if fid in
           {int(fn.get("ID")) for fn in eng.findall(f"{NSW}Function")}]
if present:
    errors.append(f"Pin-only functions should be deleted but still present: {present}")

funcs = {}
for fn in eng.findall(f"{NSW}Function"):
    funcs[int(fn.get("ID"))] = (fn.get("Type"), fn.get("Name"))

# scene fixture/channel refs
for fn in eng.findall(f"{NSW}Function"):
    ftype = fn.get("Type")
    if ftype == "Scene":
        for fv in fn.findall(f"{NSW}FixtureVal"):
            fid = int(fv.get("ID"))
            if fid not in fixtures:
                errors.append(f"Scene {fn.get('Name')}: unknown fixture {fid}")
                continue
            vals = [int(x) for x in fv.text.split(",")]
            if len(vals) % 2:
                errors.append(f"Scene {fn.get('Name')}: odd ch,val list on fixture {fid}")
            for ch in vals[::2]:
                if ch >= fixtures[fid][1]:
                    errors.append(f"Scene {fn.get('Name')}: fixture {fid} channel {ch} out of range")
    elif ftype in ("Chaser", "Collection"):
        for st in fn.findall(f"{NSW}Step"):
            ref = int(st.text)
            if ref not in funcs:
                errors.append(f"{ftype} {fn.get('Name')}: step references missing function {ref}")
    elif ftype == "EFX":
        for ef in fn.findall(f"{NSW}Fixture"):
            fid = int(ef.find(f"{NSW}ID").text)
            if fid not in fixtures:
                errors.append(f"EFX {fn.get('Name')}: unknown fixture {fid}")

# --- VC ---
vc = ws.find(f"{NSW}VirtualConsole")
midi = []
widget_ids = []
for el in vc.iter():
    tag = el.tag.replace(NSW, "")
    if tag in ("Button", "Slider", "Frame", "SoloFrame"):
        if el.get("ID") is not None:
            widget_ids.append((tag, int(el.get("ID"))))
    if tag == "Button":
        f = el.find(f"{NSW}Function")
        fid = int(f.get("ID"))
        if fid != 4294967295 and fid not in funcs:
            errors.append(f"Button '{el.get('Caption')}': missing function {fid}")
    if tag == "Slider":
        lvl = el.find(f"{NSW}Level")
        if lvl is not None:
            for ch in lvl.findall(f"{NSW}Channel"):
                fid = int(ch.get("Fixture"))
                cn = int(ch.text)
                if fid not in fixtures:
                    errors.append(f"Slider '{el.get('Caption')}': unknown fixture {fid}")
                elif cn >= fixtures[fid][1]:
                    errors.append(f"Slider '{el.get('Caption')}': fixture {fid} ch {cn} out of range")
    if tag == "Input" and el.get("Universe") == "2":
        midi.append((int(el.get("Channel")), el))

dup = [ch for ch, n in Counter(ch for ch, _ in midi).items() if n > 1]
if dup:
    errors.append(f"Duplicate MIDI input channels: {dup}")

# --- Mirror mesh checks ---
# (a) every function has at most ONE input-bearing button (the Year-3 asymmetric-solo bug)
input_buttons = Counter()
for el in vc.iter(f"{NSW}Button"):
    fid = int(el.find(f"{NSW}Function").get("ID"))
    if fid != 4294967295 and el.find(f"{NSW}Input") is not None:
        input_buttons[fid] += 1
multi = {f: n for f, n in input_buttons.items() if n > 1}
if multi:
    errors.append(f"Functions with >1 input-bearing button: {multi}")

# (b) each solo frame contains the exact expected mirror function IDs, all without Input
SHOW = [39, 22, 40, 23, 41]
COL1 = [0, 1, 2, 3, 4, 5, 26]
COL2 = [6, 7, 8, 18, 35, 51]
COL3 = [21, 27, 61]
COL4 = [19, 44, 49, 50, 60]
# Ball Wave / Circle / Neighbor Scan (col8) are movement functions living
# outside cols 2/4 -- they join the same MOVEMENT mirror family (mutual
# mirrors with COL2/COL4/SHOW) even though their real buttons aren't
# physically in either of those columns.
COL_EXTRA_MOVE = [67, 78, 81]
EXPECTED = {
    "mh-color-solo":    (COL1, COL3 + SHOW),
    "mh-move-solo":     (COL2, COL4 + COL_EXTRA_MOVE + SHOW),
    "mh-loops-solo":    (COL3, COL1 + SHOW),
    "mh-programs-solo": (COL4, COL2 + COL_EXTRA_MOVE + SHOW),
    "show-solo":        (SHOW, COL1 + COL2 + COL3 + COL4 + COL_EXTRA_MOVE),
    "mh-extra-move-solo": (COL_EXTRA_MOVE, COL2 + COL4 + SHOW),
    # Corner Test (col6) is a setup/calibration utility, deliberately NOT
    # meshed into the cross-column solo system -- local exclusivity only.
    "corner-test-solo": ([45, 46, 47, 48], []),
    # Dim Chase / Spotlight (col8) both touch ONLY the Dimmer channel and
    # nothing else in the show writes it continuously (White Bump is a
    # momentary Flash, deliberately excluded from the solo mesh, same as
    # Strobe) -- local exclusivity between the two of them is enough, no
    # cross-column mirrors needed.
    "mh-extra-dim-solo": ([72, 77], []),
}
found_solos = {}
for sf in vc.iter(f"{NSW}SoloFrame"):
    cap = sf.get("Caption")
    members, mirs = [], []
    for b in sf.findall(f"{NSW}Button"):
        fid = int(b.find(f"{NSW}Function").get("ID"))
        (members if b.find(f"{NSW}Input") is not None else mirs).append(fid)
    found_solos[cap] = (members, mirs)
for cap, (exp_members, exp_mirrors) in EXPECTED.items():
    if cap not in found_solos:
        errors.append(f"Missing solo frame: {cap}")
        continue
    members, mirs = found_solos[cap]
    if sorted(members) != sorted(exp_members):
        errors.append(f"{cap}: visible members {sorted(members)} != expected {sorted(exp_members)}")
    if sorted(mirs) != sorted(exp_mirrors):
        errors.append(f"{cap}: mirrors {sorted(mirs)} != expected {sorted(exp_mirrors)}")
extra_solos = set(found_solos) - set(EXPECTED)
if extra_solos:
    errors.append(f"Unexpected solo frames: {extra_solos}")
print("Mirror mesh:", {c: (len(m), len(mi)) for c, (m, mi) in found_solos.items()})

dupw = [w for w, n in Counter(i for _, i in widget_ids).items() if n > 1]
if dupw:
    errors.append(f"Duplicate widget IDs: {dupw}")

# unreferenced functions
used = set()
for fn in eng.findall(f"{NSW}Function"):
    if fn.get("Type") in ("Chaser", "Collection"):
        used.update(int(st.text) for st in fn.findall(f"{NSW}Step"))
for el in vc.iter(f"{NSW}Button"):
    f = int(el.find(f"{NSW}Function").get("ID"))
    if f != 4294967295:
        used.add(f)
orphans = set(funcs) - used
if orphans:
    print("Note: functions not reachable from VC or other functions:", sorted(orphans))

print(f"MIDI inputs bound: {len(midi)}, widgets: {len(widget_ids)}, functions: {len(funcs)}")
if errors:
    print("\nERRORS:")
    for e in errors:
        print(" -", e)
    raise SystemExit(1)
print("\nALL CHECKS PASSED")
