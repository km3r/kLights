"""
The show server: one HTTP + WebSocket endpoint that the phone, the tablet and
the laptop all talk to.

One responsive surface, not a phone app and a desktop app -- that was the
explicit requirement, and it is also the only way the six tabs stay in sync,
since there is exactly one implementation of each.

Two structural decisions shape everything here.

**Commands go through a queue drained at frame boundaries.** Nothing on a
server thread ever mutates the running show directly. A phone tapping a look
while the output thread is halfway through evaluating one would otherwise give a
frame built from two different shows, and the resulting one-frame flicker is
invisible in testing and infuriating in a room. Queueing costs at most one
frame -- 25 ms -- and removes the whole class.

**JSON is built on the broadcast thread, never the output thread.** The output
thread publishes its raw state by assigning one reference, which is atomic under
the GIL; the broadcast thread serialises at 10 Hz. The F2 spike was explicit
that in-process CPU-bound work is what breaks the DMX clock, and serialising a
few kilobytes 40 times a second to feed a display that cannot show 40 Hz would
be exactly that mistake.

**Presence is awareness, not ownership.** Everyone controls everything; the UI
shows who is connected and who last touched what. No locking, no claiming. A
lighting desk with two people on it works because they can see each other, not
because the desk arbitrates.

This module is the transport: sockets, HTTP, the broadcast thread. What a
command does, and what the snapshot says, is the controller's
(`engine/controller/`).
"""

from __future__ import annotations

import json
import math
import secrets
import socket
import sys
import threading
import time
import traceback
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

from . import api as apimod
from . import outputs as outputsmod
from . import scene as scenemod
from . import showfiles
from .controller import Client, ShowController
from .output.artnet import parse_targets as parse_artnet_targets
from .websocket import WebSocket, WebSocketClosed, WebSocketError


REPO = Path(__file__).resolve().parent.parent
UI_DIST = REPO / "ui" / "dist"

BROADCAST_HZ = 10.0

# How long one message may sit unfinished in a client's socket before the client
# is dropped as not reading. A client that is only behind is never dropped: it
# is sent the newest snapshot when it next takes one (see `Connection`).
#
# This was a count, three snapshots, a third of a second. That was sized for a
# 30 kB snapshot. With a show folder loaded a snapshot is about 59 kB, and a
# browser stops reading its socket whenever its main thread is busy, so a page
# that spent a second and a half loading Studio's editor was dropped while it
# was alive and about to read -- and a page driving the rig lost it. Five
# seconds is longer than a working page stalls, and short enough that a phone
# which has really gone leaves the presence list, and lets go of a rig it was
# driving, while someone is still looking at it.
#
# The snapshot is about 7 kB now: the look list, 50 kB of the 59, left it for a
# frame of its own sent when the library changes (`looks_frame`).
SEND_DEADLINE_S = 5.0

# Replies one client may have waiting. Only a client that sends commands and
# does not read the answers can reach it, and it is dropped when it does: the
# cap is what keeps that client from growing the engine's memory until
# SEND_DEADLINE_S catches up with it.
REPLY_BACKLOG = 256

MIME = {".html": "text/html; charset=utf-8", ".js": "text/javascript",
        ".css": "text/css", ".json": "application/json", ".svg": "image/svg+xml",
        ".png": "image/png", ".ico": "image/x-icon", ".webmanifest": "application/manifest+json"}


