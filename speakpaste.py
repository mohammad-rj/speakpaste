"""
SpeakPaste - Voice to Text
Hold Win+Alt to record, release to transcribe and paste.

Transcription engines (stt_engine):
  google        — Google Speech API (unofficial, free, no key)  [default]
  groq          — Groq Whisper API (requires API key)
  google-ext    — Chrome extension + Offscreen Document (Chrome in background)
  google-cloud  — Google Cloud Speech-to-Text REST API (requires API key)

Prompt modes (prompt_mode):
  off           — paste raw transcript  [default]
  gemini-lite   — transcript → Gemini Flash Lite → English prompt
  gemini-flash  — voice → Gemini Flash directly (bypasses transcription engine)
"""

import keyboard
import requests
import tempfile
import os
import sys
import threading
import datetime

if hasattr(sys.stdout, 'reconfigure'):
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
if hasattr(sys.stderr, 'reconfigure'):
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')
import time
import winreg
import ctypes
import json
import asyncio
import tkinter as tk
from tkinter import ttk, messagebox
from ctypes import wintypes
from queue import Queue, Empty
from collections import deque
import pystray
from PIL import Image, ImageDraw

# Get app directory (works for both script and exe)
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

VERSION       = "1.7.0"
GITHUB_REPO   = "mohammad-rj/speakpaste"
GITHUB_URL    = f"https://github.com/{GITHUB_REPO}"

SETTINGS_FILE = os.path.join(APP_DIR, 'settings.json')
GROQ_API_URL  = "https://api.groq.com/openai/v1/audio/transcriptions"
SAMPLE_RATE   = 16000
CHANNELS      = 1

GEMINI_DEFAULT_SYSTEM_PROMPT = (
    "You are a voice-to-prompt converter inside a coding chatbot. "
    "The user speaks conversationally (in any language) to describe what they want to code. "
    "Your job: output ONLY the English prompt the user would type to an AI coding assistant. "
    "Keep it close to the original intent — do not add features or details the user didn't mention. "
    "If the request is short and simple, keep the output short and simple. "
    "Output nothing else — no explanation, no 'here is your prompt', no quotes, just the prompt.\n\n"
    "Examples:\n"
    "Input: یه تابع بنویس که لیست رو sort کنه\n"
    "Output: Write a function that sorts a list.\n\n"
    "Input: می‌خوام بدونم چرا این کد کار نمی‌کنه\n"
    "Output: Why is this code not working?\n\n"
    "Input: یه کلاس پایتون برای مدیریت کاربر بنویس با login و logout\n"
    "Output: Write a Python class for user management with login and logout methods."
)

_DEFAULTS = {
    "stt_engine":           "google",
    "prompt_mode":          "off",
    "hotkey":               "win+alt",
    "language":             "fa",
    "mic_mode":             "always",
    "groq_api_key":         "",
    "model":                "whisper-large-v3-turbo",
    "google_cloud_api_key": "",
    "ws_port":              9137,
    "check_updates":        True,
    "gemini_api_key":       "",
    "gemini_system_prompt": GEMINI_DEFAULT_SYSTEM_PROMPT,
    "lang_mode":            "fixed",
}

# ─── Settings Load / Save ─────────────────────────────────────────────────────

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            cfg = {**_DEFAULTS, **data}
            # Migrate old engine + gemini_stt_engine fields to stt_engine + prompt_mode
            if "stt_engine" not in data:
                old_engine    = data.get("engine", "google")
                old_gemini_stt = data.get("gemini_stt_engine", "google")
                if old_engine in ("gemini-lite", "gemini-flash"):
                    cfg["stt_engine"]  = old_gemini_stt if old_engine == "gemini-lite" else "google"
                    cfg["prompt_mode"] = old_engine
                else:
                    cfg["stt_engine"]  = old_engine
                    cfg["prompt_mode"] = "off"
            return cfg
        except Exception:
            pass
    # Fallback: read from old .env
    env_path = os.path.join(APP_DIR, '.env')
    cfg = dict(_DEFAULTS)
    if os.path.exists(env_path):
        try:
            from dotenv import dotenv_values
            ev = dotenv_values(env_path)
            old_engine = ev.get("ENGINE", "google")
            if old_engine in ("gemini-lite", "gemini-flash"):
                cfg["stt_engine"]  = "google"
                cfg["prompt_mode"] = old_engine
            else:
                cfg["stt_engine"]  = old_engine
                cfg["prompt_mode"] = "off"
            if ev.get("HOTKEY"):       cfg["hotkey"]       = ev["HOTKEY"]
            if ev.get("LANGUAGE"):     cfg["language"]     = ev["LANGUAGE"]
            if ev.get("MIC_MODE"):     cfg["mic_mode"]     = ev["MIC_MODE"]
            if ev.get("GROQ_API_KEY"): cfg["groq_api_key"] = ev["GROQ_API_KEY"]
            if ev.get("MODEL"):        cfg["model"]        = ev["MODEL"]
            if ev.get("WS_PORT"):      cfg["ws_port"]      = int(ev["WS_PORT"])
        except Exception:
            pass
    return cfg


def save_settings(cfg):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ─── Config (mutable globals) ─────────────────────────────────────────────────

_cfg = load_settings()

