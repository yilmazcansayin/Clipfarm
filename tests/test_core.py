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