class Connection:
    """One socket, with the thread that owns writing to it.

    The write side is an outbox and a thread rather than a direct call, so the
    broadcast loop can hand off a payload without ever waiting on a client --
    see `ShowServer.send_all` for why that matters.

    The outbox holds two kinds of message and treats them differently, because
    they are different. A snapshot is the whole state, so the next one makes it
    worthless: one is kept, the newest, and a client that fell behind catches up
    in a single frame instead of replaying everything it missed. A reply answers
    one command and nothing repeats it: every reply is kept, in the order they
    were made -- and so is a look-library frame, which is sent only when the
    library changes and is not in any snapshot. What goes out is what a plain
    queue would have sent with the superseded snapshots left out, so a reply
    still arrives before the first snapshot made after it.

    Being behind is therefore not a reason to drop anyone. Not reading is: see
    `given_up`.
    """

    def __init__(self, ws: WebSocket):
        self.ws = ws
        self.lock = threading.Lock()
        # Guards the outbox below. Held for a few assignments and never across
        # a send, so taking it cannot wait on the client.
        self._wake = threading.Condition()
        self._replies: "deque[str]" = deque()
        self._snapshot: Optional[str] = None
        # How many of the waiting replies were made before the waiting snapshot
        # and so go out ahead of it.
        self._ahead = 0
        self._overflowed = False
        # When the pump took the message it is sending now; None between sends.
        self._sending_since: Optional[float] = None
        self._closed = False
        self.thread = threading.Thread(target=self._pump, daemon=True)
        self.thread.start()

    def offer_snapshot(self, payload: str) -> None:
        """The state as of now, replacing one this client has not taken yet."""
        with self._wake:
            self._snapshot = payload
            self._ahead = len(self._replies)
            self._wake.notify()

    def offer_reply(self, payload: str) -> None:
        """An answer to one command, or a look-library frame. Kept until sent,
        in order.

        Past `REPLY_BACKLOG` it is not kept, and the client is dropped on the
        next broadcast: a connection that is about to end loses everything
        still waiting for it anyway, and its browser answers every command it
        was waiting on with "the connection to the engine dropped".
        """
        with self._wake:
            if len(self._replies) >= REPLY_BACKLOG:
                self._overflowed = True
                return
            self._replies.append(payload)
            self._wake.notify()

    def stuck_for(self, now: float) -> float:
        """Seconds the message being sent now has been in the socket; 0.0
        between sends. `now` is `time.monotonic()`."""
        with self._wake:
            since = self._sending_since
        return 0.0 if since is None else max(0.0, now - since)

    def given_up(self, now: float) -> Optional[str]:
        """Why this client should be dropped, in the words of the notice, or
        None while it should not.

        The test is whether the socket takes a message, not how far behind the
        client is. A send returns once the kernel has the bytes, so one that
        has not returned in `SEND_DEADLINE_S` means the buffers between here
        and the client filled and did not empty by one message in that time:
        a locked phone, a laptop out of wifi range, a page that hung. A browser
        whose main thread was busy empties them the moment it is back.

        The clock is per message, and nothing restarts it part-way. So a link
        that cannot carry one message in `SEND_DEADLINE_S` counts as not
        reading, though it is: a snapshot (about 7 kB) at under 1.5 kB/s, and
        the look list (about 50 kB, as a console connects and when the
        library changes) at under 10 kB/s.
        """
        if self.stuck_for(now) >= SEND_DEADLINE_S:
            return f"{SEND_DEADLINE_S:g} seconds behind and not reading"
        if self._overflowed:
            return f"{REPLY_BACKLOG} replies behind and not reading"
        return None

    def _pump(self) -> None:
        while True:
            with self._wake:
                self._sending_since = None
                while not (self._closed or self._replies
                           or self._snapshot is not None):
                    self._wake.wait()
                if self._closed:
                    return
                if self._snapshot is not None and self._ahead == 0:
                    payload, self._snapshot = self._snapshot, None
                else:
                    payload = self._replies.popleft()
                    self._ahead = max(0, self._ahead - 1)
                # Stamped before the lock below as well as the send: the only
                # other holder is the handler writing the welcome, and a client
                # that never reads even that is stuck in the same way.
                self._sending_since = time.monotonic()
            try:
                # Same per-connection lock as before: a broadcast and a command
                # acknowledgement interleaving on one socket would splice two
                # frames together and corrupt both.
                with self.lock:
                    self.ws.send(payload)
            except (WebSocketClosed, WebSocketError, OSError):
                # `_sending_since` is left standing: if the reader has not
                # noticed the socket is dead, the deadline still will.
                return

    def close(self) -> None:
        """Tear the connection down without ever blocking the caller.

        The subtle part: `WebSocket.close` sends a courtesy close frame with a
        blocking `sendall`, and the client most likely to be dropped is the one
        whose buffer is already full -- so the polite goodbye blocks on exactly
        the socket that caused the drop, and `drop()` re-freezes the broadcast
        thread it was called from to prevent that freeze. The queue fixed the
        send path and left this one, which the test then found.

        So the close frame gets a deadline, and failing that the socket is torn
        down underneath it. A browser that misses the frame just sees the TCP
        close and reconnects on the backoff it already implements.
        """
        with self._wake:
            # Ends a pump that is waiting. One stuck in a send does not see
            # this; the shutdown below ends that one.
            self._closed = True
            self._wake.notify()
        try:
            self.ws.sock.settimeout(0.2)
            self.ws.close()
        except (OSError, WebSocketClosed, WebSocketError):
            pass
        # Unconditional, because `ws.close()` may have given up on a half-sent
        # close frame and left the socket open with the pump still parked in it.
        try:
            self.ws.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.ws.sock.close()
        except OSError:
            pass


def _no_constant(name: str) -> float:
    raise ValueError(f"{name} is not a number")


