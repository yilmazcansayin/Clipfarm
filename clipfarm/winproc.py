"""Windows'ta konsol penceresi açmadan çalışmak için yardımcılar."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform.startswith("win")
CREATE_NO_WINDOW = 0x08000000


def hide_console_windows() -> None:
    """Bu süreçten başlatılan her alt süreç (ffmpeg, pip…) pencere açmadan çalışsın.

    Uygulama pythonw ile (konsolsuz) çalışırken Windows her konsol programı için yeni bir pencere açar.
    """
    if not WINDOWS or getattr(subprocess.Popen.__init__, "_clipfarm_hidden", False):
        return
    original = subprocess.Popen.__init__

    def init(self, *args, **kwargs):
        if not kwargs.get("creationflags"):
            kwargs["creationflags"] = CREATE_NO_WINDOW
        original(self, *args, **kwargs)

    init._clipfarm_hidden = True
    subprocess.Popen.__init__ = init


def console_python() -> str:
    """Alt süreçler için python yorumlayıcısı: pythonw.exe'nin stdout'u olmadığından yanındaki python.exe."""
    exe = Path(sys.executable)
    if exe.name.lower() == "pythonw.exe" and (exe.parent / "python.exe").exists():
        return str(exe.parent / "python.exe")
    return sys.executable


def kill_tree(proc: subprocess.Popen) -> None:
    """Süreci ve başlattığı ffmpeg gibi alt süreçleri sonlandırır."""
    if proc.poll() is not None:
        return
    if WINDOWS:
        subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True,
                       creationflags=CREATE_NO_WINDOW)
    else:
        try:
            os.killpg(proc.pid, 9)
        except Exception:
            proc.kill()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def popen_kwargs() -> dict:
    """Ayrı süreç grubunda, penceresiz başlatma ayarları (kill_tree ile birlikte kullanılır)."""
    if WINDOWS:
        return {"creationflags": CREATE_NO_WINDOW}
    return {"start_new_session": True}
