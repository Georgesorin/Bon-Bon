"""
The Eclipse — Evil Eye Cooperative Stealth Exorcism Game
Team Bon-Bon @ LedHack

Hardware: Evil Eye room (4 walls × 11 LEDs each)
  - LED 0 = Eye (with IR motion sensor)
  - LED 1-10 = Buttons (2 rows × 5)

Ports:
  Send LED data → device UDP :4626
  Receive button/IR ← device UDP :7800
  Telemetry → displays UDP :6666
  Commands  ← displays UDP :6667
"""

import socket
import struct
import time
import threading
import random
import os
import sys
import json as _json
import math
import subprocess

# ──────────────────────────────────────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────────────────────────────────────
# Are we in debug mode? (single distraction btn, console sim commands)
DEBUG_MODE = "--debug" in sys.argv or "-d" in sys.argv

if DEBUG_MODE:
    UDP_DEVICE_PORT    = 4626
    UDP_BUTTON_PORT    = 7800
else:
    UDP_DEVICE_PORT    = 12345
    UDP_BUTTON_PORT    = 54321

UDP_TELEMETRY_PORT = 6666
UDP_CMD_PORT       = 6667

NUM_CHANNELS       = 4
LEDS_PER_CHANNEL   = 11   # 0 = Eye, 1-10 = Buttons
FRAME_DATA_LEN     = 132  # 11 LEDs × 12 bytes per LED row

# ── Timing ────────────────────────────────────────────────────────────────────
ROUND_INTRO_TIME   = 4.0
HIDE_TIME          = 5.0
BAIT_WAIT_TIMEOUT  = 25.0
BAIT_RUN_TIME      = 6.0   # bait has this long to reach & press distraction
EYE_BLIND_TIME     = 5.0   # window while eye is blind
RESULT_PAUSE       = 3.0

TOTAL_ROUNDS       = 4
TOTAL_LIVES        = 3

# ── Colours (R, G, B) ────────────────────────────────────────────────────────
BLACK      = (0, 0, 0)
WHITE      = (255, 255, 255)
RED        = (255, 0, 0)
GREEN      = (0, 255, 0)
BLUE       = (0, 0, 255)
PURPLE     = (180, 0, 255)
YELLOW     = (255, 200, 0)
CYAN       = (0, 255, 255)
DARK_BLUE  = (0, 0, 80)
DARK_RED   = (80, 0, 0)
DIM_GREEN  = (0, 60, 0)
DIM_PURPLE = (50, 0, 70)
DIM_RED    = (40, 0, 0)
ORANGE     = (255, 140, 0)
GOLD       = (255, 180, 0)

# ── Room topology ─────────────────────────────────────────────────────────────
ADJACENT_WALLS = {1: [2, 4], 2: [1, 3], 3: [2, 4], 4: [1, 3]}

# Button pairs close together (for distraction – reachable with two hands)
CLOSE_PAIRS = [
    (1, 2), (2, 3), (3, 4), (4, 5),
    (6, 7), (7, 8), (8, 9), (9, 10),
    (1, 6), (2, 7), (3, 8), (4, 9), (5, 10),
]

# ── Checksum ──────────────────────────────────────────────────────────────────
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
    168, 45, 198, 6, 43, 11, 57, 88, 182, 84, 189, 29, 35, 143, 138, 171,
]


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────
def _pulse(t, speed=2.0, lo=30, hi=255):
    """Sinusoidal pulse brightness based on time."""
    return int(lo + (hi - lo) * (0.5 + 0.5 * math.sin(t * speed)))


def _scale(color, brightness):
    """Scale an (R,G,B) tuple by brightness 0..255."""
    f = max(0, min(255, brightness)) / 255.0
    return (int(color[0] * f), int(color[1] * f), int(color[2] * f))


# ──────────────────────────────────────────────────────────────────────────────
# Protocol Helpers
# ──────────────────────────────────────────────────────────────────────────────
def calc_checksum(data: bytes | bytearray) -> int:
    idx = sum(data) & 0xFF
    return PASSWORD_ARRAY[idx] if idx < len(PASSWORD_ARRAY) else 0


def build_start_packet(seq: int) -> bytes:
    pkt = bytearray([
        0x75, random.randint(0, 127), random.randint(0, 127),
        0x00, 0x08, 0x02, 0x00, 0x00, 0x33, 0x44,
        (seq >> 8) & 0xFF, seq & 0xFF, 0x00, 0x00,
    ])
    pkt.append(calc_checksum(pkt))
    return bytes(pkt)


def build_end_packet(seq: int) -> bytes:
    pkt = bytearray([
        0x75, random.randint(0, 127), random.randint(0, 127),
        0x00, 0x08, 0x02, 0x00, 0x00, 0x55, 0x66,
        (seq >> 8) & 0xFF, seq & 0xFF, 0x00, 0x00,
    ])
    pkt.append(calc_checksum(pkt))
    return bytes(pkt)


