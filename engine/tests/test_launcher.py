"""The launcher's core: what its buttons do, done headless against a real engine.

The window (launcher/gui.py) is a thin layer over launcher/core.py, so this
drives core the way the buttons do: start an engine on a COPY of the sample
event, wait for it to answer, find it again the way a reopened launcher would,
stop it through its stop file, and check it tidied up after itself -- lock and
stop file gone, exit code 0. A launcher that can only stop an engine by killing
it leaves a lock behind that every editing tool then refuses to write past.

Run: python engine/tests/test_launcher.py
"""

import http.server
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.request
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from launcher import core

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label)


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


SAMPLE = REPO / "engine" / "tests" / "data" / "events" / "sample"
tmp = Path(tempfile.mkdtemp(prefix="klights-launcher-"))
try:
    print("\n1. settings")
    state = tmp / "state"
    check("no file: defaults", core.load_settings(state) == core.Settings())
    s = core.Settings(event=str(SAMPLE), port=9123, artnet="10.0.0.5,127.0.0.1", use_token=False)
    s.engine = core.EngineRecord(pid=1, port=9123, event=str(SAMPLE), token="t", stop_file="x", log="y")
    core.save_settings(s, state)
    check("round trip, engine record included", core.load_settings(state) == s)
    (state / "settings.json").write_text("{not json", encoding="utf-8")
    check("a corrupt file falls back to defaults", core.load_settings(state) == core.Settings())
    (state / "settings.json").write_text(json.dumps({"port": "8000", "use_token": 1, "nope": 3,
                                                     "previz_windowed": False}), encoding="utf-8")
    got = core.load_settings(state)
    check("wrongly typed and unknown keys are ignored, good ones kept",
          got.port == 8765 and got.use_token is True and got.previz_windowed is False)

    print("\n2. events")
    events = tmp / "events"
    shutil.copytree(SAMPLE, events / "sample")
    (events / "not-an-event").mkdir()
    check("find_events lists folders with a rig.json, only",
          [p.name for p in core.find_events(events)] == ["sample"])
    info = core.describe_event(events / "sample")
    check("describe_event loads the rig", not info.error and info.fixtures == 5
          and info.universes == (0, 1), info.summary())
    check("...and there is no lock", info.lock == "")
    bad = core.describe_event(events / "not-an-event")
    check("a folder with no rig.json says so", "no rig.json" in bad.error)

    print("\n3. commands and links")
    s = core.Settings(event=str(events / "sample"), port=9000, artnet="", extra_args="--bpm 128")
    cmd = core.engine_command(s, None, tmp / "stop")
    check("Art-Net off: no --artnet, so the engine's null output", "--artnet" not in cmd)
    check("no token: --no-token", "--no-token" in cmd and "--token" not in cmd)
    check("the stop file is passed", cmd[cmd.index("--stop-file") + 1] == str(tmp / "stop"))
    check("extra flags are appended", cmd[-2:] == ["--bpm", "128"])
    s.artnet = "10.0.0.5,127.0.0.1"
    cmd = core.engine_command(s, "abc", tmp / "stop")
    check("Art-Net and token passed through", cmd[cmd.index("--artnet") + 1] == "10.0.0.5,127.0.0.1"
          and "--token=abc" in cmd)
    # One urlsafe token in 64 starts with "-"; as `--token -x` argparse took it
    # for a flag and the engine refused to start.
    cmd = core.engine_command(s, "-xAmdxlH", tmp / "stop")
    check("a token starting with '-' is passed as --token=...", "--token=-xAmdxlH" in cmd)
    check("console link", core.console_url(8765, "abc") == "http://127.0.0.1:8765/?token=abc")
    check("...to the Setup tab", core.console_url(8765, "abc", "setup").endswith("/?token=abc#setup"))
    check("...without a token", core.console_url(8765, None, host="10.0.0.9") == "http://10.0.0.9:8765/")
    check("check_artnet: off and good are fine", core.check_artnet("") == core.check_artnet("127.0.0.1") == "")
    check("check_artnet: a typo is reported", "host:port" in core.check_artnet("10.0.0.5:abc"))
    pc = core.previz_command(Path("app.exe"), 8765)
    check("previz is pointed at the engine", "-Engine=http://127.0.0.1:8765" in pc and "-windowed" in pc)
    check("...and told a non-default Art-Net port",
          "-ArtNetPort=6455" in core.previz_command(Path("app.exe"), 8765, 6455, windowed=False))

    print("\n4. will the previz on this machine get DMX?")
    for artnet, port, gets in (("127.0.0.1", 6454, True), ("255.255.255.255", 6454, True),
                               ("10.0.0.50", 6454, False), ("", 6454, False),
                               ("10.0.0.50,127.0.0.1:6455", 6455, True),
                               ("10.0.0.50,127.0.0.1:6455", 6454, False)):
        warning = core.previz_feed_warning(artnet, port)
        check(f"--artnet {artnet or 'off'!r}, previz on {port}: {'fed' if gets else 'warned'}",
              (warning == "") == gets, warning)
    check("the warning says what to add",
          "Add 127.0.0.1:6455" in core.previz_feed_warning("10.0.0.50", 6455))

    print("\n5. processes")
    check("this process is alive", core.process_alive(os.getpid()))
    check("pid 0 is not", not core.process_alive(0))
    done = subprocess.Popen([sys.executable, "-c", "pass"])
    done.wait()
    check("an exited process is not", not core.process_alive(done.pid))
    check("this process is a Python", Path(core.process_image(os.getpid())).name.lower().startswith("python"))

    print("\n6. probing a port")
    nothing = free_port()
    check("nothing listening: down", core.probe(nothing, timeout=0.5).state == "down")

    class NotFound(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_error(404)

        def log_message(self, *args):
            pass
    other = http.server.ThreadingHTTPServer(("127.0.0.1", 0), NotFound)
    threading.Thread(target=other.serve_forever, daemon=True).start()
    got = core.probe(other.server_address[1])
    check("something that is not an engine: other", got.state == "other", got.detail)
    other.shutdown()
    other.server_close()

    print("\n7. a real engine, started and stopped the launcher's way")
    port = free_port()
    s = core.Settings(event=str(events / "sample"), port=port, artnet="", bind="127.0.0.1",
                      use_token=True)
    handle = core.start_engine(s, state)
    try:
        got = core.wait_for_engine(port, 30.0, handle)
        up = got.state == "engine" and got.event == "sample"
        check("it answers as an engine, serving the event", up,
              f"{got.state} {got.event!r} {got.detail}" + ("" if up else "\n" + Path(
                  handle.record.log).read_text(encoding="utf-8", errors="replace")[-1500:]))
        if not up:
            raise SystemExit(1)
        check("its token is a fresh one", bool(handle.record.token) and len(handle.record.token) >= 8)
        with urllib.request.urlopen(core.console_url(port, handle.record.token), timeout=5) as resp:
            check("the console link opens the console", resp.status == 200)
        check("it holds the event's lock", core.read_lock(events / "sample") != "")
        again = core.EngineHandle.resume(handle.record)
        check("a reopened launcher finds it from the saved record", again is not None and again.alive())
        check("Start would refuse the port: it is taken", core.probe(port).state == "engine")
        (again or handle).request_stop()
        stopped = handle.wait(15.0)
        check("the stop file stops it", stopped)
        check("...cleanly, exit code 0", handle.exit_code == 0, str(handle.exit_code))
        check("...releasing the event's lock", core.read_lock(events / "sample") == "")
        check("...and removing the stop file", not Path(handle.record.stop_file).exists())
        log = Path(handle.record.log).read_text(encoding="utf-8", errors="replace")
        ok = "access  token" in log and "stop requested" in log
        check("the log has the engine's output", ok, "" if ok else log[-300:])
        check("a stopped engine is not resumed", core.EngineHandle.resume(handle.record) is None)
    finally:
        if handle.alive():
            handle.kill()

    print("\n8. a stale stop file does not stop the next engine")
    core.Path(state / "engine.stop").write_text("stop\n", encoding="utf-8")
    port = free_port()
    s.port = port
    handle = core.start_engine(s, state)
    try:
        got = core.wait_for_engine(port, 30.0, handle)
        check("it starts and stays up", got.state == "engine" and handle.alive())
    finally:
        handle.request_stop()
        if not handle.wait(15.0):
            handle.kill()
        check("...and still stops on request", handle.exit_code == 0, str(handle.exit_code))
finally:
    shutil.rmtree(tmp, ignore_errors=True)

print()
if failures:
    print(f"{len(failures)} FAILURE(S):")
    for f in failures:
        print(f"  {f}")
    sys.exit(1)
print("launcher: all checks pass")
