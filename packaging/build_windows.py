"""Build a BAT download-on-demand ZIP and a self-contained offline EXE installer.

Run on Windows using the installed AI Python, after prepare_offline_models.py.
Nothing is copied from user settings or song/project directories.
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import tempfile
import zipfile

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "packaging"


def digest(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def copy_tree(source: Path, destination: Path) -> None:
    pairs = []
    for current, directories, files in os.walk(source):
        directories[:] = [name for name in directories if name not in (
            "__pycache__", ".cache", "objects-Debug", "objects-RelWithDebInfo")]
        folder = destination / Path(current).relative_to(source)
        folder.mkdir(parents=True, exist_ok=True)
        for name in files:
            if name.endswith((".pyc", ".log", ".obj")) or name == "direct_url.json":
                continue
            pairs.append((Path(current) / name, folder / name))

    def copy(pair):
        origin, target = pair
        if target.exists():
            old, new = target.stat(), origin.stat()
            if old.st_size == new.st_size and old.st_mtime_ns == new.st_mtime_ns:
                return
        shutil.copy2(origin, target)

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(copy, pairs))


def stage_app(snapshot: Path, destination: Path) -> str:
    destination.mkdir(parents=True, exist_ok=True)
    for name in ("pyproject.toml", "uv.lock", ".python-version", "README.md", "LICENSE"):
        shutil.copy2(snapshot / name, destination / name)
    copy_tree(snapshot / "src", destination / "src")
    config = destination / "src" / "autokaraoke" / "config.py"
    content = config.read_text(encoding="utf-8")
    assert "BUILD_ENABLE_DOWNLOAD = True" in content
    config.write_text(content.replace("BUILD_ENABLE_DOWNLOAD = True", "BUILD_ENABLE_DOWNLOAD = False"), encoding="utf-8")
    lock = hashlib.sha256((destination / "uv.lock").read_bytes() + (destination / "pyproject.toml").read_bytes()).hexdigest()[:16]
    (destination / "LOCKHASH").write_text(lock, encoding="ascii")
    return lock


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--label", required=True)
    parser.add_argument("--existing-release", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, required=True, help="Existing AutoKaraoke runtime root, containing python/ and venv/")
    parser.add_argument("--models", type=Path, default=HERE / ".cache" / "offline-models")
    parser.add_argument("--separation-models", type=Path, default=Path.home() / ".cache" / "autokaraoke" / "models")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iscc", type=Path, required=True)
    parser.add_argument("--stage-only", action="store_true")
    parser.add_argument("--reuse-stage", action="store_true")
    parser.add_argument("--resume-staging", action="store_true", help="Resume an incomplete stage; never alter a completed manifest")
    options = parser.parse_args()
    if os.name != "nt":
        parser.error("Build this edition on Windows")
    if any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.-_" for c in options.label):
        parser.error("Label must be a filename-safe version string")
    output = options.output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    stage = output / "stage-offline"
    assets = output / "assets"
    assets.mkdir(exist_ok=True)
    if not options.reuse_stage:
        if stage.exists() and (not options.resume_staging or (stage / "package-manifest.json").exists()):
            raise RuntimeError("Stage already exists. Use a new output path or --reuse-stage; existing files are never overwritten.")
        git = ["git", "-c", "safe.directory=" + ROOT.as_posix()]
        commit = subprocess.check_output(git + ["rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
        snapshot = output / ("source-snapshot-" + commit[:12])
        archive = output / ("source-" + commit[:12] + ".tar")
        subprocess.run(git + ["archive", "--format=tar", "--output", str(archive), commit], cwd=ROOT, check=True)
        snapshot.mkdir(exist_ok=True)
        with tarfile.open(archive) as source:
            source.extractall(snapshot, filter="data")
        # Packaging helpers are required to be committed before creating a release.
        assert (snapshot / "packaging/windows/OfflineLauncher.cs").is_file()
        stage.mkdir(exist_ok=True)
        stage_app(snapshot, stage / "app")
        copy_tree(snapshot / "docs", stage / "app" / "docs")
        licenses = stage / "licenses"
        licenses.mkdir(exist_ok=True)
        shutil.copy2(snapshot / "LICENSE", licenses / "AutoKaraoke-LICENSE.txt")
        shutil.copy2(snapshot / "packaging/THIRD-PARTY.txt", licenses / "THIRD-PARTY.txt")
        shutil.copy2(options.existing_release / "licenses/ffmpeg-LICENSE.txt", licenses / "ffmpeg-LICENSE.txt")
        bin_dir = stage / "bin"
        bin_dir.mkdir(exist_ok=True)
        for name in ("ffmpeg.exe", "ffprobe.exe"):
            shutil.copy2(options.existing_release / "bin" / name, bin_dir / name)
        print("Copying the private, relocatable Python runtime and AI libraries...", flush=True)
        base = (options.runtime / "python/cpython-3.12-windows-x86_64-none").resolve()
        copy_tree(base, stage / "runtime/python")
        copy_tree(options.runtime / "venv/Lib/site-packages", stage / "runtime/python/Lib/site-packages")
        shutil.copy2(base / "LICENSE.txt", licenses / "Python-LICENSE.txt")
        print("Copying verified model snapshots (without user caches, locks or tokens)...", flush=True)
        manifest = json.loads((options.models / "model-manifest.json").read_text(encoding="utf-8"))
        assert {m["id"] for m in manifest["models"]} == {
            "Qwen/Qwen3-ASR-1.7B", "Qwen/Qwen3-ForcedAligner-0.6B", "MahmoudAshraf/mms-300m-1130-forced-aligner"}
        for model in manifest["models"]:
            relative = Path("huggingface/hub") / ("models--" + model["id"].replace("/", "--"))
            model_root = stage / "models" / relative
            snapshot_root = model_root / "snapshots" / model["revision"]
            snapshot_root.mkdir(parents=True, exist_ok=True)
            for record in model["files"]:
                source = options.models / relative / "snapshots" / model["revision"] / record["name"]
                if source.stat().st_size != record["bytes"] or digest(source) != record["sha256"]:
                    raise ValueError("Model verification failed: " + model["id"] + "/" + record["name"])
                destination = snapshot_root / record["name"]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
            (model_root / "refs").mkdir(exist_ok=True)
            (model_root / "refs/main").write_text(model["revision"], encoding="ascii")
        shutil.copy2(options.models / "model-manifest.json", stage / "models/model-manifest.json")
        copy_tree(options.models / "licenses", licenses / "models")
        separation = stage / "models/separation"
        separation.mkdir(exist_ok=True)
        for name in ("vocals_mel_band_roformer.ckpt", "vocals_mel_band_roformer.yaml", "download_checks.json"):
            shutil.copy2(options.separation_models / name, separation / name)
        shutil.copy2(snapshot / "packaging/windows/offline_check.py", stage / "app/offline_check.py")
        # Runtime DLLs remain replaceable; Qt and other dependency license files stay in dist-info.
        libraries = stage / "runtime/python/Lib/site-packages"
        distributions = sorted(importlib.metadata.distributions(path=[str(libraries)]), key=lambda p: p.metadata["Name"].lower())
        inventory = [{"name": d.metadata["Name"], "version": d.version,
                      "license": d.metadata.get("License-Expression") or d.metadata.get("License"),
                      "home_page": d.metadata.get("Home-page")} for d in distributions]
        (licenses / "python-packages.json").write_text(json.dumps(inventory, ensure_ascii=False, indent=2), encoding="utf-8")
        source_url = f"https://github.com/Dhz8678/AutoKaraoke/tree/{commit}"
        notes = f"""AutoKaraoke 中文离线完整版 {options.label}

