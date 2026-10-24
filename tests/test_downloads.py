from pathlib import Path

from clipfarm import downloads


def test_run_falls_back_to_captured_sources(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "STATE", tmp_path / "downloads.json")
    monkeypatch.setattr(downloads, "HOME", tmp_path)
    calls = []

    def fake_download(url, out_dir, start, end, **kw):
        calls.append((url, start, end, kw["max_height"], kw["audio_only"], kw["referer"]))
        if "page" in url:
            raise RuntimeError("Unsupported URL")
        return tmp_path / "clip.mp4"

    monkeypatch.setattr(downloads, "download", fake_download)
    store = downloads.Store()
    task = store.add(url="https://site/page", sources=["https://cdn/v.m3u8"], referer="https://site/page",
                     start=5.0, end=12.0, quality="720")
    downloads.run(task, store)
    assert task.status == "done" and task.file.endswith("clip.mp4")
    assert calls[1] == ("https://cdn/v.m3u8", 5.0, 12.0, 720, False, "https://site/page")
    assert downloads.Store().tasks[task.id].status == "done"  # diske yazıldı


def test_run_reports_all_failures(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "STATE", tmp_path / "downloads.json")
    monkeypatch.setattr(downloads, "HOME", tmp_path)
    monkeypatch.setattr(downloads, "download", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("403")))
    store = downloads.Store()
    task = store.add(url="https://a", sources=["https://b"], quality="audio")
    downloads.run(task, store)
    assert task.status == "failed" and "403" in task.error and "https://b" in task.error


def test_interrupted_tasks_marked_failed(tmp_path, monkeypatch):
    monkeypatch.setattr(downloads, "STATE", tmp_path / "downloads.json")
    monkeypatch.setattr(downloads, "HOME", tmp_path)
    store = downloads.Store()
    t = store.add(url="https://a")
    t.status = "downloading"
    store.save()
    assert downloads.Store().tasks[t.id].status == "failed"
