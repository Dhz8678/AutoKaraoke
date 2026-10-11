import re

from autokaraoke.align.naive import time_lines
from autokaraoke.export.ass import ass_time, build_ass, karaoke_text
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


class FixedWidth:
    """Text measure stub: 40 px per character."""

    size = 80

    def width(self, text):
        return 40.0 * len(text)


def _events(ass, style):
    return [ln for ln in ass.splitlines() if ln.startswith("Dialogue") and f",{style}," in ln]


def test_ass_has_one_event_per_line_and_title():
    ass = build_ass(_project(), measure=FixedWidth())
    assert len(_events(ass, "Karaoke")) == 4
    assert not _events(ass, "Title")  # first line at 1 s: no title card
    assert "Style: Karaoke" in ass


def test_alternate_layout_waits_in_next_colour():
    p = _project()
    ass = build_ass(p, measure=FixedWidth())
    nxt = _events(ass, "Next")
    # Line 2 is shown while line 1 is sung, in the "next" colour until line 1 ends.
    assert any(f",{ass_time(p.lines[0].span()[1])},Next," in e for e in nxt)
    ys = [re.search(r"\\pos\(960,(\d+)\)", e).group(1) for e in _events(ass, "Karaoke")]
    assert ys[0] != ys[1] and ys[0] == ys[2]


def test_scroll_layout_current_on_top_next_below():
    from autokaraoke.export.ass import AssStyle

    p = _project()
    st = AssStyle(layout="scroll", ball=False)
    ass = build_ass(p, st, measure=FixedWidth())
    kar = _events(ass, "Karaoke")
    assert len(kar) == 4 and all(f"\\pos(960,{st.upper_y})" in e for e in kar)
    nxt = _events(ass, "Next")
    assert len(nxt) == 3 and all(f"\\pos(960,{st.lower_y})" in e for e in nxt)


def test_backing_line_style_and_no_ball():
    p = _project()
    p.lines[1].backing = True
    ass = build_ass(p, measure=FixedWidth())
    back = _events(ass, "Backing")
    assert len(back) == 1 and "(Fe" in back[0] and back[0].endswith("sù)")
    s1, e1 = p.lines[1].span()
    for ev in _events(ass, "Ball"):
        t0 = ev.split(",")[1]
        assert not (ass_time(s1) <= t0 < ass_time(e1))


def test_ball_events_follow_syllables():
    from autokaraoke.export.ass import ball_at, ball_keyframes

    kf = ball_keyframes([100, 200, 300], [1.0, 1.4, 3.0], [1.3, 1.9, 3.5], 40, 0, 0.0)
    assert [t for t, _, _ in kf] == sorted(t for t, _, _ in kf)
    for x, t in ((100, 1.0), (200, 1.4), (300, 3.0)):  # lands where each syllable starts
        bx, dy = ball_at(kf, t)
        assert abs(bx - x) < 1e-6 and abs(dy) < 1e-6
    assert ball_at(kf, 1.2)[1] < -20  # mid-hop, up in the air
    assert ball_at(kf, 2.0) == (200, 0.0)  # long gap: waits on the syllable
    ass = build_ass(_project(), measure=FixedWidth())
    balls = _events(ass, "Ball")
    assert balls and all("\\move(" in e for e in balls)


def test_long_line_is_shrunk_to_fit():
    p = _project()
    retext = __import__("autokaraoke.text.edit", fromlist=["retext_line"]).retext_line
    retext(p.lines[0], "Nel blu dipinto di blu " * 4, "it")
    ass = build_ass(p, measure=FixedWidth())
    assert "\\fscx" in _events(ass, "Karaoke")[0]


def test_style_colors_and_overrides(tmp_path):
    from autokaraoke.export.ass import AssStyle, ass_color, project_style, save_default_style

    assert ass_color("#FFC800") == "&H0000C8FF"
    st = AssStyle.from_dict({"size": "64", "ball": False, "layout": "nope", "unknown": 1})
    assert st.size == 64 and st.ball is False and st.layout == "alternate"
    save_default_style(AssStyle(font="DejaVu Sans", size=70))
    p = _project()
    p.render = {"size": 90}
    st = project_style(p)
    assert st.font == "DejaVu Sans" and st.size == 90


def test_countdown_before_first_line_after_pause():
    from autokaraoke.export.ass import AssStyle

    ass = build_ass(_project(), AssStyle(ball=False), measure=FixedWidth())  # only the reprise at 60 s follows a pause of 5+ s
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


def test_split_into_lines_keeps_timing_when_count_matches():
    from autokaraoke.text.edit import split_into_lines, to_notation

    line = _project().lines[0]
    before = [(s.start, s.end) for s in line.syllables()]
    lines = split_into_lines(line, "Nel blu\ndi|pin|to di blu", "it")
    assert [ln.text for ln in lines] == ["Nel blu", "dipinto di blu"]
    assert [(s.start, s.end) for ln in lines for s in ln.syllables()] == before
    assert lines[1].start == before[2][0] and lines[0].end == before[1][1]
    assert to_notation(lines[1]) == "di|pin|to di blu"


