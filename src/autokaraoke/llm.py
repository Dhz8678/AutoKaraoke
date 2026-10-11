"""Minimal OpenRouter chat client (OpenAI-compatible API) returning JSON."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass

import httpx

from autokaraoke import config

URL = "https://openrouter.ai/api/v1/chat/completions"


class LlmError(RuntimeError):
    pass


@dataclass
class LlmReply:
    data: dict
    model: str
    cost: float | None  # USD, as reported by OpenRouter


def _parse_json(text: str) -> dict:
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < start:
        raise LlmError("the model did not return JSON")
    return json.loads(text[start : end + 1])


def chat_json(system: str, user: str, model: str | None = None, reasoning: bool = False,
              retries: int = 2, timeout: float = 180.0) -> LlmReply:
    """``reasoning=False`` turns thinking off: for lyric editing it is ~20x faster and
    cheaper with nearly the same result."""
    key = config.openrouter_key()
    if not key:
        raise LlmError("No OpenRouter API key: set OPENROUTER_API_KEY (environment or .env file)")
    body = {
        "model": model or config.OPENROUTER_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "usage": {"include": True},
        "reasoning": {"enabled": reasoning},
    }
    headers = {
        "Authorization": f"Bearer {key}",
        "X-Title": "AutoKaraoke",
        "User-Agent": config.USER_AGENT,
    }
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = httpx.post(URL, json=body, headers=headers, timeout=timeout)
            if r.status_code >= 500 or r.status_code == 429:
                raise LlmError(f"OpenRouter HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code >= 400:
                # Client errors (bad key, no credit, unknown model) will not improve on retry.
                raise LlmError(f"OpenRouter HTTP {r.status_code}: {r.text[:300]}") from None
            payload = r.json()
            if "error" in payload:
                raise LlmError(str(payload["error"])[:300])
            content = payload["choices"][0]["message"]["content"] or ""
            usage = payload.get("usage") or {}
            return LlmReply(_parse_json(content), payload.get("model", body["model"]), usage.get("cost"))
        except (httpx.HTTPError, LlmError, json.JSONDecodeError, KeyError) as e:
            last = e
            if isinstance(e, LlmError) and "HTTP 4" in str(e) and "HTTP 429" not in str(e):
                break
            time.sleep(2 * (attempt + 1))
    raise LlmError(str(last))
