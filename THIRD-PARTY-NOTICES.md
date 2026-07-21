# Third-Party Notices

SpeakPaste's own source code is licensed under the Apache License, Version 2.0
(see [`LICENSE`](LICENSE)). Like almost every Python application, it depends on
a number of third-party packages, and the built `SpeakPaste.exe` (produced by
PyInstaller) bundles those packages' code — and in a few cases their compiled
binaries — inside the single exe file.

This document lists every dependency declared in `requirements.txt`, plus the
runtime libraries those dependencies pull in themselves, with the license each
one actually carries (verified by reading its installed package metadata and
license file under `.venv/Lib/site-packages/`, not assumed from name or
reputation). It then explains, in the last section, exactly what that means
for the licensing of the distributed `.exe`.

## Direct dependencies (from `requirements.txt`)

### Permissive (MIT / BSD / Apache-2.0) — no obligations beyond attribution

| Package | Version | Verified license | Project page |
|---|---|---|---|
| keyboard | 0.13.5 | MIT | https://github.com/boppreh/keyboard |
| sounddevice | 0.5.5 | MIT | https://github.com/spatialaudio/python-sounddevice |
| soundfile | 0.13.1 | BSD-3-Clause | https://github.com/bastibe/python-soundfile |
| numpy | 2.4.3 | BSD-3-Clause (a few vendored internal components under 0BSD/MIT/Zlib/CC0-1.0) | https://numpy.org |
| requests | 2.32.5 | Apache-2.0 | https://github.com/psf/requests |
| pillow | 12.1.1 | MIT-CMU (the historical PIL license; Pillow's own name for it) | https://github.com/python-pillow/Pillow |
| python-dotenv | 1.2.2 | BSD-3-Clause | https://github.com/theskumar/python-dotenv |
| websockets | 16.0 | BSD-3-Clause | https://github.com/python-websockets/websockets |
| SpeechRecognition | 3.15.1 | BSD-3-Clause **for the Python package itself** — see the FLAC note below, this one is not simply permissive end-to-end | https://github.com/Uberi/speech_recognition |

### Copyleft (LGPL) — carry a relink/rebuild obligation

| Package | Version | Verified license | Project page |
|---|---|---|---|
| pystray | 0.19.5 | **LGPL-3.0** (`COPYING` = GPLv3 text, `COPYING.LGPL` = the LGPLv3 additional permissions that actually apply — this is the standard two-file LGPLv3 distribution pattern, not a GPL package) | https://github.com/moses-palmer/pystray |
| edge-tts | 7.2.8 | **LGPL-3.0** for the package as a whole (its `LICENSE` file says so explicitly); one internal file, `srt_composer.py`, is separately MIT-licensed | https://github.com/rany2/edge-tts |

### Build-only tool (not itself distributed)

| Package | Version | Verified license | Project page |
|---|---|---|---|
| pyinstaller | 6.19.0 | GPL-2.0-or-later, **with PyInstaller's own bootloader exception** that explicitly permits using it to build and distribute non-free/commercial programs | https://pyinstaller.org |

PyInstaller is the build tool used to freeze `speakpaste.py` into `SpeakPaste.exe`; none of PyInstaller's own source code ends up inside the produced exe (only its small precompiled "bootloader" stub does, and that stub is exactly what the bootloader exception covers), so its GPL terms do not attach to the output.

## Transitive runtime dependencies

These aren't listed in `requirements.txt` directly, but they are installed
automatically because `requests` and `edge-tts` depend on them, and because
they are genuinely imported at runtime, PyInstaller's dependency analysis
collects them into the frozen exe as well. All verified permissive:

| Package | Verified license |
|---|---|
| aiohttp | Apache-2.0 AND MIT (bundles the vendored `llhttp` parser under its own MIT-style license) |
| aiosignal | Apache-2.0 |
| aiohappyeyeballs | PSF-2.0 |
| attrs | MIT |
| certifi | MPL-2.0 (file-level weak copyleft — see note below) |
| charset-normalizer | MIT |
| frozenlist | Apache-2.0 |
| idna | BSD-3-Clause |
| multidict | Apache-2.0 |
| propcache | Apache-2.0 |
| six | MIT |
| tabulate | MIT |
| typing_extensions | PSF-2.0 |
| urllib3 | MIT |
| yarl | Apache-2.0 |

`certifi` is MPL-2.0, which is a *file-level* weak copyleft license: it only
requires that if you modify an MPL-covered file, you publish your changes to
that specific file. SpeakPaste does not modify certifi's source, so no
additional obligation arises from it; it is listed here for completeness
rather than as a concern.

## Compiled native libraries bundled as DLLs

Two of the pure-Python audio packages above ship a compiled native library
alongside themselves, and PyInstaller's hooks collect these DLLs into the
frozen exe the same way it collects the Python code:

| Native library | Shipped by | Verified / documented license |
|---|---|---|
| `libportaudio64bit.dll` / `libportaudio64bit-asio.dll` (PortAudio) | `sounddevice` | PortAudio's own upstream license is the permissive, MIT-style "PortAudio License." **Not independently re-verified here** — no separate license file ships alongside the DLL in this virtualenv, so this entry rests on PortAudio's well-documented public licensing rather than a locally-inspected file. Treat as permissive but technically unverified-in-repo. |
| `libsndfile_x64.dll` (libsndfile) | `soundfile` | **LGPL-2.1**, per libsndfile's own upstream project. Like PortAudio above, no separate license file ships next to this DLL inside the `soundfile` wheel, so this is the project's well-known published license rather than a file verified in this virtualenv — but it is a real, additional LGPL component distributed inside the exe, on top of pystray and edge-tts. |

### The FLAC binaries bundled by SpeechRecognition — GPL-2.0

`SpeechRecognition`'s own Python code is BSD-3-Clause, but the package also
ships a set of standalone FLAC encoder executables so it can convert
recordings to FLAC before POSTing them to Google's speech API. Confirmed by
listing `site-packages/speech_recognition/`:

```
flac-win32.exe
flac-mac
flac-linux-x86
flac-linux-x86_64
```

The package's own `speechrecognition-*.dist-info/licenses/LICENSE-FLAC.txt`
is the plain **GNU General Public License, Version 2** — confirmed by reading
that file directly.

**Confirmed bundled in the SpeakPaste exe:** `speakpaste.spec` explicitly adds
`flac-win32.exe` to PyInstaller's `datas` (see the comment in the spec: "Ship
flac-win32.exe next to the package inside the bundle" — needed because
PyInstaller doesn't collect it automatically and the frozen exe otherwise
fails Google transcription with `WinError 2`). This is a real, GPL-2.0,
standalone executable shipped inside `SpeakPaste.exe`, invoked as a separate
process (not linked into the SpeakPaste binary itself) — SpeakPaste calls it
the same way it would call any external tool.

