"""Linux X11 backend: pure logic that needs no live X server.

Runs only on Linux; the module imports python-xlib, which Windows does not install.
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

if not sys.platform.startswith("linux"):
    raise unittest.SkipTest("Linux X11 backend tests run on Linux only")

import core.linux_x11 as lx  # noqa: E402
import core.osapi as osapi  # noqa: E402
from Xlib import XK  # noqa: E402


def ks(name):
    return XK.string_to_keysym(name)


class KeysymMapping(unittest.TestCase):
    def test_modifiers_cover_both_sides(self):
        self.assertEqual(lx._keysyms_for("win"), [ks("Super_L"), ks("Super_R")])
        self.assertEqual(lx._keysyms_for("windows"), [ks("Super_L"), ks("Super_R")])
        self.assertEqual(lx._keysyms_for("alt"), [ks("Alt_L"), ks("Alt_R")])
        self.assertEqual(lx._keysyms_for("ctrl"), [ks("Control_L"), ks("Control_R")])
        self.assertEqual(lx._keysyms_for("control"), [ks("Control_L"), ks("Control_R")])
        self.assertEqual(lx._keysyms_for("shift"), [ks("Shift_L"), ks("Shift_R")])

    def test_sided_variants(self):
        self.assertEqual(lx._keysyms_for("left windows"), [ks("Super_L")])
        self.assertEqual(lx._keysyms_for("right alt"), [ks("Alt_R")])
        self.assertEqual(lx._keysyms_for("left ctrl"), [ks("Control_L")])
        self.assertEqual(lx._keysyms_for("right shift"), [ks("Shift_R")])

    def test_letters_digits_fkeys(self):
        self.assertEqual(lx._keysyms_for("a"), [ks("a")])
        self.assertEqual(lx._keysyms_for("Z"), [ks("z")])
        self.assertEqual(lx._keysyms_for("7"), [ks("7")])
        self.assertEqual(lx._keysyms_for("f4"), [ks("F4")])
        self.assertEqual(lx._keysyms_for("f12"), [ks("F12")])
        self.assertEqual(lx._keysyms_for("space"), [ks("space")])

    def test_unknown_names(self):
        self.assertEqual(lx._keysyms_for("notakey"), [])
        self.assertEqual(lx._keysyms_for(""), [])
        self.assertTrue(all(lx._keysyms_for(k) for k in lx._MODIFIERS))


class ValidateHotkey(unittest.TestCase):
    def test_accepts_and_normalises(self):
        self.assertEqual(lx.validate_hotkey("win+alt"), (True, "win+alt"))
        self.assertEqual(lx.validate_hotkey("Alt + Win"), (True, "win+alt"))
        self.assertEqual(lx.validate_hotkey("shift+ctrl+r"), (True, "ctrl+shift+r"))
        self.assertEqual(lx.validate_hotkey("win+shift"), (True, "win+shift"))

    def test_rejects(self):
        cases = {
            "": "empty",
            "a": "modifier",
            "ctrl+notakey": "not a key name",
            "win": "Whisker",
            "alt+tab": "switches windows",
            "alt+f4": "closes",
            "ctrl+alt+t": "terminal",
            "delete+alt+ctrl": "log-out",
        }
        for combo, needle in cases.items():
            with self.subTest(combo=combo):
                ok, msg = lx.validate_hotkey(combo)
                self.assertFalse(ok)
                self.assertIn(needle, msg)


class TitleTrimming(unittest.TestCase):
    def test_trims_like_win32(self):
        self.assertEqual(lx._trim_title("notes.txt - Mousepad"), "Mousepad")
        self.assertEqual(lx._trim_title("a - b - Firefox "), "Firefox")
        self.assertEqual(lx._trim_title("A very long window title without dash"), "A very long window title")
        self.assertEqual(lx._trim_title(None), "")

    def test_title_from_xdotool(self):
        with mock.patch.object(lx, "_run", return_value="doc.md - Mousepad\n"):
            self.assertEqual(lx.get_active_window_title(), "Mousepad")
        with mock.patch.object(lx, "_run", return_value=None):
            self.assertEqual(lx.get_active_window_title(), "")


QUERY = "rules:      evdev\nmodel:      pc105\nlayout:     us,ir\noptions:    grp:alt_shift_toggle\n"


class LanguageMapping(unittest.TestCase):
    def test_parse_query(self):
        self.assertEqual(lx._layouts_from_query(QUERY), ["us", "ir"])
        self.assertEqual(lx._layouts_from_query("model: pc105\n"), [])

    def test_group_selects_layout(self):
        with mock.patch.object(lx, "_setxkbmap_query", return_value=QUERY):
            with mock.patch.object(lx, "_xkb_group", return_value=0):
                self.assertEqual(lx.active_language(), "en")
            with mock.patch.object(lx, "_xkb_group", return_value=1):
                self.assertEqual(lx.active_language(), "fa")
            with mock.patch.object(lx, "_xkb_group", return_value=5):
                self.assertEqual(lx.active_language(), "en")

    def test_layout_codes(self):
        expect = {"ir": "fa", "us": "en", "gb": "en", "tr": "tr", "ara": "ar", "ru": "ru",
                  "fr": "fr", "de": "de", "es": "es", "it": "it", "us(intl)": "en", "xx": "fa"}
        for layout, code in expect.items():
            with self.subTest(layout=layout):
                self.assertEqual(lx._lang_for_layout(layout), code)

    def test_no_setxkbmap_defaults_to_fa(self):
        with mock.patch.object(lx, "_setxkbmap_query", return_value=""):
            self.assertEqual(lx.active_language(), "fa")


class RemoteClient(unittest.TestCase):
    def test_remote_clients(self):
        for exe in ("xfreerdp", "xfreerdp3", "remmina", "rdesktop"):
            with mock.patch.object(lx, "get_foreground_exe", return_value=exe):
                self.assertTrue(lx.remote_client_in_front(), exe)
        with mock.patch.object(lx, "get_foreground_exe", return_value="mousepad"):
            self.assertFalse(lx.remote_client_in_front())


class OsapiSurface(unittest.TestCase):
    def test_osapi_exports_linux_backend(self):
        names = ["active_language", "type_text", "paste_text", "get_selected_text",
                 "validate_hotkey", "_phys_down", "_MODIFIERS", "get_active_window_title",
                 "remote_client_in_front", "get_foreground_exe", "clipboard_get_text",
                 "clipboard_set_text", "SENTINEL"]
        for n in names:
            with self.subTest(name=n):
                self.assertIs(getattr(osapi, n), getattr(lx, n))


if __name__ == "__main__":
    unittest.main()