STT_ENGINE           = _cfg["stt_engine"]
PROMPT_MODE          = _cfg["prompt_mode"]
HOTKEY               = _cfg["hotkey"]
LANGUAGE             = _cfg["language"]
MIC_MODE             = _cfg["mic_mode"]
GROQ_API_KEY         = _cfg["groq_api_key"]
MODEL                = _cfg["model"]
GOOGLE_CLOUD_API_KEY = _cfg["google_cloud_api_key"]
WS_PORT              = _cfg["ws_port"]
CHECK_UPDATES        = _cfg["check_updates"]
GEMINI_API_KEY       = _cfg.get("gemini_api_key", "")
GEMINI_SYSTEM_PROMPT = _cfg.get("gemini_system_prompt", GEMINI_DEFAULT_SYSTEM_PROMPT)
LANG_MODE            = _cfg.get("lang_mode", "fixed")

_session_lang    = LANGUAGE  # language captured at hotkey press time
_last_stt        = None      # intermediate STT text captured inside _transcribe_gemini
_history         = deque(maxlen=50)
_history_window  = None

# ─── State ────────────────────────────────────────────────────────────────────

is_recording     = False
is_hotkey_active = False
audio_queue      = Queue()
logs             = deque(maxlen=20)
tray_icon        = None
audio_stream     = None
running          = True

_pre_roll_buf       = deque()
_pre_roll_maxframes = int(SAMPLE_RATE * 0.5)  # 500ms pre-roll

_ws_loop    = None
_ws_clients = set()
_result_q   = Queue()

_settings_window = None  # only one open at a time

# ─── Windows Unicode Typing ───────────────────────────────────────────────────

user32 = ctypes.windll.user32
INPUT_KEYBOARD    = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP   = 0x0002

# ─── Keyboard Layout Detection ────────────────────────────────────────────────

_LANGID_TO_CODE = {
    0x029: 'fa',  # Persian / Farsi
    0x009: 'en',  # English
    0x001: 'ar',  # Arabic
    0x01F: 'tr',  # Turkish
    0x007: 'de',  # German
    0x00C: 'fr',  # French
    0x019: 'ru',  # Russian
    0x016: 'pt',  # Portuguese
    0x00A: 'es',  # Spanish
    0x011: 'ja',  # Japanese
    0x012: 'ko',  # Korean
    0x004: 'zh',  # Chinese
}

user32.GetKeyboardLayout.restype = ctypes.c_void_p


def get_keyboard_layout_language():
    """Return language code matching the currently active Windows keyboard layout."""
    try:
        hwnd    = user32.GetForegroundWindow()
        tid     = user32.GetWindowThreadProcessId(hwnd, None)
        hkl     = user32.GetKeyboardLayout(tid)
        langid  = (hkl or 0) & 0xFFFF
        primary = langid & 0x3FF
        return _LANGID_TO_CODE.get(primary, LANGUAGE)
    except Exception:
        return LANGUAGE


def active_language():
    """Return the language to use: detected from keyboard layout or the fixed setting."""
    if LANG_MODE == "keyboard":
        return get_keyboard_layout_language()
    return LANGUAGE


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         wintypes.WORD),
        ("wScan",       wintypes.WORD),
        ("dwFlags",     wintypes.DWORD),
        ("time",        wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("iu",   INPUT_UNION),
        ("_pad", ctypes.c_ubyte * 8),
    ]


# ─── Logging / Tray Icon ──────────────────────────────────────────────────────

def log(msg):
    print(msg)
    logs.append(msg)
    if tray_icon:
        recent = list(logs)[-3:]
        tray_icon.title = ("SpeakPaste\n" + "\n".join(l[:40] for l in recent))[:127]


def check_for_update():
    """Check GitHub for a newer release. Runs in background thread."""
    try:
        resp = requests.get(
            f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest",
            timeout=8,
            headers={"Accept": "application/vnd.github+json"},
        )
        if resp.status_code != 200:
            return
        latest = resp.json().get("tag_name", "").lstrip("v")
        if not latest:
            return
        # Simple version compare: split by dot, compare as ints
        def _ver(v):
            try:
                return tuple(int(x) for x in v.split('.'))
            except Exception:
                return (0,)
        if _ver(latest) > _ver(VERSION):
            log(f"Update available: v{latest}  →  {GITHUB_URL}/releases")
    except Exception:
        pass


def create_icon(state="idle"):
    colors = {"idle": (100, 200, 100), "recording": (255, 80, 80), "waiting": (255, 180, 0)}
    img  = Image.new('RGB', (64, 64), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    draw.ellipse([16, 16, 48, 48], fill=colors.get(state, colors["idle"]))
    return img


# ─── Audio Recording ──────────────────────────────────────────────────────────

def _audio_callback(indata, frames, time_info, status):
    chunk = indata.copy()
    if is_recording:
        audio_queue.put(chunk)
    else:
        _pre_roll_buf.append(chunk)
        total = sum(c.shape[0] for c in _pre_roll_buf)
        while total > _pre_roll_maxframes and _pre_roll_buf:
            total -= _pre_roll_buf.popleft().shape[0]


def _start_recording():
    global is_recording, audio_queue
    if is_recording:
        return
    if MIC_MODE == "on_demand":
        audio_stream.start()
    is_recording = True
    audio_queue  = Queue()
    for chunk in list(_pre_roll_buf):
        audio_queue.put(chunk)
    _pre_roll_buf.clear()
    log("Recording...")
    if tray_icon:
        tray_icon.icon = create_icon("recording")


def _stop_recording():
    global is_recording
    if not is_recording:
        return None
    is_recording = False
    if MIC_MODE == "on_demand":
        audio_stream.stop()
    if tray_icon:
        tray_icon.icon = create_icon("waiting")

    import numpy as np
    import soundfile as sf

    chunks = []
    while not audio_queue.empty():
        chunks.append(audio_queue.get())

    if not chunks:
        log("No audio captured")
        if tray_icon:
            tray_icon.icon = create_icon("idle")
        return None

    audio = np.concatenate(chunks, axis=0)
    tmp   = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(tmp.name, audio, SAMPLE_RATE)
    log(f"Recorded {len(audio) / SAMPLE_RATE:.1f}s")
    return tmp.name


# ─── Gemini Models & Adapters ─────────────────────────────────────────────────

GEMINI_LITE_MODEL  = "gemini-3.1-flash-lite-preview"
GEMINI_FLASH_MODEL = "gemini-3-flash-preview"


class GeminiAdapter:
    """Adapter for Google Gemini REST API (generateContent endpoint)."""
    _BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def get_url(self, model, api_key):
        return f"{self._BASE}/{model}:generateContent?key={api_key}"

    def get_headers(self):
        return {"Content-Type": "application/json"}

    def build_text_request(self, system_prompt, text):
        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": text}]}],
        }

    def build_audio_request(self, system_prompt, audio_b64):
        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [
                {"inlineData": {"mimeType": "audio/wav", "data": audio_b64}},
                {"text": "Convert this voice recording into a professional English programming prompt."},
            ]}],
        }

    def parse_response(self, data):
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError):
            return None


