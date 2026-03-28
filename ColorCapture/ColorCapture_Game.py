"""
Color Capture – Territory capture game for the 16x32 LED matrix (antigravity)
=============================================================================

Rules:
- 2–4 players, each with a unique colour.
- Players spawn at fixed edge positions at the start of every round.
- When a sensor press at (x, y) is detected, the NEAREST player is moved there
  and that cell becomes that player's colour (proximity logic, since the hardware
  can't identify *who* stepped on a tile).
- Pressing a cell already owned by another player converts it instantly.
- Round duration: 2 minutes → 10-second break w/ leaderboard → round 2 → winner.

Network (Protocol v11):
  Send : UDP broadcast → 255.255.255.255 : 4626
  Recv : UDP listen    ← 0.0.0.0         : 7800
         Packet layout: 1373 bytes, byte[0]=0x88
         Channel k data starts at byte  2 + k*171
         Sensor pressed = 0xCC

Matrix layout:
  16 wide × 32 tall   (x=0-15, y=0-31)
  8 channels, 4 rows each  (channel = y // 4)
  Zig-zag: row_in_channel even → led = row_in_channel*16 + x
            row_in_channel odd  → led = row_in_channel*16 + (15-x)
  Wire format: GRB (Green, Red, Blue per byte)
"""

import socket
import time
import threading
import random
import math
import os
import json
import sys
import tkinter.font as tkfont

# Force UTF-8 output so Windows PowerShell doesn't show '?' for Unicode chars
if hasattr(sys.stdout, "reconfigure"):
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# ─────────────────────────────────────────────────────────────────────────────
# PyInstaller Paths & Configuration
# ─────────────────────────────────────────────────────────────────────────────

if getattr(sys, 'frozen', False):
    BUNDLE_DIR = sys._MEIPASS
    APP_DIR    = os.path.dirname(sys.executable)
else:
    BUNDLE_DIR = os.path.dirname(os.path.abspath(__file__))
    APP_DIR    = BUNDLE_DIR

_CFG_FILE = os.path.join(APP_DIR, "color_capture_config.json")

def _load_config():
    defaults = {
        "device_ip": "255.255.255.255",
        "send_port": 6668,
        "recv_port": 6669,
        "bind_ip":   "0.0.0.0",
    }
    try:
        if os.path.exists(_CFG_FILE):
            with open(_CFG_FILE, encoding="utf-8") as f:
                return {**defaults, **json.load(f)}
    except Exception:
        pass
    return defaults

CONFIG = _load_config()

# ─────────────────────────────────────────────────────────────────────────────
# Matrix / Protocol constants
# ─────────────────────────────────────────────────────────────────────────────

NUM_CHANNELS     = 8
LEDS_PER_CHANNEL = 64
FRAME_DATA_LENGTH = NUM_CHANNELS * LEDS_PER_CHANNEL * 3   # 1536 bytes

BOARD_WIDTH  = 16
BOARD_HEIGHT = 32

UDP_SEND_IP   = CONFIG["device_ip"]
UDP_SEND_PORT = CONFIG["send_port"]
UDP_RECV_PORT = CONFIG["recv_port"]

# ─────────────────────────────────────────────────────────────────────────────
# Colours  (R, G, B)
# ─────────────────────────────────────────────────────────────────────────────

BLACK   = (0,   0,   0)
WHITE   = (255, 255, 255)
GREY    = (80,  80,  80)

# Palette for up to 10 players – carefully chosen vivid colours
PLAYER_COLORS = [
    (255,  40,  40),   # 0 – Coral Red
    ( 40, 120, 255),   # 1 – Sky Blue
    ( 40, 210,  40),   # 2 – Lime Green
    (255, 200,   0),   # 3 – Gold
    (220,  40, 220),   # 4 – Violet
    (  0, 230, 200),   # 5 – Cyan-Teal
    (255, 120,   0),   # 6 – Orange
    (180, 255,  80),   # 7 – Yellow-Green
    (255,  80, 160),   # 8 – Hot Pink
    ( 80, 200, 255),   # 9 – Aqua
]

# Dim version used for territory cells (50 % brightness)
def dim(color, factor=0.45):
    return (int(color[0]*factor), int(color[1]*factor), int(color[2]*factor))

# ─────────────────────────────────────────────────────────────────────────────
# Game constants
# ─────────────────────────────────────────────────────────────────────────────

ROUND_DURATION     = 60.0  # seconds per round
BREAK_DURATION     = 10.0   # seconds between rounds
PRE_GAME_DURATION  = 10.0   # seconds for placing players
TOTAL_ROUNDS       = 2

# Spawn points = the 4 corners of the board, ordered for maximum separation.
# Player 0 always starts top-left, player 1 bottom-right (diagonal), etc.
#   2 players: corners 0+1  (diagonal)
#   3 players: corners 0+1+2
#   4 players: all 4 corners
CORNERS = [
    ( 0,              0),              # 0 top-left
    (BOARD_WIDTH - 1, BOARD_HEIGHT - 1),  # 1 bottom-right
    (BOARD_WIDTH - 1, 0),              # 2 top-right
    ( 0,              BOARD_HEIGHT - 1),  # 3 bottom-left
]

MIN_PLAYERS = 2
MAX_PLAYERS = 4

# ─────────────────────────────────────────────────────────────────────────────
# Utility: LED buffer helpers
# ─────────────────────────────────────────────────────────────────────────────

def set_led(buffer: bytearray, x: int, y: int, color: tuple):
    """Write a colour into the GRB frame buffer using the zig-zag channel map."""
    if not (0 <= x < BOARD_WIDTH and 0 <= y < BOARD_HEIGHT):
        return
    channel      = y // 4
    row_in_chan  = y % 4
    if row_in_chan % 2 == 0:
        led_index = row_in_chan * 16 + x
    else:
        led_index = row_in_chan * 16 + (15 - x)
    block_size = NUM_CHANNELS * 3
    offset = led_index * block_size + channel
    if offset + NUM_CHANNELS * 2 < len(buffer):
        buffer[offset]                  = color[1]   # G
        buffer[offset + NUM_CHANNELS]   = color[0]   # R
        buffer[offset + NUM_CHANNELS*2] = color[2]   # B


def decode_sensors(data: bytes) -> set:
    """Return a set of (x, y) board coordinates that are currently pressed."""
    pressed = set()
    for ch in range(NUM_CHANNELS):
        base = 2 + ch * 171
        if base + 1 + 64 > len(data):
            break
        for led_idx in range(64):
            if data[base + 1 + led_idx] == 0xCC:
                row_in_chan = led_idx // 16
                raw_col     = led_idx % 16
                if row_in_chan % 2 == 0:
                    x = raw_col
                else:
                    x = 15 - raw_col
                y = ch * 4 + row_in_chan
                pressed.add((x, y))
    return pressed


# ─────────────────────────────────────────────────────────────────────────────
# Small 3×5 font – digits 0-9 + limited characters (for score display)
# ─────────────────────────────────────────────────────────────────────────────

