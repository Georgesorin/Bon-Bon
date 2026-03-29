"""
tension.py — "Tensiunea" | Matrix Room 16x32
Un singur fișier: logică joc + Ecran Exterior + Ecran Interior.
Porturi: 6666 (trimitere podea), 6667 (recepție podea).
Rulare: python3 tension.py
"""

import socket, threading, time, random, json, os, math, sys
import tkinter as tk
from tkinter import font as tkfont

# ── PyInstaller Paths & Config ────────────────────────────────────────────────
if getattr(sys, 'frozen', False):
    # Cand rulam ca executabil PyInstaller
    BUNDLE_DIR = sys._MEIPASS
    APP_DIR    = os.path.dirname(sys.executable)
else:
    # Cand rulam python normal
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR    = BUNDLE_DIR

_CFG = os.path.join(APP_DIR, "tension_config.json")

def _load_cfg():
    d = {"device_ip": "255.255.255.255", "send_port": 4626, "recv_port": 7800}
    try:
        if os.path.exists(_CFG):
            with open(_CFG) as f:
                d.update(json.load(f))
    except Exception as e:
        print(f"[Config] Eroare cfg: {e}")
    return d

CFG = _load_cfg()

# ── Hardware ──────────────────────────────────────────────────────────────────
NUM_CH    = 8
LEDS_CH   = 64
FRAME_LEN = NUM_CH * LEDS_CH * 3
W, H      = 16, 32

# ── Colors podea ──────────────────────────────────────────────────────────────
BLACK    = (0, 0, 0)
C_POS    = (207, 10, 29)   # Pol+ rosu
C_NEG    = (21, 96, 189)    # Pol- albastru
C_LINE   = (170, 40, 255)   # linie mov
C_ISO    = (230, 126, 48)   # izolanți portocalii
C_TGT    = (0, 220, 80)   # target verde
C_HIT    = (255, 255, 255)   # target atins alb
C_COND   = (255, 220, 0)   # conductor galben
C_BORDER = (255, 20, 147)   # margine roz (Safe Zone)

# ── Parametri joc ─────────────────────────────────────────────────────────────
ISO_N          = 12
TGT_N          = 8
TIMELIM        = 120.0    # 2 minute per rundă
PRE_START_WAIT = 10.0     # 10 de secunde
TOTAL_ROUNDS   = 2

# ── Stare globală joc ─────────────────────────────────────────────────────────
state           = "LOBBY"
num_players     = 2
running         = True

pole_pos        = [7, 2]   # Polul + sus
pole_neg        = [8, 29]  # Polul - jos
insulators      = set()
targets         = []
targets_hit     = set()
cur_path        = []
conductor_pos   = []
score           = 0
total_score     = 0
time_left       = TIMELIM
pre_game_time_left = 0.0
btimer          = 0.0
current_round   = 1
gameover_reason = ""

btn  = [[False] * LEDS_CH for _ in range(NUM_CH)]
lock = threading.RLock()

# ── MASCĂ PENTRU TEXTUL "READY?" PE PODEA ───────────────────────────────────
READY_MASK = [
    "                ", # 0
    "                ", # 1 R
    "                ", # 2
    "                ", # 3
    "                ", # 4
    "                ", # 5
    "                ", # 6 E
    "           XX   ", # 7
    "          XX    ", # 8
    "         XX     ", # 9
    "        XX      ", # 10
    "       XX       ", # 11 A
    "      XX        ", # 12
    "     XX         ", # 13
    "    XX          ", # 14
    "   XXXXXXXXXX   ", # 15
    "   XXXXXXXXXX   ", # 16 D
    "          XX    ", # 17
    "         XX     ", # 18
    "        XX      ", # 19
    "       XX       ", # 20
    "      XX        ", # 21 Y
    "     XX         ", # 22
    "    XX          ", # 23
    "   XX           ", # 24
    "                ", # 25
    "                ", # 26 ?
    "                ", # 27
    "                ", # 28
    "                ", # 29
    "                ", # 30
    "                ", # 31
]

# ── LED helpers ───────────────────────────────────────────────────────────────
def set_led(buf, x, y, color):
    if not (0 <= x < W and 0 <= y < H):
        return
    ch  = y // 4
    row = y % 4
    idx = row * 16 + x if row % 2 == 0 else row * 16 + (15 - x)
    off = idx * (NUM_CH * 3) + ch
    if off + NUM_CH * 2 < len(buf):
        buf[off]            = color[1]
        buf[off + NUM_CH]   = color[0]
        buf[off + NUM_CH*2] = color[2]

def led_xy(ch, led):
    row = led // 16
    col = led % 16
    x   = col if row % 2 == 0 else 15 - col
    y   = ch * 4 + row
    return x, y

def pressed_tiles():
    """Returnează coordonatele jucătorilor, inclusiv marginea roz."""
    out = []
    for ch in range(NUM_CH):
        for led in range(LEDS_CH):
            if btn[ch][led]:
                x, y = led_xy(ch, led)
                out.append((x, y))
    return out

# ── Bresenham ─────────────────────────────────────────────────────────────────
def get_line(x1, y1, x2, y2):
    points = []
    dx = abs(x2 - x1); dy = abs(y2 - y1)
    sx = 1 if x1 < x2 else -1
    sy = 1 if y1 < y2 else -1
    err = dx - dy
    while True:
        points.append((x1, y1))
        if x1 == x2 and y1 == y2:
            break
        e2 = 2 * err
        if e2 > -dy:
            err -= dy; x1 += sx
        if e2 < dx:
            err += dx; y1 += sy
    return points

