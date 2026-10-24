"""ffmpeg ile 9:16 dikey Shorts videosu üretimi."""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .captions import HEIGHT, WIDTH

LAYOUTS = ("blur", "crop", "split")
SHORTS_MAX_SECONDS = 180


@dataclass
class Layout:
    mode: str = "blur"
    crop_x: float = 0.5  # crop/split: oyun görüntüsünün yatay odağı (0 = sol, 1 = sağ)
    cam: tuple[float, float, float, float] | None = None  # split: kamera alanı x,y,w,h (0-1)
    cam_height: float = 0.38  # split: üstteki kamera bölümünün ekran oranı
    zoom: float = 1.0  # blur: öndeki videoyu büyütme (1.0 = tam genişlik)


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if not shutil.which(tool):
            raise SystemExit(
                f"'{tool}' bulunamadı. Kurulum: Windows 'winget install Gyan.FFmpeg', "
                "macOS 'brew install ffmpeg', Linux 'sudo apt install ffmpeg'."
            )


def probe(path: Path) -> dict:
    out = subprocess.run(
        [
            "ffprobe", "-v", "error", "-print_format", "json",
            "-show_format", "-show_streams", str(path),
        ],
        check=True, capture_output=True, text=True,
    ).stdout
    data = json.loads(out)
    video = next((s for s in data["streams"] if s["codec_type"] == "video"), None)
    if video is None:
        raise SystemExit(f"{path} içinde video akışı yok.")
    return {
        "duration": float(data["format"].get("duration", 0) or 0),
        "width": int(video["width"]),
        "height": int(video["height"]),
        "has_audio": any(s["codec_type"] == "audio" for s in data["streams"]),
    }


def _even(n: float) -> int:
    return max(2, int(round(n / 2)) * 2)


def video_filter(layout: Layout) -> str:
    """Kaynağı 1080x1920 dikey kareye dönüştüren filter_complex ([0:v] -> [v])."""
    W, H = WIDTH, HEIGHT
    if layout.mode == "blur":
        fg_w = _even(W * layout.zoom)
        return (
            "[0:v]split=2[a][b];"
            f"[a]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},"
            "gblur=sigma=30,eq=brightness=-0.12[bg];"
            f"[b]scale={fg_w}:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[v]"
        )
    if layout.mode == "crop":
        return (
            f"[0:v]scale=-2:{H},crop={W}:{H}:(iw-ow)*{layout.crop_x:.4f}:0,setsar=1[v]"
        )
    if layout.mode == "split":
        if not layout.cam:
            raise SystemExit("split düzeni için --cam x,y,w,h gerekli (örn. --cam 0.72,0.02,0.27,0.3).")
        x, y, w, h = layout.cam
        cam_h = _even(H * layout.cam_height)
        game_h = H - cam_h
        return (
            "[0:v]split=2[c][g];"
            f"[c]crop=iw*{w:.4f}:ih*{h:.4f}:iw*{x:.4f}:ih*{y:.4f},"
            f"scale={W}:{cam_h}:force_original_aspect_ratio=increase,crop={W}:{cam_h}[cam];"
            f"[g]scale=-2:{game_h},crop={W}:{game_h}:(iw-ow)*{layout.crop_x:.4f}:0[game];"
            "[cam][game]vstack=inputs=2,setsar=1[v]"
        )
    raise SystemExit(f"Bilinmeyen düzen: {layout.mode} (seçenekler: {', '.join(LAYOUTS)})")


def trim(src: Path, dst: Path, start: float | None, end: float | None) -> Path:
    """Yerel dosyadan start-end aralığını kare doğruluğunda keser."""
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    if end is not None:
        cmd += ["-to", f"{end:.3f}"]
    cmd += [
        "-i", str(src), "-c:v", "libx264", "-preset", "veryfast", "-crf", "16",
        "-c:a", "aac", "-b:a", "192k", str(dst),
    ]
    subprocess.run(cmd, check=True)
    return dst


def render(
    src: Path,
    dst: Path,
    layout: Layout,
    ass_file: Path | None = None,
    fps: int = 30,
    crf: int = 20,
    has_audio: bool = True,
) -> Path:
    """Dikey düzen + yakılmış altyazı ile son Shorts videosunu üretir."""
    graph = video_filter(layout)
    if ass_file is not None:
        # Windows'ta sürücü harfi (C:) filtre sözdizimini bozduğu için ffmpeg'i ASS
        # dosyasının klasöründe çalıştırıp yalnızca dosya adını veriyoruz.
        graph = graph[: -len("[v]")] + f",ass={ass_file.name}[v]"
    graph = graph[: -len("[v]")] + f",fps={fps},format=yuv420p[v]"
    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-stats",
        "-i", str(src.resolve()),
        "-filter_complex", graph,
        "-map", "[v]",
    ]
    if has_audio:
        cmd += ["-map", "0:a:0", "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
    cmd += [
        "-c:v", "libx264", "-preset", "medium", "-crf", str(crf),
        "-profile:v", "high", "-movflags", "+faststart",
        str(dst.resolve()),
    ]
    dst.parent.mkdir(parents=True, exist_ok=True)
    cwd = ass_file.parent if ass_file is not None else None
    subprocess.run(cmd, check=True, cwd=cwd)
    return dst
