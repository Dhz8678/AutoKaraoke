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
import codecs
import time
from dataclasses import replace
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from autokaraoke.component_progress import InstallProgress, UvProgress

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


def _environment_busy() -> bool:
    """Probe uv's environment lock without changing files or taking over a task."""
    environment = os.environ.get("UV_PROJECT_ENVIRONMENT")
    if not environment:
        return False
    path = Path(environment) / ".lock"
    try:
        stream = path.open("r+b")
    except FileNotFoundError:
        return False
    with stream:
        if os.name == "nt":
            import msvcrt

            try:
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError as exc:
                if exc.errno in (11, 13, 36) or getattr(exc, "winerror", None) in (32, 33):
                    return True
                raise
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(stream, fcntl.LOCK_UN)
    return False


def _wait_for_environment(log, progress, timeout: float = 60.0) -> None:
    start = time.monotonic()
    waiting = False
    while _environment_busy():
        elapsed = time.monotonic() - start
        detail = f"其他安装任务正在使用运行环境，等待其释放（已等待 {elapsed:.0f} 秒）。"
        progress(InstallProgress(stage="waiting", detail=detail))
        if not waiting:
            log("检测到运行环境被其他安装任务占用，正在等待环境锁释放。")
            waiting = True
        if elapsed >= timeout:
            raise RuntimeError("其他安装任务仍未完成。请等待已有安装结束后重试；当前任务没有停止其他安装进程。")
        time.sleep(0.5)
    if waiting:
        log("运行环境锁已释放，开始安装。")
    progress(InstallProgress(detail="正在启动安装器并解析依赖…"))


def _run_installer(command: list[str], log: Callable[[str], None],
                   progress: Callable[[InstallProgress], None]) -> int:
    from autokaraoke.terminal import TerminalProcess

    _wait_for_environment(log, progress)
    try:
        terminal = TerminalProcess(command)
    except (OSError, AttributeError):
        # Older Windows versions / environments without a usable pseudo-console.
        # Keep installation working, but never invent a byte percentage.
        log('当前环境无法读取实时下载量，将显示安装日志')
        progress(InstallProgress(detail='正在等待安装器（当前环境无法读取实时下载量）'))
        flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
        with subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace", creationflags=flags) as proc:
            for line in proc.stdout:
                if line := line.strip():
                    log(line)
                    progress(InstallProgress(detail=line))
            return proc.wait()
    reporter = UvProgress()
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    seen_lines = set()
    last_update = time.monotonic()
    with terminal:
        for data in terminal.output(heartbeat=1.0):
            if not data:
                silent = time.monotonic() - last_update
                progress(replace(reporter.current, detail=f"安装器仍在运行，已 {silent:.0f} 秒无新输出；正在等待响应或处理依赖。"))
                continue
            update, messages = reporter.feed(decoder.decode(data))
            last_update = time.monotonic()
            lines = reporter.log_lines()
            if not update.total and not update.package:
                update = replace(update, detail=lines[-1] if lines else "正在启动安装器并解析依赖…")
            progress(update)
            for message in messages:
                log(message)
            for line in lines:
                if line not in seen_lines:
                    log(line)
                    seen_lines.add(line)
        update, _ = reporter.feed(decoder.decode(b"", final=True))
        progress(update)
        code = terminal.wait()
    for line in reporter.log_lines():
        if line not in seen_lines:
            log(line)
    return code


def install(log: Callable[[str], None],
            progress: Callable[[InstallProgress], None] | None = None) -> None:
    """Download and install the AI components into the running environment."""
    if not packaged():
        raise RuntimeError('请运行以下命令安装：uv sync --extra gui --extra separation --extra align --extra gpu（或 --extra cpu）')
    report = progress or (lambda _update: None)
    report(InstallProgress(detail='正在检测显卡并准备下载…'))
    variant = torch_variant()
    log('已检测到 NVIDIA 显卡，正在安装支持 CUDA 的 PyTorch' if variant == "gpu" else
        '未检测到 NVIDIA 显卡，正在安装 CPU 版 PyTorch（下载较小，AI 处理速度较慢）')
    try:
        code = _run_installer(install_command(variant), log, report)
        if code != 0:
            raise RuntimeError(f'AI 组件安装失败，退出码：{code}')
    except Exception:
        report(InstallProgress(stage="failed", detail='安装失败，请查看下方日志了解原因。'))
        raise
    if marker := _marker():
        marker.write_text(os.environ.get("AUTOKARAOKE_LOCK_HASH", ""), encoding="utf-8")
    importlib.invalidate_caches()
    report(InstallProgress(stage="complete", completed=1, total=1, detail='AI 运行组件已就绪'))
