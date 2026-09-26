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
import core.win32 as w32  # noqa: E402


class TestRemoteClientDetection(unittest.TestCase):
    def test_rdp_clients_are_recognised(self):
        for exe in ("mstsc.exe", "msrdc.exe", "vmconnect.exe"):
            with mock.patch.object(w32, "get_foreground_exe", return_value=exe):
                self.assertTrue(w32.remote_client_in_front(), exe)

    def test_ordinary_apps_are_not(self):
        for exe in ("code.exe", "firefox.exe", ""):
            with mock.patch.object(w32, "get_foreground_exe", return_value=exe):
                self.assertFalse(w32.remote_client_in_front(), exe)

    def test_foreground_exe_never_raises(self):
        name = w32.get_foreground_exe()
        self.assertIsInstance(name, str)
        self.assertTrue(name == "" or name.endswith(".exe"), name)
        self.assertEqual(name, name.lower())


if __name__ == "__main__":
    unittest.main()
