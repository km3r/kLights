"""
The command registry: every command a console can send is declared with its
tier and its arguments on the handler that applies it (engine/commands.py).

What this holds in place:

  * nothing reaches the show without a declaration -- no handler is left
    undeclared, and a name nobody declared is refused;
  * a declaration says everything its handler reads, so the table is a
    complete contract (the fuzz suite generates its messages from it);
  * which commands are `configure` is written out below, so a change to who
    may do what is a change to this file, made on purpose and reviewed;
  * arguments are refused before the handler runs, with one kind of message.

Run: python engine/tests/test_commands.py
"""

import inspect
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))
from engine.tests.harness import check, finish, isolated_event  # noqa: E402

from engine import commands as commandsmod  # noqa: E402
from engine import config as configmod  # noqa: E402
from engine import server as servermod  # noqa: E402

COMMANDS = servermod.ShowController.COMMANDS
Ctl = servermod.ShowController

print("\n1. every handler is declared, and only declared commands are dispatched")
handlers = {name[len("_cmd_"):] for name in dir(Ctl) if name.startswith("_cmd_")}
check("every _cmd_ method is a declared command", handlers == set(COMMANDS),
      f"undeclared {sorted(handlers - set(COMMANDS))}, "
      f"declared without a handler {sorted(set(COMMANDS) - handlers)}")
check("each is declared under its own method's name",
      all(cmd.method == f"_cmd_{name}" for name, cmd in COMMANDS.items()),
      [n for n, c in COMMANDS.items() if c.method != f"_cmd_{n}"])
check("every declaration states one of the three tiers",
      all(cmd.tier in commandsmod.TIERS for cmd in COMMANDS.values()))

try:
    commandsmod.command("x", tier="admin")
    refused = False
except TypeError:
    refused = True
check("a declaration with no real tier does not register", refused)
try:
    commandsmod.command("x", tier=commandsmod.OPERATE, args={"id": configmod.Spec(str)})
    refused = False
except TypeError as exc:
    refused = "wire" in str(exc)
check("nor one that takes the reply id as an argument", refused)


class Twice:
    @commandsmod.command("same", tier=commandsmod.OPERATE)
    def _cmd_a(self, m, now):
        pass

    @commandsmod.command("same", tier=commandsmod.OPERATE)
    def _cmd_b(self, m, now):
        pass


try:
    commandsmod.collect(Twice)
    refused = False
except TypeError:
    refused = True
check("one name declared by two handlers is refused, not routed to the last", refused)


print("\n2. a declaration names everything its handler reads")
# What a handler reads from its message, directly or through a helper it
# hands the message to. The helpers are named here so a new one is noticed:
# a handler passing `m` to anything else fails the check below until it is.
KEY_RE = re.compile(r'\bm\["(\w+)"\]|\bm\.get\("(\w+)"|"(\w+)" in m\b')
HELPERS = {"_cell_request": servermod._cell_request, "_target": servermod._target,
           "self._save": Ctl._save, "self._typed_description": Ctl._typed_description}
# Validated by their own module, which whitelists what it reads: the
# declaration lists the same fields (sync) or the module's arguments (modulate).
OWN_VALIDATOR = {"syncmod.clean", "modmod.build"}


def keys_read(fn) -> set[str]:
    src = inspect.getsource(fn)
    return {next(g for g in hit if g) for hit in KEY_RE.findall(src)}


undeclared, unknown_helpers = {}, {}
for name, cmd in COMMANDS.items():
    fn = getattr(Ctl, cmd.method)
    src = inspect.getsource(fn)
    read = keys_read(fn)
    for callee in re.findall(r"([\w.]+)\((?:[^()]*, )?m\b[,)]", src):
        if callee in HELPERS:
            read |= keys_read(HELPERS[callee])
        elif callee not in OWN_VALIDATOR:
            unknown_helpers.setdefault(name, []).append(callee)
    missing = read - set(cmd.args) - set(commandsmod.ENVELOPE)
    if missing:
        undeclared[name] = sorted(missing)
check("no handler reads an argument its declaration leaves out", not undeclared,
      f"{undeclared}")
check("and none hands its message to a helper this check cannot see into",
      not unknown_helpers, f"{unknown_helpers}")


