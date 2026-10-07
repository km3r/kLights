"""
Everything the launcher does, without the window: settings, events, starting and
stopping the engine, probing it, and running the previz app.

Kept apart from the Tk code so it can be tested headless -- the window is a thin
layer of buttons over these functions, and engine/tests/test_launcher.py drives
them against a real engine the way the buttons do.

The engine always runs as its OWN process, never inside the launcher's. The F2
timing spike found that Python holds a 40 fps DMX clock comfortably but that
in-process GIL contention is what breaks it, and a GUI toolkit's event loop is
exactly that. A separate process also means a crashed or closed launcher cannot
stop a show.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shlex
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path
from typing import Optional

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from engine.output.artnet import ARTNET_PORT, parse_targets   # noqa: E402

EVENTS = REPO / "events"
# Per machine and per run: what this launcher last chose, the log of the engine
# it started, and that engine's stop file. Gitignored.
STATE_DIR = REPO / ".launcher"
PREVIZ_EXE = REPO / "previz" / "dist" / "Windows" / "KLightsPreviz.exe"
LOCK_NAME = ".engine.lock"          # engine/patch.py's, read here without importing it

NT = os.name == "nt"
# Every console program started from a windowless launcher (pythonw) would
# otherwise flash a console window -- including the engine itself, and tasklist
# once a second.
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


# ------------------------------------------------------------------ settings --

@dataclass
class EngineRecord:
    """An engine this launcher started, remembered so a reopened launcher can
    find it again and still stop it."""
    pid: int
    port: int
    event: str
    token: Optional[str]
    stop_file: str
    log: str
    started: float = 0.0
    bind: str = "0.0.0.0"


@dataclass
class Settings:
    event: str = str(EVENTS / "despacio")
    port: int = 8765
    # "" means no Art-Net at all: the engine's null output.
    artnet: str = "127.0.0.1"
    bind: str = "0.0.0.0"
    use_token: bool = True
    # The show folder: prepped tracks, timelines, routines, template sets. ""
    # leaves it to the engine: $KLIGHTS_SHOW_DIR, then klights.local.json.
    show_dir: str = ""
    extra_args: str = ""
    previz_port: int = ARTNET_PORT
    previz_windowed: bool = True
    engine: Optional[EngineRecord] = None


def load_settings(state_dir: Path = STATE_DIR) -> Settings:
    """The last session's choices. Anything unreadable falls back to defaults:
    a launcher that will not open because of its own preferences file is worse
    than one that forgot them."""
    try:
        data = json.loads((state_dir / "settings.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return Settings()
    if not isinstance(data, dict):
        return Settings()
    known = {f.name: f for f in fields(Settings)}
    values = {}
    for key, value in data.items():
        if key not in known or key == "engine":
            continue
        default = getattr(Settings(), key)
        if isinstance(default, bool) != isinstance(value, bool) or not isinstance(value, type(default)):
            continue
        values[key] = value
    settings = Settings(**values)
    adopt_show_dir(settings)
    record = data.get("engine")
    if isinstance(record, dict):
        try:
            settings.engine = EngineRecord(**record)
        except TypeError:
            pass
    return settings


# `--show-dir X`, `--show-dir=X`, or either with X quoted.
_SHOW_DIR_FLAG = re.compile(r"""(?:^|\s)--show-dir(?:=|\s+)("[^"]*"|'[^']*'|\S+)""")


def adopt_show_dir(settings: Settings) -> bool:
    """Move a `--show-dir` out of Extra flags into the show folder field.

    Before the field existed, Extra flags was the only place for it. Left
    there it would also win over the field, since extra flags come last on
    the command line, so the field would say one folder and the engine load
    another. True if anything moved.
    """
    if settings.show_dir:
        return False
    match = _SHOW_DIR_FLAG.search(settings.extra_args)
    if match is None:
        return False
    settings.show_dir = match.group(1).strip("\"'")
    rest = settings.extra_args[:match.start()] + settings.extra_args[match.end():]
    settings.extra_args = " ".join(rest.split())
    return True


def save_settings(settings: Settings, state_dir: Path = STATE_DIR) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    path = state_dir / "settings.json"
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(asdict(settings), indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)


# -------------------------------------------------------------------- events --

