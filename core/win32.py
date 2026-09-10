import ctypes
import ctypes.wintypes
import time
import os
import keyboard
from .config import log

# ─── Win32 Structures & Constants ─────────────────────────────────────────────

user32   = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32

INPUT_KEYBOARD       = 1
KEYEVENTF_KEYUP      = 0x0002
KEYEVENTF_UNICODE    = 0x0004

CF_UNICODETEXT = 13
CF_HDROP       = 15
GMEM_MOVEABLE  = 0x0002

user32.GetClipboardData.restype  = ctypes.c_void_p
kernel32.GlobalLock.restype      = ctypes.c_void_p
kernel32.GlobalLock.argtypes     = [ctypes.c_void_p]
kernel32.GlobalUnlock.argtypes   = [ctypes.c_void_p]
kernel32.GlobalAlloc.restype     = ctypes.c_void_p
kernel32.GlobalAlloc.argtypes    = [ctypes.c_uint, ctypes.c_size_t]
user32.SetClipboardData.restype  = ctypes.c_void_p
user32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
user32.GetKeyboardLayout.restype = ctypes.c_void_p

GMEM_MOVEABLE  = 0x0002
GMEM_ZEROINIT  = 0x0040
GHND           = GMEM_MOVEABLE | GMEM_ZEROINIT

CF_PREFERRED_DROPEFFECT = user32.RegisterClipboardFormatW("Preferred DropEffect")
CF_FILENAMEW            = user32.RegisterClipboardFormatW("FileNameW")
CF_FILENAME             = user32.RegisterClipboardFormatW("FileName")
DROPEFFECT_COPY         = 1

VK_CONTROL = 0x11
VK_C       = 0x43
VK_V       = 0x56

SENTINEL = "⁣__speakpaste_probe__⁣"

class KEYBDINPUT(ctypes.Structure):
    _fields_ = [
        ("wVk",         ctypes.wintypes.WORD),
        ("wScan",       ctypes.wintypes.WORD),
        ("dwFlags",     ctypes.wintypes.DWORD),
        ("time",        ctypes.wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_ulonglong),
    ]

class INPUT_UNION(ctypes.Union):
    _fields_ = [("ki", KEYBDINPUT)]

class INPUT(ctypes.Structure):
    _fields_ = [
        ("type", ctypes.wintypes.DWORD),
        ("iu",   INPUT_UNION),
        ("_pad", ctypes.c_ubyte * 8),
    ]

class DROPFILES(ctypes.Structure):
    _fields_ = [
        ("pFiles", ctypes.wintypes.DWORD),
        ("pt",     ctypes.wintypes.POINT),
        ("fNC",    ctypes.wintypes.BOOL),
        ("fWide",  ctypes.wintypes.BOOL),
    ]

_VK_BY_NAME = {
    "win":           (0x5B, 0x5C),
    "windows":       (0x5B, 0x5C),
    "left windows":  (0x5B,),
    "right windows": (0x5C,),
    "alt":           (0x12,),
    "left alt":      (0xA4,),
    "right alt":     (0xA5,),
    "shift":         (0x10,),
    "left shift":    (0xA0,),
    "right shift":   (0xA1,),
    "ctrl":          (0x11,),
    "control":       (0x11,),
    "left ctrl":     (0xA2,),
    "right ctrl":    (0xA3,),
}

_MODIFIERS = ["ctrl", "win", "alt", "shift"]

_WINDOWS_RESERVED = {
    "win+l": "Windows locks the screen",
    "win+x": "Windows opens the Quick Link menu",
    "win+d": "Windows shows the desktop",
    "win+e": "Windows opens File Explorer",
    "win+r": "Windows opens the Run dialog",
    "win+s": "Windows opens Search",
    "win+i": "Windows opens Settings",
    "win+a": "Windows opens the Action Center",
    "win+v": "Windows opens clipboard history",
    "win+g": "Windows opens Game Bar",
    "win+h": "Windows starts its own voice typing",
    "win+tab": "Windows opens Task View",
    "ctrl+alt+delete": "reserved by Windows security",
    "alt+tab": "Windows switches windows",
    "alt+f4": "Windows closes the active window",
}

