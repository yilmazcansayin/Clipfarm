"""Yerel panel ve Chrome eklentisi için HTTP sunucusu (yalnızca 127.0.0.1)."""

from __future__ import annotations

import json
import mimetypes
import re
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from urllib.parse import urlparse

from . import __version__, downloads, editor, youtube
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
    dl_worker: Worker
    store: downloads.Store

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
            self._json({
                "ok": True, "version": __version__,
                "claude": editor.available(), "youtube": youtube.is_connected(),
            })
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
            if not url:
                return self._json({"error": "url gerekli"}, 400)
            quality = body.get("quality") or "best"
            if quality not in downloads.QUALITIES:
                return self._json({"error": f"geçersiz kalite: {quality}"}, 400)
            start, end = body.get("start"), body.get("end")
            if start is not None and end is not None and float(end) <= float(start):
                return self._json({"error": "bitiş, başlangıçtan sonra olmalı"}, 400)
            task = self.store.add(
                url=url, sources=[s for s in body.get("sources") or [] if isinstance(s, str)][:10],
                referer=body.get("referer") or "", title=body.get("title") or "",
                start=None if start is None else float(start), end=None if end is None else float(end),
                quality=quality,
            )
            self.dl_worker.submit(lambda: downloads.run(task, self.store))
            return self._json(asdict(task), 201)
        if m := re.fullmatch(r"/api/downloads/([\w-]+)/reveal", path):
            task = self.store.tasks.get(m[1])
            if not task or not task.file:
                return self._json({"error": "dosya yok"}, 404)
            downloads.reveal(task.file)
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


def serve(port: int = 8765) -> None:
    Handler.worker = Worker()
    Handler.dl_worker = Worker()
    Handler.store = downloads.Store()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"Clipfarm paneli: http://127.0.0.1:{port}  (durdurmak için Ctrl+C)")
    print(f"İndirilenler: {downloads.DOWNLOAD_DIR}")
    print(f"Claude: {'açık' if editor.available() else 'kapalı (ANTHROPIC_API_KEY yok)'} · "
          f"YouTube: {'bağlı' if youtube.is_connected() else 'bağlı değil'}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