# ── Setup rundă ───────────────────────────────────────────────────────────────
def setup_round():
    global insulators, targets, targets_hit, cur_path, conductor_pos
    global score, time_left, btimer, pole_pos, pole_neg, gameover_reason

    # Spawn points implicite (Sus și Jos, lăsând spațiu pe marginea roz)
    pole_pos        = [7, 2]
    pole_neg        = [8, 29]
    targets_hit     = set()
    cur_path        = []
    conductor_pos   = []
    score           = 0
    time_left       = TIMELIM
    btimer          = 0.0
    gameover_reason = ""

    # ZONA SIGURĂ: Calculăm liniile inițiale ca să nu punem obstacole pe ele
    safe_zone = set()
    safe_zone.update(get_line(pole_pos[0], pole_pos[1], pole_neg[0], pole_neg[1]))
    safe_zone.update(get_line(pole_pos[0], pole_pos[1], 2, 15))
    safe_zone.update(get_line(2, 15, pole_neg[0], pole_neg[1]))
    safe_zone.update(get_line(pole_pos[0], pole_pos[1], 13, 15))
    safe_zone.update(get_line(13, 15, pole_neg[0], pole_neg[1]))
    safe_zone.update(get_line(13, 15, 2, 15))

    insulators = set()
    while len(insulators) < ISO_N:
        c = (random.randint(1, W-2), random.randint(1, H-2))
        if c not in safe_zone: 
            insulators.add(c)

    targets = []
    while len(targets) < TGT_N:
        c = (random.randint(1, W-2), random.randint(1, H-2))
        if c not in insulators and c not in safe_zone:
            targets.append(c)

    extra = f" ({num_players - 2} conductori)" if num_players > 2 else ""
    print(f"[!] Runda {current_round}/{TOTAL_ROUNDS} — {int(TIMELIM)}s · "
          f"{TGT_N} ținte · {num_players} jucători{extra}")

# ── Render podea ──────────────────────────────────────────────────────────────
def render():
    buf = bytearray(FRAME_LEN)
    with lock:
        s = state
        np = num_players

    if s == "LOBBY":
        pass

    elif s == "PRE_GAME_TIMER":
        t = time.time() * 2
        for y in range(H):
            for x in range(W):
                if y < len(READY_MASK) and x < len(READY_MASK[y]) and READY_MASK[y][x] == 'X':
                    r = int((math.sin(t*0.5 + x*0.3) + 1) * 127)
                    g = int((math.sin(t*0.5 + y*0.3 + 2) + 1) * 127)
                    b = int((math.sin(t*0.5 + (x+y)*0.2 + 4) + 1) * 127)
                    set_led(buf, x, y, (r, g, b))
                else:
                    set_led(buf, x, y, BLACK)

        with lock:
            pp = pole_pos
            pn = pole_neg
        
        set_led(buf, pp[0], pp[1], C_POS)
        set_led(buf, pn[0], pn[1], C_NEG)

        if np >= 3:
            set_led(buf, 2, 15, C_COND)
        if np >= 4:
            set_led(buf, 13, 15, C_COND)

    elif s in ("GAMEOVER", "BROKEN"):
        t = time.time()
        v = int((1 + math.sin(t * 8)) * 80) + 30
        for y in range(H):
            for x in range(W):
                set_led(buf, x, y, (v, 0, 0))

    elif s == "WIN":
        t = time.time()
        v = int((1 + math.sin(t * 4)) * 127)
        c = (0, v, int(v * 0.4))
        for y in range(H):
            for x in range(W):
                set_led(buf, x, y, c if (x + y) % 2 == 0 else BLACK)

    elif s == "BETWEEN_ROUNDS":
        t = time.time()
        v = int((1 + math.sin(t * 3)) * 80) + 30
        for y in range(H):
            for x in range(W):
                set_led(buf, x, y, (0, int(v * 0.4), v))

    else:  # PLAYING
        with lock:
            _iso  = set(insulators)
            _tgts = list(targets)
            _hit  = set(targets_hit)
            _path = list(cur_path)
            _pp   = tuple(pole_pos)
            _pn   = tuple(pole_neg)
            _cond = list(conductor_pos)

        for y in range(H):
            for x in range(W):
                if x == 0 or x == W-1 or y == 0 or y == H-1:
                    set_led(buf, x, y, C_BORDER)

        for p in _iso:
            set_led(buf, *p, C_ISO)
        for p in _tgts:
            set_led(buf, *p, C_HIT if tuple(p) in _hit else C_TGT)
        for p in _path:
            if p not in _iso:
                set_led(buf, *p, C_LINE)
        for p in _cond:
            set_led(buf, *p, C_COND)
        set_led(buf, *_pp, C_POS)
        set_led(buf, *_pn, C_NEG)

    return buf

# ── Rețea podea ───────────────────────────────────────────────────────────────
class Net:
    def __init__(self):
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.seq  = 0
        self.ip   = CFG["device_ip"]
        self.port = CFG["send_port"]

    def send(self, data):
        self.seq = (self.seq + 1) & 0xFFFF or 1
        ip, port = self.ip, self.port
        r = lambda: random.randint(0, 127)

        p = bytearray([0x75, r(), r(), 0, 8, 2, 0, 0, 0x33, 0x44,
                        self.seq >> 8, self.seq & 0xFF, 0, 0, 0, 0x0E, 0])
        self._tx(p, ip, port)

        pl = bytearray()
        for _ in range(NUM_CH):
            pl += bytes([LEDS_CH >> 8, LEDS_CH & 0xFF])
        bd = bytearray([2, 0, 0, 0x88, 0x77, 0xFF, 0xF0,
                         len(pl) >> 8, len(pl) & 0xFF]) + pl
        bl = len(bd) - 1
        p  = bytearray([0x75, r(), r(), bl >> 8, bl & 0xFF]) + bd + bytearray([0x1E, 0])
        self._tx(p, ip, port)

        for idx, i in enumerate(range(0, len(data), 984), 1):
            chunk = data[i:i + 984]
            bd    = bytearray([2, 0, 0, 0x88, 0x77, idx >> 8, idx & 0xFF,
                                len(chunk) >> 8, len(chunk) & 0xFF]) + chunk
            bl    = len(bd) - 1
            p     = bytearray([0x75, r(), r(), bl >> 8, bl & 0xFF]) + bd
            p    += bytearray([0x1E if len(chunk) == 984 else 0x36, 0])
            self._tx(p, ip, port)
            time.sleep(0.002)

        p = bytearray([0x75, r(), r(), 0, 8, 2, 0, 0, 0x55, 0x66,
                        self.seq >> 8, self.seq & 0xFF, 0, 0, 0, 0x0E, 0])
        self._tx(p, ip, port)

    def _tx(self, pkt, ip, port):
        try:
            self.sock.sendto(pkt, (ip, port))
            self.sock.sendto(pkt, ("127.0.0.1", port))
        except:
            pass