def test_split_into_lines_redistributes_on_count_change():
    from autokaraoke.text.edit import split_into_lines

    line = _project().lines[0]
    start, end = line.span()
    lines = split_into_lines(line, "Nel blu\ndipinto\nancora", "it")
    assert len(lines) == 3
    assert lines[0].start == start and lines[-1].end <= end + 1e-9
    spans = [ln.span() for ln in lines]
    assert all(a[1] <= b[0] + 1e-9 for a, b in zip(spans, spans[1:]))


def test_merge_lines_and_new_line():
    from autokaraoke.text.edit import merge_lines, new_line

    p = _project()
    a, b = p.lines[0], p.lines[1]
    merged = merge_lines(a, b)
    assert merged.text == f"{a.text} {b.text}"
    assert merged.span() == (a.span()[0], b.span()[1])

    gap = new_line("oh oh", "it", p.lines[2], p.lines[3])  # 12.25 -> reprise at 60 s
    sa, sb = gap.span()
    assert sa >= p.lines[2].span()[1] and sb <= p.lines[3].span()[0]
    assert new_line("oh", "it").span() is None  # no neighbours: untimed


def test_split_and_merge_syllable():
    from autokaraoke.project import Syllable, Word
    from autokaraoke.text.edit import merge_syllable_with_next, split_syllable

    w = Word("ciao", [Syllable("ciao", 1.0, 2.0)])
    split_syllable(w, 0, 2)
    assert [(s.text, s.start, s.end) for s in w.syllables] == [("ci", 1.0, 1.5), ("ao", 1.5, 2.0)]
    merge_syllable_with_next(w, 0)
    assert [(s.text, s.start, s.end) for s in w.syllables] == [("ciao", 1.0, 2.0)]


def test_backing_flag_roundtrip(tmp_path):
    p = _project()
    p.lines[1].backing = True
    p.render = {"layout": "scroll"}
    p.save(tmp_path)
    q = Project.load(tmp_path)
    assert q.lines[1].backing and not q.lines[0].backing and q.render == {"layout": "scroll"}


def test_line_span_ignores_untimed_syllables():
    line = _project().lines[0]
    sy = line.syllables()
    sy[-1].start = sy[-1].end = None
    sy[-2].end = None
    assert line.span() == (sy[0].start, sy[-3].end)


def test_backing_envelope():
    from autokaraoke.mix import FADE, backing_spans, envelope

    p = _project()
    p.lines[1].backing = True
    spans = backing_spans(p)
    assert spans == [p.lines[1].span()]  # exactly the sung syllables, no padding
    a, b = spans[0]
    sr = 1000
    env = envelope(spans, 70 * sr, sr)
    # Nothing leaks outside the backing line, e.g. the end of the previous line.
    assert env[: int(a * sr)].max() == 0.0 and env[int(b * sr) + 1 :].max() == 0.0
    assert 0 < env[int((a + FADE / 2) * sr)] < 1 and 0 < env[int((b - FADE / 2) * sr)] < 1
    assert env[int((a + FADE) * sr) + 1 : int((b - FADE) * sr) - 1].min() == 1.0


def test_adjacent_backing_lines_mix_as_one_span():
    from autokaraoke.mix import backing_spans

    p = _project()
    for ln in p.lines[:2]:
        ln.backing = True
    gap_end = p.lines[1].span()[0]
    p.lines[0].words[-1].syllables[-1].end = gap_end - 0.05
    assert backing_spans(p) == [(p.lines[0].span()[0], p.lines[1].span()[1])]


def test_ball_lands_on_syllable_start():
    from autokaraoke.export.ass import syllable_starts

    line = _project().lines[0]  # "Nel blu di|pin|to di blu", 40 px per character
    xs, left, scale = syllable_starts(line, FixedWidth())
    assert scale == 1.0 and xs[0] == left
    assert xs[1] - xs[0] == 40 * len("Nel ") and xs[3] - xs[2] == 40 * len("di")


def test_auto_split_off_keeps_words_whole(tmp_path):
    from autokaraoke.text.edit import parse_notation, resplit, retext_line

    assert parse_notation("(coro)", "it") == [["(co", "ro)"]]
    assert parse_notation("(coro)", "it", auto=False) == [["(coro)"]]
    assert resplit("dottore in sala\n(co|ro)", "it", auto=True) == "dot|to|re in sa|la\n(co|ro)"
    assert resplit("(co|ro)", "it", auto=False) == "(coro)"

    p = _project()
    line = p.lines[0]
    line.auto_split = False
    retext_line(line, "Nel blu dipinto", "it")
    assert [s.text for s in line.syllables()] == ["Nel", "blu", "dipinto"]
    p.save(tmp_path)
    assert Project.load(tmp_path).lines[0].auto_split is False


def test_backing_line_already_in_brackets_is_not_wrapped_again():
    from autokaraoke.export.ass import pieces
    from autokaraoke.text.edit import retext_line

    line = _project().lines[0]
    retext_line(line, "[Coro]", "it")
    assert "".join(t for t, _, _ in pieces(line, parens=True)) == "[Coro]"
