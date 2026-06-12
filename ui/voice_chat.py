import tkinter as tk
import customtkinter as ctk
import threading
import asyncio
import tempfile
import os
import time
import re
from datetime import datetime
import speech_recognition as sr
import edge_tts
from playsound import playsound
from core.memory import load_history, save_history, add_message, to_ollama_messages, clear_history
from core.brain import client, MODEL, SYSTEM_PROMPT

# ─────────────────────────────────────────
#  CONFIG
# ─────────────────────────────────────────
VOICES = {
    "ชาย (ไทย)"   : "th-TH-NiwatNeural",
    "หญิง (ไทย)"  : "th-TH-PremwadeeNeural",
    "Male (EN)"   : "en-US-GuyNeural",
    "Female (EN)" : "en-US-JennyNeural",
}

# ══════════════════════════════════════════
#  PREMIUM PALETTE
# ══════════════════════════════════════════
DARK = {
    "BG"        : "#070711",
    "BG2"       : "#0c0c1a",
    "BG3"       : "#13132a",
    "BG4"       : "#1b1b38",
    "BUBBLE_U"  : "#3b3bdc",
    "BUBBLE_U2" : "#5252f0",
    "BUBBLE_A"  : "#13132a",
    "INPUT_BG"  : "#0f0f22",
    "TEXT"      : "#ececf1",
    "TEXT2"     : "#9898b2",
    "ACCENT"    : "#6c63ff",
    "ACCENT2"   : "#8b5cf6",
    "GREEN"     : "#34d399",
    "RED"       : "#f87171",
    "GOLD"      : "#fbbf24",
    "BORDER"    : "#23234a",
    "DIVIDER"   : "#1a1a38",
}

LIGHT = {
    "BG"        : "#f4f5fc",
    "BG2"       : "#ffffff",
    "BG3"       : "#eef0fb",
    "BG4"       : "#e0e3f8",
    "BUBBLE_U"  : "#4f46e5",
    "BUBBLE_U2" : "#6366f1",
    "BUBBLE_A"  : "#ffffff",
    "INPUT_BG"  : "#ffffff",
    "TEXT"      : "#1a1a2e",
    "TEXT2"     : "#8888a0",
    "ACCENT"    : "#4f46e5",
    "ACCENT2"   : "#7c3aed",
    "GREEN"     : "#059669",
    "RED"       : "#dc2626",
    "GOLD"      : "#d97706",
    "BORDER"    : "#dde0f8",
    "DIVIDER"   : "#e8eaf6",
}

# ─────────────────────────────────────────
#  TTS / AUDIO
# ─────────────────────────────────────────
_current_voice = list(VOICES.values())[0]
_is_speaking = False

async def _tts(text, path, voice):
    await edge_tts.Communicate(text, voice).save(path)

def stop_speech():
    global _is_speaking
    _is_speaking = False

def speak(text, voice=None):
    global _is_speaking
    v = voice or _current_voice
    clean = re.sub(r"[*_`#>\-•]", " ", text).strip()
    clean = re.sub(r"\s+", " ", clean)
    def _run():
        global _is_speaking
        try:
            fd, tmp = tempfile.mkstemp(suffix=".mp3")
            os.close(fd)
            asyncio.run(_tts(clean, tmp, v))
            _is_speaking = True
            playsound(tmp)
            _is_speaking = False
            os.remove(tmp)
        except Exception as e:
            _is_speaking = False
            print(f"[TTS] {e}")
    threading.Thread(target=_run, daemon=True).start()

def is_speaking():
    return _is_speaking

# ─────────────────────────────────────────
#  AI STREAM
# ─────────────────────────────────────────
def ask_stream(messages, on_token, on_done, cancel_flag):
    def _run():
        try:
            full = ""
            stream = client.chat.completions.create(
                model=MODEL, messages=messages, max_tokens=800, stream=True,
            )
            for chunk in stream:
                if cancel_flag[0]:
                    on_done(full, cancelled=True)
                    return
                token = chunk.choices[0].delta.content or ""
                full += token
                on_token(token)
            on_done(full, cancelled=False)
        except Exception as e:
            on_token(f"\n[Error] {e}")
            on_done("", cancelled=False)
    threading.Thread(target=_run, daemon=True).start()

# ─────────────────────────────────────────
#  STT
# ─────────────────────────────────────────
recognizer = sr.Recognizer()

def listen_once():
    try:
        with sr.Microphone() as source:
            recognizer.adjust_for_ambient_noise(source, duration=0.3)
            try:
                audio = recognizer.listen(source, timeout=6, phrase_time_limit=14)
                return recognizer.recognize_google(audio, language="th-TH")
            except:
                return None
    except Exception as e:
        print(f"[STT] {e}")
        return None

