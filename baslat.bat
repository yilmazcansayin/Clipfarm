@echo off
rem Clipfarm kurulumu (ilk sefer) ve yardimci uygulamayi pencere acmadan baslatma.
rem Sonraki acilislarda "Clipfarm Baslat.vbs" ya da Windows acilisi kullanilir; bu pencere kapanir.
chcp 65001 >nul
cd /d "%~dp0"
where ffmpeg >nul 2>nul || (echo ffmpeg bulunamadi. Kurmak icin: winget install Gyan.FFmpeg & pause & exit /b 1)
set "PY=python"
where python >nul 2>nul || set "PY=py -3"
if not exist .venv (
  echo Ilk kurulum yapiliyor...
  %PY% -m venv .venv || (echo Python bulunamadi: https://www.python.org/downloads/ & pause & exit /b 1)
  .venv\Scripts\python -m pip install -U pip
  .venv\Scripts\python -m pip install -e . || (pause & exit /b 1)
)
rem Windows acilinca da pencere acmadan baslasin.
.venv\Scripts\python -m clipfarm autostart on
start "" .venv\Scripts\pythonw.exe -m clipfarm serve
echo Clipfarm arka planda calisiyor (pencere acmadan). Bu pencere kapanabilir.
timeout /t 3 >nul
