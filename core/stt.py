import os
import time
import json
import base64
import urllib.request
import urllib.parse
import urllib.error
import shutil
import datetime
import requests
from .config import log, RECORDINGS_DIR, VERSION, GEMINI_DEFAULT_SYSTEM_PROMPT, GEMINI_FLASH_MODEL, GEMINI_LITE_MODEL

_last_error = [None]

_THINKING_BUDGETS = {"MINIMAL": 512, "LOW": 1024, "MEDIUM": 4096, "HIGH": 8192}

def _thinking_budget(level):
    return _THINKING_BUDGETS.get((level or "LOW").upper(), 1024)


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


def vertex_access_token(cred_path):
    with open(cred_path, encoding="utf-8") as f:
        cred = json.load(f)

    if cred.get("type") == "authorized_user" or "refresh_token" in cred:
        body = urllib.parse.urlencode({
            "client_id":     cred["client_id"],
            "client_secret": cred["client_secret"],
            "refresh_token": cred["refresh_token"],
            "grant_type":    "refresh_token",
        }).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)["access_token"], cred.get("quota_project_id", "")

    # service_account
    import base64 as _b64
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import padding

    def _seg(d):
        return _b64.urlsafe_b64encode(json.dumps(d).encode()).rstrip(b"=")

    now = int(time.time())
    claim = {
        "iss":   cred["client_email"],
        "scope": "https://www.googleapis.com/auth/cloud-platform",
        "aud":   "https://oauth2.googleapis.com/token",
        "exp":   now + 3600,
        "iat":   now,
    }
    signing_input = _seg({"alg": "RS256", "typ": "JWT"}) + b"." + _seg(claim)
    key = serialization.load_pem_private_key(cred["private_key"].encode(), None)
    sig = key.sign(signing_input, padding.PKCS1v15(), hashes.SHA256())
    jwt = signing_input + b"." + _b64.urlsafe_b64encode(sig).rstrip(b"=")

    body = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion":  jwt.decode(),
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=body, method="POST")
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)["access_token"], cred.get("project_id", "")


