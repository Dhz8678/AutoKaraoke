"""Aligner interface shared by all engines."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import numpy as np

SAMPLE_RATE = 16000


@dataclass
class Segment:
    """A window of audio plus the units (syllables) sung in it, in order."""

    start: float  # window start in the song, seconds
    audio: np.ndarray  # mono float32 at SAMPLE_RATE
    units: list[str]


@dataclass
class Timing:
    start: float  # seconds, absolute in the song
    end: float


class Aligner(Protocol):
    name: str

    def align(self, segments: list[Segment]) -> list[list[Timing]]:
        """Return one Timing per unit for every segment."""
        ...


def load_mono(path, sample_rate: int = SAMPLE_RATE) -> np.ndarray:
    import librosa

    audio, _ = librosa.load(str(path), sr=sample_rate, mono=True)
    return audio.astype(np.float32)
