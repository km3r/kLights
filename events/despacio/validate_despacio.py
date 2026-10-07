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

BASE = Path(__file__).parent          # events/despacio/
REPO = BASE.parent.parent             # lights/
QXW = str(BASE / "despacio.qxw")
QXF = str(REPO / "shared" / "fixtures" / "MingJie-MJ-OS-018-60W-Beam.qxf")
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
func_elems = {}
for fn in eng.findall(f"{NSW}Function"):
    funcs[int(fn.get("ID"))] = (fn.get("Type"), fn.get("Name"))
    func_elems[int(fn.get("ID"))] = fn

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
    if tag == "SpeedDial":
        # <Function FadeIn=.. FadeOut=.. Duration=..>ID</Function> -- the
        # attributes are SpeedMultiplier enum INDICES (0=not sent, 5=1/2,
        # 6=x1, 7=x2, 8=x4, 9=x8), not milliseconds. Out-of-range values are
        # accepted silently by QLC+ and would misscale every attached routine.
        for f in el.findall(f"{NSW}Function"):
            fid = int(f.text)
            if fid not in funcs:
                errors.append(f"SpeedDial '{el.get('Caption')}': missing function {fid}")
            for attr in ("FadeIn", "FadeOut", "Duration"):
                v = int(f.get(attr, 0))
                if not 0 <= v <= 10:
                    errors.append(f"SpeedDial '{el.get('Caption')}' fn {fid}: "
                                  f"{attr}={v} is not a SpeedMultiplier index (0-10)")
        # A PerStep chaser cannot be tapped -- Chaser::tap() early-returns
        # unless durationMode()==Common -- so attaching one is a silent no-op
        # for the tap button and fights its own designed per-step timing.
        for f in el.findall(f"{NSW}Function"):
            fn = func_elems.get(int(f.text))
            if fn is None:
                continue
            sm = fn.find(f"{NSW}SpeedModes")
            if sm is not None and sm.get("Duration") == "PerStep":
                errors.append(f"SpeedDial '{el.get('Caption')}': function {f.text} "
                              f"('{fn.get('Name')}') is a PerStep chaser -- tap tempo "
                              f"cannot drive it; remove it from the dial")
    if tag == "CueList":
        ch = el.find(f"{NSW}Chaser")
        if ch is None or not ch.text:
            errors.append(f"CueList '{el.get('Caption')}': no <Chaser> reference")
        elif int(ch.text) not in funcs:
            errors.append(f"CueList '{el.get('Caption')}': missing chaser {ch.text}")
        sm = el.find(f"{NSW}SlidersMode")
        if sm is not None and sm.text not in ("None", "Crossfade", "Steps"):
            # Anything else silently becomes "None" in QLC+ 5.
            errors.append(f"CueList '{el.get('Caption')}': SlidersMode "
                          f"{sm.text!r} not in None/Crossfade/Steps")
        if el.find(f"{NSW}Crossfade") is not None:
            errors.append(f"CueList '{el.get('Caption')}': <Crossfade> is QLC+ 4 "
                          f"only and is dropped by 5.x -- use <SlidersMode>")
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
#
# 2026-08-01 SELF-STOP BUG (found via reported "routines immediately turn
# themselves off"): a Chaser/Collection's step/member functions are started by
# the QLC+ engine as real, independent Functions -- each one fires the same
# global functionStarted signal a button press would. A SoloFrame reacts to
# that signal purely by function ID; it has no notion of "this came from
# inside a function I already count as running." So a Chaser/Collection whose
# own ID is a member (real OR mirror) of SoloFrame F, and whose step/member
# list ALSO includes a function that is itself a member (real or mirror) of
# that SAME frame F, kills itself the instant it advances into that step --
# the frame sees the step's functionStarted and dutifully stops every OTHER
# button in F, which includes the very button that just launched it.
#
# This hit 10 routines, all of which reuse "public" scenes/chasers (ones that
# are ALSO independently-selectable buttons) as their own steps, instead of
# following the rest of the show's convention of private per-effect step
# scenes (Lazy Circle/Rainbow Wheel/Ball Spiral's own "Step 1..N" scenes,
# which are never buttons anywhere and so can never collide):
#   - SHOW (Warm Up/Despacio Idle/Deep/Peak/Landing/Spiral): each Collection's
#     own color+movement members are mirrored into every category frame
#     (since SHOW used to be folded into both COLOR and MOVEMENT below), so
#     ANY frame owning one of those members as a real button would kill the
#     SHOW preset the instant that member started. Fix: SHOW no longer joins
#     COLOR/MOVEMENT (so no other frame carries a SHOW mirror at all), and
#     show-solo itself is zero-mirror/local-only -- it still gives the 6
#     presets mutual exclusivity, it just no longer reaches into (or gets
#     reached by) individual color/movement picks.
#   - Rise (95) / Iris (98): steps include Heads-Ball/Floor/Walls/Apex/Zenith,
#     which are real buttons in mh-aerial-solo (their own home frame) or
#     mirrors of it. Fix: pulled both into their own zero-mirror SoloFrame
#     (mh-aerial-extra-solo), leaving Apex/Cathedral/Zenith/Canopy Ring's
#     mesh membership in mh-aerial-solo untouched (they were never the
#     problem -- none of them step through another family member).
#   - Color Drift (21): steps directly through MH Red/Blue/Pink/Orange, real
#     buttons of mh-color-solo and mirrors of Color Drift's own home frame
#     mh-loops-solo. Fix: isolated into its own zero-mirror SoloFrame
#     (mh-colordrift-solo); Color Spin/Rainbow Wheel (mh-loops-solo's other
#     two members) don't have this problem and keep their COLOR mesh mirrors.
#   - Pin Drift (115): steps through Pin Amber/Rose/Magenta/Indigo/Teal/Sea,
#     which are its own siblings' REAL buttons in pin-solo -- pin-solo was
#     already zero-mirror, so this was a same-frame collision, not a
#     cross-family one. Fix: isolated into its own zero-mirror SoloFrame
#     (pin-drift-solo); the 6 solid pin colors stay in pin-solo unchanged.
#
# All 4 new isolated frames follow the same deliberate-zero-mirror pattern
# already used by corner-test-solo/mh-layer-solo/pin-ch0-solo/mh-pulse-solo
# below -- local exclusivity only, no cross-family reach in either direction.
SHOW = [39, 22, 40, 23, 41, 62]
COL1 = [0, 1, 2, 3, 4, 5, 26]
# 199 = Wall Graze (2026-07-30): held static, the only pose that rakes a beam
# down the full length of each wall. Every other outward-facing pose (Walls,
# orbit+-90) points into the head's own corner and terminates under a metre out.
COL2 = [6, 7, 8, 18, 35, 51, 199]
# 21 (Color Drift) deliberately excluded -- see the 2026-08-01 self-stop note
# above; it lives in its own isolated mh-colordrift-solo now.
COL3 = [27, 61]
# 121/126/129 = the floor sweeps (Ring / Wipe / Cross), added 2026-07-26 on
# column 4's own track-row buttons rather than borrowed pads elsewhere.
# 173/176 = Floor Bounce / Floor Breathe, added 2026-07-30, same visual home
# (mh-programs-solo) but wired to column 7's leftover track-row (column 4's
# own R/S/Activator triplet was already full) -- see README's Floor sweeps
# section.
COL4 = [19, 44, 49, 50, 60, 121, 126, 129, 173, 176]
# Ball Wave / Circle / Neighbor Scan / Prowl / Crowd Cascade (col8) are
# movement functions living outside cols 2/4 -- they join the same MOVEMENT
# mirror family (mutual mirrors with COL2/COL4/SHOW) even though their real
# buttons aren't physically in either of those columns.
COL_EXTRA_MOVE = [67, 78, 81, 86, 91]
# Aerial poses (col9) and split/bicolor statics (col10), added 2026-07-25.
# Aerial writes Pan/Tilt only -> MOVEMENT family; split writes the color wheel
# + shutter only -> COLOR family. Neither crosses into the other.
# 204 = Canopy Ring (2026-07-30), Floor Ring's mirror drawn on the canopy plane
# at apex_height -- an aerial pose, so it lives with the other aerials.
# 95 (Rise) / 98 (Iris) deliberately excluded -- see the 2026-08-01 self-stop
# note above; they live in their own isolated mh-aerial-extra-solo now.
COL_AERIAL = [92, 93, 94, 204]
COL_SPLIT = [103, 104, 105, 106, 107, 167, 168, 169]
# "Color Extras" frame (row 2, added 2026-07-30): the three color-wheel slots
# that had no static (Yellow / Green / Cyan), the 4-way Quad Spectrum, and Wheel
# Walk. COLOR family like the splits -- they write the wheel + shutter only.
# They live outside columns 1-8 because every APC grid/clip/scene pad in those
# physical columns is already consumed; their real buttons are on the remaining
# free track-row slots.
COL_COLOR_EXTRA = [205, 206, 207, 208, 217]
# Dark-move routines (Teleport / Apparition / Freeze Frame / Stutter), added
# 2026-07-29, own frame "Dark Moves (MH Dim low)". All 4 write Pan/Tilt (join
# MOVEMENT, same as Prowl/Crowd Cascade) AND continuously drive Dimmer (also
# join mh-extra-dim-solo's mirror list below), since the whole point of these
# routines is going dark while travelling and lighting only once still.
# Glitch / Ascension / Blink (2026-07-30) join the same family for the same
# reason: Glitch reuses Teleport's own scenes (147-154) with a new chaser
# only; Ascension/Blink are new unison dark-travel cycles (climb through
# crowd/ball/apex/zenith; carousel through floor_ring_0..3) built the same way.
COL_SNAP = [155, 156, 161, 166, 226, 235, 244]