def find_events(root: Path = EVENTS) -> list[Path]:
    """Every folder under `root` that is an event: it has a rig.json."""
    if not root.is_dir():
        return []
    return sorted((d for d in root.iterdir() if (d / "rig.json").is_file()),
                  key=lambda d: d.name.lower())


@dataclass
class EventInfo:
    path: Path
    name: str = ""
    venue: str = ""
    fixtures: int = 0
    universes: tuple = ()
    error: str = ""
    lock: str = ""      # what the lock file says, if one is there

    def summary(self) -> str:
        if self.error:
            return self.error
        unis = ", ".join(str(u) for u in self.universes) or "none"
        return (f"{self.name} in {self.venue or 'no venue'} -- {self.fixtures} fixtures, "
                f"universe{'s' if len(self.universes) != 1 else ''} {unis}")


def read_lock(event: Path) -> str:
    """What the event's lock file says, or '' if there is none."""
    try:
        return (Path(event) / LOCK_NAME).read_text(encoding="utf-8").strip() or "an engine"
    except OSError:
        return ""


def describe_event(path: Path) -> EventInfo:
    """Load the event the way the engine will, so a broken rig is reported here
    rather than as an engine that starts and immediately exits."""
    info = EventInfo(Path(path), lock=read_lock(path))
    if not (Path(path) / "rig.json").is_file():
        info.error = f"no rig.json in {path}"
        return info
    try:
        from engine import rig as rigmod
        rig = rigmod.load_rig(Path(path))
    except Exception as exc:                    # noqa: BLE001
        info.error = f"the rig does not load: {type(exc).__name__}: {exc}"
        return info
    info.name = rig.name
    info.venue = rig.venue.name if rig.venue is not None else ""
    info.fixtures = len(rig.fixtures)
    info.universes = tuple(rig.universes)
    return info


# --------------------------------------------------------------- show folder --

@dataclass
class ShowInfo:
    """Where the engine will find its show folder, and what is in it."""
    path: Optional[Path] = None
    # "" when the field names it; else "env" or "local": the engine's own
    # default, from $KLIGHTS_SHOW_DIR or klights.local.json.
    source: str = ""
    tracks: int = 0
    timelines: int = 0
    routines: int = 0
    template_sets: int = 0
    problems: int = 0       # files that do not load, which the engine leaves out
    error: str = ""

    def summary(self) -> str:
        if self.path is None:
            return ("None. The console runs without one; Studio and timecoded shows "
                    "need one. Try shared/show-example.")
        where = {"env": f"From $KLIGHTS_SHOW_DIR: {self.path}. ",
                 "local": f"From klights.local.json: {self.path}. "}.get(self.source, "")
        if self.error:
            return where + self.error

        def n(count: int, what: str) -> str:
            return f"{count} {what}{'' if count == 1 else 's'}"
        text = where + ", ".join((n(self.tracks, "track"), n(self.timelines, "timeline"),
                                  n(self.routines, "routine"),
                                  n(self.template_sets, "template set")))
        if self.problems:
            text += (f". {n(self.problems, 'file')} will not load: "
                     f"python -m engine.showfiles check says why.")
        return text


def describe_show_dir(text: str, env: Optional[dict] = None,
                      local: Optional[Path] = None) -> ShowInfo:
    """Resolve the show folder the way the engine will, and count what is in it.

    Reads every file in the folder, so it runs off the Tk thread: a show folder
    on a NAS can take a while. A relative path is taken from the repo, which is
    where the engine is started.
    """
    from engine import showfiles
    text = text.strip()
    path = showfiles.resolve_show_dir(text or None, env=env, local=local)
    if path is None:
        return ShowInfo()
    if text:
        source = ""
    elif (os.environ if env is None else env).get(showfiles.ENV_VAR):
        source = "env"
    else:
        source = "local"
    if not path.is_absolute():
        path = REPO / path
    info = ShowInfo(path=path, source=source)
    if not path.is_dir():
        info.error = (f"{path} is not a folder. Create one with "
                      f"python -m engine.showfiles init, or pick another.")
        return info
    try:
        folder = showfiles.load_folder(path)
    except Exception as exc:                    # noqa: BLE001
        info.error = f"the folder does not load: {type(exc).__name__}: {exc}"
        return info
    info.tracks, info.timelines = len(folder.tracks), len(folder.timelines)
    info.routines, info.template_sets = len(folder.routines), len(folder.templates)
    info.problems = len(folder.errors)
    return info


