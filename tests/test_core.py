"""Unit tests for keys, curves, profiles and the controller. Run: python -m unittest -v"""

from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path

from stusteer.controller import Controller
from stusteer.keys import KEYS, key_def, normalize_key, press_order, release_order
from stusteer.mapping import Curve, shape_signed, shape_stick, shape_unsigned
from stusteer.output.dryrun import DryRunGamepad, DryRunKeyboard
from stusteer.output.keyboard_win import FocusGuard
from stusteer.profile import ProfileError, load_profile, parse_profile

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PROFILE = ROOT / "stusteer" / "profiles" / "gta5_lspdfr.json"


def small_profile(**settings):
    return parse_profile(
        {
            "schema": 2,
            "name": "test",
            "settings": {"min_hold_ms": 60, "watchdog_ms": 500, **settings},
            "outputs": {"keyboard": {"target_processes": []}},
            "devices": [
                {"id": "box", "type": "network", "controls": [
                    {"id": "horn", "type": "button", "bind": {"key": "E"}},
                    {"id": "throttle", "type": "pedal", "bind": {"axis": "RT"}, "curve": {"shape": "linear", "deadzone": 0}},
                ]},
                {"id": "phone", "type": "phone", "layouts": [
                {
                    "id": "a",
                    "controls": [
                        {"id": "wheel", "type": "wheel", "x": 0, "y": 0, "w": 10, "h": 10, "bind": {"axis": "LX"},
                         "curve": {"range": 50, "deadzone": 0}},
                        {"id": "gas", "type": "pedal", "x": 0, "y": 0, "w": 10, "h": 10, "bind": {"axis": "RT"},
                         "curve": {"deadzone": 0, "shape": "linear"}},
                        {"id": "hb", "type": "button", "x": 0, "y": 0, "w": 10, "h": 10, "bind": {"pad": "RB"}},
                        {"id": "menu", "type": "button", "x": 0, "y": 0, "w": 10, "h": 10, "bind": {"key": ["ctrl", "n"]}},
                        {"id": "e1", "type": "button", "x": 0, "y": 0, "w": 10, "h": 10, "bind": {"key": "E"}},
                        {"id": "e2", "type": "button", "x": 0, "y": 0, "w": 10, "h": 10, "bind": {"key": "E"}},
                        {"id": "ls", "type": "stick", "x": 0, "y": 0, "w": 10, "h": 10, "bind": {"stick": "L"},
                         "curve": {"deadzone": 0}},
                    ],
                }
                ]},
            ],
        }
    )


class KeysTest(unittest.TestCase):
    def test_known_codes(self):
        self.assertEqual((KEYS["E"].scan, KEYS["E"].vk), (0x12, 0x45))
        self.assertEqual(KEYS["Y"].scan, 0x15)
        self.assertTrue(KEYS["Y"].layout)  # looked up in the active layout (QWERTZ has it elsewhere)
        self.assertEqual(KEYS["B"].scan, 0x30)
        self.assertEqual(KEYS["LSHIFT"].scan, 0x2A)
        self.assertEqual(KEYS["F4"].scan, 0x3E)
        self.assertEqual((KEYS["END"].scan, KEYS["END"].extended), (0x4F, True))
        self.assertEqual((KEYS["NUMPAD1"].scan, KEYS["NUMPAD1"].extended), (0x4F, False))

    def test_keys_beyond_the_keyboard(self):
        # F13-F24: scan codes 0x64-0x6E and 0x76, VK_F13..VK_F24 = 0x7C..0x87 (Windows kbd.h / WinUser.h)
        self.assertEqual([KEYS[f"F{n}"].scan for n in (13, 15, 23, 24)], [0x64, 0x66, 0x6E, 0x76])
        self.assertEqual([KEYS[f"F{n}"].vk for n in (1, 13, 24)], [0x70, 0x7C, 0x87])
        self.assertEqual(KEYS["MEDIA_PLAY_PAUSE"].kind, "vk")
        self.assertEqual(key_def(normalize_key("rmb")).mouse, 2)
        self.assertEqual(normalize_key("sc:64"), "SC:0x64")
        self.assertEqual(key_def(normalize_key("SC:0xE05F")), key_def("SC:0xE05F"))
        self.assertTrue(key_def("SC:0xE05F").extended)
        self.assertEqual(key_def(normalize_key("vk:0x7e")).vk, 0x7E)
        for bad in ("SC:0x00", "SC:0x80", "SC:0x1234", "VK:0x100", "SC:zz"):
            with self.assertRaises(KeyError):
                normalize_key(bad)

    def test_aliases(self):
        self.assertEqual(normalize_key("shift"), "LSHIFT")
        self.assertEqual(normalize_key(" Enter "), "ENTER")
        with self.assertRaises(KeyError):
            normalize_key("SHFT")

    def test_combo_order(self):
        self.assertEqual(press_order({"N", "LCTRL"}), ["LCTRL", "N"])
        self.assertEqual(release_order({"N", "LCTRL"}), ["N", "LCTRL"])