PROVIDER_ADAPTERS = {
    "gemini": GeminiAdapter(),
}


# ─── Transcription ────────────────────────────────────────────────────────────

def _transcribe_google_direct(audio_path):
    log("Transcribing (Google)...")
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        with sr.AudioFile(audio_path) as source:
            audio = r.record(source)
        text = r.recognize_google(audio, language=_session_lang)
        log(f">> {text}")
        return text
    except Exception as e:
        log(f"Google error: {e}")
        return None
    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass


def _transcribe_google_cloud(audio_path):
    """Google Cloud Speech-to-Text REST API — official, requires API key."""
    import base64
    log("Transcribing (Google Cloud)...")
    try:
        with open(audio_path, 'rb') as f:
            audio_b64 = base64.b64encode(f.read()).decode('utf-8')

        # BCP-47: "fa" → "fa-IR", "en" → "en-US", already full codes pass through
        lang = _session_lang if '-' in _session_lang else {
            'fa': 'fa-IR', 'en': 'en-US', 'ar': 'ar-SA',
            'tr': 'tr-TR', 'de': 'de-DE', 'fr': 'fr-FR',
        }.get(_session_lang, _session_lang + '-' + _session_lang.upper())

        resp = requests.post(
            f"https://speech.googleapis.com/v1/speech:recognize?key={GOOGLE_CLOUD_API_KEY}",
            json={
                "config": {
                    "encoding":          "LINEAR16",
                    "sampleRateHertz":   SAMPLE_RATE,
                    "languageCode":      lang,
                    "enableAutomaticPunctuation": True,
                },
                "audio": {"content": audio_b64},
            },
        )
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                text = results[0]["alternatives"][0]["transcript"].strip()
                log(f">> {text}")
                return text
            log("Google Cloud: no speech detected")
            return None
        log(f"Google Cloud error {resp.status_code}: {resp.text[:120]}")
        return None
    except Exception as e:
        log(f"Google Cloud error: {e}")
        return None
    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass


def _transcribe_groq(audio_path):
    log("Transcribing (Groq)...")
    try:
        with open(audio_path, 'rb') as f:
            resp = requests.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": ("audio.wav", f, "audio/wav")},
                data={"model": MODEL, "language": _session_lang, "response_format": "text"},
            )
        if resp.status_code == 200:
            text = resp.text.strip()
            log(f">> {text}")
            return text
        log(f"Groq API error {resp.status_code}")
        return None
    except Exception as e:
        log(f"Groq error: {e}")
        return None
    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass


def _gemini_lite_prompt(text):
    """Send transcribed text through Gemini Flash Lite to get a prompt. Returns prompted text or None."""
    global _last_stt
    if not GEMINI_API_KEY:
        log("Gemini: API key not set — open Settings")
        return None
    _last_stt = text
    log("Converting to prompt (Gemini Lite)...")
    adapter = PROVIDER_ADAPTERS["gemini"]
    try:
        resp = requests.post(
            adapter.get_url(GEMINI_LITE_MODEL, GEMINI_API_KEY),
            headers=adapter.get_headers(),
            json=adapter.build_text_request(GEMINI_SYSTEM_PROMPT, text),
            timeout=30,
        )
        if resp.status_code == 200:
            result = adapter.parse_response(resp.json())
            if result:
                log(f">> {result}")
                return result
            log("Gemini: empty response")
            return None
        log(f"Gemini error {resp.status_code}: {resp.text[:120]}")
        return None
    except Exception as e:
        log(f"Gemini error: {e}")
        return None