def send_loop(net):
    while running:
        net.send(render())
        time.sleep(0.05)

def recv_floor_loop():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(("0.0.0.0", CFG["recv_port"]))
    s.settimeout(1.0)
    while running:
        try:
            data, _ = s.recvfrom(2048)
            if len(data) >= 1373 and data[0] == 0x88:
                for ch in range(NUM_CH):
                    base = 2 + ch * 171
                    for led in range(LEDS_CH):
                        btn[ch][led] = (data[base + 1 + led] == 0xCC)
        except:
            pass

# ── Logică joc ────────────────────────────────────────────────────────────────
def update(dt):
    global state, score, total_score, time_left, btimer, pre_game_time_left
    global pole_pos, pole_neg, cur_path, conductor_pos, current_round
    global gameover_reason

    with lock:
        s = state

    if s == "LOBBY":
        return

    if s == "PRE_GAME_TIMER":
        with lock:
            pre_game_time_left -= dt
            if pre_game_time_left <= 0:
                setup_round()
                state = "PLAYING"
        return

    if s in ("BROKEN", "WIN", "GAMEOVER"):
        with lock:
            btimer += dt
            if btimer > 6.0: 
                btimer = 0.0
                state  = "LOBBY"
        return

    if s == "BETWEEN_ROUNDS":
        with lock:
            btimer += dt
            if btimer > 4.0:
                btimer = 0.0
                current_round += 1
                setup_round()
                state = "PLAYING"
        return

    # ── PLAYING ──────────────────────────────────────────────────────────────
    with lock:
        time_left -= dt
        if time_left <= 0:
            total_score += score
            if current_round >= TOTAL_ROUNDS:
                state = "GAMEOVER"
                gameover_reason = "TIMP EXPIRAT!\nNu v-ați mișcat destul de repede."
                print(f"[!] TIMP EXPIRAT! Scor total: {total_score}")
            else:
                state  = "BETWEEN_ROUNDS"
                btimer = 0.0
                print(f"[!] Runda {current_round} încheiată. Scor: {score}")
            return

    pts = pressed_tiles()

    with lock:
        np = num_players

        if len(pts) >= 2:
            best_pair = None
            min_d     = float('inf')
            for i, p1 in enumerate(pts):
                for p2 in pts[i+1:]:
                    d_direct  = math.dist(p1, pole_pos) + math.dist(p2, pole_neg)
                    d_inversat = math.dist(p2, pole_pos) + math.dist(p1, pole_neg)
                    if d_direct <= d_inversat:
                        cand, dist = (p1, p2), d_direct
                    else:
                        cand, dist = (p2, p1), d_inversat
                    if dist < min_d:
                        min_d     = dist
                        best_pair = cand

            if best_pair:
                pole_pos = list(best_pair[0])
                pole_neg = list(best_pair[1])
                conductor_pos = [p for p in pts if p != best_pair[0] and p != best_pair[1]]
            else:
                conductor_pos = []

        elif len(pts) == 1:
            p = pts[0]
            if math.dist(p, pole_pos) <= math.dist(p, pole_neg):
                pole_pos = list(p)
            else:
                pole_neg = list(p)
            conductor_pos = []

        else:
            conductor_pos = []

        pp   = tuple(pole_pos)
        pn   = tuple(pole_neg)
        iso  = set(insulators)
        tgts = list(targets)
        hit  = set(targets_hit)
        cond = list(conductor_pos)

    if pp[0] == 0 or pp[0] == W-1 or pp[1] == 0 or pp[1] == H-1 or \
       pn[0] == 0 or pn[0] == W-1 or pn[1] == 0 or pn[1] == H-1:
        with lock:
            total_score += score
            state = "GAMEOVER"
            gameover_reason = "POL ÎN SAFE ZONE!\nPolii nu au voie pe marginea roz."
            print("[!] SCURTCIRCUIT! Un pol a atins Safe Zone-ul.")
        return

    if cond and np > 2:
        cond_sorted = sorted(cond, key=lambda c: math.dist(c, pp))
        path = []
        current_point = pp
        for next_point in cond_sorted:
            path.extend(get_line(current_point[0], current_point[1], next_point[0], next_point[1]))
            current_point = next_point
        path.extend(get_line(current_point[0], current_point[1], pn[0], pn[1]))
    else:
        path = get_line(pp[0], pp[1], pn[0], pn[1])

    with lock:
        cur_path = path
        for p in path:
            if p in iso:
                total_score += score
                state = "GAMEOVER"
                gameover_reason = "SCURTCIRCUIT!\nAți atins un izolator portocaliu."
                print("[!] SCURTCIRCUIT! Linia a atins un izolator.")
                return

        for t in tgts:
            tp = tuple(t)
            if tp in [tuple(p) for p in path] and tp not in hit:
                targets_hit.add(tp)
                score += 100
                print(f"[!] Țintă prinsă! Scor rundă: {score}")

        if len(targets_hit) >= TGT_N:
            total_score += score
            if current_round >= TOTAL_ROUNDS:
                state = "WIN"
                print(f"[!] VICTORIE! Scor total: {total_score}")
            else:
                state  = "BETWEEN_ROUNDS"
                btimer = 0.0
                print(f"[!] Runda {current_round} câștigată! Scor: {score}")

