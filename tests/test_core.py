import re

from autokaraoke.align.naive import time_lines
from autokaraoke.export.ass import build_ass, karaoke_text
from autokaraoke.export.lrc import build_lrc
from autokaraoke.project import Project
from autokaraoke.sources.lrclib import parse_lrc
from autokaraoke.text.lyrics import lines_from_lrc
from autokaraoke.text.syllabify import syllabify_word

LRC = """[ar:Test]
[00:01.00]Nel blu dipinto di blu
[00:05.50][01:00.00]Felice di stare lassù
[00:09.00]
[00:12.25]Volare oh oh
"""


def test_parse_lrc_multiple_tags_and_gaps():
    entries = parse_lrc(LRC)
    assert [t for t, _ in entries] == [1.0, 5.5, 9.0, 12.25, 60.0]
    assert entries[2][1] == ""
    assert entries[4][1] == "Felice di stare lassù"


def test_parse_lrc_strips_word_tags():
    assert parse_lrc("[00:01.00]<00:01.00>Hello <00:01.50>world") == [(1.0, "Hello world")]


def test_syllabify_italian_and_english():
    assert syllabify_word("dipinto", "it") == ["di", "pin", "to"]
    assert "".join(syllabify_word("lassù,", "it")) == "lassù,"
    assert syllabify_word("wonderful", "en_US") == ["won", "der", "ful"]
    assert syllabify_word("dell'amore", "it")[0] == "dell'"
    assert syllabify_word("C'è", "it") == ["C'è"]
    assert syllabify_word("don't", "en_US") == ["don't"]
    assert syllabify_word("любовь", "ru") == ["лю", "бовь"]


def test_syllabify_cjk_and_unknown_language():
    assert syllabify_word("さくらきょう", "ja") == ["さ", "く", "ら", "きょ", "う"]
    assert syllabify_word("parola", "xx") == ["parola"]


def _project() -> Project:
    p = Project(title="Volare", artist="Modugno", duration=70.0)
    p.lines = lines_from_lrc(LRC, "it", p.duration)
    time_lines(p.lines)
    return p


def test_naive_timing_is_monotonic_and_within_line():
    for line in _project().lines:
        sylls = line.syllables()
        assert sylls[0].start == line.start
        assert sylls[-1].end <= line.end + 1e-6
        for a, b in zip(sylls, sylls[1:]):
            assert a.end <= b.start + 1e-9


def test_karaoke_durations_sum_to_line_span():
    line = _project().lines[0]
    show = line.start - 1.0
    text = karaoke_text(line, show)
    total = sum(int(x) for x in re.findall(r"\\kf?(\d+)", text))
    assert total == round(line.syllables()[-1].end * 100) - round(show * 100)
    assert re.sub(r"\{[^}]*\}", "", text) == line.text


def test_ass_has_one_event_per_line_and_title():
    ass = build_ass(_project())
    assert ass.count("Dialogue: 0,") == 4  # first line at 1s: no title card
    assert "Style: Karaoke" in ass


def test_countdown_before_first_line_after_pause():
    ass = build_ass(_project())  # only the reprise at 60 s follows a pause of 5+ s
    dots = [ln for ln in ass.splitlines() if ",Countdown," in ln and ln.startswith("Dialogue")]
    assert len(dots) == 3 and dots[0].endswith("● ● ●") and "0:00:57.00" in dots[0]


def test_project_roundtrip(tmp_path):
    p = _project()
    p.save(tmp_path)
    q = Project.load(tmp_path)
    assert q.to_dict() == p.to_dict()


def test_lrc_export():
    out = build_lrc(_project())
    assert "[00:01.00]<00:01.00>Nel" in out


def test_retext_line_keeps_timing_when_count_matches():
    from autokaraoke.text.edit import retext_line, to_notation

    line = _project().lines[0]
    before = [(s.start, s.end) for s in line.syllables()]
    notation = to_notation(line)
    assert notation == "Nel blu di|pin|to di blu"
    retext_line(line, "Nel blu di|pin|to di blu!", "it")
    assert [(s.start, s.end) for s in line.syllables()] == before
    assert line.text == "Nel blu dipinto di blu!"


def test_retext_line_redistributes_on_count_change():
    from autokaraoke.text.edit import retext_line

    line = _project().lines[0]
    start, end = line.syllables()[0].start, line.syllables()[-1].end
    retext_line(line, "Nel blu", "it")
    sy = line.syllables()
    assert len(sy) == 2 and sy[0].start == start and sy[-1].end <= end + 1e-9
    assert all(s.confidence == 0.2 for s in sy)
