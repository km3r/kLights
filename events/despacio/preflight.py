"""One-command venue-readiness check for the despacio show.

Run this BEFORE leaving home (sanity) and again AT THE VENUE after patch +
calibration, before going live. It answers a single question -- "is this safe
to run tonight?" -- by rolling up every other check plus the venue-specific
guardrails that are easy to forget under setup pressure:

  1. aim_calc.py's math self-test (calibration round-trips, both mount modes).
  2. validate_despacio.py (DMX patch, VC solo-mesh, MIDI, function refs).
  3. The ACTIVE mount_mode, printed loudly.
       - FAIL if mount_mode == "venue" but any head is still uncalibrated
         (you'd go live aiming by geometry guess, not by verified readings).
       - WARN if mount_mode == "table" (that's the bench-test mode -- flip it
         to "venue" in despacio_config.json once mounted at the venue).
  4. The fixture def is installed in the QLC+ user fixtures dir AND is
     byte-identical to the project copy (divergent copies silently load a
     stale fixture -- a documented gotcha).
  5. At least one recent backups/ snapshot exists (aim_calc.py writes these).
  6. webui/gen_webui_config.py --check -- the mobile virtual console's layout
     (webui/ui_layout.js) still matches despacio.qxw, and every routine that
     partially/inconsistently writes an HTP dimmer channel is classified
     (dimPark or dimParkExempt) -- an unclassified one means a new dim
     routine could ship without the phone UI's auto-park handling it.
  7. aim_report.json (written by aim_calc.py's POSE REACHABILITY section) --
     WARN if any pose is flagged "significant" (a real range/calibration
     problem, not the routine minor clamps), and WARN if the report's mode
     doesn't match the active mount_mode (stale -- aim_calc.py hasn't been
     rerun since the last mode switch).
  8. Fader park positions that silently gate whole groups of routines:
       - WARN if MH Dim is parked too high for the HTP dimmer routines to read
         (they boost ABOVE the fader; park it at 255 and they're all no-ops).
       - NOTE the PT Speed park value, since no scene writes channel 9 and its
         direction is still unverified on this fixture.

Exit code 0 = PASS (safe), non-zero = FAIL (do not go live until fixed).
Pure stdlib; safe to run any time -- it never writes despacio.qxw.
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent          # events/despacio/
REPO = HERE.parent.parent             # lights/
FIXTURE_LIB = REPO / "shared" / "fixtures"
QXF_NAME = "MingJie-MJ-OS-018-60W-Beam.qxf"

CONFIG_PATH = HERE / "despacio_config.json"
AIM_REPORT_PATH = HERE / "aim_report.json"
VALIDATOR = HERE / "validate_despacio.py"
QXF_PROJECT = FIXTURE_LIB / QXF_NAME
QXF_INSTALLED = Path.home() / "QLC+" / "Fixtures" / QXF_NAME
BACKUP_DIR = HERE / "backups"
WEBUI_GENERATOR = HERE / "webui" / "gen_webui_config.py"

failures = []   # hard stops -- exit non-zero
warnings = []   # worth seeing, not blocking


def check(label, ok, detail="", *, warn=False):
    mark = "OK  " if ok else ("WARN" if warn else "FAIL")
    print(f"  [{mark}] {label}" + (f" -- {detail}" if detail else ""))
    if not ok:
        (warnings if warn else failures).append(f"{label}: {detail}" if detail else label)


print("=" * 60)
print("  DESPACIO PRE-FLIGHT CHECK")
print("=" * 60)

# --- 1. aim_calc self-test -----------------------------------------------
try:
    import aim_calc
    aim_calc._self_test()
    check("aim_calc.py math self-test", True)
except SystemExit as e:                     # a failed assert inside is fine to catch too
    check("aim_calc.py math self-test", False, str(e))
except Exception as e:
    check("aim_calc.py math self-test", False, f"{type(e).__name__}: {e}")
    aim_calc = None

# --- 2. structural validator ---------------------------------------------
res = subprocess.run([sys.executable, str(VALIDATOR)], capture_output=True, text=True)
val_ok = res.returncode == 0
check("validate_despacio.py (patch / VC mesh / MIDI)", val_ok,
      "" if val_ok else "see details below")
if not val_ok:
    # Surface the validator's own ERROR lines so the failure is actionable here.
    tail = [ln for ln in res.stdout.splitlines() if ln.strip().startswith("-")]
    for ln in (tail or res.stdout.splitlines()[-6:]):
        print("         " + ln)
    if res.stderr.strip():
        print("         " + res.stderr.strip().splitlines()[-1])

# --- 3. active mount mode + calibration state ----------------------------
cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
mode = cfg.get("mount_mode")
cal = cfg.get("calibrated_ball_dmx", {}).get(mode, [])
uncal = [i + 1 for i, c in enumerate(cal) if c is None]

print(f"\n  >>> ACTIVE MOUNT_MODE = {mode!r} <<<\n")

if mode in ("venue", "hung"):
    # Both are real installs (sideways vs. actually hung upside-down), so both
    # get the same hard gate: shipping a show with an uncalibrated head means
    # that head aims by geometry guess alone.
    check(f"{mode} mode: all 4 heads calibrated", not uncal,
          "" if not uncal else f"heads {uncal} still null in calibrated_ball_dmx[{mode!r}]")
elif mode == "table":
    check("mount_mode is 'table' (BENCH mode)", False, warn=True,
          detail="switch to 'venue' (sideways) or 'hung' (upside-down) in "
                 "despacio_config.json once mounted on site")
else:
    check("mount_mode recognized", False, f"unexpected mount_mode {mode!r}")

# --- 3b. pose reachability report (aim_calc.py's aim_report.json) --------
# Advisory, not a hard gate -- a "significant" flag means some pose is
# meaningfully clamped/rounded away from its geometric target under the
# current calibration (a real range problem worth a look before going live),
# but it doesn't mean the show is broken (most flagged poses are stylistic
# sweep positions -- see unreachable_poses()'s docstring in aim_calc.py).
if not AIM_REPORT_PATH.exists():
    check("aim_report.json present", False, warn=True,
          detail="not found -- run aim_calc.py at least once to generate it")
else:
    report = json.loads(AIM_REPORT_PATH.read_text(encoding="utf-8"))
    stale = report.get("mode") != mode
    check("aim_report.json matches active mount_mode", not stale, warn=True,
          detail="" if not stale else
          f"report is for mode {report.get('mode')!r}, active mode is {mode!r} -- "
          f"rerun aim_calc.py (or Save & Recalculate in the GUI)")
    significant = [e for e in report.get("unreachable", []) if e.get("severity") == "significant"]
    check("no significant pose-reachability warnings", not significant, warn=True,
          detail="" if not significant else
          "; ".join(f"head {e['head']+1} {e['pose']} off by "
                    f"{max(e['errorBearingDeg'], e['errorElevDeg'])}deg"
                    for e in significant))

# --- 3c. bearing-channel invert must be uniform across heads ------------
# Circle needs a UNIFORM pan direction across all 4 heads (a cycle,
# MH1->MH2->MH3->MH4->MH1) -- a MIXED setting is the one thing that breaks
# it (README's "Pan direction / inter-head geometry" section). Which channel
# carries bearing is mode-dependent (Pan in table/hung, Tilt in venue -- see
# MOUNT_PROFILES), so this checks whichever one actually matters for the
# active mode, not always PAN_INVERT. Per-head ELEVATION invert (the other
# channel) is legitimate and expected to vary per head -- see aim_calc.py's
# PAN_INVERT comment -- so this deliberately does NOT check that one.
if aim_calc is not None and mode in aim_calc.MOUNT_PROFILES:
    bearing_channel = aim_calc.MOUNT_PROFILES[mode]["bearing_channel"]
    bearing_invert = (aim_calc.PAN_INVERT if bearing_channel == "pan" else aim_calc.TILT_INVERT)[mode]
    uniform = len(set(bearing_invert)) <= 1
    check(f"{bearing_channel}_invert[{mode!r}] uniform across heads (bearing channel)", uniform,
          warn=True, detail="" if uniform else
          f"{bearing_channel}_invert[{mode!r}] = {bearing_invert} is mixed -- Circle will point "
          f"adjacent heads at each other instead of around the room; flip ALL FOUR together, "
          f"never per-head, to fix (per-head is fine for the OTHER channel, which carries "
          f"elevation in this mode)")

# --- 4. fixture def installed and in sync --------------------------------
if not QXF_INSTALLED.exists():
    check("fixture def installed in QLC+ user dir", False,
          f"missing {QXF_INSTALLED} -- copy it there and restart QLC+")
else:
    same = QXF_INSTALLED.read_bytes() == QXF_PROJECT.read_bytes()
    check("installed fixture def matches project copy", same,
          "" if same else "the two .qxf copies differ -- QLC+ will load the stale one; "
          "recopy the project version and restart QLC+")

# --- 5. a backup exists --------------------------------------------------
backups = sorted(BACKUP_DIR.glob("despacio-*.qxw")) if BACKUP_DIR.exists() else []
check("workspace backup exists", bool(backups), warn=True,
      detail="" if backups else "no backups/ yet -- run aim_calc.py once to create the first")

# --- 6. mobile web UI config in sync + dim-park classification complete --
if not WEBUI_GENERATOR.exists():
    check("mobile web UI (webui/gen_webui_config.py)", False, warn=True,
          detail="not present -- skipping (fine if the webui hasn't been added yet)")
else:
    res = subprocess.run([sys.executable, str(WEBUI_GENERATOR), "--check"],
                          capture_output=True, text=True, cwd=str(WEBUI_GENERATOR.parent))
    webui_ok = res.returncode == 0
    check("mobile web UI layout in sync (webui/gen_webui_config.py --check)", webui_ok,
          "" if webui_ok else "see details below")
    if not webui_ok:
        tail = [ln for ln in res.stdout.splitlines() if ln.strip().startswith("-")]
        for ln in (tail or res.stdout.splitlines()[-6:]):
            print("         " + ln)
        if res.stderr.strip():
            print("         " + res.stderr.strip().splitlines()[-1])

# --- 7. fader park positions the show's routines depend on ---------------
# Two faders ship with a saved position that silently decides whether whole
# groups of routines do anything at all. Neither is checkable by the structural
# validator (a legal value is a legal value); both are easy to leave wrong.
import xml.etree.ElementTree as ET

NSW = "{http://www.qlcplus.org/Workspace}"
QXW_PATH = HERE / "despacio.qxw"


def _slider_level(caption):
    vc = ET.parse(QXW_PATH).getroot().find(NSW + "VirtualConsole")
    for sl in vc.iter(NSW + "Slider"):
        if sl.get("Caption") == caption:
            lv = sl.find(NSW + "Level")
            if lv is not None and lv.get("Value") is not None:
                return int(lv.get("Value"))
    return None


# MH Dim: every dimmer routine in the show (Dim Chase, Spotlight, Prowl, Crowd
# Cascade, and the whole Dark Moves frame) works by pushing individual heads to
# 255 on the HTP dimmer channel. HTP takes the HIGHEST value, so a boost is only
# visible if the fader sits BELOW it -- park the fader at 255 and every one of
# those routines is a silent no-op. The workspace shipped at 255 until
# 2026-07-30, which is the one position where none of them read.
mh_dim = _slider_level("MH Dim")
if mh_dim is None:
    check("MH Dim fader found in workspace", False, warn=True,
          detail="couldn't read its saved level")
else:
    DIM_MASK_THRESHOLD = 128
    ok = mh_dim <= DIM_MASK_THRESHOLD
    check(f"MH Dim parked low enough for the HTP dim routines (saved {mh_dim})", ok,
          warn=True,
          detail="" if ok else
          f"at {mh_dim} the boost-to-255 routines (Dim Chase / Spotlight / Prowl / "
          f"Crowd Cascade / Dark Moves) will barely read or not at all -- these "
          f"only work as a boost ABOVE the fader baseline. Park it under "
          f"{DIM_MASK_THRESHOLD}.")

# PT Speed: no scene in the show writes channel 9, so this fader's park value is
# the movement speed for EVERY pose change, all night. Its direction is still on
# the unverified list in the README (the .qxf capability is a placeholder), so
# this is a reminder to sweep it on hardware rather than a pass/fail.
pt_speed = _slider_level("PT Speed")
if pt_speed is not None:
    print(f"  [note] PT Speed fader parked at {pt_speed} -- nothing in the show writes "
          f"channel 9, so this value is the movement speed for every pose change.")
    print(f"         Which end is fast is still UNVERIFIED on this fixture; sweep it "
          f"once on hardware and park it deliberately.")

# --- summary -------------------------------------------------------------
print("\n" + "=" * 60)
if failures:
    print(f"  RESULT: FAIL ({len(failures)} blocking, {len(warnings)} warning)")
    for f in failures:
        print(f"    x {f}")
    if warnings:
        for w in warnings:
            print(f"    ! {w}")
    print("=" * 60)
    raise SystemExit(1)
if warnings:
    print(f"  RESULT: PASS with {len(warnings)} warning(s)")
    for w in warnings:
        print(f"    ! {w}")
else:
    print("  RESULT: PASS -- clear to go live")
print("=" * 60)
