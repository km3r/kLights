"""
Tests for the hand-written RFC 6455 layer (`engine/websocket.py`), on its own.

The server suite drives this through a real handshake, but only with the
frames a well-behaved client sends. Every console in the room speaks through
these ~200 lines, so this suite sends the rest: each length encoding at its
boundary, a ping between two fragments of one message, a frame delivered a
byte at a time, a client that never masks, never finishes, or lies about how
long its frame is.

The client side is encoded here, independently, rather than by calling
`encode_frame` -- a bug shared by encoder and decoder would otherwise cancel
out. Each test is a `socket.socketpair()`, so nothing binds a port.

Run: python engine/tests/test_websocket.py
"""

import os
import socket
import struct
import sys
import threading
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO))

from engine import websocket as ws  # noqa: E402

failures: list[str] = []


def check(label, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {label}" + (f"  -- {detail}" if detail else ""))
    if not ok:
        failures.append(label + (f"  -- {detail}" if detail else ""))


def client_frame(payload: bytes, opcode: int = ws.OP_TEXT, fin: bool = True,
                 masked: bool = True, mask: bytes = b"\x37\xfa\x21\x3d",
                 claimed_length: int | None = None) -> bytes:
    """A client->server frame, encoded from the RFC rather than from the module."""
    length = len(payload) if claimed_length is None else claimed_length
    out = bytearray([(0x80 if fin else 0) | opcode])
    bit = 0x80 if masked else 0
    if length < 126:
        out.append(bit | length)
    elif length < 65536:
        out.append(bit | 126)
        out += struct.pack(">H", length)
    else:
        out.append(bit | 127)
        out += struct.pack(">Q", length)
    if masked:
        out += mask
        out += bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
    else:
        out += payload
    return bytes(out)


def decode_server_frame(data: bytes) -> tuple[int, bool, bytes, bytes]:
    """(opcode, fin, payload, rest) for one server->client frame."""
    first, second = data[0], data[1]
    if second & 0x80:
        raise AssertionError("server frames must not be masked")
    length, at = second & 0x7F, 2
    if length == 126:
        (length,), at = struct.unpack(">H", data[2:4]), 4
    elif length == 127:
        (length,), at = struct.unpack(">Q", data[2:10]), 10
    return first & 0x0F, bool(first & 0x80), data[at:at + length], data[at + length:]


class Pair:
    """A server-side WebSocket and the raw client socket talking to it."""

    def __init__(self):
        self.server_sock, self.client = socket.socketpair()
        self.server_sock.settimeout(3)
        self.client.settimeout(3)
        self.ws = ws.WebSocket(self.server_sock)

    def send(self, *frames: bytes) -> None:
        self.client.sendall(b"".join(frames))

    def read_all(self) -> bytes:
        self.client.settimeout(0.3)
        data = b""
        try:
            while True:
                chunk = self.client.recv(65536)
                if not chunk:
                    break
                data += chunk
        except (TimeoutError, socket.timeout):
            pass
        self.client.settimeout(3)
        return data

    def close(self):
        for s in (self.server_sock, self.client):
            try:
                s.close()
            except OSError:
                pass


def raises(fn, exc_type) -> tuple[bool, str]:
    try:
        fn()
    except exc_type as exc:
        return True, str(exc)
    except Exception as exc:  # noqa: BLE001
        return False, f"{type(exc).__name__}: {exc}"
    return False, "no exception"


print("\n1. the handshake")
# RFC 6455 section 1.3's own worked example.
check("the RFC's sample key produces the RFC's sample accept",
      ws.accept_key("dGhlIHNhbXBsZSBub25jZQ==") == "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")
resp = ws.handshake_response("dGhlIHNhbXBsZSBub25jZQ==").decode("ascii")
check("the response is a 101 with the upgrade headers and a blank line",
      resp.startswith("HTTP/1.1 101 Switching Protocols\r\n")
      and "Upgrade: websocket\r\n" in resp and "Connection: Upgrade\r\n" in resp
      and "Sec-WebSocket-Accept: s3pPLMBiTxaQ9kYGzzhZRbK+xOo=\r\n" in resp
      and resp.endswith("\r\n\r\n"))


print("\n2. encode_frame: each length form at its boundary")
for size, header_len, form in [(0, 2, "7-bit"), (125, 2, "7-bit"), (126, 4, "16-bit"),
                               (65535, 4, "16-bit"), (65536, 10, "64-bit")]:
    frame = ws.encode_frame(b"x" * size)
    op, fin, payload, rest = decode_server_frame(frame)
    check(f"{size} bytes: {form} length, {header_len}-byte header, unmasked, FIN set",
          len(frame) == header_len + size and op == ws.OP_TEXT and fin
          and payload == b"x" * size and rest == b"")
op, _, payload, _ = decode_server_frame(ws.encode_frame(b"\x01\x02", ws.OP_BINARY))
check("the opcode is carried through", op == ws.OP_BINARY and payload == b"\x01\x02")


print("\n3. receiving: lengths, masks and splits")
for size in (0, 1, 125, 126, 1000, 65535, 65536, 200_000):
    p = Pair()
    body = (b"abcdefghij" * (size // 10 + 1))[:size]
    sender = threading.Thread(target=p.send, args=(client_frame(body),))
    sender.start()
    got = p.ws.receive()
    sender.join()
    check(f"a {size}-byte text frame arrives intact", got == body.decode(),
          f"got {len(got or '')} chars")
    p.close()

p = Pair()
p.send(client_frame("héllo ✓ 🎛".encode("utf-8")))
check("UTF-8 survives the mask", p.ws.receive() == "héllo ✓ 🎛")
p.close()

p = Pair()
p.send(client_frame(b"zero mask", mask=b"\x00\x00\x00\x00"))
check("an all-zero mask is still a mask", p.ws.receive() == "zero mask")
p.close()

p = Pair()
frame = client_frame(b'{"type":"go"}')
sender = threading.Thread(target=lambda: [p.client.sendall(frame[i:i + 1]) for i in range(len(frame))])
sender.start()
check("a frame delivered one byte at a time is reassembled", p.ws.receive() == '{"type":"go"}')
sender.join()
p.close()

p = Pair()
p.send(client_frame(b"first"), client_frame(b"second"), client_frame(b"third"))
check("three frames in one segment come out as three messages, in order",
      [p.ws.receive() for _ in range(3)] == ["first", "second", "third"])
p.close()

p = Pair()
p.send(client_frame(b"\xff\xfe broken \xc3"))
got = p.ws.receive()
check("invalid UTF-8 is replaced, not raised (a junk message must not drop a client)",
      got is not None and "broken" in got and "�" in got, repr(got))
p.close()

p = Pair()
p.send(client_frame(b"\x00\x01\x02", opcode=ws.OP_BINARY))
p.send(client_frame(b"after"))
check("a binary message yields None, and the next text message still arrives",
      p.ws.receive() is None and p.ws.receive() == "after")
p.close()


print("\n4. fragmentation and control frames")
p = Pair()
p.send(client_frame(b"one ", fin=False), client_frame(b"two ", opcode=ws.OP_CONT, fin=False),
       client_frame(b"three", opcode=ws.OP_CONT))
check("three fragments are one message", p.ws.receive() == "one two three")
p.close()

p = Pair()
p.send(client_frame(b"hel", fin=False), client_frame(b"are you there", opcode=ws.OP_PING),
       client_frame(b"lo", opcode=ws.OP_CONT))
got = p.ws.receive()
op, fin, payload, rest = decode_server_frame(p.read_all())
check("a ping between fragments is answered with a pong carrying its payload",
      op == ws.OP_PONG and fin and payload == b"are you there" and rest == b"")
check("and does not break the message it interrupted", got == "hello", repr(got))
p.close()

p = Pair()
p.send(client_frame(b"", opcode=ws.OP_PONG), client_frame(b"still here"))
check("an unsolicited pong is ignored", p.ws.receive() == "still here"
      and p.read_all() == b"")
p.close()

p = Pair()
p.send(client_frame(b"\x03\xe8", opcode=ws.OP_CLOSE))
ok, why = raises(p.ws.receive, ws.WebSocketClosed)
check("a close frame raises WebSocketClosed", ok, why)
op, _, payload, _ = decode_server_frame(p.read_all())
check("having answered with a close of its own, and marked itself closed",
      op == ws.OP_CLOSE and p.ws.closed)
p.close()

p = Pair()
p.send(client_frame(b"x", opcode=0x3))
ok, why = raises(p.ws.receive, ws.WebSocketError)
check("a reserved opcode is a protocol error", ok and "unknown opcode 3" in why, why)
p.close()

p = Pair()
p.send(client_frame(b"orphan", opcode=ws.OP_CONT), client_frame(b"next"))
check("a continuation with no message to continue is dropped, not crashed on",
      p.ws.receive() is None and p.ws.receive() == "next")
p.close()


print("\n5. what a hostile or confused client cannot do")
p = Pair()
p.send(client_frame(b"plain", masked=False))
ok, why = raises(p.ws.receive, ws.WebSocketError)
check("an unmasked client frame is refused", ok and "not masked" in why, why)
p.close()

p = Pair()
# Only the header: a 2 GiB claim with no payload behind it. Refused on the
# header alone -- if it tried to read the payload first, this would hang.
p.send(client_frame(b"", claimed_length=ws.MAX_PAYLOAD + 1)[:10])
ok, why = raises(p.ws.receive, ws.WebSocketError)
check("a frame claiming more than MAX_PAYLOAD is refused before it is read",
      ok and "exceeds the limit" in why, why)
p.close()

p = Pair()
p.send(client_frame(b"", claimed_length=1 << 62)[:10])
ok, why = raises(p.ws.receive, ws.WebSocketError)
check("as is a 64-bit length near the top of the range", ok, why)
p.close()

p = Pair()
chunk = os.urandom(ws.MAX_PAYLOAD // 2)
p.send(client_frame(b"", fin=False))


def flood():
    try:
        for _ in range(3):
            p.client.sendall(client_frame(chunk, opcode=ws.OP_CONT, fin=False))
    except OSError:
        pass


sender = threading.Thread(target=flood)
sender.start()
ok, why = raises(p.ws.receive, ws.WebSocketError)
check("continuation frames that each fit but together exceed MAX_PAYLOAD are refused "
      "(the cap is on the message, not just the frame)", ok and "message" in why, why)
p.close()
sender.join()

p = Pair()
half = b"a" * (ws.MAX_PAYLOAD // 2)
sender = threading.Thread(target=p.send, args=(
    client_frame(half, fin=False) + client_frame(half, opcode=ws.OP_CONT),))
sender.start()
got = p.ws.receive()
sender.join()
check("a message of exactly MAX_PAYLOAD across two fragments is accepted",
      got is not None and len(got) == ws.MAX_PAYLOAD)
p.close()

p = Pair()
p.send(client_frame(b"cut short")[:6])
p.client.shutdown(socket.SHUT_WR)
ok, why = raises(p.ws.receive, ws.WebSocketClosed)
check("a peer that hangs up mid-frame is a clean WebSocketClosed", ok, why)
p.close()

p = Pair()
p.client.close()
ok, why = raises(p.ws.receive, ws.WebSocketClosed)
check("so is one that hangs up between frames", ok, why)
p.close()

p = Pair()
p.server_sock.settimeout(0.2)
ok, why = raises(p.ws.receive, (TimeoutError, socket.timeout))
check("a quiet client is a timeout the caller sees, not a disconnect", ok, why)
p.close()


print("\n6. sending and closing")
p = Pair()
p.ws.send("snapshot ✓")
op, fin, payload, _ = decode_server_frame(p.read_all())
check("send writes one unmasked text frame", op == ws.OP_TEXT and fin
      and payload.decode("utf-8") == "snapshot ✓")
p.ws.close()
check("close marks the socket closed", p.ws.closed)
p.ws.close()
check("and closing twice is harmless", p.ws.closed)
ok, why = raises(lambda: p.ws.send("late"), ws.WebSocketClosed)
check("sending after close raises WebSocketClosed", ok and "already closed" in why, why)
p.close()

p = Pair()
p.client.close()
errors = []
for _ in range(5):
    try:
        p.ws.send("x" * 65536)
    except ws.WebSocketClosed as exc:
        errors.append(str(exc))
        break
check("a send to a peer that has gone raises WebSocketClosed and marks it closed",
      errors and p.ws.closed, f"{errors}")
p.ws.close()
check("and close afterwards does not raise", p.ws.closed)
p.close()


print()
if failures:
    print(f"websocket: {len(failures)} FAILED")
    for f in failures:
        print(f"  - {f}")
    sys.exit(1)
print("websocket: all checks pass")