_LANGID_TO_CODE = {
    0x29: "fa", 0x09: "en", 0x1F: "tr", 0x01: "ar",
    0x19: "ru", 0x0C: "fr", 0x07: "de", 0x0A: "es", 0x10: "it",
}


def _phys_down(key):
    """True while `key` is physically held - asked of Windows, not of a cache."""
    vks = _VK_BY_NAME.get(str(key).strip().lower())
    if not vks:
        return keyboard.is_pressed(key)
    return any(user32.GetAsyncKeyState(vk) & 0x8000 for vk in vks)


def active_language():
    try:
        hwnd = user32.GetForegroundWindow()
        thread_id = user32.GetWindowThreadProcessId(hwnd, None)
        klid = user32.GetKeyboardLayout(thread_id)
        lang_id = (klid or 0) & 0xFFFF
        primary_lang = lang_id & 0x3FF
        return _LANGID_TO_CODE.get(primary_lang, "fa")
    except Exception:
        return "fa"


# ─── Clipboard Functions ──────────────────────────────────────────────────────

def clipboard_open(retries=10):
    for _ in range(retries):
        if user32.OpenClipboard(0):
            return True
        time.sleep(0.02)
    return False


def clipboard_get_text():
    if not clipboard_open():
        return None
    try:
        h = user32.GetClipboardData(CF_UNICODETEXT)
        if not h:
            return None
        p = kernel32.GlobalLock(h)
        if not p:
            return None
        try:
            return ctypes.c_wchar_p(p).value
        finally:
            kernel32.GlobalUnlock(h)
    except Exception:
        return None
    finally:
        user32.CloseClipboard()


def clipboard_set_text(text):
    if text is None:
        return
    if not clipboard_open():
        return
    try:
        user32.EmptyClipboard()
        buf  = ctypes.create_unicode_buffer(text)
        size = ctypes.sizeof(buf)
        h    = kernel32.GlobalAlloc(GMEM_MOVEABLE, size)
        if not h:
            return
        p = kernel32.GlobalLock(h)
        if not p:
            return
        ctypes.memmove(p, buf, size)
        kernel32.GlobalUnlock(h)
        user32.SetClipboardData(CF_UNICODETEXT, h)
    except Exception:
        pass
    finally:
        user32.CloseClipboard()






# ─── Keystroke & Input Synthesis ──────────────────────────────────────────────

def send_unicode_char(char_code):
    down = INPUT()
    down.type = INPUT_KEYBOARD
    down.iu.ki.wScan   = char_code
    down.iu.ki.dwFlags = KEYEVENTF_UNICODE

    up = INPUT()
    up.type = INPUT_KEYBOARD
    up.iu.ki.wScan   = char_code
    up.iu.ki.dwFlags = KEYEVENTF_UNICODE | KEYEVENTF_KEYUP

    user32.SendInput(1, ctypes.byref(down), ctypes.sizeof(INPUT))
    user32.SendInput(1, ctypes.byref(up),   ctypes.sizeof(INPUT))


def send_ctrl_c():
    def _ev(vk, up):
        i = INPUT()
        i.type = INPUT_KEYBOARD
        i.iu.ki.wVk = vk
        i.iu.ki.wScan = user32.MapVirtualKeyW(vk, 0)
        i.iu.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
        return i

    seq = (_ev(VK_CONTROL, False), _ev(VK_C, False),
           _ev(VK_C, True),        _ev(VK_CONTROL, True))
    arr = (INPUT * len(seq))(*seq)
    user32.SendInput(len(seq), ctypes.byref(arr), ctypes.sizeof(INPUT))


