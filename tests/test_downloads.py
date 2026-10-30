import http.server
import json
import subprocess
import sys
import threading
import time
from functools import partial
from pathlib import Path

import pytest

from clipfarm import downloads, drive, settings


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "STATE", tmp_path / "downloads.json")
    monkeypatch.setattr(downloads, "HOME", tmp_path)
    monkeypatch.setattr(downloads, "PARTS_DIR", tmp_path / "parts")
    monkeypatch.setattr(downloads, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(downloads, "DOWNLOAD_DIR", tmp_path / "out")
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(settings, "HOME", tmp_path)
    return tmp_path


def test_run_falls_back_to_captured_sources(home, monkeypatch):
    calls = []

    def fake_fetch(url, task, store, cookiefile, cancel, **kw):
        calls.append((url, task.start, task.end, task.quality, task.referer))
        if "page" in url:
            raise RuntimeError("Unsupported URL")
        return home / "clip.mp4"

    monkeypatch.setattr(downloads, "fetch", fake_fetch)
    store = downloads.Store()
    task = store.add(url="https://site/page", sources=["https://cdn/v.m3u8"], referer="https://site/page",
                     start=5.0, end=12.0, quality="720")
    downloads.run(task, store)
    assert task.status == "done" and task.file.endswith("clip.mp4")
    assert calls[1] == ("https://cdn/v.m3u8", 5.0, 12.0, "720", "https://site/page")
    assert downloads.Store().tasks[task.id].status == "done"  # diske yazıldı


def test_run_reports_all_failures_with_hint(home, monkeypatch):
    monkeypatch.setattr(downloads, "fetch", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("Unsupported URL: x")))
    store = downloads.Store()
    task = store.add(url="https://a", sources=["https://b"], quality="audio")
    downloads.run(task, store)
    assert task.status == "failed" and "https://b" in task.error and "yakalanan videolar" in task.error


def test_stalled_download_is_retried_then_next_source(home, monkeypatch):
    calls = []

    def fake_fetch(url, *a, **kw):
        calls.append(url)
        raise downloads.Stalled("180 sn boyunca hiç ilerlemedi")

    monkeypatch.setattr(downloads, "fetch", fake_fetch)
    store = downloads.Store()
    task = store.add(url="https://a", sources=["https://b"])
    downloads.run(task, store)
    assert calls == ["https://a", "https://a", "https://b", "https://b"]
    assert task.status == "failed" and "ilerlemedi" in task.error


def test_interrupted_tasks_resume_once_and_duplicates_fail(home):
    store = downloads.Store()
    a = store.add(url="https://a")
    b = store.add(url="https://a")
    old = store.add(url="https://old")
    old.created -= 3 * 24 * 3600
    for t in (a, b, old):
        t.status = "downloading"
    store.save()
    again = downloads.Store()
    assert [t.id for t in again.interrupted] == [a.id]
    assert again.tasks[a.id].status == "queued"
    assert again.tasks[b.id].status == "canceled" and again.tasks[old.id].status == "failed"


def test_find_active_dedupes_repeat_clicks(home):
    store = downloads.Store()
    t = store.add(url="https://a", start=1.0, end=5.0, quality="best")
    assert store.find_active("https://a", 1.0, 5.0, "best") is t
    assert store.find_active("https://a", None, None, "best") is None
    t.status = "done"
    assert store.find_active("https://a", 1.0, 5.0, "best") is None


