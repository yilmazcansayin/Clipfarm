"""Klibi efekt kullanmadan kurguyla hikâyeleştirme.

Kesme planı (kelime zamanları ve ses yüksekliğine göre):
  1. Kanca (teaser): klibin en yüksek tepkili ~2,5 sn'si en başa konur, ardından hikâye
     baştan anlatılır. İzleyici "buraya nasıl gelindi?" diye izlemeye devam eder.
  2. Ölü anlar atılır: konuşmanın olmadığı ve sesin de düşük olduğu boşluklar kesilir
     (jump cut). Konuşma yok ama ses yüksekse (gülme, bağırma, aksiyon) korunur.
  3. Baştaki ve sondaki sessizlik kırpılır; tepkiden sonra kısa bir kuyruk bırakılır.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .captions import Word

RATE = 8000
STEP = 0.1  # sn; ses yüksekliği çözünürlüğü


@dataclass
class Segment:
    start: float
    end: float

    @property
    def length(self) -> float:
        return self.end - self.start


def audio_db(media: Path) -> np.ndarray:
    """Her 0,1 sn'nin ses seviyesini (dB) döner."""
    proc = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(RATE), "-f", "s16le", "-"],
        capture_output=True,
    )
    n = int(RATE * STEP)
    samples = np.frombuffer(proc.stdout or b"", dtype=np.int16).astype(np.float32)
    usable = len(samples) - len(samples) % n
    if usable == 0:
        return np.zeros(0, dtype=np.float32)
    frames = samples[:usable].reshape(-1, n)
    return 20 * np.log10(np.sqrt(np.mean(frames * frames, axis=1)) / 32768.0 + 1e-9)


def _mean_db(db: np.ndarray, a: float, b: float) -> float:
    i, j = int(a / STEP), max(int(a / STEP) + 1, int(b / STEP))
    part = db[i:j]
    return float(np.mean(part)) if len(part) else -90.0


def plan_story(
    words: list[Word],
    duration: float,
    db: np.ndarray | None = None,
    teaser: bool = True,
    teaser_seconds: float = 2.5,
    min_gap: float = 0.7,
    pad: float = 0.15,
    tail: float = 0.6,
    max_total: float = 59.0,
) -> list[Segment]:
    """Kaynak klipten alınacak parçaları sırasıyla döner."""
    db = db if db is not None else np.zeros(0, dtype=np.float32)
    if not words:
        return [Segment(0.0, duration)]
    speech_db = float(np.median([_mean_db(db, w.start, w.end) for w in words])) if len(db) else 0.0
    start = max(0.0, words[0].start - 0.25)
    end = min(duration, words[-1].end + tail)
    # konuşmadan sonra yüksek bir tepki varsa (gülme, bağırma) kuyruğu uzat
    if len(db):
        k = int(end / STEP)
        while k < len(db) and k * STEP < duration and db[k] > speech_db - 6 and k * STEP - end < 3:
            k += 1
        end = min(duration, max(end, k * STEP))

    segments: list[Segment] = []
    cur = start
    for a, b in zip(words, words[1:]):
        gap = b.start - a.end
        if gap <= min_gap:
            continue
        cut_from, cut_to = a.end + pad, b.start - pad
        quiet = not len(db) or _mean_db(db, cut_from, cut_to) < speech_db - 8
        if quiet and cut_to - cut_from > 0.2:
            segments.append(Segment(cur, cut_from))
            cur = cut_to
    segments.append(Segment(cur, end))
    segments = [s for s in segments if s.length > 0.25]

    total = sum(s.length for s in segments)
    if teaser and len(db) and total > 12:
        # en yüksek tepkili pencere; hikâyenin ilk üçte birindeyse kanca gereksiz
        win = int(teaser_seconds / STEP)
        lo, hi = int(start / STEP), max(int(start / STEP) + win, int(end / STEP) - win)
        if hi > lo:
            sums = np.convolve(db[lo:hi + win], np.ones(win) / win, mode="valid")
            best = lo + int(np.argmax(sums))
            t0 = best * STEP
            if t0 - start > total * 0.35:
                # kancayı kelime sınırına oturt: pencerede başlayan ilk kelimeden başla
                inside = [w for w in words if t0 - 0.4 <= w.start <= t0 + 1.0]
                if inside:
                    t0 = inside[0].start - 0.1
                t1 = t0 + teaser_seconds
                ending = [w for w in words if t0 < w.end <= t1 + 0.8]
                if ending:
                    t1 = max(t1, ending[-1].end + 0.1)
                segments.insert(0, Segment(max(0.0, t0), min(duration, t1)))

    # Shorts sınırı: sondan kısalt
    out, used = [], 0.0
    for s in segments:
        if used + s.length > max_total:
            if max_total - used > 1:
                out.append(Segment(s.start, s.start + max_total - used))
            break
        out.append(s)
        used += s.length
    return out


def remap_words(words: list[Word], segments: list[Segment]) -> list[Word]:
    """Kelimeleri kurgulanmış videonun zamanına taşır (kanca parçasındaki kelimeler de dahil)."""
    out: list[Word] = []
    offset = 0.0
    for s in segments:
        for w in words:
            if s.start - 0.05 <= w.start < s.end:
                out.append(Word(round(w.start - s.start + offset, 3), round(min(w.end, s.end) - s.start + offset, 3), w.text))
        offset += s.length
    return out


def concat_filter(segments: list[Segment], has_audio: bool) -> tuple[str, str, str]:
    """Parçaları birleştiren filter_complex başı; (filtre, video etiketi, ses etiketi) döner."""
    parts, labels = [], []
    for i, s in enumerate(segments):
        parts.append(f"[0:v]trim={s.start:.3f}:{s.end:.3f},setpts=PTS-STARTPTS[sv{i}]")
        labels.append(f"[sv{i}]")
        if has_audio:
            parts.append(f"[0:a]atrim={s.start:.3f}:{s.end:.3f},asetpts=PTS-STARTPTS[sa{i}]")
            labels.append(f"[sa{i}]")
    parts.append("".join(labels) + f"concat=n={len(segments)}:v=1:a={1 if has_audio else 0}[cv]" + ("[ca]" if has_audio else ""))
    return ";".join(parts), "[cv]", "[ca]" if has_audio else ""
