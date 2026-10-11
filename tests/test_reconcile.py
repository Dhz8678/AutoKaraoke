from autokaraoke.asr import AsrWord, Transcript
from autokaraoke.project import Project
from autokaraoke.text.anchors import fill_starts, match_line_starts, tokens
from autokaraoke.text.lyrics import make_line
from autokaraoke.text.reconcile import apply_reply, build_prompt, lines_from_transcript


def _words(text: str, start: float, step: float = 0.4) -> list[AsrWord]:
    return [AsrWord(w, start + i * step, start + i * step + 0.3) for i, w in enumerate(text.split())]


SUNG = (
    _words("nel blu dipinto di blu", 10.0)
    + _words("felice di stare lassù", 15.0)
    + _words("volare oh oh", 20.0)
    + _words("nel blu dipinto di blu", 30.0)
    + _words("felice di stare lassù", 35.0)
)


def test_tokens():
    assert tokens("L'amore, è... blu!") == ["l", "amore", "è", "blu"]
    assert tokens("さくら") == ["さ", "く", "ら"]


def test_match_line_starts_follows_singing_order():
    texts = ["Nel blu, dipinto di blu", "Felice di stare lassù", "Volare, oh oh",
             "Nel blu, dipinto di blu", "Felice di stare lassù", "Never sung here"]
    starts = match_line_starts(texts, SUNG)
    assert starts[:5] == [10.0, 15.0, 20.0, 30.0, 35.0]
    assert starts[5] is None


def test_fill_starts_interpolates_and_orders():
    assert fill_starts([1.0, None, 5.0], 10.0) == [1.0, 3.0, 5.0]
    # out-of-order match is dropped, hint used when it fits
    assert fill_starts([1.0, 0.5, 5.0], 10.0, hints=[None, 2.0, None]) == [1.0, 2.0, 5.0]


def _project(texts, starts=None):
    p = Project(title="Volare", artist="Modugno", duration=60.0, language="it")
    starts = starts or [None] * len(texts)
    p.lines = [make_line(t, "it", start=s) for t, s in zip(texts, starts)]
    return p


def test_apply_reply_expands_chorus_and_can_be_rejected():
    p = _project(["[Ritornello]", "Nel blu, dipinto di blu", "Felice di stare lassù", "Volare, oh oh",
                  "(Rit. x2)"])
    tr = Transcript("Italian", SUNG)
    reply = {
        "lines": [
            {"text": "Nel blu, dipinto di blu", "from": 1, "start": 10},
            {"text": "Felice di stare lassù", "from": 2, "start": 15},
            {"text": "Volare, oh oh!", "from": 3, "start": 20},
            {"text": "Nel blu, dipinto di blu", "from": 1, "start": 29},
            {"text": "Felice di stare lassù", "from": 2, "start": 35},
        ],
        "notes": ["Ritornello espanso"],
        "doubts": [{"line": 1, "heard": "felice di stare laggiù"}],
    }
    prop = apply_reply(p, tr, reply)
    kinds = [c.kind for c in prop.changes]
    assert kinds.count("remove") == 2 and kinds.count("add") == 2 and kinds.count("edit") == 1
    assert kinds.count("doubt") == 1
    lines = prop.result()
    assert [ln.text for ln in lines] == [r["text"] for r in reply["lines"]]
    # placed from the transcript, not from the model's rough guess
    assert [ln.start for ln in lines] == [10.0, 15.0, 20.0, 30.0, 35.0]
    assert lines[1].note.endswith("felice di stare laggiù")
    for c in prop.changes:
        c.accepted = c.kind == "doubt"
    lines = prop.result()
    assert [ln.text for ln in lines] == [ln.text for ln in p.lines[:1]] + [
        "Nel blu, dipinto di blu", "felice di stare laggiù", "Volare, oh oh", "(Rit. x2)"]


def test_apply_reply_keeps_synced_timings():
    p = _project(["Nel blu, dipinto di blu", "Felice di stare lassù"], [10.0, 15.0])
    for ln in p.lines:
        for i, s in enumerate(ln.syllables()):
            s.start, s.end, s.confidence = ln.start + i * 0.3, ln.start + i * 0.3 + 0.3, 0.9
    reply = {"lines": [{"text": "Nel blu, dipinto di blu", "from": 0, "start": 11},
                       {"text": "Felice di stare lassù", "from": 1, "start": 14}]}
    prop = apply_reply(p, Transcript("Italian", SUNG), reply)
    assert prop.changes == []
    assert [s.start for s in prop.result()[0].syllables()] == [s.start for s in p.lines[0].syllables()]


def test_prompt_and_transcript_fallback():
    tr = Transcript("Italian", SUNG)
    prompt = build_prompt(_project(["Nel blu"]), tr)
    assert "L0 [?] Nel blu" in prompt and "[10.00-" in prompt
    lines = lines_from_transcript(tr, "it")
    assert lines[0].text == "nel blu dipinto di blu" and lines[0].start == 10.0


def test_dotenv(tmp_path, monkeypatch):
    from autokaraoke import config

    env = tmp_path / ".env"
    env.write_text("# comment\nOPENROUTER_KEY='abc'\nexport AK_TEST_OTHER=1\n")
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.delenv("OPENROUTER_KEY", raising=False)
    monkeypatch.setenv("AK_TEST_OTHER", "keep")
    config._load_dotenv(env)
    assert config.openrouter_key() == "abc"
    assert __import__("os").environ["AK_TEST_OTHER"] == "keep"  # real environment wins