def test_cookie_file_is_netscape_format(tmp_path):
    path = downloads.write_cookie_file([
        {"domain": ".tiktok.com", "hostOnly": False, "path": "/", "secure": True, "httpOnly": True,
         "expirationDate": 1893456000.5, "name": "sessionid", "value": "abc"},
        {"domain": "www.example.com", "hostOnly": True, "path": "/", "secure": False, "name": "s", "value": "1"},
        {"domain": "x.com", "name": "", "value": "skip"},
    ], tmp_path / "c.txt")
    lines = path.read_text().splitlines()
    assert lines[0] == "# Netscape HTTP Cookie File"
    assert lines[1].split("\t") == ["#HttpOnly_.tiktok.com", "TRUE", "/", "TRUE", "1893456000", "sessionid", "abc"]
    assert lines[2].split("\t") == ["www.example.com", "FALSE", "/", "FALSE", "0", "s", "1"]
    assert len(lines) == 3
    import http.cookiejar
    jar = http.cookiejar.MozillaCookieJar(str(path))
    jar.load(ignore_discard=True, ignore_expires=True)
    assert {c.name for c in jar} == {"sessionid", "s"}


def test_cookies_are_removed_after_download(home, monkeypatch):
    seen = {}

    def fake_fetch(url, task, store, cookiefile, cancel, **kw):
        seen["file"] = cookiefile
        assert cookiefile.exists()
        return home / "v.mp4"

    monkeypatch.setattr(downloads, "fetch", fake_fetch)
    store = downloads.Store()
    task = store.add(url="https://a")
    downloads.run(task, store, cookies=[{"domain": ".a", "name": "n", "value": "GIZLIDEGER"}])
    assert task.status == "done" and not seen["file"].exists()
    assert "GIZLIDEGER" not in downloads.STATE.read_text()


def test_settings_validate_and_persist(home):
    assert settings.load()["parallel"] == 3
    data = settings.update({"parallel": 50, "drive_upload": 1, "unknown": "x"})
    assert data["parallel"] == 8 and data["drive_upload"] is True and "unknown" not in data
    assert settings.load()["drive_upload"] is True


def test_drive_copy_to_desktop_folder(tmp_path):
    src = tmp_path / "video.mp4"
    src.write_bytes(b"x" * 1000)
    root = tmp_path / "My Drive"
    root.mkdir()
    assert drive.desktop_folder(str(root)) == root
    seen = []
    result = drive.copy_to_folder(src, root, "Clipfarm", move=True, progress=seen.append)
    assert result["link"] == str(root / "Clipfarm" / "video.mp4")
    assert (root / "Clipfarm" / "video.mp4").read_bytes() == b"x" * 1000
    assert not src.exists() and seen[-1] == 1.0


def test_send_to_drive_failure_keeps_download_done(home, monkeypatch):
    monkeypatch.setattr(drive, "save", lambda *a: (_ for _ in ()).throw(RuntimeError("Drive API kapalı")))
    store = downloads.Store()
    task = store.add(url="https://a")
    task.status, task.file = "done", str(home / "v.mp4")
    downloads.send_to_drive(task, store)
    assert task.status == "done" and task.drive_status == "failed" and "kapalı" in task.drive_error


def test_manager_cancel_queued_and_parallel_limit(home, monkeypatch):
    settings.update({"parallel": 1})
    gate = threading.Event()
    started = []

    def fake_fetch(url, task, store, cookiefile, cancel, **kw):
        started.append(url)
        while not gate.is_set():
            if cancel.is_set():
                raise downloads.Canceled()
            time.sleep(0.02)
        return home / f"{url[-1]}.mp4"

    monkeypatch.setattr(downloads, "fetch", fake_fetch)
    store = downloads.Store()
    manager = downloads.Manager(store)
    a, b, c = (store.add(url=f"https://{x}") for x in "abc")
    for t in (a, b, c):
        manager.submit(t)
    time.sleep(0.3)
    assert started == ["https://a"]  # aynı anda yalnızca 1
    assert manager.cancel(b.id) and b.status == "canceled"
    assert manager.cancel(a.id)
    for _ in range(100):
        if a.status == "canceled" and started[-1] == "https://c":
            break
        time.sleep(0.05)
    assert a.status == "canceled" and started == ["https://a", "https://c"]
    gate.set()
    for _ in range(100):
        if c.status == "done":
            break
        time.sleep(0.05)
    assert c.status == "done"


