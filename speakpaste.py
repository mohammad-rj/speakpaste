"""
SpeakPaste: Fast Multimodal Voice Assistant & Voice Typing for Windows
"""

import os
import sys
import time
import datetime
import threading
from queue import Queue, Empty
from concurrent.futures import ThreadPoolExecutor

from core.config import (
    APP_NAME, VERSION, GITHUB_REPO, GITHUB_URL, _DEFAULTS, TTS_STYLE_PRESETS,
    validate_ws_port, load_settings, save_settings, load_history, save_history, log
)
from core.win32 import (
    active_language, type_text, get_selected_text,
    validate_hotkey, _phys_down, get_active_window_title
)
from core.audio import AudioRecorder
from core.stt import (
    gemini_endpoint, vertex_access_token,
    transcribe_google_direct, transcribe_gemini, transcribe_google_cloud,
    transcribe_groq, gemini_lite_prompt, gemini_flash_prompt,
    _last_error
)
from core.tts import (
    _RATE_FA, _RATE_EN, _chars_per_sec, _split_long, _split_for_streaming,
    _time_stretch, TtsPlayer, get_player, synthesize_tts
)
from core.ui.history_window import _is_rtl
from core.websocket_server import (
    start_ws_server, transcribe_google_ext
)
from core.ui.root import start_ui_thread, ui_run
from core.ui.toast import show_toast
from core.ui.player_popup import open_tts_popup, close_tts_popup_if_idle, set_tts_progress
from core.ui.history_window import open_history
from core.ui.settings_window import open_settings
from core.ui.tray import setup_tray, create_icon

# Module-level variables for test suite / backwards compatibility
TTS_VERTEX_CRED = ""
TTS_VERTEX_PROJECT = ""
GEMINI_API_KEY = ""
GEMINI_BASE_URL = ""
GEMINI_AUTH_TOKEN = ""

def _gemini_endpoint(model):
    c = {
        "gemini_base_url": GEMINI_BASE_URL,
        "gemini_auth_token": GEMINI_AUTH_TOKEN,
        "gemini_api_key": GEMINI_API_KEY,
        "tts_vertex_cred": TTS_VERTEX_CRED,
        "tts_vertex_project": TTS_VERTEX_PROJECT,
    }
    return gemini_endpoint(model, c)

# ─── Runtime State ────────────────────────────────────────────────────────────

cfg = load_settings()
_history = load_history()
recorder = AudioRecorder()

_speed_holder = [float(cfg.get("tts_speed", 1.0))]
_tts_busy = False
_tts_queue = Queue()
_tts_worker_lock = threading.Lock()
_tts_worker_running = False

_stt_state = "idle"  # idle | recording | transcribing
_tts_state = "idle"  # idle | generating | playing

_session_lang = "fa"
_last_stt = None

running = True
is_hotkey_active = False
is_tts_hotkey_active = False
tray_icon = None

def _refresh_tray_icon():
    if not tray_icon:
        return
    if _stt_state == "recording":
        tray_icon.icon = create_icon("recording")
    elif _stt_state == "transcribing":
        tray_icon.icon = create_icon("waiting")
    elif _tts_state in ("generating", "playing"):
        tray_icon.icon = create_icon("playing")
    else:
        tray_icon.icon = create_icon("idle")

def set_stt_state(s):
    global _stt_state
    _stt_state = s
    _refresh_tray_icon()

def set_tts_state(s):
    global _tts_state
    _tts_state = s
    _refresh_tray_icon()

recorder.on_state_change = set_stt_state


# ─── TTS Coordinator ─────────────────────────────────────────────────────────

def speak_text(text, session_title="AI"):
    text = (text or "").strip()
    if not text:
        return
    _tts_queue.put((text, session_title))
    with _tts_worker_lock:
        global _tts_worker_running
        if not _tts_worker_running:
            _tts_worker_running = True
            threading.Thread(target=_tts_worker_loop, daemon=True, name="TTSWorker").start()

def _tts_worker_loop():
    global _tts_worker_running, _tts_busy
    try:
        while not _tts_queue.empty():
            try:
                item = _tts_queue.get_nowait()
            except Empty:
                break
            if isinstance(item, tuple):
                text, session_title = item
            else:
                text, session_title = item, "AI"
            _do_speak_text(text, session_title=session_title)
    finally:
        with _tts_worker_lock:
            _tts_worker_running = False
        _tts_busy = False
        set_tts_state("idle")