class CurveTest(unittest.TestCase):
    def test_linear(self):
        c = Curve(range=50, deadzone=0)
        self.assertAlmostEqual(shape_signed(25, c), 0.5)
        self.assertAlmostEqual(shape_signed(-25, c), -0.5)
        self.assertEqual(shape_signed(80, c), 1.0)  # past full lock: clamped

    def test_deadzone_and_anti_deadzone(self):
        c = Curve(range=11, deadzone=1, anti_deadzone=0.2)
        self.assertEqual(shape_signed(0.9, c), 0.0)
        self.assertAlmostEqual(shape_signed(1.0001, c), 0.2, places=3)  # jumps past the game's deadzone
        self.assertAlmostEqual(shape_signed(6, c), 0.2 + 0.8 * 0.5)
        self.assertEqual(shape_signed(11, c), 1.0)

    def test_gamma_and_invert(self):
        c = Curve(range=1, gamma=2, invert=True)
        self.assertAlmostEqual(shape_signed(0.5, c), -0.25)

    def test_pedal(self):
        c = Curve(range=1, deadzone=0.1)
        self.assertEqual(shape_unsigned(0.05, c), 0)
        self.assertEqual(shape_unsigned(-1, c), 0)
        self.assertAlmostEqual(shape_unsigned(0.55, c), 0.5)

    def test_stick_keeps_direction(self):
        x, y = shape_stick(0.3, 0.4, Curve(range=1, deadzone=0))
        self.assertAlmostEqual(x, 0.3)
        self.assertAlmostEqual(y, 0.4)
        self.assertEqual(shape_stick(0.05, 0.05, Curve(range=1, deadzone=0.1)), (0.0, 0.0))

    def test_bad_numbers(self):
        c = Curve(range=50)
        self.assertEqual(shape_signed(float("nan"), c), 0.0)
        self.assertEqual(shape_unsigned(float("inf"), c), 0.0)
        self.assertEqual(shape_stick(float("nan"), 0, c), (0.0, 0.0))

    def test_scurve_and_cap(self):
        c = Curve(range=1, shape="scurve")
        self.assertAlmostEqual(shape_unsigned(0.25, c), 0.15625)  # 3t^2 - 2t^3: soft start
        self.assertAlmostEqual(shape_unsigned(0.5, c), 0.5)
        self.assertAlmostEqual(shape_unsigned(0.75, c), 0.84375)
        self.assertAlmostEqual(shape_unsigned(1, Curve(range=1, max_output=0.7)), 0.7)
        self.assertTrue(Curve(shape="cubic").validate())
        with self.assertRaises(ValueError):
            Curve().updated({"shape": 3})

    def test_validation(self):
        self.assertEqual(Curve(range=50, deadzone=1).validate(), [])
        self.assertTrue(Curve(range=1, deadzone=2).validate())
        self.assertTrue(Curve(anti_deadzone=1).validate())
        with self.assertRaises(KeyError):
            Curve().updated({"bogus": 1})