def _gemini_flash_prompt(wav_path):
    """Send audio directly to Gemini Flash to get a prompt. Returns prompted text or None."""
    import base64
    if not GEMINI_API_KEY:
        log("Gemini: API key not set — open Settings")
        try:
            os.unlink(wav_path)
        except Exception:
            pass
        return None
    log("Converting voice to prompt (Gemini Flash)...")
    adapter = PROVIDER_ADAPTERS["gemini"]
    try:
        with open(wav_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("utf-8")
        resp = requests.post(
            adapter.get_url(GEMINI_FLASH_MODEL, GEMINI_API_KEY),
            headers=adapter.get_headers(),
            json=adapter.build_audio_request(GEMINI_SYSTEM_PROMPT, audio_b64),
            timeout=30,
        )
        if resp.status_code == 200:
            result = adapter.parse_response(resp.json())
            if result:
                log(f">> {result}")
                return result
            log("Gemini: empty response")
            return None
        log(f"Gemini error {resp.status_code}: {resp.text[:120]}")
        return None
    except Exception as e:
        log(f"Gemini error: {e}")
        return None
    finally:
        try:
            os.unlink(wav_path)
        except Exception:
            pass


# ─── Google Extension (WebSocket) ─────────────────────────────────────────────

async def _ws_handler(websocket):
    _ws_clients.add(websocket)
    log("[Google-ext] Extension connected")
    if tray_icon:
        tray_icon.icon = create_icon("idle")
    try:
        async for raw in websocket:
            try:
                _result_q.put(json.loads(raw))
            except json.JSONDecodeError:
                pass
    except Exception:
        pass
    finally:
        _ws_clients.discard(websocket)
        log("[Google-ext] Extension disconnected")
        if tray_icon:
            tray_icon.icon = create_icon("idle")


async def _ws_send(data):
    if not _ws_clients:
        return
    msg = json.dumps(data)
    await asyncio.gather(*[ws.send(msg) for ws in list(_ws_clients)], return_exceptions=True)


def _google_send(data):
    if _ws_loop:
        asyncio.run_coroutine_threadsafe(_ws_send(data), _ws_loop)


def _start_ws_server():
    global _ws_loop
    import websockets

    _ws_loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_ws_loop)

    async def _serve():
        async with websockets.serve(_ws_handler, "localhost", WS_PORT):
            log(f"[Google-ext] WebSocket ready on ws://localhost:{WS_PORT}")
            await asyncio.Future()

    _ws_loop.run_until_complete(_serve())


def _transcribe_google_ext():
    while not _result_q.empty():
        try:
            _result_q.get_nowait()
        except Empty:
            break
    if not _ws_clients:
        log("[Google-ext] Extension not connected — install & reload Chrome")
        if tray_icon:
            tray_icon.icon = create_icon("idle")
        return None
    _google_send({"cmd": "stop"})
    if tray_icon:
        tray_icon.icon = create_icon("waiting")
    try:
        result = _result_q.get(timeout=10)
        text   = result.get("text", "").strip()
        if result.get("error"):
            log(f"[Google-ext] Error: {result['error']}")
            return None
        if text:
            log(f">> {text}")
            return text
        return None
    except Empty:
        log("[Google-ext] Timeout")
        return None
    finally:
        if tray_icon:
            tray_icon.icon = create_icon("idle")


# ─── Text Injection ───────────────────────────────────────────────────────────

def _send_unicode_char(char_code):
    down = INPUT()
    down.type = INPUT_KEYBOARD
    down.iu.ki.wScan   = char_code
    down.iu.ki.dwFlags = KEYEVENTF_UNICODE

    up = INPUT()
    up.type = INPUT_KEYBOARD
    up.iu.ki.wScan   = char_code
    up.iu.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP

    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    user32.SendInput(1, ctypes.byref(up),   ctypes.sizeof(INPUT))


def type_text(text):
    if not text:
        return
    keys = HOTKEY.split('+')
    while any(keyboard.is_pressed(k) for k in keys):
        time.sleep(0.05)
    time.sleep(0.3)
    for k in ['left windows', 'right windows', 'alt', 'ctrl', 'shift']:
        try:
            keyboard.release(k)
        except Exception:
            pass
    for char in text:
        _send_unicode_char(ord(char))
        time.sleep(0.001)
    log("Typed OK")


# ─── Hotkey Handlers ──────────────────────────────────────────────────────────

def on_hotkey_press():
    global _session_lang
    _session_lang = active_language()
    if STT_ENGINE == "google-ext" and PROMPT_MODE != "gemini-flash":
        if not _ws_clients:
            log("[Google-ext] Extension not connected")
            return
        _google_send({"cmd": "start", "lang": _session_lang})
        log("Listening (Google-ext)...")
        if tray_icon:
            tray_icon.icon = create_icon("recording")
    else:
        _start_recording()


def on_hotkey_release():
    global _last_stt
    _last_stt = None

    if PROMPT_MODE == "gemini-flash":
        # Bypass STT entirely — send audio straight to Gemini Flash
        path = _stop_recording()
        text = _gemini_flash_prompt(path) if path else None
    elif STT_ENGINE == "google-ext":
        raw  = _transcribe_google_ext()
        text = _gemini_lite_prompt(raw) if (raw and PROMPT_MODE == "gemini-lite") else raw
    else:
        path = _stop_recording()
        if STT_ENGINE == "google":
            raw = _transcribe_google_direct(path) if path else None
        elif STT_ENGINE == "google-cloud":
            raw = _transcribe_google_cloud(path) if path else None
        else:  # groq
            raw = _transcribe_groq(path) if path else None
        text = _gemini_lite_prompt(raw) if (raw and PROMPT_MODE == "gemini-lite") else raw

    if text:
        engine_label = STT_ENGINE if PROMPT_MODE == "off" else f"{STT_ENGINE}+{PROMPT_MODE}"
        _history.appendleft({
            "time":   datetime.datetime.now().strftime("%H:%M:%S"),
            "engine": engine_label,
            "stt":    _last_stt,
            "output": text,
        })
        type_text(text)
    if tray_icon:
        tray_icon.icon = create_icon("idle")


def keyboard_listener():
    global running, is_hotkey_active
    keys = HOTKEY.split('+')
    while running:
        try:
            pressed = all(keyboard.is_pressed(k) for k in keys)
            if pressed and not is_hotkey_active:
                is_hotkey_active = True
                on_hotkey_press()
            elif not pressed and is_hotkey_active:
                is_hotkey_active = False
                threading.Thread(target=on_hotkey_release, daemon=True).start()
        except Exception:
            pass
        time.sleep(0.05)


# ─── Settings Window ──────────────────────────────────────────────────────────

