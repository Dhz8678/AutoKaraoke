"""Runtime configuration and build-time feature flags."""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

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


if os.name == "nt":
    CONFIG_DIR = Path(os.environ.get("APPDATA", Path.home())) / "autokaraoke"
else:
    CONFIG_DIR = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "autokaraoke"
_load_dotenv()
_load_dotenv(CONFIG_DIR / ".env")


def openrouter_key() -> str | None:
    return os.environ.get("OPENROUTER_API_KEY") or None


DEFAULT_LLM_BASE_URL = "https://openrouter.ai/api/v1"


def llm_base_url() -> str:
    return os.environ.get("AUTOKARAOKE_LLM_BASE_URL", "").strip() or DEFAULT_LLM_BASE_URL


def llm_chat_url(base_url: str | None = None) -> str:
    """Accept a /v1 base, a service root, or a full chat/completions URL."""
    value = (base_url if base_url is not None else llm_base_url()).strip()
    if not value or any(char.isspace() for char in value):
        raise ValueError("API 地址不能为空，也不能包含空格或换行。")
    parsed = urlsplit(value)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.fragment:
        raise ValueError("请输入有效的 http:// 或 https:// API 地址。")
    try:
        parsed.port
    except ValueError as exc:
        raise ValueError("API 地址的端口无效。") from exc
    path = parsed.path.rstrip("/")
    if not path:
        path = "/v1"
    if not path.endswith("/chat/completions"):
        path += "/chat/completions"
    return urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def llm_uses_openrouter() -> bool:
    try:
        return urlsplit(llm_base_url()).hostname == "openrouter.ai"
    except ValueError:
        return False  # Keep Settings accessible so a malformed saved URL can be fixed.


def llm_api_key() -> str | None:
    # Never send an old OpenRouter-only credential to a custom endpoint.
    return os.environ.get("AUTOKARAOKE_LLM_API_KEY") or (openrouter_key() if llm_uses_openrouter() else None)


def experimental_llm() -> bool:
    """LLM lyric fixing is experimental and off unless AUTOKARAOKE_EXPERIMENTAL_LLM=1."""
    return os.environ.get("AUTOKARAOKE_EXPERIMENTAL_LLM", "").strip().lower() in ("1", "true", "yes", "on")


def simplified_chinese_lyrics() -> bool:
    return os.environ.get("AUTOKARAOKE_SIMPLIFIED_CHINESE", "1").strip().lower() not in ("0", "false", "no", "off")


def llm_available() -> bool:
    # Self-hosted compatible APIs may intentionally have no authentication.
    return experimental_llm() and (llm_api_key() is not None or not llm_uses_openrouter())


OPENROUTER_MODEL = os.environ.get("AUTOKARAOKE_LLM_MODEL", "deepseek/deepseek-v4.1-flash")
