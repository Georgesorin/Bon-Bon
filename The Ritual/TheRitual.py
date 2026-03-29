import socket, threading, time, random, json, os, math, sys
import pygame
import numpy as np

import tkinter as tk
from tkinter import font as tkfont
import re
import subprocess

# ── Paths & Config ────────────────────────────────────────────────────────────
import sys
import os

PARENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
if PARENT_DIR not in sys.path:
    sys.path.append(PARENT_DIR)

try:
    from EvilEye.NetworkScanner import auto_discover_evileye
except ImportError:
    auto_discover_evileye = None

# Fallback la rularea din sursă
if getattr(sys, 'frozen', False):
    BUNDLE_DIR = sys._MEIPASS
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))

CFG_FILE = os.path.join(BUNDLE_DIR, "ritual_config.json")

def load_cfg():
    cfg = {"device_ip": "169.254.182.11", "send_port": 4626, "recv_port": 7800}
    try:
        if os.path.exists(CFG_FILE):
            with open(CFG_FILE) as f:
                cfg.update(json.load(f))
    except Exception as e:
        pass
        
    # --- AUTO-DISCOVERY MENTOR OVERRIDE ---
    if auto_discover_evileye:
        print("\n--- INIȚIERE CONEXIUNE THE RITUAL ---")
        discovered_ip = auto_discover_evileye(timeout=1.0)
        if discovered_ip:
            cfg["device_ip"] = discovered_ip
            cfg["send_port"] = 4626
            cfg["recv_port"] = 7800
            print(f"> [The Ritual] S-a atașat automat la camera hardware. IP: {discovered_ip} | Porturi: 4626/7800")
            
    return cfg

CFG = load_cfg()

# ── Hardware Constants ────────────────────────────────────────────────────────
NUM_CHANNELS     = 4
LEDS_PER_CHANNEL = 11
FRAME_DATA_LEN   = LEDS_PER_CHANNEL * NUM_CHANNELS * 3

PASSWORD_ARRAY = [
    35, 63, 187, 69, 107, 178, 92, 76, 39, 69, 205, 37, 223, 255, 165, 231,
    16, 220, 99, 61, 25, 203, 203, 155, 107, 30, 92, 144, 218, 194, 226, 88,
    196, 190, 67, 195, 159, 185, 209, 24, 163, 65, 25, 172, 126, 63, 224, 61,
    160, 80, 125, 91, 239, 144, 25, 141, 183, 204, 171, 188, 255, 162, 104, 225,
    186, 91, 232, 3, 100, 208, 49, 211, 37, 192, 20, 99, 27, 92, 147, 152,
    86, 177, 53, 153, 94, 177, 200, 33, 175, 195, 15, 228, 247, 18, 244, 150,
    165, 229, 212, 96, 84, 200, 168, 191, 38, 112, 171, 116, 121, 186, 147, 203,
    30, 118, 115, 159, 238, 139, 60, 57, 235, 213, 159, 198, 160, 50, 97, 201,
    253, 242, 240, 77, 102, 12, 183, 235, 243, 247, 75, 90, 13, 236, 56, 133,
    150, 128, 138, 190, 140, 13, 213, 18, 7, 117, 255, 45, 69, 214, 179, 50,
    28, 66, 123, 239, 190, 73, 142, 218, 253, 5, 212, 174, 152, 75, 226, 226,
    172, 78, 35, 93, 250, 238, 19, 32, 247, 223, 89, 123, 86, 138, 150, 146,
    214, 192, 93, 152, 156, 211, 67, 51, 195, 165, 66, 10, 10, 31, 1, 198,
    234, 135, 34, 128, 208, 200, 213, 169, 238, 74, 221, 208, 104, 170, 166, 36,
    76, 177, 196, 3, 141, 167, 127, 56, 177, 203, 45, 107, 46, 82, 217, 139,
    168, 45, 198, 6, 43, 11, 57, 88, 182, 84, 189, 29, 35, 143, 138, 171
]

def calc_sum(data):
    idx = sum(data) & 0xFF
    return PASSWORD_ARRAY[idx] if idx < len(PASSWORD_ARRAY) else 0

