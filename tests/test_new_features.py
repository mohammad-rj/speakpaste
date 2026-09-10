"""Unit tests for SpeakPaste's new MCP server, voice clip, and presets."""

import os
import sys
import json
import time
import asyncio
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.config as cfg
import core.win32 as w32
import core.stt as stt
import speakpaste_mcp as mcp_mod


class TestStylePresets(unittest.TestCase):
    def test_presets_exist(self):
        expected = ["charon_calm", "fenrir_pragmatic", "aoede_neutral", "puck_quick", "custom"]
        for p in expected:
            self.assertIn(p, cfg.TTS_STYLE_PRESETS)
            self.assertIn("voice", cfg.TTS_STYLE_PRESETS[p])
            self.assertIn("style", cfg.TTS_STYLE_PRESETS[p])

    def test_default_preset_is_charon(self):
        self.assertEqual(cfg._DEFAULTS["tts_vertex_preset"], "charon_calm")
        self.assertEqual(cfg._DEFAULTS["tts_vertex_voice"], "Charon")




class TestMcpServer(unittest.TestCase):
    def test_create_mcp_app(self):
        server = mcp_mod.create_mcp_app()
        self.assertIsNotNone(server)

    def test_mcp_speak_when_app_offline(self):
        # When SpeakPaste is not running on port 9998, should return friendly error
        res = asyncio.run(mcp_mod._send_speak_to_speakpaste("سلام", port=9998))
        self.assertIn("SpeakPaste desktop app is not connected", res)

    def test_mcp_speak_e2e_with_ws_server(self):
        import threading
        import core.websocket_server as ws_mod
        test_port = 9188
        received = []

        def on_speak(text, session_title="AI"):
            received.append((text, session_title))

        t = threading.Thread(target=ws_mod.start_ws_server, args=(test_port, on_speak), daemon=True)
        t.start()
        time.sleep(0.5)

        res = asyncio.run(mcp_mod._send_speak_to_speakpaste("سلام تست ام سی پی", session_title="EP_Project", port=test_port))
        self.assertIn("Voice output queued for [EP_Project]", res)
        time.sleep(0.3)
        self.assertEqual(received, [("سلام تست ام سی پی", "EP_Project")])


if __name__ == "__main__":
    unittest.main(verbosity=2)

