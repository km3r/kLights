"""The output interface every driver implements."""

from __future__ import annotations

from typing import Iterable, Protocol


class Output(Protocol):
    """Somewhere DMX frames go.

    One method, deliberately. A driver that needs setup does it in its
    constructor and a driver that needs teardown does it in `close()`; anything
    richer than that ends up with the show path depending on driver-specific
    lifecycle, which is exactly what makes a 2am hardware swap fragile.
    """

    def send(self, universe: int, frame: bytes) -> None: ...

    def close(self) -> None: ...


class NullOutput:
    """Counts frames and keeps the last one. The default when no hardware is
    configured, so the engine always has somewhere to send and never has to
    branch on whether output exists."""

    def __init__(self) -> None:
        self.frames = 0
        self.last: dict[int, bytes] = {}

    def send(self, universe: int, frame: bytes) -> None:
        self.frames += 1
        self.last[universe] = bytes(frame)

    def close(self) -> None:
        pass


class FanOut:
    """Send every frame to several outputs.

    The normal running configuration: the rig and the previz get identical
    frames from the same evaluation, so what is on screen is what is on the
    wire rather than a re-simulation that can disagree.

    A failing output is dropped rather than allowed to take the show down --
    losing previz mid-set must not stop the lights. What it did is recorded in
    `failures` for the UI to surface.
    """

    def __init__(self, outputs: Iterable[Output]) -> None:
        self.outputs = list(outputs)
        self.failures: dict[int, str] = {}

    def send(self, universe: int, frame: bytes) -> None:
        for i, out in enumerate(self.outputs):
            if i in self.failures:
                continue
            try:
                out.send(universe, frame)
            except OSError as exc:
                self.failures[i] = f"{type(exc).__name__}: {exc}"

    def close(self) -> None:
        for out in self.outputs:
            try:
                out.close()
            except OSError:
                pass
