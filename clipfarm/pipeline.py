"""Uçtan uca iş akışı: yayın adresi → aday anlar → seçim → dikey Shorts → (isteğe bağlı) YouTube.

Her iş ~/.clipfarm/jobs/<id>/ altında job.json ve medya dosyalarıyla saklanır; panel ve
Chrome eklentisi bu kayıtları okur.
"""

from __future__ import annotations

import json
import re
import threading
import time
import traceback
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable

from . import editor, highlights
from .captions import Style, Word, build_ass, build_viral_ass, transcribe
from .effects import Effects, effects_filter, find_punches, plan_flashes
from .story import audio_db, plan_story, remap_words
from .render import Layout, probe, render, trim
from .youtube import HOME

JOBS_DIR = HOME / "jobs"
SHORT_CLIP_SECONDS = 180


@dataclass
class Options:
    count: int = 5
    layout: str = "blur"
    cam: list[float] | None = None
    cam_height: float = 0.38
    crop_x: float = 0.5
    model: str = "small"  # aday anları yazıya dökmek için (hızlı)
    caption_model: str = "medium"  # klibin altyazısı için (hızlı konuşmada daha doğru)
    lang: str | None = None
    ranges: list[list[float]] | None = None  # elle seçilen [başlangıç, bitiş] aralıkları
    upload: bool = False
    privacy: str = "private"
    effects: bool = False  # renk canlandırma + flaşlar (kullanıcı istemedi, varsayılan kapalı)
    story: bool = False  # kurguyla hikâyeleştir: başa kanca, ölü anları kes (kullanıcı kesintisiz istedi)
    intro: bool = True  # başta Gemini sesiyle klibi anlatan tek cümle, sonra klip
    accent: str = "FFE500"  # altyazı vurgu rengi; boş = her cümlede değişen palet
    look: str = "viral"  # viral: örnek Shorts tarzı ortada birikerek yazılan neon altyazı; classic: alt altyazı + hook kutusu
    aspect: str = "3:4"  # 3:4 (1080x1440) ya da 9:16 (1080x1920)
    watermark: str = ""  # alt ortada küçük kanal adı, örn. "@kanalim"


@dataclass
class Clip:
    id: str
    start: float
    end: float
    hook: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    virality: int = 0
    reason: str = ""
    language: str = ""
    words: list[dict] = field(default_factory=list)  # klibe göre göreli zamanlar
    file: str = ""
    status: str = "planned"  # planned, rendering, ready, uploading, uploaded, failed
    youtube_url: str = ""
    error: str = ""
    intro: str = ""  # başta seslendirilen özet cümle


@dataclass
class Job:
    id: str
    url: str
    options: Options
    created: float = field(default_factory=time.time)
    status: str = "queued"  # queued, analyzing, rendering, ready, failed
    stream: dict = field(default_factory=dict)
    candidates: list[dict] = field(default_factory=list)
    clips: list[Clip] = field(default_factory=list)
    log: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def dir(self) -> Path:
        return JOBS_DIR / self.id

    def save(self) -> None:
        self.dir.mkdir(parents=True, exist_ok=True)
        tmp = self.dir / "job.json.tmp"
        tmp.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(self.dir / "job.json")

    def say(self, msg: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.append(f"{stamp} {msg}")
        print(f"[{self.id}] {msg}", flush=True)
        self.save()

    def clip(self, clip_id: str) -> Clip:
        return next(c for c in self.clips if c.id == clip_id)

    @classmethod
    def load(cls, job_id: str) -> "Job":
        data = json.loads((JOBS_DIR / job_id / "job.json").read_text(encoding="utf-8"))
        data["options"] = Options(**data["options"])
        data["clips"] = [Clip(**c) for c in data["clips"]]
        return cls(**data)

    @classmethod
    def new(cls, url: str, options: Options) -> "Job":
        job = cls(id=time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:4], url=url, options=options)
        job.save()
        return job


