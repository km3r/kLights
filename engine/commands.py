"""
The commands a console can send: each one's name, the access it needs and the
shape of its arguments, declared on the method that applies it.

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

The table is also the contract a client is built against: which commands
exist, what each takes, and who may send it.
"""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping, Optional

from . import config as configmod

VIEW, OPERATE, CONFIGURE = "view", "operate", "configure"
TIERS = (VIEW, OPERATE, CONFIGURE)

# What every message may carry that belongs to the wire rather than to the
# command: which command it is, and the id its reply is tagged with.
ENVELOPE = ("type", "id")


class CommandError(ValueError):
    """A message whose arguments do not fit its command's declaration."""


@dataclass(frozen=True)
class Command:
    name: str
    tier: str
    args: Mapping[str, configmod.Spec]
    method: str
    summary: str

    def check(self, message: Mapping[str, Any]) -> None:
        """Refuse a message that does not fit, naming every problem."""
        problems = configmod.problems(dict(message), dict(self.args))
        if problems:
            # One line each: these are read in a reply on a phone, not in a
            # terminal, so a file validator's indented `fix:` line becomes a
            # dash.
            raise CommandError(f"{self.name}: " + "; ".join(
                p.replace("\n      fix: ", " -- ") for p in problems))


def command(name: str, *, tier: str,
            args: Optional[Mapping[str, configmod.Spec]] = None
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
            method=fn.__name__, summary=doc.split("\n\n", 1)[0].replace("\n", " "))
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
