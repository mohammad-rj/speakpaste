"""Unit tests for SpeakPaste's pure logic.

Deliberately covers the parts that have actually broken during development -
WSOLA output length, chunk boundaries, hotkey validity, settings migration -
rather than aiming for a coverage percentage. No audio device, no network and
no API key is required, so this runs unchanged on a clean CI runner.

Run:  python -m unittest discover -s tests -v
"""
import os
import sys
import unittest

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import speakpaste as sp  # noqa: E402


class TestHotkeyValidation(unittest.TestCase):
    """A hotkey Windows reserves silently never fires - that shipped once."""

    def test_accepts_normal_combos(self):
        for combo in ("win+alt", "win+shift", "ctrl+alt+r", "ctrl+win+alt"):
            ok, msg = sp.validate_hotkey(combo)
            self.assertTrue(ok, f"{combo} should be valid, got: {msg}")

    def test_rejects_empty_and_modifierless(self):
        for combo in ("", "   ", "x", "space"):
            ok, _ = sp.validate_hotkey(combo)
            self.assertFalse(ok, f"{combo!r} should be rejected")

    def test_rejects_unknown_key_name(self):
        ok, msg = sp.validate_hotkey("ctrl+nope")
        self.assertFalse(ok)
        self.assertIn("nope", msg)

    def test_rejects_windows_reserved(self):
        for combo in ("win+l", "win+x", "alt+tab", "win+e"):
            ok, _ = sp.validate_hotkey(combo)
            self.assertFalse(ok, f"{combo} is reserved by Windows")

    def test_rejects_reserved_win_letter_even_with_extra_modifiers(self):
        # The real bug: win+alt+x looked fine and never fired, because the
        # shell takes Win+X before any application hook runs.
        ok, msg = sp.validate_hotkey("win+alt+x")
        self.assertFalse(ok)
        self.assertIn("Win+x", msg)

    def test_is_case_and_space_insensitive(self):
        ok, key = sp.validate_hotkey("  WIN + Shift ")
        self.assertTrue(ok)
        self.assertEqual(key, "win+shift")


class TestSpeechRateEstimate(unittest.TestCase):
    def test_persian_is_slower_than_english(self):
        fa = sp._chars_per_sec("سلام حال شما چطور است امروز هوا خیلی خوب است")
        en = sp._chars_per_sec("hello how are you today the weather is nice")
        self.assertLess(fa, en)

    def test_mixed_text_counts_as_persian(self):
        rate = sp._chars_per_sec("این تابع رو با یه loop ساده refactor کن")
        self.assertEqual(rate, sp._RATE_FA)


class TestChunkSplitting(unittest.TestCase):
    def setUp(self):
        self.sentence = "این یک جمله نمونه برای تست تقسیم متن است. "

    def test_short_text_is_one_chunk(self):
        parts = sp._split_for_streaming("سلام دنیا.", 8, 25)
        self.assertEqual(len(parts), 1)

    def test_first_chunk_is_shorter_than_later_ones(self):
        # Audio must start quickly, so the first piece is deliberately small.
        parts = sp._split_for_streaming(self.sentence * 20, 8, 25)
        self.assertGreater(len(parts), 2)
        self.assertLess(len(parts[0]), len(parts[1]))

    def test_no_text_is_lost(self):
        text = (self.sentence * 12).strip()
        joined = " ".join(sp._split_for_streaming(text, 8, 25))
        self.assertEqual(joined.replace(" ", ""), text.replace(" ", ""))

    def test_splits_on_sentence_boundaries(self):
        parts = sp._split_for_streaming(self.sentence * 20, 8, 25)
        for p in parts[:-1]:
            self.assertTrue(p.rstrip().endswith((".", "!", "?", "؟", ":", "؛", "،")),
                            f"chunk should end at a boundary: ...{p[-25:]!r}")

    def test_sentence_longer_than_budget_is_still_split(self):
        giant = "کلمه " * 400 + "."
        parts = sp._split_for_streaming(giant, 8, 25)
        self.assertGreater(len(parts), 1)

    def test_never_returns_empty(self):
        for text in ("", "   ", "."):
            self.assertTrue(sp._split_for_streaming(text, 8, 25))


class TestTimeStretch(unittest.TestCase):
    """Speed must change duration accurately; an earlier version drifted 7%."""

    def setUp(self):
        self.sr = 24000
        t = np.linspace(0, 4, 4 * self.sr, endpoint=False, dtype=np.float32)
        # A tone with an envelope: pure silence gives WSOLA nothing to align on.
        self.audio = (np.sin(2 * np.pi * 220 * t) *
                      (0.5 + 0.5 * np.sin(2 * np.pi * 2 * t))).astype(np.float32)

    def test_identity_returns_input_untouched(self):
        out = sp._time_stretch(self.audio, 1.0, self.sr)
        self.assertEqual(len(out), len(self.audio))

    def test_faster_is_shorter_and_slower_is_longer(self):
        self.assertLess(len(sp._time_stretch(self.audio, 1.5, self.sr)), len(self.audio))
        self.assertGreater(len(sp._time_stretch(self.audio, 0.75, self.sr)), len(self.audio))

    def test_duration_is_accurate_within_3_percent(self):
        for rate in (0.5, 0.75, 1.25, 1.5, 2.0):
            out = sp._time_stretch(self.audio, rate, self.sr)
            expected = len(self.audio) / rate
            error = abs(len(out) - expected) / expected
            self.assertLess(error, 0.03,
                            f"{rate}x drifted {error:.1%}: {len(out)} vs {expected:.0f}")

    def test_output_stays_finite_and_bounded(self):
        out = sp._time_stretch(self.audio, 1.4, self.sr)
        self.assertTrue(np.all(np.isfinite(out)))
        self.assertLess(np.max(np.abs(out)), 1.5)


