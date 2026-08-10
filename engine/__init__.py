"""The kLights show engine.

A parametric lighting engine that owns its own DMX frame clock, knows the room
in three dimensions, and is safe by construction. Replaces the QLC+ workspace
that drove the despacio show -- see docs/ and the plan for why.

Standard library only, deliberately: no pip install at load-in, no virtualenv
that rotted since the last show. See docs/SAFETY.md before pointing it at a rig.

Module map:
  geometry  -- where a head is, where it points, and what DMX makes it point there
  venue     -- the room: walls, crowd zone, canopy, truss, the mirror ball
  rig       -- what is plugged in: .qxf profiles, patch, channel roles, calibration
  safety    -- the beam-aware intensity taper, evaluated per frame from the aim
  state     -- the parameter store, the layer stack, and the DMX render
  clock     -- musical time: beats, bars, phrases, tap tempo, external sync
  motion    -- movement as a function of musical phase
  library   -- looks.json as runnable layer stacks; the three-slot model
  auto      -- self-running axes: timing, look changes, palette, energy
  calibrate -- multi-point re-aim solver, drift detection, snapshots
  runner    -- the frame clock and the output loop
  servo     -- yoke slew model: what the fixture does with the number you sent it
  server    -- the show controller, plus HTTP and WebSocket for the UI
  websocket -- a hand-rolled RFC 6455, so the server needs nothing installed
  output/   -- the driver interface and the Art-Net sender
  demo      -- an end-to-end smoke run with no UI
"""

__version__ = "0.1.0"
