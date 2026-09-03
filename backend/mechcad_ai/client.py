from __future__ import annotations

"""OpenAI / Anthropic compatible HTTP client, extracted from the legacy Gradio MVP.

Supports two wire protocols:
- openai:    POST {base_url}/chat/completions  (Authorization: Bearer)
- anthropic: POST {base_url}/v1/messages        (X-Api-Key + anthropic-version)

Both retry a trailing `/v1` path when the configured base url does not already
end with it, because several Chinese providers expose both forms.
"""

import json
import os
import time
from dataclasses import dataclass, field
from typing import Any

import requests

DEFAULT_TIMEOUT = 90
DEFAULT_MAX_RETRIES = 1

# Role-specific env vars, all optional. They mirror backend.schemas.ModelConfig.
ENV_ROLE_MAP = {
    "vision": {
        "api_key": "MECHCAD_VISION_API_KEY",
        "base_url": "MECHCAD_VISION_BASE_URL",
        "model": "MECHCAD_VISION_MODEL",
        "protocol": "MECHCAD_VISION_PROTOCOL",
        "timeout": "MECHCAD_VISION_TIMEOUT",
        "max_retries": "MECHCAD_VISION_MAX_RETRIES",
    },
    "planner": {
        "api_key": "MECHCAD_PLANNER_API_KEY",
        "base_url": "MECHCAD_PLANNER_BASE_URL",
        "model": "MECHCAD_PLANNER_MODEL",
        "protocol": "MECHCAD_PLANNER_PROTOCOL",
        "timeout": "MECHCAD_PLANNER_TIMEOUT",
        "max_retries": "MECHCAD_PLANNER_MAX_RETRIES",
    },
}


