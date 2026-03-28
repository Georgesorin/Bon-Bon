"""
Eclipse_Displays.py — The Eclipse – Lobby + HUD Windows
Auto-launched by The_Eclipse.py

Two fullscreen windows:
  1. LobbyWindow  — outside the room (touch screen, player select, START)
  2. HUDWindow    — inside the room   (live round info, lives, timer)
"""

import json
import socket
import threading
import time
import math
import tkinter as tk

# ─── Ports ────────────────────────────────────────────────────────────────────
TELEMETRY_PORT = 6666
CMD_PORT       = 6667

# ─── Dark horror palette ─────────────────────────────────────────────────────
BG      = "#06060f"
CARD    = "#0e0e22"
CARD2   = "#14142e"
LINE    = "#1e1e40"
ACCENT  = "#b44dff"   # purple accent (matches game's purple eye)
OK      = "#00e676"
WARN    = "#ffd740"
DANGER  = "#ff1744"
PURPLE  = "#b44dff"
BLUE    = "#448aff"
CYAN    = "#00e5ff"
ORANGE  = "#ff6d00"
WHITE   = "#e8e8ff"
GRAY    = "#5c5c80"
DIM     = "#1e1e3a"
GOLD    = "#ffd700"
DARK_RED = "#440000"

def ff(size, bold=False):
    return ("Consolas", size, "bold" if bold else "normal")


def hbar(parent, color=LINE, height=2, padx=20, pady=8):
    tk.Frame(parent, bg=color, height=height).pack(fill=tk.X, padx=padx, pady=pady)


def big_btn(parent, text, cmd, bg=OK, fg=BG, size=14, pady=12, padx=24):
    return tk.Button(parent, text=text, command=cmd,
                     fg=fg, bg=bg, activeforeground=fg, activebackground=bg,
                     font=ff(size, bold=True), relief="flat",
                     padx=padx, pady=pady, cursor="hand2",
                     bd=0, highlightthickness=0)


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry Receiver
# ─────────────────────────────────────────────────────────────────────────────
class TelemetryReceiver:
    _DEFAULT = {
        "state": "LOBBY", "round_state": "",
        "round": 1, "total_rounds": 4,
        "lives": 3, "player_count": 2,
        "countdown": 0, "current_eye": 0,
        "distraction_wall": 0, "score": 0,
    }

    def __init__(self):
        self._state = dict(self._DEFAULT)
        self._lock  = threading.Lock()
        self._sock  = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.settimeout(0.4)
        try:
            self._sock.bind(("0.0.0.0", TELEMETRY_PORT))
        except Exception as e:
            print(f"[Disp] Telemetry bind error: {e}")
        threading.Thread(target=self._loop, daemon=True).start()

    def get(self):
        with self._lock:
            return dict(self._state)

    def _loop(self):
        while True:
            try:
                data, _ = self._sock.recvfrom(512)
                with self._lock:
                    self._state.update(json.loads(data.decode()))
            except Exception:
                pass


# ─────────────────────────────────────────────────────────────────────────────
# Command Sender
# ─────────────────────────────────────────────────────────────────────────────
class CommandSender:
    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _send(self, d):
        try:
            self._sock.sendto(json.dumps(d).encode(), ("127.0.0.1", CMD_PORT))
        except Exception:
            pass

    def start(self, n):   self._send({"cmd": "start", "players": n})
    def restart(self):    self._send({"cmd": "restart"})
    def quit(self):       self._send({"cmd": "quit"})


