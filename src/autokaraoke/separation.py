"""Vocal / instrumental separation with UVR models via python-audio-separator."""

from __future__ import annotations

from pathlib import Path
from collections.abc import Callable
from urllib.parse import urlsplit
from uuid import uuid4

from autokaraoke.config import MODELS_DIR, SEPARATION_MODEL


def _download_model_file(url, output_path, download):
    """A cancelled/failed transfer must never look like a cached complete model."""
    from autokaraoke.task_progress import named_download

    target = Path(output_path)
    if target.is_file():
        return download(url, str(target))
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.download")
    try:
        with named_download(target.name):
            result = download(url, str(temporary))
        temporary.replace(target)
        return result
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def separate(
    audio: Path, out_dir: Path, model: str = SEPARATION_MODEL,
    log: Callable[[str], None] | None = None,
) -> tuple[Path, Path]:
    """Split ``audio`` into (vocals.wav, instrumental.wav) inside ``out_dir``."""
    try:
        from audio_separator.separator import Separator
    except ImportError as e:
        raise RuntimeError(
            "尚未安装人声分离组件，请先安装 AI 运行组件。"
        ) from e

    out_dir.mkdir(parents=True, exist_ok=True)
    MODELS_DIR.mkdir(parents=True, exist_ok=True)
    sep = Separator(output_dir=str(out_dir), model_file_dir=str(MODELS_DIR), output_format="WAV")
    original_download = sep.download_file_if_not_exists

    def download(url, output_path):
        if log and not Path(output_path).is_file():
            log(f"正在连接 {urlsplit(url).hostname} 获取 {Path(output_path).name}，等待下载响应…")
        return _download_model_file(url, output_path, original_download)

    sep.download_file_if_not_exists = download
    sep.load_model(model_filename=model)
    if log:
        log("模型已就绪，正在分离人声与伴奏…")
    # Vocal models name the accompaniment stem "Instrumental" or "Other".
    names = {"Vocals": "vocals", "Instrumental": "instrumental", "Other": "instrumental"}
    files = sep.separate(str(audio), custom_output_names=names)

    vocals = out_dir / "vocals.wav"
    instrumental = out_dir / "instrumental.wav"
    if not vocals.exists() or not instrumental.exists():
        raise RuntimeError(f"人声分离输出异常：{files}")
    return vocals, instrumental
