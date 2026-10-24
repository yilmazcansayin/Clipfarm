"""Claude ile aday anları değerlendirme ve Shorts başlığı / hook / açıklama yazma.

ANTHROPIC_API_KEY yoksa basit kurallarla devam edilir (skora göre sıralama,
transkriptten başlık).
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field

MODEL = os.environ.get("CLIPFARM_MODEL", "claude-opus-5-5")

SYSTEM = """You are the editor of a YouTube Shorts channel that posts moments from famous \
streamers' livestreams (Twitch, Kick, YouTube). You receive candidate moments that were \
detected automatically from audio volume spikes and chat activity, each with a transcript \
and word timings.

For every candidate decide whether it is worth posting. Good Shorts have a clear, \
self-contained moment within the first 2 seconds: a funny reaction, a shocking statement, \
a fail, a hype moment, a heated argument, a wholesome interaction. Reject candidates that \
are just loud gameplay without context, music, ads, gambling or slot content, or anything \
that needs a long setup to understand.

For kept candidates:
- Choose tight start/end times (absolute seconds, inside the candidate window) so the clip \
starts right before the setup and ends right after the payoff. Prefer 15-45 seconds, never \
over 60. Do not cut in the middle of a sentence.
- Write a hook: an on-screen text of at most 8 words that makes viewers stay. It adds \
context or a point of view, not a description of what is visible. No hashtags.
- Write a YouTube title under 90 characters that names the streamer, ends with #shorts.
- Write a 1-3 sentence description that gives context, then a line \
"Credit: <channel url>" using the channel given.
- 5-10 lowercase tags.
- Virality from 1 to 10, honest.

Our edits must add original value (context, commentary, story), because YouTube reduces \
reach for lightly edited re-uploads. Write in English unless the stream language makes \
that impossible."""

SCHEMA = {
    "type": "object",
    "properties": {
        "clips": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_index": {"type": "integer"},
                    "keep": {"type": "boolean"},
                    "start": {"type": "number"},
                    "end": {"type": "number"},
                    "virality": {"type": "integer"},
                    "hook": {"type": "string"},
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "reason": {"type": "string"},
                },
                "required": [
                    "candidate_index", "keep", "start", "end", "virality",
                    "hook", "title", "description", "tags", "reason",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clips"],
    "additionalProperties": False,
}


@dataclass
class ClipPlan:
    candidate_index: int
    start: float
    end: float
    virality: int = 0
    hook: str = ""
    title: str = ""
    description: str = ""
    tags: list[str] = field(default_factory=list)
    reason: str = ""


def available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_AUTH_TOKEN"))


def _candidate_payload(candidates: list[dict]) -> str:
    lines = []
    for i, c in enumerate(candidates):
        words = " ".join(f"[{w['start']:.1f}]{w['text']}" for w in c.get("words", []))
        lines.append(
            f"### Candidate {i}\nwindow: {c['start']:.1f}-{c['end']:.1f}s, peak {c['peak']:.1f}s, "
            f"audio_z {c.get('audio_z', 0)}, chat_z {c.get('chat_z', 0)}\n"
            f"transcript with word start times: {words or '(no speech detected)'}"
        )
    return "\n\n".join(lines)


def plan_with_claude(stream: dict, candidates: list[dict], count: int) -> list[ClipPlan]:
    import anthropic

    client = anthropic.Anthropic()
    user = (
        f"Stream: {stream.get('title', '')}\nStreamer: {stream.get('uploader', '')}\n"
        f"Channel: {stream.get('channel_url', '')}\nPlatform: {stream.get('platform', '')}\n\n"
        f"Pick at most {count} clips.\n\n{_candidate_payload(candidates)}"
    )
    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        system=SYSTEM,
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        messages=[{"role": "user", "content": user}],
    ) as stream_resp:
        response = stream_resp.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError("Claude bu isteği reddetti; kurallı seçime geçiliyor.")
    text = next(b.text for b in response.content if b.type == "text")
    data = json.loads(text)
    plans = []
    for item in data["clips"]:
        idx = item["candidate_index"]
        if not item["keep"] or not 0 <= idx < len(candidates):
            continue
        c = candidates[idx]
        start = max(c["start"], min(item["start"], c["end"] - 5))
        end = min(c["end"], max(item["end"], start + 5), start + 60)
        plans.append(
            ClipPlan(
                candidate_index=idx, start=start, end=end, virality=item["virality"],
                hook=item["hook"].strip(), title=item["title"].strip()[:100],
                description=item["description"].strip(), tags=item["tags"][:15],
                reason=item["reason"],
            )
        )
    plans.sort(key=lambda p: p.virality, reverse=True)
    return plans[:count]


def plan_with_rules(stream: dict, candidates: list[dict], count: int) -> list[ClipPlan]:
    ranked = sorted(range(len(candidates)), key=lambda i: candidates[i]["score"], reverse=True)
    who = stream.get("uploader") or "Streamer"
    plans = []
    for idx in ranked[:count]:
        c = candidates[idx]
        snippet = " ".join(c.get("transcript", "").split()[:9])
        title = f"{who}: \"{snippet}…\" #shorts" if snippet else f"{who} moment #shorts"
        plans.append(
            ClipPlan(
                candidate_index=idx, start=max(c["start"], c["peak"] - 25), end=min(c["end"], c["peak"] + 12),
                virality=0, hook="", title=title[:100],
                description=f"{stream.get('title', '')}\nCredit: {stream.get('channel_url', '')}",
                tags=[who.lower(), "stream highlights", "shorts"],
                reason="ses/sohbet skoru (Claude kapalı)",
            )
        )
    return plans


def plan_clips(stream: dict, candidates: list[dict], count: int, log=print) -> list[ClipPlan]:
    if available():
        try:
            return plan_with_claude(stream, candidates, count)
        except Exception as exc:  # ağ, kota, ret: kurallı yönteme düş
            log(f"Claude değerlendirmesi başarısız ({exc}); skora göre seçiliyor.")
    else:
        log("ANTHROPIC_API_KEY yok; anlar skora göre seçiliyor, başlıklar basit olacak.")
    return plan_with_rules(stream, candidates, count)
