import socket
import time
import random

try:
    import psutil
    HAS_PSUTIL = True
except ImportError:
    HAS_PSUTIL = False

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

def calc_sum(data):
    idx = sum(data) & 0xFF
    return PASSWORD_ARRAY[idx] if idx < len(PASSWORD_ARRAY) else 0

def build_discovery_packet():
    rand1, rand2 = random.randint(0, 127), random.randint(0, 127)
    payload = bytearray([0x0A, 0x02, *b"KX-HC04", 0x03, 0x00, 0x00, 0xFF, 0xFF, 0x00, 0x00, 0x00, 0x14])
    pkt = bytearray([0x67, rand1, rand2, len(payload)]) + payload
    pkt.append(calc_sum(pkt))
    return bytes(pkt), rand1, rand2

def get_local_interfaces():
    interfaces = []
    if HAS_PSUTIL:
        try:
            for iface, addrs in psutil.net_if_addrs().items():
                for addr in addrs:
                    if addr.family == socket.AF_INET and addr.address != "127.0.0.1":
                        bcast = addr.broadcast if addr.broadcast else "255.255.255.255"
                        interfaces.append((iface, addr.address, bcast))
        except Exception:
            pass
    if not interfaces:
        interfaces.append(("Default", "0.0.0.0", "255.255.255.255"))
    return interfaces

def auto_discover_evileye(timeout=1.5):
    """
    Scaunează tăcut și automat toate plăcile de rețea.
    Revine cu IP-ul camerei Evil Eye sau None dacă folosește Simulatorul local.
    """
    interfaces = get_local_interfaces()
    
    sockets = []
    pkt, r1, r2 = build_discovery_packet()
    
    print(f"\n[Auto-Discovery] Vă caut un Tărâm de Luptă Hardware (pe {len(interfaces)} interfețe)...")
    
    for iface, ip, bcast in interfaces:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        
        # Încercăm să ascultăm răspunsul fix pe portul 7800
        try:
            sock.bind((ip, 7800))
        except Exception:
            pass
        # ---------------------------------------------
        
        sock.settimeout(timeout)
        try:
            sock.sendto(pkt, (bcast, 4626))
            sockets.append((sock, iface))
        except:
            sock.close()

    end_time = time.time() + timeout
    
    while time.time() < end_time:
        for sock, iface in sockets:
            try:
                sock.settimeout(0.05)
                data, addr = sock.recvfrom(1024)
                if len(data) >= 30 and data[0] == 0x68 and data[1] == r1 and data[2] == r2:
                    model = data[6:13].decode(errors='ignore').strip('\x00')
                    print(f"[Auto-Discovery] 🎯 GĂSIT CONEXIUNE: {model} la IP-ul {addr[0]} (via {iface})!")
                    for s, _ in sockets: s.close()
                    return addr[0]
            except socket.timeout:
                continue
            except:
                pass
                
    for sock, _ in sockets: sock.close()
    print("[Auto-Discovery] 💤 Niciun device fizic răspuns. Trecem automat la adresa locală / fallback config.")
    return None

if __name__ == "__main__":
    ip = auto_discover_evileye()
    if ip:
        print(f"Baza de date updateată: Conectat la {ip}!")
    else:
        print("Scenariu Simulator / Hardcoded.")
