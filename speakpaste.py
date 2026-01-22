"""
SpeakPaste - Voice to Text with Groq Whisper
Hold Win+Alt to record, release to transcribe and paste
"""

import keyboard
import sounddevice as sd
import soundfile as sf
import numpy as np
import requests
import tempfile
import os
import sys
import threading
import time
import winreg
import ctypes
from ctypes import wintypes
from queue import Queue
from collections import deque
from dotenv import load_dotenv
import pystray
from PIL import Image, ImageDraw

# Get app directory (works for both script and exe)
if getattr(sys, 'frozen', False):
    APP_DIR = os.path.dirname(sys.executable)
else:
    APP_DIR = os.path.dirname(os.path.abspath(__file__))

# Load environment variables from .env next to exe/script
load_dotenv(os.path.join(APP_DIR, '.env'))

# Config
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
GROQ_API_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
HOTKEY = os.getenv("HOTKEY", "win+alt")
LANGUAGE = os.getenv("LANGUAGE", "fa")
MODEL = os.getenv("MODEL", "whisper-large-v3-turbo")

# Audio settings
SAMPLE_RATE = 16000
CHANNELS = 1

# State
is_recording = False
audio_queue = Queue()
logs = deque(maxlen=20)
tray_icon = None
audio_stream = None
running = True

# Windows API Definitions (64-bit Compatible)
user32 = ctypes.windll.user32
INPUT_KEYBOARD = 1
KEYEVENTF_UNICODE = 0x0004
KEYEVENTF_KEYUP = 0x0002


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong)  # 64-bit pointer
    ]


class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]


class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", wintypes.DWORD),
        ("iu", INPUT_UNION),
        ("_pad", ctypes.c_ubyte * 8)  # Padding for 64-bit alignment
    ]


def log(msg):
    print(msg)
    logs.append(msg)
    # Update tooltip with last log (max 127 chars for Windows)
    if tray_icon:
        last_logs = list(logs)[-3:]
        tooltip = "SpeakPaste\n" + "\n".join(l[:40] for l in last_logs)
        tray_icon.title = tooltip[:127]


