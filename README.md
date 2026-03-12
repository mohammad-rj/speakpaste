# SpeakPaste

**Talk → Text → Paste. Anywhere.**

Hold a hotkey, speak, release — your words appear instantly wherever your cursor is.

![Windows](https://img.shields.io/badge/Windows-10%2F11-blue) ![License](https://img.shields.io/badge/License-MIT-green)

## Download

Grab the latest **[SpeakPaste.exe](https://github.com/mohammad-rj/speakpaste/releases/latest)** — single file, no install.

---

## Engines

| Engine | Quality | Free | Requires |
|--------|---------|------|----------|
| `google` | Google (same as Android) | Yes | Nothing |
| `google-cloud` | Google Cloud STT (official) | Free tier | API key |
| `groq` | Whisper large-v3-turbo | ~8h/day free | API key |
| `google-ext` | Google (Chrome) | Yes | Chrome in background |

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
- **Microphone mode** — Always-on or On-demand (toggle live from tray)
- **Check for updates** — notified via tray tooltip on startup

Settings are saved to `settings.json` next to the exe.

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