# SHOW deliberately does NOT join either family below -- see the 2026-08-01
# self-stop note above. It used to (COLOR/MOVEMENT += SHOW), which is exactly
# what made every category frame that owned one of a SHOW preset's own
# color/movement members kill that preset the instant it started.
MOVEMENT = COL2 + COL4 + COL_EXTRA_MOVE + COL_AERIAL + COL_SNAP
COLOR = COL1 + COL3 + COL_SPLIT + COL_COLOR_EXTRA


def _mirrors(own, family):
    """Every family member this frame doesn't own a real button for."""
    return [f for f in dict.fromkeys(family) if f not in own]


EXPECTED = {
    "mh-color-solo":    (COL1, _mirrors(COL1, COLOR)),
    "mh-move-solo":     (COL2, _mirrors(COL2, MOVEMENT)),
    "mh-loops-solo":    (COL3, _mirrors(COL3, COLOR)),
    "mh-programs-solo": (COL4, _mirrors(COL4, MOVEMENT)),
    # Zero-mirror/local-only -- see the 2026-08-01 self-stop note above. The 6
    # presets still exclude each other; they no longer reach into (or can be
    # reached by) individual color/movement picks.
    "show-solo":        (SHOW, []),
    "mh-extra-move-solo": (COL_EXTRA_MOVE, _mirrors(COL_EXTRA_MOVE, MOVEMENT)),
    "mh-aerial-solo":   (COL_AERIAL, _mirrors(COL_AERIAL, MOVEMENT)),
    "mh-split-solo":    (COL_SPLIT, _mirrors(COL_SPLIT, COLOR)),
    "mh-colorx-solo":   (COL_COLOR_EXTRA, _mirrors(COL_COLOR_EXTRA, COLOR)),
    "mh-snap-solo":     (COL_SNAP, _mirrors(COL_SNAP, MOVEMENT)),
    # Corner Test (col6) is a setup/calibration utility, deliberately NOT
    # meshed into the cross-column solo system -- local exclusivity only.
    "corner-test-solo": ([45, 46, 47, 48], []),
    # Drift / Counter-Orbit (col11) are relative-EFX LAYERS: they are meant to
    # run ON TOP of whatever pose is held, adding an offset to it. Mirroring
    # them into the MOVEMENT family would make starting one cancel the very
    # pose it decorates, i.e. defeat the entire point. They only need to
    # exclude each other -- same deliberate-exclusion reasoning as
    # corner-test-solo above, for a different reason.
    # Shiver / Figure Eight / Diamond Weave (2026-07-30) are 3 more relative-EFX
    # layers -- same deliberate zero-mirror exclusion as Drift/Counter-Orbit.
    "mh-layer-solo": ([99, 100, 223, 224, 225], []),
    # Pinspots (col12) are on their own DMX channels (45/51) and share nothing
    # with the 4 heads, so they need no cross-family mesh at all -- only mutual
    # exclusivity between the color/pattern picks (static glow, the 6 fixed
    # colors, the drifting chaser, the smooth rainbow chase). Pin Breathe is
    # deliberately OUTSIDE this group -- it only writes channel 0 (dimmer),
    # which none of these color scenes touch anymore, so it can run
    # underneath any of them without a channel conflict.
    # 221 = Pin Split (2026-07-30), the first pin scene where the two fixtures
    # get different values -- a color pick like the rest, so it belongs here.
    # 115 (Pin Drift) deliberately excluded -- see the 2026-08-01 self-stop
    # note above; it lives in its own isolated pin-drift-solo now.
    "pin-solo": ([108, 109, 110, 111, 112, 113, 114, 143, 221], []),
    # Pin Breathe (146) and Pin Strobe (222) both drive the pinspots' channel 0
    # continuously, so they must exclude each other. That channel is BANDED on
    # this fixture (0-8 off / 9-134 white dimmer / 135-239 RGBW strobe / 240-255
    # full on), which is why the strobe isn't its own channel -- channel 5 is
    # Auto FX and stays parked at 0 -- and why the Pin Dim fader is range-limited
    # to 9-134 so it can never stray into the strobe band. Breathe writes 250 and
    # Strobe writes 180; HTP means the higher wins, hence the exclusivity.
    # Local only: no other family touches the pinspots' channels.
    "pin-ch0-solo": ([146, 222], []),
    # Slow Pulse / Medium Pulse (col 3) both write the shutter channel (6)
    # continuously, so they must exclude each other -- otherwise toggling one
    # off while the other is on strands the shutter at the loser's value. They
    # touch no channel any other family owns (the col 7 Strobe is a momentary
    # Flash, deliberately outside the mesh), so local exclusivity is enough.
    "mh-pulse-solo": ([28, 130], []),
    # Dim Chase / Spotlight / Prowl / Crowd Cascade (col8) all continuously
    # write the Dimmer channel (White Bump is a momentary Flash, deliberately
    # excluded from the solo mesh, same as Strobe) -- Prowl and Crowd Cascade
    # ALSO write Pan/Tilt so their real buttons live in mh-extra-move-solo
    # (the MOVEMENT family home); they get a mirror here purely so starting
    # either one stops a running Dim Chase/Spotlight dimmer-boost and vice
    # versa, without needing Dim Chase/Spotlight to join the MOVEMENT mesh.
    # Teleport/Apparition/Freeze Frame/Stutter (COL_SNAP) join this same
    # mirror list for the same reason -- they too drive Dimmer continuously.
    # 220 = MH Breathe (2026-07-30), the heads' answer to Pin Breathe: a slow
    # HTP swell to full and back on all four at once. Writes Dimmer only, so it
    # joins this group as a full member rather than needing MOVEMENT mirrors.
    "mh-extra-dim-solo": ([72, 77, 220], [86, 91] + COL_SNAP),
    # The 3 self-stop fixes (2026-08-01) -- each pulls one broken routine out
    # of a frame it used to share with one of its own steps. See the note
    # above EXPECTED for the mechanism. All 3 are deliberately zero-mirror,
    # same local-only reasoning as corner-test-solo/mh-layer-solo.
    "mh-colordrift-solo": ([21], []),
    "mh-aerial-extra-solo": ([95, 98], []),
    "pin-drift-solo": ([115], []),
}
found_solos = {}
for sf in vc.iter(f"{NSW}SoloFrame"):
    cap = sf.get("Caption")
    members, mirs = [], []
    for b in sf.findall(f"{NSW}Button"):
        fid = int(b.find(f"{NSW}Function").get("ID"))
        # A hidden mirror is identified by its "~"-prefixed caption (the same
        # signal gen_webui_config.py's walk_widgets() uses to hide these from
        # the phone UI), NOT by whether it has an <Input> -- a real button can
        # legitimately have no physical MIDI binding (webui/mouse-only, e.g.
        # Shiver/Glitch/Ascension added 2026-07-30) and must still count as
        # this SoloFrame's real member, not get miscounted as a mirror.
        caption = b.get("Caption") or ""
        (mirs if caption.startswith("~") else members).append(fid)
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

