"""Do not choose a GPU that cannot hold both ASR and timestamp models."""
from types import SimpleNamespace
from unittest.mock import patch

import pytest


@pytest.mark.parametrize("available,gib,requested,expected", [
    (True, 4, None, "cpu"),
    (True, 12, None, "cuda:0"),
    (False, 0, None, "cpu"),
    (True, 4, "cpu", "cpu"),
])
def test_transcriber_uses_cpu_on_small_gpu(available, gib, requested, expected):
    from autokaraoke.asr import Transcriber
    with patch("torch.cuda.is_available", return_value=available), \
            patch("torch.cuda.get_device_properties", return_value=SimpleNamespace(total_memory=gib * 1024**3)), \
            patch("qwen_asr.Qwen3ASRModel.from_pretrained") as load:
        Transcriber(device=requested)
    assert load.call_args.kwargs["device_map"] == expected
    assert load.call_args.kwargs["forced_aligner_kwargs"]["device_map"] == expected
