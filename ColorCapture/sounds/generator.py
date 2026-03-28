import numpy as np
import wave
import struct
import math
import os

def save_wav(filename, waveform, sample_rate=44100):
    waveform = np.clip(waveform, -1.0, 1.0)
    wav_data = (waveform * 32767).astype(np.int16)
    with wave.open(filename, 'w') as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(wav_data.tobytes())

SR = 44100

def mix(*arrs):
    length = max(len(a) for a in arrs)
    out = np.zeros(length)
    for a in arrs:
        out[:len(a)] += a
    return out

# 1. Gong
def make_gong():
    dur = 4.0
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    # FM synthesis for metallic gong
    gong = np.zeros_like(t)
    freqs = [90, 140, 204, 321, 542, 800, 1100, 1400]
    for i, f in enumerate(freqs):
        env = np.exp(-t * (1.5 + i*0.2)) * (1.0 - np.exp(-t*50))
        gong += np.sin(2 * np.pi * f * t + 1.2 * np.sin(2 * np.pi * f * 1.41 * t)) * env * (1.0 / (i+1))
    
    # Add crash noise
    crash = (np.random.random(len(t)) * 2 - 1)
    crash_env = np.exp(-t * 4.0) * (1.0 - np.exp(-t*100))
    gong += crash * crash_env * 0.15
    return gong * 0.6

# 2. Trumpet (Ta-da!)
def make_trumpet():
    def synth_brass(freq, dur):
        t = np.linspace(0, dur, int(SR * dur), endpoint=False)
        # Sawtooth approximation
        wave = sum(np.sin(2 * np.pi * freq * idx * t) / idx for idx in range(1, 10))
        # envelope
        env = np.clip(t * 20, 0, 1) * np.exp(-t * 0.5)
        return wave * env * 0.3

    n1 = synth_brass(523.25, 0.15) # C5
    silence1 = np.zeros(int(SR * 0.05))
    n2 = synth_brass(659.25, 0.15) # E5
    silence2 = np.zeros(int(SR * 0.05))
    n3 = synth_brass(783.99, 0.15) # G5
    silence3 = np.zeros(int(SR * 0.05))
    n4 = synth_brass(1046.50, 1.5)  # C6 sustained
    
    return np.concatenate([n1, silence1, n2, silence2, n3, silence3, n4])

# 3. Applause
def make_applause():
    dur = 5.0
    t = np.linspace(0, dur, int(SR * dur), endpoint=False)
    # White noise base
    noise = np.random.normal(0, 0.3, len(t))
    # Swells to simulate cheering
    swell1 = np.sin(2 * np.pi * 0.3 * t) * 0.4 + 0.6
    swell2 = np.sin(2 * np.pi * 1.1 * t + 1) * 0.3 + 0.7
    env = np.clip(dur - t, 0, 1) # fade out at end
    return noise * swell1 * swell2 * env

# 4. Fast Arcade BGM
def make_bgm():
    tempo = 160.0 # Fast paced
    beat_len = 60.0 / tempo
    seq_len = int(SR * beat_len * 16) # 4 measures
    bgm = np.zeros(seq_len)
    
    def square(freq, dur, vol, decay):
        t = np.linspace(0, dur, int(SR * dur), endpoint=False)
        w = np.sign(np.sin(2 * np.pi * freq * t)) * vol
        w *= np.exp(-decay * t)
        return w
        
    def kick(dur):
        t = np.linspace(0, dur, int(SR * dur), endpoint=False)
        w = np.sin(2 * np.pi * 55.0 * t - 10 * np.exp(-t * 80)) * 0.8
        w *= np.exp(-t * 15)
        return w

    step_len = beat_len / 4.0
    
    # 4 on the floor kicks
    for b in range(16):
        k = kick(beat_len)
        idx = int(b * SR * beat_len)
        bgm[idx : idx+len(k)] += k

    # Fast bassline arpeggiator
    arp = [110.0, 110.0, 130.81, 110.0, 146.83, 110.0, 130.81, 98.0]
    for i in range(64):
        f = arp[i % len(arp)]
        note = square(f, step_len, 0.15, 8.0)
        idx = int(i * SR * step_len)
        bgm[idx : idx+len(note)] += note

    # Melody
    mel_notes = [440.0, 0, 523.25, 0, 659.25, 523.25, 880.0, 0]
    for i in range(32):
        f = mel_notes[i % len(mel_notes)]
        if f > 0:
            note = np.sin(2 * np.pi * f * np.linspace(0, step_len*2, int(SR*step_len*2))) * 0.2
            note *= np.exp(-np.linspace(0, step_len*2, len(note)) * 3.0)
            idx = int(i * 2 * SR * step_len)
            bgm[idx : idx+len(note)] += note

    # Duplicate to make it longer
    return np.concatenate([bgm, bgm])

if __name__ == "__main__":
    out_dir = "/home/catalina-antemir/FACULTATE/PERSONAL/LEDHACK/Bon-Bon/ColorCapture/sounds"
    if not os.path.exists(out_dir):
        os.makedirs(out_dir)
        
    save_wav(os.path.join(out_dir, "gong.wav"), make_gong())
    save_wav(os.path.join(out_dir, "trumpet.wav"), make_trumpet())
    save_wav(os.path.join(out_dir, "applause.wav"), make_applause())
    save_wav(os.path.join(out_dir, "bgm.wav"), make_bgm())
    print("All sounds generated brilliantly in high quality.")