class ApiCallError(RuntimeError):
    """Raised when the external model call fails at the HTTP/parse level."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


def _env_int(name: str, default: int) -> int:
    try:
        return max(0, int(os.getenv(name, str(default))))
    except (TypeError, ValueError):
        return default


def _role_timeout(role: str) -> int:
    return _env_int(ENV_ROLE_MAP[role]["timeout"], DEFAULT_TIMEOUT)


def _role_max_retries(role: str) -> int:
    return _env_int(ENV_ROLE_MAP[role]["max_retries"], DEFAULT_MAX_RETRIES)


def resolve_role_config(settings, role: str) -> dict[str, str]:
    """Resolve api_key/base_url/model/protocol for a role from ModelConfig then env."""
    env = ENV_ROLE_MAP[role]
    protocol = str(getattr(settings, f"{role}_protocol", "") or "").strip().lower()
    base_url = str(getattr(settings, f"{role}_base_url", "") or "").strip().rstrip("/")
    api_key = str(getattr(settings, f"{role}_api_key", "") or "").strip()
    model = str(getattr(settings, f"{role}_model", "") or "").strip()

    if not api_key:
        api_key = os.getenv(env["api_key"], "").strip()
    if not base_url:
        base_url = os.getenv(env["base_url"], "").strip().rstrip("/")
    if not model:
        model = os.getenv(env["model"], "").strip()
    if not protocol:
        protocol = os.getenv(env["protocol"], "").strip().lower()

    if not protocol:
        protocol = "anthropic" if "/anthropic" in base_url else "openai"
    return {
        "api_key": api_key,
        "base_url": base_url,
        "model": model,
        "protocol": protocol,
    }


def has_configured_model(settings, role: str) -> bool:
    config = resolve_role_config(settings, role)
    return bool(config["api_key"] and config["base_url"] and config["model"])


def test_model_connection(settings, role: str, language: str = "zh") -> dict[str, Any]:
    """Make a minimal text-only request and return safe diagnostics for the UI."""
    def msg(zh: str, en: str) -> str:
        return en if language == "en" else zh

    if role not in ENV_ROLE_MAP:
        raise ValueError(f"unsupported model role: {role}")
    config = resolve_role_config(settings, role)
    used_env_fallback = any(
        not str(getattr(settings, f"{role}_{key}", "") or "").strip()
        for key in ("api_key", "base_url", "model", "protocol")
    )
    if not config["api_key"] or not config["base_url"] or not config["model"]:
        return {
            "ok": False,
            "message": msg("配置不完整：请填写 API Key、Base URL 和模型名称，或确认 .env 已配置。", "Configuration is incomplete: provide API Key, Base URL, and model name, or confirm .env is configured."),
            "endpoint": None,
            "status_code": None,
            "content_type": None,
            "used_env_fallback": used_env_fallback,
        }
    messages = [{"role": "user", "content": msg("只回复 OK，不要输出其他内容。", "Reply with OK only and nothing else.")}]
    if config["protocol"] == "anthropic":
        urls = [f"{config['base_url']}/v1/messages"]
    else:
        urls = [f"{config['base_url']}/chat/completions"]
        if not config["base_url"].endswith("/v1"):
            urls.append(f"{config['base_url']}/v1/chat/completions")
    last: dict[str, Any] = {}
    for url in urls:
        try:
            if config["protocol"] == "anthropic":
                response = _post_anthropic_test(config, url, messages)
            else:
                response = _post_openai_test(config, url, messages)
        except requests.RequestException as exc:
            last = {"message": msg(f"网络不可达：{exc}", f"Network unreachable: {exc}"), "endpoint": url}
            continue
        content_type = response.headers.get("content-type", "")
        last = {
            "endpoint": url,
            "status_code": response.status_code,
            "content_type": content_type,
        }
        if response.ok:
            if "json" not in content_type.lower():
                last["message"] = msg("服务返回成功但不是 JSON，Base URL 可能指向网页地址。", "Service returned success but not JSON; Base URL may point to a website page.")
                continue
            try:
                data = response.json()
                if (config["protocol"] == "anthropic" and data.get("content")) or (
                    config["protocol"] != "anthropic" and data.get("choices")
                ):
                    last["ok"] = True
                    last["message"] = msg("连接成功：最小文本请求已返回。", "Connection successful: the minimal text request returned.")
                    return last | {"used_env_fallback": used_env_fallback}
                last["message"] = msg("返回 JSON 但缺少标准模型响应字段。", "Returned JSON but is missing standard model response fields.")
            except ValueError:
                last["message"] = msg("服务返回内容无法解析为 JSON。", "Service response could not be parsed as JSON.")
        else:
            if response.status_code in (401, 403):
                last["message"] = msg(f"认证失败（HTTP {response.status_code}）：请检查 API Key、权限和模型访问范围。", f"Authentication failed (HTTP {response.status_code}): check API Key, permissions, and model access scope.")
            elif response.status_code == 404:
                last["message"] = msg("接口不存在：请检查协议与 Base URL，OpenAI 兼容应指向 API 根路径。", "Endpoint not found: check the protocol and Base URL; an OpenAI-compatible endpoint should point to the API root.")
            else:
                last["message"] = msg(f"服务请求失败（HTTP {response.status_code}）。", f"Service request failed (HTTP {response.status_code}).")
        if "text/html" in content_type.lower():
            last["message"] += msg(" 返回的是 HTML，Base URL 很可能填成了网站首页。", " The response is HTML; Base URL is probably the website homepage.")
    return {"ok": False, **last, "used_env_fallback": used_env_fallback}


def _post_openai_test(config: dict[str, str], url: str, messages: list[dict[str, Any]]):
    return requests.post(
        url,
        headers={"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"},
        json={"model": config["model"], "messages": messages, "max_tokens": 8, "temperature": 0},
        timeout=20,
    )


def _post_anthropic_test(config: dict[str, str], url: str, messages: list[dict[str, Any]]):
    return requests.post(
        url,
        headers={
            "X-Api-Key": config["api_key"],
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        },
        json={"model": config["model"], "messages": messages, "max_tokens": 8, "temperature": 0},
        timeout=20,
    )


def chat_completion(
    settings,
    role: str,
    messages: list[dict[str, Any]],
    *,
    max_tokens: int = 4096,
    temperature: float = 0.1,
    timeout: int | None = None,
    max_retries: int | None = None,
    response_json: bool = False,
    image_base64: str | None = None,
    image_media_type: str = "image/png",
) -> str:
    """Send a chat request to the configured model for a role and return the text.

    For vision roles an image (raw base64, no data-url prefix) may be attached to
    the final user message when ``image_base64`` is given.
    """
    config = resolve_role_config(settings, role)
    if not config["api_key"]:
        raise ApiCallError(f"{role} API key is not configured")
    timeout = _role_timeout(role) if timeout is None else timeout
    max_retries = _role_max_retries(role) if max_retries is None else max_retries
    last_error: ApiCallError | None = None
    for attempt in range(max_retries + 1):
        try:
            if config["protocol"] == "anthropic":
                return _anthropic_messages(config, messages, max_tokens, temperature, timeout, image_base64, image_media_type)
            return _openai_chat(config, messages, max_tokens, temperature, timeout, response_json, image_base64, image_media_type)
        except ApiCallError as exc:
            last_error = exc
            if not exc.retryable:
                raise
            if attempt >= max_retries:
                raise ApiCallError(f"{role} model call failed after {max_retries + 1} attempts: {last_error}", retryable=False) from exc
            time.sleep(0.5 * (attempt + 1))
    raise ApiCallError(f"{role} model call failed after {max_retries + 1} attempts: {last_error}", retryable=False)


def _openai_chat(
    config: dict[str, str],
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    timeout: int,
    response_json: bool,
    image_base64: str | None,
    image_media_type: str,
) -> str:
    payload: dict[str, Any] = {
        "model": config["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if response_json:
        payload["response_format"] = {"type": "json_object"}
    if image_base64:
        _attach_openai_image(payload["messages"], image_base64, image_media_type)

    headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
    urls = [f"{config['base_url']}/chat/completions"]
    if not config["base_url"].rstrip("/").endswith("/v1"):
        urls.append(f"{config['base_url']}/v1/chat/completions")

    last_error: Exception | None = None
    for url in urls:
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last_error = ApiCallError(f"model call failed: {exc}", retryable=True)
            continue
        content_type = response.headers.get("content-type", "").lower()
        if response.ok and "json" in content_type:
            try:
                data = response.json()
            except ValueError as exc:
                last_error = ApiCallError(f"service returned invalid JSON: {response.text[:300]}", retryable=True)
                continue
            return _extract_openai_text(data)
        if response.ok and "text/html" in content_type:
            last_error = ApiCallError(f"provider returned HTML (base url may be missing /v1): {url}")
            continue
        retryable = response.status_code in {429, 500, 502, 503, 504}
        raise ApiCallError(f"HTTP {response.status_code} from {url}: {response.text[:500]}", retryable=retryable)
    if last_error:
        raise last_error
    raise ApiCallError("model call failed: no response", retryable=True)


def _anthropic_messages(
    config: dict[str, str],
    messages: list[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    timeout: int,
    image_base64: str | None,
    image_media_type: str,
) -> str:
    system_parts: list[str] = []
    chat_messages: list[dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            system_parts.append(str(content))
        else:
            chat_messages.append({"role": role, "content": str(content)})
    if image_base64:
        chat_messages[-1]["content"] = [
            {"type": "text", "text": chat_messages[-1]["content"]},
            {"type": "image", "source": {"type": "base64", "media_type": image_media_type, "data": image_base64}},
        ]

    payload: dict[str, Any] = {
        "model": config["model"],
        "messages": chat_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    url = f"{config['base_url']}/v1/messages"
    headers = {
        "X-Api-Key": config["api_key"],
        "Authorization": f"Bearer {config['api_key']}",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise ApiCallError(f"model call failed: {exc}", retryable=True) from exc
    if not response.ok:
        retryable = response.status_code in {429, 500, 502, 503, 504}
        raise ApiCallError(f"HTTP {response.status_code} from {url}: {response.text[:500]}", retryable=retryable)
    try:
        data = response.json()
    except ValueError as exc:
        raise ApiCallError(f"service returned invalid JSON: {response.text[:300]}", retryable=True) from exc
    return _extract_anthropic_text(data)


def _attach_openai_image(messages: list[dict[str, Any]], image_base64: str, media_type: str) -> None:
    data_url = f"data:{media_type};base64,{image_base64}"
    last = messages[-1]
    if isinstance(last.get("content"), str):
        last["content"] = [
            {"type": "text", "text": last["content"]},
            {"type": "image_url", "image_url": {"url": data_url}},
        ]
    elif isinstance(last.get("content"), list):
        last["content"].append({"type": "image_url", "image_url": {"url": data_url}})


def _extract_openai_text(data: dict[str, Any]) -> str:
    try:
        return str(data["choices"][0]["message"]["content"])
    except (KeyError, IndexError, TypeError) as exc:
        raise ApiCallError(f"unexpected OpenAI-compatible response: {json.dumps(data, ensure_ascii=False)[:300]}") from exc


def _extract_anthropic_text(data: dict[str, Any]) -> str:
    blocks = data.get("content", [])
    texts = [str(block.get("text", "")) for block in blocks if isinstance(block, dict) and block.get("type") == "text"]
    if texts:
        return "\n".join(texts)
    raise ApiCallError(f"unexpected Anthropic-compatible response: {json.dumps(data, ensure_ascii=False)[:300]}")


def strip_json_fence(content: str) -> str:
    text = content.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0]
    return text.strip()


# --------------------------------------------------------------------------
# Native tool calling (OpenAI `tools` / Anthropic `tool_use`), used by the
# MechKernel agent loop. Kept protocol-shape aware: each round returns the raw
# assistant message so callers can append tool results without re-serializing.
# --------------------------------------------------------------------------


@dataclass
class ToolCall:
    """One normalized tool invocation parsed from a model response."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCallRound:
    """One model turn that may contain text and/or tool calls."""

    text: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    raw_message: dict[str, Any] = field(default_factory=dict)
    finish_reason: str | None = None


