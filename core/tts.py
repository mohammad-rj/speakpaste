import os
import re
import time
import json
import base64
import wave
import urllib.request
import urllib.parse
import urllib.error
import threading
from concurrent.futures import ThreadPoolExecutor
import sounddevice as sd
import soundfile as sf
import numpy as np

from .config import log, TTS_CACHE_DIR, TTS_STYLE_PRESETS
from .stt import gemini_endpoint

# ─── Pitch-safe Time Stretch (WSOLA) ──────────────────────────────────────────

def _time_stretch(x, rate, sr):
    """WSOLA time-stretch: changes tempo without changing pitch."""
    if abs(rate - 1.0) < 0.02 or len(x) < sr // 10:
        return x
    frame   = max(256, int(sr * 0.03))
    hop_out = frame // 2
    hop_in  = max(1, int(round(hop_out * rate)))
    search  = max(1, int(sr * 0.004))
    win     = np.hanning(frame).astype(np.float32)

    n_out = int(len(x) / rate) + 2 * frame
    out   = np.zeros(n_out, dtype=np.float32)
    norm  = np.zeros(n_out, dtype=np.float32)

    # `ideal` advances deterministically by hop_in; the similarity search only
    # picks WHICH nearby frame to copy. Advancing from the searched position
    # instead would let the offset accumulate and skew the output length.
    ideal = write = 0
    tail = None
    while ideal + frame < len(x) and write + frame < n_out:
        pos = ideal
        if tail is not None:
            lo   = max(0, ideal - search)
            hi   = min(len(x) - frame, ideal + search)
            best, best_score = ideal, -1e30
            for cand in range(lo, hi + 1, 8):
                seg = x[cand:cand + len(tail)]
                if len(seg) < len(tail):
                    break
                score = float(np.dot(seg, tail))
                if score > best_score:
                    best_score, best = score, cand
            pos = best
        seg = x[pos:pos + frame]
        if len(seg) < frame:
            break
        out[write:write + frame]  += seg * win
        norm[write:write + frame] += win
        tail   = x[pos + hop_out:pos + frame]
        write += hop_out
        ideal += hop_in

    end = write + frame
    norm[norm < 1e-6] = 1.0
    return (out[:end] / norm[:end]).astype(np.float32)


# ─── TTS Engines ──────────────────────────────────────────────────────────────

def _tts_edge(text, voice):
    """Free Microsoft Edge voices via edge_tts. Returns path to an mp3."""
    import asyncio
    import edge_tts

    out = os.path.join(TTS_CACHE_DIR, f"edge_{abs(hash((text, voice)))}.mp3")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out

    async def _run():
        comm = edge_tts.Communicate(text, voice)
        await comm.save(out)

    loop = asyncio.new_event_loop()
    try:
        loop.run_until_complete(_run())
    finally:
        loop.close()
    return out


