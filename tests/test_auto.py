import json

import numpy as np

from clipfarm import editor, highlights
from clipfarm.pipeline import _clip_from_plan, short_channel


def test_pick_candidates_finds_loud_bursts():
    rng = np.random.default_rng(0)
    loud = (-40 + rng.normal(0, 1, 1200)).astype(np.float32)
    for t in (300, 700, 1000):
        loud[t:t + 5] = -8
    score, az, cz = highlights.score_timeline(loud)
    cands = highlights.pick_candidates(score, az, cz, count=3)
    assert all(abs(c.peak - t) <= 6 for c, t in zip(cands, (300, 700, 1000)))


def test_chat_spike_adds_score():
    loud = np.full(1200, -30, dtype=np.float32)
    chat = [600 + i * 0.05 for i in range(200)] + list(range(0, 1200, 7))
    score, az, cz = highlights.score_timeline(loud, chat)
    cands = highlights.pick_candidates(score, az, cz, count=1)
    assert abs(cands[0].peak - 600) < 15


def test_parse_chat_formats(tmp_path):
    yt = tmp_path / "yt.json"
    yt.write_text("\n".join(json.dumps({"replayChatItemAction": {"videoOffsetTimeMsec": str(ms)}}) for ms in (1500, 2500)))
    tw = tmp_path / "tw.json"
    tw.write_text(json.dumps({"comments": [{"content_offset_seconds": 3.0}]}))
    assert highlights.parse_chat(yt) == [1.5, 2.5]
    assert highlights.parse_chat(tw) == [3.0]


def test_clip_words_are_shifted_and_filtered():
    plan = editor.ClipPlan(0, 100.0, 110.0)
    cand = {"words": [{"start": 99.0, "end": 99.5, "text": "önce"}, {"start": 101.0, "end": 101.4, "text": "hey"}]}
    clip = _clip_from_plan(0, plan, cand)
    assert clip.words == [{"start": 1.0, "end": 1.4, "text": "hey"}]
    assert clip.id == "clip1"


def test_short_channel():
    assert short_channel("https://www.twitch.tv/kaicenat/") == "twitch.tv/kaicenat"


def _sse(text: str) -> bytes:
    events = [
        ("message_start", {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant",
            "model": "claude-opus-5-5", "content": [], "stop_reason": None, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
            "usage": {"output_tokens": 5}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    return "".join(f"event: {e}\ndata: {json.dumps(d)}\n\n" for e, d in events).encode()


def test_plan_with_claude_request_and_parsing(monkeypatch):
    import anthropic
    import httpx2

    seen = {}
    answer = {"clips": [
        {"candidate_index": 1, "keep": True, "start": 205, "end": 230, "virality": 8, "hook": "He was NOT ready",
         "title": "Kai Cenat jumpscare #shorts", "description": "d", "tags": ["kai cenat"], "reason": "r"},
        {"candidate_index": 0, "keep": False, "start": 0, "end": 1, "virality": 1, "hook": "", "title": "",
         "description": "", "tags": [], "reason": "boring"},
    ]}

    def handler(request):
        seen["body"] = json.loads(request.content)
        seen["beta"] = request.headers.get("anthropic-beta")
        return httpx2.Response(200, headers={"content-type": "text/event-stream"}, content=_sse(json.dumps(answer)))

    real = anthropic.Anthropic
    monkeypatch.setattr(anthropic, "Anthropic", lambda: real(
        api_key="test", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler))))
    cands = [{"start": 0, "end": 60, "peak": 30, "words": []},
             {"start": 190, "end": 255, "peak": 210, "words": [{"start": 206.0, "end": 206.4, "text": "yo"}]}]
    plans = editor.plan_with_claude({"title": "t", "uploader": "Kai Cenat", "channel_url": "u"}, cands, 3)
    assert len(plans) == 1 and plans[0].candidate_index == 1
    assert (plans[0].start, plans[0].end) == (205, 230)
    body = seen["body"]
    assert body["model"] == editor.MODEL and body["fallbacks"] == "default" and body["stream"] is True
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert "server-side-fallback-2026-07-01" in seen["beta"]
    assert "[206.0]yo" in body["messages"][0]["content"]