# --- VC geometry sweep ------------------------------------------------------
# QLC+ silently CLIPS anything that falls outside its parent frame -- a button
# whose Y sits past the frame's Height just isn't drawn, with no warning at load
# or save. This project has shipped that bug three times (2026-07-23 twice,
# 2026-07-26 once), always the same shape: buttons were added to a SoloFrame and
# the frame's own fixed WindowState Height was never grown to match.
#
# Mirrors are the deliberate exception: they are parked at Y >= 1000 precisely
# so they clip off-screen (they exist only to make a solo frame's exclusivity
# cover a function whose real button lives elsewhere). So the visible/clipped
# split is the check -- anything at Y < MIRROR_Y_FLOOR must actually fit.
MIRROR_Y_FLOOR = 1000
CANVAS = (1920, 1272)
GEOM_TAGS = (f"{NSW}Button", f"{NSW}Slider", f"{NSW}Frame", f"{NSW}SoloFrame",
             f"{NSW}CueList", f"{NSW}SpeedDial", f"{NSW}Label")


def _rect(el):
    w = el.find(f"{NSW}WindowState")
    if w is None:
        return None
    try:
        return tuple(int(w.get(k)) for k in ("X", "Y", "Width", "Height"))
    except (TypeError, ValueError):
        return None


def _label(el):
    return f"{el.tag.split('}')[1]} '{el.get('Caption')}' (ID {el.get('ID')})"


