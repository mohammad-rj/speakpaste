import sounddevice as sd
import numpy as np
import soundfile as sf
import tempfile
import threading
import time
from collections import deque
from queue import Queue
from .config import log

SAMPLE_RATE = 16000

# Self-healing input stream. An RDP disconnect/reconnect (and any USB mic
# unplug, or a default-device switch) leaves the open InputStream on a dead
# endpoint: callbacks stop, or arrive for a fraction of real time, and every
# recording after that is partial or "No audio captured". A live stream calls
# back every few ms, so a callback gap longer than STALE_SEC means the device
# is gone and the stream gets reopened on the current default device.
STALE_SEC        = 2.5   # callback silence that marks a running stream dead
WATCHDOG_SEC     = 2.0   # health-check period
MIN_DELIVERY     = 0.5   # captured/elapsed ratio below which a recording was starved
REINIT_AFTER     = 2     # plain reopens in a row before PortAudio itself is reloaded
MAX_BACKOFF_SEC  = 30.0  # retry ceiling while no input device exists at all


class AudioRecorder:
    def __init__(self):
        self.is_recording = False
        self.audio_queue = Queue()
        self._pre_roll_buf = deque()
        self._pre_roll_maxframes = int(SAMPLE_RATE * 0.5)  # 500ms pre-roll
        self.audio_stream = None
        self.mic_mode = "always"
        self._lock = threading.Lock()
        self._stream_lock = threading.RLock()
        self.on_state_change = None  # callback(state_str)
        # Reloading PortAudio closes EVERY stream, the TTS player's too; the
        # app sets this to report whether that is safe right now.
        self.can_reinit = lambda: True
        self._last_cb = time.monotonic()
        self._rec_started = 0.0
        self._rec_frames = 0
        self._failed_reopens = 0
        self._next_retry = 0.0
        self._watchdog = None

    def _audio_callback(self, indata, frames, time_info, status):
        self._last_cb = time.monotonic()
        chunk = indata.copy()
        if self.is_recording:
            self._rec_frames += frames
            self.audio_queue.put(chunk)
        else:
            self._pre_roll_buf.append(chunk)
            total = sum(c.shape[0] for c in self._pre_roll_buf)
            while total > self._pre_roll_maxframes and self._pre_roll_buf:
                total -= self._pre_roll_buf.popleft().shape[0]

    def _should_run(self):
        return self.mic_mode != "on_demand" or self.is_recording

    def _open_stream(self, reinit=False):
        """Close the current stream and open a new one on the current default
        input device. Returns True when a stream is open (and running if it
        should be). Never raises: a missing device is a normal state over RDP."""
        with self._stream_lock:
            old, self.audio_stream = self.audio_stream, None
            if old is not None:
                try:
                    old.stop()
                    old.close()
                except Exception:
                    pass
            if reinit:
                # PortAudio snapshots the device list at load; a device that
                # appeared after that (a new RDP "Remote Audio") is invisible
                # until it is reloaded.
                try:
                    sd._terminate()
                    sd._initialize()
                except Exception as e:
                    log(f"Audio: PortAudio reload failed: {e}")
            try:
                stream = sd.InputStream(
                    samplerate=SAMPLE_RATE,
                    channels=1,
                    callback=self._audio_callback,
                )
                self._last_cb = time.monotonic()
                if self._should_run():
                    stream.start()
            except Exception as e:
                self.audio_stream = None
                return self._note_failure(f"cannot open input device: {e}")
            self.audio_stream = stream
            return True

    def _note_failure(self, why):
        self._failed_reopens += 1
        backoff = min(MAX_BACKOFF_SEC, WATCHDOG_SEC * (2 ** min(self._failed_reopens, 5)))
        self._next_retry = time.monotonic() + backoff
        if self._failed_reopens <= 3 or self._failed_reopens % 10 == 0:
            log(f"Audio: {why} (attempt {self._failed_reopens}, retry in {backoff:.0f}s)")
        return False

    def _heal(self, reason):
        """Reopen a dead stream; escalate to a PortAudio reload when plain
        reopens keep landing on a dead device."""
        reinit = self._failed_reopens >= REINIT_AFTER and self.can_reinit()
        log(f"Audio: input stream {reason} - reopening{' with PortAudio reload' if reinit else ''}")
        ok = self._open_stream(reinit=reinit)
        if ok:
            # Success is only proven by callbacks; the next check decides.
            self._failed_reopens += 1
            self._next_retry = time.monotonic() + STALE_SEC
        return ok

    def check_health(self, now=None):
        """One watchdog tick. Public so tests can drive it without a thread."""
        now = time.monotonic() if now is None else now
        with self._stream_lock:
            if not self._should_run():
                return
            if now < self._next_retry:
                return
            if self.audio_stream is None:
                self._heal("missing")
                return
            gap = now - self._last_cb
            if self._last_cb == 0.0:
                self._heal("starved during the last recording")
            elif gap > STALE_SEC:
                self._heal(f"silent for {gap:.1f}s")
            elif self._failed_reopens:
                log("Audio: input stream healthy again")
                self._failed_reopens = 0

    def _watchdog_loop(self):
        while True:
            time.sleep(WATCHDOG_SEC)
            try:
                self.check_health()
            except Exception as e:
                log(f"Audio watchdog error: {e}")

    def init_stream(self, mic_mode="always"):
        self.mic_mode = mic_mode
        self._open_stream()
        if self._watchdog is None:
            self._watchdog = threading.Thread(
                target=self._watchdog_loop, daemon=True, name="AudioWatchdog")
            self._watchdog.start()

    def set_mic_mode(self, mic_mode):
        old = self.mic_mode
        self.mic_mode = mic_mode
        if self.audio_stream and old != mic_mode:
            if mic_mode == "on_demand":
                try:
                    self.audio_stream.stop()
                except Exception:
                    pass
            else:
                try:
                    self._last_cb = time.monotonic()
                    self.audio_stream.start()
                except Exception:
                    pass

    def start_recording(self):
        with self._lock:
            if self.is_recording:
                return
            # A stream that went quiet while idle is dead: reopen it before
            # the user starts talking instead of recording into nothing.
            if self.mic_mode != "on_demand" and (
                    self.audio_stream is None
                    or time.monotonic() - self._last_cb > STALE_SEC / 2):
                self._pre_roll_buf.clear()
                self._heal("dead at record start")
            self.is_recording = True
            if self.mic_mode == "on_demand":
                started = False
                if self.audio_stream is not None:
                    try:
                        self._last_cb = time.monotonic()
                        self.audio_stream.start()
                        started = True
                    except Exception:
                        pass
                if not started:
                    self._heal("failed to start")
            self.audio_queue = Queue()
            self._rec_frames = 0
            for chunk in list(self._pre_roll_buf):
                self.audio_queue.put(chunk)
            self._pre_roll_buf.clear()
            self._rec_started = time.monotonic()
        log("Recording...")
        if self.on_state_change:
            self.on_state_change("recording")

    def cancel_recording(self):
        with self._lock:
            if not self.is_recording:
                return
            self.is_recording = False
            if self.mic_mode == "on_demand" and self.audio_stream:
                try:
                    self.audio_stream.stop()
                except Exception:
                    pass
            self.audio_queue = Queue()
        if self.on_state_change:
            self.on_state_change("idle")

    def stop_recording(self):
        with self._lock:
            if not self.is_recording:
                return None
            self.is_recording = False
            elapsed = time.monotonic() - self._rec_started
            delivered = self._rec_frames / SAMPLE_RATE
            if self.mic_mode == "on_demand" and self.audio_stream:
                try:
                    self.audio_stream.stop()
                except Exception:
                    pass

        # A starved recording means the device died mid-way: force the next
        # watchdog tick to reopen instead of waiting for a full silent gap.
        if elapsed > 1.0 and delivered < MIN_DELIVERY * elapsed:
            log(f"Audio: device delivered {delivered:.1f}s of {elapsed:.1f}s - scheduling reopen")
            self._last_cb = 0.0
            self._next_retry = 0.0
            if self.mic_mode != "on_demand":
                threading.Thread(target=self.check_health, daemon=True).start()

        if self.on_state_change:
            self.on_state_change("waiting")

        chunks = []
        while not self.audio_queue.empty():
            chunks.append(self.audio_queue.get())

        if not chunks:
            log("No audio captured")
            if self.on_state_change:
                self.on_state_change("idle")
            return None

        audio = np.concatenate(chunks, axis=0)
        tmp = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
        sf.write(tmp.name, audio, SAMPLE_RATE)
        tmp.close()
        log(f"Recorded {len(audio) / SAMPLE_RATE:.1f}s")
        return tmp.name
