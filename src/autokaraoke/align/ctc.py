"""CTC forced alignment with Meta's MMS model (~1100 languages via romanization).

Used as a fallback where the Qwen aligner loses track (layered backing vocals,
nonsense syllables) and for languages Qwen was not trained on. A Viterbi pass
over the CTC emissions always yields a complete, monotonic path.

Model weights: CC-BY-NC 4.0 (non-commercial use).
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache

import numpy as np
import torch

from autokaraoke.align.base import Segment, Timing

MODEL_ID = "MahmoudAshraf/mms-300m-1130-forced-aligner"
FRAME = 0.02  # seconds per emission frame (wav2vec2 stride 320 at 16 kHz)


@lru_cache(maxsize=1)
def _uroman():
    try:
        import uroman
    except ImportError:
        return None
    return uroman.Uroman()


def spell_numbers(text: str, lang: str) -> str:
    """'21st' -> 'twenty-first', '1999' -> words, so digits are aligned to what is sung."""
    if not any(ch.isdigit() for ch in text):
        return text
    try:
        from num2words import num2words
    except ImportError:
        return text

    def repl(m: re.Match) -> str:
        n, suffix = int(m.group(1)), m.group(2)
        try:
            return num2words(n, to="ordinal" if suffix else "cardinal", lang=lang or "en")
        except (NotImplementedError, OverflowError):
            return num2words(n, to="ordinal" if suffix else "cardinal", lang="en")

    return re.sub(r"(\d+)(st|nd|rd|th|°|º|ª)?", repl, text)


def romanize(text: str, lang: str = "") -> str:
    """Map a unit onto the model's a-z' alphabet."""
    text = spell_numbers(text, lang)
    if any(ch.isalpha() and not ("a" <= unicodedata.normalize("NFKD", ch)[0].lower() <= "z")
           for ch in text):
        if (rom := _uroman()) is not None:
            text = rom.romanize_string(text)
    text = unicodedata.normalize("NFKD", text.lower())
    return "".join(ch for ch in text if "a" <= ch <= "z" or ch == "'")


def viterbi(log_probs: np.ndarray, tokens: list[int], blank: int = 0) -> list[int]:
    """CTC Viterbi: return, for each token, the first frame it is emitted at, plus its last frame.

    Output is a flat list [first_0, last_0, first_1, last_1, ...].
    """
    T = log_probs.shape[0]
    S = 2 * len(tokens) + 1
    labels = np.full(S, blank)
    labels[1::2] = tokens
    emit = log_probs[:, labels]  # T x S
    # A state may skip the blank before it unless it repeats the previous token.
    can_skip = np.zeros(S, bool)
    can_skip[3::2] = labels[3::2] != labels[1:-2:2]

    neg = -1e30
    score = np.full(S, neg)
    score[0], score[1] = emit[0, 0], emit[0, 1]
    back = np.zeros((T, S), np.int8)  # 0 stay, 1 from s-1, 2 from s-2
    for t in range(1, T):
        stay = score
        step = np.concatenate([[neg], score[:-1]])
        skip = np.where(can_skip, np.concatenate([[neg, neg], score[:-2]]), neg)
        choices = np.stack([stay, step, skip])
        back[t] = choices.argmax(axis=0)
        score = choices.max(axis=0) + emit[t]

    s = S - 1 if score[S - 1] >= score[S - 2] else S - 2
    path = np.empty(T, int)
    for t in range(T - 1, -1, -1):
        path[t] = s
        s -= back[t, s]

    spans = []
    for k in range(len(tokens)):
        frames = np.nonzero(path == 2 * k + 1)[0]
        spans += [frames[0], frames[-1]] if len(frames) else [-1, -1]
    return spans


class CtcAligner:
    name = "mms"

    def __init__(self, language: str = "", device: str | None = None):
        self.language = language
        from huggingface_hub import hf_hub_download
        from transformers import Wav2Vec2ForCTC

        self.device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        dtype = torch.float16 if self.device.startswith("cuda") else torch.float32
        self.model = Wav2Vec2ForCTC.from_pretrained(MODEL_ID, torch_dtype=dtype).to(self.device).eval()
        with open(hf_hub_download(MODEL_ID, "vocab.json"), encoding="utf-8") as f:
            self.vocab: dict[str, int] = json.load(f)

    def _emissions(self, audio: np.ndarray) -> np.ndarray:
        x = torch.from_numpy((audio - audio.mean()) / (audio.std() + 1e-7))
        x = x.to(self.device, self.model.dtype)[None]
        with torch.inference_mode():
            logits = self.model(x).logits[0].float()
        return torch.log_softmax(logits, dim=-1).cpu().numpy()

    def align(self, segments: list[Segment]) -> list[list[Timing]]:
        out = []
        for seg in segments:
            log_probs = self._emissions(seg.audio)
            tokens, owner = [], []
            for i, unit in enumerate(seg.units):
                for ch in romanize(unit, self.language):
                    tokens.append(self.vocab[ch])
                    owner.append(i)
            timings: list[Timing | None] = [None] * len(seg.units)
            if tokens and len(tokens) <= log_probs.shape[0]:
                spans = viterbi(log_probs, tokens, blank=self.vocab["<blank>"])
                for k, i in enumerate(owner):
                    first, last = spans[2 * k], spans[2 * k + 1]
                    if first < 0:
                        continue
                    t0, t1 = seg.start + first * FRAME, seg.start + (last + 1) * FRAME
                    cur = timings[i]
                    timings[i] = Timing(t0, t1) if cur is None else Timing(cur.start, t1)
            # Extend each unit to the next one's start: CTC marks only the spiky
            # onsets, while karaoke wants the whole sung duration.
            prev_end = seg.start
            for i, t in enumerate(timings):
                if t is None:
                    timings[i] = Timing(prev_end, prev_end)
                prev_end = timings[i].end
            for cur, nxt in zip(timings, timings[1:]):
                if nxt.start - cur.end < 0.3:
                    cur.end = max(cur.end, nxt.start)
            out.append(timings)
        return out