def _finite_literal(text: str) -> float:
    value = float(text)
    if not math.isfinite(value):           # 1e999 parses as infinity
        raise ValueError(f"{text} is not a finite number")
    return value


def strict_json(text: str) -> Any:
    """A message off the socket, as JSON a browser could have sent.

    Python's json.loads also accepts NaN, Infinity and -Infinity, and reads
    1e999 as infinity; JSON.stringify never writes any of them. Refused here,
    once, for every command -- and a command's declared arguments (`@command`)
    refuse a number sent as a string ("nan"), which float() reads all the same,
    and a non-finite one that arrives some other way than off the socket.
    """
    return json.loads(text, parse_constant=_no_constant, parse_float=_finite_literal)


class ExclusiveHTTPServer(ThreadingHTTPServer):
    """A ThreadingHTTPServer that refuses a port something else is listening on.

    http.server turns on SO_REUSEADDR. On POSIX that only lets a restart bind
    past connections still in TIME_WAIT. On Windows it means something else
    entirely: a second socket may bind a port another process is already
    LISTENING on, the bind succeeds silently, and two engines answer one port
    -- which one a phone reaches is luck. So on Windows the flag is off and
    SO_EXCLUSIVEADDRUSE is on, which also stops anything that does set
    SO_REUSEADDR from taking the port out from under a running show. Windows
    rebinds a listener over TIME_WAIT without help, so a quick restart still
    works: checked on Windows 11 with connections the server closed first,
    which is what `stop` does to every phone.

    SO_REUSEPORT is pinned off whatever the base class says: on Linux it is
    the deliberate form of the same thing, two listeners on one port.
    """

    # Only Windows has the option, so its presence is the platform test.
    allow_reuse_address = not hasattr(socket, "SO_EXCLUSIVEADDRUSE")
    allow_reuse_port = False

    def server_bind(self) -> None:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()


