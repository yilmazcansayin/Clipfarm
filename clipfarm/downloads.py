"""Chrome eklentisinden gelen video indirme istekleri (tüm video veya aralık)."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path

from .download import download
from .youtube import HOME

DOWNLOAD_DIR = Path(os.environ.get("CLIPFARM_DOWNLOADS", Path.home() / "Downloads" / "Clipfarm"))
STATE = HOME / "downloads.json"
QUALITIES = {"best": None, "1080": 1080, "720": 720, "480": 480, "audio": None}


@dataclass
class Task:
    id: str
    url: str
    sources: list[str] = field(default_factory=list)  # sayfada yakalanan doğrudan medya adresleri
    referer: str = ""
    title: str = ""
    start: float | None = None
    end: float | None = None
    quality: str = "best"
    created: float = field(default_factory=time.time)
    status: str = "queued"  # queued, downloading, done, failed
    progress: float = 0.0
    file: str = ""
    error: str = ""


class Store:
    """İndirme kayıtlarını bellekte tutar, her değişiklikte diske yazar."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.tasks: dict[str, Task] = {}
        if STATE.exists():
            try:
                for d in json.loads(STATE.read_text(encoding="utf-8")):
                    t = Task(**d)
                    if t.status in ("queued", "downloading"):
                        t.status, t.error = "failed", "Sunucu kapandığı için yarıda kaldı."
                    self.tasks[t.id] = t
            except Exception:
                pass

    def save(self) -> None:
        with self._lock:
            data = [asdict(t) for t in sorted(self.tasks.values(), key=lambda t: t.created, reverse=True)[:200]]
        HOME.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE)

    def add(self, **kwargs) -> Task:
        task = Task(id=time.strftime("%H%M%S-") + uuid.uuid4().hex[:6], **kwargs)
        with self._lock:
            self.tasks[task.id] = task
        self.save()
        return task

    def list(self) -> list[Task]:
        with self._lock:
            return sorted(self.tasks.values(), key=lambda t: t.created, reverse=True)


def _safe_title(title: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_.()[]" else " " for c in title)
    return " ".join(keep.split())[:80]


def run(task: Task, store: Store) -> None:
    task.status, task.progress, task.error = "downloading", 0.0, ""
    store.save()
    last = [0.0]

    def progress(p: float) -> None:
        task.progress = round(p, 3)
        if time.time() - last[0] > 1:
            last[0] = time.time()
            store.save()

    name = (_safe_title(task.title) + " [%(id)s]") if task.title else "%(title).80B [%(id)s]"
    attempts = [task.url] + [s for s in task.sources if s != task.url]
    errors = []
    for url in attempts:
        try:
            path = download(
                url, DOWNLOAD_DIR, task.start, task.end,
                max_height=QUALITIES.get(task.quality), audio_only=task.quality == "audio",
                name=name, referer=task.referer or None, progress=progress,
            )
            task.file, task.status, task.progress = str(path), "done", 1.0
            break
        except Exception as exc:
            errors.append(f"{url[:80]}: {exc}")
    else:
        task.status = "failed"
        task.error = errors[0] if len(errors) == 1 else "Hiçbir kaynak indirilemedi.\n" + "\n".join(errors)
    store.save()


def reveal(path: str) -> None:
    """Dosyayı işletim sisteminin dosya yöneticisinde gösterir."""
    p = Path(path)
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", str(p)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])