def _tts_vertex(text, voice, model, style, lang, cfg):
    """Gemini-TTS. Returns path to a wav."""
    url, headers = gemini_endpoint(model, cfg)
    if not url:
        raise RuntimeError("No Gemini endpoint configured (Settings -> Text -> Speech)")

    out = os.path.join(TTS_CACHE_DIR, f"vx_{abs(hash((text, voice, model, style)))}.wav")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out

    prompt = f"{style}\n\n{text}" if style else text
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}},
                "languageCode": "fa-IR" if lang == "fa" else "en-US",
            },
        },
    }).encode("utf-8")

    resp = None
    for attempt in range(3):
        req = urllib.request.Request(url, data=body, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                resp = json.load(r)
            break
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="replace")[:160].replace("\n", " ")
            if attempt == 2:
                raise RuntimeError(f"TTS {e.code}: {detail}") from None
            log(f"TTS {e.code}, retrying ({attempt + 1}/2)")
            time.sleep(1.0 + attempt)

    part = resp["candidates"][0]["content"]["parts"][0]["inlineData"]
    pcm  = base64.b64decode(part["data"])
    m    = re.search(r"rate=(\d+)", part.get("mimeType") or "")
    rate = int(m.group(1)) if m else 24000
    with wave.open(out, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(pcm)
    return out


def synthesize_tts(text, cfg, active_lang="fa"):
    engine     = cfg.get("tts_engine", "vertex")
    preset_key = cfg.get("tts_vertex_preset", "charon_calm")
    presets    = cfg.get("tts_presets") or TTS_STYLE_PRESETS
    preset     = presets.get(preset_key, {}) if preset_key != "custom" else {}

    voice   = cfg.get("tts_vertex_voice") or preset.get("voice", "Charon")
    model   = cfg.get("tts_vertex_model", "gemini-3.1-flash-tts-preview")
    style   = cfg.get("tts_style") if (cfg.get("tts_style") is not None and preset_key == "custom") else (preset.get("style") or cfg.get("tts_style", ""))
    ed_vox  = cfg.get("tts_edge_voice", "fa-IR-FaridNeural")

    if engine == "vertex":
        try:
            path = _tts_vertex(text, voice, model, style, active_lang, cfg)
        except Exception as e:
            log(f"Gemini TTS failed, falling back to edge: {str(e)[:80]}")
            path = _tts_edge(text, ed_vox)
    else:
        path = _tts_edge(text, ed_vox)

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return data.mean(axis=1), sr, path


# ─── Chunking for Streamed Playback ───────────────────────────────────────────

_RATE_FA, _RATE_EN = 14.0, 20.0


def _chars_per_sec(text):
    head = text[:400]
    arabic = sum(1 for c in head if '؀' <= c <= 'ۿ')
    return _RATE_FA if arabic > len(head) * 0.15 else _RATE_EN


def _split_long(sent, target):
    """Cut an over-long sentence at the last comma (else space) before target."""
    window = sent[:int(target * 1.2)]
    for seps in ("،,؛;", " "):
        idx = max((window.rfind(c) for c in seps), default=-1)
        if idx > target * 0.4:
            return sent[:idx + 1], sent[idx + 1:].lstrip()
    return sent[:target], sent[target:].lstrip()


def _split_for_streaming(text, first_secs=25.0, chunk_secs=50.0):
    """Sentence-aligned chunks sized by speaking TIME rather than characters."""
    rate = _chars_per_sec(text)
    first_target, target = max(40, int(first_secs * rate)), max(80, int(chunk_secs * rate))

    sentences = [s for s in re.split(r"(?<=[.!?؟۔:;؛\n])\s+", text)
                 if s.strip()]

    parts, cur, limit = [], "", first_target
    for sent in sentences:
        while len(sent) > target * 1.6:
            head, sent = _split_long(sent, target)
            cur = (cur + " " + head).strip()
            parts.append(cur)
            cur, limit = "", target
        if cur and len(cur) + len(sent) + 1 > limit:
            parts.append(cur)
            cur, limit = sent, target
        else:
            cur = (cur + " " + sent).strip()
        if len(cur) >= limit:
            parts.append(cur)
            cur, limit = "", target
    if cur.strip():
        parts.append(cur.strip())
    return parts or [text]


# ─── Single-Clip Stream Player ────────────────────────────────────────────────

class TtsPlayer:
    def __init__(self):
        self._lock   = threading.Lock()
        self._base   = None
        self._parts  = []
        self._bounds = []
        self._audio  = None
        self._sr     = 24000
        self._pos    = 0
        self._speed  = 1.0
        self._stream = None
        self.playing = False
        self.ended   = False
        self.ended_at = None
        self.complete = True
        self.buffering = False
        self.cancelled = False

    def _callback(self, outdata, frames, time_info, status):
        with self._lock:
            if self._audio is None or not self.playing:
                outdata[:] = 0
                return
            chunk = self._audio[self._pos:self._pos + frames]
            n = len(chunk)
            outdata[:n, 0] = chunk
            if n < frames:
                outdata[n:] = 0
                self._pos = len(self._audio)
                if self.complete:
                    self.playing = False
                    self.ended = True
                    self.ended_at = time.time()
                else:
                    self.buffering = True
            else:
                self._pos += frames
                self.buffering = False

    def _ensure_stream(self):
        if self._stream is not None:
            return
        self._stream = sd.OutputStream(
            samplerate=self._sr,
            channels=1,
            dtype="float32",
            callback=self._callback,
            blocksize=1024,
        )
        self._stream.start()

    def _rebuild(self, keep_fraction=None):
        stretched = [_time_stretch(p, self._speed, self._sr) for p in self._parts]
        self._audio = (np.concatenate(stretched) if stretched else np.zeros(0, dtype=np.float32))
        self._bounds, off = [], 0
        for s in stretched:
            self._bounds.append(off)
            off += len(s)
        if keep_fraction is not None and len(self._audio):
            self._pos = min(len(self._audio) - 1, max(0, int(keep_fraction * len(self._audio))))

    def load(self, samples, sr, complete=True):
        with self._lock:
            was = self._stream
            self._stream = None
        if was is not None:
            try:
                was.stop(); was.close()
            except Exception:
                pass
        with self._lock:
            self._parts = [samples]
            self._base = samples
            self._sr   = sr
            self._pos  = 0
            self.ended = False
            self.ended_at = None
            self.complete = complete
            self.buffering = False
            self.cancelled = False
            self._rebuild()
        self._ensure_stream()
        with self._lock:
            self.playing = True

    def append(self, samples):
        with self._lock:
            if self._audio is None:
                return
            self._parts.append(samples)
            self._base = np.concatenate([self._base, samples])
            self._bounds.append(len(self._audio))
            self._audio = np.concatenate([self._audio, _time_stretch(samples, self._speed, self._sr)])
            self.buffering = False
            if self.ended:
                self.ended = False
                self.ended_at = None
                self.playing = True

    def finish(self):
        with self._lock:
            self.complete = True
            if self._audio is not None and self._pos >= len(self._audio):
                self.playing = False
                self.ended = True
                self.ended_at = time.time()

    def toggle(self):
        with self._lock:
            if self._audio is None:
                return
            if self.ended:
                self._pos = 0
                self.ended = False
                self.ended_at = None
            self.playing = not self.playing

    def stop(self):
        with self._lock:
            self.playing = False
            self._pos = 0
            if not self.complete:
                self.cancelled = True

    def close(self):
        with self._lock:
            self.playing = False
            st, self._stream = self._stream, None
        if st is not None:
            try:
                st.stop(); st.close()
            except Exception:
                pass

    def seek(self, fraction):
        with self._lock:
            if self._audio is None:
                return
            self._pos = min(len(self._audio) - 1, max(0, int(fraction * len(self._audio))))
            if self.ended and self._pos < len(self._audio) - 1:
                self.ended = False
                self.ended_at = None

    def set_speed(self, speed):
        with self._lock:
            if self._base is None or abs(speed - self._speed) < 0.01:
                self._speed = speed
                return
            have = self._audio is not None and len(self._audio) > 0
            frac = (self._pos / len(self._audio)) if have else 0.0
            self._speed = speed
            self._rebuild(keep_fraction=frac)

    def position(self):
        with self._lock:
            if self._audio is None or not len(self._audio):
                return 0.0, 0.0
            return self._pos / self._sr, len(self._audio) / self._sr

    def chunk_state(self):
        with self._lock:
            if not self._bounds or self._audio is None or not len(self._audio):
                return 0, 0, []
            total = len(self._bounds)
            idx = 0
            for i, b in enumerate(self._bounds):
                if self._pos >= b:
                    idx = i
            return idx + 1, total, [b / len(self._audio) for b in self._bounds]

    def jump_chunk(self, delta):
        with self._lock:
            if not self._bounds or self._audio is None:
                return
            idx = 0
            for i, b in enumerate(self._bounds):
                if self._pos >= b:
                    idx = i
            if delta < 0 and (self._pos - self._bounds[idx]) > 3 * self._sr:
                target = idx
            else:
                target = max(0, min(len(self._bounds) - 1, idx + delta))
            self._pos = self._bounds[target]
            if self.ended:
                self.ended = False
                self.ended_at = None
                self.playing = True

    def remaining_seconds(self):
        with self._lock:
            if self._audio is None:
                return 0.0
            return max(0, len(self._audio) - self._pos) / self._sr


_player_instance = None

def get_player():
    global _player_instance
    if _player_instance is None:
        _player_instance = TtsPlayer()
    return _player_instance
