"""Karaoke mix: the instrumental plus the original vocals only where lines are marked as backing."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from autokaraoke.project import Project

MIX_FILE = "karaoke_mix.flac"
FADE = 0.05  # seconds of fade in / out, inside the backing span
JOIN_GAP = 0.1  # backing lines closer than this are mixed as one span (no dip between them)


def backing_spans(project: Project) -> list[tuple[float, float]]:
    """From the start of the first syllable to the end of the last one of each backing line.

    The vocals never leak outside these spans: move the first / last syllable edges to
    control exactly where the fades happen.
    """
    spans = sorted(span for ln in project.lines if ln.backing and (span := ln.span()))
    merged: list[tuple[float, float]] = []
    for a, b in spans:
        if merged and a - merged[-1][1] <= JOIN_GAP:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def envelope(spans: list[tuple[float, float]], n: int, sample_rate: int) -> np.ndarray:
    """Gain per sample: 0 outside the spans, 1 inside, with linear fades inside their edges."""
    t = np.arange(n, dtype=np.float32) / sample_rate
    env = np.zeros(n, np.float32)
    for a, b in spans:
        fade = max(min(FADE, (b - a) / 2), 1e-3)
        lo, hi = int(np.ceil(a * sample_rate)), min(n, int(b * sample_rate) + 1)
        if lo >= hi:
            continue
        seg = t[lo:hi]
        gain = np.clip(np.minimum((seg - a) / fade, (b - seg) / fade), 0, 1)
        env[lo:hi] = np.maximum(env[lo:hi], gain)
    return env


def karaoke_mix(project: Project, out: Path | None = None) -> Path | None:
    """Write the mix and return its path, or None when there are no backing lines or stems."""
    import soundfile as sf

    spans = backing_spans(project)
    inst = project.abspath(project.audio_instrumental)
    voc = project.abspath(project.audio_vocals)
    if not spans or not inst or not voc or not inst.exists() or not voc.exists():
        return None
    a, sr = sf.read(str(inst), dtype="float32", always_2d=True)
    v, sr_v = sf.read(str(voc), dtype="float32", always_2d=True)
    if sr_v != sr:
        raise ValueError("人声与伴奏音轨的采样率不一致")
    n = min(len(a), len(v))
    if v.shape[1] != a.shape[1]:
        v = np.repeat(v.mean(axis=1, keepdims=True), a.shape[1], axis=1)
    mix = a[:n] + v[:n] * envelope(spans, n, sr)[:, None]
    np.clip(mix, -1.0, 1.0, out=mix)
    assert project.path is not None
    out = out or project.path / MIX_FILE
    # Write next to it and rename: the editor may be playing the previous version.
    tmp = out.with_name(out.name + ".tmp")
    sf.write(str(tmp), mix, sr, format="FLAC", subtype="PCM_16")
    tmp.replace(out)
    return out
