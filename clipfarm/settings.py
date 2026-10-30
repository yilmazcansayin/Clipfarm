"""Kullanıcı ayarları (~/.clipfarm/settings.json); eklentinin açılır penceresinden değiştirilir."""

from __future__ import annotations

import json
import threading

from .youtube import HOME

PATH = HOME / "settings.json"
DEFAULTS: dict = {
    "parallel": 3,  # aynı anda kaç indirme
    "use_cookies": True,  # giriş gerektiren videolar için sayfanın çerezlerini kullan
    "drive_upload": False,  # her indirmeyi Google Drive'a da kaydet
    "drive_folder": "Clipfarm",  # Drive'daki klasör adı
    "drive_delete_local": False,  # Drive'a yüklenince bilgisayardaki kopyayı sil
    "drive_desktop_path": "",  # Drive masaüstü klasörü (boşsa kendisi bulur)
}
_lock = threading.Lock()


def _check(key: str, value):
    default = DEFAULTS[key]
    if isinstance(default, bool):
        return bool(value)
    if isinstance(default, int):
        return max(1, min(8, int(value)))
    return str(value).strip()


def load() -> dict:
    data = dict(DEFAULTS)
    try:
        saved = json.loads(PATH.read_text(encoding="utf-8"))
        for key, value in saved.items():
            if key in DEFAULTS:
                data[key] = _check(key, value)
    except Exception:
        pass
    return data


def update(changes: dict) -> dict:
    """Bilinen anahtarları doğrulayıp kaydeder; bilinmeyenleri yok sayar."""
    with _lock:
        data = load()
        for key, value in changes.items():
            if key in DEFAULTS:
                data[key] = _check(key, value)
        HOME.mkdir(parents=True, exist_ok=True)
        tmp = PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(PATH)
    return data