class ShowServer:
    """HTTP for the UI bundle, WebSocket for everything live."""

    def __init__(self, controller: ShowController, port: int = 8765,
                 ui_dir: Path = UI_DIST, token: Optional[str] = None,
                 bind: str = "0.0.0.0"):
        self.controller = controller
        self.port = port
        self.ui_dir = Path(ui_dir)
        # None means no access control at all: every client arrives as
        # `configure`, which is what this was before tokens existed and is still
        # the right answer on a laptop with no network.
        self.token = token
        self.bind = bind
        self.sockets: dict[str, Connection] = {}
        self._next_id = 0
        self._id_lock = threading.Lock()
        self._stop = threading.Event()
        self.httpd: Optional[ThreadingHTTPServer] = None
        # The library_rev every console has been sent (`broadcast_loop`). A
        # console that connects is sent the list then, by its own handler.
        self._looks_sent = controller.library_rev
        controller.reply_to = self.send_to
        # Where this machine keeps music, for /api/audio when the track file
        # moved (klights.local.json's audio_roots).
        roots = showfiles.read_local_config().get("audio_roots")
        self.audio_roots: list[str] = ([r for r in roots if isinstance(r, str)]
                                       if isinstance(roots, list) else [])

    def token_matches(self, supplied: str) -> bool:
        """Whether a request carries the token -- in constant time, so how
        long a wrong guess takes says nothing about how close it was."""
        if self.token is None:
            return True
        return secrets.compare_digest(supplied.encode("utf-8"),
                                      self.token.encode("utf-8"))

    def new_client_id(self) -> str:
        with self._id_lock:
            self._next_id += 1
            return f"c{self._next_id}"

    def previz_scene(self) -> scenemod.Scene:
        """The previz app's scene, for the rig this engine is driving NOW.

        Rebuilt on every request rather than cached. It costs about 0.3 ms, the
        app polls once a second, and a cache would need invalidating from every
        path that can change what is drawn -- a patch reload, a live venue edit,
        a model re-exported on disk -- each one a way for the previz to show a
        room that no longer exists. One read of `self.controller.rig`: a reload
        swaps the reference rather than mutating the old rig, so the build sees
        one rig, never half of two.
        """
        controller = self.controller
        return scenemod.build(controller.rig, controller.event_dir)

    # -- broadcasting ------------------------------------------------------

    def broadcast_loop(self) -> None:
        """Serialise and push at BROADCAST_HZ.

        Deliberately not per frame. The display cannot show 40 Hz, and
        serialising this much JSON 40 times a second on a thread sharing a GIL
        with the DMX clock is precisely the in-process CPU-bound work the F2
        spike identified as the thing that breaks it.
        """
        period = 1.0 / BROADCAST_HZ
        while not self._stop.is_set():
            start = time.perf_counter()
            if self.controller.library_rev != self._looks_sent:
                # The library changed: every console gets the new list, ahead
                # of the snapshot that may name a look in it.
                try:
                    frame = self.controller.looks_frame()
                    self.send_all(json.dumps(frame), ordered=True)
                    self._looks_sent = frame["rev"]
                except Exception:                           # noqa: BLE001
                    self.controller.note("look list failed: "
                                         + traceback.format_exc(limit=1))
            try:
                payload = json.dumps(self.controller.snapshot())
            except Exception:                               # noqa: BLE001
                self.controller.note("snapshot failed: " + traceback.format_exc(limit=1))
                payload = None
            if payload is not None:
                self.send_all(payload)
            elapsed = time.perf_counter() - start
            self._stop.wait(max(0.0, period - elapsed))

    def send_all(self, payload: str, *, ordered: bool = False) -> None:
        """Hand the payload to each client's own sender. Never blocks.

        A snapshot by default, kept latest-wins; `ordered` for a message every
        client must receive, which is kept in order like a reply.

        This used to call `ws.send` inline, which is a blocking `sendall` on a
        socket with no timeout, from the single broadcast thread. One client
        that stopped reading -- a phone that locked its screen, or walked out of
        wifi range -- filled its TCP window, the send blocked, and the console
        froze for everyone else. At a venue that reads as the engine hanging,
        and the cause is nowhere near the symptom. Found by leaving three idle
        clients connected in a test.

        A timeout on the socket cannot fix it: a receive timeout can land
        halfway through a frame, and the header is already consumed by then, so
        the read stream is unrecoverable. `select` for writability cannot fix it
        either -- it reports ready when a single byte of buffer is free, and a
        30 kB snapshot then blocks partway anyway.

        So the broadcast thread is made structurally incapable of blocking: an
        outbox per client, drained by that client's own thread. Handing a
        snapshot over is an assignment under a lock nobody holds across a send.

        The outbox keeps one snapshot, the newest, so a client that is behind
        is not dropped for it -- it used to be, at three snapshots, and that
        dropped browsers that were only busy (see `SEND_DEADLINE_S`). What is
        dropped is a client whose socket has not taken one message in that
        long, and this is still where it happens: two comparisons per client
        on a thread that visits every client ten times a second anyway. Its
        browser reconnects on the backoff it already implements.
        """
        now = time.monotonic()
        for cid, conn in list(self.sockets.items()):
            why = conn.given_up(now)
            if why is None:
                (conn.offer_reply if ordered else conn.offer_snapshot)(payload)
                continue
            who = self.controller.clients.get(cid)
            self.controller.note(f"dropped {who.name if who else cid}: {why}")
            self.drop(cid)

    def send_to(self, cid: str, payload: dict) -> None:
        """One message to one client. Never blocks, because it is called from
        the output thread with a frame waiting.

        The reply waits in the client's outbox behind any made before it, and
        is not discarded for a client that is behind: a page that was busy for
        two seconds still learns that its save went through. A client with
        `REPLY_BACKLOG` of them unread is not dropped from here but by the
        broadcast loop, because closing a socket can wait on the network and
        the output thread cannot.
        """
        conn = self.sockets.get(cid)
        if conn is None:
            return
        try:
            text = json.dumps(payload)
        except (TypeError, ValueError) as exc:
            # A handler returned something JSON cannot carry. The sender still
            # learns the command ran; the data is what is lost, and says so.
            text = json.dumps({"type": "reply", "id": payload.get("id"),
                               "ok": payload.get("ok"),
                               "error": f"reply data not serialisable: {exc}"})
        conn.offer_reply(text)

    def drop(self, cid: str) -> None:
        conn = self.sockets.pop(cid, None)
        self.controller.remove_client(cid)
        # A designer that went away must not leave the rig on its transport.
        self.controller.submit_call(lambda: self.controller.preview_gone(cid))
        if conn is not None:
            conn.close()

    # -- one connection ----------------------------------------------------

    def serve_websocket(self, sock: socket.socket, key: str,
                        tier: str = "configure") -> None:
        ws = WebSocket(sock)
        cid = self.new_client_id()
        conn = Connection(ws)
        lock = conn.lock
        client = Client(id=cid, connected_at=time.time(), tier=tier)
        self.sockets[cid] = conn
        self.controller.add_client(client)
        try:
            with lock:
                ws.send(json.dumps({"type": "welcome", "id": cid,
                                    "tier": tier}))
                # The look list is not in the snapshot: a console is sent it
                # once as it connects, and again only when it changes.
                ws.send(json.dumps(self.controller.looks_frame()))
                ws.send(json.dumps(self.controller.snapshot()))
            while not self._stop.is_set():
                text = ws.receive()
                if text is None:
                    continue
                try:
                    message = strict_json(text)
                except ValueError:                 # JSONDecodeError is one too
                    continue
                # Valid JSON that is not an object -- `[1, 2]`, `"go"`, `7` --
                # used to reach `.get` below and end this client's connection.
                # It is not a command; ignore it like any other junk.
                if not isinstance(message, dict):
                    continue
                if message.get("type") == "hello":
                    client.name = str(message.get("name", "someone"))[:40]
                self.controller.submit(message, client)
        except (WebSocketClosed, WebSocketError, OSError):
            pass
        finally:
            self.drop(cid)

    # -- running -----------------------------------------------------------

    def start(self) -> None:
        server = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            # Unbuffered reads. Without this, `rfile` can pull bytes past the
            # end of the handshake into a buffer the WebSocket layer never sees
            # -- so a client that pipelines its first frame immediately after
            # the upgrade has that frame silently eaten. With rbufsize 0 the
            # socket stays the single source of truth once we hand it over.
            rbufsize = 0

            def log_message(self, *args):        # quiet; the UI shows status
                pass

            def finish(self):
                # The WebSocket session closes the socket itself, so the normal
                # teardown is operating on an already-dead connection. Let it
                # try and swallow what it finds, rather than leaving
                # ThreadingHTTPServer to print a traceback per disconnect.
                try:
                    super().finish()
                except (OSError, ValueError, AttributeError):
                    pass

            def cross_origin(self) -> bool:
                """A browser page on another site opening a socket to us.

                Browsers send Origin on a WebSocket handshake and do NOT apply
                the same-origin policy to it, so without this any page the
                operator happens to have open could drive the rig. A missing
                Origin is not a browser at all -- the tests' own client, a
                script -- and is left alone, since the token is what guards
                those.
                """
                origin = self.headers.get("Origin")
                if not origin:
                    return False
                host = self.headers.get("Host", "")
                return origin.split("//", 1)[-1].rstrip("/") != host

            def do_GET(self):
                if self.headers.get("Upgrade", "").lower() == "websocket":
                    key = self.headers.get("Sec-WebSocket-Key")
                    if not key:
                        self.send_error(400, "missing Sec-WebSocket-Key")
                        return
                    if self.cross_origin():
                        self.send_error(403, "cross-origin websocket refused")
                        return
                    # The token rides in the query string because that is what
                    # survives being turned into a QR code and scanned by a
                    # phone -- there is no login form to put it in, and there
                    # should not be one at a load-in.
                    query = parse_qs(urlparse(self.path).query)
                    supplied = (query.get("token") or [""])[0]
                    tier = ("configure" if server.token_matches(supplied)
                            else "view")
                    from .websocket import handshake_response
                    self.wfile.write(handshake_response(key))
                    self.wfile.flush()
                    # Hand the raw socket over. close_connection stops the
                    # handler looping for another request line off a socket that
                    # is now speaking a different protocol; `finish` above deals
                    # with the teardown finding it already closed.
                    self.close_connection = True
                    server.serve_websocket(self.connection, key, tier=tier)
                    return
                # The previz app's two reads first: /api/previz/ is under /api/
                # but is not engine/api.py's -- see serve_previz.
                if urlparse(self.path).path.startswith("/api/previz/"):
                    self.serve_previz(urlparse(self.path).path)
                    return
                if urlparse(self.path).path.startswith("/api/"):
                    self.serve_api()
                    return
                self.serve_static()

            def serve_previz(self, path):
                """The standalone previz app's two reads: its scene, and the
                model files that scene names.

                View tier, read-only, no token: the same rig the console
                already shows every phone. Models are served by CONTENT HASH
                and only when the current scene names that hash, so this
                cannot be used to fetch an arbitrary file, and a model that has
                not changed is never downloaded twice.
                """
                # Refused cross-origin like the rest of /api/. The app sends no
                # Origin, so this only ever stops a browser page elsewhere.
                if self.cross_origin():
                    self.send_error(403, "cross-origin request refused")
                    return
                try:
                    scene = server.previz_scene()
                except Exception as exc:                # noqa: BLE001
                    # A previz must never be able to take the console down.
                    body = json.dumps({"error": f"no previz scene: {exc}"}).encode("utf-8")
                    self.send_response(503)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                if path == "/api/previz/scene":
                    etag = f'"{scene.rev}"'
                    if self.headers.get("If-None-Match") == etag:
                        self.send_response(304)
                        self.send_header("ETag", etag)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
                    body = scene.to_json()
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("ETag", etag)
                    self.send_header("Cache-Control", "no-cache")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                prefix, suffix = "/api/previz/model/", ".glb"
                sha = path[len(prefix):-len(suffix)] if (
                    path.startswith(prefix) and path.endswith(suffix)) else ""
                if sha in scene.files:
                    body = scene.read_model(sha)
                    if body is None:
                        # Changed on disk since it was hashed. The next scene
                        # names its new hash; the app fetches that instead.
                        self.send_error(503, "model changed on disk; poll the scene again")
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "model/gltf-binary")
                    # Named by its own hash, so it can never change: cache hard.
                    self.send_header("Cache-Control", "public, max-age=31536000, immutable")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers()
                    self.wfile.write(body)
                    return
                self.send_error(404, "not part of the current previz scene")

            def serve_api(self):
                """GET /api/* -- see engine/api.py."""
                if self.cross_origin():
                    self.send_error(403, "cross-origin request refused")
                    return
                url = urlparse(self.path)
                supplied = ((parse_qs(url.query).get("token") or [""])[0]
                            or self.headers.get("X-Klights-Token", ""))
                token_ok = server.token_matches(supplied)
                try:
                    resp = apimod.handle(server.controller.show_library, url.path,
                                         self.headers.get("Range"), token_ok,
                                         server.audio_roots,
                                         server.controller.collection, url.query,
                                         looks=server.controller.looks_public)
                except OSError as exc:
                    resp = apimod.Response(500, json.dumps(
                        {"error": str(exc)}).encode("utf-8"))
                self.send_response(resp.status)
                self.send_header("Content-Type", resp.content_type)
                for name, value in resp.headers.items():
                    self.send_header(name, value)
                length = resp.length if resp.file is not None else len(resp.body)
                self.send_header("Content-Length", str(length))
                self.end_headers()
                if resp.file is None:
                    self.wfile.write(resp.body)
                    return
                with open(resp.file, "rb") as fh:
                    fh.seek(resp.start)
                    left = resp.length
                    while left > 0:
                        chunk = fh.read(min(65536, left))
                        if not chunk:
                            break
                        self.wfile.write(chunk)
                        left -= len(chunk)

            def serve_static(self):
                path = self.path.split("?", 1)[0]
                if path == "/":
                    path = "/index.html"
                target = (server.ui_dir / path.lstrip("/")).resolve()
                try:
                    # Containment check: a request for /../../secrets must not
                    # escape the bundle directory.
                    target.relative_to(server.ui_dir.resolve())
                except (ValueError, OSError):
                    self.send_error(403)
                    return
                if not target.is_file():
                    # A missing ASSET is a real 404, not a client route. The
                    # bundle's asset names are content-hashed, so a request for
                    # one that is not there means a client is holding a stale
                    # index.html -- and answering it with index.html hands the
                    # browser HTML where it asked for JavaScript, which it
                    # refuses to execute. That is a white screen with an
                    # obscure console error; a 404 is a white screen with an
                    # obvious one, and the reload below stops both.
                    if path.startswith("/assets/"):
                        self.send_error(404, "stale bundle -- reload the page")
                        return
                    # A single-page app owns its own routing, so an unknown path
                    # is a client route, not a 404 -- unless the bundle is
                    # missing entirely, which deserves a real explanation.
                    index = server.ui_dir / "index.html"
                    if not index.is_file():
                        self.send_placeholder()
                        return
                    target = index
                body = target.read_bytes()
                self.send_response(200)
                self.send_header("Content-Type",
                                 MIME.get(target.suffix, "application/octet-stream"))
                self.send_header("Content-Length", str(len(body)))
                # Caching, and the two halves are opposites on purpose.
                #
                # Assets are content-hashed by the build, so a given name can
                # never change contents: cache them for a year and a phone
                # coming back to the console loads instantly off local storage.
                #
                # index.html is the one file whose contents change under a
                # fixed name, and it is the file that names which asset to
                # fetch. With no header at all a browser applies its own
                # heuristic and can hold it for hours -- so a phone that had the
                # console open before an engine update keeps asking for an asset
                # the rebuild deleted. `version` in the snapshot exists to
                # DETECT that; this is what stops it happening.
                if path.startswith("/assets/"):
                    self.send_header("Cache-Control",
                                     "public, max-age=31536000, immutable")
                else:
                    self.send_header("Cache-Control", "no-cache")
                self.end_headers()
                self.wfile.write(body)

            def send_placeholder(self):
                body = PLACEHOLDER.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        # Binds here, so a taken port raises before any thread starts.
        self.httpd = ExclusiveHTTPServer((self.bind, self.port), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, name="http",
                         daemon=True).start()
        threading.Thread(target=self.broadcast_loop, name="broadcast",
                         daemon=True).start()

    def stop(self) -> None:
        self._stop.set()
        for cid in list(self.sockets):
            self.drop(cid)
        if self.httpd is not None:
            self.httpd.shutdown()
            self.httpd.server_close()