def build_pkt(data_id, msg_loc, payload, seq):
    rand1 = random.randint(0, 127)
    rand2 = random.randint(0, 127)
    internal = bytearray([
        0x02, 0x00, 0x00,
        (data_id >> 8) & 0xFF, data_id & 0xFF,
        (msg_loc >> 8) & 0xFF, msg_loc & 0xFF,
        (len(payload) >> 8) & 0xFF, len(payload) & 0xFF,
    ]) + payload
    hdr = bytearray([0x75, rand1, rand2, (len(internal) >> 8) & 0xFF, len(internal) & 0xFF])
    pkt = hdr + internal
    pkt[10] = (seq >> 8) & 0xFF
    pkt[11] = seq & 0xFF
    pkt.append(calc_sum(pkt))
    return bytes(pkt)

class EvilEyeNet:
    def __init__(self):
        self.ip    = CFG["device_ip"]
        self.port  = CFG["send_port"]
        self.rport = CFG["recv_port"]
        self.seq   = 0

        self.btn_state = [[False]*LEDS_PER_CHANNEL for _ in range(NUM_CHANNELS)]
        self.btn_events = []
        
        self.sock_snd = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_snd.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        self.sock_rcv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_rcv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_rcv.bind(("0.0.0.0", self.rport))
        self.sock_rcv.settimeout(0.5)

        self.running = True
        self.t_recv = threading.Thread(target=self._recv_loop, daemon=True)
        self.t_recv.start()

    def send_frame(self, led_states):
        self.seq = (self.seq + 1) & 0xFFFF or 1
        frame = bytearray(FRAME_DATA_LEN)
        for (ch, led), (r, g, b) in led_states.items():
            ch_idx = ch - 1
            if 0 <= ch_idx < NUM_CHANNELS and 0 <= led < LEDS_PER_CHANNEL:
                frame[led * 12 + ch_idx]     = int(min(255, max(0, r)))
                frame[led * 12 + 4 + ch_idx] = int(min(255, max(0, g)))
                frame[led * 12 + 8 + ch_idx] = int(min(255, max(0, b)))
        
        try:
            pkt = bytearray([0x75, random.randint(0,127), random.randint(0,127), 0x00, 0x08,
                             0x02, 0x00, 0x00, 0x33, 0x44, (self.seq>>8)&0xFF, self.seq&0xFF, 0x00, 0x00])
            pkt.append(calc_sum(pkt))
            self.sock_snd.sendto(bytes(pkt), (self.ip, self.port))
            
            p_fff0 = bytearray()
            for _ in range(NUM_CHANNELS): p_fff0 += bytes([0x00, LEDS_PER_CHANNEL])
            self.sock_snd.sendto(build_pkt(0x8877, 0xFFF0, p_fff0, self.seq), (self.ip, self.port))
            
            self.sock_snd.sendto(build_pkt(0x8877, 0x0000, frame, self.seq), (self.ip, self.port))
            
            pkt = bytearray([0x75, random.randint(0,127), random.randint(0,127), 0x00, 0x08,
                             0x02, 0x00, 0x00, 0x55, 0x66, (self.seq>>8)&0xFF, self.seq&0xFF, 0x00, 0x00])
            pkt.append(calc_sum(pkt))
            self.sock_snd.sendto(bytes(pkt), (self.ip, self.port))
        except: pass

    def _recv_loop(self):
        while self.running:
            try:
                data, _ = self.sock_rcv.recvfrom(1024)
                if len(data) == 687 and data[0] == 0x88:
                    for ch in range(1, NUM_CHANNELS + 1):
                        base = 2 + (ch - 1) * 171
                        for idx in range(LEDS_PER_CHANNEL):
                            val = data[base + 1 + idx]
                            is_down = (val == 0xCC)
                            was_down = self.btn_state[ch-1][idx]
                            if is_down and not was_down:
                                self.btn_events.append((ch, idx, "DOWN"))
                            self.btn_state[ch-1][idx] = is_down
            except: pass

    def get_events(self):
        with threading.Lock():
            evts = self.btn_events[:]
            self.btn_events.clear()
        return evts

    def close(self):
        self.running = False
        try: self.sock_rcv.close()
        except: pass
        try: self.sock_snd.close()
        except: pass