def create_icon(recording=False):
    img = Image.new('RGB', (64, 64), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    color = (255, 80, 80) if recording else (100, 200, 100)
    draw.ellipse([16, 16, 48, 48], fill=color)
    return img


def audio_callback(indata, frames, time_info, status):
    if is_recording:
        audio_queue.put(indata.copy())


def start_recording():
    global is_recording, audio_queue, tray_icon
    if is_recording:
        return
    is_recording = True
    audio_queue = Queue()
    log("🎤 Recording...")
    if tray_icon:
        tray_icon.icon = create_icon(True)


def stop_recording():
    global is_recording, tray_icon
    if not is_recording:
        return None
    is_recording = False
    if tray_icon:
        tray_icon.icon = create_icon(False)
    
    audio_data = []
    while not audio_queue.empty():
        audio_data.append(audio_queue.get())
    
    if not audio_data:
        log("❌ No audio recorded")
        return None
    
    audio_array = np.concatenate(audio_data, axis=0)
    temp_file = tempfile.NamedTemporaryFile(suffix=".wav", delete=False)
    sf.write(temp_file.name, audio_array, SAMPLE_RATE)
    duration = len(audio_array) / SAMPLE_RATE
    log(f"✅ Recorded {duration:.1f}s")
    return temp_file.name


def transcribe(audio_path):
    log("📤 Transcribing...")
    try:
        with open(audio_path, 'rb') as f:
            response = requests.post(
                GROQ_API_URL,
                headers={"Authorization": f"Bearer {GROQ_API_KEY}"},
                files={"file": ("audio.wav", f, "audio/wav")},
                data={"model": MODEL, "language": LANGUAGE, "response_format": "text"}
            )
        if response.status_code == 200:
            text = response.text.strip()
            log(f"📝 {text}")
            return text
        else:
            log(f"❌ API Error: {response.status_code}")
            return None
    except Exception as e:
        log(f"❌ Error: {e}")
        return None
    finally:
        try:
            os.unlink(audio_path)
        except:
            pass


def send_unicode_char(char_code):
    """Sends a single unicode character event"""
    # Key Down
    input_down = INPUT()
    input_down.type = INPUT_KEYBOARD
    input_down.iu.ki.wVk = 0
    input_down.iu.ki.wScan = char_code
    input_down.iu.ki.dwFlags = KEYEVENTF_UNICODE

    # Key Up
    input_up = INPUT()
    input_up.type = INPUT_KEYBOARD
    input_up.iu.ki.wVk = 0
    input_up.iu.ki.wScan = char_code
    input_up.iu.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP

    # Send both events
    user32.SendInput(1, ctypes.byref(input_down), ctypes.sizeof(INPUT))
    user32.SendInput(1, ctypes.byref(input_up), ctypes.sizeof(INPUT))


def type_text(text):
    if not text:
        return

    # 1. Wait for physical keys to release
    keys = HOTKEY.split('+')
    while any(keyboard.is_pressed(k) for k in keys):
        time.sleep(0.05)

    # 2. Safety delay (Focus settle)
    time.sleep(0.3)

    # 3. Release logical keys (Win/Alt) to prevent shortcuts
    for k in ['left windows', 'right windows', 'alt', 'ctrl', 'shift']:
        try:
            keyboard.release(k)
        except:
            pass

    # 4. Type character by character using pure Unicode injection
    try:
        for char in text:
            send_unicode_char(ord(char))
            time.sleep(0.001)  # Tiny delay for stability

        log("✅ Typed (Universal Mode)")

    except Exception as e:
        log(f"❌ Error: {e}")


def on_hotkey_release():
    if is_recording:
        audio_path = stop_recording()
        if audio_path:
            text = transcribe(audio_path)
            if text:
                type_text(text)


def get_logs_text():
    return "\n".join(logs) if logs else "No logs yet"


def on_exit(icon, item):
    global running
    running = False
    icon.stop()
    os._exit(0)


# Startup management
STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
APP_NAME = "SpeakPaste"


def get_exe_path():
    """Get path to executable or script"""
    if getattr(sys, 'frozen', False):
        return sys.executable
    return f'pythonw "{os.path.abspath(__file__)}"'


def is_in_startup():
    """Check if app is in Windows startup"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except WindowsError:
        return False


def add_to_startup():
    """Add app to Windows startup"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, get_exe_path())
        winreg.CloseKey(key)
        log("✅ Added to startup")
    except Exception as e:
        log(f"❌ Startup error: {e}")


def remove_from_startup():
    """Remove app from Windows startup"""
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE)
        winreg.DeleteValue(key, APP_NAME)
        winreg.CloseKey(key)
        log("✅ Removed from startup")
    except Exception as e:
        log(f"❌ Startup error: {e}")


def toggle_startup(icon, item):
    """Toggle startup on/off"""
    if is_in_startup():
        remove_from_startup()
    else:
        add_to_startup()


def setup_tray():
    global tray_icon
    menu = pystray.Menu(
        pystray.MenuItem("Run at startup", toggle_startup, checked=lambda item: is_in_startup()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", on_exit)
    )
    tray_icon = pystray.Icon("speakpaste", create_icon(), f"SpeakPaste\n{HOTKEY.upper()} to record", menu)
    return tray_icon


def keyboard_listener():
    global running
    keys = HOTKEY.split('+')
    while running:
        try:
            if all(keyboard.is_pressed(k) for k in keys):
                if not is_recording:
                    start_recording()
            else:
                if is_recording:
                    on_hotkey_release()
        except:
            pass
        time.sleep(0.05)


def main():
    global audio_stream, tray_icon, running
    
    if not GROQ_API_KEY:
        print("❌ Error: GROQ_API_KEY not set. Create .env file with your API key.")
        return
    
    log("🎙️ SpeakPaste Started")
    log(f"Hold {HOTKEY.upper()} to record")
    log("Right-click tray icon to exit")
    
    audio_stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=CHANNELS, callback=audio_callback)
    audio_stream.start()
    
    kb_thread = threading.Thread(target=keyboard_listener, daemon=True)
    kb_thread.start()
    
    icon = setup_tray()
    icon.run()


if __name__ == "__main__":
    main()
