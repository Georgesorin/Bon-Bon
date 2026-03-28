"""
KD_Displays.py  –  Kernel Defender – External Display Windows
Auto-launched by Kernel_Defender.py
"""

import json
import socket
import threading
import tkinter as tk

# ─── Ports ────────────────────────────────────────────────────────────────────
TELEMETRY_PORT = 6668
CMD_PORT       = 6669

# ─── Dark space palette ───────────────────────────────────────────────────────
BG      = "#06060f"   # near-black navy
CARD    = "#0e0e22"   # card / panel background
CARD2   = "#14142e"   # deeper card
LINE    = "#1e1e40"   # separator
ACCENT  = "#00e5ff"   # electric cyan
OK      = "#00e676"   # bright green
WARN    = "#ffd740"   # amber
DANGER  = "#ff1744"   # vivid red
PURPLE  = "#e040fb"   # magenta-purple
ORANGE  = "#ff6d00"   # deep orange
WHITE   = "#e8e8ff"   # off-white
GRAY    = "#5c5c80"   # muted text
DIM     = "#1e1e3a"   # very dim (empty symbol / separator)

F_MAIN  = ("Consolas", 12)   # base font tuple builder


def ff(size, bold=False):
    return ("Consolas", size, "bold" if bold else "normal")


# ─────────────────────────────────────────────────────────────────────────────
# Telemetry receiver (shared)
# ─────────────────────────────────────────────────────────────────────────────
class TelemetryReceiver:
    _DEFAULT = {
        "state": "LOBBY", "wave": 1, "total_waves": 3,
        "core_lives": 3, "quakes_left": 3,
        "enemy_count": 0, "enemies_spawned": 0, "enemies_total": 6,
        "player_count": 4, "countdown_remaining": 0,
        "elapsed_seconds": 0, "fail_wave": 0,
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
            print(f"[Disp] telemetry bind error: {e}")
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
# Command sender (lobby → game)
# ─────────────────────────────────────────────────────────────────────────────
class CommandSender:
    def __init__(self):
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def _send(self, d):
        try:
            self._sock.sendto(json.dumps(d).encode(), ("127.0.0.1", CMD_PORT))
        except Exception:
            pass

    def start(self, n):   self._send({"cmd": "start",   "players": n})
    def restart(self):    self._send({"cmd": "restart"})
    def quit(self):       self._send({"cmd": "quit"})


# ─────────────────────────────────────────────────────────────────────────────
# Shared drawing helpers
# ─────────────────────────────────────────────────────────────────────────────
def fmt_time(s):
    m, s = divmod(int(s), 60)
    return f"{m:02d}:{s:02d}"


def card(parent, **kw):
    """A rounded-feeling card panel."""
    f = tk.Frame(parent, bg=kw.pop("bg", CARD), **kw)
    return f


def hbar(parent, color=LINE, height=2, padx=20, pady=8):
    tk.Frame(parent, bg=color, height=height).pack(fill=tk.X, padx=padx, pady=pady)


def title_label(parent, text, size=22, color=ACCENT):
    tk.Label(parent, text=text, fg=color, bg=BG,
             font=ff(size, bold=True)).pack(pady=(22, 2))


def sub_label(parent, text, size=11, color=GRAY):
    tk.Label(parent, text=text, fg=color, bg=BG,
             font=ff(size)).pack(pady=(0, 4))


def big_btn(parent, text, cmd, bg=OK, fg=BG, size=14, pady=12, padx=24):
    b = tk.Button(parent, text=text, command=cmd,
                  fg=fg, bg=bg, activeforeground=fg, activebackground=bg,
                  font=ff(size, bold=True), relief="flat",
                  padx=padx, pady=pady, cursor="hand2",
                  bd=0, highlightthickness=0)
    return b


def stat_card(parent, label_text, label_color=GRAY):
    """Returns (outer_frame, value_label) for a stat card."""
    f = tk.Frame(parent, bg=CARD, padx=14, pady=10)
    tk.Label(f, text=label_text, fg=label_color, bg=CARD,
             font=ff(9, bold=True)).pack()
    val = tk.Label(f, text="—", fg=WHITE, bg=CARD, font=ff(28, bold=True))
    val.pack()
    return f, val


# ─────────────────────────────────────────────────────────────────────────────
# LOBBY WINDOW  (outside the room)
# ─────────────────────────────────────────────────────────────────────────────
class LobbyWindow(tk.Toplevel):

    def __init__(self, root, tel: TelemetryReceiver, cmd: CommandSender):
        super().__init__(root)
        self.tel, self.cmd = tel, cmd
        self._root = root
        self.title("KERNEL DEFENDER – LOBBY")
        self.configure(bg=BG)
        self.geometry("700x740+0+0")
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._quit_app)
        self._pc    = tk.IntVar(value=4)
        self._pulse = 0
        self._frames = {}
        self._build_all()
        # Floating quit button — always on top, top-right corner
        self._quit_btn = tk.Button(
            self, text="✕  QUIT", command=self._quit_app,
            fg=WHITE, bg="#330000",
            activeforeground=WHITE, activebackground=DANGER,
            font=ff(9, bold=True), relief="flat",
            padx=10, pady=4, cursor="hand2", bd=0
        )
        self._quit_btn.place(relx=1.0, rely=0, anchor="ne", x=-6, y=6)
        self._tick()

    def _quit_app(self):
        try:
            self.cmd.quit()
        except Exception:
            pass
        try:
            self._root.destroy()
        except Exception:
            pass

    # ── Build all screens ─────────────────────────────────────────────────────
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

    def _build_lobby(self):
        f = self._frames["LOBBY"]

        # ── Header ──
        hdr = tk.Frame(f, bg=CARD, pady=16)
        hdr.pack(fill=tk.X)
        tk.Label(hdr, text="⚡  KERNEL DEFENDER  ⚡",
                 fg=ACCENT, bg=CARD, font=ff(24, bold=True)).pack()
        tk.Label(hdr, text="Protect the Core. Survive the waves.",
                 fg=GRAY, bg=CARD, font=ff(10)).pack(pady=(4, 0))

        # ── Player count ──
        pc_card = tk.Frame(f, bg=CARD, pady=20)
        pc_card.pack(fill=tk.X, padx=30, pady=(24, 0))

        tk.Label(pc_card, text="HOW MANY PLAYERS?",
                 fg=GRAY, bg=CARD, font=ff(11, bold=True)).pack()

        row = tk.Frame(pc_card, bg=CARD)
        row.pack(pady=12)

        tk.Button(row, text=" − ", command=lambda: self._adj(-1),
                  fg=BG, bg=ORANGE, font=ff(20, bold=True),
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  bd=0, highlightthickness=0).pack(side=tk.LEFT, padx=12)

        self._pc_lbl = tk.Label(row, textvariable=self._pc,
                                fg=WHITE, bg=CARD2,
                                font=ff(52, bold=True), width=3)
        self._pc_lbl.pack(side=tk.LEFT)

        tk.Button(row, text=" + ", command=lambda: self._adj(+1),
                  fg=BG, bg=OK, font=ff(20, bold=True),
                  relief="flat", padx=10, pady=4, cursor="hand2",
                  bd=0, highlightthickness=0).pack(side=tk.LEFT, padx=12)

        tk.Label(pc_card, text="min 2  ·  max 10",
                 fg=GRAY, bg=CARD, font=ff(10)).pack()

        # ── Start button ──
        btn_wrap = tk.Frame(f, bg=BG)
        btn_wrap.pack(pady=20)
        big_btn(btn_wrap, "▶   START MISSION",
                lambda: self.cmd.start(self._pc.get()),
                bg=OK, size=16, pady=14, padx=40).pack()

        hbar(f, color=LINE)

        # ── Enemy guide ──
        guide = tk.Frame(f, bg=CARD, pady=14)
        guide.pack(fill=tk.X, padx=30)

        tk.Label(guide, text="ENEMY GUIDE",
                 fg=GRAY, bg=CARD, font=ff(10, bold=True)).pack(pady=(0, 8))

        for emoji, name, hits, col in [
            ("🟢", "GREEN",  "1 hit",   OK),
            ("🟡", "YELLOW", "2 hits",  WARN),
            ("🔴", "RED",    "3 hits",  DANGER),
        ]:
            row = tk.Frame(guide, bg=CARD)
            row.pack(anchor="w", padx=20, pady=3)
            tk.Label(row, text=emoji, bg=CARD, font=ff(16)).pack(side=tk.LEFT, padx=4)
            tk.Label(row, text=f"{name:<8}", fg=col, bg=CARD,
                     font=ff(13, bold=True)).pack(side=tk.LEFT, padx=6)
            tk.Label(row, text=f"→  {hits}", fg=WHITE, bg=CARD,
                     font=ff(12)).pack(side=tk.LEFT)

        hbar(guide, color=LINE, padx=0, pady=(10, 2))

        quake_row = tk.Frame(guide, bg=CARD)
        quake_row.pack(anchor="w", padx=20, pady=4)
        tk.Label(quake_row, text="⚡", bg=CARD, font=ff(16)).pack(side=tk.LEFT, padx=4)
        tk.Label(quake_row, text="Step on the CORE EDGE",
                 fg=PURPLE, bg=CARD, font=ff(12, bold=True)).pack(side=tk.LEFT, padx=6)
        tk.Label(quake_row, text="→  EARTHQUAKE  (max 3)",
                 fg=WHITE, bg=CARD, font=ff(12)).pack(side=tk.LEFT)

    def _build_countdown(self):
        f = self._frames["COUNTDOWN"]

        tk.Frame(f, bg=OK, height=6).pack(fill=tk.X)
        tk.Label(f, text="MISSION ACTIVE", fg=OK, bg=BG,
                 font=ff(26, bold=True)).pack(pady=(50, 10))
        tk.Label(f, text="GET INTO THE ROOM!", fg=WHITE, bg=BG,
                 font=ff(16)).pack()

        hbar(f, pady=20)

        tk.Label(f, text="STARTING IN", fg=GRAY, bg=BG,
                 font=ff(12, bold=True)).pack()

        self._cd_lbl = tk.Label(f, text="10", fg=WARN, bg=BG,
                                font=ff(130, bold=True))
        self._cd_lbl.pack(pady=4)

        tk.Label(f, text="seconds", fg=GRAY, bg=BG, font=ff(14)).pack()

        self._cd_sub = tk.Label(f, text="", fg=ACCENT, bg=BG, font=ff(12))
        self._cd_sub.pack(pady=16)

    def _build_playing(self):
        f = self._frames["PLAYING"]

        tk.Frame(f, bg=ACCENT, height=6).pack(fill=tk.X)
        tk.Label(f, text="⚔", fg=OK, bg=BG, font=ff(90)).pack(pady=(100, 20))
        tk.Label(f, text="BATTLE IN PROGRESS", fg=OK, bg=BG,
                 font=ff(22, bold=True)).pack()
        sub_label(f, "Check the interior screen for live stats.", size=12)

        self._lobby_wave_lbl = tk.Label(f, text="", fg=ACCENT, bg=BG,
                                        font=ff(16, bold=True))
        self._lobby_wave_lbl.pack(pady=20)

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
                bg=ACCENT, fg=BG, size=15, pady=12, padx=36).pack(pady=20)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _adj(self, d):
        self._pc.set(max(2, min(10, self._pc.get() + d)))

    def _show(self, name):
        for n, f in self._frames.items():
            f.lift() if n == name else f.lower()

    # ── Tick ──────────────────────────────────────────────────────────────────
    def _tick(self):
        s  = self.tel.get()
        st = s["state"]
        self._pulse = (self._pulse + 1) % 8

        if st == "LOBBY":
            self._show("LOBBY")

        elif st == "COUNTDOWN":
            self._show("COUNTDOWN")
            cd  = s["countdown_remaining"]
            col = (OK if cd > 7 else WARN if cd > 3 else DANGER)
            self._cd_lbl.config(text=str(cd), fg=col)
            self._cd_sub.config(text=f"{s['player_count']} players  ·  get in position!")

        elif st == "PLAYING":
            self._show("PLAYING")
            self._lobby_wave_lbl.config(
                text=f"Wave  {s['wave']} / {s['total_waves']}")

        elif st in ("GAMEOVER", "WIN"):
            self._show("ENDGAME")
            is_win = st == "WIN"

            if is_win:
                self._end_icon.config(text="🏆", fg=OK)
                self._end_title.config(text="VICTORY!", fg=OK)
                self._end_sub.config(text="All waves defeated. The Core survives!")
            else:
                fw   = s.get("fail_wave", s["wave"])
                bcol = DANGER if self._pulse < 4 else "#880000"
                self._end_icon.config(text="💀", fg=bcol)
                self._end_title.config(text="GAME OVER", fg=bcol)
                self._end_sub.config(
                    text=f"Core destroyed in Wave {fw} of {s['total_waves']}.")

            self._end_stats.config(
                text=f"Time: {fmt_time(s['elapsed_seconds'])}   ·   "
                     f"Players: {s['player_count']}")

        self.after(300, self._tick)


