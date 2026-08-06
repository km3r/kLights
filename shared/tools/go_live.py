"""
Put BlenderDMX into the live-receive state so QLC+ → Art-Net drives the fixtures.

Run this ONCE after opening stage.blend (Scripting workspace -> Open -> Run Script).
It is idempotent and safe to re-run.

Why this is needed: after a Blender restart (or crash/reload) the saved scene keeps
the 13 fixtures, but two runtime bits don't always come back wired up:
  1. The Art-Net receiver thread (fills the incoming DMX buffer).
  2. The 24 fps "run_render" timer (pushes that buffer onto the fixtures).
If #2 isn't registered, QLC+ packets arrive but NOTHING moves in the viewport.
This script guarantees both, sets universe 0 to ARTNET, and switches the viewport
to a shading mode where fixture colour/beams are actually visible.

NOTE: it deliberately does NOT toggle Art-Net off→on in one go — rebinding the
socket from a script blocks Blender's main thread and can crash it. If Art-Net is
already enabled it's left alone; if off, it's turned on once (a clean bind).

TROUBLESHOOTING — "status online but nothing updates / buffer empty":
Most likely a stray process is squatting on UDP 6454 so Blender's receiver could
not bind. Check the port owner from PowerShell (the 0.0.0.0 owner MUST be blender;
QLC+ legitimately also binds 6454 on IPv6 ::):
    Get-NetUDPEndpoint -LocalPort 6454 | %{ Get-Process -Id $_.OwningProcess }
If a stray `python ... artnet_listener.py` (or a sender --loop) owns it, kill it:
    Stop-Process -Id <pid> -Force
Then re-run this script. Never leave artnet_listener.py running while going live.
"""

import bpy
import addon_utils

ADDON = "bl_ext.blender_org.open_stage_blender_dmx"


def main():
    # 1) Make sure BlenderDMX is enabled (it can drop off after a crash/restart).
    if not hasattr(bpy.context.scene, "dmx"):
        addon_utils.enable(ADDON, default_set=True, persistent=True)
    dmx = getattr(bpy.context.scene, "dmx", None)
    if dmx is None:
        raise RuntimeError("BlenderDMX failed to enable — enable it in Preferences > Add-ons.")

    # 2) Universe 0 must read from Art-Net (not the internal programmer).
    for u in dmx.universes:
        if int(u.id) == 0 and u.input != "ARTNET":
            u.input = "ARTNET"

    # 3) Register the live-render timer (the piece that actually updates fixtures).
    #    register_render_toggle(True) is a no-op if it's already running.
    dmx.register_render_toggle(True)

    # 4) Turn the Art-Net receiver on — but only if it's currently off, so we never
    #    do a blocking off→on rebind (that crashes Blender from a script).
    if not dmx.artnet_enabled:
        dmx.artnet_enabled = True

    # 5) Put every 3D viewport into a shading mode that shows the lights.
    #    MATERIAL = fixture colours (fast).  RENDERED = colours + beams lighting the
    #    scene (heavier).  Use RENDERED for the full look.
    SHADING = "MATERIAL"   # change to "RENDERED" to see beams
    vp = 0
    for win in bpy.context.window_manager.windows:
        for area in win.screen.areas:
            if area.type == "VIEW_3D":
                for sp in area.spaces:
                    if sp.type == "VIEW_3D":
                        sp.shading.type = SHADING
                        vp += 1

    print("── Cosmos Lights: LIVE ──────────────────────────────")
    print(f" fixtures        : {len(dmx.fixtures)}")
    print(f" art-net enabled : {dmx.artnet_enabled}  (status: {dmx.artnet_status})")
    print(f" universe 0 input: {next((u.input for u in dmx.universes if int(u.id)==0), '?')}")
    print(f" render timer    : render_running={bpy.context.window_manager.dmx.render_running}")
    print(f" viewports set   : {vp} -> {SHADING}")
    print(" Now press Play/Operate in QLC+ and move a fader. The fixtures follow live.")
    print("─────────────────────────────────────────────────────")


if __name__ == "__main__":
    main()