def game_thread_func():
    last_t = time.time()
    while running:
        now    = time.time()
        update(now - last_t)
        last_t = now
        if sound_mgr:
            with lock:
                s  = state
                th = len(targets_hit)
                tl = time_left
            sound_mgr.update(s, th, tl)
        time.sleep(0.02)

# ── Helpers UI ────────────────────────────────────────────────────────────────
def lerp_color(c1, c2, t):
    return tuple(int(a + (b - a) * t) for a, b in zip(c1, c2))

def rgb_hex(r, g, b):
    return f"#{int(r):02x}{int(g):02x}{int(b):02x}"

# ── SOUND MANAGER ─────────────────────────────────────────────────────────────
class SoundManager:
    SR = 44100

    def __init__(self):
        self._ok          = False
        self._prev_state  = None
        self._prev_hits   = 0
        self._timer_warned = False
        try:
            import pygame as _pg
            import numpy  as _np
            _pg.mixer.pre_init(self.SR, -16, 2, 512)
            if not _pg.get_init():
                _pg.init()
            _pg.mixer.init(frequency=self.SR, size=-16, channels=2, buffer=512)
            self._pg = _pg
            self._np = _np
            _pg.mixer.set_num_channels(8)
            self._ch_bg   = _pg.mixer.Channel(0) 
            self._ch_sfx  = _pg.mixer.Channel(1) 
            self._ch_end  = _pg.mixer.Channel(2) 
            self._ch_tmr  = _pg.mixer.Channel(3) 
            self._build()
            self._ok = True
        except Exception as e:
            print(f"[Audio] Indisponibil: {e}")

    def _t(self, dur):
        return self._np.linspace(0, dur, int(self.SR * dur), endpoint=False)

    def _sine(self, freq, dur, vol=0.5, decay=0.0):
        t = self._t(dur)
        w = self._np.sin(2 * self._np.pi * freq * t) * vol
        if decay: w *= self._np.exp(-decay * t)
        return w

    def _mix(self, *arrs):
        n = max(len(a) for a in arrs)
        out = self._np.zeros(n)
        for a in arrs:
            out[:len(a)] += a
        return self._np.clip(out, -1, 1)

    def _concat(self, *arrs):
        return self._np.concatenate(arrs)

    def _to_snd(self, wave):
        arr    = (self._np.clip(wave, -1, 1) * 32767).astype(self._np.int16)
        stereo = self._np.column_stack([arr, arr]).copy()
        return self._pg.sndarray.make_sound(stereo)

    def _build(self):
        np = self._np
        SR = self.SR
        pg = self._pg
        _dir = os.path.join(BUNDLE_DIR, "sounds")

        def _load(fname):
            path = os.path.join(_dir, fname)
            if os.path.exists(path):
                try: return pg.mixer.Sound(path)
                except: pass
            return None

        self.snd_ding = _load("ding.mp3")

        parts = []
        for i in range(10):
            hi = 1100 if i % 2 == 0 else 1500
            parts += [self._sine(hi, 0.08, 0.65), np.zeros(int(SR * 0.06)),
                      self._sine(hi, 0.06, 0.55), np.zeros(int(SR * 0.06))]
        self.snd_timer = self._to_snd(self._concat(*parts))

        self.snd_fail = _load("faaack.mp3") or _load("fail.mp3")
        self.snd_win = _load("champions.mp3") or _load("victory.mp3")
        self.snd_bg = self._to_snd(self._gen_bg(10.0))
        self.snd_prestart = _load("elevator.mp3")
        if self.snd_prestart is None:
            self.snd_prestart = self._to_snd(self._gen_prestart(8.0))

    def _gen_bg(self, dur=10.0):
        np = self._np; SR = self.SR
        t  = self._t(dur)
        bass  = np.sin(2*np.pi*55*t) * 0.28
        bass *= 0.55 + 0.45 * np.sin(2*np.pi*1.5*t)
        hum = (np.sin(2*np.pi*110*t)*0.10 + np.sin(2*np.pi*220*t)*0.06 + np.sin(2*np.pi*330*t)*0.03)
        arp_fr = [110, 130.8, 164.8, 196.0, 164.8, 130.8]
        arp_rt = 3.0
        arp    = np.zeros(len(t))
        for bi in range(int(dur * arp_rt)):
            si = int(bi / arp_rt * SR); ei = min(int((bi+1) / arp_rt * SR), len(t))
            f  = arp_fr[bi % len(arp_fr)]
            lt = t[si:ei] - t[si]
            arp[si:ei] = np.sin(2*np.pi*f*lt) * 0.13 * np.exp(-lt*7)
        return self._mix(bass, hum, arp)

    def _gen_prestart(self, dur=8.0):
        np = self._np; SR = self.SR
        t  = self._t(dur)
        elev_notes = [392, 440, 493.9, 587.3, 659.3, 587.3, 493.9, 440]
        nd   = dur / len(elev_notes)
        mel  = np.zeros(len(t))
        for i, fr in enumerate(elev_notes):
            si = int(i * nd * SR); ei = int((i+1) * nd * SR)
            lt = np.linspace(0, nd, ei-si, endpoint=False)
            mel[si:ei] = (np.sin(2*np.pi*fr*lt)*0.28 + np.sin(2*np.pi*fr*2*lt)*0.10) * np.exp(-lt*2.5)
        bass = np.sin(2*np.pi*98*t)*0.18 * (0.5+0.5*np.sin(2*np.pi*0.8*t))
        shock_track = np.zeros(len(t))
        for st in [1.2, 3.0, 5.4, 7.1]:
            si = int(st * SR); ei = min(si + int(0.12*SR), len(t))
            raw = (np.random.random(ei-si)*2-1)
            env = np.exp(-np.linspace(0, 18, ei-si))
            shock_track[si:ei] += raw * env * 0.55
        return self._mix(mel, bass, shock_track)

    def _play_bg(self, snd):
        if self._ch_bg:
            self._ch_bg.stop()
            self._ch_bg.play(snd, loops=-1)

    def update(self, new_state, hits, timer_val):
        if not self._ok: return
        prev = self._prev_state
        if new_state != prev:
            if   new_state == "PLAYING":
                self._timer_warned = False
                self._ch_tmr.stop()
                self._play_bg(self.snd_bg)
            elif new_state == "PRE_GAME_TIMER":
                self._play_bg(self.snd_prestart)
            elif new_state in ("LOBBY", "BETWEEN_ROUNDS"):
                self._ch_bg.stop()
                self._ch_tmr.stop()
                self._timer_warned = False
            if   new_state == "WIN":
                self._ch_bg.stop()
                self._ch_tmr.stop()
                self._ch_end.play(self.snd_win)
            elif new_state == "GAMEOVER":
                self._ch_bg.stop()
                self._ch_tmr.stop()
                self._ch_end.play(self.snd_fail)

        if hits > self._prev_hits: self._ch_sfx.play(self.snd_ding)
        self._prev_hits = hits

        if new_state == "PLAYING" and 0 < timer_val <= 10.0 and not self._timer_warned:
            self._timer_warned = True
            self._ch_bg.set_volume(0.35)
            self._ch_tmr.play(self.snd_timer)

        self._prev_state = new_state