原项目：https://github.com/GiacoBot/AutoKaraoke
原作者：GiacoBot 及其贡献者。中文增强仓库：https://github.com/Dhz8678/AutoKaraoke
对应源码：{source_url}
许可证：GPL-3.0-or-later，见 licenses；AI 模型及依赖分别遵循其许可证。
MMS 模型为 CC-BY-NC-4.0，仅限非商业用途。
This project uses the universal romanizer software 'uroman' written by Ulf Hermjakob, USC Information Sciences Institute (2015-2020).

安装包由 Setup.exe 与所有同名 .bin 分卷组成。全部下载到同一文件夹，再双击 Setup.exe。
安装时不需要联网。安装后运行 AutoKaraoke.exe，不需要另外安装 Python、AI 依赖或模型。
内含 MelBand RoFormer、Qwen3-ForcedAligner-0.6B、MMS-300M、Qwen3-ASR-1.7B。
本地音频分离、歌词识别与自动对齐使用内置模型。歌词在线检索与远程歌词修正 API 仍需网络。
也可自行导入 TXT/LRC 歌词，关闭远程歌词修正，全程处理本地歌曲。

Windows 10/11 x64；建议至少 16 GB 内存、25 GB 可用磁盘空间。
CUDA 版 PyTorch 可使用受支持的 NVIDIA 显卡，或退回 CPU；CPU 较慢。
配置与歌曲保存在用户目录，不包含打包电脑的密钥、接口地址、歌曲或个人配置。
原有 BAT 联网下载版独立发布，两种方式可以并存。
"""
        (stage / "README-离线版.txt").write_text(notes, encoding="utf-8")
        (licenses / "MODEL-SOURCES.txt").write_text(
            "MelBand RoFormer vocals by Kimberley Jensen\n"
            "https://github.com/nomadkaraoke/python-audio-separator/releases/tag/model-configs\n"
            "https://huggingface.co/KimberleyJSN/melbandroformer\n\n" +
            "\n".join(m["id"] + " | " + m["license"] + "\n" + m["source"] for m in manifest["models"]), encoding="utf-8")
        compiler = Path(os.environ.get("WINDIR", "C:/Windows")) / "Microsoft.NET/Framework64/v4.0.30319/csc.exe"
        subprocess.run([str(compiler), "/nologo", "/target:winexe", "/codepage:65001", "/optimize+",
                        "/reference:System.Windows.Forms.dll", "/out:" + str(stage / "AutoKaraoke.exe"),
                        str(snapshot / "packaging/windows/OfflineLauncher.cs")], check=True)
        records = []
        all_files = sorted(p for p in stage.rglob("*") if p.is_file())
        for index, file in enumerate(all_files):
            if index % 3000 == 0:
                print(f"Hashing offline files: {index}/{len(all_files)}", flush=True)
            records.append({"path": file.relative_to(stage).as_posix(), "bytes": file.stat().st_size, "sha256": digest(file)})
        (stage / "package-manifest.json").write_text(json.dumps({"build": options.label, "source_commit": commit,
            "edition": "offline", "files": records}, ensure_ascii=False, indent=2), encoding="utf-8")
        # Preserve the original BAT-based download-on-demand edition as a separate ZIP.
        online = output / f"AutoKaraoke-{options.label}-windows-x64-online"
        online.mkdir(exist_ok=True)
        stage_app(snapshot, online / "app")
        copy_tree(snapshot / "docs", online / "app/docs")
        (online / "bin").mkdir(exist_ok=True)
        for name in ("uv.exe", "ffmpeg.exe", "ffprobe.exe"):
            shutil.copy2(options.existing_release / "bin" / name, online / "bin" / name)
        copy_tree(licenses, online / "licenses")
        (online / "licenses/python-packages.json").unlink(missing_ok=True)
        for name in ("AutoKaraoke.bat", "README.txt"):
            shutil.copy2(snapshot / "packaging/windows" / name, online / name)
        shutil.copy2(snapshot / "docs/中文增强版说明.md", online / "中文增强版说明.md")
        (online / "images").mkdir(exist_ok=True)
        shutil.copy2(snapshot / "docs/images/zh-render-typography.png", online / "images/zh-render-typography.png")
        print("Compressing the BAT online edition...", flush=True)
        with zipfile.ZipFile(assets / (online.name + ".zip"), "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive_out:
            for file in online.rglob("*"):
                if file.is_file():
                    archive_out.write(file, file.relative_to(online.parent))
    if not options.stage_only:
        verified = json.loads((stage / "package-manifest.json").read_text(encoding="utf-8"))
        if verified["build"] != options.label or not (stage / "AutoKaraoke.exe").is_file():
            raise ValueError("Offline stage does not match the requested build")
        setup_name = f"AutoKaraoke-{options.label}-windows-x64-offline-Setup"
        if (assets / (setup_name + ".exe")).exists():
            raise RuntimeError("Installer already exists; do not overwrite delivered artifacts")
        subprocess.run([str(options.iscc.resolve()), "/DStageRoot=" + str(stage),
                        "/DBuildLabel=" + options.label, "/DOutputRoot=" + str(assets),
                        str(HERE / "windows/offline.iss")], check=True)
    print("Offline stage:", stage, flush=True)
    print("Release assets:", assets, flush=True)


if __name__ == "__main__":
    main()
