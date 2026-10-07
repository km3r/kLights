"""
A minimal RFC 6455 WebSocket server, in the standard library only.

Written by hand rather than pulled in as a dependency, deliberately. The whole
engine has no third-party imports, which means a show laptop needs a Python and
a checkout and nothing else -- no pip install at load-in, no virtualenv that
rotted since the last event, no version drift between the machine that was
tested and the machine that is in the room. That is worth about 150 lines.

What is implemented: the handshake, text and binary frames, fragmentation,
ping/pong, and close. What is not: extensions (permessage-deflate), and the
`Sec-WebSocket-Protocol` negotiation. Neither is needed to push a few kilobytes
of JSON to a phone on the same LAN.
"""

from __future__ import annotations

import base64
import hashlib
import socket
import struct
from typing import Optional

# RFC 6455's magic constant. Concatenated with the client's key and hashed, it
# proves the server understood the handshake rather than merely echoing it.
WS_MAGIC = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"

OP_CONT = 0x0
OP_TEXT = 0x1
OP_BINARY = 0x2
OP_CLOSE = 0x8
OP_PING = 0x9
OP_PONG = 0xA

# A frame larger than this is refused rather than allocated. Nothing this
# protocol carries is close to it, so a huge length field means a confused or
# hostile client, and believing it would hand over a memory-sized allocation.
MAX_PAYLOAD = 1 << 20


class WebSocketError(Exception):
    pass


class WebSocketClosed(WebSocketError):
    pass


def accept_key(client_key: str) -> str:
    digest = hashlib.sha1((client_key + WS_MAGIC).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def handshake_response(client_key: str) -> bytes:
    return (
        "HTTP/1.1 101 Switching Protocols\r\n"
        "Upgrade: websocket\r\n"
        "Connection: Upgrade\r\n"
        f"Sec-WebSocket-Accept: {accept_key(client_key)}\r\n"
        "\r\n"
    ).encode("ascii")


def encode_frame(payload: bytes, opcode: int = OP_TEXT) -> bytes:
    """One unfragmented server->client frame. Server frames are never masked."""
    header = bytearray([0x80 | opcode])
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length < (1 << 16):
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    return bytes(header) + payload


class WebSocket:
    """One connection. Not thread-safe for concurrent *sends*; the server owns
    a lock per connection, because a broadcast thread and a reply to a command
    can otherwise interleave two frames on the wire and corrupt both."""

    def __init__(self, sock: socket.socket):
        self.sock = sock
        self.closed = False
        self._buffer = b""

    # -- reading -----------------------------------------------------------

    def _recv_exactly(self, n: int) -> bytes:
        while len(self._buffer) < n:
            try:
                chunk = self.sock.recv(65536)
            except (TimeoutError, socket.timeout):
                raise
            except OSError as exc:
                raise WebSocketClosed(str(exc)) from exc
            if not chunk:
                raise WebSocketClosed("peer closed the connection")
            self._buffer += chunk
        out, self._buffer = self._buffer[:n], self._buffer[n:]
        return out

    def receive(self) -> Optional[str]:
        """Next complete text message, or None for a non-text control result.

        Handles fragmentation by accumulating continuation frames, and answers
        pings inline -- a ping that goes unanswered gets the connection dropped
        by the browser after a while, which presents as the phone mysteriously
        disconnecting mid-set.
        """
        chunks: list[bytes] = []
        message_op: Optional[int] = None
        size = 0

        while True:
            first, second = self._recv_exactly(2)
            fin = bool(first & 0x80)
            opcode = first & 0x0F
            masked = bool(second & 0x80)
            length = second & 0x7F

            if length == 126:
                (length,) = struct.unpack(">H", self._recv_exactly(2))
            elif length == 127:
                (length,) = struct.unpack(">Q", self._recv_exactly(8))
            if length > MAX_PAYLOAD:
                raise WebSocketError(f"frame of {length} bytes exceeds the limit")
            # The limit is on the MESSAGE, not only the frame: continuation
            # frames each under it would otherwise add up without bound.
            if opcode == OP_CONT and size + length > MAX_PAYLOAD:
                raise WebSocketError(f"message of over {size + length} bytes "
                                     f"exceeds the limit")

            # A client frame that is not masked is a protocol violation, and
            # unmasking with a zero key would silently accept it.
            if not masked:
                raise WebSocketError("client frame was not masked")
            mask = self._recv_exactly(4)
            payload = bytearray(self._recv_exactly(length))
            for i in range(length):
                payload[i] ^= mask[i & 3]
            payload = bytes(payload)

            if opcode == OP_CLOSE:
                self.close()
                raise WebSocketClosed("peer sent close")
            if opcode == OP_PING:
                self.send_raw(encode_frame(payload, OP_PONG))
                continue
            if opcode == OP_PONG:
                continue

            if opcode in (OP_TEXT, OP_BINARY):
                message_op = opcode
                chunks = [payload]
                size = length
            elif opcode == OP_CONT:
                chunks.append(payload)
                size += length
            else:
                raise WebSocketError(f"unknown opcode {opcode}")

            if fin:
                data = b"".join(chunks)
                if message_op == OP_TEXT:
                    return data.decode("utf-8", errors="replace")
                return None

    # -- writing -----------------------------------------------------------

    def send_raw(self, data: bytes) -> None:
        if self.closed:
            raise WebSocketClosed("already closed")
        try:
            self.sock.sendall(data)
        except OSError as exc:
            self.closed = True
            raise WebSocketClosed(str(exc)) from exc

    def send(self, text: str) -> None:
        self.send_raw(encode_frame(text.encode("utf-8"), OP_TEXT))

    # No server-initiated ping. The state broadcast runs at 10 Hz, so a
    # connection is never idle long enough for an intermediary to time it out --
    # a keepalive here would be a second mechanism doing the same job. Client
    # pings are still answered in `receive`, which is what browsers actually do.

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        try:
            self.sock.sendall(encode_frame(b"", OP_CLOSE))
        except OSError:
            pass
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self.sock.close()
        except OSError:
            pass
