"""Speech recognition of the vocal stem with word timestamps.

Qwen3-ASR transcribes what is actually sung (repeated choruses, ad-libs, words
that differ from the published lyrics); Qwen3-ForcedAligner adds word times.
The transcript is used to check/correct the lyrics and to place lines when the
lyrics have no timestamps. Songs are cut at vocal pauses into short chunks:
recognition on long sung passages tends to skip repetitions.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np

from autokaraoke.align.base import SAMPLE_RATE, load_mono

MODEL_ID = "Qwen/Qwen3-ASR-1.7B"
ALIGNER_ID = "Qwen/Qwen3-ForcedAligner-0.6B"
CHUNK_SECONDS = 30.0
BATCH_SIZE = 4
HOP = 160  # 10 ms

LANGUAGES = {
    "zh": "Chinese", "en": "English", "yue": "Cantonese", "ar": "Arabic", "de": "German",
    "fr": "French", "es": "Spanish", "pt": "Portuguese", "id": "Indonesian", "it": "Italian",
    "ko": "Korean", "ru": "Russian", "th": "Thai", "vi": "Vietnamese", "ja": "Japanese",
    "tr": "Turkish", "hi": "Hindi", "ms": "Malay", "nl": "Dutch", "sv": "Swedish",
    "da": "Danish", "fi": "Finnish", "pl": "Polish", "cs": "Czech", "tl": "Filipino",
    "fa": "Persian", "el": "Greek", "ro": "Romanian", "hu": "Hungarian", "mk": "Macedonian",
}


@dataclass
class AsrWord:
    text: str
    start: float
    end: float


@dataclass
class Transcript:
    language: str = ""
    words: list[AsrWord] = field(default_factory=list)

    @property
    def text(self) -> str:
        return " ".join(w.text for w in self.words)

    def phrases(self, gap: float = 0.6, max_words: int = 14) -> list[tuple[float, float, str]]:
        """Group words into phrases at vocal pauses (a rough stand-in for lyric lines)."""
        out: list[tuple[float, float, str]] = []
        cur: list[AsrWord] = []
        for w in self.words:
            if cur and (w.start - cur[-1].end > gap or len(cur) >= max_words):
                out.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))
                cur = []
            cur.append(w)
        if cur:
            out.append((cur[0].start, cur[-1].end, " ".join(x.text for x in cur)))
        return out

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=1), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Transcript:
        d = json.loads(path.read_text(encoding="utf-8"))
        return cls(language=d.get("language", ""), words=[AsrWord(**w) for w in d.get("words", [])])


def split_at_pauses(audio: np.ndarray, max_seconds: float = CHUNK_SECONDS) -> list[tuple[int, int]]:
    """Cut points at the quietest 0.3 s inside the last third of each chunk."""
    n = len(audio) // HOP
    rms = np.sqrt((audio[: n * HOP].reshape(n, HOP) ** 2).mean(axis=1) + 1e-12)
    smooth = np.convolve(rms, np.ones(30) / 30, mode="same")
    max_frames = int(max_seconds * SAMPLE_RATE / HOP)
    bounds, a = [], 0
    while n - a > max_frames:
        lo, hi = a + max_frames * 2 // 3, a + max_frames
        cut = lo + int(np.argmin(smooth[lo:hi]))
        bounds.append((a * HOP, cut * HOP))
        a = cut
    bounds.append((a * HOP, len(audio)))
    return bounds


class Transcriber:
    def __init__(self, device: str | None = None):
        import torch
        from qwen_asr import Qwen3ASRModel

        device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        self._model = Qwen3ASRModel.from_pretrained(
            MODEL_ID,
            forced_aligner=ALIGNER_ID,
            forced_aligner_kwargs={"dtype": dtype, "device_map": device},
            max_inference_batch_size=BATCH_SIZE,
            max_new_tokens=1024,
            dtype=dtype,
            device_map=device,
        )

    def transcribe(self, audio: np.ndarray, language: str = "", context: str = "") -> Transcript:
        """``language`` is an ISO code; unsupported codes fall back to auto-detection."""
        lang = LANGUAGES.get(language.split("_")[0].lower())
        bounds = split_at_pauses(audio)
        chunks = [(audio[a:b], SAMPLE_RATE) for a, b in bounds]
        results = self._model.transcribe(
            chunks, context=context, language=lang, return_time_stamps=True
        )
        words: list[AsrWord] = []
        detected: list[str] = []
        for (a, _), res in zip(bounds, results):
            if res.language:
                detected.append(res.language)
            if res.time_stamps is None:
                continue
            offset = a / SAMPLE_RATE
            for item in res.time_stamps.items:
                words.append(AsrWord(item.text, round(offset + item.start_time, 3),
                                     round(offset + item.end_time, 3)))
        return Transcript(language=lang or (max(set(detected), key=detected.count) if detected else ""),
                          words=words)


def transcribe_file(path: Path, language: str = "", context: str = "") -> Transcript:
    return Transcriber().transcribe(load_mono(path), language, context)