# ─────────────────────────────────────────────────────────────────────────────
# LOBBY WINDOW  (outside the room — touch screen)
# ─────────────────────────────────────────────────────────────────────────────
class LobbyWindow(tk.Toplevel):

    def __init__(self, root, tel, cmd):
        super().__init__(root)
        self.tel, self.cmd = tel, cmd
        self._root = root
        self.title("THE ECLIPSE — LOBBY")
        self.configure(bg=BG)
        self.geometry("+0+0")
        self.attributes("-fullscreen", True)
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))
        self.bind("<F11>", lambda e: self.attributes("-fullscreen",
                  not self.attributes("-fullscreen")))
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._quit_app)

        self._pc    = tk.IntVar(value=2)
        self._pulse = 0
        self._frames = {}
        self._build_all()

        # Quit button
        tk.Button(self, text="✕  QUIT", command=self._quit_app,
                  fg=WHITE, bg=DARK_RED, activeforeground=WHITE,
                  activebackground=DANGER, font=ff(9, bold=True),
                  relief="flat", padx=10, pady=4, cursor="hand2", bd=0
                  ).place(relx=1.0, rely=0, anchor="ne", x=-6, y=6)

        self._tick()

    def _quit_app(self):
        try:   self.cmd.quit()
        except Exception: pass
        try:   self._root.destroy()
        except Exception: pass

    def _build_all(self):
        for name in ("LOBBY", "COUNTDOWN", "PLAYING", "ENDGAME"):
            f = tk.Frame(self, bg=BG)
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._frames[name] = f
        self._build_lobby()
        self._build_countdown()
        self._build_playing()
        self._build_endgame()
        self._show("LOBBY")

    # ── Lobby Screen ──────────────────────────────────────────────────────────
    def _build_lobby(self):
        f = self._frames["LOBBY"]

        # Header
        hdr = tk.Frame(f, bg=CARD, pady=16)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="👁  THE ECLIPSE  👁",
                 fg=ACCENT, bg=CARD, font=ff(28, bold=True)).pack()
        tk.Label(hdr, text="Cooperative Stealth Exorcism",
                 fg=GRAY, bg=CARD, font=ff(11)).pack(pady=(4, 0))

        # Player count
        pc_card = tk.Frame(f, bg=CARD, pady=20)
        pc_card.pack(fill=tk.X, padx=30, pady=(24, 0))
        tk.Label(pc_card, text="HOW MANY EXORCISTS?",
                 fg=GRAY, bg=CARD, font=ff(12, bold=True)).pack()

        row = tk.Frame(pc_card, bg=CARD)
        row.pack(pady=12)
        tk.Button(row, text=" − ", command=lambda: self._adj(-1),
                  fg=BG, bg=ORANGE, font=ff(22, bold=True),
                  relief="flat", padx=12, pady=4, cursor="hand2",
                  bd=0).pack(side=tk.LEFT, padx=14)
        self._pc_lbl = tk.Label(row, textvariable=self._pc,
                                fg=WHITE, bg=CARD2,
                                font=ff(60, bold=True), width=3)
        self._pc_lbl.pack(side=tk.LEFT)
        tk.Button(row, text=" + ", command=lambda: self._adj(+1),
                  fg=BG, bg=OK, font=ff(22, bold=True),
                  relief="flat", padx=12, pady=4, cursor="hand2",
                  bd=0).pack(side=tk.LEFT, padx=14)
        tk.Label(pc_card, text="min 2  ·  max 4",
                 fg=GRAY, bg=CARD, font=ff(10)).pack()

        # Start button
        btn_wrap = tk.Frame(f, bg=BG)
        btn_wrap.pack(pady=24)
        big_btn(btn_wrap, "👁  BEGIN THE ECLIPSE",
                lambda: self.cmd.start(self._pc.get()),
                bg=ACCENT, fg=WHITE, size=18, pady=16, padx=50).pack()

        hbar(f, color=LINE)

        # Rules
        rules = tk.Frame(f, bg=CARD, pady=14)
        rules.pack(fill=tk.X, padx=30)
        tk.Label(rules, text="HOW TO PLAY",
                 fg=GRAY, bg=CARD, font=ff(11, bold=True)).pack(pady=(0, 8))

        steps = [
            ("👁", "The Demonic Eye awakens on a wall",     PURPLE),
            ("🫣", "HIDE behind the pillar within 5s",     WARN),
            ("🏃", "One BAIT runs & holds 2 buttons nearby", ORANGE),
            ("💀", "Eye goes BLIND — others sprint to seal!",CYAN),
            ("✅", "Press the pattern to break the seal!",  OK),
        ]
        for emoji, desc, col in steps:
            row = tk.Frame(rules, bg=CARD)
            row.pack(anchor="w", padx=20, pady=3)
            tk.Label(row, text=emoji, bg=CARD, font=ff(16)).pack(side=tk.LEFT, padx=4)
            tk.Label(row, text=desc, fg=col, bg=CARD,
                     font=ff(11)).pack(side=tk.LEFT, padx=6)

        hbar(rules, color=LINE, padx=0, pady=(10, 2))
        tk.Label(rules, text="🎯 Break all 4 seals to win  ·  3 lives",
                 fg=GRAY, bg=CARD, font=ff(10)).pack(pady=4)

    # ── Countdown / Hide Screen ───────────────────────────────────────────────
    def _build_countdown(self):
        f = self._frames["COUNTDOWN"]
        tk.Frame(f, bg=ACCENT, height=6).pack(fill=tk.X)
        tk.Label(f, text="ECLIPSE ACTIVE", fg=ACCENT, bg=BG,
                 font=ff(28, bold=True)).pack(pady=(50, 10))

        self._cd_state_lbl = tk.Label(f, text="", fg=WHITE, bg=BG,
                                       font=ff(16))
        self._cd_state_lbl.pack()

        hbar(f, pady=20)

        self._cd_countdown_lbl = tk.Label(f, text="", fg=WARN, bg=BG,
                                           font=ff(100, bold=True))
        self._cd_countdown_lbl.pack(pady=4)

        self._cd_sub = tk.Label(f, text="", fg=GRAY, bg=BG, font=ff(14))
        self._cd_sub.pack(pady=16)

    # ── Playing Screen ────────────────────────────────────────────────────────
    def _build_playing(self):
        f = self._frames["PLAYING"]
        tk.Frame(f, bg=ACCENT, height=6).pack(fill=tk.X)
        tk.Label(f, text="👁", fg=ACCENT, bg=BG, font=ff(80)).pack(pady=(80, 10))
        tk.Label(f, text="ECLIPSE IN PROGRESS", fg=ACCENT, bg=BG,
                 font=ff(22, bold=True)).pack()

        self._play_round_lbl = tk.Label(f, text="", fg=WHITE, bg=BG,
                                         font=ff(16, bold=True))
        self._play_round_lbl.pack(pady=20)

        self._play_state_lbl = tk.Label(f, text="", fg=CYAN, bg=BG,
                                         font=ff(14))
        self._play_state_lbl.pack(pady=10)

    # ── Endgame Screen ────────────────────────────────────────────────────────
    def _build_endgame(self):
        f = self._frames["ENDGAME"]
        tk.Frame(f, bg=CARD, height=6).pack(fill=tk.X)

        self._end_icon  = tk.Label(f, text="", bg=BG, font=ff(90))
        self._end_icon.pack(pady=(70, 8))
        self._end_title = tk.Label(f, text="", bg=BG, font=ff(36, bold=True))
        self._end_title.pack()
        self._end_sub   = tk.Label(f, text="", fg=WHITE, bg=BG,
                                    font=ff(15), wraplength=620)
        self._end_sub.pack(pady=14)

        hbar(f)
        self._end_stats = tk.Label(f, text="", fg=GRAY, bg=BG, font=ff(13))
        self._end_stats.pack(pady=10)

        big_btn(f, "🔄   PLAY AGAIN", self.cmd.restart,
                bg=ACCENT, fg=WHITE, size=15, pady=12, padx=36).pack(pady=20)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _adj(self, d):
        self._pc.set(max(2, min(4, self._pc.get() + d)))

    def _show(self, name):
        for n, f in self._frames.items():
            f.lift() if n == name else f.lower()

    # ── Tick ──────────────────────────────────────────────────────────────────
    def _tick(self):
        s  = self.tel.get()
        st = s["state"]
        rs = s.get("round_state", "")
        self._pulse = (self._pulse + 1) % 8

        if st == "LOBBY":
            self._show("LOBBY")

        elif st == "PLAYING":
            if rs in ("ROUND_INTRO", "HIDE_PHASE"):
                self._show("COUNTDOWN")
                cd = s["countdown"]
                if rs == "ROUND_INTRO":
                    self._cd_state_lbl.config(
                        text=f"Round {s['round']}/{s['total_rounds']} — "
                             f"Eye on WALL {s['current_eye']}",
                        fg=PURPLE)
                    self._cd_countdown_lbl.config(text="👁", fg=PURPLE)
                    self._cd_sub.config(text="Preparing the ritual...")
                else:
                    col = OK if cd > 3 else WARN if cd > 1 else DANGER
                    self._cd_state_lbl.config(text="HIDE BEHIND THE PILLAR!", fg=WARN)
                    self._cd_countdown_lbl.config(
                        text=str(int(cd) + 1) if cd > 0 else "GO", fg=col)
                    self._cd_sub.config(
                        text=f"Round {s['round']} · Wall {s['current_eye']} · "
                             f"{s['player_count']} exorcists")
            else:
                self._show("PLAYING")
                self._play_round_lbl.config(
                    text=f"Round {s['round']} / {s['total_rounds']}  ·  "
                         f"Lives: {'♥ ' * s['lives']}{'♡ ' * (3 - s['lives'])}")

                state_texts = {
                    "WAITING_BAIT":  ("The Eye watches... send the BAIT!", PURPLE),
                    "BAIT_RUN":      (f"BAIT detected! Press distraction! "
                                      f"({s['countdown']:.0f}s)", DANGER),
                    "EYE_BLIND":     (f"EYE IS BLIND — GO! ({s['countdown']:.0f}s)", CYAN),
                    "ROUND_SUCCESS": ("✅ SEAL BROKEN!", OK),
                    "ROUND_FAIL":    ("❌ FAILED! Lost a life...", DANGER),
                }
                text, col = state_texts.get(rs, ("...", GRAY))
                self._play_state_lbl.config(text=text, fg=col)

        elif st in ("WIN", "GAMEOVER"):
            self._show("ENDGAME")
            is_win = st == "WIN"
            if is_win:
                self._end_icon.config(text="🏆", fg=OK)
                self._end_title.config(text="ECLIPSE SURVIVED!", fg=OK)
                self._end_sub.config(
                    text="All 4 seals broken. The demonic eyes are silenced!")
            else:
                bcol = DANGER if self._pulse < 4 else DARK_RED
                self._end_icon.config(text="💀", fg=bcol)
                self._end_title.config(text="POSSESSED", fg=bcol)
                self._end_sub.config(
                    text=f"The eyes consumed you at seal "
                         f"{s['round']} of {s['total_rounds']}.")
            self._end_stats.config(
                text=f"Seals broken: {s['score']}/{s['total_rounds']}  ·  "
                     f"Players: {s['player_count']}  ·  "
                     f"Lives remaining: {s['lives']}")

        self.after(250, self._tick)


