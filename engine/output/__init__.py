"""
DMX output drivers behind one interface.

Kept a small isolated component on purpose. Not because a rewrite is expected --
the F2 timing spike settled that Python holds the clock with roughly 65x margin
-- but because the two settings that make it hold are easy to enforce in one
place and easy to keep enforced, and because the driver is the one part whose
answer could change when real hardware replaces Art-Net over loopback.

**Art-Net first**, USB-DMX as another driver behind the same interface. That one
decision is what makes previz free: Unreal listens on the wire, so it is fully
decoupled from the show path and can never block engine work.
"""

from .base import Output, NullOutput, FanOut
from .artnet import ArtNetOutput

__all__ = ["Output", "NullOutput", "FanOut", "ArtNetOutput"]
