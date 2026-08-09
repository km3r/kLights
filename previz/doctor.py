"""Check everything the previz needs, and say which part is missing.

    python previz/doctor.py

Written because the failure modes all look identical from the outside: the
editor sits there, the rig does not move, and nothing says whether the engine
is missing, the editor is not listening, the event is wrong, or something else
already owns the Art-Net port. Each of those has a different fix and none of
them is discoverable by staring at a viewport.

Exits non-zero if anything that would stop a previz working is wrong. Things
that merely might matter are reported and do not fail the run -- a doctor that
cries wolf is a doctor nobody runs.
"""

from __future__ import annotations

import argparse
import socket
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from previz import config as previz_config      # noqa: E402
from previz import ue_remote                    # noqa: E402

ARTNET_PORT = 6454


class Report:
    def __init__(self) -> None:
        self.failed = False

    def ok(self, label: str, detail: str = "") -> None:
        print(f"  OK    {label}" + (f"  -- {detail}" if detail else ""))

    def warn(self, label: str, detail: str = "") -> None:
        print(f"  note  {label}" + (f"  -- {detail}" if detail else ""))

    def bad(self, label: str, detail: str = "") -> None:
        self.failed = True
        print(f"  FAIL  {label}" + (f"  -- {detail}" if detail else ""))


def check_event(r: Report) -> None:
    print("\nevent")
    try:
        info = previz_config.describe()
    except ValueError as exc:
        r.bad("previz.json", str(exc))
        return
    if not info["exists"]:
        r.bad(f"event {info['event']!r} (from {info['source']})",
              f"no such directory: {info['event_dir']}")
        return
    r.ok(f"event {info['event']!r}", f"from {info['source']}")

    # Building the scene is the real test -- it is what the editor does, and it
    # is where a missing venue or an unloadable rig actually surfaces.
    try:
        from previz import scene as previz_scene
        spec = previz_scene.build_scene(Path(info["event_dir"]))
    except Exception as exc:                     # noqa: BLE001
        r.bad("the scene builds", f"{type(exc).__name__}: {exc}")
        return
    placed = sum(1 for f in spec.fixtures if f.location)
    r.ok("the scene builds",
         f"{placed}/{len(spec.fixtures)} fixtures placed in {spec.venue}")
    for name in spec.unplaced:
        r.warn(f"{name} has no position", "it will not appear in the previz")
    for warning in spec.warnings:
        r.warn("rig", warning)

    try:
        previz_config.optics(spec.venue)
    except ValueError as exc:
        r.bad("optics.json", str(exc))
        return
    if previz_config.has_profile(spec.venue):
        r.ok("optics", f"{spec.venue} has its own profile")
    else:
        # Not a failure: inherited optics render, they just render like
        # somewhere else. Worth saying once, because the alternative is
        # wondering for an hour why the fog looks wrong.
        r.warn("optics", f"{spec.venue} has no profile, so it inherits numbers "
                         f"eyeballed in a different room. previz/optics.json "
                         f"has the re-sweep procedure")


def check_engine(r: Report) -> None:
    print("\nunreal")
    roots = ue_remote._engine_roots()
    usable = [x for x in roots
              if (x / ue_remote.REMOTE_EXEC_RELPATH).exists()]
    if not usable:
        r.bad("an Unreal install with remote_execution.py",
              "looked in: " + ", ".join(str(x) for x in roots) or "nowhere")
        print("        set UE_ENGINE_ROOT to the engine folder if it is "
              "somewhere else")
    else:
        r.ok("unreal engine", str(usable[0]))
        if len(usable) > 1:
            r.warn(f"{len(usable)} engines found",
                   "using the first; UE_ENGINE_ROOT overrides")

    if ue_remote.PROJECT.is_file():
        r.ok("previz project", str(ue_remote.PROJECT.relative_to(REPO)))
    else:
        r.bad("previz project", f"missing: {ue_remote.PROJECT}")


def check_editor(r: Report, timeout: float) -> None:
    print("\neditor")
    try:
        with ue_remote.Editor(timeout=timeout):
            r.ok("an editor answered", "remote execution is live")
    except ue_remote.NoEditorError as exc:
        # NOT a failure. Running this before opening the editor is a completely
        # reasonable thing to do, and half the point is checking the rest.
        r.warn("no editor answered", str(exc).splitlines()[0])
    except FileNotFoundError:
        pass                                      # already reported above
    except Exception as exc:                      # noqa: BLE001
        r.bad("talking to the editor", f"{type(exc).__name__}: {exc}")


def check_artnet(r: Report) -> None:
    print("\nart-net")
    # Art-Net has no arbitration: two senders on one universe means whichever
    # packet arrived last wins, and the symptom is a previz that stutters
    # between two shows rather than an error anybody sees.
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.bind(("0.0.0.0", ARTNET_PORT))
        r.ok(f"udp {ARTNET_PORT} is free", "nothing else is receiving Art-Net")
    except OSError as exc:
        # The previz itself binds this while it runs, so this is expected and
        # fine if the editor is already up -- which is why it is a note.
        r.warn(f"udp {ARTNET_PORT} is in use",
               f"{exc.strerror or exc}. Fine if the previz is already "
               f"running; a second receiver would fight it")
    finally:
        sock.close()


def main(argv: list | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--event", help="check this event instead of the "
                                        "configured one")
    parser.add_argument("--timeout", type=float, default=3.0,
                        help="how long to wait for an editor (default 3s)")
    args = parser.parse_args(argv)

    if args.event:
        import os
        os.environ["COSMOS_EVENT"] = args.event

    r = Report()
    check_event(r)
    check_engine(r)
    check_editor(r, args.timeout)
    check_artnet(r)

    print()
    if r.failed:
        print("previz doctor: something needs fixing (above).")
        return 1
    print("previz doctor: ready. Open the editor, then\n"
          "  python previz/ue_remote.py previz/unreal/Content/Python/go.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
