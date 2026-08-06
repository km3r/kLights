"""The despacio/cosmos show engine.

A parametric lighting engine that owns its own DMX frame clock, knows the room
in three dimensions, and is safe by construction. Replaces the QLC+ workspace
that drove the despacio show -- see docs/ and the plan for why.

Module map:
  geometry -- where a head is, where it points, and what DMX makes it point there
"""
