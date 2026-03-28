import socket
import struct
import time
import threading
import random
import subprocess
import sys
import os
import json as _json

# --- Networking Constants ---
UDP_SEND_IP        = "255.255.255.255"
UDP_SEND_PORT      = 4626
UDP_LISTEN_PORT    = 7800
UDP_TELEMETRY_PORT = 6668   # Game → KD_Displays.py  (state packets)
UDP_CMD_PORT       = 6669   # KD_Displays.py → Game  (command packets)

# --- Matrix Constants ---
NUM_CHANNELS      = 8
LEDS_PER_CHANNEL  = 64
FRAME_DATA_LENGTH = NUM_CHANNELS * LEDS_PER_CHANNEL * 3

BOARD_WIDTH  = 16
BOARD_HEIGHT = 32

# --- Countdown duration (seconds players have to enter the room) ---
COUNTDOWN_SECONDS = 10

# --- Password array (protocol checksum) ---
PASSWORD_ARRAY = [
    35, 63, 187, 69, 107, 178, 92, 76, 39, 69, 205, 37, 223, 255, 165, 231, 16, 220, 99, 61, 25, 203, 203,
    155, 107, 30, 92, 144, 218, 194, 226, 88, 196, 190, 67, 195, 159, 185, 209, 24, 163, 65, 25, 172, 126,
    63, 224, 61, 160, 80, 125, 91, 239, 144, 25, 141, 183, 204, 171, 188, 255, 162, 104, 225, 186, 91, 232,
    3, 100, 208, 49, 211, 37, 192, 20, 99, 27, 92, 147, 152, 86, 177, 53, 153, 94, 177, 200, 33, 175, 195,
    15, 228, 247, 18, 244, 150, 165, 229, 212, 96, 84, 200, 168, 191, 38, 112, 171, 116, 121, 186, 147, 203,
    30, 118, 115, 159, 238, 139, 60, 57, 235, 213, 159, 198, 160, 50, 97, 201, 242, 240, 77, 102, 12,
    183, 235, 243, 247, 75, 90, 13, 236, 56, 133, 150, 128, 138, 190, 140, 13, 213, 18, 7, 117, 255, 45, 69,
    214, 179, 50, 28, 66, 123, 239, 190, 73, 142, 218, 253, 5, 212, 174, 152, 75, 226, 226, 172, 78, 35, 93,
    250, 238, 19, 32, 247, 233, 89, 123, 86, 138, 150, 146, 214, 192, 93, 152, 156, 211, 67, 51, 195, 165,
    66, 10, 10, 31, 1, 198, 234, 135, 34, 128, 208, 200, 213, 169, 238, 74, 221, 208, 104, 170, 166, 36, 76,
    177, 196, 3, 141, 167, 127, 56, 177, 203, 45, 107, 46, 82, 217, 139, 168, 45, 198, 6, 43, 11, 57, 88,
    182, 84, 189, 29, 35, 143, 138, 171
]

# --- Colors (R, G, B) ---
BLACK   = (0, 0, 0)
WHITE   = (255, 255, 255)
RED     = (255, 0, 0)
YELLOW  = (255, 220, 0)
GREEN   = (0, 255, 0)
BLUE    = (0, 80, 255)
CYAN    = (0, 255, 255)
ORANGE  = (255, 140, 0)
PURPLE  = (180, 0, 255)
DIM_RED = (120, 0, 0)

# --- Core Zone ---
CORE_X1, CORE_X2 = 6, 9    # inclusive
CORE_Y1, CORE_Y2 = 14, 17  # inclusive

# --- Game Tick ---
TICK_INTERVAL = 0.5

# --- Wave definitions ---
WAVE_DEFS = [
    [(GREEN,  1)] * 5,
    [(GREEN,  1)] * 3 + [(YELLOW, 2)] * 2,
    [(GREEN,  1)] * 2 + [(YELLOW, 2)] * 2 + [(RED, 3)] * 3,
]
ENEMIES_PER_WAVE = [8, 14, 22]
SPAWN_INTERVAL   = [2.0, 1.5, 1.2]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _color_for_hp(hp):
    if hp >= 3:   return RED
    elif hp == 2: return YELLOW
    else:         return GREEN


