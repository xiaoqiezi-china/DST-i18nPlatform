"""OpenAI-compatible chat client (DeepSeek by default)."""

from __future__ import annotations

import json
import socket
import ssl
import urllib.error
import urllib.request
from typing import Any

from . import jsonutil


class LlmError(Exception):
    pass


def normalize_base_url(base_url: str | None) -> str:
    url = (base_url or "").strip() or "https://api.deepseek.com/v1"
    url = url.rstrip("/")
    if not url.lower().endswith("/chat/completions"):
        url += "/chat/completions"
    return url


def chat(settings: dict[str, Any], system_prompt: str, user_prompt: str) -> str:
    api_key = str(settings.get("LlmApiKey") or "").strip()
    if not api_key:
        raise LlmError("尚未配置 LLM API Key，请在“设置”中填写 DeepSeek API Key。")

    endpoint = normalize_base_url(str(settings.get("LlmBaseUrl") or ""))
    payload = {
        "model": settings.get("LlmModel"),
        "temperature": settings.get("LlmTemperature"),
        "max_tokens": settings.get("LlmMaxTokens"),
        "stream": False,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
    }
    data = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=data,
        method="POST",
        headers={
            "Authorization": "Bearer " + api_key,
            "Content-Type": "application/json; charset=utf-8",
            "Accept": "application/json",
        },
    )
    timeout = max(10, min(600, int(settings.get("RequestTimeoutSeconds") or 120)))

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8-sig", errors="replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8-sig", errors="replace")
        raise LlmError(f"LLM 返回 HTTP {exc.code}: {truncate(body, 500)}") from exc
    except (socket.timeout, TimeoutError) as exc:
        raise LlmError("LLM 请求超时（" + str(timeout) + " 秒）。") from exc
    except urllib.error.URLError as exc:
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (socket.timeout, TimeoutError)):
            raise LlmError("LLM 请求超时（" + str(timeout) + " 秒）。") from exc
        raise LlmError("无法连接 LLM 服务: " + str(reason)) from exc
    except ssl.SSLError as exc:
        raise LlmError("无法连接 LLM 服务: " + str(exc)) from exc

    try:
        node = jsonutil.loads(body)
    except Exception as exc:  # noqa: BLE001
        raise LlmError("无法解析 LLM 响应: " + truncate(body, 300)) from exc
    content = None
    try:
        content = node["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError):
        content = None
    if not isinstance(content, str):
        raise LlmError("LLM 响应缺少 choices[0].message.content")
    return content


def test(settings: dict[str, Any]) -> tuple[bool, str]:
    try:
        reply = chat(settings, "You are a connectivity probe.", "Reply with the single word: ok")
        return True, "连接成功，模型 " + str(settings.get("LlmModel")) + " 回复: " + truncate(reply.strip(), 80)
    except Exception as exc:  # noqa: BLE001
        return False, str(exc)


def truncate(value: str, maximum: int) -> str:
    text = value or ""
    return text if len(text) <= maximum else text[:maximum] + "..."
