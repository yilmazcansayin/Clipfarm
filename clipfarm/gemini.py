"""Gemini ile klibin başına okunacak tek cümlelik özet: yazdırma + seslendirme (TTS).

Anahtar ortam değişkeninden okunur: GEMINI_API_KEY (ya da GOOGLE_API_KEY).
Modeller: CLIPFARM_GEMINI_TEXT_MODEL, CLIPFARM_TTS_MODEL, ses: CLIPFARM_TTS_VOICE.
"""

from __future__ import annotations

import base64
import json
import os
import subprocess
import urllib.request
from pathlib import Path

API = "https://generativelanguage.googleapis.com/v1beta/models"
TEXT_MODEL = os.environ.get("CLIPFARM_GEMINI_TEXT_MODEL", "gemini-2.5-flash")
# Ücretsiz kota model başına günde ~10 istek; 429 alınca sıradaki modele geçilir.
TTS_MODELS = [m.strip() for m in os.environ.get(
    "CLIPFARM_TTS_MODEL",
    "gemini-3.8-flash-tts,gemini-3.8-flash-lite-tts,gemini-3.1-flash-tts-preview,"
    "gemini-2.5-flash-preview-tts,gemini-2.5-pro-preview-tts",
).split(",") if m.strip()]
VOICE = os.environ.get("CLIPFARM_TTS_VOICE", "Charon")  # kullanıcının seçtiği: sakin, hikâye anlatıcısı
TTS_RATE = 24000  # Gemini TTS: 24 kHz, 16 bit, tek kanal PCM


def api_key() -> str | None:
    return os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")


def available() -> bool:
    return bool(api_key())


def _call(model: str, body: dict, timeout: int = 120) -> dict:
    key = api_key()
    if not key:
        raise RuntimeError("Gemini anahtarı yok (GEMINI_API_KEY).")
    req = urllib.request.Request(
        f"{API}/{model}:generateContent",
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def summarize(transcript: str, streamer: str = "", title: str = "") -> str:
    """Klipte ne olduğunu anlatan, merak uyandıran tek kısa İngilizce cümle."""
    prompt = (
        "You write the spoken intro line for a YouTube Short clipped from a livestream. "
        "In ONE short sentence (max 18 words), tell the viewer what is about to happen, "
        "building curiosity without spoiling the punchline. Plain spoken English, no emojis, no hashtags, "
        "no quotes. Refer to the streamer by name.\n\n"
        f"Streamer: {streamer or 'the streamer'}\nStream title: {title}\n"
        f"Clip transcript:\n{transcript[:4000]}"
    )
    data = _call(TEXT_MODEL, {"contents": [{"parts": [{"text": prompt}]}]})
    text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"]).strip()
    return text.strip('"').splitlines()[0].strip()


def _post(url: str, body: dict, timeout: int = 180) -> dict:
    key = api_key()
    if not key:
        raise RuntimeError("Gemini anahtarı yok (GEMINI_API_KEY).")
    req = urllib.request.Request(
        url, data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json", "x-goog-api-key": key},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _tts_interactions(model: str, text: str, voice: str, style: str) -> tuple[bytes, bool]:
    """Gemini 3.x ses modelleri: Interactions API. (ses baytları, WAV mı) döner."""
    data = _post(f"{API.rsplit('/models', 1)[0]}/interactions", {
        "model": model,
        "input": [{"type": "user_input", "content": [{
            "type": "text", "text": text,
            "annotations": [{"type": "speech_metadata", "style": style}],
        }]}],
        "response_format": {"type": "audio"},
        "generation_config": {"speech_config": [{"voice": voice}]},
    })
    for step in data.get("steps", []):
        if step.get("type") == "model_output":
            for c in step.get("content", []):
                if c.get("type") == "audio":
                    return base64.b64decode(c["data"]), True
    raise RuntimeError(f"{model}: yanıtta ses yok")


def _tts_generate(model: str, text: str, voice: str, style: str) -> tuple[bytes, bool]:
    """Gemini 2.5 ses modelleri: generateContent, ham 24 kHz PCM döner."""
    data = _call(model, {
        "contents": [{"parts": [{"text": f"{style}: {text}"}]}],
        "generationConfig": {
            "responseModalities": ["AUDIO"],
            "speechConfig": {"voiceConfig": {"prebuiltVoiceConfig": {"voiceName": voice}}},
        },
    })
    part = next(p for p in data["candidates"][0]["content"]["parts"] if "inlineData" in p)
    return base64.b64decode(part["inlineData"]["data"]), False


def speak(text: str, dst: Path, voice: str | None = None, style: str = "calm, engaging storyteller") -> Path:
    """Metni seslendirip 48 kHz stereo WAV olarak kaydeder. Kota dolunca sıradaki modeli dener."""
    import urllib.error

    voice = voice or VOICE
    errors = []
    for model in TTS_MODELS:
        fn = _tts_interactions if model.startswith("gemini-3") else _tts_generate
        try:
            audio, is_wav = fn(model, text, voice, style)
            break
        except urllib.error.HTTPError as exc:
            errors.append(f"{model}: HTTP {exc.code}")
            if exc.code not in (400, 403, 404, 429):  # desteklenmeyen model ya da kota dolu
                raise
    else:
        raise RuntimeError("Hiçbir Gemini ses modeli yanıt vermedi (" + ", ".join(errors) + ")")
    dst.parent.mkdir(parents=True, exist_ok=True)
    raw = dst.with_suffix(".wav.in" if is_wav else ".pcm")
    raw.write_bytes(audio)
    fmt = [] if is_wav else ["-f", "s16le", "-ar", str(TTS_RATE), "-ac", "1"]
    # baştaki/sondaki sessizliği kırp, 48 kHz stereo WAV'a çevir
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", *fmt, "-i", str(raw),
         "-af", "silenceremove=start_periods=1:start_threshold=-45dB,areverse,"
                "silenceremove=start_periods=1:start_threshold=-45dB,areverse",
         "-ar", "48000", "-ac", "2", str(dst)],
        check=True,
    )
    raw.unlink(missing_ok=True)
    return dst