def _children(el):
    return [c for c in el if c.tag in GEOM_TAGS]


def _sweep(parent, parent_rect, path):
    kids = []
    for c in _children(parent):
        r = _rect(c)
        if r is None:
            continue
        x, y, w, h = r
        if y < MIRROR_Y_FLOOR:
            kids.append((c, r))
            # (a) does it fit inside its parent?
            if parent_rect is not None:
                pw, ph = parent_rect[2], parent_rect[3]
                if x < 0 or y < 0 or x + w > pw or y + h > ph:
                    errors.append(
                        f"geometry: {_label(c)} at ({x},{y},{w},{h}) overflows its parent "
                        f"{path} ({pw}x{ph}) -- QLC+ will clip it silently")
        else:
            # (b) mirrors must not share an off-screen slot (harmless visually,
            # but it means a build script mis-stacked them, which is how
            # duplicated/skipped mirrors happen).
            pass
        _sweep(c, r, f"{path} > {_label(c)}")

    # (c) siblings must not overlap each other
    for i in range(len(kids)):
        for j in range(i + 1, len(kids)):
            (ea, (ax, ay, aw, ah)), (eb, (bx, by, bw, bh)) = kids[i], kids[j]
            if ax < bx + bw and bx < ax + aw and ay < by + bh and by < ay + ah:
                errors.append(
                    f"geometry: {_label(ea)} and {_label(eb)} overlap inside {path}")

    # (d) duplicate mirror Y slots within one frame
    mirror_ys = Counter(_rect(c)[1] for c in _children(parent)
                        if _rect(c) and _rect(c)[1] >= MIRROR_Y_FLOOR)
    dupes = sorted(y for y, n in mirror_ys.items() if n > 1)
    if dupes:
        errors.append(
            f"geometry: {path} has multiple off-screen widgets stacked on the same "
            f"Y slot(s) {dupes} -- a build script mis-counted the mirror ladder")


