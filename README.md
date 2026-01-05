# SpeakPaste 🎙️

**Talk → Text → Paste. Anywhere.**

Hold a hotkey, speak, release — your words appear instantly wherever your cursor is. No more typing. Just talk.

![Windows](https://img.shields.io/badge/Windows-10%2F11-blue) ![License](https://img.shields.io/badge/License-MIT-green) ![Groq](https://img.shields.io/badge/Powered%20by-Groq%20Whisper-orange)

## Why SpeakPaste?

- ⚡ **Instant** — Transcription in ~1 second (Groq is fast!)
- 🌍 **50+ Languages** — Persian, Arabic, English, German, French, Spanish, Chinese, Japanese...
- 🎯 **Works Everywhere** — Browser, IDE, Notepad, Discord, Slack, anywhere you can type
- 🆓 **Free** — Groq gives you ~8 hours of transcription daily for free
- 🖥️ **System Tray** — Runs quietly in background, shows status on hover
- 🚀 **Startup Option** — Auto-start with Windows

## Quick Start (2 minutes)

### 1. Download
Download `SpeakPaste.exe` from [Releases](../../releases)

### 2. Get Free API Key
1. Go to [console.groq.com/keys](https://console.groq.com/keys)
2. Sign up with Google (free)
3. Click "Create API Key"
4. Copy the key

### 3. Configure
Create a file named `.env` in the same folder as `SpeakPaste.exe`:
```
GROQ_API_KEY=your_api_key_here
```

### 4. Run
Double-click `SpeakPaste.exe` — green icon appears in system tray.

### 5. Use
- Hold **Win+Alt** → Speak → Release → Text appears!
- Hover over tray icon to see status
- Right-click tray icon for options

## Configuration

Edit `.env` file to customize:

```env
GROQ_API_KEY=gsk_xxxxx          # Required - your Groq API key
HOTKEY=win+alt                   # Options: ctrl+alt, ctrl+shift+space, f9
LANGUAGE=fa                      # fa=Persian, en=English, ar=Arabic, de=German...
MODEL=whisper-large-v3-turbo     # Fastest model
```

## Supported Languages

English, Persian/Farsi, Arabic, German, French, Spanish, Portuguese, Italian, Dutch, Russian, Chinese, Japanese, Korean, Turkish, Hindi, and [50+ more](https://github.com/openai/whisper#available-models-and-languages).

---

## For Developers

Want to build from source or contribute?

### Requirements
- Python 3.8+
- Windows 10/11

### Setup
```bash
git clone https://github.com/user/speakpaste.git
cd speakpaste
pip install -r requirements.txt
cp .env.example .env
# Edit .env with your API key
python speakpaste.py
```

### Build Executable
```bash
pip install pyinstaller
pyinstaller --onefile --noconsole --name SpeakPaste speakpaste.py
# Output: dist/SpeakPaste.exe
```

## How It Works

1. Captures audio from microphone while hotkey is held
2. Sends audio to Groq's Whisper API for transcription
3. Copies result to clipboard
4. Simulates Ctrl+V to paste at cursor position

## License

MIT — Use it, modify it, share it.

---

**Made with ❤️ for people who hate typing**
