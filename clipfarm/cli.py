"""clipfarm komut satırı arayüzü.

Örnekler:
  clipfarm get "https://www.twitch.tv/videos/123" --start 1:02:10 --end 1:02:55
  clipfarm make "https://kick.com/xqc/clips/clip_01..." --hook "BUNU BEKLEMİYORDU 😂" --credit "kick.com/xqc"
  clipfarm make indirilen.mp4 --layout split --cam 0.72,0.02,0.27,0.3
"""

from __future__ import annotations

import argparse
import re
import sys
import tempfile
from pathlib import Path

from . import __version__
from .captions import Style, build_ass, load_words, save_words, transcribe
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

        ass_path = out_dir / f"{name}.ass"
        if args.ass:
            ass_path = Path(args.ass).resolve()
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
            if args.layout == "split" and args.caption_pos == Style.position:
                # Kamera ile oyun görüntüsünün birleştiği yere yakın dursun.
                style.position = min(0.9, args.cam_height + 0.08)
            ass_path.write_text(
                build_ass(
                    words, language, info["duration"], style,
                    hook=args.hook, hook_seconds=args.hook_seconds, credit=args.credit,
                ),
                encoding="utf-8",
            )

        layout = Layout(
            mode=args.layout, crop_x=args.crop_x, cam=args.cam,
            cam_height=args.cam_height, zoom=args.zoom,
        )
        dst = out_dir / f"{name}_short.mp4"
        print("Dikey video oluşturuluyor…")
        render(src, dst, layout, ass_path, fps=args.fps, crf=args.crf, has_audio=info["has_audio"])
        print(f"\nHazır: {dst}\nAltyazı dosyası: {ass_path}")


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

    txt = m.add_argument_group("yazılar")
    txt.add_argument("--hook", help="üstte görünen başlık (izleyiciyi tutan cümle)")
    txt.add_argument("--hook-seconds", type=float, help="başlık kaç saniye görünsün (varsayılan: tüm video)")
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
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        raise SystemExit(130)


if __name__ == "__main__":
    main()
