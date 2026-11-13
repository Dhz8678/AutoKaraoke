"""Build the release packages: a Linux AppImage and a Windows zip.

    uv run python packaging/build.py            # both
    uv run python packaging/build.py linux      # or: windows

Runs on Linux (both targets). The packages are small launchers: they contain the
application source, ffmpeg and uv. On first start uv installs Python and the editor's
libraries; the AI components (~4 GB, PyTorch with CUDA) are downloaded from inside the
app when first needed. Downloading audio from streaming services is disabled in these
builds. Output goes to dist/.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import stat
import subprocess
import sys
import tarfile
import tomllib
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = ROOT / "packaging"
CACHE = HERE / ".cache"
DIST = ROOT / "dist"

UV_VERSION = "0.13.0"
UV_URL = "https://github.com/astral-sh/uv/releases/download/{v}/{name}"
FFMPEG_URL = "https://github.com/BtbN/FFmpeg-Builds/releases/download/latest/{name}"
FFMPEG = {"linux": "ffmpeg-n8.1-latest-linux64-gpl-8.1.tar.xz",
          "windows": "ffmpeg-n8.1-latest-win64-gpl-8.1.zip"}
UV = {"linux": "uv-x86_64-unknown-linux-gnu.tar.gz", "windows": "uv-x86_64-pc-windows-msvc.zip"}
APPIMAGETOOL = ("https://github.com/AppImage/appimagetool/releases/download/continuous/"
                "appimagetool-x86_64.AppImage")


def version() -> str:
    return tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]["version"]


def fetch(url: str, name: str | None = None) -> Path:
    CACHE.mkdir(parents=True, exist_ok=True)
    out = CACHE / (name or url.rsplit("/", 1)[1])
    if not out.exists():
        print(f"  downloading {url}")
        tmp = out.with_suffix(out.suffix + ".part")
        with urllib.request.urlopen(url) as r, open(tmp, "wb") as f:
            shutil.copyfileobj(r, f)
        tmp.replace(out)
    return out


def extract_members(archive: Path, wanted: dict[str, Path]) -> None:
    """Copy archive members whose file name is a key of ``wanted`` to the mapped path."""
    found = set()
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as z:
            for info in z.infolist():
                base = info.filename.rsplit("/", 1)[-1]
                if base in wanted and not info.is_dir():
                    wanted[base].parent.mkdir(parents=True, exist_ok=True)
                    wanted[base].write_bytes(z.read(info))
                    found.add(base)
    else:
        with tarfile.open(archive) as t:
            for m in t.getmembers():
                base = m.name.rsplit("/", 1)[-1]
                if base in wanted and m.isfile():
                    wanted[base].parent.mkdir(parents=True, exist_ok=True)
                    wanted[base].write_bytes(t.extractfile(m).read())
                    wanted[base].chmod(0o755)
                    found.add(base)
    missing = set(wanted) - found
    if missing:
        raise RuntimeError(f"{archive.name}: missing {sorted(missing)}")


def stage_app(dest: Path) -> str:
    """Copy the project (source, lock file) with downloads disabled; return the lock hash."""
    dest.mkdir(parents=True)
    for name in ("pyproject.toml", "uv.lock", ".python-version", "README.md", "LICENSE"):
        shutil.copy2(ROOT / name, dest / name)
    shutil.copytree(ROOT / "src", dest / "src", ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
    config = dest / "src" / "autokaraoke" / "config.py"
    text = config.read_text()
    if "BUILD_ENABLE_DOWNLOAD = True" not in text:
        raise RuntimeError("BUILD_ENABLE_DOWNLOAD flag not found in config.py")
    config.write_text(text.replace("BUILD_ENABLE_DOWNLOAD = True", "BUILD_ENABLE_DOWNLOAD = False"))
    digest = hashlib.sha256((ROOT / "uv.lock").read_bytes() + (ROOT / "pyproject.toml").read_bytes())
    lock_hash = digest.hexdigest()[:16]
    (dest / "LOCKHASH").write_text(lock_hash)  # no newline: read by "set /p" on Windows
    return lock_hash


def licenses(dest: Path, ffmpeg_archive: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    shutil.copy2(ROOT / "LICENSE", dest / "AutoKaraoke-LICENSE.txt")
    shutil.copy2(HERE / "THIRD-PARTY.txt", dest / "THIRD-PARTY.txt")
    extract_members(ffmpeg_archive, {"LICENSE.txt": dest / "ffmpeg-LICENSE.txt"})


def square_icon(src: Path, dest: Path, size: int = 512) -> None:
    """Desktop icons must be square: pad with transparency and scale."""
    from PIL import Image

    img = Image.open(src).convert("RGBA")
    side = max(img.size)
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(img, ((side - img.width) // 2, (side - img.height) // 2))
    canvas.resize((size, size), Image.LANCZOS).save(dest)


def build_linux(work: Path) -> Path:
    appdir = work / "AutoKaraoke.AppDir"
    stage_app(appdir / "app")
    bin_dir = appdir / "usr" / "bin"
    ffmpeg = fetch(FFMPEG_URL.format(name=FFMPEG["linux"]))
    extract_members(ffmpeg, {"ffmpeg": bin_dir / "ffmpeg", "ffprobe": bin_dir / "ffprobe"})
    extract_members(fetch(UV_URL.format(v=UV_VERSION, name=UV["linux"]), f"{UV_VERSION}-{UV['linux']}"),
                    {"uv": bin_dir / "uv"})
    licenses(appdir / "usr" / "share" / "licenses", ffmpeg)
    shutil.copy2(HERE / "linux" / "AppRun", appdir / "AppRun")
    (appdir / "AppRun").chmod(0o755)
    shutil.copy2(HERE / "linux" / "autokaraoke.desktop", appdir / "autokaraoke.desktop")
    square_icon(ROOT / "src" / "autokaraoke" / "gui" / "assets" / "icon.png", appdir / "autokaraoke.png")

    tool = fetch(APPIMAGETOOL)
    tool.chmod(tool.stat().st_mode | stat.S_IEXEC)
    out = DIST / f"AutoKaraoke-{version()}-x86_64.AppImage"
    env = {"ARCH": "x86_64", "APPIMAGE_EXTRACT_AND_RUN": "1", "PATH": "/usr/bin:/bin"}
    subprocess.run([str(tool), "--no-appstream", str(appdir), str(out)], check=True, env=env)
    return out


def build_windows(work: Path) -> Path:
    name = f"AutoKaraoke-{version()}-windows-x64"
    root = work / name
    stage_app(root / "app")
    ffmpeg = fetch(FFMPEG_URL.format(name=FFMPEG["windows"]))
    extract_members(ffmpeg, {"ffmpeg.exe": root / "bin" / "ffmpeg.exe",
                             "ffprobe.exe": root / "bin" / "ffprobe.exe"})
    extract_members(fetch(UV_URL.format(v=UV_VERSION, name=UV["windows"]), f"{UV_VERSION}-{UV['windows']}"),
                    {"uv.exe": root / "bin" / "uv.exe"})
    licenses(root / "licenses", ffmpeg)
    for f in (HERE / "windows").iterdir():
        shutil.copy2(f, root / f.name)
    shutil.copy2(ROOT / "docs" / "中文增强版说明.md", root / "中文增强版说明.md")
    (root / "images").mkdir(exist_ok=True)
    shutil.copy2(ROOT / "docs" / "images" / "zh-render-typography.png",
                 root / "images" / "zh-render-typography.png")
    out = DIST / f"{name}.zip"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for f in sorted(root.rglob("*")):
            z.write(f, f.relative_to(work))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("targets", nargs="*", choices=["linux", "windows"], help="default: both")
    args = ap.parse_args()
    targets = args.targets or ["linux", "windows"]
    if sys.platform != "linux":
        sys.exit("Build on Linux (WSL works too).")
    if not (ROOT / "LICENSE").exists():
        sys.exit("LICENSE missing")
    DIST.mkdir(exist_ok=True)
    work = DIST / "build"
    for target in targets:
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True)
        print(f"Building {target}")
        out = (build_linux if target == "linux" else build_windows)(work)
        print(f"  -> {out.relative_to(ROOT)} ({out.stat().st_size / 1e6:.0f} MB)")
    shutil.rmtree(work, ignore_errors=True)


if __name__ == "__main__":
    main()