def _apply_settings(new_cfg):
    global STT_ENGINE, PROMPT_MODE, HOTKEY, LANGUAGE, MIC_MODE, GROQ_API_KEY, MODEL, WS_PORT, CHECK_UPDATES
    global GEMINI_API_KEY, GEMINI_SYSTEM_PROMPT, LANG_MODE

    old_mic    = MIC_MODE
    old_engine = STT_ENGINE

    STT_ENGINE           = new_cfg["stt_engine"]
    PROMPT_MODE          = new_cfg["prompt_mode"]
    HOTKEY               = new_cfg["hotkey"]
    LANGUAGE             = new_cfg["language"]
    GROQ_API_KEY         = new_cfg["groq_api_key"]
    MODEL                = new_cfg["model"]
    GOOGLE_CLOUD_API_KEY = new_cfg["google_cloud_api_key"]
    WS_PORT              = new_cfg["ws_port"]
    MIC_MODE             = new_cfg["mic_mode"]
    CHECK_UPDATES        = new_cfg["check_updates"]
    GEMINI_API_KEY       = new_cfg.get("gemini_api_key", "")
    GEMINI_SYSTEM_PROMPT = new_cfg.get("gemini_system_prompt", GEMINI_DEFAULT_SYSTEM_PROMPT)
    LANG_MODE            = new_cfg.get("lang_mode", "fixed")

    # Apply mic mode change live
    if audio_stream and old_mic != MIC_MODE:
        if MIC_MODE == "on_demand":
            try:
                audio_stream.stop()
            except Exception:
                pass
        else:
            try:
                audio_stream.start()
            except Exception:
                pass

    save_settings(new_cfg)
    log(f"Settings saved — stt={STT_ENGINE}, prompt={PROMPT_MODE}, mic={MIC_MODE}")

    if old_engine != STT_ENGINE:
        log("Engine changed — restart SpeakPaste to fully apply")


