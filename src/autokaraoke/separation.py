"""Vocal / instrumental separation with UVR models via python-audio-separator."""

from __future__ import annotations

from pathlib import Path

from autokaraoke.config import MODELS_DIR, SEPARATION_MODEL


def separate(
    audio: Path, out_dir: Path, model: str = SEPARATION_MODEL
) -> tuple[Path, Path]:
    """Split ``audio`` into (vocals.wav, instrumental.wav) inside ``out_dir``."""
    try:
        from audio_separator.separator import Separator
    except ImportError as e:
        raise RuntimeError(
            "audio-separator is not installed; install the 'separation' extra"
        ) from e

    out_dir.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    sep = Separator(output_dir=str(out_dir), model_file_dir=str(MODELS_DIR), output_format="WAV")
    sep.load_model(model_filename=model)
    # Vocal models name the accompaniment stem "Instrumental" or "Other".
    names = {"Vocals": "vocals", "Instrumental": "instrumental", "Other": "instrumental"}
    files = sep.separate(str(audio), custom_output_names=names)

    vocals = out_dir / "vocals.wav"
    instrumental = out_dir / "instrumental.wav"
    if not vocals.exists() or not instrumental.exists():
        raise RuntimeError(f"unexpected separator output: {files}")
    return vocals, instrumental
