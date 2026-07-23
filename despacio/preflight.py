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

Exit code 0 = PASS (safe), non-zero = FAIL (do not go live until fixed).
Pure stdlib; safe to run any time -- it never writes despacio.qxw.
"""
import json
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
CONFIG_PATH = HERE / "despacio_config.json"
VALIDATOR = HERE / "validate_despacio.py"
QXF_PROJECT = HERE / "fixtures" / "MingJie-MJ-OS-018-60W-Beam.qxf"
QXF_INSTALLED = Path(r"C:\Users\maxti\QLC+\Fixtures\MingJie-MJ-OS-018-60W-Beam.qxf")
BACKUP_DIR = HERE / "backups"

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

if mode == "venue":
    check("venue mode: all 4 heads calibrated", not uncal,
          "" if not uncal else f"heads {uncal} still null in calibrated_ball_dmx['venue']")
elif mode == "table":
    check("mount_mode is 'table' (BENCH mode)", False, warn=True,
          detail="switch to 'venue' in despacio_config.json once mounted on site")
else:
    check("mount_mode recognized", False, f"unexpected mount_mode {mode!r}")

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