print("\n3. who may do what")
# Everything that persists past tonight or steps around a guard. Written out
# rather than derived: moving a command between tiers should be a decision
# someone makes in a review, not a side effect of editing a decorator.
CONFIGURE = {
    "jog", "jog_clear", "capture", "capture_clear", "solve", "drift",
    "venue", "venue_save", "taper",
    "patch_add", "patch_remove", "patch_address", "patch_tags", "patch_position",
    "patch_autopatch", "patch_apply",
    "track_link", "show_reload", "show_latency", "rekordbox_prep",
    "timeline_draft", "timeline_save", "timeline_delete", "track_delete",
    "routine_draft", "routine_save", "routine_rename", "routine_delete",
    "template_draft", "template_save", "template_rename", "template_delete",
    "show_save", "palette_save", "palette_delete", "palette_sync",
    "look_save", "look_delete", "look_hide", "looks_reload",
    "preview_arm", "preview_transport", "preview_release",
}
VIEW = {"hello"}
by_tier = {t: {n for n, c in COMMANDS.items() if c.tier == t} for t in commandsmod.TIERS}
check("configure is exactly the commands listed here", by_tier["configure"] == CONFIGURE,
      f"extra {sorted(by_tier['configure'] - CONFIGURE)}, "
      f"missing {sorted(CONFIGURE - by_tier['configure'])}")
check("view is only hello", by_tier["view"] == VIEW, sorted(by_tier["view"]))
check("panic, GO and Follow are operate -- on purpose",
      {"panic", "go", "follow", "template_set"} <= by_tier["operate"])


print("\n4. arguments are refused before the handler runs")
controller = servermod.ShowController(isolated_event("despacio"))
viewer = servermod.Client(id="v", tier="view")
operator = servermod.Client(id="o", tier="operate")


def refusal(message, client=None) -> str:
    try:
        controller.apply(message, client)
    except ValueError as exc:
        return str(exc)
    return ""


said = refusal({"type": "no_such_thing"})
check("an undeclared command is refused by name", "unknown command" in said, said)
said = refusal({"type": ["go"]})
check("so is a type that is not a name", "unknown command" in said, said)
said = refusal({"type": "patch_apply"}, operator)
check("a configure command from an operator is refused, saying what it needs",
      "needs configure" in said, said)
said = refusal({"type": "master", "value": 0.5}, viewer)
check("anything from a viewer is refused", "needs operate" in said, said)

master = controller.master
said = refusal({"type": "master"})
check("a missing argument is named", said.startswith("master: value is required"), said)
said = refusal({"type": "master", "value": "0.2"})
check("a number sent as text is refused, not read", "must be a number" in said, said)
said = refusal({"type": "master", "value": float("nan")})
check("NaN is refused", "finite" in said, said)
said = refusal({"type": "master", "value": True})
check("true is not a number", "true/false" in said, said)
check("and none of those touched the master", controller.master == master)

said = refusal({"type": "clear_slot", "slot": "base"})
check("a choice outside the declared ones is refused with the choices",
      "'movement', 'color', 'level'" in said, said)
said = refusal({"type": "timeline_save", "doc": {}})
check("a fix travels with the refusal, on one line",
      "base_rev is required" in said and " -- the rev you opened" in said
      and "\n" not in said, said)
said = refusal({"type": "palette_sync", "palette": "p", "files": ["a", 3]})
check("each element of a list is checked", "files[1] must be text" in said, said)

said = refusal({"type": "master", "value": 0.25, "id": "r1", "junk": [1]})
check("the wire's keys and unknown extras are allowed", said == "", said)
check("and the command ran", controller.master == 0.25)
said = refusal({"type": "preset_save", "name": "x", "routine": None, "bank": None,
                "cell": None})
check("null reads as absent, so a handler that means something by it still runs",
      said == "", said)


print("\n5. who is answering")
try:
    controller._deferred()
    raised = False
except RuntimeError:
    raised = True
check("a deferred answer outside a command is refused, not sent to the last sender",
      raised)
seen = []
Ctl._cmd_test_sender = lambda self, m, now: seen.append(self._sender())
controller.COMMANDS = {**COMMANDS, "test_sender": commandsmod.Command(
    "test_sender", commandsmod.OPERATE, {}, "_cmd_test_sender", "")}
controller.apply({"type": "test_sender"}, operator)
check("a handler can ask who sent it", seen == [operator], seen)
check("and nobody, once it has returned", controller._call is None)
del controller.COMMANDS
del Ctl._cmd_test_sender

finish("commands")
