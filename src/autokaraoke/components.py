"""Optional AI components (vocal separation, alignment, speech recognition).

The release packages start with the editor only and download these on request (CUDA or
CPU-only PyTorch, depending on the graphics card) through the bundled ``uv``. The launcher scripts describe the setup with environment
variables:

  AUTOKARAOKE_UV          path of the bundled uv executable
  AUTOKARAOKE_APP         directory with pyproject.toml / uv.lock of this release
  AUTOKARAOKE_LOCK_HASH   hash of that uv.lock, to notice updates
  UV_PROJECT_ENVIRONMENT  the virtual environment the app runs in
  AUTOKARAOKE_TORCH       "gpu" or "cpu" to override the automatic choice of PyTorch build
"""

from __future__ import annotations

import importlib
import importlib.util
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

AI_MODULES = ("torch", "audio_separator", "qwen_asr", "librosa", "uroman")
AI_EXTRAS = ("gui", "separation", "align")


def has_nvidia_gpu() -> bool:
    """An NVIDIA driver is installed (nvidia-smi comes with it on Linux and Windows)."""
    exe = shutil.which("nvidia-smi")
    if exe is None and sys.platform == "win32":
        candidate = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "nvidia-smi.exe"
        exe = str(candidate) if candidate.exists() else None
    if exe is None:
        return False
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        out = subprocess.run([exe, "-L"], capture_output=True, text=True, timeout=15, creationflags=flags)
    except (OSError, subprocess.SubprocessError):
        return False
    return out.returncode == 0 and "GPU" in out.stdout


def torch_variant() -> str:
    """PyTorch build to install: CUDA for NVIDIA cards, the much smaller CPU one otherwise."""
    forced = os.environ.get("AUTOKARAOKE_TORCH", "").strip().lower()
    if forced in ("gpu", "cpu"):
        return forced
    if sys.platform == "darwin":
        return "cpu"
    return "gpu" if has_nvidia_gpu() else "cpu"


MARKER = ".autokaraoke-ai"


def packaged() -> bool:
    """Running from a release package (launcher with a bundled uv)?"""
    return bool(os.environ.get("AUTOKARAOKE_UV") and os.environ.get("AUTOKARAOKE_APP"))


def _marker() -> Path | None:
    env = os.environ.get("UV_PROJECT_ENVIRONMENT")
    return Path(env) / MARKER if env else None


def state() -> str:
    """'ok', 'missing' or 'outdated' (installed for a previous release)."""
    if any(importlib.util.find_spec(m) is None for m in AI_MODULES):
        return "missing"
    marker = _marker()
    if packaged() and marker is not None:
        want = os.environ.get("AUTOKARAOKE_LOCK_HASH", "")
        have = marker.read_text(encoding="utf-8").strip() if marker.exists() else ""
        if want and have != want:
            return "outdated"
    return "ok"


def install_command(variant: str) -> list[str]:
    cmd = [os.environ["AUTOKARAOKE_UV"], "sync", "--project", os.environ["AUTOKARAOKE_APP"],
           "--frozen", "--no-install-project", "--no-dev", "--inexact"]
    for extra in (*AI_EXTRAS, variant):
        cmd += ["--extra", extra]
    return cmd


def install(log: Callable[[str], None]) -> None:
    """Download and install the AI components into the running environment."""
    if not packaged():
        raise RuntimeError("Install them with: uv sync --extra gui --extra separation --extra align "
                           "--extra gpu (or --extra cpu)")
    variant = torch_variant()
    log("NVIDIA graphics card found: installing PyTorch with CUDA" if variant == "gpu" else
        "No NVIDIA graphics card: installing the CPU version of PyTorch (smaller; the AI steps "
        "will be slower)")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    proc = subprocess.Popen(install_command(variant), stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", errors="replace", creationflags=flags)
    assert proc.stdout is not None
    for line in proc.stdout:
        if line := line.strip():
            log(line)
    if proc.wait() != 0:
        raise RuntimeError(f"uv sync failed with exit code {proc.returncode}")
    if marker := _marker():
        marker.write_text(os.environ.get("AUTOKARAOKE_LOCK_HASH", ""), encoding="utf-8")
    importlib.invalidate_caches()
