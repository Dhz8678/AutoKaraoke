"""Karaoke mix: the instrumental plus the original vocals only where lines are marked as backing."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from autokaraoke.project import Project

MIX_FILE = "karaoke_mix.flac"
PAD = 0.15  # seconds of vocals kept around a backing line
FADE = 0.05


def backing_spans(project: Project) -> list[tuple[float, float]]:
    spans = sorted(span for ln in project.lines if ln.backing and (span := ln.span()))
    return [(max(0.0, a - PAD), b + PAD) for a, b in spans]


def envelope(spans: list[tuple[float, float]], n: int, sample_rate: int) -> np.ndarray:
    """Gain per sample: 1 inside the spans, 0 outside, with linear fades just outside them."""
    t = np.arange(n, dtype=np.float32) / sample_rate
    env = np.zeros(n, np.float32)
    for a, b in spans:
        lo, hi = int(max(0.0, a - FADE) * sample_rate), min(n, int((b + FADE) * sample_rate) + 1)
        if lo >= hi:
            continue
        seg = t[lo:hi]
        gain = np.clip(np.minimum((seg - (a - FADE)) / FADE, ((b + FADE) - seg) / FADE), 0, 1)
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
        raise ValueError("vocal and instrumental stems have different sample rates")
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
