"""A single slot holding the running previz driver, immune to module reloads.

Small and separate for one reason. `cosmos_live` is reloaded every time it is
restarted so that edits take effect, and a reload rebinds that module's globals
-- which would drop the reference to the live instance's UDP socket and tick
callback without closing either. The socket stays bound to 6454 with
SO_REUSEADDR set, Windows keeps delivering to it rather than to the new one, and
the previz goes silent while looking perfectly healthy. That is a genuinely
hard bug to see, so the state lives here where reload cannot reach it.

Nothing in this module may import `cosmos_live`, or the cycle defeats the point.
"""

current = None
