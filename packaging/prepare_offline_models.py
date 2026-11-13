"""Prepare pinned, checksum-verified model snapshots for the offline Windows build.

Run with the installed AI Python. Existing complete cache files are copied, never
modified. Downloads and manifests stay below packaging/.cache by default.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import threading
import time

os.environ.setdefault("HF_HUB_DISABLE_PROGRESS_BARS", "1")
os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
os.environ.setdefault("HF_HUB_DOWNLOAD_TIMEOUT", "60")


def retry(operation):
    for attempt in range(6):
        try:
            return operation()
        except Exception as exc:
            if attempt == 5:
                raise
            print(f"  Transient download failure ({type(exc).__name__}), retry {attempt + 1}/5", flush=True)
            time.sleep(min(2 ** attempt, 20))

MODELS = (
    ("Qwen/Qwen3-ASR-1.7B", "7278e1e70fe206f11671096ffdd38061171dd6e5", "Apache-2.0"),
    ("Qwen/Qwen3-ForcedAligner-0.6B", "c7cbfc2048c462b0d63a45797104fc9db3ad62b7", "Apache-2.0"),
    ("MahmoudAshraf/mms-300m-1130-forced-aligner", "49402e9577b1158620820667c218cd494cc44486", "CC-BY-NC-4.0"),
)


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def prepare(destination: Path, existing: Path) -> None:
    from huggingface_hub import HfApi, hf_hub_download

    destination.mkdir(parents=True, exist_ok=True)
    cache = destination / "huggingface" / "hub"
    manifest = {"models": []}
    for repo, revision, license_name in MODELS:
        print(f"Preparing {repo} @ {revision}", flush=True)
        info = retry(lambda: HfApi().model_info(repo, revision=revision, files_metadata=True, timeout=30))
        folder = "models--" + repo.replace("/", "--")
        snapshot = cache / folder / "snapshots" / revision
        snapshot.mkdir(parents=True, exist_ok=True)
        ref = cache / folder / "refs" / "main"
        ref.parent.mkdir(parents=True, exist_ok=True)
        ref.write_text(revision, encoding="ascii")
        item = {"id": repo, "revision": revision, "license": license_name,
                "source": f"https://huggingface.co/{repo}/tree/{revision}", "files": []}
        for entry in info.siblings:
            name = entry.rfilename
            if not name.endswith((".json", ".txt", ".safetensors", ".md")):
                continue
            target = snapshot / name
            source = existing / folder / "snapshots" / revision / name
            if not target.exists() and source.is_file() and source.stat().st_size == entry.size:
                print(f"  Reusing complete cached file: {name}", flush=True)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
            if not target.is_file() or target.stat().st_size != entry.size:
                stopped = threading.Event()

                def heartbeat():
                    while not stopped.wait(10):
                        parts = list((cache / folder / "blobs").glob("*.incomplete"))
                        count = max((p.stat().st_size for p in parts if p.exists()), default=0)
                        print(f"  Downloading {name}: {count / 2**20:.1f} MiB / {entry.size / 2**20:.1f} MiB", flush=True)

                worker = threading.Thread(target=heartbeat, daemon=True)
                worker.start()
                try:
                    downloaded = retry(lambda: hf_hub_download(repo, name, revision=revision, cache_dir=cache))
                    if Path(downloaded).resolve() != target.resolve():
                        shutil.copy2(downloaded, target)
                finally:
                    stopped.set()
                    worker.join()
            if target.stat().st_size != entry.size:
                raise ValueError(f"Model file size mismatch: {repo}/{name}")
            digest = sha256(target)
            if entry.lfs and digest != entry.lfs.sha256:
                raise ValueError(f"Model SHA256 mismatch: {repo}/{name}")
            item["files"].append({"name": name, "bytes": entry.size, "sha256": digest})
            print(f"  Verified {name} ({entry.size} bytes)", flush=True)
        manifest["models"].append(item)
        (destination / "model-manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def prepare_licenses(destination: Path) -> None:
    import requests
    folder = destination / "licenses"
    folder.mkdir(parents=True, exist_ok=True)
    sources = {
        "Qwen-Apache-2.0.txt": "https://raw.githubusercontent.com/QwenLM/Qwen3-ASR/main/LICENSE",
        "MMS-CC-BY-NC-4.0.txt": "https://creativecommons.org/licenses/by-nc/4.0/legalcode.txt",
        "MelBand-model-card.md": "https://huggingface.co/KimberleyJSN/melbandroformer/raw/main/README.md",
    }
    for name, url in sources.items():
        target = folder / name
        if not target.exists():
            def fetch():
                response = requests.get(url, timeout=30)
                response.raise_for_status()
                if response.text.lstrip().startswith("<!DOCTYPE"):
                    raise ValueError("Expected license text, got HTML")
                return response.content
            target.write_bytes(retry(fetch))
        print("Retained model license:", name, flush=True)
    (folder / "SOURCES.json").write_text(json.dumps(sources, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--destination", type=Path, default=Path(__file__).resolve().parent / ".cache" / "offline-models")
    parser.add_argument("--existing-cache", type=Path, default=Path.home() / ".cache" / "huggingface" / "hub")
    options = parser.parse_args()
    prepare(options.destination.resolve(), options.existing_cache.resolve())
    prepare_licenses(options.destination.resolve())


if __name__ == "__main__":
    main()
