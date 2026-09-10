import tkinter as tk
from ..win32 import _MODIFIERS

def capture_hotkey(parent, on_done):
    """Modal 'press the combination' capture dialog. Returns combo via on_done callback."""
    dlg = tk.Toplevel(parent)
    dlg.withdraw()
    dlg.title("Press a key combination")
    dlg.configure(bg="#1e1e1e")
    dlg.resizable(False, False)
    dlg.transient(parent)

    tk.Label(dlg, text="Hold the combination you want, then release.",
             bg="#1e1e1e", fg="#cccccc", font=("Segoe UI", 9),
             padx=24, pady=16).pack()
    shown = tk.Label(dlg, text="…", bg="#1e1e1e", fg="#ffffff",
                     font=("Segoe UI", 14, "bold"), pady=8)
    shown.pack()
    hint = tk.Label(dlg, text="Esc to cancel", bg="#1e1e1e", fg="#666666",
                    font=("Segoe UI", 8))
    hint.pack(pady=(0, 14))

    state = {"held": [], "done": False}

    def _finish(combo):
        if state["done"]:
            return
        state["done"] = True
        try:
            dlg.destroy()
        except Exception:
            pass
        if combo:
            on_done(combo)

    def _on_press(e):
        name = {
            "control_l": "ctrl", "control_r": "ctrl",
            "alt_l": "alt", "alt_r": "alt",
            "shift_l": "shift", "shift_r": "shift",
            "super_l": "win", "super_r": "win",
        }.get(e.keysym.lower(), e.keysym.lower())

        if name == "escape":
            _finish(None)
            return "break"
        if name not in state["held"]:
            state["held"].append(name)
        ordered = ([m for m in _MODIFIERS if m in state["held"]]
                   + [k for k in state["held"] if k not in _MODIFIERS])
        shown.config(text="+".join(ordered))
        return "break"

    def _on_release(_e):
        if state["held"] and not state["done"]:
            ordered = ([m for m in _MODIFIERS if m in state["held"]]
                       + [k for k in state["held"] if k not in _MODIFIERS])
            _finish("+".join(ordered))
        return "break"

    dlg.bind("<KeyPress>", _on_press)
    dlg.bind("<KeyRelease>", _on_release)
    dlg.protocol("WM_DELETE_WINDOW", lambda: _finish(None))

    dlg.update_idletasks()
    w, h = max(300, dlg.winfo_reqwidth()), dlg.winfo_reqheight()
    px, py = parent.winfo_rootx(), parent.winfo_rooty()
    pw, ph = parent.winfo_width(), parent.winfo_height()
    dlg.geometry(f"{w}x{h}+{px + (pw - w) // 2}+{py + (ph - h) // 3}")
    dlg.deiconify()
    dlg.grab_set()
    dlg.focus_force()

