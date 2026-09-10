import os
import sys
import json
import time
import tempfile
import datetime
from collections import deque

APP_NAME    = "SpeakPaste"
VERSION     = "1.2.0"
GITHUB_REPO = "mohammad-rj/SpeakPaste"
GITHUB_URL  = f"https://github.com/{GITHUB_REPO}"

APP_DIR = os.path.dirname(os.path.abspath(sys.argv[0]))
if not os.access(APP_DIR, os.W_OK):
    APP_DIR = os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), APP_NAME)
    os.makedirs(APP_DIR, exist_ok=True)

SETTINGS_FILE  = os.path.join(APP_DIR, "settings.json")
HISTORY_FILE   = os.path.join(APP_DIR, "history.json")
LOG_FILE       = os.path.join(APP_DIR, "speakpaste.log")
RECORDINGS_DIR = os.path.join(APP_DIR, "recordings")
os.makedirs(RECORDINGS_DIR, exist_ok=True)

TTS_CACHE_DIR  = os.path.join(tempfile.gettempdir(), "speakpaste-tts")
os.makedirs(TTS_CACHE_DIR, exist_ok=True)

# ─── Logging ──────────────────────────────────────────────────────────────────

logs = deque(maxlen=25)
_log_last_t = [None]  # holds monotonic float of previous log line; None = reset

def log(msg):
    now_dt = datetime.datetime.now()
    now_m  = time.monotonic()
    ts = now_dt.strftime("%H:%M:%S.%f")[:12]
    if _log_last_t[0] is not None:
        delta_ms = int((now_m - _log_last_t[0]) * 1000)
        prefix = f"{ts}  (+{delta_ms:4d}ms) "
    else:
        prefix = f"{ts}  (  start) "
    _log_last_t[0] = now_m

    line = f"{prefix} {msg}"
    try:
        print(line)
    except UnicodeEncodeError:
        try:
            if hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write((line + "\n").encode("utf-8", errors="replace"))
                sys.stdout.buffer.flush()
            else:
                print(line.encode("ascii", errors="replace").decode("ascii"))
        except Exception:
            pass
    except Exception:
        pass
    logs.append(line)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


# ─── Presets & Prompts ────────────────────────────────────────────────────────

GEMINI_DEFAULT_SYSTEM_PROMPT = (
    "You are a transcription assistant. Your job is to take raw transcribed speech "
    "and turn it into a clear, professional prompt ready to send to an AI model.\n"
    "Rules:\n"
    "- If speech is in Persian, output in Persian. If in English, output in English.\n"
    "- Clean up stumbles, filler words, repetitions, and hesitation.\n"
    "- Fix punctuation and sentence structure.\n"
    "- Preserve the original meaning and intent completely.\n"
    "- Output ONLY the final prompt text. No explanations, no markdown formatting."
)

GEMINI_STT_DEFAULT_MODEL = "gemini-2.5-flash"
GEMINI_FLASH_MODEL       = "gemini-2.5-flash"
GEMINI_LITE_MODEL        = "gemini-2.5-flash-lite"

TTS_DEFAULT_STYLE = (
    "Speak in a calm, focused, totally natural Persian tone. No drama, no theatrical performance, "
    "a comfortable brisk pace you could listen to all day."
)

TTS_STYLE_PRESETS = {
    "charon_calm": {
        "name": "Charon (Calm, deep, serious - default)",
        "voice": "Charon",
        "style": (
            "Speak in a calm, serious, steady Persian tone. "
            "Low energy, completely matter-of-fact and direct, zero excitement, no drama. "
            "Like a senior engineer speaking quietly and soberly at his desk."
        ),
    },
    "fenrir_pragmatic": {
        "name": "Fenrir (Pragmatic, articulate, confident)",
        "voice": "Fenrir",
        "style": (
            "Speak in a crisp, business-casual Persian tone. Objective, pragmatic, concise, neutral emotion. "
            "Clear articulation, normal comfortable speed, purely factual without any emotional flair."
        ),
    },
    "aoede_neutral": {
        "name": "Aoede (Neutral, smooth, composed)",
        "voice": "Aoede",
        "style": (
            "Speak in a completely natural, neutral Persian tone. Simple, sober, understated, zero hype, "
            "like giving a brief factual daily status."
        ),
    },
    "puck_quick": {
        "name": "Puck (Casual, brisk, modern)",
        "voice": "Puck",
        "style": (
            "Speak at a brisk, natural pace in casual Persian. Straight to the point, relaxed, "
            "no theatrical performance, just clean and direct conversation."
        ),
    },
    "custom": {
        "name": "Custom...",
        "voice": "Charon",
        "style": TTS_DEFAULT_STYLE,
    },
}

