import numpy as np

from autokaraoke.align.base import Segment, Timing
from autokaraoke.align.ctc import romanize, viterbi
from autokaraoke.align.pipeline import _plausible, _postprocess_line
from autokaraoke.project import Syllable


def _emissions(frames: list[int], vocab: int = 5) -> np.ndarray:
    """Log-probs where frame t strongly predicts token frames[t]."""
    lp = np.full((len(frames), vocab), np.log(0.01))
    for t, tok in enumerate(frames):
        lp[t, tok] = np.log(0.96)
    return lp


def test_viterbi_finds_token_spans():
    # blank=0; tokens 1,2,2 (repeat needs a blank in between)
    frames = [0, 1, 1, 0, 2, 0, 2, 2, 0]
    spans = viterbi(_emissions(frames), [1, 2, 2])
    assert spans == [1, 2, 4, 4, 6, 7]


def test_romanize():
    assert romanize("Città,") == "citta"
    assert romanize("21st", "en") == "twentyfirst"


def test_plausible_rejects_out_of_window_and_collapsed():
    seg = Segment(10.0, np.zeros(16000 * 3, np.float32), ["a", "b", "c"])
    good = [Timing(10.5, 10.7), Timing(10.7, 11.0), Timing(11.0, 11.4)]
    assert _plausible(good, seg, 10.5)
    assert not _plausible([Timing(10.5, 10.7), Timing(10.7, 11.0), Timing(11.0, 14.0)], seg, 10.5)
    collapsed = [Timing(10.5, 10.5), Timing(10.5, 10.5), Timing(10.5, 11.4)]
    assert not _plausible(collapsed, seg, 10.5)
    assert not _plausible(good, seg, 7.0)


def test_postprocess_enforces_min_duration_and_order():
    sylls = [Syllable("a", 1.0, 1.4), Syllable("b", 1.4, 1.4), Syllable("c", 1.3, 1.8)]
    rms = np.ones(300, np.float32)
    _postprocess_line(sylls, rms)
    for s in sylls:
        assert s.end - s.start >= 0.08 - 1e-6
    for a, b in zip(sylls, sylls[1:]):
        assert a.end <= b.start + 1e-6
