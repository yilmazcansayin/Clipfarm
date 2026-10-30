"""Chrome eklentisinden gelen video indirme istekleri (tüm video veya aralık).

Her indirme ayrı bir süreçte (clipfarm.dlchild) çalışır; aynı anda birkaç indirme yapılabilir.
Bir bekçi, uzun süre hiç ilerlemeyen indirmeyi durdurup yeniden dener, böylece hiçbir indirme
sonsuza kadar "takılı" kalmaz. Yarım dosyalar görev klasöründe tutulur; tekrar denemede kaldığı yerden devam eder.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from pathlib import Path

from . import drive, settings
from .winproc import console_python, kill_tree, popen_kwargs
from .youtube import HOME

DOWNLOAD_DIR = Path(os.environ.get("CLIPFARM_DOWNLOADS", Path.home() / "Downloads" / "Clipfarm"))
STATE = HOME / "downloads.json"
PARTS_DIR = HOME / "parts"
LOG_DIR = HOME / "logs"
QUALITIES = {"best": None, "1080": 1080, "720": 720, "480": 480, "audio": None}
ACTIVE = ("queued", "downloading")
# Bekçi süreleri (sn): bu süre boyunca ne ilerleme ne de dosya büyümesi olursa indirme takılmış sayılır.
STALL = {"extracting": 120, "downloading": 180, "processing": 1800}
MIN_FREE = 500 * 1024 * 1024
RESUME_WITHIN = 24 * 3600


class Canceled(Exception):
    pass


class Stalled(Exception):
    pass


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
    status: str = "queued"  # queued, downloading, done, failed, canceled
    progress: float = 0.0
    file: str = ""
    error: str = ""
    phase: str = ""  # extracting, downloading, processing, drive
    downloaded: int = 0
    total: int = 0
    speed: float = 0.0
    eta: int = 0
    drive: bool = False  # bitince Google Drive'a da kaydet
    drive_status: str = ""  # uploading, done, failed
    drive_progress: float = 0.0
    drive_link: str = ""
    drive_error: str = ""
    local_deleted: bool = False

    def key(self) -> tuple:
        return (self.url, self.start, self.end, self.quality)


class Store:
    """İndirme kayıtlarını bellekte tutar, her değişiklikte diske yazar."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.tasks: dict[str, Task] = {}
        self.interrupted: list[Task] = []  # uygulama kapanınca yarıda kalanlar (yeniden başlatılır)
        if STATE.exists():
            try:
                rows = json.loads(STATE.read_text(encoding="utf-8"))
            except Exception:
                rows = []
            known = set(Task.__dataclass_fields__)
            seen = set()
            for d in sorted(rows, key=lambda d: d.get("created", 0)):
                t = Task(**{k: v for k, v in d.items() if k in known})
                if t.status in ACTIVE:
                    if t.key() in seen or time.time() - t.created > RESUME_WITHIN:
                        t.status, t.error = "failed", "Uygulama kapandığı için yarıda kaldı."
                    else:
                        t.status, t.phase = "queued", ""
                        self.interrupted.append(t)
                        seen.add(t.key())
                self.tasks[t.id] = t

    def save(self) -> None:
        with self._lock:
            data = [asdict(t) for t in sorted(self.tasks.values(), key=lambda t: t.created, reverse=True)[:200]]
        HOME.mkdir(parents=True, exist_ok=True)
        tmp = STATE.with_suffix(f".{threading.get_ident()}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        for _ in range(5):  # Windows'ta dosya o an okunuyorsa replace kısa süre başarısız olabiliyor
            try:
                tmp.replace(STATE)
                return
            except PermissionError:
                time.sleep(0.2)
        tmp.unlink(missing_ok=True)

    def add(self, **kwargs) -> Task:
        task = Task(id=time.strftime("%H%M%S-") + uuid.uuid4().hex[:6], **kwargs)
        with self._lock:
            self.tasks[task.id] = task
        self.save()
        return task

    def find_active(self, url: str, start, end, quality: str) -> Task | None:
        """Aynı video zaten sıradaysa/iniyorsa onu döner (düğmeye birkaç kez basılınca kopya açılmasın)."""
        with self._lock:
            for t in self.tasks.values():
                if t.status in ACTIVE and t.key() == (url, start, end, quality):
                    return t
        return None

    def list(self) -> list[Task]:
        with self._lock:
            return sorted(self.tasks.values(), key=lambda t: t.created, reverse=True)


def _safe_title(title: str) -> str:
    keep = "".join(c if c.isalnum() or c in " -_.()[]" else " " for c in title)
    return " ".join(keep.split())[:80]


def write_cookie_file(cookies: list[dict], path: Path) -> Path:
    """chrome.cookies.getAll() çıktısını yt-dlp'nin okuduğu Netscape biçimine çevirir."""
    lines = ["# Netscape HTTP Cookie File"]
    for c in cookies:
        name, value, domain = c.get("name"), c.get("value"), c.get("domain")
        if not name or value is None or not domain:
            continue
        sub = not c.get("hostOnly", False)
        if sub and not domain.startswith("."):
            domain = "." + domain
        if c.get("httpOnly"):
            domain = "#HttpOnly_" + domain
        expiry = int(c.get("expirationDate") or 0)
        lines.append("\t".join([
            domain, "TRUE" if sub else "FALSE", c.get("path") or "/",
            "TRUE" if c.get("secure") else "FALSE", str(expiry), name, str(value).replace("\n", ""),
        ]))
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _dir_size(path: Path) -> int:
    total = 0
    try:
        for entry in os.scandir(path):
            try:
                if entry.is_file():
                    total += entry.stat().st_size
                elif entry.is_dir():
                    total += _dir_size(Path(entry.path))
            except OSError:
                continue
    except OSError:
        pass
    return total


def fetch(url: str, task: Task, store: Store, cookiefile: Path | None, cancel: threading.Event) -> Path:
    """Tek bir adresi ayrı süreçte indirir; iptal edilirse ya da takılırsa süreci sonlandırır."""
    tmp = PARTS_DIR / task.id
    tmp.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    name = (_safe_title(task.title) + " [%(id)s]") if _safe_title(task.title) else "%(title).80B [%(id)s]"
    spec = {
        "url": url, "out_dir": str(DOWNLOAD_DIR), "temp_dir": str(tmp), "name": name,
        "start": task.start, "end": task.end, "max_height": QUALITIES.get(task.quality),
        "audio_only": task.quality == "audio", "referer": task.referer or None,
        "cookiefile": str(cookiefile) if cookiefile else None,
    }
    log = (LOG_DIR / f"{task.id}.log").open("a", encoding="utf-8")
    log.write(f"\n=== {time.strftime('%Y-%m-%d %H:%M:%S')} {url}\n")
    log.flush()
    proc = subprocess.Popen(
        [console_python(), "-m", "clipfarm.dlchild"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=log,
        text=True, encoding="utf-8", errors="replace", env={**os.environ, "PYTHONUTF8": "1"}, **popen_kwargs(),
    )
    proc.stdin.write(json.dumps(spec))
    proc.stdin.close()
    events: queue.Queue = queue.Queue()

    def reader() -> None:
        for line in proc.stdout:
            try:
                events.put(json.loads(line))
            except ValueError:
                continue
        events.put(None)

    threading.Thread(target=reader, daemon=True).start()
    result = error = None
    last_change, last_size, last_save = time.time(), -1, 0.0
    try:
        while True:
            try:
                ev = events.get(timeout=1)
            except queue.Empty:
                ev = {}
            if cancel.is_set():
                raise Canceled()
            if ev is None:
                break
            now = time.time()
            if ev:
                last_change = now
                if "done" in ev:
                    result = ev["done"]
                elif "error" in ev:
                    error = ev["error"]
                else:
                    task.phase = ev.get("phase") or task.phase
                    if ev.get("phase") == "downloading":
                        task.downloaded = int(ev.get("downloaded") or 0)
                        task.total = int(ev.get("total") or 0)
                        task.speed = float(ev.get("speed") or 0)
                        task.eta = int(ev.get("eta") or 0)
                        if ev.get("progress") is not None:
                            task.progress = ev["progress"]
            size = _dir_size(tmp)
            if size != last_size:
                last_size, last_change = size, now
                if not task.total and task.phase in ("", "extracting", "downloading"):
                    task.downloaded = size  # aralık indirmelerinde yt-dlp ilerleme bildirmiyor
            if now - last_change > STALL.get(task.phase or "extracting", 180):
                raise Stalled(f"{int(now - last_change)} sn boyunca hiç ilerlemedi ({task.phase or 'başlangıç'}).")
            if now - last_save > 1:
                last_save = now
                store.save()
    finally:
        kill_tree(proc)
        log.close()
    if result and Path(result).exists():
        return Path(result)
    raise RuntimeError(error or f"İndirme süreci beklenmedik şekilde kapandı (kod {proc.returncode}).")


def _hint(message: str) -> str:
    low = message.lower()
    if "impersonat" in low:
        return message + "\n→ Popup'taki \"yt-dlp'yi güncelle\" düğmesine basın (tarayıcı taklidi paketi kurulur)."
    if "login" in low or "sign in" in low or "cookies" in low or "private" in low:
        return message + "\n→ Bu video giriş istiyor: sitede oturum açıkken tekrar deneyin (Ayarlar → çerezleri kullan açık olsun)."
    if "unsupported url" in low:
        return message + "\n→ Bu sayfa desteklenmiyor; videoyu oynatıp popup'taki \"yakalanan videolar\" listesinden indirin."
    return message


def run(task: Task, store: Store, cookies: list[dict] | None = None, cancel: threading.Event | None = None) -> None:
    cancel = cancel or threading.Event()
    task.status, task.progress, task.error, task.phase = "downloading", 0.0, "", "extracting"
    task.downloaded = task.total = task.eta = 0
    task.speed = 0.0
    store.save()
    tmp = PARTS_DIR / task.id
    try:
        DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(DOWNLOAD_DIR).free < MIN_FREE:
            raise RuntimeError(f"Diskte yer kalmadı ({DOWNLOAD_DIR}). Biraz yer açıp tekrar deneyin.")
        cookiefile = None
        if cookies:
            tmp.mkdir(parents=True, exist_ok=True)
            cookiefile = write_cookie_file(cookies, tmp / "cookies.txt")
        attempts = [task.url] + [s for s in task.sources if s != task.url]
        errors = []
        path = None
        for url in attempts:
            for attempt in range(2):  # takılırsa bir kez daha (yarım dosyadan devam ederek) dene
                try:
                    path = fetch(url, task, store, cookiefile, cancel)
                    break
                except Stalled as exc:
                    errors.append(f"{url[:80]}: {exc}")
                except Canceled:
                    raise
                except Exception as exc:
                    errors.append(f"{url[:80]}: {exc}")
                    break
            if path:
                break
        if not path:
            task.status = "failed"
            task.error = _hint(errors[0] if len(errors) == 1 else "Hiçbir kaynak indirilemedi.\n" + "\n".join(errors))
            return
        task.file, task.status, task.progress, task.phase = str(path), "done", 1.0, ""
        shutil.rmtree(tmp, ignore_errors=True)
        store.save()
        if task.drive:
            send_to_drive(task, store)
    except Canceled:
        task.status, task.phase, task.error = "canceled", "", ""
        shutil.rmtree(tmp, ignore_errors=True)
    except Exception as exc:
        task.status, task.error = "failed", str(exc)
    finally:
        if (tmp / "cookies.txt").exists():
            (tmp / "cookies.txt").unlink(missing_ok=True)  # çerezler diskte kalmasın
        store.save()


def send_to_drive(task: Task, store: Store) -> None:
    """İndirilen dosyayı Google Drive'a kaydeder (hata olursa indirme yine başarılı sayılır)."""
    task.drive, task.drive_status, task.drive_progress, task.drive_error = True, "uploading", 0.0, ""
    store.save()
    last = [0.0]

    def progress(p: float) -> None:
        task.drive_progress = round(p, 3)
        if time.time() - last[0] > 1:
            last[0] = time.time()
            store.save()

    try:
        result = drive.save(Path(task.file), settings.load(), progress)
        task.drive_status, task.drive_progress, task.drive_link = "done", 1.0, result.get("link", "")
        task.local_deleted = bool(result.get("deleted_local"))
    except Exception as exc:
        task.drive_status, task.drive_error = "failed", str(exc)[:400]
    store.save()


class Manager:
    """İndirmeleri sıraya alır ve ayarlardaki sayı kadarını aynı anda çalıştırır."""

    def __init__(self, store: Store) -> None:
        self.store = store
        self._cv = threading.Condition()
        self._queue: deque[str] = deque()
        self._running: dict[str, threading.Event] = {}
        self._cookies: dict[str, list[dict]] = {}  # yalnızca bellekte; diske yazılmaz
        threading.Thread(target=self._dispatch, daemon=True).start()
        for task in store.interrupted:
            self.submit(task)

    def submit(self, task: Task, cookies: list[dict] | None = None) -> None:
        with self._cv:
            if cookies:
                self._cookies[task.id] = cookies
            if task.id not in self._queue and task.id not in self._running:
                task.status, task.error = "queued", ""
                self._queue.append(task.id)
            self._cv.notify_all()
        self.store.save()

    def cancel(self, task_id: str) -> bool:
        with self._cv:
            if task_id in self._queue:
                self._queue.remove(task_id)
                task = self.store.tasks[task_id]
                task.status, task.phase = "canceled", ""
                self._cookies.pop(task_id, None)
                self.store.save()
                return True
            if task_id in self._running:
                self._running[task_id].set()
                return True
        return False

    def drive(self, task: Task) -> None:
        threading.Thread(target=send_to_drive, args=(task, self.store), daemon=True).start()

    def _dispatch(self) -> None:
        while True:
            with self._cv:
                while not self._queue or len(self._running) >= settings.load()["parallel"]:
                    self._cv.wait(timeout=5)
                task_id = self._queue.popleft()
                cancel = threading.Event()
                self._running[task_id] = cancel
                cookies = self._cookies.pop(task_id, None)
            threading.Thread(target=self._run, args=(task_id, cookies, cancel), daemon=True).start()

    def _run(self, task_id: str, cookies, cancel: threading.Event) -> None:
        try:
            run(self.store.tasks[task_id], self.store, cookies, cancel)
        finally:
            with self._cv:
                self._running.pop(task_id, None)
                self._cv.notify_all()


def reveal(path: str) -> None:
    """Dosyayı işletim sisteminin dosya yöneticisinde gösterir."""
    p = Path(path)
    if sys.platform.startswith("win"):
        subprocess.Popen(["explorer", "/select,", str(p)] if p.exists() else ["explorer", str(p.parent)])
    elif sys.platform == "darwin":
        subprocess.Popen(["open", "-R", str(p)])
    else:
        subprocess.Popen(["xdg-open", str(p.parent)])
