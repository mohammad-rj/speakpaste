"""Linux X11 backend: the same public surface as core/win32.py.

Hotkeys are polled (query_keymap), never grabbed, so keyboard_listener() keeps
its polling design. Key injection uses the XTEST extension, clipboard and
Unicode typing go through xclip and xdotool. Every call that can fail logs and
returns a safe value: nothing here may raise into the listener thread.
"""
import ctypes
import ctypes.util
import os
import re
import subprocess
import threading
import time

from .config import log

try:
    from Xlib import X, XK, display as _xdisplay
    from Xlib.ext import xtest as _xtest
except Exception as _e:  # python-xlib missing: every X call degrades to a no-op
    X = XK = _xdisplay = _xtest = None
    log(f"python-xlib unavailable: {_e}")

__all__ = [
    "active_language", "type_text", "paste_text", "get_selected_text",
    "validate_hotkey", "get_active_window_title", "remote_client_in_front",
    "get_foreground_exe", "clipboard_get_text", "clipboard_set_text",
    "SENTINEL", "REMOTE_CLIENT_EXES", "PASTE_SET_DELAY", "PASTE_RESTORE_DELAY",
]

SENTINEL = "⁣__speakpaste_probe__⁣"

PASTE_SET_DELAY     = 0.08
PASTE_RESTORE_DELAY = 0.4
_CMD_TIMEOUT        = 2

_MODIFIERS = ["ctrl", "win", "alt", "shift"]

# App key name -> X keysym names. Anything not listed is resolved by
# _keysyms_for() through XK.string_to_keysym (letters, digits, F-keys, ...).
_KEYSYMS_BY_NAME = {
    "win":           ("Super_L", "Super_R"),
    "windows":       ("Super_L", "Super_R"),
    "super":         ("Super_L", "Super_R"),
    "left windows":  ("Super_L",),
    "right windows": ("Super_R",),
    "alt":           ("Alt_L", "Alt_R"),
    "left alt":      ("Alt_L",),
    "right alt":     ("Alt_R",),
    "shift":         ("Shift_L", "Shift_R"),
    "left shift":    ("Shift_L",),
    "right shift":   ("Shift_R",),
    "ctrl":          ("Control_L", "Control_R"),
    "control":       ("Control_L", "Control_R"),
    "left ctrl":     ("Control_L",),
    "right ctrl":    ("Control_R",),
    "space":         ("space",),
    "tab":           ("Tab",),
    "enter":         ("Return",),
    "return":        ("Return",),
    "esc":           ("Escape",),
    "escape":        ("Escape",),
    "backspace":     ("BackSpace",),
    "delete":        ("Delete",),
    "insert":        ("Insert",),
    "home":          ("Home",),
    "end":           ("End",),
    "page up":       ("Prior",),
    "page down":     ("Next",),
    "up":            ("Up",),
    "down":          ("Down",),
    "left":          ("Left",),
    "right":         ("Right",),
    "caps lock":     ("Caps_Lock",),
}

_STUCK_KEYSYMS = ("Super_L", "Super_R", "Alt_L", "Alt_R",
                  "Control_L", "Control_R", "Shift_L", "Shift_R")

_XFCE_RESERVED = {
    "ctrl+alt+delete": "XFCE opens the log-out dialog",
    "alt+tab":         "XFCE switches windows",
    "alt+f4":          "XFCE closes the active window",
    "ctrl+alt+t":      "XFCE opens a terminal",
}

_LAYOUT_TO_CODE = {
    "ir": "fa", "us": "en", "gb": "en", "tr": "tr", "ara": "ar",
    "ru": "ru", "fr": "fr", "de": "de", "es": "es", "it": "it",
}

REMOTE_CLIENT_EXES = {"xfreerdp", "xfreerdp3", "remmina", "rdesktop"}


# ─── Shared X connection ──────────────────────────────────────────────────────

_x_lock = threading.RLock()
_x_disp = None
_keycode_cache = {}


def _display():
    """The one shared python-xlib Display, opened on first use. Call under _x_lock."""
    global _x_disp
    if _x_disp is None and _xdisplay is not None:
        _x_disp = _xdisplay.Display()
        _keycode_cache.clear()
    return _x_disp


def _drop_display():
    global _x_disp
    try:
        if _x_disp is not None:
            _x_disp.close()
    except Exception:
        pass
    _x_disp = None
    _keycode_cache.clear()


def _keysym_names_for(name):
    """Keysym names for an app key name, without touching X. [] when unknown."""
    n = str(name or "").strip().lower()
    if not n:
        return []
    if n in _KEYSYMS_BY_NAME:
        return list(_KEYSYMS_BY_NAME[n])
    if re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", n):
        return [n.upper()]
    if len(n) == 1:
        return [n]
    return [n, n.capitalize()]


