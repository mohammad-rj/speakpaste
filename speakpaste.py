"""
SpeakPaste - Voice to Text
Hold Win+Alt to record, release to transcribe and paste.

Engines:
  google        — Google Speech API (unofficial, no API key, no Chrome)  [default]
  groq          — Groq Whisper API (requires free API key)
  google-ext    — Chrome extension + Offscreen Document (requires Chrome in background)
  google-cloud  — Google Cloud Speech-to-Text REST API (official, requires API key)
"""

import keyboard
import requests
import tempfile
import os
import sys
import threading
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

VERSION       = "1.3.0"
GITHUB_REPO   = "mohammad-rj/speakpaste"
GITHUB_URL    = f"https://github.com/{GITHUB_REPO}"

SETTINGS_FILE = os.path.join(APP_DIR, 'settings.json')
GROQ_API_URL  = "https://api.groq.com/openai/v1/audio/transcriptions"
SAMPLE_RATE   = 16000
CHANNELS      = 1

_DEFAULTS = {
    "engine":              "google",
    "hotkey":              "win+alt",
    "language":            "fa",
    "mic_mode":            "always",
    "groq_api_key":        "",
    "model":               "whisper-large-v3-turbo",
    "google_cloud_api_key": "",
    "ws_port":             9137,
    "check_updates":       True,
}

# ─── Settings Load / Save ─────────────────────────────────────────────────────

def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, 'r', encoding='utf-8') as f:
                return {**_DEFAULTS, **json.load(f)}
        except Exception:
            pass
    # Fallback: read from old .env
    env_path = os.path.join(APP_DIR, '.env')
    cfg = dict(_DEFAULTS)
    if os.path.exists(env_path):
        try:
            from dotenv import dotenv_values
            ev = dotenv_values(env_path)
            if ev.get("ENGINE"):       cfg["engine"]       = ev["ENGINE"]
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

ENGINE               = _cfg["engine"]
HOTKEY               = _cfg["hotkey"]
LANGUAGE             = _cfg["language"]
MIC_MODE             = _cfg["mic_mode"]
GROQ_API_KEY         = _cfg["groq_api_key"]
MODEL                = _cfg["model"]
GOOGLE_CLOUD_API_KEY = _cfg["google_cloud_api_key"]
WS_PORT              = _cfg["ws_port"]
CHECK_UPDATES        = _cfg["check_updates"]

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


# ─── Transcription ────────────────────────────────────────────────────────────

def _transcribe_google_direct(audio_path):
    log("Transcribing (Google)...")
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        with sr.AudioFile(audio_path) as source:
            audio = r.record(source)
        text = r.recognize_google(audio, language=LANGUAGE)
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
        lang = LANGUAGE if '-' in LANGUAGE else {
            'fa': 'fa-IR', 'en': 'en-US', 'ar': 'ar-SA',
            'tr': 'tr-TR', 'de': 'de-DE', 'fr': 'fr-FR',
        }.get(LANGUAGE, LANGUAGE + '-' + LANGUAGE.upper())

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
                data={"model": MODEL, "language": LANGUAGE, "response_format": "text"},
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
    if ENGINE == "google-ext":
        if not _ws_clients:
            log("[Google-ext] Extension not connected")
            return
        _google_send({"cmd": "start", "lang": LANGUAGE})
        log("Listening (Google-ext)...")
        if tray_icon:
            tray_icon.icon = create_icon("recording")
    else:
        _start_recording()