PLACEHOLDER = """<!doctype html>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>kLights engine</title>
<style>
 body{font:16px/1.6 system-ui,sans-serif;background:#111;color:#eee;margin:0;padding:2rem;}
 code{background:#222;padding:.15em .4em;border-radius:3px;}
 .ok{color:#7c7}
</style>
<h1>Engine is running</h1>
<p class="ok">The WebSocket is live on this same port. The UI bundle has not been built yet.</p>
<pre><code>cd ui
npm install
npm run build</code></pre>
<p>Then reload. For development with hot reload, <code>npm run dev</code> serves on
its own port and proxies here.</p>
"""


def local_addresses(port: int) -> list[str]:
    """URLs a phone on the same network can actually reach.

    Printed at startup because the single most common load-in failure is typing
    localhost into a phone. Uses a UDP connect to a public address, which sets
    the socket's local address without sending anything -- the reliable way to
    learn which interface would carry outbound traffic.
    """
    urls = [f"http://localhost:{port}"]
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("8.8.8.8", 80))
        urls.append(f"http://{probe.getsockname()[0]}:{port}")
        probe.close()
    except OSError:
        pass
    return urls


def main(argv: Optional[list[str]] = None) -> int:
    import argparse
    import errno

    parser = argparse.ArgumentParser(description="Run the show engine and its UI")
    parser.add_argument("--event", type=Path, default=REPO / "events" / "despacio")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--artnet", metavar="IP",
                        help="send Art-Net here (e.g. 127.0.0.1 or a broadcast "
                             "address). Several: comma separated, each host or "
                             "host:port, e.g. 10.0.0.50,127.0.0.1")
    parser.add_argument("--fps", type=float, default=40.0)
    parser.add_argument("--bpm", type=float, default=124.0)
    parser.add_argument("--ui", type=Path, default=UI_DIST)
    parser.add_argument("--bind", default="0.0.0.0",
                        help="interface to listen on (127.0.0.1 for this "
                             "machine only)")
    parser.add_argument("--token",
                        help="require this token for control. Default: a fresh "
                             "one each run, printed in the URL below")
    parser.add_argument("--no-token", action="store_true",
                        help="no access control -- every client may do anything")
    parser.add_argument("--sync-port", type=int, metavar="PORT",
                        help="listen for tempo from a DJ bridge (JSON or OSC "
                             "over UDP). Off unless given -- see engine/sync.py")
    parser.add_argument("--sync-bind", default="127.0.0.1", metavar="IP",
                        help="interface for --sync-port. Loopback by default, "
                             "because the bridge normally runs on this machine "
                             "and the port has no authentication")
    parser.add_argument("--stop-file", type=Path, metavar="PATH",
                        help="stop cleanly when this file appears. For a "
                             "launcher, which cannot send Ctrl-C to a process "
                             "with no console -- see launcher/")
    parser.add_argument("--show-dir", metavar="DIR",
                        help="the show folder: prepped tracks and their "
                             "timelines (F19). Default: $KLIGHTS_SHOW_DIR, then "
                             "show_dir in klights.local.json, else none")
    args = parser.parse_args(argv)
    if args.artnet:
        try:
            parse_artnet_targets(args.artnet)
        except ValueError as exc:
            parser.error(str(exc))
    # A stop request older than this engine is not addressed to it: it is what
    # an engine that died before it could tidy up left behind.
    if args.stop_file is not None:
        args.stop_file.unlink(missing_ok=True)

    # A token by default, because the alternative default is that anyone who can
    # reach the port can re-address the rig. Regenerated every run: there is
    # nothing to remember, and a link shared last week stops working, which is
    # the correct behaviour for a link that grants control of a lighting rig.
    token = None if args.no_token else (args.token or secrets.token_urlsafe(6))

    show_dir = showfiles.resolve_show_dir(args.show_dir)
    # This machine's own output addresses (milestone 3), over the show's.
    local_outputs, problem = outputsmod.local_override(
        showfiles.read_local_config().get("outputs"))
    if problem is not None:
        print(f"outputs: {problem}", file=sys.stderr)
    controller = ShowController(args.event, artnet=args.artnet, fps=args.fps,
                                bpm=args.bpm, show_dir=show_dir,
                                local_outputs=local_outputs)
    server = ShowServer(controller, port=args.port, ui_dir=args.ui,
                        token=token, bind=args.bind)

    # The port before anything touches the rig. controller.start() writes the
    # event's lock and starts sending Art-Net, so a second engine that got that
    # far before finding its port taken would flash the rig, and on its way out
    # delete the lock that belongs to the engine already running.
    try:
        server.start()
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            raise SystemExit(f"port {args.port} is already in use -- "
                             f"is another engine running?")
        raise SystemExit(f"cannot listen on {args.bind}:{args.port}: "
                         f"{exc.strerror or exc}")

    if args.sync_port:
        # The tempo port too, for the same reason: an engine sharing it with
        # another hears none of the DJ, and nothing on screen says so.
        try:
            controller.enable_sync(args.sync_port, args.sync_bind)
        except OSError as exc:
            server.stop()
            if exc.errno == errno.EADDRINUSE:
                raise SystemExit(f"sync port {args.sync_port} is already in use -- "
                                 f"is another engine, or an OSC app, listening there?")
            raise SystemExit(f"cannot listen for sync on {args.sync_bind}:"
                             f"{args.sync_port}: {exc.strerror or exc}")

    controller.start()

    print(f"event   {controller.rig.name}  "
          f"({len(controller.rig.fixtures)} fixtures, universes {controller.rig.universes})")
    print(f"output  {'Art-Net -> ' + args.artnet if args.artnet else 'null (no wire)'}")
    # The taper is the only thing standing between a stored pose and someone's
    # eyes, and its policy is editable from a phone. Say what it is set to every
    # time, so "why is nothing dimming" is answered before it is asked.
    taper = controller.ctx.taper
    if not taper.enabled:
        print("safety  TAPER DISABLED -- beams are not guarded. See docs/SAFETY.md")
    else:
        print(f"safety  taper on, crowd level {taper.crowd_level:.0%}"
              f"{' -- beams over the crowd go dark' if taper.crowd_level == 0 else ''}"
              f"  (docs/SAFETY.md)")
    # Strobe is the one genuine medical risk here and it is easy to forget the
    # policy exists, so it is printed whether or not it is doing anything.
    strobe = controller.ctx.strobe_policy
    if not strobe.enabled:
        print("strobe  blocked entirely")
    elif strobe.max_seconds > 0 or strobe.ceiling < 1.0:
        parts = []
        if strobe.ceiling < 1.0:
            parts.append(f"capped at {strobe.ceiling:.0%} of the band")
        if strobe.max_seconds > 0:
            parts.append(f"cut off after {strobe.max_seconds:g}s continuous")
        print(f"strobe  {', '.join(parts)}")
    else:
        print("strobe  UNLIMITED -- no ceiling, no duration cap "
              "(docs/SAFETY.md)")
    print(f"timing  {', '.join(controller.runner.applied_timing)}")
    # Said out loud because it is a write path into the show clock that no token
    # guards -- a datagram cannot be challenged. Anyone reading this line should
    # be able to tell whether it is bound where they meant.
    if controller.sync is not None:
        print(f"sync    tempo ingest on {args.sync_bind}:{args.sync_port} "
              f"(UDP, JSON or OSC, unauthenticated)")
    library = controller.show_library
    if library is not None:
        f = library.folder
        problems = (f" -- {len(f.errors)} errors, see `python -m "
                    f"engine.showfiles check {library.root}`" if f.errors else "")
        print(f"shows   {library.root}: {library.describe()}{problems}")
        print(f"outputs {controller.outputs.describe()}")
        if controller.player is not None:
            print("follow  " + ("ARMED -- a matched track's timeline drives the "
                                "rig" if controller.player.armed else
                                "DISARMED -- the DJ feed shows but drives "
                                "nothing until someone arms it on the phone"))
    print(f"ui      {'bundle at ' + str(args.ui) if Path(args.ui).is_dir() else 'not built -- see the page for how'}")
    if token is None:
        print("access  OPEN -- anyone who can reach this port has full control")
    else:
        print(f"access  token {token} required to control; without it, view only")
    # The token goes in the URL because the single most common load-in failure
    # is typing something wrong into a phone, and a URL can be a QR code.
    suffix = "" if token is None else f"?token={token}"
    for url in local_addresses(args.port):
        print(f"open    {url}{suffix}")
    print("\nCtrl-C to stop." if args.stop_file is None
          else f"\nCtrl-C, or create {args.stop_file}, to stop.", flush=True)

    try:
        while True:
            time.sleep(0.5)
            if args.stop_file is not None and args.stop_file.exists():
                print("\nstop requested...", flush=True)
                break
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        server.stop()
        controller.stop()
        if args.stop_file is not None:
            args.stop_file.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
