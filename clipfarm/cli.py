"""clipfarm komut satırı arayüzü.

Örnekler:
  clipfarm get "https://www.twitch.tv/videos/123" --start 1:02:10 --end 1:02:55
  clipfarm make "https://kick.com/xqc/clips/clip_01..." --hook "BUNU BEKLEMİYORDU 😂" --credit "kick.com/xqc"
  clipfarm make indirilen.mp4 --layout split --cam 0.72,0.02,0.27,0.3
  clipfarm auto "https://www.twitch.tv/videos/2299..." --count 5
  clipfarm serve        # panel + Chrome eklentisi için yerel sunucu
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

from . import DEFAULT_PORT, __version__
from .captions import Style, build_ass, load_words, save_words, transcribe
from .effects import Effects, effects_filter
from .pipeline import make_short
from .render import LAYOUTS, SHORTS_MAX_SECONDS, Layout, probe, render, require_ffmpeg, trim
from .timeutil import parse_time


def _is_url(text: str) -> bool:
    return bool(re.match(r"^https?://", text, re.I))


def _cam(value: str) -> tuple[float, float, float, float]:
    try:
        parts = tuple(float(p) for p in value.split(","))
    except ValueError as exc:
        raise argparse.ArgumentTypeError("--cam biçimi: x,y,w,h (0-1 arası oranlar)") from exc
    if len(parts) != 4 or not all(0 <= p <= 1 for p in parts):
        raise argparse.ArgumentTypeError("--cam biçimi: x,y,w,h (0-1 arası oranlar)")
    return parts  # type: ignore[return-value]


def _slug(text: str) -> str:
    text = re.sub(r"[^\w.-]+", "_", text, flags=re.UNICODE).strip("_")
    return text[:80] or "klip"


def _add_range(p: argparse.ArgumentParser) -> None:
    p.add_argument("--start", "-s", help="başlangıç (örn. 1:02:10, 3725, 1h2m10s)")
    p.add_argument("--end", "-e", help="bitiş (örn. 1:02:55)")
    p.add_argument(
        "--cookies-from-browser", metavar="TARAYICI",
        help="indirirken tarayıcı çerezlerini kullan (örn. chrome)",
    )


def _add_look(p: argparse._ActionsContainer) -> None:
    p.add_argument("--look", choices=("viral", "classic"), default="viral",
                   help="viral: ortada harf harf biriken neon altyazı (örnek Shorts tarzı); classic: altta altyazı")
    p.add_argument("--aspect", choices=("3:4", "9:16"), default="3:4", help="çıktı oranı")
    p.add_argument("--watermark", help="alt ortada küçük kanal adı, örn. @kanalim")
    p.add_argument("--effects", action="store_true", help="renk canlandırma ve vurgu flaşlarını aç (varsayılan kapalı)")
    p.add_argument("--no-story", action="store_true", help="kurgu yapma (başa kanca koyma, ölü anları kesme)")
    p.add_argument("--accent", default="FFE500", help="altyazı vurgu rengi RRGGBB; 'multi' = her cümlede farklı renk")


def cmd_get(args: argparse.Namespace) -> None:
    from .download import download

    path = download(
        args.url, Path(args.out),
        parse_time(args.start), parse_time(args.end),
        cookies_from_browser=args.cookies_from_browser,
    )
    print(f"\nİndirildi: {path}")


def cmd_make(args: argparse.Namespace) -> None:
    require_ffmpeg()
    start, end = parse_time(args.start), parse_time(args.end)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="clipfarm_") as tmp:
        tmp_dir = Path(tmp)
        if _is_url(args.input):
            from .download import download

            src = download(
                args.input, Path(args.downloads), start, end,
                cookies_from_browser=args.cookies_from_browser,
            )
        else:
            src = Path(args.input)
            if not src.exists():
                raise SystemExit(f"Dosya bulunamadı: {src}")
            if start is not None or end is not None:
                print("Kesiliyor…")
                src = trim(src, tmp_dir / "kesit.mp4", start, end)

        info = probe(src)
        name = args.name or _slug(src.stem)
        if info["duration"] > SHORTS_MAX_SECONDS:
            print(
                f"Uyarı: video {info['duration']:.0f} sn. Shorts sınırı {SHORTS_MAX_SECONDS} sn; "
                "--start/--end ile kısaltın.",
                file=sys.stderr,
            )

        layout = Layout(
            mode=args.layout, crop_x=args.crop_x, cam=args.cam,
            cam_height=args.cam_height, zoom=args.zoom,
            height=1440 if args.aspect == "3:4" else 1920,
        )
        dst = out_dir / f"{name}_short.mp4"
        if args.ass:
            ass_path = Path(args.ass).resolve()
            print("Dikey video oluşturuluyor…")
            fx = effects_filter(Effects()) if args.effects else None
            render(src, dst, layout, ass_path, fps=args.fps, crf=args.crf, has_audio=info["has_audio"], effects=fx)
        else:
            words, language = [], args.lang or ""
            words_path = out_dir / f"{name}.words.json"
            if args.no_captions or not info["has_audio"]:
                pass
            elif args.words and Path(args.words).exists():
                words, language = load_words(Path(args.words))
            else:
                print(f"Altyazı çıkarılıyor (Whisper '{args.model}')…")
                words, language = transcribe(src, language=args.lang, model_size=args.model)
                save_words(words, words_path, language)
                print(f"Kelimeler kaydedildi: {words_path} (düzenleyip --words ile tekrar kullanabilirsiniz)")
            style = Style(
                font=args.font,
                size=args.font_size,
                highlight=args.highlight.lstrip("#"),
                position=args.caption_pos,
                uppercase=not args.no_upper,
                words_per_line=args.words_per_line,
            )
            print("Dikey video oluşturuluyor…")
            make_short(
                src, dst, layout, words, language, info["duration"],
                hook=args.hook, credit=args.credit, style=style, has_audio=info["has_audio"],
                fps=args.fps, crf=args.crf, effects=Effects() if args.effects else None,
                look=args.look, watermark=args.watermark, story=not args.no_story,
                accent=None if args.accent == "multi" else args.accent,
            )
            ass_path = dst.with_suffix(".ass")
        print(f"\nHazır: {dst}\nAltyazı dosyası: {ass_path}")


def cmd_auto(args: argparse.Namespace) -> None:
    require_ffmpeg()
    from .pipeline import Job, Options, run_job

    opts = Options(
        count=args.count, layout=args.layout, cam=list(args.cam) if args.cam else None,
        cam_height=args.cam_height, crop_x=args.crop_x, model=args.model, lang=args.lang,
        upload=args.upload, privacy=args.privacy, effects=args.effects,
        look=args.look, aspect=args.aspect, watermark=args.watermark or "",
        story=not args.no_story, accent="" if args.accent == "multi" else args.accent,
    )
    job = run_job(Job.new(args.url, opts))
    print()
    for c in job.clips:
        line = f"{c.id}: {c.status} {job.dir / c.file if c.file else ''}"
        print(line + (f"  {c.title}" if c.title else "") + (f"  {c.youtube_url}" if c.youtube_url else ""))
    if job.status == "failed":
        raise SystemExit(1)


def cmd_serve(args: argparse.Namespace) -> None:
    require_ffmpeg()
    from .server import serve

    serve(args.port)


def cmd_youtube_login(args: argparse.Namespace) -> None:
    from .youtube import login

    login()


def cmd_upload(args: argparse.Namespace) -> None:
    from .youtube import upload

    url = upload(
        Path(args.file), args.title, args.description or "", args.tags.split(",") if args.tags else [],
        privacy=args.privacy, publish_at=args.publish_at,
        progress=lambda p: print(f"\r%{p * 100:.0f}", end="", flush=True),
    )
    print(f"\nYüklendi: {url}")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="clipfarm",
        description="Yayın kesitlerinden YouTube Shorts üretme aracı (yt-dlp + ffmpeg + Whisper).",
    )
    parser.add_argument("--version", action="version", version=f"clipfarm {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    g = sub.add_parser("get", help="video/klip indir (YouTube, Twitch, Kick…)")
    g.add_argument("url")
    _add_range(g)
    g.add_argument("--out", "-o", default="downloads", help="indirme klasörü")
    g.set_defaults(func=cmd_get)

    m = sub.add_parser("make", help="URL veya dosyadan dikey, altyazılı Shorts üret")
    m.add_argument("input", help="URL ya da yerel video dosyası")
    _add_range(m)
    m.add_argument("--out", "-o", default="output", help="çıktı klasörü")
    m.add_argument("--downloads", default="downloads", help="indirme klasörü")
    m.add_argument("--name", help="çıktı dosya adı")

    lay = m.add_argument_group("düzen")
    lay.add_argument("--layout", "-l", choices=LAYOUTS, default="blur",
                     help="blur: bulanık arka plan, crop: ortadan kırp, split: üstte kamera altta oyun")
    lay.add_argument("--crop-x", type=float, default=0.5, help="kırpma odağı 0 (sol) - 1 (sağ)")
    lay.add_argument("--cam", type=_cam, help="split: kamera alanı x,y,w,h oranları")
    lay.add_argument("--cam-height", type=float, default=0.38, help="split: kamera bölümünün yüksekliği")
    lay.add_argument("--zoom", type=float, default=1.0, help="blur: ön videoyu büyüt (örn. 1.3)")
    _add_look(lay)

    txt = m.add_argument_group("yazılar")
    txt.add_argument("--hook", help="üstte görünen başlık (izleyiciyi tutan cümle)")
    txt.add_argument("--credit", help="kaynak yazısı, örn. 'twitch.tv/jahrein'")

    cap = m.add_argument_group("altyazı")
    cap.add_argument("--lang", help="konuşma dili (tr, en…). Boşsa otomatik algılanır")
    cap.add_argument("--model", default="small",
                     help="Whisper modeli: tiny, base, small, medium, large-v3 (büyük = yavaş ama doğru)")
    cap.add_argument("--no-captions", action="store_true", help="altyazı ekleme")
    cap.add_argument("--words", help="daha önce kaydedilen/düzenlenen .words.json dosyasını kullan")
    cap.add_argument("--ass", help="hazır .ass altyazı dosyasını olduğu gibi kullan")
    cap.add_argument("--font", default="Arial")
    cap.add_argument("--font-size", type=int, default=84)
    cap.add_argument("--highlight", default="FFE500", help="konuşulan kelimenin rengi (RRGGBB)")
    cap.add_argument("--caption-pos", type=float, default=Style.position,
                     help="altyazının dikey konumu 0 (üst) - 1 (alt)")
    cap.add_argument("--words-per-line", type=int, default=3)
    cap.add_argument("--no-upper", action="store_true", help="büyük harfe çevirme")

    enc = m.add_argument_group("kodlama")
    enc.add_argument("--fps", type=int, default=30)
    enc.add_argument("--crf", type=int, default=20, help="kalite (düşük = daha iyi, 18-23)")
    m.set_defaults(func=cmd_make)

    a = sub.add_parser("auto", help="uzun yayında en iyi anları bulup Shorts üret (ve isteğe bağlı yükle)")
    a.add_argument("url", help="VOD / video / klip adresi ya da yerel dosya")
    a.add_argument("--count", "-n", type=int, default=5, help="kaç klip")
    a.add_argument("--layout", "-l", choices=LAYOUTS, default="crop")
    a.add_argument("--cam", type=_cam)
    a.add_argument("--cam-height", type=float, default=0.38)
    a.add_argument("--crop-x", type=float, default=0.5)
    a.add_argument("--model", default="small", help="Whisper modeli")
    a.add_argument("--lang", help="konuşma dili (boşsa otomatik)")
    a.add_argument("--upload", action="store_true", help="hazır klipleri YouTube'a yükle")
    a.add_argument("--privacy", choices=("private", "unlisted", "public"), default="private")
    _add_look(a)
    a.set_defaults(func=cmd_auto)

    sv = sub.add_parser("serve", help="panel ve Chrome eklentisi için yerel sunucuyu başlat")
    sv.add_argument("--port", type=int, default=DEFAULT_PORT)
    sv.set_defaults(func=cmd_serve)

    yl = sub.add_parser("youtube-login", help="YouTube hesabını bağla (bir kez)")
    yl.set_defaults(func=cmd_youtube_login)

    up = sub.add_parser("upload", help="bir videoyu YouTube'a Shorts olarak yükle")
    up.add_argument("file")
    up.add_argument("--title", required=True)
    up.add_argument("--description")
    up.add_argument("--tags", help="virgülle ayrılmış")
    up.add_argument("--privacy", choices=("private", "unlisted", "public"), default="private")
    up.add_argument("--publish-at", help="zamanlanmış yayın, ISO 8601 (örn. 2026-11-01T18:00:00Z)")
    up.set_defaults(func=cmd_upload)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