def _keysyms_for(name):
    """Keysym values for an app key name ([] when X does not know it)."""
    if XK is None:
        return []
    out = [ks for ks in (XK.string_to_keysym(s) for s in _keysym_names_for(name)) if ks]
    # A guessed name tries "pause" then "Pause": keep only the first hit.
    if out and str(name).strip().lower() not in _KEYSYMS_BY_NAME:
        out = out[:1]
    return out


def _keycodes_for(name):
    """X keycodes bound to the key name. Call under _x_lock."""
    key = str(name or "").strip().lower()
    if key in _keycode_cache:
        return _keycode_cache[key]
    d = _display()
    codes = set()
    if d is not None:
        for ks in _keysyms_for(key):
            for kc, _idx in d.keysym_to_keycodes(ks):
                codes.add(kc)
    _keycode_cache[key] = codes
    return codes


def _phys_down(key):
    """True while `key` is held, read from the server keymap - no cached state."""
    try:
        with _x_lock:
            d = _display()
            if d is None:
                return False
            codes = _keycodes_for(key)
            if not codes:
                return False
            keymap = d.query_keymap()
            return any(keymap[kc // 8] & (1 << (kc % 8)) for kc in codes)
    except Exception as e:
        log(f"X keymap read failed: {e}")
        with _x_lock:
            _drop_display()
        return False


def _fake_keys(events):
    """Send (keysym_name, is_press) pairs through XTEST. Returns True on success."""
    try:
        with _x_lock:
            d = _display()
            if d is None:
                return False
            for sym, press in events:
                kc = d.keysym_to_keycode(XK.string_to_keysym(sym))
                if not kc:
                    log(f"No keycode for {sym}")
                    return False
                _xtest.fake_input(d, X.KeyPress if press else X.KeyRelease, kc)
            d.sync()
            return True
    except Exception as e:
        log(f"XTest input failed: {e}")
        with _x_lock:
            _drop_display()
        return False


def _release_stuck_modifiers():
    """Key-up for every modifier the server still reports as down."""
    try:
        with _x_lock:
            d = _display()
            if d is None:
                return
            keymap = d.query_keymap()
            for sym in _STUCK_KEYSYMS:
                kc = d.keysym_to_keycode(XK.string_to_keysym(sym))
                if kc and keymap[kc // 8] & (1 << (kc % 8)):
                    _xtest.fake_input(d, X.KeyRelease, kc)
            d.sync()
    except Exception as e:
        log(f"Modifier release failed: {e}")
        with _x_lock:
            _drop_display()


def send_ctrl_c():
    return _fake_keys([("Control_L", True), ("c", True), ("c", False), ("Control_L", False)])


def send_ctrl_v():
    return _fake_keys([("Control_L", True), ("v", True), ("v", False), ("Control_L", False)])


# ─── Subprocess helpers ───────────────────────────────────────────────────────

def _run(args, timeout=_CMD_TIMEOUT, input_text=None):
    """Run a command, return stdout text or None on any failure."""
    try:
        r = subprocess.run(
            args, input=input_text.encode("utf-8") if input_text is not None else None,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout,
        )
        if r.returncode != 0:
            return None
        return r.stdout.decode("utf-8", errors="replace")
    except Exception as e:
        log(f"{args[0]} failed: {e}")
        return None


# ─── Clipboard ────────────────────────────────────────────────────────────────

def clipboard_get_text():
    return _run(["xclip", "-selection", "clipboard", "-o"])


def clipboard_set_text(text):
    if text is None:
        return
    try:
        # xclip -i forks a child that keeps serving the selection; its stdout
        # and stderr must not be pipes or run() would wait for that child.
        subprocess.run(
            ["xclip", "-selection", "clipboard", "-i"], input=text.encode("utf-8"),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=_CMD_TIMEOUT,
        )
    except Exception as e:
        log(f"xclip set failed: {e}")


# ─── Text injection ───────────────────────────────────────────────────────────

def paste_text(text):
    old = clipboard_get_text()
    clipboard_set_text(text)
    time.sleep(PASTE_SET_DELAY)
    if not send_ctrl_v():
        clipboard_set_text(old if old is not None else "")
        raise RuntimeError("Ctrl+V injection failed")
    time.sleep(PASTE_RESTORE_DELAY)
    clipboard_set_text(old if old is not None else "")


def _xdotool_type(text):
    out = _run(["xdotool", "type", "--clearmodifiers", "--delay", "1", "--", text],
               timeout=10 + len(text) * 0.05)
    return out is not None


def type_text(text, hotkey="win+alt", inject_mode="auto"):
    if not text:
        return
    keys = hotkey.split("+")
    while any(_phys_down(k) for k in keys):
        time.sleep(0.05)
    time.sleep(0.3)
    _release_stuck_modifiers()

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

    if _xdotool_type(text):
        log("Typed OK")
    else:
        log("Typing failed (xdotool)")


def get_selected_text(tts_hotkey="win+shift", timeout=1.2):
    deadline = time.time() + 3
    hk = [k for k in tts_hotkey.split("+") if k]
    while time.time() < deadline and any(_phys_down(k) for k in hk):
        time.sleep(0.05)
    _release_stuck_modifiers()
    time.sleep(0.12)

    old = clipboard_get_text()
    clipboard_set_text(SENTINEL)
    if not send_ctrl_c():
        log("Ctrl+C failed")
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


# ─── Hotkey validation ────────────────────────────────────────────────────────

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
        if not _keysyms_for(k):
            return False, f"'{k}' is not a key name the app understands."

    if parts == ["win"]:
        return False, "Win alone opens the XFCE Whisker menu, so it cannot be the hotkey."

    key = "+".join(sorted(mods, key=_MODIFIERS.index) + sorted(p for p in parts if p not in _MODIFIERS))
    for reserved, why in _XFCE_RESERVED.items():
        if sorted(reserved.split("+")) == sorted(parts):
            return False, f"{reserved} will not reach the app - {why}."
    return True, key


# ─── Keyboard layout ──────────────────────────────────────────────────────────

class _XkbStateRec(ctypes.Structure):
    _fields_ = [
        ("group", ctypes.c_ubyte), ("locked_group", ctypes.c_ubyte),
        ("base_group", ctypes.c_ushort), ("latched_group", ctypes.c_short),
        ("mods", ctypes.c_ubyte), ("base_mods", ctypes.c_ubyte),
        ("latched_mods", ctypes.c_ubyte), ("locked_mods", ctypes.c_ubyte),
        ("compat_state", ctypes.c_ubyte), ("grab_mods", ctypes.c_ubyte),
        ("compat_grab_mods", ctypes.c_ubyte), ("lookup_mods", ctypes.c_ubyte),
        ("compat_lookup_mods", ctypes.c_ubyte), ("ptr_buttons", ctypes.c_ushort),
    ]


_XKB_USE_CORE_KBD = 0x0100
_xlib_c = None
_xlib_c_disp = None


def _xkb_group():
    """Active XKB group index, via libX11 XkbGetState. 0 on failure."""
    global _xlib_c, _xlib_c_disp
    try:
        with _x_lock:
            if _xlib_c is None:
                lib = ctypes.cdll.LoadLibrary(ctypes.util.find_library("X11") or "libX11.so.6")
                lib.XOpenDisplay.restype = ctypes.c_void_p
                lib.XOpenDisplay.argtypes = [ctypes.c_char_p]
                lib.XkbGetState.argtypes = [ctypes.c_void_p, ctypes.c_uint, ctypes.POINTER(_XkbStateRec)]
                lib.XkbGetState.restype = ctypes.c_int
                _xlib_c = lib
            if not _xlib_c_disp:
                _xlib_c_disp = _xlib_c.XOpenDisplay(None)
                if not _xlib_c_disp:
                    return 0
            st = _XkbStateRec()
            if _xlib_c.XkbGetState(_xlib_c_disp, _XKB_USE_CORE_KBD, ctypes.byref(st)) != 0:
                return 0
            return int(st.group)
    except Exception as e:
        log(f"XkbGetState failed: {e}")
        return 0


def _setxkbmap_query():
    return _run(["setxkbmap", "-query"]) or ""


def _layouts_from_query(text):
    """['us', 'ir'] from `setxkbmap -query` output."""
    for line in (text or "").splitlines():
        k, _, v = line.partition(":")
        if k.strip() == "layout":
            return [p.strip().lower() for p in v.split(",") if p.strip()]
    return []


def _lang_for_layout(layout):
    base = re.split(r"[(:]", (layout or "").strip().lower())[0]
    return _LAYOUT_TO_CODE.get(base, "fa")


def active_language():
    try:
        layouts = _layouts_from_query(_setxkbmap_query())
        if not layouts:
            return "fa"
        g = _xkb_group()
        if g >= len(layouts):
            g = 0
        return _lang_for_layout(layouts[g])
    except Exception as e:
        log(f"Layout detection failed: {e}")
        return "fa"


# ─── Foreground window ────────────────────────────────────────────────────────

def get_foreground_exe():
    """Lower-case process name (/proc/<pid>/comm) of the active window, or ""."""
    try:
        out = _run(["xdotool", "getactivewindow", "getwindowpid"])
        pid = (out or "").strip()
        if not pid.isdigit():
            return ""
        with open(f"/proc/{pid}/comm", encoding="utf-8", errors="replace") as f:
            return f.read().strip().lower()
    except Exception as e:
        log(f"Foreground process lookup failed: {e}")
        return ""


def remote_client_in_front():
    return get_foreground_exe() in REMOTE_CLIENT_EXES


def _trim_title(title):
    title = (title or "").strip()
    if " - " in title:
        return title.split(" - ")[-1].strip()
    return title[:24]


def get_active_window_title():
    try:
        return _trim_title(_run(["xdotool", "getactivewindow", "getwindowname"]))
    except Exception as e:
        log(f"Window title lookup failed: {e}")
        return ""
