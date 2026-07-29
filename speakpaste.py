"""
SpeakPaste - Voice to Text, and Text to Voice
Hold Win+Alt to record, release to transcribe and paste.
Select text anywhere and press Win+Shift to hear it read aloud.

Speech engines (tts_engine):
  edge          — free Microsoft Edge neural voices, no key  [default]
  vertex        — Gemini-TTS on Vertex AI (credential file, tunable tone)

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
import base64
import re
import wave
import urllib.parse
import urllib.request
import numpy as np
import sounddevice as sd
import soundfile as sf
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

VERSION       = "1.12.0"
GITHUB_REPO   = "mohammad-rj/speakpaste"
GITHUB_URL    = f"https://github.com/{GITHUB_REPO}"

SETTINGS_FILE = os.path.join(APP_DIR, 'settings.json')
HISTORY_FILE  = os.path.join(APP_DIR, 'history.json')
HISTORY_MAX   = 50
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

# Measured 2026-07-21 on code-switched Persian+English: 3.6 keeps "branch",
# "commit" and "merge" in Latin script where gemini-flash-latest mangles them.
GEMINI_STT_DEFAULT_MODEL = "gemini-3.6-flash"

TTS_DEFAULT_STYLE = (
    "Speak fast in casual street Persian - a quick friendly update, short pauses, "
    "clearly articulated, but NOT rushed or breathless; a notch below rapid-fire, "
    "a comfortable brisk pace you could listen to all day."
)

# Edge voices are free and need no key. Vertex needs a service-account/ADC file.
TTS_EDGE_VOICES = [
    "fa-IR-FaridNeural", "fa-IR-DilaraNeural",
    "en-US-AndrewNeural", "en-US-AriaNeural", "en-US-GuyNeural",
    "tr-TR-AhmetNeural", "tr-TR-EmelNeural", "ar-SA-HamedNeural",
]
TTS_VERTEX_VOICES = ["Zubenelgenubi", "Achird", "Sadachbia", "Umbriel", "Charon",
                     "Puck", "Kore", "Aoede", "Leda", "Orus", "Fenrir"]
TTS_VERTEX_MODELS = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-tts",
                     "gemini-2.5-pro-tts"]

_DEFAULTS = {
    "stt_engine":                "google",
    "prompt_mode":               "off",
    "hotkey":                    "win+alt",
    "language":                  "fa",
    "mic_mode":                  "always",
    "groq_api_key":              "",
    "model":                     "whisper-large-v3-turbo",
    "google_cloud_api_key":      "",
    "ws_port":                   9137,
    "check_updates":             True,
    "gemini_api_key":            "",
    # Blank = Google AI Studio. Set both to route every Gemini call through a
    # Gemini-compatible proxy instead (the proxy holds the real key).
    "gemini_base_url":           "",
    "gemini_auth_token":         "",
    "gemini_system_prompt":      GEMINI_DEFAULT_SYSTEM_PROMPT,
    "lang_mode":                 "fixed",
    "gemini_thinking_level":     "LOW",
    "gemini_media_resolution":   "LOW",
    "gemini_stt_model":          GEMINI_STT_DEFAULT_MODEL,
    "inject_mode":               "auto",     # auto | type | paste
    "notify_errors":             True,
    # ── Text-to-Speech (read selected text aloud) ───────────────────────────
    "tts_enabled":               True,
    "tts_hotkey":                "win+shift",
    "tts_engine":                "edge",       # edge | vertex
    "tts_edge_voice":            "fa-IR-FaridNeural",
    "tts_vertex_voice":          "Zubenelgenubi",
    "tts_vertex_model":          "gemini-3.1-flash-tts-preview",
    "tts_vertex_cred":           "",           # path to ADC / service-account json
    "tts_vertex_project":        "",           # blank = read from the cred file
    "tts_style":                 TTS_DEFAULT_STYLE,
    "tts_speed":                 1.0,
    "tts_popup":                 True,
    "tts_popup_autoclose":       30,           # seconds after playback ends; 0 = never
    "tts_chunk_secs":            25,           # target speech seconds per chunk
    "tts_first_chunk_secs":      8,            # shorter first chunk = faster start
}

# ─── Settings Load / Save ─────────────────────────────────────────────────────

def validate_ws_port(value):
    """Return value if it's an int in 1024..65535, else return default 9137."""
    if isinstance(value, int) and 1024 <= value <= 65535:
        return value
    return 9137


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
            cfg["ws_port"] = validate_ws_port(cfg["ws_port"])
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
    cfg["ws_port"] = validate_ws_port(cfg["ws_port"])
    return cfg


def save_settings(cfg):
    with open(SETTINGS_FILE, 'w', encoding='utf-8') as f:
        json.dump(cfg, f, indent=2, ensure_ascii=False)


# ─── History persistence ──────────────────────────────────────────────────────

def load_history():
    """Load saved history entries (newest first) into a bounded deque."""
    try:
        if os.path.exists(HISTORY_FILE):
            with open(HISTORY_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
            if isinstance(data, list):
                return deque(data, maxlen=HISTORY_MAX)
    except Exception:
        pass
    return deque(maxlen=HISTORY_MAX)


def save_history():
    """Persist the in-memory history to disk atomically (newest first)."""
    try:
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(list(_history), f, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)
    except Exception as e:
        log(f"History save error: {e}")


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
GEMINI_API_KEY            = _cfg.get("gemini_api_key", "")
# Environment wins over settings.json, so a shared machine can point the app at
# its own proxy without anyone editing the config file.
GEMINI_BASE_URL           = (os.environ.get("SPEAKPASTE_GEMINI_BASE_URL")
                             or _cfg.get("gemini_base_url", "")).rstrip("/")
GEMINI_AUTH_TOKEN         = (os.environ.get("SPEAKPASTE_GEMINI_TOKEN")
                             or _cfg.get("gemini_auth_token", ""))
GEMINI_SYSTEM_PROMPT      = _cfg.get("gemini_system_prompt", GEMINI_DEFAULT_SYSTEM_PROMPT)
LANG_MODE                 = _cfg.get("lang_mode", "fixed")
GEMINI_THINKING_LEVEL     = _cfg.get("gemini_thinking_level", "LOW")
GEMINI_MEDIA_RESOLUTION   = _cfg.get("gemini_media_resolution", "LOW")
GEMINI_STT_MODEL          = _cfg.get("gemini_stt_model", GEMINI_STT_DEFAULT_MODEL)
INJECT_MODE               = _cfg.get("inject_mode", "auto")
NOTIFY_ERRORS             = _cfg.get("notify_errors", True)

TTS_ENABLED           = _cfg.get("tts_enabled", True)
TTS_HOTKEY            = _cfg.get("tts_hotkey", "win+shift")
TTS_ENGINE            = _cfg.get("tts_engine", "edge")
TTS_EDGE_VOICE        = _cfg.get("tts_edge_voice", "fa-IR-FaridNeural")
TTS_VERTEX_VOICE      = _cfg.get("tts_vertex_voice", "Zubenelgenubi")
TTS_VERTEX_MODEL      = _cfg.get("tts_vertex_model", "gemini-3.1-flash-tts-preview")
TTS_VERTEX_CRED       = _cfg.get("tts_vertex_cred", "")
TTS_VERTEX_PROJECT    = _cfg.get("tts_vertex_project", "")
TTS_STYLE             = _cfg.get("tts_style", TTS_DEFAULT_STYLE)
TTS_SPEED             = float(_cfg.get("tts_speed", 1.0))
TTS_POPUP             = _cfg.get("tts_popup", True)
TTS_POPUP_AUTOCLOSE   = int(_cfg.get("tts_popup_autoclose", 30))
TTS_CHUNK_SECS        = float(_cfg.get("tts_chunk_secs", 25))
TTS_FIRST_CHUNK_SECS  = float(_cfg.get("tts_first_chunk_secs", 8))

_session_lang    = LANGUAGE  # language captured at hotkey press time
_last_stt        = None      # intermediate STT text captured inside _transcribe_gemini
_history         = load_history()  # persisted across restarts (newest first)
_history_window  = None

# ─── State ────────────────────────────────────────────────────────────────────

is_recording     = False
is_hotkey_active = False
is_tts_hotkey_active = False
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

# Single persistent hidden Tk root on ONE dedicated thread. All windows are
# Toplevels of this root and every Tk touch is marshalled onto its thread via
# _ui_run(). Tkinter is not thread-safe: the old code created a fresh tk.Tk()
# root + its own mainloop in a new thread on every menu click, and the tray
# thread poked those roots directly (winfo_exists/lift) — that races the Tcl
# global lock and intermittently freezes the app / never opens the window.
_ui_root  = None
_ui_queue = Queue()  # callables to execute on the Tk thread

# ─── Windows Unicode Typing ───────────────────────────────────────────────────

user32 = ctypes.windll.user32
INPUT_KEYBOARD    = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP   = 0x0002

# ─── Physical Key State ───────────────────────────────────────────────────────
# `keyboard.is_pressed()` answers from the library's own hook bookkeeping, not
# from Windows. Windows silently skips a hook callback whenever the hook thread
# is slow to answer - here, whenever the GIL is busy recording, transcribing or
# playing TTS - and one missed key-up leaves that bookkeeping wrong forever: a
# phantom Win held down made Alt alone satisfy win+alt, until a real Win press
# cleared it. GetAsyncKeyState keeps no state, so a poll loop cannot get stuck.

user32.GetAsyncKeyState.restype  = ctypes.c_short
user32.GetAsyncKeyState.argtypes = [ctypes.c_int]

_VK_BY_NAME = {
    "win":          (0x5B, 0x5C),
    "windows":      (0x5B, 0x5C),
    "left windows": (0x5B,),
    "right windows": (0x5C,),
    "alt":          (0x12,),
    "shift":        (0x10,),
    "ctrl":         (0x11,),
    "control":      (0x11,),
}


def _phys_down(key):
    """True while `key` is physically held - asked of Windows, not of a cache."""
    vks = _VK_BY_NAME.get(str(key).strip().lower())
    if not vks:                                   # letters, F-keys, anything else
        return keyboard.is_pressed(key)
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)

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


def _is_rtl(s):
    """True if the string contains any Arabic/Persian (RTL script) character."""
    for ch in s or "":
        c = ord(ch)
        if (0x0600 <= c <= 0x06FF or   # Arabic
                0x0750 <= c <= 0x077F or   # Arabic Supplement
                0x08A0 <= c <= 0x08FF or   # Arabic Extended-A
                0xFB50 <= c <= 0xFDFF or   # Arabic Presentation Forms-A
                0xFE70 <= c <= 0xFEFF):    # Arabic Presentation Forms-B
            return True
    return False


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

_log_last_t = [None]  # [timestamp of last log] for elapsed calculation
_last_error = [None]  # last engine failure, surfaced to the user as a toast
LOG_FILE    = os.path.join(APP_DIR, 'speakpaste.log')
LOG_MAX     = 512 * 1024

def log(msg):
    now  = time.time()
    dt   = datetime.datetime.now()
    ts   = dt.strftime("%H:%M:%S.") + f"{dt.microsecond // 1000:03d}"
    if _log_last_t[0] is not None:
        line = f"[{ts}] +{now - _log_last_t[0]:.2f}s  {msg}"
    else:
        line = f"[{ts}]  {msg}"
    _log_last_t[0] = now
    print(line)
    logs.append(line)
    # Also to disk: under pythonw/the frozen exe there is no console to read.
    try:
        if os.path.exists(LOG_FILE) and os.path.getsize(LOG_FILE) > LOG_MAX:
            os.replace(LOG_FILE, LOG_FILE + ".old")
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write(dt.strftime("%Y-%m-%d ") + line + "\n")
    except Exception:
        pass
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


