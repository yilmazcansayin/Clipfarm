"""yt-dlp ile YouTube / Twitch / Kick / TikTok… videolarını ve kliplerini indirme."""

from __future__ import annotations

import logging
from pathlib import Path

log = logging.getLogger("clipfarm.download")


class _Logger:
    """yt-dlp çıktısını konsol yerine logging'e yönlendirir (pythonw'da stdout yok)."""

    def debug(self, msg: str) -> None:
        if not msg.startswith("[debug] "):
            log.info(msg)

    def info(self, msg: str) -> None:
        log.info(msg)

    def warning(self, msg: str) -> None:
        log.warning(msg)

    def error(self, msg: str) -> None:
        log.error(msg)


def impersonation_available() -> bool:
    """curl_cffi kurulu mu? TikTok gibi siteler tarayıcı taklidi olmadan 403 verebiliyor."""
    try:
        import curl_cffi  # noqa: F401
    except Exception:
        return False
    return True


def download(
    url: str,
    out_dir: Path,
    start: float | None = None,
    end: float | None = None,
    max_height: int | None = 1080,
    cookies_from_browser: str | None = None,
    audio_only: bool = False,
    name: str = "%(extractor_key)s_%(id)s",
    referer: str | None = None,
    progress=None,
    cookiefile: Path | None = None,
    temp_dir: Path | None = None,
    on_status=None,
    info_out: dict | None = None,
) -> Path:
    """Videoyu (istenirse yalnızca start-end aralığını) indirir ve dosya yolunu döner.

    progress: 0-1 arası ilerleme oranıyla çağrılır.
    on_status: yt-dlp'nin ilerleme sözlüğüyle çağrılır (hız, kalan süre, boyut).
    temp_dir: yarım dosyalar burada tutulur; yeniden denemede kaldığı yerden devam eder.
    info_out: verilirse videonun tam süresi ("duration") buraya yazılır.
    """
    import yt_dlp
    from yt_dlp.utils import download_range_func

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = ""
    if start is not None or end is not None:
        suffix = f"_{int(start or 0)}-{int(end) if end is not None else 'son'}"
    if audio_only:
        fmt = "ba/b"
    elif max_height:
        fmt = f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b"
    else:
        fmt = "bv*+ba/b"
    opts: dict = {
        "outtmpl": f"{name}{suffix}.%(ext)s",
        "paths": {"home": str(out_dir), **({"temp": str(temp_dir)} if temp_dir else {})},
        "format": fmt,
        # Aynı çözünürlükte H.264'ü tercih et: TikTok'un H.265 (bytevc1) dosyaları her yerde açılmıyor.
        "format_sort": ["res", "vcodec:h264", "acodec:m4a"],
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "noprogress": True,
        "logger": _Logger(),
        # Takılan bağlantı sonsuza kadar beklemesin; kopan parçalar yeniden denensin.
        "socket_timeout": 30,
        "retries": 10,
        "fragment_retries": 10,
        "extractor_retries": 3,
        "file_access_retries": 5,
        "retry_sleep_functions": {"http": lambda n: min(2 ** n, 30), "fragment": lambda n: min(2 ** n, 30)},
        "concurrent_fragment_downloads": 4,
        "continuedl": True,
        "windowsfilenames": True,
    }
    if audio_only:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    if referer:
        opts["http_headers"] = {"Referer": referer}
    hooks = []
    if progress:
        def hook(d: dict) -> None:
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if d.get("status") == "downloading" and total:
                progress(min(0.99, d.get("downloaded_bytes", 0) / total))
            elif d.get("status") == "downloading" and d.get("fragment_count"):
                progress(min(0.99, (d.get("fragment_index") or 0) / d["fragment_count"]))

        hooks.append(hook)
    if on_status:
        hooks.append(on_status)
        opts["postprocessor_hooks"] = [on_status]
    if hooks:
        opts["progress_hooks"] = hooks
    if start is not None or end is not None:
        opts["download_ranges"] = download_range_func(
            None, [(start or 0.0, end if end is not None else float("inf"))]
        )
        opts["force_keyframes_at_cuts"] = True
    if cookiefile:
        opts["cookiefile"] = str(cookiefile)
    elif cookies_from_browser:
        # Örn. "chrome": abonelere özel / yaş kısıtlı videolar için Chrome çerezlerini kullanır.
        opts["cookiesfrombrowser"] = (cookies_from_browser,)

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info.get("_type") == "playlist" and info.get("entries"):
            info = next(e for e in info["entries"] if e)
        if info_out is not None:
            info_out["duration"] = info.get("duration")
        downloads = info.get("requested_downloads") or []
        if downloads and downloads[0].get("filepath"):
            return Path(downloads[0]["filepath"])
        return Path(ydl.prepare_filename(info)).with_suffix(".mp3" if audio_only else ".mp4")
