"""The receiver's network side: serves the phone page and runs the WebSocket session.

One asyncio event loop does everything on one thread, so the controller needs no locks:

- an HTTP(S) server: GET / and the files in stusteer/web/ (the phone app), GET /ca.crt;
- GET /ws?pin=NNNNNN&device=<id> with "Upgrade: websocket": a device session (device defaults to
  the first phone in the profile). A wrong PIN is refused, and five wrong PINs from one address
  within a minute block it for a minute;
- a 10 ms ticker that runs the controller's watchdogs and deferred key releases.

Each device has at most one session: a new connection for the same device takes over and the old
one is closed. Different devices (the phone and a button box) are connected at the same time.

Messages (JSON text frames). Device -> PC:
    {"t":"s", "seq":17, "layout":"drive", "c":{"wheel":-12.5, "gas":0.8, "horn":1}}   snapshot
    {"t":"ping", "id":5}                                  round-trip probe; answered with outputs
    {"t":"tune", "id":"gas", "curve":{"shape":"scurve"}, "options":{"mode":"relative"}}   live change
    {"t":"tune_reset", "id":"gas"}                        back to the profile's curve and options
    {"t":"save"}                                          keep the tuning for next time
PC -> device:
    {"t":"welcome", "version":2, "device":{"id","type"}, "profile":{...}}   after the PIN check
    {"t":"pong", "id":5, "out":{"axes":{...}, "buttons":[...], "keys":[...], "devices":[...]}}
    {"t":"tuned", "id":"gas", "curve":{...}, "options":{...}}  /  {"t":"saved", ...}  /  {"t":"error", ...}
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
from .profile import Control, check_options
from .websocket import ConnectionClosed, WebSocket, accept_key

log = logging.getLogger("stusteer.server")
PROTOCOL_VERSION = 2
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
        self._sessions: dict[str, WebSocket] = {}
        self._failures: dict[str, deque[float]] = defaultdict(deque)
        # The profile's own curves and options, kept for "reset"; the saved tuning goes on top.
        profile = controller.profile
        controls = [c for d in profile.devices.values() for c in d.controls.values()]
        self._base_curves = {c.address: c.curve for c in controls if c.curve}
        self._base_options = {c.address: dict(c.options) for c in controls}
        for warning in profile.apply_tuning(tuning.load()):
            log.warning("%s", warning)

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
        requested = (request.query.get("device") or [None])[0]
        device = self.controller.profile.device(requested)
        if device is None:
            log.warning("refused %s: no enabled device '%s' in the profile", ip, requested or "(default phone)")
            return await _respond(writer, 404, b"no such device")
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
        old = self._sessions.get(device.id)
        if old is not None:
            log.info("%s: a new connection takes over", device.id)
            await old.close(4000)
        self._sessions[device.id] = ws
        self.controller.neutralize(device.id, self._clock())
        log.info("%s connected from %s (%s)", device.id, ip, request.headers.get("user-agent", "?")[:80])
        try:
            await ws.send_text(json.dumps({
                "t": "welcome", "version": PROTOCOL_VERSION,
                "device": {"id": device.id, "type": device.type}, "profile": device.to_client(),
            }))
            while True:
                message = await ws.recv()
                if isinstance(message, str):
                    reply = self.handle_message(message, device.id)
                    if reply is not None:
                        await ws.send_text(json.dumps(reply))
        except ConnectionClosed as exc:
            log.info("%s disconnected (%s)", device.id, exc)
        finally:
            if self._sessions.get(device.id) is ws:
                del self._sessions[device.id]
                self.controller.neutralize(device.id, self._clock())
            await ws.close()

    def handle_message(self, text: str, device_id: str = "phone") -> dict[str, Any] | None:
        """Handle one message from a device; return the reply, if any. Never raises on bad input."""
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
                self.controller.apply(device_id, values, self._clock(), layout)
            return None
        if kind == "ping":
            return {"t": "pong", "id": msg.get("id"), "out": self.controller.outputs()}
        control = self._control(device_id, msg.get("id"))
        if kind == "tune":
            if control is None:
                return {"t": "error", "error": f"no control '{msg.get('id')}'"}
            return self._tune(control, msg.get("curve"), msg.get("options"))
        if kind == "tune_reset":
            if control is None:
                return {"t": "error", "error": f"no control '{msg.get('id')}'"}
            control.curve = self._base_curves.get(control.address)
            control.options = dict(self._base_options[control.address])
            return self._tuned_reply(control)
        if kind == "save":
            curves, options = {}, {}
            for address, base in self._base_curves.items():
                current = self.controller.profile.control(address).curve
                diff = {k: v for k, v in current.to_dict().items() if base.to_dict().get(k) != v}
                if diff:
                    curves[address] = diff
            for address, base in self._base_options.items():
                current = self.controller.profile.control(address).options
                diff = {k: v for k, v in current.items() if base.get(k) != v}
                if diff:
                    options[address] = diff
            self._tuning.save(curves, options)
            log.info("tuning saved to %s", self._tuning.path)
            return {"t": "saved", "path": str(self._tuning.path)}
        return {"t": "error", "error": f"unknown message type {kind!r}"}

    def _control(self, device_id: str, control_id: Any) -> Control | None:
        device = self.controller.profile.devices.get(device_id)
        if device is None or not isinstance(control_id, str):
            return None
        return device.controls.get(control_id)

    @staticmethod
    def _tuned_reply(control: Control) -> dict[str, Any]:
        return {
            "t": "tuned", "id": control.id,
            "curve": control.curve.to_dict() if control.curve else None, "options": dict(control.options),
        }

    def _tune(self, control: Control, curve_changes: Any, option_changes: Any) -> dict[str, Any]:
        """Apply live changes to a control's curve and options; all or nothing."""
        curve: Curve | None = control.curve
        if curve_changes is not None:
            if curve is None or not isinstance(curve_changes, dict):
                return {"t": "error", "error": f"'{control.id}' has no curve"}
            try:
                curve = curve.updated(curve_changes)
            except (KeyError, ValueError, TypeError) as exc:
                return {"t": "error", "error": f"bad curve field: {exc}"}
            problems = curve.validate()
            if problems:
                return {"t": "error", "error": "; ".join(problems)}
        options: dict[str, Any] = {}
        if option_changes is not None:
            if not isinstance(option_changes, dict):
                return {"t": "error", "error": "options must be an object"}
            options, problems = check_options(control.type, option_changes)
            if problems:
                return {"t": "error", "error": "; ".join(problems)}
        control.curve = curve
        control.options.update(options)
        log.info("tuned %s: curve=%s options=%s", control.address, curve_changes or {}, options)
        return self._tuned_reply(control)


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