def _cancel_recording():
    """Drop an in-flight recording without transcribing (TTS hotkey took over)."""
    global is_recording, audio_queue
    if not is_recording:
        return
    is_recording = False
    if MIC_MODE == "on_demand":
        try:
            audio_stream.stop()
        except Exception:
            pass
    audio_queue = Queue()
    if tray_icon:
        tray_icon.icon = create_icon("idle")


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

_THINKING_BUDGETS = {"MINIMAL": 512, "LOW": 1024, "MEDIUM": 4096, "HIGH": 8192}

def _thinking_budget(level):
    return _THINKING_BUDGETS.get(level.upper(), 1024)


class GeminiAdapter:
    """Adapter for Google Gemini REST API (generateContent endpoint)."""
    _BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def get_url(self, model, api_key):
        return f"{self._BASE}/{model}:generateContent?key={api_key}"

    def get_headers(self):
        return {"Content-Type": "application/json"}

    def build_text_request(self, system_prompt, text, thinking_level="LOW", media_resolution="LOW"):
        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [{"text": text}]}],
            "generationConfig": {
                "thinkingConfig":  {"thinkingBudget": _thinking_budget(thinking_level)},
                "mediaResolution": f"MEDIA_RESOLUTION_{media_resolution}",
            },
        }

    def build_audio_request(self, system_prompt, audio_b64, thinking_level="LOW", media_resolution="LOW"):
        return {
            "systemInstruction": {"parts": [{"text": system_prompt}]},
            "contents": [{"parts": [
                {"inlineData": {"mimeType": "audio/wav", "data": audio_b64}},
                {"text": "Convert this voice recording into a professional English programming prompt."},
            ]}],
            "generationConfig": {
                "thinkingConfig":  {"thinkingBudget": _thinking_budget(thinking_level)},
                "mediaResolution": f"MEDIA_RESOLUTION_{media_resolution}",
            },
        }

    def parse_response(self, data):
        try:
            return data["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError):
            return None


PROVIDER_ADAPTERS = {
    "gemini": GeminiAdapter(),
}


def _gemini_endpoint(model):
    """(url, headers) for a Gemini call. Returns (None, None) if unconfigured.

    Order: a custom base URL wins if one is set, then Vertex AI whenever a Google
    credential file is configured, then the plain generativelanguage.googleapis.com
    key path. The custom URL is for any Gemini-compatible proxy - it takes the same
    request body and path, so only the host and the auth header change; the proxy
    holds the real Google key, which is why the token here is not an AIzaSy one.
    """
    if GEMINI_BASE_URL:
        # A named User-Agent is not cosmetic here: proxies behind Cloudflare
        # answer 403 (error code 1010) to the default python-urllib/requests one.
        headers = {"Content-Type": "application/json",
                   "User-Agent": f"SpeakPaste/{VERSION}"}
        if GEMINI_AUTH_TOKEN:
            headers["Authorization"] = f"Bearer {GEMINI_AUTH_TOKEN}"
        return f"{GEMINI_BASE_URL}/models/{model}:generateContent", headers
    if TTS_VERTEX_CRED and os.path.exists(TTS_VERTEX_CRED):
        try:
            token, proj = _vertex_access_token(TTS_VERTEX_CRED)
            project = TTS_VERTEX_PROJECT or proj
            if project:
                return (f"https://aiplatform.googleapis.com/v1/projects/{project}"
                        f"/locations/global/publishers/google/models/"
                        f"{model}:generateContent",
                        {"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"})
        except Exception as e:
            log(f"Vertex auth failed, falling back to API key: {str(e)[:70]}")
    if GEMINI_API_KEY:
        adapter = PROVIDER_ADAPTERS["gemini"]
        return adapter.get_url(model, GEMINI_API_KEY), adapter.get_headers()
    return None, None


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
        _last_error[0] = f"Google engine failed: {e}"
        return None
    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass


GEMINI_STT_PROMPT = (
    "You are a verbatim speech transcriber. Write down exactly what is said, "
    "in the language it is said in. NEVER translate anything.\n"
    "Rules:\n"
    "1. Persian speech -> Persian script. English speech -> Latin script. "
    "Detect this yourself from the audio; there is no language setting.\n"
    "2. Code-switching is common and must be preserved: when a Persian sentence "
    "contains English words - especially technical terms like loop, refactor, "
    "commit, deploy, database, function, bug, merge, branch, API, server - "
    "write those words in ENGLISH (Latin letters) inside the Persian sentence. "
    "Do NOT find a Persian equivalent for them, and do NOT transliterate them "
    "into Persian letters.\n"
    "3. Apply normal punctuation and capitalisation.\n"
    "4. Output ONLY the transcript: no preamble, no quotes, no explanation.\n"
    "5. If there is no intelligible speech, output nothing."
)


def _transcribe_gemini(audio_path):
    """Gemini transcription. The model detects the language itself, so no
    language code is sent and the keyboard layout is irrelevant.

    Uses Vertex AI when a credential is configured (EP rule: Vertex only while
    free credit lasts) and falls back to the Gemini API key otherwise, so the
    public build still works for users who only have an API key.
    """
    log("Transcribing (Gemini, auto language)...")
    try:
        with open(audio_path, 'rb') as f:
            audio_b64 = base64.b64encode(f.read()).decode('utf-8')

        payload = {
            "systemInstruction": {"parts": [{"text": GEMINI_STT_PROMPT}]},
            "contents": [{"role": "user", "parts": [
                {"inlineData": {"mimeType": "audio/wav", "data": audio_b64}},
                {"text": "Transcribe this recording."},
            ]}],
            # Thinking tokens share the output budget and only add latency here.
            "generationConfig": {"thinkingConfig": {"thinkingBudget": 0}},
        }

        url, headers = _gemini_endpoint(GEMINI_STT_MODEL)
        if not url:
            log("Gemini STT: no Vertex credential and no API key — open Settings")
            return None

        resp = requests.post(url, headers=headers, json=payload, timeout=90)
        if resp.status_code != 200:
            log(f"Gemini STT error {resp.status_code}: {resp.text[:120]}")
            _last_error[0] = f"Gemini refused the request (HTTP {resp.status_code})."
            return None
        text = PROVIDER_ADAPTERS["gemini"].parse_response(resp.json())
        if not text:
            log("Gemini STT: no speech recognised")
            _last_error[0] = "No speech recognised - try speaking a little louder."
            return None
        log(f">> {text}")
        return text
    except Exception as e:
        log(f"Gemini STT error: {e}")
        _last_error[0] = f"Gemini engine failed: {e}"
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
        _last_error[0] = f"Groq engine failed: {e}"
        return None
    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass


def _gemini_lite_prompt(text):
    """Send transcribed text through Gemini Flash Lite to get a prompt. Returns prompted text or None."""
    global _last_stt
    url, headers = _gemini_endpoint(GEMINI_LITE_MODEL)
    if not url:
        log("Gemini: no credential and no API key — open Settings")
        return None
    _last_stt = text
    log("Converting to prompt (Gemini Lite)...")
    adapter = PROVIDER_ADAPTERS["gemini"]
    try:
        resp = requests.post(
            url, headers=headers,
            json=adapter.build_text_request(GEMINI_SYSTEM_PROMPT, text,
                                            GEMINI_THINKING_LEVEL, GEMINI_MEDIA_RESOLUTION),
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
    url, headers = _gemini_endpoint(GEMINI_FLASH_MODEL)
    if not url:
        log("Gemini: no credential and no API key — open Settings")
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
            url, headers=headers,
            json=adapter.build_audio_request(GEMINI_SYSTEM_PROMPT, audio_b64,
                                             GEMINI_THINKING_LEVEL, GEMINI_MEDIA_RESOLUTION),
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


VK_V = 0x56


def _send_ctrl_v():
    """Ctrl+V through SendInput with real virtual-key codes (see _send_ctrl_c)."""
    def _ev(vk, up):
        i = INPUT()
        i.type = INPUT_KEYBOARD
        i.iu.ki.wVk = vk
        i.iu.ki.wScan = user32.MapVirtualKeyW(vk, 0)
        i.iu.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
        return i

    seq = (_ev(VK_CONTROL, False), _ev(VK_V, False),
           _ev(VK_V, True),        _ev(VK_CONTROL, True))
    arr = (INPUT * len(seq))(*seq)
    user32.SendInput(len(seq), ctypes.byref(arr), ctypes.sizeof(INPUT))


def _paste_text(text):
    """Insert via the clipboard, restoring whatever was there before.

    Some applications ignore synthetic per-character key events, and for long
    transcripts typing one character at a time is also slow. Pasting solves
    both, at the cost of briefly touching the clipboard.
    """
    old = _clipboard_get_text()
    _clipboard_set_text(text)
    time.sleep(0.06)
    _send_ctrl_v()
    time.sleep(0.25)          # let the target app read the clipboard first
    _clipboard_set_text(old if old is not None else "")


def type_text(text):
    if not text:
        return
    keys = HOTKEY.split('+')
    while any(_phys_down(k) for k in keys):
        time.sleep(0.05)
    time.sleep(0.3)
    for k in ['left windows', 'right windows', 'alt', 'ctrl', 'shift']:
        try:
            keyboard.release(k)
        except Exception:
            pass

    # "auto" types short text (no clipboard involvement) but pastes long text,
    # where per-character SendInput would take seconds.
    mode = INJECT_MODE
    if mode == "auto":
        mode = "paste" if len(text) > 220 else "type"
    if mode == "paste":
        try:
            _paste_text(text)
            log(f"Pasted OK ({len(text)} chars)")
            return
        except Exception as e:
            log(f"Paste failed, typing instead: {e}")

    for char in text:
        _send_unicode_char(ord(char))
        time.sleep(0.001)
    log("Typed OK")


# ─── Text-to-Speech ───────────────────────────────────────────────────────────
# Reads the currently selected text (anywhere in Windows) aloud.
#
# Windows exposes no API to read another app's selection, so we use the standard
# trick: stash the clipboard, send Ctrl+C, read it, put the old content back.

CF_UNICODETEXT = 13
GMEM_MOVEABLE  = 0x0002
kernel32 = ctypes.windll.kernel32

user32.GetClipboardData.restype  = ctypes.c_void_p
kernel32.GlobalLock.restype      = ctypes.c_void_p
kernel32.GlobalLock.argtypes     = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes   = [ctypes.c_void_p]
kernel32.GlobalAlloc.restype     = ctypes.c_void_p
user32.SetClipboardData.restype  = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]

SENTINEL = "⁣__speakpaste_probe__⁣"   # invisible separators, never real text

_tts_cache_dir = os.path.join(tempfile.gettempdir(), "speakpaste-tts")
os.makedirs(_tts_cache_dir, exist_ok=True)

_tts_player     = None   # TtsPlayer singleton, created lazily
_tts_popup      = None   # popup Toplevel
_tts_busy       = False
_tts_last_text  = ""
_tts_progress   = (0, 0)   # (chunks ready, chunks total) for the popup


def _clipboard_open(retries=10):
    for _ in range(retries):
        if user32.OpenClipboard(0):
            return True
        time.sleep(0.02)
    return False


def _clipboard_get_text():
    if not _clipboard_open():
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.c_wchar_p(p).value
        finally:
            kernel32.GlobalUnlock(h)
    except Exception:
        return None
    finally:
        user32.CloseClipboard()


def _clipboard_set_text(text):
    if text is None:
        return
    if not _clipboard_open():
        return
    try:
        user32.EmptyClipboard()
        buf  = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buf)
        h    = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            return
        p = kernel32.GlobalLock(h)
        if not p:
            return
        ctypes.memmove(p, buf, size)
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
    except Exception:
        pass
    finally:
        user32.CloseClipboard()


