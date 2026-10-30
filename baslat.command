#!/bin/bash
# Clipfarm yardımcı uygulamasını başlatır (macOS'ta çift tıklanabilir; Linux'ta ./baslat.command).
cd "$(dirname "$0")"
command -v ffmpeg >/dev/null || { echo "ffmpeg bulunamadı. Kurmak için: brew install ffmpeg"; read -r; exit 1; }
if [ ! -d .venv ]; then
  echo "İlk kurulum yapılıyor..."
  python3 -m venv .venv && source .venv/bin/activate && pip install -e . || { read -r; exit 1; }
else
  source .venv/bin/activate
fi
pip install -q -U "yt-dlp[default,curl-cffi]"
clipfarm serve
