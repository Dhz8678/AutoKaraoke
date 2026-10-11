"""Runtime configuration and build-time feature flags."""

from __future__ import annotations

import os
from pathlib import Path

# Release builds overwrite this to False so the shipped binary cannot download
# audio from streaming services; users import their own audio files instead.
BUILD_ENABLE_DOWNLOAD = True


def download_enabled() -> bool:
    if not BUILD_ENABLE_DOWNLOAD:
        return False
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return False
    return True


# LRCLIB asks clients to identify themselves; add the repo URL once published.
USER_AGENT = "AutoKaraoke/0.1"

PROJECTS_DIR = Path(os.environ.get("AUTOKARAOKE_PROJECTS", "projects"))
MODELS_DIR = Path(os.environ.get("AUTOKARAOKE_MODELS", Path.home() / ".cache" / "autokaraoke" / "models"))

# Default UVR model for vocal separation (MelBand RoFormer, vocals/instrumental).
SEPARATION_MODEL = os.environ.get("AUTOKARAOKE_SEPARATION_MODEL", "vocals_mel_band_roformer.ckpt")


def _load_dotenv(path: Path = Path(".env")) -> None:
    """Minimal ``KEY=value`` reader; real environment variables win."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autokaraoke"
_load_dotenv()
_load_dotenv(CONFIG_DIR / ".env")


def openrouter_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or None


def experimental_llm() -> bool:
    """LLM lyric fixing is experimental and off unless AUTOKARAOKE_EXPERIMENTAL_LLM=1."""
    return os.environ.get("AUTOKARAOKE_EXPERIMENTAL_LLM", "").strip().lower() in ("1", "true", "yes", "on")


def llm_available() -> bool:
    return experimental_llm() and openrouter_key() is not None


OPENROUTER_MODEL = os.environ.get("AUTOKARAOKE_LLM_MODEL", "deepseek/deepseek-v4.1-flash")
