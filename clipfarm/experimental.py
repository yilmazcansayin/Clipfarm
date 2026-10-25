"""Deneysel kurgu: her efekt klipteki belirli bir ana bağlı, elle yazılmış bir plana göre uygulanır.

Onaylı varsayılan stilden (Charon girişi, kesintisiz klip, efektsiz) ayrı bir seçenektir.
Plan bir JSON dosyasıdır; bütün zamanlar yayının (VOD) saniyesi cinsindendir:

{
  "id": "clip01", "start": 1200.0, "end": 1245.0,
  "top": "SPEED MEETS EKITIKE",          # isteğe bağlı üst başlık
  "timeline": [                          # isteğe bağlı; yoksa start-end tek parça
    {"from": 1238.0, "to": 1240.5, "label": "WAIT FOR IT..."},          # kanca (soğuk açılış)
    {"from": 1200.0, "to": 1240.5},
    {"freeze": 1.2, "at": 1240.5, "push": 1.25, "bw": true, "sfx": "scratch", "label": "..."},
    {"from": 1238.5, "to": 1240.5, "speed": 0.5, "replay": true, "push": 1.3, "sfx": "rewind"}
  ],
  "events": [
    {"t": 1221.3, "type": "punch", "scale": 1.22, "hold": 0.6, "cx": 0.5, "cy": 0.35, "sfx": "boom"},
    {"t": 1230.0, "type": "push", "to_t": 1236.0, "scale": 1.15},
    {"t": 1236.2, "type": "shake", "dur": 0.4, "amp": 18},
    {"t": 1236.2, "type": "flash", "dur": 0.08},
    {"t": 1236.2, "type": "bw", "dur": 0.6},
    {"t": 1236.2, "type": "text", "dur": 1.2, "text": "NO WAY", "pos": "top"},
    {"t": 1236.2, "type": "sfx", "name": "hit", "gain_db": -4}
  ]
}

Olay zamanları kancada değil asıl parçada (aynı kaynak saniyesini içeren son normal parça)
uygulanır. Dondurma ve tekrar parçalarının kendi efektleri parçanın içinde yazılır.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import sfx as sfxlib
from .captions import WIDTH, Word, _escape, build_viral_ass, censor, upper
from .render import FONTS_DIR, Layout, probe, video_filter
from .timeutil import ass_timestamp

FPS = 30
RATE = 48000


@dataclass
class Piece:
    src0: float  # klip dosyasındaki başlangıç (sn)
    src1: float
    speed: float = 1.0
    freeze: float = 0.0  # >0 ise src0 karesi bu kadar durur
    replay: bool = False
    teaser: bool = False
    raw: dict = field(default_factory=dict)
    out0: float = 0.0

    @property
    def length(self) -> float:
        return self.freeze if self.freeze else (self.src1 - self.src0) / self.speed

    @property
    def out1(self) -> float:
        return self.out0 + self.length


def _num(x: float) -> str:
    return f"{x:.3f}"


def build_pieces(plan: dict, clip_start: float) -> list[Piece]:
    steps = plan.get("timeline") or [{"from": plan["start"], "to": plan["end"]}]
    pieces: list[Piece] = []
    seen_main = False
    for st in steps:
        if "freeze" in st:
            at = float(st["at"]) - clip_start
            p = Piece(at, at, freeze=float(st["freeze"]), raw=st)
        else:
            a, b = float(st["from"]) - clip_start, float(st["to"]) - clip_start
            p = Piece(a, b, speed=float(st.get("speed", 1.0)), replay=bool(st.get("replay")), raw=st)
            # asıl parçadan önce gelen normal parça soğuk açılış (kanca) sayılır
            p.teaser = not p.replay and not seen_main and len(steps) > 1 and bool(st.get("label")) or bool(st.get("teaser"))
            if not p.replay and not p.teaser:
                seen_main = True
        pieces.append(p)
    t = 0.0
    for p in pieces:
        p.out0 = t
        t += p.length
    return pieces


def to_out(pieces: list[Piece], src_t: float) -> float | None:
    """Kaynak saniyesini çıktı saniyesine çevirir (son eşleşen normal parça)."""
    for p in reversed(pieces):
        if p.freeze or p.replay or p.teaser:
            continue
        if p.src0 - 1e-3 <= src_t <= p.src1 + 1e-3:
            return p.out0 + (src_t - p.src0) / p.speed
    for p in reversed(pieces):  # asılda yoksa kancaya düşer
        if p.teaser and p.src0 <= src_t <= p.src1:
            return p.out0 + (src_t - p.src0) / p.speed
    return None


def remap(words: list[Word], pieces: list[Piece]) -> list[Word]:
    out = []
    for p in pieces:
        if p.freeze or p.replay:
            continue
        for w in words:
            if p.src0 - 0.05 <= w.start < p.src1:
                a = p.out0 + (w.start - p.src0) / p.speed
                b = p.out0 + (min(w.end, p.src1) - p.src0) / p.speed
                out.append(Word(round(a, 3), round(max(b, a + 0.05), 3), w.text))
    return out


@dataclass
class Motion:
    """Çıktı zamanında zoom / sarsıntı zarfları (zoompan ifadesine derlenir)."""
    zooms: list[tuple[str, float, float, float, float, float, float]] = field(default_factory=list)
    shakes: list[tuple[float, float, float]] = field(default_factory=list)

    def punch(self, t: float, scale: float, hold: float, cx: float, cy: float) -> None:
        self.zooms.append(("punch", t, t + hold, scale, cx, cy, 0.0))

    def push(self, a: float, b: float, scale: float, cx: float, cy: float) -> None:
        self.zooms.append(("push", a, b, scale, cx, cy, 0.0))

    def shake(self, t: float, dur: float, amp: float) -> None:
        self.shakes.append((t, t + dur, amp))

    def expressions(self) -> tuple[str, str, str]:
        T = f"(on/{FPS})"
        zs, cxs, cys = [], [], []
        for kind, a, b, s, cx, cy, _ in self.zooms:
            if kind == "punch":  # 80 ms'de içeri, tut, 150 ms'de geri
                env = f"max(0,min(1,min(({T}-{_num(a)})/0.08,({_num(b + 0.15)}-{T})/0.15)))"
                zs.append(f"{_num(s - 1)}*{env}")
                span = (a, b + 0.15)
            else:  # yavaş itme: doğrusal içeri, sonunda kesmeyle geri
                env = f"if(between({T},{_num(a)},{_num(b)}),({T}-{_num(a)})/{_num(max(0.01, b - a))},0)"
                zs.append(f"{_num(s - 1)}*{env}")
                span = (a, b)
            cxs.append((span, cx))
            cys.append((span, cy))
        for a, b, amp in self.shakes:  # sarsıntıda kenar görünmesin diye hafif büyütme
            zs.append(f"0.07*between({T},{_num(a)},{_num(b)})")
        z = "1+" + "+".join(zs) if zs else "1"

        def pick(items: list, default: float) -> str:
            expr = f"{default}"
            for (a, b), v in reversed(items):
                expr = f"if(between({T},{_num(a)},{_num(b)}),{v},{expr})"
            return expr

        shx = "+".join(f"{amp}*sin({T}*71)*between({T},{_num(a)},{_num(b)})" for a, b, amp in self.shakes) or "0"
        shy = "+".join(f"{amp}*cos({T}*57)*between({T},{_num(a)},{_num(b)})" for a, b, amp in self.shakes) or "0"
        x = f"max(0,min(iw-iw/zoom,iw*{pick(cxs, 0.5)}-iw/zoom/2+({shx})))"
        y = f"max(0,min(ih-ih/zoom,ih*{pick(cys, 0.5)}-ih/zoom/2+({shy})))"
        return z, x, y


def _label_events(labels: list[tuple[float, float, str, str, str]], height: int, language: str) -> list[str]:
    """Başlık / etiket yazıları (ASS satırları)."""
    out = []
    for a, b, text, pos, color in labels:
        # bulanık düzende video ortada (~%29-%71); başlık en üstte, etiketler onun altında
        y = int(height * {"title": 0.075, "top": 0.19, "center": 0.42, "bottom": 0.88}.get(pos, 0.19))
        size = 64 if pos == "title" else 78
        c = color.lstrip("#")
        ass_c = f"&H{c[4:6]}{c[2:4]}{c[0:2]}&".upper()
        body = _escape(upper(censor(text), language))
        # büyüyerek çıkar (pop), kalın siyah kenar
        anim = "\\fscx70\\fscy70\\t(0,90,\\fscx108\\fscy108)\\t(90,160,\\fscx100\\fscy100)"
        out.append(
            f"Dialogue: 6,{ass_timestamp(a)},{ass_timestamp(b)},V,,0,0,0,,"
            f"{{\\an8\\pos({WIDTH // 2},{y})\\fs{size}\\bord6\\shad0\\1c{ass_c}\\3c&H000000&{anim}}}{body}"
        )
    return out


def render_plan(plan: dict, src: Path, clip_start: float, words: list[Word], language: str, dst: Path,
                height: int = 1440, accent: str = "FFE500", crf: int = 19) -> Path:
    """src: yayından kesilmiş klip dosyası (clip_start = bu dosyanın VOD'daki başlangıcı)."""
    info = probe(src)
    pieces = build_pieces(plan, clip_start)
    duration = pieces[-1].out1
    work = dst.parent / f"{dst.stem}_work"
    work.mkdir(parents=True, exist_ok=True)

    motion = Motion()
    bw: list[tuple[float, float]] = []
    flashes: list[tuple[float, float]] = []
    labels: list[tuple[float, float, str, str, str]] = []
    sounds: list[tuple[float, str, float]] = []  # (çıktı sn, ad, dB)

    for p in pieces:
        r = p.raw
        if r.get("push"):
            motion.push(p.out0, p.out1, float(r["push"]), float(r.get("cx", 0.5)), float(r.get("cy", 0.4)))
        if r.get("bw"):
            bw.append((p.out0, p.out1))
        if r.get("label"):
            labels.append((p.out0, p.out1, r["label"], r.get("pos", "top"), r.get("color", accent)))
        if r.get("sfx"):
            sounds.append((p.out0, r["sfx"], float(r.get("gain_db", -6))))

    for ev in plan.get("events", []):
        t = to_out(pieces, float(ev["t"]) - clip_start)
        if t is None:
            continue
        kind = ev["type"]
        if kind == "punch":
            motion.punch(t, float(ev.get("scale", 1.2)), float(ev.get("hold", 0.6)),
                         float(ev.get("cx", 0.5)), float(ev.get("cy", 0.4)))
        elif kind == "push":
            t1 = to_out(pieces, float(ev["to_t"]) - clip_start) or t + 3
            motion.push(t, t1, float(ev.get("scale", 1.15)), float(ev.get("cx", 0.5)), float(ev.get("cy", 0.4)))
        elif kind == "shake":
            motion.shake(t, float(ev.get("dur", 0.4)), float(ev.get("amp", 16)))
        elif kind == "bw":
            bw.append((t, t + float(ev.get("dur", 0.6))))
        elif kind == "flash":
            flashes.append((t, t + float(ev.get("dur", 0.08))))
        elif kind == "text":
            labels.append((t, t + float(ev.get("dur", 1.2)), ev["text"], ev.get("pos", "top"), ev.get("color", accent)))
        elif kind != "sfx":
            raise ValueError(f"Bilinmeyen olay: {kind}")
        if ev.get("sfx") or kind == "sfx":
            name = ev.get("name") if kind == "sfx" else ev["sfx"]
            sounds.append((t + float(ev.get("sfx_offset", 0)), name, float(ev.get("gain_db", -6))))

    # --- filtre grafiği -------------------------------------------------------------
    n = len(pieces)
    g = [f"[0:v]fps={FPS},setpts=PTS-STARTPTS,split={n}" + "".join(f"[v{i}]" for i in range(n))]
    has_audio = info["has_audio"]
    if has_audio:
        g.append(f"[0:a]aformat=sample_rates={RATE}:channel_layouts=stereo,asplit={n}" + "".join(f"[a{i}]" for i in range(n)))
    labels_v, labels_a = [], []
    for i, p in enumerate(pieces):
        if p.freeze:
            g.append(f"[v{i}]trim=start={_num(p.src0)}:duration={1 / FPS:.4f},setpts=PTS-STARTPTS,"
                     f"tpad=stop_mode=clone:stop_duration={_num(p.freeze)},trim=duration={_num(p.freeze)}[pv{i}]")
            if has_audio:
                g.append(f"[a{i}]atrim=0:0.001,asetpts=PTS-STARTPTS,apad=whole_dur={_num(p.freeze)},volume=0[pa{i}]")
        else:
            g.append(f"[v{i}]trim={_num(p.src0)}:{_num(p.src1)},setpts=(PTS-STARTPTS)/{p.speed},fps={FPS}[pv{i}]")
            if has_audio:
                if p.speed == 1.0:
                    af = ""
                elif p.raw.get("pitch", True):  # yavaş çekimde ses de kalınlaşır (dramatik tekrar)
                    af = f",asetrate={int(RATE * p.speed)},aresample={RATE}"
                else:
                    af = f",atempo={max(0.5, p.speed)}"
                vol = f",volume={float(p.raw.get('volume', 1.0))}" if "volume" in p.raw else ""
                g.append(f"[a{i}]atrim={_num(p.src0)}:{_num(p.src1)},asetpts=PTS-STARTPTS{af}{vol}[pa{i}]")
        labels_v.append(f"[pv{i}]")
        labels_a.append(f"[pa{i}]")
    if has_audio:
        g.append("".join(v + a for v, a in zip(labels_v, labels_a)) + f"concat=n={n}:v=1:a=1[cv][ca]")
    else:
        g.append("".join(labels_v) + f"concat=n={n}:v=1:a=0[cv]")

    W0, H0 = info["width"], info["height"]
    chain = []
    for a, b in bw:
        chain.append(f"hue=s=0:enable='between(t,{_num(a)},{_num(b)})'")
    if motion.zooms or motion.shakes:
        z, x, y = motion.expressions()
        chain.append(f"zoompan=z='{z}':x='{x}':y='{y}':d=1:s={W0}x{H0}:fps={FPS}")
    g.append("[cv]" + (",".join(chain) if chain else "null") + "[mv]")
    layout = video_filter(Layout(mode="blur", height=height)).replace("[0:v]", "[mv]", 1)
    g.append(layout[: -len("[v]")] + "[lv]")

    post = []
    for a, b in flashes:
        post.append(f"drawbox=x=0:y=0:w=iw:h=ih:color=white@0.85:t=fill:enable='between(t,{_num(a)},{_num(b)})'")

    # altyazı + etiketler
    ass_path = work / "subs.ass"
    text, _, _ = build_viral_ass(remap(words, pieces), language, duration, height, accent=accent, position=0.74)
    if plan.get("top"):
        labels.append((0.0, duration, plan["top"], "title", "FFFFFF"))
    text += "\n".join(_label_events(labels, height, language)) + "\n"
    ass_path.write_text(text, encoding="utf-8")
    fonts = work / "fonts"
    fonts.mkdir(exist_ok=True)
    for f in FONTS_DIR.glob("*.ttf"):
        if not (fonts / f.name).exists():
            shutil.copy(f, fonts / f.name)
    post.append(f"ass={ass_path.name}:fontsdir=fonts")
    post.append(f"fps={FPS},format=yuv420p")
    g.append("[lv]" + ",".join(post) + "[v]")

    inputs = ["-i", str(src.resolve())]
    if has_audio:
        mix = ["[ca]"]
        for k, (t, name, gain) in enumerate(sounds, start=1):
            wav = sfxlib.write(name, work)
            inputs += ["-i", str(wav.resolve())]
            ms = int(max(0, t) * 1000)
            g.append(f"[{k}:a]aformat=sample_rates={RATE}:channel_layouts=stereo,volume={gain}dB,adelay={ms}|{ms}[s{k}]")
            mix.append(f"[s{k}]")
        if len(mix) > 1:
            g.append("".join(mix) + f"amix=inputs={len(mix)}:normalize=0:duration=first,alimiter=limit=0.95[aout]")
        else:
            g.append("[ca]alimiter=limit=0.95[aout]")

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *inputs,
           "-filter_complex", ";".join(g), "-map", "[v]"]
    if has_audio:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "192k", "-ar", str(RATE)]
    cmd += ["-c:v", "libx264", "-preset", "medium", "-crf", str(crf), "-profile:v", "high",
            "-movflags", "+faststart", "-t", _num(duration), str(dst.resolve())]
    (work / "ffmpeg_cmd.json").write_text(json.dumps(cmd, ensure_ascii=False, indent=1), encoding="utf-8")
    dst.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(cmd, check=True, cwd=work)
    return dst


