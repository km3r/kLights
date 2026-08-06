"""Stdlib static server for the despacio mobile virtual console.

Serves despacio/webui/ (this directory) on the LAN so a phone can load
index.html, which then talks directly to QLC+'s own web API over a
WebSocket (ws://<host>:9999/qlcplusWS) -- this script never touches DMX or
QLC+ itself, it just hosts the static files. QLC+ must already be running
with --web (or -w) for the page to connect.

No dependencies -- stdlib only, matching the rest of this project.

Run:
    python serve.py [port]      # default port 8080
"""
import http.server
import socket
import subprocess
import sys
from pathlib import Path

WEBUI = Path(__file__).parent
QXW = WEBUI.parent / "despacio.qxw"
CONFIG_JS = WEBUI / "ui_config.js"
GENERATOR = WEBUI / "gen_webui_config.py"
DEFAULT_PORT = 8080


def lan_ip():
    """Best-effort outbound LAN IP via a UDP "connect" (no packets actually
    sent -- just asks the OS which local interface would be used)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("8.8.8.8", 80))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def regenerate_if_stale():
    if not QXW.exists():
        print(f"WARNING: {QXW} not found -- serving whatever ui_config.js already exists")
        return
    if CONFIG_JS.exists() and CONFIG_JS.stat().st_mtime >= QXW.stat().st_mtime:
        return
    print("despacio.qxw is newer than ui_config.js -- regenerating...")
    result = subprocess.run([sys.executable, str(GENERATOR)], cwd=str(WEBUI))
    if result.returncode != 0:
        print("WARNING: gen_webui_config.py failed -- serving the last good ui_config.js, if any.")


class NoCacheHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(WEBUI), **kwargs)

    def end_headers(self):
        # A phone browser caching a stale app.js/ui_config.js across a
        # mid-show reload is exactly the failure mode this exists to avoid.
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def log_message(self, fmt, *args):
        sys.stderr.write("%s - %s\n" % (self.address_string(), fmt % args))


def main():
    port = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PORT
    regenerate_if_stale()

    ip = lan_ip()
    print(f"\nServing {WEBUI}")
    print(f"  On this machine:  http://localhost:{port}/")
    print(f"  On your phone:    http://{ip}:{port}/   (same Wi-Fi as this PC)")
    print("\nMake sure QLC+ is running with --web (or -w) and despacio.qxw loaded --")
    print("this only hosts the page; the phone talks to QLC+ directly on port 9999.\n")

    server = http.server.ThreadingHTTPServer(("0.0.0.0", port), NoCacheHandler)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping.")
        server.shutdown()


if __name__ == "__main__":
    main()