def _core_color(lives):
    if lives == 3:   return BLUE
    elif lives == 2: return CYAN
    elif lives == 1: return ORANGE
    else:            return DIM_RED


def _spawn_position():
    side = random.randint(0, 3)
    if side == 0:   return (random.randint(0, BOARD_WIDTH - 1), 0)
    elif side == 1: return (random.randint(0, BOARD_WIDTH - 1), BOARD_HEIGHT - 1)
    elif side == 2: return (0, random.randint(0, BOARD_HEIGHT - 1))
    else:           return (BOARD_WIDTH - 1, random.randint(0, BOARD_HEIGHT - 1))


def _step_toward_core(ex, ey):
    cx, cy = 7, 15
    dx = 0 if ex == cx else (1 if ex < cx else -1)
    dy = 0 if ey == cy else (1 if ey < cy else -1)
    if abs(ex - cx) >= abs(ey - cy):
        return ex + dx, ey
    else:
        return ex, ey + dy


def _in_core(x, y):
    return CORE_X1 <= x <= CORE_X2 and CORE_Y1 <= y <= CORE_Y2


# ──────────────────────────────────────────────────────────────────────────────
# Audio Manager
# ──────────────────────────────────────────────────────────────────────────────
try:
    import pygame
    HAS_PYGAME = True
except ImportError:
    HAS_PYGAME = False

class AudioManager:
    def __init__(self):
        self.enabled = False
        self.sounds = {}

        if not HAS_PYGAME:
            print("[AUDIO] Pygame not found. Audio disabled.")
            return

        try:
            pygame.mixer.init()
            self.enabled = True
            
            sfx_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sfx")
            
            self.bgm = os.path.join(sfx_dir, "kd_bgm.mp3")
            self.sad_bgm = os.path.join(sfx_dir, "sad_bgm.mp3")
            self.happy_bgm = os.path.join(sfx_dir, "happy_bgm.mp3")
            
            for name, filename in [
                ("drop", "drop.wav"),
                ("quake", "quake.mp3"),
                ("gameover", "gameover.wav"),
                ("move", "move.wav"),
                ("rotate", "rotate.wav"),
                ("prepare", "prepare.mp3"),
                ("start", "start.mp3"),
                ("you_lost", "you_lost.mp3"),
                ("you_won", "you_won.mp3")
            ]:
                path = os.path.join(sfx_dir, filename)
                if os.path.exists(path):
                    self.sounds[name] = pygame.mixer.Sound(path)
                    
            for i in range(1, 20):
                wpath = os.path.join(sfx_dir, f"wave_{i}.mp3")
                if os.path.exists(wpath):
                    self.sounds[f"wave_{i}"] = pygame.mixer.Sound(wpath)
                    
            print(f"[AUDIO] Pygame mixer initialized successfully. Loaded {len(self.sounds)} SFX.")
        except Exception as e:
            print(f"[AUDIO] Failed to initialize audio: {e}")
            self.enabled = False

    def play_music(self, bgm_file=None, loop=True):
        if self.enabled:
            target = bgm_file if bgm_file else self.bgm
            if os.path.exists(target):
                try:
                    pygame.mixer.music.load(target)
                    pygame.mixer.music.play(-1 if loop else 0)
                except Exception as e:
                    print(f"[AUDIO] Error playing music: {e}")

    def stop_music(self):
        if self.enabled:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass

    def play_sfx(self, name):
        if self.enabled and name in self.sounds:
            try:
                self.sounds[name].play()
            except Exception:
                pass

audio = AudioManager()


# ──────────────────────────────────────────────────────────────────────────────
# KernelDefenderGame
# ──────────────────────────────────────────────────────────────────────────────