def _do_speak_text(text, session_title="AI"):
    global _tts_busy
    _tts_busy = True
    set_tts_state("generating")
    player = get_player()
    try:
        first_secs = cfg.get("tts_first_chunk_secs", 25)
        rest_secs = cfg.get("tts_chunk_secs", 50)
        parts = _split_for_streaming(text, first_secs, rest_secs)
        set_tts_progress(0, len(parts))
        if cfg.get("tts_popup", True):
            open_tts_popup(
                status=f"[{session_title}] Generating 1/{len(parts)}...",
                get_player_fn=get_player,
                history_list=_history,
                speak_fn=speak_text,
                speed_holder=_speed_holder,
                autoclose_secs=cfg.get("tts_popup_autoclose", 30)
            )

        t0 = time.time()
        active_l = active_language() if cfg.get("lang_mode") == "keyboard" else cfg.get("language", "fa")
        samples, sr, cached_path = synthesize_tts(parts[0], cfg, active_lang=active_l)
        first_gen = time.time() - t0
        player.load(samples, sr, complete=(len(parts) == 1))
        player.set_speed(_speed_holder[0])
        set_tts_progress(1, len(parts))
        set_tts_state("playing")
        log(f"TTS [{session_title}] {cfg.get('tts_engine')} chunk 1/{len(parts)} ({len(parts[0])} chars) in {first_gen:.1f}s - playing")

        hist_item = {
            "time":          datetime.datetime.now().strftime("%H:%M:%S"),
            "engine":        f"tts:{cfg.get('tts_engine')}",
            "type":          "ai",
            "session_title": session_title,
            "stt":           None,
            "output":        text,
            "file":          cached_path,
        }
        _history.appendleft(hist_item)
        save_history(_history)

        if cfg.get("tts_popup", True):
            open_tts_popup(
                status=f"[{session_title}]",
                get_player_fn=get_player,
                history_list=_history,
                speak_fn=speak_text,
                speed_holder=_speed_holder,
                autoclose_secs=cfg.get("tts_popup_autoclose", 30)
            )

        if len(parts) > 1:
            _stream_rest(parts, player, active_l)

        # Wait until current playback ends before advancing to next queued item
        while player.playing and not player.cancelled:
            time.sleep(0.1)

    except Exception as e:
        log(f"TTS error: {e}")
        if cfg.get("tts_popup", True):
            open_tts_popup(
                status=f"Error: {e}",
                get_player_fn=get_player,
                history_list=_history,
                speak_fn=speak_text,
                speed_holder=_speed_holder,
                autoclose_secs=cfg.get("tts_popup_autoclose", 30)
            )
    finally:
        _tts_busy = False
        set_tts_state("idle")

def _stream_rest(parts, player, active_l):
    n_rest = len(parts) - 1
    executor = ThreadPoolExecutor(max_workers=min(3, n_rest))
    futures = {}
    for i in range(1, len(parts)):
        futures[i] = executor.submit(synthesize_tts, parts[i], cfg, active_l)

    try:
        for i in range(1, len(parts)):
            while True:
                if player.cancelled:
                    executor.shutdown(wait=False, cancel_futures=True)
                    return
                if player.remaining_seconds() < 45.0 or i == 1:
                    break
                time.sleep(0.2)

            samples, sr, _ = futures[i].result()
            if player.cancelled:
                executor.shutdown(wait=False, cancel_futures=True)
                return
            player.append(samples)
            set_tts_progress(i + 1, len(parts))
            log(f"TTS chunk {i + 1}/{len(parts)} ({len(parts[i])} chars) appended")
    except Exception as e:
        log(f"TTS stream error: {e}")
    finally:
        executor.shutdown(wait=False)
        player.finish()


def on_tts_hotkey():
    if not cfg.get("tts_enabled", True):
        return
    text = get_selected_text(tts_hotkey=cfg.get("tts_hotkey", "win+shift"))
    if not text:
        if cfg.get("tts_popup", True):
            open_tts_popup("No text selected", get_player_fn=get_player, history_list=_history,
                           speak_fn=speak_text, speed_holder=_speed_holder, autoclose_secs=2)
            threading.Timer(2.5, lambda: close_tts_popup_if_idle(get_player)).start()
        return
    if len(text) > 8000:
        text = text[:8000]
    speak_text(text)