sound_mgr = None

# ── LOGICA AUTOMATA PENTRU MONITOARE ──────────────────────────────────────────
def assign_screens():
    """
    Detecteaza automat monitoarele si le atribuie:
    m_ext (Exterior) -> Monitorul Principal (unde se porneste jocul, are bara de start).
    m_int (Interior) -> Monitorul Secundar (TV-ul din camera).
    """
    m_ext = None
    m_int = None

    try:
        from screeninfo import get_monitors
        monitors = get_monitors()
        
        # Cautam explicit care este monitorul principal si care este cel secundar
        for m in monitors:
            if m.is_primary:
                m_ext = {'x': m.x, 'y': m.y, 'w': m.width, 'h': m.height}
            else:
                m_int = {'x': m.x, 'y': m.y, 'w': m.width, 'h': m.height}
        
        # Fallback 1: Daca nu stie care e primary, il luam pe primul ca exterior
        if not m_ext and monitors:
            m_ext = {'x': monitors[0].x, 'y': monitors[0].y, 'w': monitors[0].width, 'h': monitors[0].height}
            
        # Fallback 2: Daca nu a gasit secundar dar avem mai mult de 1 ecran, il gasim pe celalalt
        if not m_int and len(monitors) > 1:
            for m in monitors:
                if m.x != m_ext['x'] or m.y != m_ext['y']:
                    m_int = {'x': m.x, 'y': m.y, 'w': m.width, 'h': m.height}
                    break
                    
    except ImportError:
        print("[Monitoare] Modulul 'screeninfo' nu e instalat. Folosesc ecran unic.")
    except Exception as e:
        print(f"[Monitoare] Eroare la detectie: {e}")

    # Fallback absolut daca suntem pe un singur monitor
    if not m_ext:
        m_ext = {'x': 0, 'y': 0, 'w': 1024, 'h': 768}
    if not m_int:
        m_int = m_ext  # Ambele ecrane se vor deschide unul peste altul

    return m_ext, m_int

