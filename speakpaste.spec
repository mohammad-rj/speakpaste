# -*- mode: python ; coding: utf-8 -*-

import os
import speech_recognition

# speech_recognition needs its bundled FLAC encoder to POST audio to Google.
# PyInstaller does not collect it automatically, so the frozen exe raises
# "[WinError 2] The system cannot find the file specified" on every Google
# transcription. Ship flac-win32.exe next to the package inside the bundle.
_SR_DIR = os.path.dirname(speech_recognition.__file__)
_sr_datas = [(os.path.join(_SR_DIR, 'flac-win32.exe'), 'speech_recognition')]

a = Analysis(
    ['speakpaste.py'],
    pathex=[],
    binaries=[],
    datas=_sr_datas,
    hiddenimports=['keyboard', 'speech_recognition', 'sounddevice', 'soundfile', 'numpy', 'websockets'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name='SpeakPaste',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
