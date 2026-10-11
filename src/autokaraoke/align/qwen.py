"""Qwen3-ForcedAligner-0.6B adapter.

The model predicts a start and end timestamp token after every text unit. We
build the prompt ourselves (instead of using the package tokenizer) so that the
units are exactly our syllables, in any language.
"""

from __future__ import annotations

import unicodedata

import torch

from autokaraoke.align.base import SAMPLE_RATE, Segment, Timing

MODEL_ID = "Qwen/Qwen3-ForcedAligner-0.6B"
# The model accepts up to 300 s of audio per sample.
MAX_SECONDS = 290.0
BATCH_SIZE = 8


def _clean(unit: str) -> str:
    """Keep what the aligner was trained on: letters, digits and apostrophes."""
    return "".join(
        ch for ch in unit if ch == "'" or unicodedata.category(ch)[0] in "LN"
    )


class QwenAligner:
    name = "qwen"

    def __init__(self, device: str | None = None):
        from qwen_asr import Qwen3ForcedAligner

        device = device or ("cuda:0" if torch.cuda.is_available() else "cpu")
        dtype = torch.bfloat16 if device.startswith("cuda") else torch.float32
        self._wrapper = Qwen3ForcedAligner.from_pretrained(MODEL_ID, dtype=dtype, device_map=device)
        self._fix = self._wrapper.aligner_processor.fix_timestamp

    def _align_batch(self, segments: list[Segment]) -> list[list[Timing]]:
        w = self._wrapper
        prompts, kept = [], []
        for seg in segments:
            idx = [i for i, u in enumerate(seg.units) if _clean(u)]
            kept.append(idx)
            body = "".join(_clean(seg.units[i]) + "<timestamp><timestamp>" for i in idx)
            prompts.append("<|audio_start|><|audio_pad|><|audio_end|>" + body)

        inputs = w.processor(
            text=prompts, audio=[s.audio for s in segments], return_tensors="pt", padding=True
        )
        inputs = inputs.to(w.model.device).to(w.model.dtype)
        with torch.inference_mode():
            output_ids = w.model.thinker(**inputs).logits.argmax(dim=-1)

        results: list[list[Timing]] = []
        for seg, idx, in_ids, out_ids in zip(segments, kept, inputs["input_ids"], output_ids):
            ticks = out_ids[in_ids == w.timestamp_token_id].float() * w.timestamp_segment_time
            ms = self._fix(ticks.cpu().numpy()) if len(ticks) else []
            timings = [None] * len(seg.units)
            for k, i in enumerate(idx):
                timings[i] = Timing(seg.start + ms[2 * k] / 1000, seg.start + ms[2 * k + 1] / 1000)
            results.append(_fill_missing(timings, seg))
        return results

    def align(self, segments: list[Segment]) -> list[list[Timing]]:
        for s in segments:
            if len(s.audio) / SAMPLE_RATE > MAX_SECONDS:
                raise ValueError("segment longer than the aligner limit")
        out: list[list[Timing]] = []
        for i in range(0, len(segments), BATCH_SIZE):
            out.extend(self._align_batch(segments[i : i + BATCH_SIZE]))
        return out


def _fill_missing(timings: list, seg: Segment) -> list[Timing]:
    """Units with no letters (e.g. a lone dash) get a zero-length slot at the previous end."""
    prev = seg.start
    for i, t in enumerate(timings):
        if t is None:
            timings[i] = Timing(prev, prev)
        else:
            prev = t.end
    return timings