def build_command_packet(data_id: int, msg_loc: int, payload: bytes, seq: int) -> bytes:
    internal = bytes([
        0x02, 0x00, 0x00,
        (data_id >> 8) & 0xFF, data_id & 0xFF,
        (msg_loc >> 8) & 0xFF, msg_loc & 0xFF,
        (len(payload) >> 8) & 0xFF, len(payload) & 0xFF,
    ]) + payload
    hdr = bytes([
        0x75, random.randint(0, 127), random.randint(0, 127),
        (len(internal) >> 8) & 0xFF, len(internal) & 0xFF,
    ])
    pkt = bytearray(hdr + internal)
    pkt[10] = (seq >> 8) & 0xFF
    pkt[11] = seq & 0xFF
    pkt.append(calc_checksum(pkt))
    return bytes(pkt)


def build_fff0_packet(seq: int) -> bytes:
    payload = bytearray()
    for _ in range(NUM_CHANNELS):
        payload += bytes([(LEDS_PER_CHANNEL >> 8) & 0xFF, LEDS_PER_CHANNEL & 0xFF])
    return build_command_packet(0x8877, 0xFFF0, bytes(payload), seq)


def build_frame_data(led_states: dict) -> bytes:
    """Build 132-byte frame from {(ch 1-4, led 0-10): (r, g, b)}."""
    frame = bytearray(FRAME_DATA_LEN)
    for (ch, led), (r, g, b) in led_states.items():
        idx = ch - 1
        if 0 <= idx < NUM_CHANNELS and 0 <= led < LEDS_PER_CHANNEL:
            frame[led * 12 + idx]     = g
            frame[led * 12 + 4 + idx] = r
            frame[led * 12 + 8 + idx] = b
    return bytes(frame)


# ──────────────────────────────────────────────────────────────────────────────
# Discovery
# ──────────────────────────────────────────────────────────────────────────────
def discover_device(timeout=3.0):
    """Broadcast Evil Eye discovery. Returns device IP or fallback."""
    print("[DISCOVERY] Searching for Evil Eye hardware...")
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    sock.settimeout(timeout)

    # REZOLVARE PENTRU WINDOWS CU MAI MULTE PLĂCI DE REȚEA (ex: Wi-Fi + LAN)
    # Setăm socket-ul să folosească explicit placa "LedHack Virtual Network"
    try:
        sock.bind(('169.254.100.1', 0))
        print("[DISCOVERY] 📍 Bound to LedHack Virtual Network (169.254.100.1)")
    except Exception:
        # Fallback pentru simulatoare (Linux, Localhost, etc) unde IP-ul nu există
        try:
            sock.bind(('0.0.0.0', 0))
        except:
            pass

    pkt = bytes([0x67, 0x00, 0x00, 0x00])

    # Trimitere pachet către toate adresele de broadcast posibile în subnetul LedHack
    bcast_targets = ['169.254.100.255', '169.254.255.255', '255.255.255.255']
    for target in bcast_targets:
        try:
            sock.sendto(pkt, (target, UDP_DEVICE_PORT))
        except Exception:
            pass

    # Așteptăm răspunsul KX-HC04
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            data, addr = sock.recvfrom(64)
            if len(data) >= 13 and data[0] == 0x68:
                model = data[6:13].rstrip(b'\x00').decode('ascii', errors='replace')
                if 'KX-HC04' in model:
                    print(f"[DISCOVERY] ✅ Found Evil Eye at {addr[0]} ({model})")
                    sock.close()
                    return addr[0]
        except socket.timeout:
            break
        except Exception as e:
            print(f"[DISCOVERY] Error: {e}")
            break

    sock.close()
    print("[DISCOVERY] ⚠ No hardware found — using 127.0.0.1 (simulator)")
    return "127.0.0.1"


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
            print("[AUDIO] Pygame not found — audio disabled.")
            return

        try:
            pygame.mixer.init(frequency=44100, size=-16, channels=2, buffer=1024)
            self.enabled = True
            print("[AUDIO] Pygame mixer initialised.")
        except Exception as e:
            print(f"[AUDIO] Init failed: {e}")
            return

        # Try loading SFX from local _sfx or fallback to KD _sfx
        sfx_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_sfx")
        kd_sfx  = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                               "..", "Kernel Defender", "_sfx")

        for name, candidates in [
            ("bgm",       ["bgm.wav", "kd_bgm.mp3"]),
            ("gameover",  ["gameover.wav"]),
            ("you_won",   ["you_won.mp3"]),
            ("you_lost",  ["you_lost.mp3"]),
            ("start",     ["start.mp3"]),
            ("prepare",   ["prepare.mp3"]),
            ("drop",      ["drop.wav"]),
            ("move",      ["move.wav"]),
            ("quake",     ["quake.mp3"]),
            ("line",      ["line.wav"]),
        ]:
            for fn in candidates:
                for d in [sfx_dir, kd_sfx]:
                    path = os.path.join(d, fn)
                    if os.path.exists(path):
                        try:
                            self.sounds[name] = pygame.mixer.Sound(path)
                        except Exception:
                            pass
                        break
                if name in self.sounds:
                    break

        # Store BGM paths for music streaming
        self.bgm_tension = None
        self.bgm_victory = None
        self.bgm_defeat  = None
        for d in [sfx_dir, kd_sfx]:
            for fname, attr in [("kd_bgm.mp3", "bgm_tension"),
                                ("happy_bgm.mp3", "bgm_victory"),
                                ("sad_bgm.mp3", "bgm_defeat")]:
                p = os.path.join(d, fname)
                if os.path.exists(p) and getattr(self, attr) is None:
                    setattr(self, attr, p)

        print(f"[AUDIO] Loaded {len(self.sounds)} SFX.")

    def play_sfx(self, name):
        if self.enabled and name in self.sounds:
            try:
                self.sounds[name].play()
            except Exception:
                pass

    def play_music(self, path=None, loop=True):
        if not self.enabled:
            return
        target = path or self.bgm_tension
        if target and os.path.exists(target):
            try:
                pygame.mixer.music.load(target)
                pygame.mixer.music.play(-1 if loop else 0)
            except Exception as e:
                print(f"[AUDIO] Music error: {e}")

    def stop_music(self):
        if self.enabled:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass


