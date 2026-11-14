"""The input stream heals itself after its device dies.

Over RDP every disconnect/reconnect replaces the "Remote Audio" endpoint and the
old InputStream goes silent; before this, SpeakPaste then recorded nothing until
it was restarted by hand. sounddevice is replaced with a fake, so no audio
device is needed.

Run:  python -m unittest discover -s tests -v
"""
import os
import sys
import time
import unittest
from unittest import mock

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core.audio as audio  # noqa: E402


class FakeStream:
    instances = []
    fail_open = False

    def __init__(self, samplerate, channels, callback):
        if FakeStream.fail_open:
            raise RuntimeError("Error querying device -1")
        self.callback = callback
        self.started = False
        self.closed = False
        FakeStream.instances.append(self)

    def start(self):
        self.started = True

    def stop(self):
        self.started = False

    def close(self):
        self.closed = True

    def feed(self, seconds):
        n = int(audio.SAMPLE_RATE * seconds)
        self.callback(np.zeros((n, 1), dtype=np.float32), n, None, None)


class TestAudioRecovery(unittest.TestCase):
    def setUp(self):
        FakeStream.instances = []
        FakeStream.fail_open = False
        self.sd = mock.patch.object(audio.sd, "InputStream", FakeStream).start()
        self.term = mock.patch.object(audio.sd, "_terminate", create=True).start()
        self.init = mock.patch.object(audio.sd, "_initialize", create=True).start()
        mock.patch.object(audio, "log", lambda *_: None).start()
        self.addCleanup(mock.patch.stopall)
        self.rec = audio.AudioRecorder()
        self.rec._open_stream()  # init_stream without the watchdog thread

    def later(self, secs):
        return time.monotonic() + secs

    def test_live_stream_is_left_alone(self):
        FakeStream.instances[0].feed(0.1)
        self.rec.check_health()
        self.assertEqual(len(FakeStream.instances), 1)

    def test_silent_stream_is_reopened(self):
        first = FakeStream.instances[0]
        self.rec.check_health(now=self.later(audio.STALE_SEC + 1))
        self.assertEqual(len(FakeStream.instances), 2)
        self.assertTrue(first.closed)
        self.assertTrue(FakeStream.instances[1].started)
        self.assertIs(self.rec.audio_stream, FakeStream.instances[1])

    def test_repeated_dead_reopens_escalate_to_portaudio_reload(self):
        for i in range(audio.REINIT_AFTER + 1):
            self.rec._next_retry = 0.0
            self.rec.check_health(now=self.later(audio.STALE_SEC + 1 + i * 10))
        self.term.assert_called()
        self.init.assert_called()

    def test_reload_is_skipped_while_tts_plays(self):
        self.rec.can_reinit = lambda: False
        for i in range(audio.REINIT_AFTER + 2):
            self.rec._next_retry = 0.0
            self.rec.check_health(now=self.later(audio.STALE_SEC + 1 + i * 10))
        self.term.assert_not_called()

    def test_missing_device_backs_off_then_recovers(self):
        FakeStream.fail_open = True
        self.rec._heal("test")
        self.assertIsNone(self.rec.audio_stream)
        self.assertGreater(self.rec._next_retry, time.monotonic())
        n = len(FakeStream.instances)
        self.rec.check_health()  # inside backoff: no attempt
        self.assertEqual(len(FakeStream.instances), n)
        FakeStream.fail_open = False
        self.rec.check_health(now=self.rec._next_retry + 0.1)
        self.assertIsNotNone(self.rec.audio_stream)
        self.rec.audio_stream.feed(0.1)
        self.rec._next_retry = 0.0
        self.rec.check_health()
        self.assertEqual(self.rec._failed_reopens, 0)

    def test_record_start_on_dead_stream_reopens_first(self):
        self.rec._last_cb = time.monotonic() - audio.STALE_SEC
        self.rec.start_recording()
        self.assertEqual(len(FakeStream.instances), 2)
        self.rec.audio_stream.feed(0.5)
        self.assertEqual(self.rec._rec_frames, audio.SAMPLE_RATE // 2)

    def test_starved_recording_forces_reopen(self):
        self.rec.mic_mode = "on_demand"  # keeps check_health off a thread here
        self.rec.start_recording()
        self.rec._rec_started -= 3.0  # 3s pressed, 0.2s delivered
        self.rec.audio_stream.feed(0.2)
        path = self.rec.stop_recording()
        self.assertEqual(self.rec._last_cb, 0.0)
        self.assertEqual(self.rec._next_retry, 0.0)
        if path:
            os.unlink(path)

    def test_on_demand_idle_is_not_healed(self):
        self.rec.mic_mode = "on_demand"
        self.rec.check_health(now=self.later(100))
        self.assertEqual(len(FakeStream.instances), 1)


class TestXrdpMic(unittest.TestCase):
    """Linux/xrdp: the always-open stream is restarted, never rebuilt, when
    its queue has had time to drift or its channel only delivers zeros."""

    def setUp(self):
        FakeStream.instances = []
        mock.patch.object(audio.sd, "InputStream", FakeStream).start()
        mock.patch.object(audio, "log", lambda *_: None).start()
        mock.patch.object(audio, "XRDP_FIXES", True).start()
        self.sleep = mock.patch.object(audio.time, "sleep").start()
        self.addCleanup(mock.patch.stopall)
        self.rec = audio.AudioRecorder()
        self.rec._open_stream()
        self.stream = FakeStream.instances[0]

    def voice(self, seconds):
        n = int(audio.SAMPLE_RATE * seconds)
        self.stream.callback(np.full((n, 1), 0.01, dtype=np.float32), n, None, None)

    def restarts(self):
        return [c for c in self.sleep.call_args_list if c.args == (audio.REFRESH_GAP_SEC,)]

    def test_zero_audio_restarts_the_same_stream(self):
        self.stream.feed(audio.DEAD_ZERO_SEC)
        self.rec.check_health()
        self.assertEqual(len(self.restarts()), 1)
        self.assertEqual(len(FakeStream.instances), 1)
        self.assertTrue(self.stream.started)

    def test_dead_restart_is_rate_limited(self):
        self.stream.feed(audio.DEAD_ZERO_SEC)
        self.rec.check_health()
        self.stream.feed(audio.DEAD_ZERO_SEC)
        self.rec.check_health()
        self.assertEqual(len(self.restarts()), 1)

    def test_old_idle_stream_gets_its_queue_reset(self):
        self.voice(0.1)
        self.rec._started_at -= audio.REFRESH_AGE_SEC
        self.rec.check_health()
        self.assertEqual(len(self.restarts()), 1)
        self.assertEqual(len(FakeStream.instances), 1)

    def test_young_stream_with_voice_is_left_alone(self):
        self.voice(0.1)
        self.rec.check_health()
        self.assertEqual(self.restarts(), [])

    def test_no_reset_while_recording_or_right_after(self):
        self.voice(0.1)
        self.rec._started_at -= audio.REFRESH_AGE_SEC
        self.rec.is_recording = True
        self.rec.check_health()
        self.rec.is_recording = False
        self.rec._last_rec_end = time.monotonic()
        self.rec.check_health()
        self.assertEqual(self.restarts(), [])

    def test_on_demand_is_left_alone(self):
        self.rec.mic_mode = "on_demand"
        self.stream.feed(audio.DEAD_ZERO_SEC)
        self.rec._started_at -= audio.REFRESH_AGE_SEC
        self.rec.check_health()
        self.assertEqual(self.restarts(), [])

    def test_dead_channel_at_record_start_is_restarted_first(self):
        self.stream.feed(audio.DEAD_ZERO_SEC / 2)
        self.rec.start_recording()
        self.assertEqual(len(self.restarts()), 1)
        self.assertTrue(self.rec.is_recording)

    def test_key_release_keeps_recording_for_the_tail(self):
        self.rec.start_recording()
        self.voice(0.5)
        path = self.rec.stop_recording()
        self.sleep.assert_any_call(audio.TAIL_SEC)
        if path:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