# ══════════════════════════════════════════════════════════════════════════════
#  ECRAN EXTERIOR — Panou de control
# ══════════════════════════════════════════════════════════════════════════════
class ExteriorWindow(tk.Toplevel):
    def __init__(self, master, m_ext):
        super().__init__(master)
        self.title("⚡ TENSIUNEA — Panou Exterior")
        self.configure(bg="#0a0a0f")
        self.resizable(True, True)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        # Asezam fereastra exact pe monitorul atribuit pentru Exterior
        self.geometry(f"{m_ext['w']}x{m_ext['h']}+{m_ext['x']}+{m_ext['y']}")
        self.after(200, lambda: self.attributes('-fullscreen', True))
        self.bind('<Escape>', lambda e: self.attributes('-fullscreen', False))

        self._num_players = tk.IntVar(value=2)
        self._build()
        self._tick()

    def _on_close(self):
        global running
        running = False
        self.master.destroy()

    def _build(self):
        BG     = "#0a0a0f"
        ACCENT = "#7700ff"
        GOLD   = "#ffcc00"
        WHITE  = "#e8e8ff"
        GRAY   = "#2a2a44"

        f_title = tkfont.Font(family="Arial", size=24, weight="bold")
        f_sub   = tkfont.Font(family="Arial", size=11)
        f_btn   = tkfont.Font(family="Arial", size=15, weight="bold")
        f_num   = tkfont.Font(family="Arial", size=26, weight="bold")
        f_info  = tkfont.Font(family="Arial", size=10)
        f_live  = tkfont.Font(family="Arial", size=12)

        tk.Label(self, text="⚡  T E N S I U N E A  ⚡",
                 font=f_title, bg=BG, fg=ACCENT).pack(pady=(22, 2))
        tk.Label(self, text="Câmpul electric al podelei — Panou Exterior",
                 font=f_sub, bg=BG, fg="#444466").pack()
        tk.Frame(self, bg=ACCENT, height=2).pack(fill=tk.X, padx=50, pady=10)

        frm = tk.Frame(self, bg=BG)
        frm.pack(pady=10)
        tk.Label(frm, text="JUCĂTORI:", font=f_sub, bg=BG, fg=WHITE).grid(
            row=0, column=0, padx=(0, 16), sticky="e")
        tk.Button(frm, text="−", font=f_num, bg=GRAY, fg=WHITE,
                  width=2, relief="flat", cursor="hand2",
                  command=self._dec).grid(row=0, column=1, padx=4)
        tk.Label(frm, textvariable=self._num_players,
                 font=f_num, bg=BG, fg=GOLD, width=3).grid(row=0, column=2)
        tk.Button(frm, text="+", font=f_num, bg=GRAY, fg=WHITE,
                  width=2, relief="flat", cursor="hand2",
                  command=self._inc).grid(row=0, column=3, padx=4)
        self.lbl_mode = tk.Label(frm, text="", font=f_info, bg=BG, fg="#555577")
        self.lbl_mode.grid(row=1, column=0, columnspan=4, pady=(6, 0))

        tk.Frame(self, bg=ACCENT, height=1).pack(fill=tk.X, padx=50, pady=8)
        frm_btns = tk.Frame(self, bg=BG)
        frm_btns.pack(pady=10)

        self.btn_start = tk.Button(
            frm_btns, text="▶  START", font=f_btn,
            bg="#007733", fg="white", width=12, height=2,
            relief="flat", cursor="hand2", command=self._start)
        self.btn_start.grid(row=0, column=0, padx=10)

        self.btn_restart = tk.Button(
            frm_btns, text="↺  RESTART", font=f_btn,
            bg="#885500", fg="white", width=12, height=2,
            relief="flat", cursor="hand2", command=self._restart)
        self.btn_restart.grid(row=0, column=1, padx=10)

        self.btn_quit = tk.Button(
            frm_btns, text="✕  QUIT", font=f_btn,
            bg="#880022", fg="white", width=12, height=2,
            relief="flat", cursor="hand2", command=self._quit)
        self.btn_quit.grid(row=0, column=2, padx=10)

        tk.Frame(self, bg=ACCENT, height=1).pack(fill=tk.X, padx=50, pady=10)
        self.lbl_status = tk.Label(self, text="STARE: LOBBY",
                                   font=f_live, bg=BG, fg=GOLD)
        self.lbl_status.pack()
        self.lbl_live = tk.Label(self, text="", font=f_info, bg=BG, fg=WHITE)
        self.lbl_live.pack(pady=4)

        tk.Frame(self, bg="#1a1a30", height=1).pack(fill=tk.X, padx=50, pady=8)
        legend = tk.Frame(self, bg=BG)
        legend.pack()
        
        items = [(C_POS, "Pol +"), (C_NEG, "Pol −"), (C_LINE, "Linie"),
                 (C_ISO, "Izolator"), (C_TGT, "Țintă"), (C_HIT, "Prins"), (C_COND, "Conductor"), (C_BORDER, "Safe Zone")]
                 
        for i, (color_tuple, lb) in enumerate(items):
            hex_color = rgb_hex(*color_tuple)
            item_frm = tk.Frame(legend, bg=BG)
            item_frm.grid(row=0, column=i, padx=8, pady=4)
            tk.Label(item_frm, text="■", font=f_info, bg=BG, fg=hex_color).pack(side=tk.LEFT)
            tk.Label(item_frm, text=lb, font=f_info, bg=BG, fg="#8888aa").pack(side=tk.LEFT, padx=2)

    def _inc(self):
        v = self._num_players.get()
        if v < 10: self._num_players.set(v + 1)

    def _dec(self):
        v = self._num_players.get()
        if v > 2: self._num_players.set(v - 1)

    def _flash(self, btn_widget, col):
        orig = btn_widget.cget("bg")
        btn_widget.config(bg=col)
        self.after(200, lambda: btn_widget.config(bg=orig))

    def _start(self):
        global state, num_players, current_round, total_score, pre_game_time_left
        n = self._num_players.get()
        with lock:
            if state == "LOBBY":
                num_players        = n
                current_round      = 1
                total_score        = 0
                pre_game_time_left = PRE_START_WAIT
                setup_round() 
                state              = "PRE_GAME_TIMER"
            elif state == "PRE_GAME_TIMER":
                pre_game_time_left = 0
        self._flash(self.btn_start, "#00ff88")

    def _restart(self):
        global state, num_players, current_round, total_score, pre_game_time_left
        n = self._num_players.get()
        with lock:
            num_players        = n
            current_round      = 1
            total_score        = 0
            pre_game_time_left = PRE_START_WAIT
            setup_round() 
            state              = "PRE_GAME_TIMER"
        self._flash(self.btn_restart, "#ffaa00")

    def _quit(self):
        global state
        with lock:
            state = "LOBBY"
        self._flash(self.btn_quit, "#ff4455")

    def _tick(self):
        if not running: return
        with lock:
            s   = state
            tl  = pre_game_time_left if s == "PRE_GAME_TIMER" else time_left
            sc  = score
            tsc = total_score
            th  = len(targets_hit)
            rnd = current_round
            np  = num_players

        n = self._num_players.get()
        if n == 2: mode = "Mod clasic — 1 Pol+ și 1 Pol−, fără conductori"
        else: mode = f"2 poli + {n - 2} conductor{'i' if (n-2) > 1 else ''} care ghidează linia"
        self.lbl_mode.config(text=mode)

        labels = {
            "LOBBY":          ("LOBBY — Așteptare",  "#ffcc00"),
            "PRE_GAME_TIMER": ("INTRARE JUCĂTORI...", "#00ffff"),
            "PLAYING":        ("ÎN JOC",             "#00ff88"),
            "GAMEOVER":       ("GAME OVER",          "#ff2244"),
            "WIN":            ("VICTORIE! 🏆",        "#00ffcc"),
            "BETWEEN_ROUNDS": ("Pauză inter-runde…", "#44aaff"),
            "BROKEN":         ("RUPT!",              "#ff2244"),
        }
        txt, col = labels.get(s, (s, "#ffffff"))
        self.lbl_status.config(text=f"STARE: {txt}", fg=col)

        mins, secs = int(tl) // 60, int(tl) % 60
        self.lbl_live.config(text=(f"Runda {rnd}/{TOTAL_ROUNDS}  |  Timp: {mins}:{secs:02d}  |  Ținte: {th}/{TGT_N}  |  Scor rundă: {sc}  |  Scor total: {tsc}"))

        start_state = tk.NORMAL if s in ("LOBBY", "PRE_GAME_TIMER") else tk.DISABLED
        start_bg = "#007733" if s in ("LOBBY", "PRE_GAME_TIMER") else "#333333"
        self.btn_start.config(state=start_state, bg=start_bg)
        self.btn_restart.config(state=tk.NORMAL if s != "LOBBY" else tk.DISABLED, bg="#885500" if s != "LOBBY" else "#333333")

        self.after(100, self._tick)