def open_settings(icon=None, item=None):
    global _settings_window

    if _settings_window and _settings_window.winfo_exists():
        _settings_window.lift()
        _settings_window.focus_force()
        return

    def _build():
        global _settings_window

        win = tk.Tk()
        win.withdraw()  # hide until fully built — prevents layout flash
        win.title("SpeakPaste — Settings")
        win.resizable(False, False)
        win.configure(padx=20, pady=16, bg="#1e1e1e")

        lbl_style = {"bg": "#1e1e1e", "fg": "#cccccc", "font": ("Segoe UI", 9)}
        hdr_style = {"bg": "#1e1e1e", "fg": "#ffffff", "font": ("Segoe UI", 9, "bold")}
        ent_style = {"bg": "#2d2d2d", "fg": "#ffffff", "insertbackground": "#ffffff",
                     "relief": "flat", "font": ("Segoe UI", 9)}

        def section(text):
            tk.Label(win, text=text, **hdr_style).pack(anchor="w", pady=(12, 2))
            ttk.Separator(win).pack(fill="x", pady=(0, 6))

        rb_cfg = dict(bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                      activebackground="#1e1e1e", activeforeground="#ffffff",
                      font=("Segoe UI", 9))

        # ── Transcription Engine ─────────────────────────────────────────────
        section("Transcription Engine")
        stt_var = tk.StringVar(value=STT_ENGINE)

        stt_section = tk.Frame(win, bg="#1e1e1e")
        stt_section.pack(fill="x")

        stt_radios = []
        for label, val in [
                ("Google  —  free, unofficial, no key",         "google"),
                ("Google Cloud  —  official, API key required", "google-cloud"),
                ("Groq Whisper  —  API key required",           "groq"),
                ("Google Extension  —  Chrome in background",   "google-ext")]:
            rb = tk.Radiobutton(stt_section, text=label, variable=stt_var, value=val, **rb_cfg)
            rb.pack(anchor="w")
            stt_radios.append(rb)

        stt_extra = tk.Frame(stt_section, bg="#1e1e1e")
        stt_extra.pack(fill="x")

        # Groq sub-frame
        groq_frame = tk.Frame(stt_extra, bg="#252525", padx=12, pady=6)
        row3 = tk.Frame(groq_frame, bg="#252525")
        row3.pack(fill="x", pady=2)
        tk.Label(row3, text="API Key:", width=14, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        key_var = tk.StringVar(value=GROQ_API_KEY)
        tk.Entry(row3, textvariable=key_var, width=34, show="*",
                 **{**ent_style, "bg": "#333333"}).pack(side="left")
        row4 = tk.Frame(groq_frame, bg="#252525")
        row4.pack(fill="x", pady=2)
        tk.Label(row4, text="Model:", width=14, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        model_var = tk.StringVar(value=MODEL)
        tk.Entry(row4, textvariable=model_var, width=34,
                 **{**ent_style, "bg": "#333333"}).pack(side="left")

        # Google Cloud sub-frame
        gcloud_frame = tk.Frame(stt_extra, bg="#252525", padx=12, pady=6)
        row5 = tk.Frame(gcloud_frame, bg="#252525")
        row5.pack(fill="x", pady=2)
        tk.Label(row5, text="API Key:", width=14, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        gcloud_key_var = tk.StringVar(value=GOOGLE_CLOUD_API_KEY)
        tk.Entry(row5, textvariable=gcloud_key_var, width=34, show="*",
                 **{**ent_style, "bg": "#333333"}).pack(side="left")
        tk.Label(gcloud_frame,
                 text="console.cloud.google.com → Speech-to-Text API → Credentials",
                 bg="#252525", fg="#666666", font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))

        def _refresh_stt_extra(*_):
            eng = stt_var.get()
            for child in stt_extra.winfo_children():
                child.pack_forget()
            if eng == "groq":
                stt_extra.configure(bg="#252525")
                groq_frame.pack(fill="x")
            elif eng == "google-cloud":
                stt_extra.configure(bg="#252525")
                gcloud_frame.pack(fill="x")
            else:
                stt_extra.configure(bg="#1e1e1e")

        stt_var.trace_add("write", _refresh_stt_extra)
        _refresh_stt_extra()

        # ── Prompt ───────────────────────────────────────────────────────────
        section("Prompt")
        prompt_var = tk.StringVar(value=PROMPT_MODE)

        for label, val in [
                ("Off  —  paste raw transcript",                                     "off"),
                ("Gemini Flash Lite  —  transcript \u2192 prompt",                   "gemini-lite"),
                ("Gemini Flash  —  voice \u2192 prompt directly  (skips engine above)", "gemini-flash")]:
            tk.Radiobutton(win, text=label, variable=prompt_var, value=val,
                           **rb_cfg).pack(anchor="w")

        prompt_extra = tk.Frame(win, bg="#1e1e1e")
        prompt_extra.pack(fill="x")

        # Gemini config (API key + system prompt) — shared for both gemini modes
        gemini_frame = tk.Frame(prompt_extra, bg="#252525", padx=12, pady=8)

        row_gk = tk.Frame(gemini_frame, bg="#252525")
        row_gk.pack(fill="x", pady=2)
        tk.Label(row_gk, text="API Key:", width=14, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        gemini_key_var = tk.StringVar(value=GEMINI_API_KEY)
        tk.Entry(row_gk, textvariable=gemini_key_var, width=34, show="*",
                 **{**ent_style, "bg": "#333333"}).pack(side="left")
        tk.Label(gemini_frame,
                 text="aistudio.google.com → Get API key",
                 bg="#252525", fg="#666666", font=("Segoe UI", 8)).pack(anchor="w", pady=(0, 6))

        tk.Label(gemini_frame, text="System Prompt:", anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(anchor="w", pady=(2, 2))
        prompt_wrap = tk.Frame(gemini_frame, bg="#252525")
        prompt_wrap.pack(fill="x")
        gemini_prompt_text = tk.Text(
            prompt_wrap, height=4, bg="#333333", fg="#ffffff",
            insertbackground="#ffffff", relief="flat",
            font=("Segoe UI", 9), wrap="word",
        )
        gemini_prompt_text.insert("1.0", GEMINI_SYSTEM_PROMPT)
        gemini_prompt_text.pack(side="left", fill="x", expand=True)
        prompt_sb = tk.Scrollbar(prompt_wrap, command=gemini_prompt_text.yview)
        prompt_sb.pack(side="right", fill="y")
        gemini_prompt_text.config(yscrollcommand=prompt_sb.set)

        def _reset_prompt():
            gemini_prompt_text.delete("1.0", "end")
            gemini_prompt_text.insert("1.0", GEMINI_DEFAULT_SYSTEM_PROMPT)

        tk.Button(gemini_frame, text="Reset to default", command=_reset_prompt,
                  bg="#3c3c3c", fg="#888888", relief="flat", font=("Segoe UI", 8),
                  activebackground="#4c4c4c", activeforeground="#cccccc").pack(anchor="e", pady=(4, 0))

        def _refresh_prompt(*_):
            mode = prompt_var.get()
            # Show/hide Gemini config
            for child in prompt_extra.winfo_children():
                child.pack_forget()
            if mode in ("gemini-lite", "gemini-flash"):
                prompt_extra.configure(bg="#252525")
                gemini_frame.pack(fill="x")
            else:
                prompt_extra.configure(bg="#1e1e1e")
            # Visually mute STT section when gemini-flash bypasses it
            # Note: intentionally NOT using state="disabled" — causes visual glitch on Windows dark theme
            fg = "#555555" if mode == "gemini-flash" else "#cccccc"
            for rb in stt_radios:
                rb.config(fg=fg)
            _refresh_stt_extra()

        prompt_var.trace_add("write", _refresh_prompt)
        _refresh_prompt()

        # ── Hotkey & Language ────────────────────────────────────────────────
        section("General")

        row1 = tk.Frame(win, bg="#1e1e1e")
        row1.pack(fill="x", pady=2)
        tk.Label(row1, text="Hotkey:", width=12, anchor="w", **lbl_style).pack(side="left")
        hotkey_var = tk.StringVar(value=HOTKEY)
        tk.Entry(row1, textvariable=hotkey_var, width=20, **ent_style).pack(side="left")

        row2 = tk.Frame(win, bg="#1e1e1e")
        row2.pack(fill="x", pady=2)
        tk.Label(row2, text="Language:", width=12, anchor="w", **lbl_style).pack(side="left")
        lang_var = tk.StringVar(value=LANGUAGE)
        lang_entry = tk.Entry(row2, textvariable=lang_var, width=20, **ent_style)
        lang_entry.pack(side="left")

        lang_mode_var = tk.BooleanVar(value=(LANG_MODE == "keyboard"))

        def _toggle_lang_mode(*_):
            if lang_mode_var.get():
                lang_entry.config(state="disabled", disabledforeground="#555555")
            else:
                lang_entry.config(state="normal")

        tk.Checkbutton(win, text="Follow Windows keyboard layout  (auto-detect Persian / English)",
                       variable=lang_mode_var, command=_toggle_lang_mode,
                       bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", activeforeground="#ffffff",
                       font=("Segoe UI", 9)).pack(anchor="w", padx=(0, 0), pady=(2, 0))
        _toggle_lang_mode()  # apply initial state

        # ── Microphone ──────────────────────────────────────────────────────
        section("Microphone")
        mic_var = tk.StringVar(value=MIC_MODE)
        tk.Radiobutton(win, text="Always on  (pre-roll active, mic indicator always visible)",
                       variable=mic_var, value="always",
                       bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", activeforeground="#ffffff",
                       font=("Segoe UI", 9)).pack(anchor="w")
        tk.Radiobutton(win, text="On demand  (mic opens only while hotkey held — more secure)",
                       variable=mic_var, value="on_demand",
                       bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", activeforeground="#ffffff",
                       font=("Segoe UI", 9)).pack(anchor="w")

        # ── Buttons ──────────────────────────────────────────────────────────
        btn_frame = tk.Frame(win, bg="#1e1e1e")
        btn_frame.pack(fill="x", pady=(18, 0))

        # ── General options ──────────────────────────────────────────────────
        section("Options")
        updates_var = tk.BooleanVar(value=CHECK_UPDATES)
        tk.Checkbutton(win, text="Check for updates on startup",
                       variable=updates_var,
                       bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", activeforeground="#ffffff",
                       font=("Segoe UI", 9)).pack(anchor="w")

        def on_save():
            new_cfg = {
                "stt_engine":           stt_var.get(),
                "prompt_mode":          prompt_var.get(),
                "hotkey":               hotkey_var.get().strip(),
                "language":             lang_var.get().strip(),
                "lang_mode":            "keyboard" if lang_mode_var.get() else "fixed",
                "mic_mode":             mic_var.get(),
                "groq_api_key":         key_var.get().strip(),
                "model":                model_var.get().strip(),
                "google_cloud_api_key": gcloud_key_var.get().strip(),
                "ws_port":              WS_PORT,
                "check_updates":        updates_var.get(),
                "gemini_api_key":       gemini_key_var.get().strip(),
                "gemini_system_prompt": gemini_prompt_text.get("1.0", "end-1c").strip(),
            }
            _apply_settings(new_cfg)
            win.destroy()

        tk.Button(btn_frame, text="Save", command=on_save, width=10,
                  bg="#0078d4", fg="white", relief="flat",
                  font=("Segoe UI", 9, "bold"),
                  activebackground="#106ebe", activeforeground="white").pack(side="right", padx=(6, 0))
        tk.Button(btn_frame, text="Cancel", command=win.destroy, width=10,
                  bg="#3c3c3c", fg="#cccccc", relief="flat",
                  font=("Segoe UI", 9),
                  activebackground="#4c4c4c", activeforeground="white").pack(side="right")

        # ── Footer ───────────────────────────────────────────────────────────
        ttk.Separator(win).pack(fill="x", pady=(16, 6))
        footer = tk.Frame(win, bg="#1e1e1e")
        footer.pack(fill="x")
        tk.Label(footer, text=f"SpeakPaste v{VERSION}",
                 bg="#1e1e1e", fg="#555555", font=("Segoe UI", 8)).pack(side="left")
        link = tk.Label(footer, text="View on GitHub ↗",
                        bg="#1e1e1e", fg="#3d8fd1", font=("Segoe UI", 8),
                        cursor="hand2")
        link.pack(side="right")
        link.bind("<Button-1>", lambda e: __import__('webbrowser').open(GITHUB_URL))

        _settings_window = win
        win.update_idletasks()  # force layout calculation
        # center on screen
        w = win.winfo_reqwidth()
        h = win.winfo_reqheight()
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win.geometry(f"+{(sw - w) // 2}+{(sh - h) // 2}")
        win.deiconify()  # now show
        win.mainloop()
        _settings_window = None

    threading.Thread(target=_build, daemon=True).start()


# ─── History Window ──────────────────────────────────────────────────────────

def open_history(icon=None, item=None):
    global _history_window

    if _history_window and _history_window.winfo_exists():
        _history_window.lift()
        _history_window.focus_force()
        return

    def _build():
        global _history_window

        win = tk.Tk()
        win.withdraw()
        win.title("SpeakPaste - History")
        win.configure(bg="#1e1e1e")
        win.resizable(True, True)

        # ── Top bar ──────────────────────────────────────────────────────────
        top = tk.Frame(win, bg="#1e1e1e", padx=14, pady=8)
        top.pack(fill="x")

        show_stt_var   = tk.BooleanVar(value=True)
        _known_len     = [-1]  # last rendered history length; -1 forces first render

        def _render():
            _known_len[0] = len(_history)
            txt.config(state="normal")
            txt.delete("1.0", "end")
            entries = list(_history)
            if not entries:
                txt.insert("end", "No history yet.", "empty")
            for entry in entries:
                txt.insert("end", entry["time"] + "  ", "ts")
                txt.insert("end", entry["engine"].upper() + "\n", "eng")
                if entry.get("stt") and show_stt_var.get():
                    txt.insert("end", "  Voice   ", "lbl_v")
                    txt.insert("end", entry["stt"] + "\n", "stt")
                lbl = "  Prompt  " if entry.get("stt") else "  "
                txt.insert("end", lbl, "lbl_p")
                txt.insert("end", entry["output"] + "\n", "out")
                txt.insert("end", "\n")
            txt.config(state="disabled")

        def _poll():
            if len(_history) != _known_len[0]:
                _render()
            win.after(500, _poll)

        tk.Checkbutton(
            top, text="Show voice text",
            variable=show_stt_var, command=_render,
            bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
            activebackground="#1e1e1e", activeforeground="#ffffff",
            font=("Segoe UI", 9),
        ).pack(side="left")

        def _clear():
            _history.clear()
            _render()

        tk.Button(
            top, text="Clear", command=_clear,
            bg="#3c3c3c", fg="#888888", relief="flat",
            font=("Segoe UI", 9),
            activebackground="#4c4c4c", activeforeground="#cccccc",
        ).pack(side="right")

        ttk.Separator(win).pack(fill="x")

        # ── Scrollable text area ─────────────────────────────────────────────
        frame = tk.Frame(win, bg="#141414")
        frame.pack(fill="both", expand=True)

        txt = tk.Text(
            frame, bg="#141414", fg="#cccccc",
            relief="flat", padx=14, pady=10,
            font=("Segoe UI", 9), wrap="word",
            cursor="arrow", state="disabled",
            spacing1=2, spacing3=2,
        )
        sb = tk.Scrollbar(frame, command=txt.yview,
                          bg="#2d2d2d", troughcolor="#1e1e1e",
                          activebackground="#3d3d3d")
        txt.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        txt.tag_config("empty",  foreground="#555555", font=("Segoe UI", 9, "italic"))
        txt.tag_config("ts",     foreground="#555555")
        txt.tag_config("eng",    foreground="#666666", font=("Segoe UI", 8))
        txt.tag_config("lbl_v",  foreground="#555555", font=("Segoe UI", 8))
        txt.tag_config("stt",    foreground="#888888")
        txt.tag_config("lbl_p",  foreground="#555555", font=("Segoe UI", 8))
        txt.tag_config("out",    foreground="#ffffff")

        _render()
        win.after(500, _poll)

        _history_window = win
        win.update_idletasks()
        w = max(win.winfo_reqwidth(), 580)
        h = max(win.winfo_reqheight(), 420)
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        win.deiconify()
        win.mainloop()
        _history_window = None

    threading.Thread(target=_build, daemon=True).start()


# ─── System Tray ─────────────────────────────────────────────────────────────

STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME    = "SpeakPaste"


def _get_exe_path():
    if getattr(sys, 'frozen', False):
        return sys.executable
    return f'pythonw "{os.path.abspath(__file__)}"'


def _is_in_startup():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def _toggle_startup(icon, item):
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE)
        if _is_in_startup():
            winreg.DeleteValue(key, APP_NAME)
            log("Removed from startup")
        else:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _get_exe_path())
            log("Added to startup")
        winreg.CloseKey(key)
    except Exception as e:
        log(f"Startup error: {e}")


def _on_exit(icon, item):
    global running
    running = False
    icon.stop()
    os._exit(0)


def _last_log(item):
    return list(logs)[-1] if logs else "Ready"


def _mic_status(item):
    if MIC_MODE == "on_demand":
        return "Mic: on-demand (secure)"
    return "Mic: always-on"


def _toggle_mic_mode(icon, item):
    global MIC_MODE
    new_mode = "on_demand" if MIC_MODE == "always" else "always"
    cfg = load_settings()
    cfg["mic_mode"] = new_mode
    _apply_settings(cfg)


def setup_tray():
    global tray_icon
    _tray_label = STT_ENGINE.upper() if PROMPT_MODE == "off" else f"{STT_ENGINE.upper()}+{PROMPT_MODE.upper()}"
    menu = pystray.Menu(
        pystray.MenuItem(f"Engine: {_tray_label}", None, enabled=False),
        pystray.MenuItem(_last_log, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Settings...", open_settings),
        pystray.MenuItem("History...", open_history),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(_mic_status, _toggle_mic_mode,
                         checked=lambda item: MIC_MODE == "on_demand"),
        pystray.MenuItem("Run at startup", _toggle_startup,
                         checked=lambda item: _is_in_startup()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", _on_exit),
    )
    tray_icon = pystray.Icon(
        "speakpaste",
        create_icon("idle"),
        f"SpeakPaste [{_tray_label}]\n{HOTKEY.upper()} to record",
        menu,
    )
    return tray_icon


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global audio_stream, running

    _label = STT_ENGINE.upper() if PROMPT_MODE == "off" else f"{STT_ENGINE.upper()}+{PROMPT_MODE.upper()}"

    needs_audio = not (STT_ENGINE == "google-ext" and PROMPT_MODE != "gemini-flash")
    if needs_audio:
        if STT_ENGINE == "groq" and not GROQ_API_KEY:
            log("WARNING: GROQ_API_KEY not set — open Settings to configure")
        if STT_ENGINE == "google-cloud" and not GOOGLE_CLOUD_API_KEY:
            log("WARNING: GOOGLE_CLOUD_API_KEY not set — open Settings to configure")
        if PROMPT_MODE in ("gemini-lite", "gemini-flash") and not GEMINI_API_KEY:
            log("WARNING: GEMINI_API_KEY not set — open Settings to configure")
        import sounddevice as sd
        audio_stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                      callback=_audio_callback)
        if MIC_MODE == "always":
            audio_stream.start()
            log(f"SpeakPaste [{_label}] ready — {HOTKEY.upper()} (mic always-on)")
        else:
            log(f"SpeakPaste [{_label}] ready — {HOTKEY.upper()} (mic on-demand)")

    elif STT_ENGINE == "google-ext":
        ws_thread = threading.Thread(target=_start_ws_server, daemon=True)
        ws_thread.start()
        time.sleep(0.2)
        log(f"SpeakPaste [GOOGLE-EXT] ready — load Chrome extension to connect")

    else:
        log("Unknown configuration — open Settings to fix")

    kb_thread = threading.Thread(target=keyboard_listener, daemon=True)
    kb_thread.start()

    if CHECK_UPDATES:
        threading.Thread(target=check_for_update, daemon=True).start()

    setup_tray().run()


if __name__ == "__main__":
    main()