def tool_result_message(tool_call: ToolCall, content: str, *, protocol: str) -> dict[str, Any]:
    """Build the protocol-correct follow-up message carrying one tool result."""
    if protocol == "anthropic":
        return {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": tool_call.id, "content": content}],
        }
    return {"role": "tool", "tool_call_id": tool_call.id, "content": content}


def _openai_tool_payload(tools: list[dict[str, Any]], tool_choice: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"tools": tools}
    if tool_choice and tool_choice != "auto":
        payload["tool_choice"] = tool_choice
    return payload


def _anthropic_tool_payload(tools: list[dict[str, Any]], tool_choice: str) -> dict[str, Any]:
    payload: dict[str, Any] = {"tools": [{"name": t["function"]["name"],
                                          "description": t["function"]["description"],
                                          "input_schema": t["function"]["parameters"]} for t in tools]}
    if tool_choice == "required":
        payload["tool_choice"] = {"type": "any"}
    return payload


def chat_completion_with_tools(
    settings,
    role: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    tool_choice: str = "auto",
    max_tokens: int = 4096,
    temperature: float = 0.1,
    timeout: int | None = None,
    max_retries: int | None = None,
) -> ToolCallRound:
    """Send a chat request with native tool definitions and return the parsed round.

    ``tools`` uses the OpenAI wire shape ``{"type": "function", "function": {...}}``;
    the Anthropic branch converts it to ``input_schema`` form automatically.
    """
    config = resolve_role_config(settings, role)
    if not config["api_key"]:
        raise ApiCallError(f"{role} API key is not configured")
    timeout = _role_timeout(role) if timeout is None else timeout
    max_retries = _role_max_retries(role) if max_retries is None else max_retries
    last_error: ApiCallError | None = None
    for attempt in range(max_retries + 1):
        try:
            if config["protocol"] == "anthropic":
                return _anthropic_tool_round(config, messages, tools, tool_choice, max_tokens, temperature, timeout)
            return _openai_tool_round(config, messages, tools, tool_choice, max_tokens, temperature, timeout)
        except ApiCallError as exc:
            last_error = exc
            if not exc.retryable:
                raise
            if attempt >= max_retries:
                raise ApiCallError(f"{role} tool call failed after {max_retries + 1} attempts: {last_error}", retryable=False) from exc
            time.sleep(0.5 * (attempt + 1))
    raise ApiCallError(f"{role} tool call failed after {max_retries + 1} attempts: {last_error}", retryable=False)