class TestPlayerChunkNavigation(unittest.TestCase):
    """Jumping between parts of a long read, without touching an audio device."""

    def setUp(self):
        self.sr = 24000
        self.p = sp.TtsPlayer()
        # Bypass the sound device: fill the buffers directly.
        self.p._sr = self.sr
        self.p._parts = [np.zeros(n * self.sr, dtype=np.float32) for n in (10, 20, 15)]
        self.p._base = np.concatenate(self.p._parts)
        self.p._speed = 1.0
        self.p._rebuild()
        self.p.complete = True

    def test_reports_all_chunks(self):
        idx, total, marks = self.p.chunk_state()
        self.assertEqual(total, 3)
        self.assertEqual(idx, 1)
        self.assertAlmostEqual(marks[1], 10 / 45, places=2)

    def test_next_and_previous_move_one_part(self):
        self.p.jump_chunk(+1)
        self.assertEqual(self.p.chunk_state()[0], 2)
        self.p.jump_chunk(+1)
        self.assertEqual(self.p.chunk_state()[0], 3)
        self.p.jump_chunk(-1)
        self.assertEqual(self.p.chunk_state()[0], 2)

    def test_clamps_at_both_ends(self):
        for _ in range(5):
            self.p.jump_chunk(-1)
        self.assertEqual(self.p.chunk_state()[0], 1)
        for _ in range(9):
            self.p.jump_chunk(+1)
        self.assertEqual(self.p.chunk_state()[0], 3)

    def test_back_restarts_current_part_when_deep_into_it(self):
        self.p._pos = int(18 * self.sr)          # 8s into part 2
        self.p.jump_chunk(-1)
        self.assertEqual(self.p._pos, int(10 * self.sr))

    def test_back_leaves_part_when_near_its_start(self):
        self.p._pos = int(12 * self.sr)          # 2s into part 2
        self.p.jump_chunk(-1)
        self.assertEqual(self.p._pos, 0)

    def test_boundaries_survive_a_speed_change(self):
        self.p.set_speed(1.5)
        _, total, marks = self.p.chunk_state()
        self.assertEqual(total, 3)
        self.assertAlmostEqual(marks[1], 10 / 45, places=2)
        self.assertAlmostEqual(self.p.position()[1], 30, delta=1.5)

    def test_appending_extends_without_disturbing_position(self):
        self.p._pos = int(5 * self.sr)
        self.p.append(np.zeros(5 * self.sr, dtype=np.float32))
        self.assertEqual(self.p.chunk_state()[1], 4)
        self.assertEqual(self.p._pos, int(5 * self.sr))


class TestSettingsMigration(unittest.TestCase):
    """Old configs must keep working; users upgrade in place."""

    def test_every_default_key_is_present(self):
        for key in ("stt_engine", "prompt_mode", "hotkey", "tts_hotkey",
                    "tts_engine", "inject_mode", "notify_errors"):
            self.assertIn(key, sp._DEFAULTS)

    def test_defaults_carry_no_credentials(self):
        for key, value in sp._DEFAULTS.items():
            if any(s in key for s in ("api_key", "cred", "project")):
                self.assertEqual(value, "", f"{key} must default to empty")

    def test_tts_hotkey_default_is_valid_and_distinct_from_stt(self):
        ok, _ = sp.validate_hotkey(sp._DEFAULTS["tts_hotkey"])
        self.assertTrue(ok)
        self.assertNotEqual(sp._DEFAULTS["tts_hotkey"], sp._DEFAULTS["hotkey"])

    def test_stt_hotkey_default_is_valid(self):
        ok, _ = sp.validate_hotkey(sp._DEFAULTS["hotkey"])
        self.assertTrue(ok)


class TestRtlDetection(unittest.TestCase):
    def test_persian_is_rtl(self):
        self.assertTrue(sp._is_rtl("سلام دنیا"))

    def test_english_is_not_rtl(self):
        self.assertFalse(sp._is_rtl("hello world"))

    def test_empty_is_not_rtl(self):
        self.assertFalse(sp._is_rtl(""))


class TestGeminiEndpointRouting(unittest.TestCase):
    """Vertex must win whenever a credential exists (billing policy)."""

    def setUp(self):
        self._cred, self._key = sp.TTS_VERTEX_CRED, sp.GEMINI_API_KEY

    def tearDown(self):
        sp.TTS_VERTEX_CRED, sp.GEMINI_API_KEY = self._cred, self._key

    def test_api_key_used_when_no_credential(self):
        sp.TTS_VERTEX_CRED = ""
        sp.GEMINI_API_KEY = "AIzaTESTKEY"
        url, _ = sp._gemini_endpoint("some-model")
        self.assertIn("generativelanguage.googleapis.com", url)

    def test_nothing_configured_returns_none(self):
        sp.TTS_VERTEX_CRED = ""
        sp.GEMINI_API_KEY = ""
        url, headers = sp._gemini_endpoint("some-model")
        self.assertIsNone(url)
        self.assertIsNone(headers)


if __name__ == "__main__":
    unittest.main(verbosity=2)