def run_plans(plans_file: Path, vod: Path, out_dir: Path, model: str = "small", lang: str | None = "en",
              only: list[str] | None = None) -> list[Path]:
    """Plan dosyasındaki her klibi yerel VOD'dan keser, yazıya döker ve deneysel kurguyla üretir."""
    from .captions import load_words, save_words, transcribe
    from .render import trim

    data = json.loads(plans_file.read_text(encoding="utf-8"))
    plans = data["clips"] if isinstance(data, dict) else data
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    for plan in plans:
        if only and plan["id"] not in only:
            continue
        times = [plan["start"], plan["end"]] + [
            float(x) for st in plan.get("timeline", []) for x in (st.get("from"), st.get("to"), st.get("at")) if x is not None
        ]
        a, b = max(0.0, min(times) - 0.5), max(times) + 0.5
        src = out_dir / "sources" / f"{plan['id']}_src.mp4"
        src.parent.mkdir(parents=True, exist_ok=True)
        if not src.exists():
            trim(vod, src, a, b)
        words_file = src.with_suffix(".words.json")
        if words_file.exists():
            words, language = load_words(words_file)
        else:
            words, language = transcribe(src, language=lang, model_size=model, vad=False)
            save_words(words, words_file, language)
        dst = out_dir / f"{plan['id']}.mp4"
        render_plan(plan, src, a, words, language or "en", dst)
        made.append(dst)
        print(f"Hazır: {dst}", flush=True)
    return made
