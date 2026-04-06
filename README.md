# SpeakPaste

**Talk → Text → Paste. Anywhere.**

Hold a hotkey, speak, release — your words appear instantly wherever your cursor is.

![Windows](https://img.shields.io/badge/Windows-10%2F11-blue) ![License](https://img.shields.io/badge/License-MIT-green)

## Download

Grab the latest **[SpeakPaste.exe](https://github.com/mohammad-rj/speakpaste/releases/latest)** — single file, no install.

---

## Engines

| Engine | Output | Free | Requires |
|--------|--------|------|----------|
| `google` | Transcribed text | Yes | Nothing |
| `google-cloud` | Transcribed text | Free tier | API key |
| `groq` | Transcribed text | ~8h/day free | API key |
| `google-ext` | Transcribed text | Yes | Chrome in background |
| `gemini-lite` | English programming prompt | Free tier | Gemini API key |
| `gemini-flash` | English programming prompt | Free tier | Gemini API key |

Default: `google` — no key, no setup.

---

## Quick Start

### Option A — Exe (recommended)

1. Download **SpeakPaste.exe** from [Releases](https://github.com/mohammad-rj/speakpaste/releases/latest)
2. Run it — green icon appears in system tray
3. Right-click → **Settings** to pick your engine and configure
4. Hold **Win+Alt**, speak, release — text appears at cursor

### Option B — Run from source

```bash
git clone https://github.com/mohammad-rj/speakpaste.git
cd speakpaste
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python speakpaste.py
```

---

## Settings

All configuration is done via the built-in **Settings window** (tray → Settings):

- **Engine** — pick your STT backend; API key field expands inline when needed
- **Hotkey** — default `win+alt`, change to anything
- **Language** — e.g. `fa`, `en`, `ar` (or full BCP-47 like `fa-IR`)
- **Follow Windows keyboard layout** — when checked, language is detected automatically from your active keyboard layout at the moment you press the hotkey; no manual switching needed (see below)
- **Microphone mode** — Always-on or On-demand (toggle live from tray)
- **Check for updates** — notified via tray tooltip on startup

Settings are saved to `settings.json` next to the exe.

---

## Auto language detection

Enable **Follow Windows keyboard layout** in Settings to let SpeakPaste detect your language automatically.

- Switch to Persian layout with **Alt+Shift** → hold hotkey → speak Persian
- Switch to English layout → hold hotkey → speak English
- No need to open Settings to change the language — just toggle your keyboard layout as usual

The language is read once when you press the hotkey and stays fixed for the entire recording session. If your layout isn't recognised, it falls back to the language set in the Language field.

Supported layouts: Persian/Farsi, English, Arabic, Turkish, German, French, Russian, Portuguese, Spanish, Japanese, Korean, Chinese.

---

## Engine details

**`google`** (default — recommended for most users)
- Google's speech API via [SpeechRecognition](https://github.com/Uberi/speech_recognition)
- Same engine as Android voice typing — excellent Persian/Farsi support
- Unofficial endpoint, no API key, no Chrome required
- Caveat: unofficial, could change without notice

**`google-cloud`**
- Official [Google Cloud Speech-to-Text](https://cloud.google.com/speech-to-text) REST API
- Higher accuracy and reliability than the unofficial engine
- Free tier: 60 min/month — sufficient for personal use
- Get a key: [console.cloud.google.com](https://console.cloud.google.com) → Speech-to-Text API → Credentials

**`groq`**
- Records audio → sends to [Groq Whisper API](https://console.groq.com)
- Free API key, ~8 hours/day limit
- Very accurate, 50+ languages

**`google-ext`**
- Chrome Manifest V3 extension with Offscreen Document
- `webkitSpeechRecognition` running fully hidden in background
- Requires Chrome installed and running
- Setup: `chrome://extensions` → Developer mode → Load unpacked → select `extension/`

**`gemini-lite`**
- Records audio → Google STT (free) → text → [Gemini Flash Lite](https://aistudio.google.com) → English programming prompt
- Speak in any language — output is always a clean English prompt for your AI coding assistant
- Get a free key: [aistudio.google.com](https://aistudio.google.com) → Get API key
- System prompt is fully customizable in Settings

**`gemini-flash`**
- Records audio → sends WAV directly to [Gemini Flash](https://aistudio.google.com) (multimodal) → English programming prompt
- Skips the STT step entirely — Gemini understands voice directly
- Same Gemini API key as `gemini-lite`; configurable system prompt

---

## Microphone mode

| Mode | Mic | Pre-roll | Privacy |
|------|-----|----------|---------|
| Always-on | Open all the time | 500ms buffer — no cut-off | Mic icon always visible |
| On-demand | Opens only while hotkey held | None | Closed when idle |

Toggle live from tray without restarting.

---

## Build from source

```bash
pip install pyinstaller
pyinstaller speakpaste.spec
```

Output: `dist/SpeakPaste.exe`

## License

MIT