def list_jobs() -> list[Job]:
    if not JOBS_DIR.exists():
        return []
    jobs = []
    for d in JOBS_DIR.iterdir():
        if (d / "job.json").exists():
            try:
                jobs.append(Job.load(d.name))
            except Exception:
                continue
    return sorted(jobs, key=lambda j: j.created, reverse=True)


def _is_url(text: str) -> bool:
    return bool(re.match(r"^https?://", text, re.I))


def short_channel(url: str) -> str:
    """'https://www.twitch.tv/kaicenat' → 'twitch.tv/kaicenat'."""
    return re.sub(r"^https?://(www\.|m\.)?", "", url or "").rstrip("/")


def stream_info(url: str) -> dict:
    if not _is_url(url):
        p = Path(url)
        return {"title": p.stem, "uploader": "", "channel_url": "", "platform": "local",
                "duration": probe(p)["duration"], "local": True}
    import yt_dlp

    with yt_dlp.YoutubeDL({"quiet": True, "noplaylist": True, "skip_download": True}) as ydl:
        info = ydl.extract_info(url, download=False)
    # uploader_url okunur adres verir (youtube.com/@IShowSpeed); channel_url ise UC… kimliği
    channel_url = info.get("uploader_url") or info.get("channel_url") or ""
    if not channel_url and info.get("extractor_key", "").lower().startswith("twitch") and info.get("uploader_id"):
        channel_url = f"https://www.twitch.tv/{info['uploader_id']}"
    if not channel_url and info.get("extractor_key", "").lower().startswith("kick") and info.get("channel"):
        channel_url = f"https://kick.com/{info['channel']}"
    return {
        "title": info.get("title") or "",
        "uploader": info.get("uploader") or info.get("channel") or "",
        "channel_url": channel_url,
        "platform": info.get("extractor_key", ""),
        "duration": float(info.get("duration") or 0),
        "is_live": bool(info.get("is_live")),
        "subtitles": sorted((info.get("subtitles") or {}).keys()),
    }