audio = AudioManager()


# ──────────────────────────────────────────────────────────────────────────────
# Network Service
# ──────────────────────────────────────────────────────────────────────────────
class NetworkService:
    """Manages send (LED frames) and receive (button/IR events) threads."""

    def __init__(self, device_ip: str, game):
        self.game      = game
        self.device_ip = device_ip
        self.running   = True
        self._seq      = 0
        self._seq_lock = threading.Lock()

        # Send socket
        self.sock_send = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_send.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

        # Receive socket
        self.sock_recv = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock_recv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock_recv.settimeout(1.0)
        try:
            self.sock_recv.bind(("0.0.0.0", UDP_BUTTON_PORT))
            print(f"[NET] Listening for buttons on :{UDP_BUTTON_PORT}")
        except Exception as e:
            print(f"[NET] ⚠ Could not bind button port: {e}")

    def _next_seq(self):
        with self._seq_lock:
            self._seq = (self._seq + 1) & 0xFFFF
            if self._seq == 0:
                self._seq = 1
            return self._seq

    def send_loop(self):
        """Continuously sends LED frames (~20 FPS)."""
        while self.running:
            try:
                led_states = self.game.get_led_states()
                frame = build_frame_data(led_states)
                seq = self._next_seq()
                ep = (self.device_ip, UDP_DEVICE_PORT)

                self.sock_send.sendto(build_start_packet(seq), ep)
                time.sleep(0.008)
                self.sock_send.sendto(build_fff0_packet(seq), ep)
                time.sleep(0.008)
                self.sock_send.sendto(
                    build_command_packet(0x8877, 0x0000, frame, seq), ep)
                time.sleep(0.008)
                self.sock_send.sendto(build_end_packet(seq), ep)

                # Also send to localhost for simulator
                if self.device_ip != "127.0.0.1":
                    lo = ("127.0.0.1", UDP_DEVICE_PORT)
                    self.sock_send.sendto(build_start_packet(seq), lo)
                    time.sleep(0.008)
                    self.sock_send.sendto(build_fff0_packet(seq), lo)
                    time.sleep(0.008)
                    self.sock_send.sendto(
                        build_command_packet(0x8877, 0x0000, frame, seq), lo)
                    time.sleep(0.008)
                    self.sock_send.sendto(build_end_packet(seq), lo)

            except Exception:
                pass
            time.sleep(0.02)  # ~20 FPS

    def recv_loop(self):
        """Receives button/IR events from hardware."""
        EXPECTED_LEN = 687
        while self.running:
            try:
                data, _ = self.sock_recv.recvfrom(2048)
            except socket.timeout:
                continue
            except Exception:
                continue

            if len(data) != EXPECTED_LEN or data[0] != 0x88:
                continue

            # Parse button states
            states = {}
            for ch in range(1, NUM_CHANNELS + 1):
                base = 2 + (ch - 1) * 171
                for led in range(LEDS_PER_CHANNEL):
                    pos = base + 1 + led
                    if pos < len(data):
                        states[(ch, led)] = (data[pos] == 0xCC)

            with self.game.lock:
                self.game.button_states = states

    def start(self):
        threading.Thread(target=self.send_loop, daemon=True).start()
        threading.Thread(target=self.recv_loop, daemon=True).start()

    def stop(self):
        self.running = False


# ──────────────────────────────────────────────────────────────────────────────
# EclipseGame — State Machine
# ──────────────────────────────────────────────────────────────────────────────
if DEBUG_MODE:
    print("[DEBUG] 🔧 Debug mode ON — single distraction btn, sim commands enabled")

