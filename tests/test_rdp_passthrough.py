"""Hotkeys are left to the remote PC while a Remote Desktop window is in front.

With keyboardhook:i:1 the RDP client forwards Win+Alt to the session, where a
second SpeakPaste records it; the local copy answering too recorded twice and
typed into the RDP window.

Run:  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.osapi as osapi  # noqa: E402

WINDOWS = sys.platform == "win32"
# remote_client_in_front reads get_foreground_exe from its own backend module.
backend = sys.modules[osapi.remote_client_in_front.__module__]


class TestRemoteClientDetection(unittest.TestCase):
    def test_remote_clients_are_recognised(self):
        for exe in sorted(osapi.REMOTE_CLIENT_EXES):
            with mock.patch.object(backend, "get_foreground_exe", return_value=exe):
                self.assertTrue(osapi.remote_client_in_front(), exe)

    @unittest.skipUnless(WINDOWS, "Windows RDP client exe names")
    def test_windows_rdp_clients_are_listed(self):
        for exe in ("mstsc.exe", "msrdc.exe", "vmconnect.exe"):
            self.assertIn(exe, osapi.REMOTE_CLIENT_EXES)

    def test_ordinary_apps_are_not(self):
        for exe in ("code.exe", "firefox.exe", "code", "firefox", ""):
            with mock.patch.object(backend, "get_foreground_exe", return_value=exe):
                self.assertFalse(osapi.remote_client_in_front(), exe)

    def test_foreground_exe_never_raises(self):
        name = osapi.get_foreground_exe()
        self.assertIsInstance(name, str)
        self.assertEqual(name, name.lower())
        if WINDOWS:
            self.assertTrue(name == "" or name.endswith(".exe"), name)


if __name__ == "__main__":
    unittest.main()
