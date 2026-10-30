"""İndirilen videoları Google Drive'a kaydetme.

İki yol var, kendisi seçer:
  1. Bilgisayarda "Google Drive masaüstü" kuruluysa dosyayı Drive klasörüne (örn. G:\\My Drive\\Clipfarm) kopyalar.
  2. Değilse Drive API ile doğrudan yükler. Bunun için bir kez `clipfarm drive-login` (ya da eklentideki
     "Drive'a bağlan") gerekir; YouTube için kullanılan client_secret.json kullanılır ve Google Cloud
     projesinde "Google Drive API" etkin olmalıdır.
Uygulama yalnızca kendi oluşturduğu dosyaları görebilir (drive.file izni), Drive'daki diğer dosyalara dokunamaz.
"""

from __future__ import annotations

import shutil
import string
import sys
from pathlib import Path

from .youtube import CLIENT_SECRET, HOME

SCOPES = ["https://www.googleapis.com/auth/drive.file"]
TOKEN = HOME / "drive_token.json"
MY_DRIVE_NAMES = ("My Drive", "Drive'ım", "Drive’ım", "Mi unidad", "Meine Ablage", "Mon Drive")
CHUNK = 16 * 1024 * 1024
_folder_ids: dict[str, str] = {}


def desktop_folder(configured: str = "") -> Path | None:
    """Google Drive masaüstü uygulamasının "My Drive" klasörü (kurulu değilse None)."""
    candidates: list[Path] = []
    if configured:
        candidates.append(Path(configured))
    if sys.platform.startswith("win"):
        for letter in "G" + string.ascii_uppercase.replace("G", "").replace("A", "").replace("B", "").replace("C", ""):
            candidates += [Path(f"{letter}:\\{name}") for name in MY_DRIVE_NAMES]
    else:
        cloud = Path.home() / "Library" / "CloudStorage"
        if cloud.is_dir():
            for account in sorted(cloud.glob("GoogleDrive-*")):
                candidates += [account / name for name in MY_DRIVE_NAMES]
    for path in candidates:
        try:
            if path.is_dir():
                return path
        except OSError:
            continue
    return None


def is_connected() -> bool:
    return TOKEN.exists()


def _credentials(interactive: bool):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES) if TOKEN.exists() else None
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif interactive:
        if not CLIENT_SECRET.exists():
            raise RuntimeError(f"{CLIENT_SECRET} bulunamadı (YouTube kurulumundaki OAuth dosyası).")
        from google_auth_oauthlib.flow import InstalledAppFlow

        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0, open_browser=True)
    else:
        raise RuntimeError("Google Drive bağlı değil. Eklentide \"Drive'a bağlan\"a basın.")
    HOME.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


def login() -> None:
    _credentials(interactive=True)


def _service():
    from googleapiclient.discovery import build

    return build("drive", "v3", credentials=_credentials(interactive=False), cache_discovery=False)


def _folder_id(service, name: str) -> str:
    if name in _folder_ids:
        return _folder_ids[name]
    safe = name.replace("\\", "\\\\").replace("'", "\\'")
    found = service.files().list(
        q=f"name = '{safe}' and mimeType = 'application/vnd.google-apps.folder' and trashed = false",
        spaces="drive", fields="files(id)", pageSize=1,
    ).execute().get("files", [])
    if found:
        folder = found[0]["id"]
    else:
        folder = service.files().create(
            body={"name": name, "mimeType": "application/vnd.google-apps.folder"}, fields="id",
        ).execute()["id"]
    _folder_ids[name] = folder
    return folder


def _friendly(exc: Exception) -> str:
    text = str(exc)
    if "accessNotConfigured" in text or "has not been used in project" in text or "is disabled" in text:
        return ("Google Cloud projesinde \"Google Drive API\" kapalı. Cloud Console → API'ler → "
                "Google Drive API → Etkinleştir, sonra tekrar deneyin.")
    if "invalid_grant" in text:
        return "Drive oturumunun süresi dolmuş. Eklentide \"Drive'a bağlan\"a yeniden basın."
    return text[:300]


def upload(path: Path, folder: str, progress=None) -> dict:
    """Drive API ile kaldığı yerden devam edebilen (resumable) yükleme; büyük dosyalar parça parça gider."""
    from googleapiclient.errors import HttpError
    from googleapiclient.http import MediaFileUpload

    try:
        service = _service()
        media = MediaFileUpload(str(path), resumable=True, chunksize=CHUNK)
        request = service.files().create(
            body={"name": path.name, "parents": [_folder_id(service, folder)]},
            media_body=media, fields="id,webViewLink,size",
        )
        response = None
        while response is None:
            status, response = request.next_chunk(num_retries=5)
            if status and progress:
                progress(status.progress())
    except HttpError as exc:
        raise RuntimeError(_friendly(exc)) from exc
    except Exception as exc:
        if "invalid_grant" in str(exc) or "Drive bağlı değil" in str(exc):
            raise RuntimeError(_friendly(exc)) from exc
        raise
    if int(response.get("size") or -1) != path.stat().st_size:
        raise RuntimeError("Drive'daki dosyanın boyutu tutmuyor; yerel kopya silinmedi.")
    return {"mode": "api", "id": response["id"], "link": response.get("webViewLink", "")}


def copy_to_folder(path: Path, root: Path, folder: str, move: bool, progress=None) -> dict:
    """Drive masaüstü klasörüne kopyalar (Drive uygulaması arka planda buluta yükler)."""
    dest_dir = root / folder
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / path.name
    size = path.stat().st_size or 1
    done = 0
    with path.open("rb") as src, dest.open("wb") as dst:
        while chunk := src.read(CHUNK):
            dst.write(chunk)
            done += len(chunk)
            if progress:
                progress(done / size)
    shutil.copystat(path, dest)
    if dest.stat().st_size != path.stat().st_size:
        raise RuntimeError("Drive klasörüne kopyalanan dosyanın boyutu tutmuyor.")
    if move:
        path.unlink()
    return {"mode": "folder", "link": str(dest)}


def save(path: Path, settings: dict, progress=None) -> dict:
    """Ayarlara göre dosyayı Drive'a kaydeder. Dönen sözlükte link ve dosyanın yerelden silinip silinmediği var."""
    folder = settings.get("drive_folder") or "Clipfarm"
    delete = bool(settings.get("drive_delete_local"))
    root = desktop_folder(settings.get("drive_desktop_path", ""))
    if root:
        result = copy_to_folder(path, root, folder, move=delete, progress=progress)
        result["deleted_local"] = delete
        return result
    result = upload(path, folder, progress)
    if delete:
        path.unlink()
    result["deleted_local"] = delete
    return result


def status(settings: dict) -> dict:
    root = desktop_folder(settings.get("drive_desktop_path", ""))
    return {
        "desktop": str(root) if root else "",
        "connected": is_connected(),
        "ready": bool(root) or is_connected(),
    }
