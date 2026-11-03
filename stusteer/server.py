"""The receiver's network side: serves the phone page and runs the WebSocket session.

One asyncio event loop does everything on one thread, so the controller needs no locks:

- an HTTP(S) server: GET / and the files in stusteer/web/ (the phone app), GET /ca.crt;
- GET /ws?pin=NNNNNN with "Upgrade: websocket": the controller session. A wrong PIN is refused, and
  five wrong PINs from one address within a minute block it for a minute;
- a 10 ms ticker that runs the controller's watchdog and deferred key releases.

Only one phone controls the PC at a time: a new connection takes over and the old one is closed.

Messages (JSON text frames). Phone -> PC:
    {"t":"s", "seq":17, "layout":"drive", "c":{"wheel":-12.5, "gas":0.8, "horn":1}}   snapshot
    {"t":"ping", "id":5}                                  round-trip probe; answered with outputs
    {"t":"tune", "id":"wheel", "curve":{"range":70}}      change a curve live
    {"t":"tune_reset", "id":"wheel"}                      back to the profile's curve
    {"t":"save"}                                          keep the tuning for next time
PC -> phone:
    {"t":"welcome", "version":1, "profile":{...}}         after the PIN is accepted
    {"t":"pong", "id":5, "out":{"axes":{...}, "buttons":[...], "keys":[...]}}
    {"t":"tuned", "id":"wheel", "curve":{...}}  /  {"t":"saved", "path":"..."}  /  {"t":"error", ...}
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import socket
import ssl
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlsplit

from .config import TuningStore
from .controller import Controller
from .mapping import Curve
from .websocket import ConnectionClosed, WebSocket, accept_key

log = logging.getLogger("stusteer.server")
PROTOCOL_VERSION = 1
WEB_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/style.css": ("style.css", "text/css; charset=utf-8"),
    "/manifest.webmanifest": ("manifest.webmanifest", "application/manifest+json"),
    "/icon.svg": ("icon.svg", "image/svg+xml"),
}


@dataclass
class Request:
    method: str
    path: str
    query: dict[str, list[str]]
    headers: dict[str, str]


class Receiver:
    def __init__(
        self,
        controller: Controller,
        pin: str,
        tuning: TuningStore,
        web_dir: Path,
        ca_cert: Path | None = None,
        clock=time.monotonic,
    ) -> None:
        self.controller = controller
        self._pin = pin
        self._tuning = tuning
        self._web_dir = web_dir
        self._ca_cert = ca_cert
        self._clock = clock
        self._active: WebSocket | None = None
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        # The profile's own curves, kept for "reset"; then the saved tuning goes on top of them.
        profile = controller.profile
        self._profile_curves = {cid: c.curve for cid, c in profile.controls.items() if c.curve}
        saved = tuning.load()
        for warning in profile.apply_tuning(saved):
            log.warning("%s", warning)
        self._tuned: dict[str, dict[str, Any]] = {
            cid: changes for cid, changes in saved.items()
            if cid in self._profile_curves and profile.controls[cid].curve != self._profile_curves[cid]
        }

    # -- serving -------------------------------------------------------------------------------

    async def serve(self, host: str, port: int, ssl_context: ssl.SSLContext | None) -> asyncio.Server:
        server = await asyncio.start_server(self._handle, host, port, ssl=ssl_context)
        asyncio.get_running_loop().create_task(self._ticker())
        return server

    async def _ticker(self) -> None:
        while True:
            self.controller.tick(self._clock())
            await asyncio.sleep(0.01)

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        peer = writer.get_extra_info("peername")
        ip = peer[0] if peer else "?"
        sock = writer.get_extra_info("socket")
        if sock is not None:
            try:  # send small frames at once instead of waiting to batch them (Nagle's algorithm)
                sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            except OSError:
                pass
        try:
            request = await asyncio.wait_for(_read_request(reader), timeout=10)
            if request is None:
                return
            if request.path == "/ws":
                await self._websocket(request, reader, writer, ip)
            elif request.path == "/api/pin":
                # The page checks the PIN here first: a refused WebSocket gives the browser no
                # reason, so it could not tell "wrong PIN" from "PC not reachable".
                status = self._check_pin(request, ip)
                await _respond(writer, status, b"ok" if status == 200 else b"refused")
            else:
                await self._static(request, writer)
        except (asyncio.TimeoutError, ConnectionError, ssl.SSLError):
            pass
        finally:
            if not writer.is_closing():
                writer.close()

    async def _static(self, request: Request, writer: asyncio.StreamWriter) -> None:
        if request.method != "GET":
            return await _respond(writer, 405, b"method not allowed")
        if request.path == "/ca.crt" and self._ca_cert is not None and self._ca_cert.exists():
            return await _respond(writer, 200, self._ca_cert.read_bytes(), "application/x-x509-ca-cert")
        entry = WEB_FILES.get(request.path)
        if entry is None:  # an allow-list, so no path can escape the web folder
            return await _respond(writer, 404, b"not found")
        name, ctype = entry
        await _respond(writer, 200, (self._web_dir / name).read_bytes(), ctype)

    # -- the session ---------------------------------------------------------------------------

    def _blocked(self, ip: str) -> bool:
        window = self._failures[ip]
        now = self._clock()
        while window and now - window[0] > 60:
            window.popleft()
        return len(window) >= 5

    def _check_pin(self, request: Request, ip: str) -> int:
        """200 if the request carries the right PIN, 403 if not, 429 if this address is blocked."""
        if self._blocked(ip):
            log.warning("refused %s: too many wrong PINs, try again in a minute", ip)
            return 429
        pin = (request.query.get("pin") or [""])[0]
        if not hmac.compare_digest(pin.encode(), self._pin.encode()):
            self._failures[ip].append(self._clock())
            log.warning("refused %s: wrong PIN", ip)
            return 403
        return 200

    async def _websocket(self, request: Request, reader, writer, ip: str) -> None:
        status = self._check_pin(request, ip)
        if status != 200:
            return await _respond(writer, status, b"refused")
        key = request.headers.get("sec-websocket-key")
        if request.headers.get("upgrade", "").lower() != "websocket" or not key:
            return await _respond(writer, 400, b"expected a WebSocket upgrade")
        writer.write(
            (
                "HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Accept: {accept_key(key)}\r\n\r\n"
            ).encode("ascii")
        )
        await writer.drain()
        ws = WebSocket(reader, writer)
        if self._active is not None:
            log.info("a new phone connection takes over")
            await self._active.close(4000)
        self._active = ws
        self.controller.neutralize(self._clock())
        log.info("phone connected from %s (%s)", ip, request.headers.get("user-agent", "?")[:80])
        try:
            await ws.send_text(
                json.dumps({"t": "welcome", "version": PROTOCOL_VERSION, "profile": self.controller.profile.to_client()})
            )
            while True:
                message = await ws.recv()
                if isinstance(message, str):
                    reply = self.handle_message(message)
                    if reply is not None:
                        await ws.send_text(json.dumps(reply))
        except ConnectionClosed as exc:
            log.info("phone disconnected (%s)", exc)
        finally:
            if self._active is ws:
                self._active = None
                self.controller.neutralize(self._clock())
            await ws.close()

    def handle_message(self, text: str) -> dict[str, Any] | None:
        """Handle one message from the phone; return the reply, if any. Never raises on bad input."""
        try:
            msg = json.loads(text)
        except json.JSONDecodeError:
            return {"t": "error", "error": "not JSON"}
        if not isinstance(msg, dict):
            return {"t": "error", "error": "expected an object"}
        kind = msg.get("t")
        if kind == "s":
            values = msg.get("c")
            if isinstance(values, dict):
                layout = msg.get("layout") if isinstance(msg.get("layout"), str) else None
                self.controller.apply(values, self._clock(), layout)
            return None
        if kind == "ping":
            return {"t": "pong", "id": msg.get("id"), "out": self.controller.outputs()}
        if kind == "tune":
            return self._tune(msg.get("id"), msg.get("curve"))
        if kind == "tune_reset":
            control_id = msg.get("id")
            if control_id in self._profile_curves:
                self.controller.profile.controls[control_id].curve = self._profile_curves[control_id]
                self._tuned.pop(control_id, None)
                return {"t": "tuned", "id": control_id, "curve": self._profile_curves[control_id].to_dict()}
            return {"t": "error", "error": f"no curve for '{control_id}'"}
        if kind == "save":
            self._tuning.save(self._tuned)
            log.info("tuning saved to %s", self._tuning.path)
            return {"t": "saved", "path": str(self._tuning.path)}
        return {"t": "error", "error": f"unknown message type {kind!r}"}

    def _tune(self, control_id: Any, changes: Any) -> dict[str, Any]:
        control = self.controller.profile.controls.get(control_id) if isinstance(control_id, str) else None
        if control is None or control.curve is None or not isinstance(changes, dict):
            return {"t": "error", "error": f"cannot tune '{control_id}'"}
        try:
            curve: Curve = control.curve.updated(changes)
        except (KeyError, ValueError, TypeError) as exc:
            return {"t": "error", "error": f"bad curve field: {exc}"}
        problems = curve.validate()
        if problems:
            return {"t": "error", "error": "; ".join(problems)}
        control.curve = curve
        base = self._profile_curves[control_id].to_dict()
        self._tuned[control_id] = {k: v for k, v in curve.to_dict().items() if base.get(k) != v}
        log.info("tuned %s: %s", control_id, self._tuned[control_id])
        return {"t": "tuned", "id": control_id, "curve": curve.to_dict()}


async def _read_request(reader: asyncio.StreamReader) -> Request | None:
    try:
        head = await reader.readuntil(b"\r\n\r\n")
    except (asyncio.IncompleteReadError, asyncio.LimitOverrunError):
        return None
    if len(head) > 16 * 1024:
        return None
    lines = head.decode("latin-1").split("\r\n")
    parts = lines[0].split(" ")
    if len(parts) != 3:
        return None
    method, target, _ = parts
    url = urlsplit(target)
    headers = {}
    for line in lines[1:]:
        if ":" in line:
            name, value = line.split(":", 1)
            headers[name.strip().lower()] = value.strip()
    return Request(method, url.path, parse_qs(url.query), headers)


async def _respond(writer: asyncio.StreamWriter, status: int, body: bytes, ctype: str = "text/plain") -> None:
    reasons = {200: "OK", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
               429: "Too Many Requests"}
    writer.write(
        (
            f"HTTP/1.1 {status} {reasons.get(status, 'Error')}\r\nContent-Type: {ctype}\r\n"
            f"Content-Length: {len(body)}\r\nCache-Control: no-cache\r\nConnection: close\r\n\r\n"
        ).encode("ascii")
        + body
    )
    await writer.drain()


def lan_addresses() -> list[str]:
    """This PC's IPv4 addresses on the local network (what the phone should open)."""
    found: set[str] = set()
    try:  # the address the OS would use to reach the LAN; connect() on UDP sends nothing
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("10.255.255.255", 1))
            found.add(s.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.add(str(info[4][0]))
    except OSError:
        pass
    return sorted(a for a in found if not a.startswith("127.") and not a.startswith("169.254."))
