"""An original 15s soundtrack synced to the ad's beats: a tense pulsing bass
at 120 BPM, ticks as the seats light up, pops as dots land, a riser into
the verdict drop, a chime for 'Right', and a warm chord on the end card."""
import wave
import numpy as np

SR = 44100
DUR = 15.0
N = int(SR * DUR)
L = np.zeros(N); Rr = np.zeros(N)
t_all = np.arange(N) / SR
rng = np.random.default_rng(7)

def add(sig, start, pan=0.0, gain=1.0):
    i = int(start * SR)
    if i >= N: return
    sig = sig[: N - i] * gain
    L[i:i + len(sig)] += sig * (1 - max(0, pan))
    Rr[i:i + len(sig)] += sig * (1 + min(0, pan))

def env(n, a=0.005, d=0.2, curve=4.0):
    t = np.arange(n) / SR
    e = np.minimum(1, t / max(a, 1e-4)) * np.exp(-curve * np.maximum(0, t - a) / max(d, 1e-4))
    return e

def lowpass(x, cutoff):
    a = np.exp(-2 * np.pi * np.asarray(cutoff) / SR)
    y = np.zeros_like(x); prev = 0.0
    if np.ndim(a) == 0:
        for i, v in enumerate(x):
            prev = (1 - a) * v + a * prev; y[i] = prev
    else:
        for i, v in enumerate(x):
            prev = (1 - a[i]) * v + a[i] * prev; y[i] = prev
    return y

def saw(freq, n):
    ph = np.cumsum(np.full(n, freq) / SR) if np.ndim(freq) == 0 else np.cumsum(freq / SR)
    return 2 * (ph % 1) - 1

def sine(freq, n):
    ph = np.cumsum(np.full(n, freq) / SR) if np.ndim(freq) == 0 else np.cumsum(freq / SR)
    return np.sin(2 * np.pi * ph)

def kick(n=int(0.45 * SR), big=False):
    t = np.arange(n) / SR
    f = 42 + (140 if big else 95) * np.exp(-t * 28)
    s = sine(f, n) * env(n, 0.001, 0.35 if big else 0.22, 5)
    click = rng.standard_normal(n) * env(n, 0.0005, 0.01, 30) * 0.3
    return np.tanh((s + click) * (2.2 if big else 1.6))

def hat(n=int(0.06 * SR)):
    x = rng.standard_normal(n)
    x = x - lowpass(x, 7000)
    return x * env(n, 0.0005, 0.03, 6) * 0.35

def blip(freq, n=int(0.12 * SR)):
    return sine(freq, n) * env(n, 0.002, 0.08, 5) * 0.5 + sine(freq * 2, n) * env(n, 0.002, 0.04, 6) * 0.15

def impact(n=int(1.6 * SR)):
    t = np.arange(n) / SR
    boom = sine(38 + 60 * np.exp(-t * 6), n) * env(n, 0.002, 1.2, 3)
    noise = lowpass(rng.standard_normal(n), 900) * env(n, 0.002, 0.5, 4) * 1.4
    return np.tanh((boom + noise) * 1.8) * 0.9

def whoosh(dur=0.5, up=True):
    n = int(dur * SR); t = np.arange(n) / SR
    x = rng.standard_normal(n)
    cut = (300 + 5000 * (t / dur) ** 2) if up else (5300 - 5000 * (t / dur) ** 0.5)
    y = lowpass(x, cut)
    shape = np.sin(np.pi * t / dur) ** 2
    return y * shape * 0.6

# Hook slam at 0.1s and a low rumble under the question.
add(impact(), 0.08, gain=0.9)
add(whoosh(0.35, up=True), 1.2, gain=0.8)

# Bass pulse: 8th notes at 120 BPM from 1.5s to 13s, A minor -> F -> C -> G feel.
beat = 0.5
roots = {1.5: 55.0, 4.0: 43.65, 6.0: 65.41, 8.0: 49.0, 11.5: 55.0}
def root_at(tt):
    r = 55.0
    for k in sorted(roots):
        if tt >= k: r = roots[k]
    return r