class KernelDefenderGame:
    """
    State machine:
      LOBBY → COUNTDOWN → PLAYING → GAMEOVER | WIN
    """

    def __init__(self):
        self.lock    = threading.RLock()
        self.running = True

        # ── State ──
        self.state = "LOBBY"   # LOBBY | COUNTDOWN | PLAYING | GAMEOVER | WIN

        # ── Lobby ──
        self.player_count = 4   # default

        # ── Countdown ──
        self.countdown_end = 0.0

        # ── Timing ──
        self.game_start_time = 0.0
        self.game_elapsed    = 0.0   # updated every tick during PLAYING

        # ── Core ──
        self.core_lives = 3

        # ── Enemies ──
        self.enemies = []

        # ── Wave ──
        self.wave            = 0
        self.enemies_spawned = 0
        self.last_spawn_time = 0.0
        self.fail_wave       = 0   # wave index (1-based) when GAMEOVER happened

        # ── Earthquake ──
        self.quakes_remaining = 3
        self.quake_active     = False
        self.quake_x          = 0
        self.quake_dir        = -1
        self.quake_origin_x   = 0
        self.last_quake_anim  = 0.0

        # ── Touch input ──
        self.active_touches = []
        self._prev_touches  = set()

        # ── Enemy movement timer ──
        self.last_tick = 0.0

        print("[KD] Kernel Defender ready. Waiting in LOBBY.")

    # ── Public commands (called from CommandReceiver) ─────────────────────────

    def cmd_start(self, player_count: int):
        """Transition LOBBY → COUNTDOWN."""
        with self.lock:
            if self.state != "LOBBY":
                return
            self.player_count  = max(2, min(10, player_count))
            self.state         = "COUNTDOWN"
            self.countdown_end = time.time() + COUNTDOWN_SECONDS
            audio.play_sfx("prepare")
            print(f"[KD] COUNTDOWN started for {self.player_count} players. "
                  f"{COUNTDOWN_SECONDS}s to enter the room...")

    def cmd_restart(self):
        """Reset fully and go back to LOBBY."""
        with self.lock:
            audio.stop_music()
            self.__init__()
        print("[KD] Restarted – back in LOBBY.")

    def cmd_quit(self):
        self.running = False

    # ── Internal helpers ──────────────────────────────────────────────────────

    def _begin_play(self):
        """Transition COUNTDOWN → PLAYING (called when countdown expires)."""
        self.state           = "PLAYING"
        self.core_lives      = 3
        self.enemies         = []
        self.wave            = 0
        self.enemies_spawned = 0
        self.last_spawn_time = time.time()
        self.last_tick       = time.time()
        self.quakes_remaining= 3
        self.quake_active    = False
        self.fail_wave       = 0
        self.game_start_time = time.time()
        self.game_elapsed    = 0.0
        self.active_touches  = []
        self._prev_touches   = set()
        audio.play_sfx("start")
        audio.play_music()
        print("[KD] PLAYING! Wave 1 begins.")

    def _fresh_touches(self):
        cur   = set(self.active_touches)
        fresh = cur - self._prev_touches
        self._prev_touches = cur
        return fresh

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self):
        with self.lock:
            now = time.time()

            if self.state == "LOBBY":
                return

            if self.state == "COUNTDOWN":
                if now >= self.countdown_end:
                    self._begin_play()
                return

            if self.state in ("GAMEOVER", "WIN"):
                return

            # ── PLAYING ──
            self.game_elapsed = now - self.game_start_time

            self._try_spawn(now)

            if now - self.last_tick >= TICK_INTERVAL:
                self.last_tick = now
                self._move_enemies()

            fresh = self._fresh_touches()
            if fresh:
                self._process_touches(fresh)

            if self.quake_active:
                self._advance_quake(now)

            self._check_wave_transition(now)

    # ── Spawn ─────────────────────────────────────────────────────────────────

    def _try_spawn(self, now):
        if self.wave >= len(WAVE_DEFS):
            return

        # 1. DIFICULTATE DINAMICĂ: 4 jucători e standardul.
        scale_factor = self.player_count / 4.0
        max_enemies = int(ENEMIES_PER_WAVE[self.wave] * scale_factor)
        max_enemies = max(1, max_enemies) # Minim 1 inamic garantat
        
        if self.enemies_spawned >= max_enemies:
            return

        # Viteza de spawn crește (scade intervalul) cu cât sunt mai mulți jucători
        current_spawn_interval = SPAWN_INTERVAL[self.wave] / scale_factor
        current_spawn_interval = max(0.4, current_spawn_interval) # Limită ca să nu apară instant toți

        if now - self.last_spawn_time < current_spawn_interval:
            return

        pool         = WAVE_DEFS[self.wave]
        color, hp    = random.choice(pool)
        
        # 2. ANTI-REPETIȚIE
        if not hasattr(self, 'recent_spawns'):
            self.recent_spawns = []
            
        attempts = 0
        while True:
            x, y = _spawn_position()
            if _in_core(x, y):
                x += (2 if x < 8 else -2)
            
            # Verificăm dacă poziția e prea recentă
            if (x, y) not in self.recent_spawns or attempts > 10:
                self.recent_spawns.append((x, y))
                if len(self.recent_spawns) > 5:  # Memorează ultimele 5 locații
                    self.recent_spawns.pop(0)
                break
            attempts += 1

        # 3. NERF "LINIA DE 32" (Marginile laterale X=0 și X=15 sunt prea aproape de nucleu)
        if x == 0 or x == BOARD_WIDTH - 1:
            hp = min(hp, 2) # Niciodată Roșu (3 HP) pe laterale. Maxim Galben (2 HP).

        self.enemies.append({"x": x, "y": y, "hp": hp})
        self.enemies_spawned += 1
        self.last_spawn_time  = now

    # ── Enemy movement ────────────────────────────────────────────────────────

    def _move_enemies(self):
        surviving = []
        for e in self.enemies:
            nx, ny = _step_toward_core(e["x"], e["y"])
            e["x"], e["y"] = nx, ny
            if _in_core(nx, ny):
                self.core_lives -= 1
                audio.play_sfx("drop")
                print(f"[KD] Core hit! Lives: {self.core_lives}")
                if self.core_lives <= 0:
                    self.fail_wave = self.wave + 1
                    self.state     = "GAMEOVER"
                    audio.stop_music()
                    audio.play_sfx("you_lost")
                    audio.play_music(audio.sad_bgm, loop=False)
                    print(f"[KD] GAME OVER at Wave {self.fail_wave}!")
            else:
                surviving.append(e)
        self.enemies = surviving

    # ── Combat ───────────────────────────────────────────────────────────────

    def _process_touches(self, fresh_coords):
        for tx, ty in fresh_coords:
            if _in_core(tx, ty) and not self.quake_active and self.quakes_remaining > 0:
                if tx in (6, 7):
                    self._start_quake(CORE_X1, direction=-1)
                elif tx in (8, 9):
                    self._start_quake(CORE_X2, direction=+1)
                continue
            for e in self.enemies:
                if e["x"] == tx and e["y"] == ty:
                    e["hp"] -= 1
                    audio.play_sfx("drop")
                    break
        self.enemies = [e for e in self.enemies if e["hp"] > 0]

    # ── Earthquake ────────────────────────────────────────────────────────────

    def _start_quake(self, origin_x, direction):
        self.quake_active     = True
        self.quake_origin_x   = origin_x
        self.quake_x          = origin_x
        self.quake_dir        = direction
        self.quakes_remaining -= 1
        self.last_quake_anim  = time.time()
        audio.play_sfx("quake")
        print(f"[KD] QUAKE {'LEFT' if direction < 0 else 'RIGHT'}, left={self.quakes_remaining}")

    def _advance_quake(self, now):
        if now - self.last_quake_anim < 0.07:
            return
        self.last_quake_anim = now
        sx = self.quake_x
        
        # BUFF-UL TĂU: La Valul 3 (index 2), cutremurul dă damage 2!
        quake_damage = 2 if self.wave >= 2 else 1
        
        for e in self.enemies:
            if e["x"] == sx:
                e["hp"] -= quake_damage
                
        self.enemies = [e for e in self.enemies if e["hp"] > 0]
        self.quake_x += self.quake_dir
        if self.quake_x < 0 or self.quake_x >= BOARD_WIDTH:
            self.quake_active = False

    # ── Wave transition ───────────────────────────────────────────────────────

    def _check_wave_transition(self, now):
        if self.wave >= len(WAVE_DEFS):
            return
            
        scale_factor = self.player_count / 4.0
        max_enemies = int(ENEMIES_PER_WAVE[self.wave] * scale_factor)
        max_enemies = max(1, max_enemies)
        
        if self.enemies_spawned >= max_enemies and len(self.enemies) == 0:
            next_wave = self.wave + 1
            if next_wave >= len(WAVE_DEFS):
                self.state = "WIN"
                audio.stop_music()
                audio.play_sfx("you_won")
                audio.play_music(audio.happy_bgm, loop=False)
                print("[KD] WIN! All waves cleared!")
            else:
                self.wave            = next_wave
                self.enemies_spawned = 0
                self.last_spawn_time = now + 2.0
                audio.play_sfx(f"wave_{next_wave + 1}")
                print(f"[KD] Wave {self.wave + 1} starting!")

    # ── Render ────────────────────────────────────────────────────────────────

    def render(self):
        buf = bytearray(FRAME_DATA_LENGTH)

        with self.lock:
            state = self.state

            if state == "LOBBY":
                self._render_lobby(buf)

            elif state == "COUNTDOWN":
                self._render_countdown(buf)

            elif state == "PLAYING":
                self._render_playing(buf)

            elif state == "GAMEOVER":
                self._render_gameover(buf)

            elif state == "WIN":
                self._render_win(buf)

        return buf

    def _render_lobby(self, buf):
        """Pulsing nucleu in blue while waiting in lobby."""
        if int(time.time() * 2) % 2 == 0:
            for cx in range(CORE_X1, CORE_X2 + 1):
                for cy in range(CORE_Y1, CORE_Y2 + 1):
                    self.set_led(buf, cx, cy, BLUE)

    def _render_countdown(self, buf):
        """Horizontal fill bar shrinking = countdown progress on LED grid."""
        remaining  = max(0, self.countdown_end - time.time())
        fraction   = remaining / COUNTDOWN_SECONDS   # 1.0 → 0.0
        fill_cols  = int(fraction * BOARD_WIDTH)
        # Background red blink
        if int(time.time() * 4) % 2 == 0:
            for y in range(BOARD_HEIGHT):
                for x in range(fill_cols):
                    self.set_led(buf, x, y, (30, 0, 0))
        # Bright green fill bar at whatever remains
        bar_y = BOARD_HEIGHT - 1
        for x in range(fill_cols):
            self.set_led(buf, x, bar_y, GREEN)
        # Nucleu flashing white
        if int(time.time() * 3) % 2 == 0:
            for cx in range(CORE_X1, CORE_X2 + 1):
                for cy in range(CORE_Y1, CORE_Y2 + 1):
                    self.set_led(buf, cx, cy, WHITE)

    def _render_playing(self, buf):
        for e in self.enemies:
            self.set_led(buf, e["x"], e["y"], _color_for_hp(e["hp"]))
        core_col = _core_color(self.core_lives)
        for cx in range(CORE_X1, CORE_X2 + 1):
            for cy in range(CORE_Y1, CORE_Y2 + 1):
                self.set_led(buf, cx, cy, core_col)
        if self.quake_active:
            for y in range(BOARD_HEIGHT):
                self.set_led(buf, self.quake_x, y, WHITE)
        # HUD: quake dots top-right
        for i in range(3):
            self.set_led(buf, BOARD_WIDTH - 1 - i, 0,
                         PURPLE if i < self.quakes_remaining else BLACK)
        # HUD: wave indicator top-left
        for i in range(self.wave + 1):
            self.set_led(buf, i, 0, ORANGE)

    def _render_gameover(self, buf):
        if int(time.time() * 3) % 2 == 0:
            for y in range(BOARD_HEIGHT):
                for x in range(BOARD_WIDTH):
                    self.set_led(buf, x, y, (50, 0, 0))
        blink = int(time.time() * 4) % 2 == 0
        for cx in range(CORE_X1, CORE_X2 + 1):
            for cy in range(CORE_Y1, CORE_Y2 + 1):
                self.set_led(buf, cx, cy, RED if blink else BLACK)

    def _render_win(self, buf):
        roll = int(time.time() * 8) % BOARD_HEIGHT
        for y in range(BOARD_HEIGHT):
            for x in range(BOARD_WIDTH):
                col = GREEN if ((x + y + roll) % 4 == 0) else BLACK
                self.set_led(buf, x, y, col)

    # ── set_led ───────────────────────────────────────────────────────────────

    def set_led(self, buffer, x, y, color):
        if x < 0 or x >= 16: return
        channel = y // 4
        if channel >= 8: return
        row_in_channel = y % 4
        led_index = (row_in_channel * 16 + x) if row_in_channel % 2 == 0 \
                    else (row_in_channel * 16 + (15 - x))
        block_size = NUM_CHANNELS * 3
        offset = led_index * block_size + channel
        if offset + NUM_CHANNELS * 2 < len(buffer):
            buffer[offset]                  = color[1]
            buffer[offset + NUM_CHANNELS]   = color[0]
            buffer[offset + NUM_CHANNELS*2] = color[2]