VK_CONTROL = 0x11
VK_C       = 0x43


def _send_ctrl_c():
    """Ctrl+C through Win32 SendInput with real virtual-key codes.

    The `keyboard` library's press_and_release does not reach target windows
    here (verified against Notepad: the clipboard never changes), so the copy
    uses the same SendInput path that text injection already relies on.
    """
    def _ev(vk, up):
        i = INPUT()
        i.type = INPUT_KEYBOARD
        i.iu.ki.wVk = vk
        i.iu.ki.wScan = user32.MapVirtualKeyW(vk, 0)
        i.iu.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
        return i

    seq = (_ev(VK_CONTROL, False), _ev(VK_C, False),
           _ev(VK_C, True),        _ev(VK_CONTROL, True))
    arr = (INPUT * len(seq))(*seq)
    user32.SendInput(len(seq), ctypes.byref(arr), ctypes.sizeof(INPUT))


def get_selected_text(timeout=1.2):
    """Copy the active selection without clobbering the user's clipboard."""
    # Ctrl+C only means "copy" once the hotkey's own modifiers are physically up.
    deadline = time.time() + 3
    hk = [k for k in TTS_HOTKEY.split('+') if k]
    while time.time() < deadline and any(_phys_down(k) for k in hk):
        time.sleep(0.05)
    for k in ['left windows', 'right windows', 'alt', 'ctrl', 'shift']:
        try:
            keyboard.release(k)
        except Exception:
            pass
    time.sleep(0.12)

    old = _clipboard_get_text()
    # A sentinel lets us tell "copied the same text again" from "nothing copied".
    # It must contain no NUL: the clipboard string would be truncated there.
    _clipboard_set_text(SENTINEL)
    try:
        _send_ctrl_c()
    except Exception as e:
        log(f"Ctrl+C failed: {e}")
        _clipboard_set_text(old if old is not None else "")
        return None

    deadline = time.time() + timeout
    got = None
    while time.time() < deadline:
        time.sleep(0.05)
        cur = _clipboard_get_text()
        if cur and cur != SENTINEL:
            got = cur
            break

    _clipboard_set_text(old if old is not None else "")
    log(f"Selection: {len(got or '')} chars"
        + ("" if got else " (Ctrl+C produced nothing)"))
    return (got or "").strip() or None


# ── Engines ───────────────────────────────────────────────────────────────────

def _tts_edge(text, voice):
    """Free Microsoft Edge voices. No API key. Returns path to an mp3."""
    import asyncio
    import edge_tts

    out = os.path.join(_tts_cache_dir, f"edge_{abs(hash((text, voice)))}.mp3")
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


def _vertex_access_token(cred_path):
    """Refresh-token / service-account -> access token (Vertex, never AI Studio)."""
    with open(cred_path, encoding='utf-8') as f:
        cred = json.load(f)

    if cred.get("type") == "authorized_user" or "refresh_token" in cred:
        body = urllib.parse.urlencode({
            "client_id":     cred["client_id"],
            "client_secret": cred["client_secret"],
            "refresh_token": cred["refresh_token"],
            "grant_type":    "refresh_token",
        }).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token",
                                     data=body, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"], cred.get("quota_project_id", "")

    # service_account: signed JWT -> token
    import base64 as _b64
    try:
        from cryptography.hazmat.primitives import hashes, serialization
        from cryptography.hazmat.primitives.asymmetric import padding
    except ImportError:
        raise RuntimeError("service-account creds need the 'cryptography' package; "
                           "use an authorized_user (ADC) json instead")

    def _seg(d):
        return _b64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=")

    now    = int(time.time())
    claim  = {"iss": cred["client_email"],
              "scope": "https://www.googleapis.com/auth/cloud-platform",
              "aud": "https://oauth2.googleapis.com/token",
              "iat": now, "exp": now + 3600}
    signing_input = _seg({"alg": "RS256", "typ": "JWT"}) + b"." + _seg(claim)
    key = serialization.load_pem_private_key(cred["private_key"].encode(), None)
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    jwt = signing_input + b"." + _b64.urlsafe_b64encode(sig).rstrip(b"=")

    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion":  jwt.decode(),
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token",
                                 data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"], cred.get("project_id", "")


def _tts_vertex(text, voice, model, style, lang):
    """Gemini-TTS. Returns path to a wav.

    Routed through the same endpoint resolver as every other Gemini call, so a
    custom base URL, Vertex, or a plain API key all work here without a second
    code path - only the request body below is TTS-specific.
    """
    url, headers = _gemini_endpoint(model)
    if not url:
        raise RuntimeError("No Gemini endpoint configured (Settings -> Prompt)")

    out = os.path.join(_tts_cache_dir,
                       f"vx_{abs(hash((text, voice, model, style)))}.wav")
    if os.path.exists(out) and os.path.getsize(out) > 0:
        return out

    prompt = f"{style}\n\n{text}" if style else text
    body = json.dumps({
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {
                "voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}},
                "languageCode": f"{lang}-IR" if lang == "fa" else "en-US",
            },
        },
    }).encode()
    # The preview TTS models intermittently answer 400/503 for input they
    # accept on the very next try, so a couple of retries beats failing a read.
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


def _edge_voice_for(lang):
    voice = TTS_EDGE_VOICE
    # Follow the keyboard layout when the voice does not match the language.
    if LANG_MODE == "keyboard" and lang == "en" and voice.startswith("fa-"):
        voice = "en-US-AndrewNeural"
    return voice


def synthesize_tts(text):
    """text -> (float32 mono samples, samplerate). Engine per settings."""
    lang = active_language() if LANG_MODE == "keyboard" else LANGUAGE
    if TTS_ENGINE == "vertex":
        try:
            path = _tts_vertex(text, TTS_VERTEX_VOICE, TTS_VERTEX_MODEL,
                               TTS_STYLE, lang)
        except Exception as e:
            # The preview TTS model rejects some perfectly valid Persian input
            # with a bare 400. Falling back keeps the read going rather than
            # dropping a chunk of the user's text on the floor.
            log(f"Vertex TTS failed, falling back to edge: {str(e)[:80]}")
            path = _tts_edge(text, _edge_voice_for(lang))
    else:
        path = _tts_edge(text, _edge_voice_for(lang))

    data, sr = sf.read(path, dtype="float32", always_2d=True)
    return data.mean(axis=1), sr


# ── Chunking for streamed playback ───────────────────────────────────────────
# Measured 2026-07-21 with edge-tts: Persian ~14-16 chars/s of speech, English
# ~20, and generating N seconds of audio costs roughly 0.8*N seconds. So a long
# text is cut into sentence-aligned pieces: a SHORT first piece (audio starts
# quickly) followed by long ones (the synthesizer keeps a lead over playback).

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


def _split_for_streaming(text, first_secs, chunk_secs):
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


# ── Playback (pitch-preserving speed, seek, pause) ────────────────────────────

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


class TtsPlayer:
    """Single-clip player with pause/resume, seek and live pitch-safe speed."""

    def __init__(self):
        self._lock   = threading.Lock()
        self._base   = None      # as decoded (concatenated)
        self._parts  = []        # per-chunk decoded audio, for re-stretching
        self._bounds = []        # sample offset of each chunk inside _audio
        self._audio  = None      # speed-adjusted
        self._sr     = 24000
        self._pos    = 0
        self._speed  = 1.0
        self._stream = None
        self.playing = False
        self.ended   = False
        self.ended_at = None
        self.complete = True     # False while more chunks are still coming
        self.buffering = False   # ran dry but the text is not finished
        self.cancelled = False   # user stopped: producers should give up

    # -- internals
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
                    # Out of audio but more is being synthesized: hold the
                    # stream open and play silence until the next chunk lands.
                    self.buffering = True
            else:
                self._pos += frames
                self.buffering = False

    def _ensure_stream(self):
        if self._stream is not None:
            return
        self._stream = sd.OutputStream(samplerate=self._sr, channels=1,
                                       dtype="float32", callback=self._callback,
                                       blocksize=1024)
        self._stream.start()

    def _rebuild(self, keep_fraction=None):
        # Each chunk is stretched on its own and the results concatenated, so
        # appending a new chunk never re-processes what is already playing.
        stretched = [_time_stretch(p, self._speed, self._sr) for p in self._parts]
        self._audio = (np.concatenate(stretched) if stretched
                       else np.zeros(0, dtype=np.float32))
        # Sample offset where each chunk starts, for chunk-to-chunk navigation.
        self._bounds, off = [], 0
        for s in stretched:
            self._bounds.append(off)
            off += len(s)
        if keep_fraction is not None and len(self._audio):
            self._pos = min(len(self._audio) - 1,
                            max(0, int(keep_fraction * len(self._audio))))

    # -- public
    def load(self, samples, sr, complete=True):
        """Start a new clip. complete=False means more chunks will be appended."""
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
        """Add the next chunk of a streamed read; playback continues seamlessly."""
        with self._lock:
            if self._audio is None:
                return
            self._parts.append(samples)
            self._base = np.concatenate([self._base, samples])
            self._bounds.append(len(self._audio))
            self._audio = np.concatenate(
                [self._audio, _time_stretch(samples, self._speed, self._sr)])
            self.buffering = False
            if self.ended:          # drained before this chunk arrived
                self.ended = False
                self.ended_at = None
                self.playing = True

    def finish(self):
        """No more chunks are coming."""
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
            # Stop paying to synthesize text the user has abandoned.
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
            self._pos = min(len(self._audio) - 1,
                            max(0, int(fraction * len(self._audio))))
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

    # -- chunk navigation
    def chunk_state(self):
        """(current chunk number, total chunks, boundary fractions 0..1)."""
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
        """Skip to another chunk. Going back mid-chunk restarts the current one
        first, the way every media player behaves."""
        with self._lock:
            if not self._bounds or self._audio is None:
                return
            idx = 0
            for i, b in enumerate(self._bounds):
                if self._pos >= b:
                    idx = i
            if delta < 0 and (self._pos - self._bounds[idx]) > 3 * self._sr:
                target = idx          # >3s in: restart this chunk instead
            else:
                target = max(0, min(len(self._bounds) - 1, idx + delta))
            self._pos = self._bounds[target]
            if self.ended:
                self.ended = False
                self.ended_at = None
                self.playing = True

    def remaining_seconds(self):
        """Audio still buffered ahead of the play head - the synthesizer's lead."""
        with self._lock:
            if self._audio is None:
                return 0.0
            return max(0, len(self._audio) - self._pos) / self._sr


def _close_tts_popup_if_idle():
    """Close the popup if it is only showing a message (nothing is playing)."""
    if _tts_popup and not (_tts_player and _tts_player.playing):
        try:
            _ui_run(_tts_popup["win"].destroy)
        except Exception:
            pass
        globals()["_tts_popup"] = None


def _get_player():
    global _tts_player
    if _tts_player is None:
        _tts_player = TtsPlayer()
    return _tts_player