# ─────────────────────────────────────────────────────────────────────────────
# HUD WINDOW  (inside the room — live info)
# ─────────────────────────────────────────────────────────────────────────────
class HUDWindow(tk.Toplevel):

    def __init__(self, root, tel, cmd):
        super().__init__(root)
        self.tel, self.cmd = tel, cmd
        self._root = root
        self.title("THE ECLIPSE — HUD")
        self.configure(bg=BG)
        sw = root.winfo_screenwidth()
        self.geometry(f"+{sw}+0")
        self.attributes("-fullscreen", True)
        self.bind("<Escape>", lambda e: self.attributes("-fullscreen", False))
        self.bind("<F11>", lambda e: self.attributes("-fullscreen",
                  not self.attributes("-fullscreen")))
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._quit_app)

        self._blink  = False
        self._frames = {}
        self._build_all()

        tk.Button(self, text="✕  QUIT", command=self._quit_app,
                  fg=WHITE, bg=DARK_RED, activeforeground=WHITE,
                  activebackground=DANGER, font=ff(9, bold=True),
                  relief="flat", padx=10, pady=4, cursor="hand2", bd=0
                  ).place(relx=1.0, rely=0, anchor="ne", x=-6, y=6)

        self._tick()

    def _quit_app(self):
        try:   self.cmd.quit()
        except Exception: pass
        try:   self._root.destroy()
        except Exception: pass

    def _build_all(self):
        for name in ("WAIT", "PLAYING", "ENDGAME"):
            f = tk.Frame(self, bg=BG)
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._frames[name] = f
        self._build_wait()
        self._build_playing()
        self._build_endgame()
        self._show("WAIT")

    # ── Wait Screen ───────────────────────────────────────────────────────────
    def _build_wait(self):
        f = self._frames["WAIT"]
        tk.Frame(f, bg=CARD, height=6).pack(fill=tk.X)
        tk.Label(f, text="👁  ECLIPSE HUD  👁",
                 fg=ACCENT, bg=BG, font=ff(16, bold=True)).pack(pady=(40, 0))
        hbar(f)
        tk.Label(f, text="⏳", fg=GRAY, bg=BG, font=ff(60)).pack(pady=(60, 20))
        self._wait_lbl = tk.Label(f, text="Waiting in lobby...",
                                   fg=GRAY, bg=BG, font=ff(15))
        self._wait_lbl.pack()

    # ── Playing HUD ──────────────────────────────────────────────────────────
    def _build_playing(self):
        f = self._frames["PLAYING"]
        tk.Frame(f, bg=ACCENT, height=6).pack(fill=tk.X)

        # Top bar
        top = tk.Frame(f, bg=CARD, pady=10)
        top.pack(fill=tk.X)
        tk.Label(top, text="👁  ECLIPSE HUD  👁",
                 fg=ACCENT, bg=CARD, font=ff(15, bold=True)).pack()

        # Round + Eye info
        row1 = tk.Frame(f, bg=BG)
        row1.pack(fill=tk.X, padx=24, pady=16)

        rf = tk.Frame(row1, bg=CARD, padx=16, pady=12)
        rf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        tk.Label(rf, text="SEAL", fg=GRAY, bg=CARD, font=ff(10, bold=True)).pack()
        self._h_round_lbl = tk.Label(rf, text="1 / 4", fg=WHITE, bg=CARD,
                                      font=ff(36, bold=True))
        self._h_round_lbl.pack()

        ef = tk.Frame(row1, bg=CARD, padx=16, pady=12)
        ef.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        tk.Label(ef, text="EYE WALL", fg=GRAY, bg=CARD,
                 font=ff(10, bold=True)).pack()
        self._h_eye_lbl = tk.Label(ef, text="—", fg=PURPLE, bg=CARD,
                                    font=ff(36, bold=True))
        self._h_eye_lbl.pack()

        hbar(f, color=LINE)

        # Current phase
        self._h_phase_lbl = tk.Label(f, text="", fg=CYAN, bg=BG,
                                      font=ff(20, bold=True))
        self._h_phase_lbl.pack(pady=8)

        # Big countdown
        self._h_countdown = tk.Label(f, text="", fg=WARN, bg=BG,
                                      font=ff(120, bold=True))
        self._h_countdown.pack(pady=4)

        hbar(f, color=LINE)

        # Lives row
        lives_wrap = tk.Frame(f, bg=CARD, pady=14)
        lives_wrap.pack(fill=tk.X, padx=24)
        tk.Label(lives_wrap, text="LIVES", fg=GRAY, bg=CARD,
                 font=ff(11, bold=True)).pack()
        self._h_hearts_row = tk.Frame(lives_wrap, bg=CARD)
        self._h_hearts_row.pack(pady=8)
        self._h_hearts = []
        for _ in range(3):
            l = tk.Label(self._h_hearts_row, text="♥", fg=DANGER, bg=CARD,
                         font=ff(50, bold=True))
            l.pack(side=tk.LEFT, padx=12)
            self._h_hearts.append(l)

        hbar(f, color=LINE)

        # Score
        score_wrap = tk.Frame(f, bg=CARD, pady=10)
        score_wrap.pack(fill=tk.X, padx=24)
        tk.Label(score_wrap, text="SEALS BROKEN", fg=GRAY, bg=CARD,
                 font=ff(10, bold=True)).pack()
        self._h_score_lbl = tk.Label(score_wrap, text="0", fg=OK, bg=CARD,
                                      font=ff(36, bold=True))
        self._h_score_lbl.pack()

    # ── Endgame HUD ──────────────────────────────────────────────────────────
    def _build_endgame(self):
        f = self._frames["ENDGAME"]
        self._he_top = tk.Frame(f, bg=CARD, height=6)
        self._he_top.pack(fill=tk.X)

        self._he_icon = tk.Label(f, text="", bg=BG, font=ff(80))
        self._he_icon.pack(pady=(60, 10))
        self._he_title = tk.Label(f, text="", bg=BG, font=ff(32, bold=True))
        self._he_title.pack()
        self._he_sub = tk.Label(f, text="", fg=WHITE, bg=BG,
                                 font=ff(14), wraplength=540)
        self._he_sub.pack(pady=12)

        hbar(f)
        self._he_stats = tk.Label(f, text="", fg=GRAY, bg=BG, font=ff(13))
        self._he_stats.pack(pady=8)

        hbar(f)
        btn_row = tk.Frame(f, bg=BG)
        btn_row.pack(pady=16)
        big_btn(btn_row, "🔄   PLAY AGAIN", self.cmd.restart,
                bg=ACCENT, fg=WHITE, size=15, pady=12, padx=28
                ).pack(side=tk.LEFT, padx=8)
        big_btn(btn_row, "✕   QUIT", self._quit_app,
                bg=DARK_RED, fg=WHITE, size=15, pady=12, padx=28
                ).pack(side=tk.LEFT, padx=8)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _show(self, name):
        for n, f in self._frames.items():
            f.lift() if n == name else f.lower()

    # ── Tick ──────────────────────────────────────────────────────────────────
    def _tick(self):
        s  = self.tel.get()
        st = s["state"]
        rs = s.get("round_state", "")
        self._blink = not self._blink

        if st == "LOBBY":
            self._show("WAIT")
            self._wait_lbl.config(text="Waiting in lobby…", fg=GRAY)

        elif st == "PLAYING":
            self._show("PLAYING")

            # Round info
            self._h_round_lbl.config(text=f"{s['round']} / {s['total_rounds']}")
            self._h_eye_lbl.config(text=f"WALL {s['current_eye']}" if
                                   s['current_eye'] else "—")

            # Phase label
            phase_map = {
                "ROUND_INTRO":   ("⏳ Preparing...",           PURPLE),
                "HIDE_PHASE":    ("🫣 HIDE NOW!",              WARN),
                "WAITING_BAIT":  ("👁 Eye watches... send bait", PURPLE),
                "BAIT_RUN":      ("🏃 BAIT — PRESS DISTRACTION!", DANGER),
                "EYE_BLIND":     ("💀 EYE IS BLIND — GO!!!",   CYAN),
                "ROUND_SUCCESS": ("✅ SEAL BROKEN!",           OK),
                "ROUND_FAIL":    ("❌ FAILED!",                DANGER),
            }
            text, col = phase_map.get(rs, ("...", GRAY))
            self._h_phase_lbl.config(text=text, fg=col)

            # Countdown
            cd = s.get("countdown", 0)
            if rs in ("HIDE_PHASE", "BAIT_RUN", "EYE_BLIND"):
                cd_col = OK if cd > 3 else WARN if cd > 1.5 else DANGER
                self._h_countdown.config(
                    text=f"{cd:.0f}" if cd > 0 else "⚡", fg=cd_col)
            elif rs == "ROUND_SUCCESS":
                self._h_countdown.config(text="✅", fg=OK)
            elif rs == "ROUND_FAIL":
                self._h_countdown.config(
                    text="❌" if self._blink else "", fg=DANGER)
            else:
                self._h_countdown.config(text="👁", fg=PURPLE)

            # Lives
            lives = s.get("lives", 3)
            for i, h in enumerate(self._h_hearts):
                if i < lives:
                    alive_col = DANGER
                    if lives == 1 and self._blink:
                        alive_col = WARN
                    h.config(text="♥", fg=alive_col)
                else:
                    h.config(text="♡", fg=DIM)

            # Score
            self._h_score_lbl.config(text=str(s.get("score", 0)))

        elif st in ("WIN", "GAMEOVER"):
            self._show("ENDGAME")
            is_win = st == "WIN"
            if is_win:
                self._he_top.config(bg=OK)
                self._he_icon.config(text="🏆", fg=OK)
                self._he_title.config(text="ECLIPSE SURVIVED!", fg=OK)
                self._he_sub.config(
                    text="All 4 seals broken! The eyes are silenced.")
            else:
                bcol = DANGER if self._blink else DARK_RED
                self._he_top.config(bg=bcol)
                self._he_icon.config(text="💀", fg=bcol)
                self._he_title.config(text="POSSESSED", fg=bcol)
                self._he_sub.config(
                    text=f"Consumed at seal {s['round']} of {s['total_rounds']}.",
                    fg=DANGER)
            self._he_stats.config(
                text=f"Seals: {s.get('score', 0)}/{s['total_rounds']}  ·  "
                     f"Players: {s['player_count']}  ·  "
                     f"Lives: {s.get('lives', 0)}/3")

        self.after(250, self._tick)


# ─────────────────────────────────────────────────────────────────────────────
# Main
# ─────────────────────────────────────────────────────────────────────────────
def main():
    root = tk.Tk()
    root.withdraw()

    tel   = TelemetryReceiver()
    cmd   = CommandSender()
    lobby = LobbyWindow(root, tel, cmd)
    hud   = HUDWindow(root, tel, cmd)

    print("[DISPLAYS] Eclipse Lobby + HUD running. Close via UI or Ctrl+C.")
    root.mainloop()


if __name__ == "__main__":
    main()
