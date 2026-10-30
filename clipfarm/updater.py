"""yt-dlp'yi arka planda (pencere açmadan) güncel tutar.

TikTok, Instagram gibi siteler sık değişir; eski yt-dlp en sık indirme hatası sebebidir. curl_cffi,
TikTok gibi tarayıcı taklidi isteyen siteler için gerekir. İndirmeler ayrı süreçte çalıştığından
güncellenen sürüm yeniden başlatmaya gerek kalmadan bir sonraki indirmede kullanılır.
"""

from __future__ import annotations

import subprocess
import threading
import time
from importlib import metadata

from .winproc import console_python, popen_kwargs
from .youtube import HOME

PACKAGE = "yt-dlp[default,curl-cffi]"
STAMP = HOME / "ytdlp_updated"
EVERY = 20 * 3600
_lock = threading.Lock()
state = {"running": False, "last": "", "error": ""}


def ytdlp_version() -> str:
    try:
        return metadata.version("yt-dlp")
    except metadata.PackageNotFoundError:
        return ""


def has_impersonation() -> bool:
    try:
        metadata.version("curl_cffi")
    except metadata.PackageNotFoundError:
        try:
            metadata.version("curl-cffi")
        except metadata.PackageNotFoundError:
            return False
    return True


def update() -> str:
    """pip ile yt-dlp'yi (ve curl_cffi'yi) günceller; yeni sürümü döner."""
    if not _lock.acquire(blocking=False):
        raise RuntimeError("Güncelleme zaten sürüyor.")
    state.update(running=True, error="")
    try:
        proc = subprocess.run(
            [console_python(), "-m", "pip", "install", "-q", "--disable-pip-version-check", "-U", PACKAGE],
            capture_output=True, text=True, timeout=900, **popen_kwargs(),
        )
        if proc.returncode != 0:
            raise RuntimeError((proc.stderr or proc.stdout).strip()[-400:] or "pip başarısız oldu")
        HOME.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
        state["last"] = ytdlp_version()
        return state["last"]
    except Exception as exc:
        state["error"] = str(exc)[:400]
        raise
    finally:
        state["running"] = False
        _lock.release()


def due() -> bool:
    try:
        return time.time() - STAMP.stat().st_mtime > EVERY or not has_impersonation()
    except OSError:
        return True


def update_in_background(force: bool = False) -> bool:
    if state["running"] or not (force or due()):
        return False

    def work() -> None:
        try:
            update()
        except Exception:
            pass

    threading.Thread(target=work, daemon=True).start()
    return True
