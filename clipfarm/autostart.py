"""Windows açılınca yardımcı uygulamayı pencere açmadan başlatma (Başlangıç klasörüne .vbs koyar)."""

from __future__ import annotations

import os
import sys
from pathlib import Path

NAME = "Clipfarm.vbs"


def startup_dir() -> Path:
    return Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming")) / (
        "Microsoft/Windows/Start Menu/Programs/Startup"
    )


def script(python: Path) -> str:
    """pythonw ile `clipfarm serve`; 0 = gizli pencere, False = bekleme."""
    pyw = python.with_name("pythonw.exe") if python.name.lower() == "python.exe" else python
    cmd = f'"{pyw}" -m clipfarm serve'.replace('"', '""')
    return (
        "' Clipfarm yardimci uygulamasi (pencere acmadan). Kaldirmak icin: clipfarm autostart off\r\n"
        f'CreateObject("WScript.Shell").Run "{cmd}", 0, False\r\n'
    )


def enable() -> Path:
    if not sys.platform.startswith("win"):
        raise SystemExit("Açılışta başlatma yalnızca Windows'ta destekleniyor.")
    path = startup_dir() / NAME
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(script(Path(sys.executable)), encoding="utf-16")  # WSH, UTF-16 betikleri okuyabiliyor (Türkçe kullanıcı adları)
    return path


def disable() -> bool:
    path = startup_dir() / NAME
    if path.exists():
        path.unlink()
        return True
    return False