# ── Sound Manager ─────────────────────────────────────────────────────────────
class SoundManager:
    SR = 44100
    def __init__(self):
        pygame.mixer.pre_init(self.SR, -16, 2, 512)
        pygame.init()
        pygame.mixer.init(frequency=self.SR, size=-16, channels=2, buffer=512)
        pygame.mixer.set_num_channels(8)

        self.ch_bg  = pygame.mixer.Channel(0)
        self.ch_sfx = pygame.mixer.Channel(1)
        self.ch_beat = pygame.mixer.Channel(2)
        
        self.snd_good = self._synth_ding(800, 0.2)
        self.snd_bad  = self._synth_ding(150, 0.4, vol=0.8)
        self.snd_beat = self._synth_beat()
        self.snd_win  = self._synth_win()
        self.snd_lose = self._synth_lose()
        self.snd_bg   = self._synth_bg()
        self.snd_timer = self._synth_timer()
        
    def _t(self, dur): return np.linspace(0, dur, int(self.SR * dur), endpoint=False)
    def _to_snd(self, wave):
        arr = (np.clip(wave, -1, 1) * 32767).astype(np.int16)
        stereo = np.column_stack([arr, arr]).copy()
        return pygame.sndarray.make_sound(stereo)

    def _synth_ding(self, freq, dur, vol=0.5):
        t = self._t(dur)
        w = np.sin(2*np.pi*freq*t) * np.exp(-t*(5.0/dur))
        return self._to_snd(w * vol)

    def _synth_beat(self):
        t = self._t(0.1)
        w = np.sin(2*np.pi*60*t) * np.exp(-t*30)
        return self._to_snd(w * 0.9)

    def _synth_bg(self):
        # Illuminati / Ritual drone
        t = self._t(10.0)
        # Deep bass 73 Hz + harmonics for chanting feel
        w = np.sin(2*np.pi*73*t)*0.35 + np.sin(2*np.pi*110*t)*0.2 + np.sin(2*np.pi*146*t)*0.1
        # Slow pulsing wobble (chorus)
        w += np.sin(2*np.pi * (73 + 0.5 * np.sin(2*np.pi*0.2*t)) * t) * 0.15
        # Amplitude swelling (breathing/chanting)
        w *= (0.5 + 0.5*np.sin(2*np.pi*0.4*t))
        return self._to_snd(w * 0.8)

    def _synth_win(self):
        t = self._t(3.0)
        f_seq = [440, 554.37, 659.25, 880]
        w = np.zeros(len(t))
        for i, f in enumerate(f_seq):
            s = int(i * 0.4 * self.SR)
            e = int((i * 0.4 + 1.0) * self.SR)
            lt = t[:e-s]
            w[s:e] += np.sin(2*np.pi*f*lt) * np.exp(-lt*2) * 0.4
        return self._to_snd(w)
        
    def _synth_lose(self):
        # Flame ignition / Torch sound
        t = self._t(3.0)
        noise = np.random.normal(0, 1, len(t))
        # envelope: fast attack, slow fade
        env = np.exp(-t * 1.5)
        # Low frequency rumble of fire
        rumble = np.sin(2*np.pi*40*t) * np.exp(-t * 0.5)
        w = (noise * 0.5 + rumble * 0.8) * env
        return self._to_snd(w)
        
    def _synth_timer(self):
        t = self._t(0.15)
        w = np.sin(2*np.pi*1000*t) * np.exp(-t * 15)
        return self._to_snd(w * 0.3)

    def play_bg(self):
        if not self.ch_bg.get_busy():
            self.ch_bg.play(self.snd_bg, loops=-1)

    def stop_bg(self): self.ch_bg.stop()
    def play_beat(self): self.ch_beat.play(self.snd_beat)
    def play_good(self): self.ch_sfx.play(self.snd_good)
    def play_bad(self): self.ch_sfx.play(self.snd_bad)
    def play_win(self): self.ch_bg.play(self.snd_win)
    def play_lose(self): self.ch_bg.play(self.snd_lose)
    def play_timer(self): self.ch_beat.play(self.snd_timer)


# ── Monitor Detection ─────────────────────────────────────────────────────────
def detect_monitors():
    monitors = []
    try:
        from screeninfo import get_monitors
        for m in get_monitors():
            monitors.append({'x': m.x, 'y': m.y, 'w': m.width, 'h': m.height})
    except:
        pass

    if not monitors:
        try:
            out = subprocess.check_output(['xrandr', '--current'], stderr=subprocess.DEVNULL).decode()
            for mm in re.finditer(r'(\d+)x(\d+)\+(\d+)\+(\d+)', out):
                monitors.append({'x': int(mm.group(3)), 'y': int(mm.group(4)), 'w': int(mm.group(1)), 'h': int(mm.group(2))})
        except:
            pass
    if not monitors:
        monitors = [{'x': 0, 'y': 0, 'w': 1920, 'h': 1080}]

    monitors.sort(key=lambda m: m['x'])
    return monitors