## What `speakpaste.spec` actually bundles

Reading `speakpaste.spec` directly:

- **Explicit `datas` entry:** `flac-win32.exe` from the `speech_recognition`
  package (the only binary the spec file names by hand — everything else
  below is collected automatically by PyInstaller's import/dependency
  analysis, not written into the spec).
- **Explicit `hiddenimports`:** `keyboard`, `speech_recognition`,
  `sounddevice`, `soundfile`, `numpy`, `websockets`.
- **Everything else `speakpaste.py` imports** (`requests`, `pystray`,
  `PIL`/Pillow, `python-dotenv`, `edge_tts` and its whole `aiohttp` stack,
  `tkinter`) is picked up automatically by PyInstaller's static analysis of
  the script, since none of it is excluded.
- **Native DLLs**: PyInstaller's `sounddevice` and `soundfile` hooks (shipped
  via `pyinstaller-hooks-contrib`, present in this project's `.venv`)
  automatically pull in `_sounddevice_data/portaudio-binaries/*.dll` and
  `_soundfile_data/libsndfile_x64.dll` so the audio packages work when
  frozen — these are not named in the spec file but are real binaries in the
  output.
- **`upx=True`**: the exe is UPX-compressed; this only changes the binary's
  compression container, it doesn't add any new licensed code.

Only `flac-win32.exe` is a hand-written spec entry; the LGPL and GPL
components above enter the exe through PyInstaller's ordinary automatic
collection of whatever the script imports, exactly like every permissive
dependency does.

## Licensing of the distributed executable

- **SpeakPaste's own source** (`speakpaste.py` and everything else in this
  repository) is licensed under the **Apache License 2.0** — see `LICENSE`.

- **The single-file `SpeakPaste.exe`** built by `pyinstaller speakpaste.spec`
  is a different thing: it statically bundles, inside one binary, code and
  compiled libraries under several different licenses. Verified in this
  document, the copyleft/weak-copyleft pieces are:
  - **`pystray`** — LGPL-3.0 (pure Python)
  - **`edge-tts`** — LGPL-3.0 (pure Python, one file MIT)
  - **`libsndfile_x64.dll`** (via `soundfile`) — LGPL-2.1 (compiled native
    library)
  - **the bundled `flac-win32.exe`** (via `SpeechRecognition`) — GPL-2.0
    (standalone executable, invoked as an external process rather than
    linked in)

  Everything else bundled (numpy, requests, pillow, websockets, python-dotenv,
  keyboard, soundfile's own wrapper, PortAudio, aiohttp and its stack) is
  permissive and imposes no relink or source-availability obligation.

- **Why this is fine, and how the LGPL obligation is satisfied:** LGPL
  licensing (both v2.1 and v3) requires that someone who receives a
  statically-linked binary be able to relink the LGPL-covered component
  against a modified version of it. SpeakPaste satisfies this by publishing,
  in this same repository, the complete Python source (`speakpaste.py`), the
  exact dependency list and pinned build recipe (`requirements.txt`), and the
  PyInstaller build spec (`speakpaste.spec`) used to produce the exe. Anyone
  who wants a build against a modified `pystray`, `edge-tts`, or `libsndfile`
  can install that modified version into the same virtualenv and rebuild —
  they are not locked out of relinking, which is the entire point of the LGPL.

- **Exact rebuild command:**

  ```
  pip install -r requirements.txt
  pyinstaller speakpaste.spec
  ```

- **The GPL-2.0 FLAC binary** is not linked into SpeakPaste's own code at all
  — it's an unmodified, standalone executable that ships alongside the exe
  and is invoked as a separate process, the same way the upstream
  `SpeechRecognition` project itself distributes and uses it. Its own
  complete corresponding source is publicly available from the FLAC project
  regardless of anything SpeakPaste does, and SpeakPaste does not modify it.

If you only want a build with no GPL/LGPL components at all, avoid the
`pystray` tray icon, the `edge-tts` engine, and the `google` speech engine
(`_transcribe_google_direct`, the only one that calls
`speech_recognition.recognize_google` and therefore needs the FLAC binary) —
the remaining engines (`google-cloud`, `groq`, `google-ext`, `gemini-lite`,
`gemini-flash`) talk to their REST/WebSocket APIs directly and do not touch
`SpeechRecognition`'s FLAC conversion path.
