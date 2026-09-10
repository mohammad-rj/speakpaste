import tkinter as tk
import re
import os
import soundfile as sf
from .root import ui_run, get_ui_root
from ..win32 import clipboard_set_text
from ..config import log

_history_window = None

def _is_rtl(text):
    for ch in text[:80]:
        if '\u0600' <= ch <= '\u06FF' or '\u0750' <= ch <= '\u077F':
            return True
    return False


def open_history(history_deque, get_player_fn=None, speak_fn=None):
    def _build():
        global _history_window
        ui_root = get_ui_root()
        if ui_root is None:
            return

        if _history_window and _history_window.winfo_exists():
            _history_window.lift()
            _history_window.focus_force()
            return

        win = tk.Toplevel(ui_root)
        win.withdraw()
        win.title("SpeakPaste - History")
        win.configure(bg="#1e1e1e")
        win.resizable(True, True)

        top = tk.Frame(win, bg="#1e1e1e", padx=14, pady=8)
        top.pack(fill="x")

        show_stt_var = tk.BooleanVar(value=True)
        known_len    = [-1]
        click_tags   = []

        def _copy(text_val):
            clipboard_set_text(text_val)
            flash.config(text="✓ copied")
            win.after(1400, lambda: flash.config(text=""))

        def _clickable(txt, start, end, value):
            tag = f"click{len(click_tags)}"
            click_tags.append(tag)
            txt.tag_add(tag, start, end)
            txt.tag_config(tag)
            txt.tag_bind(tag, "<Button-1>", lambda e, v=value: _copy(v))
            txt.tag_bind(tag, "<Enter>", lambda e, t=tag: (txt.config(cursor="hand2"), txt.tag_config(t, background="#242424")))
            txt.tag_bind(tag, "<Leave>", lambda e, t=tag: (txt.config(cursor="arrow"), txt.tag_config(t, background="")))

        def _render():
            known_len[0] = len(history_deque)
            txt.config(state="normal")
            for t in click_tags:
                txt.tag_delete(t)
            click_tags.clear()
            txt.delete("1.0", "end")
            entries = list(history_deque)
            if not entries:
                txt.insert("end", "No history yet.", "empty")
            for entry in entries:
                txt.insert("end", entry.get("time", "") + "  ", "ts")
                session_tag = entry.get("session_title")
                if session_tag:
                    txt.insert("end", f"[{session_tag}] ", "sess")
                txt.insert("end", entry.get("engine", "").upper() + "\n", "eng")
                if entry.get("stt") and show_stt_var.get():
                    ln_start = txt.index("end-1c")
                    txt.insert("end", "  Voice   ", "lbl_v")
                    txt.insert("end", entry["stt"], "stt")
                    if _is_rtl(entry["stt"]):
                        txt.tag_add("rtl", ln_start, txt.index("end-1c"))
                    _clickable(txt, ln_start, txt.index("end-1c"), entry["stt"])
                    txt.insert("end", "\n")

                lbl = "  Prompt  " if entry.get("stt") else "  "
                ln_start = txt.index("end-1c")
                txt.insert("end", lbl, "lbl_p")
                out_txt = entry.get("output", "")
                txt.insert("end", out_txt, "out")
                if _is_rtl(out_txt):
                    txt.tag_add("rtl", ln_start, txt.index("end-1c"))
                _clickable(txt, ln_start, txt.index("end-1c"), out_txt)
                txt.insert("end", "\n\n")
            txt.config(state="disabled")

        def _poll():
            if not win.winfo_exists():
                return
            if len(history_deque) != known_len[0]:
                _render()
            win.after(500, _poll)

        tk.Checkbutton(
            top, text="Show voice text", variable=show_stt_var, command=_render,
            bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
            activebackground="#1e1e1e", activeforeground="#ffffff", font=("Segoe UI", 9)
        ).pack(side="left")

        flash = tk.Label(top, text="", bg="#1e1e1e", fg="#4c9aff", font=("Segoe UI", 8))
        flash.pack(side="left", padx=(10, 0))

        tk.Button(
            top, text="Clear", command=lambda: (history_deque.clear(), _render()),
            bg="#2d2d2d", fg="#888888", relief="flat", font=("Segoe UI", 8),
            activebackground="#3d3d3d", activeforeground="#cccccc"
        ).pack(side="right")

        body = tk.Frame(win, bg="#1e1e1e", padx=14, pady=12)
        body.pack(fill="both", expand=True)

        txt = tk.Text(
            body, bg="#141414", fg="#dddddd", insertbackground="#ffffff",
            relief="flat", font=("Segoe UI", 9), wrap="word", padx=8, pady=8, state="disabled"
        )
        sb = tk.Scrollbar(body, command=txt.yview)
        txt.config(yscrollcommand=sb.set)
        sb.pack(side="right", fill="y")
        txt.pack(side="left", fill="both", expand=True)

        txt.tag_config("ts",    foreground="#666666", font=("Segoe UI", 8))
        txt.tag_config("sess",  foreground="#e5c07b", font=("Segoe UI", 8, "bold"))
        txt.tag_config("eng",   foreground="#4c9aff", font=("Segoe UI", 8, "bold"))
        txt.tag_config("lbl_v", foreground="#555555", font=("Segoe UI", 8))
        txt.tag_config("lbl_p", foreground="#555555", font=("Segoe UI", 8))
        txt.tag_config("stt",   foreground="#888888")
        txt.tag_config("out",   foreground="#ffffff")
        txt.tag_config("empty", foreground="#555555", font=("Segoe UI", 9, "italic"))
        txt.tag_config("rtl",   justify="right")

        _render()
        _poll()

        def _on_close():
            global _history_window
            _history_window = None
            win.destroy()

        win.protocol("WM_DELETE_WINDOW", _on_close)

        win.update_idletasks()
        w, h = 540, 420
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        win.deiconify()
        _history_window = win

    ui_run(_build)

