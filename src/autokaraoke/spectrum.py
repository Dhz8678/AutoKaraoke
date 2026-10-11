"""Audio analysis for the editor: waveform envelope, log-frequency spectrogram and onsets.

Everything is computed on a 10 ms grid so it lines up with the timeline.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

HOP_S = 0.01
N_FFT = 2048
N_BANDS = 160
F_MIN, F_MAX = 40.0, 10000.0
DB_RANGE = 70.0


@dataclass
class Analysis:
    peaks: np.ndarray  # max |amplitude| per 10 ms, 0..1
    spec: np.ndarray  # uint8 (bands, frames), band 0 = lowest frequency
    onsets: np.ndarray  # seconds, sorted


def compute_peaks(audio: np.ndarray, sample_rate: int) -> np.ndarray:
    """Max |amplitude| per 10 ms, normalized to 0..1."""
    hop = int(sample_rate * HOP_S)
    n = len(audio) // hop
    peaks = np.abs(audio[: n * hop]).reshape(n, hop).max(axis=1)
    return (peaks / (peaks.max() + 1e-9)).astype(np.float32)


def band_filters(sample_rate: int, n_fft: int = N_FFT, n_bands: int = N_BANDS) -> np.ndarray:
    """Triangular filters with log-spaced centres, at least ~2 FFT bins wide. Shape (bands, bins)."""
    freqs = np.fft.rfftfreq(n_fft, 1 / sample_rate)
    f_max = min(F_MAX, sample_rate / 2 * 0.95)
    centres = np.geomspace(F_MIN, f_max, n_bands + 2)
    bin_hz = sample_rate / n_fft
    fb = np.zeros((n_bands, len(freqs)), np.float32)
    for b in range(n_bands):
        lo, c, hi = centres[b], centres[b + 1], centres[b + 2]
        lo, hi = min(lo, c - bin_hz), max(hi, c + bin_hz)
        up = (freqs - lo) / (c - lo)
        down = (hi - freqs) / (hi - c)
        fb[b] = np.clip(np.minimum(up, down), 0, None)
        fb[b] /= fb[b].sum() + 1e-12
    return fb


def band_power(audio: np.ndarray, sample_rate: int, block: int = 1024) -> np.ndarray:
    """Power per band and 10 ms frame, shape (bands, frames). Computed in blocks of frames."""
    hop = int(sample_rate * HOP_S)
    n_frames = len(audio) // hop
    padded = np.pad(audio.astype(np.float32), (N_FFT // 2, N_FFT // 2 + hop))
    window = np.hanning(N_FFT).astype(np.float32)
    fb = band_filters(sample_rate)
    out = np.empty((fb.shape[0], n_frames), np.float32)
    frames = np.lib.stride_tricks.sliding_window_view(padded, N_FFT)[::hop]
    for a in range(0, n_frames, block):
        b = min(a + block, n_frames)
        spec = np.fft.rfft(frames[a:b] * window, axis=1)
        out[:, a:b] = fb @ (spec.real**2 + spec.imag**2).T
    return out


def onset_times(log_power: np.ndarray, min_gap: float = 0.06) -> np.ndarray:
    """Spectral-flux onsets: where energy rises sharply across many bands (consonants, attacks)."""
    if log_power.shape[1] < 3:
        return np.zeros(0)
    flux = np.clip(np.diff(log_power, axis=1), 0, None).sum(axis=0)
    flux = np.convolve(flux, np.ones(3) / 3, mode="same")
    # Adaptive threshold: local mean over ~0.5 s plus a fraction of the global spread.
    win = 50
    local = np.convolve(flux, np.ones(win) / win, mode="same")
    thresh = local + flux.std()
    peak = (flux > thresh) & (flux >= np.roll(flux, 1)) & (flux >= np.roll(flux, -1))
    times = (np.flatnonzero(peak) + 1) * HOP_S
    kept: list[float] = []
    for t in times:
        if not kept or t - kept[-1] >= min_gap:
            kept.append(float(t))
    return np.asarray(kept)


def analyse(audio: np.ndarray, sample_rate: int) -> Analysis:
    """``audio`` is mono float."""
    power = band_power(audio, sample_rate)
    db = 10 * np.log10(power + 1e-10)
    top = np.percentile(db, 99.5) if db.size else 0.0
    spec = np.clip((db - (top - DB_RANGE)) / DB_RANGE, 0, 1)
    return Analysis(
        peaks=compute_peaks(audio, sample_rate),
        spec=(spec * 255).astype(np.uint8),
        onsets=onset_times(np.maximum(db, top - DB_RANGE)),
    )


def load_analysis(path) -> Analysis:
    import soundfile as sf

    audio, sr = sf.read(str(path), dtype="float32", always_2d=True)
    return analyse(audio.mean(axis=1), sr)


# Perceptual dark-to-bright colormap (approximation of "magma").
_MAGMA = np.array([
    (0, 0, 4), (28, 16, 68), (79, 18, 123), (129, 37, 129), (181, 54, 122),
    (229, 80, 100), (251, 135, 97), (254, 194, 135), (252, 253, 191),
], np.float32)


def colormap_lut() -> np.ndarray:
    x = np.linspace(0, len(_MAGMA) - 1, 256)
    return np.stack([np.interp(x, np.arange(len(_MAGMA)), _MAGMA[:, c]) for c in range(3)],
                    axis=1).astype(np.uint8)