class _Quiet(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass


@pytest.fixture
def media_server(tmp_path):
    root = tmp_path / "www"
    root.mkdir()
    subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "testsrc=size=320x240:rate=10", "-f", "lavfi",
                    "-i", "sine", "-t", "3", "-pix_fmt", "yuv420p", "-shortest", str(root / "video.mp4")], check=True)
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), partial(_Quiet, directory=str(root)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_port}"
    httpd.shutdown()


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"), reason="ffmpeg yok")
def test_real_download_in_child_process(home, media_server):
    store = downloads.Store()
    task = store.add(url=f"{media_server}/video.mp4", title="Deneme video", quality="720")
    downloads.run(task, store)
    assert task.status == "done", task.error
    assert Path(task.file).exists() and Path(task.file).parent == home / "out"
    assert Path(task.file).name.startswith("Deneme video")
    assert not (home / "parts" / task.id).exists()  # yarım dosya klasörü temizlendi
    assert (home / "logs" / f"{task.id}.log").exists()


def test_stalled_child_is_killed(home, monkeypatch):
    # Hiç çıktı vermeyen (takılmış) bir alt süreç: bekçi onu öldürüp Stalled fırlatmalı.
    monkeypatch.setattr(downloads, "STALL", {"extracting": 1, "downloading": 1, "processing": 1})
    monkeypatch.setattr(downloads, "console_python", lambda: sys.executable)
    real_popen = subprocess.Popen

    def fake_popen(cmd, **kw):
        return real_popen([sys.executable, "-c", "import time,sys; sys.stdin.read(); time.sleep(60)"], **kw)

    monkeypatch.setattr(downloads.subprocess, "Popen", fake_popen)
    store = downloads.Store()
    task = store.add(url="https://a")
    t0 = time.time()
    with pytest.raises(downloads.Stalled):
        downloads.fetch("https://a", task, store, None, threading.Event())
    assert time.time() - t0 < 15


def test_short_range_is_recut_from_full_video(home, monkeypatch):
    # yt-dlp aralığı sessizce kısa keserse (Pinterest HLS: 27 sn yerine 2 sn) tüm video indirilip kesilir.
    calls = []

    def fake_fetch(url, task, store, cookiefile, cancel, ranged=True, out_dir=None, meta=None):
        calls.append(ranged)
        if meta is not None:
            meta["duration"] = 28.0
        target = (out_dir or downloads.DOWNLOAD_DIR)
        target.mkdir(parents=True, exist_ok=True)
        f = target / ("clip_0-27.mp4" if ranged else "full.mp4")
        f.write_bytes(b"short" if ranged else b"full")
        return f

    durations = {"clip_0-27.mp4": 2.0}
    monkeypatch.setattr(downloads, "fetch", fake_fetch)
    monkeypatch.setattr(downloads, "_duration", lambda p: durations.get(Path(p).name, 27.0))
    import clipfarm.render as render
    monkeypatch.setattr(render, "trim", lambda src, dst, s, e: dst.write_bytes(b"cut " + src.read_bytes()) or dst)
    store = downloads.Store()
    task = store.add(url="https://pin", start=0.0, end=27.0)
    downloads.run(task, store)
    assert task.status == "done" and calls == [True, False]
    assert Path(task.file).read_bytes() == b"cut full"
    assert not (home / "parts" / task.id).exists()


def test_range_past_end_of_video_is_not_recut(home, monkeypatch):
    monkeypatch.setattr(downloads, "_duration", lambda p: 9.5)
    t = downloads.Task(id="x", url="u", start=50.0, end=80.0)
    assert not downloads._range_too_short(Path("a.mp4"), t, 60.0)  # video 60 sn: beklenen 10, gelen 9.5
    assert downloads._range_too_short(Path("a.mp4"), downloads.Task(id="y", url="u", start=0.0, end=27.0), 28.0)
