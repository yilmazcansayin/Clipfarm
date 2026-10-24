"""YouTube Data API v3 ile Shorts yükleme.

Kurulum (bir kez):
  1. https://console.cloud.google.com → yeni proje → "YouTube Data API v3"ü etkinleştir.
  2. OAuth izin ekranı → "External", kendi Google hesabını test kullanıcısı olarak ekle.
  3. Kimlik bilgileri → OAuth istemci kimliği → "Masaüstü uygulaması" → JSON'u indir.
  4. Dosyayı ~/.clipfarm/client_secret.json olarak kaydet ve `clipfarm youtube-login` çalıştır.
"""

from __future__ import annotations

import os
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
HOME = Path(os.environ.get("CLIPFARM_HOME", Path.home() / ".clipfarm"))
CLIENT_SECRET = HOME / "client_secret.json"
TOKEN = HOME / "youtube_token.json"
CATEGORY_GAMING, CATEGORY_ENTERTAINMENT = "20", "24"


def _credentials(interactive: bool):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    creds = None
    if TOKEN.exists():
        creds = Credentials.from_authorized_user_file(str(TOKEN), SCOPES)
    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
    elif interactive:
        from google_auth_oauthlib.flow import InstalledAppFlow

        if not CLIENT_SECRET.exists():
            raise SystemExit(
                f"{CLIENT_SECRET} bulunamadı. Google Cloud Console'dan 'Masaüstü uygulaması' "
                "OAuth istemcisi oluşturup JSON dosyasını bu adla kaydedin (README → YouTube)."
            )
        flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
        creds = flow.run_local_server(port=0, prompt="consent")
    else:
        raise RuntimeError("YouTube hesabı bağlı değil. Önce `clipfarm youtube-login` çalıştırın.")
    HOME.mkdir(parents=True, exist_ok=True)
    TOKEN.write_text(creds.to_json(), encoding="utf-8")
    return creds


def login() -> None:
    _credentials(interactive=True)
    print(f"YouTube hesabı bağlandı. Anahtar: {TOKEN}")


def is_connected() -> bool:
    return TOKEN.exists()


def upload(
    path: Path,
    title: str,
    description: str = "",
    tags: list[str] | None = None,
    privacy: str = "private",
    publish_at: str | None = None,
    category: str = CATEGORY_ENTERTAINMENT,
    progress=None,
) -> str:
    """Videoyu yükler ve izleme adresini döner. publish_at: ISO 8601 (privacy 'private' olmalı)."""
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    if "#shorts" not in (title + " " + description).lower():
        description = (description + "\n\n#shorts").strip()
    body = {
        "snippet": {
            "title": title[:100],
            "description": description[:5000],
            "tags": tags or [],
            "categoryId": category,
        },
        "status": {
            "privacyStatus": "private" if publish_at else privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    if publish_at:
        body["status"]["publishAt"] = publish_at
    youtube = build("youtube", "v3", credentials=_credentials(interactive=False), cache_discovery=False)
    request = youtube.videos().insert(
        part="snippet,status",
        body=body,
        media_body=MediaFileUpload(str(path), mimetype="video/mp4", chunksize=8 * 1024 * 1024, resumable=True),
    )
    response = None
    while response is None:
        status, response = request.next_chunk()
        if status and progress:
            progress(status.progress())
    return f"https://youtube.com/shorts/{response['id']}"
