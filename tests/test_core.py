import pytest

from clipfarm.captions import Word, build_ass, chunk_words, upper
from clipfarm.render import Layout, video_filter
from clipfarm.timeutil import ass_timestamp, parse_time


@pytest.mark.parametrize(
    "text,expected",
    [("95", 95), ("1:30", 90), ("1:02:03", 3723), ("1h2m3s", 3723), ("90.5", 90.5), (None, None)],
)
def test_parse_time(text, expected):
    assert parse_time(text) == expected


def test_parse_time_invalid():
    with pytest.raises(ValueError):
        parse_time("abc")


def test_ass_timestamp():
    assert ass_timestamp(3723.456) == "1:02:03.46"


def test_turkish_upper():
    assert upper("istanbul ılık", "tr") == "İSTANBUL ILIK"
    assert upper("this", "en") == "THIS"


def test_chunking_breaks_on_count_gap_and_sentence():
    words = [
        Word(0.0, 0.2, "bir"), Word(0.2, 0.4, "iki"), Word(0.4, 0.6, "üç"),
        Word(0.6, 0.8, "dört."), Word(0.8, 1.0, "beş"), Word(3.0, 3.2, "altı"),
    ]
    chunks = chunk_words(words, max_words=3)
    assert [[w.text for w in c] for c in chunks] == [["bir", "iki", "üç"], ["dört."], ["beş"], ["altı"]]


def test_build_ass_has_hook_credit_and_highlight():
    ass = build_ass(
        [Word(0, 0.5, "selam"), Word(0.5, 1.0, "millet")], "tr", 5.0,
        hook="Bunu {beklemiyordu}", credit="kick.com/eray",
    )
    assert "Style: Hook" in ass
    assert "BUNU (BEKLEMIYORDU)" not in ass  # hook büyük harfe çevrilmez
    assert "Bunu (beklemiyordu)" in ass
    assert "kick.com/eray" in ass
    assert ass.count("Dialogue:") == 4
    assert "{\\c&H00E5FF&" in ass


@pytest.mark.parametrize("mode", ["blur", "crop", "split"])
def test_video_filter_ends_with_v(mode):
    graph = video_filter(Layout(mode=mode, cam=(0.7, 0, 0.3, 0.3)))
    assert graph.endswith("[v]")


def test_effects_filter_grade_and_flashes():
    from clipfarm.effects import Effects, Flash, effects_filter, plan_flashes

    flashes = plan_flashes([3.1, 9.0], word_starts=[1.0, 2.9, 9.6], colors=["00E5FF", "39FF14"])
    assert [(f.t, f.kind) for f in flashes] == [(2.9, "tint"), (9.0, "bw")]
    fx = effects_filter(Effects(flashes=flashes + [Flash(12.0, "white")]))
    assert "eq=saturation" in fx and "0x00E5FF@0.45" in fx and "between(t,2.9,3.02)" in fx
    assert "hue=s=0:enable='between(t,9,9.3)'" in fx and "eq=brightness=0.3" in fx
    assert effects_filter(Effects(grade=False)) == ""


def test_viral_captions_accumulate_and_censor():
    from clipfarm.captions import Word, build_viral_ass, censor, group_words

    assert censor("that fucking ass") == "that f**king a*s"
    words = [Word(i * 0.4, i * 0.4 + 0.3, t) for i, t in enumerate("you know this is the world's biggest".split())]
    groups = group_words(words)
    assert sum(len(line) for g in groups for line in g) == len(words)
    assert all(len(g) <= 2 for g in groups)
    text, colors, starts = build_viral_ass(words, "en", 4.0, 1440, hook="Speed wins")
    assert "PlayResY: 1440" in text and "Montserrat Black" in text
    assert len(colors) == len(groups) and starts == [w.start for w in words]
    full = [l for l in text.splitlines() if l.startswith("Dialogue: 2") and "WORLD'S" in l]
    assert full and "YOU" in full[-1] and "KNOW" in full[-1]  # önceki kelimeler ekranda kalıyor


def test_story_teaser_cuts_dead_air_and_remaps_words():
    import numpy as np

    from clipfarm.captions import Word
    from clipfarm.story import plan_story, remap_words

    # 0-5 sn konuşma, 5-8 sessiz, 8-14 konuşma, 14-16.5 bağırma
    db = np.full(180, -25.0, dtype=np.float32)
    db[50:80] = -70
    db[140:165] = -8
    words = ([Word(0.5 + i * 0.5, 0.9 + i * 0.5, f"a{i}") for i in range(9)]
             + [Word(8.2 + i * 0.5, 8.6 + i * 0.5, f"b{i}") for i in range(11)]
             + [Word(14.2, 14.8, "NO"), Word(15.0, 15.6, "WAY")])
    segs = plan_story(words, 18.0, db)
    assert segs[0].start >= 13.5 and segs[0].length <= 3.5  # kanca: bağırma başa
    assert all(not (s.start < 6.5 < s.end) for s in segs[1:])  # sessiz boşluk kesildi
    remapped = remap_words(words, segs)
    assert remapped[0].text == "NO" and remapped[0].start < 0.5
    assert [w.text for w in remapped[2:4]] == ["a0", "a1"]
    assert all(b.start >= a.start for a, b in zip(remapped, remapped[1:]))
