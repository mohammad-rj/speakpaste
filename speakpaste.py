"""
SpeakPaste - Voice to Text
Hold Win+Alt to record, release to transcribe and paste.

Engines:
  google     — Google Speech API (unofficial, no API key, no Chrome)  [default]
  groq       — Groq Whisper API (requires free API key)
  google-ext — Chrome extension + Offscreen Document (requires Chrome in background)
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
from ctypes import wintypes
from queue import Queue, Empty
from collections import deque
from dotenv import load_dotenv
import pystray
from PIL import Image, ImageDraw

# Get app directory (works for both script and exe)
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

load_dotenv(os.path.join(APP_DIR, '.env'))

# ─── Config ───────────────────────────────────────────────────────────────────

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
HOTKEY    = os.getenv("HOTKEY",    "win+alt")
LANGUAGE  = os.getenv("LANGUAGE",  "fa")
MODEL     = os.getenv("MODEL",     "whisper-large-v3-turbo")
ENGINE    = os.getenv("ENGINE",    "google") # "google" | "groq" | "google-ext"
WS_PORT   = int(os.getenv("WS_PORT", "9137"))

SAMPLE_RATE = 16000
CHANNELS    = 1

# ─── State ────────────────────────────────────────────────────────────────────

is_recording    = False
is_hotkey_active = False
audio_queue     = Queue()
logs            = deque(maxlen=20)
tray_icon       = None
audio_stream    = None
running         = True

# Google engine WebSocket state
_ws_loop    = None
_ws_clients = set()
_result_q   = Queue()

# ─── Windows Unicode Typing ───────────────────────────────────────────────────

user32 = ctypes.windll.user32
INPUT_KEYBOARD    = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP   = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",        wintypes.WORD),
        ("wScan",      wintypes.WORD),
        ("dwFlags",    wintypes.DWORD),
        ("time",       wintypes.DWORD),
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


def create_icon(state="idle"):
    """state: idle | recording | waiting"""
    colors = {"idle": (100, 200, 100), "recording": (255, 80, 80), "waiting": (255, 180, 0)}
    img  = Image.new('RGB', (64, 64), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    draw.ellipse([16, 16, 48, 48], fill=colors.get(state, colors["idle"]))
    return img


# ─── Audio Recording (groq + google engines) ─────────────────────────────────

def _audio_callback(indata, frames, time_info, status):
    if is_recording:
        audio_queue.put(indata.copy())


def _start_recording():
    global is_recording, audio_queue
    if is_recording:
        return
    is_recording = True
    audio_queue  = Queue()
    log("Recording...")
    if tray_icon:
        tray_icon.icon = create_icon("recording")


def _stop_recording():
    global is_recording
    if not is_recording:
        return None
    is_recording = False
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


def _transcribe_google_direct(audio_path):
    """Uses SpeechRecognition → Google's unofficial API. No Chrome needed."""
    log("Transcribing (Google)...")
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        with sr.AudioFile(audio_path) as source:
            audio = r.record(source)
        text = r.recognize_google(audio, language=LANGUAGE)
        log(f">> {text}")
        return text
    except Exception as sr_err:
        log(f"Google error: {sr_err}")
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
        else:
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


# ─── Google Engine (WebSocket bridge to Chrome extension) ────────────────────

async def _ws_handler(websocket):
    _ws_clients.add(websocket)
    log("[Google] Extension connected")
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
        log("[Google] Extension disconnected")
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
            log(f"[Google] WebSocket ready on ws://localhost:{WS_PORT}")
            await asyncio.Future()

    _ws_loop.run_until_complete(_serve())


def _transcribe_google():
    """Send stop, drain old results, wait for final text from extension."""
    # Drain stale results
    while not _result_q.empty():
        try:
            _result_q.get_nowait()
        except Empty:
            break

    if not _ws_clients:
        log("[Google] Extension not connected — install & reload Chrome")
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
            log(f"[Google] Error: {result['error']}")
            return None
        if text:
            log(f">> {text}")
            return text
        return None
    except Empty:
        log("[Google] Timeout waiting for result")
        return None
    finally:
        if tray_icon:
            tray_icon.icon = create_icon("idle")


# ─── Universal Text Injection ─────────────────────────────────────────────────

def _send_unicode_char(char_code):
    down         = INPUT()
    down.type    = INPUT_KEYBOARD
    down.iu.ki.wScan  = char_code
    down.iu.ki.dwFlags = KEYEVENTF_UNICODE

    up           = INPUT()
    up.type      = INPUT_KEYBOARD
    up.iu.ki.wScan    = char_code
    up.iu.ki.dwFlags  = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP

    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    user32.SendInput(1, ctypes.byref(up),   ctypes.sizeof(INPUT))


def type_text(text):
    if not text:
        return

    # Wait for hotkey keys to physically release
    keys = HOTKEY.split('+')
    while any(keyboard.is_pressed(k) for k in keys):
        time.sleep(0.05)

    time.sleep(0.3)  # Let focus settle

    # Release modifier keys
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
    else:  # groq | google
        _start_recording()


def on_hotkey_release():
    if ENGINE == "google-ext":
        text = _transcribe_google()
    elif ENGINE == "google":
        path = _stop_recording()
        text = _transcribe_google_direct(path) if path else None
    else:  # groq
        path = _stop_recording()
        text = _transcribe_groq(path) if path else None

    if text:
        type_text(text)
    elif tray_icon:
        tray_icon.icon = create_icon("idle")


# ─── Keyboard Listener ────────────────────────────────────────────────────────

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


def setup_tray():
    global tray_icon
    menu = pystray.Menu(
        pystray.MenuItem(f"Engine: {ENGINE.upper()}", None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Run at startup", _toggle_startup, checked=lambda item: _is_in_startup()),
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

    if ENGINE in ("groq", "google"):
        if ENGINE == "groq" and not GROQ_API_KEY:
            print("ERROR: GROQ_API_KEY not set. Create .env with your key.")
            return
        import sounddevice as sd
        audio_stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, callback=_audio_callback)
        audio_stream.start()
        log(f"SpeakPaste [{ENGINE.upper()}] started — hold {HOTKEY.upper()} to record")

    elif ENGINE == "google-ext":
        ws_thread = threading.Thread(target=_start_ws_server, daemon=True)
        ws_thread.start()
        time.sleep(0.2)
        log(f"SpeakPaste [GOOGLE-EXT] started — hold {HOTKEY.upper()} to record")
        log("Load the Chrome extension, then Chrome will auto-connect")

    else:
        print(f"ERROR: Unknown ENGINE '{ENGINE}'. Use 'groq', 'google', or 'google-ext'.")
        return

    kb_thread = threading.Thread(target=keyboard_listener, daemon=True)
    kb_thread.start()

    setup_tray().run()


if __name__ == "__main__":
    main()
