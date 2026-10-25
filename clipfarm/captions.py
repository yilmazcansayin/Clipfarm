"""Whisper ile otomatik altyazı ve Shorts tarzı ASS altyazı dosyası üretimi."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path

from .timeutil import ass_timestamp

WIDTH, HEIGHT = 1080, 1920


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Style:
    font: str = "Arial"
    size: int = 84
    color: str = "FFFFFF"  # RRGGBB
    highlight: str = "FFE500"  # konuşulan kelimenin rengi
    outline: int = 7
    position: float = 0.68  # altyazının dikey konumu (0 = üst, 1 = alt)
    uppercase: bool = True
    words_per_line: int = 3


_MODELS: dict[tuple[str, str], object] = {}


def load_model(model_size: str = "small", device: str = "auto"):
    """Whisper modelini bir kez yükleyip sonraki çağrılarda tekrar kullanır."""
    key = (model_size, device)
    if key not in _MODELS:
        from faster_whisper import WhisperModel

        _MODELS[key] = WhisperModel(model_size, device=device, compute_type="auto")
    return _MODELS[key]


def transcribe(
    media: Path,
    language: str | None = None,
    model_size: str = "small",
    device: str = "auto",
) -> tuple[list[Word], str]:
    """Kelime zaman damgalarıyla konuşmayı yazıya döker. (kelimeler, dil) döner."""
    model = load_model(model_size, device)
    segments, info = model.transcribe(
        str(media),
        language=language,
        word_timestamps=True,
        vad_filter=True,
    )
    words: list[Word] = []
    for seg in segments:
        for w in seg.words or []:
            text = w.word.strip()
            if text:
                words.append(Word(float(w.start), float(w.end), text))
    return words, info.language


def save_words(words: list[Word], path: Path, language: str) -> None:
    path.write_text(
        json.dumps(
            {"language": language, "words": [asdict(w) for w in words]},
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )


def load_words(path: Path) -> tuple[list[Word], str]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return [Word(**w) for w in data["words"]], data.get("language", "")


def chunk_words(
    words: list[Word], max_words: int = 3, max_chars: int = 18, max_gap: float = 0.6
) -> list[list[Word]]:
    """Kelimeleri ekranda birlikte görünecek kısa gruplara böler."""
    chunks: list[list[Word]] = []
    current: list[Word] = []
    for w in words:
        if current:
            chars = sum(len(x.text) + 1 for x in current) + len(w.text)
            gap = w.start - current[-1].end
            ends_sentence = current[-1].text[-1:] in ".!?…"
            if len(current) >= max_words or chars > max_chars or gap > max_gap or ends_sentence:
                chunks.append(current)
                current = []
        current.append(w)
    if current:
        chunks.append(current)
    return chunks


def upper(text: str, language: str) -> str:
    """Türkçe için doğru büyük harf (i → İ, ı → I)."""
    if language in ("tr", "az"):
        text = text.replace("i", "İ").replace("ı", "I")
    return text.upper()


def _ass_color(rrggbb: str, alpha: str = "00") -> str:
    rrggbb = rrggbb.lstrip("#")
    r, g, b = rrggbb[0:2], rrggbb[2:4], rrggbb[4:6]
    return f"&H{alpha}{b}{g}{r}".upper()


def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", "\\N")


def build_ass(
    words: list[Word],
    language: str,
    duration: float,
    style: Style | None = None,
    hook: str | None = None,
    hook_seconds: float | None = None,
    credit: str | None = None,
) -> str:
    """Kelime kelime vurgulanan altyazı + üst başlık (hook) + kaynak yazısı içeren ASS üretir."""
    style = style or Style()
    caption_margin = int(HEIGHT * (1 - style.position))
    primary = _ass_color(style.color)
    hl = _ass_color(style.highlight).replace("&H00", "&H", 1) + "&"
    lines = [
        "[Script Info]",
        "ScriptType: v4.00+",
        f"PlayResX: {WIDTH}",
        f"PlayResY: {HEIGHT}",
        "WrapStyle: 0",
        "ScaledBorderAndShadow: yes",
        "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Caption,{style.font},{style.size},{primary},{primary},&H00000000,&H80000000,"
        f"-1,0,0,0,100,100,0,0,1,{style.outline},2,2,60,60,{caption_margin},1",
        f"Style: Hook,{style.font},64,&H00000000,&H00000000,&H00FFFFFF,&H00FFFFFF,"
        "-1,0,0,0,100,100,0,0,3,18,0,8,80,80,170,1",
        f"Style: Credit,{style.font},34,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,"
        "0,0,0,0,100,100,0,0,1,3,0,8,60,60,290,1",
        "",
        "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def event(start: float, end: float, style_name: str, text: str, layer: int = 0) -> str:
        return (
            f"Dialogue: {layer},{ass_timestamp(start)},{ass_timestamp(end)},"
            f"{style_name},,0,0,0,,{text}"
        )

    if hook:
        hook_end = duration if not hook_seconds else min(duration, hook_seconds)
        # açılışta küçükten büyüyerek gelir
        pop_in = "{\\fad(150,0)\\fscx70\\fscy70\\t(0,220,\\fscx100\\fscy100)}"
        lines.append(event(0, hook_end, "Hook", pop_in + _escape(hook), layer=2))
    if credit:
        lines.append(event(0, duration, "Credit", _escape(credit), layer=1))

    def fmt(text: str) -> str:
        return _escape(upper(text, language) if style.uppercase else text)

    for chunk in chunk_words(words, max_words=style.words_per_line):
        for i, w in enumerate(chunk):
            start = w.start
            end = chunk[i + 1].start if i + 1 < len(chunk) else max(w.end, w.start + 0.25)
            parts = []
            for j, other in enumerate(chunk):
                token = fmt(other.text)
                if j == i:
                    # konuşulan kelime büyük başlayıp yerine oturur (pop)
                    token = f"{{\\c{hl}\\fscx116\\fscy116\\t(0,90,\\fscx108\\fscy108)}}{token}{{\\r}}"
                parts.append(token)
            lines.append(event(start, end, "Caption", " ".join(parts)))
    return "\n".join(lines) + "\n"


# --- örnek Shorts tarzı ("viral") altyazı -------------------------------------------------
# Ekranın ortasında kelimeler konuşuldukça harf harf yazılır ve iki satıra kadar birikir;
# satırlar dolunca ya da konuşma durunca temizlenir. Önemli kelimeler neon parıltılı renkle.

PALETTE = ["00E5FF", "39FF14", "FFE500", "FF2BD6", "FF3B30", "B14DFF"]  # camgöbeği, yeşil, sarı, pembe, kırmızı, mor
FONT = "Montserrat Black"
STOPWORDS = set(
    "a an the and or but so to of in on at for with from by is are was were be been am i you he she it we they "
    "me him her us them my your his its our their this that these those what who how why when where do did does "
    "not no yes yeah oh uh um like just bro gonna wanna got get go come can will would could should have has had "
    "there here up out if then than too very all".split()
)
_PROFANITY = ("fuck", "shit", "bitch", "nigga", "nigger", "pussy", "dick", "cunt", "ass", "asshole", "damn")


def censor(text: str) -> str:
    """Küfürlerin ortasını yıldızlar (Shorts reklam uygunluğu için): fucking → f*cking, ass → a*s."""
    import re

    def repl(m: "re.Match[str]") -> str:
        w = m.group(0)
        return w[0] + "*" * (len(w) - 2) + w[-1] if len(w) <= 4 else w[0] + "*" + w[2:]

    pattern = r"(?i)\b(" + "|".join(_PROFANITY) + r")(?=[a-z]*\b)"
    return re.sub(pattern, repl, text)


def group_words(words: list[Word], max_line_chars: int = 16, max_gap: float = 0.8) -> list[list[list[Word]]]:
    """Kelimeleri ekranda birlikte birikecek gruplara (en fazla 2 satır) böler."""
    groups: list[list[list[Word]]] = []
    lines: list[list[Word]] = [[]]
    for w in words:
        cur = lines[-1]
        prev = cur[-1] if cur else (lines[0][-1] if lines[0] else None)
        if prev is not None and (w.start - prev.end > max_gap or prev.text[-1:] in ".!?…"):
            groups.append(lines)
            lines = [[]]
            cur = lines[-1]
        if cur and sum(len(x.text) + 1 for x in cur) + len(w.text) > max_line_chars:
            if len(lines) == 2:
                groups.append(lines)
                lines = [[]]
            else:
                lines.append([])
            cur = lines[-1]
        cur.append(w)
    if lines[0]:
        groups.append(lines)
    return groups


def _is_strong(text: str) -> bool:
    core = "".join(c for c in text.lower() if c.isalnum() or c == "'").strip("'")
    return bool(core) and core not in STOPWORDS and len(core) >= 4


def build_viral_ass(
    words: list[Word],
    language: str,
    duration: float,
    height: int = HEIGHT,
    hook: str | None = None,
    watermark: str | None = None,
    font: str = FONT,
    size: int = 88,
    position: float = 0.52,
    typing_step: float = 0.03,
    accent: str | None = "FFE500",
) -> tuple[str, list[str], list[float]]:
    """ASS metni, grup renkleri ve kelime başlangıçlarını döner (flaşları kelimeye hizalamak için)."""
    W, H = WIDTH, height
    y = int(H * position)
    lines = [
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {W}", f"PlayResY: {H}", "WrapStyle: 2",
        "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, "
        "BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, "
        "BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: V,{font},{size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,2,0,1,3,0,5,40,40,0,1",
        f"Style: Mark,{font},28,&H40FFFFFF,&H40FFFFFF,&H80000000,&H00000000,0,0,0,0,100,100,1,0,1,2,0,2,40,40,{int(H * 0.06)},1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ]

    def ev(start: float, end: float, text: str, style: str = "V", layer: int = 1) -> None:
        if end - start >= 0.01:
            lines.append(f"Dialogue: {layer},{ass_timestamp(start)},{ass_timestamp(end)},{style},,0,0,0,,{text}")

    def fmt(text: str) -> str:
        return _escape(upper(censor(text), language))

    def colored(text: str, color: str | None) -> str:
        """Yazı katmanı: dolgu renkli, ince siyah kenar."""
        fill = (_ass_color(color).replace("&H00", "&H", 1) + "&") if color else "&HFFFFFF&"
        return f"{{\\1c{fill}\\3c&H000000&}}{text}"

    def glowed(text: str, color: str | None) -> str:
        """Parıltı katmanı: dolgu görünmez, kalın bulanık kenar kelimenin rengi (beyaz kelimede koyu gölge)."""
        if not color:
            return f"{{\\3c&H000000&\\3a&H60&}}{text}"
        c = _ass_color(color).replace("&H00", "&H", 1) + "&"
        return f"{{\\3c{c}\\3a&H58&}}{text}"

    def both(pos: str, rows: list[list[tuple[str, str | None]]], start: float, end: float, layer: int) -> None:
        main = "\\N".join(" ".join(colored(t, c) for t, c in r) for r in rows if r)
        glow = "\\N".join(" ".join(glowed(t, c) for t, c in r) for r in rows if r)
        ev(start, end, f"{{{pos}\\1a&HFF&\\bord5\\blur7}}" + glow, layer=layer)
        ev(start, end, f"{{{pos}\\bord3\\blur0.8}}" + main, layer=layer + 1)

    if hook:
        hook_text = fmt(hook)
        hook_accent = accent or PALETTE[0]
        head, _, last = hook_text.rpartition(" ")
        hy = int(H * 0.11)
        for i in range(1, len(hook_text) + 1):  # harf harf yazılır, sonra sabit kalır
            if head and i > len(head) + 1:
                row = [(head, None), (hook_text[len(head) + 1:i], hook_accent)]
            else:
                row = [(hook_text[:i], None)]
            t0 = (i - 1) * 0.035
            t1 = duration if i == len(hook_text) else i * 0.035
            both(f"\\an8\\q0\\pos({W // 2},{hy})\\fs{int(size * 0.75)}", [row], t0, t1, layer=3)

    if watermark:
        ev(0, duration, _escape(watermark), style="Mark", layer=0)

    colors: list[str] = []
    starts: list[float] = []
    groups = group_words(words)
    for gi, group in enumerate(groups):
        color = accent or PALETTE[gi % len(PALETTE)]  # tek renk (varsayılan) ya da her cümlede değişen palet
        colors.append(color)
        flat = [(li, w) for li, line in enumerate(group) for w in line]
        last_end = flat[-1][1].end + 0.5
        next_start = groups[gi + 1][0][0].start if gi + 1 < len(groups) else duration
        group_end = min(max(last_end, flat[-1][1].start + 0.4), next_start, duration)
        accents = {
            id(w) for li, w in flat
            if (li == 1 and _is_strong(w.text)) or (li == 0 and len(group) == 1 and _is_strong(w.text))
            or (li == 0 and len(w.text) >= 6 and _is_strong(w.text))
        }
        for k, (li, w) in enumerate(flat):
            starts.append(w.start)
            t_next = flat[k + 1][1].start if k + 1 < len(flat) else group_end
            token = fmt(w.text)
            steps = max(1, min(len(token), int((min(t_next, w.end) - w.start) / typing_step)))
            for s in range(1, steps + 1):
                cut = len(token) if s == steps else max(1, round(len(token) * s / steps))
                rows: list[list[tuple[str, str | None]]] = [[] for _ in group]
                for lj, other in flat[:k]:
                    rows[lj].append((fmt(other.text), color if id(other) in accents else None))
                rows[li].append((token[:cut], color if id(w) in accents else None))
                t0 = w.start + (s - 1) * typing_step
                t1 = t_next if s == steps else w.start + s * typing_step
                both(f"\\an8\\pos({W // 2},{y})", rows, t0, t1, layer=1)  # üstten hizalı: 2. satır gelince 1. satır zıplamaz
    return "\n".join(lines) + "\n", colors, starts