def _openai_tool_round(
    config: dict[str, str],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
) -> ToolCallRound:
    payload: dict[str, Any] = {
        "model": config["model"],
        "messages": messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **_openai_tool_payload(tools, tool_choice),
    }
    headers = {"Authorization": f"Bearer {config['api_key']}", "Content-Type": "application/json"}
    urls = [f"{config['base_url']}/chat/completions"]
    if not config["base_url"].rstrip("/").endswith("/v1"):
        urls.append(f"{config['base_url']}/v1/chat/completions")

    last_error: Exception | None = None
    for url in urls:
        try:
            response = requests.post(url, headers=headers, json=payload, timeout=timeout)
        except requests.RequestException as exc:
            last_error = ApiCallError(f"model call failed: {exc}", retryable=True)
            continue
        content_type = response.headers.get("content-type", "").lower()
        if response.ok and "json" in content_type:
            try:
                data = response.json()
            except ValueError as exc:
                last_error = ApiCallError(f"service returned invalid JSON: {response.text[:300]}", retryable=True)
                continue
            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            tool_calls: list[ToolCall] = []
            for raw_call in message.get("tool_calls") or []:
                function = raw_call.get("function") or {}
                raw_args = function.get("arguments")
                if isinstance(raw_args, str):
                    try:
                        args = json.loads(raw_args) if raw_args.strip() else {}
                    except json.JSONDecodeError:
                        args = {}
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {}
                tool_calls.append(ToolCall(
                    id=str(raw_call.get("id") or f"call_{len(tool_calls)}"),
                    name=str(function.get("name") or ""),
                    arguments=args if isinstance(args, dict) else {},
                ))
            return ToolCallRound(
                text=str(message.get("content") or ""),
                tool_calls=tool_calls,
                raw_message=message,
                finish_reason=choice.get("finish_reason"),
            )
        if response.ok and "text/html" in content_type:
            last_error = ApiCallError(f"provider returned HTML (base url may be missing /v1): {url}")
            continue
        retryable = response.status_code in {429, 500, 502, 503, 504}
        raise ApiCallError(f"HTTP {response.status_code} from {url}: {response.text[:500]}", retryable=retryable)
    if last_error:
        raise last_error
    raise ApiCallError("model call failed: no response", retryable=True)


