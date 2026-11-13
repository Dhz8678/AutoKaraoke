"""Configurable Chat Completions client returning JSON; OpenRouter by default."""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from urllib.parse import urlsplit

import httpx

from autokaraoke import config

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
        raise LlmError("模型未返回有效的 JSON 对象")
    return json.loads(text[start : end + 1])


def chat_json(system: str, user: str, model: str | None = None, reasoning: bool = False,
              retries: int = 2, timeout: float = 180.0, *, base_url: str | None = None,
              api_key: str | None = None, max_tokens: int | None = None) -> LlmReply:
    """``reasoning=False`` turns thinking off: for lyric editing it is ~20x faster and
    cheaper with nearly the same result."""
    try:
        url = config.llm_chat_url(base_url)
    except ValueError as exc:
        raise LlmError(str(exc)) from exc
    key = config.llm_api_key() if api_key is None else api_key
    openrouter = urlsplit(url).hostname == "openrouter.ai"
    if openrouter and not key:
        raise LlmError("请在设置中填写 API 密钥（OpenRouter 需要密钥）。")
    body = {
        "model": model or config.OPENROUTER_MODEL,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
    }
    headers = {
        "User-Agent": config.USER_AGENT,
    }
    if max_tokens is not None:
        body["max_tokens"] = max_tokens
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if openrouter:
        body.update({"usage": {"include": True}, "reasoning": {"enabled": reasoning}})
        headers["X-Title"] = "AutoKaraoke"
    last: Exception | None = None
    for attempt in range(retries + 1):
        try:
            r = httpx.post(url, json=body, headers=headers, timeout=timeout)
            if r.status_code >= 500 or r.status_code == 429:
                raise LlmError(f"模型 API HTTP {r.status_code}: {r.text[:200]}")
            if r.status_code >= 400:
                # Client errors (bad key, no credit, unknown model) will not improve on retry.
                raise LlmError(f"模型 API HTTP {r.status_code}: {r.text[:300]}") from None
            payload = r.json()
            if "error" in payload:
                raise LlmError(str(payload["error"])[:300])
            try:
                content = payload["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LlmError("接口已响应，但返回格式不兼容 Chat Completions。") from exc
            if not isinstance(content, str) or not content.strip():
                raise LlmError("接口已响应，但模型没有返回文本正文；请检查模型和生成参数。")
            usage = payload.get("usage") or {}
            return LlmReply(_parse_json(content), payload.get("model", body["model"]), usage.get("cost"))
        except (httpx.HTTPError, LlmError, json.JSONDecodeError, KeyError) as e:
            last = e
            if isinstance(e, LlmError) and "HTTP 4" in str(e) and "HTTP 429" not in str(e):
                break
            if attempt < retries:
                time.sleep(2 * (attempt + 1))
    raise LlmError(str(last) or type(last).__name__) from last


@dataclass(frozen=True)
class ConnectionTest:
    success: bool
    elapsed: float
    message: str


def test_connection(base_url: str, api_key: str, model: str) -> ConnectionTest:
    """Send one short synthetic request using an unsaved settings snapshot."""
    start = time.monotonic()
    try:
        if not model.strip():
            raise LlmError("请填写歌词修正模型名称。")
        reply = chat_json(
            'Return exactly this JSON object: {"ok": true}. Do not add explanation.',
            'API 连接测试，请仅返回 {"ok": true}。', model=model.strip(),
            retries=0, timeout=30.0, base_url=base_url, api_key=api_key, max_tokens=128)
        if reply.data.get("ok") is not True:
            raise LlmError('接口已响应，但模型未按要求返回 {"ok": true}，请检查 JSON 输出能力。')
        message = f"测试成功：模型 {reply.model} 可正常返回 JSON。"
        success = True
    except httpx.TimeoutException:
        message = "测试超时：请检查服务响应，或等待本地模型完成加载后重试。"
        success = False
    except Exception as exc:
        cause = exc.__cause__ or exc
        if isinstance(cause, httpx.TimeoutException):
            message = "测试超时：请检查服务响应，或等待本地模型完成加载后重试。"
        elif isinstance(cause, httpx.ConnectError):
            message = f"连接失败，请检查 API 地址、端口及服务是否已启动：{cause}"
        else:
            message = f"测试失败：{exc}"
        success = False
    # Server diagnostics occasionally echo credentials; never show those in UI/logs.
    if api_key:
        message = message.replace(api_key, "[密钥已隐藏]")
    return ConnectionTest(success, time.monotonic() - start, message[:600])