MONITORS = []

# ── Global Game Logic ─────────────────────────────────────────────────────────
game_lock = threading.RLock()
class GameLogic:
    def __init__(self):
        self.net = EvilEyeNet()
        self.snd = SoundManager()
        self.state = "LOBBY"
        self.num_players = 2
        
        self.misses_total = 0
        self.misses_limit = 5
        self.consecutive_hits = 0
        self.win_target = 12

        self.phase = 1
        self.beat_time = 0
        self.next_beat = 0
        self.window_expire = 0

        self.base_tempo = 2.5
        self.base_window = 2.0
        self.tempo = self.base_tempo
        self.window = self.base_window

        self.active_walls = []
        self.active_targets = set()
        
        self.pulse_seq_idx = 0
        self.pulse_walls = [1, 2, 3, 4]
        self.round_counter = 0
        self.led_state_cache = {}
        
        self.pre_start_timer = 5.0
        self.pre_start_time = 0.0
        self.last_timer_beep = 0

    def get_global_color(self):
        # Pentru pereții activi pe care NU trebuie să apeși: Culoare ROȘIE (Avertizare!)
        if self.misses_total <= 5: return (255, 40, 0) # Roșu pal spre portocaliu
        else: return (255, 0, 0) # Roșu sânge (Atenție extremă)

    def set_phase(self, ph):
        self.phase = ph
        self.round_counter = 0
        self.consecutive_hits = 0
        if ph == 1:
            self.tempo = 6.0; self.window = 5.8
        elif ph == 2:
            self.tempo = 5.5; self.window = 5.3
        elif ph == 3:
            self.tempo = 5.0; self.window = 4.8

    def reset(self):
        with game_lock:
            self.misses_total = 0
            self.set_phase(1)
            self.state = "PRE_START"
            self.pre_start_timer = 5.0
            self.pre_start_time = time.time()
            self.last_timer_beep = 6
            self.pulse_seq_idx = 0
            self.active_targets.clear()
            
            # Simple scaling: restrict active walls if only 2 players
            if self.num_players == 2:
                self.pulse_walls = [1, 3] # Only N & S walls
            elif self.num_players == 3:
                self.pulse_walls = [1, 2, 3]
            else:
                self.pulse_walls = [1, 2, 3, 4]

    def update(self):
        now = time.time()
        
        with game_lock:
            if self.state == "PRE_START":
                elapsed = now - self.pre_start_time
                sec_left = int(self.pre_start_timer - elapsed)
                if sec_left < self.last_timer_beep and sec_left > 0:
                    self.snd.play_timer()
                    self.last_timer_beep = sec_left
                
                if elapsed >= self.pre_start_timer:
                    self.state = "PLAYING"
                    self.next_beat = now + 1.0
                    self.snd.play_bg()
                return

            events = self.net.get_events()
            
        for (ch, idx, ev) in events:
            if ev == "DOWN":
                with game_lock:
                    if self.state == "PLAYING" and idx > 0:
                        # ----- HITBOX PERMISIV (Anti-Offset Hardware) -----
                        # Dacă apasă fix butonul, cel de lângă din stânga, sau cel din dreapta, este acceptat
                        hit_target = None
                        if (ch, idx) in self.active_targets:
                            hit_target = (ch, idx)
                        elif (ch, idx - 1) in self.active_targets:
                            hit_target = (ch, idx - 1)
                        elif (ch, idx + 1) in self.active_targets:
                            hit_target = (ch, idx + 1)
                            
                        if hit_target:
                            # Hit!
                            self.active_targets.remove(hit_target)
                            self.snd.play_good()
                            self.consecutive_hits += 1
                        else:
                            if any(c == ch for (c,i) in self.active_targets) or ch in self.active_walls:
                                self._register_miss()

        with game_lock:
            if self.state == "PLAYING":
                if self.active_targets and now > self.window_expire:
                    misses = len(self.active_targets)
                    self.active_targets.clear()
                    for _ in range(misses):
                        self._register_miss()

                if now >= self.next_beat:
                    self.snd.play_beat()
                    self._trigger_beat(now)

                if self.misses_total >= self.misses_limit:
                    self.state = "LOSE"
                    self.snd.stop_bg()
                    self.snd.play_lose()
                elif self.phase == 3 and self.consecutive_hits >= self.win_target:
                    self.state = "WIN"
                    self.snd.stop_bg()
                    self.snd.play_win()

    def _register_miss(self):
        self.misses_total += 1
        self.consecutive_hits = 0
        self.snd.play_bad()
        if self.phase == 2:
            pass # Am sters penalizarea de timp pentru ca jocul sa ramana abordabil

    def _trigger_beat(self, now):
        self.beat_time = now
        self.next_beat = now + self.tempo
        self.window_expire = now + self.window
        self.active_targets.clear()

        if self.phase == 1 and self.round_counter >= 8:
            self.set_phase(2)
        elif self.phase == 2 and self.round_counter >= 12:
            self.set_phase(3)

        self.round_counter += 1
        self.active_walls = []
        
        if self.phase in (1, 2):
            w = self.pulse_walls[self.pulse_seq_idx % len(self.pulse_walls)]
            self.active_walls.append(w)
            self.pulse_seq_idx += 1
        else: # Phase 3: Chaos
            if random.random() < 0.25 and len(self.pulse_walls) > 1:
                ws = random.sample(self.pulse_walls, 2)
                self.active_walls.extend(ws)
            else:
                self.active_walls.append(random.choice(self.pulse_walls))

        for ch in self.active_walls:
            num_targets = 1
            if self.phase == 2: num_targets = random.choice([1, 2])
            elif self.phase == 3: num_targets = random.choice([1, 2, 2]) # chance of 2 targets
            
            t_idxs = random.sample(range(1, 11), num_targets)
            for i in t_idxs:
                self.active_targets.add((ch, i))

    def render_leds(self):
        new_leds = {}
        now = time.time()
        
        with game_lock:
            state = self.state
            phase = self.phase
            g_col = self.get_global_color()
            a_walls = list(self.active_walls)
            a_targets = set(self.active_targets)
            b_time = self.beat_time
            win_len = self.window

        if state == "LOBBY":
            val = int(abs(math.sin(now)) * 100)
            col = (val, val, int(val*1.5))
            for c in range(1, NUM_CHANNELS+1):
                new_leds[(c, 0)] = col
                for i in range(1, 11): new_leds[(c, i)] = col

        elif state == "PRE_START":
            elapsed = now - self.pre_start_time
            remaining = int(self.pre_start_timer - elapsed)
            b = int(abs(math.sin(now * np.pi * 2)) * 255)
            col = (0, b, b) # Cyan pulsing countdown
            for c in range(1, NUM_CHANNELS+1):
                new_leds[(c, 0)] = col
                for i in range(1, 11): 
                    new_leds[(c, i)] = col if i <= remaining * 2 else (0,0,0)

        elif state == "WIN":
            for c in range(1, NUM_CHANNELS+1):
                new_leds[(c, 0)] = (0, 0, 0)
                for i in range(1, 11):
                    new_leds[(c, i)] = (0, int(abs(math.sin(now*3 + c + i))*255), 0)

        elif state == "LOSE":
            col = (255, 0, 0) if int(now*5) % 2 == 0 else (50, 0, 0)
            for c in range(1, NUM_CHANNELS+1):
                new_leds[(c, 0)] = col
                for i in range(1, 11): new_leds[(c, i)] = (0,0,0)

        elif state == "PLAYING":
            for c in range(1, NUM_CHANNELS+1):
                if c in a_walls:
                    frac = 1.0 - max(0, min(1, (now - b_time) / win_len))
                    new_leds[(c, 0)] = (
                        int(g_col[0] + frac * (255 - g_col[0])),
                        int(g_col[1] + frac * (255 - g_col[1])),
                        int(g_col[2] + frac * (255 - g_col[2]))
                    )
                else:
                    new_leds[(c, 0)] = (int(g_col[0]*0.2), int(g_col[1]*0.2), int(g_col[2]*0.2))

                blink = int(abs(math.sin(now * 10)) * 155 + 100) # Pulsare de la 100 la 255
                for i in range(1, 11):
                    # ȚINTELE BUNE = VERDE PULSANT!
                    new_leds[(c, i)] = (0, blink, 0) if (c, i) in a_targets else (0, 0, 0)

        with game_lock:
            if new_leds != self.led_state_cache:
                self.net.send_frame(new_leds)
                self.led_state_cache = new_leds

    def main_loop(self):
        while self.net.running:
            self.update()
            self.render_leds()
            time.sleep(0.01)

