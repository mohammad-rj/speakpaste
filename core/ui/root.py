import tkinter as tk
import threading
from queue import Queue, Empty
from ..config import log

_ui_queue = Queue()
_ui_root  = None

def ui_run(fn):
    """Queue a callable to run on the Tk UI thread. Safe from any thread."""
    _ui_queue.put(fn)


def get_ui_root():
    return _ui_root


def _ui_thread_main():
    global _ui_root
    _ui_root = tk.Tk()
    _ui_root.withdraw()

    def _pump():
        try:
            while True:
                fn = _ui_queue.get_nowait()
                try:
                    fn()
                except Exception as e:
                    log(f"UI error: {e}")
        except Empty:
            pass
        _ui_root.after(80, _pump)

    _ui_root.after(80, _pump)
    _ui_root.mainloop()


def start_ui_thread():
    t = threading.Thread(target=_ui_thread_main, daemon=True, name="TkUiThread")
    t.start()
    return t