# Each glyph is a list of (col, row) pixel offsets (col 0-2, row 0-4)
_FONT3x5 = {
    '0': [(0,0),(1,0),(2,0),(0,1),(2,1),(0,2),(2,2),(0,3),(2,3),(0,4),(1,4),(2,4)],
    '1': [(1,0),(1,1),(1,2),(1,3),(1,4)],
    '2': [(0,0),(1,0),(2,0),(2,1),(0,2),(1,2),(2,2),(0,3),(0,4),(1,4),(2,4)],
    '3': [(0,0),(1,0),(2,0),(2,1),(1,2),(2,2),(2,3),(0,4),(1,4),(2,4)],
    '4': [(0,0),(2,0),(0,1),(2,1),(0,2),(1,2),(2,2),(2,3),(2,4)],
    '5': [(0,0),(1,0),(2,0),(0,1),(0,2),(1,2),(2,2),(2,3),(0,4),(1,4),(2,4)],
    '6': [(0,0),(1,0),(0,1),(0,2),(1,2),(2,2),(0,3),(2,3),(0,4),(1,4),(2,4)],
    '7': [(0,0),(1,0),(2,0),(2,1),(2,2),(1,3),(1,4)],
    '8': [(0,0),(1,0),(2,0),(0,1),(2,1),(0,2),(1,2),(2,2),(0,3),(2,3),(0,4),(1,4),(2,4)],
    '9': [(0,0),(1,0),(2,0),(0,1),(2,1),(0,2),(1,2),(2,2),(2,3),(0,4),(1,4),(2,4)],
    'P': [(0,0),(1,0),(2,0),(0,1),(2,1),(0,2),(1,2),(2,2),(0,3),(0,4)],
    ':': [(1,1),(1,3)],
    '-': [(0,2),(1,2),(2,2)],
    ' ': [],
}

def draw_text(buffer: bytearray, text: str, ox: int, oy: int, color: tuple):
    """Render a string using the 3×5 font starting at (ox, oy)."""
    cx = ox
    for ch in text:
        glyph = _FONT3x5.get(ch, _FONT3x5.get(' ', []))
        for (dc, dr) in glyph:
            set_led(buffer, cx + dc, oy + dr, color)
        cx += 4   # 3 px wide + 1 px gap


# ─────────────────────────────────────────────────────────────────────────────
# SOUND MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class SoundManager:
    """Generează audio sintetic pentru ColorCapture. Folosește numpy și pygame."""
    SR = 44100
    
    def __init__(self):
        self._ok = False
        self._prev_state = None
        self._last_timer_sec = -1
        try:
            import pygame as _pg
            import numpy as _np
            _pg.mixer.pre_init(self.SR, -16, 2, 512)
            if not _pg.get_init():
                _pg.init()
            _pg.mixer.init(frequency=self.SR, size=-16, channels=2, buffer=512)
            self._pg = _pg
            self._np = _np
            
            _pg.mixer.set_num_channels(8)
            self._ch_bg = _pg.mixer.Channel(0)
            self._ch_sfx = _pg.mixer.Channel(1)
            self._ch_tmr = _pg.mixer.Channel(2)
            
            self._build()
            self._ok = True
            print("[Audio] OK — ColorCapture procedural sounds generated.")
        except Exception as e:
            print(f"[Audio] Funcționalitatea audio nu s-a putut inițializa: {e}")
            
    def _mix(self, *arrs):
        n = max(len(a) for a in arrs)
        out = self._np.zeros(n)
        for a in arrs:
            out[:len(a)] += a
        return self._np.clip(out, -1.0, 1.0)
        
    def _concat(self, *arrs):
        return self._np.concatenate(arrs)
        
    def _to_snd(self, wave):
        arr = (self._np.clip(wave, -1.0, 1.0) * 32767).astype(self._np.int16)
        stereo = self._np.column_stack([arr, arr]).copy()
        return self._pg.sndarray.make_sound(stereo)
        
    def _sine(self, freq, dur, vol=0.5, decay=0.0):
        t = self._np.linspace(0, dur, int(self.SR * dur), endpoint=False)
        w = self._np.sin(2 * self._np.pi * freq * t) * vol
        if decay > 0: w *= self._np.exp(-decay * t)
        return w
        
    def _square_decay(self, freq, dur, vol=0.3, decay=5.0):
        t = self._np.linspace(0, dur, int(self.SR * dur), endpoint=False)
        w = self._np.sign(self._np.sin(2 * self._np.pi * freq * t)) * vol
        w *= self._np.exp(-decay * t)
        return w

    def _white_noise(self, dur, vol=0.3, decay=0.0):
        t = self._np.linspace(0, dur, int(self.SR * dur), endpoint=False)
        w = (self._np.random.random(len(t)) * 2 - 1) * vol
        if decay > 0: w *= self._np.exp(-decay * t)
        return w
        
    def _build(self):
        np = self._np
        pg = self._pg
        _dir = os.path.join(BUNDLE_DIR, "sounds")
        
        def _load(fname):
            """Încarcă fișier real dacă există, altfel None."""
            path = os.path.join(_dir, fname)
            if os.path.exists(path):
                try:
                    return pg.mixer.Sound(path)
                except Exception as e:
                    print(f"[Audio] Eroare citire {fname}: {e}")
            return None
        
        # 1. Beep timer
        self.snd_beep = _load("timer.mp3") or _load("beep.mp3")
        if not self.snd_beep:
            self.snd_beep = self._to_snd(self._sine(880.0, 0.1, 0.45))
        
        # 2. Gong
        self.snd_gong = _load("gong.wav") or _load("gong.mp3")
        if not self.snd_gong:
            crash = self._white_noise(3.0, vol=0.7, decay=3.0)
            bass_gong = sum(self._sine(f, 3.0, vol=0.7/idx, decay=1.5*idx) for idx, f in enumerate([60, 120, 240, 360, 480], 1))
            self.snd_gong = self._to_snd(self._mix(crash, bass_gong))
        
        # 3. Trompeta sfarsit
        self.snd_trumpet = _load("trumpet.wav") or _load("trumpet.mp3") or _load("tada.mp3")
        if not self.snd_trumpet:
            n1 = self._square_decay(523.25, 0.15, vol=0.3, decay=1.0)
            n2 = self._square_decay(659.25, 0.15, vol=0.3, decay=1.0)
            n3 = self._square_decay(783.99, 0.15, vol=0.3, decay=1.0)
            n4 = self._square_decay(1046.50, 1.2, vol=0.4, decay=1.5)
            self.snd_trumpet = self._to_snd(self._concat(n1, n2, n3, n4))
        
        # 4. Aplauze
        self.snd_applause = _load("applause.wav") or _load("applause.mp3") or _load("cheer.mp3")
        if not self.snd_applause:
            noise = self._white_noise(4.0, 0.25)
            swells = np.sin(np.linspace(0, 15*np.pi, len(noise))) * 0.3 + 0.7
            fade = np.linspace(1.2, 0.0, len(noise))
            self.snd_applause = self._to_snd(noise * swells * fade)
        
        # 5. BGM loop chase
        self.snd_bgm = _load("bgm.wav") or _load("bgm.mp3") or _load("chase.mp3") or _load("music.mp3")
        if not self.snd_bgm:
            tempo = 140.0
            beat_len = 60.0 / tempo
            seq_len = int(self.SR * beat_len * 4) # 1 masura
            bgm_wave = np.zeros(seq_len)
            
            # Patru pe sfert - kick
            for b in range(4):
                idx = int(b * self.SR * beat_len)
                kick = self._sine(55.0, 0.2, vol=0.8, decay=15.0) + self._white_noise(0.05, 0.3, 30.0)
                bgm_wave[idx:idx+len(kick)] += kick
                
            # Bas inaispezece
            arp = [110.0, 110.0, 130.81, 110.0, 146.83, 110.0, 130.81, 98.0]
            step_len = beat_len / 4.0
            for i in range(16):
                f = arp[i % len(arp)]
                idx = int(i * self.SR * step_len)
                note = self._square_decay(f, step_len, vol=0.2, decay=10.0)
                bgm_wave[idx:idx+len(note)] += note
                
            self.snd_bgm = self._to_snd(self._mix(bgm_wave))
        
    def _play_bg(self, snd):
        if self._ch_bg:
            self._ch_bg.stop()
            self._ch_bg.play(snd, loops=-1)
            
    def update(self, state, pre_game_left):
        if not self._ok: return
        
        if state != self._prev_state:
            # Transitions
            if   state in ("LOBBY", "PRE_GAME"):
                self._ch_bg.stop()
                self._last_timer_sec = -1
            elif state == "PLAYING":
                self._ch_tmr.stop()
                self._ch_sfx.play(self.snd_gong)
                self._play_bg(self.snd_bgm)
            elif state == "BREAK":
                self._ch_bg.stop()
                self._ch_sfx.play(self.snd_trumpet)
            elif state == "FINAL":
                self._ch_bg.stop()
                self._ch_sfx.play(self.snd_applause)
                
        # Timer Beep Logica
        if state == "PRE_GAME" and pre_game_left >= 0:
            sec = int(math.ceil(pre_game_left))
            if sec != self._last_timer_sec and sec > 0:
                self._ch_tmr.play(self.snd_beep)
                self._last_timer_sec = sec
                
        self._prev_state = state