# ──────────────────────────────────────────────────────────────────────────────
# CommandReceiver  –  listens for JSON commands from KD_Displays.py
# ──────────────────────────────────────────────────────────────────────────────

class CommandReceiver:
    """
    Listens on UDP_CMD_PORT for JSON command packets.
    Supported commands:
      {"cmd": "start",   "players": N}
      {"cmd": "restart"}
      {"cmd": "quit"}
    """

    def __init__(self, game: KernelDefenderGame):
        self.game = game
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.settimeout(1.0)
        try:
            self._sock.bind(("0.0.0.0", UDP_CMD_PORT))
            print(f"[CMD] Command receiver on port {UDP_CMD_PORT}")
        except Exception as e:
            print(f"[CMD] WARNING: could not bind cmd port {UDP_CMD_PORT}: {e}")

        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def _loop(self):
        while self.game.running:
            try:
                data, _ = self._sock.recvfrom(512)
                cmd = _json.loads(data.decode())
                action = cmd.get("cmd", "")
                if action == "start":
                    self.game.cmd_start(int(cmd.get("players", 4)))
                elif action == "restart":
                    self.game.cmd_restart()
                elif action == "quit":
                    self.game.cmd_quit()
            except socket.timeout:
                continue
            except Exception:
                continue


# ──────────────────────────────────────────────────────────────────────────────
# TelemetryBroadcaster  –  sends state JSON to KD_Displays.py
# ──────────────────────────────────────────────────────────────────────────────

