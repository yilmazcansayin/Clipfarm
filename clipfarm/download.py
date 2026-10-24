"""yt-dlp ile YouTube / Twitch / Kick videolarını ve kliplerini indirme."""

from __future__ import annotations

from pathlib import Path


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
) -> Path:
    """Videoyu (istenirse yalnızca start-end aralığını) indirir ve dosya yolunu döner.

    progress: 0-1 arası ilerleme oranıyla çağrılır.
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
        "outtmpl": str(out_dir / f"{name}{suffix}.%(ext)s"),
        "format": fmt,
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
    }
    if audio_only:
        opts["postprocessors"] = [
            {"key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192"}
        ]
    if referer:
        opts["http_headers"] = {"Referer": referer}
    if progress:
        def hook(d: dict) -> None:
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            if d.get("status") == "downloading" and total:
                progress(min(0.99, d.get("downloaded_bytes", 0) / total))
            elif d.get("status") == "downloading" and d.get("fragment_count"):
                progress(min(0.99, (d.get("fragment_index") or 0) / d["fragment_count"]))

        opts["progress_hooks"] = [hook]
    if start is not None or end is not None:
        opts["download_ranges"] = download_range_func(
            None, [(start or 0.0, end if end is not None else float("inf"))]
        )
        opts["force_keyframes_at_cuts"] = True
    if cookies_from_browser:
        # Örn. "chrome": abonelere özel / yaş kısıtlı videolar için Chrome çerezlerini kullanır.
        opts["cookiesfrombrowser"] = (cookies_from_browser,)

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        if info.get("_type") == "playlist" and info.get("entries"):
            info = next(e for e in info["entries"] if e)
        downloads = info.get("requested_downloads") or []
        if downloads and downloads[0].get("filepath"):
            return Path(downloads[0]["filepath"])
        return Path(ydl.prepare_filename(info)).with_suffix(".mp3" if audio_only else ".mp4")