def speak_text(text):
    """Synthesize and play `text`.

    Long text is streamed: the first (short) chunk starts playing as soon as it
    is ready and the remaining chunks are synthesized in the background while
    earlier ones play, so there is no silent wait for the whole text and no gap
    between chunks.
    """
    global _tts_busy, _tts_last_text, _tts_progress
    if _tts_busy:
        log("TTS busy, ignoring")
        return
    _tts_busy = True
    _tts_last_text = text
    player = _get_player()
    try:
        if tray_icon:
            tray_icon.icon = create_icon("recording")

        parts = _split_for_streaming(text, TTS_FIRST_CHUNK_SECS, TTS_CHUNK_SECS)
        _tts_progress = (0, len(parts))
        if TTS_POPUP:
            open_tts_popup(status=f"Generating 1/{len(parts)}...")

        t0 = time.time()
        samples, sr = synthesize_tts(parts[0])
        first_gen = time.time() - t0
        player.load(samples, sr, complete=(len(parts) == 1))
        player.set_speed(TTS_SPEED)
        _tts_progress = (1, len(parts))
        log(f"TTS {TTS_ENGINE} chunk 1/{len(parts)} "
            f"({len(parts[0])} chars) in {first_gen:.1f}s - playing")
        if TTS_POPUP:
            open_tts_popup(status="")

        _history.appendleft({
            "time":   datetime.datetime.now().strftime("%H:%M:%S"),
            "engine": f"tts:{TTS_ENGINE}",
            "stt":    None,
            "output": text,
        })
        save_history()

        if len(parts) > 1:
            threading.Thread(target=_stream_rest, args=(parts, player),
                             daemon=True).start()
            return          # _stream_rest owns _tts_busy from here
    except Exception as e:
        log(f"TTS error: {e}")
        if TTS_POPUP:
            open_tts_popup(status=f"Error: {e}")
    _tts_busy = False
    if tray_icon:
        tray_icon.icon = create_icon("idle")


TTS_LOOKAHEAD = 3      # chunks synthesized concurrently
TTS_MAX_LEAD  = 45.0   # seconds of ready audio to keep ahead of the play head


def _stream_rest(parts, player):
    """Synthesize chunks 2..N while earlier ones play.

    Several chunks are generated CONCURRENTLY - generating a chunk can take
    longer than it takes to speak it, so a strictly sequential producer falls
    behind and the player stalls between chunks. Results are still appended in
    order, so playback stays correct.
    """
    global _tts_busy, _tts_progress
    from concurrent.futures import ThreadPoolExecutor

    total = len(parts)
    ready, order_lock, next_idx = {}, threading.Lock(), [1]
    pending  = [0.0]       # seconds synthesized but not yet appended
    inflight = [0.0]       # estimated seconds currently being synthesized
    cancelled = threading.Event()

    def _flush_ready():
        """Append every consecutive finished chunk, keeping playback in order."""
        while next_idx[0] in ready:
            samples, sr = ready.pop(next_idx[0])
            if samples is not None:
                pending[0] = max(0.0, pending[0] - len(samples) / sr)
                player.append(samples)
            next_idx[0] += 1
            _tts_progress = (next_idx[0], total)

    def _lead():
        """Audio already paid for and not yet heard: appended + finished-but-
        queued + currently being generated. Counting in-flight work is what
        keeps N concurrent workers from all clearing the gate and overshooting.
        """
        with order_lock:
            return player.remaining_seconds() + pending[0] + inflight[0]

    def _work(i):
        # Stay ahead of playback, but not further than TTS_MAX_LEAD: audio the
        # user may never reach still costs time and (on Vertex) money.
        est = len(parts[i]) / _chars_per_sec(parts[i])
        while (not cancelled.is_set() and not player.cancelled
               and _lead() > TTS_MAX_LEAD):
            time.sleep(0.4)
        if cancelled.is_set() or player.cancelled:
            return
        with order_lock:
            inflight[0] += est
        t0 = time.time()
        try:
            samples, sr = synthesize_tts(parts[i])
        except Exception as e:
            log(f"TTS chunk {i + 1}/{total} failed, skipping: {str(e)[:70]}")
            samples, sr = None, 24000
        with order_lock:
            inflight[0] = max(0.0, inflight[0] - est)
            ready[i] = (samples, sr)
            if samples is not None:
                pending[0] += len(samples) / sr
            _flush_ready()
        if samples is not None:
            log(f"TTS chunk {i + 1}/{total} in {time.time() - t0:.1f}s "
                f"(buffer ahead: {_lead():.0f}s)")

    try:
        with ThreadPoolExecutor(max_workers=TTS_LOOKAHEAD) as pool:
            futures = [pool.submit(_work, i) for i in range(1, total)]
            while any(not f.done() for f in futures):
                if player.cancelled or (player.ended and player.complete):
                    cancelled.set()
                    log("TTS stream cancelled - remaining chunks skipped")
                    break
                time.sleep(0.2)
    except Exception as e:
        log(f"TTS stream error: {e}")
    finally:
        with order_lock:
            _flush_ready()
        player.finish()
        _tts_busy = False
        if tray_icon:
            tray_icon.icon = create_icon("idle")


# ── Popup control widget ─────────────────────────────────────────────────────

def open_tts_popup(status=""):
    """Small always-on-top player widget; self-closes after playback."""

    def _build():
        global _tts_popup

        if _tts_popup and _tts_popup["win"].winfo_exists():
            if status:
                _tts_popup["status"].config(text=status)
            elif _tts_popup["status"].cget("text").startswith(("Generating", "Error")):
                _tts_popup["status"].config(text="")
            _tts_popup["win"].lift()
            return

        BG, CARD, DIM, FG   = "#181818", "#202020", "#7a7a7a", "#e8e8e8"
        TRACK, ACCENT       = "#3a3a3a", "#4c9aff"
        W                   = 348
        SEEK_W, SPEED_W     = 328, 56
        BAR_H               = 6    # thin timeline; height is auto-fit to content below

        win = tk.Toplevel(_ui_root)
        win.withdraw()
        win.overrideredirect(True)          # no title bar - it dwarfed the content
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)       # fade in; avoids the open-flash
        win.configure(bg=TRACK)             # 1px hairline border

        card = tk.Frame(win, bg=BG)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        pad = tk.Frame(card, bg=BG, padx=9, pady=3)
        pad.pack(fill="both", expand=True)

        # ── single row: transport, time/part, speed, status, close ───────────
        top = tk.Frame(pad, bg=BG)
        top.pack(fill="x")

        def _icon_btn(parent, glyph, size=11):
            b = tk.Label(parent, text=glyph, bg=BG, fg=FG,
                         font=("Segoe UI Symbol", size), cursor="hand2")
            b.bind("<Enter>", lambda e: b.config(fg="#ffffff"))
            b.bind("<Leave>", lambda e: b.config(fg=FG))
            return b

        prev_btn = _icon_btn(top, "◀◀", 8)
        prev_btn.pack(side="left", padx=(0, 5))
        play_btn = _icon_btn(top, "❚❚", 10)
        play_btn.pack(side="left")
        next_btn = _icon_btn(top, "▶▶", 8)
        next_btn.pack(side="left", padx=(5, 0))
        stop_btn = _icon_btn(top, "■", 9)
        stop_btn.pack(side="left", padx=(7, 9))

        time_lbl = tk.Label(top, text="0:00 / 0:00", bg=BG, fg=DIM,
                            font=("Segoe UI", 8))
        time_lbl.pack(side="left")

        chunk_lbl = tk.Label(top, text="", bg=BG, fg="#5f5f5f",
                             font=("Segoe UI", 8))
        chunk_lbl.pack(side="left", padx=(6, 0))

        # Packed side="right" in this order so the visual left-to-right result
        # is: speed slider, speed value, status, close (each pack claims the
        # next slice from the right edge, so first-packed ends up rightmost).
        close_btn = tk.Label(top, text="✕", bg=BG, fg="#5a5a5a",
                             font=("Segoe UI", 9), cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg="#ff6b6b"))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg="#5a5a5a"))

        status_lbl = tk.Label(top, text=status, bg=BG, fg="#cca700",
                              font=("Segoe UI", 8))
        status_lbl.pack(side="right", padx=(0, 8))

        speed_val = tk.Label(top, text=f"{TTS_SPEED:.2f}x", bg=BG, fg=DIM,
                             font=("Segoe UI", 8), width=5, anchor="e")
        speed_val.pack(side="right")

        # ── canvas sliders: thin, rounded, no chunky tk.Scale chrome ─────────
        def _bar(parent, width, height=BAR_H):
            return tk.Canvas(parent, width=width, height=height, bg=BG,
                             highlightthickness=0, bd=0, cursor="hand2")

        def _draw(cv, width, frac, active=True, marks=(), height=BAR_H):
            cv.delete("all")
            y, x0, x1 = height // 2, 5, width - 5
            r = 3 if height >= 8 else 2
            cv.create_line(x0, y, x1, y, fill=TRACK, width=r, capstyle="round")
            x = x0 + (x1 - x0) * max(0.0, min(1.0, frac))
            col = ACCENT if active else "#5a5a5a"
            if x > x0:
                cv.create_line(x0, y, x, y, fill=col, width=r, capstyle="round")
            # chunk boundaries, so the split is visible rather than guessed at
            for m in marks:
                if m <= 0:
                    continue
                mx = x0 + (x1 - x0) * m
                cv.create_line(mx, y - r - 1, mx, y + r + 1, fill="#7a7a7a", width=1)
            cv.create_oval(x - r - 1, y - r - 1, x + r + 1, y + r + 1, fill=col, outline="")

        speed_cv = _bar(top, SPEED_W)
        speed_cv.pack(side="right", padx=(0, 6))

        seek_cv = _bar(pad, SEEK_W)
        seek_cv.pack(fill="x", pady=(2, 0))

        state = {"seeking": False, "closed": False, "frac": 0.0}

        def _fmt(s):
            return f"{int(s // 60)}:{int(s % 60):02d}"

        def _frac_at(event, width):
            return max(0.0, min(1.0, (event.x - 5) / max(1, width - 10)))

        # seek: drag freely, commit on release
        def _seek_drag(e):
            state["seeking"] = True
            state["frac"] = _frac_at(e, SEEK_W)
            _draw(seek_cv, SEEK_W, state["frac"])
            _, dur = _get_player().position()
            if dur:
                time_lbl.config(text=f"{_fmt(state['frac'] * dur)} / {_fmt(dur)}")

        def _seek_commit(e):
            _get_player().seek(_frac_at(e, SEEK_W))
            state["seeking"] = False

        seek_cv.bind("<Button-1>", _seek_drag)
        seek_cv.bind("<B1-Motion>", _seek_drag)
        seek_cv.bind("<ButtonRelease-1>", _seek_commit)

        # speed: 0.5x .. 2.0x, applied live
        def _speed_set(e):
            global TTS_SPEED
            v = round(0.5 + _frac_at(e, SPEED_W) * 1.5, 2)
            TTS_SPEED = v
            speed_val.config(text=f"{v:.2f}x")
            _draw(speed_cv, SPEED_W, (v - 0.5) / 1.5)
            threading.Thread(target=lambda: _get_player().set_speed(v),
                             daemon=True).start()

        speed_cv.bind("<Button-1>", _speed_set)
        speed_cv.bind("<B1-Motion>", _speed_set)

        _draw(seek_cv, SEEK_W, 0.0)
        _draw(speed_cv, SPEED_W, (TTS_SPEED - 0.5) / 1.5)

        # ── drag the whole widget by its background ──────────────────────────
        drag = {"x": 0, "y": 0}

        def _drag_start(e):
            drag["x"], drag["y"] = e.x_root - win.winfo_x(), e.y_root - win.winfo_y()

        def _drag_move(e):
            win.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

        for w_ in (card, pad, top, time_lbl, status_lbl):
            w_.bind("<Button-1>", _drag_start)
            w_.bind("<B1-Motion>", _drag_move)

        def _close():
            global _tts_popup
            state["closed"] = True
            _tts_popup = None
            try:
                _get_player().stop()
            except Exception:
                pass
            win.destroy()

        def _toggle(_e=None):
            _get_player().toggle()
            play_btn.config(text="❚❚" if _get_player().playing else "▶")

        def _stop(_e=None):
            _get_player().stop()
            play_btn.config(text="▶")

        def _prev(_e=None):
            _get_player().jump_chunk(-1)

        def _next(_e=None):
            _get_player().jump_chunk(+1)

        play_btn.bind("<Button-1>", _toggle)
        stop_btn.bind("<Button-1>", _stop)
        prev_btn.bind("<Button-1>", _prev)
        next_btn.bind("<Button-1>", _next)
        close_btn.bind("<Button-1>", lambda e: _close())
        win.bind("<Escape>", lambda e: _close())

        def _tick():
            if state["closed"] or not win.winfo_exists():
                return
            p = _get_player()
            cur, dur = p.position()
            idx, total, marks = p.chunk_state()
            if not state["seeking"]:
                time_lbl.config(text=f"{_fmt(cur)} / {_fmt(dur)}"
                                     + ("+" if not p.complete else ""))
                _draw(seek_cv, SEEK_W, (cur / dur) if dur else 0.0, p.playing,
                      marks if total > 1 else ())
            play_btn.config(text="❚❚" if p.playing else "▶")
            # chunk counter + grey out the arrows at the ends
            chunk_lbl.config(text=f"{idx}/{total}" if total > 1 else "")
            prev_btn.config(fg=FG if total > 1 else "#3a3a3a")
            next_btn.config(fg=FG if (total > 1 and idx < total) else "#3a3a3a")
            # while streaming, show which chunk is being synthesized
            done, total = _tts_progress
            if not p.complete and total > 1:
                status_lbl.config(text=f"{done}/{total}"
                                       + ("  buffering" if p.buffering else ""))
            elif status_lbl.cget("text") and "/" in status_lbl.cget("text"):
                status_lbl.config(text="")
            if (TTS_POPUP_AUTOCLOSE and p.ended and p.ended_at
                    and time.time() - p.ended_at > TTS_POPUP_AUTOCLOSE):
                _close()
                return
            win.after(200, _tick)

        # Size and final position BEFORE mapping, so it never jumps. Height is
        # measured from the actual (now very compact) content rather than a
        # hand-guessed constant, so it stays minimal if fonts/padding change.
        win.update_idletasks()
        H = pad.winfo_reqheight() + 2   # +2 for the 1px hairline border top/bottom
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{W}x{H}+{sw - W - 24}+{sh - H - 72}")
        win.update_idletasks()
        win.deiconify()

        def _fade(a=0.0):
            if state["closed"] or not win.winfo_exists():
                return
            a = min(0.97, a + 0.13)
            win.attributes("-alpha", a)
            if a < 0.97:
                win.after(16, lambda: _fade(a))

        _fade()
        _tts_popup = {"win": win, "status": status_lbl}
        _tick()

    _ui_run(_build)


