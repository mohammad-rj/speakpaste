# SpeakPaste

**Talk → Text → Paste. Anywhere.**

Hold a hotkey, speak, release — your words appear instantly wherever your cursor is.

![Windows](https://img.shields.io/badge/Windows-10%2F11-blue) ![License](https://img.shields.io/badge/License-MIT-green)

## Engines

| Engine | Quality | Free | Chrome needed |
|--------|---------|------|---------------|
| `google` | Google (same as Android) | Yes | No |
| `groq` | Whisper large-v3-turbo | ~8h/day free | No |
| `google-ext` | Google (streaming-ready) | Yes | Yes (background) |

Default: `google` — Google's speech API, no API key, no Chrome.

## Quick Start

### 1. Install

```bash
git clone https://github.com/mohammad-rj/speakpaste.git
cd speakpaste
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
```

Default `.env` works out of the box with `ENGINE=google`. No API key needed.

### 3. Run

```bash
python speakpaste.py
```

Green icon appears in system tray. Hold **Win+Alt**, speak, release.

---

## Configuration

Edit `.env`:

```env
# Engine: google | groq | google-ext
ENGINE=google

# Language (BCP-47 for google, ISO-639-1 for groq)
LANGUAGE=fa-IR       # Persian
# LANGUAGE=en-US    # English
# LANGUAGE=ar-SA    # Arabic

# Hotkey
HOTKEY=win+alt

# Groq settings (only needed for ENGINE=groq)
GROQ_API_KEY=your_key_here
MODEL=whisper-large-v3-turbo
```

### Engine details

**`google`** (recommended)
- Uses Google's speech API directly via [SpeechRecognition](https://github.com/Uberi/speech_recognition) library
- Same engine as Android voice typing
- Unofficial endpoint — no API key, no sign-up, no Chrome
- Caveat: unofficial, could break without notice

**`groq`**
- Records audio → sends to [Groq Whisper API](https://console.groq.com)
- Requires free API key
- Very accurate, 50+ languages

**`google-ext`**
- Chrome Manifest V3 extension with Offscreen Document
- `webkitSpeechRecognition` running fully hidden (no Chrome window)
- Requires Chrome installed and running in background
- Setup: `chrome://extensions` → Developer mode → Load unpacked → select `extension/` folder

---

## Build Executable

```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name SpeakPaste speakpaste.py
```

Output: `dist/SpeakPaste.exe` — copy `.env` next to it.

## License

MIT
