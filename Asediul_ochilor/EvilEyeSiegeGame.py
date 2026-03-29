import time
import random
import threading
import json
import os
import sys
from enum import Enum

# --- Sistemul de Configurare as in Simulator/Evil Eye ---
_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "asediul_config.json")

def _load_config():
    defaults = {
        # --- Configurare Rețea / Porturi ---
        "device_ip": "169.254.182.11",     # Adresa unde trimitem date (Spre Simulator sau Hardware EvilEye)
        "send_port": 4626,            # Portul către care TRIMITEM frame-uri cu culori
        "recv_port": 7800,            # Portul pe care ASCULTĂM apăsările de butoane
        "bind_ip": "255.255.255.255",         # IP-ul pe care facem host la pachetele de intrare
        
        # --- Parametri Joc ---
        'num_players': 4,           # Dacă e <= 2, Faza 3 e o repetare pentru Faza 2
        'phase1_duration': 30.0,    # Durata fixă a Fazei 1
        'time_per_blue_ph1': 3.0,   # Secunde adăugate în bank pentru fiecare albastru prins
        'phase2_target': 10,        # Numărul de butoane albastre pentru a sparge primul ochi (Faza 2)
        'phase3_target': 15,        # Numărul de butoane albastre pentru a sparge Boss-ul (Faza 3)
        'phase1_max_blues': 4,      # Pătrate albastre simultane pe pereți în Faza 1
        'phase23_max_blues': 3,     # Pătrate albastre simultane în luptă
        'min_phase2_time': 20.0,    # Timp mimim garantat de siguranță la intrarea în Faza 2
        'strict_hold': True         # True = Mâna trebuie ținută pe galben (Hardware). False = Pt Simulator Coop
    }
    
    try:
        # Dacă fișierul există, preluăm ce e în el și combinăm cu default-urile noastre
        if os.path.exists(_CONFIG_FILE):
            with open(_CONFIG_FILE, 'r', encoding="utf-8") as f:
                data = json.load(f)
                defaults.update(data)
        else:
            # Dacă nu, IL CREĂM fizic folosind setările standard
            with open(_CONFIG_FILE, 'w', encoding="utf-8") as f:
                json.dump(defaults, f, indent=4)
    except:
        pass
        
    return defaults


class GameState(Enum):
    LOBBY = 0
    PHASE_1 = 1
    PHASE_2 = 2
    PHASE_2_REPEAT = 3
    PHASE_3 = 4
    GAME_OVER = 5
    VICTORY = 6