def on_tts_hotkey():
    """Read the current selection aloud (fired on hotkey release)."""
    if not TTS_ENABLED:
        return
    text = get_selected_text()
    if not text:
        # Silence here reads as "the hotkey is broken" - say what happened.
        log("TTS: no text selected")
        if TTS_POPUP:
            open_tts_popup(status="No text selected")
            threading.Timer(2.5, _close_tts_popup_if_idle).start()
        return
    if len(text) > 8000:
        text = text[:8000]
    speak_text(text)


def speak_clipboard(icon=None, item=None):
    """Tray action: read whatever is on the clipboard."""
    text = (_clipboard_get_text() or "").strip()
    if text:
        threading.Thread(target=speak_text, args=(text,), daemon=True).start()
    else:
        log("TTS: clipboard empty")


# ─── Hotkey Handlers ──────────────────────────────────────────────────────────

def on_hotkey_press():
    global _session_lang
    _log_last_t[0] = None  # reset elapsed timer at start of each invocation
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
        elif STT_ENGINE == "gemini":
            raw = _transcribe_gemini(path) if path else None
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
        save_history()
        type_text(text)
    else:
        # Silence after a hotkey press is indistinguishable from a broken
        # hotkey, so say what went wrong. _last_error is set by the engine.
        show_toast(_last_error[0] or "Nothing was transcribed - try speaking again.")
    _last_error[0] = None
    if tray_icon:
        tray_icon.icon = create_icon("idle")


def keyboard_listener():
    global running, is_hotkey_active, is_tts_hotkey_active
    stt_blocked = False   # set after a TTS trigger, cleared once STT keys are up
    while running:
        try:
            # The TTS combo may share modifiers with the STT one, so check it
            # first and keep STT muted while it is held.
            tts_keys = TTS_HOTKEY.split('+') if (TTS_ENABLED and TTS_HOTKEY) else []
            tts_down = bool(tts_keys) and all(_phys_down(k) for k in tts_keys)

            if tts_down and not is_tts_hotkey_active:
                is_tts_hotkey_active = True
                log(f"TTS hotkey down ({TTS_HOTKEY})")
                if is_hotkey_active:      # STT started first: cancel it silently
                    is_hotkey_active = False
                    _cancel_recording()
            elif not tts_down and is_tts_hotkey_active:
                is_tts_hotkey_active = False
                log("TTS hotkey released - reading selection")
                # Letting go of ctrl first leaves win+alt down, which would look
                # like a fresh voice-typing press - block STT until it is released.
                stt_blocked = True
                threading.Thread(target=on_tts_hotkey, daemon=True).start()

            if is_tts_hotkey_active:
                time.sleep(0.05)
                continue

            keys = HOTKEY.split('+')
            pressed = all(_phys_down(k) for k in keys)
            if stt_blocked:
                if not pressed:
                    stt_blocked = False
                time.sleep(0.05)
                continue

            if pressed and not is_hotkey_active:
                is_hotkey_active = True
                on_hotkey_press()
            elif not pressed and is_hotkey_active:
                is_hotkey_active = False
                threading.Thread(target=on_hotkey_release, daemon=True).start()
        except Exception:
            pass
        time.sleep(0.05)


# ─── Hotkey capture & validation ──────────────────────────────────────────────

# Combos Windows claims before any application hook sees them. Learned the hard
# way: win+alt+x was configured, looked correct, and simply never fired because
# the shell consumes Win+X for the Quick Link menu.
_WINDOWS_RESERVED = {
    "win+l": "Windows locks the screen",
    "win+x": "Windows opens the Quick Link menu",
    "win+d": "Windows shows the desktop",
    "win+e": "Windows opens File Explorer",
    "win+r": "Windows opens the Run dialog",
    "win+s": "Windows opens Search",
    "win+i": "Windows opens Settings",
    "win+a": "Windows opens the Action Center",
    "win+v": "Windows opens clipboard history",
    "win+g": "Windows opens Game Bar",
    "win+h": "Windows starts its own voice typing",
    "win+tab": "Windows opens Task View",
    "ctrl+alt+delete": "reserved by Windows security",
    "alt+tab": "Windows switches windows",
    "alt+f4": "Windows closes the active window",
}

_MODIFIERS = ("ctrl", "win", "alt", "shift")


def validate_hotkey(combo):
    """(ok, message). Rejects empty, modifier-less and Windows-reserved combos."""
    combo = (combo or "").strip().lower()
    if not combo:
        return False, "Hotkey is empty."
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        return False, "Hotkey is empty."

    mods = [p for p in parts if p in _MODIFIERS]
    if not mods:
        return False, "Needs at least one modifier (ctrl, win, alt or shift)."

    for k in parts:
        try:
            keyboard.key_to_scan_codes(k)
        except Exception:
            return False, f"'{k}' is not a key name the app understands."

    key = "+".join(sorted(mods, key=_MODIFIERS.index)
                   + sorted(p for p in parts if p not in _MODIFIERS))
    for reserved, why in _WINDOWS_RESERVED.items():
        r_parts = reserved.split("+")
        if sorted(r_parts) == sorted(parts):
            return False, f"{reserved} will not reach the app - {why}."
        # A Win+letter combo is swallowed even with extra modifiers held.
        if ("win" in parts and len(r_parts) == 2 and r_parts[0] == "win"
                and r_parts[1] in parts):
            return False, (f"Windows takes Win+{r_parts[1]} ({why}), so this "
                           f"combo may never fire.")
    return True, key


def capture_hotkey(parent, on_done):
    """Modal 'press the combination' capture. Returns the combo via on_done."""
    dlg = tk.Toplevel(parent)
    dlg.withdraw()
    dlg.title("Press a key combination")
    dlg.configure(bg="#1e1e1e")
    dlg.resizable(False, False)
    dlg.transient(parent)

    tk.Label(dlg, text="Hold the combination you want, then release.",
             bg="#1e1e1e", fg="#cccccc", font=("Segoe UI", 9),
             padx=24, pady=(16)).pack()
    shown = tk.Label(dlg, text="…", bg="#1e1e1e", fg="#ffffff",
                     font=("Segoe UI", 14, "bold"), pady=8)
    shown.pack()
    hint = tk.Label(dlg, text="Esc to cancel", bg="#1e1e1e", fg="#666666",
                    font=("Segoe UI", 8), pady=(0))
    hint.pack(pady=(0, 14))

    state = {"held": [], "done": False}

    def _finish(combo):
        if state["done"]:
            return
        state["done"] = True
        try:
            dlg.destroy()
        except Exception:
            pass
        if combo:
            on_done(combo)

    def _on_press(e):
        name = {"control_l": "ctrl", "control_r": "ctrl", "alt_l": "alt",
                "alt_r": "alt", "shift_l": "shift", "shift_r": "shift",
                "super_l": "win", "super_r": "win"}.get(e.keysym.lower(),
                                                        e.keysym.lower())
        if name == "escape":
            _finish(None)
            return "break"
        if name not in state["held"]:
            state["held"].append(name)
        ordered = ([m for m in _MODIFIERS if m in state["held"]]
                   + [k for k in state["held"] if k not in _MODIFIERS])
        shown.config(text="+".join(ordered))
        return "break"

    def _on_release(_e):
        if state["held"] and not state["done"]:
            ordered = ([m for m in _MODIFIERS if m in state["held"]]
                       + [k for k in state["held"] if k not in _MODIFIERS])
            _finish("+".join(ordered))
        return "break"

    dlg.bind("<KeyPress>", _on_press)
    dlg.bind("<KeyRelease>", _on_release)
    dlg.protocol("WM_DELETE_WINDOW", lambda: _finish(None))

    dlg.update_idletasks()
    w, h = max(300, dlg.winfo_reqwidth()), dlg.winfo_reqheight()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    dlg.geometry(f"{w}x{h}+{px + (pw - w) // 2}+{py + (ph - h) // 3}")
    dlg.deiconify()
    dlg.grab_set()
    dlg.focus_force()


# ─── Toast notification ───────────────────────────────────────────────────────

_toast_win = None


def show_toast(message, kind="error", seconds=4):
    """Brief borderless message near the tray. A failure that only writes to a
    log line is indistinguishable from the hotkey not working at all."""
    if not NOTIFY_ERRORS:
        return

    def _build():
        global _toast_win
        if _toast_win is not None:
            try:
                _toast_win.destroy()
            except Exception:
                pass
            _toast_win = None

        accent = {"error": "#e05252", "info": "#4c9aff", "ok": "#4caf50"}.get(kind, "#4c9aff")
        win = tk.Toplevel(_ui_root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        win.configure(bg="#3a3a3a")

        card = tk.Frame(win, bg="#181818")
        card.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Frame(card, bg=accent, width=3).pack(side="left", fill="y")
        body = tk.Frame(card, bg="#181818", padx=12, pady=9)
        body.pack(side="left", fill="both", expand=True)
        tk.Label(body, text="SpeakPaste", bg="#181818", fg="#7a7a7a",
                 font=("Segoe UI", 7)).pack(anchor="w")
        tk.Label(body, text=message, bg="#181818", fg="#e8e8e8",
                 font=("Segoe UI", 9), wraplength=280, justify="left"
                 ).pack(anchor="w", pady=(1, 0))

        win.update_idletasks()
        w = max(240, min(320, win.winfo_reqwidth()))
        h = win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 72}")
        win.deiconify()

        def _fade_in(a=0.0):
            if not win.winfo_exists():
                return
            a = min(0.96, a + 0.16)
            win.attributes("-alpha", a)
            if a < 0.96:
                win.after(16, lambda: _fade_in(a))

        def _dismiss():
            global _toast_win
            if _toast_win is win:
                _toast_win = None
            try:
                win.destroy()
            except Exception:
                pass

        card.bind("<Button-1>", lambda e: _dismiss())
        for child in body.winfo_children():
            child.bind("<Button-1>", lambda e: _dismiss())
        win.after(int(seconds * 1000), _dismiss)
        _fade_in()
        _toast_win = win

    _ui_run(_build)


