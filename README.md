# SpeakPaste

**Speak to type. Select to listen.** Anywhere in Windows.

![Windows](https://img.shields.io/badge/Windows-10%2F11-blue) ![License](https://img.shields.io/badge/License-Apache%202.0-green) ![Free](https://img.shields.io/badge/Engines-free%20by%20default-brightgreen)

|  | Hold | What happens |
|---|---|---|
| 🎙️ **Type with your voice** | `Win + Alt` | Speak, let go, and your words are typed wherever the cursor is |
| 🔊 **Hear any text** | `Win + Shift` | Whatever text you have selected is read aloud |

No account. No API key needed. Both default engines are free.

**[⬇ Download SpeakPaste.exe](https://github.com/mohammad-rj/speakpaste/releases/latest)** — one file, no installer.

---

## Why you might want it

- **It works in every app.** Not a browser extension and not a text box you paste into — text goes straight to your cursor in Word, VS Code, Telegram, a terminal, anywhere.
- **It speaks your language.** Strong Persian/Farsi support, plus 100+ others. The `gemini` engine detects the language from your voice, so you never set it.
- **It keeps mixed language intact.** Say *"برو روی branch جدید و commit رو merge کن"* and the English technical words stay in English instead of being mangled into Persian letters. Most dictation tools cannot do this.
- **It reads back.** Long article, PDF, someone's message — select it and listen instead of reading.

---

## Getting started

1. Download **SpeakPaste.exe** and run it. A green dot appears in your system tray.
2. Hold **Win+Alt**, say something, release. The text is typed at your cursor.
3. Select any text and press **Win+Shift**. It is read aloud.

That is the whole product. Everything below is optional tuning.

To change anything, right-click the tray icon → **Settings**.

<details>
<summary>Run from source instead</summary>

```bash
git clone https://github.com/mohammad-rj/speakpaste.git
cd speakpaste
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python speakpaste.py
```
</details>

---

## Typing with your voice

Hold the hotkey while you talk, release when you are done. The transcript is typed
into the focused window through Windows itself, so it behaves like a real keyboard
and works even in apps that block pasting.

**Choose an engine** in Settings → *Speech → Text*:

| Engine | Cost | Needs | Good for |
|---|---|---|---|
| **`gemini`** | Free credit | Google credential *or* free API key | **Best Persian, detects language by itself, keeps English words in English** |
| `google` | Free | Nothing | Fast and accurate, but you must set the language yourself |
| `google-cloud` | Free tier | API key | Official endpoint, more reliable than `google` |
| `groq` | Free tier | API key | Whisper, 50+ languages |
| `google-ext` | Free | Chrome running | Browser-based fallback |

`google` is the default because it needs no setup. Switch to `gemini` if you mix
Persian and English, or if you do not want to think about language settings at all.

**Optional AI rewriting** — in Settings → *Prompt*, SpeakPaste can send what you said
through Gemini and type a clean English coding prompt instead of a raw transcript.
Useful when you talk to an AI assistant in your own language but want it to receive
polished English.

---

## Listening to text

Select text anywhere and press **Win+Shift**. A small player appears in the corner:

<p align="center">
  <img src="docs/screenshots/player.png" alt="SpeakPaste's floating player widget">
</p>

- **⏸ / ▶** pause and resume, **⏹** stop
- **◀◀ / ▶▶** jump between parts of a long text — pressing back mid-part replays that part from its start
- **Seek bar** — click anywhere to jump; the tick marks show where each part begins
- **Speed** — 0.5× to 2×, changes instantly, and the voice does not turn into a chipmunk
- Closes itself 30 seconds after it finishes

Your clipboard is not disturbed. SpeakPaste copies the selection, reads it, and puts
your previous clipboard content back exactly as it was.

Long text starts playing after a few seconds rather than making you wait for the
whole thing: it is split at sentence boundaries, the next parts are prepared while
the current one plays, and the counter shows which part you are on (`2/5`).
Missed something? Press ◀◀ to hear that part again.

| Engine | Cost | Needs | Voice |
|---|---|---|---|
| **`edge`** | Free | Nothing | Microsoft neural voices — Persian, English, Turkish, Arabic |
| `vertex` | Free credit | Google credential | Gemini TTS, and you can describe *how* it should read in plain words |

The tray menu also has **Read clipboard aloud** for cases where copying with Ctrl+C
does not work, such as inside a terminal.

---

## Settings

Right-click the tray icon → **Settings**. Three tabs:

**Speech → Text** — engine, API keys, and the optional AI prompt rewriting.

**Text → Speech** — voice engine, voice, reading speed, the player widget, and a
**Test voice** button so you can hear a change without closing the window.

**General** — hotkeys, language, microphone mode, update checks.

A few things worth knowing:

- **Language** only matters for engines that cannot detect it themselves. With
  `gemini` it is ignored entirely. With `google`, either set it to your usual
  language or tick *Follow Windows keyboard layout* to switch with Alt+Shift.
- **Microphone mode** — *Always on* keeps a 500 ms buffer so the start of your
  sentence is never clipped. *On demand* only opens the mic while the hotkey is held,
  which is the more private option. Toggle it from the tray at any time.
- Settings live in `settings.json` next to the exe.

---

## History

Tray → **History** shows everything from this session — what you dictated and what
was read aloud, newest first, Persian lines right-aligned.

**Click any line to copy it back to your clipboard.** History survives restarts.

---

## Privacy

Audio and text are sent to whichever engine you pick, and nowhere else. There is no
SpeakPaste server, no account, and no telemetry.

Keys and history are stored in plain files (`settings.json`, `history.json`) beside
the executable, so treat that folder as private and do not share it.

---

## Build it yourself

```bash
pip install -r requirements.txt
pip install pyinstaller
pyinstaller speakpaste.spec
```

Output: `dist/SpeakPaste.exe`

---

## License

SpeakPaste's own source is [Apache License 2.0](LICENSE).

The built executable bundles third-party components under their own licenses,
including LGPL libraries (`pystray`, `edge-tts`, `libsndfile`) and a GPL-2.0 FLAC
encoder used by the `google` engine. [`THIRD-PARTY-NOTICES.md`](THIRD-PARTY-NOTICES.md)
lists every dependency with its verified license and explains how to rebuild or
relink the executable.