def _download_audio_and_chat(job: Job) -> tuple[Path, Path | None]:
    import yt_dlp

    out = job.dir / "analysis"
    out.mkdir(parents=True, exist_ok=True)
    opts = {
        "outtmpl": str(out / "stream.%(ext)s"),
        # HLS (m3u8) ses eksik parçalı gelebiliyor (ffmpeg boşlukları sessizlikle doldurur, zamanlar yine doğru
        # ama dosya süresi kısa görünür). Doğrudan https dosyası tercih edilir.
        "format": "bestaudio[protocol=https]/bestaudio[protocol=http]/bestaudio/worst",
        "noplaylist": True,
        "quiet": True,
        "writesubtitles": True,
        "subtitleslangs": ["live_chat", "rechat"],
        "ignoreerrors": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(job.url, download=True)
    audio = Path(info["requested_downloads"][0]["filepath"])
    chat = next((p for p in out.iterdir() if p.suffix == ".json"), None)
    return audio, chat


def _transcribe_candidates(job: Job, audio: Path, candidates: list[highlights.Candidate]) -> None:
    seg = job.dir / "analysis" / "segment.wav"
    seg.parent.mkdir(parents=True, exist_ok=True)
    for i, c in enumerate(candidates, 1):
        job.say(f"Aday {i}/{len(candidates)} yazıya dökülüyor ({c.start:.0f}-{c.end:.0f} sn)…")
        highlights.extract_audio_segment(audio, c.start, c.end, seg)
        words, _lang = transcribe(seg, language=job.options.lang, model_size=job.options.model)
        c.words = [{"start": round(w.start + c.start, 2), "end": round(w.end + c.start, 2), "text": w.text}
                   for w in words]
        c.transcript = " ".join(w.text for w in words)
        job.stream.setdefault("language", _lang)


def analyze(job: Job) -> list[editor.ClipPlan]:
    """Uzun yayında aday anları bulur ve en iyilerini seçer."""
    opts = job.options
    job.say("Ses ve sohbet indiriliyor (yalnızca analiz için, video değil)…")
    if job.stream.get("local"):
        audio, chat = Path(job.url), None
    else:
        audio, chat = _download_audio_and_chat(job)
    job.say("Ses yüksekliği analiz ediliyor…")
    loud = highlights.loudness_per_second(audio)
    chat_times = highlights.parse_chat(chat) if chat else []
    job.say(f"Sohbet: {len(chat_times)} mesaj bulundu." if chat_times else "Sohbet kaydı yok; yalnızca ses kullanılıyor.")
    score, az, cz = highlights.score_timeline(loud, chat_times)
    pool = max(opts.count * 3, opts.count + 2)
    candidates = highlights.pick_candidates(score, az, cz, pool, before=40, after=25)
    if not candidates:
        raise RuntimeError("Belirgin bir an bulunamadı.")
    _transcribe_candidates(job, audio, candidates)
    job.candidates = [c.to_dict() for c in candidates]
    job.say(f"{len(candidates)} aday bulundu; en iyileri seçiliyor…")
    return editor.plan_clips(job.stream, job.candidates, opts.count, log=job.say)


def make_short(
    src: Path,
    dst: Path,
    layout: Layout,
    words: list[Word] | None,
    language: str,
    duration: float,
    hook: str | None = None,
    credit: str | None = None,
    style: Style | None = None,
    has_audio: bool = True,
    fps: int = 30,
    crf: int = 20,
    effects: Effects | None = None,
    look: str = "viral",
    watermark: str | None = None,
    story: bool = False,
    accent: str | None = "FFE500",
    intro_audio: Path | None = None,
    intro_words: list[Word] | None = None,
) -> Path:
    """Kaynak klipten altyazılı dikey video üretir. Altyazı dosyası dst yanına yazılır.

    look="viral": örnek Shorts'lardaki gibi ortada harf harf yazılıp biriken neon altyazı,
    üstte harf harf yazılan hook; efektler (renk + vurgu flaşları) altyazının kelimelerine hizalanır.
    look="classic": altta kelime kelime vurgulanan altyazı, üstte hook kutusu ve kaynak yazısı.
    """
    ass_path = dst.with_suffix(".ass")
    segments = None
    if story:
        segments = plan_story(words or [], duration, audio_db(src) if has_audio else None)
        words = remap_words(words or [], segments)
        duration = sum(seg.length for seg in segments)
    intro_seconds = 0.0
    if intro_audio is not None and has_audio:
        intro_seconds = round(probe_audio_seconds(intro_audio) + 0.35, 3)  # anlatımdan sonra kısa nefes
        words = list(intro_words or []) + [
            Word(round(w.start + intro_seconds, 3), round(w.end + intro_seconds, 3), w.text) for w in (words or [])
        ]
        duration += intro_seconds
    if look == "viral":
        text, colors, starts = build_viral_ass(
            words or [], language, duration, layout.height, hook=hook, watermark=watermark or None,
            accent=accent or None, position=0.74 if layout.mode == "blur" else 0.52,
            break_at=intro_seconds or None,
        )
        if effects is not None and has_audio and not effects.flashes:
            effects.flashes = plan_flashes(find_punches(src), starts, colors)
    else:
        style = style or Style()
        if layout.mode == "split" and style.position == Style.position:
            style.position = min(0.9, layout.cam_height + 0.08)
        text = build_ass(words or [], language, duration, style, hook=hook, credit=credit)
    ass_path.write_text(text, encoding="utf-8")
    fx = effects_filter(effects) if effects is not None else None
    if effects is not None and segments:
        effects.flashes = []  # flaş zamanları kurgusuz videoya göre; kurguda kapalı
        fx = effects_filter(effects)
    return render(src, dst, layout, ass_path, fps=fps, crf=crf, has_audio=has_audio, effects=fx or None,
                  segments=segments, intro_audio=intro_audio if intro_seconds else None, intro_seconds=intro_seconds)


def render_clip(job: Job, clip: Clip) -> None:
    from .download import download

    clip.status, clip.error = "rendering", ""
    job.save()
    src_dir = job.dir / "sources"
    src_dir.mkdir(parents=True, exist_ok=True)
    if job.stream.get("local"):
        src = trim(Path(job.url), src_dir / f"{clip.id}.mp4", clip.start, clip.end)
    else:
        src = download(job.url, src_dir, clip.start, clip.end)
    info = probe(src)
    # Analizde çıkarılan kelimeler ses-akışının zamanına göre; indirilen video parçası
    # anahtar kareden başlayabildiği için kayıyordu. Altyazıyı her zaman bu dosyadan çıkarıyoruz.
    if info["has_audio"]:
        job.say(f"{clip.id}: altyazı çıkarılıyor…")
        words, lang = transcribe(src, language=job.options.lang, model_size=job.options.caption_model, vad=False)
        clip.words = [asdict(w) for w in words]
        clip.language = lang
    o = job.options
    intro_audio, intro_words = None, None
    if o.intro and info["has_audio"]:
        intro_audio, intro_words = _narrate_intro(job, clip, src_dir)
    layout = Layout(
        mode=o.layout, crop_x=o.crop_x, cam=tuple(o.cam) if o.cam else None, cam_height=o.cam_height,
        height=1440 if o.aspect == "3:4" else 1920,
    )
    dst = job.dir / f"{clip.id}.mp4"
    make_short(
        src, dst, layout,
        [Word(**w) for w in clip.words], clip.language or job.stream.get("language", ""),
        info["duration"], hook=clip.hook or None,
        credit=short_channel(job.stream.get("channel_url", "")) or None,
        has_audio=info["has_audio"],
        effects=Effects() if o.effects else None,
        look=o.look, watermark=o.watermark, story=o.story, accent=o.accent,
        intro_audio=intro_audio, intro_words=intro_words,
    )
    clip.file = dst.name
    clip.status = "ready"
    job.save()


def probe_audio_seconds(path: Path) -> float:
    import subprocess

    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    return float(out or 0)


def _narrate_intro(job: Job, clip: Clip, work: Path) -> tuple[Path | None, list[Word] | None]:
    """Gemini ile özet cümleyi (yoksa) yazdırır, seslendirir ve kelime zamanlarını çıkarır."""
    from . import gemini

    if not gemini.available():
        job.say(f"{clip.id}: Gemini anahtarı yok (GEMINI_API_KEY); giriş anlatımı atlandı.")
        return None, None
    try:
        if not clip.intro:
            transcript = " ".join(w["text"] for w in clip.words)
            clip.intro = gemini.summarize(transcript, job.stream.get("uploader", ""), job.stream.get("title", ""))
        job.say(f"{clip.id}: giriş anlatımı: {clip.intro}")
        wav = gemini.speak(clip.intro, work / f"{clip.id}_intro.wav")
        words, _ = transcribe(wav, language="en", model_size=job.options.caption_model)
        return wav, words
    except Exception as exc:  # anlatım olmadan da klip üretilsin
        job.say(f"{clip.id}: giriş anlatımı yapılamadı: {exc}")
        return None, None


def upload_clip(job: Job, clip: Clip, privacy: str | None = None, publish_at: str | None = None) -> None:
    from . import youtube

    clip.status, clip.error = "uploading", ""
    job.save()
    try:
        clip.youtube_url = youtube.upload(
            job.dir / clip.file, clip.title, clip.description, clip.tags,
            privacy=privacy or job.options.privacy, publish_at=publish_at,
        )
        clip.status = "uploaded"
        job.say(f"{clip.id} yüklendi: {clip.youtube_url}")
    except Exception as exc:
        clip.status, clip.error = "failed", f"Yükleme hatası: {exc}"
        job.say(clip.error)
    job.save()


def _clip_from_plan(i: int, plan: editor.ClipPlan, candidate: dict | None) -> Clip:
    words = []
    if candidate:
        words = [
            {"start": round(w["start"] - plan.start, 2), "end": round(w["end"] - plan.start, 2), "text": w["text"]}
            for w in candidate.get("words", [])
            if w["start"] >= plan.start - 0.05 and w["end"] <= plan.end + 0.3
        ]
    return Clip(
        id=f"clip{i + 1}", start=round(plan.start, 2), end=round(plan.end, 2), hook=plan.hook,
        title=plan.title, description=plan.description, tags=plan.tags, virality=plan.virality,
        reason=plan.reason, words=words,
    )


def run_job(job: Job) -> Job:
    try:
        job.status = "analyzing"
        job.say(f"Bilgi alınıyor: {job.url}")
        job.stream = stream_info(job.url)
        if job.stream.get("is_live"):
            raise RuntimeError("Bu bir canlı yayın. Yayın bitince VOD (kayıt) adresiyle tekrar deneyin.")
        o = job.options
        who = job.stream.get("uploader") or "Streamer"
        credit_line = f"Credit: {job.stream.get('channel_url', '')}".strip()
        if o.ranges:
            plans = [
                editor.ClipPlan(i, float(s), float(e), title=f"{who} moment #shorts",
                                description=f"{job.stream.get('title', '')}\n{credit_line}", reason="elle seçildi")
                for i, (s, e) in enumerate(o.ranges)
            ]
        elif job.stream.get("duration", 0) and job.stream["duration"] <= SHORT_CLIP_SECONDS:
            plans = [editor.ClipPlan(0, 0.0, job.stream["duration"], title=f"{who}: {job.stream.get('title', '')} #shorts"[:100],
                                     description=credit_line, reason="kısa klip")]
        else:
            plans = analyze(job)
        candidates = job.candidates if not o.ranges else []
        job.clips = [
            _clip_from_plan(i, p, candidates[p.candidate_index] if p.candidate_index < len(candidates) else None)
            for i, p in enumerate(plans)
        ]
        job.status = "rendering"
        job.say(f"{len(job.clips)} klip üretiliyor…")
        for clip in job.clips:
            try:
                render_clip(job, clip)
                job.say(f"{clip.id} hazır ({clip.end - clip.start:.0f} sn).")
            except Exception as exc:
                clip.status, clip.error = "failed", str(exc)
                job.say(f"{clip.id} üretilemedi: {exc}")
        if o.upload:
            for clip in job.clips:
                if clip.status == "ready":
                    upload_clip(job, clip)
        ok = any(c.status in ("ready", "uploaded") for c in job.clips)
        job.status = "ready" if ok else "failed"
        if not ok:
            job.error = "Hiçbir klip üretilemedi; ayrıntılar yukarıdaki kayıtta."
        job.say("Bitti.")
    except Exception as exc:
        job.status, job.error = "failed", str(exc)
        job.say(f"Hata: {exc}")
        traceback.print_exc()
    job.save()
    return job


class Worker:
    """İşleri sırayla (aynı anda bir tane) arka planda çalıştırır."""

    def __init__(self) -> None:
        self._queue: list[Callable[[], None]] = []
        self._cv = threading.Condition()
        threading.Thread(target=self._loop, daemon=True).start()

    def submit(self, fn: Callable[[], None]) -> None:
        with self._cv:
            self._queue.append(fn)
            self._cv.notify()

    def _loop(self) -> None:
        while True:
            with self._cv:
                while not self._queue:
                    self._cv.wait()
                fn = self._queue.pop(0)
            try:
                fn()
            except Exception:
                traceback.print_exc()
