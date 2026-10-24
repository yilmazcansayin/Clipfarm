"""yt-dlp ile YouTube / Twitch / Kick videolarını ve kliplerini indirme."""

from __future__ import annotations

from pathlib import Path


def download(
    url: str,
    out_dir: Path,
    start: float | None = None,
    end: float | None = None,
    max_height: int = 1080,
    cookies_from_browser: str | None = None,
) -> Path:
    """Videoyu (istenirse yalnızca start-end aralığını) indirir ve dosya yolunu döner."""
    import yt_dlp
    from yt_dlp.utils import download_range_func

    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = ""
    if start is not None or end is not None:
        suffix = f"_{int(start or 0)}-{int(end) if end is not None else 'son'}"
    opts: dict = {
        "outtmpl": str(out_dir / f"%(extractor_key)s_%(id)s{suffix}.%(ext)s"),
        "format": (
            f"bv*[height<={max_height}]+ba/b[height<={max_height}]/bv*+ba/b"
        ),
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": False,
        "no_warnings": False,
    }
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
        return Path(ydl.prepare_filename(info)).with_suffix(".mp4")