class TelemetryBroadcaster:
    INTERVAL = 0.25

    def __init__(self, game: KernelDefenderGame):
        self.game  = game
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        threading.Thread(target=self._loop, daemon=True).start()
        print(f"[TEL] Telemetry on port {UDP_TELEMETRY_PORT}")

    def _snapshot(self):
        g = self.game
        with g.lock:
            cd = max(0, int(g.countdown_end - time.time())) if g.state == "COUNTDOWN" else 0
            
            # Calculăm și aici numărul dinamic
            scale_factor = g.player_count / 4.0
            max_enemies = int(ENEMIES_PER_WAVE[min(g.wave, len(ENEMIES_PER_WAVE) - 1)] * scale_factor)
            max_enemies = max(1, max_enemies)
            
            return {
                "state": g.state, "wave": g.wave + 1, "total_waves": len(WAVE_DEFS),
                "core_lives": g.core_lives, "quakes_left": g.quakes_remaining,
                "enemy_count": len(g.enemies), "enemies_spawned": g.enemies_spawned,
                "enemies_total": max_enemies,
                "player_count": g.player_count, "countdown_remaining": cd,
                "elapsed_seconds": int(g.game_elapsed), "fail_wave": g.fail_wave,
            }

    def _loop(self):
        while True:
            try:
                payload = _json.dumps(self._snapshot()).encode()
                self._sock.sendto(payload, ("127.0.0.1", UDP_TELEMETRY_PORT))
            except Exception:
                pass
            time.sleep(self.INTERVAL)


