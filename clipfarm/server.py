"""Yerel panel ve Chrome eklentisi için HTTP sunucusu (yalnızca 127.0.0.1)."""

from __future__ import annotations

import json
import logging
import mimetypes
import re
import sys
import threading
import time
import urllib.request
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import urlparse

from . import DEFAULT_PORT, __version__, downloads, drive, editor, settings, updater, youtube
from .pipeline import JOBS_DIR, Job, Options, Worker, list_jobs, render_clip, run_job, upload_clip

EDITABLE = ("hook", "title", "description", "tags")


def _summary(job: Job, full: bool = False) -> dict:
    data = asdict(job)
    if not full:
        data.pop("candidates", None)
        data["log"] = job.log[-3:]
    for c in data["clips"]:
        c.pop("words", None)
        c["media"] = f"/media/{job.id}/{c['file']}" if c["file"] else ""
    return data


class Handler(BaseHTTPRequestHandler):
    server_version = f"clipfarm/{__version__}"
    worker: Worker
    manager: downloads.Manager
    store: downloads.Store
    drive_login: dict = {"running": False, "error": ""}
    restart = False

    def log_message(self, fmt, *args):  # sessiz
        pass

    # --- yardımcılar ---
    def _origin_ok(self) -> bool:
        origin = self.headers.get("Origin")
        if not origin:
            return True
        host = urlparse(origin)
        return origin.startswith("chrome-extension://") or (
            host.hostname in ("127.0.0.1", "localhost") and host.port == self.server.server_port
        )

    def _cors(self) -> None:
        origin = self.headers.get("Origin", "")
        if origin.startswith("chrome-extension://"):
            self.send_header("Access-Control-Allow-Origin", origin)
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, data, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._cors()
        self.end_headers()
        self.wfile.write(body)

    def _body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(length) or b"{}") if length else {}

    def _job(self, job_id: str) -> Job | None:
        if not re.fullmatch(r"[\w-]+", job_id) or not (JOBS_DIR / job_id / "job.json").exists():
            return None
        return Job.load(job_id)

    # --- yöntemler ---
    def do_OPTIONS(self):
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self):
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            html = resources.files("clipfarm").joinpath("web/index.html").read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.end_headers()
            self.wfile.write(html)
        elif path == "/api/status":
            prefs = settings.load()
            self._json({
                "ok": True, "version": __version__,
                "claude": editor.available(), "youtube": youtube.is_connected(),
                "ytdlp": updater.ytdlp_version(), "impersonate": updater.has_impersonation(),
                "updating": updater.state["running"], "update_error": updater.state["error"],
                "drive": {**drive.status(prefs), "login_running": self.drive_login["running"],
                          "login_error": self.drive_login["error"]},
                "download_dir": str(downloads.DOWNLOAD_DIR),
            })
        elif path == "/api/settings":
            self._json(settings.load())
        elif path == "/api/jobs":
            self._json([_summary(j) for j in list_jobs()[:50]])
        elif path == "/api/downloads":
            self._json([asdict(t) for t in self.store.list()[:50]])
        elif m := re.fullmatch(r"/api/jobs/([\w-]+)", path):
            job = self._job(m[1])
            self._json(_summary(job, full=True) if job else {"error": "iş bulunamadı"}, 200 if job else 404)
        elif m := re.fullmatch(r"/media/([\w-]+)/([\w.-]+\.mp4)", path):
            self._media(JOBS_DIR / m[1] / m[2])
        else:
            self._json({"error": "bulunamadı"}, 404)

    def do_POST(self):
        if not self._origin_ok():
            return self._json({"error": "izin verilmeyen kaynak"}, 403)
        path = urlparse(self.path).path
        body = self._body()
        if path == "/api/downloads":
            url = (body.get("url") or "").strip()
            if not re.match(r"^https?://", url, re.I):
                return self._json({"error": "geçerli bir video adresi (https://…) gerekli"}, 400)
            quality = body.get("quality") or "best"
            if quality not in downloads.QUALITIES:
                return self._json({"error": f"geçersiz kalite: {quality}"}, 400)
            try:
                start, end = body.get("start"), body.get("end")
                start = None if start is None else float(start)
                end = None if end is None else float(end)
            except (TypeError, ValueError):
                return self._json({"error": "başlangıç/bitiş sayı olmalı"}, 400)
            if start is not None and end is not None and end <= start:
                return self._json({"error": "bitiş, başlangıçtan sonra olmalı"}, 400)
            if existing := self.store.find_active(url, start, end, quality):
                return self._json({**asdict(existing), "duplicate": True})
            prefs = settings.load()
            task = self.store.add(
                url=url, sources=[s for s in body.get("sources") or [] if isinstance(s, str)][:10],
                referer=body.get("referer") or "", title=body.get("title") or "",
                start=start, end=end, quality=quality,
                drive=bool(body["drive"]) if "drive" in body else prefs["drive_upload"],
            )
            cookies = body.get("cookies") if prefs["use_cookies"] else None
            self.manager.submit(task, [c for c in cookies or [] if isinstance(c, dict)][:300])
            return self._json(asdict(task), 201)
        if m := re.fullmatch(r"/api/downloads/([\w-]+)/(reveal|cancel|retry|drive|forget)", path):
            task = self.store.tasks.get(m[1])
            if not task:
                return self._json({"error": "indirme bulunamadı"}, 404)
            action = m[2]
            if action == "reveal":
                if not task.file:
                    return self._json({"error": "dosya yok"}, 404)
                downloads.reveal(task.file)
            elif action == "cancel":
                if not self.manager.cancel(task.id):
                    return self._json({"error": "bu indirme zaten bitmiş"}, 400)
            elif action == "retry":
                if task.status in downloads.ACTIVE:
                    return self._json({"error": "indirme zaten sürüyor"}, 400)
                self.manager.submit(task)
            elif action == "drive":
                if task.status != "done" or task.local_deleted or not Path(task.file).exists():
                    return self._json({"error": "Drive'a gönderilecek dosya yok"}, 400)
                if task.drive_status == "uploading":
                    return self._json({"error": "zaten yükleniyor"}, 400)
                self.manager.drive(task)
            elif action == "forget":
                if task.status in downloads.ACTIVE:
                    return self._json({"error": "önce iptal edin"}, 400)
                self.store.tasks.pop(task.id, None)
                self.store.save()
            return self._json({"ok": True, **asdict(task)})
        if path == "/api/settings":
            return self._json(settings.update(body))
        if path == "/api/drive/login":
            if self.drive_login["running"]:
                return self._json({"ok": True, "running": True})
            threading.Thread(target=_drive_login, daemon=True).start()
            return self._json({"ok": True, "running": True})
        if path == "/api/restart":
            # Kod güncellendikten sonra yardımcıyı pencere açmadan yeniden başlatır (indirme sürerken olmaz).
            if any(t.status in downloads.ACTIVE for t in self.store.list()):
                return self._json({"error": "süren indirmeler var; bitince ya da iptal edince tekrar deneyin"}, 409)
            self._json({"ok": True})
            threading.Thread(target=_restart, args=(self.server,), daemon=True).start()
            return
        if path == "/api/ytdlp/update":
            if not updater.update_in_background(force=True):
                return self._json({"error": "güncelleme zaten sürüyor"}, 400)
            return self._json({"ok": True})
        if path == "/api/jobs":
            url = (body.get("url") or "").strip()
            if not url:
                return self._json({"error": "url gerekli"}, 400)
            opts = Options(
                count=int(body.get("count") or 5),
                layout=body.get("layout") or "blur",
                cam=body.get("cam") or None,
                ranges=body.get("ranges") or None,
                upload=bool(body.get("upload")),
                privacy=body.get("privacy") or "private",
                lang=body.get("lang") or None,
            )
            job = Job.new(url, opts)
            self.worker.submit(lambda: run_job(Job.load(job.id)))
            return self._json(_summary(job), 201)
        m = re.fullmatch(r"/api/jobs/([\w-]+)/clips/([\w-]+)/(render|upload)", path)
        if m and (job := self._job(m[1])):
            try:
                clip = job.clip(m[2])
            except StopIteration:
                return self._json({"error": "klip bulunamadı"}, 404)
            if m[3] == "render":
                self.worker.submit(lambda: _safe_render(job.id, clip.id))
                clip.status = "rendering"
            else:
                if not youtube.is_connected():
                    return self._json({"error": "YouTube hesabı bağlı değil: `clipfarm youtube-login`"}, 400)
                privacy, publish_at = body.get("privacy"), body.get("publish_at")
                self.worker.submit(lambda: _safe_upload(job.id, clip.id, privacy, publish_at))
                clip.status = "uploading"
            job.save()
            return self._json(_summary(job, full=True))
        self._json({"error": "bulunamadı"}, 404)

    def do_PATCH(self):
        if not self._origin_ok():
            return self._json({"error": "izin verilmeyen kaynak"}, 403)
        m = re.fullmatch(r"/api/jobs/([\w-]+)/clips/([\w-]+)", urlparse(self.path).path)
        if not m or not (job := self._job(m[1])):
            return self._json({"error": "bulunamadı"}, 404)
        clip = job.clip(m[2])
        for key, value in self._body().items():
            if key in EDITABLE:
                setattr(clip, key, value if key != "tags" else [t.strip() for t in value if t.strip()])
        job.save()
        self._json(_summary(job, full=True))

    def _media(self, file: Path) -> None:
        if not file.exists():
            return self._json({"error": "dosya yok"}, 404)
        size = file.stat().st_size
        start, end = 0, size - 1
        rng = self.headers.get("Range")
        if rng and (m := re.fullmatch(r"bytes=(\d*)-(\d*)", rng.strip())):
            if m[1]:
                start = int(m[1])
                end = int(m[2]) if m[2] else end
            elif m[2]:
                start = max(0, size - int(m[2]))
            end = min(end, size - 1)
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        else:
            self.send_response(200)
        self.send_header("Content-Type", mimetypes.guess_type(file.name)[0] or "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(end - start + 1))
        self.end_headers()
        with file.open("rb") as f:
            f.seek(start)
            remaining = end - start + 1
            while remaining > 0:
                chunk = f.read(min(1 << 20, remaining))
                if not chunk:
                    break
                try:
                    self.wfile.write(chunk)
                except (BrokenPipeError, ConnectionResetError):
                    return
                remaining -= len(chunk)


def _safe_render(job_id: str, clip_id: str) -> None:
    job = Job.load(job_id)
    clip = job.clip(clip_id)
    try:
        render_clip(job, clip)
        job.say(f"{clip.id} yeniden üretildi.")
    except Exception as exc:
        clip.status, clip.error = "failed", str(exc)
        job.say(f"{clip.id} üretilemedi: {exc}")


def _safe_upload(job_id: str, clip_id: str, privacy: str | None, publish_at: str | None) -> None:
    job = Job.load(job_id)
    upload_clip(job, job.clip(clip_id), privacy, publish_at)


def _restart(httpd) -> None:
    time.sleep(0.5)  # yanıt gitsin
    Handler.restart = True
    httpd.shutdown()  # serve() döngüsü biter, yeni süreci serve() başlatır


def _spawn_again(port: int) -> None:
    import subprocess

    from .winproc import popen_kwargs

    subprocess.Popen([sys.executable, "-m", "clipfarm", "serve", "--port", str(port)],
                     stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     close_fds=True, **popen_kwargs())


def _drive_login() -> None:
    Handler.drive_login.update(running=True, error="")
    try:
        drive.login()
    except Exception as exc:
        Handler.drive_login["error"] = str(exc)[:300]
    finally:
        Handler.drive_login["running"] = False


class Server(ThreadingHTTPServer):
    # Windows'ta SO_REUSEADDR, portu kullanan başka bir uygulama varken de bağlanmaya izin
    # veriyor ve istekler o uygulamaya gidiyor; orada kapalı tutup çakışmayı hata olarak gösteriyoruz.
    allow_reuse_address = not sys.platform.startswith("win")


def _already_running(port: int) -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/status", timeout=2) as r:
            return json.load(r).get("ok") is True
    except Exception:
        return False


def _log_to_file() -> None:
    """pythonw ile (konsolsuz) çalışırken çıktıları ~/.clipfarm/server.log dosyasına yazar."""
    log = youtube.HOME / "server.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    if log.exists() and log.stat().st_size > 5 * 1024 * 1024:
        log.replace(log.with_suffix(".log.1"))
    stream = log.open("a", encoding="utf-8", buffering=1)
    sys.stdout = sys.stderr = stream


def _keep_ytdlp_fresh() -> None:
    while True:
        updater.update_in_background()
        time.sleep(6 * 3600)


def serve(port: int = DEFAULT_PORT) -> None:
    from .winproc import hide_console_windows

    hidden = sys.stdout is None or sys.stderr is None
    if hidden:
        _log_to_file()
    logging.basicConfig(stream=sys.stderr, level=logging.WARNING, format="%(asctime)s %(message)s")
    hide_console_windows()
    if _already_running(port):
        print(f"Clipfarm zaten çalışıyor: http://127.0.0.1:{port}")
        return
    Handler.worker = Worker()
    Handler.store = downloads.Store()
    Handler.manager = downloads.Manager(Handler.store)
    try:
        httpd = Server(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise SystemExit(
            f"127.0.0.1:{port} portu başka bir uygulama tarafından kullanılıyor ({exc}). "
            "O uygulamayı kapatın ya da `clipfarm serve --port <başka port>` kullanın "
            "(eklentideki extension/shared.js dosyasındaki adresi de değiştirin)."
        )
    threading.Thread(target=_keep_ytdlp_fresh, daemon=True).start()
    print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Clipfarm paneli: http://127.0.0.1:{port}"
          + ("" if hidden else "  (durdurmak için Ctrl+C)"))
    print(f"İndirilenler: {downloads.DOWNLOAD_DIR}")
    print(f"Claude: {'açık' if editor.available() else 'kapalı (ANTHROPIC_API_KEY yok)'} · "
          f"YouTube: {'bağlı' if youtube.is_connected() else 'bağlı değil'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    httpd.server_close()
    if Handler.restart:
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S')} Yeniden başlatılıyor…")
        _spawn_again(port)