def _anthropic_tool_round(
    config: dict[str, str],
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    tool_choice: str,
    max_tokens: int,
    temperature: float,
    timeout: int,
) -> ToolCallRound:
    system_parts: list[str] = []
    chat_messages: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") == "system":
            system_parts.append(str(message.get("content")))
        else:
            chat_messages.append(message)

    payload: dict[str, Any] = {
        "model": config["model"],
        "messages": chat_messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
        **_anthropic_tool_payload(tools, tool_choice),
    }
    if system_parts:
        payload["system"] = "\n\n".join(system_parts)

    url = f"{config['base_url']}/v1/messages"
    headers = {
        "X-Api-Key": config["api_key"],
        "Authorization": f"Bearer {config['api_key']}",
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json",
    }
    try:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        raise ApiCallError(f"model call failed: {exc}", retryable=True) from exc
    if not response.ok:
        retryable = response.status_code in {429, 500, 502, 503, 504}
        raise ApiCallError(f"HTTP {response.status_code} from {url}: {response.text[:500]}", retryable=retryable)
    try:
        data = response.json()
    except ValueError as exc:
        raise ApiCallError(f"service returned invalid JSON: {response.text[:300]}", retryable=True) from exc
    blocks = data.get("content", [])
    tool_calls: list[ToolCall] = []
    texts: list[str] = []
    for block in blocks:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "tool_use":
            args = block.get("input")
            tool_calls.append(ToolCall(
                id=str(block.get("id") or f"toolu_{len(tool_calls)}"),
                name=str(block.get("name") or ""),
                arguments=args if isinstance(args, dict) else {},
            ))
        elif block.get("type") == "text":
            texts.append(str(block.get("text", "")))
    return ToolCallRound(
        text="\n".join(texts),
        tool_calls=tool_calls,
        raw_message={"role": "assistant", "content": blocks},
        finish_reason=data.get("stop_reason"),
    )


def parse_json_object(content: str) -> dict[str, Any]:
    text = strip_json_fence(content)
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        # Tolerate a leading "```json" block or surrounding prose by extracting the
        # first balanced {...} region.
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end <= start:
            raise
        parsed = json.loads(text[start : end + 1])
    if not isinstance(parsed, dict):
        raise ApiCallError("model returned valid JSON but not an object")
    return parsed