# ──────────────────────────────────────────────────────────────────────────────
# NetworkManager  (LED frame send + full-matrix touch receive)
# ──────────────────────────────────────────────────────────────────────────────

class NetworkManager:
    def __init__(self, game: KernelDefenderGame):
        self.game   = game
        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_send.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.running   = True
        self.sequence_number = 0

        try:
            self.sock_recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self.sock_recv.bind(("0.0.0.0", UDP_LISTEN_PORT))
            print(f"[NM] Touch receiver on port {UDP_LISTEN_PORT}")
        except Exception as e:
            print(f"[NM] CRITICAL: could not bind recv socket: {e}")
            self.running = False

    def send_loop(self):
        while self.running:
            frame = self.game.render()
            self.send_packet(frame)
            time.sleep(0.05)

    def send_packet(self, frame_data):
        self.sequence_number = (self.sequence_number + 1) & 0xFFFF
        if self.sequence_number == 0:
            self.sequence_number = 1

        target_ip = UDP_SEND_IP
        port      = UDP_SEND_PORT

        r1, r2 = random.randint(0, 127), random.randint(0, 127)
        start = bytearray([
            0x75, r1, r2, 0x00, 0x08,
            0x02, 0x00, 0x00, 0x33, 0x44,
            (self.sequence_number >> 8) & 0xFF,
             self.sequence_number       & 0xFF,
            0x00, 0x00, 0x00, 0x0E, 0x00,
        ])
        self._send(start, target_ip, port)

        r1, r2 = random.randint(0, 127), random.randint(0, 127)
        fff0_payload = bytearray()
        for _ in range(NUM_CHANNELS):
            fff0_payload += bytes([(LEDS_PER_CHANNEL >> 8) & 0xFF, LEDS_PER_CHANNEL & 0xFF])
        fff0_internal = bytearray([
            0x02, 0x00, 0x00, 0x88, 0x77, 0xFF, 0xF0,
            (len(fff0_payload) >> 8) & 0xFF,
             len(fff0_payload)       & 0xFF,
        ]) + fff0_payload
        fff0_len = len(fff0_internal) - 1
        fff0_pkt = bytearray([0x75, r1, r2,
            (fff0_len >> 8) & 0xFF, fff0_len & 0xFF,
        ]) + fff0_internal
        fff0_pkt += bytearray([0x1E, 0x00])
        self._send(fff0_pkt, target_ip, port)

        chunk_size = 984
        idx = 1
        for i in range(0, len(frame_data), chunk_size):
            r1, r2 = random.randint(0, 127), random.randint(0, 127)
            chunk  = frame_data[i:i + chunk_size]
            internal = bytearray([
                0x02, 0x00, 0x00, 0x88, 0x77,
                (idx >> 8) & 0xFF, idx & 0xFF,
                (len(chunk) >> 8) & 0xFF, len(chunk) & 0xFF,
            ]) + chunk
            plen = len(internal) - 1
            pkt  = bytearray([0x75, r1, r2,
                (plen >> 8) & 0xFF, plen & 0xFF,
            ]) + internal
            pkt += bytearray([0x1E if len(chunk) == 984 else 0x36, 0x00])
            self._send(pkt, target_ip, port)
            idx += 1
            time.sleep(0.005)

        r1, r2 = random.randint(0, 127), random.randint(0, 127)
        end = bytearray([
            0x75, r1, r2, 0x00, 0x08,
            0x02, 0x00, 0x00, 0x55, 0x66,
            (self.sequence_number >> 8) & 0xFF,
             self.sequence_number       & 0xFF,
            0x00, 0x00, 0x00, 0x0E, 0x00,
        ])
        self._send(end, target_ip, port)

    def _send(self, pkt, ip, port):
        try:
            self.sock_send.sendto(pkt, (ip, port))
            self.sock_send.sendto(pkt, ("127.0.0.1", port))
        except Exception:
            pass

    def recv_loop(self):
        self.sock_recv.settimeout(1.0)
        while self.running:
            try:
                data, _ = self.sock_recv.recvfrom(4096)
            except socket.timeout:
                continue
            except Exception:
                continue

            if len(data) < 1373 or data[0] != 0x88:
                continue

            touches = []
            for ch in range(NUM_CHANNELS):
                base = 2 + ch * 171
                for led_idx in range(64):
                    byte_pos = base + 1 + led_idx
                    if byte_pos >= len(data):
                        break
                    if data[byte_pos] != 0xCC:
                        continue
                    row_in_channel = led_idx // 16
                    col_raw        = led_idx % 16
                    x = col_raw if row_in_channel % 2 == 0 else 15 - col_raw
                    y = ch * 4 + row_in_channel
                    if 0 <= x < BOARD_WIDTH and 0 <= y < BOARD_HEIGHT:
                        touches.append((x, y))

            with self.game.lock:
                self.game.active_touches = touches

    def start_bg(self):
        threading.Thread(target=self.send_loop, daemon=True).start()
        threading.Thread(target=self.recv_loop, daemon=True).start()