for top in _children(vc):
    r = _rect(top)
    if r is None:
        continue
    if r[0] < 0 or r[1] < 0 or r[0] + r[2] > CANVAS[0] or r[1] + r[3] > CANVAS[1]:
        errors.append(f"geometry: {_label(top)} at {r} falls outside the "
                      f"{CANVAS[0]}x{CANVAS[1]} canvas")
    _sweep(top, r, _label(top))

# unreferenced functions
used = set()
for fn in eng.findall(f"{NSW}Function"):
    if fn.get("Type") in ("Chaser", "Collection"):
        used.update(int(st.text) for st in fn.findall(f"{NSW}Step"))
for el in vc.iter(f"{NSW}Button"):
    f = int(el.find(f"{NSW}Function").get("ID"))
    if f != 4294967295:
        used.add(f)
# A function can also be driven without a button: a CueList plays a chaser, and
# a Slider in Adjust mode drives a running function's attribute. Without these,
# the orphan report flags legitimately-wired functions and stops being useful.
for el in vc.iter(f"{NSW}CueList"):
    ch = el.find(f"{NSW}Chaser")
    if ch is not None and ch.text:
        used.add(int(ch.text))
for el in vc.iter(f"{NSW}Slider"):
    adj = el.find(f"{NSW}Adjust")
    if adj is not None and adj.get("Function"):
        used.add(int(adj.get("Function")))
orphans = set(funcs) - used
if orphans:
    print("Note: functions not reachable from VC or other functions:", sorted(orphans))

