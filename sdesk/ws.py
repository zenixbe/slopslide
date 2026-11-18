"""The little of WebSocket (RFC 6455) that a Chrome extension needs to talk to sdesk. No dependencies, no I/O: bytes in, bytes
out, so it can be tested without a network.

Only what is used: the opening handshake, text frames (both ways), ping/pong and close. A message may arrive in fragments;
they are joined. Frames from the client must be masked (the standard says so) and are rejected if they are not.
"""
import base64
import hashlib
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BIN, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
MAX_MESSAGE = 4 * 1024 * 1024          # a window list with hundreds of tabs is well under this


class ProtocolError(Exception):
    pass


def accept_key(client_key):
    """The Sec-WebSocket-Accept value for a client's Sec-WebSocket-Key."""
    return base64.b64encode(hashlib.sha1((client_key.strip() + GUID).encode()).digest()).decode()


def parse_request(data):
    """(path, {lower-case header: value}) from the client's HTTP upgrade request, or None while it is incomplete."""
    end = data.find(b"\r\n\r\n")
    if end < 0:
        return None
    lines = data[:end].decode("latin-1").split("\r\n")
    parts = lines[0].split()
    if len(parts) < 3 or parts[0] != "GET":
        raise ProtocolError("not a GET request")
    headers = {}
    for line in lines[1:]:
        name, _, value = line.partition(":")
        headers[name.strip().lower()] = value.strip()
    return parts[1], headers


def handshake_response(headers):
    """The 101 response for a valid upgrade request; ProtocolError if it is not one."""
    if headers.get("upgrade", "").lower() != "websocket" or "sec-websocket-key" not in headers:
        raise ProtocolError("not a websocket upgrade")
    return ("HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
            "Sec-WebSocket-Accept: %s\r\n\r\n" % accept_key(headers["sec-websocket-key"])).encode()


def encode_frame(payload, opcode=OP_TEXT):
    """A single unmasked (server to client) frame."""
    if isinstance(payload, str):
        payload = payload.encode()
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([n])
    elif n < 65536:
        head += bytes([126]) + struct.pack(">H", n)
    else:
        head += bytes([127]) + struct.pack(">Q", n)
    return head + payload


class Decoder:
    """Feed it bytes from the socket; it returns the complete messages: [(opcode, payload bytes)]."""

    def __init__(self):
        self.buf = b""
        self._frag_op = None
        self._frag = b""

    def feed(self, data):
        self.buf += data
        out = []
        while True:
            frame = self._take()
            if frame is None:
                return out
            fin, op, payload = frame
            if op >= 0x8:                                   # control frame: never fragmented, may come between fragments
                out.append((op, payload))
            elif op == OP_CONT:
                if self._frag_op is None:
                    raise ProtocolError("continuation without a start")
                self._frag += payload
                if len(self._frag) > MAX_MESSAGE:
                    raise ProtocolError("message too large")
                if fin:
                    out.append((self._frag_op, self._frag))
                    self._frag_op, self._frag = None, b""
            elif fin:
                out.append((op, payload))
            else:
                self._frag_op, self._frag = op, payload

    def _take(self):
        b = self.buf
        if len(b) < 2:
            return None
        fin, op = bool(b[0] & 0x80), b[0] & 0x0F
        masked, n, pos = bool(b[1] & 0x80), b[1] & 0x7F, 2
        if not masked:
            raise ProtocolError("client frame is not masked")
        if n == 126:
            if len(b) < 4:
                return None
            n, pos = struct.unpack(">H", b[2:4])[0], 4
        elif n == 127:
            if len(b) < 10:
                return None
            n, pos = struct.unpack(">Q", b[2:10])[0], 10
        if n > MAX_MESSAGE:
            raise ProtocolError("frame too large")
        if len(b) < pos + 4 + n:
            return None
        mask = b[pos:pos + 4]
        data = bytearray(b[pos + 4:pos + 4 + n])
        for i in range(n):
            data[i] ^= mask[i % 4]
        self.buf = b[pos + 4 + n:]
        return fin, op, bytes(data)


def client_frame(payload, opcode=OP_TEXT, mask=b"\x01\x02\x03\x04", fin=True):
    """A masked client-to-server frame (what a browser sends); for tests and for a stand-in client."""
    if isinstance(payload, str):
        payload = payload.encode()
    n = len(payload)
    head = bytes([(0x80 if fin else 0) | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    elif n < 65536:
        head += bytes([0x80 | 126]) + struct.pack(">H", n)
    else:
        head += bytes([0x80 | 127]) + struct.pack(">Q", n)
    return head + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(payload))