# ──────────────────────────────────────────────────────────────────────────────
# Entry point
# ──────────────────────────────────────────────────────────────────────────────

def game_thread_func(game):
    while game.running:
        game.tick()
        time.sleep(0.02)


if __name__ == "__main__":
    # Launch KD_Displays.py automatically in a separate process
    displays_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "KD_Displays.py")
    try:
        displays_proc = subprocess.Popen(
            [sys.executable, displays_path],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        print(f"[KD] Launched KD_Displays.py (PID {displays_proc.pid})")
    except Exception as e:
        displays_proc = None
        print(f"[KD] WARNING: Could not launch KD_Displays.py: {e}")

    game = KernelDefenderGame()
    net  = NetworkManager(game)
    tel  = TelemetryBroadcaster(game)
    cmd  = CommandReceiver(game)
    net.start_bg()

    gt = threading.Thread(target=game_thread_func, args=(game,), daemon=True)
    gt.start()

    print("=" * 55)
    print("  KERNEL DEFENDER – LED Matrix Game")
    print(f"  Send  → {UDP_SEND_IP}:{UDP_SEND_PORT}")
    print(f"  Recv  ← 0.0.0.0:{UDP_LISTEN_PORT}")
    print(f"  Tel   → localhost:{UDP_TELEMETRY_PORT}")
    print(f"  Cmd   ← 0.0.0.0:{UDP_CMD_PORT}")
    print("=" * 55)
    print("State: LOBBY – use KD_Displays.py to start game")
    print("Console commands: restart | status | quit")

    def console_loop():
        try:
            while game.running:
                try:
                    # input() blocks, so we run it in a daemon thread so it dies gracefully
                    raw = input("> ").strip().lower()
                except EOFError:
                    break

                if raw in ("quit", "exit"):
                    game.running = False
                elif raw == "restart":
                    game.cmd_restart()
                elif raw == "status":
                    with game.lock:
                        elapsed = int(game.game_elapsed)
                        print(f"  State   : {game.state}")
                        print(f"  Players : {game.player_count}")
                        print(f"  Wave    : {game.wave + 1}/{len(WAVE_DEFS)}")
                        print(f"  Lives   : {game.core_lives}/3")
                        print(f"  Quakes  : {game.quakes_remaining}/3")
                        print(f"  Enemies : {len(game.enemies)}")
                        print(f"  Elapsed : {elapsed}s")
                else:
                    if raw: print("Commands: restart | status | quit")
        except KeyboardInterrupt:
            game.running = False

    console_thread = threading.Thread(target=console_loop, daemon=True)
    console_thread.start()

    try:
        while game.running:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass

    game.running = False
    net.running  = False
    if displays_proc:
        displays_proc.terminate()
    print("Exiting Kernel Defender.")
