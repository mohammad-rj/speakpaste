import tkinter as tk
from .root import ui_run, get_ui_root

_toast_win = None

def show_toast(message, kind="error", seconds=4, notify_errors=True):
    if not notify_errors and kind == "error":
        return

    def _build():
        global _toast_win
        ui_root = get_ui_root()
        if ui_root is None:
            return

        if _toast_win is not None:
            try:
                _toast_win.destroy()
            except Exception:
                pass
            _toast_win = None

        accent = {"error": "#e05252", "info": "#4c9aff", "ok": "#4caf50"}.get(kind, "#4c9aff")
        win = tk.Toplevel(ui_root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        win.configure(bg="#3a3a3a")

        card = tk.Frame(win, bg="#181818")
        card.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Frame(card, bg=accent, width=3).pack(side="left", fill="y")
        body = tk.Frame(card, bg="#181818", padx=12, pady=9)
        body.pack(side="left", fill="both", expand=True)
        tk.Label(body, text="SpeakPaste", bg="#181818", fg="#7a7a7a", font=("Segoe UI", 7)).pack(anchor="w")
        tk.Label(body, text=message, bg="#181818", fg="#e8e8e8", font=("Segoe UI", 9),
                 wraplength=280, justify="left").pack(anchor="w", pady=(1, 0))

        win.update_idletasks()
        w = max(240, min(320, win.winfo_reqwidth()))
        h = win.winfo_reqheight()
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{w}x{h}+{sw - w - 24}+{sh - h - 72}")
        win.deiconify()

        def _fade_in(a=0.0):
            if not win.winfo_exists():
                return
            a = min(0.96, a + 0.16)
            win.attributes("-alpha", a)
            if a < 0.96:
                win.after(16, lambda: _fade_in(a))

        _fade_in()
        _toast_win = win

        def _dismiss():
            global _toast_win
            if win.winfo_exists():
                try:
                    win.destroy()
                except Exception:
                    pass
            if _toast_win is win:
                _toast_win = None

        win.bind("<Button-1>", lambda e: _dismiss())
        body.bind("<Button-1>", lambda e: _dismiss())
        win.after(int(seconds * 1000), _dismiss)

    ui_run(_build)

