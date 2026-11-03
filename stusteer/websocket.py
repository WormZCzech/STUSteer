"""A small WebSocket server-side implementation (RFC 6455) on asyncio streams.

Why a WebSocket: a web page cannot open raw UDP or TCP sockets; a WebSocket is the lowest-latency
channel a browser offers. It starts as an HTTP request with "Upgrade: websocket"; the server answers
"101 Switching Protocols" with a hash of the client's key, and from then on both sides exchange
small frames over the same TCP connection.

A frame is: 1 byte FIN flag + opcode (1 text, 2 binary, 8 close, 9 ping, 10 pong), 1 byte MASK
flag + 7-bit length (126 = a 16-bit length follows, 127 = a 64-bit one), then for client frames a
4-byte mask that is XOR-ed over the payload. Our messages are tiny JSON texts (~100 bytes).

Written by hand (about 100 lines) so the receiver needs no extra package for it.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import struct

GUID = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
OP_CONT, OP_TEXT, OP_BINARY, OP_CLOSE, OP_PING, OP_PONG = 0x0, 0x1, 0x2, 0x8, 0x9, 0xA
MAX_MESSAGE = 64 * 1024


def accept_key(client_key: str) -> str:
    """The Sec-WebSocket-Accept value for a client's Sec-WebSocket-Key."""
    digest = hashlib.sha1((client_key + GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def encode_frame(opcode: int, payload: bytes) -> bytes:
    """A server frame (servers never mask)."""
    head = bytes([0x80 | opcode])
    n = len(payload)
    if n < 126:
        head += bytes([n])
    elif n < 1 << 16:
        head += bytes([126]) + struct.pack("!H", n)
    else:
        head += bytes([127]) + struct.pack("!Q", n)
    return head + payload


def unmask(payload: bytes, mask: bytes) -> bytes:
    # XOR with the mask repeated over the payload, done on one big integer: much faster in Python
    # than a byte-by-byte loop.
    if not payload:
        return payload
    n = len(payload)
    key = (mask * (n // 4 + 1))[:n]
    return (int.from_bytes(payload, "big") ^ int.from_bytes(key, "big")).to_bytes(n, "big")


class ConnectionClosed(Exception):
    pass


class WebSocket:
    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        self._reader = reader
        self._writer = writer
        self.closed = False

    async def _read_frame(self) -> tuple[bool, int, bytes]:
        b1, b2 = await self._reader.readexactly(2)
        fin, opcode = bool(b1 & 0x80), b1 & 0x0F
        masked, n = bool(b2 & 0x80), b2 & 0x7F
        if n == 126:
            (n,) = struct.unpack("!H", await self._reader.readexactly(2))
        elif n == 127:
            (n,) = struct.unpack("!Q", await self._reader.readexactly(8))
        if not masked:
            raise ConnectionClosed("client frames must be masked (RFC 6455 5.1)")
        if n > MAX_MESSAGE:
            await self.close(1009)
            raise ConnectionClosed("message too big")
        mask = await self._reader.readexactly(4)
        return fin, opcode, unmask(await self._reader.readexactly(n), mask)

    async def recv(self) -> str | bytes:
        """The next text (str) or binary (bytes) message. Answers pings; raises ConnectionClosed."""
        parts: list[bytes] = []
        first_opcode = None
        while True:
            try:
                fin, opcode, payload = await self._read_frame()
            except (asyncio.IncompleteReadError, ConnectionError) as exc:
                self.closed = True
                raise ConnectionClosed(str(exc)) from exc
            if opcode == OP_PING:
                await self._send(OP_PONG, payload)
                continue
            if opcode == OP_PONG:
                continue
            if opcode == OP_CLOSE:
                await self.close(1000)
                raise ConnectionClosed("closed by the phone")
            if opcode != OP_CONT:
                first_opcode, parts = opcode, []
            parts.append(payload)
            if sum(len(p) for p in parts) > MAX_MESSAGE:
                await self.close(1009)
                raise ConnectionClosed("message too big")
            if fin:
                data = b"".join(parts)
                return data.decode("utf-8") if first_opcode == OP_TEXT else data

    async def _send(self, opcode: int, payload: bytes) -> None:
        if self.closed:
            raise ConnectionClosed("already closed")
        try:
            self._writer.write(encode_frame(opcode, payload))
            await self._writer.drain()
        except ConnectionError as exc:
            self.closed = True
            raise ConnectionClosed(str(exc)) from exc

    async def send_text(self, text: str) -> None:
        await self._send(OP_TEXT, text.encode("utf-8"))

    async def close(self, code: int = 1000) -> None:
        if self.closed:
            return
        try:
            await self._send(OP_CLOSE, struct.pack("!H", code))
        except ConnectionClosed:
            pass
        self.closed = True
        self._writer.close()
