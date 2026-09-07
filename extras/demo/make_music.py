"""Фоновая «напряжённая» музыка для демо-роликов — синтез на numpy, без внешних файлов.

Ре-минор, ~104 BPM: низкий пульсирующий бас, дрон-пад, арпеджио на «стеклянном» синте,
редкие удары, шумовые нарастания перед каждой сменой гармонии. Громкость — фоновая.
Использование: python make_music.py <секунды> <out.wav>
"""
import sys

import numpy as np

SR = 44100


def _env(n, a, d, s, r, sr=SR):
    a, d, r = int(a * sr), int(d * sr), int(r * sr)
    out = np.ones(n) * s
    a = min(a, n); out[:a] = np.linspace(0, 1, a)
    d = min(d, max(n - a, 0)); out[a:a + d] = np.linspace(1, s, d)
    r = min(r, n); out[n - r:] *= np.linspace(1, 0, r)
    return out


def _tone(freq, dur, kind="saw", detune=0.0):
    t = np.arange(int(dur * SR)) / SR
    f = freq * (1 + detune)
    if kind == "saw":
        x = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 9))
    elif kind == "sine":
        x = np.sin(2 * np.pi * f * t)
    elif kind == "square":
        x = sum(np.sin(2 * np.pi * f * k * t) / k for k in range(1, 12, 2))
    else:
        x = np.sin(2 * np.pi * f * t) + 0.4 * np.sin(2 * np.pi * f * 2 * t + 0.3)
    return x / np.max(np.abs(x) + 1e-9)


def _lowpass(x, alpha):
    y = np.empty_like(x); acc = 0.0
    for i, v in enumerate(x):   # медленно, но пусть — вызывается только для коротких кусков
        acc += alpha * (v - acc); y[i] = acc
    return y


def _lowpass_fast(x, cutoff):
    # однополюсный фильтр через lfilter-подобную рекурсию на numpy (векторно через scipy нет — делаем FFT)
    X = np.fft.rfft(x)
    freqs = np.fft.rfftfreq(len(x), 1 / SR)
    X *= 1 / np.sqrt(1 + (freqs / cutoff) ** 4)
    return np.fft.irfft(X, len(x))


NOTE = {"D2": 73.42, "F2": 87.31, "G2": 98.0, "A2": 110.0, "Bb2": 116.54, "C3": 130.81,
        "D3": 146.83, "F3": 174.61, "A3": 220.0, "Bb3": 233.08, "C4": 261.63, "D4": 293.66,
        "E4": 329.63, "F4": 349.23, "G4": 392.0, "A4": 440.0, "Bb4": 466.16, "C5": 523.25, "D5": 587.33}

# гармония: 4 такта на аккорд — Dm, Bb, Gm, A (напряжённый доминантный)
PROG = [("D2", ["D4", "F4", "A4", "D5"]), ("Bb2", ["Bb3", "D4", "F4", "Bb4"]),
        ("G2", ["G4", "Bb4", "D5", "G4"]), ("A2", ["A4", "C5", "E4", "A4"])]


def build(duration: float) -> np.ndarray:
    bpm = 104
    beat = 60 / bpm
    bar = beat * 4
    n = int(duration * SR)
    out = np.zeros(n)
    rng = np.random.default_rng(13)

    # --- бас: восьмые, пульс
    pos = 0.0; bar_i = 0
    while pos < duration:
        root, arp = PROG[(bar_i // 4) % len(PROG)]
        for k in range(8):
            t0 = pos + k * beat / 2
            if t0 >= duration:
                break
            dur = beat / 2 * 0.9
            x = _tone(NOTE[root], dur, "saw") * _env(int(dur * SR), 0.005, 0.12, 0.35, 0.05)
            x = _lowpass_fast(x, 260 if k % 2 == 0 else 180)
            amp = 0.55 if k % 2 == 0 else 0.38
            i0 = int(t0 * SR); out[i0:i0 + len(x)] += x[: n - i0] * amp
        # --- пад (дрон): весь такт, две расстроенные пилы
        x = (_tone(NOTE[root] * 2, bar, "saw", 0.003) + _tone(NOTE[root] * 2, bar, "saw", -0.003)
             + 0.6 * _tone(NOTE[arp[1]] / 2, bar, "saw", 0.002))
        x = _lowpass_fast(x, 600) * _env(int(bar * SR), 0.6, 0.5, 0.8, 0.8)
        i0 = int(pos * SR); out[i0:i0 + len(x)] += x[: n - i0] * 0.16
        # --- арпеджио: шестнадцатые на «стекле», с 2-го круга
        if bar_i >= 4:
            pattern = arp + arp[::-1]
            for k in range(16):
                t0 = pos + k * beat / 4
                if t0 >= duration:
                    break
                dur = beat / 4 * 1.6
                x = _tone(NOTE[pattern[k % len(pattern)]], dur, "bell") * _env(int(dur * SR), 0.002, 0.1, 0.25, 0.08)
                vel = 0.22 if k % 4 == 0 else 0.13
                i0 = int(t0 * SR); out[i0:i0 + len(x)] += x[: n - i0] * vel
        # --- удар на «раз» каждого второго такта + шумовое нарастание перед сменой аккорда
        if bar_i % 2 == 0:
            dur = 0.5
            t = np.arange(int(dur * SR)) / SR
            kick = np.sin(2 * np.pi * (55 + 120 * np.exp(-t * 18)) * t) * np.exp(-t * 7)
            i0 = int(pos * SR); out[i0:i0 + len(kick)] += kick[: n - i0] * 0.7
        if bar_i % 4 == 3:
            dur = bar
            noise = rng.standard_normal(int(dur * SR))
            noise = _lowpass_fast(noise, 1800) * np.linspace(0, 1, int(dur * SR)) ** 3
            i0 = int(pos * SR); out[i0:i0 + len(noise)] += noise[: n - i0] * 0.12
        pos += bar; bar_i += 1

    # тихая «тревожная» нота-тиканье на каждую четверть
    for b in range(int(duration / beat)):
        t0 = b * beat
        dur = 0.06
        x = _tone(NOTE["A4"] * 4, dur, "square") * _env(int(dur * SR), 0.001, 0.03, 0.2, 0.02)
        i0 = int(t0 * SR); out[i0:i0 + len(x)] += x[: n - i0] * (0.05 if b % 4 else 0.08)

    # общая огибающая: вступление 3 с, финал — затухание 5 с
    out *= _env(n, 3.0, 0.0, 1.0, 5.0)
    out = np.tanh(out * 1.2)               # мягкий лимитер
    out *= 0.35 / (np.max(np.abs(out)) + 1e-9)   # пик −9 dBFS: фоновая громкость
    return out


def write_wav(path: str, x: np.ndarray) -> None:
    import wave
    pcm = (np.clip(x, -1, 1) * 32767).astype("<i2")
    stereo = np.column_stack([pcm, pcm]).tobytes()
    with wave.open(path, "wb") as wf:
        wf.setnchannels(2); wf.setsampwidth(2); wf.setframerate(SR); wf.writeframes(stereo)


if __name__ == "__main__":
    dur = float(sys.argv[1]); out = sys.argv[2]
    write_wav(out, build(dur))
    print(f"music: {dur:.1f}s → {out}")