# ─────────────────────────────────────────────────────────────────────────────
# HUD WINDOW  (inside the room)
# ─────────────────────────────────────────────────────────────────────────────
class HUDWindow(tk.Toplevel):

    def __init__(self, root, tel: TelemetryReceiver, cmd: CommandSender):
        super().__init__(root)
        self.tel, self.cmd = tel, cmd
        self._root = root
        self.title("KERNEL DEFENDER – HUD")
        self.configure(bg=BG)
        self.geometry("600x720+720+0")
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._quit_app)
        self._blink  = False
        self._frames = {}
        self._build_all()
        # Floating quit button — always visible, top-right
        self._quit_btn = tk.Button(
            self, text="✕  QUIT", command=self._quit_app,
            fg=WHITE, bg="#330000",
            activeforeground=WHITE, activebackground=DANGER,
            font=ff(9, bold=True), relief="flat",
            padx=10, pady=4, cursor="hand2", bd=0
        )
        self._quit_btn.place(relx=1.0, rely=0, anchor="ne", x=-6, y=6)
        self._tick()

    def _quit_app(self):
        try:
            self.cmd.quit()
        except Exception:
            pass
        try:
            self._root.destroy()
        except Exception:
            pass

    # ── Build ─────────────────────────────────────────────────────────────────
    def _build_all(self):
        for name in ("WAIT", "PLAYING", "ENDGAME"):
            f = tk.Frame(self, bg=BG)
            f.place(relx=0, rely=0, relwidth=1, relheight=1)
            self._frames[name] = f
        self._build_wait()
        self._build_playing()
        self._build_endgame()
        self._show("WAIT")

    def _build_wait(self):
        f = self._frames["WAIT"]
        tk.Frame(f, bg=CARD, height=6).pack(fill=tk.X)
        tk.Label(f, text="◈  TELEMETRY HUD  ◈",
                 fg=ACCENT, bg=BG, font=ff(16, bold=True)).pack(pady=(40, 0))
        hbar(f)
        tk.Label(f, text="⏳", fg=GRAY, bg=BG, font=ff(60)).pack(pady=(60, 20))
        self._wait_lbl = tk.Label(f, text="Waiting in lobby...",
                                  fg=GRAY, bg=BG, font=ff(15))
        self._wait_lbl.pack()

    def _build_playing(self):
        f = self._frames["PLAYING"]

        # ── Top bar ──
        top_bar = tk.Frame(f, bg=CARD, pady=10)
        top_bar.pack(fill=tk.X)
        tk.Label(top_bar, text="◈  TELEMETRY HUD  ◈",
                 fg=ACCENT, bg=CARD, font=ff(15, bold=True)).pack()

        # ── Wave + Time cards ──
        row1 = tk.Frame(f, bg=BG)
        row1.pack(fill=tk.X, padx=24, pady=16)

        wf = tk.Frame(row1, bg=CARD, padx=16, pady=12)
        wf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        tk.Label(wf, text="WAVE", fg=GRAY, bg=CARD, font=ff(10, bold=True)).pack()
        self._wave_lbl = tk.Label(wf, text="1 / 3", fg=WHITE, bg=CARD,
                                  font=ff(32, bold=True))
        self._wave_lbl.pack()

        tf = tk.Frame(row1, bg=CARD, padx=16, pady=12)
        tf.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        tk.Label(tf, text="TIME", fg=GRAY, bg=CARD, font=ff(10, bold=True)).pack()
        self._time_lbl = tk.Label(tf, text="00:00", fg=ACCENT, bg=CARD,
                                  font=ff(32, bold=True))
        self._time_lbl.pack()

        # ── Progress bar ──
        bar_wrap = tk.Frame(f, bg=BG)
        bar_wrap.pack(fill=tk.X, padx=24)
        tk.Label(bar_wrap, text="WAVE PROGRESS", fg=GRAY, bg=BG,
                 font=ff(9, bold=True)).pack(anchor="w")
        self._bar_cv = tk.Canvas(bar_wrap, height=20, bg=CARD,
                                 highlightthickness=0)
        self._bar_cv.pack(fill=tk.X, pady=(2, 0))
        self._bar_rect = self._bar_cv.create_rectangle(
            0, 0, 0, 20, fill=ACCENT, outline="")
        self._bar_cv.bind("<Configure>", lambda _: self._redraw_bar())

        hbar(f, color=LINE)

        # ── Core lives ──
        lives_wrap = tk.Frame(f, bg=CARD, pady=14)
        lives_wrap.pack(fill=tk.X, padx=24)
        tk.Label(lives_wrap, text="CORE INTEGRITY", fg=GRAY, bg=CARD,
                 font=ff(10, bold=True)).pack()
        self._hearts_row = tk.Frame(lives_wrap, bg=CARD)
        self._hearts_row.pack(pady=8)
        self._heart_lbls = []
        for _ in range(3):
            l = tk.Label(self._hearts_row, text="♥", fg=DANGER, bg=CARD,
                         font=ff(44, bold=True))
            l.pack(side=tk.LEFT, padx=10)
            self._heart_lbls.append(l)

        hbar(f, color=LINE)

        # ── Seismic charges ──
        quake_wrap = tk.Frame(f, bg=CARD, pady=14)
        quake_wrap.pack(fill=tk.X, padx=24)
        tk.Label(quake_wrap, text="SEISMIC CHARGES", fg=GRAY, bg=CARD,
                 font=ff(10, bold=True)).pack()
        self._quakes_row = tk.Frame(quake_wrap, bg=CARD)
        self._quakes_row.pack(pady=8)
        self._quake_lbls = []
        for _ in range(3):
            l = tk.Label(self._quakes_row, text="⚡", fg=PURPLE, bg=CARD,
                         font=ff(38, bold=True))
            l.pack(side=tk.LEFT, padx=10)
            self._quake_lbls.append(l)

        hbar(f, color=LINE)

        # ── Threats ──
        threat_row = tk.Frame(f, bg=BG)
        threat_row.pack(fill=tk.X, padx=24, pady=4)

        tc = tk.Frame(threat_row, bg=CARD, padx=20, pady=10)
        tc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(0, 8))
        tk.Label(tc, text="THREATS ACTIVE", fg=GRAY, bg=CARD,
                 font=ff(10, bold=True)).pack()
        self._enemy_lbl = tk.Label(tc, text="0", fg=OK, bg=CARD,
                                   font=ff(40, bold=True))
        self._enemy_lbl.pack()

        pc = tk.Frame(threat_row, bg=CARD, padx=20, pady=10)
        pc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=(8, 0))
        tk.Label(pc, text="SQUAD SIZE", fg=GRAY, bg=CARD,
                 font=ff(10, bold=True)).pack()
        self._squad_lbl = tk.Label(pc, text="—", fg=WHITE, bg=CARD,
                                   font=ff(40, bold=True))
        self._squad_lbl.pack()

    def _build_endgame(self):
        f = self._frames["ENDGAME"]

        self._h_top = tk.Frame(f, bg=CARD, height=6)
        self._h_top.pack(fill=tk.X)

        self._h_icon  = tk.Label(f, text="", bg=BG, font=ff(80))
        self._h_icon.pack(pady=(60, 10))

        self._h_title = tk.Label(f, text="", bg=BG, font=ff(32, bold=True))
        self._h_title.pack()

        self._h_sub   = tk.Label(f, text="", fg=WHITE, bg=BG,
                                 font=ff(14), wraplength=540)
        self._h_sub.pack(pady=12)

        hbar(f)

        self._h_stats = tk.Label(f, text="", fg=GRAY, bg=BG, font=ff(13))
        self._h_stats.pack(pady=8)

        hbar(f)

        btn_row = tk.Frame(f, bg=BG)
        btn_row.pack(pady=16)
        big_btn(btn_row, "🔄   PLAY AGAIN", self.cmd.restart,
                bg=ACCENT, fg=BG, size=15, pady=12, padx=28).pack(side=tk.LEFT, padx=8)
        big_btn(btn_row, "✕   QUIT", self._quit_app,
                bg="#330000", fg=WHITE, size=15, pady=12, padx=28).pack(side=tk.LEFT, padx=8)

    # ── Helpers ───────────────────────────────────────────────────────────────
    def _show(self, name):
        for n, f in self._frames.items():
            f.lift() if n == name else f.lower()

    def _redraw_bar(self):
        s  = self.tel.get()
        total  = max(s.get("enemies_total", 1), 1)
        done   = s.get("enemies_spawned", 0)
        w      = self._bar_cv.winfo_width() or 550
        col    = [OK, WARN, DANGER][min(s.get("wave", 1) - 1, 2)]
        self._bar_cv.coords(self._bar_rect, 0, 0, int(w * done / total), 20)
        self._bar_cv.itemconfig(self._bar_rect, fill=col)

    # ── Tick ──────────────────────────────────────────────────────────────────
    def _tick(self):
        s  = self.tel.get()
        st = s["state"]
        self._blink = not self._blink

        # ── WAIT ──
        if st in ("LOBBY", "COUNTDOWN"):
            self._show("WAIT")
            if st == "COUNTDOWN":
                cd  = s["countdown_remaining"]
                col = OK if cd > 7 else WARN if cd > 3 else DANGER
                self._wait_lbl.config(
                    text=f"Starting in  {cd}s …", fg=col)
            else:
                self._wait_lbl.config(text="Waiting in lobby…", fg=GRAY)

        # ── PLAYING ──
        elif st == "PLAYING":
            self._show("PLAYING")

            # Wave
            wn  = s["wave"]
            wcol = [OK, WARN, DANGER][min(wn - 1, 2)]
            self._wave_lbl.config(text=f"{wn} / {s['total_waves']}", fg=wcol)

            # Time
            self._time_lbl.config(text=fmt_time(s["elapsed_seconds"]))

            # Bar
            self._redraw_bar()

            # Hearts
            lives = s["core_lives"]
            for i, h in enumerate(self._heart_lbls):
                if i < lives:
                    alive_col = (DANGER if lives == 1 and self._blink
                                 else WARN if lives == 2 else DANGER)
                    h.config(text="♥", fg=alive_col)
                else:
                    h.config(text="♡", fg=DIM)

            # Quakes
            qn = s["quakes_left"]
            for i, q in enumerate(self._quake_lbls):
                q.config(text="⚡" if i < qn else "·",
                         fg=PURPLE if i < qn else DIM)

            # Threats
            ec  = s["enemy_count"]
            ec_col = OK if ec == 0 else WARN if ec < 5 else DANGER
            self._enemy_lbl.config(text=str(ec), fg=ec_col)
            self._squad_lbl.config(text=str(s["player_count"]))

        # ── ENDGAME ──
        elif st in ("GAMEOVER", "WIN"):
            self._show("ENDGAME")
            is_win = st == "WIN"

            if is_win:
                self._h_top.config(bg=OK)
                self._h_icon.config(text="🏆", fg=OK)
                self._h_title.config(text="VICTORY!", fg=OK)
                self._h_sub.config(
                    text=f"All {s['total_waves']} waves survived. Core intact!")
            else:
                fw   = s.get("fail_wave", s["wave"])
                bcol = DANGER if self._blink else "#880000"
                self._h_top.config(bg=bcol)
                self._h_icon.config(text="💀", fg=bcol)
                self._h_title.config(text="GAME OVER", fg=bcol)
                self._h_sub.config(
                    text=f"Core destroyed — Wave {fw} of {s['total_waves']}",
                    fg=DANGER)

            self._h_stats.config(
                text=(f"Time: {fmt_time(s['elapsed_seconds'])}   ·   "
                      f"Core: {s['core_lives']}/3   ·   "
                      f"Charges: {s['quakes_left']}/3   ·   "
                      f"Squad: {s['player_count']}"))

        self.after(300, self._tick)


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

    # Bind Escape on root as emergency exit
    root.bind_all("<Escape>", lambda e: (cmd.quit(), root.destroy()))

    root.mainloop()


if __name__ == "__main__":
    main()