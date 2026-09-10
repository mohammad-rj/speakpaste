import tkinter as tk
from tkinter import ttk, filedialog
import os

from .root import ui_run, get_ui_root
from .hotkey_dialog import capture_hotkey
from ..win32 import validate_hotkey
from ..config import (
    VERSION, GITHUB_URL, TTS_STYLE_PRESETS, TTS_DEFAULT_STYLE,
    TTS_EDGE_VOICES, TTS_VERTEX_VOICES, TTS_VERTEX_MODELS,
    GEMINI_DEFAULT_SYSTEM_PROMPT, GEMINI_STT_DEFAULT_MODEL
)

_settings_window = None

def open_settings(current_cfg, on_save_callback, on_test_voice_callback=None):
    def _build():
        global _settings_window
        ui_root = get_ui_root()
        if ui_root is None:
            return

        if _settings_window and _settings_window.winfo_exists():
            _settings_window.lift()
            _settings_window.focus_force()
            return

        win = tk.Toplevel(ui_root)
        win.withdraw()
        win.title("SpeakPaste - Settings")
        win.resizable(False, True)
        win.configure(bg="#1e1e1e")

        style = ttk.Style(win)
        try:
            style.theme_use("clam")
        except Exception:
            pass
        style.configure("Dark.TNotebook", background="#1e1e1e", borderwidth=0, tabmargins=(8, 6, 8, 0))
        style.configure("Dark.TNotebook.Tab", background="#252525", foreground="#999999",
                        padding=(16, 7), borderwidth=0, font=("Segoe UI", 9))
        style.map("Dark.TNotebook.Tab",
                  background=[("selected", "#1e1e1e")],
                  foreground=[("selected", "#ffffff")],
                  expand=[("selected", (0, 0, 0, 2))])

        nb = ttk.Notebook(win, style="Dark.TNotebook")
        nb.pack(side="top", fill="both", expand=True)

        def _make_tab(title):
            page = tk.Frame(nb, bg="#1e1e1e")
            nb.add(page, text=title)
            cv = tk.Canvas(page, bg="#1e1e1e", highlightthickness=0, bd=0)
            sb = tk.Scrollbar(page, orient="vertical", command=cv.yview)
            cv.configure(yscrollcommand=sb.set)
            sb.pack(side="right", fill="y")
            cv.pack(side="left", fill="both", expand=True)
            inner = tk.Frame(cv, bg="#1e1e1e", padx=20, pady=10)
            iid = cv.create_window((0, 0), window=inner, anchor="nw")
            inner.bind("<Configure>", lambda e: cv.configure(scrollregion=cv.bbox("all")))
            cv.bind("<Configure>", lambda e: cv.itemconfig(iid, width=e.width))
            cv.bind("<MouseWheel>", lambda e: cv.yview_scroll(int(-1 * (e.delta / 120)), "units"))
            inner.bind("<MouseWheel>", lambda e: cv.yview_scroll(int(-1 * (e.delta / 120)), "units"))
            return inner

        tab_stt    = _make_tab("Voice Typing")
        tab_prompt = _make_tab("Prompt")
        tab_tts    = _make_tab("Text → Speech")
        tab_gen    = _make_tab("General")

        lbl_style = dict(bg="#1e1e1e", fg="#cccccc", font=("Segoe UI", 9))
        ent_style = dict(bg="#2d2d2d", fg="#ffffff", insertbackground="#ffffff",
                         relief="flat", font=("Segoe UI", 9))
        rb_cfg    = dict(bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                         activebackground="#1e1e1e", activeforeground="#ffffff",
                         font=("Segoe UI", 9))
        chk_cfg   = dict(bg="#1e1e1e", fg="#cccccc", selectcolor="#2d2d2d",
                         activebackground="#1e1e1e", activeforeground="#ffffff",
                         font=("Segoe UI", 9))

        def section(text, parent):
            f = tk.Frame(parent, bg="#1e1e1e")
            f.pack(fill="x", pady=(14, 6))
            tk.Label(f, text=text, bg="#1e1e1e", fg="#ffffff", font=("Segoe UI", 10, "bold")).pack(side="left")
            tk.Frame(f, bg="#333333", height=1).pack(side="left", fill="x", expand=True, padx=(8, 0), pady=6)

        def _om(parent, var, values, width=None):
            om = tk.OptionMenu(parent, var, *values)
            om.config(bg="#333333", fg="#cccccc", activebackground="#444444",
                      activeforeground="#ffffff", highlightthickness=0,
                      relief="flat", font=("Segoe UI", 9), anchor="w")
            if width:
                om.config(width=width)
            om["menu"].config(bg="#333333", fg="#cccccc",
                              activebackground="#0078d4", activeforeground="#ffffff")
            return om

        def _hotkey_row(row, outer, var):
            warn = tk.Label(outer, text="", bg="#1e1e1e", fg="#e05252", font=("Segoe UI", 8))
            def _check(*_):
                ok, msg = validate_hotkey(var.get())
                warn.config(text="" if ok else "⚠  " + msg)
            def _record():
                def _got(combo):
                    ok, msg = validate_hotkey(combo)
                    var.set(combo)
                    warn.config(text="" if ok else "⚠  " + msg)
                capture_hotkey(win, _got)

            tk.Entry(row, textvariable=var, width=18, **ent_style).pack(side="left")
            tk.Button(row, text="Record", command=_record, bg="#3c3c3c", fg="#cccccc", relief="flat",
                      font=("Segoe UI", 8), activebackground="#4c4c4c", activeforeground="#ffffff").pack(side="left", padx=(6, 0))
            var.trace_add("write", _check)
            _check()
            return warn

        # ══ TAB 1: VOICE TYPING ══════════════════════════════════════════════
        section("Speech Recognition Engine", tab_stt)
        stt_var = tk.StringVar(value=current_cfg.get("stt_engine", "google"))
        stt_section = tk.Frame(tab_stt, bg="#1e1e1e")
        stt_section.pack(fill="x")

        stt_radios = []
        for label, val in [
                ("Google (free, unofficial, no key)", "google"),
                ("Gemini (detects language automatically)", "gemini"),
                ("Google Cloud (official, API key required)", "google-cloud"),
                ("Groq Whisper (API key required)", "groq"),
                ("Google Extension (Chrome in background)", "google-ext")]:
            rb = tk.Radiobutton(stt_section, text=label, variable=stt_var, value=val, **rb_cfg)
            rb.pack(anchor="w")
            stt_radios.append(rb)

        stt_extra = tk.Frame(stt_section, bg="#1e1e1e")
        stt_extra.pack(fill="x")

        # Groq sub-frame
        groq_frame = tk.Frame(stt_extra, bg="#252525", padx=12, pady=8)
        row_g1 = tk.Frame(groq_frame, bg="#252525"); row_g1.pack(fill="x", pady=2)
        tk.Label(row_g1, text="Groq API key:", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        key_var = tk.StringVar(value=current_cfg.get("groq_api_key", ""))
        tk.Entry(row_g1, textvariable=key_var, width=34, show="*", **{**ent_style, "bg": "#333333"}).pack(side="left")
        row_g2 = tk.Frame(groq_frame, bg="#252525"); row_g2.pack(fill="x", pady=2)
        tk.Label(row_g2, text="Model:", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        model_var = tk.StringVar(value=current_cfg.get("model", "whisper-large-v3-turbo"))
        tk.Entry(row_g2, textvariable=model_var, width=34, **{**ent_style, "bg": "#333333"}).pack(side="left")

        # Google Cloud sub-frame
        gcloud_frame = tk.Frame(stt_extra, bg="#252525", padx=12, pady=8)
        row_gc = tk.Frame(gcloud_frame, bg="#252525"); row_gc.pack(fill="x", pady=2)
        tk.Label(row_gc, text="API key:", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        gcloud_key_var = tk.StringVar(value=current_cfg.get("google_cloud_api_key", ""))
        tk.Entry(row_gc, textvariable=gcloud_key_var, width=34, show="*", **{**ent_style, "bg": "#333333"}).pack(side="left")

        # Gemini STT sub-frame
        gstt_frame = tk.Frame(stt_extra, bg="#252525", padx=12, pady=8)
        row_gm = tk.Frame(gstt_frame, bg="#252525"); row_gm.pack(fill="x", pady=2)
        tk.Label(row_gm, text="Model:", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        gstt_model_var = tk.StringVar(value=current_cfg.get("gemini_stt_model", GEMINI_STT_DEFAULT_MODEL))
        tk.Entry(row_gm, textvariable=gstt_model_var, width=34, **{**ent_style, "bg": "#333333"}).pack(side="left")

        def _refresh_stt_extra(*_):
            eng = stt_var.get()
            for child in stt_extra.winfo_children():
                child.pack_forget()
            if eng == "groq":
                stt_extra.configure(bg="#252525"); groq_frame.pack(fill="x")
            elif eng == "google-cloud":
                stt_extra.configure(bg="#252525"); gcloud_frame.pack(fill="x")
            elif eng == "gemini":
                stt_extra.configure(bg="#252525"); gstt_frame.pack(fill="x")
            else:
                stt_extra.configure(bg="#1e1e1e")

        stt_var.trace_add("write", _refresh_stt_extra)
        _refresh_stt_extra()

        # ══ TAB 2: PROMPT ════════════════════════════════════════════════════
        section("Prompt Mode", tab_prompt)
        prompt_var = tk.StringVar(value=current_cfg.get("prompt_mode", "off"))
        for label, val in [
                ("Off (paste raw transcript)", "off"),
                ("Gemini Flash Lite (transcribe via STT, then refine via Gemini)", "gemini-lite"),
                ("Gemini Flash direct (send audio straight to Gemini Flash)", "gemini-flash")]:
            tk.Radiobutton(tab_prompt, text=label, variable=prompt_var, value=val, **rb_cfg).pack(anchor="w")

        prompt_extra = tk.Frame(tab_prompt, bg="#1e1e1e")
        prompt_extra.pack(fill="x", pady=(4, 0))

        gemini_frame = tk.Frame(prompt_extra, bg="#252525", padx=12, pady=8)
        row_gk = tk.Frame(gemini_frame, bg="#252525"); row_gk.pack(fill="x", pady=2)
        tk.Label(row_gk, text="Gemini API Key:", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        gemini_key_var = tk.StringVar(value=current_cfg.get("gemini_api_key", ""))
        tk.Entry(row_gk, textvariable=gemini_key_var, width=34, show="*", **{**ent_style, "bg": "#333333"}).pack(side="left")

        row_gurl = tk.Frame(gemini_frame, bg="#252525"); row_gurl.pack(fill="x", pady=2)
        tk.Label(row_gurl, text="Base URL (opt):", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        gemini_url_var = tk.StringVar(value=current_cfg.get("gemini_base_url", ""))
        tk.Entry(row_gurl, textvariable=gemini_url_var, width=34, **{**ent_style, "bg": "#333333"}).pack(side="left")

        row_gtok = tk.Frame(gemini_frame, bg="#252525"); row_gtok.pack(fill="x", pady=2)
        tk.Label(row_gtok, text="Bearer Token:", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        gemini_token_var = tk.StringVar(value=current_cfg.get("gemini_auth_token", ""))
        tk.Entry(row_gtok, textvariable=gemini_token_var, width=34, show="*", **{**ent_style, "bg": "#333333"}).pack(side="left")

        tk.Label(gemini_frame, text="System Prompt:", anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(anchor="w", pady=(6, 2))
        prompt_wrap = tk.Frame(gemini_frame, bg="#252525"); prompt_wrap.pack(fill="x")
        gemini_prompt_text = tk.Text(prompt_wrap, height=4, bg="#333333", fg="#ffffff", insertbackground="#ffffff", relief="flat", font=("Segoe UI", 9), wrap="word")
        gemini_prompt_text.insert("1.0", current_cfg.get("gemini_system_prompt", GEMINI_DEFAULT_SYSTEM_PROMPT))
        gemini_prompt_text.pack(side="left", fill="x", expand=True)

        quality_frame = tk.Frame(gemini_frame, bg="#252525"); quality_frame.pack(fill="x", pady=(8, 0))
        row_think = tk.Frame(quality_frame, bg="#252525"); row_think.pack(fill="x", pady=2)
        tk.Label(row_think, text="Thinking level:", width=16, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        thinking_var = tk.StringVar(value=current_cfg.get("gemini_thinking_level", "LOW"))
        _om(row_think, thinking_var, ["MINIMAL", "LOW", "MEDIUM", "HIGH"]).pack(side="left")

        row_res = tk.Frame(quality_frame, bg="#252525"); row_res.pack(fill="x", pady=2)
        tk.Label(row_res, text="Media resolution:", width=16, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        media_res_var = tk.StringVar(value=current_cfg.get("gemini_media_resolution", "LOW"))
        _om(row_res, media_res_var, ["LOW", "MEDIUM", "HIGH"]).pack(side="left")

        def _refresh_prompt(*_):
            mode = prompt_var.get()
            for child in prompt_extra.winfo_children():
                child.pack_forget()
            if mode in ("gemini-lite", "gemini-flash"):
                prompt_extra.configure(bg="#252525"); gemini_frame.pack(fill="x")
            else:
                prompt_extra.configure(bg="#1e1e1e")

        prompt_var.trace_add("write", _refresh_prompt)
        _refresh_prompt()

        # ══ TAB 3: TEXT → SPEECH ════════════════════════════════════════════
        section("Read Selected Text", tab_tts)
        tts_on_var = tk.BooleanVar(value=current_cfg.get("tts_enabled", True))
        tk.Checkbutton(tab_tts, text="Enable (select text anywhere, press hotkey, hear it)",
                       variable=tts_on_var, **chk_cfg).pack(anchor="w")

        row_th = tk.Frame(tab_tts, bg="#1e1e1e"); row_th.pack(fill="x", pady=(6, 2))
        tk.Label(row_th, text="Hotkey:", width=12, anchor="w", **lbl_style).pack(side="left")
        tts_hotkey_var = tk.StringVar(value=current_cfg.get("tts_hotkey", "win+shift"))
        _hotkey_row(row_th, tab_tts, tts_hotkey_var).pack(anchor="w", pady=(0, 4))

        section("Voice Engine", tab_tts)
        tts_eng_var = tk.StringVar(value=current_cfg.get("tts_engine", "vertex"))
        for label, val in [
                ("Edge (free, no key, Microsoft neural voices)", "edge"),
                ("Gemini TTS (AI Studio / Vertex / Proxy)", "vertex")]:
            tk.Radiobutton(tab_tts, text=label, variable=tts_eng_var, value=val, **rb_cfg).pack(anchor="w")

        tts_extra = tk.Frame(tab_tts, bg="#1e1e1e"); tts_extra.pack(fill="x", pady=(4, 0))

        # Edge sub-frame
        edge_frame = tk.Frame(tts_extra, bg="#252525", padx=12, pady=8)
        row_ev = tk.Frame(edge_frame, bg="#252525"); row_ev.pack(fill="x", pady=2)
        tk.Label(row_ev, text="Voice:", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        edge_voice_var = tk.StringVar(value=current_cfg.get("tts_edge_voice", "fa-IR-FaridNeural"))
        _om(row_ev, edge_voice_var, TTS_EDGE_VOICES, width=24).pack(side="left")

        # Vertex sub-frame
        vx_frame = tk.Frame(tts_extra, bg="#252525", padx=12, pady=8)

        def _vx_row(label, value, values):
            r = tk.Frame(vx_frame, bg="#252525"); r.pack(fill="x", pady=2)
            tk.Label(r, text=label, width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
            v = tk.StringVar(value=value)
            _om(r, v, values, width=28).pack(side="left")
            return v

        presets = current_cfg.get("tts_presets", TTS_STYLE_PRESETS)
        preset_keys = [k for k in ["charon_calm", "fenrir_pragmatic", "aoede_neutral", "puck_quick", "custom"] if k in presets]
        preset_names = [presets[k]["name"] for k in preset_keys]
        preset_map = {presets[k]["name"]: k for k in preset_keys}
        cur_preset_key = current_cfg.get("tts_vertex_preset", "charon_calm")
        cur_preset_name = presets.get(cur_preset_key, {}).get("name", preset_names[0] if preset_names else "Charon")
        vx_preset_var = tk.StringVar(value=cur_preset_name)

        r_pres = tk.Frame(vx_frame, bg="#252525"); r_pres.pack(fill="x", pady=2)
        tk.Label(r_pres, text="Voice Preset:", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        _om(r_pres, vx_preset_var, preset_names, width=28).pack(side="left")

        init_voice = current_cfg.get("tts_vertex_voice") or presets.get(cur_preset_key, {}).get("voice", "Charon")
        vx_voice_var = _vx_row("Voice:", init_voice, TTS_VERTEX_VOICES)
        vx_model_var = _vx_row("Model:", current_cfg.get("tts_vertex_model", "gemini-3.1-flash-tts-preview"), TTS_VERTEX_MODELS)

        r_apikey = tk.Frame(vx_frame, bg="#252525"); r_apikey.pack(fill="x", pady=2)
        tk.Label(r_apikey, text="API Key / Token:", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        vx_key_var = tk.StringVar(value=current_cfg.get("gemini_api_key", ""))
        tk.Entry(r_apikey, textvariable=vx_key_var, width=30, show="*", **{**ent_style, "bg": "#333333"}).pack(side="left")

        r_baseurl = tk.Frame(vx_frame, bg="#252525"); r_baseurl.pack(fill="x", pady=2)
        tk.Label(r_baseurl, text="Base URL (opt):", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        vx_base_var = tk.StringVar(value=current_cfg.get("gemini_base_url", ""))
        tk.Entry(r_baseurl, textvariable=vx_base_var, width=30, **{**ent_style, "bg": "#333333"}).pack(side="left")

        r_cred = tk.Frame(vx_frame, bg="#252525"); r_cred.pack(fill="x", pady=2)
        tk.Label(r_cred, text="Credential JSON:", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        vx_cred_var = tk.StringVar(value=current_cfg.get("tts_vertex_cred", ""))
        tk.Entry(r_cred, textvariable=vx_cred_var, width=30, **{**ent_style, "bg": "#333333"}).pack(side="left")

        def _pick_cred():
            p = filedialog.askopenfilename(parent=win, title="Google credential JSON", filetypes=[("JSON", "*.json")])
            if p: vx_cred_var.set(p)

        tk.Button(r_cred, text="...", command=_pick_cred, width=3, bg="#3c3c3c", fg="#cccccc", relief="flat").pack(side="left", padx=(4, 0))

        r_proj = tk.Frame(vx_frame, bg="#252525"); r_proj.pack(fill="x", pady=2)
        tk.Label(r_proj, text="Project id:", width=14, anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(side="left")
        vx_proj_var = tk.StringVar(value=current_cfg.get("tts_vertex_project", ""))
        tk.Entry(r_proj, textvariable=vx_proj_var, width=30, **{**ent_style, "bg": "#333333"}).pack(side="left")

        tk.Label(vx_frame, text="Tone prompt:", anchor="w", bg="#252525", fg="#cccccc", font=("Segoe UI", 9)).pack(anchor="w", pady=(8, 2))
        style_wrap = tk.Frame(vx_frame, bg="#252525"); style_wrap.pack(fill="x")
        tts_style_text = tk.Text(style_wrap, height=4, bg="#333333", fg="#ffffff", insertbackground="#ffffff", relief="flat", font=("Segoe UI", 9), wrap="word")
        init_style = current_cfg.get("tts_style") or presets.get(cur_preset_key, {}).get("style", TTS_DEFAULT_STYLE)
        tts_style_text.insert("1.0", init_style)
        tts_style_text.pack(side="left", fill="x", expand=True)

        def _on_preset_selected(*_):
            k = preset_map.get(vx_preset_var.get(), "custom")
            if k != "custom" and k in presets:
                p = presets[k]
                vx_voice_var.set(p["voice"])
                tts_style_text.delete("1.0", "end")
                tts_style_text.insert("1.0", p["style"])

        vx_preset_var.trace_add("write", _on_preset_selected)

        def _reset_tts_style():
            tts_style_text.delete("1.0", "end")
            tts_style_text.insert("1.0", TTS_DEFAULT_STYLE)
            vx_preset_var.set("Custom...")

        tk.Button(vx_frame, text="Reset to default", command=_reset_tts_style, bg="#3c3c3c", fg="#888888", relief="flat", font=("Segoe UI", 8)).pack(anchor="e", pady=(4, 0))

        def _refresh_tts_extra(*_):
            for child in tts_extra.winfo_children():
                child.pack_forget()
            tts_extra.configure(bg="#252525")
            (vx_frame if tts_eng_var.get() == "vertex" else edge_frame).pack(fill="x")

        tts_eng_var.trace_add("write", _refresh_tts_extra)
        _refresh_tts_extra()

        section("Playback", tab_tts)
        row_sp = tk.Frame(tab_tts, bg="#1e1e1e"); row_sp.pack(fill="x", pady=2)
        tk.Label(row_sp, text="Speed:", width=12, anchor="w", **lbl_style).pack(side="left")
        tts_speed_var = tk.DoubleVar(value=float(current_cfg.get("tts_speed", 1.0)))
        speed_lbl = tk.Label(row_sp, text=f"{tts_speed_var.get():.2f}x", width=6, bg="#1e1e1e", fg="#cccccc", font=("Segoe UI", 9))
        tk.Scale(row_sp, from_=0.5, to=2.5, resolution=0.05, orient="horizontal",
                 variable=tts_speed_var, showvalue=False, length=200, bg="#1e1e1e",
                 troughcolor="#2d2d2d", highlightthickness=0, bd=0, sliderrelief="flat",
                 activebackground="#0078d4", command=lambda v: speed_lbl.config(text=f"{float(v):.2f}x")).pack(side="left")
        speed_lbl.pack(side="left", padx=(6, 0))

        tts_popup_var = tk.BooleanVar(value=current_cfg.get("tts_popup", True))
        tk.Checkbutton(tab_tts, text="Show the player widget while reading", variable=tts_popup_var, **chk_cfg).pack(anchor="w", pady=(8, 0))

        row_ac = tk.Frame(tab_tts, bg="#1e1e1e"); row_ac.pack(fill="x", pady=2)
        tk.Label(row_ac, text="Auto-close:", width=12, anchor="w", **lbl_style).pack(side="left")
        tts_close_var = tk.StringVar(value=str(current_cfg.get("tts_popup_autoclose", 30)))
        tk.Entry(row_ac, textvariable=tts_close_var, width=6, **ent_style).pack(side="left")
        tk.Label(row_ac, text="seconds after playback ends  (0 = keep open)", bg="#1e1e1e", fg="#666666", font=("Segoe UI", 8)).pack(side="left", padx=(6, 0))

        def _test_voice():
            if on_test_voice_callback:
                on_test_voice_callback(_collect())

        tk.Button(tab_tts, text="🔊  Test voice", command=_test_voice, bg="#3c3c3c", fg="#cccccc",
                  relief="flat", font=("Segoe UI", 9)).pack(anchor="w", pady=(12, 4))

        # ══ TAB 4: GENERAL ═══════════════════════════════════════════════════
        section("Voice Typing", tab_gen)
        row1 = tk.Frame(tab_gen, bg="#1e1e1e"); row1.pack(fill="x", pady=2)
        tk.Label(row1, text="Hotkey:", width=12, anchor="w", **lbl_style).pack(side="left")
        hotkey_var = tk.StringVar(value=current_cfg.get("hotkey", "win+alt"))
        _hotkey_row(row1, tab_gen, hotkey_var).pack(anchor="w", pady=(0, 4))

        row2 = tk.Frame(tab_gen, bg="#1e1e1e"); row2.pack(fill="x", pady=2)
        tk.Label(row2, text="Language:", width=12, anchor="w", **lbl_style).pack(side="left")
        lang_var = tk.StringVar(value=current_cfg.get("language", "fa"))
        lang_entry = tk.Entry(row2, textvariable=lang_var, width=20, **ent_style); lang_entry.pack(side="left")

        lang_mode_var = tk.BooleanVar(value=(current_cfg.get("lang_mode", "fixed") == "keyboard"))
        def _toggle_lang_mode(*_):
            if lang_mode_var.get(): lang_entry.config(state="disabled")
            else: lang_entry.config(state="normal")
        tk.Checkbutton(tab_gen, text="Follow Windows keyboard layout (auto-detect Persian / English)",
                       variable=lang_mode_var, command=_toggle_lang_mode, **chk_cfg).pack(anchor="w", pady=(2, 0))
        _toggle_lang_mode()

        section("Microphone", tab_gen)
        mic_var = tk.StringVar(value=current_cfg.get("mic_mode", "always"))
        tk.Radiobutton(tab_gen, text="Always on  (pre-roll active, mic indicator always visible)", variable=mic_var, value="always", **rb_cfg).pack(anchor="w")
        tk.Radiobutton(tab_gen, text="On demand  (mic opens only while hotkey held)", variable=mic_var, value="on_demand", **rb_cfg).pack(anchor="w")

        section("Text insertion", tab_gen)
        inject_var = tk.StringVar(value=current_cfg.get("inject_mode", "auto"))
        for label, val in [
                ("Automatic (type short text, paste long text)", "auto"),
                ("Always type (key by key, never touches clipboard)", "type"),
                ("Always paste (for apps that ignore synthetic keystrokes)", "paste")]:
            tk.Radiobutton(tab_gen, text=label, variable=inject_var, value=val, **rb_cfg).pack(anchor="w")

        section("Options", tab_gen)
        updates_var = tk.BooleanVar(value=current_cfg.get("check_updates", True))
        tk.Checkbutton(tab_gen, text="Check for updates on startup", variable=updates_var, **chk_cfg).pack(anchor="w")
        notify_var = tk.BooleanVar(value=current_cfg.get("notify_errors", True))
        tk.Checkbutton(tab_gen, text="Show a notification when something fails", variable=notify_var, **chk_cfg).pack(anchor="w")

        # ── Sticky Footer ────────────────────────────────────────────────────
        footer_area = tk.Frame(win, bg="#1e1e1e", padx=20, pady=10)
        footer_area.pack(side="bottom", fill="x")
        btn_frame = tk.Frame(footer_area, bg="#1e1e1e"); btn_frame.pack(fill="x")

        def _collect():
            try: autoclose = max(0, int(tts_close_var.get().strip() or 0))
            except ValueError: autoclose = 30
            k_api = vx_key_var.get().strip() or gemini_key_var.get().strip()
            u_base = vx_base_var.get().strip() or gemini_url_var.get().strip()
            p_key = preset_map.get(vx_preset_var.get(), "custom")
            return {
                "stt_engine":           stt_var.get(),
                "prompt_mode":          prompt_var.get(),
                "hotkey":               hotkey_var.get().strip(),
                "language":             lang_var.get().strip(),
                "lang_mode":            "keyboard" if lang_mode_var.get() else "fixed",
                "mic_mode":             mic_var.get(),
                "groq_api_key":         key_var.get().strip(),
                "model":                model_var.get().strip(),
                "google_cloud_api_key": gcloud_key_var.get().strip(),
                "ws_port":              current_cfg.get("ws_port", 9137),
                "check_updates":        updates_var.get(),
                "gemini_api_key":       k_api,
                "gemini_base_url":      u_base,
                "gemini_auth_token":    gemini_token_var.get().strip(),
                "gemini_system_prompt": gemini_prompt_text.get("1.0", "end-1c").strip(),
                "gemini_thinking_level": thinking_var.get(),
                "gemini_media_resolution": media_res_var.get(),
                "gemini_stt_model":     gstt_model_var.get().strip() or GEMINI_STT_DEFAULT_MODEL,
                "inject_mode":          inject_var.get(),
                "notify_errors":        notify_var.get(),
                "tts_enabled":          tts_on_var.get(),
                "tts_hotkey":           tts_hotkey_var.get().strip(),
                "tts_engine":           tts_eng_var.get(),
                "tts_edge_voice":       edge_voice_var.get(),
                "tts_vertex_preset":    p_key,
                "tts_vertex_voice":     vx_voice_var.get(),
                "tts_vertex_model":     vx_model_var.get(),
                "tts_vertex_cred":      vx_cred_var.get().strip(),
                "tts_vertex_project":   vx_proj_var.get().strip(),
                "tts_style":            tts_style_text.get("1.0", "end-1c").strip(),
                "tts_speed":            round(float(tts_speed_var.get()), 2),
                "tts_popup":            tts_popup_var.get(),
                "tts_popup_autoclose":  autoclose,
                "tts_presets":          presets,
            }

        def _on_close():
            global _settings_window
            _settings_window = None
            win.destroy()

        def on_save():
            cfg = _collect()
            if on_save_callback:
                on_save_callback(cfg)
            _on_close()

        tk.Button(btn_frame, text="Save", command=on_save, width=10, bg="#0078d4", fg="white", relief="flat", font=("Segoe UI", 9, "bold")).pack(side="right", padx=(6, 0))
        tk.Button(btn_frame, text="Cancel", command=_on_close, width=10, bg="#3c3c3c", fg="#cccccc", relief="flat", font=("Segoe UI", 9)).pack(side="right")

        win.protocol("WM_DELETE_WINDOW", _on_close)

        win.update_idletasks()
        w, h = 540, 620
        sw, sh = win.winfo_screenwidth(), win.winfo_screenheight()
        win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        win.deiconify()
        _settings_window = win

    ui_run(_build)

