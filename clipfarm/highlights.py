"""Uzun yayınlarda klip olabilecek anları bulma.

Sinyaller:
  * ses yüksekliği: yayıncının bağırdığı, güldüğü, ortamın coştuğu anlar
  * sohbet yoğunluğu: YouTube canlı sohbet tekrarı / Twitch rechat (varsa)
Her saniye için iki sinyal kendi kayan ortalamasına göre z-skora çevrilir,
birleştirilir ve en yüksek tepeler birbirinden uzak olacak şekilde seçilir.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

ANALYSIS_RATE = 8000  # Hz; ses yüksekliği için yeterli, bellek dostu


@dataclass
class Candidate:
    peak: float  # saniye (kaynak videoya göre)
    start: float
    end: float
    score: float
    audio_z: float = 0.0
    chat_z: float = 0.0
    transcript: str = ""
    words: list[dict] = field(default_factory=list)  # kaynağa göre mutlak zamanlar

    def to_dict(self) -> dict:
        return asdict(self)


def loudness_per_second(media: Path) -> np.ndarray:
    """Her saniyenin RMS ses seviyesini dB olarak döner (ffmpeg ile akış halinde okur)."""
    proc = subprocess.Popen(
        [
            "ffmpeg", "-v", "error", "-i", str(media), "-vn", "-ac", "1",
            "-ar", str(ANALYSIS_RATE), "-f", "s16le", "-",
        ],
        stdout=subprocess.PIPE,
    )
    assert proc.stdout is not None
    values: list[float] = []
    chunk_bytes = ANALYSIS_RATE * 2
    leftover = b""
    while True:
        data = proc.stdout.read(chunk_bytes * 60)
        if not data:
            break
        data = leftover + data
        usable = len(data) - len(data) % chunk_bytes
        leftover = data[usable:]
        samples = np.frombuffer(data[:usable], dtype=np.int16).astype(np.float32)
        for sec in samples.reshape(-1, ANALYSIS_RATE):
            rms = float(np.sqrt(np.mean(sec * sec)) + 1e-9)
            values.append(20 * np.log10(rms / 32768.0))
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg ses analizi başarısız: {media}")
    return np.array(values, dtype=np.float32)


def parse_chat(path: Path) -> list[float]:
    """YouTube live_chat.json veya Twitch rechat.json dosyasından mesaj zamanlarını (sn) okur."""
    text = path.read_text(encoding="utf-8", errors="ignore").strip()
    times: list[float] = []
    if not text:
        return times
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = None
    if isinstance(data, dict) and "comments" in data:  # Twitch rechat
        for c in data["comments"]:
            off = c.get("content_offset_seconds")
            if off is not None:
                times.append(float(off))
        return times
    if isinstance(data, list):  # bazı Twitch dökümleri doğrudan liste
        for c in data:
            off = c.get("content_offset_seconds") if isinstance(c, dict) else None
            if off is not None:
                times.append(float(off))
        return times
    for line in text.splitlines():  # YouTube: satır başına bir JSON
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        action = item.get("replayChatItemAction", {})
        off = action.get("videoOffsetTimeMsec")
        if off is not None:
            times.append(int(off) / 1000.0)
    return times


def _rolling_z(x: np.ndarray, window: int, min_scale: float = 1.0) -> np.ndarray:
    """Her noktayı çevresindeki pencerenin medyanı ve yayılımına göre z-skora çevirir.

    min_scale: yayılımın alt sınırı; neredeyse sabit bölümlerde küçük oynamaların
    dev z-skorlara dönüşmesini engeller (ses için dB, sohbet için mesaj sayısı).
    """
    if len(x) == 0:
        return x
    n = len(x)
    out = np.zeros(n, dtype=np.float32)
    step = max(1, window // 4)
    for i in range(0, n, step):
        lo, hi = max(0, i - window // 2), min(n, i + window // 2)
        seg = x[lo:hi]
        med = float(np.median(seg))
        mad = max(float(np.median(np.abs(seg - med))) * 1.4826, min_scale)
        j_hi = min(n, i + step)
        out[i:j_hi] = (x[i:j_hi] - med) / mad
    return out


def _smooth(x: np.ndarray, width: int) -> np.ndarray:
    if width <= 1 or len(x) == 0:
        return x
    kernel = np.ones(width, dtype=np.float32) / width
    return np.convolve(x, kernel, mode="same")


def score_timeline(
    loudness_db: np.ndarray, chat_times: list[float] | None = None, chat_weight: float = 1.0
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """(birleşik skor, ses z, sohbet z) dizilerini saniye başına döner."""
    n = len(loudness_db)
    # Sessiz bölümler (ara ekranı vb.) skoru bozmasın.
    floor = np.percentile(loudness_db, 5) if n else -90
    audio = np.maximum(loudness_db, floor)
    audio_z = np.clip(_smooth(_rolling_z(audio, 300), 4), -3, 6)
    chat_z = np.zeros(n, dtype=np.float32)
    if chat_times:
        counts = np.bincount(
            np.clip(np.array(chat_times, dtype=np.int64), 0, max(n - 1, 0)), minlength=n
        ).astype(np.float32)[:n]
        # Sohbet tepkisi olaydan birkaç saniye sonra gelir; geri kaydır (başa sarmadan).
        counts = np.concatenate([counts[6:], np.zeros(min(6, n), dtype=np.float32)])[:n]
        chat_z = np.clip(_smooth(_rolling_z(_smooth(counts, 10), 600, min_scale=0.3), 6), -3, 8)
    score = audio_z + chat_weight * chat_z
    return score, audio_z, chat_z


def pick_candidates(
    score: np.ndarray,
    audio_z: np.ndarray,
    chat_z: np.ndarray,
    count: int,
    min_gap: int = 90,
    before: float = 30.0,
    after: float = 15.0,
    skip_start: float = 120.0,
    min_score: float = 1.5,
) -> list[Candidate]:
    """En yüksek skorlu tepeleri, aralarında en az min_gap saniye olacak şekilde seçer."""
    n = len(score)
    order = np.argsort(score)[::-1]
    taken: list[int] = []
    for idx in order:
        if len(taken) >= count:
            break
        if idx < skip_start and n > skip_start * 4:
            continue  # yayın açılışı genelde "başlıyoruz" ekranıdır
        if score[idx] < min_score:
            break
        if all(abs(int(idx) - t) >= min_gap for t in taken):
            taken.append(int(idx))
    result = []
    for t in sorted(taken):
        result.append(
            Candidate(
                peak=float(t),
                start=max(0.0, t - before),
                end=min(float(n), t + after),
                score=round(float(score[t]), 2),
                audio_z=round(float(audio_z[t]), 2),
                chat_z=round(float(chat_z[t]), 2),
            )
        )
    return result


def decode_to_wav(media: Path, dst: Path, rate: int = 16000) -> Path:
    """Tüm sesi baştan sona çözüp tek kanallı WAV yapar.

    Eksik parçalı HLS dosyalarında -ss ile atlamak kapsayıcının zaman damgalarına gider ve
    10-20 sn kayabilir; baştan çözülen ses ise (boşluklar sessizlik olur) yayının gerçek
    zamanıyla örtüşür. WAV içinde atlama örnek düzeyinde kesindir.
    """
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(media), "-vn", "-ac", "1", "-ar", str(rate), str(dst)],
        check=True,
    )
    return dst


def extract_audio_segment(media: Path, start: float, end: float, dst: Path) -> Path:
    subprocess.run(
        [
            "ffmpeg", "-y", "-v", "error", "-ss", f"{start:.2f}", "-to", f"{end:.2f}",
            "-i", str(media), "-vn", "-ac", "1", "-ar", "16000", str(dst),
        ],
        check=True,
    )
    return dst