class EclipseGame:
    """
    Top-level states:  LOBBY, PLAYING, WIN, GAMEOVER
    Round states (within PLAYING):
        ROUND_INTRO → HIDE_PHASE → WAITING_BAIT → BAIT_RUN →
        EYE_BLIND → ROUND_SUCCESS | ROUND_FAIL
    """

    def __init__(self):
        self.lock = threading.RLock()
        self.running = True

        # ── Top-level state ───────────────────────────────────────────────────
        self.state       = "LOBBY"
        self.round_state = ""

        # ── Game data ─────────────────────────────────────────────────────────
        self.player_count = 2
        self.lives        = TOTAL_LIVES
        self.current_round = 0
        self.total_rounds  = TOTAL_ROUNDS
        self.score         = 0

        # ── Round data ────────────────────────────────────────────────────────
        self.eye_order          = []        # randomised wall order
        self.current_eye_wall   = 0         # which wall has the demonic eye
        self.distraction_wall   = 0         # which adjacent wall has distraction
        self.distraction_btns   = []        # [btn1, btn2] on distraction wall
        self.pattern_btns       = []        # [btn1, btn2, ...] on eye wall
        self.phase_start        = 0.0

        # ── Inputs ────────────────────────────────────────────────────────────
        self.button_states = {}   # (ch, led) -> bool, updated by recv thread
        self._sim_overrides = {}  # debug overrides: (ch, led) -> bool

        # ── LED render ────────────────────────────────────────────────────────
        self._led_states = {}     # (ch, led) -> (R, G, B)

        print("[ECLIPSE] Game ready. State: LOBBY")

    # ── Debug / Sim helpers ───────────────────────────────────────────────────
    def sim_press(self, ch, led, hold_seconds=0.5):
        """Simulate a button press from the console."""
        with self.lock:
            self._sim_overrides[(ch, led)] = True
            print(f"  [SIM] Pressing Wall {ch}, LED {led}")
        def _release():
            time.sleep(hold_seconds)
            with self.lock:
                self._sim_overrides.pop((ch, led), None)
        threading.Thread(target=_release, daemon=True).start()

    def sim_hold(self, ch, led):
        """Hold a button indefinitely (until sim_release)."""
        with self.lock:
            self._sim_overrides[(ch, led)] = True
            print(f"  [SIM] Holding Wall {ch}, LED {led}")

    def sim_release_all(self):
        with self.lock:
            self._sim_overrides.clear()
            print("  [SIM] Released all")

    def _btn(self, ch, led):
        """Check button state (real hardware OR sim override)."""
        return (self._sim_overrides.get((ch, led), False) or
                self.button_states.get((ch, led), False))

    # ── Commands ──────────────────────────────────────────────────────────────
    def cmd_start(self, player_count: int):
        with self.lock:
            if self.state != "LOBBY":
                return
            self.player_count = max(2, min(4, player_count))
            self.lives        = TOTAL_LIVES
            self.current_round = 0
            self.score         = 0

            # Randomise eye order
            self.eye_order = list(range(1, NUM_CHANNELS + 1))
            random.shuffle(self.eye_order)

            self.state = "PLAYING"
            self._start_round()
            audio.play_music()
            print(f"[ECLIPSE] Game started! {self.player_count} players, "
                  f"order: {self.eye_order}")

    def cmd_restart(self):
        with self.lock:
            audio.stop_music()
            self.__init__()
        print("[ECLIPSE] Restarted → LOBBY")

    def cmd_quit(self):
        self.running = False

    # ── Round setup ───────────────────────────────────────────────────────────
    def _start_round(self):
        """Set up a new round."""
        if self.current_round >= len(self.eye_order):
            self.state = "WIN"
            audio.stop_music()
            audio.play_sfx("you_won")
            audio.play_music(audio.bgm_victory, loop=False)
            print("[ECLIPSE] 🏆 All seals broken — VICTORY!")
            return

        self.current_eye_wall = self.eye_order[self.current_round]

        # Pick random adjacent wall for distraction
        adj_options = ADJACENT_WALLS[self.current_eye_wall]
        self.distraction_wall = random.choice(adj_options)

        # Pick distraction buttons (2 close together)
        self.distraction_btns = list(random.choice(CLOSE_PAIRS))

        # Pick pattern buttons (depends on player count)
        pattern_count = max(2, self.player_count)  # 2p→2, 3p→3, 4p→4
        available = list(range(1, 11))
        random.shuffle(available)
        self.pattern_btns = sorted(available[:pattern_count])

        self.round_state = "ROUND_INTRO"
        self.phase_start = time.time()

        audio.play_sfx("prepare")
        print(f"[ECLIPSE] Round {self.current_round + 1}/{self.total_rounds}: "
              f"Eye=Wall {self.current_eye_wall}, "
              f"Distraction=Wall {self.distraction_wall} btns {self.distraction_btns}, "
              f"Pattern btns {self.pattern_btns}")

    # ── Tick ──────────────────────────────────────────────────────────────────
    def tick(self):
        with self.lock:
            if self.state != "PLAYING":
                self._render()
                return

            now = time.time()
            elapsed = now - self.phase_start

            if self.round_state == "ROUND_INTRO":
                if elapsed >= ROUND_INTRO_TIME:
                    self.round_state = "HIDE_PHASE"
                    self.phase_start = now
                    audio.play_sfx("start")
                    print("[ECLIPSE] HIDE! 5 seconds!")

            elif self.round_state == "HIDE_PHASE":
                # Check if Eye detects anyone (warning only)
                if self._btn(self.current_eye_wall, 0):
                    audio.play_sfx("drop")  # Warning beep
                if elapsed >= HIDE_TIME:
                    self.round_state = "WAITING_BAIT"
                    self.phase_start = now
                    print("[ECLIPSE] Eye is watching... send the bait!")

            elif self.round_state == "WAITING_BAIT":
                # Wait for Eye IR to detect someone
                if self._btn(self.current_eye_wall, 0):
                    self.round_state = "BAIT_RUN"
                    self.phase_start = now
                    audio.play_sfx("quake")  # Alarm!
                    print("[ECLIPSE] BAIT DETECTED! Run to distraction!")
                elif elapsed >= BAIT_WAIT_TIMEOUT:
                    # Timeout — remind players
                    self.round_state = "WAITING_BAIT"
                    self.phase_start = now
                    print("[ECLIPSE] Still waiting for bait...")

            elif self.round_state == "BAIT_RUN":
                # Check if bait pressed distraction buttons
                d_wall = self.distraction_wall
                d_btns = self.distraction_btns
                if DEBUG_MODE:
                    # In debug: ANY of the distraction btns is enough
                    dist_ok = any(
                        self._btn(d_wall, b) for b in d_btns
                    )
                else:
                    # In prod: ALL distraction btns must be pressed
                    dist_ok = all(
                        self._btn(d_wall, b) for b in d_btns
                    )
                if dist_ok:
                    self.round_state = "EYE_BLIND"
                    self.phase_start = now
                    audio.play_sfx("line")  # GO sound
                    print("[ECLIPSE] EYE IS BLIND! GO GO GO!")
                elif elapsed >= BAIT_RUN_TIME:
                    # Bait didn't make it in time
                    self._round_fail("Bait didn't reach distraction in time!")

            elif self.round_state == "EYE_BLIND":
                # Check bait is STILL holding distraction buttons
                d_wall = self.distraction_wall
                d_btns = self.distraction_btns
                if DEBUG_MODE:
                    still_held = any(self._btn(d_wall, b) for b in d_btns)
                else:
                    still_held = all(self._btn(d_wall, b) for b in d_btns)
                if not still_held:
                    # Bait released! Eye wakes up!
                    self._round_fail("Bait released the buttons! Eye woke up!")
                    return

                # Check if pattern buttons are ALL pressed on eye wall
                e_wall = self.current_eye_wall
                if DEBUG_MODE:
                    # In debug: any pattern btn counts as all
                    all_pattern = any(
                        self._btn(e_wall, b) for b in self.pattern_btns
                    )
                else:
                    all_pattern = all(
                        self._btn(e_wall, b) for b in self.pattern_btns
                    )
                if all_pattern:
                    self._round_success()
                elif elapsed >= EYE_BLIND_TIME:
                    self._round_fail("Time's up! Pattern not completed!")

            elif self.round_state == "ROUND_SUCCESS":
                if elapsed >= RESULT_PAUSE:
                    self.current_round += 1
                    self._start_round()

            elif self.round_state == "ROUND_FAIL":
                if elapsed >= RESULT_PAUSE:
                    if self.lives <= 0:
                        self.state = "GAMEOVER"
                        audio.stop_music()
                        audio.play_sfx("you_lost")
                        audio.play_music(audio.bgm_defeat, loop=False)
                        print("[ECLIPSE] 💀 GAME OVER!")
                    else:
                        # Retry same round
                        self._start_round()

            self._render()

    def _round_success(self):
        self.round_state = "ROUND_SUCCESS"
        self.phase_start = time.time()
        self.score += 1
        audio.play_sfx("line")
        print(f"[ECLIPSE] ✅ Seal {self.current_round + 1} BROKEN! "
              f"Score: {self.score}")

    def _round_fail(self, reason: str):
        self.lives -= 1
        self.round_state = "ROUND_FAIL"
        self.phase_start = time.time()
        audio.play_sfx("gameover")
        print(f"[ECLIPSE] ❌ FAIL: {reason} Lives left: {self.lives}")

    # ── Rendering ─────────────────────────────────────────────────────────────
    def _render(self):
        """Update internal LED state dict based on current game state."""
        leds = {}
        now = time.time()

        # Start with all LEDs off
        for ch in range(1, 5):
            for led in range(LEDS_PER_CHANNEL):
                leds[(ch, led)] = BLACK

        if self.state == "LOBBY":
            # All eyes dim red, subtle pulse
            b = _pulse(now, speed=1.0, lo=20, hi=80)
            for ch in range(1, 5):
                leds[(ch, 0)] = _scale(RED, b)

        elif self.state == "WIN":
            # Rainbow celebration
            t = int(now * 4) % 10
            for ch in range(1, 5):
                leds[(ch, 0)] = GREEN
                for led in range(1, 11):
                    if (led + t) % 3 == 0:
                        leds[(ch, led)] = GREEN
                    elif (led + t) % 3 == 1:
                        leds[(ch, led)] = CYAN
                    else:
                        leds[(ch, led)] = GOLD

        elif self.state == "GAMEOVER":
            # All eyes red pulsing
            b = _pulse(now, speed=3.0, lo=40, hi=255)
            for ch in range(1, 5):
                leds[(ch, 0)] = _scale(RED, b)
                for led in range(1, 11):
                    leds[(ch, led)] = _scale(DARK_RED, b // 3)

        elif self.state == "PLAYING":
            ew = self.current_eye_wall
            dw = self.distraction_wall

            # Non-active eyes: very dim red ambient
            for ch in range(1, 5):
                if ch != ew:
                    leds[(ch, 0)] = _scale(RED, 15)

            if self.round_state == "ROUND_INTRO":
                # Eye: PURPLE pulsing
                b = _pulse(now, speed=2.0, lo=60, hi=255)
                leds[(ew, 0)] = _scale(PURPLE, b)
                # Pattern buttons: dim green pulse
                bp = _pulse(now, speed=1.5, lo=10, hi=80)
                for btn in self.pattern_btns:
                    leds[(ew, btn)] = _scale(GREEN, bp)

            elif self.round_state == "HIDE_PHASE":
                # Eye: PURPLE fast pulse (menacing)
                b = _pulse(now, speed=4.0, lo=80, hi=255)
                leds[(ew, 0)] = _scale(PURPLE, b)
                # Pattern: very dim green
                for btn in self.pattern_btns:
                    leds[(ew, btn)] = DIM_GREEN
                # Countdown: show on non-eye walls using LEDs as bar
                elapsed = now - self.phase_start
                remaining_frac = max(0, 1.0 - elapsed / HIDE_TIME)
                lit_count = int(remaining_frac * 10) + 1
                for ch in range(1, 5):
                    if ch != ew:
                        for i in range(1, min(lit_count + 1, 11)):
                            leds[(ch, i)] = _scale(YELLOW, 40)

            elif self.round_state == "WAITING_BAIT":
                # Eye: PURPLE slow pulse
                b = _pulse(now, speed=1.5, lo=100, hi=255)
                leds[(ew, 0)] = _scale(PURPLE, b)
                # Pattern: dim green
                for btn in self.pattern_btns:
                    leds[(ew, btn)] = DIM_GREEN
                # Distraction buttons: YELLOW pulsing
                bd = _pulse(now, speed=2.0, lo=40, hi=200)
                for btn in self.distraction_btns:
                    leds[(dw, btn)] = _scale(YELLOW, bd)

            elif self.round_state == "BAIT_RUN":
                # Eye: RED solid
                leds[(ew, 0)] = RED
                # Pattern: dim green still visible
                for btn in self.pattern_btns:
                    leds[(ew, btn)] = DIM_GREEN
                # Distraction buttons: YELLOW (bright if pressed)
                for btn in self.distraction_btns:
                    pressed = self._btn(dw, btn)
                    leds[(dw, btn)] = CYAN if pressed else YELLOW
                # Countdown bar on distraction wall (other buttons)
                elapsed = now - self.phase_start
                remaining = max(0, 1.0 - elapsed / BAIT_RUN_TIME)
                lit = int(remaining * 8) + 1
                others = [b for b in range(1, 11)
                          if b not in self.distraction_btns]
                for i, b in enumerate(others[:lit]):
                    leds[(dw, b)] = _scale(ORANGE, 30)

            elif self.round_state == "EYE_BLIND":
                # Eye: DARK BLUE (blind!)
                b = _pulse(now, speed=1.0, lo=30, hi=80)
                leds[(ew, 0)] = _scale(DARK_BLUE, b)
                # Pattern buttons: BRIGHT GREEN (GO NOW!)
                bg = _pulse(now, speed=6.0, lo=150, hi=255)
                for btn in self.pattern_btns:
                    pressed = self._btn(ew, btn)
                    leds[(ew, btn)] = WHITE if pressed else _scale(GREEN, bg)
                # Distraction: CYAN (held)
                for btn in self.distraction_btns:
                    leds[(dw, btn)] = CYAN
                # Countdown: remaining blind time on other non-eye walls
                elapsed = now - self.phase_start
                remaining = max(0, 1.0 - elapsed / EYE_BLIND_TIME)
                lit = int(remaining * 10) + 1
                for ch in range(1, 5):
                    if ch not in (ew, dw):
                        for i in range(1, min(lit + 1, 11)):
                            leds[(ch, i)] = _scale(BLUE, 30)

            elif self.round_state == "ROUND_SUCCESS":
                # All target wall LEDs flash green
                flash = int(now * 6) % 2 == 0
                for led in range(LEDS_PER_CHANNEL):
                    leds[(ew, led)] = GREEN if flash else BLACK
                # Other walls: brief green
                for ch in range(1, 5):
                    if ch != ew:
                        leds[(ch, 0)] = _scale(GREEN, 60) if flash else BLACK

            elif self.round_state == "ROUND_FAIL":
                # Eye flashes red intensely
                flash = int(now * 5) % 2 == 0
                leds[(ew, 0)] = RED if flash else BLACK
                # All walls dim red
                b = 50 if flash else 20
                for ch in range(1, 5):
                    for led in range(1, 11):
                        leds[(ch, led)] = _scale(RED, b)

        self._led_states = leds

    def get_led_states(self):
        with self.lock:
            return dict(self._led_states)

    def get_telemetry(self):
        with self.lock:
            elapsed = time.time() - self.phase_start if self.phase_start else 0
            countdown = 0

            if self.round_state == "HIDE_PHASE":
                countdown = max(0, HIDE_TIME - elapsed)
            elif self.round_state == "BAIT_RUN":
                countdown = max(0, BAIT_RUN_TIME - elapsed)
            elif self.round_state == "EYE_BLIND":
                countdown = max(0, EYE_BLIND_TIME - elapsed)
            elif self.round_state == "ROUND_INTRO":
                countdown = max(0, ROUND_INTRO_TIME - elapsed)

            return {
                "state":         self.state,
                "round_state":   self.round_state,
                "round":         self.current_round + 1,
                "total_rounds":  self.total_rounds,
                "lives":         self.lives,
                "player_count":  self.player_count,
                "countdown":     round(countdown, 1),
                "current_eye":   self.current_eye_wall,
                "distraction_wall": self.distraction_wall,
                "score":         self.score,
            }


# ──────────────────────────────────────────────────────────────────────────────
# Telemetry Broadcaster  (game → displays)
# ──────────────────────────────────────────────────────────────────────────────
class TelemetryBroadcaster:
    INTERVAL = 0.20

    def __init__(self, game: EclipseGame):
        self.game  = game
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        threading.Thread(target=self._loop, daemon=True).start()
        print(f"[TEL] Telemetry → localhost:{UDP_TELEMETRY_PORT}")

    def _loop(self):
        while self.game.running:
            try:
                payload = _json.dumps(self.game.get_telemetry()).encode()
                self._sock.sendto(payload, ("127.0.0.1", UDP_TELEMETRY_PORT))
            except Exception:
                pass
            time.sleep(self.INTERVAL)


# ──────────────────────────────────────────────────────────────────────────────
# Command Receiver  (displays → game)
# ──────────────────────────────────────────────────────────────────────────────
class CommandReceiver:
    def __init__(self, game: EclipseGame):
        self.game  = game
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.settimeout(1.0)
        try:
            self._sock.bind(("0.0.0.0", UDP_CMD_PORT))
            print(f"[CMD] Commands ← :{UDP_CMD_PORT}")
        except Exception as e:
            print(f"[CMD] ⚠ Bind error: {e}")
        threading.Thread(target=self._loop, daemon=True).start()

    def _loop(self):
        while self.game.running:
            try:
                data, _ = self._sock.recvfrom(512)
                cmd = _json.loads(data.decode())
                action = cmd.get("cmd", "")
                if action == "start":
                    self.game.cmd_start(int(cmd.get("players", 2)))
                elif action == "restart":
                    self.game.cmd_restart()
                elif action == "quit":
                    self.game.cmd_quit()
            except socket.timeout:
                continue
            except Exception:
                continue


# ──────────────────────────────────────────────────────────────────────────────
# Game Thread
# ──────────────────────────────────────────────────────────────────────────────
def game_thread_func(game):
    while game.running:
        game.tick()
        time.sleep(0.02)  # 50 Hz tick


# ──────────────────────────────────────────────────────────────────────────────
# Entry Point
# ──────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    # Auto-launch displays
    displays_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), "Eclipse_Displays.py")
    displays_proc = None
    if os.path.exists(displays_path):
        try:
            displays_proc = subprocess.Popen(
                [sys.executable, displays_path],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print(f"[ECLIPSE] Launched Eclipse_Displays.py (PID {displays_proc.pid})")
        except Exception as e:
            print(f"[ECLIPSE] ⚠ Could not launch displays: {e}")

    if DEBUG_MODE:
        device_ip = "127.0.0.1"
        print(f"[HARDWARE] DEBUG MODE: Using local simulator at {device_ip} on ports {UDP_DEVICE_PORT}/{UDP_BUTTON_PORT}")
    else:
        # Discover hardware / Hardcoded IPs from mentor
        device_ip = "169.254.162.11"
        print(f"[HARDWARE] Using mentor-provided IP: {device_ip} and ports {UDP_DEVICE_PORT}/{UDP_BUTTON_PORT}")

    # Create game & services
    game = EclipseGame()
    net  = NetworkService(device_ip, game)
    tel  = TelemetryBroadcaster(game)
    cmd  = CommandReceiver(game)
    net.start()

    # Game thread
    gt = threading.Thread(target=game_thread_func, args=(game,), daemon=True)
    gt.start()

    print("=" * 55)
    print("   THE ECLIPSE — Evil Eye Stealth Exorcism")
    print(f"   Device  → {device_ip}:{UDP_DEVICE_PORT}")
    print(f"   Buttons ← 0.0.0.0:{UDP_BUTTON_PORT}")
    print(f"   Telemetry → localhost:{UDP_TELEMETRY_PORT}")
    print(f"   Commands  ← 0.0.0.0:{UDP_CMD_PORT}")
    print("=" * 55)
    print("State: LOBBY — use Displays to start or type: start <N>")
    if DEBUG_MODE:
        print("═" * 55)
        print("  🔧 DEBUG COMMANDS:")
        print("  sim eye       — simulate Eye IR detection")
        print("  sim distract  — simulate bait pressing distraction")
        print("  sim pattern   — simulate pattern buttons pressed")
        print("  sim release   — release all simulated buttons")
        print("  skip          — skip current phase")
        print("  test          — auto-play a full round")
        print("═" * 55)
    print("Commands: start <N> | restart | status | quit")

    def _auto_test_round():
        """Automatically play through one complete round with sim commands."""
        print("\n[TEST] ═══ Auto-playing one round... ═══")
        # Wait for WAITING_BAIT
        for _ in range(200):
            t = game.get_telemetry()
            if t["round_state"] == "WAITING_BAIT":
                break
            time.sleep(0.1)
        else:
            print("[TEST] Timed out waiting for WAITING_BAIT")
            return

        print("[TEST] Step 1: Triggering Eye IR (bait steps out)...")
        ew = t["current_eye"]
        game.sim_press(ew, 0, hold_seconds=0.5)
        time.sleep(1.0)

        t = game.get_telemetry()
        if t["round_state"] != "BAIT_RUN":
            print(f"[TEST] Unexpected state: {t['round_state']}")
            return

        print("[TEST] Step 2: Bait pressing distraction buttons...")
        dw = game.distraction_wall
        for b in game.distraction_btns:
            game.sim_hold(dw, b)
        time.sleep(1.5)

        t = game.get_telemetry()
        if t["round_state"] != "EYE_BLIND":
            print(f"[TEST] Expected EYE_BLIND, got: {t['round_state']}")
            game.sim_release_all()
            return

        print("[TEST] Step 3: Others pressing pattern buttons...")
        for b in game.pattern_btns:
            game.sim_press(ew, b, hold_seconds=2.0)
        time.sleep(1.0)

        t = game.get_telemetry()
        print(f"[TEST] Result: {t['round_state']}")
        game.sim_release_all()
        print("[TEST] ═══ Round test complete ═══\n")

    def console_loop():
        try:
            while game.running:
                try:
                    raw = input("> ").strip().lower()
                except EOFError:
                    break

                if raw in ("quit", "exit"):
                    game.running = False
                elif raw == "restart":
                    game.cmd_restart()
                elif raw.startswith("start"):
                    parts = raw.split()
                    n = int(parts[1]) if len(parts) > 1 else 2
                    game.cmd_start(n)
                elif raw == "status":
                    t = game.get_telemetry()
                    print(f"  State      : {t['state']}")
                    print(f"  Round state: {t['round_state']}")
                    print(f"  Round      : {t['round']}/{t['total_rounds']}")
                    print(f"  Lives      : {t['lives']}/{TOTAL_LIVES}")
                    print(f"  Players    : {t['player_count']}")
                    print(f"  Eye Wall   : {t['current_eye']}")
                    print(f"  Distract   : Wall {game.distraction_wall} btns {game.distraction_btns}")
                    print(f"  Pattern    : btns {game.pattern_btns}")
                    print(f"  Score      : {t['score']}")
                    print(f"  Countdown  : {t['countdown']}s")

                # ── Debug / Sim commands ──────────────────────────────────────
                elif raw == "sim eye":
                    ew = game.current_eye_wall
                    if ew:
                        game.sim_press(ew, 0, hold_seconds=0.5)
                    else:
                        print("  No active eye wall")
                elif raw == "sim distract":
                    dw = game.distraction_wall
                    if dw and game.distraction_btns:
                        for b in game.distraction_btns:
                            game.sim_hold(dw, b)
                    else:
                        print("  No distraction set")
                elif raw == "sim pattern":
                    ew = game.current_eye_wall
                    if ew and game.pattern_btns:
                        for b in game.pattern_btns:
                            game.sim_press(ew, b, hold_seconds=2.0)
                    else:
                        print("  No pattern set")
                elif raw == "sim release":
                    game.sim_release_all()
                elif raw == "skip":
                    with game.lock:
                        game.phase_start = time.time() - 999
                    print("  ⏩ Phase timer expired")
                elif raw == "test":
                    threading.Thread(target=_auto_test_round,
                                     daemon=True).start()
                else:
                    if raw:
                        print("Commands: start <N> | restart | status | quit")
                        if DEBUG_MODE:
                            print("Debug:    sim eye | sim distract | sim pattern | "
                                  "sim release | skip | test")
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
    net.stop()
    if displays_proc:
        displays_proc.terminate()
    print("[ECLIPSE] Exited.")