TTS_EDGE_VOICES = [
    "fa-IR-FaridNeural", "fa-IR-DilaraNeural",
    "en-US-AndrewNeural", "en-US-AriaNeural", "en-US-GuyNeural",
    "tr-TR-AhmetNeural", "tr-TR-EmelNeural", "ar-SA-HamedNeural",
]
TTS_VERTEX_VOICES = ["Charon", "Fenrir", "Aoede", "Puck", "Zubenelgenubi", "Achird", "Sadachbia", "Umbriel",
                     "Kore", "Leda", "Orus"]
TTS_VERTEX_MODELS = ["gemini-3.1-flash-tts-preview", "gemini-2.5-flash-tts", "gemini-2.5-pro-tts"]

_DEFAULTS = {
    "stt_engine":                "google",     # google | gemini | groq | google-cloud | google-ext
    "prompt_mode":               "off",        # off | gemini-lite | gemini-flash
    "hotkey":                    "win+alt",
    "language":                  "fa",
    "mic_mode":                  "always",
    "groq_api_key":              "",
    "model":                     "whisper-large-v3-turbo",
    "google_cloud_api_key":      "",
    "ws_port":                   9137,
    "check_updates":             True,
    # ── Gemini Prompt Mode / TTS ────────────────────────────────────────────────
    "gemini_api_key":            "",
    "gemini_base_url":           "",
    "gemini_auth_token":         "",
    "gemini_system_prompt":      GEMINI_DEFAULT_SYSTEM_PROMPT,
    "lang_mode":                 "fixed",
    "gemini_thinking_level":     "LOW",
    "gemini_media_resolution":   "LOW",
    "gemini_stt_model":          GEMINI_STT_DEFAULT_MODEL,
    "inject_mode":               "auto",       # auto | type | paste
    "notify_errors":             True,
    # ── Text-to-Speech ──────────────────────────────────────────────────────────
    "tts_enabled":               True,
    "tts_hotkey":                "win+shift",
    "tts_engine":                "vertex",     # edge | vertex
    "tts_edge_voice":            "fa-IR-FaridNeural",
    "tts_vertex_preset":         "charon_calm",
    "tts_vertex_voice":          "Charon",
    "tts_vertex_model":          "gemini-3.1-flash-tts-preview",
    "tts_vertex_cred":           "",
    "tts_vertex_project":        "",
    "tts_style":                 TTS_STYLE_PRESETS["charon_calm"]["style"],
    "tts_speed":                 1.0,
    "tts_popup":                 True,
    "tts_popup_autoclose":       30,
    "tts_chunk_secs":            50,
    "tts_first_chunk_secs":      25,
    "tts_presets":               TTS_STYLE_PRESETS,
}


def validate_ws_port(value):
    if type(value) is int and 1024 <= value <= 65535:
        return value
    return 9137


def load_settings():
    if os.path.exists(SETTINGS_FILE):
        try:
            with open(SETTINGS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            cfg = {**_DEFAULTS, **data}
            cfg["ws_port"] = validate_ws_port(cfg["ws_port"])
            if cfg.get("stt_engine") == "voice_clip":
                cfg["stt_engine"] = "google"
            presets = cfg.get("tts_presets") or {}
            for pk, pv in TTS_STYLE_PRESETS.items():
                if pk in presets:
                    presets[pk]["name"] = pv["name"]
                else:
                    presets[pk] = dict(pv)
            cfg["tts_presets"] = presets
            return cfg
        except Exception as e:
            log(f"Settings load error: {e}")
    return dict(_DEFAULTS)


def save_settings(cfg):
    try:
        with open(SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, ensure_ascii=False)
    except Exception as e:
        log(f"Settings save error: {e}")


def load_history(max_items=50):
    if os.path.exists(HISTORY_FILE):
        try:
            with open(HISTORY_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, list):
                return deque(data[:max_items], maxlen=max_items)
        except Exception as e:
            log(f"History load error: {e}")
    return deque(maxlen=max_items)


def save_history(history_deque):
    try:
        tmp = HISTORY_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(list(history_deque), f, ensure_ascii=False)
        os.replace(tmp, HISTORY_FILE)
    except Exception as e:
        log(f"History save error: {e}")
