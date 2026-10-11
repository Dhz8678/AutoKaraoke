"""ffmpeg helpers."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise RuntimeError(f"{tool} not found in PATH")


def probe_duration(path: Path) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json", str(path)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    return float(json.loads(out)["format"]["duration"])


def to_wav(src: Path, dst: Path, sample_rate: int = 44100) -> Path:
    """Decode any audio/video file to stereo 16-bit WAV."""
    subprocess.run(
        ["ffmpeg", "-y", "-v", "error", "-i", str(src), "-vn", "-ac", "2", "-ar", str(sample_rate),
         "-c:a", "pcm_s16le", str(dst)],
        check=True,
    )
    return dst