tt = 1.5
while tt < 13.0:
    n = int(beat / 2 * SR)
    f = root_at(tt)
    tone = saw(f, n) * 0.6 + saw(f * 2.005, n) * 0.25
    intensity = 0.5 + 0.5 * min(1, (tt - 1.5) / 6.5) if tt < 8 else 0.9
    tone = lowpass(tone, 600 + 1400 * intensity) * env(n, 0.004, 0.16, 3) * 0.55 * intensity
    add(tone, tt)
    tt += beat / 2
# Kicks on the beat, hats on the off-beat, busier after the drop.
tt = 1.5
while tt < 13.0:
    if tt < 7.9 or tt >= 8.0:
        add(kick(), tt, gain=0.55 if tt < 4 else 0.7)
    add(hat(), tt + beat / 2, pan=0.3, gain=0.8)
    if tt >= 8.0:
        add(hat(), tt + beat / 4, pan=-0.3, gain=0.4)
    tt += beat

# Seats lighting: rising ticks.
for i in range(12):
    add(blip(880 * 2 ** (i / 12)), 1.75 + i * 0.15, pan=(-0.6 + 1.2 * i / 11), gain=0.45)
# Badges and dots landing.
for i in range(12):
    add(blip(1320 * 2 ** ((i % 5) / 12), int(0.07 * SR)), 4.95 + i * 0.13, pan=(i % 2) * 0.4 - 0.2, gain=0.3)
# Chips and arguments slide in.
for s in (4.5, 4.85, 5.2):
    add(whoosh(0.3), s - 0.05, gain=0.35)
add(whoosh(0.4), 6.1, pan=-0.5, gain=0.5)
add(whoosh(0.4), 6.7, pan=0.5, gain=0.5)

# Riser into the verdict drop at 8.0.
n = int(1.9 * SR); t = np.arange(n) / SR
riser = lowpass(rng.standard_normal(n), 200 + 7000 * (t / 1.9) ** 2) * (t / 1.9) ** 2 * 0.7
riser += sine(220 * 2 ** (t / 1.9 * 2), n) * (t / 1.9) ** 3 * 0.15
add(riser, 6.1)
add(impact(), 8.0, gain=1.0)
add(kick(big=True), 8.0, gain=0.8)
# Challenger: a tense two-note sting.
for f, s in ((466.2, 8.12), (440.0, 8.3)):
    n = int(0.3 * SR)
    add(lowpass(saw(f, n), 2500) * env(n, 0.005, 0.2, 4) * 0.25, s)
# Bars filling: soft rising tones.
for k, s in enumerate((9.3, 9.65, 10.0)):
    n = int(0.6 * SR); t = np.arange(n) / SR
    add(sine(330 * 2 ** (k * 4 / 12) * (1 + 0.5 * t), n) * env(n, 0.02, 0.4, 4) * 0.25, s, pan=(k - 1) * 0.4)
# 'Right' stamp: major chime.
for f in (1046.5, 1318.5, 1568.0):
    n = int(1.2 * SR)
    add(sine(f, n) * env(n, 0.003, 0.9, 3) * 0.18 + sine(f * 2, n) * env(n, 0.003, 0.3, 5) * 0.05, 11.65)
add(kick(), 11.65, gain=0.5)

# End card: warm A-major pad with a gentle swell, then fade out.
n = int(2.1 * SR); t = np.arange(n) / SR
pad = sum(lowpass(saw(f, n) + saw(f * 1.004, n), 1800) for f in (110.0, 138.6, 164.8, 220.0, 277.2))
pad *= np.minimum(1, t / 0.25) * np.minimum(1, (2.1 - t) / 0.9) * 0.08
add(pad, 12.95)
add(whoosh(0.5, up=False), 12.9, gain=0.5)
add(blip(1760), 13.95, gain=0.4)

mix = np.stack([L, Rr], axis=1)
mix *= np.minimum(1, (DUR - t_all) / 0.4)[:, None]  # tail fade
peak = np.max(np.abs(mix)) or 1
mix = np.tanh(mix / peak * 1.4) * 0.89
with wave.open("music.wav", "wb") as w:
    w.setnchannels(2); w.setsampwidth(2); w.setframerate(SR)
    w.writeframes((mix * 32767).astype("<i2").tobytes())
print("ok", peak)
