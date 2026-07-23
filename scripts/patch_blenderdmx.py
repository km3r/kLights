"""
Patch the whole Cosmos rig into BlenderDMX from patch_sheet.csv.

Reads patch_sheet.csv (the single source of truth), copies the generated GDTF
profiles into BlenderDMX's profile folder, and adds every fixture with the
correct profile / mode / universe / start address. If the venue scaffold
(scripts/scaffold_venue.py) has been run, each fixture is also moved to its
matching "Fixture Positions" empty (matched by the qlcplus_id custom property).

Run it from Blender:
    Scripting workspace -> Open -> scripts/patch_blenderdmx.py -> Run Script

Prerequisites:
    - BlenderDMX addon installed and enabled
    - gdtf/ contains the .gdtf files (run scripts/build_gdtf.py first)

Re-running is safe: it clears the GDTF cache, removes the fixtures it manages,
and re-patches from scratch so the scene always matches the CSV.
"""

import bpy
import csv
import os
import shutil
import importlib

import mathutils

# ── Config ────────────────────────────────────────────────────────────────────
# Absolute project dir. __file__ is unreliable inside Blender's text editor, so
# this is the dependable anchor — edit it if you move the project.
PROJECT_DIR = r"C:\Users\maxti\Documents\code\cosmos\lights"
CSV_PATH = os.path.join(PROJECT_DIR, "patch_sheet.csv")
GDTF_SRC = os.path.join(PROJECT_DIR, "gdtf")

CLEAR_EXISTING = True   # remove all existing BlenderDMX fixtures before patching
POSITION_FROM_EMPTIES = True  # move fixtures onto scaffold "Fixture Positions" empties
DISPLAY_BEAMS = True
ADD_TARGET = True


# ── Addon plumbing ────────────────────────────────────────────────────────────

def _find_addon_attr(module_suffix, attr):
    """Locate a class/attr inside the BlenderDMX extension package by module suffix."""
    for name, mod in list(importlib.sys.modules.items()):
        if "open_stage_blender_dmx" in name and name.endswith(module_suffix) and mod is not None:
            obj = getattr(mod, attr, None)
            if obj is not None:
                return obj
    return None


def ensure_logger():
    """BlenderDMX's logger is lazily set up; addFixture crashes without it."""
    import logging
    dmx_log = _find_addon_attr("logging_setup", "DMX_Log")
    if dmx_log is not None and getattr(dmx_log, "log", None) is None:
        dmx_log.enable(logging.ERROR)


def ensure_dmx_initialized(dmx):
    """Make sure the master 'DMX' collection exists (the panel's Create step)."""
    if getattr(dmx, "collection", None) is None:
        dmx.new()


def copy_profiles(dmx):
    """Copy generated .gdtf files into BlenderDMX's profile folder."""
    gdtf_file = _find_addon_attr("gdtf_file", "DMX_GDTF_File")
    dst = gdtf_file.get_profiles_path()
    os.makedirs(dst, exist_ok=True)
    copied = []
    for f in os.listdir(GDTF_SRC):
        if f.lower().endswith(".gdtf"):
            shutil.copy2(os.path.join(GDTF_SRC, f), os.path.join(dst, f))
            copied.append(f)
    return copied, gdtf_file


def clear_profile_cache(gdtf_file, rows):
    """Drop cached parses + stale model collections so updated profiles reload."""
    gdtf_class = _find_addon_attr("open_stage_blender_dmx.gdtf", "DMX_GDTF")

    # Remove cached fixture-model collections for every profile/mode we patch.
    seen = set()
    for r in rows:
        key = (r["gdtf_profile"], r["mode"])
        if key in seen:
            continue
        seen.add(key)
        try:
            prof = gdtf_file.load_gdtf_profile(r["gdtf_profile"])
        except Exception:
            continue
        for db in (True, False):
            for at in (True, False):
                name = gdtf_class.getName(prof, r["mode"], db, at, False)
                if name in bpy.data.collections:
                    bpy.data.collections.remove(bpy.data.collections[name])

    # Drop the pygdtf parse cache.
    if getattr(gdtf_file, "instance", None) is not None:
        gdtf_file.gdtf_fixtures.clear()
        gdtf_file.instance = None


# ── Patch ─────────────────────────────────────────────────────────────────────

def read_patch():
    with open(CSV_PATH, newline="", encoding="utf-8") as fh:
        return [r for r in csv.DictReader(fh) if r.get("gdtf_profile", "").strip()]


def empty_positions():
    """Map qlcplus_id -> world location from scaffold 'Fixture Positions' empties."""
    positions = {}
    for obj in bpy.data.objects:
        if obj.type == "EMPTY" and "qlcplus_id" in obj.keys():
            positions[int(obj["qlcplus_id"])] = obj.matrix_world.translation.copy()
    return positions


class _Break:
    """Minimal stand-in for a dmx_break (build() reads .universe and .address)."""
    def __init__(self, universe, address):
        self.dmx_break = 1
        self.universe = universe
        self.address = address


def patch():
    dmx = getattr(bpy.context.scene, "dmx", None)
    if dmx is None:
        raise RuntimeError("BlenderDMX addon is not enabled — enable it in Preferences > Add-ons.")

    ensure_logger()
    ensure_dmx_initialized(dmx)

    rows = read_patch()
    copied, gdtf_file = copy_profiles(dmx)
    print(f"Copied {len(copied)} GDTF profiles into BlenderDMX.")
    clear_profile_cache(gdtf_file, rows)

    if CLEAR_EXISTING:
        # bpy collection items are index-bound proxies — iterating a snapshot
        # while removing skips every other one. Always remove the current first.
        guard = 0
        while len(dmx.fixtures) and guard < 1000:
            dmx.removeFixture(dmx.fixtures[0])
            guard += 1
        print("Removed existing fixtures.")

    positions = empty_positions() if POSITION_FROM_EMPTIES else {}
    universes_used = set()
    ok, failed = 0, []

    for r in rows:
        qid = int(r["qlcplus_id"])
        universe = int(r["artnet_universe"])
        address = int(r["dmx_start"])
        universes_used.add(universe)

        pos = None
        if qid in positions:
            pos = mathutils.Matrix.Translation(positions[qid])

        try:
            dmx.addFixture(
                r["gdtf_profile"],          # profile filename
                r["mode"],                  # mode name
                [_Break(universe, address)],  # dmx_breaks
                (1.0, 1.0, 1.0, 1.0),       # gel_color (white)
                DISPLAY_BEAMS,
                ADD_TARGET,
                position=pos,
                fixture_id=str(qid),
                user_fixture_name=r["name"],
                show_error=False,
            )
            ok += 1
            placed = " @empty" if pos is not None else ""
            print(f"  + {r['name']:28s} U{universe} @{address:>3}  {r['mode']}{placed}")
        except Exception as e:
            failed.append((r["name"], str(e)))
            print(f"  ! FAILED {r['name']}: {e}")

    # Make sure each used Art-Net universe exists in the patch panel.
    for u in sorted(universes_used):
        try:
            dmx.ensureUniverseExists(u)
        except Exception:
            pass

    print(f"\nPatched {ok}/{len(rows)} fixtures across universe(s) {sorted(universes_used)}.")
    if failed:
        print("Failures:")
        for name, err in failed:
            print(f"  - {name}: {err}")
    else:
        print("All fixtures patched. Enable DMX > Art-Net (universe 0) to go live.")


if __name__ == "__main__":
    patch()