# ══════════════════════════════════════════════════════════════════════════════
#  ECRAN INTERIOR — HUD cameră
# ══════════════════════════════════════════════════════════════════════════════
class InteriorWindow(tk.Tk):
    def __init__(self, m_int):
        super().__init__()
        self.title("⚡ TENSIUNEA — HUD Interior")
        self.configure(bg="#000010")
        self.resizable(True, True)

        # Asezam fereastra exact pe monitorul atribuit pentru Interior
        self.geometry(f"{m_int['w']}x{m_int['h']}+{m_int['x']}+{m_int['y']}")
        self.after(200, lambda: self.attributes('-fullscreen', True))
        self.bind('<Escape>', lambda e: self.attributes('-fullscreen', False))

        self._anim_t = 0.0
        self._last_t = time.time()
        self._build()
        self._tick()

    def _build(self):
        BG = "#000010"
        f_round  = tkfont.Font(family="Arial", size=16, weight="bold")
        f_timer  = tkfont.Font(family="Arial", size=88, weight="bold")
        f_label  = tkfont.Font(family="Arial", size=12)
        f_score  = tkfont.Font(family="Arial", size=34, weight="bold")
        f_target = tkfont.Font(family="Arial", size=26, weight="bold")
        f_msg    = tkfont.Font(family="Arial", size=24, weight="bold")
        f_sq     = tkfont.Font(family="Arial", size=28, weight="bold")

        top = tk.Frame(self, bg="#080818")
        top.pack(fill=tk.X)
        self.lbl_round = tk.Label(top, text="RUNDA 1 / 2", font=f_round, bg="#080818", fg="#7700ff")
        self.lbl_round.pack(side=tk.LEFT, padx=24, pady=8)
        self.lbl_np = tk.Label(top, text="👥 2", font=f_round, bg="#080818", fg="#333355")
        self.lbl_np.pack(side=tk.RIGHT, padx=24, pady=8)

        main = tk.Frame(self, bg=BG)
        main.pack(fill=tk.BOTH, expand=True)
        main.columnconfigure(0, weight=1)
        main.columnconfigure(1, weight=2)
        main.columnconfigure(2, weight=1)

        left = tk.Frame(main, bg=BG)
        left.grid(row=0, column=0, sticky="nsew", padx=10, pady=16)
        tk.Label(left, text="SCOR RUNDĂ", font=f_label, bg=BG, fg="#444466").pack(pady=(30, 2))
        self.lbl_score = tk.Label(left, text="0", font=f_score, bg=BG, fg="#ffcc00")
        self.lbl_score.pack()
        tk.Frame(left, bg="#1a1a33", height=1).pack(fill=tk.X, padx=16, pady=12)
        tk.Label(left, text="SCOR TOTAL", font=f_label, bg=BG, fg="#444466").pack(pady=(2, 2))
        self.lbl_total = tk.Label(left, text="0", font=f_score, bg=BG, fg="#ff8800")
        self.lbl_total.pack()

        center = tk.Frame(main, bg=BG)
        center.grid(row=0, column=1, sticky="nsew", pady=8)
        tk.Label(center, text="TIMP RĂMAS", font=f_label, bg=BG, fg="#444466").pack(pady=(16, 0))
        self.lbl_timer = tk.Label(center, text="2:00", font=f_timer, bg=BG, fg="#00ccff")
        self.lbl_timer.pack()
        self.bar = tk.Canvas(center, bg=BG, height=14, highlightthickness=0)
        self.bar.pack(fill=tk.X, padx=30, pady=(0, 8))
        self.lbl_msg = tk.Label(center, text="", font=f_msg, bg=BG, fg="#ffffff", justify=tk.CENTER)
        self.lbl_msg.pack(pady=4)

        right = tk.Frame(main, bg=BG)
        right.grid(row=0, column=2, sticky="nsew", padx=10, pady=16)
        tk.Label(right, text="ȚINTE PRINSE", font=f_label, bg=BG, fg="#444466").pack(pady=(30, 2))
        self.lbl_tgt = tk.Label(right, text="0 / 8", font=f_target, bg=BG, fg="#00dd66")
        self.lbl_tgt.pack()
        tiles_frm = tk.Frame(right, bg=BG)
        tiles_frm.pack(pady=12)
        self._tiles = []
        for i in range(TGT_N):
            lb = tk.Label(tiles_frm, text="■", font=f_sq, bg=BG, fg="#0d2e18")
            lb.grid(row=i // 4, column=i % 4, padx=5, pady=5)
            self._tiles.append(lb)

        self.frm_end = tk.Frame(self, bg=BG)
        self.lbl_end_emojis = tk.Label(self.frm_end, text="", font=("Arial", 46), bg=BG, fg="white")
        self.lbl_end_emojis.pack(pady=(80, 10))
        self.lbl_end_title = tk.Label(self.frm_end, text="", font=("Arial", 60, "bold"), bg=BG, fg="white")
        self.lbl_end_title.pack(pady=10)
        self.lbl_end_reason = tk.Label(self.frm_end, text="", font=("Arial", 24), bg=BG, fg="white")
        self.lbl_end_reason.pack(pady=20)
        self.lbl_end_stats = tk.Label(self.frm_end, text="", font=("Arial", 20), bg=BG, fg="white")
        self.lbl_end_stats.pack(side=tk.BOTTOM, pady=50)

    def _tick(self):
        if not running: return
        now = time.time()
        self._anim_t += now - self._last_t
        self._last_t  = now
        t = self._anim_t
        with lock:
            s      = state
            tl     = time_left
            sc     = score
            tsc    = total_score
            th     = len(targets_hit)
            rnd    = current_round
            np     = num_players
            rsn    = gameover_reason
            if s == "PRE_GAME_TIMER":
                disp_time = pre_game_time_left
                frac = max(0.0, disp_time / PRE_START_WAIT)
            else:
                disp_time = time_left
                frac = max(0.0, disp_time / TIMELIM)
        
        if s in ("WIN", "GAMEOVER"):
            self.frm_end.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
            self.frm_end.lift()
            if s == "GAMEOVER":
                p = abs(math.sin(t * 4))
                bg_col = rgb_hex(*lerp_color((30, 0, 0), (70, 0, 0), p))
                fg_col = rgb_hex(*lerp_color((255, 50, 50), (255, 100, 100), p))
                self.lbl_end_emojis.config(text="🥀😬🥀", fg=fg_col, bg=bg_col)
                self.lbl_end_title.config(text="GAME OVER", fg=fg_col, bg=bg_col)
                self.lbl_end_reason.config(text=rsn, fg="#ffaaaa", bg=bg_col)
                stats_txt = f"Ținte prinse: {th} / {TGT_N}   |   Pierdut în runda: {rnd}"
                self.lbl_end_stats.config(text=stats_txt, fg="#ffffff", bg=bg_col)
                self.frm_end.config(bg=bg_col)
            else:
                p = abs(math.sin(t * 2))
                bg_col = rgb_hex(*lerp_color((0, 30, 10), (0, 70, 20), p))
                fg_col = rgb_hex(*lerp_color((50, 255, 100), (150, 255, 150), p))
                self.lbl_end_emojis.config(text="🎊👑🎊", fg=fg_col, bg=bg_col)
                self.lbl_end_title.config(text="VICTORIE!", fg=fg_col, bg=bg_col)
                self.lbl_end_reason.config(text="Energie restabilită cu succes!", fg="#aaffaa", bg=bg_col)
                stats_txt = f"Scor total: {tsc}   |   Ținte prinse: {th} / {TGT_N}"
                self.lbl_end_stats.config(text=stats_txt, fg="#ffffff", bg=bg_col)
                self.frm_end.config(bg=bg_col)
            self.after(50, self._tick)
            return
            
        self.frm_end.place_forget()
        self.lbl_round.config(text=f"RUNDA {rnd} / {TOTAL_ROUNDS}")
        self.lbl_np.config(text=f"👥 {np}")
        mins, secs = int(disp_time) // 60, int(disp_time) % 60
        self.lbl_timer.config(text=f"{mins}:{secs:02d}")
        
        if s == "PRE_GAME_TIMER":
            col = (0, 255, 255) 
        elif frac > 0.5:
            col = lerp_color((255, 200, 0), (0, 200, 255), (frac - 0.5) / 0.5)
        elif frac > 0.2:
            col = lerp_color((255, 50, 0), (255, 200, 0), (frac - 0.2) / 0.3)
        else:
            p   = abs(math.sin(t * 5))
            col = lerp_color((80, 0, 0), (255, 20, 0), p)
        self.lbl_timer.config(fg=rgb_hex(*col))
        self.bar.update_idletasks()
        bw = self.bar.winfo_width() or 1
        self.bar.delete("all")
        self.bar.create_rectangle(0, 0, bw, 14, fill="#0a0a20", outline="")
        fw = int(bw * frac)
        if fw > 0:
            self.bar.create_rectangle(0, 0, fw, 14, fill=rgb_hex(*col), outline="")
        self.lbl_score.config(text=str(sc))
        self.lbl_total.config(text=str(tsc))
        self.lbl_tgt.config(text=f"{th} / {TGT_N}")
        
        for i, lb in enumerate(self._tiles):
            if i < th:
                p = abs(math.sin(t * 2 + i * 0.6))
                lb.config(fg=rgb_hex(*lerp_color((120, 255, 120), (255, 255, 255), p)))
            else:
                lb.config(fg="#0d2e18")
                
        if s == "PRE_GAME_TIMER":
            msg, mcol, bg = "PREGĂTIȚI-VĂ!\nSTART ÎN CURÂND", "#00ffff", "#001122"
        elif s == "BROKEN":
            p = abs(math.sin(t * 4))
            mcol = rgb_hex(*lerp_color((200, 0, 0), (255, 60, 60), p))
            bg   = rgb_hex(*lerp_color((18, 0, 0), (35, 4, 4), p))
            msg  = "LINIE RUPTĂ!"
        elif s == "BETWEEN_ROUNDS":
            msg, mcol, bg = "Pregătire…", "#4488ff", "#001122"
        elif s == "LOBBY":
            msg, mcol, bg = "AȘTEPTARE…", "#334466", "#000010"
        else:
            msg, mcol, bg = "", "#ffffff", "#000010"
            
        self.lbl_msg.config(text=msg, fg=mcol)
        self.configure(bg=bg)
        self.after(50, self._tick)

# ── Main ──────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    M_EXT, M_INT = assign_screens()
    sound_mgr = SoundManager()
    net = Net()
    
    threading.Thread(target=send_loop,       args=(net,), daemon=True).start()
    threading.Thread(target=recv_floor_loop,              daemon=True).start()
    threading.Thread(target=game_thread_func,             daemon=True).start()
    
    root     = InteriorWindow(M_INT)
    exterior = ExteriorWindow(root, M_EXT)
    
    print("\u26a1 TENSIUNEA pornit.")
    root.mainloop()
    running = False