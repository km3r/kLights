"""
What the controller's parts share: who a client is, how a command's reply is
addressed, and the argument shapes commands declare.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Optional

from .. import commands as commandsmod
from .. import config as configmod
from .. import state as statemod


# What a command handler returns when it will answer later, from the worker
# (`ShowController._deferred`). Never sent anywhere.
DEFERRED = object()

# Three tiers, because "anyone on the venue wifi can do anything" stopped being
# acceptable once the patch became editable from the UI. The threat here is not
# an attacker -- it is the guest who opens the URL you showed someone, starts
# pressing, and re-addresses the rig between sets.
#
#   view       read the state. The default for a client that arrives with no
#              token, so showing someone the console stays a one-tap thing.
#   operate    run the show: looks, color, tempo, master, blackout, panic.
#   configure  change what survives the night, or bypass a guard: jog (which
#              disables the safety taper for that head), writing a calibration,
#              editing the room, editing the patch.
#
# PANIC is deliberately `operate`, not `configure`. It is the one command whose
# cost of being unavailable to the wrong person exceeds the cost of being
# available to them -- everything it does is undone by pressing it again.
#
# Each command states its tier where it is handled (`@command`), and a command
# that does not is not a command. GO is `operate`: driving the night is the
# job, not configuration.
TIERS = commandsmod.TIERS

# The shapes command arguments share. `Spec` is what config files are checked
# with; engine/commands.py says how a command is.
S, N = configmod.Spec, configmod.Number
_NAME = S(str, required=True, non_empty=True)
_LOOK_NAME = S(str, required=True, non_empty=True,
               fix="a look's name, as the picker lists it")
_SLOT = S(str, choices=statemod.SLOTS)
_TARGET = S(str, fix='a group, a fixture name, or "all"')
_TAGS = S(list, each=S(str))
_XYZ = S(dict, of={axis: S(N, required=True) for axis in ("x", "y", "z")},
         fix='{"x", "y", "z"} in millimetres, y up, from the front-left floor')
_DOC = S(dict, required=True, fix="the whole document")
_BASE_REV = S(str, required=True,
              fix='the rev you opened, or "" for a new file -- so a save never '
                  "overwrites a change it has not seen")


@dataclass
class Client:
    """One connected browser. `name` is whatever it called itself."""
    id: str
    name: str = "someone"
    connected_at: float = 0.0
    last_action: str = ""
    last_action_at: float = 0.0
    tier: str = "configure"

    def may(self, need: str) -> bool:
        return TIERS.index(self.tier) >= TIERS.index(need)

    def public(self, now: float) -> dict:
        return {"id": self.id, "name": self.name, "tier": self.tier,
                "connected_for": round(now - self.connected_at, 1),
                "last_action": self.last_action,
                "last_action_ago": (round(now - self.last_action_at, 1)
                                    if self.last_action else None)}


def _finite(value: Any, what: str, minimum: Optional[float] = None) -> float:
    """A number off the socket, refused if it is not a finite one -- or, given
    `minimum`, if it is below it.

    Python's json.loads accepts NaN and Infinity, so a client can send what a
    browser never would -- and clamping does not catch NaN: min(1.0, nan) is
    1.0, which put the master, or the energy that drives auto strobe, at FULL.
    """
    number = float(value)
    if not math.isfinite(number):
        raise ValueError(f"{what} must be a finite number, got {value!r}")
    if minimum is not None and number < minimum:
        raise ValueError(f"{what} must be at least {minimum:g}, got {number:g}")
    return number


def reply_id(message: dict) -> Optional[Any]:
    """The id a command wants its reply tagged with, or None for no reply.

    Only a short string or a plain integer. Anything else is treated as no id at
    all rather than an error: the id is the sender's bookkeeping, and a command
    that did its job should not fail because its label was odd. Bounded, because
    it is echoed back and a client should not be able to make the engine send it
    a megabyte.
    """
    if not isinstance(message, dict):
        return None
    rid = message.get("id")
    if isinstance(rid, bool):
        return None
    if isinstance(rid, int) and abs(rid) < 2 ** 53:
        return rid
    if isinstance(rid, str) and 0 < len(rid) <= 64:
        return rid
    return None


def describe(message: dict) -> str:
    kind = message.get("type", "?")
    if kind == "select_look":
        return f"selected {message.get('name')!r}"
    if kind == "color":
        return f"colored {message.get('target', 'all')}"
    if kind == "level":
        target = message.get("target", "all")
        return (f"cleared the level on {target}" if message.get("clear")
                else f"dimmed {target} to {round(float(message.get('value', 1)) * 100)}%")
    if kind == "auto":
        return f"turned {message.get('axis')} {'on' if message.get('on') else 'off'}"
    if kind in ("tap", "downbeat", "bpm", "speed"):
        return f"set the tempo ({kind})"
    if kind == "jog":
        return f"jogged {message.get('fixture')}"
    if kind == "capture":
        return f"captured {message.get('fixture')}"
    return kind