class ProfileTest(unittest.TestCase):
    def test_default_profile_is_valid(self):
        profile = load_profile(DEFAULT_PROFILE)
        phone = profile.device(None)
        self.assertEqual(phone.id, "phone")
        self.assertEqual([l["id"] for l in phone.layouts], ["drive", "drive_touch", "foot", "lspdfr"])
        self.assertEqual(phone.controls["wheel"].axis, "LX")
        self.assertEqual(phone.controls["stop"].keys, ("LSHIFT",))
        self.assertEqual(profile.outputs.key_targets, ("GTA5.exe",))
        gas = phone.controls["gas"]
        self.assertEqual((gas.options["mode"], gas.curve.shape), ("absolute", "scurve"))  # A by default, S-curve
        self.assertEqual(phone.controls["aim"].options["mode"], "digital")
        self.assertIsNone(profile.device("buttonbox"))  # the example box is disabled
        client = phone.to_client()
        json.dumps(client)  # must be serialisable for the welcome message
        wheel = next(c for c in client["layouts"][0]["controls"] if c["id"] == "wheel")
        self.assertEqual(wheel["curve"]["range"], 60)
        self.assertEqual(next(c for c in client["layouts"][0]["controls"] if c["id"] == "gas")["mode"], "absolute")

    def test_every_problem_is_reported(self):
        data = json.loads(DEFAULT_PROFILE.read_text(encoding="utf-8"))
        broken = copy.deepcopy(data)
        controls = broken["devices"][0]["layouts"][0]["controls"]
        controls[0]["bind"] = {"axis": "ZZ"}
        controls[1]["x"] = 150
        controls[2]["bind"] = {"key": "NOPE"}
        controls[3]["id"] = controls[4]["id"]
        controls[0]["mode"] = "sideways"
        broken["devices"][1]["type"] = "midi"
        with self.assertRaises(ProfileError) as ctx:
            parse_profile(broken)
        message = str(ctx.exception)
        self.assertIn("6 problem(s)", message)
        self.assertIn("unknown key 'NOPE'", message)
        self.assertIn("duplicate control id", message)
        self.assertIn("mode must be one of", message)
        self.assertIn("planned, not available yet", message)
        with self.assertRaises(ProfileError):
            parse_profile({"layouts": []})  # schema 1 files are refused with a pointer to the format

    def test_tuning_overrides(self):
        profile = small_profile()
        warnings = profile.apply_tuning({
            "curves": {"phone/wheel": {"range": 30}, "phone/hb": {"range": 3}, "phone/gas": {"deadzone": 5}},
            "options": {"phone/gas": {"mode": "relative", "travel": 0.3}, "phone/wheel": {"mode": "x"}},
        })
        self.assertEqual(profile.control("phone/wheel").curve.range, 30)
        self.assertEqual(profile.control("phone/gas").options, {"mode": "relative", "travel": 0.3})
        self.assertEqual(len(warnings), 3)


class FakeClockController:
    def __init__(self, **settings):
        self.pad = DryRunGamepad()
        self.kb = DryRunKeyboard()
        self.c = Controller(small_profile(**settings), self.pad, self.kb)

    @property
    def last_pad(self):
        return self.pad.history[-1]


class ControllerTest(unittest.TestCase):
    def test_axes_follow_snapshots(self):
        t = FakeClockController()
        t.c.apply("phone", {"wheel": -25, "gas": 0.5, "ls": [0, 1]}, now=0.0)
        buttons, axes = t.last_pad
        self.assertEqual(buttons, frozenset())
        self.assertAlmostEqual(axes["LX"], -0.5)  # wheel and stick both drive LX: furthest from zero wins
        self.assertAlmostEqual(axes["LY"], 1.0)
        self.assertAlmostEqual(axes["RT"], 0.5)
        t.c.apply("phone", {}, now=0.01)
        self.assertEqual(set(t.last_pad[1].values()), {0.0})

    def test_button_minimum_hold(self):
        t = FakeClockController()
        t.c.apply("phone", {"hb": 1}, now=0.0)
        self.assertEqual(t.last_pad[0], frozenset({"RB"}))
        t.c.apply("phone", {}, now=0.005)  # a 5 ms tap: still held, the game might not have seen it yet
        self.assertEqual(t.last_pad[0], frozenset({"RB"}))
        t.c.tick(0.059)
        self.assertEqual(t.last_pad[0], frozenset({"RB"}))
        t.c.tick(0.061)
        self.assertEqual(t.last_pad[0], frozenset())

    def test_key_combo_order_and_sharing(self):
        t = FakeClockController(min_hold_ms=0)
        t.c.apply("phone", {"menu": 1}, now=0.0)
        self.assertEqual(t.kb.history, [("LCTRL", True), ("N", True)])
        t.c.apply("phone", {}, now=0.1)
        self.assertEqual(t.kb.history[2:], [("N", False), ("LCTRL", False)])
        t.kb.history.clear()
        t.c.apply("phone", {"e1": 1, "e2": 1}, now=0.2)
        t.c.apply("phone", {"e2": 1}, now=0.3)  # still held by the other button
        self.assertEqual(t.kb.history, [("E", True)])
        t.c.apply("phone", {}, now=0.4)
        self.assertEqual(t.kb.history, [("E", True), ("E", False)])

    def test_watchdog_releases_everything(self):
        t = FakeClockController()
        t.c.apply("phone", {"gas": 1, "hb": 1, "e1": 1}, now=0.0)
        t.c.tick(0.4)
        self.assertEqual(t.last_pad[1]["RT"], 1.0)
        t.c.tick(0.51)
        self.assertEqual(t.last_pad, (frozenset(), dict.fromkeys(["LX", "LY", "RX", "RY", "LT", "RT"], 0.0)))
        self.assertEqual(t.kb.history[-1], ("E", False))
        t.c.apply("phone", {"gas": 1}, now=0.6)  # the phone is back
        self.assertEqual(t.last_pad[1]["RT"], 1.0)

    def test_neutralize_and_shutdown(self):
        t = FakeClockController()
        t.c.apply("phone", {"e1": 1, "hb": 1, "gas": 0.7}, now=0.0)
        t.c.neutralize("phone", 0.01)
        self.assertEqual(t.last_pad[1]["RT"], 0.0)
        self.assertNotIn(("E", False), t.kb.history)  # hold time first
        t.c.release_all_now()
        self.assertEqual(t.kb.history[-1], ("E", False))
        self.assertEqual(t.last_pad[0], frozenset())

    def test_garbage_input_is_harmless(self):
        t = FakeClockController()
        t.c.apply("phone", {"wheel": "left", "gas": None, "ls": [1], "hb": "yes", "nope": 1, "e1": True}, now=0.0)
        self.assertEqual(t.last_pad[0], frozenset())
        self.assertEqual(t.kb.history, [("E", True)])
        self.assertEqual(set(t.last_pad[1].values()), {0.0})

    def test_only_changes_are_sent(self):
        t = FakeClockController()
        t.c.apply("phone", {"gas": 0.5}, now=0.0)
        t.c.apply("phone", {"gas": 0.5}, now=0.1)
        t.c.tick(0.2)
        self.assertEqual(len(t.pad.history), 1)


