import tkinter as tk
import time
import os
import threading
import soundfile as sf
from .root import ui_run, get_ui_root
from ..config import log

_tts_popup    = None
_tts_progress = (0, 0)

def set_tts_progress(done, total):
    global _tts_progress
    _tts_progress = (done, total)


def close_tts_popup_if_idle(get_player_fn):
    global _tts_popup
    player = get_player_fn()
    if _tts_popup and not (player and player.playing):
        try:
            ui_run(_tts_popup["win"].destroy)
        except Exception:
            pass
        _tts_popup = None


def open_tts_popup(status="", get_player_fn=None, history_list=None, speak_fn=None,
                   speed_holder=None, autoclose_secs=30):
    """Small always-on-top player widget; self-closes after playback."""
    def _build():
        global _tts_popup
        ui_root = get_ui_root()
        if ui_root is None:
            return

        if _tts_popup and _tts_popup["win"].winfo_exists():
            if status:
                _tts_popup["status"].config(text=status)
            elif _tts_popup["status"].cget("text").startswith(("Generating", "Error")):
                _tts_popup["status"].config(text="")
            _tts_popup["win"].lift()
            if _tts_popup.get("refresh_drawer"):
                _tts_popup["refresh_drawer"]()
            return

        BG, CARD, DIM, FG   = "#181818", "#202020", "#7a7a7a", "#e8e8e8"
        TRACK, ACCENT       = "#3a3a3a", "#4c9aff"
        W                   = 348
        SEEK_W, SPEED_W     = 328, 56
        BAR_H               = 6

        win = tk.Toplevel(ui_root)
        win.withdraw()
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.attributes("-alpha", 0.0)
        win.configure(bg=TRACK)

        card = tk.Frame(win, bg=BG)
        card.pack(fill="both", expand=True, padx=1, pady=1)
        pad = tk.Frame(card, bg=BG, padx=9, pady=3)
        pad.pack(fill="both", expand=True)

        top = tk.Frame(pad, bg=BG)
        top.pack(fill="x")

        def _icon_btn(parent, glyph, size=11):
            b = tk.Label(parent, text=glyph, bg=BG, fg=FG,
                         font=("Segoe UI Symbol", size), cursor="hand2")
            b.bind("<Enter>", lambda e: b.config(fg="#ffffff"))
            b.bind("<Leave>", lambda e: b.config(fg=FG))
            return b

        prev_btn = _icon_btn(top, "◀◀", 8)
        prev_btn.pack(side="left", padx=(0, 5))
        play_btn = _icon_btn(top, "❚❚", 10)
        play_btn.pack(side="left")
        next_btn = _icon_btn(top, "▶▶", 8)
        next_btn.pack(side="left", padx=(5, 0))
        stop_btn = _icon_btn(top, "■", 9)
        stop_btn.pack(side="left", padx=(7, 9))

        time_lbl = tk.Label(top, text="0:00 / 0:00", bg=BG, fg=DIM, font=("Segoe UI", 8))
        time_lbl.pack(side="left")

        chunk_lbl = tk.Label(top, text="", bg=BG, fg="#5f5f5f", font=("Segoe UI", 8))
        chunk_lbl.pack(side="left", padx=(6, 0))

        close_btn = tk.Label(top, text="✕", bg=BG, fg="#5a5a5a", font=("Segoe UI", 9), cursor="hand2")
        close_btn.pack(side="right")
        close_btn.bind("<Enter>", lambda e: close_btn.config(fg="#ff6b6b"))
        close_btn.bind("<Leave>", lambda e: close_btn.config(fg="#5a5a5a"))

        drawer_btn = tk.Label(top, text="▲", bg=BG, fg="#5a5a5a", font=("Segoe UI Symbol", 8), cursor="hand2")
        drawer_btn.pack(side="right", padx=(0, 4))
        drawer_btn.bind("<Enter>", lambda e: drawer_btn.config(fg="#ffffff"))
        drawer_btn.bind("<Leave>", lambda e: drawer_btn.config(fg="#5a5a5a"))

        status_lbl = tk.Label(top, text=status, bg=BG, fg="#cca700", font=("Segoe UI", 8))
        status_lbl.pack(side="right", padx=(0, 8))

        current_speed = speed_holder[0] if speed_holder else 1.0
        speed_val = tk.Label(top, text=f"{current_speed:.2f}x", bg=BG, fg=DIM, font=("Segoe UI", 8), width=5, anchor="e")
        speed_val.pack(side="right")

        def _bar(parent, width, height=BAR_H):
            return tk.Canvas(parent, width=width, height=height, bg=BG, highlightthickness=0, bd=0, cursor="hand2")

        def _draw(cv, width, frac, active=True, marks=(), height=BAR_H):
            cv.delete("all")
            y, x0, x1 = height // 2, 5, width - 5
            r = 3 if height >= 8 else 2
            cv.create_line(x0, y, x1, y, fill=TRACK, width=r, capstyle="round")
            x = x0 + (x1 - x0) * max(0.0, min(1.0, frac))
            col = ACCENT if active else "#5a5a5a"
            if x > x0:
                cv.create_line(x0, y, x, y, fill=col, width=r, capstyle="round")
            for m in marks:
                if m <= 0:
                    continue
                mx = x0 + (x1 - x0) * m
                cv.create_line(mx, y - r - 1, mx, y + r + 1, fill="#7a7a7a", width=1)
            cv.create_oval(x - r - 1, y - r - 1, x + r + 1, y + r + 1, fill=col, outline="")

        speed_cv = _bar(top, SPEED_W)
        speed_cv.pack(side="right", padx=(0, 6))

        seek_cv = _bar(pad, SEEK_W)
        seek_cv.pack(fill="x", pady=(2, 0))

        state = {"seeking": False, "closed": False, "frac": 0.0}

        def _fmt(s):
            return f"{int(s // 60)}:{int(s % 60):02d}"

        def _frac_at(event, width):
            return max(0.0, min(1.0, (event.x - 5) / max(1, width - 10)))

        def _seek_drag(e):
            state["seeking"] = True
            state["frac"] = _frac_at(e, SEEK_W)
            _draw(seek_cv, SEEK_W, state["frac"])
            if get_player_fn:
                _, dur = get_player_fn().position()
                if dur:
                    time_lbl.config(text=f"{_fmt(state['frac'] * dur)} / {_fmt(dur)}")

        def _seek_commit(e):
            if get_player_fn:
                get_player_fn().seek(_frac_at(e, SEEK_W))
            state["seeking"] = False

        seek_cv.bind("<Button-1>", _seek_drag)
        seek_cv.bind("<B1-Motion>", _seek_drag)
        seek_cv.bind("<ButtonRelease-1>", _seek_commit)

        def _apply_speed(v):
            v = max(0.5, min(2.5, round(v, 2)))
            if speed_holder:
                speed_holder[0] = v
            speed_val.config(text=f"{v:.2f}x")
            _draw(speed_cv, SPEED_W, (v - 0.5) / 2.0)
            if get_player_fn:
                threading.Thread(target=lambda: get_player_fn().set_speed(v), daemon=True).start()

        def _speed_set(e):
            v = 0.5 + _frac_at(e, SPEED_W) * 2.0
            _apply_speed(v)

        def _step_speed(delta):
            cur = speed_holder[0] if speed_holder else 1.0
            _apply_speed(cur + delta)

        speed_cv.bind("<Button-1>", _speed_set)
        speed_cv.bind("<B1-Motion>", _speed_set)

        _draw(seek_cv, SEEK_W, 0.0)
        _draw(speed_cv, SPEED_W, (current_speed - 0.5) / 2.0)

        # Drag window
        drag = {"x": 0, "y": 0}

        def _drag_start(e):
            drag["x"], drag["y"] = e.x_root - win.winfo_x(), e.y_root - win.winfo_y()

        def _drag_move(e):
            win.geometry(f"+{e.x_root - drag['x']}+{e.y_root - drag['y']}")

        for w_ in (card, pad, top, time_lbl, status_lbl):
            w_.bind("<Button-1>", _drag_start)
            w_.bind("<B1-Motion>", _drag_move)

        def _close():
            global _tts_popup
            state["closed"] = True
            _tts_popup = None
            if get_player_fn:
                try:
                    get_player_fn().stop()
                except Exception:
                    pass
            win.destroy()

        def _toggle(_e=None):
            if get_player_fn:
                get_player_fn().toggle()
                play_btn.config(text="❚❚" if get_player_fn().playing else "▶")

        def _stop(_e=None):
            if get_player_fn:
                get_player_fn().stop()
                play_btn.config(text="▶")

        active_session = [None]
        recent_sessions = []

        def _play_session(item):
            active_session[0] = item
            audio_file = item.get("file")
            if audio_file and os.path.exists(audio_file):
                try:
                    s, r = sf.read(audio_file, dtype="float32", always_2d=True)
                    if get_player_fn:
                        get_player_fn().load(s.mean(axis=1), r)
                        play_btn.config(text="❚❚")
                        status_lbl.config(text="")
                except Exception as ex:
                    log(f"Play file error: {ex}")
            else:
                full_text = item.get("output", "")
                if speak_fn and full_text:
                    sess_t = item.get("session_title", "AI")
                    threading.Thread(target=speak_fn, args=(full_text, sess_t), daemon=True).start()
            if drawer_open[0]:
                _populate_drawer()

        def _prev(_e=None):
            if get_player_fn:
                idx, total, _ = get_player_fn().chunk_state()
                if total > 1 and idx > 1:
                    get_player_fn().jump_chunk(-1)
                    return
            if recent_sessions and active_session[0] in recent_sessions:
                cur_i = recent_sessions.index(active_session[0])
                if cur_i + 1 < len(recent_sessions):
                    _play_session(recent_sessions[cur_i + 1])

        def _next(_e=None):
            if get_player_fn:
                idx, total, _ = get_player_fn().chunk_state()
                if total > 1 and idx < total:
                    get_player_fn().jump_chunk(+1)
                    return
            if recent_sessions and active_session[0] in recent_sessions:
                cur_i = recent_sessions.index(active_session[0])
                if cur_i - 1 >= 0:
                    _play_session(recent_sessions[cur_i - 1])

        play_btn.bind("<Button-1>", _toggle)
        stop_btn.bind("<Button-1>", _stop)
        prev_btn.bind("<Button-1>", _prev)
        next_btn.bind("<Button-1>", _next)
        close_btn.bind("<Button-1>", lambda e: _close())
        win.bind("<Escape>", lambda e: _close())

        # ── Drawer ───────────────────────────────────────────────────────────
        drawer_frame = tk.Frame(card, bg="#1a1a1a", padx=8, pady=6)
        drawer_open = [False]

        def _populate_drawer():
            nonlocal recent_sessions
            for child in drawer_frame.winfo_children():
                child.destroy()

            hdr = tk.Frame(drawer_frame, bg="#1a1a1a")
            hdr.pack(fill="x", pady=(0, 4))
            tk.Label(hdr, text="Recent Voice Sessions", bg="#1a1a1a", fg="#888888",
                     font=("Segoe UI", 8, "bold")).pack(side="left")

            recent_sessions = [e for e in (history_list or []) if e.get("output")][:6]
            if not recent_sessions:
                tk.Label(drawer_frame, text="No voice sessions yet", bg="#1a1a1a", fg="#555555",
                         font=("Segoe UI", 8)).pack(anchor="w", pady=2)
                return

            if active_session[0] is None and recent_sessions:
                active_session[0] = recent_sessions[0]

            for item in recent_sessions:
                is_active = (item is active_session[0])
                row_bg = "#263040" if is_active else "#222222"
                row = tk.Frame(drawer_frame, bg=row_bg, padx=6, pady=3, cursor="hand2")
                row.pack(fill="x", pady=2)

                is_ai = item.get("type") == "ai" or item.get("engine", "").startswith("tts:")
                raw_title = (item.get("session_title") or ("AI" if is_ai else "YOU")).strip()
                prefix = "AI" if is_ai else "YOU"
                if raw_title and raw_title not in ("AI", "YOU"):
                    short_title = raw_title[:9] + "…" if len(raw_title) > 10 else raw_title
                    badge_txt = f"{prefix}:{short_title}"
                else:
                    badge_txt = prefix

                badge_fg = "#4c9aff" if is_ai else "#4ade80"
                badge_bg = "#192433" if is_ai else "#182c1e"

                badge = tk.Label(row, text=badge_txt, bg=badge_bg, fg=badge_fg,
                                 font=("Segoe UI", 7, "bold"), padx=3, pady=0)
                badge.pack(side="left", padx=(0, 5))

                t_lbl = tk.Label(row, text=item.get("time", ""), bg=row_bg, fg="#777777", font=("Segoe UI", 7))
                t_lbl.pack(side="left", padx=(0, 4))

                snip = (item.get("output", "")).replace("\n", " ").strip()
                if len(snip) > 30:
                    snip = snip[:28] + "..."
                txt_col = "#ffffff" if is_active else "#cccccc"
                txt_lbl = tk.Label(row, text=snip, bg=row_bg, fg=txt_col, font=("Segoe UI", 8), anchor="w")
                txt_lbl.pack(side="left", fill="x", expand=True)

                is_playing = is_active and get_player_fn and get_player_fn().playing
                icon_txt = "❚❚" if is_playing else "▶"
                icon_col = "#4c9aff" if is_active else "#888888"
                play_rec = tk.Label(row, text=icon_txt, bg=row_bg, fg=icon_col, font=("Segoe UI Symbol", 8), cursor="hand2")
                play_rec.pack(side="right", padx=(4, 0))

                def _make_handler(target_item):
                    return lambda _e: _play_session(target_item)

                handler = _make_handler(item)
                row.bind("<Button-1>", handler)
                txt_lbl.bind("<Button-1>", handler)
                badge.bind("<Button-1>", handler)
                t_lbl.bind("<Button-1>", handler)
                play_rec.bind("<Button-1>", handler)

        def _toggle_drawer(_e=None):
            sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
            cur_x = win.winfo_x()
            if drawer_open[0]:
                drawer_open[0] = False
                drawer_btn.config(text="▲")
                drawer_frame.pack_forget()
                win.update_idletasks()
                H_new = pad.winfo_reqheight() + 2
                y_new = (sh - 72) - H_new
                win.geometry(f"{W}x{H_new}+{cur_x}+{y_new}")
            else:
                drawer_open[0] = True
                drawer_btn.config(text="▼")
                _populate_drawer()
                drawer_frame.pack(fill="x", after=pad)
                win.update_idletasks()
                H_new = pad.winfo_reqheight() + drawer_frame.winfo_reqheight() + 4
                y_new = (sh - 72) - H_new
                win.geometry(f"{W}x{H_new}+{cur_x}+{y_new}")

        drawer_btn.bind("<Button-1>", _toggle_drawer)

        # Keyboard shortcuts
        win.bind("<z>", lambda e: _step_speed(-0.1))
        win.bind("<Z>", lambda e: _step_speed(-0.1))
        win.bind("<x>", lambda e: _step_speed(+0.1))
        win.bind("<X>", lambda e: _step_speed(+0.1))
        win.bind("<c>", lambda e: _apply_speed(1.0))
        win.bind("<C>", lambda e: _apply_speed(1.0))
        win.bind("<space>", _toggle)

        def _tick():
            if state["closed"] or not win.winfo_exists():
                return
            if get_player_fn:
                p = get_player_fn()
                cur, dur = p.position()
                idx, total, marks = p.chunk_state()
                if not state["seeking"]:
                    time_lbl.config(text=f"{_fmt(cur)} / {_fmt(dur)}" + ("+" if not p.complete else ""))
                    _draw(seek_cv, SEEK_W, (cur / dur) if dur else 0.0, p.playing, marks if total > 1 else ())
                play_btn.config(text="❚❚" if p.playing else "▶")
                chunk_lbl.config(text=f"{idx}/{total}" if total > 1 else "")
                prev_btn.config(fg=FG if total > 1 else "#3a3a3a")
                next_btn.config(fg=FG if (total > 1 and idx < total) else "#3a3a3a")

                done, total_p = _tts_progress
                if not p.complete and total_p > 1:
                    status_lbl.config(text=f"{done}/{total_p}" + ("  buffering" if p.buffering else ""))
                elif status_lbl.cget("text") and "/" in status_lbl.cget("text"):
                    status_lbl.config(text="")

                if autoclose_secs and p.ended and p.ended_at and (time.time() - p.ended_at > autoclose_secs):
                    _close()
                    return
            win.after(200, _tick)

        win.update_idletasks()
        H = pad.winfo_reqheight() + 2
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{W}x{H}+{sw - W - 24}+{sh - H - 72}")
        win.update_idletasks()
        win.deiconify()

        def _fade(a=0.0):
            if state["closed"] or not win.winfo_exists():
                return
            a = min(0.97, a + 0.13)
            win.attributes("-alpha", a)
            if a < 0.97:
                win.after(16, lambda: _fade(a))

        _fade()
        _tts_popup = {
            "win": win,
            "status": status_lbl,
            "refresh_drawer": lambda: _populate_drawer() if drawer_open[0] else None,
            "set_active": lambda it: (active_session.__setitem__(0, it), _populate_drawer() if drawer_open[0] else None)
        }
        _tick()

    ui_run(_build)

