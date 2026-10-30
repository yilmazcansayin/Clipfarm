"""Tek bir indirmeyi ayrı bir süreçte çalıştırır (`python -m clipfarm.dlchild`).

Ayrı süreç sayesinde takılan bir indirme ana uygulamayı kilitlemez ve iptal edilince gerçekten durur.
Girdi: stdin'den JSON (download() argümanları). Çıktı: stdout'a satır satır JSON olayları:
  {"phase": "...", "downloaded": n, "total": n, "speed": n, "eta": n, "progress": 0-1}
  {"done": "<dosya yolu>"}  ya da  {"error": "<mesaj>"}
yt-dlp günlükleri stderr'e gider (ana uygulama bunları görev günlüğüne yazar).
"""

from __future__ import annotations

import io
import json
import logging
import sys
import time
from pathlib import Path


def _emit(out, data: dict) -> None:
    out.write(json.dumps(data, ensure_ascii=False) + "\n")
    out.flush()


def main() -> int:
    # Windows'ta konsolsuz süreçlerin varsayılan kodlaması cp1254 olabiliyor; Türkçe/emoji başlıklar bozulmasın.
    out = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", line_buffering=True)
    err = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    logging.basicConfig(stream=err, level=logging.INFO, format="%(asctime)s %(message)s")
    spec = json.loads(sys.stdin.buffer.read().decode("utf-8"))
    from .download import download
    from .winproc import hide_console_windows

    hide_console_windows()  # ffmpeg pencere açmasın

    last = [0.0]

    def on_status(d: dict) -> None:
        status = d.get("status")
        if "postprocessor" in d:  # postprocessor_hooks
            if status == "started":
                _emit(out, {"phase": "processing", "step": d.get("postprocessor")})
            return
        if status == "finished":
            _emit(out, {"phase": "processing"})
            return
        if status != "downloading" or time.time() - last[0] < 0.5:
            return
        last[0] = time.time()
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        done = d.get("downloaded_bytes") or 0
        if total:
            frac = done / total
        elif d.get("fragment_count"):
            frac = (d.get("fragment_index") or 0) / d["fragment_count"]
        else:
            frac = None
        _emit(out, {
            "phase": "downloading", "downloaded": done, "total": total,
            "speed": d.get("speed"), "eta": d.get("eta"),
            "progress": None if frac is None else round(min(frac, 0.99), 4),
        })

    _emit(out, {"phase": "extracting"})
    try:
        path = download(
            spec["url"], Path(spec["out_dir"]), spec.get("start"), spec.get("end"),
            max_height=spec.get("max_height"), audio_only=spec.get("audio_only", False),
            name=spec["name"], referer=spec.get("referer") or None,
            cookiefile=Path(spec["cookiefile"]) if spec.get("cookiefile") else None,
            temp_dir=Path(spec["temp_dir"]) if spec.get("temp_dir") else None,
            on_status=on_status,
        )
    except BaseException as exc:  # noqa: BLE001 - her hatayı ana uygulamaya bildir
        msg = str(exc) or exc.__class__.__name__
        _emit(out, {"error": msg.replace("\x1b[0;31m", "").replace("\x1b[0m", "")})
        return 1
    _emit(out, {"done": str(path)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