class MultiDeviceTest(unittest.TestCase):
    def test_devices_merge_and_time_out_separately(self):
        t = FakeClockController()
        t.c.apply("phone", {"gas": 0.4, "e1": 1}, now=0.0)
        t.c.apply("box", {"throttle": 0.9, "horn": 1}, now=0.0)
        self.assertAlmostEqual(t.last_pad[1]["RT"], 0.9)  # the furthest from zero wins
        t.c.apply("box", {"throttle": 0.9, "horn": 1}, now=0.3)
        t.c.apply("box", {}, now=0.4)                       # E still held by the phone
        self.assertNotIn(("E", False), t.kb.history)
        self.assertAlmostEqual(t.last_pad[1]["RT"], 0.4)
        t.c.apply("box", {"throttle": 1}, now=0.45)
        t.c.tick(0.6)                                       # the phone went quiet 0.6 s ago
        self.assertEqual(t.kb.history[-1], ("E", False))
        self.assertEqual(t.last_pad[1]["RT"], 1.0)          # the box is still alive
        t.c.neutralize("box", 0.7)
        self.assertEqual(t.last_pad[1]["RT"], 0.0)
        self.assertEqual(t.c.outputs()["devices"], [])

    def test_unknown_device_is_ignored(self):
        t = FakeClockController()
        t.c.apply("nobody", {"gas": 1}, now=0.0)
        self.assertEqual(t.pad.history, [])


class FocusGuardTest(unittest.TestCase):
    def test_only_target_window_gets_presses(self):
        window = ["chrome.exe"]
        guard = FocusGuard(["GTA5.exe"], lambda: window[0])
        self.assertFalse(guard.allows_press())
        window[0] = "gta5.EXE"
        self.assertTrue(guard.allows_press())
        self.assertTrue(FocusGuard([], lambda: None).allows_press())

    def test_blocked_press_is_retried(self):
        class Guarded(DryRunKeyboard):
            allow = False

            def key(self, name, down):
                return super().key(name, down) if (self.allow or not down) else False

        kb = Guarded()
        c = Controller(small_profile(), DryRunGamepad(), kb)
        c.apply("phone", {"e1": 1}, now=0.0)
        self.assertEqual(kb.history, [])
        kb.allow = True  # GTA gets focus while the button is still held
        c.tick(0.01)
        self.assertEqual(kb.history, [("E", True)])


if __name__ == "__main__":
    unittest.main()
