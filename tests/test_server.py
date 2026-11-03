"""Tests for the WebSocket framing and the receiver over a real local socket (HTTP and HTTPS)."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import ssl
import struct
import tempfile
import unittest
from pathlib import Path

from stusteer.config import TuningStore, load_pin
from stusteer.controller import Controller
from stusteer.output.dryrun import DryRunGamepad, DryRunKeyboard
from stusteer.profile import load_profile
from stusteer.server import Receiver
from stusteer.websocket import OP_TEXT, accept_key, unmask

ROOT = Path(__file__).resolve().parents[1]
PROFILE = ROOT / "stusteer" / "profiles" / "gta5_lspdfr.json"
WEB = ROOT / "stusteer" / "web"


def client_frame(opcode: int, payload: bytes, mask: bytes = b"\x01\x02\x03\x04") -> bytes:
    n = len(payload)
    head = bytes([0x80 | opcode])
    if n < 126:
        head += bytes([0x80 | n])
    else:
        head += bytes([0x80 | 126]) + struct.pack("!H", n)
    return head + mask + unmask(payload, mask)  # XOR is its own inverse


async def read_frame(reader) -> tuple[int, bytes]:
    b1, b2 = await reader.readexactly(2)
    n = b2 & 0x7F
    if n == 126:
        (n,) = struct.unpack("!H", await reader.readexactly(2))
    return b1 & 0x0F, await reader.readexactly(n)


class FramingTest(unittest.TestCase):
    def test_rfc_example_key(self):
        # The example handshake from RFC 6455 section 1.3.
        self.assertEqual(accept_key("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_unmask_roundtrip(self):
        data = os.urandom(1000)
        mask = b"\xaa\x0f\x33\x99"
        self.assertEqual(unmask(unmask(data, mask), mask), data)


class ReceiverTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        folder = Path(self.tmp.name)
        self.pad, self.kb = DryRunGamepad(), DryRunKeyboard()
        profile = load_profile(PROFILE)
        profile.outputs.key_targets = ()
        profile.devices["buttonbox"].enabled = True  # the example ESP32 box, for the multi-device test
        self.controller = Controller(profile, self.pad, self.kb)
        self.pin = load_pin(folder)
        self.receiver = Receiver(self.controller, self.pin, TuningStore(folder, "test"), WEB)
        self.server = await self.receiver.serve("127.0.0.1", 0, self.ssl_server())
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()
        self.tmp.cleanup()

    def ssl_server(self):
        return None

    def ssl_client(self):
        return None

    async def http(self, path: str) -> tuple[int, bytes]:
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port, ssl=self.ssl_client())
        writer.write(f"GET {path} HTTP/1.1\r\nHost: x\r\n\r\n".encode())
        await writer.drain()
        data = await reader.read()
        writer.close()
        head, _, body = data.partition(b"\r\n\r\n")
        return int(head.split()[1]), body

    async def open_ws(self, pin: str, device: str | None = None):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port, ssl=self.ssl_client())
        key = base64.b64encode(os.urandom(16)).decode()
        query = f"pin={pin}" + (f"&device={device}" if device else "")
        writer.write(
            (f"GET /ws?{query} HTTP/1.1\r\nHost: x\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
             f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode()
        )
        await writer.drain()
        head = await reader.readuntil(b"\r\n\r\n")
        return reader, writer, head, key

    async def send(self, writer, obj):
        writer.write(client_frame(OP_TEXT, json.dumps(obj).encode()))
        await writer.drain()

    async def test_serves_the_page_and_nothing_else(self):
        status, body = await self.http("/")
        self.assertEqual(status, 200)
        self.assertIn(b"STUSteer", body)
        self.assertEqual((await self.http("/app.js"))[0], 200)
        self.assertEqual((await self.http("/../stusteer/server.py"))[0], 404)
        self.assertEqual((await self.http("/profiles/gta5_lspdfr.json"))[0], 404)

    async def test_pin_check_and_rate_limit(self):
        self.assertEqual((await self.http(f"/api/pin?pin={self.pin}"))[0], 200)
        wrong = "000000" if self.pin != "000000" else "111111"
        for _ in range(5):
            self.assertEqual((await self.http(f"/api/pin?pin={wrong}"))[0], 403)
        self.assertEqual((await self.http(f"/api/pin?pin={self.pin}"))[0], 429)  # blocked even with the right PIN

    async def test_session_drives_the_controller(self):
        reader, writer, head, key = await self.open_ws(self.pin)
        self.assertIn(b"101 Switching Protocols", head)
        self.assertIn(accept_key(key).encode(), head)
        opcode, payload = await read_frame(reader)
        welcome = json.loads(payload)
        self.assertEqual(welcome["t"], "welcome")
        self.assertEqual(welcome["device"], {"id": "phone", "type": "phone"})
        self.assertEqual(welcome["profile"]["layouts"][0]["id"], "drive")

        await self.send(writer, {"t": "s", "layout": "drive", "c": {"wheel": 30, "gas": 1, "backup": 1}})
        await self.send(writer, {"t": "ping", "id": 7})
        _, payload = await read_frame(reader)
        pong = json.loads(payload)
        self.assertEqual(pong["id"], 7)
        self.assertAlmostEqual(pong["out"]["axes"]["LX"], (30 - 0.8) / (60 - 0.8), places=3)
        self.assertEqual(pong["out"]["axes"]["RT"], 1.0)
        self.assertEqual(pong["out"]["keys"], ["B"])

        await self.send(writer, {"t": "tune", "id": "wheel", "curve": {"range": 40}})
        _, payload = await read_frame(reader)
        self.assertEqual(json.loads(payload)["curve"]["range"], 40)
        await self.send(writer, {"t": "tune", "id": "wheel", "curve": {"deadzone": 99}})
        _, payload = await read_frame(reader)
        self.assertEqual(json.loads(payload)["t"], "error")
        await self.send(writer, {"t": "tune", "id": "gas", "curve": {"shape": "linear"}, "options": {"mode": "relative"}})
        _, payload = await read_frame(reader)
        tuned = json.loads(payload)
        self.assertEqual((tuned["curve"]["shape"], tuned["options"]["mode"]), ("linear", "relative"))
        await self.send(writer, {"t": "tune", "id": "gas", "options": {"mode": "sideways"}})
        _, payload = await read_frame(reader)
        self.assertEqual(json.loads(payload)["t"], "error")
        await self.send(writer, {"t": "save"})
        _, payload = await read_frame(reader)
        saved = json.loads(Path(json.loads(payload)["path"]).read_text())
        self.assertEqual(saved["curves"], {"phone/wheel": {"range": 40.0}, "phone/gas": {"shape": "linear"}})
        self.assertEqual(saved["options"], {"phone/gas": {"mode": "relative"}})
        await self.send(writer, {"t": "tune_reset", "id": "gas"})
        _, payload = await read_frame(reader)
        reset = json.loads(payload)
        self.assertEqual((reset["curve"]["shape"], reset["options"]["mode"]), ("scurve", "absolute"))

        writer.close()  # the phone vanishes: everything is released
        for _ in range(50):
            await asyncio.sleep(0.02)
            if not self.controller.outputs()["keys"] and self.controller.outputs()["axes"]["RT"] == 0:
                break
        self.assertEqual(self.controller.outputs()["keys"], [])
        self.assertEqual(self.controller.outputs()["axes"]["RT"], 0.0)
        self.assertEqual(self.kb.history[-1], ("B", False))

    async def test_phone_and_button_box_together(self):
        p_reader, p_writer, _, _ = await self.open_ws(self.pin)
        await read_frame(p_reader)
        b_reader, b_writer, head, _ = await self.open_ws(self.pin, device="buttonbox")
        self.assertIn(b"101", head)
        _, payload = await read_frame(b_reader)
        welcome = json.loads(payload)
        self.assertEqual(welcome["device"]["type"], "network")
        self.assertEqual([c["id"] for c in welcome["profile"]["controls"]][:2], ["sw1", "sw2"])

        await self.send(p_writer, {"t": "s", "c": {"backup": 1}})
        await self.send(b_writer, {"t": "s", "c": {"sw1": 1, "pot": 0.5}})
        await self.send(b_writer, {"t": "ping", "id": 1})
        _, payload = await read_frame(b_reader)
        out = json.loads(payload)["out"]
        self.assertEqual(out["keys"], ["B", "F13"])
        self.assertEqual(out["devices"], ["buttonbox", "phone"])
        self.assertAlmostEqual(out["axes"]["RT"], (0.5 - 0.03) / 0.97, places=3)

        b_writer.close()  # the box drops off; the phone's key stays held
        for _ in range(50):
            await asyncio.sleep(0.02)
            if self.controller.outputs()["keys"] == ["B"]:
                break
        self.assertEqual(self.controller.outputs()["keys"], ["B"])
        p_writer.close()

    async def test_disabled_or_unknown_device_is_refused(self):
        self.controller.profile.devices["buttonbox"].enabled = False
        for device in ("buttonbox", "nope"):
            _, writer, head, _ = await self.open_ws(self.pin, device=device)
            self.assertIn(b"404", head.split(b"\r\n")[0])
            writer.close()

    async def test_wrong_pin_gets_no_session(self):
        wrong = "000000" if self.pin != "000000" else "111111"
        _, writer, head, _ = await self.open_ws(wrong)
        self.assertIn(b"403", head.split(b"\r\n")[0])
        writer.close()

    async def test_bad_messages_get_errors(self):
        self.assertEqual(self.receiver.handle_message("{")["t"], "error")
        self.assertEqual(self.receiver.handle_message("[]")["t"], "error")
        self.assertEqual(self.receiver.handle_message('{"t":"nope"}')["t"], "error")
        self.assertIsNone(self.receiver.handle_message('{"t":"s","c":"x"}'))


class ReceiverHttpsTest(ReceiverTest):
    """The same tests over TLS, with a certificate from our local CA."""

    def ssl_server(self):
        from stusteer.certs import ensure_certificates

        self.cert_dir = tempfile.TemporaryDirectory()
        cert, key = ensure_certificates(Path(self.cert_dir.name), ["192.168.1.50"])
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(cert, key)
        return context

    def ssl_client(self):
        context = ssl.create_default_context(cafile=str(Path(self.cert_dir.name) / "ca.crt"))
        context.check_hostname = True
        return context  # verifies the chain and that 127.0.0.1 is in the certificate

    async def asyncTearDown(self):
        await super().asyncTearDown()
        self.cert_dir.cleanup()

    def test_certificate_is_reused_until_addresses_change(self):
        from stusteer.certs import ensure_certificates

        folder = Path(self.cert_dir.name)
        before = (folder / "server.crt").read_bytes()
        ensure_certificates(folder, ["192.168.1.50"])
        self.assertEqual((folder / "server.crt").read_bytes(), before)
        ca = (folder / "ca.crt").read_bytes()
        ensure_certificates(folder, ["192.168.1.77"])
        self.assertNotEqual((folder / "server.crt").read_bytes(), before)
        self.assertEqual((folder / "ca.crt").read_bytes(), ca)  # the phone's trust stays valid


if __name__ == "__main__":
    unittest.main()