# -------------------------------------------------------------------- engine --

def python_exe() -> str:
    """python.exe even when the launcher runs under pythonw.exe: the engine's
    output has to go somewhere, and pythonw has nowhere for it to go."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").is_file():
        return str(exe.with_name("python.exe"))
    return str(exe)


def check_artnet(artnet: str) -> str:
    """'' if `artnet` is usable as --artnet (or off), else what is wrong."""
    if not artnet.strip():
        return ""
    try:
        parse_targets(artnet)
    except ValueError as exc:
        return str(exc)
    return ""


def engine_command(settings: Settings, token: Optional[str], stop_file: Path) -> list[str]:
    cmd = [python_exe(), "-u", "-m", "engine.server",
           "--event", str(settings.event),
           "--port", str(settings.port),
           "--bind", settings.bind,
           "--stop-file", str(stop_file)]
    if settings.artnet.strip():
        cmd += ["--artnet", settings.artnet.strip()]
    # `--token=` and never `--token X`: a urlsafe token starts with "-" one time
    # in 64, and argparse then reads it as a flag and refuses to start.
    cmd.append(f"--token={token}" if token else "--no-token")
    if settings.show_dir.strip():
        # `=` for the same reason as the token: a folder may start with "-".
        cmd.append(f"--show-dir={settings.show_dir.strip()}")
    if settings.extra_args.strip():
        cmd += shlex.split(settings.extra_args, posix=not NT)
    return cmd


def process_alive(pid: int) -> bool:
    """Whether `pid` is a running process.

    Not os.kill(pid, 0): on Windows that does not probe, it TERMINATES the
    process with exit code 0.
    """
    if pid <= 0:
        return False
    if NT:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(0x1000, False, pid)   # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = wintypes.DWORD()
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return bool(ok) and code.value == 259            # STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def process_image(pid: int) -> str:
    """The executable `pid` is running, or '' if that cannot be found out."""
    if NT:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        handle = kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return ""
        try:
            size = wintypes.DWORD(1024)
            buf = ctypes.create_unicode_buffer(size.value)
            if not kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
                return ""
            return buf.value
        finally:
            kernel32.CloseHandle(handle)
    try:
        return os.readlink(f"/proc/{pid}/exe")
    except OSError:
        return ""


class EngineHandle:
    """An engine this launcher started -- in this session, or a previous one.

    Stopped by creating its stop file (engine.server --stop-file), which works
    the same whether or not the launcher that started it is still the one
    asking. Never by killing, unless the stop is refused and someone says so:
    a killed engine skips its own tidy-up, starting with its lock file.
    """

    def __init__(self, record: EngineRecord, proc: Optional[subprocess.Popen] = None):
        self.record = record
        self.proc = proc
        self.exit_code: Optional[int] = None

    @property
    def pid(self) -> int:
        return self.record.pid

    def alive(self) -> bool:
        if self.proc is not None:
            code = self.proc.poll()
            if code is None:
                return True
            self.exit_code = code
            return False
        return process_alive(self.record.pid)

    def request_stop(self) -> None:
        path = Path(self.record.stop_file)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("stop\n", encoding="utf-8")

    def wait(self, timeout: float) -> bool:
        """True once it has exited, False if still running after `timeout`."""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self.alive():
                return True
            time.sleep(0.1)
        return not self.alive()

    def kill(self) -> None:
        """The last resort, for an engine that ignored its stop file. Removes
        the lock the engine would have, since it no longer can."""
        if self.proc is not None:
            self.proc.kill()
        elif NT:
            subprocess.run(["taskkill", "/F", "/PID", str(self.pid)], capture_output=True,
                           creationflags=NO_WINDOW)
        else:
            import signal
            os.kill(self.pid, signal.SIGKILL)
        self.wait(5.0)
        for path in (Path(self.record.event) / LOCK_NAME, Path(self.record.stop_file)):
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass

    @classmethod
    def resume(cls, record: Optional[EngineRecord]) -> Optional["EngineHandle"]:
        """The engine a previous launcher session left running, if it still is.

        Its PID must still be a Python. Windows reuses PIDs, and a handle on
        whatever now has the number would offer to force-kill it.
        """
        if record is None or not process_alive(record.pid):
            return None
        image = Path(process_image(record.pid)).name.lower()
        if image and not image.startswith("python"):
            return None
        return cls(record)


def start_engine(settings: Settings, state_dir: Path = STATE_DIR) -> EngineHandle:
    """Start engine.server as a detached process logging to state_dir/engine.log.

    Detached so that it outlives the launcher: closing a window is not a
    reason for a show to stop. A fresh token per start, like the engine's own
    default -- a link shared last week stops working.
    """
    state_dir.mkdir(parents=True, exist_ok=True)
    log = state_dir / "engine.log"
    if log.exists():
        try:
            os.replace(log, state_dir / "engine.previous.log")
        except OSError:
            pass
    stop_file = state_dir / "engine.stop"
    stop_file.unlink(missing_ok=True)
    token = secrets.token_urlsafe(6) if settings.use_token else None
    cmd = engine_command(settings, token, stop_file)
    flags = (NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if NT else 0
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"}
    with log.open("wb") as out:
        out.write(("$ " + subprocess.list2cmdline(cmd) + "\n").encode("utf-8"))
        out.flush()
        proc = subprocess.Popen(cmd, cwd=REPO, stdin=subprocess.DEVNULL, stdout=out,
                                stderr=subprocess.STDOUT, env=env, creationflags=flags,
                                start_new_session=not NT)
    record = EngineRecord(pid=proc.pid, port=settings.port, event=str(settings.event),
                          token=token, stop_file=str(stop_file), log=str(log),
                          started=time.time(), bind=settings.bind)
    return EngineHandle(record, proc)


# --------------------------------------------------------------------- probe --

@dataclass
class Probe:
    """What is answering on a port.

    "engine": a kLights engine serving a previz scene. "other": something speaks
    HTTP there but has no scene -- an engine older than /api/previz, or not an
    engine at all. "down": nothing.
    """
    state: str
    event: str = ""
    venue: str = ""
    rev: str = ""
    fixtures: int = 0
    warnings: list = field(default_factory=list)
    detail: str = ""


# Loopback never goes through a proxy. urllib would otherwise read the Windows
# system proxy from the registry and send 127.0.0.1 to it.
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def probe(port: int, host: str = "127.0.0.1", timeout: float = 0.6) -> Probe:
    try:
        with _OPENER.open(f"http://{host}:{port}/api/previz/scene", timeout=timeout) as resp:
            scene = json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        if exc.code == 503:
            # An engine, whose scene does not build. It is running a show;
            # the previz is what is broken.
            try:
                detail = json.loads(exc.read()).get("error", "")
            except (ValueError, AttributeError, OSError):
                detail = ""
            return Probe("engine", detail=detail or "its previz scene does not build")
        return Probe("other", detail=f"HTTP {exc.code}")
    except (urllib.error.URLError, OSError, ValueError):
        return Probe("down")
    if not isinstance(scene, dict):
        return Probe("other", detail="not a previz scene")
    return Probe("engine", event=str(scene.get("event", "")), venue=str(scene.get("venue", "")),
                 rev=str(scene.get("rev", ""))[:8], fixtures=len(scene.get("fixtures", [])),
                 warnings=list(scene.get("warnings", [])))


def wait_for_engine(port: int, timeout: float, handle: Optional[EngineHandle] = None) -> Probe:
    """Poll until `port` answers as an engine, `handle` exits, or time runs out."""
    deadline = time.monotonic() + timeout
    result = Probe("down")
    while time.monotonic() < deadline:
        result = probe(port, timeout=0.5)
        if result.state == "engine":
            return result
        if handle is not None and not handle.alive():
            return result
        time.sleep(0.2)
    return result


# ------------------------------------------------------------------- console --

def console_url(port: int, token: Optional[str], tab: Optional[str] = None,
                host: str = "127.0.0.1") -> str:
    """The web console. `tab` is a hash the console restores (e.g. "setup"),
    or "studio" for Studio, where shows are made.

    Setup is a Design-mode tab: a browser left in Perform mode opens on Show
    instead. A desktop browser starts in Design unless someone switched it.
    """
    url = f"http://{host}:{port}/"
    if token:
        url += f"?token={token}"
    if tab:
        url += f"#{tab}"
    return url


def lan_address() -> Optional[str]:
    """This machine's address on the network a phone would join.

    The same trick engine.server prints its URLs with: a UDP connect sets the
    socket's local address without sending anything.
    """
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
    except OSError:
        return None


# -------------------------------------------------------------------- previz --

def previz_command(exe: Path, port: int, artnet_port: int = ARTNET_PORT,
                   windowed: bool = True) -> list[str]:
    cmd = [str(exe), f"-Engine=http://127.0.0.1:{port}"]
    if artnet_port != ARTNET_PORT:
        cmd.append(f"-ArtNetPort={artnet_port}")
    if windowed:
        cmd += ["-windowed", "-ResX=1600", "-ResY=900"]
    return cmd


def this_machine() -> set[str]:
    """Every address that means this computer. Can block on a DNS lookup, so
    the window asks for it from its poller thread, never while drawing."""
    addresses = {"localhost", "0.0.0.0"}
    try:
        addresses.update(socket.gethostbyname_ex(socket.gethostname())[2])
    except OSError:
        pass
    lan = lan_address()
    if lan:
        addresses.add(lan)
    return addresses


def previz_feed_warning(artnet: str, previz_port: int = ARTNET_PORT,
                        local: Optional[set[str]] = None) -> str:
    """Why a previz on THIS machine would get no DMX from these settings, or ''.

    `local` is `this_machine()`, worked out once by the caller; without it this
    looks it up, which may wait on DNS.
    """
    if not artnet.strip():
        return ("Art-Net is off, so the previz gets no DMX: every head stays at "
                "its rest pose.")
    try:
        targets = parse_targets(artnet)
    except ValueError as exc:
        return str(exc)
    if local is None:
        local = this_machine()
    for host, port in targets:
        if port != previz_port:
            continue
        if (host.startswith("127.") or host in local or host.endswith(".255")
                or host == "<broadcast>"):
            return ""
    add = "127.0.0.1" if previz_port == ARTNET_PORT else f"127.0.0.1:{previz_port}"
    return (f"Art-Net goes only to {artnet.strip()}, so nothing reaches port "
            f"{previz_port} on this machine and the previz will not move. "
            f"Add {add}.")


def previz_pids() -> list[int]:
    """Every running KLightsPreviz process: the launcher stub and the game it
    starts both have that name. Windows only; elsewhere, nothing to find."""
    if not NT:
        return []
    try:
        out = subprocess.run(["tasklist", "/FI", "IMAGENAME eq KLightsPreviz.exe", "/FO", "CSV", "/NH"],
                             capture_output=True, text=True, timeout=5,
                             creationflags=NO_WINDOW).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    pids = []
    for line in out.splitlines():
        cells = [c.strip('"') for c in line.split('","')]
        if len(cells) > 1 and cells[1].strip('"').isdigit():
            pids.append(int(cells[1].strip('"')))
    return pids


def start_previz(exe: Path, port: int, artnet_port: int = ARTNET_PORT,
                 windowed: bool = True) -> subprocess.Popen:
    return subprocess.Popen(previz_command(exe, port, artnet_port, windowed), cwd=exe.parent,
                            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, close_fds=True)


def stop_previz(pid: int, timeout: float = 8.0) -> bool:
    """Close the previz: politely, then not.

    The exe the launcher starts is Unreal's stub, which starts the real game
    and waits for it. Closing the TREE politely asks the game's window to close
    and the stub then exits by itself; killing the stub alone would leave the
    game running with nothing tracking it.
    """
    if not NT:
        import signal
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass
    else:
        subprocess.run(["taskkill", "/T", "/PID", str(pid)], capture_output=True,
                       creationflags=NO_WINDOW)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_alive(pid):
            return True
        time.sleep(0.2)
    if NT:
        subprocess.run(["taskkill", "/F", "/T", "/PID", str(pid)], capture_output=True,
                       creationflags=NO_WINDOW)
    return not process_alive(pid)


def build_previz_command(steps: tuple[str, ...] = ()) -> list[str]:
    return [python_exe(), "-u", str(REPO / "previz" / "build.py"), *steps]


def previz_freshness() -> tuple[str, str]:
    from previz import build
    return build.freshness()