def send_ctrl_v():
    def _ev(vk, up):
        i = INPUT()
        i.type = INPUT_KEYBOARD
        i.iu.ki.wVk = vk
        i.iu.ki.wScan = user32.MapVirtualKeyW(vk, 0)
        i.iu.ki.dwFlags = KEYEVENTF_KEYUP if up else 0
        return i

    seq = (_ev(VK_CONTROL, False), _ev(VK_V, False),
           _ev(VK_V, True),        _ev(VK_CONTROL, True))
    arr = (INPUT * len(seq))(*seq)
    user32.SendInput(len(seq), ctypes.byref(arr), ctypes.sizeof(INPUT))


def paste_text(text):
    old = clipboard_get_text()
    clipboard_set_text(text)
    time.sleep(0.06)
    send_ctrl_v()
    time.sleep(0.25)
    clipboard_set_text(old if old is not None else "")


def type_text(text, hotkey="win+alt", inject_mode="auto"):
    if not text:
        return
    keys = hotkey.split("+")
    while any(_phys_down(k) for k in keys):
        time.sleep(0.05)
    time.sleep(0.3)
    for k in ["left windows", "right windows", "alt", "ctrl", "shift"]:
        try:
            keyboard.release(k)
        except Exception:
            pass

    mode = inject_mode
    if mode == "auto":
        mode = "paste" if len(text) > 220 else "type"
    if mode == "paste":
        try:
            paste_text(text)
            log(f"Pasted OK ({len(text)} chars)")
            return
        except Exception as e:
            log(f"Paste failed, typing instead: {e}")

    for char in text:
        send_unicode_char(ord(char))
        time.sleep(0.001)
    log("Typed OK")





def get_selected_text(tts_hotkey="win+shift", timeout=1.2):
    deadline = time.time() + 3
    hk = [k for k in tts_hotkey.split("+") if k]
    while time.time() < deadline and any(_phys_down(k) for k in hk):
        time.sleep(0.05)
    for k in ["left windows", "right windows", "alt", "ctrl", "shift"]:
        try:
            keyboard.release(k)
        except Exception:
            pass
    time.sleep(0.12)

    old = clipboard_get_text()
    clipboard_set_text(SENTINEL)
    try:
        send_ctrl_c()
    except Exception as e:
        log(f"Ctrl+C failed: {e}")
        clipboard_set_text(old if old is not None else "")
        return None

    deadline = time.time() + timeout
    got = None
    while time.time() < deadline:
        time.sleep(0.05)
        cur = clipboard_get_text()
        if cur and cur != SENTINEL:
            got = cur
            break

    clipboard_set_text(old if old is not None else "")
    log(f"Selection: {len(got or '')} chars" + ("" if got else " (Ctrl+C produced nothing)"))
    return (got or "").strip() or None


def validate_hotkey(combo):
    combo = (combo or "").strip().lower()
    if not combo:
        return False, "Hotkey is empty."
    parts = [p.strip() for p in combo.split("+") if p.strip()]
    if not parts:
        return False, "Hotkey is empty."

    mods = [p for p in parts if p in _MODIFIERS]
    if not mods:
        return False, "Needs at least one modifier (ctrl, win, alt or shift)."

    for k in parts:
        try:
            keyboard.key_to_scan_codes(k)
        except Exception:
            return False, f"'{k}' is not a key name the app understands."

    key = "+".join(sorted(mods, key=_MODIFIERS.index) + sorted(p for p in parts if p not in _MODIFIERS))
    for reserved, why in _WINDOWS_RESERVED.items():
        r_parts = reserved.split("+")
        if sorted(r_parts) == sorted(parts):
            return False, f"{reserved} will not reach the app - {why}."
        if "win" in parts and len(r_parts) == 2 and r_parts[0] == "win" and r_parts[1] in parts:
            return False, f"Windows takes Win+{r_parts[1]} ({why}), so this combo may never fire."
    return True, key


def get_active_window_title():
    try:
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        if length > 0:
            buff = ctypes.create_unicode_buffer(length + 1)
            user32.GetWindowTextW(hwnd, buff, length + 1)
            title = buff.value.strip()
            if " - " in title:
                parts = title.split(" - ")
                return parts[-1].strip()
            return title[:24]
    except Exception:
        pass
    return ""