def gemini_endpoint(model, cfg):
    base_url = cfg["gemini_base_url"] if "gemini_base_url" in cfg else (os.environ.get("SPEAKPASTE_GEMINI_BASE_URL") or "")
    base_url = (base_url or "").rstrip("/")
    auth_tok = cfg["gemini_auth_token"] if "gemini_auth_token" in cfg else (os.environ.get("SPEAKPASTE_GEMINI_TOKEN") or "")
    api_key  = cfg.get("gemini_api_key", "")
    cred     = cfg.get("tts_vertex_cred", "")
    project  = cfg.get("tts_vertex_project", "")

    if base_url:
        headers = {
            "Content-Type": "application/json",
            "User-Agent": f"SpeakPaste/{VERSION}",
        }
        if auth_tok:
            headers["Authorization"] = f"Bearer {auth_tok}"
        elif api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        return f"{base_url}/models/{model}:generateContent", headers

    if cred and os.path.exists(cred):
        try:
            token, proj = vertex_access_token(cred)
            p = project or proj
            if p:
                return (
                    f"https://aiplatform.googleapis.com/v1/projects/{p}/locations/global/publishers/google/models/{model}:generateContent",
                    {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
                )
        except Exception as e:
            log(f"Vertex auth failed, falling back to API key: {str(e)[:70]}")

    if api_key:
        adapter = PROVIDER_ADAPTERS["gemini"]
        return adapter.get_url(model, api_key), adapter.get_headers()

    return None, None


# ─── Transcription Engines ───────────────────────────────────────────────────

def transcribe_google_direct(audio_path, session_lang="fa"):
    log("Transcribing (Google)...")
    try:
        import speech_recognition as sr
        r = sr.Recognizer()
        with sr.AudioFile(audio_path) as source:
            audio = r.record(source)
        text = r.recognize_google(audio, language=session_lang)
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


def transcribe_gemini(audio_path, cfg):
    log("Transcribing (Gemini, auto language)...")
    stt_model = cfg.get("gemini_stt_model", "gemini-2.5-flash")
    try:
        with open(audio_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "contents": [{
                "parts": [
                    {"inlineData": {"mimeType": "audio/wav", "data": audio_b64}},
                    {"text": GEMINI_STT_PROMPT},
                ]
            }],
            "generationConfig": {
                "thinkingConfig": {"thinkingBudget": _thinking_budget("MINIMAL")},
                "temperature": 0.0,
            },
        }
        url, headers = gemini_endpoint(stt_model, cfg)
        if not url:
            _last_error[0] = "No Gemini endpoint configured (Settings -> Voice Typing)"
            return None

        req = urllib.request.Request(url, data=json.dumps(payload).encode(), method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.load(resp)
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        log(f">> {text}")
        return text or None
    except Exception as e:
        log(f"Gemini transcription error: {e}")
        _last_error[0] = f"Gemini STT failed: {e}"
        return None
    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass


def transcribe_google_cloud(audio_path, api_key, session_lang="fa"):
    if not api_key:
        log("Google Cloud STT: API key missing (open Settings)")
        _last_error[0] = "Google Cloud API key is empty (Settings -> Voice Typing)"
        try:
            os.unlink(audio_path)
        except Exception:
            pass
        return None

    log("Transcribing (Google Cloud)...")
    url = f"https://speech.googleapis.com/v1/speech:recognize?key={api_key}"
    try:
        with open(audio_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("utf-8")

        payload = {
            "config": {
                "encoding":        "LINEAR16",
                "sampleRateHertz": 16000,
                "languageCode":    "fa-IR" if session_lang == "fa" else "en-US",
                "model":           "default",
            },
            "audio": {"content": audio_b64},
        }
        resp = requests.post(url, json=payload, timeout=30)
        if resp.status_code == 200:
            results = resp.json().get("results", [])
            if results:
                text = results[0]["alternatives"][0]["transcript"].strip()
                log(f">> {text}")
                return text
            log("Google Cloud: empty transcription result")
            return None
        log(f"Google Cloud error {resp.status_code}: {resp.text[:120]}")
        _last_error[0] = f"Google Cloud error {resp.status_code}: {resp.text[:80]}"
        return None
    except Exception as e:
        log(f"Google Cloud error: {e}")
        _last_error[0] = f"Google Cloud error: {e}"
        return None
    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass


def transcribe_groq(audio_path, api_key, model="whisper-large-v3-turbo", session_lang="fa"):
    if not api_key:
        log("Groq API key missing (open Settings)")
        _last_error[0] = "Groq API key is empty (Settings -> Voice Typing)"
        try:
            os.unlink(audio_path)
        except Exception:
            pass
        return None

    log("Transcribing (Groq)...")
    url = "https://api.groq.com/openai/v1/audio/transcriptions"
    headers = {"Authorization": f"Bearer {api_key}"}
    data = {"model": model, "language": session_lang}
    try:
        with open(audio_path, "rb") as f:
            files = {"file": (os.path.basename(audio_path), f, "audio/wav")}
            resp = requests.post(url, headers=headers, data=data, files=files, timeout=30)
        if resp.status_code == 200:
            text = resp.json().get("text", "").strip()
            log(f">> {text}")
            return text
        log(f"Groq error {resp.status_code}: {resp.text}")
        _last_error[0] = f"Groq error {resp.status_code}: {resp.text[:80]}"
        return None
    except Exception as e:
        log(f"Groq error: {e}")
        _last_error[0] = f"Groq error: {e}"
        return None
    finally:
        try:
            os.unlink(audio_path)
        except Exception:
            pass


# ─── Prompt Functions ────────────────────────────────────────────────────────

def gemini_lite_prompt(raw_text, cfg):
    if not raw_text:
        return None
    url, headers = gemini_endpoint(GEMINI_LITE_MODEL, cfg)
    if not url:
        return raw_text
    log("Refining transcript to prompt (Gemini Flash Lite)...")
    adapter = PROVIDER_ADAPTERS["gemini"]
    sys_prompt = cfg.get("gemini_system_prompt", GEMINI_DEFAULT_SYSTEM_PROMPT)
    th_level   = cfg.get("gemini_thinking_level", "LOW")
    m_res      = cfg.get("gemini_media_resolution", "LOW")
    try:
        resp = requests.post(
            url, headers=headers,
            json=adapter.build_text_request(sys_prompt, raw_text, th_level, m_res),
            timeout=15,
        )
        if resp.status_code == 200:
            result = adapter.parse_response(resp.json())
            if result:
                log(f">> {result}")
                return result
        return raw_text
    except Exception as e:
        log(f"Gemini Lite error: {e}")
        return raw_text


def gemini_flash_prompt(wav_path, cfg):
    url, headers = gemini_endpoint(GEMINI_FLASH_MODEL, cfg)
    if not url:
        log("Gemini: no credential and no API key (open Settings)")
        _last_error[0] = "No Gemini credentials configured (Settings -> Prompt)"
        try:
            os.unlink(wav_path)
        except Exception:
            pass
        return None
    log("Converting voice to prompt (Gemini Flash)...")
    adapter = PROVIDER_ADAPTERS["gemini"]
    sys_prompt = cfg.get("gemini_system_prompt", GEMINI_DEFAULT_SYSTEM_PROMPT)
    th_level   = cfg.get("gemini_thinking_level", "LOW")
    m_res      = cfg.get("gemini_media_resolution", "LOW")
    try:
        with open(wav_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("utf-8")
        resp = requests.post(
            url, headers=headers,
            json=adapter.build_audio_request(sys_prompt, audio_b64, th_level, m_res),
            timeout=30,
        )
        if resp.status_code == 200:
            result = adapter.parse_response(resp.json())
            if result:
                log(f">> {result}")
                return result
            return None
        log(f"Gemini error {resp.status_code}: {resp.text[:120]}")
        _last_error[0] = f"Gemini Flash error {resp.status_code}: {resp.text[:80]}"
        return None
    except Exception as e:
        log(f"Gemini error: {e}")
        _last_error[0] = f"Gemini Flash error: {e}"
        return None
    finally:
        try:
            os.unlink(wav_path)
        except Exception:
            pass
