import os
import sys
import winreg
import pystray
from PIL import Image, ImageDraw
from ..config import APP_NAME, log, logs

STARTUP_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"


def _get_exe_path():
    if getattr(sys, "frozen", False):
        return sys.executable
    return f'pythonw "{os.path.abspath(sys.argv[0])}"'


def is_in_startup():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_READ)
        winreg.QueryValueEx(key, APP_NAME)
        winreg.CloseKey(key)
        return True
    except OSError:
        return False


def toggle_startup():
    try:
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, STARTUP_KEY, 0, winreg.KEY_SET_VALUE)
        if is_in_startup():
            winreg.DeleteValue(key, APP_NAME)
            log("Removed from startup")
        else:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _get_exe_path())
            log("Added to startup")
        winreg.CloseKey(key)
    except Exception as e:
        log(f"Startup error: {e}")


def create_icon(state="idle"):
    colors = {
        "idle":      (100, 200, 100),
        "recording": (255, 80, 80),
        "waiting":   (255, 180, 0),
        "playing":   (76, 154, 255),
    }
    img = Image.new("RGB", (64, 64), color=(30, 30, 30))
    draw = ImageDraw.Draw(img)
    draw.ellipse([16, 16, 48, 48], fill=colors.get(state, colors["idle"]))
    return img


def setup_tray(cfg, on_settings=None, on_history=None, on_read_clipboard=None,
               on_stop_reading=None, on_toggle_mic=None, on_select_preset=None, on_quit=None):
    stt_e = cfg.get("stt_engine", "google").upper()
    pm = cfg.get("prompt_mode", "off").upper()
    _tray_label = stt_e if pm == "OFF" else f"{stt_e}+{pm}"

    def _last_log(item):
        return list(logs)[-1] if logs else "Ready"

    def _mic_status(item):
        if cfg.get("mic_mode") == "on_demand":
            return "Mic: on-demand (secure)"
        return "Mic: always-on"

    presets = cfg.get("tts_presets", {})
    preset_items = []
    for k in ["charon_calm", "fenrir_pragmatic", "aoede_neutral", "puck_quick"]:
        if k in presets:
            p = presets[k]
            def _make_action(key):
                return lambda icon, item: on_select_preset(key) if on_select_preset else None
            def _make_check(key):
                return lambda item: cfg.get("tts_vertex_preset", "charon_calm") == key
            preset_items.append(
                pystray.MenuItem(
                    p.get("name", k),
                    _make_action(k),
                    checked=_make_check(k)
                )
            )

    menu_items = [
        pystray.MenuItem(f"Engine: {_tray_label}", None, enabled=False),
        pystray.MenuItem(_last_log, None, enabled=False),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Settings...", lambda icon, item: on_settings() if on_settings else None),
        pystray.MenuItem("History...", lambda icon, item: on_history() if on_history else None),
        pystray.Menu.SEPARATOR,
    ]
    if preset_items:
        menu_items.append(
            pystray.MenuItem("Voice Style", pystray.Menu(*preset_items),
                             visible=lambda item: cfg.get("tts_enabled", True) and cfg.get("tts_engine", "vertex") == "vertex")
        )
    menu_items.extend([
        pystray.MenuItem("Read clipboard aloud", lambda icon, item: on_read_clipboard() if on_read_clipboard else None,
                         visible=lambda item: cfg.get("tts_enabled", True)),
        pystray.MenuItem("Stop reading", lambda icon, item: on_stop_reading() if on_stop_reading else None,
                         visible=lambda item: cfg.get("tts_enabled", True)),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem(_mic_status, lambda icon, item: on_toggle_mic() if on_toggle_mic else None,
                         checked=lambda item: cfg.get("mic_mode") == "on_demand"),
        pystray.MenuItem("Run at startup", lambda icon, item: toggle_startup(),
                         checked=lambda item: is_in_startup()),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Exit", lambda icon, item: on_quit() if on_quit else None),
    ])

    menu = pystray.Menu(*menu_items)

    hk = cfg.get("hotkey", "win+alt").upper()
    tts_hk = cfg.get("tts_hotkey", "win+shift").upper()
    tooltip = f"SpeakPaste [{_tray_label}]\n{hk} to record"
    if cfg.get("tts_enabled", True):
        tooltip += f"\n{tts_hk} to read selection"

    icon = pystray.Icon("speakpaste", create_icon("idle"), tooltip, menu)
    return icon