# ─── Tk UI thread (single root) ───────────────────────────────────────────────

def _ui_run(fn):
    """Queue a zero-arg callable to run on the Tk UI thread. Safe from any thread."""
    _ui_queue.put(fn)


def _ui_thread_main():
    """Owns the one and only Tk root and its mainloop for the whole process."""
    global _ui_root
    _ui_root = tk.Tk()
    _ui_root.withdraw()  # invisible master; real windows are Toplevels

    def _pump():
        try:
            while True:
                fn = _ui_queue.get_nowait()
                try:
                    fn()
                except Exception as e:
                    log(f"UI error: {e}")
        except Empty:
            pass
        _ui_root.after(80, _pump)

    _ui_root.after(80, _pump)
    _ui_root.mainloop()


# ─── Settings Window ──────────────────────────────────────────────────────────

def _apply_settings(new_cfg):
    global STT_ENGINE, PROMPT_MODE, HOTKEY, LANGUAGE, MIC_MODE, GROQ_API_KEY, MODEL, WS_PORT, CHECK_UPDATES
    global GOOGLE_CLOUD_API_KEY
    global GEMINI_API_KEY, GEMINI_SYSTEM_PROMPT, LANG_MODE, GEMINI_THINKING_LEVEL, GEMINI_MEDIA_RESOLUTION
    global GEMINI_BASE_URL, GEMINI_AUTH_TOKEN
    global GEMINI_STT_MODEL, INJECT_MODE, NOTIFY_ERRORS
    global TTS_ENABLED, TTS_HOTKEY, TTS_ENGINE, TTS_EDGE_VOICE, TTS_VERTEX_VOICE
    global TTS_VERTEX_MODEL, TTS_VERTEX_CRED, TTS_VERTEX_PROJECT, TTS_STYLE
    global TTS_SPEED, TTS_POPUP, TTS_POPUP_AUTOCLOSE, TTS_CHUNK_SECS, TTS_FIRST_CHUNK_SECS

    old_mic    = MIC_MODE
    old_engine = STT_ENGINE

    STT_ENGINE              = new_cfg["stt_engine"]
    PROMPT_MODE             = new_cfg["prompt_mode"]
    HOTKEY                  = new_cfg["hotkey"]
    LANGUAGE                = new_cfg["language"]
    GROQ_API_KEY            = new_cfg["groq_api_key"]
    MODEL                   = new_cfg["model"]
    GOOGLE_CLOUD_API_KEY    = new_cfg["google_cloud_api_key"]
    WS_PORT                 = new_cfg["ws_port"]
    MIC_MODE                = new_cfg["mic_mode"]
    CHECK_UPDATES           = new_cfg["check_updates"]
    GEMINI_API_KEY          = new_cfg.get("gemini_api_key", "")
    GEMINI_BASE_URL         = (os.environ.get("SPEAKPASTE_GEMINI_BASE_URL")
                               or new_cfg.get("gemini_base_url", "")).rstrip("/")
    GEMINI_AUTH_TOKEN       = (os.environ.get("SPEAKPASTE_GEMINI_TOKEN")
                               or new_cfg.get("gemini_auth_token", ""))
    GEMINI_SYSTEM_PROMPT    = new_cfg.get("gemini_system_prompt", GEMINI_DEFAULT_SYSTEM_PROMPT)
    LANG_MODE               = new_cfg.get("lang_mode", "fixed")
    GEMINI_THINKING_LEVEL   = new_cfg.get("gemini_thinking_level", "LOW")
    GEMINI_MEDIA_RESOLUTION = new_cfg.get("gemini_media_resolution", "LOW")
    GEMINI_STT_MODEL        = new_cfg.get("gemini_stt_model", GEMINI_STT_DEFAULT_MODEL)
    INJECT_MODE             = new_cfg.get("inject_mode", "auto")
    NOTIFY_ERRORS           = new_cfg.get("notify_errors", True)

    TTS_ENABLED         = new_cfg.get("tts_enabled", True)
    TTS_HOTKEY          = new_cfg.get("tts_hotkey", "win+shift")
    TTS_ENGINE          = new_cfg.get("tts_engine", "edge")
    TTS_EDGE_VOICE      = new_cfg.get("tts_edge_voice", "fa-IR-FaridNeural")
    TTS_VERTEX_VOICE    = new_cfg.get("tts_vertex_voice", "Zubenelgenubi")
    TTS_VERTEX_MODEL    = new_cfg.get("tts_vertex_model", "gemini-3.1-flash-tts-preview")
    TTS_VERTEX_CRED     = new_cfg.get("tts_vertex_cred", "")
    TTS_VERTEX_PROJECT  = new_cfg.get("tts_vertex_project", "")
    TTS_STYLE           = new_cfg.get("tts_style", TTS_DEFAULT_STYLE)
    TTS_SPEED           = float(new_cfg.get("tts_speed", 1.0))
    TTS_POPUP           = new_cfg.get("tts_popup", True)
    TTS_POPUP_AUTOCLOSE = int(new_cfg.get("tts_popup_autoclose", 30))
    TTS_CHUNK_SECS       = float(new_cfg.get("tts_chunk_secs", 25))
    TTS_FIRST_CHUNK_SECS = float(new_cfg.get("tts_first_chunk_secs", 8))

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
    def _build():
        global _settings_window

        if _settings_window and _settings_window.winfo_exists():
            _settings_window.lift()
            _settings_window.focus_force()
            return

        win = tk.Toplevel(_ui_root)
        win.withdraw()  # hide until fully built — prevents layout flash
        win.title("SpeakPaste — Settings")
        win.resizable(False, True)
        win.configure(bg="#1e1e1e")

        # ── Tabs ─────────────────────────────────────────────────────────────
        # ttk on Windows ignores most colour options unless the theme is 'clam'.
        style = ttk.Style(win)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Dark.TNotebook", background="#1e1e1e", borderwidth=0,
                        tabmargins=(8, 6, 8, 0))
        style.configure("Dark.TNotebook.Tab", background="#252525", foreground="#999999",
                        padding=(16, 7), borderwidth=0, font=("Segoe UI", 9))
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", "#1e1e1e")],
                  foreground=[("selected", "#ffffff")],
                  expand=[("selected", (0, 0, 0, 2))])

        nb = ttk.Notebook(win, style="Dark.TNotebook")
        nb.pack(side="top", fill="both", expand=True)

        def _make_tab(title):
            """A scrollable dark page inside the notebook. Returns its body frame."""
            page = tk.Frame(nb, bg="#1e1e1e")
            nb.add(page, text=title)
            cv = tk.Canvas(page, bg="#1e1e1e", highlightthickness=0, bd=0)
            sb = tk.Scrollbar(page, orient="vertical", command=cv.yview)
            cv.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            cv.pack(side="left", fill="both", expand=True)
            inner = tk.Frame(cv, bg="#1e1e1e", padx=20, pady=10)
            iid = cv.create_window((0, 0), window=inner, anchor="nw")
            inner.bind("<Configure>",
                       lambda e: cv.configure(scrollregion=cv.bbox("all")))
            cv.bind("<Configure>", lambda e: cv.itemconfig(iid, width=e.width))
            cv.bind("<MouseWheel>",
                    lambda e: cv.yview_scroll(int(-1 * (e.delta / 120)), "units"))
            inner.bind("<MouseWheel>",
                       lambda e: cv.yview_scroll(int(-1 * (e.delta / 120)), "units"))
            return inner

        tab_stt = _make_tab("  Speech → Text  ")
        tab_tts = _make_tab("  Text → Speech  ")
        tab_gen = _make_tab("  General  ")

        # `body` stays the name the STT/prompt widgets below are built into.
        body = tab_stt

        lbl_style = {"bg": "#1e1e1e", "fg": "#cccccc", "font": ("Segoe UI", 9)}
        hdr_style = {"bg": "#1e1e1e", "fg": "#ffffff", "font": ("Segoe UI", 9, "bold")}
        ent_style = {"bg": "#2d2d2d", "fg": "#ffffff", "insertbackground": "#ffffff",
                     "relief": "flat", "font": ("Segoe UI", 9)}

        def section(text, parent=None):
            p = parent if parent is not None else body
            tk.Label(p, text=text, **hdr_style).pack(anchor="w", pady=(12, 2))
            ttk.Separator(p).pack(fill="x", pady=(0, 6))

        rb_cfg = dict(bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                      activebackground="#1e1e1e", activeforeground="#ffffff",
                      font=("Segoe UI", 9))

        # ── Transcription Engine ─────────────────────────────────────────────
        section("Transcription Engine")
        stt_var = tk.StringVar(value=STT_ENGINE)

        stt_section = tk.Frame(body, bg="#1e1e1e")
        stt_section.pack(fill="x")

        stt_radios = []
        for label, val in [
                ("Google  —  free, unofficial, no key",         "google"),
                ("Gemini  —  detects the language itself, no language setting", "gemini"),
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

        # Gemini STT sub-frame
        gstt_frame = tk.Frame(stt_extra, bg="#252525", padx=12, pady=6)
        row_gm = tk.Frame(gstt_frame, bg="#252525")
        row_gm.pack(fill="x", pady=2)
        tk.Label(row_gm, text="Model:", width=14, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        gstt_model_var = tk.StringVar(value=GEMINI_STT_MODEL)
        tk.Entry(row_gm, textvariable=gstt_model_var, width=34,
                 **{**ent_style, "bg": "#333333"}).pack(side="left")
        tk.Label(gstt_frame,
                 text=("The model detects the spoken language itself - the Language "
                       "setting and keyboard layout are ignored.\nEnglish words inside "
                       "Persian speech stay in English. Uses the Vertex credential from "
                       "the Text → Speech tab,\nfalling back to the Gemini API key below."),
                 justify="left",
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
            elif eng == "gemini":
                stt_extra.configure(bg="#252525")
                gstt_frame.pack(fill="x")
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
            tk.Radiobutton(body, text=label, variable=prompt_var, value=val,
                           **rb_cfg).pack(anchor="w")

        prompt_extra = tk.Frame(body, bg="#1e1e1e")
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

        # Optional: send every Gemini call to a compatible proxy instead of Google.
        row_gurl = tk.Frame(gemini_frame, bg="#252525")
        row_gurl.pack(fill="x", pady=2)
        tk.Label(row_gurl, text="Base URL:", width=14, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        gemini_url_var = tk.StringVar(value=GEMINI_BASE_URL)
        tk.Entry(row_gurl, textvariable=gemini_url_var, width=34,
                 **{**ent_style, "bg": "#333333"}).pack(side="left")

        row_gtok = tk.Frame(gemini_frame, bg="#252525")
        row_gtok.pack(fill="x", pady=2)
        tk.Label(row_gtok, text="Base URL token:", width=14, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        gemini_token_var = tk.StringVar(value=GEMINI_AUTH_TOKEN)
        tk.Entry(row_gtok, textvariable=gemini_token_var, width=34, show="*",
                 **{**ent_style, "bg": "#333333"}).pack(side="left")
        tk.Label(gemini_frame,
                 text="Leave empty to call Google directly. Set it to use any "
                      "Gemini-compatible proxy,\ne.g. https://your-proxy/v1beta "
                      "— the token is sent as Authorization: Bearer.",
                 justify="left",
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

        # Quality settings — thinking level + media resolution
        quality_frame = tk.Frame(gemini_frame, bg="#252525")
        quality_frame.pack(fill="x", pady=(8, 0))

        row_think = tk.Frame(quality_frame, bg="#252525")
        row_think.pack(fill="x", pady=2)
        tk.Label(row_think, text="Thinking level:", width=16, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        thinking_var = tk.StringVar(value=GEMINI_THINKING_LEVEL)
        om_think = tk.OptionMenu(row_think, thinking_var, "MINIMAL", "LOW", "MEDIUM", "HIGH")
        om_think.config(bg="#333333", fg="#cccccc", activebackground="#444444",
                        activeforeground="#ffffff", highlightthickness=0,
                        relief="flat", font=("Segoe UI", 9))
        om_think["menu"].config(bg="#333333", fg="#cccccc",
                                activebackground="#0078d4", activeforeground="#ffffff")
        om_think.pack(side="left")

        row_res = tk.Frame(quality_frame, bg="#252525")
        row_res.pack(fill="x", pady=2)
        tk.Label(row_res, text="Media resolution:", width=16, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        media_res_var = tk.StringVar(value=GEMINI_MEDIA_RESOLUTION)
        om_res = tk.OptionMenu(row_res, media_res_var, "LOW", "MEDIUM", "HIGH")
        om_res.config(bg="#333333", fg="#cccccc", activebackground="#444444",
                      activeforeground="#ffffff", highlightthickness=0,
                      relief="flat", font=("Segoe UI", 9))
        om_res["menu"].config(bg="#333333", fg="#cccccc",
                              activebackground="#0078d4", activeforeground="#ffffff")
        om_res.pack(side="left")

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

        # ══ TEXT → SPEECH TAB ════════════════════════════════════════════════
        chk_cfg = dict(bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                       activebackground="#1e1e1e", activeforeground="#ffffff",
                       font=("Segoe UI", 9))

        section("Read Selected Text", tab_tts)
        tts_on_var = tk.BooleanVar(value=TTS_ENABLED)
        tk.Checkbutton(tab_tts, text="Enable  —  select text anywhere, press the hotkey, hear it",
                       variable=tts_on_var, **chk_cfg).pack(anchor="w")

        def _hotkey_row(row, outer, var):
            """Entry + Record button in `row`; the validation warning is a
            sibling of `row` inside `outer`, so no cross-container pack(in_=)
            trick is needed (that trick raised a TclError - the warning label's
            real parent was the row, not the tab, and Tk rejects packing a
            widget into a container it is already nested under)."""
            warn = tk.Label(outer, text="", bg="#1e1e1e", fg="#e0a030",
                            font=("Segoe UI", 8), wraplength=330, justify="left")

            def _check(*_):
                ok, msg = validate_hotkey(var.get())
                warn.config(text="" if ok else "⚠  " + msg)

            def _record():
                def _got(combo):
                    ok, msg = validate_hotkey(combo)
                    var.set(combo)
                    warn.config(text="" if ok else "⚠  " + msg)
                capture_hotkey(win, _got)

            tk.Entry(row, textvariable=var, width=18, **ent_style).pack(side="left")
            tk.Button(row, text="Record", command=_record,
                      bg="#3c3c3c", fg="#cccccc", relief="flat",
                      font=("Segoe UI", 8), activebackground="#4c4c4c",
                      activeforeground="#ffffff").pack(side="left", padx=(6, 0))
            var.trace_add("write", _check)
            _check()
            return warn

        row_th = tk.Frame(tab_tts, bg="#1e1e1e")
        row_th.pack(fill="x", pady=(6, 2))
        tk.Label(row_th, text="Hotkey:", width=12, anchor="w", **lbl_style).pack(side="left")
        tts_hotkey_var = tk.StringVar(value=TTS_HOTKEY)
        _hotkey_row(row_th, tab_tts, tts_hotkey_var).pack(anchor="w", pady=(0, 4))

        section("Voice Engine", tab_tts)
        tts_eng_var = tk.StringVar(value=TTS_ENGINE)
        for label, val in [
                ("Edge  —  free, no key, instant  (Microsoft neural voices)", "edge"),
                ("Vertex Gemini-TTS  —  Google Cloud credential, tunable tone", "vertex")]:
            tk.Radiobutton(tab_tts, text=label, variable=tts_eng_var, value=val,
                           **rb_cfg).pack(anchor="w")

        tts_extra = tk.Frame(tab_tts, bg="#1e1e1e")
        tts_extra.pack(fill="x", pady=(4, 0))

        def _om(parent, var, values, width=None):
            om = tk.OptionMenu(parent, var, *values)
            om.config(bg="#333333", fg="#cccccc", activebackground="#444444",
                      activeforeground="#ffffff", highlightthickness=0,
                      relief="flat", font=("Segoe UI", 9), anchor="w")
            if width:
                om.config(width=width)
            om["menu"].config(bg="#333333", fg="#cccccc",
                              activebackground="#0078d4", activeforeground="#ffffff")
            return om

        # Edge sub-frame
        edge_frame = tk.Frame(tts_extra, bg="#252525", padx=12, pady=8)
        row_ev = tk.Frame(edge_frame, bg="#252525")
        row_ev.pack(fill="x", pady=2)
        tk.Label(row_ev, text="Voice:", width=14, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        edge_voice_var = tk.StringVar(value=TTS_EDGE_VOICE)
        _om(row_ev, edge_voice_var, TTS_EDGE_VOICES, width=24).pack(side="left")
        tk.Label(edge_frame, text="Free Microsoft Edge voices. No account, no cost.",
                 bg="#252525", fg="#666666", font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))

        # Vertex sub-frame
        vx_frame = tk.Frame(tts_extra, bg="#252525", padx=12, pady=8)

        def _vx_row(label, value, values):
            r = tk.Frame(vx_frame, bg="#252525")
            r.pack(fill="x", pady=2)
            tk.Label(r, text=label, width=14, anchor="w",
                     bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
            v = tk.StringVar(value=value)
            _om(r, v, values, width=28).pack(side="left")
            return v

        vx_voice_var = _vx_row("Voice:", TTS_VERTEX_VOICE, TTS_VERTEX_VOICES)
        vx_model_var = _vx_row("Model:", TTS_VERTEX_MODEL, TTS_VERTEX_MODELS)

        r_cred = tk.Frame(vx_frame, bg="#252525")
        r_cred.pack(fill="x", pady=2)
        tk.Label(r_cred, text="Credential:", width=14, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        vx_cred_var = tk.StringVar(value=TTS_VERTEX_CRED)
        tk.Entry(r_cred, textvariable=vx_cred_var, width=30,
                 **{**ent_style, "bg": "#333333"}).pack(side="left")

        def _pick_cred():
            from tkinter import filedialog
            p = filedialog.askopenfilename(parent=win, title="Google credential JSON",
                                           filetypes=[("JSON", "*.json")])
            if p:
                vx_cred_var.set(p)

        tk.Button(r_cred, text="...", command=_pick_cred, width=3,
                  bg="#3c3c3c", fg="#cccccc", relief="flat",
                  activebackground="#4c4c4c").pack(side="left", padx=(4, 0))

        r_proj = tk.Frame(vx_frame, bg="#252525")
        r_proj.pack(fill="x", pady=2)
        tk.Label(r_proj, text="Project id:", width=14, anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        vx_proj_var = tk.StringVar(value=TTS_VERTEX_PROJECT)
        tk.Entry(r_proj, textvariable=vx_proj_var, width=30,
                 **{**ent_style, "bg": "#333333"}).pack(side="left")
        tk.Label(vx_frame, text="Blank = read from the credential file. Vertex AI only, never AI Studio.",
                 bg="#252525", fg="#666666", font=("Segoe UI", 8)).pack(anchor="w", pady=(2, 0))

        tk.Label(vx_frame, text="Tone prompt:", anchor="w",
                 bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 2))
        style_wrap = tk.Frame(vx_frame, bg="#252525")
        style_wrap.pack(fill="x")
        tts_style_text = tk.Text(style_wrap, height=4, bg="#333333", fg="#ffffff",
                                 insertbackground="#ffffff", relief="flat",
                                 font=("Segoe UI", 9), wrap="word")
        tts_style_text.insert("1.0", TTS_STYLE)
        tts_style_text.pack(side="left", fill="x", expand=True)
        style_sb = tk.Scrollbar(style_wrap, command=tts_style_text.yview)
        style_sb.pack(side="right", fill="y")
        tts_style_text.config(yscrollcommand=style_sb.set)

        def _reset_tts_style():
            tts_style_text.delete("1.0", "end")
            tts_style_text.insert("1.0", TTS_DEFAULT_STYLE)

        tk.Button(vx_frame, text="Reset to default", command=_reset_tts_style,
                  bg="#3c3c3c", fg="#888888", relief="flat", font=("Segoe UI", 8),
                  activebackground="#4c4c4c", activeforeground="#cccccc").pack(anchor="e", pady=(4, 0))

        def _refresh_tts_extra(*_):
            for child in tts_extra.winfo_children():
                child.pack_forget()
            tts_extra.configure(bg="#252525")
            (vx_frame if tts_eng_var.get() == "vertex" else edge_frame).pack(fill="x")

        tts_eng_var.trace_add("write", _refresh_tts_extra)
        _refresh_tts_extra()

        section("Playback", tab_tts)
        row_sp = tk.Frame(tab_tts, bg="#1e1e1e")
        row_sp.pack(fill="x", pady=2)
        tk.Label(row_sp, text="Speed:", width=12, anchor="w", **lbl_style).pack(side="left")
        tts_speed_var = tk.DoubleVar(value=TTS_SPEED)
        speed_lbl = tk.Label(row_sp, text=f"{TTS_SPEED:.2f}x", width=6,
                             bg="#1e1e1e", fg="#cccccc", font=("Segoe UI", 9))
        tk.Scale(row_sp, from_=0.5, to=2.0, resolution=0.05, orient="horizontal",
                 variable=tts_speed_var, showvalue=False, length=200,
                 bg="#1e1e1e", troughcolor="#2d2d2d", highlightthickness=0, bd=0,
                 sliderrelief="flat", activebackground="#0078d4",
                 command=lambda v: speed_lbl.config(text=f"{float(v):.2f}x")).pack(side="left")
        speed_lbl.pack(side="left", padx=(6, 0))
        tk.Label(tab_tts, text="Pitch is preserved at any speed (WSOLA time-stretch).",
                 bg="#1e1e1e", fg="#666666", font=("Segoe UI", 8)).pack(anchor="w")

        tts_popup_var = tk.BooleanVar(value=TTS_POPUP)
        tk.Checkbutton(tab_tts, text="Show the player widget while reading",
                       variable=tts_popup_var, **chk_cfg).pack(anchor="w", pady=(8, 0))

        row_ac = tk.Frame(tab_tts, bg="#1e1e1e")
        row_ac.pack(fill="x", pady=2)
        tk.Label(row_ac, text="Auto-close:", width=12, anchor="w", **lbl_style).pack(side="left")
        tts_close_var = tk.StringVar(value=str(TTS_POPUP_AUTOCLOSE))
        tk.Entry(row_ac, textvariable=tts_close_var, width=6, **ent_style).pack(side="left")
        tk.Label(row_ac, text="seconds after playback ends  (0 = keep open)",
                 bg="#1e1e1e", fg="#666666", font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))

        def _test_voice():
            sample = ("این یک نمونه صدا برای تست تنظیمات است."
                      if edge_voice_var.get().startswith("fa")
                      or tts_eng_var.get() == "vertex"
                      else "This is a sample of the selected voice.")
            _apply_settings(_collect())
            threading.Thread(target=speak_text, args=(sample,), daemon=True).start()

        tk.Button(tab_tts, text="🔊  Test voice", command=lambda: _test_voice(),
                  bg="#3c3c3c", fg="#cccccc", relief="flat", font=("Segoe UI", 9),
                  activebackground="#4c4c4c", activeforeground="#ffffff").pack(anchor="w", pady=(12, 4))

        # ══ GENERAL TAB ══════════════════════════════════════════════════════
        section("Voice Typing", tab_gen)

        row1 = tk.Frame(tab_gen, bg="#1e1e1e")
        row1.pack(fill="x", pady=2)
        tk.Label(row1, text="Hotkey:", width=12, anchor="w", **lbl_style).pack(side="left")
        hotkey_var = tk.StringVar(value=HOTKEY)
        _hotkey_row(row1, tab_gen, hotkey_var).pack(anchor="w", pady=(0, 4))

        row2 = tk.Frame(tab_gen, bg="#1e1e1e")
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

        tk.Checkbutton(tab_gen, text="Follow Windows keyboard layout  (auto-detect Persian / English)",
                       variable=lang_mode_var, command=_toggle_lang_mode,
                       **chk_cfg).pack(anchor="w", padx=(0, 0), pady=(2, 0))
        _toggle_lang_mode()  # apply initial state

        # ── Microphone ──────────────────────────────────────────────────────
        section("Microphone", tab_gen)
        mic_var = tk.StringVar(value=MIC_MODE)
        tk.Radiobutton(tab_gen, text="Always on  (pre-roll active, mic indicator always visible)",
                       variable=mic_var, value="always", **rb_cfg).pack(anchor="w")
        tk.Radiobutton(tab_gen, text="On demand  (mic opens only while hotkey held — more secure)",
                       variable=mic_var, value="on_demand", **rb_cfg).pack(anchor="w")

        # ── Text insertion ───────────────────────────────────────────────────
        section("Text insertion", tab_gen)
        inject_var = tk.StringVar(value=INJECT_MODE)
        for label, val in [
                ("Automatic  —  type short text, paste long text", "auto"),
                ("Always type  —  key by key, never touches the clipboard", "type"),
                ("Always paste  —  for apps that ignore synthetic keystrokes", "paste")]:
            tk.Radiobutton(tab_gen, text=label, variable=inject_var, value=val,
                           **rb_cfg).pack(anchor="w")
        tk.Label(tab_gen, text="Pasting restores your previous clipboard content afterwards.",
                 bg="#1e1e1e", fg="#666666", font=("Segoe UI", 8)).pack(anchor="w")

        # ── General options ──────────────────────────────────────────────────
        section("Options", tab_gen)
        updates_var = tk.BooleanVar(value=CHECK_UPDATES)
        tk.Checkbutton(tab_gen, text="Check for updates on startup",
                       variable=updates_var, **chk_cfg).pack(anchor="w")
        notify_var = tk.BooleanVar(value=NOTIFY_ERRORS)
        tk.Checkbutton(tab_gen, text="Show a notification when something fails",
                       variable=notify_var, **chk_cfg).pack(anchor="w")

        # ── Buttons (shared footer, outside the notebook) ─────────────────────
        footer_area = tk.Frame(win, bg="#1e1e1e", padx=20, pady=10)
        footer_area.pack(side="bottom", fill="x")
        btn_frame = tk.Frame(footer_area, bg="#1e1e1e")
        btn_frame.pack(fill="x")

        def _collect():
            """Every widget value as a settings dict (used by Save and Test voice)."""
            try:
                autoclose = max(0, int(tts_close_var.get().strip() or 0))
            except ValueError:
                autoclose = 30
            return {
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
                "gemini_api_key":           gemini_key_var.get().strip(),
                "gemini_base_url":          gemini_url_var.get().strip(),
                "gemini_auth_token":        gemini_token_var.get().strip(),
                "gemini_system_prompt":     gemini_prompt_text.get("1.0", "end-1c").strip(),
                "gemini_thinking_level":    thinking_var.get(),
                "gemini_media_resolution":  media_res_var.get(),
                "gemini_stt_model":         gstt_model_var.get().strip() or GEMINI_STT_DEFAULT_MODEL,
                "inject_mode":          inject_var.get(),
                "notify_errors":        notify_var.get(),
                "tts_enabled":          tts_on_var.get(),
                "tts_hotkey":           tts_hotkey_var.get().strip(),
                "tts_engine":           tts_eng_var.get(),
                "tts_edge_voice":       edge_voice_var.get(),
                "tts_vertex_voice":     vx_voice_var.get(),
                "tts_vertex_model":     vx_model_var.get(),
                "tts_vertex_cred":      vx_cred_var.get().strip(),
                "tts_vertex_project":   vx_proj_var.get().strip(),
                "tts_style":            tts_style_text.get("1.0", "end-1c").strip(),
                "tts_speed":            round(float(tts_speed_var.get()), 2),
                "tts_popup":            tts_popup_var.get(),
                "tts_popup_autoclose":  autoclose,
            }

        def on_save():
            _apply_settings(_collect())
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
        footer = tk.Frame(footer_area, bg="#1e1e1e")
        footer.pack(fill="x", pady=(10, 0))
        tk.Label(footer, text=f"SpeakPaste v{VERSION}",
                 bg="#1e1e1e", fg="#555555", font=("Segoe UI", 8)).pack(side="left")
        link = tk.Label(footer, text="View on GitHub ↗",
                        bg="#1e1e1e", fg="#3d8fd1", font=("Segoe UI", 8),
                        cursor="hand2")
        link.pack(side="right")
        link.bind("<Button-1>", lambda e: __import__('webbrowser').open(GITHUB_URL))

        _settings_window = win

        def _on_close():
            global _settings_window
            _settings_window = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)

        win.update_idletasks()  # force layout calculation
        # Size to the widest tab so switching tabs never resizes the window.
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        w  = max(t.winfo_reqwidth() for t in (tab_stt, tab_tts, tab_gen)) + 40
        h  = min(max(t.winfo_reqheight() for t in (tab_stt, tab_tts, tab_gen))
                 + footer_area.winfo_reqheight() + 60, int(sh * 0.90))
        win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        win.deiconify()  # now show

    _ui_run(_build)


# ─── History Window ──────────────────────────────────────────────────────────

def open_history(icon=None, item=None):
    def _build():
        global _history_window

        if _history_window and _history_window.winfo_exists():
            _history_window.lift()
            _history_window.focus_force()
            return

        win = tk.Toplevel(_ui_root)
        win.withdraw()
        win.title("SpeakPaste - History")
        win.configure(bg="#1e1e1e")
        win.resizable(True, True)

        # ── Top bar ──────────────────────────────────────────────────────────
        top = tk.Frame(win, bg="#1e1e1e", padx=14, pady=8)
        top.pack(fill="x")

        show_stt_var   = tk.BooleanVar(value=True)
        _known_len     = [-1]  # last rendered history length; -1 forces first render

        # Click any entry to copy it. Each clickable run gets its own tag whose
        # binding closes over that entry's text.
        _click_tags = []

        def _copy(text_value):
            _clipboard_set_text(text_value)
            flash.config(text="✓ copied")
            win.after(1400, lambda: flash.config(text=""))

        def _clickable(start, end, value):
            tag = f"click{len(_click_tags)}"
            _click_tags.append(tag)
            txt.tag_add(tag, start, end)
            txt.tag_config(tag)
            txt.tag_bind(tag, "<Button-1>", lambda e, v=value: _copy(v))
            txt.tag_bind(tag, "<Enter>",
                         lambda e, t=tag: (txt.config(cursor="hand2"),
                                           txt.tag_config(t, background="#242424")))
            txt.tag_bind(tag, "<Leave>",
                         lambda e, t=tag: (txt.config(cursor="arrow"),
                                           txt.tag_config(t, background="")))

        def _render():
            _known_len[0] = len(_history)
            txt.config(state="normal")
            for t in _click_tags:
                txt.tag_delete(t)
            _click_tags.clear()
            txt.delete("1.0", "end")
            entries = list(_history)
            if not entries:
                txt.insert("end", "No history yet.", "empty")
            for entry in entries:
                txt.insert("end", entry["time"] + "  ", "ts")
                txt.insert("end", entry["engine"].upper() + "\n", "eng")
                if entry.get("stt") and show_stt_var.get():
                    ln_start = txt.index("end-1c")
                    txt.insert("end", "  Voice   ", "lbl_v")
                    txt.insert("end", entry["stt"], "stt")
                    if _is_rtl(entry["stt"]):
                        txt.tag_add("rtl", ln_start, txt.index("end-1c"))
                    _clickable(ln_start, txt.index("end-1c"), entry["stt"])
                    txt.insert("end", "\n")
                lbl = "  Prompt  " if entry.get("stt") else "  "
                ln_start = txt.index("end-1c")
                txt.insert("end", lbl, "lbl_p")
                txt.insert("end", entry["output"], "out")
                if _is_rtl(entry["output"]):
                    txt.tag_add("rtl", ln_start, txt.index("end-1c"))
                _clickable(ln_start, txt.index("end-1c"), entry["output"])
                txt.insert("end", "\n\n")
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
            save_history()
            _render()

        tk.Button(
            top, text="Clear", command=_clear,
            bg="#3c3c3c", fg="#888888", relief="flat",
            font=("Segoe UI", 9),
            activebackground="#4c4c4c", activeforeground="#cccccc",
        ).pack(side="right")

        flash = tk.Label(top, text="", bg="#1e1e1e", fg="#4caf50",
                         font=("Segoe UI", 8))
        flash.pack(side="right", padx=(0, 10))
        tk.Label(top, text="click any line to copy", bg="#1e1e1e", fg="#555555",
                 font=("Segoe UI", 8)).pack(side="left", padx=(12, 0))

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
        # Persian/Arabic lines render right-aligned; English stays left (per-line).
        txt.tag_config("rtl",    justify="right")

        _render()
        win.after(500, _poll)

        _history_window = win

        def _on_close():
            global _history_window
            _history_window = None
            win.destroy()
        win.protocol("WM_DELETE_WINDOW", _on_close)

        win.update_idletasks()
        w = max(win.winfo_reqwidth(), 580)
        h = max(win.winfo_reqheight(), 420)
        sw = win.winfo_screenwidth()
        sh = win.winfo_screenheight()
        win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        win.deiconify()

    _ui_run(_build)


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
        pystray.MenuItem("Read clipboard aloud", speak_clipboard,
                         visible=lambda item: TTS_ENABLED),
        pystray.MenuItem("Stop reading", lambda i, it: _get_player().stop(),
                         visible=lambda item: TTS_ENABLED),
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
        (f"SpeakPaste [{_tray_label}]\n{HOTKEY.upper()} to record"
         + (f"\n{TTS_HOTKEY.upper()} to read selection" if TTS_ENABLED else "")),
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
        if PROMPT_MODE in ("gemini-lite", "gemini-flash") and not _gemini_endpoint("x")[0]:
            log("WARNING: no Gemini endpoint configured — open Settings")
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

    # Single Tk root on its own thread — Settings/History open as Toplevels on it.
    ui_thread = threading.Thread(target=_ui_thread_main, daemon=True)
    ui_thread.start()
    time.sleep(0.2)  # let the Tk root come up before any tray callback fires

    if CHECK_UPDATES:
        threading.Thread(target=check_for_update, daemon=True).start()

    setup_tray().run()


if __name__ == "__main__":
    main()
