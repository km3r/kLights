"""
The commands a console can send: each one's name, the access it needs, the
shape of its arguments and of its answer, declared on the method that applies
it.

    @command("master", tier=OPERATE, args={"value": Spec(Number, required=True)})
    def _cmd_master(self, m: dict, now: float) -> None:
        ...

**One declaration per command, beside its handler.** The tier used to live in
a table at the top of server.py, apart from the eighty-odd handlers, and a
command missing from it was `operate` -- so a new command that should have
needed `configure` and was not added to the table ran for anyone with a
phone. Now a command cannot be registered without a tier, and a message
naming anything not registered is refused. Forgetting is a failure to start,
not an open door.

**Arguments are checked before the handler runs**, against the same `Spec`
the config files are checked with, so a missing key, a string where a number
belongs, NaN, true-as-a-number, or a slot that is not a slot is refused in one
place with one kind of message, rather than by whichever of a hundred
hand-written checks the handler happened to have. What is left in a handler is
what only it can judge: whether the look exists, whether the timeline is
driving, whether two values agree with each other.

Unknown keys are allowed, as in a file: `type` and `id` are the wire's, and a
newer console may send a field this engine does not read yet. A null is read as
absent, so a handler that means something by null (`routine: null` clears a
pad's routine) still sees it.

**A command that answers with data declares that too** (`reply`), in the same
`Spec`. The engine checks each answer against it as it goes out: a mismatch is
said in the notices and, under the test suites (`STRICT`), refused, so a
declaration cannot drift from what its handler really sends.

The table is the contract a client is built against -- which commands exist,
what each takes and answers, and who may send it -- and the console's own
copy of it is generated from here (`typescript`, written to
ui/src/commands.generated.ts by engine/tests/dump_designer_fixtures.py).
"""

from __future__ import annotations

import json
import textwrap
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from . import config as configmod

VIEW, OPERATE, CONFIGURE = "view", "operate", "configure"
TIERS = (VIEW, OPERATE, CONFIGURE)

# What every message may carry that belongs to the wire rather than to the
# command: which command it is, and the id its reply is tagged with.
ENVELOPE = ("type", "id")

# Set by the test suites (engine/tests/harness.py): an answer that does not
# match its command's `reply` is refused instead of sent with a notice. A show
# would rather have the answer than the argument about its shape.
STRICT = False


class CommandError(ValueError):
    """A message whose arguments do not fit its command's declaration."""


def _one_line(problems: list[str]) -> str:
    # One line each: these are read in a reply on a phone, not in a terminal,
    # so a file validator's indented `fix:` line becomes a dash.
    return "; ".join(p.replace("\n      fix: ", " -- ") for p in problems)


@dataclass(frozen=True)
class Command:
    name: str
    tier: str
    args: Mapping[str, configmod.Spec]
    method: str
    summary: str
    # The data its reply carries; empty for a command that answers with none.
    reply: Mapping[str, configmod.Spec] = field(default_factory=lambda: MappingProxyType({}))

    def check(self, message: Mapping[str, Any]) -> None:
        """Refuse a message that does not fit, naming every problem."""
        problems = configmod.problems(dict(message), dict(self.args))
        if problems:
            raise CommandError(f"{self.name}: " + _one_line(problems))

    def reply_problems(self, data: Any) -> Optional[str]:
        """What is wrong with an answer to this command, or None."""
        if not self.reply:
            return (None if data is None
                    else f"{self.name} declares no reply data, and answered with some")
        if not isinstance(data, dict):
            return f"{self.name} answered with {type(data).__name__}, not its reply"
        problems = configmod.problems(data, dict(self.reply))
        return f"{self.name}'s reply: " + _one_line(problems) if problems else None


def command(name: str, *, tier: str,
            args: Optional[Mapping[str, configmod.Spec]] = None,
            reply: Optional[Mapping[str, configmod.Spec]] = None
            ) -> Callable[[Callable], Callable]:
    """Declare the method below as the handler for `name`."""
    if tier not in TIERS:
        raise TypeError(f"command {name!r}: tier must be one of {TIERS}, "
                        f"got {tier!r}")
    for key in args or {}:
        if key in ENVELOPE:
            raise TypeError(f"command {name!r}: {key!r} is the wire's, not an "
                            f"argument")

    def mark(fn: Callable) -> Callable:
        doc = (fn.__doc__ or "").strip()
        fn.__command__ = Command(
            name=name, tier=tier, args=MappingProxyType(dict(args or {})),
            method=fn.__name__, summary=doc.split("\n\n", 1)[0].replace("\n", " "),
            reply=MappingProxyType(dict(reply or {})))
        return fn
    return mark