def speak_clipboard():
    from core.win32 import clipboard_get_text
    txt = (clipboard_get_text() or "").strip()
    if txt:
        threading.Thread(target=speak_text, args=(txt,), daemon=True).start()
    else:
        log("TTS: clipboard is empty")


# ─── Hotkey Handlers ──────────────────────────────────────────────────────────

def on_hotkey_press():
    global _session_lang
    _session_lang = active_language() if cfg.get("lang_mode") == "keyboard" else cfg.get("language", "fa")
    set_stt_state("recording")
    if cfg.get("stt_engine") == "google-ext" and cfg.get("prompt_mode") != "gemini-flash":
        from core.websocket_server import google_send, _ws_clients
        if not _ws_clients:
            log("[Google-ext] Extension not connected")
            set_stt_state("idle")
            return
        google_send({"cmd": "start", "lang": _session_lang})
        log("Listening (Google-ext)...")
    else:
        recorder.start_recording()


def on_hotkey_release():
    global _last_stt
    _last_stt = None
    hotkey = cfg.get("hotkey", "win+alt")
    inject_mode = cfg.get("inject_mode", "auto")
    engine = cfg.get("stt_engine", "google")
    prompt_mode = cfg.get("prompt_mode", "off")

    set_stt_state("transcribing")

    # 1. Gemini Flash direct
    if prompt_mode == "gemini-flash":
        raw_path = recorder.stop_recording()
        text = gemini_flash_prompt(raw_path, cfg) if raw_path else None
    elif engine == "google-ext":
        raw  = transcribe_google_ext()
        text = gemini_lite_prompt(raw, cfg) if (raw and prompt_mode == "gemini-lite") else raw
    else:
        raw_path = recorder.stop_recording()
        if engine == "google":
            raw = transcribe_google_direct(raw_path, _session_lang) if raw_path else None
        elif engine == "gemini":
            raw = transcribe_gemini(raw_path, cfg) if raw_path else None
        elif engine == "google-cloud":
            raw = transcribe_google_cloud(raw_path, cfg.get("google_cloud_api_key", ""), _session_lang) if raw_path else None
        else:  # groq
            raw = transcribe_groq(raw_path, cfg.get("groq_api_key", ""), cfg.get("model", "whisper-large-v3-turbo"), _session_lang) if raw_path else None
        text = gemini_lite_prompt(raw, cfg) if (raw and prompt_mode == "gemini-lite") else raw

    if text:
        label = engine if prompt_mode == "off" else f"{engine}+{prompt_mode}"
        win_title = get_active_window_title()
        session_title = win_title if win_title else "YOU"
        _history.appendleft({
            "time":          datetime.datetime.now().strftime("%H:%M:%S"),
            "engine":        label,
            "type":          "user",
            "session_title": session_title,
            "stt":           _last_stt,
            "output":        text,
        })
        save_history(_history)
        type_text(text, hotkey=hotkey, inject_mode=inject_mode)
    else:
        show_toast(_last_error[0] or "Nothing was transcribed - try speaking again.", notify_errors=cfg.get("notify_errors", True))
    _last_error[0] = None
    set_stt_state("idle")


def keyboard_listener():
    global running, is_hotkey_active, is_tts_hotkey_active
    stt_blocked = False
    while running:
        try:
            tts_enabled = cfg.get("tts_enabled", True)
            tts_hk = cfg.get("tts_hotkey", "win+shift")
            tts_keys = [k for k in tts_hk.split("+") if k] if (tts_enabled and tts_hk) else []
            tts_down = bool(tts_keys) and all(_phys_down(k) for k in tts_keys)

            if tts_down and not is_tts_hotkey_active:
                is_tts_hotkey_active = True
                log(f"TTS hotkey down ({tts_hk})")
                if is_hotkey_active:
                    is_hotkey_active = False
                    recorder.cancel_recording()
            elif not tts_down and is_tts_hotkey_active:
                is_tts_hotkey_active = False
                log("TTS hotkey released - reading selection")
                stt_blocked = True
                threading.Thread(target=on_tts_hotkey, daemon=True).start()

            if is_tts_hotkey_active:
                time.sleep(0.05)
                continue

            hk = cfg.get("hotkey", "win+alt")
            keys = [k for k in hk.split("+") if k]
            pressed = bool(keys) and all(_phys_down(k) for k in keys)

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


