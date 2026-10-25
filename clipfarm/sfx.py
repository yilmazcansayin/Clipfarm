"""Deneysel kurgu için ses efektleri: hepsi kodla sentezlenir, telif riski yok.

Meme seslerinin (vine boom, bruh…) birebir kopyası değiller; aynı işi gören, aynı karakterde
sesler: alçak "boom", hızlı "whoosh", gerilim "riser", plak cızırtısı, geri sarma…
"""

from __future__ import annotations

import wave
from pathlib import Path

import numpy as np

RATE = 48000
NAMES = ("boom", "whoosh", "riser", "ding", "scratch", "shutter", "rewind", "pop", "drop", "hit", "sting")


def _t(seconds: float) -> np.ndarray:
    return np.arange(int(RATE * seconds)) / RATE


def _sweep(f0: float, f1: float, seconds: float, curve: str = "exp") -> np.ndarray:
    """f0 → f1 kayan sinüsün fazı."""
    t = _t(seconds)
    if curve == "exp":
        f = f0 * (f1 / f0) ** (t / seconds)
    else:
        f = f0 + (f1 - f0) * t / seconds
    return 2 * np.pi * np.cumsum(f) / RATE


def _lowpass(x: np.ndarray, cutoff: np.ndarray | float) -> np.ndarray:
    """Tek kutuplu alçak geçiren; cutoff zamanla değişebilir."""
    c = np.broadcast_to(np.asarray(cutoff, dtype=np.float64), x.shape)
    a = 1 - np.exp(-2 * np.pi * c / RATE)
    y = np.empty_like(x)
    acc = 0.0
    for i in range(len(x)):
        acc += a[i] * (x[i] - acc)
        y[i] = acc
    return y


def _tail(x: np.ndarray, seconds: float = 0.6, mix: float = 0.25, seed: int = 1) -> np.ndarray:
    """Kısa, ucuz bir yankı kuyruğu (azalan gürültüyle evrişim)."""
    rng = np.random.default_rng(seed)
    ir = rng.standard_normal(int(RATE * seconds)) * np.exp(-_t(seconds) * 7)
    wet = np.convolve(x, ir)[: len(x) + len(ir)]
    dry = np.concatenate([x, np.zeros(len(ir))])[: len(wet)]
    wet /= np.max(np.abs(wet)) + 1e-9
    return dry + mix * wet * np.max(np.abs(x))


