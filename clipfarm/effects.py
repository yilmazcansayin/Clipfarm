"""Shorts düzenleme efektleri: yavaş yakınlaşma, ses tepelerinde zoom vuruşu + flaş, ilerleme çubuğu.

Efektler dikey (1080x1920) kareye, altyazı yakılmadan önce uygulanır; böylece yazılar sabit kalır.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from .captions import HEIGHT, WIDTH

RATE = 8000
WINDOW = 0.1  # sn; tepe bulma çözünürlüğü


@dataclass
class Effects:
    drift: float = 0.06  # klip boyunca toplam yavaş yakınlaşma oranı
    punch: float = 0.12  # vuruş anında ek yakınlaşma
    punch_seconds: float = 0.45  # vuruşun sönme süresi
    flash: float = 0.18  # vuruş anındaki parlaklık artışı (0 = kapalı)
    progress_bar: bool = True
    bar_color: str = "FFE500"
    punches: list[float] = field(default_factory=list)  # klibe göre göreli vuruş anları (sn)


def find_punches(media: Path, max_count: int = 6, min_gap: float = 2.5, skip_start: float = 0.6) -> list[float]:
    """Klibin sesindeki ani yükselmeleri (bağırma, gülme, patlama) bulur."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        capture_output=True,
    )
    if proc.returncode != 0 or not proc.stdout:
        return []
    n = int(RATE * WINDOW)
    samples = np.frombuffer(proc.stdout, dtype=np.int16).astype(np.float32)
    usable = len(samples) - len(samples) % n
    if usable < n * 10:
        return []
    frames = samples[:usable].reshape(-1, n)
    db = 20 * np.log10(np.sqrt(np.mean(frames * frames, axis=1)) / 32768.0 + 1e-9)
    # ani yükselme: o anki seviye, önceki 1 sn'nin ortalamasından ne kadar yüksek
    prev = np.convolve(db, np.ones(10) / 10, mode="full")[: len(db)]
    prev = np.concatenate([[db[0]], prev[:-1]])
    rise = db - prev
    loud_enough = db > np.percentile(db, 70)
    threshold = max(6.0, float(np.mean(rise) + 1.5 * np.std(rise)))
    order = np.argsort(-rise)
    picked: list[float] = []
    for i in order:
        if rise[i] < threshold or len(picked) >= max_count:
            break
        t = i * WINDOW
        if t < skip_start or not loud_enough[i] or any(abs(t - p) < min_gap for p in picked):
            continue
        picked.append(round(t, 2))
    return sorted(picked)


def _num(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


def effects_filter(duration: float, fx: Effects) -> str:
    """[v] zincirine eklenecek filtreler (virgülle ayrılmış, etiket yok)."""
    d = max(duration, 0.1)
    zoom = f"1+{_num(fx.drift)}*t/{_num(d)}" if fx.drift else "1"
    for p in fx.punches:
        # vuruş anında hızla büyür, punch_seconds içinde doğrusal olarak söner
        zoom += f"+{_num(fx.punch)}*gte(t,{_num(p)})*max(0,1-(t-{_num(p)})/{_num(fx.punch_seconds)})"
    parts = []
    if zoom != "1":
        parts.append(
            f"scale=w='2*trunc({WIDTH // 2}*({zoom}))':h='2*trunc({HEIGHT // 2}*({zoom}))'"
            ":eval=frame:flags=bicubic"
        )
        parts.append(f"crop={WIDTH}:{HEIGHT}")
    if fx.flash and fx.punches:
        when = "+".join(f"between(t,{_num(p)},{_num(p + 0.07)})" for p in fx.punches)
        parts.append(f"eq=brightness={_num(fx.flash)}:enable='{when}'")
    parts.append("eq=saturation=1.12:contrast=1.04")
    if fx.progress_bar:
        parts.append(
            f"drawbox=x=0:y=ih-14:w='iw*t/{_num(d)}':h=14:color=0x{fx.bar_color}@0.9:t=fill"
        )
    return ",".join(parts)