def collect(cls: type) -> Mapping[str, Command]:
    """Every command declared on `cls` and its bases, by name. A name declared
    twice is a mistake that would otherwise route to whichever came last."""
    table: dict[str, Command] = {}
    for klass in reversed(cls.__mro__):
        for value in vars(klass).values():
            cmd = getattr(value, "__command__", None)
            if not isinstance(cmd, Command):
                continue
            other = table.get(cmd.name)
            if other is not None and other.method != cmd.method:
                raise TypeError(f"command {cmd.name!r} is declared by both "
                                f"{other.method} and {cmd.method}")
            table[cmd.name] = cmd
    return MappingProxyType(table)


@dataclass(frozen=True)
class Call:
    """The command being applied now: who sent it and how to answer them.

    Set for the length of one `apply` and cleared after, so a handler that
    answers later (from the worker) or needs to know its sender asks for it
    explicitly -- and work done outside any command finds no caller, rather
    than the last command's."""
    command: Command
    client: Any
    reply_id: Any


# -- the console's copy ---------------------------------------------------------

def _ts(spec: configmod.Spec, indent: str) -> str:
    """A Spec as a TypeScript type."""
    if spec.choices is not None:
        return " | ".join(json.dumps(c) for c in spec.choices)
    types = spec.types if isinstance(spec.types, tuple) else (spec.types,)
    out: list[str] = []
    for t in types:
        if t is object:
            return "unknown"
        if t is bool:
            name = "boolean"
        elif t in (int, float):
            name = "number"
        elif t is str:
            name = "string"
        elif t is type(None):
            name = "null"
        elif t is list:
            inner = _ts(spec.each, indent) if spec.each is not None else "unknown"
            name = f"({inner})[]" if " | " in inner else f"{inner}[]"
        elif t is dict:
            name = _ts_object(spec.of, indent) if spec.of else "object"
        else:
            name = "unknown"
        if name not in out:
            out.append(name)
    return " | ".join(out)


def _comment(text: str, indent: str) -> list[str]:
    text = " ".join(text.split())
    if not text:
        return []
    width = max(40, 78 - len(indent))
    lines = textwrap.wrap(text.replace("*/", "*∕"), width - 4)
    if len(lines) == 1:
        return [f"{indent}/** {lines[0]} */"]
    return ([f"{indent}/** {lines[0]}"] + [f"{indent} *  {ln}" for ln in lines[1:]]
            + [f"{indent} */"])


def _ts_object(fields: Mapping[str, configmod.Spec], indent: str) -> str:
    """Fields as a TypeScript object type. An optional field may be null: the
    engine reads a null as absent."""
    if not fields:
        return "{}"
    inner = indent + "  "
    lines = ["{"]
    for key, spec in fields.items():
        lines += _comment(spec.fix, inner)
        kind = _ts(spec, inner)
        if spec.required:
            lines.append(f"{inner}{key}: {kind};")
        else:
            lines.append(f"{inner}{key}?: {kind} | null;")
    lines.append(f"{indent}}}")
    return "\n".join(lines)


def typescript(table: Mapping[str, Command]) -> str:
    """The command table as TypeScript: every command's arguments, its reply
    and its tier. What ui/src/commands.generated.ts is."""
    names = sorted(table)
    out = ['''/**
 * The engine's commands: what each takes, what each answers with, and who may
 * send it.
 *
 * GENERATED from the `@command` declarations in engine/controller/ (see
 * engine/commands.py) by engine/tests/dump_designer_fixtures.py. Do not edit:
 * change the declaration and run that script. `test_api` fails while this
 * file is out of date.
 */

export type Tier = "view" | "operate" | "configure";

/** Each command's arguments, by name. An optional argument may also be null,
 *  which the engine reads as absent. */
export interface CommandArgs {''']
    for name in names:
        cmd = table[name]
        out += _comment(cmd.summary, "  ")
        out.append(f"  {name}: {_ts_object(cmd.args, '  ')};")
    out.append("}")
    out.append("")
    out.append("/** What a command sent with `request` answers with, for the commands "
               "that\n *  answer with data. Any other answers with none. */")
    out.append("export interface CommandReplies {")
    for name in names:
        if table[name].reply:
            out.append(f"  {name}: {_ts_object(table[name].reply, '  ')};")
    out.append("}")
    out.append('''
export type CommandName = keyof CommandArgs;

/** One command, as sent: its name as `type`, and its arguments. */
export type Command = { [K in CommandName]: { type: K } & CommandArgs[K] }[CommandName];

/** What command `K` answers with. */
export type ReplyData<K extends CommandName> =
  K extends keyof CommandReplies ? CommandReplies[K] : undefined;

/** Who may send each command. */
export const COMMAND_TIERS: Readonly<Record<CommandName, Tier>> = {''')
    for name in names:
        out.append(f'  {name}: "{table[name].tier}",')
    out.append("};")
    out.append('''
const RANK: Readonly<Record<Tier, number>> = { view: 0, operate: 1, configure: 2 };

/** Whether a console at `tier` may send `command` -- the engine's own rule,
 *  so a control can say so before it is pressed. */
export function mayRun(tier: Tier, command: CommandName): boolean {
  return RANK[tier] >= RANK[COMMAND_TIERS[command]];
}''')
    return "\n".join(out) + "\n"