# --- Position data must be calibration-derived ------------------------------
# The defect this exists for (found 2026-07-30): Lazy Circle / Slow Sweep /
# Grand Sweep were absolute-DMX EFX centred on channel 127, and Heads Cross A/B
# were hand-picked absolute Scenes. A QLC+ EFX has ONE global centre and no
# per-fixture offset, so none of them could follow a rig whose heads have
# different calibrated ball points -- in "venue" mode two heads orbited the ball
# while the other two pointed ~180 deg away into their own corners. They fed
# Warm Up / Idle / Deep, i.e. three of the six SHOW looks, and nothing in the
# tooling could see it: aim_calc.py's self-test and the reachability report only
# examine poses aim_calc GENERATES, so anything hand-authored was invisible.
#
# Two checks, deliberately separate because the two failure shapes differ:
#   (a) every Scene that writes Pan/Tilt must be a function aim_calc.py owns;
#   (b) every EFX must be IsRelative -- an absolute EFX cannot be per-head
#       correct on this rig, full stop, so the only safe kind is one that writes
#       an offset onto whatever pose is already running.
# Both allow-lists are intentionally EMPTY. Adding an entry is a deliberate
# statement that a specific function's positions are hand-picked and will NOT
# track recalibration -- which is a real decision someone may need to make, but
# it should be an explicit, reviewed one, not a silent default.
HAND_PICKED_POSITION_FUNCTIONS = set()
ABSOLUTE_EFX_ALLOWED = set()

try:
    import aim_calc
    _targets = set(aim_calc.build_targets(aim_calc.compute_poses()))
except Exception as exc:                                   # pragma: no cover
    errors.append(f"could not import aim_calc to check position provenance: {exc}")
    _targets = None

if _targets is not None:
    # (a) Scenes writing the Pan (0) or Tilt (2) channel of any moving head.
    PT_CHANNELS = {0, 2}
    MH_FIXTURES = {0, 1, 2, 3}
    for fn in eng.findall(f"{NSW}Function"):
        if fn.get("Type") != "Scene":
            continue
        fid = int(fn.get("ID"))
        writes_position = False
        for fv in fn.findall(f"{NSW}FixtureVal"):
            if int(fv.get("ID")) not in MH_FIXTURES:
                continue
            vals = [int(v) for v in (fv.text or "").split(",") if v.strip()]
            if PT_CHANNELS & set(vals[0::2]):
                writes_position = True
                break
        if not writes_position:
            continue
        if fid not in _targets and fid not in HAND_PICKED_POSITION_FUNCTIONS:
            errors.append(
                f"Scene {fid} '{fn.get('Name')}' writes Pan/Tilt but is not in "
                f"aim_calc.py's TARGETS -- its positions are hand-picked and will "
                f"NOT follow recalibration. Derive it in build_targets(), or add "
                f"it to HAND_PICKED_POSITION_FUNCTIONS to accept that.")

    # ...and the mirror of the same rule: every TARGETS id must actually exist,
    # otherwise aim_calc.py is silently generating poses for functions the
    # workspace doesn't have (which is exactly the state this project was in on
    # 2026-07-30: TARGETS listed the Floor Bounce/Breathe scenes before they
    # were added). apply_to_qxw() fails loud on this, but only when someone
    # runs it -- preflight should catch it too.
    missing_targets = sorted(_targets - set(funcs))
    if missing_targets:
        errors.append(
            f"aim_calc.py TARGETS references function IDs not in the workspace: "
            f"{missing_targets} -- run aim_calc.py or reconcile build_targets()")

# (b) EFX must be relative-only.
for fn in eng.findall(f"{NSW}Function"):
    if fn.get("Type") != "EFX":
        continue
    fid = int(fn.get("ID"))
    rel = fn.find(f"{NSW}IsRelative")
    is_relative = rel is not None and (rel.text or "").strip() == "1"
    if not is_relative and fid not in ABSOLUTE_EFX_ALLOWED:
        errors.append(
            f"EFX {fid} '{fn.get('Name')}' has IsRelative=0. An absolute EFX has a "
            f"single global centre and cannot match per-head calibration -- make it "
            f"relative (a layer over a pose), rebuild it as a calibrated chaser, or "
            f"add it to ABSOLUTE_EFX_ALLOWED.")

print(f"MIDI inputs bound: {len(midi)}, widgets: {len(widget_ids)}, functions: {len(funcs)}")
if errors:
    print("\nERRORS:")
    for e in errors:
        print(" -", e)
    raise SystemExit(1)
print("\nALL CHECKS PASSED")
