"""
Build the standalone previz app: one command, every step, checked.

    python previz/build.py              # everything: editor, assets, test, package
    python previz/build.py editor       # compile the C++ module for the editor
    python previz/build.py assets       # materials + the empty map (build_assets.py)
    python previz/build.py test         # the KLights.* automation tests, headless
    python previz/build.py package      # cook and stage previz/dist/Windows/KLightsPreviz.exe
    python previz/build.py assets --force   # rebuild the assets from nothing

The editor is needed to BUILD the app, never to run it. Unreal is found at
UE_ENGINE_ROOT, else the standard 5.8 install. Each step writes its full log to
previz/unreal/Saved/Logs/build_<step>.log and prints only the end of it when it
fails -- these logs run to tens of thousands of lines.

Stdlib only, like everything else here.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PROJECT = REPO / "previz" / "unreal" / "KLightsPreviz.uproject"
LOGS = REPO / "previz" / "unreal" / "Saved" / "Logs"
DIST = REPO / "previz" / "dist"
EXE = DIST / "Windows" / "KLightsPreviz.exe"
STEPS = ("editor", "assets", "test", "package")


def engine_root() -> Path:
    candidates = [os.environ.get("UE_ENGINE_ROOT"), r"C:\Program Files\Epic Games\UE_5.8"]
    for candidate in filter(None, candidates):
        root = Path(candidate)
        if root.name != "Engine":
            root = root / "Engine"
        if (root / "Build" / "BatchFiles" / "Build.bat").is_file():
            return root
    raise SystemExit("no Unreal 5.8 found -- set UE_ENGINE_ROOT to its folder")


def run(step: str, command: list[str]) -> tuple[int, Path]:
    """Run `command`, logging everything to build_<step>.log."""
    LOGS.mkdir(parents=True, exist_ok=True)
    log = LOGS / f"build_{step}.log"
    started = time.monotonic()
    print(f"[{step}] ...", flush=True)
    with log.open("w", encoding="utf-8", errors="replace") as out:
        code = subprocess.call(command, stdout=out, stderr=subprocess.STDOUT)
    print(f"[{step}] exit {code} in {time.monotonic() - started:.0f}s -- {log.relative_to(REPO)}")
    return code, log


def tail(log: Path, pattern: str = r"error|Error|FAILED|Fatal", lines: int = 25) -> None:
    text = log.read_text(encoding="utf-8", errors="replace").splitlines()
    hits = [line for line in text if re.search(pattern, line)]
    for line in (hits or text)[-lines:]:
        print("   ", line)


def editor(root: Path, args) -> bool:
    code, log = run("editor", [str(root / "Build" / "BatchFiles" / "Build.bat"), "KLightsPrevizEditor",
                               "Win64", "Development", f"-Project={PROJECT}", "-WaitMutex"])
    if code != 0:
        tail(log)
    return code == 0


def assets(root: Path, args) -> bool:
    script = PROJECT.parent / "Content" / "Python" / "build_assets.py"
    log = LOGS / "build_assets_editor.log"
    target = f"{script} --force" if args.force else str(script)
    code, out = run("assets", [str(root / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"), str(PROJECT),
                               f"-ExecutePythonScript={target}", "-unattended", "-nosplash", "-nopause",
                               f"-abslog={log}"])
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    built = [line.split("[kLights] ", 1)[1] for line in text.splitlines() if "[kLights] " in line]
    for line in built:
        print("    " + line)
    ok = "build_assets done" in text and "Traceback" not in text
    if not ok:
        tail(log, r"Traceback|Error|error")
    return ok


def test(root: Path, args) -> bool:
    log = LOGS / "build_test_editor.log"
    run("test", [str(root / "Binaries" / "Win64" / "UnrealEditor-Cmd.exe"), str(PROJECT),
                 "-ExecCmds=Automation RunTests KLights; Quit", "-unattended", "-nullrhi", "-nosplash",
                 "-nopause", f"-abslog={log}"])
    text = log.read_text(encoding="utf-8", errors="replace") if log.is_file() else ""
    results = re.findall(r"Test Completed\. Result=\{(\w+)\} Name=\{\w+\} Path=\{([\w.]+)\}", text)
    for result, name in results:
        print(f"    {result:<8} {name}")
    for line in re.findall(r"LogAutomationController: (?:Error: )?(.*worst disagreement.*)", text):
        print(f"             {line}")
    if not results:
        print("    no KLights tests ran -- did the editor module build?")
        tail(log)
        return False
    return all(result == "Success" for result, _ in results)


def package(root: Path, args) -> bool:
    code, log = run("package", [str(root / "Build" / "BatchFiles" / "RunUAT.bat"), "BuildCookRun",
                                f"-project={PROJECT}", "-noP4", "-platform=Win64", "-clientconfig=Development",
                                "-build", "-cook", "-stage", "-pak", "-iostore", "-archive",
                                f"-archivedirectory={DIST}", "-unattended", "-utf8output", "-nocompileeditor"])
    if code != 0 or not EXE.is_file():
        tail(log)
        return False
    # The whole point of the C++: no Python in the app. PythonScriptPlugin is
    # Editor-only in the .uproject; this is the proof it stayed that way.
    staged = (DIST / "Windows" / "Manifest_NonUFSFiles_Win64.txt")
    if staged.is_file() and "PythonScriptPlugin/Binaries" in staged.read_text(errors="replace").replace("\\", "/"):
        print("    the packaged app contains Python binaries -- check the .uproject's TargetAllowList")
        return False
    size = sum(f.stat().st_size for f in (DIST / "Windows").rglob("*") if f.is_file())
    print(f"    {EXE.relative_to(REPO)}  ({size / 1e6:.0f} MB staged)")
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Build the standalone previz app.")
    parser.add_argument("steps", nargs="*", metavar="step",
                        help=f"which of {', '.join(STEPS)}, in order (default: all)")
    parser.add_argument("--force", action="store_true", help="assets: rebuild them from nothing")
    args = parser.parse_args(argv)
    unknown = [s for s in args.steps if s not in STEPS]
    if unknown:
        parser.error(f"no step {', '.join(unknown)} (choose from {', '.join(STEPS)})")
    root = engine_root()
    for step in args.steps or STEPS:
        if not globals()[step](root, args):
            print(f"[{step}] FAILED")
            return 1
    print("done.  Run it:  python -m engine.server --event events/<name> --artnet 127.0.0.1")
    print(f"       then:   {EXE.relative_to(REPO)}  [-Engine=http://<host>:8765]")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
