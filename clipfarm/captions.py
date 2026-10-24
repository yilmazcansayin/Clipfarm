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
        lines.append(event(0, hook_end, "Hook", _escape(hook), layer=2))
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
                    token = f"{{\\c{hl}\\fscx108\\fscy108}}{token}{{\\r}}"
                parts.append(token)
            lines.append(event(start, end, "Caption", " ".join(parts)))
    return "\n".join(lines) + "\n"
