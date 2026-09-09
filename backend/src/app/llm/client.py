"""LLM 统一客户端（OpenAI Chat 兼容，含 Ollama 本地）。

Ollama 本地无需 api_key：base_url=http://localhost:11434/v1，走同 /chat/completions。
"""
from __future__ import annotations

import time
from typing import Any

import httpx

DEFAULT_URLS: dict[str, str] = {
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "moonshot": "https://api.moonshot.cn/v1",
    "anthropic": "https://api.anthropic.com",
    "ollama": "http://localhost:11434/v1",
}


def _base_url(provider_row: dict[str, Any]) -> str:
    url = (provider_row.get("base_url") or DEFAULT_URLS.get(provider_row.get("provider") or "", "")).strip().rstrip("/")
    if not url:
        raise ValueError("provider 缺少 base_url")
    return url


_THINKING_MARKERS = ("qwen", "qwq", "deepseek-r1", "deepseek-reasoner", "reasoner", "thinking")


def thinking_disable_body(provider_row: dict[str, Any] | None) -> dict[str, Any] | None:
    """按供应商返回“关闭思维/推理模式”的请求体片段。

    - 优先看模型设置里的手动开关 disable_thinking；
    - 兼容旧逻辑：Ollama/自定义本地服务上、名字明显是思考模型的（qwen/qwq/r1 等）默认也关闭；
    - OpenAI/Anthropic 官方接口不支持关闭思考，即便勾选也返回 None（请换非思考模型）。
    """
    if not provider_row:
        return None
    prov = str(provider_row.get("provider") or "").lower()
    model = str(provider_row.get("model") or "").lower()
    want = bool(provider_row.get("disable_thinking"))
    if not want and prov in {"ollama", "custom"} and any(m in model for m in _THINKING_MARKERS):
        want = True
    if not want:
        return None
    if prov == "ollama":
        return {"chat_template_kwargs": {"enable_thinking": False}}
    if prov == "custom" and any(m in model for m in ("qwen", "qwq")):
        return {"enable_thinking": False}  # DashScope / OpenAI 兼容的 Qwen
    if prov in {"openai", "anthropic"}:
        return None
    # DeepSeek v4 / Moonshot Kimi K2-Thinking / GLM / MiniMax 等 OpenAI 兼容网关
    return {"thinking": {"type": "disabled"}}


async def llm_chat(
    provider_row: dict[str, Any],
    messages: list[dict[str, str]],
    temperature: float | None = None,
    max_tokens: int | None = None,
    timeout_s: float = 90.0,
    extra_body: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """调用一次 chat/completions，返回 content/usage/latency/model。"""
    url = f"{_base_url(provider_row)}/chat/completions"
    api_key = provider_row.get("api_key")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    body: dict[str, Any] = {
        "model": provider_row["model"],
        "messages": messages,
        "temperature": temperature if temperature is not None else float(provider_row.get("temperature") or 0.2),
        "max_tokens": int(max_tokens if max_tokens is not None else (provider_row.get("max_tokens") or 2000)),
    }
    thinking = thinking_disable_body(provider_row)
    if thinking:
        body.update(thinking)
    if extra_body:
        body.update(extra_body)
    started = time.time()
    async with httpx.AsyncClient(timeout=timeout_s) as client:
        resp = await client.post(url, json=body, headers=headers)
    latency_ms = int((time.time() - started) * 1000)
    if resp.status_code != 200:
        raise RuntimeError(f"http_{resp.status_code}: {resp.text[:400]}")

    data = resp.json()
    choices = data.get("choices") or []
    msg = choices[0].get("message", {}) if choices else {}
    content = msg.get("content", "") or ""
    reasoning = msg.get("reasoning_content", "") or msg.get("reasoning", "") or ""
    usage = data.get("usage") or {}
    return {
        "content": content,
        "reasoning_content": reasoning,
        "model": data.get("model") or provider_row["model"],
        "usage": {
            "prompt_tokens": usage.get("prompt_tokens"),
            "completion_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        },
        "latency_ms": latency_ms,
    }
