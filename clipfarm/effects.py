"""Örnek Shorts'lardaki görünüm: canlı renk + vurgulu anlarda kısa renk flaşı / siyah-beyaz kare.

Efektler dikey kareye, altyazı yakılmadan önce uygulanır; yazılar etkilenmez. Flaşlar
rastgele değil: sesin ani yükseldiği anlara konur ve o anda başlayan kelimeye hizalanır.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

RATE = 8000
WINDOW = 0.1  # sn; tepe bulma çözünürlüğü
FLASH_KINDS = ("tint", "bw", "white")
FLASH_SECONDS = {"tint": 0.12, "bw": 0.3, "white": 0.15}


@dataclass
class Flash:
    t: float
    kind: str = "tint"  # tint: renkli perde, bw: siyah-beyaz, white: aşırı pozlama
    color: str = "39FF14"  # tint rengi (RRGGBB)


@dataclass
class Effects:
    grade: bool = True  # canlı renk + hafif keskinlik
    flashes: list[Flash] = field(default_factory=list)


def find_punches(media: Path, max_count: int = 3, min_gap: float = 6.0, skip_start: float = 1.0) -> list[float]:
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
    loud_enough = db > np.percentile(db, 80)
    threshold = max(8.0, float(np.mean(rise) + 2 * np.std(rise)))
    picked: list[float] = []
    for i in np.argsort(-rise):
        if rise[i] < threshold or len(picked) >= max_count:
            break
        t = i * WINDOW
        if t < skip_start or not loud_enough[i] or any(abs(t - p) < min_gap for p in picked):
            continue
        picked.append(round(float(t), 2))
    return sorted(picked)


def plan_flashes(peaks: list[float], word_starts: list[float], colors: list[str]) -> list[Flash]:
    """Ses tepelerini en yakın kelime başlangıcına hizalayıp sırayla farklı flaş türleri atar."""
    flashes = []
    for i, p in enumerate(peaks):
        near = [w for w in word_starts if abs(w - p) <= 0.35]
        t = min(near, key=lambda w: abs(w - p)) if near else p
        flashes.append(Flash(round(t, 2), FLASH_KINDS[i % len(FLASH_KINDS)], colors[i % len(colors)] if colors else "39FF14"))
    return flashes


def _num(x: float) -> str:
    return f"{x:.3f}".rstrip("0").rstrip(".") or "0"


def effects_filter(fx: Effects) -> str:
    """[v] zincirine eklenecek filtreler (virgülle ayrılmış, etiket yok). Boşsa ''."""
    parts = []
    if fx.grade:
        parts.append("eq=saturation=1.22:contrast=1.06:brightness=0.02")
        parts.append("unsharp=5:5:0.5:5:5:0")
    for f in fx.flashes:
        when = f"between(t,{_num(f.t)},{_num(f.t + FLASH_SECONDS[f.kind])})"
        if f.kind == "tint":
            parts.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=0x{f.color}@0.45:t=fill:enable='{when}'")
        elif f.kind == "bw":
            parts.append(f"hue=s=0:enable='{when}'")
        else:
            parts.append(f"eq=brightness=0.3:saturation=0.5:enable='{when}'")
    return ",".join(parts)
