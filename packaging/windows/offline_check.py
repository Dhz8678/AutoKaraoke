"""Offline release acceptance, using a fresh config and synthetic audio only."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import socket
import sys
import tempfile
import time


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--inference", choices=("align", "mms", "asr", "separation"))
    options = parser.parse_args()
    if options.inference == "separation":
        os.environ["CUDA_VISIBLE_DEVICES"] = "-1"  # Do not compete with an open editor for GPU memory.
    started = time.monotonic()
    root = Path(os.environ["AUTOKARAOKE_OFFLINE_ROOT"])
    output = Path(tempfile.mkdtemp(prefix="autokaraoke-offline-check-", dir=options.report.parent))
    os.environ["APPDATA"] = str(output / "config")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    for key in list(os.environ):
        if key.startswith("AUTOKARAOKE_LLM_") or key in ("OPENROUTER_API_KEY", "AUTOKARAOKE_EXPERIMENTAL_LLM"):
            os.environ.pop(key, None)

    def no_network(self, address):
        raise RuntimeError("Offline acceptance blocked a network connection")

    socket.socket.connect = no_network
    result = {"network_blocked": True, "external_api_used": False, "real_song_used": False}
    try:
        import numpy as np
        import soundfile as sf
        import torch
        from autokaraoke import components, config
        from huggingface_hub import hf_hub_download
        torch.set_num_threads(4)
        result["python"] = sys.version.split()[0]
        result["torch"] = torch.__version__
        result["cuda_available"] = torch.cuda.is_available()
        assert Path(sys.executable).is_relative_to(root), "Using a system Python"
        assert components.state() == "ok", "AI dependencies are missing"
        assert not config.download_enabled(), "Streaming download must remain disabled"
        assert config.MODELS_DIR == root / "models" / "separation"
        manifest = json.loads((root / "models" / "model-manifest.json").read_text(encoding="utf-8"))
        result["models"] = []
        for model in manifest["models"]:
            for file in model["files"]:
                path = Path(hf_hub_download(model["id"], file["name"], revision=model["revision"], local_files_only=True))
                assert path.is_relative_to(root), "Using a model from the user's cache"
                assert path.stat().st_size == file["bytes"]
            result["models"].append(model["id"])
        from PySide6.QtCore import QSettings
        from PySide6.QtWidgets import QApplication
        from autokaraoke.gui.app import install_chinese_translation
        from autokaraoke.gui.main_window import MainWindow
        app = QApplication([])
        QSettings.setDefaultFormat(QSettings.Format.IniFormat)
        QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(output / "qt-settings"))
        install_chinese_translation(app)
        window = MainWindow()
        window.show()
        app.processEvents()
        assert window.act_new.isEnabled()
        window.grab().save(str(output / "offline-editor.png"))
        window.close()
        result["gui"] = "passed"
        if options.inference:
            print("Running synthetic offline inference:", options.inference, flush=True)
            if options.inference == "separation":
                from autokaraoke.separation import separate
                wave = np.zeros((44100, 2), dtype=np.float32)
                audio = output / "synthetic.wav"
                sf.write(audio, wave, 44100)
                vocals, music = separate(audio, output / "stems")
                assert sf.info(vocals).frames > 0 and sf.info(music).frames > 0
            elif options.inference == "asr":
                from autokaraoke.asr import Transcriber
                transcriber = Transcriber(device="cpu")
                audio = output / "synthetic.wav"
                sf.write(audio, np.zeros(16000, dtype=np.float32), 16000)
                transcript = transcriber.transcribe(np.zeros(16000, dtype=np.float32), language="zh")
                result["transcript_type"] = type(transcript).__name__
            else:
                from autokaraoke.align.base import Segment
                if options.inference == "align":
                    from autokaraoke.align.qwen import QwenAligner
                    aligner = QwenAligner(device="cpu")
                else:
                    from autokaraoke.align.ctc import CtcAligner
                    aligner = CtcAligner(device="cpu")
                segment = Segment(start=0, audio=np.zeros(16000, dtype=np.float32), units=["你", "好"])
                timings = aligner.align([segment])
                assert len(timings) == 1 and len(timings[0]) == 2
            result["synthetic_inference"] = options.inference
        result["success"] = True
    except Exception:
        import traceback
        result["success"] = False
        result["error"] = traceback.format_exc()
    result["elapsed_seconds"] = round(time.monotonic() - started, 2)
    options.report.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