# ─── Settings / Actions ───────────────────────────────────────────────────────

def on_save_settings(new_cfg):
    global cfg
    old_mic = cfg.get("mic_mode")
    cfg = new_cfg
    _speed_holder[0] = float(cfg.get("tts_speed", 1.0))
    save_settings(cfg)
    recorder.set_mic_mode(cfg.get("mic_mode", "always"))
    log("Settings saved.")


def on_test_voice(test_cfg):
    global cfg
    cfg = test_cfg
    _speed_holder[0] = float(cfg.get("tts_speed", 1.0))
    sample = ("این یک نمونه صدا برای تست تنظیمات است."
              if cfg.get("tts_engine") == "vertex" or cfg.get("tts_edge_voice", "").startswith("fa")
              else "This is a sample of the selected voice.")
    threading.Thread(target=speak_text, args=(sample,), daemon=True).start()


def check_for_update():
    try:
        import requests
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
        def _ver(v):
            try:
                return tuple(int(x) for x in v.split('.'))
            except Exception:
                return (0,)
        if _ver(latest) > _ver(VERSION):
            log(f"Update available: v{latest}  →  {GITHUB_URL}/releases")
    except Exception:
        pass


def on_quit():
    global running
    running = False
    try:
        if tray_icon:
            tray_icon.stop()
    except Exception:
        pass
    os._exit(0)


# ─── Main Entry Point ─────────────────────────────────────────────────────────

def main():
    global tray_icon
    _label = cfg.get("stt_engine", "google").upper() if cfg.get("prompt_mode") == "off" else f"{cfg.get('stt_engine', 'google').upper()}+{cfg.get('prompt_mode', '').upper()}"
    log(f"Starting {APP_NAME} v{VERSION} [{_label}]...")

    # 1. Start background UI loop
    start_ui_thread()

    # 2. Initialize audio input stream
    recorder.init_stream(mic_mode=cfg.get("mic_mode", "always"))

    # 3. Start WebSocket server for Chrome extension and MCP server
    ws_port = cfg.get("ws_port", 9137)
    threading.Thread(
        target=start_ws_server,
        args=(ws_port, speak_text),
        daemon=True,
        name="WebSocketServer"
    ).start()

    # 4. Start keyboard listener
    threading.Thread(
        target=keyboard_listener,
        daemon=True,
        name="KeyboardListener"
    ).start()

    # 5. Check updates
    if cfg.get("check_updates", True):
        threading.Thread(target=check_for_update, daemon=True, name="UpdateChecker").start()

    def _toggle_mic():
        new_mode = "on_demand" if cfg.get("mic_mode") == "always" else "always"
        cfg["mic_mode"] = new_mode
        recorder.set_mic_mode(new_mode)
        save_settings(cfg)
        log(f"Mic mode changed: {new_mode}")

    def _select_preset(preset_key):
        presets = cfg.get("tts_presets") or TTS_STYLE_PRESETS
        if preset_key in presets:
            p = presets[preset_key]
            cfg["tts_vertex_preset"] = preset_key
            cfg["tts_vertex_voice"] = p["voice"]
            cfg["tts_style"] = p["style"]
            save_settings(cfg)
            log(f"Voice style changed to: {p.get('name', preset_key)}")
            show_toast("Voice Style", f"Selected: {p.get('name', preset_key)}")

    def _stop_reading():
        with _tts_worker_lock:
            while not _tts_queue.empty():
                try: _tts_queue.get_nowait()
                except Exception: break
        get_player().stop()
        set_tts_state("idle")

    # 6. Run system tray (blocks on this main thread)
    tray_icon = setup_tray(
        cfg=cfg,
        on_settings=lambda: open_settings(cfg, on_save_settings, on_test_voice),
        on_history=lambda: open_history(_history, get_player, speak_text),
        on_read_clipboard=speak_clipboard,
        on_stop_reading=_stop_reading,
        on_toggle_mic=_toggle_mic,
        on_select_preset=_select_preset,
        on_quit=on_quit,
    )
    try:
        log("Entering tray_icon.run()...")
        tray_icon.run()
        log("tray_icon.run() returned normally.")
    except Exception as e:
        import traceback
        log(f"tray_icon.run() exception: {e}\n{traceback.format_exc()}")


if __name__ == "__main__":
    main()