def on_hotkey_release():
    if ENGINE == "google-ext":
        text = _transcribe_google_ext()
    elif ENGINE == "google":
        path = _stop_recording()
        text = _transcribe_google_direct(path) if path else None
    elif ENGINE == "google-cloud":
        path = _stop_recording()
        text = _transcribe_google_cloud(path) if path else None
    else:  # groq
        path = _stop_recording()
        text = _transcribe_groq(path) if path else None

    if text:
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
    global ENGINE, HOTKEY, LANGUAGE, MIC_MODE, GROQ_API_KEY, MODEL, WS_PORT, CHECK_UPDATES

    old_mic    = MIC_MODE
    old_engine = ENGINE

    ENGINE               = new_cfg["engine"]
    HOTKEY               = new_cfg["hotkey"]
    LANGUAGE             = new_cfg["language"]
    GROQ_API_KEY         = new_cfg["groq_api_key"]
    MODEL                = new_cfg["model"]
    GOOGLE_CLOUD_API_KEY = new_cfg["google_cloud_api_key"]
    WS_PORT              = new_cfg["ws_port"]
    MIC_MODE             = new_cfg["mic_mode"]
    CHECK_UPDATES        = new_cfg["check_updates"]

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
    log(f"Settings saved — engine={ENGINE}, mic={MIC_MODE}, lang={LANGUAGE}")

    if old_engine != ENGINE:
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

        # ── Engine ──────────────────────────────────────────────────────────
        section("Engine")
        engine_var = tk.StringVar(value=ENGINE)
        engines = [("Google  —  free, unofficial, no key", "google"),
                   ("Google Cloud  —  official, API key required", "google-cloud"),
                   ("Groq Whisper  —  API key required", "groq"),
                   ("Google Extension  —  Chrome in background", "google-ext")]
        for label, val in engines:
            tk.Radiobutton(win, text=label, variable=engine_var, value=val,
                           bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                           activebackground="#1e1e1e", activeforeground="#ffffff",
                           font=("Segoe UI", 9)).pack(anchor="w")

        # ── Inline engine config (fixed-height slot below radio buttons) ────
        engine_extra = tk.Frame(win, bg="#1e1e1e", height=74)
        engine_extra.pack_propagate(False)   # never resize — window stays stable
        engine_extra.pack(fill="x")

        # Groq sub-frame
        groq_frame = tk.Frame(engine_extra, bg="#252525", padx=12, pady=6)
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
        gcloud_frame = tk.Frame(engine_extra, bg="#252525", padx=12, pady=6)
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

        def _refresh_visibility(*_):
            eng = engine_var.get()
            for child in engine_extra.winfo_children():
                child.pack_forget()
            if eng == "groq":
                engine_extra.configure(bg="#252525")
                groq_frame.configure(bg="#252525")
                groq_frame.pack(fill="x")
            elif eng == "google-cloud":
                engine_extra.configure(bg="#252525")
                gcloud_frame.configure(bg="#252525")
                gcloud_frame.pack(fill="x")
            else:
                engine_extra.configure(bg="#1e1e1e")  # invisible gap, same as bg

        engine_var.trace_add("write", _refresh_visibility)
        _refresh_visibility()

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
        tk.Entry(row2, textvariable=lang_var, width=20, **ent_style).pack(side="left")

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
                "engine":               engine_var.get(),
                "hotkey":               hotkey_var.get().strip(),
                "language":             lang_var.get().strip(),
                "mic_mode":             mic_var.get(),
                "groq_api_key":         key_var.get().strip(),
                "model":                model_var.get().strip(),
                "google_cloud_api_key": gcloud_key_var.get().strip(),
                "ws_port":              WS_PORT,
                "check_updates":        updates_var.get(),
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
    menu = pystray.Menu(
        pystray.MenuItem(f"Engine: {ENGINE.upper()}", None, enabled=False),
        pystray.MenuItem(_last_log, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Settings...", open_settings),
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
        f"SpeakPaste [{ENGINE.upper()}]\n{HOTKEY.upper()} to record",
        menu,
    )
    return tray_icon


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    global audio_stream, running

    if ENGINE in ("groq", "google", "google-cloud"):
        if ENGINE == "groq" and not GROQ_API_KEY:
            log("WARNING: GROQ_API_KEY not set — open Settings to configure")
        if ENGINE == "google-cloud" and not GOOGLE_CLOUD_API_KEY:
            log("WARNING: GOOGLE_CLOUD_API_KEY not set — open Settings to configure")
        import sounddevice as sd
        audio_stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS,
                                      callback=_audio_callback)
        if MIC_MODE == "always":
            audio_stream.start()
            log(f"SpeakPaste [{ENGINE.upper()}] ready — {HOTKEY.upper()} (mic always-on)")
        else:
            log(f"SpeakPaste [{ENGINE.upper()}] ready — {HOTKEY.upper()} (mic on-demand)")

    elif ENGINE == "google-ext":
        ws_thread = threading.Thread(target=_start_ws_server, daemon=True)
        ws_thread.start()
        time.sleep(0.2)
        log(f"SpeakPaste [GOOGLE-EXT] ready — load Chrome extension to connect")

    else:
        log(f"Unknown engine '{ENGINE}' — open Settings to fix")

    kb_thread = threading.Thread(target=keyboard_listener, daemon=True)
    kb_thread.start()

    if CHECK_UPDATES:
        threading.Thread(target=check_for_update, daemon=True).start()

    setup_tray().run()


if __name__ == "__main__":
    main()