# ─────────────────────────────────────────
#  MARKDOWN
# ─────────────────────────────────────────
def parse_md(text):
    segments = []
    in_code = False
    for line in text.split("\n"):
        if line.strip().startswith("```"):
            in_code = not in_code
            segments.append((line + "\n", "code"))
            continue
        if in_code:
            segments.append((line + "\n", "code"))
            continue
        if line.startswith("### "):
            segments.append((line[4:] + "\n", "h3"))
        elif line.startswith("## "):
            segments.append((line[3:] + "\n", "h2"))
        elif line.startswith("# "):
            segments.append((line[2:] + "\n", "h1"))
        elif line.startswith(("- ", "• ")):
            segments.append(("  • " + line[2:] + "\n", "bullet"))
        else:
            for p in re.split(r"(\*\*.*?\*\*)", line):
                if p.startswith("**") and p.endswith("**"):
                    segments.append((p[2:-2], "bold"))
                else:
                    segments.append((p, "normal"))
            segments.append(("\n", "normal"))
    return segments

# ══════════════════════════════════════════
#  MAIN APP
# ══════════════════════════════════════════
class LouisAI:
    def __init__(self, root):
        self.root = root
        self.root.title("LouisAI — AI Assistant")
        self.root.geometry("860x920")
        self.root.minsize(660, 640)

        self._mode = "dark"
        self.C = DARK
        self.history = load_history()
        self.chat_widgets = []
        self.realtime_on = False
        self.cancel_flag = [False]
        self.is_thinking = False
        self.dot_job = None
        self._pulse_job = None
        self._ph = True
        self._ph_txt = "พิมพ์ข้อความที่นี่...   (Enter ส่ง  |  Shift+Enter ขึ้นบรรทัด)"
        self.voice_key = list(VOICES.keys())[0]

        # ── Widget registries for theme switching ──
        # Each list holds (widget, option, color_key) tuples
        self._theme_bg    = []   # widgets that take bg = C[color_key]
        self._theme_fg    = []   # widgets that take fg = C[color_key]
        self._theme_both  = []   # (widget, bg_key, fg_key)
        self._theme_canvas = []  # (canvas, bg_key, item_configs)
        self._theme_ctk   = []   # (ctk_widget, {option: color_key})

        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")

        try:
            self.root.configure(fg_color=self.C["BG"])
        except Exception:
            self.root.configure(bg=self.C["BG"])

        self._build()
        self._load_history_to_ui()

    # ══ THEME REGISTRY HELPERS ════════════
    def _reg_bg(self, widget, key="BG"):
        """Register a widget whose bg= should follow C[key]."""
        self._theme_bg.append((widget, key))
        return widget

    def _reg_both(self, widget, bg_key="BG2", fg_key="TEXT"):
        """Register a widget whose bg AND fg should update."""
        self._theme_both.append((widget, bg_key, fg_key))
        return widget

    def _reg_ctk(self, widget, **options):
        """Register a CustomTkinter widget with named color keys."""
        self._theme_ctk.append((widget, options))
        return widget

    # ══ BUILD ════════════════════════════
    def _build(self):
        self._build_topbar()
        self._build_chat()
        self._build_bottom()

    # ── TOP BAR ──────────────────────────
    def _build_topbar(self):
        self.topbar = tk.Frame(self.root, bg=self.C["BG2"], height=66)
        self.topbar.pack(fill=tk.X)
        self.topbar.pack_propagate(False)
        self._reg_bg(self.topbar, "BG2")

        # ── Left: avatar + name ──
        left = tk.Frame(self.topbar, bg=self.C["BG2"])
        left.pack(side=tk.LEFT, padx=16)
        self._reg_bg(left, "BG2")

        self._avatar_canvas = tk.Canvas(left, width=40, height=40,
                                        bg=self.C["BG2"], highlightthickness=0)
        self._avatar_canvas.pack(side=tk.LEFT, pady=13)
        self._av_oval = self._avatar_canvas.create_oval(
            2, 2, 38, 38, fill=self.C["ACCENT"], outline=self.C["ACCENT2"], width=2)
        self._av_text = self._avatar_canvas.create_text(
            20, 20, text="✦", fill="white", font=("Segoe UI", 14, "bold"))
        # Register canvas for bg update
        self._theme_canvas.append((
            self._avatar_canvas, "BG2",
            [(self._av_oval, {"fill": "ACCENT", "outline": "ACCENT2"})]
        ))

        name_frame = tk.Frame(left, bg=self.C["BG2"])
        name_frame.pack(side=tk.LEFT, padx=10)
        self._reg_bg(name_frame, "BG2")

        self._name_label = tk.Label(name_frame, text="LouisAI",
                                    bg=self.C["BG2"], fg=self.C["TEXT"],
                                    font=("Segoe UI", 16, "bold"))
        self._name_label.pack(anchor="w")
        self._reg_both(self._name_label, "BG2", "TEXT")

        status_row = tk.Frame(name_frame, bg=self.C["BG2"])
        status_row.pack(anchor="w")
        self._reg_bg(status_row, "BG2")

        self._dot_canvas = tk.Canvas(status_row, width=10, height=10,
                                     bg=self.C["BG2"], highlightthickness=0)
        self._dot_canvas.pack(side=tk.LEFT, pady=2)
        self._dot_id = self._dot_canvas.create_oval(1, 1, 9, 9,
                                                     fill=self.C["GREEN"], outline="")
        self._theme_canvas.append((
            self._dot_canvas, "BG2",
            [(self._dot_id, {"fill": "GREEN"})]
        ))

        self._status_label = tk.Label(status_row, text="พร้อม",
                                      bg=self.C["BG2"], fg=self.C["GREEN"],
                                      font=("Segoe UI", 10))
        self._status_label.pack(side=tk.LEFT, padx=4)
        self._reg_both(self._status_label, "BG2", "GREEN")

        # ── Right: controls ──
        right = tk.Frame(self.topbar, bg=self.C["BG2"])
        right.pack(side=tk.RIGHT, padx=14)
        self._reg_bg(right, "BG2")

        self.voice_var = tk.StringVar(value=self.voice_key)
        self._voice_om = ctk.CTkOptionMenu(
            right, values=list(VOICES.keys()), width=130,
            variable=self.voice_var,
            font=ctk.CTkFont("Segoe UI", 12),
            fg_color=self.C["BG4"],
            button_color=self.C["ACCENT"],
            button_hover_color=self.C["ACCENT2"],
            text_color=self.C["TEXT"],
            command=self._on_voice_change)
        self._voice_om.pack(side=tk.LEFT, padx=6, pady=18)
        self._reg_ctk(self._voice_om,
                      fg_color="BG4", button_color="ACCENT",
                      button_hover_color="ACCENT2", text_color="TEXT")

        self._btn_theme = self._icon_btn(right, "☀️", self.toggle_theme, "BG2")
        self._btn_theme.pack(side=tk.LEFT, padx=3)

        self._btn_clear = self._icon_btn(right, "🗑", self.clear_chat, "BG2")
        self._btn_clear.pack(side=tk.LEFT, padx=3)

        # Divider
        self._topbar_div = tk.Frame(self.root, bg=self.C["BORDER"], height=1)
        self._topbar_div.pack(fill=tk.X)
        self._reg_bg(self._topbar_div, "BORDER")

    def _icon_btn(self, parent, text, command, bg_key="BG2"):
        btn = tk.Label(parent, text=text, bg=self.C[bg_key],
                       font=("Segoe UI", 17), cursor="hand2", padx=4)
        btn.bind("<Button-1>", lambda e: command())
        btn.bind("<Enter>",    lambda e: btn.configure(bg=self.C["BG4"]))
        btn.bind("<Leave>",    lambda e: btn.configure(bg=self.C[bg_key]))
        self._reg_both(btn, bg_key, "TEXT")
        return btn

    # ── CHAT AREA ────────────────────────
    def _build_chat(self):
        self._chat_outer = tk.Frame(self.root, bg=self.C["BG"])
        self._chat_outer.pack(fill=tk.BOTH, expand=True)
        self._reg_bg(self._chat_outer, "BG")

        self._canvas = tk.Canvas(self._chat_outer, bg=self.C["BG"],
                                 highlightthickness=0, bd=0)
        self._vsb = tk.Scrollbar(self._chat_outer, orient="vertical",
                                 command=self._canvas.yview)
        self._canvas.configure(yscrollcommand=self._vsb.set)
        self._vsb.pack(side=tk.RIGHT, fill=tk.Y)
        self._canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._reg_bg(self._canvas, "BG")

        self._msg_frame = tk.Frame(self._canvas, bg=self.C["BG"])
        self._cwin = self._canvas.create_window((0, 0), window=self._msg_frame, anchor="nw")
        self._reg_bg(self._msg_frame, "BG")

        self._msg_frame.bind("<Configure>",
            lambda e: self._canvas.configure(scrollregion=self._canvas.bbox("all")))
        self._canvas.bind("<Configure>",
            lambda e: self._canvas.itemconfig(self._cwin, width=e.width))
        self._canvas.bind_all("<MouseWheel>",
            lambda e: self._canvas.yview_scroll(int(-1*(e.delta/120)), "units"))

    # ── BOTTOM BAR ───────────────────────
    def _build_bottom(self):
        self._bottom_div = tk.Frame(self.root, bg=self.C["BORDER"], height=1)
        self._bottom_div.pack(fill=tk.X)
        self._reg_bg(self._bottom_div, "BORDER")

        self._bottom = tk.Frame(self.root, bg=self.C["BG2"])
        self._bottom.pack(fill=tk.X, side=tk.BOTTOM)
        self._reg_bg(self._bottom, "BG2")

        # Toolbar row
        self._toolbar = tk.Frame(self._bottom, bg=self.C["BG2"])
        self._toolbar.pack(fill=tk.X, padx=18, pady=(10, 6))
        self._reg_bg(self._toolbar, "BG2")

        self._btn_rt = ctk.CTkButton(
            self._toolbar, text="⏺  Realtime",
            fg_color=self.C["BG3"], text_color=self.C["TEXT2"],
            hover_color=self.C["BG4"],
            font=ctk.CTkFont("Segoe UI", 12, weight="bold"),
            corner_radius=20, width=0, height=30,
            command=self.toggle_rt)
        self._btn_rt.pack(side=tk.LEFT, padx=(0, 6))
        self._reg_ctk(self._btn_rt, fg_color="BG3", text_color="TEXT2", hover_color="BG4")

        self._btn_cancel = ctk.CTkButton(
            self._toolbar, text="⛔  หยุด",
            fg_color=self.C["BG3"], text_color=self.C["RED"],
            hover_color=self.C["BG4"],
            font=ctk.CTkFont("Segoe UI", 12, weight="bold"),
            corner_radius=20, width=0, height=30,
            command=self.cancel_response, state="disabled")
        self._btn_cancel.pack(side=tk.LEFT)
        self._reg_ctk(self._btn_cancel, fg_color="BG3", text_color="RED", hover_color="BG4")

        tk.Frame(self._toolbar, bg=self.C["BG2"]).pack(side=tk.LEFT, expand=True)

        self._hint = tk.Label(self._toolbar, text="",
                              bg=self.C["BG2"], fg=self.C["TEXT2"],
                              font=("Segoe UI", 10))
        self._hint.pack(side=tk.RIGHT)
        self._reg_both(self._hint, "BG2", "TEXT2")

        # Glowing input container (nested frames = border trick)
        self._glow_outer = tk.Frame(self._bottom, bg=self.C["ACCENT"], padx=2, pady=2)
        self._glow_outer.pack(fill=tk.X, padx=16, pady=(0, 14))
        self._reg_bg(self._glow_outer, "ACCENT")

        self._glow_inner = tk.Frame(self._glow_outer, bg=self.C["INPUT_BG"])
        self._glow_inner.pack(fill=tk.BOTH)
        self._reg_bg(self._glow_inner, "INPUT_BG")

        self._entry = tk.Text(self._glow_inner, font=("Segoe UI", 14),
                              bg=self.C["INPUT_BG"], fg=self.C["TEXT"],
                              insertbackground=self.C["ACCENT"],
                              relief="flat", bd=0, height=3,
                              padx=14, pady=10, wrap=tk.WORD)
        self._entry.pack(side=tk.LEFT, fill=tk.X, expand=True)
        self._reg_both(self._entry, "INPUT_BG", "TEXT")

        self._entry.bind("<Return>",      self._on_enter)
        self._entry.bind("<Shift-Return>", lambda e: None)
        self._entry.bind("<KeyPress>",    self._ph_on_keypress)
        self._entry.bind("<KeyRelease>",  self._on_key)
        self._entry.bind("<Control-v>",   self._on_paste)
        self._entry.bind("<Control-V>",   self._on_paste)
        self._entry.bind("<<Paste>>",     self._on_paste)
        self._entry.focus()

        # Placeholder
        self._entry.insert("1.0", self._ph_txt)
        self._entry.configure(fg=self.C["TEXT2"])
        self._entry.bind("<FocusIn>",  self._ph_clear)
        self._entry.bind("<FocusOut>", self._ph_add)

        btn_col = tk.Frame(self._glow_inner, bg=self.C["INPUT_BG"])
        btn_col.pack(side=tk.RIGHT, padx=6)
        self._reg_bg(btn_col, "INPUT_BG")

        self._btn_mic  = self._round_btn(btn_col, "🎤", self.on_talk,  "TEXT2",  "INPUT_BG")
        self._btn_mic.pack(pady=(6, 2))
        self._btn_send = self._round_btn(btn_col, "➤",  self.on_send,  "ACCENT", "INPUT_BG")
        self._btn_send.pack(pady=(2, 6))

    def _round_btn(self, parent, text, command, fg_key, bg_key):
        lbl = tk.Label(parent, text=text, font=("Segoe UI", 18),
                       bg=self.C[bg_key], fg=self.C[fg_key], cursor="hand2")
        lbl.bind("<Button-1>", lambda e: command())
        lbl.bind("<Enter>",    lambda e: lbl.configure(bg=self.C["BG4"]))
        lbl.bind("<Leave>",    lambda e: lbl.configure(bg=self.C[bg_key]))
        self._reg_both(lbl, bg_key, fg_key)
        return lbl

    # ── PLACEHOLDER ──────────────────────
    def _ph_clear(self, e=None):
        if self._ph:
            self._entry.delete("1.0", tk.END)
            self._entry.configure(fg=self.C["TEXT"])
            self._ph = False

    def _ph_on_keypress(self, e=None):
        if self._ph and e and e.keysym not in (
            "Return", "Tab", "Escape",
            "Shift_L", "Shift_R", "Control_L", "Control_R",
            "Alt_L", "Alt_R", "Super_L", "Super_R",
            "Left", "Right", "Up", "Down",
            "F1","F2","F3","F4","F5","F6","F7","F8","F9","F10","F11","F12",
        ):
            self._ph_clear()

    def _ph_add(self, e=None):
        try:
            focused = self._entry.focus_get() == self._entry
        except Exception:
            focused = False
        if not focused and not self._entry.get("1.0", tk.END).strip():
            self._entry.insert("1.0", self._ph_txt)
            self._entry.configure(fg=self.C["TEXT2"])
            self._ph = True

    def _on_key(self, e=None):
        if not self._ph:
            chars = len(self._entry.get("1.0", tk.END).strip())
            self._hint.configure(text=f"{chars} ตัวอักษร" if chars else "")

    def _on_paste(self, e=None):
        self._ph_clear()
        try:
            text = self.root.clipboard_get()
            if text:
                try:
                    self._entry.delete(tk.SEL_FIRST, tk.SEL_LAST)
                except Exception:
                    pass
                self._entry.insert(tk.INSERT, text)
                self._on_key()
        except Exception as ex:
            print(f"[Paste Error] {ex}")
        return "break"

    # ══ THEME SWITCH ═════════════════════
    def toggle_theme(self):
        self._mode = "light" if self._mode == "dark" else "dark"
        self.C = LIGHT if self._mode == "light" else DARK
        ctk.set_appearance_mode(self._mode)
        self._btn_theme.configure(text="🌙" if self._mode == "dark" else "☀️")
        self._apply_all_colors()

    def _apply_all_colors(self):
        C = self.C

        # Root window
        try:
            self.root.configure(fg_color=C["BG"])
        except Exception:
            self.root.configure(bg=C["BG"])

        # bg-only widgets
        for widget, key in self._theme_bg:
            try:
                widget.configure(bg=C[key])
            except Exception:
                pass

        # bg+fg widgets
        for widget, bg_key, fg_key in self._theme_both:
            try:
                widget.configure(bg=C[bg_key], fg=C[fg_key])
            except Exception:
                pass

        # Canvas backgrounds + oval/item fills
        for canvas, bg_key, items in self._theme_canvas:
            try:
                canvas.configure(bg=C[bg_key])
            except Exception:
                pass
            for item_id, cfg in items:
                kw = {k: C[v] for k, v in cfg.items()}
                try:
                    canvas.itemconfig(item_id, **kw)
                except Exception:
                    pass

        # CustomTkinter widgets
        for widget, options in self._theme_ctk:
            kw = {}
            for opt, key in options.items():
                if key in C:
                    kw[opt] = C[key]
            try:
                widget.configure(**kw)
            except Exception:
                pass

        # Entry placeholder color depends on state
        if self._ph:
            self._entry.configure(fg=C["TEXT2"])
        else:
            self._entry.configure(fg=C["TEXT"],
                                  insertbackground=C["ACCENT"])

        # Rebuild chat to re-render all bubble colors correctly
        self._rebuild_chat()

    def _rebuild_chat(self):
        """Destroy all chat bubble widgets and re-render from history."""
        for w in self.chat_widgets:
            try:
                w.destroy()
            except Exception:
                pass
        self.chat_widgets.clear()
        self._day_separator()
        if not self.history:
            self._status_line("✦  สวัสดี! ผมคือ LouisAI — พิมพ์หรือพูดเพื่อเริ่มต้นได้เลยครับ")
            return
        self._status_line(f"── โหลดประวัติ {len(self.history)} ข้อความ ──")
        for h in self.history[-20:]:
            if h["role"] == "user":
                self._bubble_user(h["content"])
            elif h["role"] == "assistant":
                txt, cb = self._bubble_ai_start()
                self._update_ai_text(txt, h["content"])
                self._setup_copy(cb, h["content"])

    # ── VOICE ────────────────────────────
    def _on_voice_change(self, value=None):
        global _current_voice
        val = value or self._voice_om.get()
        _current_voice = VOICES[val]

    # ── BUBBLES ──────────────────────────
    def _now(self):
        return datetime.now().strftime("%H:%M")

    def _bubble_user(self, text):
        C = self.C
        outer = tk.Frame(self._msg_frame, bg=C["BG"])
        outer.pack(fill=tk.X, padx=16, pady=(10, 2))
        self.chat_widgets.append(outer)

        tk.Label(outer, text=self._now(), bg=C["BG"],
                 fg=C["TEXT2"], font=("Segoe UI", 9)).pack(anchor="e", padx=6)

        row = tk.Frame(outer, bg=C["BG"])
        row.pack(anchor="e")

        bubble = tk.Frame(row, bg=C["BUBBLE_U"], padx=14, pady=9)
        bubble.pack(side=tk.RIGHT, anchor="e")
        tk.Label(bubble, text=text, bg=C["BUBBLE_U"], fg="#ffffff",
                 font=("Segoe UI", 14), wraplength=480, justify="left",
                 anchor="w").pack()

        av = tk.Canvas(row, width=34, height=34, bg=C["BG"], highlightthickness=0)
        av.pack(side=tk.RIGHT, padx=(6, 0), anchor="n", pady=2)
        av.create_oval(2, 2, 32, 32, fill=C["BUBBLE_U2"], outline=C["ACCENT"], width=1)
        av.create_text(17, 17, text="U", fill="white", font=("Segoe UI", 12, "bold"))

        self._scroll_end()

    def _bubble_ai_start(self):
        C = self.C
        outer = tk.Frame(self._msg_frame, bg=C["BG"])
        outer.pack(fill=tk.X, padx=16, pady=(10, 2))
        self.chat_widgets.append(outer)

        tk.Label(outer, text=self._now(), bg=C["BG"],
                 fg=C["TEXT2"], font=("Segoe UI", 9)).pack(anchor="w", padx=44)

        row = tk.Frame(outer, bg=C["BG"])
        row.pack(anchor="w", fill=tk.X)

        av = tk.Canvas(row, width=34, height=34, bg=C["BG"], highlightthickness=0)
        av.pack(side=tk.LEFT, padx=(0, 6), anchor="n", pady=2)
        av.create_oval(2, 2, 32, 32, fill=C["ACCENT"], outline=C["ACCENT2"], width=2)
        av.create_text(17, 17, text="✦", fill="white", font=("Segoe UI", 11, "bold"))

        right_col = tk.Frame(row, bg=C["BG"])
        right_col.pack(side=tk.LEFT, fill=tk.X, expand=True)

        name_row = tk.Frame(right_col, bg=C["BG"])
        name_row.pack(anchor="w")
        tk.Label(name_row, text="LouisAI", bg=C["BG"],
                 fg=C["ACCENT2"], font=("Segoe UI", 10, "bold")).pack(side=tk.LEFT)

        copy_lbl = tk.Label(name_row, text="📋", bg=C["BG"], fg=C["TEXT2"],
                            font=("Segoe UI", 10), cursor="hand2")
        copy_lbl.pack_forget()

        bubble_outer = tk.Frame(right_col, bg=C["BORDER"], padx=1, pady=1)
        bubble_outer.pack(anchor="w", pady=(3, 0))

        bubble = tk.Frame(bubble_outer, bg=C["BUBBLE_A"])
        bubble.pack(fill=tk.BOTH)

        txt = tk.Text(bubble, bg=C["BUBBLE_A"], fg=C["TEXT"],
                      font=("Segoe UI", 14), wrap=tk.WORD,
                      relief="flat", bd=0, state="disabled", cursor="arrow",
                      width=50, height=1, highlightthickness=0,
                      padx=14, pady=10, spacing1=2, spacing2=3, spacing3=2)
        txt.pack(fill=tk.X)
        self._apply_txt_tags(txt)

        return txt, copy_lbl

    def _apply_txt_tags(self, txt):
        C = self.C
        txt.tag_config("normal", font=("Segoe UI", 14),         foreground=C["TEXT"])
        txt.tag_config("bold",   font=("Segoe UI", 14, "bold"), foreground=C["TEXT"])
        txt.tag_config("h1",     font=("Segoe UI", 18, "bold"), foreground=C["ACCENT"])
        txt.tag_config("h2",     font=("Segoe UI", 16, "bold"), foreground=C["ACCENT"])
        txt.tag_config("h3",     font=("Segoe UI", 14, "bold"), foreground=C["ACCENT2"])
        txt.tag_config("bullet", font=("Segoe UI", 14),         foreground=C["TEXT"])
        txt.tag_config("code",   font=("Courier New", 12),
                       foreground=C["GREEN"], background=C["BG4"],
                       lmargin1=8, lmargin2=8, rmargin=8)

    def _update_ai_text(self, txt, full):
        txt.config(state="normal")
        txt.delete("1.0", tk.END)
        for seg, tag in parse_md(full):
            txt.insert(tk.END, seg, tag)
        lines = int(txt.index(tk.END).split(".")[0])
        txt.config(height=max(1, lines - 1), state="disabled")
        self._scroll_end()

    def _thinking_dots(self, txt):
        frames = ["กำลังคิด ●○○", "กำลังคิด ●●○", "กำลังคิด ●●●", "กำลังคิด ○●●"]
        idx = [0]
        def _tick():
            if self.is_thinking:
                txt.config(state="normal")
                txt.delete("1.0", tk.END)
                txt.insert(tk.END, frames[idx[0] % 4], "normal")
                txt.config(state="disabled")
                idx[0] += 1
                self.dot_job = self.root.after(360, _tick)
        _tick()

    # ── STATUS ───────────────────────────
    def _pulse_dot(self, color, phase=0):
        alt = self.C["BG2"]
        self._dot_canvas.itemconfig(self._dot_id, fill=color if phase % 2 == 0 else alt)
        self._pulse_job = self.root.after(600, lambda: self._pulse_dot(color, phase + 1))

    def _stop_pulse(self, final_color):
        if self._pulse_job:
            self.root.after_cancel(self._pulse_job)
            self._pulse_job = None
        self._dot_canvas.itemconfig(self._dot_id, fill=final_color)

    def set_status(self, text, color=None):
        col = color or self.C["GREEN"]
        self._status_label.configure(text=text, fg=col)
        self._stop_pulse(col)
        if col in (self.C["GOLD"], self.C["ACCENT"]):
            self._pulse_dot(col)

    def _status_line(self, text):
        lbl = tk.Label(self._msg_frame, text=text, bg=self.C["BG"],
                       fg=self.C["TEXT2"], font=("Segoe UI", 10, "italic"))
        lbl.pack(pady=6)
        self.chat_widgets.append(lbl)
        self._scroll_end()

    def _day_separator(self):
        row = tk.Frame(self._msg_frame, bg=self.C["BG"])
        row.pack(fill=tk.X, padx=20, pady=8)
        self.chat_widgets.append(row)
        tk.Frame(row, bg=self.C["DIVIDER"], height=1).pack(
            fill=tk.X, side=tk.LEFT, expand=True, pady=7)
        tk.Label(row, text=datetime.now().strftime("%d %b %Y"),
                 bg=self.C["BG"], fg=self.C["TEXT2"],
                 font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=10)
        tk.Frame(row, bg=self.C["DIVIDER"], height=1).pack(
            fill=tk.X, side=tk.LEFT, expand=True, pady=7)

    # ── SCROLL ───────────────────────────
    def _scroll_end(self):
        self.root.update_idletasks()
        self._canvas.yview_moveto(1.0)

    # ── LOAD HISTORY ─────────────────────
    def _load_history_to_ui(self):
        self._day_separator()
        if not self.history:
            self._status_line("✦  สวัสดี! ผมคือ LouisAI — พิมพ์หรือพูดเพื่อเริ่มต้นได้เลยครับ")
            return
        self._status_line(f"── โหลดประวัติ {len(self.history)} ข้อความ ──")
        for h in self.history[-20:]:
            if h["role"] == "user":
                self._bubble_user(h["content"])
            elif h["role"] == "assistant":
                txt, cb = self._bubble_ai_start()
                self._update_ai_text(txt, h["content"])
                self._setup_copy(cb, h["content"])

    def _setup_copy(self, lbl, text):
        lbl.pack(side=tk.LEFT, padx=8)
        lbl.bind("<Button-1>", lambda e: (
            self.root.clipboard_clear(),
            self.root.clipboard_append(text)
        ))
        lbl.bind("<Enter>", lambda e: lbl.configure(fg=self.C["ACCENT"]))
        lbl.bind("<Leave>", lambda e: lbl.configure(fg=self.C["TEXT2"]))

    # ── SEND ─────────────────────────────
    def _on_enter(self, e):
        if not (e.state & 0x1):
            self.on_send()
            return "break"

    def on_send(self):
        if self.is_thinking:
            return
        raw = self._entry.get("1.0", tk.END).strip()
        if not raw or raw == self._ph_txt:
            return
        self._entry.delete("1.0", tk.END)
        self._ph = False
        self._hint.configure(text="")
        stop_speech()
        self._process(raw)

    def _process(self, text):
        self.history = add_message(self.history, "user", text)
        self._bubble_user(text)
        self.is_thinking = True
        self.cancel_flag = [False]
        self._btn_cancel.configure(state="normal")
        self.set_status("กำลังคิด...", self.C["GOLD"])

        txt, copy_btn = self._bubble_ai_start()
        self._thinking_dots(txt)
        buf = [""]

        def on_token(t):
            if not buf[0] and not t.strip():
                return
            if not buf[0] and self.dot_job:
                self.root.after_cancel(self.dot_job)
                self.dot_job = None
            buf[0] += t
            self.root.after(0, lambda: self._update_ai_text(txt, buf[0]))

        def on_done(full, cancelled=False):
            if self.dot_job:
                self.root.after_cancel(self.dot_job)
            self.is_thinking = False
            self.root.after(0, lambda: self._btn_cancel.configure(state="disabled"))
            if full:
                self.history = add_message(self.history, "assistant", full)
                self.root.after(0, lambda: self._setup_copy(copy_btn, full))
                self.root.after(0, lambda: self.set_status("พร้อม", self.C["GREEN"]))
                if not cancelled:
                    speak(full)
            else:
                self.root.after(0, lambda: self.set_status("พร้อม", self.C["GREEN"]))

        msgs = to_ollama_messages(self.history[:-1], SYSTEM_PROMPT)
        msgs.append({"role": "user", "content": text})
        ask_stream(msgs, on_token, on_done, self.cancel_flag)

    # ── CANCEL ───────────────────────────
    def cancel_response(self):
        self.cancel_flag[0] = True
        stop_speech()
        self.set_status("หยุดแล้ว", self.C["RED"])

    # ── MIC ──────────────────────────────
    def on_talk(self):
        if self.is_thinking:
            return
        stop_speech()
        self._btn_mic.configure(fg=self.C["GREEN"])
        self.set_status("ฟังอยู่...", self.C["ACCENT"])
        threading.Thread(target=self._do_listen, daemon=True).start()

    def _do_listen(self):
        text = listen_once()
        self.root.after(0, lambda: self._btn_mic.configure(fg=self.C["TEXT2"]))
        if text:
            self.root.after(0, lambda: self._process(text))
        else:
            self.root.after(0, lambda: self.set_status("ไม่ได้ยิน ลองใหม่", self.C["RED"]))
            self.root.after(2500, lambda: self.set_status("พร้อม", self.C["GREEN"]))

    # ── REALTIME ─────────────────────────
    def toggle_rt(self):
        self.realtime_on = not self.realtime_on
        if self.realtime_on:
            self._btn_rt.configure(text="⏺  Realtime ON", text_color=self.C["GREEN"])
            self._status_line("🟢 Realtime Mode เปิดแล้ว — พูดได้เลย")
            threading.Thread(target=self._rt_loop, daemon=True).start()
        else:
            self._btn_rt.configure(text="⏺  Realtime", text_color=self.C["TEXT2"])
            self._status_line("⚫ Realtime Mode ปิดแล้ว")

    def _rt_loop(self):
        while self.realtime_on:
            if not self.is_thinking and not is_speaking():
                text = listen_once()
                if text and self.realtime_on and not self.is_thinking and not is_speaking():
                    self.root.after(0, lambda t=text: self._process(t))
                    time.sleep(1.5)
            else:
                time.sleep(0.5)

    # ── CLEAR CHAT ───────────────────────
    def clear_chat(self):
        from tkinter import messagebox
        if messagebox.askyesno("ล้างแชท", "ต้องการล้างประวัติการสนทนาทั้งหมดไหม?"):
            self.history = clear_history()
            self._rebuild_chat()


if __name__ == "__main__":
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    app = LouisAI(root)
    root.mainloop()