# ── UI Elements Tkinter ───────────────────────────────────────────────────────
def rgb_hex(r, g, b): return f"#{int(r):02x}{int(g):02x}{int(b):02x}"

class ExteriorWindow(tk.Toplevel):
    def __init__(self, master, game: GameLogic):
        super().__init__(master)
        self.game = game
        self.title("THE RITUAL — Panou Exterior Setup")
        self.configure(bg="#111116")
        
        if len(MONITORS) >= 2: m_ext = MONITORS[1]
        else: m_ext = MONITORS[0] if MONITORS else {'x':0, 'y':0, 'w':800, 'h':600}
        
        self.geometry(f"{m_ext['w']}x{m_ext['h']}+{m_ext['x']}+{m_ext['y']}")
        self.after(200, lambda: self.attributes('-fullscreen', True))
        self.bind('<Escape>', lambda e: self.attributes('-fullscreen', False))
        
        self._build()
        self._tick()

    def _build(self):
        BG     = "#111116"
        ACCENT = "#8822ff"
        GOLD   = "#ffcc00"
        WHITE  = "#e8e8ff"
        GRAY   = "#2a2a44"

        f_title = tkfont.Font(family="Arial", size=24, weight="bold")
        f_sub   = tkfont.Font(family="Arial", size=11)
        f_btn   = tkfont.Font(family="Arial", size=15, weight="bold")
        f_num   = tkfont.Font(family="Arial", size=26, weight="bold")
        
        tk.Label(self, text="👁  T H E   R I T U A L  👁", font=f_title, bg=BG, fg=ACCENT).pack(pady=(22, 2))
        tk.Label(self, text="Panou de Setup - Camera Evil Eye", font=f_sub, bg=BG, fg="#666688").pack()
        tk.Frame(self, bg=ACCENT, height=2).pack(fill=tk.X, padx=50, pady=10)

        # Pregătiri Menu
        frm = tk.Frame(self, bg=BG)
        frm.pack(pady=10)
        
        tk.Label(frm, text="JUCĂTORI:", font=f_sub, bg=BG, fg=WHITE).grid(row=0, column=0, padx=16, sticky="e")
        tk.Button(frm, text="−", font=f_num, bg=GRAY, fg=WHITE, width=2, relief="flat", cursor="hand2", command=self._dec).grid(row=0, column=1, padx=4)
        
        self.lbl_num = tk.Label(frm, text=str(self.game.num_players), font=f_num, bg=BG, fg=GOLD, width=3)
        self.lbl_num.grid(row=0, column=2)
        
        tk.Button(frm, text="+", font=f_num, bg=GRAY, fg=WHITE, width=2, relief="flat", cursor="hand2", command=self._inc).grid(row=0, column=3, padx=4)

        tk.Frame(self, bg=ACCENT, height=1).pack(fill=tk.X, padx=50, pady=12)

        frm_btns = tk.Frame(self, bg=BG)
        frm_btns.pack(pady=10)

        self.btn_start   = tk.Button(frm_btns, text="▶  START", font=f_btn, bg="#008844", fg="white", width=12, height=2, relief="flat", cursor="hand2", command=self._start)
        self.btn_start.grid(row=0, column=0, padx=10)

        self.btn_restart = tk.Button(frm_btns, text="↺  RESTART", font=f_btn, bg="#aa6600", fg="white", width=12, height=2, relief="flat", cursor="hand2", command=self._restart)
        self.btn_restart.grid(row=0, column=1, padx=10)

        self.btn_quit    = tk.Button(frm_btns, text="✕  OPREȘTE", font=f_btn, bg="#aa1133", fg="white", width=12, height=2, relief="flat", cursor="hand2", command=self._quit)
        self.btn_quit.grid(row=0, column=2, padx=10)

    def _inc(self):
        with game_lock:
            if self.game.state == "LOBBY" and self.game.num_players < 10:
                self.game.num_players += 1
                self.lbl_num.config(text=str(self.game.num_players))

    def _dec(self):
        with game_lock:
            if self.game.state == "LOBBY" and self.game.num_players > 2:
                self.game.num_players -= 1
                self.lbl_num.config(text=str(self.game.num_players))

    def _start(self):
        with game_lock:
            if self.game.state == "LOBBY": self.game.reset()

    def _restart(self):
        with game_lock: self.game.reset()

    def _quit(self):
        with game_lock:
            self.game.state = "LOBBY"
            self.game.snd.stop_bg()

    def _tick(self):
        with game_lock: s = self.game.state
        self.btn_start.config(state=tk.NORMAL if s == "LOBBY" else tk.DISABLED, bg="#008844" if s == "LOBBY" else "#333333")
        self.btn_restart.config(state=tk.NORMAL if s != "LOBBY" else tk.DISABLED, bg="#aa6600" if s != "LOBBY" else "#333333")
        self.after(200, self._tick)

