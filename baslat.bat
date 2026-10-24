@echo off
rem Clipfarm yardımcı uygulamasını başlatır (ilk çalıştırmada kurulum yapar).
chcp 65001 >nul
cd /d "%~dp0"
where ffmpeg >nul 2>nul || (echo ffmpeg bulunamadi. Kurmak icin: winget install Gyan.FFmpeg & pause & exit /b 1)
set "PY=python"
where python >nul 2>nul || set "PY=py -3"
if not exist .venv (
  echo Ilk kurulum yapiliyor...
  %PY% -m venv .venv || (echo Python bulunamadi: https://www.python.org/downloads/ & pause & exit /b 1)
  call .venv\Scripts\activate.bat
  python -m pip install -U pip
  pip install -e . || (pause & exit /b 1)
) else (
  call .venv\Scripts\activate.bat
)
pip install -q -U yt-dlp
clipfarm serve
pause