def synth(name: str) -> np.ndarray:
    rng = np.random.default_rng(len(name))
    if name == "boom":  # vine-boom karakteri: alçak, doygun, uzun kuyruk
        t = _t(1.4)
        body = np.sin(_sweep(95, 42, 1.4)) * np.exp(-t * 3.2)
        click = rng.standard_normal(len(t)) * np.exp(-t * 90) * 0.35
        x = np.tanh(2.2 * (body + click))
        return _tail(x, 0.8, 0.3)
    if name == "hit":  # kısa, sert darbe (tokat/yumruk anı)
        t = _t(0.35)
        x = np.sin(_sweep(160, 50, 0.35)) * np.exp(-t * 16) + rng.standard_normal(len(t)) * np.exp(-t * 60) * 0.6
        return np.tanh(1.8 * x)
    if name == "whoosh":  # geçiş: filtreli gürültü, kesme frekansı yükselip düşer
        s = 0.55
        t = _t(s)
        env = np.sin(np.pi * t / s) ** 2
        cutoff = 300 + 5000 * np.sin(np.pi * t / s) ** 3
        return _lowpass(rng.standard_normal(len(t)), cutoff) * env * 3.0
    if name == "riser":  # gerilim: yükselen ton + yükselen gürültü, sonda keskin biter
        s = 1.6
        t = _t(s)
        env = (t / s) ** 2
        tone = np.sin(_sweep(180, 1400, s)) * 0.5 + np.sin(_sweep(181.5, 1410, s)) * 0.5
        noise = _lowpass(rng.standard_normal(len(t)), 500 + 7000 * t / s) * 2.0
        return (tone * 0.6 + noise * 0.5) * env
    if name == "ding":  # doğru cevap / "işte bu" anı
        t = _t(1.0)
        x = np.sin(2 * np.pi * 1318.5 * t) + 0.35 * np.sin(2 * np.pi * 2637 * t) + 0.15 * np.sin(2 * np.pi * 3955 * t)
        return x * np.exp(-t * 4.5) * np.minimum(1, t * 400)
    if name == "scratch":  # plak cızırtısı: ileri-geri hızla kayan testere + gürültü
        s = 0.5
        t = _t(s)
        f = 220 + 520 * np.abs(np.sin(2 * np.pi * 5.5 * t))
        phase = 2 * np.pi * np.cumsum(f) / RATE
        saw = 2 * ((phase / (2 * np.pi)) % 1) - 1
        grit = _lowpass(rng.standard_normal(len(t)), f * 6) * 1.5
        env = np.minimum(1, t * 80) * np.exp(-t * 2.5)
        return np.tanh(1.5 * (saw * 0.6 + grit * 0.5)) * env
    if name == "shutter":  # fotoğraf makinesi: iki kısa klik
        x = np.zeros(int(RATE * 0.25))
        for at in (0.0, 0.07):
            i = int(at * RATE)
            n = int(RATE * 0.03)
            x[i:i + n] += rng.standard_normal(n) * np.exp(-_t(0.03) * 150)
        return _lowpass(x, 6000) * 2
    if name == "rewind":  # geri sarma: hızla tekrar eden yükselen cıvıltılar
        s = 0.7
        t = _t(s)
        chirp = np.sin(2 * np.pi * (600 + 2600 * ((t * 14) % 1)) * t)
        env = np.minimum(1, t * 30) * np.minimum(1, (s - t) * 12)
        return chirp * env * 0.6 + _lowpass(rng.standard_normal(len(t)), 3000) * env * 0.4
    if name == "pop":  # yazı/etiket çıkışı
        t = _t(0.09)
        return np.sin(_sweep(900, 260, 0.09)) * np.exp(-t * 35)
    if name == "sting":  # garip an: inen üç koparma nota ("womp womp"), Curb müziğinin yerine
        out = []
        for i, f in enumerate((392.0, 349.2, 293.7)):
            n = int(RATE * (0.32 if i < 2 else 0.9))
            period = int(RATE / f)
            buf = rng.uniform(-1, 1, period)
            y = np.empty(n)
            for k in range(n):  # Karplus-Strong telli çalgı
                y[k] = buf[k % period]
                buf[k % period] = 0.996 * 0.5 * (buf[k % period] + buf[(k + 1) % period])
            out.append(y)
        return np.concatenate(out)
    if name == "drop":  # bas düşüşü: büyük anın hemen öncesinde
        t = _t(1.8)
        x = np.sin(_sweep(140, 35, 1.8)) * np.exp(-t * 1.4) * np.minimum(1, t * 50)
        return np.tanh(2.5 * x)
    raise ValueError(f"Bilinmeyen ses efekti: {name} (seçenekler: {', '.join(NAMES)})")


def write(name: str, folder: Path) -> Path:
    """Efekti 48 kHz stereo WAV olarak yazar (önbellek: aynı dosya varsa tekrar üretmez)."""
    folder.mkdir(parents=True, exist_ok=True)
    dst = folder / f"sfx_{name}.wav"
    if dst.exists():
        return dst
    x = synth(name)
    x = x / (np.max(np.abs(x)) + 1e-9) * 0.89  # tepe ~-1 dBFS; düzey render'da ayarlanır
    pcm = (np.repeat(x[:, None], 2, axis=1) * 32767).astype("<i2")
    with wave.open(str(dst), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(pcm.tobytes())
    return dst