class InteriorWindow(tk.Tk):
    def __init__(self, game: GameLogic):
        super().__init__()
        self.game = game
        self.title("THE RITUAL — HUD Interior")
        self.configure(bg="#050505")
        
        m_int = MONITORS[0] if MONITORS else {'x':0, 'y':0, 'w':1920, 'h':1080}
        self.geometry(f"{m_int['w']}x{m_int['h']}+{m_int['x']}+{m_int['y']}")
        self.after(200, lambda: self.attributes('-fullscreen', True))
        self.bind('<Escape>', lambda e: self.attributes('-fullscreen', False))
        
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        
        self._build()
        self._tick()

    def _on_close(self):
        with game_lock: self.game.net.close()
        self.destroy()

    def _build(self):
        BG = "#050505"
        f_round  = tkfont.Font(family="Consolas", size=20, weight="bold")
        f_title  = tkfont.Font(family="Consolas", size=48, weight="bold")
        f_alert  = tkfont.Font(family="Consolas", size=60, weight="bold")
        
        top = tk.Frame(self, bg="#000000")
        top.pack(fill=tk.X)
        self.lbl_phase = tk.Label(top, text="THE RITUAL", font=f_round, bg="black", fg="#8822ff")
        self.lbl_phase.pack(side=tk.LEFT, padx=30, pady=15)
        
        self.lbl_np = tk.Label(top, text="👥 2", font=f_round, bg="black", fg="#555")
        self.lbl_np.pack(side=tk.RIGHT, padx=30, pady=15)

        main = tk.Frame(self, bg=BG)
        main.pack(fill=tk.BOTH, expand=True)
        
        self.lbl_misses = tk.Label(main, text="GREȘELI: 0 / 5", font=f_title, bg=BG, fg="#0f0")
        self.lbl_misses.pack(pady=(80, 20))
        
        self.lbl_combo = tk.Label(main, text="COMBO: 0 / 12", font=f_title, bg=BG, fg="#0ff")
        self.lbl_combo.pack()
        
        # SPLASH SCREENS ptr Game Over / Win
        self.frm_end = tk.Frame(self, bg=BG)
        self.lbl_end = tk.Label(self.frm_end, text="", font=f_alert, bg=BG, fg="white")
        self.lbl_end.pack(expand=True)

    def _tick(self):
        if not self.game.net.running:
            self.destroy()
            return
            
        with game_lock:
            s       = self.game.state
            ph      = self.game.phase
            miss    = self.game.misses_total
            lim     = self.game.misses_limit
            combo   = self.game.consecutive_hits
            tgt     = self.game.win_target
            np      = self.game.num_players
            rgb_col = self.game.get_global_color()
            
        self.lbl_np.config(text=f"👥 {np}")
            
        if s in ("WIN", "LOSE"):
            self.frm_end.place(relx=0, rely=0, relwidth=1.0, relheight=1.0)
            self.frm_end.lift()
            if s == "LOSE":
                self.frm_end.config(bg="#300000")
                self.lbl_end.config(text="GAME OVER\nEȘEC ÎN RITUAL", bg="#300000", fg="#ff0000")
            else:
                self.frm_end.config(bg="#002200")
                self.lbl_end.config(text="VICTORIE!\nRITUAL COMPLETAT.", bg="#002200", fg="#00ff00")
        else:
            self.frm_end.place_forget()
            p_txt = "LOBBY" if s == "LOBBY" else f"FAZA {ph}"
            self.lbl_phase.config(text=p_txt)
            self.lbl_misses.config(text=f"GREȘELI: {miss} / {lim}", fg=rgb_hex(*rgb_col))
            self.lbl_combo.config(text=f"COMBO: {combo} / {tgt}")

        self.after(50, self._tick)

if __name__ == "__main__":
    MONITORS = detect_monitors()
    game = GameLogic()
    threading.Thread(target=game.main_loop, daemon=True).start()
    
    root = InteriorWindow(game)
    exterior = ExteriorWindow(root, game)
    root.mainloop()