sound_mgr = SoundManager()

# ─────────────────────────────────────────────────────────────────────────────
# Player
# ─────────────────────────────────────────────────────────────────────────────

class Player:
    def __init__(self, player_id: int, color: tuple, spawn: tuple):
        self.id    = player_id
        self.color = color
        self.spawn = spawn          # (x, y)
        self.x, self.y = spawn
        self.total_score = 0        # cumulative across rounds
        self.round_cells = 0        # cells owned this round (computed at round end)

    def reset_to_spawn(self):
        self.x, self.y = self.spawn
        self.round_cells = 0


# ─────────────────────────────────────────────────────────────────────────────
# ColorCaptureGame
# ─────────────────────────────────────────────────────────────────────────────

class ColorCaptureGame:
    """
    States:
      LOBBY      – waiting for 'start' command
      PLAYING    – active round
      BREAK      – inter-round break, show leaderboard
      FINAL      – game over, display overall winner
    """

    def __init__(self, num_players: int = 2):
        self.lock = threading.RLock()
        self.running = True

        self._num_players = max(2, min(10, num_players))
        self.players: list[Player] = []

        # Board: (x,y) → player_id or None (None = uncaptured)
        self.board: dict[tuple, int | None] = {}

        self.state        = "LOBBY"
        self.current_round = 0
        self.total_rounds  = TOTAL_ROUNDS
        self.round_start_time = 0.0
        self.break_start_time = 0.0
        self.pre_game_start_time = 0.0

        # Sensor state
        self._prev_pressed: set  = set()
        self._cur_pressed:  set  = set()

        # Animation / display helpers
        self._anim_t = 0
        self._flash_phase = 0
        self._flash_timer  = 0.0
        self._round_winner: Player | None = None   # winner of last round
        self._final_winner: Player | None = None   # overall winner

        # Smooth player glow
        self._player_glow = {}   # player_id -> glow intensity 0-1

        # Spawn-preview: how many corners to highlight in LOBBY (set by launcher)
        self._preview_count = 0

    # ─── Setup ───────────────────────────────────────────────────────────────

    def _setup_players(self):
        self.players = []
        for i in range(self._num_players):
            color = PLAYER_COLORS[i % len(PLAYER_COLORS)]
            spawn = CORNERS[i]   # each player gets their own corner
            self.players.append(Player(i, color, spawn))

    def _reset_board(self):
        self.board = {}
        for y in range(BOARD_HEIGHT):
            for x in range(BOARD_WIDTH):
                self.board[(x, y)] = None

    def _reset_players_to_spawn(self):
        for p in self.players:
            p.reset_to_spawn()

    def set_preview(self, n: int):
        """Show N spawn-point markers on the matrix during the launcher phase."""
        with self.lock:
            self._preview_count = max(0, min(MAX_PLAYERS, n))

    # ─── Public API ──────────────────────────────────────────────────────────

    def start_game(self, num_players: int | None = None, num_rounds: int | None = None):
        with self.lock:
            if num_players is not None:
                self._num_players = max(MIN_PLAYERS, min(MAX_PLAYERS, num_players))
            if num_rounds is not None:
                self.total_rounds = max(1, min(10, num_rounds))
            self._setup_players()
            self.current_round = 0
            for p in self.players:
                p.total_score = 0
            self._start_round()

    def restart(self):
        """Restart from round 1, keeping the same player count."""
        with self.lock:
            self._setup_players()
            self.current_round = 0
            for p in self.players:
                p.total_score = 0
            self._start_round()

    # ─── Internal helpers ────────────────────────────────────────────────────

    def _start_round(self):
        self.current_round += 1
        self._reset_board()
        self._reset_players_to_spawn()
        self.pre_game_start_time = time.time()
        self.state = "PRE_GAME"
        print(f"[ColorCapture] Round {self.current_round} PRE-GAME "
              f"({self._num_players} players). Get ready!")

    def _end_round(self):
        """Count cells, update scores, decide what comes next."""
        # Count owned cells per player
        cell_counts = {p.id: 0 for p in self.players}
        for owner in self.board.values():
            if owner is not None:
                cell_counts[owner] = cell_counts.get(owner, 0) + 1
        for p in self.players:
            p.round_cells = cell_counts.get(p.id, 0)
            p.total_score += p.round_cells

        # Find round winner
        self._round_winner = max(self.players, key=lambda p: p.round_cells)

        print(f"[ColorCapture] Round {self.current_round} ended.")
        for p in sorted(self.players, key=lambda p: p.round_cells, reverse=True):
            print(f"  Player {p.id} ({_color_name(p.color)}): "
                  f"{p.round_cells} cells | total {p.total_score}")

        self.break_start_time = time.time()
        self.state = "BREAK"

    def _start_final(self):
        """Show final result after all rounds are done."""
        self._final_winner = max(self.players, key=lambda p: p.total_score)
        self.state = "FINAL"
        print(f"[ColorCapture] GAME OVER. "
              f"Winner: Player {self._final_winner.id} "
              f"({_color_name(self._final_winner.color)}) "
              f"with {self._final_winner.total_score} pts.")

    # ─── Proximity / Input logic ──────────────────────────────────────────────

    def _proximity_threshold(self) -> int:
        """Dynamic squared-distance threshold.

        Fewer players -> each person covers more of the board -> allow larger radius.
        4 players: radius ~8 cells  (sq=64)
        2 players: radius ~12 cells (sq=144)
        """
        n = max(MIN_PLAYERS, len(self.players))
        return max(64, min(144, 64 + (MAX_PLAYERS - n) * 40))

    def _nearest_player(self, x: int, y: int) -> 'Player | None':
        """Return the player whose current position is closest to (x, y),
        or None if every player is too far away (beyond dynamic threshold)."""
        threshold = self._proximity_threshold()
        best, best_d = None, threshold + 1
        for p in self.players:
            d = (p.x - x) ** 2 + (p.y - y) ** 2
            if d < best_d:
                best, best_d = p, d
        return best

    def _handle_press(self, x: int, y: int):
        """Process a fresh sensor press at (x, y).

        The player's colour is always their own fixed colour and never changes.
        Only the ownership of the cell on the board changes.
        """
        if not self.players:
            return
        player = self._nearest_player(x, y)
        if player is None:
            return   # press is too far from any player – ignore
        # Move that player to the pressed position
        player.x = x
        player.y = y
        # Capture the cell with that player's id (their colour stays the same always)
        self.board[(x, y)] = player.id
        # Glow feedback
        self._player_glow[player.id] = 1.0

    def update_sensors(self, pressed: set):
        """Called by the network thread with the current set of pressed (x,y)."""
        with self.lock:
            # Detect fresh presses (rising edge)
            new_presses = pressed - self._prev_pressed
            self._prev_pressed = pressed
            if self.state == "PLAYING":
                for (x, y) in new_presses:
                    self._handle_press(x, y)

    # ─── Game tick ───────────────────────────────────────────────────────────

    def tick(self):
        with self.lock:
            self._anim_t += 1

            if self.state == "LOBBY":
                return

            if self.state == "PRE_GAME":
                elapsed = time.time() - self.pre_game_start_time
                if elapsed >= PRE_GAME_DURATION:
                    # Trecere efectivă la joc
                    self.round_start_time = time.time()
                    self.state = "PLAYING"
                    # Acum validăm celulele de start
                    for p in self.players:
                        self.board[(p.x, p.y)] = p.id
                    print(f"[ColorCapture] Round {self.current_round} STARTED!")

            elif self.state == "PLAYING":
                elapsed = time.time() - self.round_start_time
                if elapsed >= ROUND_DURATION:
                    self._end_round()
                # Decay glow
                for pid in list(self._player_glow):
                    self._player_glow[pid] = max(0.0,
                                                 self._player_glow[pid] - 0.05)

            elif self.state == "BREAK":
                elapsed = time.time() - self.break_start_time
                if elapsed >= BREAK_DURATION:
                    if self.current_round < self.total_rounds:
                        self._start_round()
                    else:
                        self._start_final()

            elif self.state == "FINAL":
                pass   # stays until restart

    # ─── Render ──────────────────────────────────────────────────────────────

    def render(self) -> bytearray:
        buffer = bytearray(FRAME_DATA_LENGTH)
        with self.lock:
            if self.state == "LOBBY":
                self._render_lobby(buffer)
            elif self.state == "PRE_GAME":
                self._render_pre_game(buffer)
            elif self.state == "PLAYING":
                self._render_playing(buffer)
            elif self.state == "BREAK":
                self._render_break(buffer)
            elif self.state == "FINAL":
                self._render_final(buffer)
        return buffer

    # ── Lobby render ────────────────────────────────────────────

    def _render_lobby(self, buf: bytearray):
        """Animate a colour-wave idle screen.

        When _preview_count > 0 (set by the launcher), the spawn corners
        for the selected number of players pulse on top of the animation.
        """
        t = self._anim_t * 0.06
        for y in range(BOARD_HEIGHT):
            for x in range(BOARD_WIDTH):
                hue = (x / BOARD_WIDTH + y / BOARD_HEIGHT * 0.5 + t) % 1.0
                r, g, b = _hsv_to_rgb(hue, 0.9, 0.3)
                set_led(buf, x, y, (r, g, b))

        # Spawn-point preview: draw a pulsing cross at each active corner
        n = self._preview_count
        if n > 0:
            # Slow pulse for the crosses (independent of the wave)
            pulse = (math.sin(self._anim_t * 0.18) + 1) / 2   # 0 -> 1
            for i in range(n):
                col_rgb = PLAYER_COLORS[i]
                px, py  = CORNERS[i]
                # Full-brightness centre pixel
                bright = tuple(int(c * (0.6 + 0.4 * pulse)) for c in col_rgb)
                set_led(buf, px, py, bright)
                # Dimmer arms of the cross
                arm = tuple(int(c * 0.45 * pulse) for c in col_rgb)
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1),
                               (-1,-1), (1,-1), (-1, 1), (1, 1)]:
                    set_led(buf, px + dx, py + dy, arm)

    # ── Pre-Game render ─────────────────────────────────────────

    def _render_pre_game(self, buf: bytearray):
        # Fundal intunecat
        for y in range(BOARD_HEIGHT):
            for x in range(BOARD_WIDTH):
                set_led(buf, x, y, (4, 4, 8))

        # Pulse spawn points rapid
        pulse_spawn = (math.sin(self._anim_t * 0.3) + 1) / 2
        for p in self.players:
            col_rgb = p.color
            px, py  = p.spawn
            bright = tuple(int(c * (0.6 + 0.4 * pulse_spawn)) for c in col_rgb)
            set_led(buf, px, py, bright)
            arm = tuple(int(c * 0.45 * pulse_spawn) for c in col_rgb)
            for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1),
                           (-1,-1), (1,-1), (-1, 1), (1, 1)]:
                set_led(buf, px + dx, py + dy, arm)

        # Scris countdown
        elapsed = time.time() - self.pre_game_start_time
        remaining = max(0, int(math.ceil(PRE_GAME_DURATION - elapsed)))
        s = str(remaining)
        # desenam in centru (latime caracter=3px, gap=1. pt '10' -> latime 7px. centru 16= x:4)
        draw_text(buf, s, (BOARD_WIDTH - 4 * len(s)) // 2 + 1, 14, WHITE)

    # ── Playing render ───────────────────────────────────────────

    def _render_playing(self, buf: bytearray):
        # 1. Draw territory (dim version of each player's fixed colour)
        for (x, y), owner_id in self.board.items():
            if owner_id is None:
                color = BLACK
            else:
                base_col = self.players[owner_id].color
                color = dim(base_col, 0.50)   # slightly brighter so territory is readable
            set_led(buf, x, y, color)

        # 2. Draw player positions (bright dot with glow)
        #    Each player's colour is ALWAYS their own fixed colour – it never changes.
        for p in self.players:
            glow = self._player_glow.get(p.id, 0.0)
            # Soft ring of glow around player
            if glow > 0.05:
                ring_col = tuple(int(c * glow * 0.55) for c in p.color)
                for dx, dy in [(-1,0),(1,0),(0,-1),(0,1)]:
                    nx, ny = p.x + dx, p.y + dy
                    if 0 <= nx < BOARD_WIDTH and 0 <= ny < BOARD_HEIGHT:
                        cur = _get_led(buf, nx, ny)
                        blended = tuple(min(255, cur[i] + ring_col[i]) for i in range(3))
                        set_led(buf, nx, ny, blended)
            # Player dot – always full brightness of their own colour
            set_led(buf, p.x, p.y, p.color)

    # ── Break render ─────────────────────────────────────────────

    def _render_break(self, buf: bytearray):
        """Show a vertical bar-chart leaderboard that works for 2–10 players.

        Layout (16 wide × 32 tall):
          - Each player gets a 1-px wide column (plus 1px gap) centred on screen.
            Up to 10 players → 10 columns × 2px = 20px, fits in 16 with overlap
            so we auto-size: col_w = max(1, 16 // n_players).
          - Bar height is proportional to cell count vs. max cells in this round.
          - Winner column pulses brighter.
          - Bottom row = countdown bar (white shrinking left→right).
        """
        # Dark background
        for y in range(BOARD_HEIGHT):
            for x in range(BOARD_WIDTH):
                set_led(buf, x, y, (4, 4, 8))

        sorted_players = sorted(self.players,
                                key=lambda p: p.round_cells, reverse=True)
        n     = len(sorted_players)
        max_c = max((p.round_cells for p in sorted_players), default=1) or 1

        # Column layout: give every player a slot; fit within BOARD_WIDTH
        col_w    = max(1, BOARD_WIDTH // n)             # px per player column
        total_w  = col_w * n
        x_offset = (BOARD_WIDTH - total_w) // 2         # centre horizontally

        usable_h = BOARD_HEIGHT - 2   # reserve bottom 2 rows for countdown bar
        pulse    = (math.sin(self._anim_t * 0.25) + 1) / 2   # 0–1

        for rank, p in enumerate(sorted_players):
            bar_h  = max(1, round(p.round_cells / max_c * usable_h))
            x_col  = x_offset + rank * col_w

            is_winner = (p is self._round_winner)
            brightness = (0.55 + 0.45 * pulse) if is_winner else 0.40
            col_dim  = dim(p.color, brightness)
            col_full = p.color

            # Draw the bar from the bottom up
            for dy in range(bar_h):
                py = usable_h - 1 - dy
                for dx in range(col_w - (1 if n > 2 else 0)):  # 1px gap when >2 players
                    set_led(buf, x_col + dx, py, col_dim)

            # Bright top pixel of each bar
            top_y = usable_h - bar_h
            for dx in range(col_w - (1 if n > 2 else 0)):
                set_led(buf, x_col + dx, top_y, col_full)

        # Show spawn points clearly so players know exactly where to return
        # (Only if there is another round to play)
        if self.current_round < self.total_rounds:
            pulse_spawn = (math.sin(self._anim_t * 0.18) + 1) / 2   # 0 -> 1
            for p in self.players:
                col_rgb = p.color
                px, py  = p.spawn
                # Full-brightness centre pixel
                bright = tuple(int(c * (0.6 + 0.4 * pulse_spawn)) for c in col_rgb)
                set_led(buf, px, py, bright)
                # Dimmer arms of the cross
                arm = tuple(int(c * 0.45 * pulse_spawn) for c in col_rgb)
                for dx, dy in [(-1, 0), (1, 0), (0, -1), (0, 1),
                               (-1,-1), (1,-1), (-1, 1), (1, 1)]:
                    set_led(buf, px + dx, py + dy, arm)

    # ── Final render ─────────────────────────────────────────────

    def _render_final(self, buf: bytearray):
        """Full-screen winner colour with pulsing effect."""
        winner = self._final_winner
        if winner is None:
            return

        pulse = (math.sin(self._anim_t * 0.12) + 1) / 2   # 0 → 1
        factor = 0.25 + 0.65 * pulse

        for y in range(BOARD_HEIGHT):
            for x in range(BOARD_WIDTH):
                c = tuple(int(winner.color[i] * factor) for i in range(3))
                set_led(buf, x, y, c)

        # Draw "W" glyph (WINNER) using 3×5 font
        draw_text(buf, "P" + str(winner.id + 1), 4, 12, WHITE)
        # Score row
        score_str = str(winner.total_score)
        draw_text(buf, score_str, 5, 20, WHITE)


# ─────────────────────────────────────────────────────────────────────────────
# Network Manager  (Protocol v11)
# ─────────────────────────────────────────────────────────────────────────────

class NetworkManager:
    def __init__(self, game: ColorCaptureGame):
        self.game    = game
        self.running = True
        self.sequence_number = 0

        # Send socket (broadcast)
        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_send.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        bind_ip = CONFIG.get("bind_ip", "0.0.0.0")
        if bind_ip and bind_ip != "0.0.0.0":
            try:
                self.sock_send.bind((bind_ip, 0))
                print(f"[Network] Send socket bound to {bind_ip}")
            except Exception as e:
                print(f"[Network] Warning: cannot bind send socket to {bind_ip}: {e}")

        # Receive socket
        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_recv.settimeout(0.5)
        try:
            self.sock_recv.bind(("0.0.0.0", UDP_RECV_PORT))
            print(f"[Network] Listening for sensor data on port {UDP_RECV_PORT}")
        except Exception as e:
            print(f"[Network] CRITICAL: cannot bind recv socket: {e}")
            self.running = False

    # ─── Send helpers (Protocol v11) ──────────────────────────────────────

    def _next_seq(self) -> int:
        self.sequence_number = (self.sequence_number + 1) & 0xFFFF
        if self.sequence_number == 0:
            self.sequence_number = 1
        return self.sequence_number

    def _sendto(self, packet: bytearray):
        target = (UDP_SEND_IP, UDP_SEND_PORT)
        loopback = ("127.0.0.1", UDP_SEND_PORT)
        try:
            self.sock_send.sendto(packet, target)
            self.sock_send.sendto(packet, loopback)
        except Exception:
            pass

    def send_packet(self, frame_data: bytearray):
        seq = self._next_seq()
        rand = lambda: random.randint(0, 127)

        # 1. Start Packet
        r1, r2 = rand(), rand()
        pkt = bytearray([0x75, r1, r2, 0x00, 0x08,
                          0x02, 0x00, 0x00, 0x33, 0x44,
                          (seq >> 8) & 0xFF, seq & 0xFF,
                          0x00, 0x00, 0x00])
        pkt += bytearray([0x0E, 0x00])
        self._sendto(pkt)

        # 2. FFF0 Packet
        r1, r2 = rand(), rand()
        fff0_payload = bytearray()
        for _ in range(NUM_CHANNELS):
            fff0_payload += bytes([(LEDS_PER_CHANNEL >> 8) & 0xFF,
                                    LEDS_PER_CHANNEL & 0xFF])
        fff0_internal = bytearray([0x02, 0x00, 0x00, 0x88, 0x77,
                                    0xFF, 0xF0,
                                    (len(fff0_payload) >> 8) & 0xFF,
                                    len(fff0_payload) & 0xFF]) + fff0_payload
        fff0_len = len(fff0_internal) - 1
        fff0_pkt = bytearray([0x75, r1, r2,
                               (fff0_len >> 8) & 0xFF, fff0_len & 0xFF]
                             ) + fff0_internal
        fff0_pkt += bytearray([0x1E, 0x00])
        self._sendto(fff0_pkt)

        # 3. Data Packets
        chunk_size = 984
        data_idx   = 1
        for i in range(0, len(frame_data), chunk_size):
            r1, r2 = rand(), rand()
            chunk = frame_data[i:i + chunk_size]
            internal = bytearray([0x02, 0x00, 0x00, 0x88, 0x77,
                                   (data_idx >> 8) & 0xFF, data_idx & 0xFF,
                                   (len(chunk) >> 8) & 0xFF, len(chunk) & 0xFF,
                                   ]) + chunk
            payload_len = len(internal) - 1
            pkt = bytearray([0x75, r1, r2,
                              (payload_len >> 8) & 0xFF, payload_len & 0xFF]
                            ) + internal
            pkt += bytearray([0x1E if len(chunk) == 984 else 0x36, 0x00])
            self._sendto(pkt)
            data_idx += 1
            time.sleep(0.002)

        # 4. End Packet
        r1, r2 = rand(), rand()
        pkt = bytearray([0x75, r1, r2, 0x00, 0x08,
                          0x02, 0x00, 0x00, 0x55, 0x66,
                          (seq >> 8) & 0xFF, seq & 0xFF,
                          0x00, 0x00, 0x00])
        pkt += bytearray([0x0E, 0x00])
        self._sendto(pkt)

    # ─── Thread loops ─────────────────────────────────────────────────────

    def send_loop(self):
        """~20 FPS render + send loop."""
        while self.running:
            frame = self.game.render()
            self.send_packet(frame)
            time.sleep(0.05)

    def recv_loop(self):
        """Receive sensor UDP packets and forward to game."""
        while self.running:
            try:
                data, _ = self.sock_recv.recvfrom(2048)
                if len(data) >= 1373 and data[0] == 0x88:
                    pressed = decode_sensors(data)
                    self.game.update_sensors(pressed)
                elif data and data[0] == 0x88:
                    print(f"[Network] Dropped short 0x88 packet (len={len(data)})")
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f"[Network] Recv error: {e}")

    def start(self):
        t_send = threading.Thread(target=self.send_loop, daemon=True)
        t_recv = threading.Thread(target=self.recv_loop, daemon=True)
        t_send.start()
        t_recv.start()

    def stop(self):
        self.running = False
        try:
            self.sock_send.close()
        except Exception:
            pass
        try:
            self.sock_recv.close()
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Utility functions
# ─────────────────────────────────────────────────────────────────────────────

def _hsv_to_rgb(h, s, v):
    """Convert HSV to RGB, each 0.0–1.0 input, returns int (0–255) tuple."""
    if s == 0:
        iv = int(v * 255)
        return (iv, iv, iv)
    i  = int(h * 6)
    f  = h * 6 - i
    p  = int(v * (1 - s) * 255)
    q  = int(v * (1 - f * s) * 255)
    t  = int(v * (1 - (1 - f) * s) * 255)
    vi = int(v * 255)
    i %= 6
    return [(vi,t,p),(q,vi,p),(p,vi,t),(p,q,vi),(t,p,vi),(vi,p,q)][i]


def _color_name(color: tuple) -> str:
    """Return a human-readable name for known player colours."""
    names = {
        (255, 40,  40):  "Red",
        ( 40,120, 255):  "Blue",
        ( 40,210,  40):  "Green",
        (255,200,   0):  "Gold",
        (220, 40, 220):  "Violet",
        (  0,230, 200):  "Teal",
        (255,120,   0):  "Orange",
        (180,255,  80):  "YellowGreen",
        (255, 80, 160):  "Pink",
        ( 80,200, 255):  "Aqua",
    }
    return names.get(color, f"rgb{color}")


def _get_led(buffer: bytearray, x: int, y: int) -> tuple:
    """Read back a colour from the GRB frame buffer (returns RGB tuple)."""
    if not (0 <= x < BOARD_WIDTH and 0 <= y < BOARD_HEIGHT):
        return (0, 0, 0)
    channel     = y // 4
    row_in_chan = y % 4
    if row_in_chan % 2 == 0:
        led_index = row_in_chan * 16 + x
    else:
        led_index = row_in_chan * 16 + (15 - x)
    block_size = NUM_CHANNELS * 3
    offset = led_index * block_size + channel
    if offset + NUM_CHANNELS * 2 < len(buffer):
        g = buffer[offset]
        r = buffer[offset + NUM_CHANNELS]
        b = buffer[offset + NUM_CHANNELS * 2]
        return (r, g, b)
    return (0, 0, 0)


# ─────────────────────────────────────────────────────────────────────────────
# Game tick thread
# ─────────────────────────────────────────────────────────────────────────────

def game_thread_func(game: ColorCaptureGame):
    while game.running:
        game.tick()
        
        try:
            with game.lock:
                st = game.state
                pre_left = 0.0
                if st == "PRE_GAME":
                    pre_left = PRE_GAME_DURATION - (time.time() - game.pre_game_start_time)
            
            if 'sound_mgr' in globals():
                sound_mgr.update(st, pre_left)
        except Exception:
            pass
            
        time.sleep(0.016)   # ~60 logic ticks / second


# ─────────────────────────────────────────────────────────────────────────────
# Launcher GUI
# ─────────────────────────────────────────────────────────────────────────────

def show_launcher(game: ColorCaptureGame) -> None:
    """
    Show a pre-game tkinter window where the user picks 2, 3 or 4 players
    and clicks START.  When the window closes the game is already started.
    Falls back to a simple console prompt if tkinter is unavailable.
    """
    try:
        import tkinter as tk
    except ImportError:
        # Headless fallback
        while True:
            try:
                raw = input(f"Players ({MIN_PLAYERS}-{MAX_PLAYERS}): ").strip()
                n = int(raw)
                if MIN_PLAYERS <= n <= MAX_PLAYERS:
                    game.start_game(num_players=n)
                    return
            except ValueError:
                pass
            print(f"Please enter {MIN_PLAYERS}, {MIN_PLAYERS+1}, ... or {MAX_PLAYERS}.")
        return

    # ── colour helpers ────────────────────────────────────────────────────────
    def rgb_hex(color):
        return "#{:02x}{:02x}{:02x}".format(*color)

    # Card data: player colour + corner name
    card_data = [
        (PLAYER_COLORS[0], "Top-Left"),
        (PLAYER_COLORS[1], "Bot-Right"),
        (PLAYER_COLORS[2], "Top-Right"),
        (PLAYER_COLORS[3], "Bot-Left"),
    ]

    chosen_n = [MIN_PLAYERS]   # mutable container

    # ── window setup ─────────────────────────────────────────────────────────
    root = tk.Tk()
    root.title("Color Capture")
    BG      = "#08081a"
    BG2     = "#0f0f2a"
    ACCENT  = "#1a2a6c"
    SEP     = "#1e2e5a"
    root.configure(bg=BG)

    W0, H0 = 440, 600
    sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
    root.geometry(f"{W0}x{H0}+{(sw - W0)//2}+{(sh - H0)//2}")

    # ── dynamic font scaling ──────────────────────────────────────────────────
    _BASE_H = 600.0
    _font_cache = {}   # (base_size, bold) -> tk.font.Font

    def F(base_size, bold=False):
        """Get (or create) a scalable Font object for the given base size."""
        key = (base_size, bold)
        if key not in _font_cache:
            _font_cache[key] = tkfont.Font(
                family="Consolas", size=base_size,
                weight="bold" if bold else "normal")
        return _font_cache[key]

    def _on_resize(event):
        if event.widget is not root:
            return
        h = max(event.height, 100)
        sf = h / _BASE_H
        for (base_size, _), font_obj in _font_cache.items():
            new_size = max(6, int(base_size * sf))
            font_obj.configure(size=new_size)

    root.bind("<Configure>", _on_resize)

    # Centre all launcher content in a frame
    _launcher = tk.Frame(root, bg=BG)
    _launcher.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

    # ── title ─────────────────────────────────────────────────────────────────
    tk.Label(_launcher, text="COLOR CAPTURE",
             bg=BG, fg="#ffffff",
             font=F(26, True)).pack(pady=(28, 2))
    tk.Label(_launcher, text="LED Matrix Territory Game",
             bg=BG, fg="#4455aa",
             font=F(10)).pack()

    tk.Frame(_launcher, bg=SEP, height=1).pack(fill=tk.X, padx=30, pady=(18, 0))

    # ── player count selector ─────────────────────────────────────────────────
    tk.Label(_launcher, text="NUMBER OF PLAYERS",
             bg=BG, fg="#6677bb",
             font=F(9, True)).pack(pady=(14, 10))

    sel_var  = tk.IntVar(value=MIN_PLAYERS)
    btn_refs = {}   # n -> button widget

    cnt_frame = tk.Frame(_launcher, bg=BG)
    cnt_frame.pack()

    def refresh_buttons(chosen):
        for n, b in btn_refs.items():
            if n == chosen:
                b.configure(bg="#2244cc", relief="sunken",
                            fg="white", font=F(22, True))
            else:
                b.configure(bg="#141430", relief="flat",
                            fg="#445588", font=F(22, True))
        refresh_cards(chosen)

    def make_select(n):
        def _cb():
            sel_var.set(n)
            refresh_buttons(n)
            game.set_preview(n)   # update matrix preview immediately
        return _cb

    for n in range(MIN_PLAYERS, MAX_PLAYERS + 1):
        b = tk.Button(
            cnt_frame, text=str(n), width=4,
            bg="#2244cc" if n == MIN_PLAYERS else "#141430",
            fg="white" if n == MIN_PLAYERS else "#445588",
            font=F(22, True),
            relief="sunken" if n == MIN_PLAYERS else "flat",
            activebackground="#3355ee", activeforeground="white",
            bd=0, pady=10, cursor="hand2",
            command=make_select(n)
        )
        b.pack(side=tk.LEFT, padx=10)
        btn_refs[n] = b

    # ── player cards ──────────────────────────────────────────────────────────
    tk.Label(_launcher, text="SPAWN CORNERS",
             bg=BG, fg="#6677bb",
             font=F(9, True)).pack(pady=(18, 8))

    cards_frame = tk.Frame(_launcher, bg=BG)
    cards_frame.pack()

    card_widgets = []   # list of (outer_frame, label)

    for i in range(MAX_PLAYERS):
        col_rgb, corner = card_data[i]
        col_hex         = rgb_hex(col_rgb)
        # luminance-based text colour
        lum = 0.299*col_rgb[0] + 0.587*col_rgb[1] + 0.114*col_rgb[2]
        txt = "#000000" if lum > 140 else "#ffffff"

        outer = tk.Frame(cards_frame, bg="#141430",
                         width=88, height=68,
                         highlightthickness=2, highlightbackground="#1e2e5a")
        outer.pack_propagate(False)
        outer.pack(side=tk.LEFT, padx=5)

        lbl = tk.Label(outer,
                       text=f"P{i+1}\n{corner}",
                       bg="#141430", fg="#2a3a6a",
                       font=F(8, True), justify=tk.CENTER)
        lbl.pack(expand=True)
        card_widgets.append((outer, lbl, col_hex, txt))

    def refresh_cards(n):
        for i, (outer, lbl, col_hex, txt) in enumerate(card_widgets):
            if i < n:
                outer.configure(bg=col_hex, highlightbackground=col_hex)
                lbl.configure(bg=col_hex, fg=txt)
            else:
                outer.configure(bg="#141430", highlightbackground="#1e2e5a")
                lbl.configure(bg="#141430", fg="#2a3a6a")

    refresh_cards(MIN_PLAYERS)   # initial state
    game.set_preview(MIN_PLAYERS)  # show default spawn points on matrix

    tk.Frame(_launcher, bg=SEP, height=1).pack(fill=tk.X, padx=30, pady=(18, 0))

    # ── rounds selector ───────────────────────────────────────────────────────
    tk.Label(_launcher, text="NUMBER OF ROUNDS",
             bg=BG, fg="#6677bb",
             font=F(9, True)).pack(pady=(12, 6))

    rnd_var = tk.IntVar(value=TOTAL_ROUNDS)
    rnd_btn_refs = {}

    rnd_frame = tk.Frame(_launcher, bg=BG)
    rnd_frame.pack()

    def refresh_rnd_buttons(chosen):
        for r, b in rnd_btn_refs.items():
            if r == chosen:
                b.configure(bg="#aa2244", relief="sunken",
                            fg="white", font=F(18, True))
            else:
                b.configure(bg="#141430", relief="flat",
                            fg="#884455", font=F(18, True))

    def make_rnd_select(r):
        def _cb():
            rnd_var.set(r)
            refresh_rnd_buttons(r)
        return _cb

    for r in range(1, 4 + 1):
        b = tk.Button(
            rnd_frame, text=str(r), width=4,
            bg="#aa2244" if r == TOTAL_ROUNDS else "#141430",
            fg="white" if r == TOTAL_ROUNDS else "#884455",
            font=F(18, True),
            relief="sunken" if r == TOTAL_ROUNDS else "flat",
            activebackground="#ee3355", activeforeground="white",
            bd=0, pady=6, cursor="hand2",
            command=make_rnd_select(r)
        )
        b.pack(side=tk.LEFT, padx=10)
        rnd_btn_refs[r] = b

    tk.Frame(_launcher, bg=SEP, height=1).pack(fill=tk.X, padx=30, pady=(15, 0))

    # ── game control panel + scoreboard (shown after START) ──────────────────
    def _show_game_panel(n_players: int):
        """Transform root into control panel + open separate scoreboard Toplevel."""

        # ── 1. Transform root into the Control Panel ─────────────────────────
        for w in root.winfo_children():
            w.destroy()

        PW, PH = 320, 260
        sw2 = root.winfo_screenwidth()
        sh2 = root.winfo_screenheight()
        root.geometry(f"{PW}x{PH}+{(sw2 - PW)//2 - 220}+{(sh2 - PH)//2}")
        root.title("Color Capture - Control")
        BG   = "#08081a"
        SEP2 = "#1e2e5a"

        ctrl = tk.Frame(root, bg=BG)
        ctrl.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(ctrl, text="COLOR CAPTURE",
                 bg=BG, fg="#ffffff",
                 font=F(20, True)).pack(pady=(16, 2))

        dots_frame = tk.Frame(ctrl, bg=BG)
        dots_frame.pack(pady=(3, 0))
        for i in range(n_players):
            col_hex = "#{:02x}{:02x}{:02x}".format(*PLAYER_COLORS[i])
            tk.Label(dots_frame, text="   ", bg=col_hex,
                     width=3, relief="flat").pack(side=tk.LEFT, padx=3, pady=2)

        tk.Frame(ctrl, bg=SEP2, height=1).pack(fill=tk.X, padx=20, pady=(10, 0))

        status_var = tk.StringVar(value="ROUND 1  -  PLAYING")
        status_lbl = tk.Label(ctrl, textvariable=status_var,
                              bg=BG, fg="#44ff88",
                              font=F(12, True))
        status_lbl.pack(pady=(8, 2))

        tk.Frame(ctrl, bg=SEP2, height=1).pack(fill=tk.X, padx=20, pady=(8, 0))

        btn_row = tk.Frame(ctrl, bg=BG)
        btn_row.pack(pady=(12, 0))

        def do_restart():
            game.restart()
            status_var.set("RESTARTED  -  PLAYING")
            status_lbl.configure(fg="#44ff88")

        def do_exit():
            game.running = False
            try: sb.destroy()
            except Exception: pass
            root.destroy()

        tk.Button(btn_row, text="RESTART",
                  command=do_restart,
                  bg="#1a4a88", fg="white",
                  font=F(12, True),
                  relief="flat", padx=14, pady=8,
                  cursor="hand2",
                  activebackground="#2255aa").pack(side=tk.LEFT, padx=8)

        tk.Button(btn_row, text="EXIT",
                  command=do_exit,
                  bg="#881a1a", fg="white",
                  font=F(12, True),
                  relief="flat", padx=14, pady=8,
                  cursor="hand2",
                  activebackground="#aa2222").pack(side=tk.LEFT, padx=8)

        root.protocol("WM_DELETE_WINDOW", do_exit)

        # ── 2. Open the SCOREBOARD in a separate Toplevel ─────────────────────
        SBW = 420
        SBH = 180 + n_players * 52
        sb_x = (sw2 - PW) // 2 - 220 + PW + 20
        sb_y = (sh2 - SBH) // 2
        sb = tk.Toplevel(root)
        sb.title("Color Capture - Scoreboard")
        sb.configure(bg=BG)
        sb.geometry(f"{SBW}x{SBH}+{sb_x}+{sb_y}")
        sb.protocol("WM_DELETE_WINDOW", do_exit)

        # Font scaling for scoreboard window too
        _SB_BASE_H = float(SBH)
        def _sb_resize(event):
            if event.widget is not sb:
                return
            h = max(event.height, 100)
            sf = h / _SB_BASE_H
            for (base_size, _), font_obj in _font_cache.items():
                new_size = max(6, int(base_size * sf))
                font_obj.configure(size=new_size)
        sb.bind("<Configure>", _sb_resize)

        sb_inner = tk.Frame(sb, bg=BG)
        sb_inner.place(relx=0.5, rely=0.5, anchor=tk.CENTER)

        tk.Label(sb_inner, text="SCOREBOARD",
                 bg=BG, fg="#ffffff",
                 font=F(18, True)).pack(pady=(14, 2))

        round_time_frame = tk.Frame(sb_inner, bg=BG)
        round_time_frame.pack()
        rnd_var  = tk.StringVar(value=f"ROUND 1 / {game.total_rounds}")
        time_var = tk.StringVar(value="TIME LEFT: --:--")
        tk.Label(round_time_frame, textvariable=rnd_var,
                 bg=BG, fg="#8899cc",
                 font=F(11, True)).pack(side=tk.LEFT, padx=10)
        tk.Label(round_time_frame, textvariable=time_var,
                 bg=BG, fg="#ffcc44",
                 font=F(11, True)).pack(side=tk.LEFT, padx=10)

        tk.Frame(sb_inner, bg=SEP2, height=1).pack(fill=tk.X, padx=16, pady=(10, 4))

        # Player score rows (bar + label)
        BAR_MAX_W = 200   # pixels for max-score bar
        BAR_H     = 16
        rows = []         # list of (bar_frame, score_lbl)
        for i in range(n_players):
            col_hex = "#{:02x}{:02x}{:02x}".format(*PLAYER_COLORS[i])
            row_f = tk.Frame(sb_inner, bg=BG)
            row_f.pack(fill=tk.X, padx=16, pady=4)

            # Colour swatch
            tk.Label(row_f, text="  ", bg=col_hex,
                     width=2, relief="flat").pack(side=tk.LEFT)

            # Player name
            tk.Label(row_f, text=f" P{i+1} ",
                     bg=BG, fg=col_hex,
                     font=F(11, True),
                     width=4, anchor="w").pack(side=tk.LEFT)

            # Bar container (fixed width background)
            bar_bg = tk.Frame(row_f, bg="#111130", width=BAR_MAX_W, height=BAR_H)
            bar_bg.pack_propagate(False)
            bar_bg.pack(side=tk.LEFT, padx=(4, 6))

            bar_fill = tk.Frame(bar_bg, bg=col_hex, width=0, height=BAR_H)
            bar_fill.place(x=0, y=0, height=BAR_H, width=0)

            # Score text
            score_var = tk.StringVar(value="0 cells | total 0")
            score_lbl = tk.Label(row_f, textvariable=score_var,
                                 bg=BG, fg="#aabbdd",
                                 font=F(10))
            score_lbl.pack(side=tk.LEFT)

            rows.append((bar_fill, score_var, BAR_MAX_W))

        # Winner banner (hidden initially)
        tk.Frame(sb_inner, bg=SEP2, height=1).pack(fill=tk.X, padx=16, pady=(6, 4))
        winner_var = tk.StringVar(value="")
        winner_lbl = tk.Label(sb_inner, textvariable=winner_var,
                              bg=BG, fg="#ffffff",
                              font=F(14, True),
                              pady=6)
        winner_lbl.pack()

        # ── 3. Shared polling loop ────────────────────────────────────────────
        def _poll():
            if not game.running:
                return
            with game.lock:
                st         = game.state
                rnd        = game.current_round
                t_start    = game.round_start_time
                players    = list(game.players)
                board_snap = dict(game.board)
                winner     = game._final_winner

            # Control panel status
            if st == "PLAYING":
                status_var.set(f"ROUND {rnd}/{game.total_rounds}  -  PLAYING")
                status_lbl.configure(fg="#44ff88")
            elif st == "PRE_GAME":
                status_var.set(f"ROUND {rnd}/{game.total_rounds}  -  PRE-GAME")
                status_lbl.configure(fg="#ffffff")
            elif st == "BREAK":
                status_var.set(f"ROUND {rnd} DONE  -  BREAK")
                status_lbl.configure(fg="#ffcc44")
            elif st == "FINAL":
                w_name = f"P{winner.id + 1}" if winner else "?"
                w_hex  = "#{:02x}{:02x}{:02x}".format(*winner.color) if winner else "#fff"
                status_var.set(f"GAME OVER - WINNER: {w_name}")
                status_lbl.configure(fg=w_hex)
            else:
                status_var.set(st)
                status_lbl.configure(fg="#aabbff")

            # Scoreboard: round + timer
            rnd_var.set(f"ROUND {rnd} / {game.total_rounds}")
            if st == "PLAYING":
                elapsed   = time.time() - t_start
                remaining = max(0.0, ROUND_DURATION - elapsed)
                mins, secs = divmod(int(remaining), 60)
                time_var.set(f"TIME LEFT: {mins}:{secs:02d}")
            elif st == "PRE_GAME":
                elapsed_pre = time.time() - game.pre_game_start_time
                remaining_pre = max(0.0, PRE_GAME_DURATION - elapsed_pre)
                time_var.set(f"START IN: {int(remaining_pre)}s")
            elif st == "BREAK":
                elapsed_brk = time.time() - game.break_start_time
                remaining_brk = max(0.0, BREAK_DURATION - elapsed_brk)
                time_var.set(f"NEXT ROUND IN: {int(remaining_brk)}s")
            elif st == "FINAL":
                time_var.set("FINISHED")
            else:
                time_var.set("")

            # Scoreboard: player bars + scores
            max_cells = max(
                (sum(1 for v in board_snap.values() if v == p.id) for p in players),
                default=1
            ) or 1

            for p in players:
                if p.id >= len(rows):
                    continue
                bar_fill, score_var, bmax = rows[p.id]
                cells = sum(1 for v in board_snap.values() if v == p.id)
                bar_w = max(1, int(cells / max_cells * bmax)) if max_cells else 0
                bar_fill.place(x=0, y=0, height=BAR_H, width=bar_w)
                score_var.set(f"{cells:3d} cells | total {p.total_score}")

            # Winner banner
            if st == "FINAL" and winner:
                w_hex = "#{:02x}{:02x}{:02x}".format(*winner.color)
                winner_var.set(
                    f"WINNER: P{winner.id+1}  ({_color_name(winner.color)}) "
                    f"- {winner.total_score} pts"
                )
                winner_lbl.configure(fg=w_hex)
            else:
                winner_var.set("")

            root.after(400, _poll)

        _poll()


    # ── start button ─────────────────────────────────────────────────────────
    def on_start():
        n = sel_var.get()
        r = rnd_var.get()
        chosen_n[0] = n
        game.start_game(num_players=n, num_rounds=r)
        _show_game_panel(n)   # transform window instead of closing it

    start_btn = tk.Button(
        _launcher, text="  START GAME  ",
        command=on_start,
        bg="#22aa44", fg="white",
        font=F(18, True),
        relief="flat", padx=24, pady=12,
        cursor="hand2",
        activebackground="#33cc55", activeforeground="white"
    )
    start_btn.pack(pady=(20, 4))

    tk.Label(_launcher, text="Captureaza cat mai mult din harta!",
             bg=BG, fg="#4466aa",
             font=F(10)).pack(pady=(0, 8))

    def _hover_in(e):  start_btn.configure(bg="#2ec455")
    def _hover_out(e): start_btn.configure(bg="#22aa44")
    start_btn.bind("<Enter>", _hover_in)
    start_btn.bind("<Leave>", _hover_out)

    root.bind("<Return>", lambda e: on_start())
    root.protocol("WM_DELETE_WINDOW", lambda: (game.__setattr__("running", False), root.destroy()))

    root.mainloop()



if __name__ == "__main__":
    print("=" * 60)
    print("  Color Capture - LED Matrix Territory Game")
    print("=" * 60)
    print(f"  Send  -> {UDP_SEND_IP}:{UDP_SEND_PORT}")
    print(f"  Recv  <- 0.0.0.0:{UDP_RECV_PORT}")
    print(f"  Board : {BOARD_WIDTH}x{BOARD_HEIGHT}  Channels: {NUM_CHANNELS}")
    print("=" * 60)
    print("  Launcher window opening...")

    # Create game (starts in LOBBY - shows animated idle on matrix while waiting)
    game = ColorCaptureGame(num_players=MIN_PLAYERS)
    net  = NetworkManager(game)
    net.start()

    gt = threading.Thread(target=game_thread_func, args=(game,), daemon=True)
    gt.start()

    # Show the launcher GUI - blocks until user clicks START (or closes window)
    show_launcher(game)

    if not game.running:
        net.stop()
        print("[ColorCapture] Exited (window closed).")
        sys.exit(0)

    print(f"[ColorCapture] Game launched with {game._num_players} players.")
    print("Commands: restart | info | quit")

    # Minimal console loop (no 'start' needed - use launcher for that)
    try:
        while game.running:
            try:
                cmd = input("> ").strip().lower()
            except EOFError:
                break

            if cmd in ("quit", "exit", "q"):
                game.running = False
                break

            elif cmd == "restart":
                game.restart()
                print("Game restarted.")

            elif cmd == "info":
                with game.lock:
                    st  = game.state
                    rnd = game.current_round
                    print(f"State: {st}  Round: {rnd}/{TOTAL_ROUNDS}")
                    for p in sorted(game.players,
                                    key=lambda p: p.total_score, reverse=True):
                        cells = sum(1 for v in game.board.values() if v == p.id)
                        print(f"  P{p.id+1} ({_color_name(p.color)}): "
                              f"{cells} cells | total {p.total_score}")

            else:
                print("Commands: restart | info | quit")

    except KeyboardInterrupt:
        print("\n[ColorCapture] Interrupted.")
    finally:
        game.running = False
        net.stop()
        print("[ColorCapture] Exited.")

