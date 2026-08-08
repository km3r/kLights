"""
Push Python into a running Unreal editor from this repo.

Epic's `remote_execution.py` ships inside the engine install and is the
first-party way to drive an editor from outside it: the editor broadcasts a
discovery ping on UDP multicast 239.0.0.1:6766, a client answers, and commands
then run over a TCP connection. This module is a thin, opinionated wrapper --
it finds the engine, handles discovery and teardown, and turns a failure into a
non-zero exit code instead of a silently-dropped command.

Why this and not MCP: the F10 plan expected UE 5.8 to ship a first-party MCP
*server* at 127.0.0.1:8000/mcp. What 5.8.1 actually ships is `MCPClientToolset`,
an adapter that lets the editor's own assistant connect OUT to MCP servers.
There is nothing to attach to. Remote execution does the same job, is stable,
and has been in the engine for years.

    python previz/ue_remote.py --ping
    python previz/ue_remote.py previz/unreal/Content/Python/build_level.py
    python previz/ue_remote.py -c "import unreal; print(unreal.SystemLibrary.get_engine_version())"

The editor must be running with Python remote execution enabled --
`previz/unreal/Config/DefaultEngine.ini` does that for the previz project.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import sys
import time
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
PROJECT = REPO / "previz" / "unreal" / "CosmosPrevis.uproject"

# Where the engine keeps the client library. Newest first, so a machine with
# several engines installed side by side uses the one the project targets.
ENGINE_ROOTS = [
    Path(r"C:\Program Files\Epic Games\UE_5.8"),
    Path(r"C:\Program Files\Epic Games\UE_5.7"),
]
REMOTE_EXEC_RELPATH = Path(
    "Engine/Plugins/Experimental/PythonScriptPlugin/Content/Python/remote_execution.py")

DISCOVERY_TIMEOUT = 8.0


class NoEditorError(RuntimeError):
    """No editor answered the discovery ping."""


def _engine_roots() -> list[Path]:
    """Engine installs to search, honouring UE_ENGINE_ROOT if it is set."""
    override = os.environ.get("UE_ENGINE_ROOT")
    roots = [Path(override)] if override else []
    return roots + [r for r in ENGINE_ROOTS if r not in roots]


def load_remote_execution():
    """Import Epic's `remote_execution` module straight out of the engine.

    Loaded by path rather than copied into this repo on purpose: it is a client
    for a protocol the *engine* defines, so a vendored copy would be a second
    implementation free to drift out of step with the editor it talks to.
    """
    for root in _engine_roots():
        path = root / REMOTE_EXEC_RELPATH
        if path.exists():
            spec = importlib.util.spec_from_file_location("remote_execution", path)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
    raise FileNotFoundError(
        "no Unreal install with remote_execution.py found. Looked in:\n  "
        + "\n  ".join(str(r / REMOTE_EXEC_RELPATH) for r in _engine_roots())
        + "\nSet UE_ENGINE_ROOT to the engine folder if it lives elsewhere.")


class Editor:
    """A connected editor. Use as a context manager.

    Discovery is a multicast ping, so an editor that is running but has remote
    execution switched off is indistinguishable from one that is not running at
    all -- hence the specific error message rather than a bare timeout.
    """

    def __init__(self, timeout: float = DISCOVERY_TIMEOUT, verbose: bool = False):
        self._re = load_remote_execution()
        self._timeout = timeout
        self._verbose = verbose
        self._conn = None
        self.node_id: Optional[str] = None

    def __enter__(self) -> "Editor":
        config = self._re.RemoteExecutionConfig()
        # Must match DefaultEngine.ini exactly. On a multi-NIC Windows box the
        # 0.0.0.0 default binds an arbitrary adapter and the ping never reaches
        # the editor, which presents as "no editor found" with both sides up.
        config.multicast_bind_address = "127.0.0.1"
        config.multicast_group_endpoint = ("239.0.0.1", 6766)

        self._conn = self._re.RemoteExecution(config)
        self._conn.start()

        deadline = time.monotonic() + self._timeout
        while time.monotonic() < deadline:
            nodes = self._conn.remote_nodes
            if nodes:
                node = nodes[0]
                self.node_id = node["node_id"]
                if self._verbose:
                    print(f"editor: {node.get('project_name', '?')} "
                          f"({node.get('engine_version', '?')})", file=sys.stderr)
                self._conn.open_command_connection(self.node_id)
                return self
            time.sleep(0.1)

        self._conn.stop()
        raise NoEditorError(
            f"no Unreal editor answered on 239.0.0.1:6766 within {self._timeout:.0f}s.\n"
            f"  * Is the editor open on {PROJECT}?\n"
            f"  * Is Python remote execution enabled? "
            f"(Project Settings -> Plugins -> Python -> Enable Remote Execution)\n"
            f"  * Does its Multicast Bind Address read 127.0.0.1?")

    def __exit__(self, *exc) -> None:
        if self._conn is not None:
            try:
                if self._conn.has_command_connection():
                    self._conn.close_command_connection()
            finally:
                self._conn.stop()

    def run(self, command: str, exec_file: bool = True) -> dict:
        """Run Python in the editor and return its result dict.

        Never raises on a *Python* failure -- the traceback comes back in the
        result and is far more useful printed than re-raised here, since it
        happened in another process.
        """
        mode = (self._re.MODE_EXEC_FILE if exec_file
                else self._re.MODE_EVAL_STATEMENT)
        return self._conn.run_command(command, unattended=True, exec_mode=mode,
                                      raise_on_failure=False)


def _print_result(result: dict) -> int:
    """Print an editor result the way a shell wants it, and return an exit code."""
    for entry in result.get("output") or []:
        stream = sys.stderr if entry.get("type") in ("Error", "Warning") else sys.stdout
        print(entry.get("output", "").rstrip("\n"), file=stream)
    if not result.get("success", False):
        print(f"[unreal] command failed: {result.get('result', '')}", file=sys.stderr)
        return 1
    value = result.get("result")
    if value not in (None, "", "None"):
        print(value)
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run Python inside a running Unreal editor.")
    parser.add_argument("script", nargs="?",
                        help="path to a .py file to execute in the editor")
    parser.add_argument("-c", "--command",
                        help="a Python statement to execute instead of a file")
    parser.add_argument("--ping", action="store_true",
                        help="just report which editors are listening")
    parser.add_argument("--timeout", type=float, default=DISCOVERY_TIMEOUT)
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()

    if not any((args.script, args.command, args.ping)):
        parser.error("give a script path, -c COMMAND, or --ping")

    try:
        with Editor(timeout=args.timeout, verbose=args.verbose) as editor:
            if args.ping:
                result = editor.run(
                    "import unreal; print(unreal.Paths.get_project_file_path())")
                return _print_result(result)

            if args.command:
                return _print_result(editor.run(args.command, exec_file=True))

            path = Path(args.script)
            if not path.exists():
                print(f"no such script: {path}", file=sys.stderr)
                return 2
            # Send the file's *contents*, not its path. The editor's working
            # directory is the engine binaries folder, so a relative path would
            # resolve somewhere surprising -- and sending text means an unsaved
            # edit can never run stale.
            source = path.read_text(encoding="utf-8")
            preamble = (
                f"__file__ = {str(path.resolve())!r}\n"
                f"import sys\n"
                f"sys.path.insert(0, {str(REPO)!r}) "
                f"if {str(REPO)!r} not in sys.path else None\n")
            return _print_result(editor.run(preamble + source))
    except (NoEditorError, FileNotFoundError) as exc:
        print(str(exc), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
