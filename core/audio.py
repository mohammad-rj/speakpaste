import sounddevice as sd
import numpy as np
import soundfile as sf
import tempfile
import threading
from collections import deque
from queue import Queue
from .config import log

SAMPLE_RATE = 16000

class AudioRecorder:
    def __init__(self):
        self.is_recording = False
        self.audio_queue = Queue()
        self._pre_roll_buf = deque()
        self._pre_roll_maxframes = int(SAMPLE_RATE * 0.5)  # 500ms pre-roll
        self.audio_stream = None
        self.mic_mode = "always"
        self._lock = threading.Lock()
        self.on_state_change = None  # callback(state_str)

    def _audio_callback(self, indata, frames, time_info, status):
        chunk = indata.copy()
        if self.is_recording:
            self.audio_queue.put(chunk)
        else:
            self._pre_roll_buf.append(chunk)
            total = sum(c.shape[0] for c in self._pre_roll_buf)
            while total > self._pre_roll_maxframes and self._pre_roll_buf:
                total -= self._pre_roll_buf.popleft().shape[0]

    def init_stream(self, mic_mode="always"):
        self.mic_mode = mic_mode
        if self.audio_stream is not None:
            try:
                self.audio_stream.stop()
                self.audio_stream.close()
            except Exception:
                pass
        self.audio_stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            callback=self._audio_callback,
        )
        if self.mic_mode != "on_demand":
            self.audio_stream.start()

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
                    self.audio_stream.start()
                except Exception:
                    pass

    def start_recording(self):
        with self._lock:
            if self.is_recording:
                return
            if self.mic_mode == "on_demand" and self.audio_stream:
                self.audio_stream.start()
            self.is_recording = True
            self.audio_queue = Queue()
            for chunk in list(self._pre_roll_buf):
                self.audio_queue.put(chunk)
            self._pre_roll_buf.clear()
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
            if self.mic_mode == "on_demand" and self.audio_stream:
                try:
                    self.audio_stream.stop()
                except Exception:
                    pass

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