class EvilEyeSiegeGame:
    """
    Logica principală a jocului "Asediul celor 4 Ochi demonici"
    Acum cu propria gestiune de configurări și porting.
    """
    def __init__(self, light_service=None, config=None):
        self.light = light_service
        
        # Încărcare procedură de fișier AsediulConfig
        self._cfg = _load_config()
        if config:
            self._cfg.update(config)
            
        self.config = self._cfg

        # Starea sistemului
        self.state = GameState.LOBBY
        self.phase_timer = 0.0          # Cronometru descrescător care dictează Faza curentă
        self.time_bank = 0.0            # Timpul stocat în Faza 1
        
        self.blue_count = 0             # Score vizual pt Faza 1
        self.current_blues_caught = 0   # Progres stadiu Faza 2/3 (se resetează per fază)
        
        self.active_blues = []          # Lista de (canal, index_led) pt pătrate albastre aprinse
        self.red_eyes = []              # Lista canalelor pe care a apărut ochi roșu
        self.yellow_locs = []           # Locațiile triggerelor Galbene (de imobilizare)
        self.yellow_states = {}         # Dictionar {(ch, led): bool} pentru menținerea apăsării
        self.yellow_release_timers = {} # Timers pentru debouncing la eliberare (hardware fix)
        
        # Multithreading / Siguranță
        self._running = False
        self._thread = None
        self._lock = threading.Lock()

    def set_config(self, key, value):
        with self._lock:
            self.config[key] = value

    def start_game(self):
        """Pornește ciclul de joc direct din Faza 1"""
        with self._lock:
            self.state = GameState.PHASE_1
            self.phase_timer = self.config['phase1_duration']
            self.time_bank = 0.0
            self.blue_count = 0
            
            self.active_blues = []
            self.red_eyes = []
            self.yellow_locs = []
            self.yellow_states = {}
            self.yellow_release_timers = {}
            
            if self.light:
                self.light.all_off()
            
            print(f"\n=============================================")
            print(f"⚔️ ASEDILUL CELOR 4 OCHI DEMONICI A ÎNCEPUT! ⚔️")
            print(f"FAZA 1 (Adunarea Timpului) - Ai {int(self.phase_timer)} secunde")
            print(f"Apăsați cât mai multe butoane ALBASTRE!")
            print(f"=============================================\n")
            
        if not self._running:
            self._running = True
            # Pornim o buclă Tick în background care scade timerul
            self._thread = threading.Thread(target=self._game_loop, daemon=True)
            self._thread.start()

    def stop_game(self):
        """Forțează oprirea jocului"""
        self._running = False
        self.state = GameState.LOBBY
        if self.light:
            self.light.all_off()

    def handle_button_event(self, ch, led, is_pressed, is_disconnected=False):
        """Funcția care interceptează pachetele UDP hardware"""
        # Corectare decalaj liniar si circular (rezolva problema cand butonul sarea la 11 sau la peretele gresit)
        offset = self.config.get('button_offset', 0)
        if offset != 0 and led > 0:
            g_idx = (ch - 1) * 10 + (led - 1)
            g_idx = (g_idx - offset) % 40
            ch = (g_idx // 10) + 1
            led = (g_idx % 10) + 1
            
        if led <= 0:
            return 
            
        with self._lock:
            if self.state in (GameState.GAME_OVER, GameState.VICTORY, GameState.LOBBY):
                return
                
            loc = (ch, led)
            
            if is_pressed:
                # DEBUG pt camera fizica:
                if self.state == GameState.PHASE_1:
                    print(f"  [DEBUG-HARDWARE] S-a inregistrat lovitură pe Peretele {ch}, Buton {led}. (Asteptate: {self.active_blues})")
                
                # ─── LOGICĂ PENTRU APĂSARE ───
                
                # Faza 1
                if self.state == GameState.PHASE_1:
                    if loc in self.active_blues:
                        self.active_blues.remove(loc)
                        self.blue_count += 1
                        self.time_bank += self.config['time_per_blue_ph1']
                        
                        if self.light:
                            self.light.set_led(ch, led, 0, 0, 0)
                        print(f"  + Albastru prins! Banca timpului amânat e: {self.time_bank}s")
                        
                # Faza 2 / 3
                elif self.state in (GameState.PHASE_2, GameState.PHASE_3, GameState.PHASE_2_REPEAT):
                    # Buton GALBEN (Hold point pt Imobilizare)
                    if loc in self.yellow_locs:
                        if loc in self.yellow_release_timers:
                            del self.yellow_release_timers[loc]
                        if not self.yellow_states.get(loc, False):
                            self.yellow_states[loc] = True
                            print(f"  > Imobilizare confirmată! Buton Galben ({ch},{led}) APĂSAT!")
                        
                    # Buton ALBASTRU (Atac pe boss)
                    elif loc in self.active_blues:
                        if all(self.yellow_states.values()) and len(self.yellow_states) > 0:
                            self.active_blues.remove(loc)
                            self.current_blues_caught += 1
                            if self.light:
                                self.light.set_led(ch, led, 0, 0, 0)
                            
                            target = self.config['phase2_target'] if self.state == GameState.PHASE_2 else self.config['phase3_target']
                            print(f"   Lovitură asupra Ochiului! Progres: {self.current_blues_caught} / {target}")
                            
                            self._check_phase_completion()
                            
            else:
                # ─── LOGICĂ PENTRU ELIBERARE BUTON ───
                if self.state in (GameState.PHASE_2, GameState.PHASE_3, GameState.PHASE_2_REPEAT):
                    if loc in self.yellow_locs:
                        if self.yellow_states.get(loc, False) and loc not in self.yellow_release_timers:
                            # Start timer de toleranță de pană la 1.5sec pentru hardware-bouncing
                            self.yellow_release_timers[loc] = time.time()


    def _game_loop(self):
        fps = 20
        dt = 1.0 / fps
        while self._running:
            with self._lock:
                self._tick(dt)
            time.sleep(dt)


    def _tick(self, dt):
        """Logica Cronologică Temporală"""
        if self.state in (GameState.LOBBY, GameState.GAME_OVER, GameState.VICTORY):
            return
            
        # --- VERIFICARE DEBOUNCING PENTRU ELIBERARE BUTON GALBEN (Toleranta Hardware) ---
        if self.state in (GameState.PHASE_2, GameState.PHASE_3, GameState.PHASE_2_REPEAT):
            current_time = time.time()
            for loc in list(self.yellow_release_timers.keys()):
                if current_time - self.yellow_release_timers[loc] > 1.5: # Toleranta 1.5 secunde
                    ch, y_led = loc
                    self.yellow_states[loc] = False
                    del self.yellow_release_timers[loc]
                    
                    if self.config.get('strict_hold', True):
                        self._trigger_game_over(reason=f"Mână eliberată de pe butonul Galben ({ch},{y_led}) prea devreme!")
                    else:
                        print(f"  [Simulator Mode] Mână eliberată de pe Galben ({ch},{y_led}) (ignorată).")
        
        if self.state in (GameState.GAME_OVER, GameState.VICTORY):
            return
            
        self.phase_timer -= dt
        
        # 1. VERIFICARE EXPIRARE TIMP
        if self.phase_timer <= 0:
            if self.state == GameState.PHASE_1:
                self._start_phase_2()
            else:
                self._trigger_game_over(reason="Timpul alocat a expirat!")
            return

        # 2. LOGICĂ DE SPAWNING Butoane
        # Spawn Faza 1
        if self.state == GameState.PHASE_1:
            if len(self.active_blues) < self.config['phase1_max_blues']:
                self._spawn_blue()
                
        # Spawn Faza Luptă (doar cu condiția de Hold imobillizare valida)
        elif self.state in (GameState.PHASE_2, GameState.PHASE_3, GameState.PHASE_2_REPEAT):
            if all(self.yellow_states.values()) and len(self.yellow_states) > 0:
                if len(self.active_blues) < self.config['phase23_max_blues']:
                    self._spawn_blue(exclude_channels=self.red_eyes)


    def _spawn_blue(self, exclude_channels=None):
        if exclude_channels is None: exclude_channels = []
            
        available_walls = [w for w in [1, 2, 3, 4] if w not in exclude_channels]
        if not available_walls: return
            
        ch = random.choice(available_walls)
        led = random.randint(1, 10) 
        loc = (ch, led)
        
        if loc not in self.active_blues and loc not in self.yellow_locs:
            self.active_blues.append(loc)
            if self.light:
                self.light.set_led(ch, led, 0, 0, 255) # Albastru RGB


    def _start_phase_2(self):
        self.state = GameState.PHASE_2
        self.phase_timer = max(self.time_bank, self.config['min_phase2_time'])
        
        self.current_blues_caught = 0
        if self.light: self.light.all_off()
        self.active_blues.clear()
        
        wall = random.choice([1, 2, 3, 4])
        self.red_eyes = [wall]
        y_led = random.randint(1, 10)
        self.yellow_locs = [(wall, y_led)]
        self.yellow_states = {(wall, y_led): False}
        self.yellow_release_timers = {}
        
        if self.light:
            self.light.set_led(wall, 0, 255, 0, 0)
            self.light.set_led(wall, y_led, 255, 255, 0)
        
        print(f"\n=============================================")
        print(f"FAZA 2 (Imobilizarea Primului Ochi)")
        print(f"Timp Extras: {int(self.phase_timer)}s | Țintă: {self.config['phase2_target']} Apăsări")
        print(f"!! OBLIGATORIU: Ține apăsat pe Peretele {wall}, butonul Galben index {y_led} !!")
        print(f"=============================================")


    def _start_phase_3(self):
        self.state = GameState.PHASE_3
        
        # Jucătorii sunt penalizați cu -15 secunde la trecerea spre Etapa 3
        self.phase_timer -= 15.0
        if self.phase_timer <= 0:
            self._trigger_game_over(reason="Timpul nu a fost suficient pentru a supraviețui penalizării de -15s spre Faza 3!")
            return
            
        self.current_blues_caught = 0
        if self.light: self.light.all_off()
        self.active_blues.clear()
        
        w1, w2 = random.sample([1, 2, 3, 4], 2)
        self.red_eyes = [w1, w2]
        y1_led = random.randint(1, 10)
        y2_led = random.randint(1, 10)
        
        self.yellow_locs = [(w1, y1_led), (w2, y2_led)]
        self.yellow_states = {(w1, y1_led): False, (w2, y2_led): False}
        self.yellow_release_timers = {}
        
        if self.light:
            self.light.set_led(w1, 0, 255, 0, 0)
            self.light.set_led(w2, 0, 255, 0, 0)
            self.light.set_led(w1, y1_led, 255, 255, 0)
            self.light.set_led(w2, y2_led, 255, 255, 0)
        
        print(f"\n=============================================")
        print(f"FAZA 3 (BOSS BATTLE FINAL - 2 OCHI)")
        print(f"Timp Extins Rămas: {int(self.phase_timer)}s | Țintă: {self.config['phase3_target']} Apăsări")
        print(f"!! HOLD pe peretele {w1}/{y1_led} SI Peretele {w2}/{y2_led} !!")
        print(f"=============================================")


    def _start_phase_2_repeat(self):
        self.state = GameState.PHASE_2_REPEAT
        
        # Se scad aceleași 15 secunde conform regulei
        self.phase_timer -= 15.0
        if self.phase_timer <= 0:
            self._trigger_game_over(reason="Timpul nu a fost suficient pentru a supraviețui penalizării de -15s spre ultima fază!")
            return
            
        self.current_blues_caught = 0
        if self.light: self.light.all_off()
        
        wall = random.choice([1, 2, 3, 4])
        self.red_eyes = [wall]
        y_led = random.randint(1, 10)
        self.yellow_locs = [(wall, y_led)]
        self.yellow_states = {(wall, y_led): False}
        self.yellow_release_timers = {}
        
        if self.light:
            self.light.set_led(wall, 0, 255, 0, 0)
            self.light.set_led(wall, y_led, 255, 255, 0)
        
        print(f"\n=============================================")
        print(f"FAZA 2 REPEAT (Deoarece <= 2 Jucători)")
        print(f"Hold obligatoriu pe {wall}. Ținta finală este de {self.config['phase3_target']}!")
        print(f"=============================================")


    def _check_phase_completion(self):
        if self.state == GameState.PHASE_2:
            if self.current_blues_caught >= self.config['phase2_target']:
                print(">> Faza 2 COMPLETĂ! Ochi înfrânt <<")
                if self.config['num_players'] <= 2:
                    self._start_phase_2_repeat()
                else:
                    self._start_phase_3()
                    
        elif self.state in (GameState.PHASE_3, GameState.PHASE_2_REPEAT):
            if self.current_blues_caught >= self.config['phase3_target']:
                self._trigger_victory()


    def _trigger_game_over(self, reason):
        self.state = GameState.GAME_OVER
        if self.light: self.light.set_all(255, 0, 0)
        print(f"\n*********************************************")
        print(f"                  GAME OVER                  ")
        print(f" MOTIV: {reason} ")
        print(f"*********************************************")


    def _trigger_victory(self):
        self.state = GameState.VICTORY
        if self.light: self.light.set_all(0, 255, 0)
        score = int(self.phase_timer)
        print(f"\n*********************************************")
        print(f"                  VICTORIE!                  ")
        print(f" Timp rămas adunat din Etapele 2 și 3: {score} secunde")
        print(f"*********************************************")


# =========================================================================================
# Execuția directă (Împletirea Logicii de Configurare JSON cu porturile din EvilEye)
# Acționează exact ca Simulatorul când rulezi "python3 EvilEyeSiegeGame.py"
# =========================================================================================
if __name__ == "__main__":
    print("\n--------------------------------------------------------------")
    print("      LANSATOR JOC : ASEDIUL CELOR 4 OCHI DEMONICI              ")
    print("--------------------------------------------------------------")
    
    # Adaugăm calea relativă către LightService din root-ul de proiect 
    PARENT_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), '..'))
    if PARENT_DIR not in sys.path:
        sys.path.append(PARENT_DIR)
        
    try:
        from EvilEye.Controller import LightService
        from EvilEye.NetworkScanner import auto_discover_evileye
    except ImportError:
        print("EROARE: Nu pot gasi fisierele EvilEye necesare.")
        sys.exit(1)

    # 1. Încărcăm configurația 
    cfg = _load_config()
    
    print("\n--- INIȚIERE CONEXIUNE ASEDIUL ---")
    discovered_ip = auto_discover_evileye(timeout=1.0)
    
    if discovered_ip:
        device_ip = discovered_ip
        send_port = 4626
        recv_port = 7800
        print(f"\n> [AUTO] Joc atașat la IP-ul hardware real din ESCAPE ROOM: {device_ip}\n")
    else:
        device_ip = cfg.get("device_ip", "127.0.0.1")
        send_port = cfg.get("send_port", 4626)
        recv_port = cfg.get("recv_port", 7800)
        print(f"\n> [MANUAL/SIMULATOR] Folosim setările curente din config: {device_ip}:{send_port}\n")

    bind_ip   = cfg.get("bind_ip", "0.0.0.0")

    # 2. Conectam Network Service-ul standard
    service = LightService()
    service.set_device(device_ip, send_port)
    service.set_recv_port(recv_port)
    service.set_bind_ip(bind_ip)
    
    service.start_polling()
    service.start_receiver()
    
    # 3. Creăm o instanță a Game Engine-ului nostru
    game = EvilEyeSiegeGame(light_service=service)

    # Routing intercepție apasari catre logica jocului
    def on_btn(ch, led, is_triggered, is_disconnected):
        game.handle_button_event(ch, led, is_pressed=is_triggered)
    service.on_button_state = on_btn
    
    # -------------------------------------------------------------
    # MENIU DE START GUI (Selectare Jucători)
    # -------------------------------------------------------------
    import tkinter as tk
    
    root = tk.Tk()
    root.title("Meniu Asediul Ochilor")
    root.geometry("900x550")
    root.configure(bg="#111111")
    root.eval('tk::PlaceWindow . center') # Aducere in centrul ecranului
    
    # Variabilă de control să vedem dacă am apăsat un buton sau am închis fereastra cu X
    game_started = [False]
    
    def start_with_players(num_players):
        game.set_config('num_players', num_players)
        game.set_config('button_offset', 1)  # Activ permanent +1 pentru hardware
        print(f"\n[MENIU] Au fost selectați {num_players} jucători. START JOC!")
        game_started[0] = True
        root.destroy()
        
    # UI Elements
    tk.Label(root, text="ASEDIUL CELOR 4 OCHI DEMONICI", fg="#ff3333", bg="#111", font=("Consolas", 26, "bold")).pack(pady=(30, 10))
    tk.Label(root, text="Selectează câți jucători participă:", fg="white", bg="#111", font=("Consolas", 14)).pack(pady=5)
    
    btn_frame = tk.Frame(root, bg="#111")
    btn_frame.pack(pady=20)
    
    # Buton 2 Jucători
    b2 = tk.Button(btn_frame, 
              text="2 PLAYERI", 
              command=lambda: start_with_players(2), 
              bg="#222", fg="#00ff88", font=("Consolas", 18, "bold"), 
              relief="flat", activebackground="#333", activeforeground="#00ff88",
              width=20, height=2, cursor="hand2")
    b2.pack(side=tk.LEFT, padx=15)
    
    # Buton 3 Jucători
    b3 = tk.Button(btn_frame, 
              text="3 PLAYERI", 
              command=lambda: start_with_players(3), 
              bg="#222", fg="#ff4444", font=("Consolas", 18, "bold"), 
              relief="flat", activebackground="#333", activeforeground="#ff4444",
              width=20, height=2, cursor="hand2")
    b3.pack(side=tk.LEFT, padx=15)

    info_frame = tk.Frame(root, bg="#111")
    info_frame.pack(pady=30)
    
    tk.Label(info_frame, text="Etapa 1: Aduna timpii de culoare albastra pentru viitor.", fg="#aaa", bg="#111", font=("Consolas", 14, "italic")).pack(anchor="w", pady=4)
    tk.Label(info_frame, text="Etapa 2: Tine ochiul pe loc in timp ce il lovesti din parti.", fg="#aaa", bg="#111", font=("Consolas", 14, "italic")).pack(anchor="w", pady=4)
    tk.Label(info_frame, text="Etapa 3: Adevaratul challange.", fg="#aaa", bg="#111", font=("Consolas", 14, "italic")).pack(anchor="w", pady=4)

    # Așteaptă inputul jucătorului
    root.mainloop()
    
    # -------------------------------------------------------------
    # BĂTĂLIA POATE SA INCEAPA (Doar dacă nu s-a dat X pe fereastră)
    # -------------------------------------------------------------
    if game_started[0]:
        game.start_game()
        
        # -------------------------------------------------------------
        # DASHBOARD LIVE ÎN TIMPUL JOCULUI
        # -------------------------------------------------------------
        dash = tk.Tk()
        dash.title("Live Dashboard - Asediul Ochilor")
        dash.geometry("500x320")
        dash.configure(bg="#0a0a0a")
        # dash.attributes('-topmost', True) # Îl ține deasupra, opțional
        
        lbl_stadiu = tk.Label(dash, text="SE ÎNCARCĂ...", font=("Consolas", 20, "bold"), bg="#0a0a0a", fg="#00ccff")
        lbl_stadiu.pack(pady=(30, 5))
        
        lbl_timp = tk.Label(dash, text="--", font=("Consolas", 40, "bold"), bg="#0a0a0a", fg="#ffffff")
        lbl_timp.pack(pady=10)
        
        lbl_bank = tk.Label(dash, text="", font=("Consolas", 14), bg="#0a0a0a", fg="#ffcc00")
        lbl_bank.pack(pady=10)
        
        # Funcție de actualizare a interfeței apelată automat o dată la 100 milisecunde
        def update_dash():
            # Condiții de final
            if game.state == GameState.GAME_OVER:
                lbl_stadiu.config(text="GAME OVER", fg="#ff0000")
                lbl_timp.config(text="Ai pierdut!", fg="#ff0000")
                lbl_bank.config(text="")
                dash.after(8000, dash.destroy)
                return
            elif game.state == GameState.VICTORY:
                lbl_stadiu.config(text="VICTORIE TOTALĂ!", fg="#00ff00")
                lbl_timp.config(text=f"{int(game.phase_timer)} sec", fg="#00ff00")
                lbl_bank.config(text="Timp rămas adunat din Etapele 2 și 3!")
                dash.after(10000, dash.destroy)
                return
                
            # Logica formatării continue
            t_rem = int(game.phase_timer)
            if t_rem < 0: t_rem = 0
            
            if game.state == GameState.PHASE_1:
                lbl_stadiu.config(text="ETAPA 1: ADUNĂ TIMP", fg="#00ccff")
                lbl_timp.config(text=f"{t_rem}s", fg="#fff")
                lbl_bank.config(text=f"Timp Acumulat (Bonus): {int(game.time_bank)} secunde")
                
            elif game.state == GameState.PHASE_2:
                lbl_stadiu.config(text="ETAPA 2: PRIMUL OCHI", fg="#ffcc00")
                lbl_timp.config(text=f"{t_rem}s", fg="#fff")
                lbl_bank.config(text=f"Ai acumulat: {int(game.time_bank)}s din etapa 1\nProgres Daune: {game.current_blues_caught} / {game.config['phase2_target']}")
                
            elif game.state in (GameState.PHASE_3, GameState.PHASE_2_REPEAT):
                lbl_stadiu.config(text="ETAPA 3: ADEVARATUL CHALLANGE", fg="#ff3333")
                lbl_timp.config(text=f"{t_rem}s", fg="#fff")
                lbl_bank.config(text=f"Progres Boss: {game.current_blues_caught} / {game.config['phase3_target']}")
                
            dash.after(100, update_dash)
            
        update_dash()
        
        try:
            # Rulăm bucla grafică în loc de time.sleep() invizibil
            dash.mainloop()
        except KeyboardInterrupt:
            print("\n[!] Închidere solicitată de utilizator manual.")
        finally:
            game.stop_game()
            service.stop_polling()
            service.stop_receiver()
            print("Instanță curățată din memorie.")
    else:
        # A dat X din dreapta sus
        service.stop_polling()
        service.stop_receiver()
        print("Ieșire din meniu fără lansarea jocului.")
